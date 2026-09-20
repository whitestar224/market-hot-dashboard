@echo off
cd /d "%~dp0"
if exist "C:\Python314\pythonw.exe" (
  start "" "C:\Python314\pythonw.exe" "configure_binance_web3.py"
) else (
  start "" pythonw "configure_binance_web3.py"
)
