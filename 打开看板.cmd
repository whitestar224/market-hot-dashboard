@echo off
rem One-click entry: make sure the service is up, then open the dashboard.
rem This is the everyday replacement for hand-running "python server.py".
rem Content stays pure ASCII on purpose (cmd.exe uses the ANSI code page).
setlocal
cd /d "%~dp0"

set "XINGYUN_PYTHON=C:\Python314\python.exe"
if not exist "%XINGYUN_PYTHON%" set "XINGYUN_PYTHON=python.exe"
set "XINGYUN_PYTHONW=C:\Python314\pythonw.exe"
if not exist "%XINGYUN_PYTHONW%" set "XINGYUN_PYTHONW=pythonw.exe"
set PYTHONIOENCODING=utf-8

rem probe() talks to /api/service-liveness with proxies disabled: exit 0 = up.
"%XINGYUN_PYTHON%" -c "import sys, service_guard as g; sys.exit(0 if g.probe('127.0.0.1', 8765) else 3)" >nul 2>nul

if errorlevel 3 (
  echo The service is not running. Starting it in the background...
  powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%XINGYUN_PYTHONW%' -ArgumentList 'service_guard.py','start' -WorkingDirectory '%CD%'"
  echo Waiting for port 8765 to open, up to 60 seconds...
  powershell.exe -NoProfile -WindowStyle Hidden -Command "$d=(Get-Date).AddSeconds(60); while((Get-Date) -lt $d){ if(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue){ break }; Start-Sleep -Milliseconds 1000 }"
)

start "" "http://127.0.0.1:8765/index.html"
endlocal
