param(
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'server.py') -PathType Leaf)) {
    throw "Workspace validation failed: server.py was not found under $projectRoot"
}

$removedBytes = [int64]0
$removedItems = [System.Collections.Generic.List[string]]::new()

function Get-ItemBytes([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return [int64]0 }
    $item = Get-Item -LiteralPath $path -Force
    if (-not $item.PSIsContainer) { return [int64]$item.Length }
    $measure = Get-ChildItem -LiteralPath $path -Force -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum
    return [int64]$measure.Sum
}

function Get-ProjectRelativePath([string]$path) {
    $fullPath = [System.IO.Path]::GetFullPath($path)
    if (-not $fullPath.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the project: $fullPath"
    }
    return $fullPath.Substring($projectPrefix.Length)
}

function Remove-GeneratedPath([string]$relativePath) {
    $target = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $relativePath))
    if (-not $target.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the project: $target"
    }
    if (-not (Test-Path -LiteralPath $target)) { return }
    $script:removedBytes += Get-ItemBytes $target
    $script:removedItems.Add($relativePath)
    if (-not $WhatIf) {
        Remove-Item -LiteralPath $target -Force -Recurse
    }
}

$generatedDirectories = @(
    'release',
    '.pyinstaller-build',
    '.qa-desktop-runtime',
    '.runtime-tools\downloads',
    '.runtime-cache\installers',
    '.runtime-cache\confirmed-native-audit-candles',
    '.runtime-cache\dragon-wave-precomputed',
    '.runtime-cache\strategy-issue-candles',
    '.runtime-cache\edge-chain-review',
    '.runtime-cache\edge-personal-x-review',
    '.runtime-cache\edge-personal-x-review2',
    '.runtime-cache\edge-v17-check',
    '.runtime-cache\edge-v17-ledger',
    '.runtime-cache\edge-v18-interaction'
)

foreach ($relativePath in $generatedDirectories) {
    Remove-GeneratedPath $relativePath
}

$backendRoot = Join-Path $projectRoot 'dist-backend'
if (Test-Path -LiteralPath $backendRoot -PathType Container) {
    foreach ($item in Get-ChildItem -LiteralPath $backendRoot -Force) {
        if ($item.Name -eq '.gitkeep') { continue }
        $relative = Get-ProjectRelativePath $item.FullName
        Remove-GeneratedPath $relative
    }
}

$rootArtifacts = @(
    'dashboard-*.png',
    'newsflash-preview.png'
)
foreach ($pattern in $rootArtifacts) {
    foreach ($item in Get-ChildItem -LiteralPath $projectRoot -File -Filter $pattern -Force -ErrorAction SilentlyContinue) {
        $relative = Get-ProjectRelativePath $item.FullName
        Remove-GeneratedPath $relative
    }
}

$bytecodeRoots = @(
    (Join-Path $projectRoot '__pycache__'),
    (Join-Path $projectRoot 'tests\__pycache__'),
    (Join-Path $projectRoot 'tools\__pycache__'),
    (Join-Path $projectRoot 'desktop\__pycache__')
)
foreach ($directory in $bytecodeRoots) {
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { continue }
    $relative = Get-ProjectRelativePath $directory
    Remove-GeneratedPath $relative
}

$runtimeCache = Join-Path $projectRoot '.runtime-cache'
if (Test-Path -LiteralPath $runtimeCache -PathType Container) {
    $qrFiles = Get-ChildItem -LiteralPath $runtimeCache -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'wechat-auth-qr-*' } |
        Sort-Object LastWriteTime -Descending
    $cutoff = (Get-Date).AddMinutes(-30)
    for ($index = 0; $index -lt $qrFiles.Count; $index++) {
        if ($index -lt 8 -and $qrFiles[$index].LastWriteTime -ge $cutoff) { continue }
        $relative = Get-ProjectRelativePath $qrFiles[$index].FullName
        Remove-GeneratedPath $relative
    }
}

[pscustomobject]@{
    WhatIf = [bool]$WhatIf
    RemovedBytes = $removedBytes
    RemovedCount = $removedItems.Count
    RemovedPaths = @($removedItems | Select-Object -First 50)
    PathsTruncated = $removedItems.Count -gt 50
    Preserved = @(
        '.runtime-cache\*.db and state JSON',
        '.runtime-cache\libretranslate-venv',
        '.runtime-tools\napcat-* and WeChat runtime packages',
        'node_modules',
        '.runtime',
        'deliverables'
    )
} | ConvertTo-Json -Depth 4
