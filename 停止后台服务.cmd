@echo off
rem 2026-11-15: no supervisor any more - stop the two bare processes directly.
cd /d "%~dp0"
"C:\Python314\python.exe" tools\restart_service.py --stop
pause
