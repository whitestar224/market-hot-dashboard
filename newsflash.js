const flashState = {
  items: [],
  query: ""
};
const FLASH_CACHE_KEY = "xingyunshe:newsflash:payload:v2";

const flashList = document.querySelector("#flashList");
const flashSearch = document.querySelector("#flashSearch");
const refreshFlash = document.querySelector("#refreshFlash");
const flashStatus = document.querySelector("#flashStatus");
const clockEl = document.querySelector("#clock");
const flashSourceSummary = document.querySelector("#flashSourceSummary");

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

function renderFlash() {
  const query = flashState.query.toLowerCase();
  const items = flashState.items.filter((item) => {
    if (!query) return true;
    const sourceNames = Array.isArray(item.sources) ? item.sources.map((source) => source.name).join(" ") : "";
    return [item.title, item.content, item.source, sourceNames].join(" ").toLowerCase().includes(query);
  });

  if (!items.length) {
    flashList.innerHTML = '<div class="loading-panel">没有匹配的快讯。</div>';
    return;
  }

  flashList.innerHTML = items
    .map(
      (item) => {
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
    )
    .join("");
}

async function loadFlash(options = {}) {
  const hasData = flashState.items.length > 0 || hydrateFlashCache();
  try {
    const response = await fetch(`/api/newsflash${options.refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    flashState.items = payload.items || [];
    writeCachedPayload(FLASH_CACHE_KEY, payload);
    const sourceCount = Number(payload.sourceCount || 1);
    const deduplicatedCount = Number(payload.deduplicatedCount || 0);
    setFlashStatus(`${sourceCount} 个来源 · 已去重 ${deduplicatedCount} 条`, "ok");
    const activeSources = (Array.isArray(payload.sources) ? payload.sources : [])
      .filter((source) => source?.status === "ok" && Number(source?.count || 0) > 0)
      .map((source) => source.name)
      .filter(Boolean);
    if (flashSourceSummary && activeSources.length) {
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

flashList.addEventListener("click", async (event) => {
  const button = event.target.closest(".flash-explain-button");
  if (!button || !flashList.contains(button) || button.disabled) return;
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
setInterval(loadFlash, 60_000);
