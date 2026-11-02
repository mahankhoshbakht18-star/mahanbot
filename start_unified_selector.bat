@echo off
chcp 65001 >nul
title MAHANBOT Unified Bank Selector

echo ============================================================
echo   در حال راه‌اندازی سامانه یکپارچه انتخاب بانک MAHANBOT...
echo ============================================================
echo.

python setup_and_verify.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [خطا] برخی پیش‌نیازها برقرار نیست. لطفاً خطاها را برطرف کنید.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [1/2] در حال راه‌اندازی سرور MAHANBOT...
start "MAHANBOT Server" cmd /k "python server.py"

echo [2/2] باز کردن مرورگر و انتقال به پنل یکپارچه...
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8000/unified-selector

echo.
echo ============================================================
echo   سامانه با موفقیت اجرا شد: http://127.0.0.1:8000/unified-selector
echo ============================================================
