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
    echo [1/5] Creating Python 3.12 virtual environment...
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
    echo [1/5] Virtual environment is ready.
)

if not exist ".venv\.mahanbot_dependencies_ready" (
    echo [2/5] Installing core packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto :install_error
    ".venv\Scripts\python.exe" -m pip install -r requirements-unified.txt
    if errorlevel 1 goto :install_error
    type nul > ".venv\.mahanbot_dependencies_ready"
) else (
    echo [2/5] Core packages are ready.
)

if not exist ".venv\.mahanbot_model_runtime_ready" (
    echo [3/5] Installing offline model runtime...
    ".venv\Scripts\python.exe" -m pip install -r requirements-model.txt
    if errorlevel 1 goto :model_install_error
    type nul > ".venv\.mahanbot_model_runtime_ready"
) else (
    echo [3/5] Offline model runtime is ready.
)

 echo [4/5] Checking Windows private-network firewall...
netsh advfirewall firewall show rule name="MahanBot SMS Notify 8010" >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process netsh -ArgumentList 'advfirewall firewall add rule name=\"MahanBot SMS Notify 8010\" dir=in action=allow protocol=TCP localport=8010 profile=private' -Verb RunAs -Wait" >nul 2>&1
)

 echo [5/5] Starting dashboard, database, model lab and SMS service...
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
 echo Core package installation failed. Check your internet connection and run this file again.
pause
exit /b 1

:model_install_error
 echo.
 echo Offline model runtime installation failed. Check your internet connection and free disk space, then run this file again.
pause
exit /b 1
