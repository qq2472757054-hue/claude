"""端到端：合成素材 → 扫描 → 分析 → 生成 → 校验 → 素材流转。"""
import json
import threading

import pytest

from app.analysis.worker import analyze_basic
from app.ffrun import Cancelled, ffmpeg
from app.jobs import runner
from app.jobs.queue import recover
from app.library import scanner
from app.templates import schema
from tools.make_demo_library import make_image, make_music, make_video

TEMPLATE = """
name: 测试短片
cities: [张家界]
aspects: ["9:16", "1:1"]
duration: 10
subtitle_language: bilingual
transition: { type: fade, duration: 0.3 }
fill_mode: blur
music: { style: 轻快, volume: 0.6 }
brand: { logo_corner: true, end_card: true }
text: { hook: "Hello Zhangjiajie", hook_zh: "你好张家界", cta: "DM us", cta_zh: "私信我们" }
sections:
  - { name: 开场, types: [景点], count: 2, seconds: [2, 2.5], caption: "Big views", caption_zh: "大景" }
  - { name: 讲解, types: [导游出镜], count: 1, seconds: [2, 4], keep_audio: true }
cover: { enabled: true, title: "张家界 Test" }
"""


@pytest.fixture
def lib(ctx):
    root = ctx.lib.root
    make_video(root / "待使用/张家界/天门山/景 1.mp4", 6, "640x360", 1, "none", seed=1)
    make_image(root / "待使用/张家界/天门山/图 2.jpg", seed=2)
    make_video(root / "待使用/张家界/导游 3.mp4", 6, "360x640", 1, "tone", seed=3)
    make_video(root / "待使用/张家界/私密 4.mp4", 6, "640x360", 1, "none", seed=4)
    make_music(root / "配乐/轻快/song.mp3", 20)
    make_music(root / "配乐/轻快/未授权.mp3", 20, seed=9)
    scanner.scan(ctx)
    scanner.scan_music(ctx)
    for m in ctx.db.all("SELECT * FROM material"):
        analyze_basic(ctx, m)
    types = {"景 1": "景点", "图 2": "景点", "导游 3": "导游出镜", "私密 4": "景点"}
    for name, t in types.items():
        pub = "否" if name == "私密 4" else "是"
        ctx.db.execute("UPDATE material SET type=?, publishable=? WHERE rel_path LIKE ?", (t, pub, f"%{name}%"))
    ctx.db.execute("UPDATE clip SET low_quality=0")
    ctx.db.execute("UPDATE music SET commercial_ok='是' WHERE rel_path LIKE '%song.mp3'")
    g = ctx.db.one("SELECT id FROM material WHERE type='导游出镜'")
    ctx.db.insert("transcript", {"material_id": g["id"], "language": "zh", "segments_json": json.dumps(
        [{"start": 0.5, "end": 2.8, "text": "大家好，欢迎来到张家界"}], ensure_ascii=False)})
    (ctx.lib.templates / "测试短片.yaml").write_text(TEMPLATE, encoding="utf-8")
    return ctx


def new_job(ctx, **kw):
    row = {"batch_id": "", "template_name": "测试短片", "template_file": "模板/测试短片.yaml", "city": "张家界",
           "aspect": "9:16,1:1", "seed": 7, "status": "排队中", "overrides_json": "{}"}
    row.update(kw)
    return ctx.db.insert("job", row)


def statuses(ctx):
    return {r["rel_path"].split("/")[-1]: r["status"] for r in ctx.db.all("SELECT rel_path, status FROM material")}


def test_full_job(lib):
    ctx = lib
    ids = runner.run_job(ctx, new_job(ctx), threading.Event())
    assert len(ids) == 2
    for oid in ids:
        o = ctx.db.one("SELECT * FROM output WHERE id=?", (oid,))
        folder = ctx.lib.abs(o["file_path"]).parent
        assert folder.parent == ctx.lib.out_pending
        for f in ("video.mp4", "cover_9x16.jpg", "cover_16x9.jpg", "subtitles.srt", "caption.txt", "info.json"):
            assert (folder / f).is_file(), f
        info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
        assert info["seed"] == 7 and info["music"].endswith("song.mp3")
        srt = (folder / "subtitles.srt").read_text(encoding="utf-8")
        assert "你好张家界" in srt and "大家好" in srt
        # 响度 -14 LUFS ±1
        r = ffmpeg(["-i", str(folder / "video.mp4"), "-af", "ebur128", "-f", "null", "-"])
        integrated = [l for l in r.stderr.splitlines() if l.strip().startswith("I:")][-1]
        assert abs(float(integrated.split()[1]) + 14) <= 1.0
        # 不能公开的素材永远不出现
        assert all("私密" not in m["rel_path"] for m in info["materials"])
        assert "未授权" not in (info["music"] or "")
    st = statuses(ctx)
    assert st["私密 4.mp4"] == "待使用"
    assert st["导游 3.mp4"] == "已使用"
    assert not (ctx.lib.temp / "job_1").exists()


def test_cancel_leaves_materials(lib, monkeypatch):
    ctx = lib
    before = statuses(ctx)
    cancel = threading.Event()
    import app.render.engine as engine
    orig = engine.render_aspect

    def cancelling(**kw):
        cancel.set()
        return orig(**kw)

    monkeypatch.setattr(runner, "render_aspect", cancelling)
    with pytest.raises(Cancelled):
        runner.run_job(ctx, new_job(ctx), cancel)
    assert statuses(ctx) == before
    assert ctx.db.scalar("SELECT COUNT(*) FROM output") == 0


def test_shortage_fails_without_moving(lib):
    ctx = lib
    t = schema.load(ctx.lib.templates / "测试短片.yaml")
    t.sections[0].count = 9
    schema.save(t, ctx.lib.templates / "测试短片.yaml")
    before = statuses(ctx)
    with pytest.raises(runner.JobError) as e:
        runner.run_job(ctx, new_job(ctx), threading.Event())
    assert "张家界·景点" in str(e.value) and "还差" in str(e.value)
    assert statuses(ctx) == before


def test_crash_recovery(lib):
    ctx = lib
    jid = new_job(ctx, status="渲染中")
    (ctx.lib.temp / f"job_{jid}").mkdir(parents=True)
    (ctx.lib.temp / f"job_{jid}" / "seg.mp4").write_bytes(b"x")
    notes = recover(ctx)
    assert ctx.db.one("SELECT status FROM job WHERE id=?", (jid,))["status"] == "失败"
    assert not (ctx.lib.temp / f"job_{jid}").exists()
    assert notes
