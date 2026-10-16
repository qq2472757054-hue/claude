"""切镜：PySceneDetect，把长视频切成若干镜头。"""
from __future__ import annotations

from pathlib import Path

from .. import logs

log = logs.get("scenes")

MIN_CLIP = 1.5


def detect(path: str | Path, duration: float, threshold: float = 27.0) -> list[tuple[float, float]]:
    """返回 [(start, end)]，丢弃短于 1.5 秒的片段。失败时整段视频作为一个片段。"""
    scenes: list[tuple[float, float]] = []
    try:
        from scenedetect import ContentDetector, SceneManager, open_video

        video = None
        for backend in ("pyav", "opencv"):
            try:
                video = open_video(str(path), backend=backend)
                break
            except Exception:
                continue
        if video is None:
            raise RuntimeError("无法打开视频")
        sm = SceneManager()
        sm.auto_downscale = True
        sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=15))
        sm.detect_scenes(video)
        scenes = [(s.get_seconds(), e.get_seconds()) for s, e in sm.get_scene_list()]
    except Exception as e:
        log.warning("切镜失败，整段作为一个片段: %s (%s)", path, e)
    if not scenes:
        scenes = [(0.0, float(duration))]
    out = []
    for s, e in scenes:
        e = min(e, duration) if duration > 0 else e
        if e - s >= MIN_CLIP:
            out.append((round(s, 3), round(e, 3)))
    return out
