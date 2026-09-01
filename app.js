const BINANCE_WALLET_PERIOD_KEY = "xingyunshe:binance-wallet-hot:period:v2";
const BINANCE_WALLET_PERIODS = new Set(["5m", "1h", "4h", "24h"]);
const MARKET_CACHE_KEY = "xingyunshe:market-hot:payload:v8";
const MARKET_PRIORITY_VIEW_KEY = "xingyunshe:market-hot:priority-view:v1";
const MARKET_PRIORITY_PERIOD_KEY = "xingyunshe:market-hot:priority-period:v1";
const MARKET_PRIORITY_PERIODS = new Set(["1h", "6h", "24h"]);

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
  priorityPeriod: normalizePriorityPeriod(readLocalPreference(MARKET_PRIORITY_PERIOD_KEY, "24h")),
  smartPriority: {}
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

const groupLabels = {
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

function visibleSources() {
  const priorityScores = state.sort === "priority" ? activePriorityScores() : null;
  return state.sources
    .filter(matchesFilter)
    .map((source) => ({
      ...source,
      rows: sortRows((source.rows || []).filter(matchesQuery), priorityScores)
    }));
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
    source.rows.map((row) => ({
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

  summaryEl.innerHTML = cards
    .map(
      (card) => `
        <article class="summary-card">
          <p>${card.label}</p>
          <strong>${card.value}</strong>
          <span>${card.meta}</span>
        </article>
      `
    )
    .join("");
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

  tickerRail.innerHTML = `<div class="ticker-track">${items}${items}</div>`;
}


function renderBoards() {
  syncPriorityControls();
  const sources = visibleSources();
  renderSummary(sources);

  boardsEl.innerHTML = sources
    .map(
      (source, index) => `
        <article class="board-card ${source.status === "unavailable" ? "is-muted" : ""}" style="--accent: ${source.accent}; --delay: ${index * 55}ms">
          <header class="board-head">
            <div class="board-head-copy">
              <p>${groupLabels[source.group] || source.group}</p>
              <h3>${source.title}</h3>
            </div>
            ${renderBoardHeadActions(source)}
          </header>
          <div class="rows">
            ${
              source.rows.length
                ? `<div class="board-table-head"><span>#</span><span>名称</span><span>价格/热度</span><span>涨跌幅</span></div>${source.rows.map((row, rowIndex) => renderRow(row, source, rowIndex + 1)).join("")}`
                : renderEmpty(source)
            }
          </div>
        </article>
      `
    )
    .join("");
  requestAiInsights(sources);
}

function renderBoardHeadActions(source) {
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
  return `<em class="row-insight-text${tone}" title="${escapeHtml(insight.detail)}">${escapeHtml(insight.detail)}</em>`;
}

function renderChainBadge(row, source) {
  const sourceId = String(source?.id || source?.sourceName || source?.sourceLabel || "").toLowerCase();
  if (!sourceId.includes("okx-dex") && !sourceId.includes("ave") && !sourceId.includes("binance-wallet")) return "";
  const label = String(row.chainLabel || row.chain || "").trim();
  if (!label) return "";
  return `<em class="chain-badge" title="${escapeHtml(row.chain || label)}">${escapeHtml(label.toUpperCase())}</em>`;
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

function renderRow(row, source, rank) {
  const change = parseSignedNumber(row.change);
  const direction = change >= 0 ? "up" : "down";
  const stockGroup = isStockGroup(source?.group);
  const symbol = escapeHtml(stockGroup ? row.name || row.symbol || "--" : row.symbol || "--");
  const name = escapeHtml(stockGroup ? row.symbol || "" : row.name || "");
  const metric = escapeHtml(primaryMetric(row));
  const metricHint = escapeHtml(row.price ? row.turnover || row.metricLabel || "" : row.note || "");
  const changeLabel = escapeHtml(row.change || "--");
  const insight = renderInsight(row, source, rank || row.rank || 999);

  return `
    <a class="rank-row rank-row-link" href="${escapeHtml(rowTargetUrl(row, source) || "#")}" target="_blank" rel="noreferrer" title="打开 ${symbol} 交易/行情页面">
      <div class="rank-badge">${escapeHtml(row.rank ?? "")}</div>
      <div class="asset-cell">
        ${renderAssetIcon(row, source)}
        <div class="asset-line">
          <strong title="${symbol}">${symbol}${renderChainBadge(row, source)}</strong>
          <span title="${name}${insight ? ` · ${insight.replace(/<[^>]+>/g, "")}` : ""}">${name}${insight}</span>
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
  renderBoards();
});

sortSelect.addEventListener("change", (event) => {
  state.sort = event.target.value;
  saveLocalPreference(MARKET_PRIORITY_VIEW_KEY, state.sort === "priority" ? "smart" : "platform");
  renderBoards();
});

filterButtons.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.filter = button.dataset.filter;
  filterButtons.querySelectorAll("button").forEach((item) => {
    item.classList.toggle("active", item === button);
    item.setAttribute("aria-pressed", item === button ? "true" : "false");
  });
  renderBoards();
});

priorityPeriodSelect?.addEventListener("change", (event) => {
  state.priorityPeriod = normalizePriorityPeriod(event.target.value);
  saveLocalPreference(MARKET_PRIORITY_PERIOD_KEY, state.priorityPeriod);
  renderBoards();
});

boardsEl.addEventListener("change", (event) => {
  const select = event.target.closest('select[data-role="binance-wallet-period"]');
  if (!select) return;
  void loadBinanceWalletPeriod(select.value);
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
