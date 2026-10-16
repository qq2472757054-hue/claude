@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if exist "python\python.exe" (
  set "PY=python\python.exe"
) else (
  set "PY=python"
)
"%PY%" -m app.main %*
if errorlevel 1 (
  echo.
  echo [!] Program exited with an error. See the message above, or the log folder.
  pause
)
