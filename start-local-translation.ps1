$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime-cache"
$venvDir = Join-Path $runtimeDir "libretranslate-venv"
$python = Join-Path $venvDir "Scripts\python.exe"
$executable = Join-Path $venvDir "Scripts\libretranslate.exe"
$stdoutPath = Join-Path $runtimeDir "libretranslate.log"
$stderrPath = Join-Path $runtimeDir "libretranslate.err.log"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

if (-not (Test-Path $python)) {
  $launcher = Get-Command py -ErrorAction SilentlyContinue
  if (-not $launcher) {
    throw "Python Launcher was not found. Install Python 3.10 or 3.11 first."
  }
  & py -3.10 -m venv $venvDir
  if ($LASTEXITCODE -ne 0) {
    & py -3.11 -m venv $venvDir
  }
}

if (-not (Test-Path $executable)) {
  & $python -m pip install --disable-pip-version-check "libretranslate==1.9.6"
}

$running = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -like "*libretranslate*--port*5000*"
}
if (-not $running) {
  $arguments = @(
    "--host", "127.0.0.1",
    "--port", "5000",
    "--load-only", "en,zh",
    "--disable-web-ui",
    "--disable-files-translation",
    "--translation-cache", "all",
    "--threads", "2"
  )
  Start-Process -FilePath $executable -ArgumentList $arguments -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
}

$ready = $false
for ($i = 0; $i -lt 180; $i++) {
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:5000/languages" -TimeoutSec 3 | Out-Null
    $ready = $true
    break
  } catch {
    Start-Sleep -Seconds 1
  }
}

if (-not $ready) {
  throw "LibreTranslate startup timed out. Check $stderrPath"
}

Write-Host "LibreTranslate is ready at http://127.0.0.1:5000" -ForegroundColor Green
