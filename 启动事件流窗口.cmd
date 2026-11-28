@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYW=C:\Python314\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
rem Pure ASCII on purpose: cmd parses batch files with the console codepage, so
rem Chinese comments here were split into stray commands and the launcher died
rem silently under pythonw. 2026-11-28: closing the window then double-clicking
rem this file did nothing at all.
rem --replace makes the script retire any older instance first, including a
rem zombie whose window is gone but whose process still holds the mutex.
start "" "%PYW%" "%~dp0src\app\alerts\alert_stream_overlay.py" --replace
exit /b 0
