"""打包 Windows 便携版（在 Windows 上运行，需要联网）。

产物：dist/酷玩混剪工具/  —— 内含 Python 嵌入版、全部依赖、FFmpeg、程序代码、启动.bat。
整个文件夹拷到任何一台 Windows 10/11 电脑上，双击 启动.bat 即可运行。

用法：python tools/build_portable.py [--python 3.11.9] [--ffmpeg-url URL] [--pip-index URL] [--zip]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-6.1.1-essentials_build.zip"
COPY = ["app", "assets", "prompts", "tools", "启动.bat", "requirements.txt", "README.md", "docs"]


def download(url: str, dst: Path) -> Path:
    print(f"下载 {url}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default="3.11.9")
    ap.add_argument("--ffmpeg-url", default=FFMPEG_URL)
    ap.add_argument("--pip-index", default="", help="如 https://pypi.tuna.tsinghua.edu.cn/simple")
    ap.add_argument("--out", default=str(ROOT / "dist" / "酷玩混剪工具"))
    ap.add_argument("--zip", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    cache = ROOT / "dist" / "_cache"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1) 程序代码
    for name in COPY:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, out / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif src.is_file():
            shutil.copy2(src, out / name)

    # 2) Python 嵌入版
    ver = a.python
    pz = cache / f"python-{ver}-embed-amd64.zip"
    if not pz.exists():
        download(f"https://www.python.org/ftp/python/{ver}/python-{ver}-embed-amd64.zip", pz)
    pydir = out / "python"
    with zipfile.ZipFile(pz) as z:
        z.extractall(pydir)
    pth = next(pydir.glob("python*._pth"))
    lines = [l for l in pth.read_text().splitlines() if l.strip() and not l.startswith("#import")]
    lines += ["..", "Lib\\site-packages", "import site"]   # ".." = 程序根目录，便于 python -m app.main
    pth.write_text("\n".join(lines) + "\n")
    gp = cache / "get-pip.py"
    if not gp.exists():
        download("https://bootstrap.pypa.io/get-pip.py", gp)
    py = pydir / "python.exe"
    idx = ["-i", a.pip_index] if a.pip_index else []
    subprocess.check_call([str(py), str(gp), "--no-warn-script-location", *idx])
    subprocess.check_call([str(py), "-m", "pip", "install", "--no-warn-script-location", *idx,
                           "-r", str(out / "requirements.txt")])

    # 3) FFmpeg
    fz = cache / Path(a.ffmpeg_url).name
    if not fz.exists():
        download(a.ffmpeg_url, fz)
    vend = out / "vendor" / "ffmpeg"
    vend.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(fz) as z:
        for n in z.namelist():
            if n.endswith(("/bin/ffmpeg.exe", "/bin/ffprobe.exe")) or n.endswith("LICENSE"):
                with z.open(n) as src, open(vend / Path(n).name, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    # 4) 自检
    subprocess.check_call([str(py), "-c", "import app.main, gradio, cv2, scenedetect, faster_whisper, PIL"],
                          cwd=out)
    print(f"\n完成：{out}")
    if a.zip:
        z = shutil.make_archive(str(out), "zip", out.parent, out.name)
        print(f"压缩包：{z}")


if __name__ == "__main__":
    sys.exit(main())
