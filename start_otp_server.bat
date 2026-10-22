@echo off
chcp 65001 >nul
title MAHANBOT — Dedicated OTP Server
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist .venv\Scripts\python.exe (
    echo [ERROR] Virtual environment not found! Please run 'install.bat' first.
    pause
    exit /b 1
)

echo Starting Dedicated OTP Relay Server on port 8010...
.venv\Scripts\python.exe otp_server.py
pause
