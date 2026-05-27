const listingState = {
  sections: [],
  filter: "all",
  query: "",
  isLoading: false
};
const LISTING_CACHE_KEY = "xingyunshe:listing-events:payload:v2";

const listingBoard = document.querySelector("#listingBoard");
const todayListings = document.querySelector("#todayListings");
const listingSearch = document.querySelector("#listingSearch");
const listingFilter = document.querySelector("#listingFilter");
const listingStatus = document.querySelector("#listingStatus");
const refreshListings = document.querySelector("#refreshListings");
const clockNode = document.querySelector("#clock");
const listingCount = document.querySelector("#listingCount");
const listingToday = document.querySelector("#listingToday");
const listingSources = document.querySelector("#listingSources");

const dayFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "long",
  day: "numeric"
});

const compactDayFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit"
});

const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false
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
    // Local cache only affects perceived page speed.
  }
}

function hydrateListingCache() {
  const cached = readCachedPayload(LISTING_CACHE_KEY);
  const sections = Array.isArray(cached?.sections) ? cached.sections : [];
  if (!sections.length) return false;
  listingState.sections = sections;
  setListingStatus(`缓存数据 · ${formatDateTime(cached.updatedAt || cached._cache?.updatedAt)}`, "ok");
  renderListings();
  return true;
}

function setListingStatus(text, mode = "normal") {
  if (!listingStatus) return;
  listingStatus.textContent = text;
  listingStatus.dataset.mode = mode;
}

function startOfDay(value) {
  const date = new Date(value);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

function isToday(value) {
  if (!value) return false;
  return startOfDay(value) === startOfDay(Date.now());
}

function formatDate(value) {
  if (!value) return "待定";
  return compactDayFormatter.format(new Date(value));
}

function formatDay(value) {
  if (!value) return "待定日期";
  const date = new Date(value);
  const today = startOfDay(Date.now());
  const rowDay = startOfDay(value);
  if (rowDay === today) return "今天";
  if (rowDay === today - 24 * 60 * 60 * 1000) return "昨天";
  if (rowDay === today + 24 * 60 * 60 * 1000) return "明天";
  return dayFormatter.format(date);
}

function formatTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  const isMidnight = date.getHours() === 0 && date.getMinutes() === 0 && date.getSeconds() === 0;
  return isMidnight ? formatDate(value) : timeFormatter.format(date);
}

function formatDateTime(value) {
  if (!value) return "刚刚更新";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function parseSignedNumber(value) {
  return Number.parseFloat(String(value || "0").replace("%", "").replace("+", "")) || 0;
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
  const amount = Number(row.amount) || amountFromText(row.turnover || row.metric || row.note || row.price);
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
  const isContractListing = row.group === "crypto" || /合约|永续|上线|上新|will list|perpetual|usdt/i.test([
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
    row.status,
    row.source,
    row.sourceLabel,
    row.metric,
    row.note,
    row.sectionTitle,
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
  if (/高热|热门|热度|讨论|关注|超购|融资|首日|ipo|新币|合约/i.test(text)) score += 12;
  if (isContractListing) {
    if (/binance|币安|okx|欧易|bitget/i.test(text)) score += 18;
    if (/合约|永续|perpetual|will list|上线|上新|usdt/i.test(text)) score += 18;
    if (/ai|人工智能|meme|memecoin|defi|rwa|gamefi|launchpool|空投|热门/i.test(text)) score += 18;
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
    score: Math.min(100, Math.round(score))
  };
}

function allRows() {
  return listingState.sections.flatMap((section) =>
    (section.rows || []).map((row) => ({
      ...row,
      sectionTitle: section.title,
      sectionAccent: section.accent
    }))
  );
}

function rowMatches(row) {
  if (listingState.filter !== "all" && row.group !== listingState.filter) return false;
  if (!listingState.query) return true;
  const query = listingState.query.toLowerCase();
  const target = [
    row.title,
    row.symbol,
    row.source,
    row.sourceLabel,
    row.status,
    row.metric,
    row.note,
    row.sectionTitle,
    ...(row.tags || [])
  ].join(" ").toLowerCase();
  return target.includes(query);
}

function sortRows(rows) {
  return [...rows].sort((a, b) => {
    const dateDiff = (b.date || 0) - (a.date || 0);
    if (dateDiff) return dateDiff;
    return String(a.source || "").localeCompare(String(b.source || ""), "zh-CN");
  });
}

function visibleRows() {
  return sortRows(allRows().filter(rowMatches));
}

function renderMetrics(rows = allRows()) {
  const todayRows = rows.filter((row) => isToday(row.date));
  const sources = new Set(rows.map((row) => row.source).filter(Boolean));
  listingCount.textContent = rows.length || "--";
  listingToday.textContent = todayRows.length || "--";
  listingSources.textContent = sources.size || "--";
}

function sourceInitial(source) {
  const value = String(source || "").trim();
  if (!value) return "NX";
  if (/binance/i.test(value)) return "BN";
  if (/bitget/i.test(value)) return "BG";
  if (/okx|欧易/i.test(value)) return "OK";
  if (/nasdaq/i.test(value)) return "NQ";
  if (/东方财富|eastmoney|a股/i.test(value)) return "CN";
  if (/富途/.test(value)) return "FT";
  return value.slice(0, 2).toUpperCase();
}

function renderTodayOverview(rows) {
  if (!todayListings) return;
  const todayRows = sortRows(rows.filter((row) => isToday(row.date))).slice(0, 8);
  const cryptoCount = todayRows.filter((row) => row.group === "crypto").length;
  const ipoCount = todayRows.filter((row) => row.group === "ipo").length;
  const stockCount = todayRows.filter((row) => row.group === "hk" || row.group === "us" || row.group === "cn").length;

  if (!todayRows.length) {
    todayListings.innerHTML = `
      <article class="today-listing-card is-empty">
        <div>
          <p class="section-label">TODAY</p>
          <h3>今日暂无上新或 IPO 事件</h3>
          <span>页面仍会按发布时间展示近期交易所上新、IPO 日历和港美股上市信息。</span>
        </div>
        <b>${new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date())}</b>
      </article>
    `;
    return;
  }

  todayListings.innerHTML = `
    <article class="today-listing-card">
      <div>
        <p class="section-label">TODAY</p>
        <h3>今日上新和 IPO 情况</h3>
        <span>交易所 ${cryptoCount} 条 · IPO ${ipoCount} 条 · 港美A股 ${stockCount} 条</span>
      </div>
      <b>${todayRows.length}</b>
    </article>
    <div class="today-listing-scroll">
      ${todayRows.map((row, index) => renderTodayItem(row, index + 1)).join("")}
    </div>
  `;
}

function renderTodayItem(row, rank = 99) {
  const title = escapeHtml(row.title || row.symbol || "--");
  const heat = rowHeatInfo(row, rank);
  return `
    <a class="today-listing-item ${heat.high ? "is-hot" : ""}" href="${escapeHtml(row.url || "#")}" target="_blank" rel="noreferrer">
      <span>${escapeHtml(sourceInitial(row.source))}</span>
      <div>
        <b title="${title}">${title}</b>
        <em>${escapeHtml(row.symbol || row.status || row.source || "--")}${heat.high ? ` · 高热 ${heat.score}` : ""}</em>
      </div>
      <time>${formatTime(row.date)}</time>
    </a>
  `;
}

function groupRowsByDay(rows) {
  const groups = new Map();
  rows.forEach((row) => {
    const key = row.date ? String(startOfDay(row.date)) : "unknown";
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        date: row.date || 0,
        rows: []
      });
    }
    groups.get(key).rows.push(row);
  });
  return [...groups.values()].sort((a, b) => (b.date || 0) - (a.date || 0));
}

function renderListings() {
  const rows = visibleRows();
  renderMetrics();
  renderTodayOverview(allRows());

  if (!rows.length) {
    listingBoard.innerHTML = '<div class="loading-panel">没有匹配当前筛选条件的上新或 IPO 信息。</div>';
    return;
  }

  listingBoard.innerHTML = groupRowsByDay(rows).map(renderTimelineDay).join("");
}

function renderTimelineDay(group) {
  return `
    <section class="timeline-day" aria-label="${escapeHtml(formatDay(group.date))}">
      <div class="timeline-date">
        <b>${escapeHtml(formatDay(group.date))}</b>
        <span>${group.rows.length} 条</span>
      </div>
      <div class="timeline-items">
        ${group.rows.map((row, index) => renderTimelineItem(row, index + 1)).join("")}
      </div>
    </section>
  `;
}

function renderTimelineItem(row, rank = 99) {
  const heat = rowHeatInfo(row, rank);
  const tags = [
    ...(heat.high ? ["高热"] : []),
    ...(row.tags || [])
  ].slice(0, 5).map((tag) => `<span class="${tag === "高热" ? "is-hot" : ""}">${escapeHtml(tag)}</span>`).join("");
  const title = escapeHtml(row.title || row.symbol || "--");
  const symbol = escapeHtml(row.symbol || row.sourceLabel || "--");
  const metric = escapeHtml(row.metric || row.price || row.status || "");
  const note = escapeHtml(row.note || "");
  const status = escapeHtml(row.status || row.sectionTitle || "--");
  const source = escapeHtml(row.source || row.sourceLabel || "--");
  const url = escapeHtml(row.url || "#");

  return `
    <article class="timeline-item ${heat.high ? "is-hot" : ""}" style="--accent: ${escapeHtml(heat.high ? "#ff695d" : row.sectionAccent || "#53d6ff")}">
      <time class="timeline-time">${formatTime(row.date)}</time>
      <span class="timeline-dot" aria-hidden="true"></span>
      <a class="timeline-card ${heat.high ? "is-hot" : ""}" href="${url}" target="_blank" rel="noreferrer">
        <div class="timeline-card-head">
          <span class="timeline-source">
            <i>${escapeHtml(sourceInitial(row.source))}</i>
            ${source}
          </span>
          <span class="timeline-status">${heat.high ? `高热 ${heat.score}` : status}</span>
        </div>
        <h3 title="${title}">${title}</h3>
        <div class="timeline-meta">
          <b>${symbol}</b>
          ${metric ? `<span title="${metric}">${metric}</span>` : ""}
        </div>
        ${note ? `<p>${note}</p>` : ""}
        ${tags ? `<div class="listing-tags">${tags}</div>` : ""}
      </a>
    </article>
  `;
}

async function loadListings(options = {}) {
  if (listingState.isLoading) return;
  listingState.isLoading = true;
  let hasExistingData = listingState.sections.length > 0;
  if (!hasExistingData) {
    hasExistingData = hydrateListingCache();
  }

  try {
    const response = await fetch(`/api/listing-events${options.refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const sections = Array.isArray(payload.sections) ? payload.sections : [];
    if (!sections.length) throw new Error("Empty listing payload");
    listingState.sections = sections;
    writeCachedPayload(LISTING_CACHE_KEY, payload);
    setListingStatus(`真实数据 · ${formatDateTime(payload.updatedAt)}`, "ok");
    renderListings();
  } catch (error) {
    console.warn("Listing refresh failed.", error);
    setListingStatus(hasExistingData ? "保留上次数据" : "服务未连接", hasExistingData ? "ok" : "error");
    if (!hasExistingData) {
      listingBoard.innerHTML = `
        <div class="loading-panel error-panel">
          <b>上新和 IPO 数据暂时不可用</b>
          <span>${escapeHtml(error.message || error)}</span>
        </div>
      `;
      if (todayListings) {
        todayListings.innerHTML = '<div class="loading-panel error-panel">今日概览暂时不可用。</div>';
      }
    }
  } finally {
    listingState.isLoading = false;
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

listingSearch.addEventListener("input", (event) => {
  listingState.query = event.target.value.trim();
  renderListings();
});

listingFilter.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  listingState.filter = button.dataset.filter;
  listingFilter.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderListings();
});

refreshListings.addEventListener("click", () => loadListings({ refresh: true }));

updateClock();
setInterval(updateClock, 1000);
hydrateListingCache();
loadListings();
setInterval(loadListings, 5 * 60 * 1000);
