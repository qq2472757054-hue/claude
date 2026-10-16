"""调用 ffmpeg / ffprobe：支持取消、记录完整命令与 stderr。"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import logs
from .paths import ffmpeg_bin, ffprobe_bin

log = logs.get("ffmpeg")

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Cancelled(Exception):
    """任务被用户取消。"""


class FFmpegError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-50:])
        super().__init__(f"ffmpeg 失败 (code {returncode})\n命令: {cmd_str(cmd)}\n{tail}")

    @property
    def tail(self) -> str:
        return "\n".join(self.stderr.strip().splitlines()[-50:])


def cmd_str(cmd: list[str]) -> str:
    return subprocess.list2cmdline([str(c) for c in cmd])


def run(cmd: list[str], *, cwd: str | Path | None = None,
        cancel: threading.Event | None = None, timeout: float | None = None,
        capture_stdout: bool = False, quiet: bool = False) -> subprocess.CompletedProcess:
    """运行外部命令。cancel 被置位时杀掉进程并抛出 Cancelled。"""
    cmd = [str(c) for c in cmd]
    stdout = subprocess.PIPE if capture_stdout else subprocess.DEVNULL
    proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=stdout,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []

    def _reader(stream, sink):
        for chunk in iter(lambda: stream.read(65536), b""):
            sink.append(chunk)

    threads = [threading.Thread(target=_reader, args=(proc.stderr, err_chunks), daemon=True)]
    if capture_stdout:
        threads.append(threading.Thread(target=_reader, args=(proc.stdout, out_chunks), daemon=True))
    for t in threads:
        t.start()
    start = time.monotonic()
    while True:
        try:
            proc.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled()
        if timeout is not None and time.monotonic() - start > timeout:
            proc.kill()
            proc.wait()
            raise FFmpegError(cmd, -1, "超时")
    for t in threads:
        t.join()
    stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
    stdout_b = b"".join(out_chunks)
    if proc.returncode != 0:
        err = FFmpegError(cmd, proc.returncode, stderr)
        if not quiet:
            log.error("命令失败: %s\n%s", cmd_str(cmd), err.tail)
        raise err
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout_b, stderr)


def ffmpeg(args: list[str], **kw) -> subprocess.CompletedProcess:
    return run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args], **kw)


def probe_raw(path: str | Path) -> dict[str, Any]:
    r = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", str(path)], capture_stdout=True, timeout=120)
    return json.loads(r.stdout.decode("utf-8", errors="replace") or "{}")


def _rotation(stream: dict) -> int:
    rot = 0
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            rot = int(float(tags["rotate"]))
        except ValueError:
            rot = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    return rot % 360


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        v = stream.get(key)
        if v and v != "0/0":
            try:
                f = float(Fraction(v))
                if f > 0:
                    return round(f, 3)
            except (ValueError, ZeroDivisionError):
                pass
    return 0.0


def probe(path: str | Path) -> dict[str, Any]:
    """返回 {duration, width, height(已按旋转修正), fps, rotation, has_audio, has_video, vcodec, acodec}。"""
    raw = probe_raw(path)
    streams = raw.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = raw.get("format") or {}
    dur = 0.0
    for src in (fmt.get("duration"), (v or {}).get("duration")):
        try:
            if src is not None:
                dur = float(src)
                break
        except ValueError:
            pass
    info = {
        "duration": dur,
        "width": 0, "height": 0, "fps": 0.0, "rotation": 0,
        "has_video": v is not None, "has_audio": a is not None,
        "vcodec": (v or {}).get("codec_name", ""),
        "acodec": (a or {}).get("codec_name", ""),
        "pix_fmt": (v or {}).get("pix_fmt", ""),
        "format_name": fmt.get("format_name", ""),
    }
    if v is not None:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = _rotation(v)
        if rot in (90, 270):
            w, h = h, w
        info.update(width=w, height=h, fps=_fps(v), rotation=rot)
    return info
