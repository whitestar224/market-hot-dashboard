@echo off
rem Install the hidden logon autostart entry for the background service.
rem Content stays pure ASCII on purpose (cmd.exe uses the ANSI code page).
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_autostart.ps1"
if errorlevel 1 (
  echo.
  echo Install FAILED - see the message above.
  pause
  exit /b 1
)

echo.
echo Done. The background service will now start by itself, hidden,
echo about 20 seconds after you log in. Logs: .runtime-cache\service\
echo.
echo To undo, run the uninstall script.
pause
endlocal
