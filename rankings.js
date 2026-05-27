const rankingMode = document.body.dataset.rankingMode === "turnover" ? "turnover" : "gainers";
const RANKING_API = rankingMode === "gainers" ? "/api/gainers-rankings" : "/api/turnover-rankings";
const MARKET_CACHE_KEY = "xingyunshe:market-hot:payload:v2";
const RANKING_CACHE_KEY = `xingyunshe:ranking:${rankingMode}:payload:v4`;

const state = {
  filter: "all",
  query: "",
  rows: [],
  sources: [],
  isLoading: false,
  lastRequestedAt: 0
};

const nodes = {
  grid: document.querySelector("#rankingGrid"),
  search: document.querySelector("#rankingSearch"),
  filter: document.querySelector("#rankingFilter"),
  status: document.querySelector("#rankingStatus"),
  clock: document.querySelector("#clock"),
  refresh: document.querySelector("#refreshRanking"),
  total: document.querySelector("#rankingTotal"),
  leader: document.querySelector("#rankingLeader"),
  crypto: document.querySelector("#rankingCrypto"),
  stock: document.querySelector("#rankingStock")
};

const groupLabels = {
  crypto: "币圈",
  aicoin: "AIcoin",
  hk: "港股",
  us: "美股",
  cn: "A股"
};

const modeConfig = {
  gainers: {
    title: "涨幅榜",
    combinedTitle: "综合涨幅榜",
    cryptoTitle: "币圈涨幅榜",
    stockTitle: "股票涨幅榜",
    metricHead: "价格/热度",
    valueHead: "涨幅",
    empty: "暂无可用涨幅数据"
  },
  turnover: {
    title: "成交额榜",
    combinedTitle: "综合成交额榜",
    cryptoTitle: "币圈成交额榜",
    stockTitle: "股票成交额榜",
    metricHead: "价格/涨幅",
    valueHead: "成交额",
    empty: "暂无可用成交额数据"
  }
}[rankingMode];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[char]);
}

function parseSignedNumber(value) {
  return Number.parseFloat(String(value || "0").replace("%", "").replace("+", "").replace(",", "")) || 0;
}

function amountFromText(value) {
  const text = String(value || "").replace(/,/g, "").trim();
  const match = text.match(/[-+]?\d+(?:\.\d+)?\s*([亿万KMBW])?/i);
  if (!match) return 0;
  let amount = Number.parseFloat(match[0]) || 0;
  const unit = String(match[1] || "").toUpperCase();
  if (unit === "亿" || unit === "B") amount *= 100_000_000;
  else if (unit === "万" || unit === "W") amount *= 10_000;
  else if (unit === "M") amount *= 1_000_000;
  else if (unit === "K") amount *= 1_000;
  return amount;
}

function rowAmount(row) {
  return Number(row.amount) || amountFromText(row.turnover || row.metricLabel || row.note);
}

function formatAmount(value) {
  const amount = Number(value) || 0;
  if (amount >= 100_000_000) return `${(amount / 100_000_000).toFixed(amount >= 1_000_000_000 ? 1 : 2)}亿`;
  if (amount >= 10_000) return `${(amount / 10_000).toFixed(amount >= 1_000_000 ? 1 : 2)}万`;
  if (amount >= 1_000) return `${(amount / 1_000).toFixed(1)}K`;
  return amount ? amount.toFixed(0) : "--";
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
  if (!nodes.status) return;
  nodes.status.textContent = text;
  nodes.status.dataset.mode = mode;
}

function readCache() {
  const keys = rankingMode === "turnover" ? [RANKING_CACHE_KEY, MARKET_CACHE_KEY] : [RANKING_CACHE_KEY];
  for (const key of keys) {
    try {
      const payload = JSON.parse(localStorage.getItem(key) || "null");
      if (payload?.sources?.length) return payload;
    } catch {
      // Cache is optional.
    }
  }
  return null;
}

function writeCache(payload) {
  try {
    localStorage.setItem(RANKING_CACHE_KEY, JSON.stringify(payload));
    if (rankingMode === "turnover") localStorage.setItem(MARKET_CACHE_KEY, JSON.stringify(payload));
  } catch {
    // Local cache only improves perceived speed.
  }
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
  if (id.includes("okx-dex")) return "https://web3.okx.com/zh-hans/token?ct=30&pt=4";

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

  if (group === "us") return symbol ? `https://www.futunn.com/quote/us/${symbol}` : "";

  if (group === "cn") {
    const code = String(row.symbol || "").replace(/\D/g, "").slice(-6);
    return code ? `https://stockpage.10jqka.com.cn/${code}/` : "";
  }

  return "";
}

function iconSources(row) {
  return [
    row.icon,
    ...(Array.isArray(row.icons) ? row.icons : []),
    ...(Array.isArray(row.iconCandidates) ? row.iconCandidates : [])
  ]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .filter((item, index, list) => list.indexOf(item) === index);
}

function iconFallbackText(row, source) {
  const group = source?.group || "";
  const rawName = String(row.name || row.symbol || source?.sourceLabel || "").trim();
  const symbol = String(row.symbol || "").trim();
  const text = group === "hk" || group === "cn" ? rawName || symbol : symbol || rawName;
  const chars = Array.from(text.replace(/[-_/.\s]+/g, ""));
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

window.advanceAssetIcon = window.advanceAssetIcon || function advanceAssetIcon(image) {
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
  const sources = iconSources(row);
  const src = sources[0] || "";
  const label = escapeHtml(iconFallbackText(row, source));
  const alt = escapeHtml(row.symbol || row.name || "");
  const dataset = escapeHtml(JSON.stringify(sources));
  const image = src
    ? `<img src="${escapeHtml(src)}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="window.advanceAssetIcon(this)">`
    : "";
  return `<span class="asset-icon ${assetIconClass(source)} ${src ? "" : "is-fallback"}" data-icons="${dataset}" data-icon-index="0">${image}<em>${label}</em></span>`;
}

function flattenRows(sources) {
  return sources.flatMap((source) =>
    (source.rows || []).map((row) => {
      const change = parseSignedNumber(row.change);
      const amount = rowAmount(row);
      return {
        ...row,
        source,
        sourceId: source.id,
        sourceTitle: source.title,
        sourceLabel: source.sourceLabel,
        group: source.group,
        changeValue: change,
        amountValue: amount,
        targetUrl: rowTargetUrl(row, source)
      };
    })
  );
}

function hydrateFromPayload(payload) {
  const sources = Array.isArray(payload?.sources) ? payload.sources : [];
  if (!sources.length) return false;
  state.sources = sources;
  state.rows = flattenRows(sources);
  setStatus(`缓存数据 · ${formatTime(payload.updatedAt || payload._cache?.updatedAt)}`, "ok");
  render();
  return true;
}

function isStockGroup(group) {
  return group === "hk" || group === "us" || group === "cn";
}

function displayAssetName(row) {
  return isStockGroup(row?.group) ? row.name || row.symbol || "--" : row.symbol || row.name || "--";
}

function rowMatchesFilter(row) {
  if (state.filter === "all") return true;
  if (state.filter === "crypto") return row.group === "crypto" || row.group === "aicoin";
  if (state.filter === "stock") return isStockGroup(row.group);
  return row.group === state.filter;
}

function rowMatchesQuery(row) {
  if (!state.query) return true;
  const target = [row.symbol, row.name, row.note, row.sourceTitle, row.sourceLabel, groupLabels[row.group], ...(row.tags || [])]
    .join(" ")
    .toLowerCase();
  return target.includes(state.query.toLowerCase());
}

function rankedRows(scope = "all") {
  let rows = state.rows.filter(rowMatchesQuery);
  if (scope === "crypto") rows = rows.filter((row) => row.group === "crypto" || row.group === "aicoin");
  else if (scope === "stock") rows = rows.filter((row) => isStockGroup(row.group));
  else if (scope !== "all") rows = rows.filter((row) => row.group === scope);
  rows = rows.filter(rowMatchesFilter);

  if (rankingMode === "gainers") {
    return rows
      .filter((row) => Number.isFinite(row.changeValue) && row.changeValue > 0)
      .sort((a, b) => b.changeValue - a.changeValue || b.amountValue - a.amountValue);
  }

  return rows
    .filter((row) => row.amountValue > 0)
    .sort((a, b) => b.amountValue - a.amountValue || b.changeValue - a.changeValue);
}

function sourceMatchesFilter(source) {
  if (state.filter === "all") return true;
  if (state.filter === "crypto") return source.group === "crypto" || source.group === "aicoin";
  if (state.filter === "stock") return isStockGroup(source.group);
  return source.group === state.filter;
}

function sortedRankingRows(rows) {
  const matchingRows = rows.filter(rowMatchesQuery);
  if (rankingMode === "gainers") {
    return matchingRows
      .filter((row) => Number.isFinite(row.changeValue) && row.changeValue > 0)
      .sort((a, b) => b.changeValue - a.changeValue || b.amountValue - a.amountValue);
  }
  return matchingRows
    .filter((row) => row.amountValue > 0)
    .sort((a, b) => b.amountValue - a.amountValue || b.changeValue - a.changeValue);
}

function sourceSubtitle(source) {
  const name = source.sourceName || source.subtitle || "";
  const group = groupLabels[source.group] || source.group || "市场";
  const sortLabel = rankingMode === "turnover" ? "独立成交额排序" : "独立涨幅排序";
  if (/dex|链上|onchain/i.test(`${source.id || ""} ${source.title || ""} ${name}`)) {
    return `${name || "链上数据"} · ${sortLabel}`;
  }
  return `${name || group} · ${sortLabel}`;
}

function sourceBoardTitle(source) {
  const fallback = `${source.sourceLabel || groupLabels[source.group] || "市场"}${modeConfig.title}`;
  const title = source.title || fallback;
  if (rankingMode !== "turnover") return title;
  return String(title)
    .replace(/热门币种/g, "成交额榜")
    .replace(/热门榜/g, "成交额榜")
    .replace(/热榜/g, "成交额榜")
    .replace(/热门/g, "成交额榜")
    .replace(/涨幅榜/g, "成交额榜");
}

function buildSourceBoards() {
  return state.sources
    .filter(sourceMatchesFilter)
    .map((source, index) => {
      const rows = sortedRankingRows(flattenRows([source])).slice(0, 10);
      return {
        id: source.id || `source-${index}`,
        title: sourceBoardTitle(source),
        subtitle: sourceSubtitle(source),
        sourceLabel: source.sourceLabel,
        rows,
        accent: source.accent || "#f6bb48"
      };
    });
}

function buildBoards() {
  return buildSourceBoards();
}

function rowPrimaryMetric(row) {
  if (rankingMode === "turnover") {
    return row.price || row.change || row.metricLabel || "--";
  }
  return row.price || row.turnover || row.metricLabel || row.note || "--";
}

function rowSecondaryMetric(row) {
  if (rankingMode === "turnover") return row.change || row.sourceLabel || groupLabels[row.group] || "";
  return row.turnover || row.metricLabel || row.sourceTitle || "";
}

function rowValue(row) {
  if (rankingMode === "turnover") return row.turnover || formatAmount(row.amountValue);
  return row.change || `${row.changeValue.toFixed(2)}%`;
}

function rowValueClass(row) {
  if (rankingMode === "turnover") return row.changeValue >= 0 ? "up" : "down";
  return row.changeValue >= 0 ? "up" : "down";
}

function renderInsight(row, source, rank) {
  const insight = window.XingyunInsights?.buildRowInsight(row, { source, rank, mode: rankingMode });
  if (!insight) return "";
  const tone = insight.tone === "is-hot" ? " is-hot" : "";
  return `<em class="row-insight-text${tone}" title="${escapeHtml(insight.detail)}">${escapeHtml(insight.detail)}</em>`;
}

function renderLeaderAnalysis(board) {
  if (rankingMode !== "gainers" || !board?.rows?.length) return "";
  const leader = board.rows[0];
  const insight = window.XingyunInsights?.buildRowInsight(leader, { source: leader.source || board, rank: 1, mode: rankingMode });
  if (!insight?.detail) return "";
  const asset = displayAssetName(leader);
  const theme = insight.theme && insight.theme !== insight.detail ? insight.theme : "";
  const tone = insight.tone === "is-hot" ? " is-hot" : "";
  return `
    <div class="leader-analysis${tone}" title="${escapeHtml(insight.detail)}">
      <span>榜首分析</span>
      <b>${escapeHtml(asset)}</b>
      <em>${escapeHtml(insight.detail)}</em>
      ${theme ? `<small>${escapeHtml(theme)}</small>` : ""}
    </div>
  `;
}

function renderRow(row, index, board) {
  const stockGroup = isStockGroup(row.group);
  const symbol = escapeHtml(stockGroup ? row.name || row.symbol || "--" : row.symbol || row.name || "--");
  const name = escapeHtml(stockGroup ? row.symbol || row.sourceTitle || "" : row.name || row.sourceTitle || "");
  const metric = escapeHtml(rowPrimaryMetric(row));
  const metricHint = escapeHtml(rowSecondaryMetric(row));
  const value = escapeHtml(rowValue(row));
  const url = escapeHtml(row.targetUrl || "#");
  const sourceTag = escapeHtml(row.sourceLabel || groupLabels[row.group] || "MK");
  const insight = renderInsight(row, row.source || board, index + 1);

  return `
    <a class="rank-row rank-row-link ranking-row" href="${url}" target="_blank" rel="noreferrer" title="打开 ${symbol} 交易/行情页面">
      <div class="rank-badge">${index + 1}</div>
      <div class="asset-cell">
        ${renderAssetIcon(row, row.source)}
        <div class="asset-line">
          <strong title="${symbol}">${symbol}<small>${sourceTag}</small></strong>
          <span title="${name}${insight ? ` · ${insight.replace(/<[^>]+>/g, "")}` : ""}">${name}${insight}</span>
        </div>
      </div>
      <div class="price-cell">
        <b title="${metric}">${metric}</b>
        ${metricHint ? `<span title="${metricHint}">${metricHint}</span>` : ""}
      </div>
      <div class="metric-cell">
        <b class="${rowValueClass(row)}" title="${value}">${value}</b>
        <span>${escapeHtml(groupLabels[row.group] || row.sourceTitle || "")}</span>
      </div>
    </a>
  `;
}

function renderBoard(board, index) {
  const rows = board.rows.length
    ? board.rows.map((row, rowIndex) => renderRow(row, rowIndex, board)).join("")
    : `<div class="empty-state"><b>${modeConfig.empty}</b><span>可以切换市场筛选或刷新榜单。</span></div>`;

  return `
    <article class="board-card ranking-board" style="--accent: ${board.accent}; --delay: ${index * 45}ms">
      <header class="board-head">
        <div>
          <p>${modeConfig.title}</p>
          <h3>${escapeHtml(board.title)}</h3>
        </div>
        <strong>${board.rows.length || "--"}</strong>
      </header>
      ${renderLeaderAnalysis(board)}
      <div class="rows">
        <div class="board-table-head">
          <span>#</span><span>名称</span><span>${modeConfig.metricHead}</span><span>${modeConfig.valueHead}</span>
        </div>
        ${rows}
      </div>
    </article>
  `;
}

function renderMetrics() {
  const rows = rankedRows("all");
  const leader = rows[0];
  if (nodes.total) nodes.total.textContent = String(rows.length);
  if (nodes.leader) nodes.leader.textContent = leader ? displayAssetName(leader) : "--";
  if (nodes.crypto) nodes.crypto.textContent = String(rankedRows("crypto").length);
  if (nodes.stock) nodes.stock.textContent = String(rankedRows("stock").length);
}

function render() {
  renderMetrics();
  const boards = buildBoards();
  if (!boards.some((board) => board.rows.length)) {
    nodes.grid.innerHTML = `<div class="loading-panel">${modeConfig.empty}</div>`;
    return;
  }
  nodes.grid.innerHTML = boards.map(renderBoard).join("");
  requestAiInsights(boards);
}

function requestAiInsights(sources) {
  window.XingyunAiInsights?.requestForSources(sources, {
    mode: rankingMode,
    onUpdate: render
  });
}

async function loadRankingData(options = {}) {
  const now = Date.now();
  if (!options.refresh && state.rows.length && now - state.lastRequestedAt < 55_000) return;
  if (state.isLoading) return;
  state.isLoading = true;
  state.lastRequestedAt = now;

  let hasCache = state.rows.length > 0;
  if (!hasCache) {
    hasCache = hydrateFromPayload(readCache());
  }

  try {
    const response = await fetch(`${RANKING_API}${options.refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!Array.isArray(payload.sources) || !payload.sources.length) throw new Error("Empty ranking data payload");
    writeCache(payload);
    hydrateFromPayload(payload);
    setStatus(`真实数据 · ${formatTime(payload.updatedAt || payload._cache?.updatedAt)}`, "ok");
  } catch (error) {
    if (hasCache) {
      setStatus("保留缓存数据", "ok");
      console.warn("Ranking data refresh failed; keeping cache.", error);
    } else {
      setStatus("服务未连接", "error");
      nodes.grid.innerHTML = `
        <div class="loading-panel error-panel">
          <b>需要通过本地服务打开页面</b>
          <span>请访问 http://127.0.0.1:8765/，否则无法读取跨市场榜单数据。</span>
        </div>
      `;
    }
  } finally {
    state.isLoading = false;
  }
}

function updateClock() {
  if (!nodes.clock) return;
  nodes.clock.textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(new Date());
}

nodes.search?.addEventListener("input", () => {
  state.query = nodes.search.value.trim();
  render();
});

nodes.filter?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  state.filter = button.dataset.filter;
  nodes.filter.querySelectorAll("button").forEach((item) => {
    item.classList.toggle("active", item === button);
    item.setAttribute("aria-pressed", item === button ? "true" : "false");
  });
  render();
});

nodes.refresh?.addEventListener("click", () => loadRankingData({ refresh: true }));

updateClock();
setInterval(updateClock, 1000);
loadRankingData();
setInterval(() => {
  if (document.visibilityState === "visible") loadRankingData();
}, 60_000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadRankingData();
});
