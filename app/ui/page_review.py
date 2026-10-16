"""审核页面：播放、查看字幕/文案/素材；通过（生成发布包）/ 退回 / 删除 / 编辑后重新生成 / 换封面帧。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import gradio as gr

from ..db import now
from ..imageio import grab_frame
from ..jobs import runner
from ..jobs.queue import queue
from ..library import mover
from ..render import cover as cover_mod
from ..render import imgfx
from ..templates import schema
from . import common

HEADERS = ["ID", "名称", "类型", "城市", "模板", "比例", "审核状态", "生成时间"]
STATUS_DIR = {"待审核": "out_pending", "已审核": "out_approved", "已退回": "out_rejected"}


def list_outputs(status, city, tpl, date):
    c = common.ctx()
    if c is None:
        return [[""] * len(HEADERS)]
    sql, args = ["SELECT * FROM output WHERE 1=1"], []
    if status and status != "全部":
        sql.append("AND review_status = ?")
        args.append(status)
    if city and city != "全部":
        sql.append("AND city = ?")
        args.append(city)
    if tpl and tpl != "全部":
        sql.append("AND template_name = ?")
        args.append(tpl)
    if date:
        sql.append("AND name LIKE ?")
        args.append(f"{str(date).replace('-', '')}%")
    rows = c.db.all(" ".join(sql) + " ORDER BY id DESC LIMIT 500", args)
    data = [[r["id"], r["name"], "视频" if r["kind"] == "video" else "海报", r["city"], r["template_name"],
             r["aspect"], r["review_status"], r["created_at"]] for r in rows]
    return data or [[""] * len(HEADERS)]


def template_names():
    c = common.ctx()
    if c is None:
        return ["全部"]
    return ["全部"] + [r["template_name"] for r in c.db.all(
        "SELECT DISTINCT template_name FROM output WHERE template_name != '' ORDER BY template_name")]


def _plan(c, o):
    if not o or not o["job_id"]:
        return None
    j = c.db.one("SELECT * FROM job WHERE id = ?", (o["job_id"],))
    if not j or not j["plan_json"]:
        return None
    try:
        return {"job": j, **json.loads(j["plan_json"])}
    except json.JSONDecodeError:
        return None


EMPTY_N = 17


def load_output(oid):
    c = common.need_ctx()
    if oid in (None, "", 0):
        return ("请在上面的列表中选择一条",) + (gr.update(),) * (EMPTY_N - 1)
    o = c.db.one("SELECT * FROM output WHERE id = ?", (int(oid),))
    if not o:
        return ("这条输出不存在（可能已删除）",) + (gr.update(),) * (EMPTY_N - 1)
    f = c.lib.abs(o["file_path"])
    folder = f.parent
    is_video = o["kind"] == "video"
    covers = [str(folder / n) for n in (o["cover_paths"] or "").split(",") if n and (folder / n).is_file()]
    srt = ""
    sp = folder / "subtitles.srt"
    if sp.is_file():
        srt = sp.read_text(encoding="utf-8")
    info = {}
    ip = folder / "info.json"
    if ip.is_file():
        info = json.loads(ip.read_text(encoding="utf-8"))
    mats = info.get("materials") or []
    mat_md = "\n".join(f"{i}. [{m.get('section', '')}] `{m['rel_path']}`　素材#{m['material_id']}　"
                       f"起点 {m['src_start']:.1f}s　时长 {m['duration']:.1f}s"
                       + ("　🔊原声" if m.get("keep_audio") else "") for i, m in enumerate(mats)) or "—"
    head = [f"### {o['name']}",
            f"状态：**{o['review_status']}**　城市：{o['city']}　模板：{o['template_name']}　比例：{o['aspect']}",
            f"文件夹：`{folder}`"]
    if info:
        head.append(f"时长 {info.get('duration')} 秒　随机种子 {info.get('seed')}　配乐：{info.get('music') or '无'}"
                    f"　编码器：{info.get('encoder')}")
    notes = (o["notes"] or "").strip()
    if notes:
        head.append("⚠️ " + notes.replace("\n", "　⚠️ "))
    if o["reject_reason"]:
        head.append(f"退回原因：{o['reject_reason']}")
    plan = _plan(c, o)
    hook_en = hook_zh = cta_en = cta_zh = ""
    caps, voice_rows, slot_choices = [["", "", ""]], [["", "", "", "", ""]], []
    if plan:
        texts = plan.get("texts", {})
        hook_en, hook_zh = (list(texts.get("hook", ["", ""])) + ["", ""])[:2]
        cta_en, cta_zh = (list(texts.get("cta", ["", ""])) + ["", ""])[:2]
        caps = [[k, *(list(v) + ["", ""])[:2]] for k, v in sorted(texts.get("captions", {}).items(),
                                                                  key=lambda kv: int(kv[0]))] or caps
        vr = []
        for k, lines in sorted(texts.get("voice", {}).items(), key=lambda kv: int(kv[0])):
            for ln in lines:
                vr.append([int(k), ln["start"], ln["end"], ln.get("text", ""), ln.get("text_zh", "")])
        voice_rows = vr or voice_rows
        for i, ch in enumerate(plan["timeline"]["choices"]):
            slot_choices.append((f"{i}. {Path(ch['rel_path']).name}（{ch.get('type', '')}）", i))
    dur = float(info.get("duration") or 30)
    return ("\n\n".join(head),
            gr.update(value=str(f) if is_video and f.is_file() else None, visible=is_video),
            gr.update(value=str(f) if not is_video and f.is_file() else None, visible=not is_video),
            covers, srt, o["title"] or "", o["caption"] or "", o["hashtags"] or "", mat_md,
            hook_en, hook_zh, cta_en, cta_zh, caps, voice_rows,
            gr.update(choices=slot_choices, value=[]), gr.update(maximum=max(dur, 1), value=min(1.0, dur)))


def _write_caption(folder: Path, caption: str, hashtags: str) -> None:
    (folder / "caption.txt").write_text(f"{caption.strip()}\n\n{hashtags.strip()}\n", encoding="utf-8")


def save_texts(oid, title, caption, hashtags):
    c = common.need_ctx()
    o = _get(c, oid)
    c.db.update("output", o["id"], {"title": title, "caption": caption, "hashtags": hashtags})
    folder = c.lib.abs(o["file_path"]).parent
    _write_caption(folder, caption, hashtags)
    return "已保存标题和发布文案（caption.txt 已更新）。"


def _get(c, oid):
    if oid in (None, "", 0):
        raise gr.Error("请先选择一条输出")
    o = c.db.one("SELECT * FROM output WHERE id = ?", (int(oid),))
    if not o:
        raise gr.Error("这条输出不存在")
    return o


def _move_folder(c, o, to_status: str) -> Path:
    src_file = c.lib.abs(o["file_path"])
    src = src_file.parent
    dst_root = getattr(c.lib, STATUS_DIR[to_status])
    dst = dst_root / src.name
    n = 2
    while dst.exists():
        dst = dst_root / f"{src.name}_{n}"
        n += 1
    shutil.move(str(src), str(dst))
    upd = {"review_status": to_status, "file_path": c.lib.rel(dst / src_file.name)}
    if o["srt_path"]:
        upd["srt_path"] = c.lib.rel(dst / Path(o["srt_path"]).name)
    c.db.update("output", o["id"], upd)
    return dst


def approve(oid, title, caption, hashtags):
    c = common.need_ctx()
    o = _get(c, oid)
    if o["review_status"] == "已审核":
        return "这条已经审核通过了。"
    c.db.update("output", o["id"], {"title": title, "caption": caption, "hashtags": hashtags})
    folder = c.lib.abs(o["file_path"]).parent
    _write_caption(folder, caption, hashtags)
    info_p = folder / "info.json"
    if info_p.is_file():
        info = json.loads(info_p.read_text(encoding="utf-8"))
        info.update({"approved_at": now(), "title": title})
        info_p.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    dst = _move_folder(c, o, "已审核")
    return f"✅ 已通过，发布包在：`{dst}`"


def reject(oid, reason, back):
    c = common.need_ctx()
    o = _get(c, oid)
    if not (reason or "").strip():
        raise gr.Error("请填写退回原因")
    msg = ""
    if back:
        res = mover.return_to_pending(c, o["id"])
        msg = f"，{len(res.moved)} 个素材已退回 待使用/" + (f"（{len(res.errors)} 个失败）" if res.errors else "")
    c.db.update("output", o["id"], {"reject_reason": reason.strip()})
    if o["review_status"] != "已退回":
        _move_folder(c, o, "已退回")
    return f"已退回{msg}。"


def delete(oid, back):
    c = common.need_ctx()
    o = _get(c, oid)
    runner.delete_output(c, o["id"], return_materials=bool(back))
    return "已删除" + ("，素材已退回 待使用/" if back else "") + "。"


def regenerate(oid, hook_en, hook_zh, cta_en, cta_zh, caps, voice, replace, title, caption, hashtags):
    c = common.need_ctx()
    o = _get(c, oid)
    plan = _plan(c, o)
    if not plan or o["kind"] != "video":
        raise gr.Error("这条输出没有可复用的生成记录，无法重新生成")
    if o["review_status"] != "待审核":
        raise gr.Error("只有“待审核”的视频可以编辑后重新生成")
    captions = {}
    for r in common.to_rows(caps):
        if r and str(r[0]).strip() not in ("", "nan"):
            captions[str(int(float(r[0])))] = [str(r[1] or ""), str(r[2] or "")]
    vmap: dict[str, list] = {}
    for r in common.to_rows(voice):
        if not r or str(r[0]).strip() in ("", "nan"):
            continue
        try:
            vmap.setdefault(str(int(float(r[0]))), []).append(
                {"start": float(r[1]), "end": float(r[2]), "text": str(r[3] or ""), "text_zh": str(r[4] or "")})
        except (TypeError, ValueError):
            raise gr.Error("语音字幕的时间必须是数字")
    text = {"hook": [hook_en or "", hook_zh or ""], "cta": [cta_en or "", cta_zh or ""], "captions": captions,
            "title": title, "post": caption,
            "hashtags": [h.lstrip("#") for h in (hashtags or "").split() if h.strip()]}
    replace = [int(x) for x in (replace or [])]
    if vmap and not replace:
        text["voice"] = vmap
    overrides = {"reuse_plan": plan["timeline"]["choices"], "replace": replace, "text": text,
                 "replaces_output": o["id"], "music_id": plan.get("music_id")}
    j = plan["job"]
    ids = queue.submit(c, j["template_file"], j["template_name"], o["city"], [o["aspect"]], 1, overrides, "")
    return (f"已加入队列（任务 {ids[0]}）。新视频生成成功后会替换这一条；"
            f"{'替换的片段会重新挑选素材。' if replace else ''}可在“生成”页查看进度。")


def recover_cover(oid, t, title):
    c = common.need_ctx()
    o = _get(c, oid)
    if o["kind"] != "video":
        raise gr.Error("海报没有封面")
    f = c.lib.abs(o["file_path"])
    frame = grab_frame(f, float(t or 0), None)
    if frame is None:
        raise gr.Error("取帧失败")
    plan = _plan(c, o)
    style = None
    if plan:
        try:
            style = schema.load(c.lib.abs(plan["job"]["template_file"])).subtitle_style
        except Exception:
            style = None
    brand = common.settings()["brand"]
    logo = c.lib.abs(brand["logo"]) if brand.get("logo") else None
    paths = cover_mod.make_covers(frame, f.parent, title or o["title"], imgfx.find_font(c.lib.fonts),
                                  logo, style.color if style else "#FFFFFF",
                                  style.outline_color if style else "#000000")
    c.db.update("output", o["id"], {"cover_paths": ",".join(p.name for p in paths), "title": title or o["title"]})
    # 加时间戳参数避免浏览器缓存旧图
    return [str(p) for p in paths], f"封面已用第 {float(t):.1f} 秒的画面重新生成。"


def preview_frame(oid, t):
    c = common.ctx()
    if c is None or not oid:
        return None
    o = c.db.one("SELECT * FROM output WHERE id = ?", (int(oid),))
    if not o or o["kind"] != "video":
        return None
    return grab_frame(c.lib.abs(o["file_path"]), float(t or 0), 360)


def build():
    with gr.Row():
        f_status = gr.Dropdown(["待审核", "已审核", "已退回", "全部"], value="待审核", label="审核状态")
        f_city = gr.Dropdown(["全部"] + common.cities(), value="全部", label="城市")
        f_tpl = gr.Dropdown(template_names(), value="全部", label="模板")
        f_date = gr.Textbox(label="日期（如 20261015，可留空）")
        refresh = gr.Button("🔄 刷新列表")
    filters = [f_status, f_city, f_tpl, f_date]
    table = gr.Dataframe(headers=HEADERS, value=lambda: list_outputs("待审核", "全部", "全部", ""),
                         interactive=False, wrap=True, max_height=260)
    oid = gr.Number(label="当前选择的输出 ID", precision=0)
    head = gr.Markdown()
    with gr.Row():
        with gr.Column(scale=2):
            video = gr.Video(label="成片", height=560)
            poster = gr.Image(label="海报", visible=False, height=560, interactive=False)
        with gr.Column(scale=3):
            covers = gr.Gallery(label="封面", columns=2, height=360, object_fit="contain")
            with gr.Accordion("换封面帧", open=False):
                cover_t = gr.Slider(0, 30, value=1, step=0.1, label="拖动选择时间（秒）")
                cover_prev = gr.Image(label="这一帧", height=240, interactive=False)
                cover_btn = gr.Button("用这一帧重新生成封面")
            title = gr.Textbox(label="封面标题 / 视频标题")
            caption = gr.Textbox(label="发布文案（可直接复制）", lines=4)
            hashtags = gr.Textbox(label="话题标签")
            save_txt = gr.Button("💾 保存标题和文案")
    with gr.Row():
        srt = gr.Textbox(label="字幕（SRT）", lines=8, interactive=False)
        mats = gr.Markdown(label="所用素材")
    with gr.Row():
        ok_btn = gr.Button("✅ 通过（生成发布包）", variant="primary")
        reason = gr.Textbox(label="退回原因", scale=2)
        back = gr.Checkbox(label="素材退回待使用", value=False)
        rej_btn = gr.Button("↩️ 退回", variant="stop")
        del_btn = gr.Button("🗑 删除")
    with gr.Accordion("编辑后重新生成（修改字幕文字 / 替换片段，其余素材复用）", open=False):
        with gr.Row():
            hook_en = gr.Textbox(label="开场标题（英文/主语言）")
            hook_zh = gr.Textbox(label="开场标题（中文）")
        with gr.Row():
            cta_en = gr.Textbox(label="结尾行动号召（英文/主语言）")
            cta_zh = gr.Textbox(label="结尾行动号召（中文）")
        caps = gr.Dataframe(headers=["段落序号", "说明文字（英文/主语言）", "说明文字（中文）"],
                            interactive=True, wrap=True, label="段落说明文字")
        voice = gr.Dataframe(headers=["片段序号", "开始", "结束", "英文", "中文"], interactive=True, wrap=True,
                             label="语音字幕（时间相对该片段开头）")
        replace = gr.CheckboxGroup(label="替换这些片段（重新挑选素材）", choices=[])
        regen = gr.Button("🔁 重新生成")
    msg = gr.Markdown()

    outs = [head, video, poster, covers, srt, title, caption, hashtags, mats, hook_en, hook_zh, cta_en, cta_zh,
            caps, voice, replace, cover_t]
    for f in filters:
        f.change(list_outputs, filters, table)
    refresh.click(list_outputs, filters, table).then(lambda: gr.update(choices=template_names()), None, f_tpl)

    def pick(df, evt: gr.SelectData):
        rows = common.to_rows(df)
        r = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        v = rows[r][0] if r is not None and r < len(rows) else None
        return (v, *load_output(v))

    table.select(pick, table, [oid, *outs])
    after = dict(fn=list_outputs, inputs=filters, outputs=table)
    ok_btn.click(approve, [oid, title, caption, hashtags], msg).then(**after)
    rej_btn.click(reject, [oid, reason, back], msg).then(**after)
    del_btn.click(delete, [oid, back], msg).then(**after)
    save_txt.click(save_texts, [oid, title, caption, hashtags], msg)
    regen.click(regenerate, [oid, hook_en, hook_zh, cta_en, cta_zh, caps, voice, replace, title, caption, hashtags],
                msg)
    cover_t.release(preview_frame, [oid, cover_t], cover_prev)
    cover_btn.click(recover_cover, [oid, cover_t, title], [covers, msg])
    return {"refresh": (list_outputs, filters, table)}
