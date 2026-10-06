@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PIDFILE=%TEMP%\xys-trench-overlay.pid"
REM Use the absolute path: a Git/msys install on PATH shadows cmd's timeout.exe.
set "SLEEP=%SystemRoot%\System32\timeout.exe"

if not exist "%PIDFILE%" (
  echo Trench overlay is not running ^(no pid file^).
  "%SLEEP%" /t 4 >nul 2>&1
  exit /b 0
)

set /p OLDPID=<"%PIDFILE%"
set "RUNNING="
for /f "delims=" %%L in ('tasklist /FI "PID eq !OLDPID!" /NH 2^>nul ^| findstr /C:"!OLDPID!"') do set "RUNNING=1"

if defined RUNNING (
  taskkill /PID !OLDPID! /F >nul 2>&1
  echo Trench overlay stopped ^(pid !OLDPID!^).
) else (
  echo Trench overlay was not running; cleaned up a stale pid file.
)
del "%PIDFILE%" >nul 2>&1
"%SLEEP%" /t 4 >nul 2>&1
