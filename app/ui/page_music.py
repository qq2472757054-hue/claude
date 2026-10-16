"""配乐页面：登记来源、授权说明、能否商用。"""
from __future__ import annotations

import gradio as gr

from ..library import scanner
from . import common

HEADERS = ["ID", "文件", "风格", "来源", "授权说明", "能否商用", "时长(秒)", "使用次数", "文件状态"]


def load():
    c = common.ctx()
    if c is None:
        return [["", "", "", "", "", "", "", "", ""]], common.NO_LIBRARY
    rows = c.db.all("SELECT * FROM music ORDER BY style, rel_path")
    data = [[r["id"], r["rel_path"].split("/", 1)[-1], r["style"], r["source"], r["license_note"],
             r["commercial_ok"], round(r["duration"] or 0, 1), r["use_count"], "丢失" if r["missing"] else "正常"]
            for r in rows]
    ok = sum(1 for r in rows if r["commercial_ok"] == "是" and not r["missing"])
    styles = sorted({r["style"] for r in rows if r["commercial_ok"] == "是" and not r["missing"]})
    md = (f"共 {len(rows)} 首，其中 **{ok}** 首“能否商用 = 是”可参与自动选曲"
          + (f"（风格：{'、'.join(styles)}）" if styles else "") + "。"
          "\n\n把曲目放进 `配乐/<风格>/` 文件夹（子文件夹名就是风格），再点“扫描配乐”。"
          "**只有“能否商用 = 是”的曲目才会被用到视频里。**")
    return data or [["", "", "", "", "", "", "", "", ""]], md


def scan():
    c = common.need_ctx()
    n = scanner.scan_music(c)
    data, md = load()
    return data, f"新登记 {n} 首。\n\n" + md


def save(df):
    c = common.need_ctx()
    n, problems = 0, []
    for r in common.to_rows(df):
        if r[0] in (None, "") or str(r[0]) == "nan":
            continue
        ok = str(r[5] or "").strip()
        if ok not in ("是", "否", "未确认"):
            problems.append(f"#{r[0]} 能否商用只能填 是/否/未确认")
            continue
        c.db.update("music", int(r[0]), {"source": str(r[3] or ""), "license_note": str(r[4] or ""),
                                         "commercial_ok": ok})
        n += 1
    data, md = load()
    return data, f"已保存 {n} 首。" + ("\n" + "\n".join(problems) if problems else "") + "\n\n" + md


def preview(df, evt: gr.SelectData):
    c = common.need_ctx()
    rows = common.to_rows(df)
    r = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    if r is None or r >= len(rows) or rows[r][0] in (None, ""):
        return None
    m = c.db.one("SELECT rel_path FROM music WHERE id = ?", (int(rows[r][0]),))
    p = c.lib.abs(m["rel_path"]) if m else None
    return str(p) if p and p.is_file() else None


def build():
    md = gr.Markdown()
    with gr.Row():
        scan_btn = gr.Button("🔍 扫描配乐", variant="primary")
        save_btn = gr.Button("💾 保存修改")
        refresh = gr.Button("🔄 刷新")
    table = gr.Dataframe(headers=HEADERS, interactive=True, static_columns=[0, 1, 2, 6, 7, 8], wrap=True,
                         datatype=["number", "str", "str", "str", "str", "str", "number", "number", "str"])
    player = gr.Audio(label="试听（点击表格中的一行）", interactive=False)
    scan_btn.click(scan, None, [table, md])
    save_btn.click(save, table, [table, md])
    refresh.click(load, None, [table, md])
    table.select(preview, table, player)
    return {"load": (load, None, [table, md])}
