#!/bin/bash
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 app.py
else
  echo "没有找到 python3。请先安装 Python 3.9 或更高版本：https://www.python.org/downloads/"
  read -r -p "按回车键关闭…"
fi
