"""基础信息：时长、分辨率、帧率、旋转、音轨、编码。"""
from __future__ import annotations

from pathlib import Path

from ..ffrun import probe
from ..imageio import open_image


def basic_info(path: str | Path, is_image: bool) -> dict:
    """返回可直接写入 material 表的字段。读不了时 broken=1。"""
    if is_image:
        try:
            im = open_image(path)
            return {"width": im.width, "height": im.height, "duration": 0.0, "fps": 0.0,
                    "rotation": 0, "has_audio": 0, "vcodec": "image", "broken": 0}
        except Exception:
            return {"broken": 1}
    try:
        info = probe(path)
    except Exception:
        return {"broken": 1}
    if not info["has_video"] or info["width"] <= 0 or info["duration"] <= 0:
        return {"broken": 1}
    return {"width": info["width"], "height": info["height"], "duration": info["duration"],
            "fps": info["fps"], "rotation": info["rotation"], "has_audio": int(info["has_audio"]),
            "vcodec": info["vcodec"], "broken": 0}
