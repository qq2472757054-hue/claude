@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 准绳 · 利润核算
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo 没有找到 Python。请先安装 Python 3.9 或更高版本：https://www.python.org/downloads/
echo 安装时请勾选 "Add Python to PATH"。
goto end
:use_py
py -3 app.py
goto end
:use_python
python app.py
:end
pause
