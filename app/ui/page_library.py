"""素材库页面：扫描、浏览、编辑标签、批量编辑、Excel、素材详情（片段/识别文本/使用记录）。"""
from __future__ import annotations

import datetime as _dt
import json

import gradio as gr

from ..analysis.worker import worker
from ..db import now
from ..library import excel_io, scanner
from . import common

HEADERS = ["选", "ID", "文件", "城市", "景点", "类型", "服务内容", "能否公开使用", "评分", "备注",
           "时长(秒)", "片段(低质量)", "语音", "状态", "分析"]
EDIT_COLS = {"景点": "spot", "类型": "type", "服务内容": "service", "能否公开使用": "publishable",
             "评分": "rating", "备注": "note", "城市": "city"}
STATIC = [1, 2, 10, 11, 12, 13, 14]
ALL = "全部"


def banner() -> str:
    c = common.ctx()
    if c is None:
        return common.NO_LIBRARY
    st = worker.status()
    total = c.db.scalar("SELECT COUNT(*) FROM material") or 0
    unconfirmed = c.db.scalar("SELECT COUNT(*) FROM material WHERE publishable='未确认' AND status!='丢失'") or 0
    no_type = c.db.scalar("SELECT COUNT(*) FROM material WHERE (type='' OR type IS NULL) AND status!='丢失'") or 0
    pending = c.db.scalar("SELECT COUNT(*) FROM material WHERE status='待使用'") or 0
    used = c.db.scalar("SELECT COUNT(*) FROM material WHERE status='已使用'") or 0
    lost = c.db.scalar("SELECT COUNT(*) FROM material WHERE status='丢失'") or 0
    parts = [f"共 **{total}** 个素材（待使用 {pending}，已使用 {used}{f'，丢失 {lost}' if lost else ''}）"]
    if unconfirmed:
        parts.append(f"<span style='color:#d00;font-weight:bold'>⚠️ {unconfirmed} 个素材“能否公开使用”未确认，不会参与生成</span>")
    if no_type:
        parts.append(f"<span style='color:#c60'>{no_type} 个素材还没填“类型”</span>")
    if st["pending"] or st["asr_pending"] or st["current"]:
        state_txt = "已暂停" if st["paused"] else "进行中"
        parts.append(f"分析{state_txt}：待分析 {st['pending']}，待语音识别 {st['asr_pending']}"
                     + (f"（当前：{st['current']}）" if st["current"] else ""))
    elif st["paused"]:
        parts.append("分析已暂停")
    if st["model_hint"]:
        parts.append("🔈 语音识别模型未就绪，请到“设置”页下载模型（其余分析不受影响）")
    if st["last_error"]:
        parts.append(f"最近分析错误：{st['last_error']}")
    return "  \n".join(parts)


def _query(city, mtype, status, pub, kw):
    c = common.need_ctx()
    sql = ["SELECT m.*, (SELECT COUNT(*) FROM clip WHERE material_id=m.id) AS n_clip, "
           "(SELECT COUNT(*) FROM clip WHERE material_id=m.id AND low_quality=1 AND manual_restore=0) AS n_low, "
           "(SELECT thumb_path FROM clip WHERE material_id=m.id ORDER BY start LIMIT 1) AS thumb "
           "FROM material m WHERE 1=1"]
    args: list = []
    if city and city != ALL:
        sql.append("AND m.city = ?")
        args.append(city)
    if mtype and mtype != ALL:
        if mtype == "（未填）":
            sql.append("AND (m.type = '' OR m.type IS NULL)")
        else:
            sql.append("AND m.type = ?")
            args.append(mtype)
    if status and status != ALL:
        sql.append("AND m.status = ?")
        args.append(status)
    if pub and pub != ALL:
        sql.append("AND m.publishable = ?")
        args.append(pub)
    if kw:
        sql.append("AND (m.rel_path LIKE ? OR m.spot LIKE ? OR m.note LIKE ? OR m.service LIKE ?)")
        args += [f"%{kw}%"] * 4
    sql.append("ORDER BY m.status, m.rel_path")
    return c, c.db.all(" ".join(sql), args)


def load_table(city, mtype, status, pub, kw):
    if common.ctx() is None:
        return gr.update(value=[[False, None] + [""] * 13]), [], banner()
    c, rows = _query(city, mtype, status, pub, kw)
    data = []
    gallery = []
    for r in rows:
        name = r["rel_path"].split("/", 1)[-1]
        asr = r["asr_status"] or ("图片" if r["is_image"] else "")
        analysis = "无法读取" if r["broken"] else r["analysis_status"]
        data.append([False, r["id"], name, r["city"], r["spot"], r["type"], r["service"], r["publishable"],
                     r["rating"] or 0, r["note"], round(r["duration"] or 0, 1),
                     f"{r['n_clip']}({r['n_low']})", asr, r["status"], analysis])
        t = common.thumb_abs(c, r["thumb"])
        if t and len(gallery) < 300:
            flag = "" if r["publishable"] == "是" else ("🚫" if r["publishable"] == "否" else "❓")
            gallery.append((t, f"{flag}#{r['id']} {r['type'] or '未填类型'} · {name.split('/')[-1]}"))
    if not data:
        data = [[False, None, "", "", "", "", "", "", 0, "", 0, "", "", "", ""]]
    return gr.update(value=data), gallery, banner()


def save_table(df, *filters):
    c = common.need_ctx()
    types = common.material_types()
    changed, problems = 0, []
    for row in common.to_rows(df):
        if row[1] in (None, "") or str(row[1]) == "nan":
            continue
        mid = int(row[1])
        cur = c.db.one("SELECT * FROM material WHERE id = ?", (mid,))
        if not cur:
            continue
        vals = dict(zip(HEADERS, row))
        upd = {}
        for h, k in EDIT_COLS.items():
            if h not in vals:
                continue
            v = vals[h]
            v = "" if v is None or str(v) == "nan" else v
            if k == "rating":
                try:
                    v = max(0, min(5, int(float(v or 0))))
                except ValueError:
                    problems.append(f"#{mid} 评分无效")
                    continue
            else:
                v = str(v).strip()
            if k == "publishable" and v not in ("是", "否", "未确认"):
                problems.append(f"#{mid} 能否公开使用只能填 是/否/未确认")
                continue
            if k == "type" and v and v not in types:
                problems.append(f"#{mid} 类型“{v}”不在类型列表中（可在设置里添加）")
                continue
            if cur.get(k) != v:
                upd[k] = v
        if upd:
            upd["updated_at"] = now()
            c.db.update("material", mid, upd)
            changed += 1
    msg = f"已保存 {changed} 个素材的修改。"
    if problems:
        msg += "\n" + "\n".join(problems[:20])
    table, gallery, ban = load_table(*filters)
    return msg, table, gallery, ban


def batch_apply(df, field, value, *filters):
    c = common.need_ctx()
    if not field:
        raise gr.Error("请选择要批量修改的字段")
    key = EDIT_COLS[field]
    ids = [int(r[1]) for r in common.to_rows(df) if r[0] in (True, "true", "True", 1) and r[1] not in (None, "")]
    if not ids:
        raise gr.Error("请先在表格第一列勾选要修改的素材（或点“全选”）")
    if key == "publishable" and value not in ("是", "否", "未确认"):
        raise gr.Error("能否公开使用只能是 是/否/未确认")
    if key == "type" and value and value not in common.material_types():
        raise gr.Error(f"类型“{value}”不在类型列表中")
    if key == "rating":
        try:
            value = max(0, min(5, int(float(value or 0))))
        except ValueError:
            raise gr.Error("评分应为 0–5 的数字")
    with c.db.connect() as conn:
        for mid in ids:
            conn.execute(f"UPDATE material SET {key} = ?, updated_at = ? WHERE id = ?", (value, now(), mid))
    table, gallery, ban = load_table(*filters)
    return f"已把 {len(ids)} 个素材的“{field}”改为“{value}”。", table, gallery, ban


def select_all(df, flag: bool):
    rows = common.to_rows(df)
    for r in rows:
        if r[1] not in (None, ""):
            r[0] = flag
    return rows


def do_scan(*filters):
    c = common.need_ctx()
    rep = scanner.scan(c)
    try:
        n_music = scanner.scan_music(c)
    except Exception:
        n_music = 0
    worker.kick()
    msg = rep.summary() + (f"\n配乐新登记 {n_music} 首。" if n_music else "")
    table, gallery, ban = load_table(*filters)
    return msg, table, gallery, ban


def export_excel():
    c = common.need_ctx()
    path = c.lib.data / "导出" / f"素材登记表_{_dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"
    excel_io.export_xlsx(c, path, common.material_types())
    return str(path), f"已导出：{path}"


def import_excel(file, *filters):
    c = common.need_ctx()
    if not file:
        raise gr.Error("请先选择 Excel 文件")
    msg = excel_io.import_xlsx(c, file, common.material_types())
    table, gallery, ban = load_table(*filters)
    return msg, table, gallery, ban


# ---------- 详情 ----------

def detail(mid):
    c = common.need_ctx()
    empty = ("请选择一个素材", gr.update(value=None, visible=False), gr.update(value=None, visible=False),
             [], gr.update(choices=[], value=None), [["", "", ""]], "", "")
    if mid in (None, "", 0):
        return empty
    m = c.db.one("SELECT * FROM material WHERE id = ?", (int(mid),))
    if not m:
        return empty
    path = c.lib.abs(m["rel_path"])
    info = [f"**#{m['id']} {m['rel_path']}**",
            f"城市：{m['city']}　景点：{m['spot'] or '—'}　类型：{m['type'] or '未填'}　能否公开使用：**{m['publishable']}**　评分：{m['rating'] or 0}",
            f"状态：{m['status']}　分析：{m['analysis_status']}　语音：{m['asr_status'] or '—'}　被使用 {m['use_count']} 次",
            f"原始备份：{m['backup_path']}"]
    if not m["is_image"]:
        info.append(f"{m['width']}×{m['height']}　{m['fps']}fps　时长 {m['duration']:.1f} 秒　"
                    f"旋转 {m['rotation']}°　{'有' if m['has_audio'] else '无'}音轨　编码 {m['vcodec']}")
    clips = c.db.all("SELECT * FROM clip WHERE material_id = ? ORDER BY start", (m["id"],))
    gal, choices = [], []
    for i, cl in enumerate(clips, 1):
        low = cl["low_quality"] and not cl["manual_restore"]
        label = (f"片段{i} {cl['start']:.1f}-{cl['end']:.1f}s 清晰度{cl['sharpness']:.0f} 亮度{cl['brightness']:.0f}"
                 + (f" ⚠️低质量:{cl['quality_reason']}" if low else "")
                 + ("（已手动恢复）" if cl["manual_restore"] else "") + f" 用过{cl['use_count']}次")
        t = common.thumb_abs(c, cl["thumb_path"])
        if t:
            gal.append((t, label))
        choices.append((label, cl["id"]))
    tr = c.db.one("SELECT * FROM transcript WHERE material_id = ?", (m["id"],))
    rows = [["", "", ""]]
    tr_info = "没有识别文本" if not tr else ""
    if tr:
        segs = json.loads(tr["edited_text_json"] or tr["segments_json"] or "[]")
        rows = [[s["start"], s["end"], s["text"]] for s in segs] or rows
        tr_info = (f"识别语言：{tr['language']}　模型：{tr['model']}"
                   + ("　（已人工修改）" if tr["edited_text_json"] else ""))
    uses = c.db.all("SELECT u.created_at, o.name, o.review_status FROM usage u LEFT JOIN output o "
                    "ON o.id = u.output_id WHERE u.material_id = ? ORDER BY u.id DESC", (m["id"],))
    use_md = "\n".join(f"- {u['created_at']}　{u['name'] or '（输出已删除）'}　{u['review_status'] or ''}"
                       for u in uses) or "还没有被用过"
    is_img = bool(m["is_image"])
    exists = path.is_file()
    return ("\n\n".join(info),
            gr.update(value=str(path) if exists and not is_img else None, visible=exists and not is_img),
            gr.update(value=str(path) if exists and is_img else None, visible=exists and is_img),
            gal, gr.update(choices=choices, value=None), rows, tr_info, use_md)


def toggle_clip(mid, clip_id):
    c = common.need_ctx()
    if not clip_id:
        raise gr.Error("请先选择一个片段")
    cl = c.db.one("SELECT * FROM clip WHERE id = ?", (int(clip_id),))
    if cl["low_quality"] and not cl["manual_restore"]:
        c.db.update("clip", cl["id"], {"manual_restore": 1})
        msg = "已恢复该片段，可以参与生成。"
    elif cl["manual_restore"]:
        c.db.update("clip", cl["id"], {"manual_restore": 0})
        msg = "已取消恢复，该片段重新标记为低质量。"
    else:
        c.db.update("clip", cl["id"], {"low_quality": 1, "quality_reason": "人工标记", "manual_restore": 0})
        msg = "已把该片段标记为低质量，不参与生成。"
    return (msg, *detail(mid))


def save_transcript(mid, df):
    c = common.need_ctx()
    if not mid:
        raise gr.Error("请先选择素材")
    segs = []
    for r in common.to_rows(df):
        if len(r) < 3 or not str(r[2] or "").strip():
            continue
        try:
            segs.append({"start": float(r[0]), "end": float(r[1]), "text": str(r[2]).strip()})
        except (TypeError, ValueError):
            raise gr.Error("开始/结束时间必须是数字（秒）")
    tr = c.db.one("SELECT id FROM transcript WHERE material_id = ?", (int(mid),))
    data = json.dumps(sorted(segs, key=lambda s: s["start"]), ensure_ascii=False)
    if tr:
        c.db.update("transcript", tr["id"], {"edited_text_json": data})
    else:
        c.db.insert("transcript", {"material_id": int(mid), "language": "", "segments_json": "[]",
                                   "edited_text_json": data, "model": "人工", "created_at": now()})
        c.db.update("material", int(mid), {"asr_status": "已识别"})
    return "识别文本已保存（生成时优先使用修改后的文本）。"


def redo_asr(mid):
    c = common.need_ctx()
    if not mid:
        raise gr.Error("请先选择素材")
    with c.db.connect() as conn:
        conn.execute("DELETE FROM transcript WHERE material_id = ?", (int(mid),))
        conn.execute("UPDATE material SET asr_status = '' WHERE id = ? AND is_image = 0 AND has_audio = 1",
                     (int(mid),))
    worker.model_ready_again()
    return "已加入重新识别队列。"


def reanalyze(mid):
    c = common.need_ctx()
    if not mid:
        raise gr.Error("请先选择素材")
    c.db.update("material", int(mid), {"analysis_status": "待分析"})
    worker.kick()
    return "已加入重新分析队列（切镜、画质、缩略图）。"


def pause_resume():
    if worker.paused:
        worker.resume()
        return "分析已继续", banner()
    worker.pause()
    return "分析已暂停", banner()


def build():
    f_values = ALL
    with gr.Column():
        ban = gr.Markdown(banner)
        with gr.Row():
            scan_btn = gr.Button("🔍 扫描新素材", variant="primary")
            pause_btn = gr.Button("⏯ 暂停/继续分析")
            refresh_btn = gr.Button("🔄 刷新")
            export_btn = gr.Button("📤 导出登记表 Excel")
        with gr.Row():
            import_file = gr.File(label="导入登记表（填好的 Excel）", file_types=[".xlsx"], scale=3)
            import_btn = gr.Button("📥 导入 Excel", scale=1)
            export_file = gr.File(label="导出的文件", interactive=False, scale=3)
        msg = gr.Markdown()
        with gr.Row():
            f_city = gr.Dropdown([ALL] + common.cities(), value=f_values, label="城市")
            f_type = gr.Dropdown([ALL, "（未填）"] + common.material_types(), value=ALL, label="类型")
            f_status = gr.Dropdown([ALL, "待使用", "已使用", "丢失"], value="待使用", label="状态")
            f_pub = gr.Dropdown([ALL, "是", "否", "未确认"], value=ALL, label="能否公开使用")
            f_kw = gr.Textbox(label="关键词（文件名/景点/备注）")
        filters = [f_city, f_type, f_status, f_pub, f_kw]
        with gr.Tab("缩略图"):
            gallery = gr.Gallery(label="点击缩略图查看详情", columns=6, height=520, allow_preview=False)
        with gr.Tab("登记表（可编辑）"):
            gr.Markdown("直接在表格里改 **景点 / 类型 / 服务内容 / 能否公开使用(是/否/未确认) / 评分(0-5) / 备注**，"
                        "改完点“保存表格修改”。批量修改：勾选第一列，再用下面的批量修改。")
            table = gr.Dataframe(headers=HEADERS, datatype=["bool", "number", "str", "str", "str", "str", "str",
                                                            "str", "number", "str", "number", "str", "str",
                                                            "str", "str"],
                                 interactive=True, static_columns=STATIC, wrap=True, max_height=600,
                                 column_widths=["50px", "60px", "240px"])
            with gr.Row():
                save_btn = gr.Button("💾 保存表格修改", variant="primary")
                all_btn = gr.Button("全选")
                none_btn = gr.Button("全不选")
            with gr.Row():
                b_field = gr.Dropdown(list(EDIT_COLS.keys()), label="批量修改字段")
                b_value = gr.Textbox(label="改为（类型请填类型列表中的名称；能否公开使用填 是/否/未确认）")
                b_btn = gr.Button("应用到勾选的素材")
        with gr.Accordion("素材详情", open=True):
            with gr.Row():
                d_id = gr.Number(label="素材 ID", precision=0, scale=1)
                d_load = gr.Button("查看", scale=1)
                d_reanalyze = gr.Button("重新分析", scale=1)
            d_info = gr.Markdown()
            with gr.Row():
                d_video = gr.Video(label="预览", visible=False, height=360)
                d_image = gr.Image(label="预览", visible=False, height=360, interactive=False)
            d_clips = gr.Gallery(label="片段（镜头）", columns=5, height=220, allow_preview=False, object_fit="contain")
            with gr.Row():
                d_clip_sel = gr.Dropdown(label="选择片段", choices=[], scale=4)
                d_toggle = gr.Button("恢复低质量片段 / 标记为低质量", scale=2)
            gr.Markdown("**语音识别文本**（可直接修改文字和时间，修改后生成时优先使用）")
            d_tr_info = gr.Markdown()
            d_tr = gr.Dataframe(headers=["开始(秒)", "结束(秒)", "文本"], datatype=["number", "number", "str"],
                                interactive=True, wrap=True, column_widths=["90px", "90px", "600px"])
            with gr.Row():
                d_tr_save = gr.Button("💾 保存识别文本")
                d_tr_redo = gr.Button("重新识别")
            gr.Markdown("**被用在了哪些视频里**")
            d_use = gr.Markdown()
        d_msg = gr.Markdown()

    detail_outs = [d_info, d_video, d_image, d_clips, d_clip_sel, d_tr, d_tr_info, d_use]
    lt_outs = [table, gallery, ban]
    for f in filters:
        f.change(load_table, filters, lt_outs)
    refresh_btn.click(load_table, filters, lt_outs)
    scan_btn.click(do_scan, filters, [msg, *lt_outs])
    pause_btn.click(pause_resume, None, [msg, ban])
    export_btn.click(export_excel, None, [export_file, msg])
    import_btn.click(import_excel, [import_file, *filters], [msg, *lt_outs])
    save_btn.click(save_table, [table, *filters], [msg, *lt_outs])
    all_btn.click(lambda df: select_all(df, True), table, table)
    none_btn.click(lambda df: select_all(df, False), table, table)
    b_btn.click(batch_apply, [table, b_field, b_value, *filters], [msg, *lt_outs])

    def gallery_pick(evt: gr.SelectData):
        cap = evt.value.get("caption", "") if isinstance(evt.value, dict) else ""
        mid = None
        if "#" in cap:
            try:
                mid = int(cap.split("#", 1)[1].split()[0])
            except ValueError:
                mid = None
        return (mid, *detail(mid))

    gallery.select(gallery_pick, None, [d_id, *detail_outs])

    def table_pick(df, evt: gr.SelectData):
        rows = common.to_rows(df)
        r = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        if evt.index and isinstance(evt.index, (list, tuple)) and evt.index[1] == 0:
            return (gr.update(), *[gr.update()] * len(detail_outs))
        mid = rows[r][1] if r is not None and r < len(rows) else None
        return (mid, *detail(mid))

    table.select(table_pick, table, [d_id, *detail_outs])
    d_load.click(detail, d_id, detail_outs)
    d_toggle.click(toggle_clip, [d_id, d_clip_sel], [d_msg, *detail_outs])
    d_tr_save.click(save_transcript, [d_id, d_tr], d_msg)
    d_tr_redo.click(redo_asr, d_id, d_msg)
    d_reanalyze.click(reanalyze, d_id, d_msg)
    timer = gr.Timer(5)
    timer.tick(banner, None, ban)
    return {"load": (load_table, filters, lt_outs), "filters": filters, "f_city": f_city, "f_type": f_type}
