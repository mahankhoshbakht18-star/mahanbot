@echo off
chcp 65001 >nul
title MAHANBOT — Installer and Setup
cd /d "%~dp0"
echo ========================================================
echo       MAHANBOT v1.8.0 — System Setup and Verification
echo ========================================================
echo.

where py >nul 2>nul
if %errorlevel% neq 0 (
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python 3.10+ 64-bit is not installed or not in PATH!
        echo Please download and install Python from https://www.python.org/
        echo Make sure to check "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
    set PY_CMD=python
) else (
    set PY_CMD=py -3
)

echo [1/5] Creating Python virtual environment (.venv)...
if not exist .venv (
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
)
echo      Virtual environment ready.

echo.
echo [2/5] Upgrading pip and installing required dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements!
    pause
    exit /b 1
)

echo.
echo [3/5] Installing Playwright Chromium browser binary...
.venv\Scripts\playwright.exe install chromium
if errorlevel 1 (
    echo [WARNING] Playwright browser install had warnings. Chrome system browser will be used as fallback.
)

echo.
echo [4/5] Initializing configuration files...
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo      Created .env from .env.example
    )
)

echo.
echo [5/5] Checking clean database template...
if not exist cbi_clean_template.db (
    .venv\Scripts\python.exe scripts\create_clean_distribution_db.py
)
if not exist cbi_ultimate.db (
    if exist cbi_clean_template.db (
        copy cbi_clean_template.db cbi_ultimate.db >nul
        echo      Initialized fresh cbi_ultimate.db
    )
)

echo.
echo ========================================================
echo       Installation Completed Successfully!
echo       You can now double-click 'start.bat' to run MAHANBOT.
echo ========================================================
echo.
pause
exit /b 0
