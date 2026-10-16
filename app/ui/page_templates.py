"""模板页面：表单编辑模板（不需要写 YAML）。"""
from __future__ import annotations

import gradio as gr

from ..templates import schema
from . import common

SEC_HEADERS = ["段落名称", "素材类型（用、分隔）", "景点（可空，用、分隔）", "段数", "最短秒", "最长秒",
               "保留原声(是/否)", "说明文字（英文/主语言）", "说明文字（中文）"]


def _split(s) -> list[str]:
    s = str(s or "")
    for ch in ("，", ",", "/", "；", ";"):
        s = s.replace(ch, "、")
    return [x.strip() for x in s.split("、") if x.strip() and x.strip() != "nan"]


def choices():
    c = common.ctx()
    if c is None:
        return []
    return [(t.name, c.lib.rel(t.file_path)) for t in schema.list_templates(c.lib.templates)]




def load(rel):
    c = common.need_ctx()
    t = schema.load(c.lib.abs(rel)) if rel else schema.Template(sections=[schema.Section(name="段落1", types=["景点"])])
    st = t.subtitle_style
    secs = [[s.name, "、".join(s.types), "、".join(s.spots), s.count, s.seconds[0], s.seconds[1],
             "是" if s.keep_audio else "否", s.caption, s.caption_zh] for s in t.sections]
    return (t.name, t.cities, t.aspects, t.duration, t.subtitle_language,
            t.text.get("hook", ""), t.text.get("hook_zh", ""), t.text.get("cta", ""), t.text.get("cta_zh", ""),
            bool(t.text.get("use_llm")), st.font, st.font_size, st.font_size_zh, st.color, st.outline_color,
            st.outline, st.position, st.box, t.transition_type, float(t.transition.get("duration", 0.4)),
            t.music.get("style", ""), t.music.get("track", ""), float(t.music.get("volume", 0.6)), t.fill_mode,
            bool(t.brand.get("logo_corner")), bool(t.brand.get("end_card")),
            bool(t.cover.get("enabled", True)), t.cover.get("title", ""), secs, bool(t.color_grade))


def _build(vals) -> schema.Template:
    (name, cities, aspects, duration, lang, hook, hook_zh, cta, cta_zh, use_llm, font, fs, fs_zh, color,
     ocolor, outline, pos, box, ttype, tdur, mstyle, mtrack, mvol, fill, logo, endcard, cov_on, cov_title,
     secs, grade) = vals
    sections = []
    for r in common.to_rows(secs):
        if not r or not str(r[0] or "").strip() or str(r[0]) == "nan":
            continue
        try:
            sections.append(schema.Section(
                name=str(r[0]).strip(), types=_split(r[1]), spots=_split(r[2]), count=int(float(r[3] or 1)),
                seconds=[float(r[4] or 2.5), float(r[5] or 3.5)],
                keep_audio=str(r[6]).strip() in ("是", "True", "true", "1"),
                caption="" if str(r[7]) == "nan" else str(r[7] or ""),
                caption_zh="" if str(r[8]) == "nan" else str(r[8] or "")))
        except (TypeError, ValueError):
            raise gr.Error(f"段落“{r[0]}”的段数或时长不是数字")
    types = set(common.material_types())
    for s in sections:
        bad = [x for x in s.types if x not in types]
        if bad:
            raise gr.Error(f"段落“{s.name}”的素材类型 {'、'.join(bad)} 不在类型列表中（设置页可添加）")
    return schema.Template(
        name=(name or "").strip(), cities=list(cities or []), aspects=list(aspects or []),
        duration=float(duration or 30), subtitle_language=lang,
        transition={"type": ttype, "duration": float(tdur or 0.4)}, fill_mode=fill,
        music={"style": mstyle or "", "track": mtrack or "", "volume": float(mvol or 0.6)},
        brand={"logo_corner": bool(logo), "end_card": bool(endcard)},
        text={"hook": hook or "", "hook_zh": hook_zh or "", "cta": cta or "", "cta_zh": cta_zh or "",
              "use_llm": bool(use_llm)},
        subtitle_style=schema.SubtitleStyle(font=font or "Noto Sans SC", font_size=int(fs or 64),
                                            font_size_zh=int(fs_zh or 56), color=color or "#FFFFFF",
                                            outline_color=ocolor or "#000000", outline=int(outline or 0),
                                            position=pos, box=bool(box)),
        sections=sections, cover={"enabled": bool(cov_on), "title": cov_title or ""}, color_grade=bool(grade))


def save(rel, *vals):
    c = common.need_ctx()
    t = _build(vals)
    errs = t.validate()
    if errs:
        raise gr.Error("；".join(errs))
    path = c.lib.abs(rel) if rel else c.lib.templates / f"{schema.safe_filename(t.name)}.yaml"
    if not rel and path.exists():
        raise gr.Error("已有同名模板，请换个名称")
    schema.save(t, path)
    new_rel = c.lib.rel(path)
    return gr.update(choices=choices(), value=new_rel), f"已保存：{path.name}"


def save_as(*vals):
    c = common.need_ctx()
    t = _build(vals)
    t.name = t.name + " 副本" if not t.name.endswith("副本") else t.name + "2"
    path = c.lib.templates / f"{schema.safe_filename(t.name)}.yaml"
    n = 2
    while path.exists():
        path = c.lib.templates / f"{schema.safe_filename(t.name)}_{n}.yaml"
        n += 1
    schema.save(t, path)
    return gr.update(choices=choices(), value=c.lib.rel(path)), f"已复制为：{t.name}"


def delete(rel):
    c = common.need_ctx()
    if not rel:
        raise gr.Error("请先选择模板")
    p = c.lib.abs(rel)
    trash = c.lib.data / "已删除模板"
    trash.mkdir(parents=True, exist_ok=True)
    p.replace(trash / p.name)
    return gr.update(choices=choices(), value=None), f"已删除（文件移到 数据/已删除模板/，可找回）：{p.name}"


def build():
    with gr.Row():
        sel = gr.Dropdown(label="选择模板", choices=choices(), scale=3)
        new_btn = gr.Button("➕ 新建空白模板", scale=1)
        refresh = gr.Button("刷新", scale=1)
    msg = gr.Markdown()
    with gr.Row():
        name = gr.Textbox(label="模板名称")
        cities = gr.CheckboxGroup(common.cities(), label="城市（可多选）")
        aspects = gr.CheckboxGroup(list(schema.ASPECTS.keys()), label="输出比例")
        duration = gr.Number(label="目标时长（秒，允许 ±10%）", value=30)
    gr.Markdown("**段落结构**（从上到下依次出现；素材类型可写多个，用“、”分隔；保留原声的段落按说话语句定长；在表格上点右键可以增加或删除行）")
    secs = gr.Dataframe(headers=SEC_HEADERS, interactive=True, wrap=True, row_count=(4, "dynamic"),
                        datatype=["str", "str", "str", "number", "number", "number", "str", "str", "str"])
    gr.Markdown(f"可用素材类型：{'、'.join(common.material_types())}")
    with gr.Accordion("文字内容", open=True):
        with gr.Row():
            lang = gr.Radio([(v, k) for k, v in schema.SUBTITLE_LANGS.items()], label="字幕语言", value="en")
            use_llm = gr.Checkbox(label="用 DeepSeek 自动生成标题和说明文字（需在设置中开启）")
        with gr.Row():
            hook = gr.Textbox(label="开场标题（英文/主语言）")
            hook_zh = gr.Textbox(label="开场标题（中文，双语时用）")
        with gr.Row():
            cta = gr.Textbox(label="结尾行动号召（英文/主语言）")
            cta_zh = gr.Textbox(label="结尾行动号召（中文）")
    with gr.Accordion("字幕样式", open=False):
        with gr.Row():
            font = gr.Textbox(label="字体名称", value="Noto Sans SC")
            fs = gr.Number(label="字号", value=64)
            fs_zh = gr.Number(label="中文字号（双语）", value=56)
            outline = gr.Number(label="描边粗细", value=4)
        with gr.Row():
            color = gr.ColorPicker(label="文字颜色", value="#FFFFFF")
            ocolor = gr.ColorPicker(label="描边颜色", value="#000000")
            pos = gr.Radio([(v, k) for k, v in schema.POSITIONS.items()], label="位置", value="bottom")
            box = gr.Checkbox(label="带底条")
    with gr.Accordion("画面、转场、配乐、品牌", open=False):
        with gr.Row():
            ttype = gr.Radio([(v, k) for k, v in schema.TRANSITIONS.items()], label="转场", value="fade")
            tdur = gr.Slider(0.1, 1.5, value=0.4, step=0.1, label="转场时长（秒）")
            fill = gr.Radio([(v, k) for k, v in schema.FILL_MODES.items()], label="横竖不一致时", value="blur")
            grade = gr.Checkbox(label="轻微统一调色")
        with gr.Row():
            mstyle = gr.Textbox(label="配乐风格（= 配乐/ 下的子文件夹名）")
            mtrack = gr.Textbox(label="指定曲目（可空，填文件名）")
            mvol = gr.Slider(0, 1, value=0.6, step=0.05, label="配乐音量")
        with gr.Row():
            logo = gr.Checkbox(label="加 logo 角标", value=True)
            endcard = gr.Checkbox(label="片尾品牌卡", value=True)
            cov_on = gr.Checkbox(label="生成封面", value=True)
            cov_title = gr.Textbox(label="封面标题")
    with gr.Row():
        save_btn = gr.Button("💾 保存", variant="primary")
        copy_btn = gr.Button("📄 复制为新模板")
        del_btn = gr.Button("🗑 删除模板", variant="stop")
    fields = [name, cities, aspects, duration, lang, hook, hook_zh, cta, cta_zh, use_llm, font, fs, fs_zh, color,
              ocolor, outline, pos, box, ttype, tdur, mstyle, mtrack, mvol, fill, logo, endcard, cov_on, cov_title,
              secs, grade]
    sel.change(lambda r: load(r) if r else tuple(gr.update() for _ in fields), sel, fields)
    new_btn.click(lambda: (gr.update(value=None), *load(None)), None, [sel, *fields])
    refresh.click(lambda: gr.update(choices=choices()), None, sel)
    save_btn.click(save, [sel, *fields], [sel, msg])
    copy_btn.click(save_as, fields, [sel, msg])
    del_btn.click(delete, sel, [sel, msg])
    return {"refresh": (lambda: gr.update(choices=choices()), None, sel)}
