@echo off
rem RootData project hot board: one-time authorisation / manual refresh.
rem
rem The site sits behind a Tencent WAF slider, so a plain HTTP client can never
rem pass it. This opens a dedicated Chrome profile (NOT the user's own browser
rem window), and the user drags the slider once. The clearance ticket is kept in
rem .runtime-cache\chrome-rootdata, so the service refreshes on its own later.
rem
rem Keep this file pure ASCII: cmd.exe decodes it with the system ANSI code page.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set "XINGYUN_PYTHON=C:\Python314\python.exe"
if not exist "%XINGYUN_PYTHON%" set "XINGYUN_PYTHON=python.exe"

"%XINGYUN_PYTHON%" tools\rootdata_login.py --login
echo.
echo Done. Press any key to close this window.
pause >nul
endlocal
