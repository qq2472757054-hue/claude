"""素材流转（开发文档 6.12）。

规则：
1. 只在任务成功后移动素材；失败/取消不动。
2. 先写数据库、再移动文件；文件移动失败则回滚数据库并记录日志。
3. 启动时一致性检查：以实际文件位置为准修正数据库。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import logs
from ..db import now
from ..paths import PENDING, USED
from ..state import Ctx

log = logs.get("mover")


class MoveError(RuntimeError):
    pass


def swap_root(rel_path: str, to_status: str) -> str:
    """待使用/重庆/a.mp4 → 已使用/重庆/a.mp4（保持城市/子文件夹结构）。"""
    parts = PurePosixPath(rel_path).parts
    if not parts or parts[0] not in (PENDING, USED):
        raise MoveError(f"素材不在 待使用/ 或 已使用/ 中：{rel_path}")
    return PurePosixPath(to_status, *parts[1:]).as_posix()


def _free_target(ctx: Ctx, rel: str) -> str:
    """目标位置已有同名文件时自动加序号。"""
    p = PurePosixPath(rel)
    candidate = rel
    n = 2
    while ctx.lib.abs(candidate).exists():
        candidate = p.with_name(f"{p.stem}_{n}{p.suffix}").as_posix()
        n += 1
    return candidate


def _log_move(ctx: Ctx, material_id: int, src: str, dst: str, ok: bool, msg: str = "") -> None:
    try:
        ctx.db.insert("move_log", {"material_id": material_id, "src": src, "dst": dst,
                                   "ok": int(ok), "message": msg, "created_at": now()})
    except Exception:
        log.exception("写移动日志失败")


def move_material(ctx: Ctx, material_id: int, to_status: str) -> str | None:
    """移动一个素材文件到 to_status（待使用/已使用）。返回新相对路径；无需移动返回 None。"""
    m = ctx.db.one("SELECT * FROM material WHERE id = ?", (material_id,))
    if m is None:
        raise MoveError(f"素材 {material_id} 不存在")
    if m["status"] == to_status and PurePosixPath(m["rel_path"]).parts[0] == to_status:
        return None
    src_rel = m["rel_path"]
    src = ctx.lib.abs(src_rel)
    if not src.exists():
        raise MoveError(f"找不到素材文件：{src_rel}")
    dst_rel = _free_target(ctx, swap_root(src_rel, to_status))
    dst = ctx.lib.abs(dst_rel)
    # 1) 先写数据库
    ctx.db.update("material", material_id,
                  {"rel_path": dst_rel, "status": to_status, "updated_at": now()})
    # 2) 再移动文件，失败回滚
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError:
            shutil.move(str(src), str(dst))
    except Exception as e:
        ctx.db.update("material", material_id,
                      {"rel_path": src_rel, "status": m["status"], "updated_at": now()})
        _log_move(ctx, material_id, src_rel, dst_rel, False, str(e))
        log.error("移动素材失败，已回滚数据库: %s -> %s: %s", src_rel, dst_rel, e)
        raise MoveError(f"移动素材失败：{src_rel}（{e}）") from e
    _log_move(ctx, material_id, src_rel, dst_rel, True)
    log.info("素材移动: %s -> %s", src_rel, dst_rel)
    return dst_rel


def should_move(ctx: Ctx, material_id: int, rule: str) -> bool:
    if rule == "all_clips_used":
        left = ctx.db.scalar(
            "SELECT COUNT(*) FROM clip WHERE material_id = ? AND use_count = 0 "
            "AND (low_quality = 0 OR manual_restore = 1)", (material_id,))
        return (left or 0) == 0
    return True


@dataclass
class MoveResult:
    moved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def record_success(ctx: Ctx, job_id: int, output_ids: list[int],
                   used: list[tuple[int, int | None]], rule: str) -> MoveResult:
    """任务成功后调用：写使用记录、累加次数、按规则把素材移到 已使用/。

    used: [(material_id, clip_id)]（图片可无 clip）
    """
    res = MoveResult()
    ts = now()
    with ctx.db.connect() as c:
        for oid in output_ids or [None]:
            for mid, cid in used:
                c.execute("INSERT INTO usage (material_id, clip_id, output_id, job_id, created_at) "
                          "VALUES (?, ?, ?, ?, ?)", (mid, cid, oid, job_id, ts))
        for cid in {cid for _m, cid in used if cid}:
            c.execute("UPDATE clip SET use_count = use_count + 1 WHERE id = ?", (cid,))
        for mid in {m for m, _c in used}:
            c.execute("UPDATE material SET use_count = use_count + 1 WHERE id = ?", (mid,))
    for mid in sorted({m for m, _c in used}):
        m = ctx.db.one("SELECT status FROM material WHERE id = ?", (mid,))
        if not m or m["status"] != PENDING or not should_move(ctx, mid, rule):
            continue
        try:
            dst = move_material(ctx, mid, USED)
            if dst:
                res.moved.append(dst)
        except MoveError as e:
            res.errors.append(str(e))
    return res


def return_to_pending(ctx: Ctx, output_id: int) -> MoveResult:
    """退回时把该条输出用过的素材从 已使用/ 移回 待使用/。"""
    res = MoveResult()
    out = ctx.db.one("SELECT job_id FROM output WHERE id = ?", (output_id,))
    rows = ctx.db.all("SELECT DISTINCT material_id FROM usage WHERE output_id = ?", (output_id,))
    if not rows and out and out["job_id"]:
        rows = ctx.db.all("SELECT DISTINCT material_id FROM usage WHERE job_id = ?", (out["job_id"],))
    for r in rows:
        mid = r["material_id"]
        m = ctx.db.one("SELECT status FROM material WHERE id = ?", (mid,))
        if not m or m["status"] != USED:
            continue
        try:
            dst = move_material(ctx, mid, PENDING)
            if dst:
                res.moved.append(dst)
        except MoveError as e:
            res.errors.append(str(e))
    return res


def consistency_check(ctx: Ctx) -> list[str]:
    """数据库记录与实际文件位置不一致时，以实际文件为准修正。返回给用户看的提示。"""
    notes: list[str] = []
    for m in ctx.db.all("SELECT id, rel_path, status, sha256, size FROM material"):
        rel = m["rel_path"]
        p = ctx.lib.abs(rel)
        top = PurePosixPath(rel).parts[0] if rel else ""
        if p.exists():
            if top in (PENDING, USED) and m["status"] != top:
                ctx.db.update("material", m["id"], {"status": top, "updated_at": now()})
                notes.append(f"状态修正：{rel} → {top}")
            continue
        # 文件不在记录位置：先看另一侧（可能是移动到一半被中断）
        found = None
        for other in (PENDING, USED):
            try:
                cand = swap_root(rel, other)
            except MoveError:
                continue
            if ctx.lib.abs(cand).exists():
                found = (cand, other)
                break
        if found:
            ctx.db.update("material", m["id"],
                          {"rel_path": found[0], "status": found[1], "updated_at": now()})
            notes.append(f"位置修正：{rel} → {found[0]}")
        elif m["status"] != "丢失":
            ctx.db.update("material", m["id"], {"status": "丢失", "updated_at": now()})
            notes.append(f"文件丢失：{rel}（原始备份仍在 原始备份/ 中）")
    for o in ctx.db.all("SELECT id, file_path, review_status FROM output"):
        p = ctx.lib.abs(o["file_path"])
        if not p.exists():
            fixed = _find_output(ctx, o["file_path"])
            if fixed:
                status = {"待审核": "待审核", "已审核": "已审核", "已退回": "已退回"}[Path(fixed).parts[1]]
                ctx.db.update("output", o["id"], {"file_path": fixed, "review_status": status})
                notes.append(f"输出位置修正：{fixed}")
    if notes:
        log.warning("一致性检查修正 %d 项:\n%s", len(notes), "\n".join(notes))
    return notes


def _find_output(ctx: Ctx, rel: str) -> str | None:
    parts = PurePosixPath(rel).parts
    if len(parts) < 3:
        return None
    for status in ("待审核", "已审核", "已退回"):
        cand = PurePosixPath(parts[0], status, *parts[2:]).as_posix()
        if ctx.lib.abs(cand).exists():
            return cand
    return None
