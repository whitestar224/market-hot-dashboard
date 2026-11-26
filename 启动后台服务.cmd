@echo off
rem 2026-11-15: no supervisor any more. Start the two bare processes through
rem the same script the logon launcher uses, then report status.
cd /d "%~dp0"
call ".runtime-cache\_start_service.cmd"
timeout /t 8 /nobreak >nul
"C:\Python314\python.exe" tools\restart_service.py --status
pause
