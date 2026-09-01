const newboardState = {
  coinSections: [],
  stockSections: [],
  filter: "coin",
  query: "",
  isLoading: false
};
const NEWBOARD_CACHE_KEY = "xingyunshe:newboards:payload:v2";

const newboardGrid = document.querySelector("#newboardGrid");
const newboardSearch = document.querySelector("#newboardSearch");
const newboardFilter = document.querySelector("#newboardFilter");
const newboardStatus = document.querySelector("#newboardStatus");
const refreshNewboards = document.querySelector("#refreshNewboards");
const clockNode = document.querySelector("#clock");
const newboardTotal = document.querySelector("#newboardTotal");
const newboardCoin = document.querySelector("#newboardCoin");
const newboardStock = document.querySelector("#newboardStock");
const newboardToday = document.querySelector("#newboardToday");

const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
});

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
    // Local cache is only for instant first paint.
  }
}

function hydrateNewboardCache() {
  const cached = readCachedPayload(NEWBOARD_CACHE_KEY);
  const coinSections = Array.isArray(cached?.coinSections) ? cached.coinSections : [];
  const stockSections = Array.isArray(cached?.stockSections) ? cached.stockSections : [];
  if (!coinSections.length && !stockSections.length) return false;
  newboardState.coinSections = coinSections;
  newboardState.stockSections = stockSections;
  setStatus(`缓存数据 · ${formatDateTime(cached.updatedAt)}`, "ok");
  renderBoards();
  return true;
}

function setStatus(text, mode = "normal") {
  if (!newboardStatus) return;
  newboardStatus.textContent = text;
  newboardStatus.dataset.mode = mode;
}

function toDay(value) {
  const date = new Date(value || 0);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

function isToday(value) {
  if (!value) return false;
  return toDay(value) === toDay(Date.now());
}

function formatDateTime(value) {
  if (!value) return "--";
  return dateTimeFormatter.format(new Date(value));
}

function formatDate(value) {
  if (!value) return "--";
  return dateFormatter.format(new Date(value)).replace(/\//g, "/");
}

function parseSignedNumber(value) {
  return Number.parseFloat(String(value || "0").replace("%", "").replace("+", "")) || 0;
}

function valueClass(value) {
  const numeric = parseSignedNumber(value);
  if (numeric > 0) return "up";
  if (numeric < 0) return "down";
  return "";
}

function amountFromText(value) {
  const text = String(value || "").replace(/,/g, "").trim();
  const match = text.match(/[-+]?\d+(?:\.\d+)?/);
  if (!match) return 0;
  let amount = Number.parseFloat(match[0]) || 0;
  if (/[亿B]/i.test(text)) amount *= 100_000_000;
  else if (/[万]/.test(text)) amount *= 10_000;
  else if (/M/i.test(text)) amount *= 1_000_000;
  else if (/K/i.test(text)) amount *= 1_000;
  return amount;
}

function rowHeatInfo(row, rank = 99) {
  const amount = Number(row.amount) || amountFromText(row.turnover || row.metric || row.note);
  const heat = Number(row.heat) || 0;
  const change = Math.abs(parseSignedNumber(row.change));
  const isIpo = row.group === "ipo" || row.group === "cn" || /ipo|nasdaq|nyse|上市|招股|申购|upcoming|priced/i.test([
    row.group,
    row.status,
    row.source,
    row.sourceLabel,
    row.metric,
    ...(row.tags || [])
  ].join(" "));
  const isContractListing = row.group === "crypto" || /perpetual|usdt|will list/i.test([
    row.group,
    row.status,
    row.source,
    row.sourceLabel,
    row.metric,
    row.title,
    row.symbol,
    ...(row.tags || [])
  ].join(" "));
  const text = [
    row.title,
    row.symbol,
    row.asset,
    row.status,
    row.metric,
    row.note,
    ...(row.tags || [])
  ].join(" ").toLowerCase();
  let score = 0;
  if (rank <= 3) score += 8;
  else if (rank <= 5) score += 4;
  if (amount >= 1_000_000_000) score += 46;
  else if (amount >= 300_000_000) score += 34;
  else if (amount >= 100_000_000) score += 24;
  else if (amount >= 30_000_000) score += 14;
  else if (amount >= 5_000_000) score += 6;
  if (heat >= 90) score += 30;
  else if (heat >= 80) score += 20;
  else if (heat >= 70) score += 10;
  if (change >= 40) score += 18;
  else if (change >= 25) score += 10;
  else if (change >= 15) score += 5;
  if (/高热|热门|热度|讨论|关注|超购|融资|首日|ipo|新币/i.test(text)) score += 12;
  if (isContractListing) {
    if (/binance|okx|bitget/i.test(text)) score += 18;
    if (/perpetual|will list|usdt/i.test(text)) score += 18;
    if (/ai|meme|memecoin|defi|rwa|gamefi|launchpool/i.test(text)) score += 18;
  }
  if (isIpo) {
    if (amount >= 1_000_000_000) score += 40;
    else if (amount >= 300_000_000) score += 32;
    else if (amount >= 100_000_000) score += 22;
    else if (amount >= 50_000_000) score += 14;
    else if (amount >= 20_000_000) score += 8;
    if (/ai|人工智能|智能|芯片|半导体|robot|机器人|digital|infrastructure|crypto|bitcoin|区块链|web3|cerebras|blackstone/i.test(text)) score += 28;
    if (/global|select|nasdaq|nyse|首发|申购|招股|upcoming|priced/i.test(text)) score += 10;
  }
  const threshold = isIpo ? 112 : isContractListing ? 60 : 78;
  return {
    high: score >= threshold,
    score: Math.min(100, Math.round(score)),
    amount
  };
}

function iconSources(row) {
  return [
    row.icon,
    ...(Array.isArray(row.icons) ? row.icons : []),
    ...(Array.isArray(row.iconCandidates) ? row.iconCandidates : [])
  ]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .filter((item, index, arr) => arr.indexOf(item) === index);
}

function iconFallbackText(row, board) {
  const text = String(row.asset || row.symbol || row.name || board?.sourceLabel || "NX").replace(/[-_/.\s]+/g, "");
  const chars = Array.from(text);
  const maxLength = board?.kind === "stock" ? 1 : 2;
  return (chars.slice(0, maxLength).join("") || "NX").toUpperCase();
}

function assetIconClass(row, board) {
  if (board?.kind === "stock") {
    if (row.group === "hk") return "is-stock is-hk";
    if (row.group === "us") return "is-stock is-us";
    return "is-stock is-cn";
  }
  return "is-coin";
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

function renderAssetIcon(row, board) {
  const sources = iconSources(row);
  const src = sources[0] || "";
  const label = escapeHtml(iconFallbackText(row, board));
  const alt = escapeHtml(row.symbol || row.name || "");
  const dataset = escapeHtml(JSON.stringify(sources));
  const image = src
    ? `<img src="${escapeHtml(src)}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="window.advanceAssetIcon(this)">`
    : "";
  return `<span class="asset-icon ${assetIconClass(row, board)} ${src ? "" : "is-fallback"}" data-icons="${dataset}" data-icon-index="0">${image}<em>${label}</em></span>`;
}

function rowsFromSections(sections) {
  return sections.flatMap((section) =>
    (section.rows || []).map((row) => ({
      ...row,
      sectionId: section.id,
      sectionAccent: section.accent,
      sectionTitle: section.title,
      sectionSource: section.sourceName,
      sourceLabel: row.sourceLabel || section.sourceLabel,
      source: row.source || section.sourceName || section.title
    }))
  );
}

function allRows() {
  return [...rowsFromSections(newboardState.coinSections), ...rowsFromSections(newboardState.stockSections)];
}

function rowMatchesQuery(row) {
  if (!newboardState.query) return true;
  const query = newboardState.query.toLowerCase();
  const target = [
    row.title,
    row.name,
    row.symbol,
    row.asset,
    row.source,
    row.sourceLabel,
    row.status,
    row.metric,
    row.note,
    ...(row.tags || [])
  ].join(" ").toLowerCase();
  return target.includes(query);
}

function sortedRows(rows) {
  return [...rows].sort((a, b) => {
    const timeDiff = (b.date || 0) - (a.date || 0);
    if (timeDiff) return timeDiff;
    const rankDiff = (Number(a.rank) || 999) - (Number(b.rank) || 999);
    if (rankDiff) return rankDiff;
    return String(a.symbol || a.title || "").localeCompare(String(b.symbol || b.title || ""), "zh-CN");
  });
}

function renderMetrics() {
  const rows = allRows();
  const coinRows = rowsFromSections(newboardState.coinSections);
  const stockRows = rowsFromSections(newboardState.stockSections).filter((row) => ["ipo", "cn", "hk", "us"].includes(row.group));
  const todayCount = rows.filter((row) => isToday(row.date)).length;

  newboardTotal.textContent = String(rows.length);
  newboardCoin.textContent = String(coinRows.length);
  newboardStock.textContent = String(stockRows.length);
  newboardToday.textContent = String(todayCount);
}

function buildCoinBoards() {
  const order = [
    "binance-new",
    "okx-new",
    "bitget-new",
    "binance-alpha-new",
    "hyperliquid-new",
    "trade-xyz-new",
    "aster-new",
    "gate-new",
    "htx-new"
  ];
  return [...newboardState.coinSections]
    .sort((a, b) => {
      const aIndex = order.indexOf(a.id);
      const bIndex = order.indexOf(b.id);
      return (aIndex === -1 ? 9 : aIndex) - (bIndex === -1 ? 9 : bIndex);
    })
    .map((section) => ({
      id: section.id,
      kind: "coin",
      title: section.title,
      subtitle: section.subtitle,
      sourceLabel: section.sourceLabel,
      sourceName: section.sourceName,
      accent: section.accent || "#58c7f3",
      status: section.status,
      emptyMessage: section.emptyMessage,
      rows: sortedRows((section.rows || []).filter(rowMatchesQuery)).slice(0, 10)
    }))
    .filter((board) => board.rows.length || !newboardState.query);
}

function buildStockBoards() {
  const groups = {
    ipo: {
      id: "ipo",
      kind: "stock",
      title: "IPO 日历",
      subtitle: "Nasdaq IPO Calendar",
      sourceLabel: "IPO",
      accent: "#f6bb48",
      sourceName: "Nasdaq",
      rows: []
    },
    cn: {
      id: "cn",
      kind: "stock",
      title: "A股新股榜",
      subtitle: "东方财富新股申购与上市",
      sourceLabel: "CN",
      accent: "#ff9f1c",
      sourceName: "东方财富",
      rows: []
    },
    hk: {
      id: "hk",
      kind: "stock",
      title: "港股新股榜",
      subtitle: "富途 IPO 中心最近上市",
      sourceLabel: "HK",
      accent: "#ff8c6b",
      sourceName: "富途",
      rows: []
    },
    us: {
      id: "us",
      kind: "stock",
      title: "美股新股榜",
      subtitle: "富途 IPO 中心最近上市",
      sourceLabel: "US",
      accent: "#7bd88f",
      sourceName: "富途",
      rows: []
    }
  };

  rowsFromSections(newboardState.stockSections)
    .filter((row) => ["ipo", "cn", "hk", "us"].includes(row.group) && rowMatchesQuery(row))
    .forEach((row) => {
      groups[row.group].rows.push(row);
    });

  return Object.values(groups)
    .filter((board) => board.rows.length)
    .map((board) => ({ ...board, rows: sortedRows(board.rows).slice(0, 10) }));
}

function renderTag(row, heat) {
  const tag = (row.tags || [row.status]).filter(Boolean)[0];
  const baseTag = tag ? `<span class="newboard-tag">${escapeHtml(tag)}</span>` : "";
  const heatTag = heat?.high ? `<span class="newboard-tag is-hot">高热</span>` : "";
  return `${baseTag}${heatTag}`;
}

function renderInsight(row, board, rank) {
  const insight = window.XingyunInsights?.buildRowInsight(
    { ...row, highHeat: rowHeatInfo(row, rank).high },
    { source: board, rank, mode: "newboards" }
  );
  if (!insight) return "";
  const tone = insight.tone === "is-hot" ? " is-hot" : "";
  return `<em class="row-insight-text${tone}" title="${escapeHtml(insight.detail)}">${escapeHtml(insight.detail)}</em>`;
}

function renderCoinRow(row, rank, board) {
  const symbol = escapeHtml(row.symbol || row.name || "--");
  const turnover = escapeHtml(row.turnover || row.metric || "--");
  const dateLabel = escapeHtml(row.dateLabel || formatDate(row.date));
  const price = escapeHtml(row.price || "--");
  const change = escapeHtml(row.change || "--");
  const url = escapeHtml(row.url || "#");
  const changeClass = valueClass(row.change);
  const heat = rowHeatInfo(row, rank);
  const insight = renderInsight(row, board, rank);

  return `
    <a class="rank-row newboard-row-link newcoin-rank-row ${heat.high ? "is-hot" : ""}" href="${url}" target="_blank" rel="noreferrer">
      <div class="rank-badge">${rank}</div>
      <div class="asset-cell">
        ${renderAssetIcon(row, board)}
        <div class="asset-line">
          <strong title="${symbol}">${symbol} ${renderTag(row, heat)}</strong>
          <span>${turnover}${heat.high ? ` · 热度 ${heat.score}` : ""}${insight}</span>
        </div>
      </div>
      <div class="price-cell newcoin-date-cell">
        <b>${dateLabel}</b>
        <span>${escapeHtml(row.status || "新币")}</span>
      </div>
      <div class="metric-cell">
        <b title="${price}">${price}</b>
        <span class="${changeClass}">${change}</span>
      </div>
    </a>
  `;
}

function renderStockRow(row, rank, board) {
  const title = escapeHtml(row.title || row.symbol || "--");
  const symbol = escapeHtml(row.symbol || row.source || "--");
  const metric = escapeHtml(row.metric || row.price || row.note || row.status || "--");
  const status = escapeHtml(row.status || row.sectionTitle || "--");
  const url = escapeHtml(row.url || "#");
  const dateLabel = escapeHtml(row.date ? formatDateTime(row.date) : "--");
  const heat = rowHeatInfo(row, rank);
  const insight = renderInsight(row, board, rank);

  return `
    <a class="rank-row newboard-row-link ${heat.high ? "is-hot" : ""}" href="${url}" target="_blank" rel="noreferrer">
      <div class="rank-badge">${rank}</div>
      <div class="asset-cell">
        ${renderAssetIcon(row, board)}
        <div class="asset-line">
          <strong title="${title}">${title} ${renderTag(row, heat)}</strong>
          <span>${symbol}${heat.high ? ` · 热度 ${heat.score}` : ""}${insight}</span>
        </div>
      </div>
      <div class="price-cell">
        <b>${dateLabel}</b>
        <span>${escapeHtml(row.source || row.sourceLabel || "--")}</span>
      </div>
      <div class="metric-cell">
        <b title="${metric}">${metric}</b>
        <span>${status}</span>
      </div>
    </a>
  `;
}

function renderBoard(board, index) {
  const moreUrls = {
    "okx-new": "https://www.okx.com/zh-hans/markets/rankings/spot/new-crypto",
    "bitget-new": "https://www.bitget.com/zh-CN/markets/rank/hot",
    "binance-new": "https://www.binance.com/zh-CN/markets/trading_data/rankings",
    "binance-alpha-new": "https://www.binance.com/zh-CN/alpha",
    "hyperliquid-new": "https://app.hyperliquid.xyz/trade",
    "trade-xyz-new": "https://trade.xyz",
    "aster-new": "https://www.asterdex.com/en",
    "gate-new": "https://www.gate.com/futures/USDT",
    "htx-new": "https://www.htx.com/futures/linear_swap/exchange",
    cn: "https://datapc.eastmoney.com/da/purchase/index?color=b"
  };
  const moreUrl = moreUrls[board.id] || "#";
  const isCoin = board.kind === "coin";
  const head = isCoin
    ? `<span>名称 | 成交额</span><span>时间</span><span>最新价 | 涨跌幅</span>`
    : `<span>名称</span><span>时间</span><span>状态</span>`;
  const rows = board.rows.length
    ? board.rows.map((row, rowIndex) => (isCoin ? renderCoinRow(row, rowIndex + 1, board) : renderStockRow(row, rowIndex + 1, board))).join("")
    : `<div class="newboard-empty">${escapeHtml(board.emptyMessage || "当前没有可展示的数据。")}</div>`;

  return `
    <article class="board-card newboard-card ${board.rows.length ? "" : "is-muted"}" style="--accent: ${board.accent}; --delay: ${index * 45}ms">
      <header class="board-head newboard-card-head">
        <div>
          <p>${isCoin ? "新币 / 新市场榜" : "新股榜"}</p>
          <h3>${escapeHtml(board.title)}</h3>
        </div>
        <a href="${escapeHtml(moreUrl)}" target="_blank" rel="noreferrer">更多 ›</a>
      </header>
      <div class="rows">
        <div class="board-table-head newboard-table-head ${isCoin ? "is-coin" : ""}">
          <span>#</span>
          ${head}
        </div>
        ${rows}
      </div>
    </article>
  `;
}

function visibleBoards() {
  const coins = buildCoinBoards();
  const stocks = buildStockBoards();
  if (newboardState.filter === "coin") return coins;
  if (newboardState.filter === "stock") return stocks;
  return [...coins, ...stocks];
}

function renderBoards() {
  renderMetrics();
  const boards = visibleBoards();
  if (!boards.length) {
    newboardGrid.innerHTML = '<div class="loading-panel">没有匹配的新币或新股信息。</div>';
    return;
  }
  newboardGrid.innerHTML = boards.map(renderBoard).join("");
  requestAiInsights(boards);
}

function requestAiInsights(boards) {
  window.XingyunAiInsights?.requestForSources(boards, {
    mode: "newboards",
    onUpdate: renderBoards
  });
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
  return response.json();
}

async function loadNewboards(options = {}) {
  if (newboardState.isLoading) return;
  newboardState.isLoading = true;
  let hasData = newboardState.coinSections.length > 0 || newboardState.stockSections.length > 0;
  if (!hasData) {
    hasData = hydrateNewboardCache();
  }

  try {
    const errors = [];
    let updatedAt = Date.now();
    const refreshSuffix = options.refresh ? "?refresh=1" : "";
    try {
      const coinPayload = await fetchJson(`/api/new-coin-rankings${refreshSuffix}`);
      const coinSections = Array.isArray(coinPayload.sections) ? coinPayload.sections : [];
      if (coinSections.length) {
        newboardState.coinSections = coinSections;
        updatedAt = Math.max(updatedAt, coinPayload.updatedAt || 0);
        renderBoards();
      }
    } catch (error) {
      errors.push(error?.message || "新币榜请求失败");
    }

    try {
      const stockPayload = await fetchJson(`/api/listing-events${refreshSuffix}`);
      const stockSections = Array.isArray(stockPayload.sections) ? stockPayload.sections : [];
      if (stockSections.length) {
        newboardState.stockSections = stockSections;
        updatedAt = Math.max(updatedAt, stockPayload.updatedAt || 0);
        renderBoards();
      }
    } catch (error) {
      errors.push(error?.message || "新股榜请求失败");
    }

    if (!newboardState.coinSections.length && !newboardState.stockSections.length) {
      throw new Error(errors.join("；") || "Empty payload");
    }

    setStatus(errors.length ? `部分更新失败 · ${formatDateTime(updatedAt)}` : `真实数据 · ${formatDateTime(updatedAt)}`, errors.length ? "error" : "ok");
    writeCachedPayload(NEWBOARD_CACHE_KEY, {
      updatedAt,
      coinSections: newboardState.coinSections,
      stockSections: newboardState.stockSections
    });
    renderBoards();
  } catch (error) {
    console.warn("Newboards refresh failed.", error);
    setStatus(hasData ? "保留上次数据" : "服务未连接", hasData ? "ok" : "error");
    if (!hasData) {
      newboardGrid.innerHTML = `
        <div class="loading-panel error-panel">
          <b>新币和新股数据暂时不可用</b>
          <span>${escapeHtml(error.message || error)}</span>
        </div>
      `;
    }
  } finally {
    newboardState.isLoading = false;
  }
}

function updateClock() {
  const now = new Date();
  clockNode.textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(now);
}

newboardSearch.addEventListener("input", (event) => {
  newboardState.query = event.target.value.trim();
  renderBoards();
});

newboardFilter.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  newboardState.filter = button.dataset.filter;
  newboardFilter.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderBoards();
});

refreshNewboards.addEventListener("click", () => loadNewboards({ refresh: true }));

updateClock();
setInterval(updateClock, 1000);
hydrateNewboardCache();
loadNewboards();
setInterval(loadNewboards, 5 * 60 * 1000);
