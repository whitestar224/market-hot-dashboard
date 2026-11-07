const flashState = {
  items: [],
  query: ""
};

// 空投线报不和普通快讯堆在同一页（用户 2026-11-07：「是在这个位置做一个按钮作为
// 页面入口，不要和原先的快讯堆在一起」）。这里两个来源的条目在聚合快讯页只做计数，
// 挂到控制条那个入口按钮上；正文全部在 airdrop.html。
const AIRDROP_SOURCE_IDS = new Set(["foresight", "foresight-tag-airdrop"]);

// airdrop.html 用 window.FLASH_SOURCE_IDS 声明本页只要这几个源，接口按 ?source= 过滤。
// 空数组 = 聚合快讯页（全源）。
const FLASH_ONLY_SOURCE_IDS = Array.isArray(window.FLASH_SOURCE_IDS)
  ? window.FLASH_SOURCE_IDS.map((id) => String(id || "").trim().toLowerCase()).filter(Boolean)
  : [];
const FLASH_IS_AIRDROP_PAGE = FLASH_ONLY_SOURCE_IDS.length > 0;
const FLASH_CACHE_KEY = FLASH_IS_AIRDROP_PAGE
  ? `xingyunshe:newsflash:payload:v2:${FLASH_ONLY_SOURCE_IDS.join("+")}`
  : "xingyunshe:newsflash:payload:v2";

const flashList = document.querySelector("#flashList");
const flashSearch = document.querySelector("#flashSearch");
const refreshFlash = document.querySelector("#refreshFlash");
const flashStatus = document.querySelector("#flashStatus");
const clockEl = document.querySelector("#clock");
const flashSourceSummary = document.querySelector("#flashSourceSummary");
const flashAirdropCount = document.querySelector("#flashAirdropCount");

function setFlashStatus(text, mode = "normal") {
  flashStatus.textContent = text;
  flashStatus.dataset.mode = mode;
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
    // Local cache is non-critical.
  }
}

function hydrateFlashCache() {
  const cached = readCachedPayload(FLASH_CACHE_KEY);
  const items = Array.isArray(cached?.items) ? cached.items : [];
  if (!items.length) return false;
  flashState.items = items;
  setFlashStatus("缓存快讯", "ok");
  renderFlash();
  return true;
}

function stripToPreview(text) {
  return (text || "").replace(/\s+/g, " ").trim();
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

function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function formatFlashTime(ts) {
  const number = Number(ts || 0);
  const milliseconds = number > 10_000_000_000 ? number : number * 1000;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(milliseconds));
}

function flashCardHtml(item) {
  const source = escapeHtml(item.source || "市场快讯");
  const sourceCount = Math.max(1, Array.isArray(item.sources) ? item.sources.length : 1);
  const sourceTitle = escapeHtml(
    Array.isArray(item.sources) ? item.sources.map((entry) => entry.name).filter(Boolean).join("、") : source
  );
  const url = safeHttpUrl(item.url);
  return `
    <article class="flash-card">
      <div class="flash-card-meta">
        <time>${formatFlashTime(item.add_time)}</time>
        <span class="flash-source" title="${sourceTitle}">${source}</span>
        ${sourceCount > 1 ? `<span class="flash-duplicate">${sourceCount} 个来源已合并</span>` : ""}
      </div>
      <div class="flash-card-body">
        <h2>${escapeHtml(item.title)}</h2>
        <p>${escapeHtml(stripToPreview(item.content))}</p>
        <div class="flash-card-actions">
          ${url ? `<a class="flash-source-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">查看来源</a>` : ""}
          ${item.explanationEligible && item.explanationKey ? `
            <button class="flash-explain-button" type="button"
              data-explanation-id="${escapeHtml(item.explanationKey)}"
              title="${escapeHtml(item.explanationReason || "热点事件")}">解释推文</button>
          ` : ""}
        </div>
      </div>
    </article>
  `;
}

function isAirdropItem(item) {
  const ids = new Set(
    (Array.isArray(item.sources) ? item.sources : [])
      .map((entry) => String(entry?.id || "").trim().toLowerCase())
      .filter(Boolean)
  );
  ids.add(String(item.sourceId || "").trim().toLowerCase());
  return [...ids].some((id) => AIRDROP_SOURCE_IDS.has(id));
}

function updateAirdropEntry(count) {
  if (!flashAirdropCount) return;
  if (!count) {
    flashAirdropCount.hidden = true;
    flashAirdropCount.textContent = "0";
    return;
  }
  flashAirdropCount.hidden = false;
  flashAirdropCount.textContent = String(count);
}

function renderFlash() {
  const query = flashState.query.toLowerCase();
  const matched = flashState.items.filter((item) => {
    if (!query) return true;
    const sourceNames = Array.isArray(item.sources) ? item.sources.map((source) => source.name).join(" ") : "";
    return [item.title, item.content, item.source, sourceNames].join(" ").toLowerCase().includes(query);
  });

  // 聚合快讯页：空投条目只计数（入口按钮上的角标），不进主列表，避免和普通快讯堆在一起。
  // 空投线报页：全部条目就是主列表。
  const airdropCount = matched.filter(isAirdropItem).length;
  updateAirdropEntry(airdropCount);
  const items = FLASH_IS_AIRDROP_PAGE ? matched : matched.filter((item) => !isAirdropItem(item));

  if (!items.length) {
    flashList.innerHTML = '<div class="loading-panel">没有匹配的快讯。</div>';
    return;
  }
  flashList.innerHTML = items.map(flashCardHtml).join("");
}

async function loadFlash(options = {}) {
  const hasData = flashState.items.length > 0 || hydrateFlashCache();
  const params = new URLSearchParams();
  if (FLASH_ONLY_SOURCE_IDS.length) params.set("source", FLASH_ONLY_SOURCE_IDS.join(","));
  if (options.refresh) params.set("refresh", "1");
  const queryString = params.toString();
  try {
    const response = await fetch(`/api/newsflash${queryString ? `?${queryString}` : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    flashState.items = payload.items || [];
    writeCachedPayload(FLASH_CACHE_KEY, payload);
    const sourceCount = Number(payload.sourceCount || 1);
    const deduplicatedCount = Number(payload.deduplicatedCount || 0);
    if (FLASH_IS_AIRDROP_PAGE) {
      const liveSources = (Array.isArray(payload.sources) ? payload.sources : [])
        .filter((entry) => FLASH_ONLY_SOURCE_IDS.includes(String(entry?.id || "").toLowerCase()))
        .filter((entry) => entry?.status === "ok" && Number(entry?.count || 0) > 0);
      const liveCount = liveSources.length || FLASH_ONLY_SOURCE_IDS.length;
      setFlashStatus(`${liveCount} 个来源 · ${flashState.items.length} 条线报`, "ok");
    } else {
      setFlashStatus(`${sourceCount} 个来源 · 已去重 ${deduplicatedCount} 条`, "ok");
    }
    const activeSources = (Array.isArray(payload.sources) ? payload.sources : [])
      .filter((source) => source?.status === "ok" && Number(source?.count || 0) > 0)
      // 空投来源的条目已经搬到 airdrop.html，摘要里再列一遍会让人以为这页还有它们。
      .filter((source) => !AIRDROP_SOURCE_IDS.has(String(source?.id || "").toLowerCase()))
      .map((source) => source.name)
      .filter(Boolean);
    if (flashSourceSummary && activeSources.length && !FLASH_IS_AIRDROP_PAGE) {
      flashSourceSummary.textContent = `已接入 ${activeSources.join(" · ")}`;
      flashSourceSummary.title = activeSources.join("、");
    }
  } catch (error) {
    setFlashStatus(hasData ? "保留上次快讯" : "服务未连接", hasData ? "ok" : "error");
    if (hasData) return;
    flashList.innerHTML = `
      <div class="loading-panel error-panel">
        <b>需要通过本地服务打开页面</b>
        <span>请访问 http://127.0.0.1:8765/newsflash.html，浏览器直接打开文件无法聚合新闻源。</span>
      </div>
    `;
    return;
  }
  renderFlash();
}

function updateClock() {
  clockEl.textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(new Date());
}

flashSearch.addEventListener("input", (event) => {
  flashState.query = event.target.value.trim();
  renderFlash();
});

refreshFlash.addEventListener("click", () => loadFlash({ refresh: true }));

// 事件委托挂 document：入口按钮、主列表、解释按钮分布在不同子树，
// 绑在 flashList 上会漏掉板块/顶部按钮上的点击。
document.addEventListener("click", async (event) => {
  const button = event.target.closest(".flash-explain-button");
  if (!button || button.disabled) return;
  const id = String(button.dataset.explanationId || "");
  if (!/^[a-f0-9]{40}$/.test(id)) return;
  const originalText = button.textContent;
  button.disabled = true;
  button.dataset.state = "opening";
  button.textContent = "正在打开…";
  try {
    const response = await fetch("/api/newsflash/explanations/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    button.dataset.state = "opened";
    button.textContent = "已打开";
    window.setTimeout(() => {
      button.disabled = false;
      button.dataset.state = "";
      button.textContent = originalText;
    }, 1800);
  } catch (error) {
    button.disabled = false;
    button.dataset.state = "error";
    button.textContent = error?.message?.includes("更新") ? "请先刷新" : "重试解释";
  }
});

updateClock();
setInterval(updateClock, 1000);
hydrateFlashCache();
loadFlash();
// The panel previously refreshed once a minute, and the backend cache adds its
// own TTL on top — together that let a flash sit unseen for up to ~2 minutes.
// Poll on a short clock; the backend serves stale-while-revalidate so this never
// blocks on a slow upstream fetch.
setInterval(loadFlash, 15_000);
