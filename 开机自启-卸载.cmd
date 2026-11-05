@echo off
rem Remove the logon autostart entry (the running service is not touched).
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\uninstall_autostart.ps1"
echo.
pause
endlocal
