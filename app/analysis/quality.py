"""画质评分：清晰度（拉普拉斯方差）、亮度、帧差。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..imageio import grab_frame, open_image, to_gray

# 在 320 像素宽的灰度图上计算
DARK_THRESHOLD = 30.0        # 平均亮度（0–255）低于此值视为过暗
BRIGHT_THRESHOLD = 235.0     # 高于此值视为过曝
BLUR_THRESHOLD = 25.0        # 拉普拉斯方差低于此值视为过糊
STATIC_THRESHOLD = 0.4       # 抽样帧之间平均绝对差低于此值视为几乎静止
SAMPLES = 5


@dataclass
class Quality:
    sharpness: float
    brightness: float
    motion: float
    low_quality: bool
    reason: str


def laplacian_var(gray: np.ndarray) -> float:
    try:
        import cv2

        return float(cv2.Laplacian(gray, cv2.CV_32F).var())
    except Exception:  # 没有 OpenCV 时的等价实现
        k = (gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:] - 4 * gray[1:-1, 1:-1])
        return float(k.var())


def judge(sharpness: float, brightness: float, motion: float | None) -> tuple[bool, str]:
    reasons = []
    if brightness < DARK_THRESHOLD:
        reasons.append("过暗")
    if brightness > BRIGHT_THRESHOLD:
        reasons.append("过曝")
    if sharpness < BLUR_THRESHOLD:
        reasons.append("过糊")
    if motion is not None and motion < STATIC_THRESHOLD:
        reasons.append("几乎静止")
    return bool(reasons), "、".join(reasons)


def score_frames(grays: list[np.ndarray], is_image: bool) -> Quality:
    if not grays:
        return Quality(0, 0, 0, True, "无法读取画面")
    sharp = float(np.median([laplacian_var(g) for g in grays]))
    bright = float(np.mean([g.mean() for g in grays]))
    motion = None
    if not is_image and len(grays) > 1:
        diffs = [float(np.abs(a - b).mean()) for a, b in zip(grays, grays[1:]) if a.shape == b.shape]
        motion = float(np.mean(diffs)) if diffs else 0.0
    low, reason = judge(sharp, bright, motion)
    return Quality(round(sharp, 2), round(bright, 2), round(motion or 0.0, 3), low, reason)


def score_clip(path: str | Path, start: float, end: float, is_image: bool) -> Quality:
    if is_image:
        im = open_image(path)
        im.thumbnail((320, 320 * 4))
        return score_frames([to_gray(im)], True)
    span = max(end - start, 0.01)
    times = [start + span * (i + 0.5) / SAMPLES for i in range(SAMPLES)]
    grays = []
    for t in times:
        im = grab_frame(path, t, 320)
        if im is not None:
            grays.append(to_gray(im))
    return score_frames(grays, False)
