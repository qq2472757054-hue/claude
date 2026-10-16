"""启动入口：初始化素材库与数据库，启动后台分析和任务队列，打开浏览器。

用法：python -m app.main [--port 7860] [--no-browser]
"""
from __future__ import annotations

import argparse
import socket
import sys

from . import VERSION, config, logs, state


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def startup() -> tuple[list[str], bool]:
    """启动检查：中断任务恢复、一致性检查、扫描新素材。返回给用户看的提示。"""
    from .analysis.worker import worker
    from .jobs.queue import queue, recover
    from .library import mover, scanner
    from .paths import ffmpeg_bin

    notes: list[str] = []
    logs.setup(None)
    import shutil

    if not shutil.which(ffmpeg_bin()) and not __import__("os").path.isfile(ffmpeg_bin()):
        notes.append("❌ 找不到 ffmpeg，无法分析和生成视频。请确认 vendor/ffmpeg/ 中有 ffmpeg 和 ffprobe。")
    ctx = state.get()
    if ctx is None:
        if config.load().get("library_root"):
            notes.append("⚠️ 设置中的素材库目录无法打开，请到“设置”页重新选择。")
        return notes, False
    log = logs.get("main")
    log.info("启动 v%s，素材库：%s", VERSION, ctx.lib.root)
    notes += recover(ctx)
    notes += mover.consistency_check(ctx)
    try:
        rep = scanner.scan(ctx)
        if rep.new or rep.duplicates or rep.errors or rep.relocated:
            notes.append("启动扫描：" + rep.summary().replace("\n", "；"))
        scanner.scan_music(ctx)
    except Exception as e:
        log.exception("启动扫描失败")
        notes.append(f"启动扫描失败：{e}")
    worker.start()
    queue.start()
    return notes, True


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="酷玩假期 旅游短视频混剪工具")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    notes, has_lib = startup()
    import gradio as gr

    from .ui.app import CSS, build

    demo, _holder = build(notes, has_lib)
    ctx = state.get()
    allowed = [str(ctx.lib.root.resolve())] if ctx else []
    port = _free_port(args.port)
    print(f"\n酷玩假期 短视频混剪工具 v{VERSION}\n请在浏览器打开：http://127.0.0.1:{port}\n"
          f"（关闭这个窗口即退出工具）\n", flush=True)
    demo.queue(default_concurrency_limit=4).launch(
        server_name="127.0.0.1", server_port=port, inbrowser=not args.no_browser,
        allowed_paths=allowed, theme=gr.themes.Soft(), css=CSS, show_error=True, quiet=True,
        max_file_size="2gb")


if __name__ == "__main__":
    sys.exit(main())
