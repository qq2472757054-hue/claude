"""图片读取（含 heic、EXIF 方向）与视频抽帧。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .ffrun import ffmpeg

try:  # heic 支持
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass


def open_image(path: str | Path) -> Image.Image:
    """打开图片并按 EXIF 方向转正，返回 RGB。"""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return im.convert("RGB")


def grab_frame(path: str | Path, t: float, width: int | None = 320) -> Image.Image | None:
    """从视频 t 秒处取一帧（自动处理旋转）。width=None 表示原尺寸。"""
    vf = f"scale={width}:-2" if width else "null"
    try:
        r = ffmpeg(["-ss", f"{max(t, 0):.3f}", "-i", str(path), "-frames:v", "1",
                    "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                   capture_stdout=True, timeout=120)
    except Exception:
        return None
    if not r.stdout:
        return None
    import io
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def to_gray(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("L"), dtype=np.float32)
