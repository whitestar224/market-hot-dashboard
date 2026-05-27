const state = {
  query: "",
  selectedId: "",
  briefs: []
};
const BRIEF_CACHE_KEY = "xingyunshe:automation-briefs:payload:v1";

const briefList = document.querySelector("#briefList");
const briefOverview = document.querySelector("#briefOverview");
const briefSearch = document.querySelector("#briefSearch");
const refreshBriefs = document.querySelector("#refreshBriefs");
const briefStatus = document.querySelector("#briefStatus");
const clockEl = document.querySelector("#clock");
const briefCount = document.querySelector("#briefCount");
const briefLatest = document.querySelector("#briefLatest");
const briefWords = document.querySelector("#briefWords");

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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
    // Local cache is just a rendering shortcut.
  }
}

function hydrateBriefCache() {
  const cached = readCachedPayload(BRIEF_CACHE_KEY);
  const briefs = Array.isArray(cached?.briefs) ? cached.briefs : [];
  if (!briefs.length) return false;
  state.briefs = briefs;
  state.selectedId = latestBrief()?.id || state.briefs[0]?.id || "";
  setStatus(`缓存简报 ${state.briefs.length} 份`, "ok");
  renderBriefs();
  return true;
}

function formatTime(value, fallback = "暂无完成时间") {
  if (!value) return fallback;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function compactNumber(value) {
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);
}

function setStatus(text, mode = "normal") {
  briefStatus.textContent = text;
  briefStatus.dataset.mode = mode;
}

function cleanContent(content) {
  return String(content || "")
    .replace(/<heartbeat>[\s\S]*?<\/heartbeat>\s*$/g, "")
    .replace(/::inbox-item\{[\s\S]*?\}\s*$/g, "")
    .trim();
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function wordCount(content) {
  const clean = cleanContent(content);
  const cjk = clean.match(/[\u4e00-\u9fa5]/g)?.length || 0;
  const latin = clean.replace(/[\u4e00-\u9fa5]/g, " ").match(/[A-Za-z0-9_./%-]+/g)?.length || 0;
  return cjk + latin;
}

function getLead(content) {
  return cleanContent(content)
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)[0] || "暂无摘要";
}

function getTags(brief) {
  const content = cleanContent(brief.content);
  const tickers = [...content.matchAll(/`([^`]{1,24})`/g)].map((match) => match[1]);
  const markets = ["Binance", "OKX", "Bitget", "港股", "美股", "A股", "风险", "主升浪"].filter((item) =>
    content.includes(item)
  );
  return [...new Set([...tickers, ...markets])].slice(0, 8);
}

function isMarkdownTable(block) {
  const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
  return lines.length >= 2 && lines[0].includes("|") && /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$/.test(lines[1]);
}

function isTableDivider(line) {
  return /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$/.test(String(line || "").trim());
}

function splitTableCells(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function classifyTable(header = []) {
  const joined = header.join(" ");
  if (/交易对|分类|现价|24h|成交额|判断|涨跌幅|价格/.test(joined)) return "market";
  return "default";
}

function cellTone(raw) {
  const text = String(raw || "");
  if (/(风险|下跌|退潮|弱|跌|卖压)/.test(text)) return "risk";
  if (/(龙头|强|主升|机会|放量|突破|高涨幅)/.test(text)) return "strong";
  if (/(\+|\b涨)/.test(text)) return "up";
  if (/(-|\b跌)/.test(text)) return "down";
  return "";
}

function renderCategoryCell(raw) {
  const tone = cellTone(raw) || "neutral";
  return `<span class="brief-signal ${tone}">${inlineMarkdown(raw)}</span>`;
}

function renderSymbolCell(raw) {
  const text = String(raw || "").trim();
  const symbol = text.match(/[A-Z0-9]{2,}(?:USDT|USD|HK|SH|SZ)?/i)?.[0] || text;
  const rest = text.replace(symbol, "").trim();
  return `
    <span class="brief-symbol-cell">
      <b>${escapeHtml(symbol)}</b>
      ${rest ? `<em>${inlineMarkdown(rest)}</em>` : ""}
    </span>
  `;
}

function renderMetricCell(raw) {
  const tone = cellTone(raw);
  return `<span class="brief-metric ${tone}">${inlineMarkdown(raw)}</span>`;
}

function tableCellClass(header, raw) {
  const label = String(header || "");
  const tone = cellTone(raw);
  return [
    /分类/.test(label) ? "is-signal" : "",
    /交易对|名称|代码|币种/.test(label) ? "is-symbol" : "",
    /24h|涨跌|涨幅|跌幅|现价|价格|成交额/.test(label) ? "is-metric" : "",
    /判断|备注|观察/.test(label) ? "is-judgement" : "",
    tone ? `tone-${tone}` : ""
  ].filter(Boolean).join(" ");
}

function renderTableCell(header, raw) {
  const label = String(header || "");
  if (/分类/.test(label)) return renderCategoryCell(raw);
  if (/交易对|名称|代码|币种/.test(label)) return renderSymbolCell(raw);
  if (/24h|涨跌|涨幅|跌幅|现价|价格|成交额/.test(label)) return renderMetricCell(raw);
  return inlineMarkdown(raw);
}

function parseTable(block) {
  const lines = Array.isArray(block) ? block : block.split("\n").map((line) => line.trim()).filter(Boolean);
  const headerRaw = splitTableCells(lines[0]);
  const rowsRaw = lines.slice(2).map(splitTableCells);
  const tableType = classifyTable(headerRaw);
  return `
    <div class="brief-table-wrap brief-table-${tableType}">
      <table>
        <thead><tr>${headerRaw.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rowsRaw.map((row) => `
            <tr>
              ${headerRaw.map((header, index) => {
                const raw = row[index] || "";
                return `<td class="${tableCellClass(header, raw)}">${renderTableCell(header, raw)}</td>`;
              }).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function splitBriefBlocks(clean) {
  const lines = clean.split("\n");
  const blocks = [];
  let buffer = [];

  const flushBuffer = () => {
    const text = buffer.join("\n").trim();
    if (text) blocks.push({ type: "text", value: text });
    buffer = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    const next = (lines[index + 1] || "").trim();
    if (line.includes("|") && isTableDivider(next)) {
      flushBuffer();
      const tableLines = [line, next];
      index += 2;
      while (index < lines.length) {
        const row = lines[index].trim();
        if (!row || !row.includes("|")) break;
        tableLines.push(row);
        index += 1;
      }
      index -= 1;
      blocks.push({ type: "table", lines: tableLines });
      continue;
    }
    if (!line) {
      flushBuffer();
      continue;
    }
    buffer.push(line);
  }
  flushBuffer();
  return blocks;
}

function renderList(lines) {
  return `
    <ul class="brief-bullets">
      ${lines.map((line) => `<li>${inlineMarkdown(line.replace(/^-+\s*/, ""))}</li>`).join("")}
    </ul>
  `;
}

function renderBriefBody(content) {
  const clean = cleanContent(content);
  if (!clean) return '<p class="brief-paragraph">暂无简报正文。</p>';

  return splitBriefBlocks(clean)
    .map((block) => {
      if (block.type === "table") return parseTable(block.lines);

      const text = block.value;
      if (isMarkdownTable(text)) return parseTable(text);

      const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
      const first = lines[0] || "";

      if (lines.every((line) => line.startsWith("- "))) {
        return renderList(lines);
      }

      if (lines.length === 1 && /^\*\*[^*]+\*\*$/.test(first)) {
        return `<h3>${inlineMarkdown(first.replace(/^\*\*|\*\*$/g, ""))}</h3>`;
      }

      if (lines.length === 1 && /^(Codex|港股|美股|A股|总结|风险|Binance|OKX|Bitget|大盘|说明|龙头战法判断)/.test(first)) {
        return `<h3>${inlineMarkdown(first)}</h3>`;
      }

      return `<p class="brief-paragraph">${inlineMarkdown(text).replace(/\n/g, "<br>")}</p>`;
    })
    .join("");
}

function visibleBriefs() {
  if (!state.query) return state.briefs;
  const query = state.query.toLowerCase();
  return state.briefs.filter((brief) =>
    [brief.name, brief.status, brief.rrule, brief.content].join(" ").toLowerCase().includes(query)
  );
}

function latestBrief(briefs = state.briefs) {
  return [...briefs].sort((a, b) => (b.completedAt || 0) - (a.completedAt || 0))[0];
}

function renderMetrics() {
  const latest = latestBrief();
  const totalWords = state.briefs.reduce((sum, brief) => sum + wordCount(brief.content), 0);
  const activeCount = state.briefs.filter((brief) => brief.status === "ACTIVE").length;
  const matchedCount = visibleBriefs().length;

  briefCount.textContent = state.briefs.length || "--";
  briefLatest.textContent = latest ? formatTime(latest.completedAt, "--") : "--";
  briefWords.textContent = totalWords ? compactNumber(totalWords) : "--";

  briefOverview.innerHTML = [
    ["活跃自动化", `${activeCount}/${state.briefs.length || 0}`, "仍在按小时产出简报"],
    ["最新生成", latest ? formatTime(latest.completedAt) : "--", latest ? latest.name : "等待任务完成"],
    ["当前匹配", `${matchedCount} 份`, state.query ? `关键词：${state.query}` : "未启用搜索过滤"],
    ["阅读纪律", "不凑数", "数据缺失的任务会在简报中保留风险说明"]
  ]
    .map(
      ([label, value, meta]) => `
        <article class="brief-stat-card">
          <span>${label}</span>
          <strong>${value}</strong>
          <p>${meta}</p>
        </article>
      `
    )
    .join("");
}

function renderSidebar(briefs, selected) {
  return `
    <aside class="brief-sidebar" aria-label="简报任务">
      <div class="brief-sidebar-head">
        <span>任务流</span>
        <b>${briefs.length}</b>
      </div>
      <div class="brief-tabs">
        ${briefs
          .map((brief) => {
            const tags = getTags(brief).slice(0, 4);
            return `
              <button class="brief-tab ${brief.id === selected.id ? "active" : ""}" type="button" data-brief-id="${escapeHtml(brief.id)}">
                <span>${escapeHtml(brief.id)}</span>
                <strong>${escapeHtml(brief.name)}</strong>
                <em>${formatTime(brief.completedAt)} · ${escapeHtml(brief.status || "UNKNOWN")}</em>
                <small>${tags.map((tag) => `<i>${escapeHtml(tag)}</i>`).join("")}</small>
              </button>
            `;
          })
          .join("")}
      </div>
    </aside>
  `;
}

function renderReader(brief) {
  const tags = getTags(brief);
  return `
    <article class="brief-reader">
      <header class="brief-reader-head">
        <div>
          <p>${escapeHtml(brief.id)} · ${escapeHtml(brief.rrule || "未配置频率")}</p>
          <h2>${escapeHtml(brief.name)}</h2>
          <span>${escapeHtml(getLead(brief.content))}</span>
        </div>
        <time>${formatTime(brief.completedAt)}</time>
      </header>
      <div class="brief-chip-row">
        ${tags.length ? tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("") : "<span>无提取标签</span>"}
      </div>
      <div class="brief-body">${renderBriefBody(brief.content)}</div>
    </article>
  `;
}

function renderBriefs() {
  renderMetrics();
  const briefs = visibleBriefs();
  if (!briefs.length) {
    briefList.innerHTML = '<div class="empty-state"><b>没有匹配的简报</b><span>换一个关键词试试。</span></div>';
    return;
  }

  const selected = briefs.find((brief) => brief.id === state.selectedId) || latestBrief(briefs) || briefs[0];
  state.selectedId = selected.id;
  briefList.innerHTML = `${renderSidebar(briefs, selected)}${renderReader(selected)}`;
}

async function loadBriefs(options = {}) {
  const hasData = state.briefs.length > 0 || hydrateBriefCache();
  try {
    const response = await fetch(`/api/automation-briefs${options.refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.briefs = payload.briefs || [];
    state.selectedId = latestBrief()?.id || state.briefs[0]?.id || "";
    writeCachedPayload(BRIEF_CACHE_KEY, payload);
    setStatus(`已读取 ${state.briefs.length} 份`, "ok");
    renderBriefs();
  } catch (error) {
    setStatus(hasData ? "保留上次简报" : "读取失败", hasData ? "ok" : "error");
    if (hasData) return;
    briefOverview.innerHTML = "";
    briefList.innerHTML = `
      <div class="loading-panel error-panel">
        <b>没有读到自动化简报</b>
        <span>请访问 http://127.0.0.1:8765/briefs.html，本地服务需要能读取 Codex 自动化记录。</span>
      </div>
    `;
  }
}

function tickClock() {
  clockEl.textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date());
}

briefSearch.addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  renderBriefs();
});

briefList.addEventListener("click", (event) => {
  const tab = event.target.closest(".brief-tab");
  if (!tab) return;
  state.selectedId = tab.dataset.briefId;
  renderBriefs();
});

refreshBriefs.addEventListener("click", () => loadBriefs({ refresh: true }));

tickClock();
setInterval(tickClock, 1000);
hydrateBriefCache();
loadBriefs();
