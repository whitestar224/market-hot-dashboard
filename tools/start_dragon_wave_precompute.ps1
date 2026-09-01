param(
  [string]$Version = "v89"
)

$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) { return }

$workspace = Split-Path -Parent $PSScriptRoot
$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -eq "node.exe" -and $_.CommandLine -like "*precompute_dragon_wave_cases.js*"
} | Select-Object -First 1
if ($existing) { return }

$logRoot = Join-Path $workspace ".runtime-cache\dragon-wave-precomputed"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stdoutLog = Join-Path $logRoot "$Version.stdout.log"
$stderrLog = Join-Path $logRoot "$Version.stderr.log"
$process = Start-Process `
  -FilePath $nodeCommand.Source `
  -ArgumentList @("tools/precompute_dragon_wave_cases.js", "--version=$Version") `
  -WorkingDirectory $workspace `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

Start-Sleep -Milliseconds 200
if (-not $process.HasExited) {
  try { $process.PriorityClass = "Idle" } catch { }
}
