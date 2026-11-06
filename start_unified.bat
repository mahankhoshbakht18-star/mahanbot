@echo off
chcp 65001 >nul
title MAHANBOT Unified Selector Runner
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist .venv\Scripts\python.exe (
    echo [ERROR] Virtual environment not found in .venv!
    pause
    exit /b 1
)

echo Starting MAHANBOT Unified Selector Runner...
echo.

.venv\Scripts\python.exe run_unified.py %*
pause
