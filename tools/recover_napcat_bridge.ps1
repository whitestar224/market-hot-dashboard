param(
    [Parameter(Mandatory = $true)][string]$RuntimeDir,
    [Parameter(Mandatory = $true)][string]$QqPath,
    [Parameter(Mandatory = $true)][string]$Account
)

$ErrorActionPreference = 'Stop'

function Test-LocalPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        if (-not $task.Wait(1500)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Test-OneBotPorts {
    return (Test-LocalPort 3000) -and (Test-LocalPort 3001)
}

$runtimePath = (Resolve-Path -LiteralPath $RuntimeDir).Path.TrimEnd('\')
$qqExecutable = (Resolve-Path -LiteralPath $QqPath).Path
$launcher = Join-Path $runtimePath 'launcher.bat'
$napcatExecutable = Join-Path $runtimePath 'NapCatWinBootMain.exe'

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "NapCat launcher not found: $launcher"
}
if (-not (Test-Path -LiteralPath $napcatExecutable -PathType Leaf)) {
    throw "NapCat executable not found: $napcatExecutable"
}
if ($Account -notmatch '^\d+$') {
    throw 'QQ account must contain digits only'
}
if (Test-OneBotPorts) {
    @{ ok = $true; status = 'healthy'; changed = $false } | ConvertTo-Json -Compress
    exit 0
}

$processSnapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$accountPattern = '(?i)(?:^|\s)-q\s+' + [regex]::Escape($Account) + '(?:\s|$)'
$managedRoots = @(
    $processSnapshot | Where-Object {
        $path = [string]$_.ExecutablePath
        $command = [string]$_.CommandLine
        ($path -and $path.Equals($napcatExecutable, [System.StringComparison]::OrdinalIgnoreCase)) -or
        ($path -and $path.Equals($qqExecutable, [System.StringComparison]::OrdinalIgnoreCase) -and $command -match $accountPattern)
    }
)

# Only the exact NapCat process tree and QQ instances carrying NapCat's `-q <account>`
# marker are managed. A normal QQ process from the same install directory is never touched.
$managedIds = [System.Collections.Generic.HashSet[int]]::new()
$queue = [System.Collections.Generic.Queue[int]]::new()
foreach ($root in $managedRoots) {
    if ($managedIds.Add([int]$root.ProcessId)) { $queue.Enqueue([int]$root.ProcessId) }
}
while ($queue.Count -gt 0) {
    $parentId = $queue.Dequeue()
    foreach ($child in $processSnapshot | Where-Object { [int]$_.ParentProcessId -eq $parentId }) {
        if ($managedIds.Add([int]$child.ProcessId)) { $queue.Enqueue([int]$child.ProcessId) }
    }
}

$managed = @(
    Get-Process -Id @($managedIds) -ErrorAction SilentlyContinue |
        Where-Object { -not $_.HasExited }
)
$napcatRootIds = @($managedRoots | Where-Object { ([string]$_.ExecutablePath) -ieq $napcatExecutable } | ForEach-Object { [int]$_.ProcessId })
$ordered = @($managed | Sort-Object @{ Expression = { if ($napcatRootIds -contains $_.Id) { 1 } else { 0 } } })
foreach ($process in $ordered) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
}

$deadline = (Get-Date).AddSeconds(12)
do {
    $remaining = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object {
                if ($_.HasExited) { return $false }
                try { $path = $_.Path } catch { return $false }
                return $managedIds.Contains([int]$_.Id)
            }
    )
    if (-not $remaining) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

if ($remaining) {
    throw "QQ residual processes could not be terminated: $($remaining.Id -join ',')"
}

$manualQq = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $path = [string]$_.ExecutablePath
            $command = [string]$_.CommandLine
            $path -and
                $path.Equals($qqExecutable, [System.StringComparison]::OrdinalIgnoreCase) -and
                $command -notmatch $accountPattern
        }
)
if ($manualQq) {
    @{
        ok = $true
        status = 'manual_qq_active'
        changed = ($managed.Count -gt 0)
        stopped = $managed.Count
        skippedLaunch = $true
    } | ConvertTo-Json -Compress
    exit 0
}

Start-Process -FilePath 'cmd.exe' `
    -ArgumentList @('/d', '/c', "launcher.bat $Account") `
    -WorkingDirectory $runtimePath `
    -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(55)
do {
    if (Test-OneBotPorts) {
        @{ ok = $true; status = 'recovered'; changed = $true; stopped = $managed.Count } | ConvertTo-Json -Compress
        exit 0
    }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

throw 'NapCat restarted but OneBot ports did not become healthy within 55 seconds'
