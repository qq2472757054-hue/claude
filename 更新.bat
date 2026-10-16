@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Updating program files...
git pull
if errorlevel 1 (
  echo.
  echo [!] Update failed. Is Git installed? Is the network OK?
  pause
  exit /b 1
)
if exist "python\python.exe" (
  set "PY=python\python.exe"
) else (
  set "PY=python"
)
echo Updating dependencies...
"%PY%" -m pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo Done. Double-click the start script to run.
pause
