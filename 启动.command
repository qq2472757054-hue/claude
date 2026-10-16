#!/bin/bash
# macOS 启动脚本：双击运行。第一次运行会自动安装依赖（需要联网一次）。
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
if [ -x "python/bin/python3" ]; then
  PY="python/bin/python3"
else
  if [ ! -x ".venv/bin/python3" ]; then
    echo "第一次运行：正在准备运行环境（需要几分钟）…"
    python3 -m venv .venv || { echo "需要先安装 Python 3.11 或更新版本：https://www.python.org/downloads/"; read -r; exit 1; }
    .venv/bin/python3 -m pip install -r requirements.txt || { echo "安装依赖失败，请检查网络后重试"; read -r; exit 1; }
  fi
  PY=".venv/bin/python3"
fi
if [ ! -x "vendor/ffmpeg/ffmpeg" ] && ! command -v ffmpeg >/dev/null 2>&1; then
  echo "找不到 ffmpeg：请把 ffmpeg 和 ffprobe 放进 vendor/ffmpeg/，或用 Homebrew 安装（brew install ffmpeg）。"
  read -r
  exit 1
fi
"$PY" -m app.main "$@"
