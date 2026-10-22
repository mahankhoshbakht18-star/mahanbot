@echo off
chcp 65001 >nul
title MAHANBOT Server
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist .venv\Scripts\python.exe (
    echo [ERROR] Virtual environment not found!
    echo Please run 'install.bat' first to install required components.
    echo.
    pause
    exit /b 1
)

echo Starting MAHANBOT server...
echo Access Web Dashboard at: http://127.0.0.1:8000
echo.

:: Launch default browser after 2 seconds in background
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000"

.venv\Scripts\python.exe server.py
pause
