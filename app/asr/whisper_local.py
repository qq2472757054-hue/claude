"""本地 faster-whisper 识别（CTranslate2，CPU int8），带 Silero VAD。"""
from __future__ import annotations

import threading
from pathlib import Path

from .. import logs
from . import model_manager
from .base import ASREngine, ModelNotReady, Segment, TranscribeResult, register

log = logs.get("asr.whisper")

_models: dict[tuple, object] = {}
_models_lock = threading.Lock()


def _decode(path: str | Path):
    """用 ffmpeg 解码为 16kHz 单声道 float32（不依赖 PyAV 版本，中文路径也没问题）。"""
    import numpy as np

    from ..ffrun import ffmpeg

    r = ffmpeg(["-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
               capture_stdout=True, timeout=1800)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


class WhisperLocal(ASREngine):
    name = "whisper_local"

    def __init__(self, cfg: dict):
        self.size = cfg.get("model_size", "small")
        self.compute_type = cfg.get("compute_type", "int8")
        self.models_dir = Path(cfg["models_dir"])

    def _model(self):
        if not model_manager.is_ready(self.models_dir, self.size):
            raise ModelNotReady(model_manager.manual_hint(self.models_dir, self.size))
        key = (str(self.models_dir), self.size, self.compute_type)
        with _models_lock:
            if key not in _models:
                from faster_whisper import WhisperModel

                device = "auto"
                try:
                    import ctranslate2

                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    device = "cpu"
                compute = self.compute_type if device == "cpu" else "float16"
                log.info("加载识别模型 %s (%s, %s)", self.size, device, compute)
                _models[key] = WhisperModel(str(model_manager.model_dir(self.models_dir, self.size)),
                                            device=device, compute_type=compute)
            return _models[key]

    def detect_speech(self, path: str | Path) -> bool:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        audio = _decode(path)
        if audio.size == 0:
            return False
        ts = get_speech_timestamps(audio, VadOptions(min_speech_duration_ms=400))
        speech = sum(t["end"] - t["start"] for t in ts) / 16000.0
        return speech >= 0.8

    def transcribe(self, path: str | Path, cancel: threading.Event | None = None) -> TranscribeResult:
        model = self._model()
        audio = _decode(path)
        segments, info = model.transcribe(audio, vad_filter=True, beam_size=5,
                                          condition_on_previous_text=False)
        out: list[Segment] = []
        for s in segments:  # 生成器，逐句解码，可中途取消
            if cancel is not None and cancel.is_set():
                break
            text = s.text.strip()
            if text:
                out.append(Segment(round(s.start, 2), round(s.end, 2), text))
        return TranscribeResult(language=info.language or "", segments=out,
                                has_speech=bool(out), model=f"faster-whisper-{self.size}")


@register("whisper_local")
def _factory(cfg: dict) -> ASREngine:
    return WhisperLocal(cfg)
