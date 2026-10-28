@echo off
setlocal
cd /d "%~dp0"
set "PY=C:\Python314\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" tools\mm_leader_report.py --top 40 --open
if errorlevel 1 pause
endlocal
