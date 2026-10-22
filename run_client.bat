@echo off
chcp 65001 >nul
title MAHANBOT 2.0 - CLIENT EDITION
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set MAHANBOT_EDITION=CLIENT

if exist .venv\Scripts\python.exe (
    set "PY_EXE=.venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

echo ========================================================
echo   MAHANBOT 2.0 — نسخه متقاضی / کاربری (CLIENT)
echo ========================================================
echo   سامانه ثبت‌نام: http://127.0.0.1:8000
echo ========================================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000"

"%PY_EXE%" server.py
pause
