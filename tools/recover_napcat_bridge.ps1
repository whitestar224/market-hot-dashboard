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
$qqDirectory = Split-Path -Parent $qqExecutable
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

$managed = @(
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            if ($_.HasExited) { return $false }
            try {
                $path = $_.Path
            } catch {
                return $false
            }
            if (-not $path) { return $false }
            return $path.Equals($napcatExecutable, [System.StringComparison]::OrdinalIgnoreCase) -or
                $path.StartsWith($qqDirectory + '\', [System.StringComparison]::OrdinalIgnoreCase)
        }
)

# Stop only binaries resolved inside the configured QQ directory or the exact NapCat runtime.
# Children are stopped before their parents to avoid the half-exited QQ state blocking a new login.
$ordered = @($managed | Sort-Object @{ Expression = { if ($_.Path -ieq $napcatExecutable) { 2 } elseif ($_.Path -ieq $qqExecutable) { 1 } else { 0 } } })
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
                return $path -and (
                    $path.Equals($napcatExecutable, [System.StringComparison]::OrdinalIgnoreCase) -or
                    $path.StartsWith($qqDirectory + '\', [System.StringComparison]::OrdinalIgnoreCase)
                )
            }
    )
    if (-not $remaining) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

if ($remaining) {
    throw "QQ residual processes could not be terminated: $($remaining.Id -join ',')"
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
