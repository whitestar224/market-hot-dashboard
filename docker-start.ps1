$ErrorActionPreference = "Stop"

# Compose automatically reads a file named ".env" and expands "$" inside values.
# The app reads .env.production itself from /run/secrets/xingyun.env, so disable
# Compose's implicit .env loading to keep secrets and proxy URLs unchanged.
$env:COMPOSE_DISABLE_ENV_FILE = "true"

$emptyAiCoinDir = Join-Path $PSScriptRoot ".runtime-cache\aicoin-host-empty"
New-Item -ItemType Directory -Force -Path $emptyAiCoinDir | Out-Null

if (-not $env:AICOIN_HOST_USER_DATA_DIR) {
  $localAiCoinDir = Join-Path $env:APPDATA "AiCoin"
  if (Test-Path $localAiCoinDir) {
    $env:AICOIN_HOST_USER_DATA_DIR = (Resolve-Path $localAiCoinDir).Path -replace "\\", "/"
  }
}

& (Join-Path $PSScriptRoot "desktop-alert-bridge.ps1")

docker compose up -d --build
