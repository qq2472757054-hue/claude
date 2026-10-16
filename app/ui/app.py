"""组装界面。"""
from __future__ import annotations

import gradio as gr

from .. import VERSION
from . import (page_generate, page_library, page_music, page_poster, page_review, page_settings,
               page_templates)

CSS = """
.gradio-container {max-width: 1500px !important}
footer {display: none !important}
"""


def build(startup_notes: list[str], has_library: bool) -> tuple[gr.Blocks, dict]:
    holder: dict = {}
    with gr.Blocks(title="酷玩假期 · 短视频混剪工具") as demo:
        gr.Markdown(f"## 🎬 酷玩假期 · 旅游短视频混剪工具　<small>v{VERSION}　本地运行，不上传素材</small>")
        if startup_notes:
            with gr.Accordion(f"启动检查：{len(startup_notes)} 条提示", open=True):
                gr.Markdown("\n".join(f"- {n}" for n in startup_notes[:50]))
        with gr.Tabs(selected="library" if has_library else "settings") as tabs:
            with gr.Tab("素材库", id="library") as t_lib:
                lib = page_library.build()
            with gr.Tab("配乐", id="music") as t_music:
                music = page_music.build()
            with gr.Tab("模板", id="templates") as t_tpl:
                tpl = page_templates.build()
            with gr.Tab("生成", id="generate") as t_gen:
                gen = page_generate.build()
            with gr.Tab("审核", id="review") as t_rev:
                rev = page_review.build()
            with gr.Tab("海报", id="poster"):
                page_poster.build()
            with gr.Tab("设置", id="settings") as t_set:
                st = page_settings.build(holder)
        # 切换到某页时刷新该页数据
        fn, ins, outs = lib["load"]
        t_lib.select(fn, ins, outs)
        demo.load(fn, ins, outs)
        t_music.select(*music["load"])
        demo.load(*music["load"])
        t_tpl.select(*tpl["refresh"])
        t_gen.select(*gen["refresh"])
        t_rev.select(*rev["refresh"])
        t_set.select(*st["load"])
        demo.load(*st["load"])
    holder["blocks"] = demo
    return demo, holder
