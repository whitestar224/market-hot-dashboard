param(
  [string]$OutputPath = (Join-Path $PSScriptRoot "..\tests\fixtures\husdt_5m_2025-07-02_1045.json")
)

$target = [DateTimeOffset]::Parse("2025-07-02T02:45:00Z").ToUnixTimeMilliseconds()
$start = $target - 140 * 5 * 60 * 1000
$end = $target + 20 * 5 * 60 * 1000
$url = "https://fapi.binance.com/fapi/v1/klines?symbol=HUSDT&interval=5m&startTime=$start&endTime=$end&limit=1000"
$payload = Invoke-RestMethod -Uri $url -TimeoutSec 30
if (-not $payload -or $payload.Count -lt 150) {
  throw "Binance returned an incomplete HUSDT 5m regression window."
}

$rows = @($payload | ForEach-Object {
  ,@(
    [long]$_[0],
    [double]$_[1],
    [double]$_[2],
    [double]$_[3],
    [double]$_[4],
    [double]$_[5],
    [double]$_[7],
    [double]$_[9],
    [long]$_[8]
  )
})
$json = $rows | ConvertTo-Json -Depth 3
$resolved = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($resolved)) | Out-Null
[System.IO.File]::WriteAllText($resolved, $json, [System.Text.UTF8Encoding]::new($false))
Write-Output $resolved
