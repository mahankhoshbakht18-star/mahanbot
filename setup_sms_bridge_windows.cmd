@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment was not found.
  echo Run setup_windows.cmd first, then run this file again.
  pause
  exit /b 1
)

if not exist "server_sms_bridge.py" (
  echo [ERROR] server_sms_bridge.py was not found in this folder.
  pause
  exit /b 1
)

if exist ".env.sms-bridge" (
  echo Existing .env.sms-bridge was preserved.
  echo Delete it manually only when you intentionally want a new device key.
  goto :done
)

for /f "usebackq delims=" %%K in (`".venv\Scripts\python.exe" -c "import secrets; print(secrets.token_urlsafe(36))"`) do set "DEVICE_KEY=%%K"

if not defined DEVICE_KEY (
  echo [ERROR] Failed to generate the device key.
  pause
  exit /b 1
)

> ".env.sms-bridge" (
  echo # Local MahanBot SMS-arrival notification settings
  echo # Never share or commit the real device key.
  echo MAHANBOT_SMS_DEVICE_KEY=!DEVICE_KEY!
  echo MAHANBOT_HOST=0.0.0.0
  echo MAHANBOT_PORT=8000
  echo MAHANBOT_BROWSER_CHANNEL=msedge
  echo MAHANBOT_DEV_MODE=true
  echo # Optional old database path:
  echo # MAHANBOT_DB_PATH=C:\Users\amir\Desktop\mahanbot-old\cbi_ultimate.db
)

:done
echo.
echo SMS notification bridge setup is ready.
echo Configuration file: %CD%\.env.sms-bridge
echo.
echo Next steps:
echo 1. Add MAHANBOT_DB_PATH to .env.sms-bridge when using the old database.
echo 2. Run open_sms_bridge_firewall_admin.cmd as Administrator once.
echo 3. Run start_sms_bridge_windows.cmd.
echo 4. Configure the Android app with the PC IPv4 address and device key.
echo.
pause
