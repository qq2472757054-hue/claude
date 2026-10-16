"""生成页面：选模板 → 城市 → 比例 → 数量 → 开始生成；任务队列状态。"""
from __future__ import annotations

import gradio as gr

from ..jobs.queue import queue
from ..render.picker import load_candidates
from ..templates import schema
from . import common

JOB_HEADERS = ["任务", "模板", "城市", "比例", "状态", "进度", "提示 / 错误", "创建时间"]


def template_choices():
    c = common.ctx()
    if c is None:
        return []
    ts = schema.list_templates(c.lib.templates)
    return [(t.name, c.lib.rel(t.file_path)) for t in ts]


def _load(rel):
    c = common.need_ctx()
    if not rel:
        raise gr.Error("请先选择模板")
    return c, schema.load(c.lib.abs(rel))


def on_template(rel):
    if not rel:
        return gr.update(choices=[], value=None), gr.update(value=[]), ""
    c, t = _load(rel)
    cities = t.cities or common.cities()
    return (gr.update(choices=cities, value=cities[0] if cities else None),
            gr.update(value=t.aspects), describe(t))


def describe(t: schema.Template) -> str:
    lines = [f"**{t.name}**　目标 {t.duration:g} 秒　字幕：{schema.SUBTITLE_LANGS[t.subtitle_language]}　"
             f"转场：{schema.TRANSITIONS[t.transition_type]}　配乐风格：{t.music.get('style') or '—'}"]
    for i, s in enumerate(t.sections, 1):
        lines.append(f"{i}. {s.name}：{'/'.join(s.types)} × {s.count} 段，每段 {s.seconds[0]:g}–{s.seconds[1]:g} 秒"
                     + ("（保留原声）" if s.keep_audio else "") + (f"（景点：{'/'.join(s.spots)}）" if s.spots else ""))
    return "\n".join(lines)


def preflight(rel, city, count):
    """估算素材是否够用。"""
    if not rel or not city:
        return ""
    c, t = _load(rel)
    s = common.settings()
    cands = load_candidates(c, city, bool(s["render"].get("allow_used_materials")))
    lines = []
    for sec in t.sections:
        n = len({x.clip_id for x in cands if x.type in sec.types and (not sec.spots or x.spot in sec.spots)})
        need = sec.count
        mark = "✅" if n >= need * max(int(count or 1), 1) else ("⚠️" if n >= need else "❌")
        lines.append(f"{mark} {sec.name}（{'/'.join(sec.types)}）：可用片段 {n}，每条需要 {need}")
    music_ok = c.db.scalar("SELECT COUNT(*) FROM music WHERE commercial_ok='是' AND missing=0 AND style=?",
                           (t.music.get("style", ""),)) or 0
    lines.append(("✅" if music_ok else "⚠️") + f" 配乐“{t.music.get('style')}”：可商用曲目 {music_ok} 首"
                 + ("" if music_ok else "（没有配乐也能生成，但视频没有背景音乐）"))
    unconf = c.db.scalar("SELECT COUNT(*) FROM material WHERE city=? AND publishable='未确认'", (city,)) or 0
    if unconf:
        lines.append(f"ℹ️ {city} 还有 {unconf} 个素材“能否公开使用”未确认，未计入")
    free = common.disk_free_gb(c.lib.root)
    if free < 5:
        lines.append(f"<span style='color:#d00'>⚠️ 磁盘剩余空间只有 {free:.1f} GB（低于 5 GB），生成可能失败</span>")
    lines.append("说明：❌ 表示一条都不够；⚠️ 表示够生成部分条数（多条之间素材会有重合）。")
    return "\n\n".join(lines)


def start(rel, city, aspects, count):
    c, t = _load(rel)
    if not city:
        raise gr.Error("请选择城市")
    if not aspects:
        raise gr.Error("请至少选择一个输出比例")
    free = common.disk_free_gb(c.lib.root)
    warn = f"\n⚠️ 磁盘剩余空间只有 {free:.1f} GB" if free < 5 else ""
    ids = queue.submit(c, rel, t.name, city, list(aspects), int(count or 1))
    return f"已加入队列：{len(ids)} 个任务（编号 {ids[0]}–{ids[-1]}）。生成完成后到“审核”页查看。{warn}", jobs_table()


def jobs_table(show_all: bool = False):
    c = common.ctx()
    if c is None:
        return [[""] * len(JOB_HEADERS)]
    limit = 200 if show_all else 40
    rows = c.db.all(f"SELECT * FROM job ORDER BY id DESC LIMIT {limit}")
    data = []
    for j in rows:
        note = j["error"] or (j["warnings"] or "").replace("\n", "；")
        data.append([j["id"], j["template_name"], j["city"], j["aspect"], j["status"], j["progress"] or "",
                     note, j["created_at"] or ""])
    return data or [[""] * len(JOB_HEADERS)]


def notices(show_all=False):
    msgs = queue.pop_notices()
    for m in msgs:
        if "失败" in m:
            gr.Warning(m)
        else:
            gr.Info(m)
    return jobs_table(bool(show_all))


def cancel(job_id):
    c = common.need_ctx()
    if not job_id:
        raise gr.Error("请填写任务编号（或点表格中的一行）")
    return queue.cancel(c, int(job_id)), jobs_table()


def retry(job_id):
    c = common.need_ctx()
    if not job_id:
        raise gr.Error("请填写任务编号（或点表格中的一行）")
    return queue.retry(c, int(job_id)), jobs_table()


def cancel_all_queued():
    c = common.need_ctx()
    n = 0
    for j in c.db.all("SELECT id FROM job WHERE status = '排队中'"):
        queue.cancel(c, j["id"])
        n += 1
    return f"已取消 {n} 个排队中的任务", jobs_table()


def build():
    with gr.Row():
        with gr.Column(scale=2):
            tpl = gr.Dropdown(label="① 选择模板", choices=template_choices())
            city = gr.Dropdown(label="② 城市", choices=[])
            aspects = gr.CheckboxGroup(list(schema.ASPECTS.keys()), label="③ 输出比例（可多选，同一条视频出多个比例）")
            count = gr.Slider(1, 20, value=3, step=1, label="④ 生成条数")
            with gr.Row():
                go = gr.Button("🎬 开始生成", variant="primary")
                refresh_tpl = gr.Button("刷新模板列表")
            msg = gr.Markdown()
        with gr.Column(scale=3):
            desc = gr.Markdown()
            check = gr.Markdown()
    gr.Markdown("### 任务队列（一个一个按顺序生成，避免电脑卡顿）")
    table = gr.Dataframe(headers=JOB_HEADERS, value=jobs_table, interactive=False, wrap=True,
                         column_widths=["60px", "140px", "70px", "80px", "80px", "200px", "360px", "150px"])
    with gr.Row():
        job_id = gr.Number(label="任务编号", precision=0)
        cancel_btn = gr.Button("取消任务")
        retry_btn = gr.Button("重试任务")
        cancel_all = gr.Button("取消全部排队任务")
        show_all = gr.Checkbox(label="显示更多历史任务")
    op_msg = gr.Markdown()

    tpl.change(on_template, tpl, [city, aspects, desc]).then(preflight, [tpl, city, count], check)
    city.change(preflight, [tpl, city, count], check)
    count.release(preflight, [tpl, city, count], check)
    refresh_tpl.click(lambda: gr.update(choices=template_choices()), None, tpl)
    go.click(start, [tpl, city, aspects, count], [msg, table])
    cancel_btn.click(cancel, job_id, [op_msg, table])
    retry_btn.click(retry, job_id, [op_msg, table])
    cancel_all.click(cancel_all_queued, None, [op_msg, table])
    show_all.change(jobs_table, show_all, table)

    def pick_row(df, evt: gr.SelectData):
        rows = common.to_rows(df)
        r = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        return rows[r][0] if r is not None and r < len(rows) else None

    table.select(pick_row, table, job_id)
    timer = gr.Timer(2)
    timer.tick(notices, show_all, table)
    return {"tpl": tpl, "refresh": (lambda: gr.update(choices=template_choices()), None, tpl)}
