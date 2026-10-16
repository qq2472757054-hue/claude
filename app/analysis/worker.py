"""后台分析：基础信息 → 切镜 → 画质 → 缩略图 → 语音检测与识别。可暂停。"""
from __future__ import annotations

import json
import threading
import time

from .. import logs, state
from ..asr import base as asr_base
from ..asr.base import ModelNotReady
from ..db import now
from . import probe, quality, scenes, thumbs

log = logs.get("analysis")


def analyze_basic(ctx: state.Ctx, m: dict) -> None:
    path = ctx.lib.abs(m["rel_path"])
    is_image = bool(m["is_image"])
    info = probe.basic_info(path, is_image)
    if info.get("broken"):
        ctx.db.update("material", m["id"], {"broken": 1, "analysis_status": "失败",
                                            "updated_at": now()})
        log.warning("无法读取素材: %s", m["rel_path"])
        return
    ctx.db.update("material", m["id"], {**info, "updated_at": now()})
    if is_image:
        spans = [(0.0, 0.0)]
    else:
        spans = scenes.detect(path, info["duration"])
    with ctx.db.connect() as c:
        c.execute("DELETE FROM clip WHERE material_id = ?", (m["id"],))
    for i, (s, e) in enumerate(spans):
        q = quality.score_clip(path, s, e, is_image)
        thumb_rel = f"数据/thumbs/{m['id']}_{i}.jpg"
        thumbs.make_thumb(path, ctx.lib.abs(thumb_rel), (s + e) / 2, is_image)
        ctx.db.insert("clip", {
            "material_id": m["id"], "start": s, "end": e, "sharpness": q.sharpness,
            "brightness": q.brightness, "motion": q.motion, "low_quality": int(q.low_quality),
            "quality_reason": q.reason, "thumb_path": thumb_rel,
        })
    asr_status = "" if (not is_image and info.get("has_audio")) else ("无音轨" if not is_image else "")
    ctx.db.update("material", m["id"], {"analysis_status": "已分析", "asr_status": asr_status,
                                        "updated_at": now()})


def analyze_speech(ctx: state.Ctx, m: dict, cancel: threading.Event | None = None) -> str:
    """返回新的 asr_status。"""
    if ctx.db.one("SELECT id FROM transcript WHERE material_id = ?", (m["id"],)):
        return "已识别"
    engine = asr_base.create(ctx.settings, ctx.lib.models)
    path = ctx.lib.abs(m["rel_path"])
    if not engine.detect_speech(path):
        return "无人声"
    res = engine.transcribe(path, cancel)
    if cancel is not None and cancel.is_set():
        return ""
    if not res.segments:
        return "无人声"
    ctx.db.insert("transcript", {
        "material_id": m["id"], "language": res.language,
        "segments_json": json.dumps(res.segments_dicts(), ensure_ascii=False),
        "edited_text_json": "", "model": res.model, "created_at": now(),
    })
    return "已识别"


class AnalysisWorker:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self.current = ""
        self.last_error = ""
        self.model_missing_hint = ""

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self._wake.set()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="analysis", daemon=True)
        self._thread.start()

    def kick(self) -> None:
        self.start()
        self._wake.set()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def status(self) -> dict:
        ctx = state.get()
        pending = asr_pending = done = 0
        if ctx:
            pending = ctx.db.scalar("SELECT COUNT(*) FROM material WHERE analysis_status='待分析'")
            asr_pending = ctx.db.scalar(
                "SELECT COUNT(*) FROM material WHERE analysis_status='已分析' AND is_image=0 "
                "AND has_audio=1 AND asr_status=''")
            done = ctx.db.scalar("SELECT COUNT(*) FROM material WHERE analysis_status!='待分析'")
        return {"pending": pending or 0, "asr_pending": asr_pending or 0, "done": done or 0,
                "paused": self.paused, "current": self.current, "last_error": self.last_error,
                "model_hint": self.model_missing_hint}

    def _next(self, ctx):
        m = ctx.db.one("SELECT * FROM material WHERE analysis_status='待分析' ORDER BY id LIMIT 1")
        if m:
            return "basic", m
        if self.model_missing_hint:
            return None, None
        m = ctx.db.one("SELECT * FROM material WHERE analysis_status='已分析' AND is_image=0 "
                       "AND has_audio=1 AND broken=0 AND asr_status='' AND status!='丢失' "
                       "ORDER BY id LIMIT 1")
        if m:
            return "asr", m
        return None, None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._pause.is_set():
                self.current = ""
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            ctx = state.get()
            if ctx is None:
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            kind, m = self._next(ctx)
            if m is None:
                self.current = ""
                self._wake.wait(5.0)
                self._wake.clear()
                continue
            try:
                if kind == "basic":
                    self.current = f"分析 {m['rel_path']}"
                    analyze_basic(ctx, m)
                else:
                    self.current = f"语音识别 {m['rel_path']}"
                    st = analyze_speech(ctx, m, self._pause)
                    if st:
                        ctx.db.update("material", m["id"], {"asr_status": st})
            except ModelNotReady as e:
                self.model_missing_hint = str(e)
                log.warning("语音模型未就绪，跳过识别")
            except Exception as e:
                log.exception("分析失败 %s", m["rel_path"])
                self.last_error = f"{m['rel_path']}：{e}"
                if kind == "basic":
                    ctx.db.update("material", m["id"], {"analysis_status": "失败"})
                else:
                    ctx.db.update("material", m["id"], {"asr_status": "失败"})
            time.sleep(0.01)

    def model_ready_again(self) -> None:
        self.model_missing_hint = ""
        self.kick()


worker = AnalysisWorker()
