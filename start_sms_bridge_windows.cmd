@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv was not found. Run setup_windows.cmd first.
  pause
  exit /b 1
)

if not exist ".env.sms-bridge" (
  echo [ERROR] .env.sms-bridge was not found.
  echo Run setup_sms_bridge_windows.cmd first.
  pause
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env.sms-bridge") do (
  if not "%%A"=="" set "%%A=%%B"
)

if not defined MAHANBOT_SMS_DEVICE_KEY (
  echo [ERROR] MAHANBOT_SMS_DEVICE_KEY is missing.
  pause
  exit /b 1
)

if not defined MAHANBOT_HOST set "MAHANBOT_HOST=0.0.0.0"
if not defined MAHANBOT_PORT set "MAHANBOT_PORT=8000"
if not defined MAHANBOT_BROWSER_CHANNEL set "MAHANBOT_BROWSER_CHANNEL=msedge"
if not defined MAHANBOT_DEV_MODE set "MAHANBOT_DEV_MODE=true"

echo.
echo MahanBot SMS Notify Bridge
echo Local dashboard: http://127.0.0.1:%MAHANBOT_PORT%/
echo Android URL: http://YOUR-PC-IP:%MAHANBOT_PORT%
echo Browser runtime: %MAHANBOT_BROWSER_CHANNEL%
if defined MAHANBOT_DB_PATH echo Database: %MAHANBOT_DB_PATH%
echo.
echo Keep this window open. Press CTRL+C to stop.
echo.

".venv\Scripts\python.exe" server_sms_bridge.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Server stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
