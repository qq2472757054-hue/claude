"""SQLite 数据库：表结构与简单访问封装。"""
from __future__ import annotations

import contextlib
import datetime as _dt
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS material (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    rel_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待使用',          -- 待使用 / 已使用 / 丢失
    city TEXT DEFAULT '',
    spot TEXT DEFAULT '',
    type TEXT DEFAULT '',
    service TEXT DEFAULT '',
    publishable TEXT NOT NULL DEFAULT '未确认',      -- 是 / 否 / 未确认
    note TEXT DEFAULT '',
    rating INTEGER DEFAULT 0,
    duration REAL DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    fps REAL DEFAULT 0,
    rotation INTEGER DEFAULT 0,
    has_audio INTEGER DEFAULT 0,
    vcodec TEXT DEFAULT '',
    is_image INTEGER DEFAULT 0,
    broken INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0,
    backup_path TEXT DEFAULT '',
    analysis_status TEXT DEFAULT '待分析',           -- 待分析 / 已分析 / 失败
    asr_status TEXT DEFAULT '',                     -- '' / 无人声 / 已识别 / 失败 / 无音轨
    use_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_material_status ON material(status);

CREATE TABLE IF NOT EXISTS clip (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES material(id) ON DELETE CASCADE,
    start REAL NOT NULL,
    "end" REAL NOT NULL,
    sharpness REAL DEFAULT 0,
    brightness REAL DEFAULT 0,
    motion REAL DEFAULT 0,
    low_quality INTEGER DEFAULT 0,
    quality_reason TEXT DEFAULT '',
    manual_restore INTEGER DEFAULT 0,
    thumb_path TEXT DEFAULT '',
    use_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_clip_material ON clip(material_id);

CREATE TABLE IF NOT EXISTS transcript (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL UNIQUE REFERENCES material(id) ON DELETE CASCADE,
    language TEXT DEFAULT '',
    segments_json TEXT DEFAULT '[]',
    edited_text_json TEXT DEFAULT '',
    model TEXT DEFAULT '',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS music (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    rel_path TEXT NOT NULL,
    style TEXT DEFAULT '',
    source TEXT DEFAULT '',
    license_note TEXT DEFAULT '',
    commercial_ok TEXT NOT NULL DEFAULT '未确认',    -- 是 / 否 / 未确认
    duration REAL DEFAULT 0,
    use_count INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS template (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT DEFAULT '',
    template_id INTEGER,
    template_name TEXT DEFAULT '',
    template_file TEXT DEFAULT '',
    city TEXT DEFAULT '',
    aspect TEXT DEFAULT '',                 -- 逗号分隔，一条任务可出多个比例
    seed INTEGER,
    status TEXT NOT NULL DEFAULT '排队中',  -- 排队中/挑选素材/渲染中/校验中/完成/失败/已取消
    progress TEXT DEFAULT '',
    error TEXT DEFAULT '',
    warnings TEXT DEFAULT '',
    plan_json TEXT DEFAULT '',
    overrides_json TEXT DEFAULT '',
    created_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS output (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'video',     -- video / poster
    name TEXT DEFAULT '',
    city TEXT DEFAULT '',
    template_name TEXT DEFAULT '',
    aspect TEXT DEFAULT '',
    file_path TEXT NOT NULL,                -- 相对素材库根目录
    review_status TEXT NOT NULL DEFAULT '待审核',
    reject_reason TEXT DEFAULT '',
    title TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    hashtags TEXT DEFAULT '',
    srt_path TEXT DEFAULT '',
    cover_paths TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL,
    clip_id INTEGER,
    output_id INTEGER,
    job_id INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_material ON usage(material_id);
CREATE INDEX IF NOT EXISTS idx_usage_job ON usage(job_id);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS move_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER,
    src TEXT,
    dst TEXT,
    ok INTEGER,
    message TEXT,
    created_at TEXT
);
"""


def now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as c:
            c.executescript(SCHEMA)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """一个事务：正常退出提交，异常回滚。"""
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def all(self, sql: str, params: tuple | list | dict = ()) -> list[dict[str, Any]]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list | dict = ()) -> dict[str, Any] | None:
        with self.connect() as c:
            r = c.execute(sql, params).fetchone()
            return dict(r) if r else None

    def scalar(self, sql: str, params: tuple | list | dict = ()) -> Any:
        with self.connect() as c:
            r = c.execute(sql, params).fetchone()
            return r[0] if r else None

    def execute(self, sql: str, params: tuple | list | dict = ()) -> int:
        """执行一条写语句，返回 lastrowid。"""
        with self.connect() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid

    def insert(self, table: str, row: dict[str, Any]) -> int:
        cols = ", ".join(f'"{k}"' for k in row)
        qs = ", ".join("?" for _ in row)
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(row.values()))

    def update(self, table: str, row_id: int, changes: dict[str, Any]) -> None:
        if not changes:
            return
        sets = ", ".join(f'"{k}" = ?' for k in changes)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*changes.values(), row_id))
