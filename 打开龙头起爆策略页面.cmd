@echo off
setlocal
cd /d "%~dp0"

if not defined DRAGON_PYTHON (
  for %%P in (pythonw.exe python.exe) do (
    if not defined DRAGON_PYTHON for /f "delims=" %%I in ('where %%P 2^>nul') do set "DRAGON_PYTHON=%%I"
  )
)
if not defined DRAGON_PYTHON (
  echo Python was not found. Set DRAGON_PYTHON to a Python executable.
  exit /b 1
)

powershell.exe -NoProfile -WindowStyle Hidden -Command ^
  "$existing = Get-NetTCPConnection -LocalPort 8791 -State Listen -ErrorAction SilentlyContinue; if (-not $existing) { Start-Process -FilePath '%DRAGON_PYTHON%' -ArgumentList 'quiet_http_server.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden }"

powershell.exe -NoProfile -Command "Start-Sleep -Milliseconds 1200" >nul 2>nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\start_dragon_wave_precompute.ps1" -Version v89
start "" "http://127.0.0.1:8791/dragon-wave.html?v=89"
endlocal
