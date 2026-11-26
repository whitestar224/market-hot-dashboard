(function attachDragonWaveData(root, factory) {
  const data = factory();
  if (typeof module === "object" && module.exports) module.exports = data;
  root.DragonWaveData = data;
})(typeof globalThis !== "undefined" ? globalThis : this, function createDataLayer() {
  "use strict";

  const INTERVALS = Object.freeze({
    "1m": Object.freeze({ label: "1分钟", ms: 60_000, okx: "1m", bitget: "1m", bitgetSpot: "1min", bybit: "1", gate: "1m", mexc: "1m", kucoin: "1min", before: 4800, after: 400 }),
    "5m": Object.freeze({ label: "5分钟", ms: 300_000, okx: "5m", bitget: "5m", bitgetSpot: "5min", bybit: "5", gate: "5m", mexc: "5m", kucoin: "5min", before: 1600, after: 400 }),
    "15m": Object.freeze({ label: "15分钟", ms: 900_000, okx: "15m", bitget: "15m", bitgetSpot: "15min", bybit: "15", gate: "15m", mexc: "15m", kucoin: "15min", before: 1400, after: 400 }),
    "1h": Object.freeze({ label: "1小时", ms: 3_600_000, okx: "1H", bitget: "1H", bitgetSpot: "1h", bybit: "60", gate: "1h", mexc: "60m", kucoin: "1hour", before: 1000, after: 300 }),
    "4h": Object.freeze({ label: "4小时", ms: 14_400_000, okx: "4H", bitget: "4H", bitgetSpot: "4h", bybit: "240", gate: "4h", mexc: "4h", kucoin: "4hour", before: 700, after: 300 }),
    "1d": Object.freeze({ label: "日线", ms: 86_400_000, okx: "1Dutc", bitget: "1D", bitgetSpot: "1day", bybit: "D", gate: "1d", mexc: "1d", kucoin: "1day", before: 600, after: 200 }),
  });

  // 新币上市早期的 4 小时和日线天然很短。它们不足以计算长指标，
  // 但仍是“上市时间短”的有效上下文证据，不能在数据入口被丢弃。
  const MIN_CANDLES_BY_INTERVAL = Object.freeze({
    "1m": 30,
    "5m": 30,
    "15m": 30,
    "1h": 30,
    "4h": 1,
    "1d": 1,
  });

  const VENUES = Object.freeze({
    "binance-futures": Object.freeze({ id: "binance-futures", provider: "binance", market: "futures", label: "Binance 永续" }),
    "okx-swap": Object.freeze({ id: "okx-swap", provider: "okx", market: "futures", label: "OKX 永续" }),
    "bitget-futures": Object.freeze({ id: "bitget-futures", provider: "bitget", market: "futures", label: "Bitget 永续" }),
    "bybit-futures": Object.freeze({ id: "bybit-futures", provider: "bybit", market: "futures", label: "Bybit 永续" }),
    "hyperliquid-perp": Object.freeze({ id: "hyperliquid-perp", provider: "hyperliquid", market: "futures", label: "Hyperliquid 永续" }),
    "binance-spot": Object.freeze({ id: "binance-spot", provider: "binance", market: "spot", label: "Binance 现货" }),
    "okx-spot": Object.freeze({ id: "okx-spot", provider: "okx", market: "spot", label: "OKX 现货" }),
    "bitget-spot": Object.freeze({ id: "bitget-spot", provider: "bitget", market: "spot", label: "Bitget 现货" }),
    "bybit-spot": Object.freeze({ id: "bybit-spot", provider: "bybit", market: "spot", label: "Bybit 现货" }),
    "gate-spot": Object.freeze({ id: "gate-spot", provider: "gate", market: "spot", label: "Gate 现货" }),
    "mexc-spot": Object.freeze({ id: "mexc-spot", provider: "mexc", market: "spot", label: "MEXC 现货" }),
    "kucoin-spot": Object.freeze({ id: "kucoin-spot", provider: "kucoin", market: "spot", label: "KuCoin 现货" }),
  });

  // Preserve compatibility with symbols that were entered incorrectly in an
  // older document sample or persisted URL. Keep aliases at the data boundary
  // so every provider, cache key and feedback lookup uses the canonical pair.
  const SYMBOL_ALIASES = Object.freeze({
    BELSS: "BLESS",
    // TradingView 将该合约转写为 BIANRENSHENGUSDT.P；币安官方接口
    // 使用中文原生 symbol“币安人生USDT”。二者是同一合约，不是 BANANAS31。
    BIANRENSHENG: "币安人生",
    "币安人生": "币安人生",
  });

  function normalizePair(value) {
    const compact = String(value || "")
      .trim()
      .toUpperCase()
      .replace(/[-_/\s]/g, "")
      .replace(/\.P$/, "")
      .replace(/PERP(?:ETUAL)?$/, "");
    if (!compact) return "BTCUSDT";
    const quote = ["USDT", "USDC", "BUSD"].find((candidate) => compact.endsWith(candidate)) || "USDT";
    const base = quote && compact.endsWith(quote) ? compact.slice(0, -quote.length) : compact;
    return `${SYMBOL_ALIASES[base] || base}${quote}`;
  }

  function toVenueBase(pair) {
    return normalizePair(pair)
      .replace(/(USDT|USDC|BUSD)$/, "")
      .replace(/^1000(?=[A-Z])/, "");
  }

  function providerChain(provider = "auto", market = "futures") {
    if (provider !== "auto") {
      const preferred = market === "futures"
        ? { binance: "binance-futures", okx: "okx-swap", bitget: "bitget-futures", bybit: "bybit-futures", hyperliquid: "hyperliquid-perp" }[provider]
        : null;
      const spot = { binance: "binance-spot", okx: "okx-spot", bitget: "bitget-spot", bybit: "bybit-spot", gate: "gate-spot", mexc: "mexc-spot", kucoin: "kucoin-spot" }[provider];
      return [VENUES[preferred || spot]].filter(Boolean);
    }
    const futures = ["binance-futures", "okx-swap", "bitget-futures", "bybit-futures", "hyperliquid-perp"];
    const spot = ["binance-spot", "okx-spot", "bitget-spot", "bybit-spot", "gate-spot", "mexc-spot", "kucoin-spot"];
    return (market === "futures" ? [...futures, ...spot] : spot).map((id) => VENUES[id]);
  }

  function timestamp(value) {
    const number = Number(value);
    return number > 0 && number < 10_000_000_000 ? number * 1000 : number;
  }

  function toCandle(time, open, high, low, close, volume, quoteVolume, intervalMs, closeTime, takerBuyVolume, tradeCount) {
    const start = timestamp(time);
    return {
      time: start,
      closeTime: Number(closeTime) || start + intervalMs - 1,
      open: Number(open),
      high: Number(high),
      low: Number(low),
      close: Number(close),
      volume: Number(volume) || 0,
      quoteVolume: Number(quoteVolume) || 0,
      takerBuyVolume: Number(takerBuyVolume) || 0,
      tradeCount: Number(tradeCount) || 0,
    };
  }

  function parseRows(kind, rows, intervalMs) {
    const parsed = (Array.isArray(rows) ? rows : []).map((row) => {
      if (kind === "binance") return toCandle(row[0], row[1], row[2], row[3], row[4], row[5], row[7], intervalMs, row[6], row[9], row[8]);
      if (kind === "okx") return toCandle(row[0], row[1], row[2], row[3], row[4], row[5], row[7] || row[6], intervalMs);
      if (kind === "bitget") return toCandle(row[0], row[1], row[2], row[3], row[4], row[5], row[6], intervalMs);
      if (kind === "bybit") return toCandle(row[0], row[1], row[2], row[3], row[4], row[5], row[6], intervalMs);
      if (kind === "mexc") return toCandle(row[0], row[1], row[2], row[3], row[4], row[5], row[7], intervalMs, row[6]);
      if (kind === "gate") return toCandle(row[0], row[5], row[3], row[4], row[2], row[6], row[1], intervalMs);
      if (kind === "kucoin") return toCandle(row[0], row[1], row[3], row[4], row[2], row[5], row[6], intervalMs);
      if (kind === "hyperliquid") return toCandle(row.t, row.o, row.h, row.l, row.c, row.v, Number(row.v) * Number(row.c), intervalMs, row.T);
      return null;
    }).filter((row) => row && row.time > 0 && row.open > 0 && row.high >= row.low && row.close > 0);

    const unique = new Map();
    parsed.forEach((row) => unique.set(row.time, row));
    return [...unique.values()].sort((a, b) => a.time - b.time);
  }

  const EXCLUDED_LEADER_BASES = new Set(["USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDE", "DAI", "USDP"]);

  function leaderRecord(venue, pair, lastPrice, changePercent, quoteVolume) {
    const normalizedPair = normalizePair(pair);
    return {
      venue,
      pair: normalizedPair,
      symbol: toVenueBase(normalizedPair),
      lastPrice: Number(lastPrice) || 0,
      changePercent: Number(changePercent) || 0,
      quoteVolume: Number(quoteVolume) || 0,
    };
  }

  function parseLeaderRows(kind, rows) {
    return (Array.isArray(rows) ? rows : []).map((row) => {
      if (kind === "binance") {
        return leaderRecord("Binance", row.symbol, row.lastPrice, row.priceChangePercent, row.quoteVolume);
      }
      if (kind === "okx") {
        const match = /^(.+)-USDT-(?:SWAP|FUTURES?)$/.exec(String(row.instId || ""));
        if (!match) return null;
        const open = Number(row.open24h);
        const last = Number(row.last);
        const change = open > 0 ? (last / open - 1) * 100 : 0;
        return leaderRecord("OKX", `${match[1]}USDT`, last, change, row.volCcy24h || row.vol24h);
      }
      if (kind === "bitget") {
        const rawChange = Number(row.change24h ?? row.priceChangePercent ?? 0);
        const change = Math.abs(rawChange) <= 5 ? rawChange * 100 : rawChange;
        return leaderRecord("Bitget", row.symbol, row.lastPr || row.last, change, row.usdtVolume || row.quoteVolume || row.baseVolume);
      }
      return null;
    }).filter(Boolean);
  }

  function rankLeaders(rows, options = {}) {
    const minChangePercent = Number(options.minChangePercent ?? 6);
    const minQuoteVolume = Number(options.minQuoteVolume ?? 3_000_000);
    const limit = Number(options.limit ?? 24);
    const merged = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (!row?.symbol || EXCLUDED_LEADER_BASES.has(row.symbol)) return;
      if (/(UP|DOWN|BULL|BEAR)$/.test(row.symbol)) return;
      if (row.lastPrice <= 0 || row.changePercent < minChangePercent || row.quoteVolume < minQuoteVolume) return;
      const current = merged.get(row.symbol);
      if (!current) {
        merged.set(row.symbol, {
          ...row,
          id: `live-${row.symbol.toLowerCase()}`,
          venues: [row.venue],
        });
        return;
      }
      if (!current.venues.includes(row.venue)) current.venues.push(row.venue);
      current.changePercent = Math.max(current.changePercent, row.changePercent);
      if (row.quoteVolume > current.quoteVolume) {
        current.quoteVolume = row.quoteVolume;
        current.lastPrice = row.lastPrice;
        current.pair = row.pair;
        current.venue = row.venue;
      }
    });
    return [...merged.values()]
      .map((row) => ({
        ...row,
        score: row.changePercent * (1 + Math.log10(Math.max(row.quoteVolume, 1) / 1_000_000)),
      }))
      .sort((a, b) => b.score - a.score || b.quoteVolume - a.quoteVolume)
      .slice(0, limit);
  }

  function buildWindow(focusTime, interval, now = Date.now()) {
    const config = INTERVALS[interval];
    const safeFocus = Math.min(Number(focusTime) || now, now);
    return {
      start: Math.max(0, safeFocus - config.before * config.ms),
      end: Math.min(now, safeFocus + config.after * config.ms),
      limit: config.before + config.after + 1,
    };
  }

  function buildCaseWindow(startDate, endDate, interval, now = Date.now()) {
    const config = INTERVALS[interval];
    const sourceStart = typeof startDate === "number"
      ? startDate
      : Date.parse(`${startDate}T00:00:00+08:00`);
    const sourceEnd = typeof endDate === "number"
      ? endDate
      : Date.parse(`${endDate}T23:59:59.999+08:00`);
    if (!config || !Number.isFinite(sourceStart) || !Number.isFinite(sourceEnd) || sourceEnd < sourceStart) {
      return buildWindow(now, interval, now);
    }
    const warmupBars = ["1m", "5m", "15m"].includes(interval) ? 120 : 90;
    const start = Math.max(0, sourceStart - warmupBars * config.ms);
    const end = Math.min(now, sourceEnd);
    return {
      start,
      end,
      limit: Math.max(1, Math.ceil((end - start) / config.ms) + 1),
      completeCase: true,
      sourceStart,
      sourceEnd: Math.min(now, sourceEnd),
    };
  }

  async function fetchJson(url, init, signal, timeoutMs = 15_000) {
    const requestController = new AbortController();
    let timedOut = false;
    const abortFromParent = () => requestController.abort();
    if (signal?.aborted) requestController.abort();
    else signal?.addEventListener("abort", abortFromParent, { once: true });
    const timeout = setTimeout(() => {
      timedOut = true;
      requestController.abort();
    }, timeoutMs);
    try {
      const response = await fetch(url, {
        ...init,
        signal: requestController.signal,
        headers: { Accept: "application/json", ...(init?.headers || {}) },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (error) {
      if (timedOut) throw new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒）`);
      throw error;
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abortFromParent);
    }
  }

  async function fetchLeaders(options = {}) {
    const signal = options.signal;
    const requests = [
      {
        venue: "Binance",
        run: async () => parseLeaderRows("binance", await fetchJson("https://fapi.binance.com/fapi/v1/ticker/24hr", null, signal)),
      },
      {
        venue: "OKX",
        run: async () => {
          const payload = await fetchJson("https://www.okx.com/api/v5/market/tickers?instType=SWAP", null, signal);
          if (payload.code !== "0") throw new Error(payload.msg || `OKX ${payload.code}`);
          return parseLeaderRows("okx", payload.data);
        },
      },
      {
        venue: "Bitget",
        run: async () => {
          const payload = await fetchJson("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", null, signal);
          if (payload.code !== "00000") throw new Error(payload.msg || `Bitget ${payload.code}`);
          return parseLeaderRows("bitget", payload.data);
        },
      },
    ];
    const settled = await Promise.allSettled(requests.map((request) => request.run()));
    const rows = [];
    const sources = [];
    const errors = [];
    settled.forEach((result, index) => {
      if (result.status === "fulfilled") {
        rows.push(...result.value);
        sources.push(requests[index].venue);
      } else {
        errors.push({ venue: requests[index].venue, message: result.reason?.message || String(result.reason) });
      }
    });
    return {
      leaders: rankLeaders(rows, options),
      sources,
      errors,
      scannedAt: Date.now(),
    };
  }

  async function fetchBinance(venue, pair, interval, window, signal) {
    const base = venue.market === "futures" ? "https://fapi.binance.com/fapi/v1/klines" : "https://api.binance.com/api/v3/klines";
    const rows = [];
    const intervalMs = INTERVALS[interval].ms;
    let cursor = window.start;
    const maximumPages = Math.ceil(window.limit / 1500) + 1;
    for (let page = 0; page < maximumPages && rows.length < window.limit && cursor <= window.end; page += 1) {
      const query = new URLSearchParams({
        symbol: normalizePair(pair),
        interval,
        startTime: String(cursor),
        endTime: String(window.end),
        limit: String(Math.min(1500, window.limit - rows.length)),
      });
      const directUrl = `${base}?${query}`;
      const localRuntime = typeof location !== "undefined"
        && ["127.0.0.1", "localhost"].includes(String(location.hostname || "").toLowerCase());
      let payload;
      if (localRuntime) {
        const proxyQuery = new URLSearchParams({
          market: venue.market,
          pair: normalizePair(pair),
          interval,
          start: String(cursor),
          end: String(window.end),
          limit: String(Math.min(1500, window.limit - rows.length)),
        });
        try {
          payload = await fetchJson(`/api/dragon-wave-candles/binance?${proxyQuery}`, null, signal);
        } catch (proxyError) {
          payload = await fetchJson(directUrl, null, signal);
        }
      } else {
        payload = await fetchJson(directUrl, null, signal);
      }
      if (!Array.isArray(payload) || !payload.length) break;
      rows.push(...payload);
      const newest = Math.max(...payload.map((row) => Number(row[0])));
      const nextCursor = newest + intervalMs;
      if (!newest || nextCursor <= cursor || payload.length < Math.min(1500, window.limit - rows.length + payload.length)) break;
      cursor = nextCursor;
    }
    return parseRows("binance", rows, intervalMs).filter((row) => row.time >= window.start && row.time <= window.end);
  }

  async function fetchOkx(venue, pair, interval, window, signal) {
    const base = toVenueBase(pair);
    const instId = venue.market === "futures" ? `${base}-USDT-SWAP` : `${base}-USDT`;
    const rows = [];
    let cursor = window.end;
    const maximumPages = Math.ceil(window.limit / 300) + 1;
    for (let page = 0; page < maximumPages && rows.length < window.limit; page += 1) {
      const query = new URLSearchParams({
        instId,
        bar: INTERVALS[interval].okx,
        after: String(cursor),
        limit: String(Math.min(300, window.limit - rows.length)),
      });
      const directUrl = `https://www.okx.com/api/v5/market/history-candles?${query}`;
      const localRuntime = typeof location !== "undefined"
        && ["127.0.0.1", "localhost"].includes(String(location.hostname || "").toLowerCase());
      let payload;
      if (localRuntime) {
        const proxyQuery = new URLSearchParams({
          market: venue.market,
          pair: normalizePair(pair),
          interval,
          start: String(window.start),
          end: String(cursor),
          limit: String(Math.min(300, window.limit - rows.length)),
        });
        try {
          payload = await fetchJson(`/api/dragon-wave-candles/okx?${proxyQuery}`, null, signal, 35_000);
        } catch (proxyError) {
          payload = await fetchJson(directUrl, null, signal);
        }
      } else {
        payload = await fetchJson(directUrl, null, signal);
      }
      if (payload.code !== "0" || !Array.isArray(payload.data) || !payload.data.length) break;
      const oldest = Math.min(...payload.data.map((row) => Number(row[0])));
      if (!oldest || oldest >= cursor) break;
      rows.push(...payload.data);
      if (oldest <= window.start) break;
      cursor = oldest - 1;
    }
    return parseRows("okx", rows, INTERVALS[interval].ms).filter((row) => row.time >= window.start && row.time <= window.end);
  }

  async function fetchBitget(venue, pair, interval, window, signal) {
    const symbol = `${toVenueBase(pair)}USDT`;
    if (venue.market === "futures") {
      const rows = [];
      let cursor = window.end;
      const maximumPages = Math.ceil(window.limit / 200) + 1;
      for (let page = 0; page < maximumPages && rows.length < window.limit; page += 1) {
        const query = new URLSearchParams({
          symbol,
          productType: "USDT-FUTURES",
          granularity: INTERVALS[interval].bitget,
          startTime: String(window.start),
          endTime: String(cursor),
          limit: String(Math.min(200, window.limit - rows.length)),
        });
        const directUrl = `https://api.bitget.com/api/v2/mix/market/history-candles?${query}`;
        const localRuntime = typeof location !== "undefined"
          && ["127.0.0.1", "localhost"].includes(String(location.hostname || "").toLowerCase());
        let payload;
        if (localRuntime) {
          const proxyQuery = new URLSearchParams({
            market: venue.market,
            pair: normalizePair(pair),
            interval,
            start: String(window.start),
            end: String(cursor),
            limit: String(Math.min(200, window.limit - rows.length)),
          });
          try {
            payload = await fetchJson(`/api/dragon-wave-candles/bitget?${proxyQuery}`, null, signal, 35_000);
          } catch (proxyError) {
            payload = await fetchJson(directUrl, null, signal);
          }
        } else {
          payload = await fetchJson(directUrl, null, signal);
        }
        if (payload.code !== "00000") throw new Error(payload.msg || `Bitget ${payload.code}`);
        if (!Array.isArray(payload.data) || !payload.data.length) break;
        const oldest = Math.min(...payload.data.map((row) => Number(row[0])));
        if (!oldest || oldest >= cursor) break;
        rows.push(...payload.data);
        if (oldest <= window.start) break;
        cursor = oldest - 1;
      }
      return parseRows("bitget", rows, INTERVALS[interval].ms).filter((row) => row.time >= window.start && row.time <= window.end);
    }

    const rows = [];
    let cursor = window.end;
    const maximumPages = Math.ceil(window.limit / 200) + 1;
    for (let page = 0; page < maximumPages && rows.length < window.limit; page += 1) {
      const query = new URLSearchParams({
        symbol,
        granularity: INTERVALS[interval].bitgetSpot,
        endTime: String(cursor),
        limit: String(Math.min(200, window.limit - rows.length)),
      });
      const directUrl = `https://api.bitget.com/api/v2/spot/market/history-candles?${query}`;
      const localRuntime = typeof location !== "undefined"
        && ["127.0.0.1", "localhost"].includes(String(location.hostname || "").toLowerCase());
      let payload;
      if (localRuntime) {
        const proxyQuery = new URLSearchParams({
          market: venue.market,
          pair: normalizePair(pair),
          interval,
          start: String(window.start),
          end: String(cursor),
          limit: String(Math.min(200, window.limit - rows.length)),
        });
        try {
          payload = await fetchJson(`/api/dragon-wave-candles/bitget?${proxyQuery}`, null, signal, 35_000);
        } catch (proxyError) {
          payload = await fetchJson(directUrl, null, signal);
        }
      } else {
        payload = await fetchJson(directUrl, null, signal);
      }
      if (payload.code !== "00000" || !Array.isArray(payload.data) || !payload.data.length) break;
      rows.push(...payload.data);
      const oldest = Math.min(...payload.data.map((row) => Number(row[0])));
      if (!oldest || oldest <= window.start) break;
      cursor = oldest - 1;
    }
    return parseRows("bitget", rows, INTERVALS[interval].ms).filter((row) => row.time >= window.start && row.time <= window.end);
  }

  async function fetchBybit(venue, pair, interval, window, signal) {
    const rows = [];
    let cursor = window.end;
    const maximumPages = Math.ceil(window.limit / 1000) + 1;
    for (let page = 0; page < maximumPages && rows.length < window.limit && cursor >= window.start; page += 1) {
      const query = new URLSearchParams({
        category: venue.market === "futures" ? "linear" : "spot",
        symbol: `${toVenueBase(pair)}USDT`,
        interval: INTERVALS[interval].bybit,
        start: String(window.start),
        end: String(cursor),
        limit: String(Math.min(1000, window.limit - rows.length)),
      });
      const directUrl = `https://api.bybit.com/v5/market/kline?${query}`;
      const localRuntime = typeof location !== "undefined"
        && ["127.0.0.1", "localhost"].includes(String(location.hostname || "").toLowerCase());
      let payload;
      if (localRuntime) {
        const proxyQuery = new URLSearchParams({
          market: venue.market,
          pair: normalizePair(pair),
          interval,
          start: String(window.start),
          end: String(cursor),
          limit: String(Math.min(1000, window.limit - rows.length)),
        });
        try {
          payload = await fetchJson(`/api/dragon-wave-candles/bybit?${proxyQuery}`, null, signal, 40_000);
        } catch (proxyError) {
          payload = await fetchJson(directUrl, null, signal);
        }
      } else {
        payload = await fetchJson(directUrl, null, signal);
      }
      if (Number(payload?.retCode) !== 0) throw new Error(payload?.retMsg || `Bybit ${payload?.retCode}`);
      const batch = payload?.result?.list;
      if (!Array.isArray(batch) || !batch.length) break;
      rows.push(...batch);
      const oldest = Math.min(...batch.map((row) => Number(row[0])));
      if (!oldest || oldest <= window.start) break;
      cursor = oldest - 1;
    }
    return parseRows("bybit", rows, INTERVALS[interval].ms)
      .filter((row) => row.time >= window.start && row.time <= window.end);
  }

  async function fetchGate(pair, interval, window, signal) {
    const rows = [];
    const intervalMs = INTERVALS[interval].ms;
    let cursor = window.end;
    const maximumPages = Math.ceil(window.limit / 1000) + 1;
    for (let page = 0; page < maximumPages && rows.length < window.limit && cursor >= window.start; page += 1) {
      const pageLimit = Math.min(1000, window.limit - rows.length);
      const segmentStart = Math.max(window.start, cursor - Math.max(1, pageLimit - 1) * intervalMs);
      const query = new URLSearchParams({
        currency_pair: `${toVenueBase(pair)}_USDT`,
        interval: INTERVALS[interval].gate,
        from: String(Math.floor(segmentStart / 1000)),
        to: String(Math.floor(cursor / 1000)),
        limit: String(pageLimit),
      });
      const directUrl = `https://api.gateio.ws/api/v4/spot/candlesticks?${query}`;
      const localRuntime = typeof location !== "undefined"
        && ["127.0.0.1", "localhost"].includes(String(location.hostname || "").toLowerCase());
      let payload;
      if (localRuntime) {
        const proxyQuery = new URLSearchParams({
          market: "spot",
          pair: normalizePair(pair),
          interval,
          start: String(segmentStart),
          end: String(cursor),
          limit: String(pageLimit),
        });
        try {
          payload = await fetchJson(`/api/dragon-wave-candles/gate?${proxyQuery}`, null, signal, 35_000);
        } catch (proxyError) {
          payload = await fetchJson(directUrl, null, signal);
        }
      } else {
        payload = await fetchJson(directUrl, null, signal);
      }
      if (!Array.isArray(payload) || !payload.length) break;
      rows.push(...payload);
      const oldest = Math.min(...payload.map((row) => Number(row[0]) * 1000));
      if (!oldest || oldest <= window.start || oldest >= cursor) break;
      cursor = oldest - 1;
    }
    return parseRows("gate", rows, intervalMs).filter((row) => row.time >= window.start && row.time <= window.end);
  }

  async function fetchKucoin(pair, interval, window, signal) {
    const query = new URLSearchParams({
      symbol: `${toVenueBase(pair)}-USDT`,
      type: INTERVALS[interval].kucoin,
      startAt: String(Math.floor(window.start / 1000)),
      endAt: String(Math.floor(window.end / 1000)),
    });
    const payload = await fetchJson(`https://api.kucoin.com/api/v1/market/candles?${query}`, null, signal);
    if (payload.code !== "200000") throw new Error(payload.msg || `KuCoin ${payload.code}`);
    return parseRows("kucoin", payload.data, INTERVALS[interval].ms);
  }

  async function fetchMexc(pair, interval, window, signal) {
    const rows = [];
    const intervalMs = INTERVALS[interval].ms;
    let cursor = window.start;
    const maximumPages = Math.ceil(window.limit / 1000) + 1;
    for (let page = 0; page < maximumPages && rows.length < window.limit && cursor <= window.end; page += 1) {
      const pageLimit = Math.min(1000, window.limit - rows.length);
      const segmentEnd = Math.min(window.end, cursor + Math.max(1, pageLimit - 1) * intervalMs);
      const query = new URLSearchParams({
        symbol: normalizePair(pair),
        interval: INTERVALS[interval].mexc,
        startTime: String(cursor),
        endTime: String(segmentEnd),
        limit: String(pageLimit),
      });
      const directUrl = `https://api.mexc.com/api/v3/klines?${query}`;
      const localRuntime = typeof location !== "undefined"
        && ["127.0.0.1", "localhost"].includes(String(location.hostname || "").toLowerCase());
      let payload;
      if (localRuntime) {
        const proxyQuery = new URLSearchParams({
          market: "spot",
          pair: normalizePair(pair),
          interval,
          start: String(cursor),
          end: String(segmentEnd),
          limit: String(pageLimit),
        });
        try {
          payload = await fetchJson(`/api/dragon-wave-candles/mexc?${proxyQuery}`, null, signal, 35_000);
        } catch (proxyError) {
          payload = await fetchJson(directUrl, null, signal);
        }
      } else {
        payload = await fetchJson(directUrl, null, signal);
      }
      if (!Array.isArray(payload) || !payload.length) break;
      rows.push(...payload);
      const newest = Math.max(...payload.map((row) => Number(row[0])));
      if (!newest || newest >= window.end || newest < cursor) break;
      cursor = newest + intervalMs;
    }
    return parseRows("mexc", rows, intervalMs).filter((row) => row.time >= window.start && row.time <= window.end);
  }

  async function fetchHyperliquid(pair, interval, window, signal) {
    const payload = await fetchJson("https://api.hyperliquid.xyz/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "candleSnapshot",
        req: { coin: toVenueBase(pair), interval, startTime: window.start, endTime: window.end },
      }),
    }, signal);
    return parseRows("hyperliquid", payload, INTERVALS[interval].ms);
  }

  async function fetchVenue(venue, params) {
    const { pair, interval, window, signal } = params;
    if (venue.provider === "binance") return fetchBinance(venue, pair, interval, window, signal);
    if (venue.provider === "okx") return fetchOkx(venue, pair, interval, window, signal);
    if (venue.provider === "bitget") return fetchBitget(venue, pair, interval, window, signal);
    if (venue.provider === "bybit") return fetchBybit(venue, pair, interval, window, signal);
    if (venue.provider === "gate") return fetchGate(pair, interval, window, signal);
    if (venue.provider === "mexc") return fetchMexc(pair, interval, window, signal);
    if (venue.provider === "kucoin") return fetchKucoin(pair, interval, window, signal);
    if (venue.provider === "hyperliquid") return fetchHyperliquid(pair, interval, window, signal);
    throw new Error(`Unknown provider: ${venue.provider}`);
  }

  function assessCandleCoverage(candles, window, interval) {
    const rows = Array.isArray(candles) ? candles : [];
    const intervalMs = INTERVALS[interval]?.ms || 0;
    if (!rows.length || !intervalMs) {
      return {
        score: 0,
        spanCoverage: 0,
        continuityRatio: 0,
        startGapBars: Infinity,
        endGapBars: Infinity,
        completeEnough: false,
      };
    }
    const sourceStart = Number.isFinite(window?.sourceStart) ? window.sourceStart : window.start;
    const sourceEnd = Number.isFinite(window?.sourceEnd) ? window.sourceEnd : window.end;
    // Compare exchange-aligned candle opens instead of raw wall-clock boundaries.
    // This matters most for daily candles: a China-local date starts eight hours
    // before the exchange's UTC daily candle, but that is not missing market data.
    const alignedStart = Math.ceil(sourceStart / intervalMs) * intervalMs;
    const alignedEnd = Math.floor(sourceEnd / intervalMs) * intervalMs;
    const expectedBars = alignedEnd >= alignedStart
      ? Math.floor((alignedEnd - alignedStart) / intervalMs) + 1
      : 1;
    const inRange = rows.filter((row) => row.time >= alignedStart && row.time <= alignedEnd);
    const firstTime = inRange[0]?.time;
    const lastTime = inRange[inRange.length - 1]?.time;
    const coveredBars = inRange.length
      ? Math.floor((lastTime - firstTime) / intervalMs) + 1
      : 0;
    const spanCoverage = Math.max(0, Math.min(1, coveredBars / expectedBars));
    const continuitySpan = inRange.length
      ? Math.floor((inRange[inRange.length - 1].time - inRange[0].time) / intervalMs) + 1
      : 0;
    const continuityRatio = continuitySpan
      ? Math.max(0, Math.min(1, inRange.length / continuitySpan))
      : 0;
    const startGapBars = Number.isFinite(firstTime)
      ? Math.max(0, Math.round((firstTime - alignedStart) / intervalMs))
      : Infinity;
    const endGapBars = Number.isFinite(lastTime)
      ? Math.max(0, Math.round((alignedEnd - lastTime) / intervalMs))
      : Infinity;
    // New listings often begin during the first requested calendar day rather
    // than exactly at 00:00. Treat that exchange-listing boundary as complete
    // when the series is continuous afterwards; a multi-day missing prefix is
    // still rejected and triggers another venue lookup (the GRASS failure).
    const listingBoundary = Number.isFinite(firstTime)
      && firstTime >= alignedStart
      && firstTime - alignedStart < 24 * 60 * 60 * 1000;
    const completeEnough = (spanCoverage >= 0.985 || listingBoundary)
      && continuityRatio >= 0.97
      && (startGapBars <= 2 || listingBoundary)
      && endGapBars <= 2;
    return {
      score: spanCoverage * 0.82 + continuityRatio * 0.18,
      spanCoverage,
      continuityRatio,
      startGapBars,
      endGapBars,
      firstTime,
      lastTime,
      listingBoundary,
      completeEnough,
    };
  }

  function isCandleCoverageAcceptable(candles, window, interval) {
    const minimum = MIN_CANDLES_BY_INTERVAL[interval] || 30;
    if (!Array.isArray(candles) || candles.length < minimum) return false;
    if (!window?.completeCase) return true;
    return assessCandleCoverage(candles, window, interval).completeEnough;
  }

  async function fetchCandles(params) {
    const pair = normalizePair(params.pair);
    const interval = params.interval;
    const window = params.window || buildWindow(params.focusTime, interval);
    const attempts = [];
    const successful = [];
    for (const venue of providerChain(params.provider || "auto", params.market || "futures")) {
      try {
        const candles = await fetchVenue(venue, { pair, interval, window, signal: params.signal });
        const minimum = MIN_CANDLES_BY_INTERVAL[interval] || 30;
        if (candles.length < minimum) throw new Error(`仅返回 ${candles.length}/${minimum} 根 K 线`);
        const coverage = assessCandleCoverage(candles, window, interval);
        const value = { candles, venue, pair, interval, window, attempts, coverage };
        successful.push(value);
        if (!window.completeCase || coverage.completeEnough || (params.provider || "auto") !== "auto") return value;
        attempts.push({
          venue: venue.label,
          message: `指定区间仅覆盖 ${(coverage.spanCoverage * 100).toFixed(1)}%，继续寻找更完整数据源`,
        });
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        attempts.push({ venue: venue.label, message: error?.message || String(error) });
      }
    }
    if (successful.length) {
      successful.sort((a, b) => b.coverage.score - a.coverage.score || b.candles.length - a.candles.length);
      return { ...successful[0], attempts };
    }
    const failure = new Error(`所有公开数据源均无可用 ${pair} ${interval} K 线`);
    failure.attempts = attempts;
    throw failure;
  }

  return Object.freeze({
    INTERVALS,
    MIN_CANDLES_BY_INTERVAL,
    VENUES,
    normalizePair,
    toVenueBase,
    providerChain,
    parseRows,
    parseLeaderRows,
    rankLeaders,
    buildWindow,
    buildCaseWindow,
    assessCandleCoverage,
    isCandleCoverageAcceptable,
    fetchCandles,
    fetchLeaders,
  });
});
