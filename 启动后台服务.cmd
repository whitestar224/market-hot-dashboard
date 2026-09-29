@echo off
cd /d "%~dp0"
set XINGYUN_MARKET_WORKER=1
python service_guard.py start
pause
