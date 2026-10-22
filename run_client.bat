@echo off
chcp 65001 >nul
title MAHANBOT 2.0 - CLIENT EDITION
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set MAHANBOT_EDITION=CLIENT

:: Local bundled Chromium detection
if exist "%~dp0browsers\chromium-1194" (
    set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
)

:: Python executable resolution
if exist "%~dp0runtime\python.exe" (
    set "PY_EXE=%~dp0runtime\python.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

echo ========================================================
echo   MAHANBOT 2.0 — نسخه متقاضی / مشتریان (CLIENT)
echo ========================================================
echo   سامانه ثبت‌نام: http://127.0.0.1:8000
echo ========================================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000"

"%PY_EXE%" server.py
pause
