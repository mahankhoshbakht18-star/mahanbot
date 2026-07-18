@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
  echo [ERROR] Run this file as Administrator.
  pause
  exit /b 1
)

set "PORT=8000"
if exist "%~dp0.env.sms-bridge" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env.sms-bridge") do (
    if /I "%%A"=="MAHANBOT_PORT" set "PORT=%%B"
  )
)

netsh advfirewall firewall delete rule name="MahanBot SMS Notify Bridge" >nul 2>&1
netsh advfirewall firewall add rule name="MahanBot SMS Notify Bridge" dir=in action=allow protocol=TCP localport=%PORT% profile=private

if not "%ERRORLEVEL%"=="0" (
  echo [ERROR] Windows Firewall rule could not be created.
  pause
  exit /b 1
)

echo.
echo Firewall rule created for TCP port %PORT% on Private networks only.
echo Do not change your public Wi-Fi profile to Private unless you trust that network.
echo.
pause
