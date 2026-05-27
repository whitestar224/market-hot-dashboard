const flashState = {
  items: [],
  query: ""
};
const FLASH_CACHE_KEY = "xingyunshe:newsflash:payload:v1";

const flashList = document.querySelector("#flashList");
const flashSearch = document.querySelector("#flashSearch");
const refreshFlash = document.querySelector("#refreshFlash");
const flashStatus = document.querySelector("#flashStatus");
const clockEl = document.querySelector("#clock");

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

function formatFlashTime(ts) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(ts * 1000));
}

function renderFlash() {
  const query = flashState.query.toLowerCase();
  const items = flashState.items.filter((item) => {
    if (!query) return true;
    return [item.title, item.content, item.time].join(" ").toLowerCase().includes(query);
  });

  if (!items.length) {
    flashList.innerHTML = '<div class="loading-panel">没有匹配的快讯。</div>';
    return;
  }

  flashList.innerHTML = items
    .map(
      (item) => `
        <article class="flash-card">
          <time>${formatFlashTime(item.add_time)}</time>
          <div>
            <h2>${item.title}</h2>
            <p>${stripToPreview(item.content)}</p>
            ${item.url ? `<a href="${item.url}" target="_blank" rel="noreferrer">相关链接</a>` : ""}
          </div>
        </article>
      `
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
    setFlashStatus("实时快讯", "ok");
  } catch (error) {
    setFlashStatus(hasData ? "保留上次快讯" : "服务未连接", hasData ? "ok" : "error");
    if (hasData) return;
    flashList.innerHTML = `
      <div class="loading-panel error-panel">
        <b>需要通过本地服务打开页面</b>
        <span>请访问 http://127.0.0.1:8765/newsflash.html，浏览器直接打开文件无法代理律动数据。</span>
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

updateClock();
setInterval(updateClock, 1000);
hydrateFlashCache();
loadFlash();
setInterval(loadFlash, 60_000);
