@echo off
rem RootData: pull the project heat board and the fundraising page right now.
rem
rem No browser, no login. cn.rootdata.com is plain server-rendered HTML; the
rem only requirement is a working outbound proxy (Clash 127.0.0.1:7890 by
rem default, override with ROOTDATA_PROXY). The running service refreshes these
rem two snapshots every 15 minutes on its own, so this file is only for
rem "I want it updated right now".
rem
rem Keep this file pure ASCII: cmd.exe decodes it with the system ANSI code page.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set "XINGYUN_PYTHON=C:\Python314\python.exe"
if not exist "%XINGYUN_PYTHON%" set "XINGYUN_PYTHON=python.exe"

"%XINGYUN_PYTHON%" tools\rootdata_login.py --pull
echo.
echo Done. Press any key to close this window.
pause >nul
endlocal
