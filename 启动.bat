@echo off
chcp 65001 >nul
title Lyric2TTML - Lyrics to TTML Web UI
cd /d "%~dp0"

echo ========================================
echo   Lyric2TTML web server
echo   http://127.0.0.1:8765
echo ========================================
echo.

set "PYTHONIOENCODING=utf-8"

rem Prefer the project venv (has pypinyin + speaker model deps), fall back to system python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8765"

"%PY%" web_server.py

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start.
  echo         Python 3.10+ is required: https://www.python.org/downloads/
  echo         Then install dependencies: pip install -r requirements.txt
)

pause
