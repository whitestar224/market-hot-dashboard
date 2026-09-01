param(
  [switch]$Once,
  [switch]$TestMessage,
  [switch]$SendExisting
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$scriptPath = Join-Path $projectRoot "dingtalk_price_watch_bot.py"

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
  $pythonExecutable = $python.Source
  $pythonPrefix = @()
} else {
  $launcher = Get-Command py -ErrorAction SilentlyContinue
  if (-not $launcher) {
    throw "Python was not found. Install Python 3.10 or newer first."
  }
  $pythonExecutable = $launcher.Source
  $pythonPrefix = @("-3")
}

$arguments = @($pythonPrefix) + @($scriptPath)
if ($Once) {
  $arguments += "--once"
}
if ($TestMessage) {
  $arguments += "--test-message"
}
if ($SendExisting) {
  $arguments += "--send-existing"
}

Push-Location $projectRoot
try {
  & $pythonExecutable @arguments
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
