@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURE_ARVAN_RELAY.ps1"
if errorlevel 1 (
  echo.
  echo Configuration failed.
  pause
  exit /b 1
)
echo.
echo Configuration completed.
pause
