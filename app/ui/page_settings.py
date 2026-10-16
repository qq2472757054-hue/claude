"""设置页面。"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import gradio as gr

from .. import VERSION, config, state
from ..analysis.worker import worker
from ..asr import model_manager
from ..library import mover
from ..llm import deepseek
from . import common

_download_state = {"running": False, "msg": ""}


def default_root() -> str:
    if os.name == "nt":
        for drive in ("D:", "E:"):
            if Path(drive + "/").exists():
                return f"{drive}/酷玩素材库"
    return str(Path.home() / "酷玩素材库")


def browse_folder(current):
    """用本机文件夹对话框选择（工具运行在本机，可直接弹窗）。"""
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="选择素材库文件夹", initialdir=current or str(Path.home()))
        root.destroy()
        return chosen or current
    except Exception:
        gr.Warning("无法打开文件夹选择窗口，请直接在输入框里填写路径")
        return current


def apply_root(root, app_holder):
    root = (root or "").strip().strip('"')
    if not root:
        raise gr.Error("请填写素材库位置")
    p = Path(root).expanduser()
    try:
        c = state.init_library(str(p))
    except OSError as e:
        raise gr.Error(f"无法使用这个文件夹：{e}")
    common.allow_path(app_holder.get("blocks"), c.lib.root)
    from ..jobs.queue import queue, recover
    notes = recover(c) + mover.consistency_check(c)
    queue.start()
    worker.kick()
    msg = f"素材库已设置为：`{c.lib.root}`，目录结构已创建。请把素材拷进 `待使用/<城市>/` 后到“素材库”页点“扫描新素材”。"
    if notes:
        msg += "\n\n" + "\n".join("- " + n for n in notes[:30])
    return msg


def _list(s: str) -> list[str]:
    s = (s or "").replace("，", "、").replace(",", "、").replace("\n", "、")
    out = []
    for x in s.split("、"):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


def save_settings(cities, types, name_zh, name_en, phone, whatsapp, line, website, model_size, hf, llm_on,
                  api_key, llm_model, allow_used, overlap, move_rule, hw, keep_tmp):
    s = config.load()
    s["cities"] = _list(cities) or s["cities"]
    s["material_types"] = _list(types) or s["material_types"]
    s["brand"].update({"name_zh": name_zh, "name_en": name_en, "phone": phone, "whatsapp": whatsapp,
                       "line": line, "website": website})
    old_size = s["asr"]["model_size"]
    s["asr"].update({"model_size": model_size, "hf_endpoint": (hf or "").strip()})
    s["llm"].update({"enabled": bool(llm_on), "api_key": (api_key or "").strip(), "model": llm_model or "deepseek-chat"})
    s["render"].update({"allow_used_materials": bool(allow_used), "max_overlap_in_batch": float(overlap),
                        "move_rule": move_rule, "hw_encoder": hw, "keep_failed_temp": bool(keep_tmp)})
    config.save(s)
    c = state.get()
    if c is not None:
        c.lib.ensure(s["cities"])
    if old_size != model_size:
        worker.model_ready_again()
    return "设置已保存。（城市、类型列表在其他页面刷新后生效）"


def upload_logo(file):
    c = common.need_ctx()
    if not file:
        raise gr.Error("请选择 logo 图片（建议透明背景 png）")
    dst = c.lib.brand / "logo.png"
    from PIL import Image

    with Image.open(file) as im:
        im.convert("RGBA").save(dst)
    config.update(brand={"logo": "品牌/logo.png"})
    return str(dst), "logo 已更新。"


def model_status():
    c = state.get()
    s = config.load()
    size = s["asr"]["model_size"]
    if c is None:
        return "请先设置素材库"
    ready = model_manager.is_ready(c.lib.models, size)
    txt = f"当前模型：{size}　状态：{'✅ 已就绪' if ready else '❌ 未下载'}"
    if _download_state["running"]:
        txt += "\n\n⏳ 正在下载…（模型较大，small 约 480MB，请耐心等待）"
    elif _download_state["msg"]:
        txt += "\n\n" + _download_state["msg"]
    if not ready and not _download_state["running"]:
        txt += "\n\n" + model_manager.manual_hint(c.lib.models, size).replace("\n", "\n\n")
    return txt


def download_model():
    c = common.need_ctx()
    if _download_state["running"]:
        return model_status()
    s = config.load()

    def run():
        _download_state.update(running=True, msg="")
        try:
            model_manager.download(c.lib.models, s["asr"]["model_size"], s["asr"].get("hf_endpoint", ""))
            _download_state["msg"] = "✅ 下载完成，语音识别会自动开始。"
            worker.model_ready_again()
        except Exception as e:
            _download_state["msg"] = f"❌ {e}"
        finally:
            _download_state["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return model_status()


def recheck_model():
    worker.model_ready_again()
    return model_status()


def llm_test(api_key, model):
    s = config.load()
    s["llm"].update({"api_key": (api_key or "").strip(), "model": model or "deepseek-chat"})
    return deepseek.test_connection(s)


def llm_usage():
    c = state.get()
    if c is None:
        return ""
    u = deepseek.month_usage(c)
    return f"本月 DeepSeek 调用 {u['calls']} 次，输入 {u['prompt_tokens']} tokens，输出 {u['completion_tokens']} tokens。"


def run_check():
    c = common.need_ctx()
    notes = mover.consistency_check(c)
    return "一致性检查完成，" + (f"修正 {len(notes)} 项：\n" + "\n".join("- " + n for n in notes[:50])
                              if notes else "数据库与文件一致。")


def info_md():
    c = state.get()
    logs = c.lib.logs if c else "（未设置素材库）"
    free = f"{shutil.disk_usage(c.lib.root).free / 1024 ** 3:.1f} GB" if c else "—"
    return (f"版本 **v{VERSION}**　日志目录：`{logs}`　素材库磁盘剩余：{free}\n\n"
            f"设置文件：`{config.SETTINGS_PATH}`")


def open_logs():
    c = common.need_ctx()
    try:
        if os.name == "nt":
            os.startfile(str(c.lib.logs))  # type: ignore[attr-defined]
        elif shutil.which("open"):
            os.system(f'open "{c.lib.logs}"')
        else:
            os.system(f'xdg-open "{c.lib.logs}" >/dev/null 2>&1 &')
        return "已打开日志文件夹"
    except Exception as e:
        return f"打不开：{e}。日志在 {c.lib.logs}"


def build(app_holder: dict):
    s = config.load()
    gr.Markdown("### ① 素材库位置")
    with gr.Row():
        root = gr.Textbox(label="素材库根目录", value=s.get("library_root") or default_root(), scale=4)
        browse = gr.Button("📁 选择文件夹…", scale=1)
        apply_btn = gr.Button("使用这个位置", variant="primary", scale=1)
    root_msg = gr.Markdown("" if s.get("library_root") else
                           "👋 第一次使用：请选择（或填写）一个文件夹作为素材库，点“使用这个位置”，工具会自动建好全部子文件夹。")
    gr.Markdown("### ② 城市与素材类型")
    with gr.Row():
        cities = gr.Textbox(label="城市列表（用、分隔）", value="、".join(s["cities"]))
        types = gr.Textbox(label="素材类型（用、分隔）", value="、".join(s["material_types"]))
    gr.Markdown("### ③ 品牌信息（片尾卡、海报品牌条、封面 logo 使用）")
    b = s["brand"]
    with gr.Row():
        name_zh = gr.Textbox(label="品牌名（中）", value=b["name_zh"])
        name_en = gr.Textbox(label="品牌名（英）", value=b["name_en"])
        phone = gr.Textbox(label="电话", value=b["phone"])
    with gr.Row():
        whatsapp = gr.Textbox(label="WhatsApp", value=b["whatsapp"])
        line = gr.Textbox(label="LINE", value=b["line"])
        website = gr.Textbox(label="网址", value=b["website"])
    with gr.Row():
        logo_file = gr.File(label="上传 logo（png，透明背景最好）", file_types=["image"])
        logo_btn = gr.Button("更新 logo")
        logo_prev = gr.Image(label="当前 logo", height=120, interactive=False)
    gr.Markdown("### ④ 语音识别（本地运行，不上传任何内容）")
    with gr.Row():
        model_size = gr.Radio(["base", "small", "medium"], value=s["asr"]["model_size"],
                              label="模型大小（越大越准越慢；普通笔记本推荐 small）")
        hf = gr.Textbox(label="模型下载镜像地址（国内可填 https://hf-mirror.com）", value=s["asr"]["hf_endpoint"])
    with gr.Row():
        dl_btn = gr.Button("⬇️ 下载模型")
        recheck_btn = gr.Button("我已手动放好模型，重新检测")
    model_md = gr.Markdown(model_status)
    gr.Markdown("### ⑤ DeepSeek 文案（可选，默认关闭）")
    with gr.Row():
        llm_on = gr.Checkbox(label="启用 DeepSeek", value=s["llm"]["enabled"])
        api_key = gr.Textbox(label="API Key", value=s["llm"]["api_key"], type="password")
        llm_model = gr.Textbox(label="模型名", value=s["llm"]["model"])
        test_btn = gr.Button("测试连接")
    llm_md = gr.Markdown(llm_usage)
    gr.Markdown("### ⑥ 生成规则")
    r = s["render"]
    with gr.Row():
        allow_used = gr.Checkbox(label="允许使用“已使用”的素材（优先级更低）", value=r["allow_used_materials"])
        overlap = gr.Slider(0, 1, value=r["max_overlap_in_batch"], step=0.05, label="同批次两条视频素材重合率上限")
    with gr.Row():
        move_rule = gr.Radio([("用过任一片段就移到 已使用/", "any_clip_used"),
                              ("片段全部用完才移动", "all_clips_used")], value=r["move_rule"], label="素材移动规则")
        hw = gr.Radio([("自动使用显卡/核显加速", "auto"), ("关闭硬件加速", "off")], value=r["hw_encoder"],
                      label="硬件编码")
        keep_tmp = gr.Checkbox(label="渲染失败时保留临时文件供排查", value=r.get("keep_failed_temp", True))
    save_btn = gr.Button("💾 保存设置", variant="primary")
    save_msg = gr.Markdown()
    gr.Markdown("### ⑦ 维护")
    with gr.Row():
        check_btn = gr.Button("检查数据库与文件是否一致")
        logs_btn = gr.Button("打开日志文件夹")
    maint_msg = gr.Markdown()
    gr.Markdown(info_md)

    browse.click(browse_folder, root, root)
    apply_btn.click(lambda r: apply_root(r, app_holder), root, root_msg)
    save_btn.click(save_settings, [cities, types, name_zh, name_en, phone, whatsapp, line, website, model_size, hf,
                                   llm_on, api_key, llm_model, allow_used, overlap, move_rule, hw, keep_tmp],
                   save_msg).then(model_status, None, model_md)
    logo_btn.click(upload_logo, logo_file, [logo_prev, save_msg])
    dl_btn.click(download_model, None, model_md)
    recheck_btn.click(recheck_model, None, model_md)
    test_btn.click(llm_test, [api_key, llm_model], llm_md)
    check_btn.click(run_check, None, maint_msg)
    logs_btn.click(open_logs, None, maint_msg)
    t = gr.Timer(3)
    t.tick(model_status, None, model_md)

    def current_logo():
        c = state.get()
        if c is None:
            return None
        p = c.lib.abs(config.load()["brand"].get("logo") or "品牌/logo.png")
        return str(p) if p.is_file() else None

    return {"load": (current_logo, None, logo_prev)}
