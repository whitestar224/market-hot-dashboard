@echo off
rem Move from a hand-started "python server.py" to the supervised background
rem service. Only touches a process this project can positively identify.
rem Content stays pure ASCII on purpose (cmd.exe uses the ANSI code page).
setlocal
cd /d "%~dp0"

set "XINGYUN_PYTHON=C:\Python314\python.exe"
if not exist "%XINGYUN_PYTHON%" set "XINGYUN_PYTHON=python.exe"
set "XINGYUN_PYTHONW=C:\Python314\pythonw.exe"
if not exist "%XINGYUN_PYTHONW%" set "XINGYUN_PYTHONW=pythonw.exe"
set PYTHONIOENCODING=utf-8

echo.
echo This stops a hand-started "python server.py" squatting on port 8765
echo and restarts the dashboard under the background supervisor.
echo Pages reload themselves; expect ~10 seconds of downtime.
echo.
choice /c YN /m "Continue"
if errorlevel 2 exit /b 0

echo.
"%XINGYUN_PYTHON%" tools\guard_takeover.py
if errorlevel 1 (
  echo.
  echo Takeover aborted - nothing was changed.
  pause
  exit /b 1
)

set XINGYUN_MARKET_WORKER=1
start "" "%XINGYUN_PYTHONW%" service_guard.py start
echo.
echo Supervisor launched. Waiting for port 8765...
powershell.exe -NoProfile -WindowStyle Hidden -Command "$d=(Get-Date).AddSeconds(90); while((Get-Date) -lt $d){ if(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue){ break }; Start-Sleep -Milliseconds 1000 }"

echo.
"%XINGYUN_PYTHON%" service_guard.py status
echo.
pause
endlocal
