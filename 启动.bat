@echo off
chcp 65001 >nul
title Lyric2TTML - 歌词转逐字 TTML
cd /d "%~dp0"

echo ========================================
echo   Lyric2TTML
echo   桌面窗口优先；没有 PySide6 就用浏览器界面
echo ========================================
echo.

set "PYTHONIOENCODING=utf-8"

rem 优先用项目里的 venv（装了 pypinyin / 声纹依赖），否则退回系统 python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

rem ⚠️ 直接跑 launcher.py 而不是 web_server.py：
rem 这样"有 Qt 就开桌面窗口、没有就退回浏览器界面"的逻辑只有一份
rem （launcher.main），不会出现"bat 和 exe 行为不一样"。
"%PY%" launcher.py

if errorlevel 1 (
  echo.
  echo [错误] 启动失败。
  echo       需要 Python 3.10+：https://www.python.org/downloads/
  echo       然后装依赖：pip install -r requirements.txt
  echo       想用桌面窗口再装：pip install -r requirements-desktop.txt
)

pause
