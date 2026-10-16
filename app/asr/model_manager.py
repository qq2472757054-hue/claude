"""faster-whisper 模型：下载（可配镜像）、手动放置检测。

模型目录：数据/models/faster-whisper-<size>/，内含 model.bin、config.json、tokenizer.json、vocabulary.*
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .. import logs

log = logs.get("asr.model")

REPOS = {
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}
REQUIRED = ["model.bin", "config.json", "tokenizer.json"]
_download_lock = threading.Lock()


def model_dir(models_dir: str | Path, size: str) -> Path:
    return Path(models_dir) / f"faster-whisper-{size}"


def is_ready(models_dir: str | Path, size: str) -> bool:
    d = model_dir(models_dir, size)
    return all((d / f).is_file() for f in REQUIRED) and any(d.glob("vocabulary.*"))


def manual_hint(models_dir: str | Path, size: str) -> str:
    repo = REPOS.get(size, REPOS["small"])
    d = model_dir(models_dir, size)
    return (
        f"语音识别模型（{size}）还没有准备好。\n"
        f"方法一：在“设置”里填写国内镜像地址（例如 https://hf-mirror.com），然后点“下载模型”。\n"
        f"方法二：用浏览器打开 https://huggingface.co/{repo}/tree/main （或镜像站同名页面），"
        f"下载 model.bin、config.json、tokenizer.json、vocabulary.* 这几个文件，"
        f"放到文件夹：{d}"
    )


def download(models_dir: str | Path, size: str, hf_endpoint: str = "",
             progress=None) -> Path:
    """下载模型到 数据/models/。失败抛出 RuntimeError（带中文说明）。"""
    if size not in REPOS:
        raise ValueError(f"不支持的模型大小：{size}")
    with _download_lock:
        d = model_dir(models_dir, size)
        if is_ready(models_dir, size):
            return d
        if hf_endpoint:
            os.environ["HF_ENDPOINT"] = hf_endpoint.rstrip("/")
        try:
            from huggingface_hub import snapshot_download

            if progress:
                progress(0.05, f"正在下载 {REPOS[size]} …")
            snapshot_download(
                repo_id=REPOS[size], local_dir=str(d),
                allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                                "tokenizer.json", "vocabulary.*"],
                endpoint=hf_endpoint.rstrip("/") or None,
            )
        except Exception as e:
            log.exception("模型下载失败")
            raise RuntimeError("模型下载失败（%s）。\n%s" % (e, manual_hint(models_dir, size))) from e
        if not is_ready(models_dir, size):
            raise RuntimeError("模型文件不完整。\n" + manual_hint(models_dir, size))
        if progress:
            progress(1.0, "模型下载完成")
        log.info("模型已下载: %s", d)
        return d
