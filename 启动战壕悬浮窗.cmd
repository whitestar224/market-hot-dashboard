@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PIDFILE=%TEMP%\xys-trench-overlay.pid"
set "LOGFILE=%TEMP%\xys-trench-overlay.log"
REM Absolute path: a Git/msys install on PATH shadows cmd's timeout.exe.
set "SLEEP=%SystemRoot%\System32\timeout.exe"

set "RUNNING="
if exist "%PIDFILE%" (
  set /p OLDPID=<"%PIDFILE%"
  for /f "delims=" %%L in ('tasklist /FI "PID eq !OLDPID!" /NH 2^>nul ^| findstr /C:"!OLDPID!"') do set "RUNNING=1"
)
if defined RUNNING (
  echo Trench overlay is already running ^(pid !OLDPID!^).
  echo Drag it with the plain left mouse button; the position is remembered.
  echo Press Alt+Q once to hide it, again to show it.
  echo Use the stop script in this folder to close it.
  "%SLEEP%" /t 5 >nul 2>&1
  exit /b 0
)

REM Pick a pythonw that actually ships tkinter. The project runtime is
REM C:\Python314; a bare "pythonw" on PATH is often a build without tkinter and
REM would fail silently with no console to report it.
set "PYW=%XYS_TRENCH_PYTHONW%"
if not defined PYW if exist "C:\Python314\pythonw.exe" set "PYW=C:\Python314\pythonw.exe"
if not defined PYW (
  for /f "delims=" %%W in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%W"
)
if not defined PYW (
  echo [ERROR] pythonw.exe not found. Install Python 3 with tkinter, or set XYS_TRENCH_PYTHONW.
  "%SLEEP%" /t 15 >nul 2>&1
  exit /b 1
)

for %%D in ("%PYW%") do set "PYDIR=%%~dpD"
"%PYDIR%python.exe" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] "%PYW%" has no tkinter module, so the overlay cannot start.
  echo         Install a Python build that includes tkinter,
  echo         or point XYS_TRENCH_PYTHONW at one, then run this again.
  "%SLEEP%" /t 20 >nul 2>&1
  exit /b 2
)

echo.
echo [1/2] Environment check
echo ---------------------------------------------------------------
"%PYDIR%python.exe" trench_overlay.py --selfcheck
echo ---------------------------------------------------------------
if errorlevel 1 (
  echo [WARN] Self-check found a problem above. The panel may open but stay empty.
  echo        Starting anyway in 5 seconds ^(press Ctrl+C to abort^)...
  "%SLEEP%" /t 5 >nul 2>&1
)

echo.
echo [2/2] Starting the overlay...
REM Clear the pid so the wait below can only succeed for THIS attempt.
REM The log is appended, never deleted - a failed start must leave evidence.
del "%PIDFILE%" >nul 2>&1
>>"%LOGFILE%" echo.
>>"%LOGFILE%" echo === launcher invoked %DATE% %TIME% ===
start "" "%PYW%" trench_overlay.py

REM Wait up to ~12s for the overlay to publish its pid file. Without this the
REM launcher would claim success even when pythonw died on the first line.
set "STARTED="
for /l %%I in (1,1,12) do (
  if not defined STARTED (
    if exist "%PIDFILE%" (set "STARTED=1") else ("%SLEEP%" /t 1 >nul 2>&1)
  )
)

if defined STARTED (
  set /p OVERLAYPID=<"%PIDFILE%"
  echo.
  echo Overlay is running ^(pid !OVERLAYPID!^).
  echo   Left drag    : move the panel (position is remembered)
  echo   Alt+Q        : hide / show the panel (it is shown by default)
  echo   Stop it      : run the stop script in this folder
  echo   Log          : %LOGFILE%
  "%SLEEP%" /t 6 >nul 2>&1
  exit /b 0
)

echo.
echo [ERROR] The overlay did NOT start ^(no pid file after 12s^). Last log lines:
echo ---------------------------------------------------------------
"%PYDIR%python.exe" trench_overlay.py --logtail 20
echo ---------------------------------------------------------------
echo Usual causes: another copy already holds the hotkey, or pythonw.exe
echo cannot create a window ^(no tkinter / blocked by policy^).
echo Full log: %LOGFILE%
"%SLEEP%" /t 30 >nul 2>&1
exit /b 3
