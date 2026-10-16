"""渲染一条视频（一个比例）：片段 → 片尾卡 → 拼接转场 → logo → 字幕 → 混音。"""
from __future__ import annotations

import os
import random
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import logs
from ..imageio import grab_frame, open_image
from ..templates.schema import ASPECTS, Template
from . import ffmpeg_cmd as fc
from . import imgfx, subtitles
from .planner import TextPack, Timeline, text_events

log = logs.get("engine")


@dataclass
class RenderResult:
    video: Path
    ass: Path
    srt: Path
    screens: list
    duration: float
    encoder: str


def aspect_tag(aspect: str) -> str:
    return aspect.replace(":", "x")


def render_aspect(*, lib, tl: Timeline, template: Template, aspect: str, texts: TextPack,
                  audio_wav: Path, work: Path, brand: dict, encoder: str | None,
                  cancel: threading.Event | None = None, progress=None, seed: int = 0,
                  color_grade: bool = False) -> RenderResult:
    w, h = ASPECTS[aspect]
    tag = aspect_tag(aspect)
    rng = random.Random(f"{seed}-{aspect}")
    font_bold = imgfx.find_font(lib.fonts, bold=True)
    segs: list[Path] = []
    used_encoder = encoder
    n_total = len(tl.choices) + (1 if tl.end_card else 0)

    def step(i, msg):
        if progress:
            progress(i / max(n_total + 1, 1), msg)

    for i, c in enumerate(tl.choices):
        step(i, f"[{aspect}] 处理片段 {i + 1}/{len(tl.choices)}")
        out = work / f"seg_{tag}_{i:02d}.mp4"
        src = lib.abs(c.rel_path)
        if c.is_image:
            canvas = imgfx.fill(open_image(src), w * 2, h * 2, template.fill_mode)
            png = work / f"img_{tag}_{i:02d}.png"
            canvas.save(png)
            before, after = fc.segment_image_args(png, tl.durs[i], w, h, rng, out)
        else:
            before, after = fc.segment_video_args(src, c.src_start, tl.durs[i], c.width, c.height,
                                                  w, h, template.fill_mode, False, color_grade, out)
        used_encoder = fc.run_encode(before, after, used_encoder, "inter", cancel=cancel)
        segs.append(out)

    logo_path = lib.abs(brand.get("logo") or "品牌/logo.png") if brand.get("logo") else None
    if tl.end_card:
        step(len(tl.choices), f"[{aspect}] 片尾品牌卡")
        bg = None
        last = tl.choices[-1] if tl.choices else None
        if last is not None:
            try:
                bg = open_image(lib.abs(last.rel_path)) if last.is_image else grab_frame(
                    lib.abs(last.rel_path), last.src_start + last.dur / 2, 640)
            except Exception:
                bg = None
        card = imgfx.end_card(w, h, brand, logo_path, font_bold, bg)
        png = work / f"endcard_{tag}.png"
        card.save(png)
        out = work / f"seg_{tag}_end.mp4"
        before, after = fc.segment_image_args(png, tl.durs[-1], w, h, rng, out, static=True)
        used_encoder = fc.run_encode(before, after, used_encoder, "inter", cancel=cancel)
        segs.append(out)

    # 字幕
    bilingual = template.subtitle_language == "bilingual"
    style = template.subtitle_style
    scale = w / 1080 if aspect == "16:9" else 1.0
    font_regular = imgfx.find_font(lib.fonts, bold=True)
    m_en = subtitles.font_measurer(font_regular, int(style.font_size * scale))
    m_zh = subtitles.font_measurer(font_regular, int(style.font_size_zh * scale))
    max_px = subtitles.max_text_px(w, aspect) - 2 * style.outline
    events = text_events(tl, texts, template.subtitle_language)
    # 开场标题字号更大：按比例收紧宽度
    screens = []
    for ev in events:
        k = 1.3 if ev.kind == "hook" else 1.1 if ev.kind == "cta" else 1.0
        screens += subtitles.layout_events([ev], aspect, bilingual, m_en, m_zh, max_px / k)
    ass_path = work / f"subs_{tag}.ass"
    ass_path.write_text(subtitles.build_ass(screens, w, h, aspect, style), encoding="utf-8")
    srt_path = work / f"subs_{tag}.srt"
    srt_path.write_text(subtitles.build_srt(screens), encoding="utf-8")

    # 拼接 + logo + 字幕 + 音轨
    step(n_total, f"[{aspect}] 合成成片")
    inputs: list[str] = []
    for s in segs:
        inputs += ["-i", s.name]
    chain, total = fc.xfade_chain(len(segs), tl.durs, tl.trans_dur, tl.transitions)
    flt = [chain]
    cur = "vcat"
    idx = len(segs)
    if brand.get("logo_corner_enabled", True) and template.brand.get("logo_corner") and logo_path \
            and Path(logo_path).is_file():
        logo = imgfx.load_logo(logo_path, int(min(w, h) * 0.14))
        if logo is not None:
            lp = work / f"logo_{tag}.png"
            logo.save(lp)
            inputs += ["-loop", "1", "-i", lp.name]
            margin = int(min(w, h) * 0.04)
            end_at = tl.starts[-1] if tl.end_card else total + 1
            flt.append(f"[{idx}:v]format=rgba,colorchannelmixer=aa=0.9[lg]")
            flt.append(f"[{cur}][lg]overlay={margin}:{margin}:shortest=1:"
                       f"enable='lt(t,{end_at:.3f})'[vl]")
            cur = "vl"
            idx += 1
    fonts_rel = os.path.relpath(lib.fonts, work).replace("\\", "/")
    if screens:
        flt.append(f"[{cur}]ass={fc.filter_path(ass_path.name)}:fontsdir={fc.filter_path(fonts_rel)}[vs]")
        cur = "vs"
    flt.append(f"[{cur}]format=yuv420p[vout]")
    inputs += ["-i", audio_wav.name if audio_wav.parent == work else str(audio_wav)]
    audio_idx = idx
    out_video = work / f"final_{tag}.mp4"
    before = [*inputs, "-filter_complex", ";".join(flt), "-map", "[vout]", "-map", f"{audio_idx}:a"]
    after = ["-r", str(fc.FPS), "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", out_video.name]
    used_encoder = fc.run_encode(before, after, used_encoder, "final", cancel=cancel, cwd=work)
    step(n_total + 1, f"[{aspect}] 完成")
    return RenderResult(out_video, ass_path, srt_path, screens, total, used_encoder or "libx264")
