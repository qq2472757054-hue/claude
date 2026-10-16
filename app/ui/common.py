"""界面公用工具。"""
from __future__ import annotations

import shutil
from pathlib import Path

import gradio as gr

from .. import config, state

NO_LIBRARY = "⚠️ 还没有设置素材库位置，请先到“设置”页选择素材库文件夹。"


def ctx():
    return state.get()


def need_ctx():
    c = state.get()
    if c is None:
        raise gr.Error(NO_LIBRARY)
    return c


def settings() -> dict:
    return config.load()


def cities() -> list[str]:
    return list(settings()["cities"])


def material_types() -> list[str]:
    return list(settings()["material_types"])


def allow_path(app_blocks, path: str | Path) -> None:
    """运行中把素材库加入可访问路径（切换素材库后无需重启）。"""
    p = str(Path(path).resolve())
    if app_blocks is not None and p not in app_blocks.allowed_paths:
        app_blocks.allowed_paths.append(p)


def disk_free_gb(path: str | Path) -> float:
    try:
        return shutil.disk_usage(path).free / 1024 ** 3
    except OSError:
        return 999.0


def fmt_sec(s: float | None) -> str:
    if not s:
        return ""
    return f"{s:.1f}"


def to_rows(df) -> list[list]:
    """gr.Dataframe 的值（pandas / list）统一转成行列表。"""
    if df is None:
        return []
    if hasattr(df, "values"):
        return df.values.tolist()
    if isinstance(df, dict) and "data" in df:
        return df["data"]
    return list(df)


def thumb_abs(c, rel: str | None) -> str | None:
    if not rel:
        return None
    p = c.lib.abs(rel)
    return str(p) if p.is_file() else None
