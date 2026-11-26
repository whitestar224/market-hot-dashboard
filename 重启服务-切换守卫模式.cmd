@echo off
setlocal
cd /d "%~dp0"
echo.
echo  [1/3] Stopping whatever listens on port 8765 ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
    echo        kill pid %%a
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul
echo.
echo  [2/3] Starting the supervisor in a hidden window ...
start "" /b wscript.exe "%~dp0autostart-service.vbs" 0
echo.
echo  [3/3] Waiting 15 seconds, then checking port 8765 ...
timeout /t 15 /nobreak >nul
netstat -ano | findstr ":8765" | findstr "LISTENING"
if errorlevel 1 (
    echo        FAILED - see .runtime-cache\service\autostart.log
) else (
    echo        OK - 8765 is listening
)
echo.
echo  You can close this window. The supervisor keeps running in background.
pause
