$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$privateDir = Join-Path $root "desktop-private"
$seedDir = Join-Path $privateDir "runtime-seed"
$targetEnv = Join-Path $privateDir ".env.desktop"

New-Item -ItemType Directory -Force -Path $privateDir | Out-Null
New-Item -ItemType Directory -Force -Path $seedDir | Out-Null

$allowPrefixes = @(
  "AICOIN_",
  "AUTOMATION_",
  "BINANCE_",
  "BITGET_",
  "DEEPSEEK_",
  "DISCORD_",
  "EMAIL_",
  "FUTU_",
  "GITHUB_",
  "GMAIL_",
  "GOOGLE_",
  "HTTP_",
  "HTTPS_",
  "LONGPORT_",
  "MARKET_",
  "NO_PROXY",
  "OKX_",
  "PRICE_WATCH_",
  "QQ_",
  "REQUESTS_",
  "SMTP_",
  "THS_",
  "WECHAT_",
  "XINGYUN_",
  "X_KOL_"
)

$values = [ordered]@{
  "XINGYUN_ENV" = "desktop"
  "XINGYUN_LOAD_ENV_EXAMPLE" = "0"
  "XINGYUN_DISABLE_DESKTOP_ALERT" = "0"
  "OKX_PRODUCT_TYPE" = "futures"
  "OKX_FUTURES_TYPE" = "USDT"
  "OKX_COUNTRY_FILTER" = "1"
  "OKX_RANK_ZONE" = "utc24"
  "OKX_RANK_PAGE_SIZE" = "25"
  "OKX_DESKTOP_PROXY_ENABLED" = "auto"
  "OKX_DESKTOP_PROXY_CONNECT_HOST" = "127.0.0.1"
  "OKX_DESKTOP_PROXY_PORTS" = "17000,17001,17002,17003,17004,17005"
  "OKX_DESKTOP_PROXY_HOST" = "www.okx.com"
  "OKX_DESKTOP_USER_AGENT" = "OKX/2.6.1"
  "OKX_DESKTOP_PROXY_TIMEOUT" = "3"
  "OKX_ENABLE_WS" = "1"
  "OKX_FUTURES_CACHE_MAX_AGE_HOURS" = "168"
}

function Should-IncludeEnvKey([string]$key) {
  foreach ($prefix in $allowPrefixes) {
    if ($key -eq $prefix -or $key.StartsWith($prefix)) {
      return $true
    }
  }
  return $false
}

function Read-EnvFile([string]$path) {
  if (!(Test-Path $path)) {
    return
  }
  Get-Content -Encoding UTF8 $path | ForEach-Object {
    $line = $_.Trim()
    if (!$line -or $line.StartsWith("#") -or !$line.Contains("=")) {
      return
    }
    $index = $line.IndexOf("=")
    $key = $line.Substring(0, $index).Trim().Trim([char]0xFEFF)
    $value = $line.Substring($index + 1).Trim()
    if ($key -and (Should-IncludeEnvKey $key)) {
      $values[$key] = $value
    }
  }
}

Read-EnvFile (Join-Path $root ".env.production")
Read-EnvFile (Join-Path $root ".env")

$lines = @("# XingyunShe desktop private config", "# Generated locally. Do not commit or publish with personal secrets.")
foreach ($key in ($values.Keys | Sort-Object)) {
  $lines += "$key=$($values[$key])"
}
$lines += ""
Set-Content -Encoding UTF8 -Path $targetEnv -Value $lines

$cacheFiles = @(
  "okx_futures_hot.json",
  "api_market-hot.json",
  "api_gainers-rankings.json",
  "api_turnover-rankings.json",
  "deepseek_rank_insights.json",
  "okx_dex_source.json"
)

foreach ($fileName in $cacheFiles) {
  $source = Join-Path (Join-Path $root ".runtime-cache") $fileName
  if (Test-Path $source) {
    Copy-Item -LiteralPath $source -Destination (Join-Path $seedDir $fileName) -Force
  }
}

Write-Host "Desktop private config prepared: $targetEnv"
