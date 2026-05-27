$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cacheDir = Join-Path $root ".runtime-cache"
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

try {
  Invoke-WebRequest -Uri "http://127.0.0.1:8766/health" -UseBasicParsing -TimeoutSec 1 | Out-Null
  Write-Host "Xingyun desktop alert bridge is already running."
  exit 0
} catch {
  # Start a hidden bridge process below.
}

$stdoutPath = Join-Path $cacheDir "desktop_alert_bridge.log"
$stderrPath = Join-Path $cacheDir "desktop_alert_bridge.err.log"
$args = @("desktop_alert_bridge.py", "--host", "127.0.0.1", "--port", "8766")
Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
Start-Sleep -Milliseconds 500
Write-Host "Xingyun desktop alert bridge started: http://127.0.0.1:8766"
