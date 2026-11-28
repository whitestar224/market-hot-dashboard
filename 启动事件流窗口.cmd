@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYW=C:\Python314\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
rem Pure ASCII on purpose: cmd parses batch files with the console codepage, so
rem Chinese comments here were split into stray commands and the launcher died
rem silently under pythonw. 2026-11-28.
rem
rem Two processes:
rem   1) alert_stream_hotkey.py  - resident hotkey host. It owns Alt+Q and is the
rem      only reason the window can be brought back after it was closed, because
rem      the hotkey used to be polled inside the window process itself.
rem   2) alert_stream_overlay.py - the window. --replace retires an older
rem      instance (or a zombie holding the single-instance mutex) first.
start "" "%PYW%" "%~dp0src\app\alerts\alert_stream_hotkey.py"
start "" "%PYW%" "%~dp0src\app\alerts\alert_stream_overlay.py" --replace
exit /b 0
