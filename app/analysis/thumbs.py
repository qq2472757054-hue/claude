"""片段缩略图。"""
from __future__ import annotations

from pathlib import Path

from ..imageio import grab_frame, open_image


def make_thumb(src: str | Path, dst: str | Path, t: float, is_image: bool, width: int = 360) -> bool:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if is_image:
            im = open_image(src)
            im.thumbnail((width, width * 4))
        else:
            im = grab_frame(src, t, width)
            if im is None:
                return False
        im.save(dst, "JPEG", quality=82)
        return True
    except Exception:
        return False
