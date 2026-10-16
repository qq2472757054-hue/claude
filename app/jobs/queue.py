"""任务队列：串行执行，可取消、可重试；启动时恢复。"""
from __future__ import annotations

import json
import random
import shutil
import threading
import time
import uuid

from .. import logs, state
from ..db import now
from ..ffrun import Cancelled
from . import runner

log = logs.get("queue")

ACTIVE = ("挑选素材", "渲染中", "校验中")


class JobQueue:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel: dict[int, threading.Event] = {}
        self.current: int | None = None
        self.last_finished: list[str] = []      # 给界面弹提示用
        self._lock = threading.Lock()

    # ----- 提交 -----
    def submit(self, ctx, template_file: str, template_name: str, city: str, aspects: list[str],
               count: int, overrides: dict | None = None, batch_id: str | None = None) -> list[int]:
        batch_id = batch_id if batch_id is not None else uuid.uuid4().hex[:10]
        ids = []
        for _ in range(count):
            ids.append(ctx.db.insert("job", {
                "batch_id": batch_id, "template_name": template_name, "template_file": template_file,
                "city": city, "aspect": ",".join(aspects), "seed": random.randint(1, 2**31 - 1),
                "status": "排队中", "overrides_json": json.dumps(overrides or {}, ensure_ascii=False),
                "created_at": now()}))
        self.start()
        self._wake.set()
        return ids

    def cancel(self, ctx, job_id: int) -> str:
        j = ctx.db.one("SELECT status FROM job WHERE id = ?", (job_id,))
        if not j:
            return "任务不存在"
        if j["status"] == "排队中":
            ctx.db.update("job", job_id, {"status": "已取消", "finished_at": now()})
            return f"任务 {job_id} 已取消"
        if j["status"] in ACTIVE:
            ev = self._cancel.get(job_id)
            if ev:
                ev.set()
            return f"正在停止任务 {job_id}…"
        return f"任务 {job_id} 当前状态为“{j['status']}”，无需取消"

    def retry(self, ctx, job_id: int) -> str:
        j = ctx.db.one("SELECT * FROM job WHERE id = ?", (job_id,))
        if not j or j["status"] not in ("失败", "已取消"):
            return "只有失败或已取消的任务可以重试"
        new_id = ctx.db.insert("job", {
            "batch_id": j["batch_id"], "template_name": j["template_name"],
            "template_file": j["template_file"], "city": j["city"], "aspect": j["aspect"],
            "seed": random.randint(1, 2**31 - 1), "status": "排队中",
            "overrides_json": j["overrides_json"], "created_at": now()})
        self.start()
        self._wake.set()
        return f"已重新排队（新任务 {new_id}）"

    # ----- 执行 -----
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="jobs", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for ev in self._cancel.values():
            ev.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ctx = state.get()
            job = ctx.db.one("SELECT * FROM job WHERE status = '排队中' ORDER BY id LIMIT 1") if ctx else None
            if job is None:
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            self._run_one(ctx, job)

    def _run_one(self, ctx, job: dict) -> None:
        jid = job["id"]
        cancel = threading.Event()
        self._cancel[jid] = cancel
        self.current = jid
        t0 = time.monotonic()
        try:
            runner.run_job(ctx, jid, cancel)
            ctx.db.update("job", jid, {"status": "完成", "progress": f"用时 {time.monotonic() - t0:.0f} 秒",
                                       "finished_at": now()})
            self.last_finished.append(f"任务 {jid}（{job['template_name']}·{job['city']}）生成完成")
            log.info("任务 %s 完成，用时 %.0f 秒", jid, time.monotonic() - t0)
        except Cancelled:
            ctx.db.update("job", jid, {"status": "已取消", "progress": "", "finished_at": now()})
            shutil.rmtree(ctx.lib.temp / f"job_{jid}", ignore_errors=True)
            log.info("任务 %s 已取消，临时文件已清理，素材未移动", jid)
        except Exception as e:
            log.exception("任务 %s 失败", jid)
            msg = runner.friendly_error(e)
            ctx.db.update("job", jid, {"status": "失败", "error": msg, "progress": "", "finished_at": now()})
            self.last_finished.append(f"任务 {jid} 失败：{msg}")
            work = ctx.lib.temp / f"job_{jid}"
            if ctx.settings["render"].get("keep_failed_temp", True) and work.exists():
                kept = ctx.lib.temp / f"失败_job_{jid}"
                shutil.rmtree(kept, ignore_errors=True)
                try:
                    work.rename(kept)
                except OSError:
                    pass
            else:
                shutil.rmtree(work, ignore_errors=True)
        finally:
            self._cancel.pop(jid, None)
            self.current = None

    def pop_notices(self) -> list[str]:
        out, self.last_finished = self.last_finished, []
        return out


def recover(ctx) -> list[str]:
    """启动时：中断的任务标记失败，清理临时目录（保留 失败_* 供排查，7 天后清理）。"""
    notes = []
    for j in ctx.db.all(f"SELECT id FROM job WHERE status IN ({','.join('?' * len(ACTIVE))})", ACTIVE):
        outs = ctx.db.all("SELECT id FROM output WHERE job_id = ?", (j["id"],))
        if outs:
            # 输出已写好、素材还没移动时被关闭：补做素材流转
            try:
                plan = json.loads(ctx.db.scalar("SELECT plan_json FROM job WHERE id = ?", (j["id"],)) or "{}")
                used = [(c["material_id"], c["clip_id"]) for c in plan["timeline"]["choices"]]
                if not ctx.db.scalar("SELECT COUNT(*) FROM usage WHERE job_id = ?", (j["id"],)):
                    from ..library import mover
                    mover.record_success(ctx, j["id"], [o["id"] for o in outs], used,
                                         ctx.settings["render"].get("move_rule", "any_clip_used"))
                ctx.db.update("job", j["id"], {"status": "完成", "finished_at": now()})
                notes.append(f"任务 {j['id']} 的输出已生成，已补做素材流转")
                continue
            except (KeyError, json.JSONDecodeError):
                pass
        ctx.db.update("job", j["id"], {"status": "失败", "error": "程序被关闭，任务中断（素材未移动），可点“重试”",
                                       "finished_at": now()})
        notes.append(f"任务 {j['id']} 在程序关闭时被中断，已标记为失败，素材未移动")
    if ctx.lib.temp.is_dir():
        cutoff = time.time() - 7 * 86400
        for p in ctx.lib.temp.iterdir():
            if p.name.startswith("失败_") and p.stat().st_mtime > cutoff:
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    # 写到一半的输出目录
    if ctx.lib.out_pending.is_dir():
        for p in ctx.lib.out_pending.iterdir():
            if p.name.startswith(".") and p.name.endswith(".part"):
                shutil.rmtree(p, ignore_errors=True)
    return notes


queue = JobQueue()
