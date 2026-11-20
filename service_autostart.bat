@echo off
rem Background service launcher. Invoked by autostart-service.vbs at logon,
rem or by hand from a command prompt. Keep this file pure ASCII: cmd.exe
rem decodes it with the system ANSI code page, so non-ASCII text is unsafe.
rem
rem 2026-11-20: ONE process only. The market worker (12 sources) now runs
rem inside server.py as a daemon thread (start_inprocess_market_worker), so
rem the old "server.py --worker-market" line is gone. Keeping both would give
rem TWO writers for the same snapshot file plus a second console window the
rem user does not want. The 20:53 reboot proved this file was still launching
rem both - it used to be fixed only in .runtime-cache/_start_service.cmd,
rem which is the restart path, not the boot path.
rem
rem To go back to the split layout on purpose: add --no-inproc-worker to the
rem server.py line below AND restore the --worker-market line.
setlocal
cd /d "%~dp0"

rem HTTP side must not rebuild the 12 sources inside a request thread; it
rem reads the snapshot the in-process worker writes. The worker thread sets
rem this itself as well - keeping it here also covers the cold-start window.
set XINGYUN_MARKET_WORKER=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

if not exist ".runtime-cache\service" mkdir ".runtime-cache\service"

rem Prefer the 3.14 runtime; fall back to whatever python is on PATH.
set "XINGYUN_PYTHON=C:\Python314\python.exe"
if not exist "%XINGYUN_PYTHON%" set "XINGYUN_PYTHON=python.exe"

rem %date% is locale dependent (Chinese builds emit non-ASCII weekday names),
rem so only %time% is echoed here.
echo [%time%] launcher start >> ".runtime-cache\service\autostart.log"

rem The redirect needs the "cmd /c" wrapper: a redirect written after "start"
rem applies to start itself, so the child would write nothing at all.
start "xingyunshe" /min cmd /c "%XINGYUN_PYTHON% server.py >> .runtime-cache\smart_money_restart.log 2>&1"

echo [%time%] launcher started one bare process (in-process market worker) >> ".runtime-cache\service\autostart.log"
endlocal
