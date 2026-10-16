"""全局运行上下文：当前设置、素材库、数据库。"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from . import config, logs
from .db import Database
from .paths import Library


@dataclass
class Ctx:
    lib: Library
    db: Database

    @property
    def settings(self) -> dict:
        return config.load()


_ctx: Ctx | None = None
_lock = threading.RLock()


def init_library(root: str) -> Ctx:
    """切换/初始化素材库：创建目录、打开数据库、配置日志。"""
    global _ctx
    with _lock:
        settings = config.load()
        lib = Library(root)
        lib.ensure(settings["cities"])
        logs.setup(lib.logs)
        db = Database(lib.db_path)
        _ctx = Ctx(lib=lib, db=db)
        if settings.get("library_root") != str(root):
            config.update(library_root=str(root))
        return _ctx


def get() -> Ctx | None:
    global _ctx
    with _lock:
        if _ctx is None:
            root = config.load().get("library_root")
            if root:
                try:
                    return init_library(root)
                except OSError:
                    return None
        return _ctx


def require() -> Ctx:
    c = get()
    if c is None:
        raise RuntimeError("还没有设置素材库根目录，请先到“设置”页选择素材库位置。")
    return c


def reset_for_tests() -> None:
    global _ctx
    _ctx = None
