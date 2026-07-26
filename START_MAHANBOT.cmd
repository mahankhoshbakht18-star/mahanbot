@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title MahanBot - Unified One Click
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

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
            echo Python 3.12 was not found. Install 64-bit Python 3.12 and try again.
            pause
            exit /b 1
        )
    )
) else (
    echo [1/5] Virtual environment is ready.
)

for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -LiteralPath 'requirements-unified.txt' -Algorithm SHA256).Hash.ToLower()"`) do set "CORE_HASH=%%H"
if not defined CORE_HASH goto :install_error
set "CORE_MARKER=.venv\.mahanbot_core_!CORE_HASH!"
if not exist "!CORE_MARKER!" (
    echo [2/5] Installing or updating core packages...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
    if errorlevel 1 goto :install_error
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-unified.txt
    if errorlevel 1 goto :install_error
    del /q ".venv\.mahanbot_core_*" >nul 2>&1
    type nul > "!CORE_MARKER!"
) else (
    echo [2/5] Core packages match the current requirements.
)

for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -LiteralPath 'requirements-model.txt' -Algorithm SHA256).Hash.ToLower()"`) do set "MODEL_HASH=%%H"
if not defined MODEL_HASH goto :model_install_error
set "MODEL_MARKER=.venv\.mahanbot_model_!MODEL_HASH!"
if not exist "!MODEL_MARKER!" (
    echo [3/5] Installing or updating offline model runtime...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-model.txt
    if errorlevel 1 goto :model_install_error
    del /q ".venv\.mahanbot_model_*" >nul 2>&1
    type nul > "!MODEL_MARKER!"
) else (
    echo [3/5] Offline model runtime matches the current requirements.
)

if not exist "my_captcha_model.pth" (
    echo [WARN] my_captcha_model.pth was not found beside unified_server.py.
    echo        The dashboard will start, but the model lab will show it as unavailable.
)

 echo [4/5] Checking Windows private-network firewall...
netsh advfirewall firewall show rule name="MahanBot SMS OTP 8010" >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process netsh -ArgumentList 'advfirewall firewall add rule name=\"MahanBot SMS OTP 8010\" dir=in action=allow protocol=TCP localport=8010 profile=private' -Verb RunAs -Wait" >nul 2>&1
    if errorlevel 1 echo [WARN] Firewall rule was not created. Dashboard startup will continue.
)

 echo [5/5] Starting dashboard, database, model lab and SMS service...
 echo Close this window or press Ctrl+C to stop MahanBot.
 echo.
".venv\Scripts\python.exe" unified_launcher.py
set "EXIT_CODE=%ERRORLEVEL%"
 echo.
 echo MahanBot stopped with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:install_error
 echo.
 echo Core package installation failed. Check the internet connection and requirements-unified.txt.
pause
exit /b 1

:model_install_error
 echo.
 echo Offline model runtime installation failed. Check the internet connection, disk space and requirements-model.txt.
pause
exit /b 1
