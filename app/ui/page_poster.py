"""图文海报页面。"""
from __future__ import annotations

import gradio as gr

from ..imageio import grab_frame, open_image
from ..render import imgfx
from ..render.poster import LAYOUTS, SIZES, render_poster, save_poster
from . import common


def library_sources(city):
    """可选的素材库图片 / 视频片段（取片段中间帧）。"""
    c = common.ctx()
    if c is None:
        return gr.update(choices=[])
    sql = ("SELECT c.id AS cid, c.start, c.\"end\", m.rel_path, m.is_image, m.type, m.spot FROM clip c "
           "JOIN material m ON m.id = c.material_id WHERE m.publishable = '是' AND m.status != '丢失'")
    args = []
    if city and city != "全部":
        sql += " AND m.city = ?"
        args.append(city)
    rows = c.db.all(sql + " ORDER BY m.rating DESC, m.rel_path LIMIT 400", args)
    choices = []
    for r in rows:
        name = r["rel_path"].split("/")[-1]
        label = f"{'🖼' if r['is_image'] else '🎞'} {r['type'] or ''} {r['spot'] or ''} {name}" + (
            "" if r["is_image"] else f" @{(r['start'] + r['end']) / 2:.1f}s")
        choices.append((label, r["cid"]))
    return gr.update(choices=choices, value=[])


def _images(uploads, clip_ids):
    c = common.need_ctx()
    ims = []
    for f in uploads or []:
        path = f if isinstance(f, str) else getattr(f, "name", None)
        if path:
            ims.append(open_image(path))
    for cid in clip_ids or []:
        r = c.db.one("SELECT c.start, c.\"end\", m.rel_path, m.is_image FROM clip c JOIN material m "
                     "ON m.id = c.material_id WHERE c.id = ?", (int(cid),))
        if not r:
            continue
        p = c.lib.abs(r["rel_path"])
        im = open_image(p) if r["is_image"] else grab_frame(p, (r["start"] + r["end"]) / 2, None)
        if im is not None:
            ims.append(im)
    if not ims:
        raise gr.Error("请上传图片或从素材库选择 1–4 张图片")
    return ims[:4]


def _points(*pts):
    return [p for p in pts if p and p.strip()]


def preview(uploads, clip_ids, layout, title, subtitle, p1, p2, p3, p4):
    c = common.need_ctx()
    ims = _images(uploads, clip_ids)
    im = render_poster(ims, layout, "4:5", title or "", subtitle or "", _points(p1, p2, p3, p4),
                       common.settings()["brand"], imgfx.find_font(c.lib.fonts),
                       c.lib.abs(common.settings()["brand"].get("logo") or "品牌/logo.png"))
    return im


def save(uploads, clip_ids, layout, sizes, city, title, subtitle, p1, p2, p3, p4):
    c = common.need_ctx()
    if not sizes:
        raise gr.Error("请选择至少一个尺寸")
    if not (title or "").strip():
        raise gr.Error("请填写标题")
    ims = _images(uploads, clip_ids)
    ids = save_poster(c, ims, layout, list(sizes), title.strip(), (subtitle or "").strip(),
                      _points(p1, p2, p3, p4), common.settings()["brand"], "" if city == "全部" else city)
    return f"已生成 {len(ids)} 张海报，放在 输出/待审核/，请到“审核”页查看。"


def build():
    with gr.Row():
        with gr.Column():
            uploads = gr.File(label="上传图片（1–4 张）", file_count="multiple", file_types=["image"])
            city = gr.Dropdown(["全部"] + common.cities(), value="全部", label="城市（筛选素材库 / 用于命名）")
            clips = gr.Dropdown(label="或从素材库选择（图片或视频片段截帧）", multiselect=True, choices=[])
            load_btn = gr.Button("载入素材库列表")
            layout = gr.Radio([(v, k) for k, v in LAYOUTS.items()], value="single", label="版式")
            sizes = gr.CheckboxGroup(list(SIZES.keys()), value=["4:5"], label="尺寸")
            title = gr.Textbox(label="标题")
            subtitle = gr.Textbox(label="副标题")
            p1 = gr.Textbox(label="要点 1")
            p2 = gr.Textbox(label="要点 2")
            p3 = gr.Textbox(label="要点 3")
            p4 = gr.Textbox(label="要点 4")
            with gr.Row():
                prev_btn = gr.Button("👀 预览")
                save_btn = gr.Button("💾 生成海报", variant="primary")
            msg = gr.Markdown()
        with gr.Column():
            out = gr.Image(label="预览（4:5）", interactive=False, height=760)
    gr.Markdown("底部品牌条的品牌名、WhatsApp / LINE / 网址取自“设置”页。")
    load_btn.click(library_sources, city, clips)
    city.change(library_sources, city, clips)
    inputs = [uploads, clips, layout, title, subtitle, p1, p2, p3, p4]
    prev_btn.click(preview, inputs, out)
    save_btn.click(save, [uploads, clips, layout, sizes, city, title, subtitle, p1, p2, p3, p4], msg)
    return {}
