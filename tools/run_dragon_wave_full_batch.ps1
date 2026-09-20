param(
  [ValidatePattern('^v\d+$')][string]$Version = 'v91',
  [ValidateSet('BelowNormal', 'Normal')][string]$CalculationPriority = 'BelowNormal'
)

# Run in a hidden PowerShell process. The calculation and final verification
# survive the chat turn; logs are unique and previous runs are never truncated.
$ErrorActionPreference = 'Stop'
$batchWorkspace = Split-Path -Parent $PSScriptRoot
$batchNode = (Get-Command node.exe -ErrorAction Stop).Source
$batchLogRoot = Join-Path $batchWorkspace '.runtime-cache\dragon-wave-precomputed'
New-Item -ItemType Directory -Path $batchLogRoot -Force | Out-Null
$batchRunId = '{0}-{1}-{2}' -f $Version, (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
$batchStdout = Join-Path $batchLogRoot "$batchRunId.full.stdout.log"
$batchStderr = Join-Path $batchLogRoot "$batchRunId.full.stderr.log"
$verifyStdout = Join-Path $batchLogRoot "$batchRunId.verify.stdout.log"
$verifyStderr = Join-Path $batchLogRoot "$batchRunId.verify.stderr.log"

$batchProcess = Start-Process -FilePath $batchNode `
  -ArgumentList @('tools/precompute_dragon_wave_cases.js', "--version=$Version", '--full-batch', '--local-only') `
  -WorkingDirectory $batchWorkspace -WindowStyle Hidden `
  -RedirectStandardOutput $batchStdout -RedirectStandardError $batchStderr -PassThru
try { $batchProcess.PriorityClass = $CalculationPriority } catch { }
Write-Output "Calculation PID=$($batchProcess.Id); priority=$CalculationPriority; output=$batchStdout; errors=$batchStderr"
$batchProcess.WaitForExit()
$batchExit = $batchProcess.ExitCode
Write-Output "Calculation exit=$batchExit. Starting read-only verification."

$verifyProcess = Start-Process -FilePath $batchNode `
  -ArgumentList @('tools/report_dragon_wave_precompute.js', "--version=$Version", '--verify-results') `
  -WorkingDirectory $batchWorkspace -WindowStyle Hidden `
  -RedirectStandardOutput $verifyStdout -RedirectStandardError $verifyStderr -PassThru
try { $verifyProcess.PriorityClass = 'BelowNormal' } catch { }
$verifyProcess.WaitForExit()
Write-Output "Verification exit=$($verifyProcess.ExitCode); output=$verifyStdout; errors=$verifyStderr"
if ($batchExit -ne 0) { exit $batchExit }
exit $verifyProcess.ExitCode
