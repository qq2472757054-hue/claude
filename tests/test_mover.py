import os
import shutil

import pytest

from app.library import mover, scanner


def add_file(ctx, rel, content=b"x"):
    p = ctx.lib.abs(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


@pytest.fixture
def two(ctx):
    add_file(ctx, "待使用/张家界/天门山/a 1.mp4", b"aaa")
    add_file(ctx, "待使用/重庆/b.jpg", b"bbbb")
    rep = scanner.scan(ctx)
    assert len(rep.new) == 2
    return ctx


def mid(ctx, name):
    return ctx.db.one("SELECT id FROM material WHERE rel_path LIKE ?", (f"%{name}",))["id"]


def test_scan_backup_tags_and_dedupe(two):
    ctx = two
    m = ctx.db.one("SELECT * FROM material WHERE rel_path = '待使用/张家界/天门山/a 1.mp4'")
    assert m["city"] == "张家界" and m["spot"] == "天门山" and m["publishable"] == "未确认"
    assert ctx.lib.abs(m["backup_path"]).read_bytes() == b"aaa"
    add_file(ctx, "待使用/成都/copy.mp4", b"aaa")
    rep = scanner.scan(ctx)
    assert rep.duplicates and not rep.new


def test_move_keeps_structure_and_db_first(two):
    ctx = two
    i = mid(ctx, "a 1.mp4")
    dst = mover.move_material(ctx, i, "已使用")
    assert dst == "已使用/张家界/天门山/a 1.mp4"
    assert ctx.lib.abs(dst).exists() and not ctx.lib.abs("待使用/张家界/天门山/a 1.mp4").exists()
    row = ctx.db.one("SELECT status, rel_path FROM material WHERE id = ?", (i,))
    assert row == {"status": "已使用", "rel_path": dst}
    log = ctx.db.one("SELECT * FROM move_log WHERE material_id = ?", (i,))
    assert log["ok"] == 1


def test_move_failure_rolls_back_db(two, monkeypatch):
    ctx = two
    i = mid(ctx, "b.jpg")

    def boom(*a, **k):
        raise OSError("磁盘被拔掉了")

    monkeypatch.setattr(os, "rename", boom)
    monkeypatch.setattr(shutil, "move", boom)
    with pytest.raises(mover.MoveError):
        mover.move_material(ctx, i, "已使用")
    row = ctx.db.one("SELECT status, rel_path FROM material WHERE id = ?", (i,))
    assert row == {"status": "待使用", "rel_path": "待使用/重庆/b.jpg"}
    assert ctx.lib.abs("待使用/重庆/b.jpg").exists()


def test_name_collision_gets_suffix(two):
    ctx = two
    add_file(ctx, "已使用/重庆/b.jpg", b"other")
    dst = mover.move_material(ctx, mid(ctx, "b.jpg"), "已使用")
    assert dst == "已使用/重庆/b_2.jpg"


def test_consistency_check_follows_real_file(two):
    ctx = two
    i = mid(ctx, "a 1.mp4")
    # 模拟“数据库已改、文件还没移动”时被强制关闭
    ctx.db.update("material", i, {"rel_path": "已使用/张家界/天门山/a 1.mp4", "status": "已使用"})
    notes = mover.consistency_check(ctx)
    row = ctx.db.one("SELECT status, rel_path FROM material WHERE id = ?", (i,))
    assert row["status"] == "待使用" and row["rel_path"] == "待使用/张家界/天门山/a 1.mp4"
    assert notes
    ctx.lib.abs(row["rel_path"]).unlink()
    mover.consistency_check(ctx)
    assert ctx.db.one("SELECT status FROM material WHERE id = ?", (i,))["status"] == "丢失"


def test_record_success_and_return(two):
    ctx = two
    a, b = mid(ctx, "a 1.mp4"), mid(ctx, "b.jpg")
    ca1 = ctx.db.insert("clip", {"material_id": a, "start": 0, "end": 3})
    ctx.db.insert("clip", {"material_id": a, "start": 3, "end": 6})
    cb = ctx.db.insert("clip", {"material_id": b, "start": 0, "end": 0})
    oid = ctx.db.insert("output", {"job_id": 7, "file_path": "输出/待审核/x/video.mp4"})
    res = mover.record_success(ctx, 7, [oid], [(a, ca1), (b, cb)], "all_clips_used")
    # a 还有一个片段没用过 → 不移动；b 只有一个片段 → 移动
    assert ctx.db.one("SELECT status FROM material WHERE id=?", (a,))["status"] == "待使用"
    assert ctx.db.one("SELECT status FROM material WHERE id=?", (b,))["status"] == "已使用"
    assert res.moved == ["已使用/重庆/b.jpg"]
    assert ctx.db.scalar("SELECT COUNT(*) FROM usage WHERE output_id=?", (oid,)) == 2
    back = mover.return_to_pending(ctx, oid)
    assert back.moved == ["待使用/重庆/b.jpg"]
    assert ctx.db.one("SELECT status FROM material WHERE id=?", (b,))["status"] == "待使用"


def test_any_clip_rule_moves(two):
    ctx = two
    a = mid(ctx, "a 1.mp4")
    c1 = ctx.db.insert("clip", {"material_id": a, "start": 0, "end": 3})
    ctx.db.insert("clip", {"material_id": a, "start": 3, "end": 6})
    mover.record_success(ctx, 1, [], [(a, c1)], "any_clip_used")
    assert ctx.db.one("SELECT status FROM material WHERE id=?", (a,))["status"] == "已使用"
