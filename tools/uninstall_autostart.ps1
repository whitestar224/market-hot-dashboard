# Remove the logon autostart entry. The service itself is left untouched.
$ErrorActionPreference = 'Stop'

$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup 'market-dashboard-service.lnk'

if (Test-Path -LiteralPath $link) {
    Remove-Item -LiteralPath $link -Force
    Write-Output ('removed: ' + $link)
} else {
    Write-Output 'autostart was not installed; nothing to remove.'
}
