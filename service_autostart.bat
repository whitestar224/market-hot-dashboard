@echo off
rem Background service launcher. Invoked by autostart-service.vbs at logon,
rem or by hand from a command prompt. Keep this file pure ASCII: cmd.exe
rem decodes it with the system ANSI code page, so non-ASCII text is unsafe.
setlocal
cd /d "%~dp0"

rem market feed scraping runs in its own supervised child process
set XINGYUN_MARKET_WORKER=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

if not exist ".runtime-cache\service" mkdir ".runtime-cache\service"

rem Prefer the 3.14 runtime; fall back to whatever python is on PATH.
set "XINGYUN_PYTHON=C:\Python314\python.exe"
if not exist "%XINGYUN_PYTHON%" set "XINGYUN_PYTHON=python.exe"

rem %date% is locale dependent (Chinese builds emit non-ASCII weekday names),
rem so only %time% is echoed here. The supervisor writes real timestamps.
echo [%time%] launcher start >> ".runtime-cache\service\autostart.log"

rem stdout/stderr go to a file on purpose: with no console attached, a full
rem pipe buffer would block the supervisor on its next print().
"%XINGYUN_PYTHON%" service_guard.py start >> ".runtime-cache\service\autostart.log" 2>&1

echo [%time%] launcher exit code=%errorlevel% >> ".runtime-cache\service\autostart.log"
endlocal
