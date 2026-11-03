@echo off
title MAHANBOT Unified Bank Selector

echo ============================================================
echo   MAHANBOT Unified Bank Selector
echo ============================================================
echo.

python setup_and_verify.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Pre-flight checks failed. Please resolve the issues above.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [1/2] Launching MAHANBOT Server...
start "MAHANBOT Server" cmd /k "python server.py"

echo [2/2] Opening browser to Unified Dashboard...
ping 127.0.0.1 -n 3 >nul
start http://127.0.0.1:8000/unified-selector

echo.
echo ============================================================
echo   MAHANBOT Unified Selector is running at:
echo   http://127.0.0.1:8000/unified-selector
echo ============================================================
