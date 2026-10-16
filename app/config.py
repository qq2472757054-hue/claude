"""设置读写（settings.toml）。

设置文件放在程序目录下，便携包整体拷走后设置仍在。
"""
from __future__ import annotations

import copy
import os
import threading
import tomllib
from pathlib import Path
from typing import Any

from .paths import APP_ROOT, Library

SETTINGS_PATH = Path(os.environ.get("COOLPLAY_SETTINGS", APP_ROOT / "settings.toml"))

DEFAULTS: dict[str, Any] = {
    "library_root": "",
    "cities": ["重庆", "成都", "张家界"],
    "material_types": ["景点", "美食", "游客互动", "导游出镜", "酒店", "车辆", "接待", "夜景", "其他"],
    "brand": {
        "name_zh": "酷玩假期",
        "name_en": "Coolplay Vacation",
        "logo": "品牌/logo.png",
        "phone": "",
        "whatsapp": "",
        "line": "",
        "website": "",
    },
    "asr": {
        "engine": "whisper_local",
        "model_size": "small",
        "compute_type": "int8",
        "hf_endpoint": "",
    },
    "render": {
        "hw_encoder": "auto",
        "allow_used_materials": False,
        "max_overlap_in_batch": 0.3,
        "move_rule": "any_clip_used",
        "target_lufs": -14,
        "keep_failed_temp": True,
        "color_grade": False,
    },
    "llm": {
        "enabled": False,
        "provider": "deepseek",
        "api_key": "",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
    },
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(force: bool = False) -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None and not force:
            return copy.deepcopy(_cache)
        data: dict[str, Any] = {}
        if SETTINGS_PATH.is_file():
            with open(SETTINGS_PATH, "rb") as f:
                data = tomllib.load(f)
        _cache = _merge(DEFAULTS, data)
        return copy.deepcopy(_cache)


def save(settings: dict[str, Any]) -> None:
    global _cache
    with _lock:
        merged = _merge(DEFAULTS, settings)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(dumps_toml(merged), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
        _cache = merged


def update(**changes: Any) -> dict[str, Any]:
    s = load()
    s = _merge(s, changes)
    save(s)
    return s


def library() -> Library | None:
    root = load().get("library_root")
    if not root:
        return None
    return Library(root)


# ---------- 极简 TOML 写出（只需支持本程序用到的类型） ----------

def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        s = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{s}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"不支持的设置值类型: {type(v)}")


def dumps_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    tables = []
    for k, v in data.items():
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            lines.append(f"{k} = {_toml_value(v)}")
    for name, table in tables:
        lines.append("")
        lines.append(f"[{name}]")
        for k, v in table.items():
            lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"
