const test = require("node:test");
const assert = require("node:assert/strict");

const Data = require("../dragon-wave-data.js");

test("exposes the six requested chart intervals", () => {
  assert.deepEqual(Object.keys(Data.INTERVALS), ["1m", "5m", "15m", "1h", "4h", "1d"]);
  assert.equal(Data.MIN_CANDLES_BY_INTERVAL["4h"], 1);
  assert.equal(Data.MIN_CANDLES_BY_INTERVAL["1d"], 1);
});

test("preloads substantially more history for the three execution timeframes", () => {
  const now = 1_800_000_000_000;
  const focus = now - 2_000 * 60_000;
  assert.ok(Data.buildWindow(focus, "1m", now).limit >= 2_000);
  assert.ok(Data.buildWindow(focus, "5m", now).limit >= 1_800);
  assert.ok(Data.buildWindow(focus, "15m", now).limit >= 1_600);
});

test("builds a complete document-case window instead of clipping small-timeframe history", () => {
  const now = Date.parse("2026-08-14T00:00:00+08:00");
  const oneMinute = Data.buildCaseWindow("2026-07-09", "2026-08-10", "1m", now);
  const fiveMinute = Data.buildCaseWindow("2026-07-09", "2026-08-10", "5m", now);
  assert.ok(oneMinute.limit > 47_000);
  assert.ok(fiveMinute.limit > 9_000);
  assert.equal(oneMinute.completeCase, true);
  assert.equal(fiveMinute.completeCase, true);
});

test("normalizes symbols and removes multiplier aliases for non-Binance venues", () => {
  assert.equal(Data.normalizePair("tut/usdt"), "TUTUSDT");
  assert.equal(Data.normalizePair("TUT"), "TUTUSDT");
  assert.equal(Data.normalizePair("BELSS"), "BLESSUSDT");
  assert.equal(Data.normalizePair("BELSSUSDT"), "BLESSUSDT");
  assert.equal(Data.normalizePair("BLESS"), "BLESSUSDT");
  assert.equal(Data.normalizePair("BIANRENSHENG"), "币安人生USDT");
  assert.equal(Data.normalizePair("BIANRENSHENG.P"), "币安人生USDT");
  assert.equal(Data.normalizePair("BIANRENSHENGUSDT"), "币安人生USDT");
  assert.equal(Data.normalizePair("BIANRENSHENGUSDT.P"), "币安人生USDT");
  assert.equal(Data.normalizePair("币安人生"), "币安人生USDT");
  assert.equal(Data.toVenueBase("1000PEPEUSDT"), "PEPE");
});

test("auto provider chain prioritizes Binance, OKX and Bitget before broad fallbacks", () => {
  const chain = Data.providerChain("auto", "futures");
  assert.deepEqual(chain.slice(0, 3).map((item) => item.id), ["binance-futures", "okx-swap", "bitget-futures"]);
  assert.equal(chain[3].id, "bybit-futures");
  assert.ok(chain.some((item) => item.id === "gate-spot"));
  assert.ok(chain.some((item) => item.id === "mexc-spot"));
  assert.ok(chain.some((item) => item.id === "kucoin-spot"));
});

test("parses Binance, OKX, Bitget, Bybit, Gate and KuCoin candle schemas", () => {
  const binance = Data.parseRows("binance", [[1000, "1", "3", "0.5", "2", "10", 1999, "20", 12, "6"]], 60_000);
  const okx = Data.parseRows("okx", [["1000", "1", "3", "0.5", "2", "10", "9", "20", "1"]], 60_000);
  const bitget = Data.parseRows("bitget", [["1000", "1", "3", "0.5", "2", "10", "20"]], 60_000);
  const bybit = Data.parseRows("bybit", [["1000", "1", "3", "0.5", "2", "10", "20"]], 60_000);
  const gate = Data.parseRows("gate", [["1", "20", "2", "3", "0.5", "1", "10"]], 60_000);
  const mexc = Data.parseRows("mexc", [["1000", "1", "3", "0.5", "2", "10", "60999", "20"]], 60_000);
  const kucoin = Data.parseRows("kucoin", [["1", "1", "2", "3", "0.5", "10", "20"]], 60_000);
  for (const parsed of [binance, okx, bitget, bybit, gate, mexc, kucoin]) {
    assert.equal(parsed[0].open, 1);
    assert.equal(parsed[0].high, 3);
    assert.equal(parsed[0].low, 0.5);
    assert.equal(parsed[0].close, 2);
    assert.equal(parsed[0].volume, 10);
    assert.equal(parsed[0].quoteVolume, 20);
  }
  assert.equal(binance[0].tradeCount, 12);
  assert.equal(binance[0].takerBuyVolume, 6);
});

test("complete historical cases keep searching when the first exchange covers only the tail", async () => {
  const originalFetch = global.fetch;
  const baseTime = Date.parse("2024-10-28T00:00:00Z");
  const intervalMs = 15 * 60_000;
  const fullRows = Array.from({ length: 200 }, (_, index) => [
    String(baseTime + index * intervalMs),
    "1", "1.1", "0.9", "1.05", "100", "105",
  ]);
  const partialBinance = fullRows.slice(120).map((row) => [
    ...row.slice(0, 6), Number(row[0]) + intervalMs - 1, row[6], 1, "50",
  ]);
  global.fetch = async (url) => {
    const value = String(url);
    if (value.includes("binance.com")) {
      return { ok: true, status: 200, json: async () => partialBinance };
    }
    if (value.includes("okx.com")) {
      return { ok: true, status: 200, json: async () => ({ code: "0", data: [] }) };
    }
    if (value.includes("bitget.com")) {
      return { ok: true, status: 200, json: async () => ({ code: "00000", data: [] }) };
    }
    if (value.includes("bybit.com")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ retCode: 0, retMsg: "OK", result: { list: [...fullRows].reverse() } }),
      };
    }
    throw new Error(`unexpected venue: ${value}`);
  };
  const window = {
    start: baseTime,
    end: baseTime + 199 * intervalMs,
    sourceStart: baseTime,
    sourceEnd: baseTime + 199 * intervalMs,
    limit: 200,
    completeCase: true,
  };
  try {
    const result = await Data.fetchCandles({
      pair: "GRASSUSDT",
      interval: "15m",
      provider: "auto",
      market: "futures",
      window,
    });
    assert.equal(result.venue.id, "bybit-futures");
    assert.equal(result.candles.length, 200);
    assert.equal(result.coverage.completeEnough, true);
    assert.ok(result.attempts.some((item) => item.venue === "Binance 永续" && item.message.includes("仅覆盖")));
    assert.equal(Data.isCandleCoverageAcceptable(partialBinance.map((row) => Data.parseRows("binance", [row], intervalMs)[0]), window, "15m"), false);
    assert.equal(Data.isCandleCoverageAcceptable(result.candles, window, "15m"), true);
  } finally {
    global.fetch = originalFetch;
  }
});

test("accepts a continuous new listing that begins during the first requested day", () => {
  const intervalMs = 60 * 60_000;
  const sourceStart = Date.parse("2025-02-20T00:00:00Z");
  const sourceEnd = Date.parse("2025-02-22T23:00:00Z");
  const candles = Array.from({ length: 60 }, (_, index) => ({
    time: sourceStart + (12 + index) * intervalMs,
  }));
  const coverage = Data.assessCandleCoverage(candles, {
    sourceStart,
    sourceEnd,
    start: sourceStart,
    end: sourceEnd,
    completeCase: true,
  }, "1h");
  assert.equal(coverage.listingBoundary, true);
  assert.equal(coverage.completeEnough, true);
});

test("auto mode falls through from Binance to OKX when the first venue has no data", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  const baseTime = 1_700_000_000_000;
  const rows = Array.from({ length: 40 }, (_, index) => {
    const time = baseTime + index * 60_000;
    return [String(time), "1", "3", "0.5", "2", "10", "9", "20", "1"];
  }).reverse();
  global.fetch = async (url) => {
    calls.push(String(url));
    if (String(url).includes("binance.com")) return { ok: false, status: 404, json: async () => ({}) };
    if (String(url).includes("okx.com")) return { ok: true, status: 200, json: async () => ({ code: "0", data: rows }) };
    throw new Error("unexpected venue");
  };
  try {
    const result = await Data.fetchCandles({
      pair: "BTCUSDT",
      interval: "1m",
      provider: "auto",
      market: "futures",
      window: { start: baseTime - 60_000, end: baseTime + 50 * 60_000, limit: 40 },
    });
    assert.equal(result.venue.id, "okx-swap");
    assert.equal(result.candles.length, 40);
    assert.equal(result.attempts[0].venue, "Binance 永续");
    assert.ok(calls[0].includes("binance.com"));
    assert.ok(calls[1].includes("okx.com"));
  } finally {
    global.fetch = originalFetch;
  }
});

test("keeps short new-coin 4h and daily histories while low timeframes still require enough bars", async () => {
  const originalFetch = global.fetch;
  const baseTime = Date.parse("2025-02-20T00:00:00Z");
  const specs = {
    "4H": { count: 13, ms: 4 * 60 * 60_000 },
    "1Dutc": { count: 3, ms: 24 * 60 * 60_000 },
    "5m": { count: 5, ms: 5 * 60_000 },
  };
  global.fetch = async (url) => {
    const request = new URL(String(url));
    const spec = specs[request.searchParams.get("bar")];
    assert.ok(spec, `unexpected OKX bar: ${request.searchParams.get("bar")}`);
    const rows = Array.from({ length: spec.count }, (_, index) => [
      String(baseTime + index * spec.ms),
      "0.70", "0.73", "0.69", "0.72", "100", "72", "72", "1",
    ]).reverse();
    return { ok: true, status: 200, json: async () => ({ code: "0", data: rows }) };
  };
  try {
    const fourHour = await Data.fetchCandles({
      pair: "PIUSDT",
      interval: "4h",
      provider: "okx",
      market: "futures",
      window: { start: baseTime, end: baseTime + 12 * specs["4H"].ms, limit: 13 },
    });
    assert.equal(fourHour.candles.length, 13);

    const daily = await Data.fetchCandles({
      pair: "PIUSDT",
      interval: "1d",
      provider: "okx",
      market: "futures",
      window: { start: baseTime, end: baseTime + 2 * specs["1Dutc"].ms, limit: 3 },
    });
    assert.equal(daily.candles.length, 3);

    await assert.rejects(
      Data.fetchCandles({
        pair: "PIUSDT",
        interval: "5m",
        provider: "okx",
        market: "futures",
        window: { start: baseTime, end: baseTime + 4 * specs["5m"].ms, limit: 5 },
      }),
      (error) => error.attempts?.[0]?.message === "仅返回 5/30 根 K 线",
    );
  } finally {
    global.fetch = originalFetch;
  }
});

test("uses Bitget spot granularity names instead of futures interval names", async () => {
  const originalFetch = global.fetch;
  let requestedUrl = "";
  const baseTime = 1_700_000_000_000;
  global.fetch = async (url) => {
    requestedUrl = String(url);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        code: "00000",
        data: Array.from({ length: 40 }, (_, index) => [
          String(baseTime + index * 300_000),
          "1", "1.1", "0.9", "1.05", "100", "105",
        ]),
      }),
    };
  };
  try {
    await Data.fetchCandles({
      pair: "HUSDT",
      interval: "5m",
      provider: "bitget",
      market: "spot",
      window: { start: baseTime, end: baseTime + 50 * 300_000, limit: 40 },
    });
    assert.match(requestedUrl, /granularity=5min/);
  } finally {
    global.fetch = originalFetch;
  }
});

test("Bitget spot pagination loads the complete requested small-timeframe case", async () => {
  const originalFetch = global.fetch;
  const baseTime = 1_700_000_000_000;
  const intervalMs = 300_000;
  const sourceRows = Array.from({ length: 450 }, (_, index) => [
    String(baseTime + index * intervalMs),
    "1", "1.1", "0.9", "1.05", "100", "105",
  ]);
  let requests = 0;
  global.fetch = async (url) => {
    requests += 1;
    const request = new URL(String(url));
    const endTime = Number(request.searchParams.get("endTime"));
    const limit = Number(request.searchParams.get("limit"));
    const data = sourceRows.filter((row) => Number(row[0]) <= endTime).slice(-limit);
    return { ok: true, status: 200, json: async () => ({ code: "00000", data }) };
  };
  try {
    const result = await Data.fetchCandles({
      pair: "HUSDT",
      interval: "5m",
      provider: "bitget",
      market: "spot",
      window: {
        start: baseTime,
        end: baseTime + 449 * intervalMs,
        limit: 450,
      },
    });
    assert.equal(requests, 3);
    assert.equal(result.candles.length, 450);
    assert.equal(result.candles[0].time, baseTime);
  } finally {
    global.fetch = originalFetch;
  }
});

test("merges and ranks live leaders from Binance, OKX and Bitget", () => {
  const rows = [
    ...Data.parseLeaderRows("binance", [
      { symbol: "AKEUSDT", lastPrice: "0.018", priceChangePercent: "82", quoteVolume: "42000000" },
      { symbol: "USDCUSDT", lastPrice: "1", priceChangePercent: "12", quoteVolume: "90000000" },
    ]),
    ...Data.parseLeaderRows("okx", [
      { instId: "AKE-USDT-SWAP", last: "0.0175", open24h: "0.01", volCcy24h: "38000000" },
      { instId: "TUT-USDT-SWAP", last: "0.05", open24h: "0.04", volCcy24h: "25000000" },
    ]),
    ...Data.parseLeaderRows("bitget", [
      { symbol: "AKEUSDT", lastPr: "0.0178", change24h: "0.78", usdtVolume: "36000000" },
    ]),
  ];
  const leaders = Data.rankLeaders(rows, { minChangePercent: 5, minQuoteVolume: 3_000_000, limit: 20 });
  assert.equal(leaders[0].symbol, "AKE");
  assert.deepEqual(leaders[0].venues.sort(), ["Binance", "Bitget", "OKX"]);
  assert.ok(leaders.some((item) => item.symbol === "TUT"));
  assert.ok(!leaders.some((item) => item.symbol === "USDC"));
});
