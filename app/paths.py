"""程序目录、素材库目录结构与外部程序（ffmpeg）定位。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_ROOT / "assets"
PROMPTS_DIR = APP_ROOT / "prompts"
BUILTIN_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "builtin"
VENDOR_FFMPEG_DIR = APP_ROOT / "vendor" / "ffmpeg"

# 素材库内的固定目录名
PENDING = "待使用"
USED = "已使用"
BACKUP = "原始备份"
MUSIC = "配乐"
FONTS = "字体"
BRAND = "品牌"
OUTPUT = "输出"
OUT_PENDING = "待审核"
OUT_APPROVED = "已审核"
OUT_REJECTED = "已退回"
TEMPLATES = "模板"
DATA = "数据"
TEMP = "临时"
LOGS = "日志"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac"}
DEFAULT_MUSIC_STYLES = ["轻快", "抒情", "动感", "城市夜景"]


class Library:
    """素材库根目录及其子目录。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @property
    def pending(self) -> Path:
        return self.root / PENDING

    @property
    def used(self) -> Path:
        return self.root / USED

    @property
    def backup(self) -> Path:
        return self.root / BACKUP

    @property
    def music(self) -> Path:
        return self.root / MUSIC

    @property
    def fonts(self) -> Path:
        return self.root / FONTS

    @property
    def brand(self) -> Path:
        return self.root / BRAND

    @property
    def output(self) -> Path:
        return self.root / OUTPUT

    @property
    def out_pending(self) -> Path:
        return self.output / OUT_PENDING

    @property
    def out_approved(self) -> Path:
        return self.output / OUT_APPROVED

    @property
    def out_rejected(self) -> Path:
        return self.output / OUT_REJECTED

    @property
    def templates(self) -> Path:
        return self.root / TEMPLATES

    @property
    def data(self) -> Path:
        return self.root / DATA

    @property
    def db_path(self) -> Path:
        return self.data / "coolplay.sqlite3"

    @property
    def thumbs(self) -> Path:
        return self.data / "thumbs"

    @property
    def models(self) -> Path:
        return self.data / "models"

    @property
    def temp(self) -> Path:
        return self.root / TEMP

    @property
    def logs(self) -> Path:
        return self.root / LOGS

    def abs(self, rel_path: str) -> Path:
        return self.root / Path(rel_path)

    def rel(self, path: str | Path) -> str:
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()

    def ensure(self, cities: list[str]) -> None:
        """创建完整目录结构（已存在则跳过）。"""
        dirs = [
            self.pending, self.used, self.backup, self.music, self.fonts, self.brand,
            self.out_pending, self.out_approved, self.out_rejected, self.templates,
            self.data, self.thumbs, self.models, self.temp, self.logs,
        ]
        for city in cities:
            dirs += [self.pending / city, self.used / city]
        for style in DEFAULT_MUSIC_STYLES:
            dirs.append(self.music / style)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        # 内置字体复制到素材库 字体/
        src_fonts = ASSETS_DIR / "fonts"
        if src_fonts.is_dir():
            for f in src_fonts.iterdir():
                dst = self.fonts / f.name
                if f.is_file() and not dst.exists():
                    shutil.copy2(f, dst)
        # 内置模板复制到 模板/
        if BUILTIN_TEMPLATES_DIR.is_dir():
            for f in BUILTIN_TEMPLATES_DIR.glob("*.yaml"):
                dst = self.templates / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)


def _exe(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    vendored = VENDOR_FFMPEG_DIR / exe
    if vendored.is_file():
        return str(vendored)
    vendored_bin = VENDOR_FFMPEG_DIR / "bin" / exe
    if vendored_bin.is_file():
        return str(vendored_bin)
    found = shutil.which(name)
    return found or exe


def ffmpeg_bin() -> str:
    return _exe("ffmpeg")


def ffprobe_bin() -> str:
    return _exe("ffprobe")


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)
