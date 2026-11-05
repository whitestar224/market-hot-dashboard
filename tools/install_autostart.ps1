# Install (or refresh) the logon autostart entry for the background service.
#
# Keep this file pure ASCII. Windows PowerShell 5.1 decodes .ps1 with the system
# ANSI code page when the file has no BOM, so any Chinese literal written here
# would come out garbled. All paths are derived from $PSScriptRoot instead, and
# the one Chinese filename we touch is built from code points.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$vbs = Join-Path $root 'autostart-service.vbs'
$bat = Join-Path $root 'service_autostart.bat'

foreach ($required in @($vbs, $bat)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "missing required file: $required"
    }
}

$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup 'market-dashboard-service.lnk'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$shortcut.Arguments = '"' + $vbs + '"'
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7
$shortcut.Description = 'Market dashboard background service (starts hidden at logon)'
$shortcut.Save()

# The previous launcher ran "pythonw.exe service_guard.py start" without
# XINGYUN_MARKET_WORKER=1, so market scraping ran inside the main process and
# behaved differently from 启动后台服务.cmd. Retire it.
$legacyName = ([char]0x661F + [char]0x4E91 + [char]0x793E + [char]0x540E +
               [char]0x53F0 + [char]0x76D1 + [char]0x542C + '.cmd')
$legacy = Join-Path $startup $legacyName
if (Test-Path -LiteralPath $legacy) {
    Remove-Item -LiteralPath $legacy -Force
    Write-Output ('removed legacy launcher: ' + $legacy)
}

Write-Output ('installed : ' + $link)
Write-Output ('run       : wscript.exe "' + $vbs + '"')
Write-Output ('fallback  : service_autostart.bat')
Write-Output ('startup   : ' + $startup)
