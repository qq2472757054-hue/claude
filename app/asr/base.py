"""语音识别引擎接口。新引擎（如 FunASR）实现 ASREngine 并在 ENGINES 中登记即可。"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscribeResult:
    language: str
    segments: list[Segment] = field(default_factory=list)
    has_speech: bool = True
    model: str = ""

    def segments_dicts(self) -> list[dict]:
        return [asdict(s) for s in self.segments]


class ModelNotReady(RuntimeError):
    """模型尚未下载/放置。"""


class ASREngine:
    name = "base"

    def detect_speech(self, path: str | Path) -> bool:
        """是否有人声（VAD）。"""
        raise NotImplementedError

    def transcribe(self, path: str | Path, cancel: threading.Event | None = None) -> TranscribeResult:
        raise NotImplementedError


ENGINES: dict[str, Callable[[dict], ASREngine]] = {}


def register(name: str):
    def deco(factory):
        ENGINES[name] = factory
        return factory
    return deco


def create(settings: dict, models_dir: Path) -> ASREngine:
    asr = settings.get("asr", {})
    name = asr.get("engine", "whisper_local")
    if name not in ENGINES:
        # 延迟导入内置引擎
        from . import whisper_local  # noqa: F401
    if name not in ENGINES:
        raise ValueError(f"未知的识别引擎：{name}")
    return ENGINES[name]({**asr, "models_dir": str(models_dir)})
