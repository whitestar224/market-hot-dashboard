@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYW=C:\Python314\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
rem 传绝对路径：浮窗的 retire_stale_overlay() 靠它认出"这是同一个脚本的旧窗口"，
rem 相对路径只能靠 cwd 兜底判断，跨目录启动时会漏。
start "" "%PYW%" "%~dp0src\app\alerts\alert_stream_overlay.py"
exit /b 0
