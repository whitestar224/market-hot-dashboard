@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYW=C:\Python314\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
start "" "%PYW%" alert_stream_overlay.py
exit /b 0
