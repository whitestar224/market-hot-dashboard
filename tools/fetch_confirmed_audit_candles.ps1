param(
    [Parameter(Mandatory = $true)][string]$Provider,
    [Parameter(Mandatory = $true)][string]$Market,
    [Parameter(Mandatory = $true)][string]$Pair,
    [Parameter(Mandatory = $true)][long]$FocusStartMs,
    [Parameter(Mandatory = $true)][long]$FocusEndMs,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string[]]$SelectedIntervals = @('1m', '5m', '15m', '1h', '4h', '1d')
)

$ErrorActionPreference = 'Stop'

if ($Provider -notin @('binance', 'okx')) { throw "Unsupported provider: $Provider" }
if ($Market -notin @('futures', 'spot')) { throw "Unsupported market: $Market" }
if ($Pair -notmatch '^[A-Z0-9]+USDT$') { throw "Invalid pair: $Pair" }

$intervals = [ordered]@{
    '1m'  = @{ ms = 60000L;    warmup = 4800; binance = '1m';  okx = '1m' }
    '5m'  = @{ ms = 300000L;   warmup = 1600; binance = '5m';  okx = '5m' }
    '15m' = @{ ms = 900000L;   warmup = 1400; binance = '15m'; okx = '15m' }
    '1h'  = @{ ms = 3600000L;  warmup = 1000; binance = '1h';  okx = '1H' }
    '4h'  = @{ ms = 14400000L; warmup = 700;  binance = '4h';  okx = '4H' }
    '1d'  = @{ ms = 86400000L; warmup = 600;  binance = '1d';  okx = '1Dutc' }
}

function Invoke-JsonRequest([string]$Uri) {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 30
    # Return the JSON text instead of an already-decoded nested array. Windows
    # PowerShell enumerates function-returned arrays and would otherwise wrap a
    # whole Binance page as one row, corrupting the candle shape.
    return [string]$response.Content
}

function Convert-CandleRow($Row, [long]$IntervalMs, [string]$Kind) {
    $quoteVolume = 0
    $closeTime = [long]$Row[0] + $IntervalMs - 1
    $takerBuyVolume = 0
    $tradeCount = 0
    if ($Kind -eq 'binance') {
        $quoteVolume = [double]$Row[7]
        $closeTime = [long]$Row[6]
        $takerBuyVolume = [double]$Row[9]
        $tradeCount = [double]$Row[8]
    } elseif ($Row.Count -gt 7) {
        $quoteVolume = [double]$Row[7]
    } elseif ($Row.Count -gt 6) {
        $quoteVolume = [double]$Row[6]
    }
    return [ordered]@{
        time = [long]$Row[0]
        closeTime = $closeTime
        open = [double]$Row[1]
        high = [double]$Row[2]
        low = [double]$Row[3]
        close = [double]$Row[4]
        volume = [double]$Row[5]
        quoteVolume = $quoteVolume
        takerBuyVolume = $takerBuyVolume
        tradeCount = $tradeCount
    }
}

function Get-BinanceCandles([string]$Interval, [long]$StartMs, [long]$EndMs, [int]$Limit) {
    $base = if ($Market -eq 'futures') { 'https://fapi.binance.com/fapi/v1/klines' } else { 'https://api.binance.com/api/v3/klines' }
    $rows = [System.Collections.Generic.List[object]]::new()
    $cursor = $StartMs
    while ($cursor -le $EndMs -and $rows.Count -lt $Limit) {
        $pageLimit = [Math]::Min(1500, $Limit - $rows.Count)
        $uri = "${base}?symbol=$Pair&interval=$Interval&startTime=$cursor&endTime=$EndMs&limit=$pageLimit"
        $decoded = (Invoke-JsonRequest $uri) | ConvertFrom-Json
        $wrappedPage = ($decoded.Count -eq 1) -and ($decoded[0] -is [System.Array]) -and ($decoded[0].Count -gt 0) -and ($decoded[0][0] -is [System.Array])
        $payload = if ($wrappedPage) {
            [object[]]$decoded[0]
        } else {
            [object[]]$decoded
        }
        if ($payload.Count -eq 0) { break }
        foreach ($row in $payload) { $rows.Add($row) }
        $newest = ($payload | ForEach-Object { [long]$_[0] } | Measure-Object -Maximum).Maximum
        $next = [long]$newest + [long]$intervals[$Interval].ms
        if ($newest -le 0 -or $next -le $cursor) { break }
        $cursor = $next
        if ($payload.Count -lt $pageLimit) { break }
    }
    return $rows
}

function Get-OkxCandles([string]$Interval, [long]$StartMs, [long]$EndMs, [int]$Limit) {
    $base = $Pair.Substring(0, $Pair.Length - 4)
    if ($base.StartsWith('1000')) { $base = $base.Substring(4) }
    $instrument = if ($Market -eq 'futures') { "$base-USDT-SWAP" } else { "$base-USDT" }
    $rows = [System.Collections.Generic.List[object]]::new()
    $cursor = $EndMs
    while ($rows.Count -lt $Limit) {
        $pageLimit = [Math]::Min(300, $Limit - $rows.Count)
        $bar = $intervals[$Interval].okx
        $uri = "https://www.okx.com/api/v5/market/history-candles?instId=$instrument&bar=$bar&after=$cursor&limit=$pageLimit"
        $payload = (Invoke-JsonRequest $uri) | ConvertFrom-Json
        if ($payload.code -ne '0' -or @($payload.data).Count -eq 0) { break }
        foreach ($row in @($payload.data)) { $rows.Add($row) }
        $oldest = (@($payload.data) | ForEach-Object { [long]$_[0] } | Measure-Object -Minimum).Minimum
        if ($oldest -le 0 -or $oldest -ge $cursor) { break }
        if ($oldest -le $StartMs) { break }
        $cursor = [long]$oldest - 1
    }
    return $rows
}

$result = [ordered]@{
    provider = $Provider
    market = $Market
    pair = $Pair
    fetchedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    intervals = [ordered]@{}
    errors = [ordered]@{}
}

foreach ($entry in $intervals.GetEnumerator()) {
    $interval = $entry.Key
    if ($SelectedIntervals -notcontains $interval) { continue }
    $meta = $entry.Value
    $candidateStart = $FocusStartMs - [long]$meta.warmup * [long]$meta.ms
    $startMs = if ($candidateStart -gt 0) { [long]$candidateStart } else { 0L }
    $endMs = $FocusEndMs + 3L * [long]$meta.ms
    $limit = [int][Math]::Ceiling(($endMs - $startMs) / [double]$meta.ms) + 2
    try {
        $rawRows = if ($Provider -eq 'binance') {
            Get-BinanceCandles $interval $startMs $endMs $limit
        } else {
            Get-OkxCandles $interval $startMs $endMs $limit
        }
        $deduped = [ordered]@{}
        foreach ($row in @($rawRows)) {
            $candle = Convert-CandleRow $row ([long]$meta.ms) $Provider
            if ($candle.time -ge $startMs -and $candle.time -le $endMs) {
                $deduped[[string]$candle.time] = $candle
            }
        }
        $candles = @($deduped.Values | Sort-Object { [long]$_.time })
        $result.intervals[$interval] = [ordered]@{
            start = $startMs
            end = $endMs
            count = $candles.Count
            candles = $candles
        }
    } catch {
        $result.errors[$interval] = "$($_.Exception.Message) @ line $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim())"
        $result.intervals[$interval] = [ordered]@{ start = $startMs; end = $endMs; count = 0; candles = @() }
    }
}

$directory = Split-Path -Parent $OutputPath
if ($directory) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
$json = $result | ConvertTo-Json -Depth 8 -Compress
[IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
Write-Output $OutputPath
