@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title MahanBot - Unified One Click

 echo.
 echo ================================================
 echo        MahanBot - Unified One Click
 echo ================================================
 echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating Python 3.12 virtual environment...
    py -3.12 -m venv .venv >nul 2>&1
    if errorlevel 1 (
        python -m venv .venv
        if errorlevel 1 (
            echo.
            echo Python 3.12 was not found. Install Python and try again.
            pause
            exit /b 1
        )
    )
) else (
    echo [1/4] Virtual environment is ready.
)

if not exist ".venv\.mahanbot_dependencies_ready" (
    echo [2/4] Installing required packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto :install_error
    ".venv\Scripts\python.exe" -m pip install -r requirements-unified.txt
    if errorlevel 1 goto :install_error
    type nul > ".venv\.mahanbot_dependencies_ready"
) else (
    echo [2/4] Required packages are ready.
)

 echo [3/4] Checking Windows private-network firewall...
netsh advfirewall firewall show rule name="MahanBot SMS Notify 8010" >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process netsh -ArgumentList 'advfirewall firewall add rule name=\"MahanBot SMS Notify 8010\" dir=in action=allow protocol=TCP localport=8010 profile=private' -Verb RunAs -Wait" >nul 2>&1
)

 echo [4/4] Starting dashboard, database and SMS notification service...
 echo Close this window or press Ctrl+C to stop MahanBot.
 echo.
".venv\Scripts\python.exe" unified_launcher.py
set EXIT_CODE=%ERRORLEVEL%
 echo.
 echo MahanBot stopped with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:install_error
 echo.
 echo Package installation failed. Check your internet connection and run this file again.
pause
exit /b 1
