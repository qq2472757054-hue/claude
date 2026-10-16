"""拼装 FFmpeg 命令；硬件编码检测与回退。"""
from __future__ import annotations

import platform
import random
import tempfile
import threading
from pathlib import Path

from .. import logs
from ..ffrun import FFmpegError, ffmpeg

log = logs.get("ffmpeg_cmd")

FPS = 30
_hw_cache: dict[str, str | None] = {}
_hw_lock = threading.Lock()

XFADE_TYPES = {
    "fade": ["fade"],
    "slide": ["slideleft", "slideup"],
    "random": ["fade", "slideleft", "slideright", "slideup", "wipeleft", "dissolve", "circleopen",
               "smoothleft"],
}


def _candidates() -> list[str]:
    if platform.system() == "Darwin":
        return ["h264_videotoolbox"]
    return ["h264_nvenc", "h264_qsv", "h264_amf"]


def detect_hw_encoder(mode: str = "auto") -> str | None:
    """返回可用的硬件 H.264 编码器名，或 None。结果缓存。"""
    if mode == "off":
        return None
    with _hw_lock:
        if "result" in _hw_cache:
            return _hw_cache["result"]
        found = None
        for enc in _candidates():
            try:
                with tempfile.TemporaryDirectory() as td:
                    ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-t", "0.5",
                            *encoder_args(enc, "inter"), str(Path(td) / "t.mp4")], timeout=30, quiet=True)
                found = enc
                break
            except Exception:
                continue
        log.info("硬件编码器: %s", found or "无（使用 libx264）")
        _hw_cache["result"] = found
        return found


def encoder_args(enc: str | None, purpose: str = "final") -> list[str]:
    """purpose: inter（中间文件，求快、高质量）/ final（最终输出）。"""
    common = ["-pix_fmt", "yuv420p"]
    if enc in (None, "libx264"):
        if purpose == "inter":
            return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", *common]
        return ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-profile:v", "high",
                "-maxrate", "12M", "-bufsize", "24M", *common]
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p4", "-rc", "vbr", "-cq", "19" if purpose == "inter" else "21",
                "-b:v", "0", "-profile:v", "high", *common]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-global_quality", "19" if purpose == "inter" else "22",
                "-profile:v", "high", "-pix_fmt", "nv12"]
    if enc == "h264_amf":
        return ["-c:v", enc, "-quality", "balanced", "-b:v", "16M" if purpose == "inter" else "10M", *common]
    if enc == "h264_videotoolbox":
        return ["-c:v", enc, "-b:v", "16M" if purpose == "inter" else "10M", "-profile:v", "high", *common]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "20", *common]


def run_encode(args_before_enc: list[str], args_after_enc: list[str], enc: str | None,
               purpose: str, **kw) -> str | None:
    """用 enc 编码；硬件编码失败时自动回退 libx264。返回实际使用的编码器。"""
    if enc:
        try:
            ffmpeg([*args_before_enc, *encoder_args(enc, purpose), *args_after_enc], **kw)
            return enc
        except FFmpegError as e:
            log.warning("硬件编码 %s 失败，回退 libx264：%s", enc, e.tail[-300:])
    ffmpeg([*args_before_enc, *encoder_args(None, purpose), *args_after_enc], **kw)
    return None


# ---------- 片段 ----------

def video_fill_filter(src_w: int, src_h: int, w: int, h: int, mode: str, grade: bool) -> str:
    """输入 [0:v]，输出 [v]。"""
    same = src_w > 0 and src_h > 0 and abs(src_w / src_h - w / h) / (w / h) < 0.03
    tail = f"setsar=1,fps={FPS},format=yuv420p"
    if grade:
        tail = "eq=contrast=1.04:saturation=1.08:brightness=0.01," + tail
    if mode == "crop" or same:
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,"
                f"crop={w}:{h},{tail},tpad=stop_mode=clone:stop_duration=1[v]")
    bw, bh = max(w // 8 // 2 * 2, 2), max(h // 8 // 2 * 2, 2)
    return (f"[0:v]split=2[a][b];"
            f"[a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=6:2,scale={w}:{h}:flags=bilinear,eq=brightness=-0.06[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail},tpad=stop_mode=clone:stop_duration=1[v]")


def segment_video_args(src: Path, src_start: float, dur: float, src_w: int, src_h: int,
                       w: int, h: int, mode: str, keep_audio: bool, grade: bool, out: Path
                       ) -> tuple[list[str], list[str]]:
    before = ["-ss", f"{src_start:.3f}", "-t", f"{dur + 0.5:.3f}", "-i", str(src),
              "-filter_complex", video_fill_filter(src_w, src_h, w, h, mode, grade), "-map", "[v]"]
    if keep_audio:
        before += ["-map", "0:a:0?", "-af", "aresample=48000,aformat=channel_layouts=stereo,apad"]
    after = ["-t", f"{dur:.3f}", "-r", str(FPS)]
    after += ["-c:a", "pcm_s16le"] if keep_audio else ["-an"]
    after += [str(out)]
    return before, after


def kenburns_filter(w: int, h: int, dur: float, rng: random.Random) -> str:
    """缓慢推拉平移，输入为 2 倍尺寸的画布。"""
    n = max(int(round(dur * FPS)), 1)
    move = rng.choice(["in", "out", "left", "right", "up", "down"])
    zmax = 1.15
    if move == "in":
        z, x, y = f"1+{zmax - 1:.3f}*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif move == "out":
        z, x, y = f"{zmax:.3f}-{zmax - 1:.3f}*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif move == "left":
        z, x, y = f"{zmax:.3f}", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"
    elif move == "right":
        z, x, y = f"{zmax:.3f}", f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"
    elif move == "up":
        z, x, y = f"{zmax:.3f}", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-on/{n})"
    else:
        z, x, y = f"{zmax:.3f}", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*on/{n}"
    return (f"[0:v]zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={FPS},"
            f"setsar=1,format=yuv420p[v]")


def segment_image_args(canvas_png: Path, dur: float, w: int, h: int, rng: random.Random,
                       out: Path, static: bool = False) -> tuple[list[str], list[str]]:
    if static:
        flt = f"[0:v]scale={w}:{h},setsar=1,fps={FPS},format=yuv420p[v]"
        before = ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(canvas_png),
                  "-filter_complex", flt, "-map", "[v]"]
    else:
        before = ["-i", str(canvas_png), "-filter_complex", kenburns_filter(w, h, dur, rng),
                  "-map", "[v]"]
    after = ["-t", f"{dur:.3f}", "-r", str(FPS), "-an", str(out)]
    return before, after


# ---------- 拼接 ----------

def xfade_chain(n: int, durs: list[float], trans: float, kinds: list[str]) -> tuple[str, float]:
    """生成 [i:v] 的 xfade/concat 链，输出 [vcat]。返回 (filter, 总时长)。"""
    parts = [f"[{i}:v]settb=AVTB,fps={FPS},format=yuv420p[s{i}]" for i in range(n)]
    if n == 1:
        parts.append("[s0]null[vcat]")
        return ";".join(parts), durs[0]
    if trans <= 0:
        ins = "".join(f"[s{i}]" for i in range(n))
        parts.append(f"{ins}concat=n={n}:v=1:a=0[vcat]")
        return ";".join(parts), sum(durs)
    prev = "s0"
    offset = 0.0
    total = durs[0]
    for i in range(1, n):
        offset = total - trans
        label = "vcat" if i == n - 1 else f"x{i}"
        parts.append(f"[{prev}][s{i}]xfade=transition={kinds[i - 1]}:duration={trans:.3f}:"
                     f"offset={offset:.3f}[{label}]")
        total = total + durs[i] - trans
        prev = label
    return ";".join(parts), total


def filter_path(p: str) -> str:
    """滤镜参数中的路径转义（使用相对路径时基本不需要）。"""
    p = p.replace("\\", "/")
    for ch in (":", "'", "[", "]", ",", ";"):
        p = p.replace(ch, "\\" + ch)
    return p
