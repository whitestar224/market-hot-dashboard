const BINANCE_WALLET_PERIOD_KEY = "xingyunshe:binance-wallet-hot:period:v2";
const BINANCE_WALLET_PERIODS = new Set(["5m", "1h", "4h", "24h"]);
const AVE_CHAIN_KEY = "xingyunshe:ave-hot:chain:v1";
const AVE_PERIOD_KEY = "xingyunshe:ave-hot:period:v1";
const AVE_CHAIN_OPTIONS = new Set(["all", "robinhood", "solana", "eth", "bsc", "base"]);
const AVE_PERIODS = new Set(["1h", "4h", "24h"]);
const GMGN_CHAIN_KEY = "xingyunshe:gmgn-hot-search:chain:v1";
const GMGN_PERIOD_KEY = "xingyunshe:gmgn-hot-search:period:v1";
const GMGN_CHAIN_OPTIONS = new Set(["all", "sol", "bsc", "base", "eth", "robinhood", "arc", "stable"]);
const GMGN_PERIODS = new Set(["1m", "5m", "1h", "6h", "24h"]);
const MARKET_CACHE_KEY = "xingyunshe:market-hot:payload:v15";
const MARKET_PRIORITY_VIEW_KEY = "xingyunshe:market-hot:priority-view:v1";
const MARKET_PRIORITY_PERIOD_KEY = "xingyunshe:market-hot:priority-period:v1";
const MARKET_PRIORITY_PERIODS = new Set(["1h", "6h", "24h"]);
const TOTAL_BOARD_PAGE_SIZE = 10;
const EXCHANGE_AI_REQUEST_LIMIT = 24;
const exchangeAiNarrativeCache = new Map();
const exchangeAiNarrativePending = new Set();
const exchangeAiNarrativeRetryAt = new Map();
let exchangeAiNarrativeRequestActive = false;
const gmgnTrenchXPostCache = new Map();
const gmgnTrenchXPostPending = new Set();
const gmgnTrenchXPostRetryAt = new Map();
const gmgnTrenchXPostQueue = [];
let gmgnTrenchXPostRequestActive = false;

function readLocalPreference(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function saveLocalPreference(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Preference persistence is optional.
  }
}

function normalizePriorityView(value) {
  return String(value || "smart").toLowerCase() === "platform" ? "platform" : "smart";
}

function normalizePriorityPeriod(value) {
  const period = String(value || "24h").toLowerCase();
  return MARKET_PRIORITY_PERIODS.has(period) ? period : "24h";
}

function normalizeBinanceWalletPeriod(value) {
  const period = String(value || "24h").trim().toLowerCase();
  return BINANCE_WALLET_PERIODS.has(period) ? period : "24h";
}

function normalizeAveChain(value) {
  const chain = String(value || "all").trim().toLowerCase();
  return AVE_CHAIN_OPTIONS.has(chain) ? chain : "all";
}

function normalizeAvePeriod(value) {
  const period = String(value || "4h").trim().toLowerCase();
  return AVE_PERIODS.has(period) ? period : "4h";
}

function normalizeGmgnChain(value) {
  const chain = String(value || "all").trim().toLowerCase();
  return GMGN_CHAIN_OPTIONS.has(chain) ? chain : "all";
}

function normalizeGmgnPeriod(value) {
  const period = String(value || "1h").trim().toLowerCase();
  return GMGN_PERIODS.has(period) ? period : "1h";
}

function readBinanceWalletPeriod() {
  try {
    return normalizeBinanceWalletPeriod(localStorage.getItem(BINANCE_WALLET_PERIOD_KEY));
  } catch {
    return "24h";
  }
}

function saveBinanceWalletPeriod(period) {
  try {
    localStorage.setItem(BINANCE_WALLET_PERIOD_KEY, normalizeBinanceWalletPeriod(period));
  } catch {
    // Preference persistence is optional.
  }
}

const state = {
  filter: "all",
  sort: normalizePriorityView(readLocalPreference(MARKET_PRIORITY_VIEW_KEY, "smart")) === "smart" ? "priority" : "rank",
  query: "",
  sources: [],
  isLoading: false,
  lastRequestedAt: 0,
  binanceWalletPeriod: readBinanceWalletPeriod(),
  binanceWalletLoading: false,
  aveChain: normalizeAveChain(readLocalPreference(AVE_CHAIN_KEY, "all")),
  avePeriod: normalizeAvePeriod(readLocalPreference(AVE_PERIOD_KEY, "4h")),
  gmgnChain: normalizeGmgnChain(readLocalPreference(GMGN_CHAIN_KEY, "all")),
  gmgnPeriod: normalizeGmgnPeriod(readLocalPreference(GMGN_PERIOD_KEY, "1h")),
  gmgnHotLoading: false,
  priorityPeriod: normalizePriorityPeriod(readLocalPreference(MARKET_PRIORITY_PERIOD_KEY, "24h")),
  smartPriority: {},
  totalPage: 1
};

const boardsEl = document.querySelector("#leaderboards");
const summaryEl = document.querySelector("#summaryGrid");
const searchInput = document.querySelector("#searchInput");
const sortSelect = document.querySelector("#sortSelect");
const filterButtons = document.querySelector("#filterButtons");
const clockEl = document.querySelector("#clock");
const tickerRail = document.querySelector("#tickerRail");
const dataStatus = document.querySelector("#dataStatus");
const priorityPeriodSelect = document.querySelector("#priorityPeriodSelect");

function renderLive(node, html) {
  if (!node) return false;
  if (window.XingyunLiveDom?.render) return window.XingyunLiveDom.render(node, html);
  if (node.innerHTML === html) return false;
  node.innerHTML = html;
  return true;
}

const groupLabels = {
  total: "币圈 · 跨榜去重",
  crypto: "币圈",
  aicoin: "AIcoin",
  hk: "港股",
  us: "美股",
  cn: "A股"
};

function parseSignedNumber(value) {
  return Number.parseFloat(String(value || "0").replace("%", "").replace("+", "")) || 0;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[char]);
}

function readCachedPayload(key) {
  try {
    const payload = JSON.parse(localStorage.getItem(key) || "null");
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
}

function writeCachedPayload(key, payload) {
  try {
    localStorage.setItem(key, JSON.stringify(payload));
  } catch {
    // Local cache is a UI speed-up only.
  }
}

function hydrateMarketCache() {
  const cached = readCachedPayload(MARKET_CACHE_KEY);
  const sources = Array.isArray(cached?.sources) ? cached.sources : [];
  if (!sources.length) return false;
  state.sources = sources;
  state.smartPriority = cached.smartPriority && typeof cached.smartPriority === "object" ? cached.smartPriority : {};
  setStatus(`缓存数据 · ${formatTime(cached.updatedAt || cached._cache?.updatedAt)}`, "ok");
  renderTicker();
  renderBoards();
  return true;
}

function iconFallbackText(row, source) {
  const group = source?.group || "";
  const rawName = String(row.name || row.symbol || source?.sourceLabel || "").trim();
  const symbol = String(row.symbol || "").trim();
  const text = group === "hk" || group === "cn" ? rawName || symbol : symbol || rawName;
  const clean = text.replace(/[-_/.\s]+/g, "");
  const chars = Array.from(clean);
  const maxLength = group === "us" || group === "crypto" || group === "aicoin" ? 2 : 1;
  return (chars.slice(0, maxLength).join("") || source?.sourceLabel || "?").toUpperCase();
}

function assetIconClass(source) {
  const group = source?.group || "";
  if (group === "hk") return "is-stock is-hk";
  if (group === "us") return "is-stock is-us";
  if (group === "cn") return "is-stock is-cn";
  if (group === "aicoin") return "is-coin is-aicoin";
  return "is-coin";
}

function normalizeIconSource(url, row, source) {
  const value = String(url || "").trim();
  if (!value) return "";
  if (value.startsWith("//")) return `https:${value}`;
  if (/^https?:\/\//i.test(value) || value.startsWith("data:image/")) return value;
  const sourceId = String(source?.id || source?.sourceName || source?.sourceLabel || "").toLowerCase();
  if (sourceId.includes("ave") || ["token_icon/", "ipfs/", "signals/", "upload/", "token/"].some((prefix) => value.startsWith(prefix))) {
    return `https://www.iconaves.com/${value.replace(/^\/+/, "")}`;
  }
  return value;
}

function iconSources(row, source) {
  const sources = [
    row.icon,
    ...(Array.isArray(row.icons) ? row.icons : []),
    ...(Array.isArray(row.iconCandidates) ? row.iconCandidates : [])
  ]
    .map((item) => normalizeIconSource(item, row, source))
    .filter(Boolean);
  return [...new Set(sources)];
}

window.advanceAssetIcon = function advanceAssetIcon(image) {
  const parent = image?.parentElement;
  if (!parent) return;
  let sources = [];
  try {
    sources = JSON.parse(parent.dataset.icons || "[]");
  } catch {
    sources = [];
  }
  const nextIndex = Number(parent.dataset.iconIndex || 0) + 1;
  if (sources[nextIndex]) {
    parent.dataset.iconIndex = String(nextIndex);
    image.src = sources[nextIndex];
    return;
  }
  parent.classList.add("is-fallback");
  image.remove();
};

function renderAssetIcon(row, source) {
  const sources = iconSources(row, source);
  const src = sources[0] || "";
  const label = escapeHtml(iconFallbackText(row, source));
  const alt = escapeHtml(row.symbol || row.name || "");
  const dataset = escapeHtml(JSON.stringify(sources));
  const image = src
    ? `<img src="${escapeHtml(src)}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="window.advanceAssetIcon(this)">`
    : "";
  return `<span class="asset-icon ${assetIconClass(source)} ${src ? "" : "is-fallback"}" data-icons="${dataset}" data-icon-index="0">${image}<em>${label}</em></span>`;
}

function normalizeMarketSymbol(value) {
  return String(value || "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9.]/g, "");
}

function cleanPair(value, fallbackAsset = "") {
  const raw = normalizeMarketSymbol(value).replace(/SWAP$/, "");
  if (raw.endsWith("USDT") || raw.endsWith("USDC") || raw.endsWith("USD")) return raw;
  const asset = normalizeMarketSymbol(fallbackAsset || raw);
  return asset ? `${asset}USDT` : "";
}

function okxInstId(row, asset) {
  const rawName = String(row.name || "").toUpperCase();
  const matched = rawName.match(/[A-Z0-9]+-(USDT|USDC|USD)-SWAP/);
  if (matched) return matched[0];
  const pair = cleanPair(row.name, asset);
  if (pair.endsWith("USDT")) return `${pair.slice(0, -4)}-USDT-SWAP`;
  if (pair.endsWith("USDC")) return `${pair.slice(0, -4)}-USDC-SWAP`;
  return `${asset}-USDT-SWAP`;
}

function rowTargetUrl(row, source) {
  if (row.url) return String(row.url);

  const id = String(source?.id || "").toLowerCase();
  const label = String(source?.sourceLabel || "").toLowerCase();
  const group = String(source?.group || "").toLowerCase();
  const title = String(source?.title || "");
  const symbol = normalizeMarketSymbol(row.symbol || row.asset || row.name);
  const pair = cleanPair(row.name || row.symbol, symbol);

  if (!symbol && group !== "hk" && group !== "cn") return "";

  if (id.includes("binance") || label === "bn") {
    const asset = symbol.replace(/(USDT|USDC|USD)$/u, "");
    return asset ? `https://www.binance.com/zh-CN/trade/${asset}_USDT?type=spot` : "";
  }

  if (id.includes("okx") || label === "ok") {
    const asset = symbol.replace(/(USDT|USDC|USD)$/u, "");
    const isSwap = /swap|合约/i.test(`${row.name || ""} ${title}`);
    if (isSwap) return `https://www.okx.com/zh-hans/trade-swap/${okxInstId(row, asset).toLowerCase()}`;
    return asset ? `https://www.okx.com/zh-hans/trade-spot/${asset.toLowerCase()}-usdt` : "";
  }

  if (id.includes("bitget") || label === "bg") {
    return pair ? `https://www.bitget.com/zh-CN/spot/${pair}` : "";
  }

  if (group === "aicoin" || id.includes("aicoin") || label === "ai") {
    const note = String(row.note || "").toLowerCase();
    const asset = symbol.replace(/(USDT|USDC|USD)$/u, "");
    if (note.includes("binance")) return `https://www.binance.com/zh-CN/trade/${asset}_USDT?type=spot`;
    if (note.includes("bitget")) return `https://www.bitget.com/zh-CN/spot/${asset}USDT`;
    if (note.includes("okx") || note.includes("swap")) return `https://www.okx.com/zh-hans/trade-swap/${asset.toLowerCase()}-usdt-swap`;
    return asset ? `https://www.aicoin.com/zh-Hans/currencies/${asset.toLowerCase()}` : "";
  }

  if (group === "hk") {
    const code = String(row.symbol || "").replace(/\D/g, "").padStart(5, "0").slice(-5);
    return code ? `https://www.futunn.com/quote/hk/${code}` : "";
  }

  if (group === "us") {
    return symbol ? `https://www.futunn.com/quote/us/${symbol}` : "";
  }

  if (group === "cn") {
    const code = String(row.symbol || "").replace(/\D/g, "").slice(-6);
    return code ? `https://stockpage.10jqka.com.cn/${code}/` : "";
  }

  return "";
}

function primaryMetric(row) {
  return row.price || row.turnover || row.metricLabel || row.note || "--";
}

function isStockGroup(group) {
  return ["hk", "us", "cn"].includes(String(group || "").toLowerCase());
}

function displayAssetName(row, group) {
  return isStockGroup(group) ? row.name || row.symbol || "--" : row.symbol || row.name || "--";
}

function formatTime(value) {
  if (!value) return "刚刚更新";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function setStatus(text, mode = "normal") {
  if (!dataStatus) return;
  dataStatus.textContent = text;
  dataStatus.dataset.mode = mode;
}

function matchesFilter(source) {
  if (state.filter === "all") return true;
  if (state.filter === "crypto") return source.group === "crypto" || source.group === "aicoin";
  return source.group === state.filter;
}

function matchesQuery(row) {
  if (!state.query) return true;
  const target = [row.symbol, row.name, row.note, ...(row.tags || [])].join(" ").toLowerCase();
  return target.includes(state.query.toLowerCase());
}

function priorityIdentityKeys(row) {
  const contract = String(row?.contractAddress || row?.address || "").trim().toLowerCase();
  const symbol = String(row?.symbol || row?.name || "").trim().toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  return [contract ? `contract:${contract}` : "", symbol ? `symbol:${symbol}` : ""].filter(Boolean);
}

function activePriorityScores() {
  const scores = new Map();
  const rows = Array.isArray(activePriorityPayload()?.rows) ? activePriorityPayload().rows : [];
  rows.forEach((row) => {
    priorityIdentityKeys(row).forEach((key) => {
      const current = scores.get(key);
      if (!current || Number(row.narrativeScore || 0) > Number(current.narrativeScore || 0)) scores.set(key, row);
    });
  });
  return scores;
}

function priorityMatch(row, scores) {
  for (const key of priorityIdentityKeys(row)) {
    if (scores.has(key)) return scores.get(key);
  }
  return null;
}

function sortRows(rows, priorityScores = null) {
  const sorted = [...rows];
  if (state.sort === "priority") {
    const scores = priorityScores || activePriorityScores();
    return sorted.sort((a, b) => {
      const aProfile = priorityMatch(a, scores);
      const bProfile = priorityMatch(b, scores);
      const aScore = Number(aProfile?.narrativeScore ?? a.narrativeScore ?? 0);
      const bScore = Number(bProfile?.narrativeScore ?? b.narrativeScore ?? 0);
      return bScore - aScore || Number(a.rank || 999) - Number(b.rank || 999);
    });
  }
  if (state.sort === "heat") return sorted.sort((a, b) => (b.heat || 0) - (a.heat || 0));
  if (state.sort === "change") return sorted.sort((a, b) => parseSignedNumber(b.change) - parseSignedNumber(a.change));
  if (state.sort === "amount") return sorted.sort((a, b) => (b.amount || 0) - (a.amount || 0));
  return sorted.sort((a, b) => a.rank - b.rank);
}

function aveRowForPeriod(row, period = state.avePeriod) {
  const selectedPeriod = normalizeAvePeriod(period);
  const metric = row?.periodMetrics && typeof row.periodMetrics === "object"
    ? row.periodMetrics[selectedPeriod]
    : null;
  if (!metric || typeof metric !== "object") return row;
  return {
    ...row,
    period: selectedPeriod,
    change: metric.change || row.change,
    amount: Number(metric.amount || 0),
    turnover: metric.turnover || row.turnover,
    transactions: Number(metric.transactions || 0),
    note: `Ave ${selectedPeriod} 热搜 · ${Number(metric.transactions || 0)} 笔交易`
  };
}

function aveRowsForSource(source) {
  const selectedChain = normalizeAveChain(state.aveChain);
  let rows = Array.isArray(source?.rows) ? source.rows : [];
  if (selectedChain !== "all") {
    const board = (Array.isArray(source?.chainBoards) ? source.chainBoards : [])
      .find((item) => normalizeAveChain(item?.chain) === selectedChain);
    rows = Array.isArray(board?.rows) ? board.rows : [];
  }
  return rows.slice(0, 10).map((row) => aveRowForPeriod(row));
}

function gmgnRowsForSource(source) {
  const selectedChain = normalizeGmgnChain(state.gmgnChain);
  const selectedPeriod = normalizeGmgnPeriod(state.gmgnPeriod);
  const board = (Array.isArray(source?.periodBoards) ? source.periodBoards : [])
    .find((item) => (
      normalizeGmgnChain(item?.chain) === selectedChain
      && normalizeGmgnPeriod(item?.period) === selectedPeriod
    ));
  const rows = Array.isArray(board?.rows)
    ? board.rows
    : selectedChain === "all" && selectedPeriod === normalizeGmgnPeriod(source?.period)
      ? source?.rows || []
      : [];
  return rows.slice(0, 10);
}

function rowsForSourceView(source) {
  const sourceId = String(source?.id || "");
  if (sourceId === "ave") return aveRowsForSource(source);
  if (sourceId === "gmgn-hot-search") return gmgnRowsForSource(source);
  return source?.rows || [];
}

function visibleSources() {
  const priorityScores = state.sort === "priority" ? activePriorityScores() : null;
  if (state.filter === "total") return [buildTotalBoardSource(priorityScores)];
  return state.sources
    .filter(matchesFilter)
    .map((source) => {
      const rows = rowsForSourceView(source).filter(matchesQuery);
      return {
        ...source,
        rows: String(source?.id || "") === "gmgn-trenches"
          ? rows
          : sortRows(rows, priorityScores)
      };
    });
}

function buildTotalBoardSource(priorityScores = null) {
  const deduper = window.XingyunMarketTotalBoard;
  const totalSources = state.sources.filter((source) => deduper?.isTotalBoardSource(source));
  const buckets = deduper?.dedupeTotalBoardEntries(totalSources, {
    matchesRow: (row) => matchesQuery(row)
  }) || [];
  const rows = buckets.map((bucket) => {
    const candidates = bucket.entries.map((entry) => ({
      ...entry.row,
      totalOriginSource: entry.source
    }));
    const representative = sortRows(candidates, priorityScores)[0] || {};
    const sourceLabels = [...new Set(bucket.entries.map((entry) => String(entry.source?.sourceLabel || "").trim()).filter(Boolean))];
    const sourceTitles = [...new Set(bucket.entries.map((entry) => String(entry.source?.title || "").trim()).filter(Boolean))];
    return {
      ...representative,
      heat: Math.max(0, ...candidates.map((row) => Number(row.heat || 0))),
      amount: Math.max(0, ...candidates.map((row) => Number(row.amount || 0))),
      totalIdentity: bucket.identity,
      totalSourceCount: sourceTitles.length,
      totalSourceLabels: sourceLabels,
      totalSourceTitles: sourceTitles
    };
  });
  const rankedRows = sortRows(rows, priorityScores).map((row, index) => ({
    ...row,
    rank: index + 1
  }));
  const pagination = deduper?.paginateTotalBoardEntries(rankedRows, state.totalPage, TOTAL_BOARD_PAGE_SIZE) || {
    page: 1,
    rows: rankedRows.slice(0, TOTAL_BOARD_PAGE_SIZE),
    totalCount: rankedRows.length,
    totalPages: Math.max(1, Math.ceil(rankedRows.length / TOTAL_BOARD_PAGE_SIZE))
  };
  state.totalPage = pagination.page;
  return {
    id: "total-board",
    group: "total",
    title: "币圈热门总榜",
    subtitle: "币圈与 AIcoin 热门来源合并 · 合约地址优先去重",
    accent: "#f6bb48",
    sourceLabel: "ALL",
    status: rankedRows.length ? "ok" : "unavailable",
    rows: pagination.rows,
    summaryRows: rankedRows,
    totalCount: pagination.totalCount,
    totalPage: pagination.page,
    totalPages: pagination.totalPages,
    emptyTitle: "总榜暂无匹配标的",
    emptyMessage: state.query ? "没有标的符合当前搜索条件。" : "当前热门来源暂时没有返回可用标的。"
  };
}

function activePriorityPayload() {
  const periods = state.smartPriority?.periods;
  return periods && typeof periods === "object" ? periods[state.priorityPeriod] || {} : {};
}

function syncPriorityControls() {
  if (sortSelect && sortSelect.value !== state.sort) sortSelect.value = state.sort;
  if (priorityPeriodSelect) {
    priorityPeriodSelect.value = state.priorityPeriod;
    priorityPeriodSelect.disabled = state.sort !== "priority";
  }
}

function renderSummary(sources) {
  const rows = sources.flatMap((source) =>
    (source.summaryRows || source.rows).map((row) => ({
      ...row,
      board: row.originalBoard || source.title,
      group: row.group || source.group,
      sourceLabel: source.sourceLabel,
      sourceUpdatedAt: source.updatedAt
    }))
  );

  const topHeat = [...rows].sort((a, b) => (b.heat || 0) - (a.heat || 0))[0];
  const topChange = [...rows].sort((a, b) => parseSignedNumber(b.change) - parseSignedNumber(a.change))[0];
  const positiveRatio = rows.length
    ? Math.round((rows.filter((row) => parseSignedNumber(row.change) > 0).length / rows.length) * 100)
    : 0;
  const maxAmount = [...rows].sort((a, b) => (b.amount || 0) - (a.amount || 0))[0];

  const cards = [
    {
      label: "最高热度",
      value: topHeat ? `${displayAssetName(topHeat, topHeat.group)} ${topHeat.heat}` : "--",
      meta: topHeat ? topHeat.board : "暂无可用数据"
    },
    {
      label: "最强涨幅",
      value: topChange ? `${displayAssetName(topChange, topChange.group)} ${topChange.change || "--"}` : "--",
      meta: topChange ? topChange.name : "暂无可用数据"
    },
    {
      label: "上涨占比",
      value: `${positiveRatio}%`,
      meta: `${rows.length} 个标的纳入当前视图`
    },
    {
      label: "成交/交易龙头",
      value: maxAmount ? displayAssetName(maxAmount, maxAmount.group) : "--",
      meta: maxAmount ? maxAmount.turnover || maxAmount.metricLabel || "交易热度" : "暂无可用数据"
    }
  ];

  renderLive(summaryEl, cards
    .map(
      (card) => `
        <article class="summary-card">
          <p>${card.label}</p>
          <strong>${card.value}</strong>
          <span>${card.meta}</span>
        </article>
      `
    )
    .join(""));
}

function renderTicker() {
  if (!tickerRail) return;

  const rows = state.sources
    .flatMap((source) => (source.rows || []).slice(0, 4).map((row) => ({ ...row, sourceLabel: source.sourceLabel, group: source.group })))
    .sort((a, b) => (b.heat || 0) - (a.heat || 0))
    .slice(0, 16);

  if (!rows.length) {
    tickerRail.innerHTML = '<div class="ticker-track"><span class="ticker-item">暂无可用数据，请确认本地服务已启动</span></div>';
    return;
  }

  const items = rows
    .map(
      (row) => `
        <span class="ticker-item">
          <b>${row.sourceLabel}</b>
          <span>${displayAssetName(row, row.group)}</span>
          <em class="${parseSignedNumber(row.change) >= 0 ? "up" : "down"}">${row.change || row.heat}</em>
        </span>
      `
    )
    .join("");

  renderLive(tickerRail, `<div class="ticker-track">${items}${items}</div>`);
}


function safeExternalUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw, window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch {
    return "";
  }
}

function compactGmgnUsd(value) {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount) || amount <= 0) return "--";
  if (amount >= 1_000_000_000) return `$${(amount / 1_000_000_000).toFixed(2)}B`;
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(2)}M`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(1)}K`;
  return `$${amount.toFixed(amount >= 10 ? 0 : 2)}`;
}

function gmgnElapsed(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "时间未知";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds}秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}时`;
  return `${Math.floor(seconds / 86400)}天`;
}

function gmgnXPostTime(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "发布时间未知";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).format(new Date(timestamp));
  } catch {
    return "发布时间未知";
  }
}

function compactGmgnCount(value) {
  const count = Number(value || 0);
  if (!Number.isFinite(count) || count <= 0) return "0";
  if (count >= 10_000) return `${(count / 10_000).toFixed(count >= 100_000 ? 0 : 1)}万`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return String(Math.round(count));
}

function renderGmgnTrenchXTooltip(symbol, original = {}) {
  const statusId = String(original.statusId || "").trim();
  const cached = statusId ? gmgnTrenchXPostCache.get(statusId) || {} : {};
  const post = cached?.post && typeof cached.post === "object" ? cached.post : {};
  const author = post?.author && typeof post.author === "object" ? post.author : {};
  const metrics = post?.metrics && typeof post.metrics === "object" ? post.metrics : {};
  const handle = String(author.handle || original.handle || "").replace(/^@/, "");
  const authorName = String(author.name || (handle ? `@${handle}` : `${symbol} 原 X`));
  const text = String(post.text || original.text || "").trim();
  const avatar = safeExternalUrl(author.avatar);
  const publishedAt = Number(post.publishedAt || original.publishedAt || 0);
  const media = Array.isArray(post.media)
    ? post.media.filter((item) => safeExternalUrl(item?.previewUrl)).slice(0, 4)
    : [];
  const loading = statusId && gmgnTrenchXPostPending.has(statusId);
  const unavailable = cached && cached.ok === false;
  const body = text
    || (loading
      ? "正在按需读取对应原帖正文和媒体…"
      : unavailable
        ? String(cached.error || "原帖可能已删除、受限或暂时不可读取。")
        : statusId
          ? "悬停后读取对应原帖正文和媒体；不会预抓全部历史帖子。"
          : "GMGN 当前只返回了 X 主页，没有可对应的帖子编号。");
  const mediaHtml = media.length ? `
    <span class="gmgn-trench-x-media is-${media.length}">
      ${media.map((item) => {
        const previewUrl = safeExternalUrl(item.previewUrl);
        const mediaType = String(item.type || "photo").toLowerCase();
        return `<span class="gmgn-trench-x-media-item">
          <img src="${escapeHtml(previewUrl)}" alt="${escapeHtml(symbol)} 原帖媒体" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentElement.remove()" />
          ${mediaType === "video" ? '<em>视频</em>' : ""}
        </span>`;
      }).join("")}
    </span>` : "";
  const metricsHtml = post?.statusId ? `
    <span class="gmgn-trench-x-metrics">
      <span>↩ ${escapeHtml(compactGmgnCount(metrics.reply))}</span>
      <span>⇄ ${escapeHtml(compactGmgnCount(metrics.repost))}</span>
      <span>♥ ${escapeHtml(compactGmgnCount(metrics.like))}</span>
      <span>◉ ${escapeHtml(compactGmgnCount(metrics.view))}</span>
    </span>` : "";
  return `
    <span class="gmgn-trench-x-head">
      ${avatar ? `<img src="${escapeHtml(avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : '<i aria-hidden="true">𝕏</i>'}
      <span><b>${escapeHtml(authorName)}</b><small>${escapeHtml(handle ? `@${handle}` : gmgnXPostTime(publishedAt))}</small></span>
      <time>${escapeHtml(gmgnXPostTime(publishedAt))}</time>
    </span>
    <p class="gmgn-trench-x-text">${escapeHtml(body)}</p>
    ${mediaHtml}
    ${metricsHtml}
    <small class="gmgn-trench-x-source">${post?.statusId ? "GMGN 匹配链接 · 免费公开原帖源" : "GMGN 原帖链接 · 首次悬停按需读取"}${statusId ? " · 点击打开" : ""}</small>`;
}

function gmgnTrenchSocialLinks(row) {
  const context = row?.narrativeContext && typeof row.narrativeContext === "object" ? row.narrativeContext : {};
  const links = context?.links && typeof context.links === "object" ? context.links : {};
  const original = row?.xOriginal && typeof row.xOriginal === "object" ? row.xOriginal : {};
  return {
    x: safeExternalUrl(original.url || links.twitter),
    website: safeExternalUrl(links.website),
    gmgn: safeExternalUrl(row.tradeUrl || row.url),
    original
  };
}

function renderGmgnTrenchTool({ kind, label, href = "", tooltip = "", disabled = false, statusId = "", xHandle = "", symbol = "", publishedAt = 0 }) {
  const icons = { person: "人", ai: "✦", wallet: "▣", x: "𝕏", web: "◎", gmgn: "↗" };
  const tag = href ? "a" : "button";
  const linkAttrs = href ? `href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer"` : 'type="button"';
  const xAttrs = kind === "x"
    ? `data-status-id="${escapeHtml(statusId)}" data-x-handle="${escapeHtml(xHandle)}" data-symbol="${escapeHtml(symbol)}" data-published-at="${escapeHtml(publishedAt)}"`
    : "";
  return `
    <${tag} class="gmgn-trench-tool is-${kind} ${disabled ? "is-disabled" : ""}" ${linkAttrs} ${xAttrs} aria-label="${escapeHtml(label)}">
      <span aria-hidden="true">${icons[kind] || "·"}</span>
      ${tooltip ? `<span class="gmgn-trench-popover" role="tooltip">${tooltip}</span>` : ""}
    </${tag}>`;
}

async function writeClipboardText(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through when the browser blocks Clipboard API access.
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  textarea.setSelectionRange(0, text.length);
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  textarea.remove();
  return copied;
}

async function copyGmgnTrenchContract(button) {
  if (!button || button.disabled) return;
  const contract = String(button.dataset.contract || "").trim();
  if (!contract) return;
  const originalLabel = String(button.dataset.shortContract || button.textContent || "CA").trim();
  button.disabled = true;
  const copied = await writeClipboardText(contract);
  if (!button.isConnected) return;
  button.classList.toggle("is-copied", copied);
  button.classList.toggle("is-copy-error", !copied);
  button.textContent = copied ? "CA 已复制" : "复制失败";
  button.title = copied ? `已复制：${contract}` : "浏览器未允许复制，请重试";
  window.setTimeout(() => {
    if (!button.isConnected) return;
    button.textContent = originalLabel;
    button.title = `点击复制完整 CA：${contract}`;
    button.classList.remove("is-copied", "is-copy-error");
    button.disabled = false;
  }, 1600);
}

function renderGmgnTrenchRow(row, source, index) {
  const metrics = row?.metrics && typeof row.metrics === "object" ? row.metrics : {};
  const facts = row?.launchFacts && typeof row.launchFacts === "object" ? row.launchFacts : {};
  const socials = gmgnTrenchSocialLinks(row);
  const aiResult = exchangeAiResult(row, source);
  const narrative = aiResult.narrative;
  const aiKey = exchangeAiIdentity(row, source);
  const aiPending = exchangeAiNarrativePending.has(aiKey);
  const chain = String(row?.chain || row?.network || "").trim().toLowerCase();
  const binanceAiSupported = ["eth", "ethereum", "bsc", "bnb", "base", "robinhood", "sol", "solana"].includes(chain);
  const xHandle = String(socials.original?.handle || "").replace(/^@/, "");
  const xStatusId = String(socials.original?.statusId || "").trim();
  const xPublishedAt = Number(socials.original?.publishedAt || 0);
  const symbol = String(row?.symbol || "--");
  const name = String(row?.name || symbol);
  const contract = String(row?.contractAddress || "");
  const binanceWallet = safeExternalUrl(row?.binanceWalletUrl);
  const binanceWalletAttrs = binanceWallet
    ? `href="${escapeHtml(binanceWallet)}" target="_blank" rel="noopener noreferrer"`
    : "";
  const avatarMarkup = binanceWallet
    ? `<a class="gmgn-trench-wallet-avatar" ${binanceWalletAttrs} title="在币安钱包交易 ${escapeHtml(symbol)}" aria-label="在币安钱包交易 ${escapeHtml(symbol)}">${renderAssetIcon(row, source)}</a>`
    : `<span class="gmgn-trench-avatar">${renderAssetIcon(row, source)}</span>`;
  const shortContract = contract.length > 13 ? `${contract.slice(0, 5)}…${contract.slice(-5)}` : contract;
  const filterWarnings = Array.isArray(row?.filterWarnings) ? row.filterWarnings.filter(Boolean) : [];
  const filterWarningBadge = filterWarnings.length
    ? `<i class="gmgn-trench-risk-note" title="${escapeHtml(filterWarnings.join(" · "))}">风险</i>` : "";
  const researchMark = row?.researchMark && typeof row.researchMark === "object" ? row.researchMark : null;
  const researchMarkTitle = researchMark ? [
    researchMark.label || "V4.9 好标的",
    researchMark.potentialLabel,
    [researchMark.attentionState, researchMark.currentStage].filter(Boolean).join(" / "),
    Number(researchMark.opportunityScore) > 0 ? `机会分 ${Math.round(Number(researchMark.opportunityScore))}` : "",
    researchMark.summary,
    researchMark.nextTrigger ? `下一触发：${researchMark.nextTrigger}` : "",
    researchMark.invalidation ? `失效：${researchMark.invalidation}` : "",
    researchMark.executionPermission ? `执行许可 ${researchMark.executionPermission}` : ""
  ].filter(Boolean).join(" · ") : "";
  const researchBadge = researchMark
    ? `<i class="gmgn-trench-research-badge" title="${escapeHtml(researchMarkTitle)}">${escapeHtml(researchMark.label || "V4.9 好标的")}</i>`
    : "";
  const personSignal = row?.personSignal && typeof row.personSignal === "object" ? row.personSignal : null;
  const personName = String(personSignal?.personName || personSignal?.personHandle || "重要人物").trim();
  const personAction = String(personSignal?.actionLabel || "点名").trim();
  const personTooltip = personSignal ? `
    <b>${escapeHtml(personName)} · ${escapeHtml(personAction)} ${escapeHtml(symbol)}</b>
    <p>${escapeHtml(personSignal.postText || personSignal.quoteText || "已捕捉到与该战壕新币直接相关的原始人物动态。")}</p>
    <small>${escapeHtml(personSignal.personRole || "重要人物")} · 置信度 ${Math.round(Number(personSignal.confidence) || 0)}% · 已进入 V4.9 快速投研</small>` : "";
  const personBadge = personSignal
    ? `<i class="gmgn-trench-person-badge" title="${escapeHtml(`${personName} ${personAction}`)}">人物${escapeHtml(personAction)}</i>`
    : "";
  const aiCopy = narrative
    || (!binanceAiSupported
      ? "币安 AI 叙事接口当前不支持这条链，因此不会发送无效请求。"
      : aiPending
        ? "币安 AI 叙事正在按限速队列读取，请稍候。"
        : aiResult.status === "UNAVAILABLE"
          ? "币安 AI 当前未返回该合约的叙事；空结果已缓存，短时间内不会重复请求。"
          : "将通过热门榜同款币安 AI 叙事接口读取；只处理首屏最新标的。");
  const aiTooltip = `
    <b>${escapeHtml(symbol)} · 币安 AI 叙事</b>
    <p>${escapeHtml(aiCopy)}</p>
    <small>${narrative ? "来源：热门榜同款 Binance AI 接口" : "最新 10 个按需读取 · 正负结果均缓存"}</small>`;
  const xTooltip = socials.x ? renderGmgnTrenchXTooltip(symbol, socials.original) : "";
  const tools = [
    personSignal ? renderGmgnTrenchTool({
      kind: "person",
      label: `查看 ${personName} 的${personAction}信号`,
      href: safeExternalUrl(personSignal.postUrl),
      tooltip: personTooltip
    }) : "",
    renderGmgnTrenchTool({ kind: "ai", label: `查看 ${symbol} 的币安 AI 叙事`, tooltip: aiTooltip }),
    binanceWallet ? renderGmgnTrenchTool({
      kind: "wallet",
      label: `在币安钱包交易 ${symbol}`,
      href: binanceWallet,
      tooltip: "打开币安 Web3 对应合约交易页；沿用当前浏览器已登录的币安会话。"
    }) : "",
    socials.x ? renderGmgnTrenchTool({
      kind: "x",
      label: `查看 ${symbol} 的对应原 X`,
      href: socials.x,
      tooltip: xTooltip,
      statusId: xStatusId,
      xHandle,
      symbol,
      publishedAt: xPublishedAt
    }) : "",
    socials.website ? renderGmgnTrenchTool({ kind: "web", label: `打开 ${symbol} 网站`, href: socials.website }) : "",
    socials.gmgn ? renderGmgnTrenchTool({ kind: "gmgn", label: `在 GMGN 查看 ${symbol}`, href: socials.gmgn }) : ""
  ].join("");
  const liveKey = `${row?.network || row?.chain || "chain"}:${contract || symbol}:${row?.receivedAt || index}`;
  return `
    <article class="gmgn-trench-hot-row ${researchMark ? "is-research-pick" : ""}" data-live-key="gmgn-trench:${escapeHtml(liveKey)}" role="listitem">
      <div class="gmgn-trench-identity">
        <span class="gmgn-trench-rank">${escapeHtml(index)}</span>
        ${avatarMarkup}
        <div class="gmgn-trench-copy">
          <div class="gmgn-trench-name">
            ${binanceWallet
              ? `<a class="gmgn-trench-token-link" ${binanceWalletAttrs} title="在币安钱包交易 ${escapeHtml(symbol)}"><strong>${escapeHtml(symbol)}</strong><span>${escapeHtml(name)}</span></a>`
              : `<span class="gmgn-trench-token-link is-static"><strong>${escapeHtml(symbol)}</strong><span>${escapeHtml(name)}</span></span>`}
            ${researchBadge}
            ${personBadge}
            ${filterWarningBadge}
          </div>
          <div class="gmgn-trench-subline">
            <em>${escapeHtml(gmgnElapsed(row?.poolCreatedAt))}</em>
            <b>${escapeHtml(row?.chainLabel || row?.network || "链")}</b>
            ${contract
              ? `<button class="gmgn-trench-ca-copy" type="button" data-contract="${escapeHtml(contract)}" data-short-contract="${escapeHtml(shortContract)}" title="点击复制完整 CA：${escapeHtml(contract)}" aria-label="复制 ${escapeHtml(symbol)} 的完整合约地址" aria-live="polite">${escapeHtml(shortContract)}</button>`
              : '<span class="gmgn-trench-ca-missing">CA 未返回</span>'}
          </div>
          <div class="gmgn-trench-tools">${tools}</div>
        </div>
      </div>
      <div class="gmgn-trench-metrics" aria-label="${escapeHtml(symbol)} 链上数据">
        <span><small>MC</small><b>${compactGmgnUsd(metrics.marketCapUsd || metrics.fdvUsd || row.marketCapUsd)}</b></span>
        <span><small>流动性</small><b>${compactGmgnUsd(metrics.liquidityUsd || row.liquidityUsd)}</b></span>
        <span><small>24H</small><b>${compactGmgnUsd(metrics.volumeH24Usd || row.amount)}</b></span>
        <span><small>TX</small><b>${escapeHtml(metrics.transactionsH24 || row.transactions || "--")}</b></span>
        <span><small>持有人</small><b>${escapeHtml(facts.holders || metrics.holders || "--")}</b></span>
      </div>
    </article>`;
}

function renderGmgnTrenchBoard(source) {
  const rows = Array.isArray(source?.rows) ? source.rows : [];
  const online = Number(source?.sourceOnline || 0);
  const total = Number(source?.sourceTotal || 6);
  const liveState = source?.rateLimited
    ? `GMGN 限频保护中 · ${Math.ceil(Number(source.retryAfterSeconds || 0) / 60)} 分钟后再试`
    : source?.live
      ? `GMGN 来源在线 ${online}/${total}`
      : "实时源暂时断开 · 正在展示已接收历史";
  if (!rows.length) return `<div class="rows">${renderEmpty(source)}</div>`;
  return `
    <div class="gmgn-trench-history-strip">
      <span><b>${escapeHtml(source.historyCount || rows.length)}</b> 个已接收新币</span>
      <small>${escapeHtml(liveState)} · 严格按开盘时间倒序，首屏最新 10 个</small>
    </div>
    <div class="gmgn-trench-hot-scroll" role="list" aria-label="GMGN 战壕新币接收历史">
      ${rows.map((row, index) => renderGmgnTrenchRow(row, source, index + 1)).join("")}
    </div>`;
}

function refreshGmgnTrenchXTools(statusId) {
  if (!/^\d{2,20}$/.test(String(statusId || ""))) return;
  boardsEl.querySelectorAll(`.gmgn-trench-tool.is-x[data-status-id="${statusId}"]`).forEach((tool) => {
    const popover = tool.querySelector(".gmgn-trench-popover");
    if (!popover) return;
    popover.innerHTML = renderGmgnTrenchXTooltip(tool.dataset.symbol || "原帖", {
      statusId,
      handle: tool.dataset.xHandle || "",
      publishedAt: Number(tool.dataset.publishedAt || 0)
    });
  });
}

async function drainGmgnTrenchXPostQueue() {
  if (gmgnTrenchXPostRequestActive) return;
  const item = gmgnTrenchXPostQueue.shift();
  if (!item) return;
  gmgnTrenchXPostRequestActive = true;
  try {
    const response = await fetch(`/api/gmgn-trench-x-post?statusId=${encodeURIComponent(item.statusId)}`, {
      cache: "no-store",
      headers: { "Accept": "application/json" }
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
    gmgnTrenchXPostCache.set(item.statusId, payload);
    if (payload?.ok) {
      gmgnTrenchXPostRetryAt.delete(item.statusId);
    } else {
      gmgnTrenchXPostRetryAt.set(item.statusId, Date.now() + 10 * 60 * 1000);
    }
  } catch (error) {
    gmgnTrenchXPostCache.set(item.statusId, {
      ok: false,
      status: "unavailable",
      error: "原帖暂时读取失败，请稍后再悬停查看。"
    });
    gmgnTrenchXPostRetryAt.set(item.statusId, Date.now() + 10 * 60 * 1000);
    console.warn("GMGN trench X post lookup failed; keeping the original link.", error);
  } finally {
    gmgnTrenchXPostPending.delete(item.statusId);
    gmgnTrenchXPostRequestActive = false;
    refreshGmgnTrenchXTools(item.statusId);
    if (gmgnTrenchXPostQueue.length) {
      setTimeout(() => void drainGmgnTrenchXPostQueue(), 300);
    }
  }
}

function queueGmgnTrenchXPost(tool) {
  const statusId = String(tool?.dataset?.statusId || "").trim();
  if (!/^\d{2,20}$/.test(statusId) || gmgnTrenchXPostPending.has(statusId)) return;
  const retryAt = gmgnTrenchXPostRetryAt.get(statusId) || 0;
  const cached = gmgnTrenchXPostCache.get(statusId);
  if (cached?.ok || retryAt > Date.now()) return;
  if (cached) gmgnTrenchXPostCache.delete(statusId);
  gmgnTrenchXPostPending.add(statusId);
  gmgnTrenchXPostQueue.push({ statusId });
  refreshGmgnTrenchXTools(statusId);
  void drainGmgnTrenchXPostQueue();
}

function renderBoardRows(source) {
  if (String(source?.id || "") === "gmgn-trenches") return renderGmgnTrenchBoard(source);
  return `
    <div class="rows">
      ${source.rows.length
        ? `<div class="board-table-head"><span>#</span><span>名称</span><span>价格/热度</span><span>涨跌幅</span></div>${source.rows.map((row, rowIndex) => renderRow(row, source, rowIndex + 1)).join("")}`
        : renderEmpty(source)}
    </div>`;
}

function renderBoards() {
  syncPriorityControls();
  const sources = visibleSources();
  renderSummary(sources);

  renderLive(boardsEl, sources
    .map(
      (source, index) => `
        <article class="board-card ${source.status === "unavailable" ? "is-muted" : ""} ${source.id === "total-board" ? "is-total-board" : ""}" data-live-key="board:${escapeHtml(source.id || source.title)}" style="--accent: ${source.accent}; --delay: ${index * 55}ms">
          <header class="board-head">
            <div class="board-head-copy">
              <p>${groupLabels[source.group] || source.group}</p>
              <h3>${source.title}</h3>
              ${source.subtitle ? `<span>${escapeHtml(source.subtitle)}</span>` : ""}
            </div>
            ${renderBoardHeadActions(source)}
          </header>
          ${renderBoardRows(source)}
          ${renderTotalPagination(source)}
        </article>
      `
    )
    .join(""));
  if (state.filter !== "total") {
    requestAiInsights(sources.filter((source) => String(source?.id || "") !== "gmgn-trenches"));
  }
  requestExchangeAiNarratives(sources);
}

function renderBoardHeadActions(source) {
  if (String(source?.id || "") === "total-board") {
    return `<div class="board-head-actions is-total-head"><span>${source.totalCount || source.rows.length} 个去重标的</span><strong>ALL</strong></div>`;
  }
  if (String(source?.id || "") === "gmgn-trenches") {
    return `
      <div class="board-head-actions is-gmgn-trenches-head">
        <span>${escapeHtml(source.currentCount || 0)} 个本轮</span>
        <strong>GMGN</strong>
      </div>`;
  }
  if (String(source?.id || "") === "ave") {
    const periodOptions = Array.isArray(source.periodOptions) && source.periodOptions.length
      ? source.periodOptions
      : [{ value: "1h", label: "1 小时" }, { value: "4h", label: "4 小时" }, { value: "24h", label: "24 小时" }];
    const chainOptions = Array.isArray(source.chainOptions) && source.chainOptions.length
      ? source.chainOptions
      : [
          { value: "all", label: "综合" }, { value: "robinhood", label: "Robinhood" },
          { value: "solana", label: "SOL" }, { value: "eth", label: "ETH" },
          { value: "bsc", label: "BSC" }, { value: "base", label: "Base" }
        ];
    return `
      <div class="board-head-actions is-ave-hot is-onchain-hot">
        <label class="board-period-control">
          <span>周期</span>
          <select data-role="ave-period" aria-label="选择 Ave 热搜观察时间">
            ${periodOptions.map((option) => {
              const value = normalizeAvePeriod(option?.value);
              return `<option value="${escapeHtml(value)}" ${value === state.avePeriod ? "selected" : ""}>${escapeHtml(option?.label || value)}</option>`;
            }).join("")}
          </select>
        </label>
        <label class="board-period-control">
          <span>链</span>
          <select data-role="ave-chain" aria-label="选择 Ave 专链热搜榜">
            ${chainOptions.map((option) => {
              const value = normalizeAveChain(option?.value);
              return `<option value="${escapeHtml(value)}" ${value === state.aveChain ? "selected" : ""}>${escapeHtml(option?.label || value)}</option>`;
            }).join("")}
          </select>
        </label>
        <strong>${escapeHtml(source.sourceLabel || "AVE")}</strong>
      </div>
    `;
  }
  if (String(source?.id || "") === "gmgn-hot-search") {
    const periodOptions = Array.isArray(source.periodOptions) && source.periodOptions.length
      ? source.periodOptions
      : ["1m", "5m", "1h", "6h", "24h"].map((value) => ({ value, label: value }));
    const chainOptions = Array.isArray(source.chainOptions) && source.chainOptions.length
      ? source.chainOptions
      : [
          { value: "all", label: "综合" }, { value: "sol", label: "SOL" },
          { value: "bsc", label: "BSC" }, { value: "base", label: "Base" },
          { value: "eth", label: "ETH" }, { value: "robinhood", label: "Robinhood" },
          { value: "arc", label: "ARC" }, { value: "stable", label: "Stable" }
        ];
    return `
      <div class="board-head-actions is-gmgn-hot is-onchain-hot">
        <label class="board-period-control">
          <span>周期</span>
          <select data-role="gmgn-period" aria-label="选择 GMGN 热搜观察时间">
            ${periodOptions.map((option) => {
              const value = normalizeGmgnPeriod(option?.value);
              return `<option value="${escapeHtml(value)}" ${value === state.gmgnPeriod ? "selected" : ""}>${escapeHtml(option?.label || value)}</option>`;
            }).join("")}
          </select>
        </label>
        <label class="board-period-control">
          <span>链</span>
          <select data-role="gmgn-chain" aria-label="选择 GMGN 热搜链">
            ${chainOptions.map((option) => {
              const value = normalizeGmgnChain(option?.value);
              return `<option value="${escapeHtml(value)}" ${value === state.gmgnChain ? "selected" : ""}>${escapeHtml(option?.label || value)}</option>`;
            }).join("")}
          </select>
        </label>
        <strong>${escapeHtml(source.sourceLabel || "GMGN")}</strong>
      </div>
    `;
  }
  if (String(source?.id || "") !== "binance-wallet-hot") {
    return `<div class="board-head-actions"><strong>${escapeHtml(source.sourceLabel || "--")}</strong></div>`;
  }

  const selectedPeriod = normalizeBinanceWalletPeriod(state.binanceWalletPeriod || source.period);
  const options = Array.isArray(source.periodOptions) && source.periodOptions.length
    ? source.periodOptions
    : [
        { value: "5m", label: "5 分钟" },
        { value: "1h", label: "1 小时" },
        { value: "4h", label: "4 小时" },
        { value: "24h", label: "24 小时" }
      ];
  const optionHtml = options
    .map((option) => {
      const value = normalizeBinanceWalletPeriod(option?.value);
      return `<option value="${escapeHtml(value)}" ${value === selectedPeriod ? "selected" : ""}>${escapeHtml(option?.label || value)}</option>`;
    })
    .join("");

  return `
    <div class="board-head-actions is-wallet-hot">
      <label class="board-period-control ${state.binanceWalletLoading ? "is-loading" : ""}">
        <span>观察窗口</span>
        <select data-role="binance-wallet-period" aria-label="选择币安钱包热度观察时间" ${state.binanceWalletLoading ? "disabled" : ""}>
          ${optionHtml}
        </select>
      </label>
      <strong>${escapeHtml(source.sourceLabel || "BW")}</strong>
    </div>
  `;
}

function totalPageTokens(currentPage, totalPages) {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const pages = new Set([1, totalPages, currentPage - 1, currentPage, currentPage + 1]);
  const visible = [...pages].filter((page) => page >= 1 && page <= totalPages).sort((a, b) => a - b);
  const tokens = [];
  visible.forEach((page, index) => {
    if (index && page - visible[index - 1] > 1) tokens.push("ellipsis");
    tokens.push(page);
  });
  return tokens;
}

function renderTotalPagination(source) {
  if (String(source?.id || "") !== "total-board" || Number(source.totalPages || 1) <= 1) return "";
  const currentPage = Number(source.totalPage || 1);
  const totalPages = Number(source.totalPages || 1);
  const pageButtons = totalPageTokens(currentPage, totalPages)
    .map((token) => token === "ellipsis"
      ? '<span class="total-page-ellipsis" aria-hidden="true">…</span>'
      : `<button type="button" class="total-page-button ${token === currentPage ? "active" : ""}" data-role="total-page" data-page="${token}" ${token === currentPage ? 'aria-current="page"' : ""}>${token}</button>`)
    .join("");
  return `
    <nav class="total-pagination" aria-label="总榜分页">
      <button type="button" class="total-page-button total-page-nav" data-role="total-page" data-page="${currentPage - 1}" ${currentPage <= 1 ? "disabled" : ""}>上一页</button>
      <div class="total-page-numbers">${pageButtons}</div>
      <button type="button" class="total-page-button total-page-nav" data-role="total-page" data-page="${currentPage + 1}" ${currentPage >= totalPages ? "disabled" : ""}>下一页</button>
      <span class="total-page-status">第 ${currentPage} / ${totalPages} 页 · 每页 ${TOTAL_BOARD_PAGE_SIZE} 个</span>
    </nav>`;
}

function requestAiInsights(sources) {
  window.XingyunAiInsights?.requestForSources(sources, {
    mode: "hot",
    onUpdate: renderBoards
  });
}

function renderEmpty(source) {
  return `
    <div class="empty-state">
      <b>${source.emptyTitle || "暂无数据"}</b>
      <span>${source.emptyMessage || "这个数据源当前没有返回可用榜单。"}</span>
    </div>
  `;
}

function renderInsight(row, source, rank) {
  const insight = window.XingyunInsights?.buildRowInsight(row, { source, rank, mode: "hot" });
  if (!insight) return "";
  const tone = insight.tone === "is-hot" ? " is-hot" : "";
  const provider = window.XingyunAiInsights?.providerLabel?.(insight.provider) || "规则";
  return `<em class="row-insight-text${tone}" title="${escapeHtml(`${provider} 分析 · ${insight.detail}`)}"><b>${escapeHtml(provider)}</b>${escapeHtml(insight.detail)}</em>`;
}

function renderChainBadge(row, source) {
  const sourceId = String(source?.id || source?.sourceName || source?.sourceLabel || "").toLowerCase();
  if (!sourceId.includes("okx-dex") && !sourceId.includes("ave") && !sourceId.includes("gmgn") && !sourceId.includes("binance-wallet")) return "";
  const label = String(row.chainLabel || row.chain || "").trim();
  if (!label) return "";
  return `<em class="chain-badge" title="${escapeHtml(row.chain || label)}">${escapeHtml(label.toUpperCase())}</em>`;
}

function exchangeAiContract(row) {
  const explicit = String(row?.contractAddress || row?.address || "").trim();
  if (explicit) return explicit;
  const match = String(row?.url || "").match(/\/token\/[^/]+\/([^/?#]+)/i);
  if (!match?.[1]) return "";
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

function exchangeAiIdentity(row, source) {
  const sourceId = String(source?.id || source?.sourceName || source?.sourceLabel || "").trim().toLowerCase();
  const chain = String(row?.chain || row?.chainLabel || "").trim().toLowerCase();
  const contract = exchangeAiContract(row).toLowerCase();
  const symbol = String(row?.symbol || row?.name || "").trim().toUpperCase();
  return `${sourceId}:${chain}:${contract || symbol}`;
}

function exchangeAiResult(row, source) {
  const cached = exchangeAiNarrativeCache.get(exchangeAiIdentity(row, source)) || {};
  return {
    narrative: String(row?.exchangeAiNarrative || row?.binanceAiNarrative || cached.exchangeAiNarrative || "").trim(),
    source: String(row?.exchangeAiNarrativeSource || row?.binanceAiNarrativeSource || cached.exchangeAiNarrativeSource || "Binance AI").trim(),
    provider: String(row?.exchangeAiNarrativeProvider || cached.exchangeAiNarrativeProvider || "binance").trim(),
    status: String(row?.exchangeAiNarrativeStatus || row?.binanceAiNarrativeStatus || cached.exchangeAiNarrativeStatus || "").trim().toUpperCase()
  };
}

function renderExchangeAiNarrative(row, source) {
  const result = exchangeAiResult(row, source);
  const narrative = result.narrative;
  if (!narrative) return "";
  const symbol = String(row?.symbol || row?.name || "该标的").trim();
  const providerName = result.source || "交易所 AI";
  return `
    <span class="binance-ai-narrative" data-ai-provider="${escapeHtml(result.provider)}" tabindex="0" title="" aria-label="查看 ${escapeHtml(symbol)} 的 ${escapeHtml(providerName)} 叙事分析">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M20.074 2.5c.787 0 1.425.638 1.425 1.425v16.149c0 .787-.638 1.425-1.425 1.425H3.925A1.425 1.425 0 0 1 2.5 20.074V3.925C2.5 3.138 3.138 2.5 3.925 2.5h16.149Zm-9.342 5.11a.3.3 0 0 0-.541.005l-1.675 3.737a.3.3 0 0 1-.149.149l-3.793 1.724a.3.3 0 0 0-.004.541l3.752 1.768a.3.3 0 0 1 .143.143l1.768 3.751a.3.3 0 0 0 .541-.004l1.724-3.793a.3.3 0 0 1 .149-.148l3.737-1.676a.3.3 0 0 0 .004-.54l-3.747-1.767a.3.3 0 0 1-.143-.143L10.732 7.61Zm6.062-3.058a.267.267 0 0 0-.482.004l-.765 1.708a.267.267 0 0 1-.132.133l-1.74.79a.267.267 0 0 0-.003.481l1.721.812a.267.267 0 0 1 .127.126l.811 1.722a.267.267 0 0 0 .48-.004l.792-1.74a.267.267 0 0 1 .132-.131l1.708-.767a.267.267 0 0 0 .005-.48l-1.718-.81a.267.267 0 0 1-.126-.127l-.81-1.717Z" />
      </svg>
      <span class="binance-ai-narrative-tooltip" role="tooltip">
        <span class="binance-ai-narrative-copy">${escapeHtml(narrative)}</span>
        <small>免责声明：本内容由 ${escapeHtml(providerName)} 生成或整理，仅供参考。投资前请务必自行研究。</small>
      </span>
    </span>`;
}

function exchangeAiCandidate(row, source) {
  const sourceId = String(source?.id || "").trim().toLowerCase();
  if (exchangeAiResult(row, source).narrative) return false;
  if ([
    "binance", "binance-gainers",
    "okx", "okx-gainers", "okx-turnover",
    "bitget", "bitget-gainers",
    "aicoin"
  ].includes(sourceId)) {
    return Boolean(String(row?.symbol || "").trim());
  }
  if (!["okx-dex", "okx-dex-gainers", "ave", "gmgn-hot-search", "gmgn-trenches"].includes(sourceId)) return false;
  if (sourceId === "gmgn-trenches") {
    const chain = String(row?.chain || row?.network || "").trim().toLowerCase();
    if (!["eth", "ethereum", "bsc", "bnb", "base", "robinhood", "sol", "solana"].includes(chain)) return false;
  }
  return Boolean(String(row?.chain || "").trim() && exchangeAiContract(row));
}

async function requestExchangeAiNarratives(sources) {
  if (exchangeAiNarrativeRequestActive) return;
  const now = Date.now();
  const items = [];
  const seen = new Set();
  const gmgnTrenchBatchLimit = 4;
  const sourceList = Array.isArray(sources) ? sources : [];
  const prioritizedSources = [
    ...sourceList.filter((source) => String(source?.id || "").toLowerCase() === "gmgn-trenches"),
    ...sourceList.filter((source) => String(source?.id || "").toLowerCase() !== "gmgn-trenches")
  ];
  for (const source of prioritizedSources) {
    const sourceId = String(source?.id || "").toLowerCase();
    const sourceRows = Array.isArray(source?.rows) ? source.rows : [];
    const rows = sourceId === "gmgn-trenches"
      ? sourceRows.slice(0, Math.min(10, Math.max(1, Number(source?.visibleRows || 10))))
      : sourceRows;
    for (const row of rows) {
      const rowSource = row?.totalOriginSource || source;
      if (!exchangeAiCandidate(row, rowSource)) continue;
      const key = exchangeAiIdentity(row, rowSource);
      if (!key || seen.has(key) || exchangeAiNarrativeCache.has(key) || exchangeAiNarrativePending.has(key)) continue;
      if ((exchangeAiNarrativeRetryAt.get(key) || 0) > now) continue;
      seen.add(key);
      items.push({
        key,
        sourceId: String(rowSource?.id || ""),
        symbol: String(row?.symbol || ""),
        name: String(row?.name || ""),
        chain: String(row?.chain || ""),
        contractAddress: exchangeAiContract(row)
      });
      if (sourceId === "gmgn-trenches" && items.length >= gmgnTrenchBatchLimit) break;
      if (items.length >= EXCHANGE_AI_REQUEST_LIMIT) break;
    }
    // Finish this small protected batch first. renderBoards() runs again after
    // completion and advances through the remaining rows within the latest 10.
    if (sourceId === "gmgn-trenches" && items.length) break;
    if (items.length >= EXCHANGE_AI_REQUEST_LIMIT) break;
  }
  if (!items.length) return;

  exchangeAiNarrativeRequestActive = true;
  items.forEach((item) => exchangeAiNarrativePending.add(item.key));
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45_000);
  let shouldRender = false;
  try {
    const response = await fetch("/api/exchange-ai-narratives", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
      signal: controller.signal
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const returned = Array.isArray(payload?.items) ? payload.items : [];
    const returnedKeys = new Set();
    returned.forEach((item) => {
      const key = String(item?.key || "");
      if (!key) return;
      returnedKeys.add(key);
      exchangeAiNarrativeCache.set(key, item);
      exchangeAiNarrativeRetryAt.delete(key);
    });
    items.forEach((item) => {
      if (!returnedKeys.has(item.key)) {
        exchangeAiNarrativeCache.set(item.key, { exchangeAiNarrativeStatus: "UNAVAILABLE" });
      }
    });
    shouldRender = true;
  } catch (error) {
    const retryAt = Date.now() + 5 * 60 * 1000;
    items.forEach((item) => exchangeAiNarrativeRetryAt.set(item.key, retryAt));
    console.warn("Exchange AI narrative request failed; keeping existing insights.", error);
  } finally {
    clearTimeout(timeout);
    items.forEach((item) => exchangeAiNarrativePending.delete(item.key));
    exchangeAiNarrativeRequestActive = false;
    if (shouldRender) queueMicrotask(renderBoards);
  }
}

function currentBinanceWalletSource(period = state.binanceWalletPeriod) {
  const normalizedPeriod = normalizeBinanceWalletPeriod(period);
  return state.sources.find(
    (source) => String(source?.id || "") === "binance-wallet-hot"
      && normalizeBinanceWalletPeriod(source?.period) === normalizedPeriod
  );
}

function replaceBinanceWalletSource(source) {
  const sourceIndex = state.sources.findIndex((item) => String(item?.id || "") === "binance-wallet-hot");
  if (sourceIndex >= 0) {
    state.sources = state.sources.map((item, index) => (index === sourceIndex ? source : item));
  } else {
    state.sources = [source, ...state.sources];
  }
}

async function loadBinanceWalletPeriod(period, options = {}) {
  const normalizedPeriod = normalizeBinanceWalletPeriod(period);
  state.binanceWalletPeriod = normalizedPeriod;
  saveBinanceWalletPeriod(normalizedPeriod);
  if (!options.refresh && currentBinanceWalletSource(normalizedPeriod)) {
    renderBoards();
    return;
  }
  if (state.binanceWalletLoading) return;

  state.binanceWalletLoading = true;
  renderBoards();
  try {
    const suffix = options.refresh ? "&refresh=1" : "";
    const response = await fetch(`/api/binance-wallet-hot?period=${encodeURIComponent(normalizedPeriod)}${suffix}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const source = await response.json();
    if (!source || String(source.id || "") !== "binance-wallet-hot") throw new Error("Invalid Binance Wallet payload");
    replaceBinanceWalletSource(source);
    writeCachedPayload(MARKET_CACHE_KEY, {
      sources: state.sources,
      smartPriority: state.smartPriority,
      updatedAt: new Date().toISOString()
    });
    setStatus(`币安钱包 ${source.periodLabel || normalizedPeriod}热榜`, source.status === "ok" ? "ok" : "normal");
    renderTicker();
  } catch (error) {
    setStatus("币安钱包热榜暂时保留上次数据", "error");
    console.warn("Binance Wallet period refresh failed; keeping previous data.", error);
  } finally {
    state.binanceWalletLoading = false;
    renderBoards();
  }
}

function currentGmgnHotSource() {
  return state.sources.find((source) => String(source?.id || "") === "gmgn-hot-search");
}

function replaceGmgnHotSource(source) {
  const sourceIndex = state.sources.findIndex((item) => String(item?.id || "") === "gmgn-hot-search");
  if (sourceIndex >= 0) {
    state.sources = state.sources.map((item, index) => (index === sourceIndex ? source : item));
    return;
  }
  const aveIndex = state.sources.findIndex((item) => String(item?.id || "") === "ave");
  if (aveIndex >= 0) {
    state.sources = [
      ...state.sources.slice(0, aveIndex + 1),
      source,
      ...state.sources.slice(aveIndex + 1)
    ];
  } else {
    state.sources = [...state.sources, source];
  }
}

async function loadGmgnHotSource(options = {}) {
  if (!options.refresh && currentGmgnHotSource()?.periodBoards?.length) return;
  if (state.gmgnHotLoading) return;
  state.gmgnHotLoading = true;
  try {
    const suffix = options.refresh ? "?refresh=1" : "";
    const response = await fetch(`/api/gmgn-hot-search${suffix}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const source = await response.json();
    if (!source || String(source.id || "") !== "gmgn-hot-search" || !Array.isArray(source.periodBoards)) {
      throw new Error("Invalid GMGN Hot Search payload");
    }
    replaceGmgnHotSource(source);
    writeCachedPayload(MARKET_CACHE_KEY, {
      sources: state.sources,
      smartPriority: state.smartPriority,
      updatedAt: new Date().toISOString()
    });
    renderTicker();
    renderBoards();
  } catch (error) {
    console.warn("GMGN Hot Search refresh failed; keeping previous data.", error);
  } finally {
    state.gmgnHotLoading = false;
  }
}

function renderRow(row, source, rank) {
  const rowSource = row.totalOriginSource || source;
  const change = parseSignedNumber(row.change);
  const direction = change >= 0 ? "up" : "down";
  const stockGroup = isStockGroup(rowSource?.group);
  const symbol = escapeHtml(stockGroup ? row.name || row.symbol || "--" : row.symbol || "--");
  const name = escapeHtml(stockGroup ? row.symbol || "" : row.name || "");
  const metric = escapeHtml(primaryMetric(row));
  const metricHint = escapeHtml(row.price ? row.turnover || row.metricLabel || "" : row.note || "");
  const changeLabel = escapeHtml(row.change || "--");
  const insight = renderInsight(row, rowSource, rank || row.rank || 999);
  const totalSourceTitles = Array.isArray(row.totalSourceTitles) ? row.totalSourceTitles : [];
  const totalSourceLabels = Array.isArray(row.totalSourceLabels) ? row.totalSourceLabels : [];
  const sourceTrace = totalSourceTitles.length
    ? `<em class="total-source-trace" title="来源：${escapeHtml(totalSourceTitles.join(" / "))}">${escapeHtml(totalSourceLabels.join(" / ") || `${totalSourceTitles.length} 个榜单`)}</em>`
    : "";
  const liveKey = row.totalAssetKey
    || `${rowSource?.id || rowSource?.title || "source"}:${row.contractAddress || row.address || row.symbol || row.name || rank}`;

  return `
    <a class="rank-row rank-row-link" data-live-key="row:${escapeHtml(liveKey)}" href="${escapeHtml(rowTargetUrl(row, rowSource) || "#")}" target="_blank" rel="noreferrer" title="打开 ${symbol} 交易/行情页面">
      <div class="rank-badge">${escapeHtml(row.rank ?? "")}</div>
      <div class="asset-cell">
        ${renderAssetIcon(row, rowSource)}
        <div class="asset-line">
          <div class="asset-symbol-line">
            <strong title="${symbol}">${symbol}</strong>
            ${renderExchangeAiNarrative(row, rowSource)}
            ${renderChainBadge(row, rowSource)}
          </div>
          <span title="${name}${totalSourceTitles.length ? ` · 来源：${escapeHtml(totalSourceTitles.join(" / "))}` : ""}${insight ? ` · ${insight.replace(/<[^>]+>/g, "")}` : ""}">${name}${sourceTrace}${insight}</span>
        </div>
      </div>
      <div class="price-cell">
        <b title="${metric}">${metric}</b>
        ${metricHint ? `<span title="${metricHint}">${metricHint}</span>` : ""}
      </div>
      <div class="metric-cell">
        <b class="${direction}">${changeLabel}</b>
      </div>
      <div class="heat-cell" aria-label="热度 ${row.heat || 0}">
        <i style="width: ${Math.max(4, Math.min(100, row.heat || 0))}%"></i>
        <em>${row.heat || "--"}</em>
      </div>
      <p class="note-cell">${escapeHtml(row.note || "")}</p>
    </a>
  `;
}

async function loadMarketData(options = {}) {
  const now = Date.now();
  if (!options.refresh && state.sources.length && now - state.lastRequestedAt < 55_000) return;
  if (state.isLoading) return;
  state.isLoading = true;
  state.lastRequestedAt = now;

  let hasExistingData = state.sources.length > 0;
  if (!hasExistingData) {
    hasExistingData = hydrateMarketCache();
  }

  try {
    const response = await fetch(`/api/market-hot${options.refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const nextSources = Array.isArray(payload.sources) ? payload.sources : [];
    if (!nextSources.length) throw new Error("Empty market data payload");
    const preferredWalletSource = state.binanceWalletPeriod === "24h"
      ? null
      : currentBinanceWalletSource(state.binanceWalletPeriod);
    const walletIndex = nextSources.findIndex((source) => String(source?.id || "") === "binance-wallet-hot");
    if (preferredWalletSource && walletIndex >= 0) nextSources[walletIndex] = preferredWalletSource;
    state.sources = nextSources;
    state.smartPriority = payload.smartPriority && typeof payload.smartPriority === "object"
      ? payload.smartPriority
      : state.smartPriority;
    writeCachedPayload(MARKET_CACHE_KEY, payload);
    setStatus("真实数据", "ok");
    renderTicker();
    renderBoards();
    const receivedWallet = state.sources.find((source) => String(source?.id || "") === "binance-wallet-hot");
    if (preferredWalletSource) {
      void loadBinanceWalletPeriod(state.binanceWalletPeriod, { refresh: true });
    } else if (!receivedWallet || normalizeBinanceWalletPeriod(receivedWallet.period) !== state.binanceWalletPeriod) {
      void loadBinanceWalletPeriod(state.binanceWalletPeriod);
    }
  } catch (error) {
    if (hasExistingData) {
      setStatus("保留上次数据", "ok");
      console.warn("Market data refresh failed; keeping previous data.", error);
    } else {
      setStatus("服务未连接", "error");
      boardsEl.innerHTML = `
        <div class="loading-panel error-panel">
          <b>需要通过本地服务打开页面</b>
          <span>请访问 http://127.0.0.1:8765/，否则浏览器无法读取富途、同花顺和律动页面数据。</span>
        </div>
      `;
    }
  } finally {
    state.isLoading = false;
    void loadGmgnHotSource();
  }
}

function updateClock() {
  const now = new Date();
  clockEl.textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(now);
}

searchInput.addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  state.totalPage = 1;
  renderBoards();
});

sortSelect.addEventListener("change", (event) => {
  state.sort = event.target.value;
  state.totalPage = 1;
  saveLocalPreference(MARKET_PRIORITY_VIEW_KEY, state.sort === "priority" ? "smart" : "platform");
  renderBoards();
});

filterButtons.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.filter = button.dataset.filter;
  state.totalPage = 1;
  filterButtons.querySelectorAll("button").forEach((item) => {
    item.classList.toggle("active", item === button);
    item.setAttribute("aria-pressed", item === button ? "true" : "false");
  });
  renderBoards();
});

priorityPeriodSelect?.addEventListener("change", (event) => {
  state.priorityPeriod = normalizePriorityPeriod(event.target.value);
  state.totalPage = 1;
  saveLocalPreference(MARKET_PRIORITY_PERIOD_KEY, state.priorityPeriod);
  renderBoards();
});

window.addEventListener("xingyun:rank-ai-toggle", () => {
  renderBoards();
});

boardsEl.addEventListener("pointerover", (event) => {
  const tool = event.target.closest?.('.gmgn-trench-tool.is-x[data-status-id]');
  if (tool) queueGmgnTrenchXPost(tool);
});

boardsEl.addEventListener("focusin", (event) => {
  const tool = event.target.closest?.('.gmgn-trench-tool.is-x[data-status-id]');
  if (tool) queueGmgnTrenchXPost(tool);
});

boardsEl.addEventListener("click", (event) => {
  const contractButton = event.target.closest?.(".gmgn-trench-ca-copy[data-contract]");
  if (contractButton) {
    event.preventDefault();
    event.stopPropagation();
    void copyGmgnTrenchContract(contractButton);
    return;
  }
  const button = event.target.closest('button[data-role="total-page"]');
  if (!button || button.disabled) return;
  const page = Number.parseInt(button.dataset.page || "1", 10);
  if (!Number.isFinite(page) || page < 1 || page === state.totalPage) return;
  state.totalPage = page;
  renderBoards();
  requestAnimationFrame(() => {
    boardsEl.querySelector(".board-card.is-total-board")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

boardsEl.addEventListener("change", (event) => {
  const walletSelect = event.target.closest('select[data-role="binance-wallet-period"]');
  if (walletSelect) {
    void loadBinanceWalletPeriod(walletSelect.value);
    return;
  }
  const avePeriodSelect = event.target.closest('select[data-role="ave-period"]');
  if (avePeriodSelect) {
    state.avePeriod = normalizeAvePeriod(avePeriodSelect.value);
    saveLocalPreference(AVE_PERIOD_KEY, state.avePeriod);
    renderBoards();
    return;
  }
  const aveChainSelect = event.target.closest('select[data-role="ave-chain"]');
  if (aveChainSelect) {
    state.aveChain = normalizeAveChain(aveChainSelect.value);
    saveLocalPreference(AVE_CHAIN_KEY, state.aveChain);
    renderBoards();
    return;
  }
  const gmgnPeriodSelect = event.target.closest('select[data-role="gmgn-period"]');
  if (gmgnPeriodSelect) {
    state.gmgnPeriod = normalizeGmgnPeriod(gmgnPeriodSelect.value);
    saveLocalPreference(GMGN_PERIOD_KEY, state.gmgnPeriod);
    renderBoards();
    return;
  }
  const gmgnChainSelect = event.target.closest('select[data-role="gmgn-chain"]');
  if (gmgnChainSelect) {
    state.gmgnChain = normalizeGmgnChain(gmgnChainSelect.value);
    saveLocalPreference(GMGN_CHAIN_KEY, state.gmgnChain);
    renderBoards();
  }
});

updateClock();
setInterval(updateClock, 1000);
hydrateMarketCache();
loadMarketData();
setInterval(() => {
  if (document.visibilityState === "visible") loadMarketData();
}, 60_000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadMarketData();
});
