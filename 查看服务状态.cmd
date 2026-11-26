@echo off
rem 2026-11-15: no supervisor any more - report the two bare processes instead.
cd /d "%~dp0"
"C:\Python314\python.exe" tools\restart_service.py --status
pause
