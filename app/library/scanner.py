"""扫描 待使用/：去重、备份、入库。"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import logs
from ..db import now
from ..paths import IMAGE_EXTS, PENDING, USED, VIDEO_EXTS, MUSIC_EXTS
from ..state import Ctx

log = logs.get("scanner")


@dataclass
class ScanReport:
    new: list[str] = field(default_factory=list)
    duplicates: list[tuple[str, str]] = field(default_factory=list)   # (新文件, 已有文件)
    relocated: list[tuple[str, str]] = field(default_factory=list)    # (旧位置, 新位置)
    errors: list[tuple[str, str]] = field(default_factory=list)
    skipped: int = 0

    def summary(self) -> str:
        parts = [f"新入库 {len(self.new)} 个"]
        if self.duplicates:
            parts.append(f"重复文件 {len(self.duplicates)} 个（未入库）")
        if self.relocated:
            parts.append(f"位置更新 {len(self.relocated)} 个")
        if self.errors:
            parts.append(f"出错 {len(self.errors)} 个")
        lines = ["，".join(parts) + "。"]
        for new, old in self.duplicates[:20]:
            lines.append(f"· 重复：{new}（与 {old} 内容相同）")
        for p, e in self.errors[:20]:
            lines.append(f"· 出错：{p}：{e}")
        return "\n".join(lines)


def sha256_file(path: str | Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def is_media(path: Path) -> bool:
    ext = path.suffix.lower()
    return ext in VIDEO_EXTS or ext in IMAGE_EXTS


def iter_media(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or name.startswith("~$"):
                continue
            p = Path(dirpath) / name
            if is_media(p):
                yield p


def tags_from_rel(rel_path: str) -> tuple[str, str]:
    """待使用/<城市>/<景点>/.../文件 → (城市, 景点)。"""
    parts = Path(rel_path).parts
    city = parts[1] if len(parts) > 2 else ""
    spot = parts[2] if len(parts) > 3 else ""
    return city, spot


def backup_rel(sha: str, filename: str) -> str:
    return f"原始备份/{sha[:12]}/{filename}"


def backup_file(ctx: Ctx, src: Path, sha: str) -> str:
    rel = backup_rel(sha, src.name)
    dst = ctx.lib.abs(rel)
    size = src.stat().st_size
    if dst.exists() and dst.stat().st_size == size:
        return rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".copying")
    shutil.copy2(src, tmp)
    if tmp.stat().st_size != size:
        tmp.unlink(missing_ok=True)
        raise OSError("备份后文件大小不一致")
    os.replace(tmp, dst)
    return rel


def scan(ctx: Ctx, progress: Callable[[float, str], None] | None = None) -> ScanReport:
    report = ScanReport()
    db = ctx.db
    known = {r["rel_path"]: r for r in db.all("SELECT id, rel_path, size, sha256 FROM material")}
    files = list(iter_media(ctx.lib.pending))
    total = max(len(files), 1)
    for i, path in enumerate(files):
        rel = ctx.lib.rel(path)
        if progress:
            progress(i / total, f"扫描 {rel}")
        try:
            size = path.stat().st_size
            k = known.get(rel)
            if k and k["size"] == size:
                report.skipped += 1
                continue
            sha = sha256_file(path)
            existing = db.one("SELECT * FROM material WHERE sha256 = ?", (sha,))
            if existing:
                old_abs = ctx.lib.abs(existing["rel_path"])
                if existing["rel_path"] == rel:
                    db.update("material", existing["id"], {"size": size})
                elif not old_abs.exists():
                    # 用户手动挪了位置：以实际文件为准
                    status = PENDING
                    db.update("material", existing["id"],
                              {"rel_path": rel, "status": status, "updated_at": now()})
                    report.relocated.append((existing["rel_path"], rel))
                else:
                    report.duplicates.append((rel, existing["rel_path"]))
                continue
            brel = backup_file(ctx, path, sha)
            city, spot = tags_from_rel(rel)
            is_image = path.suffix.lower() in IMAGE_EXTS
            db.insert("material", {
                "sha256": sha, "rel_path": rel, "status": PENDING, "city": city, "spot": spot,
                "type": "", "publishable": "未确认", "is_image": int(is_image), "size": size,
                "backup_path": brel, "analysis_status": "待分析", "created_at": now(),
                "updated_at": now(),
            })
            report.new.append(rel)
        except Exception as e:  # 单个文件出错不影响其他文件
            log.exception("扫描出错 %s", rel)
            report.errors.append((rel, str(e)))
    if progress:
        progress(1.0, "扫描完成")
    log.info("扫描结果: %s", report.summary().splitlines()[0])
    return report


def scan_music(ctx: Ctx) -> int:
    """登记 配乐/ 下的曲目，子文件夹名即风格。返回新登记数量。"""
    db = ctx.db
    known = {r["rel_path"] for r in db.all("SELECT rel_path FROM music")}
    found_rel: set[str] = set()
    added = 0
    for dirpath, _dirs, files in os.walk(ctx.lib.music):
        for name in sorted(files):
            p = Path(dirpath) / name
            if p.suffix.lower() not in MUSIC_EXTS:
                continue
            rel = ctx.lib.rel(p)
            found_rel.add(rel)
            if rel in known:
                continue
            sha = sha256_file(p)
            parts = Path(rel).parts
            style = parts[1] if len(parts) > 2 else ""
            row = db.one("SELECT id, rel_path FROM music WHERE sha256 = ?", (sha,))
            if row and ctx.lib.abs(row["rel_path"]).is_file():
                continue  # 内容重复的曲目只登记一次
            if row:
                db.update("music", row["id"], {"rel_path": rel, "style": style, "missing": 0})
                continue
            duration = 0.0
            try:
                from ..ffrun import probe
                duration = probe(p)["duration"]
            except Exception:
                log.warning("读取配乐时长失败: %s", rel)
            db.insert("music", {"sha256": sha, "rel_path": rel, "style": style,
                                "commercial_ok": "未确认", "duration": duration})
            added += 1
    for r in db.all("SELECT id, rel_path FROM music"):
        db.update("music", r["id"], {"missing": 0 if r["rel_path"] in found_rel else 1})
    return added


__all__ = ["scan", "scan_music", "sha256_file", "ScanReport", "tags_from_rel", "USED"]
