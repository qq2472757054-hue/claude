"""配乐选择、人声压低（ducking）、响度统一（两遍 loudnorm）。"""
from __future__ import annotations

import json
import random
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import logs
from ..ffrun import ffmpeg

log = logs.get("audio")


@dataclass
class VoicePart:
    path: Path        # 原始素材
    src_start: float  # 在素材中的起点
    start: float      # 在成片时间轴上的开始时间
    dur: float


def pick_music(ctx, style: str, track: str, rng: random.Random, avoid: set[int] | None = None) -> dict | None:
    """只从“能否商用 = 是”的曲目中选。指定曲目优先；同批次尽量不重复；使用次数少的优先。"""
    avoid = avoid or set()
    rows = ctx.db.all("SELECT * FROM music WHERE commercial_ok = '是' AND missing = 0")
    rows = [r for r in rows if ctx.lib.abs(r["rel_path"]).is_file()]
    if track:
        for r in rows:
            if r["rel_path"] == track or Path(r["rel_path"]).name == track:
                return r
    pool = [r for r in rows if r["style"] == style] if style else rows
    if not pool:
        return None
    fresh = [r for r in pool if r["id"] not in avoid] or pool
    min_use = min(r["use_count"] for r in fresh)
    least = [r for r in fresh if r["use_count"] == min_use]
    return rng.choice(least)


def _premix(out: Path, total: float, music: Path | None, volume: float, voices: list[VoicePart],
            cancel: threading.Event | None) -> None:
    args: list[str] = []
    flt: list[str] = []
    idx = 0
    music_label = None
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music)]
        fade_out_start = max(total - 2.0, 0)
        flt.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                   f"asetpts=N/SR/TB,volume={volume:.3f},afade=t=in:st=0:d=1,"
                   f"afade=t=out:st={fade_out_start:.3f}:d=2[mus]")
        music_label = "mus"
        idx += 1
    voice_labels = []
    for v in voices:
        args += ["-ss", f"{v.src_start:.3f}", "-t", f"{v.dur + 0.3:.3f}", "-i", str(v.path)]
        delay = int(round(v.start * 1000))
        flt.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{v.dur:.3f},"
                   f"afade=t=in:d=0.08,afade=t=out:st={max(v.dur - 0.12, 0):.3f}:d=0.12,"
                   f"adelay={delay}|{delay}[v{idx}]")
        voice_labels.append(f"v{idx}")
        idx += 1
    if voice_labels:
        ins = "".join(f"[{l}]" for l in voice_labels)
        flt.append(f"{ins}amix=inputs={len(voice_labels)}:normalize=0:dropout_transition=0,"
                   f"apad,atrim=0:{total:.3f}[voice]")
    if music_label and voice_labels:
        flt.append("[voice]asplit=2[vo][sc]")
        # 人声出现时把配乐压低
        flt.append(f"[{music_label}][sc]sidechaincompress=threshold=0.02:ratio=12:attack=15:"
                   f"release=450:makeup=1[ducked]")
        flt.append("[ducked][vo]amix=inputs=2:normalize=0:duration=first[out]")
    elif music_label:
        flt.append(f"[{music_label}]anull[out]")
    elif voice_labels:
        flt.append("[voice]anull[out]")
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        flt.append(f"[{idx}:a]atrim=0:{total:.3f}[out]")
    ffmpeg([*args, "-filter_complex", ";".join(flt), "-map", "[out]", "-t", f"{total:.3f}",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], cancel=cancel)


def measure_loudness(path: Path, target: float, cancel: threading.Event | None = None) -> dict | None:
    r = ffmpeg(["-i", str(path), "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", "-"], cancel=cancel)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return None
    try:
        return json.loads(m[-1])
    except json.JSONDecodeError:
        return None


def build_audio(work: Path, total: float, music: Path | None, volume: float, voices: list[VoicePart],
                target_lufs: float = -14.0, cancel: threading.Event | None = None) -> tuple[Path, str]:
    """生成成片音轨 wav。返回 (路径, 说明)。"""
    premix = work / "audio_premix.wav"
    _premix(premix, total, music, volume, voices, cancel)
    meas = measure_loudness(premix, target_lufs, cancel)
    out = work / "audio_final.wav"
    try:
        input_i = float(meas["input_i"]) if meas else float("-inf")
    except (TypeError, ValueError):
        input_i = float("-inf")
    if meas is None or input_i < -60:
        # 没有声音（或几乎静音）：不做响度统一
        premix.replace(out)
        return out, "音轨为静音，未做响度统一"
    flt = (f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:measured_I={meas['input_i']}:"
           f"measured_TP={meas['input_tp']}:measured_LRA={meas['input_lra']}:"
           f"measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:linear=true,"
           f"aresample=48000")
    ffmpeg(["-i", str(premix), "-af", flt, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)],
           cancel=cancel)
    # 线性模式受峰值限制可能达不到目标：再测一次，不够则用动态模式
    check = measure_loudness(out, target_lufs, cancel)
    try:
        got = float(check["input_i"]) if check else target_lufs
    except (TypeError, ValueError):
        got = target_lufs
    if abs(got - target_lufs) > 0.8:
        ffmpeg(["-i", str(premix), "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11,aresample=48000",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], cancel=cancel)
    return out, f"响度统一到 {target_lufs} LUFS"
