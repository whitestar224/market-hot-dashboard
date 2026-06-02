const RSS_SOURCES_KEY = "xingyunshe:rss:sources:v1";
const RSS_ITEMS_KEY = "xingyunshe:rss:items:v1";
const RSS_READ_KEY = "xingyunshe:rss:read:v1";
const RSS_AUTO_KEY = "xingyunshe:rss:auto-refresh:v1";
const RSS_ALERT_KEY = "xingyunshe:rss:alerts:v1";
const RSS_MAX_STORED_ITEMS = 1800;
const RSS_RENDER_LIMIT = 500;
const RSS_RETENTION_MS = 14 * 24 * 60 * 60 * 1000;
const RSS_ALERT_WINDOW_MS = 24 * 60 * 60 * 1000;
const RSS_AUTH_ALERT_LAST_KEY = "xingyunshe:rss:wechat-auth-alert:last";
const RSS_AUTH_ALERT_COOLDOWN_MS = 15 * 60 * 1000;
let wechatLoginPending = false;
let renderQueued = false;
let rssSourceSaveTimer = null;
let rssSourceHydrated = false;
let rssItemSaveTimer = null;
let rssItemsHydrated = false;

const rssState = {
  sources: [],
  items: new Map(),
  read: new Set(),
  activeSourceId: "all",
  viewFilter: "all",
  query: "",
  modalType: "feed",
  loading: new Set(),
  refreshingAll: false,
  auto: {
    enabled: true,
    intervalMs: 180000,
    nextAt: 0,
    lastRunAt: 0,
    timer: null
  }
};

const nodes = {
  clock: document.querySelector("#clock"),
  status: document.querySelector("#rssStatus"),
  sourceCount: document.querySelector("#rssSourceCount"),
  itemCount: document.querySelector("#rssItemCount"),
  unreadCount: document.querySelector("#rssUnreadCount"),
  wechatCount: document.querySelector("#rssWechatCount"),
  allCount: document.querySelector("#rssAllCount"),
  allSource: document.querySelector("#rssAllSource"),
  sourceList: document.querySelector("#rssSourceList"),
  itemList: document.querySelector("#rssItemList"),
  search: document.querySelector("#rssSearch"),
  viewFilter: document.querySelector("#rssViewFilter"),
  refreshAll: document.querySelector("#rssRefreshAll"),
  autoToggle: document.querySelector("#rssAutoToggle"),
  autoInterval: document.querySelector("#rssAutoInterval"),
  nextRefresh: document.querySelector("#rssNextRefresh"),
  wechatAuthBox: document.querySelector(".rss-wechat-auth"),
  wechatAuthState: document.querySelector("#rssWechatAuthState"),
  wechatLogin: document.querySelector("#rssWechatLogin"),
  wechatQrPanel: document.querySelector("#rssWechatQrPanel"),
  wechatQrImage: document.querySelector("#rssWechatQrImage"),
  wechatQrHint: document.querySelector("#rssWechatQrHint"),
  exportOpml: document.querySelector("#rssExportOpml"),
  importBtn: document.querySelector("#rssImportBtn"),
  opmlInput: document.querySelector("#rssOpmlInput"),
  addBtn: document.querySelector("#rssAddBtn"),
  modal: document.querySelector("#rssModal"),
  form: document.querySelector("#rssForm"),
  closeModal: document.querySelector("#rssCloseModal"),
  cancelBtn: document.querySelector("#rssCancelBtn"),
  titleInput: document.querySelector("#rssTitleInput"),
  urlInput: document.querySelector("#rssUrlInput"),
  wechatInput: document.querySelector("#rssWechatInput"),
  feedFields: document.querySelector("#rssFeedFields"),
  wechatFields: document.querySelector("#rssWechatFields"),
  typeTabs: document.querySelector(".rss-type-tabs"),
  currentScope: document.querySelector("#rssCurrentScope"),
  readerTitle: document.querySelector("#rssReaderTitle")
};

const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
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

function readJson(key, fallback) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "");
    return parsed ?? fallback;
  } catch {
    return fallback;
  }
}

function saveJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function uid(prefix = "rss") {
  if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`;
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function stableSourceId(value) {
  let hash = 0;
  const text = String(value || "");
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
  }
  return `wx-${Math.abs(hash).toString(36)}`;
}

function setStatus(text, mode = "normal") {
  if (!nodes.status) return;
  nodes.status.textContent = text;
  nodes.status.dataset.mode = mode;
}

function setWechatAuthState(text, authorized = false) {
  if (nodes.wechatAuthState) nodes.wechatAuthState.textContent = text;
  if (nodes.wechatAuthBox) nodes.wechatAuthBox.dataset.authorized = authorized ? "true" : "false";
}

function isWechatAuthError(value) {
  return /授权|登录|token|401|unauthorized|invalid|微信读书/i.test(String(value || ""));
}

function requestWechatAuthAlert(reason, force = false) {
  const now = Date.now();
  const lastAt = Number(localStorage.getItem(RSS_AUTH_ALERT_LAST_KEY) || 0);
  if (now - lastAt < RSS_AUTH_ALERT_COOLDOWN_MS) return;
  localStorage.setItem(RSS_AUTH_ALERT_LAST_KEY, String(now));
  fetch("/api/wechat-auth-alert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason, force })
  }).catch(() => {});
}

function notifyWechatQrDesktop(payload, reason = "公众号授权需要更新") {
  const now = Date.now();
  const lastAt = Number(localStorage.getItem(RSS_AUTH_ALERT_LAST_KEY) || 0);
  if (now - lastAt < RSS_AUTH_ALERT_COOLDOWN_MS) return;
  localStorage.setItem(RSS_AUTH_ALERT_LAST_KEY, String(now));
  fetch("/api/desktop-alert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      key: `wechat-auth-qr:${payload.uuid || Math.floor(now / RSS_AUTH_ALERT_COOLDOWN_MS)}`,
      kind: "公众号授权",
      source: "微信公众号订阅",
      sourceLabel: "微",
      title: "微信公众号授权需要更新",
      body: `${reason}。请用微信扫码，授权后会自动恢复订阅更新。`,
      url: `${location.origin}/rss.html`,
      imageUrl: payload.qrImageUrl || payload.qrUrl || "",
      imagePath: payload.qrImagePath || payload.imagePath || "",
      priority: "扫码授权"
    })
  }).catch(() => {});
}

async function loadWechatAuthStatus(force = false) {
  if (!nodes.wechatAuthState) return;
  try {
    const response = await fetch(`/api/wechat-account-status${force ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "授权状态检查失败");
    const count = Number(payload.activeCount || 0);
    const hasWechatSources = rssState.sources.some((source) => source.type === "wechat");
    if (payload.needsAuth || (!payload.authorized && hasWechatSources)) {
      setWechatAuthState(payload.invalidCount ? "授权已失效" : "授权需更新", false);
      beginWechatLogin({ silent: true, desktop: false, reason: "公众号授权已失效或不可用" });
    } else {
      setWechatAuthState(count ? `已授权 ${count} 个账号` : "未授权", count > 0);
    }
  } catch (error) {
    setWechatAuthState("授权状态异常", false);
  }
}

async function syncWechatAuthorizedFromCache() {
  try {
    const response = await fetch("/api/wechat-account-status", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.authorized) return false;
    const name = payload.accounts?.find((item) => item?.valid)?.name || "微信读书账号";
    setWechatAuthState(`已授权 ${name}`, true);
    if (nodes.wechatQrPanel) nodes.wechatQrPanel.hidden = true;
    if (nodes.wechatQrHint) nodes.wechatQrHint.textContent = "授权已恢复";
    setStatus("公众号授权已恢复", "success");
    wechatLoginPending = false;
    refreshAll({ manual: true });
    return true;
  } catch {
    return false;
  }
}

async function pollWechatLogin(uuid, attempt = 0) {
  if (!uuid || attempt > 300) {
    if (nodes.wechatQrHint) nodes.wechatQrHint.textContent = "二维码已超时，请重新发起扫码授权。";
    setWechatAuthState("二维码已过期", false);
    wechatLoginPending = false;
    return;
  }
  try {
    const response = await fetch("/api/wechat-login-poll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uuid })
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "登录状态检查失败");
    if (payload.status === "authorized") {
      setWechatAuthState(`已授权 ${payload.account?.name || "微信读书账号"}`, true);
      if (nodes.wechatQrPanel) nodes.wechatQrPanel.hidden = true;
      setStatus("公众号授权完成", "success");
      wechatLoginPending = false;
      refreshAll({ manual: true });
      return;
    }
    if (attempt > 0 && attempt % 3 === 0 && await syncWechatAuthorizedFromCache()) {
      return;
    }
    if (nodes.wechatQrHint) nodes.wechatQrHint.textContent = payload.message || "等待扫码确认";
  } catch (error) {
    if (attempt > 0 && attempt % 3 === 0 && await syncWechatAuthorizedFromCache()) {
      return;
    }
    if (nodes.wechatQrHint) nodes.wechatQrHint.textContent = error.message || String(error);
  }
  setTimeout(() => pollWechatLogin(uuid, attempt + 1), 2000);
}

async function beginWechatLogin(options = {}) {
  if (wechatLoginPending) return;
  wechatLoginPending = true;
  try {
    setWechatAuthState("生成二维码", false);
    const response = await fetch("/api/wechat-login-begin", { method: "POST" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "二维码创建失败");
    if (nodes.wechatQrImage) {
      nodes.wechatQrImage.src = payload.qrDataUrl || payload.qrUrl;
      nodes.wechatQrImage.onerror = () => {
        if (nodes.wechatQrHint) nodes.wechatQrHint.textContent = "二维码加载失败，请重新点击扫码授权。";
      };
    }
    if (nodes.wechatQrPanel) nodes.wechatQrPanel.hidden = false;
    if (nodes.wechatQrHint) nodes.wechatQrHint.textContent = "用微信扫码确认，授权后自动刷新订阅。";
    setWechatAuthState("等待扫码", false);
    if (options.desktop !== false) {
      notifyWechatQrDesktop(payload, options.reason || "公众号授权已失效或不可用");
    }
    pollWechatLogin(payload.uuid);
  } catch (error) {
    wechatLoginPending = false;
    setWechatAuthState("授权失败", false);
    if (!options.silent) setStatus(error.message || String(error), "error");
  }
}

function tickClock() {
  if (!nodes.clock) return;
  nodes.clock.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function normalizeUrl(value) {
  const url = String(value || "").trim();
  if (!/^https?:\/\//i.test(url)) throw new Error("请输入 http 或 https 开头的链接");
  return url;
}

function normalizedWechatTitleKey(source) {
  const candidates = [source?.query, source?.title];
  for (const value of candidates) {
    if (!sourceTitleQuality(value)) continue;
    return String(value).trim().toLowerCase().replace(/\s+/g, "");
  }
  return "";
}

function sourceIdentity(source) {
  if (source.type === "wechat") {
    const titleKey = normalizedWechatTitleKey(source);
    const mpId = String(source.mpId || "");
    return titleKey
      || (isWechatPlatformMpId(mpId, source.platform) ? mpId : "")
      || source.seedUrl
      || source.feedUrl
      || source.title;
  }
  return source.feedUrl;
}

function sourceMergeKey(source) {
  return `${source?.type || "feed"}:${sourceIdentity(source) || source?.id || ""}`;
}

function sourceTitleQuality(value) {
  const text = String(value || "").trim();
  if (!text || ["微信公众号", "微信公众账号", "订阅号", "wechat"].includes(text.toLowerCase())) return 0;
  return text.length;
}

function normalizeClientSource(source) {
  const next = { ...(source || {}) };
  if (next.type === "wechat") {
    if (!sourceTitleQuality(next.title) && sourceTitleQuality(next.query)) next.title = next.query;
    if (!next.query && sourceTitleQuality(next.title)) next.query = next.title;
    if (next.mpId && !isWechatPlatformMpId(next.mpId, next.platform)) next.mpId = "";
    if (!isWechatPlatformMpId(next.mpId, next.platform) && !sourceTitleQuality(next.title) && !sourceTitleQuality(next.query)) return null;
  }
  return next;
}

function sourceFieldQuality(source, field) {
  const value = String(source?.[field] || "").trim();
  if (!value) return 0;
  if (field === "mpId" && !isWechatPlatformMpId(value, source?.platform)) return 0;
  return value.length;
}

function isWechatPlatformMpId(value, platform = "") {
  const text = String(value || "").trim();
  return text.toUpperCase().startsWith("MP_WXS_") || (String(platform || "").toLowerCase() === "wewe-platform" && /^[A-Fa-f0-9]{16,64}$/.test(text));
}

function sourceNeedsWechatRepair(source) {
  if (!source || source.type !== "wechat" || isWechatPlatformMpId(source.mpId, source.platform)) return false;
  const message = String(source.lastMessage || source.error || "");
  if (/缺少真实公众号 ID|文章链接|公众号 ID/.test(message)) return true;
  const items = sourceItems(source.id);
  const hasSeedArticle = /^https?:\/\/mp\.weixin\.qq\.com\/s\//i.test(source.seedUrl || source.siteUrl || "");
  return !items.length && !hasSeedArticle;
}

function mergeSourceRecord(existing, incoming) {
  const existingTime = Math.max(Number(existing.lastFetchedAt) || 0, Number(existing.createdAt) || 0);
  const incomingTime = Math.max(Number(incoming.lastFetchedAt) || 0, Number(incoming.createdAt) || 0);
  const base = incomingTime >= existingTime ? { ...existing, ...incoming } : { ...incoming, ...existing };
  if (existing.id && incoming.id && existing.id !== incoming.id) {
    const existingCount = sourceItems(existing.id).length;
    const incomingCount = sourceItems(incoming.id).length;
    if (existingCount >= incomingCount) base.id = existing.id;
  }
  if (sourceTitleQuality(existing.title) > sourceTitleQuality(incoming.title)) base.title = existing.title;
  if (sourceTitleQuality(incoming.title) > sourceTitleQuality(existing.title)) base.title = incoming.title;
  for (const field of ["query", "seedUrl", "siteUrl", "cover", "platform", "etag", "lastModified"]) {
    if (!String(base[field] || "").trim()) base[field] = existing[field] || incoming[field] || "";
  }
  if (sourceFieldQuality(existing, "mpId") > sourceFieldQuality(incoming, "mpId")) base.mpId = existing.mpId;
  if (sourceFieldQuality(incoming, "mpId") > sourceFieldQuality(existing, "mpId")) base.mpId = incoming.mpId;
  if (base.mpId && !isWechatPlatformMpId(base.mpId, base.platform)) base.mpId = "";
  return base;
}

function mergeSources(...groups) {
  const merged = new Map();
  for (const sources of groups) {
    for (const rawSource of Array.isArray(sources) ? sources : []) {
      const source = normalizeClientSource(rawSource);
      if (!source) continue;
      const key = sourceMergeKey(source);
      if (!key || key.endsWith(":")) continue;
      const existing = merged.get(key);
      if (!existing) {
        merged.set(key, source);
        continue;
      }
      merged.set(key, mergeSourceRecord(existing, source));
    }
  }
  return [...merged.values()];
}

function sourcesFingerprint(sources) {
  return JSON.stringify((sources || []).map((source) => ({
    id: source.id,
    type: source.type,
    title: source.title,
    feedUrl: source.feedUrl,
    seedUrl: source.seedUrl,
    query: source.query,
    mpId: source.mpId,
    lastFetchedAt: source.lastFetchedAt,
    lastNewCount: source.lastNewCount,
    lastSkippedExisting: source.lastSkippedExisting
  })));
}

async function persistSourcesToServerNow() {
  try {
    await fetch("/api/rss-sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources: rssState.sources })
    });
  } catch {
    // Local cache remains the immediate fallback; the next save/load will retry the DB sync.
  }
}

function schedulePersistSourcesToServer() {
  clearTimeout(rssSourceSaveTimer);
  rssSourceSaveTimer = setTimeout(persistSourcesToServerNow, 350);
}

async function persistItemsToServerNow() {
  try {
    const response = await fetch("/api/rss-items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: [...rssState.items.values()] })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "RSS items sync failed");
    }
  } catch (error) {
    console.warn("RSS items DB sync failed", error);
    // Browser cache remains the immediate fallback; the next item save will retry the DB sync.
  }
}

function schedulePersistItemsToServer() {
  clearTimeout(rssItemSaveTimer);
  rssItemSaveTimer = setTimeout(persistItemsToServerNow, 500);
}

async function hydrateSourcesFromServer() {
  const localSources = readJson(RSS_SOURCES_KEY, []);
  const before = sourcesFingerprint(rssState.sources);
  try {
    const response = await fetch("/api/rss-sources", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "rss sources sync failed");
    const remoteSources = Array.isArray(payload.sources) ? payload.sources : [];
    const merged = mergeSources(localSources, remoteSources);
    const remoteFingerprint = sourcesFingerprint(remoteSources);
    const mergedFingerprint = sourcesFingerprint(merged);
    if (mergedFingerprint !== before) {
      rssState.sources = merged;
      if (rssState.activeSourceId !== "all" && !rssState.sources.some((source) => source.id === rssState.activeSourceId)) {
        rssState.activeSourceId = "all";
      }
      saveJson(RSS_SOURCES_KEY, rssState.sources);
      requestRenderItems();
    }
    rssSourceHydrated = true;
    if (mergedFingerprint !== remoteFingerprint) {
      await persistSourcesToServerNow();
    }
  } catch {
    rssSourceHydrated = true;
  }
}

async function hydrateItemsFromServer() {
  try {
    const response = await fetch("/api/rss-items", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "rss items sync failed");
    const remoteItems = Array.isArray(payload.items) ? payload.items : [];
    const localItemCount = rssState.items.size;
    let changed = false;
    for (const rawItem of remoteItems) {
      const sourceId = String(rawItem.sourceId || "").trim();
      if (!sourceId) continue;
      const key = rawItem.key || itemKey(sourceId, rawItem);
      if (!key || rssState.items.has(key)) continue;
      rssState.items.set(key, { ...rawItem, key, sourceId });
      changed = true;
    }
    rssItemsHydrated = true;
    if (changed) {
      persistItems();
      requestRenderItems();
    } else if (localItemCount > remoteItems.length) {
      await persistItemsToServerNow();
    } else if (rssState.items.size) {
      schedulePersistItemsToServer();
    }
  } catch {
    rssItemsHydrated = true;
  }
}

function isLegacyWechatFeed(source) {
  return source.type === "wechat"
    && /^https?:\/\//i.test(source.feedUrl || "")
    && !source.mpId
    && !source.seedUrl
    && !source.query;
}

async function resolveWechatSource(input, title) {
  const response = await fetch("/api/wechat-mp-resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, title })
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || "公众号解析失败");
  return {
    ...payload.source,
    id: uid("wechat"),
    createdAt: Date.now()
  };
}

function sourceInitial(source) {
  if (source.type === "wechat") return "微";
  const text = source.title || source.feedUrl || "RSS";
  const chars = Array.from(text.replace(/\s+/g, ""));
  return (chars.slice(0, 2).join("") || "RSS").toUpperCase();
}

function sourceTone(source) {
  if (source.type === "wechat") return "wechat";
  const host = (() => {
    try {
      return new URL(source.feedUrl).hostname;
    } catch {
      return "";
    }
  })();
  if (/crypto|coin|block|chain|btc|eth/i.test(host)) return "crypto";
  return "feed";
}

function formatDate(value) {
  if (!value) return "--";
  return dateTimeFormatter.format(new Date(value)).replace(/\//g, "-");
}

function relativeDate(value) {
  if (!value) return "未知时间";
  const diff = Date.now() - value;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}天前`;
  return formatDate(value);
}

function itemKey(sourceId, item) {
  return `${sourceId}|${item.id || item.url || item.title}`;
}

function sourceItems(sourceId = "all") {
  const list = [...rssState.items.values()];
  return sourceId === "all" ? list : list.filter((item) => item.sourceId === sourceId);
}

function filteredItems() {
  const query = rssState.query.toLowerCase();
  return sourceItems(rssState.activeSourceId)
    .filter((item) => {
      if (rssState.viewFilter === "unread" && rssState.read.has(item.key)) return false;
      if (rssState.viewFilter === "wechat" && item.sourceType !== "wechat") return false;
      if (!query) return true;
      return [item.title, item.summary, item.sourceTitle, item.author].join(" ").toLowerCase().includes(query);
    })
    .sort((a, b) => (b.publishedAt || 0) - (a.publishedAt || 0));
}

function persistSources() {
  saveJson(RSS_SOURCES_KEY, rssState.sources);
  if (rssSourceHydrated) schedulePersistSourcesToServer();
}

function persistItems() {
  const seen = new Map();
  const items = [];
  const cutoff = Date.now() - RSS_RETENTION_MS;
  for (const item of [...rssState.items.values()].sort((a, b) => (b.publishedAt || 0) - (a.publishedAt || 0))) {
    if (item.publishedAt && item.publishedAt < cutoff) continue;
    const titleKey = `${item.sourceId || ""}|${String(item.title || "").trim().toLowerCase()}`;
    const canonical = item.title ? titleKey : item.key;
    const existing = seen.get(canonical);
    if (existing) {
      if (!existing.summary && item.summary) existing.summary = item.summary;
      if (!existing.url && item.url) existing.url = item.url;
      if (!existing.image && item.image) existing.image = item.image;
      continue;
    }
    seen.set(canonical, item);
    items.push(item);
    if (items.length >= RSS_MAX_STORED_ITEMS) break;
  }
  rssState.items = new Map(items.map((item) => [item.key, item]));
  saveJson(RSS_ITEMS_KEY, items);
  if (rssItemsHydrated) schedulePersistItemsToServer();
}

function pruneStoredItems() {
  persistItems();
  const validKeys = new Set(rssState.items.keys());
  rssState.read = new Set([...rssState.read].filter((key) => validKeys.has(key)).slice(-RSS_MAX_STORED_ITEMS));
  persistRead();
}

function requestRenderItems() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    renderItems();
  });
}

function persistRead() {
  saveJson(RSS_READ_KEY, [...rssState.read].slice(-RSS_MAX_STORED_ITEMS));
}

function recentAlertKeys() {
  const cutoff = Date.now() - RSS_RETENTION_MS;
  const saved = readJson(RSS_ALERT_KEY, []);
  return new Map(
    saved
      .filter((item) => item && item.time >= cutoff && item.key)
      .map((item) => [item.key, item.time])
  );
}

function persistAlertKeys(map) {
  const cutoff = Date.now() - RSS_RETENTION_MS;
  const items = [...map.entries()]
    .filter(([, time]) => time >= cutoff)
    .slice(-RSS_MAX_STORED_ITEMS)
    .map(([key, time]) => ({ key, time }));
  saveJson(RSS_ALERT_KEY, items);
}

function alertKey(source, item) {
  return `${source.id}|${item.id || item.url || item.title}`;
}

function shouldAlertItem(item) {
  const published = Number(item.publishedAt) || Date.now();
  return published >= Date.now() - RSS_ALERT_WINDOW_MS;
}

function sendRssAlerts(source, items) {
  const fresh = (items || []).filter(shouldAlertItem);
  if (!fresh.length) return;
  const seen = recentAlertKeys();
  let changed = false;
  for (const item of fresh) {
    const key = alertKey(source, item);
    if (seen.has(key)) continue;
    seen.set(key, Date.now());
    changed = true;
    fetch("/api/desktop-alert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        key,
        kind: source.type === "wechat" ? "公众号更新" : "RSS更新",
        source: source.title || item.sourceTitle || "RSS订阅",
        sourceLabel: source.type === "wechat" ? "微" : "RSS",
        title: item.title || "新的订阅内容",
        body: item.summary || item.author || "",
        url: item.url || "",
        time: item.publishedAt || Date.now(),
        priority: "新增",
        sound: true
      })
    }).catch(() => {});
  }
  if (changed) persistAlertKeys(seen);
}

function loadAutoSettings() {
  const saved = readJson(RSS_AUTO_KEY, {});
  const now = Date.now();
  rssState.auto.enabled = saved.enabled !== false;
  rssState.auto.intervalMs = Number(saved.intervalMs) || 180000;
  rssState.auto.lastRunAt = Number(saved.lastRunAt) || 0;
  rssState.auto.nextAt = Number(saved.nextAt) || 0;
  if (!rssState.auto.nextAt || rssState.auto.nextAt <= now) {
    rssState.auto.nextAt = Math.max(now + rssState.auto.intervalMs, rssState.auto.lastRunAt + rssState.auto.intervalMs);
  }
}

function persistAutoSettings() {
  saveJson(RSS_AUTO_KEY, {
    enabled: rssState.auto.enabled,
    intervalMs: rssState.auto.intervalMs,
    nextAt: rssState.auto.nextAt,
    lastRunAt: rssState.auto.lastRunAt
  });
}

function loadState() {
  rssState.sources = mergeSources(readJson(RSS_SOURCES_KEY, []));
  const items = readJson(RSS_ITEMS_KEY, []);
  rssState.items = new Map(items.map((item) => [item.key, item]));
  rssState.read = new Set(readJson(RSS_READ_KEY, []));
  pruneStoredItems();
  loadAutoSettings();
}

function updateMetrics() {
  const allItems = [...rssState.items.values()];
  const unread = allItems.filter((item) => !rssState.read.has(item.key)).length;
  if (nodes.sourceCount) nodes.sourceCount.textContent = String(rssState.sources.length);
  if (nodes.itemCount) nodes.itemCount.textContent = String(allItems.length);
  if (nodes.unreadCount) nodes.unreadCount.textContent = String(unread);
  if (nodes.wechatCount) nodes.wechatCount.textContent = String(rssState.sources.filter((source) => source.type === "wechat").length);
  if (nodes.allCount) nodes.allCount.textContent = `${allItems.length} 条内容`;
}

function formatCountdown(ms) {
  if (!rssState.auto.enabled) return "自动更新已关闭";
  if (!rssState.sources.length) return "添加订阅后自动更新";
  if (rssState.refreshingAll) return "静默同步";
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes <= 0) return `下次 ${rest}秒`;
  return `下次 ${minutes}:${String(rest).padStart(2, "0")}`;
}

function updateAutoUi() {
  if (nodes.autoToggle) nodes.autoToggle.checked = rssState.auto.enabled;
  if (nodes.autoInterval) nodes.autoInterval.value = String(rssState.auto.intervalMs);
  if (nodes.nextRefresh) nodes.nextRefresh.textContent = formatCountdown(rssState.auto.nextAt - Date.now());
}

function clearAutoTimer() {
  if (rssState.auto.timer) {
    clearTimeout(rssState.auto.timer);
    rssState.auto.timer = null;
  }
}

function scheduleAutoRefresh(resetNext = true) {
  clearAutoTimer();
  if (!rssState.auto.enabled || !rssState.sources.length) {
    rssState.auto.nextAt = 0;
    persistAutoSettings();
    updateAutoUi();
    return;
  }
  if (resetNext || !rssState.auto.nextAt || rssState.auto.nextAt <= Date.now()) {
    rssState.auto.nextAt = Date.now() + rssState.auto.intervalMs;
  }
  persistAutoSettings();
  const delay = Math.max(1000, rssState.auto.nextAt - Date.now());
  rssState.auto.timer = setTimeout(() => {
    refreshAll({ auto: true });
  }, delay);
  updateAutoUi();
}

function renderSources() {
  if (nodes.allSource) {
    nodes.allSource.classList.toggle("active", rssState.activeSourceId === "all");
  }
  if (!nodes.sourceList) return;
  if (!rssState.sources.length) {
    nodes.sourceList.innerHTML = `
      <div class="rss-source-empty">
        <b>还没有订阅源</b>
        <span>添加普通 RSS，或直接订阅微信公众号。</span>
      </div>
    `;
    return;
  }

  nodes.sourceList.innerHTML = rssState.sources.map((source) => {
    const count = sourceItems(source.id).length;
    const unread = sourceItems(source.id).filter((item) => !rssState.read.has(item.key)).length;
    const isLoading = rssState.loading.has(source.id);
    const isActive = rssState.activeSourceId === source.id;
    const needsRepair = sourceNeedsWechatRepair(source);
    const status = source.error ? "异常" : isLoading ? "同步" : needsRepair ? "需文章链接修复" : `${unread} 未读`;
    return `
      <div class="rss-source-shell ${isActive ? "active" : ""}">
        <button class="rss-source-row" type="button" data-source-id="${escapeHtml(source.id)}">
          <span class="rss-source-icon ${sourceTone(source)}">${escapeHtml(sourceInitial(source))}</span>
          <span>
            <b>${escapeHtml(source.title || "未命名订阅")}</b>
            <em>${escapeHtml(status)} · ${count} 条</em>
          </span>
        </button>
        <button class="rss-source-remove" type="button" data-remove-source="${escapeHtml(source.id)}" aria-label="删除订阅">×</button>
      </div>
    `;
  }).join("");
}

function renderReaderHeader() {
  const active = rssState.sources.find((source) => source.id === rssState.activeSourceId);
  if (nodes.currentScope) {
    nodes.currentScope.textContent = active ? "订阅源 /" : "全部订阅 /";
  }
  if (nodes.readerTitle) {
    nodes.readerTitle.textContent = active ? active.title : "聚合信息流";
  }
}

function renderItems() {
  updateMetrics();
  renderReaderHeader();
  renderSources();
  if (!nodes.itemList) return;
  const items = filteredItems();
  if (!items.length) {
    const loading = rssState.loading.size > 0;
    nodes.itemList.innerHTML = `<div class="loading-panel">${loading ? "暂无新内容。" : "没有匹配的内容。"}</div>`;
    return;
  }

  const visibleItems = items.slice(0, RSS_RENDER_LIMIT);
  const moreHint = items.length > visibleItems.length
    ? `<div class="loading-panel">已显示最近 ${RSS_RENDER_LIMIT} 条，继续缩小搜索或切换订阅源可查看更多。</div>`
    : "";

  nodes.itemList.innerHTML = visibleItems.map((item) => {
    const unread = !rssState.read.has(item.key);
    const href = item.url || "#";
    return `
      <a class="rss-article-row ${unread ? "unread" : "read"}" href="${escapeHtml(href)}" target="_blank" rel="noreferrer" data-item-key="${escapeHtml(item.key)}">
        <span class="rss-unread-dot" aria-hidden="true"></span>
        <span class="rss-article-main">
          <b>${escapeHtml(item.title || "未命名内容")}</b>
          ${item.summary ? `<em>${escapeHtml(item.summary)}</em>` : ""}
        </span>
        <span class="rss-article-source">
          <i class="${item.sourceType === "wechat" ? "wechat" : ""}">${escapeHtml(item.sourceType === "wechat" ? "公众号" : "RSS")}</i>
          ${escapeHtml(item.sourceTitle || "")}
        </span>
        <time datetime="${item.publishedAt ? new Date(item.publishedAt).toISOString() : ""}">
          <b>${escapeHtml(relativeDate(item.publishedAt))}</b>
          <em>${escapeHtml(formatDate(item.publishedAt))}</em>
        </time>
      </a>
    `;
  }).join("") + moreHint;
}

function sourceCheckpoint(source) {
  const items = sourceItems(source.id).sort((a, b) => (b.publishedAt || 0) - (a.publishedAt || 0));
  const knownIds = [];
  const seen = new Set();
  let since = 0;
  for (const item of items) {
    since = Math.max(since, Number(item.publishedAt) || 0);
    for (const value of [item.id, item.url, item.title]) {
      const key = String(value || "").trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      knownIds.push(key);
      if (knownIds.length >= 500) break;
    }
    if (knownIds.length >= 500) break;
  }
  return {
    knownIds,
    since,
    lastFetchedAt: source.lastFetchedAt || 0,
    etag: source.etag || "",
    lastModified: source.lastModified || "",
    backfill: !items.length,
    fullSync: !items.length
  };
}

async function fetchSource(source, options = {}) {
  const deferRender = options.deferRender === true;
  const quietStatus = options.quietStatus === true;
  const sourceStatus = (text, mode = "normal") => {
    source.lastMessage = text;
    source.lastStatusMode = mode;
    if (!quietStatus) setStatus(text, mode);
  };
  rssState.loading.add(source.id);
  source.error = "";
  if (!deferRender) requestRenderItems();
  try {
    const legacyWechat = isLegacyWechatFeed(source);
    const endpoint = source.type === "wechat" && !legacyWechat ? "/api/wechat-mp-fetch" : "/api/rss-fetch";
    const checkpoint = sourceCheckpoint(source);
    const body = source.type === "wechat" && !legacyWechat
      ? { source, ...checkpoint, backfill: options.fullSync || checkpoint.backfill, fullSync: options.fullSync || checkpoint.fullSync }
      : { url: source.feedUrl, ...checkpoint };
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "订阅源更新失败");
    if (source.type === "wechat" && sourceTitleQuality(payload.feed?.title)) {
      source.title = payload.feed.title;
    } else {
      source.title = source.title || payload.feed?.title || "未命名订阅";
    }
    source.siteUrl = payload.feed?.siteUrl || source.siteUrl || "";
    source.description = payload.feed?.description || source.description || "";
    source.cover = payload.feed?.cover || source.cover || "";
    source.feedUrl = source.feedUrl || payload.feed?.feedUrl || "";
    const feedId = payload.feed?.id || "";
    const feedPlatform = payload.feed?.platform || source.platform || "";
    source.platform = feedPlatform;
    if (source.type === "wechat") {
      source.mpId = isWechatPlatformMpId(feedId, feedPlatform) ? feedId : (isWechatPlatformMpId(source.mpId, source.platform) ? source.mpId : "");
    } else {
      source.mpId = feedId || source.mpId || "";
    }
    source.etag = payload.etag || source.etag || "";
    source.lastModified = payload.lastModified || source.lastModified || "";
    source.lastFetchedAt = payload.fetchedAt || Date.now();
    source.lastNewCount = (payload.items || []).length;
    source.lastSkippedExisting = payload.skippedExisting || 0;
    source.error = "";
    const newItems = [];
    for (const item of payload.items || []) {
      const key = itemKey(source.id, item);
      if (!rssState.items.has(key)) newItems.push(item);
      rssState.items.set(key, {
        ...item,
        key,
        sourceId: source.id,
        sourceType: source.type,
        sourceTitle: source.title || item.sourceTitle || payload.feed?.title || "RSS",
        fetchedAt: payload.fetchedAt || Date.now()
      });
    }
    if (!checkpoint.backfill || options.alertBackfill) {
      sendRssAlerts(source, newItems);
    }
    if (source.query || payload.feed?.title) {
      source.query = source.query || payload.feed?.title || "";
    }
    const newCount = (payload.items || []).length;
    const skipNote = payload.skippedExisting ? `，跳过 ${payload.skippedExisting} 条旧内容` : "";
    const okMessage = payload.notModified
      ? `${source.title} 没有变化`
      : newCount
      ? `已更新 ${source.title}，新增 ${newCount} 条${skipNote}`
      : `${source.title} 暂无新内容${skipNote}`;
    const hasItems = (payload.items || []).length > 0;
    const statusText = hasItems ? (payload.notice || okMessage) : (payload.warning || payload.notice || okMessage);
    const statusMode = hasItems ? "success" : (payload.warning ? "error" : "success");
    sourceStatus(statusText, statusMode);
  } catch (error) {
    source.error = error.message || String(error);
    if (source.type === "wechat" && isWechatAuthError(source.error)) {
      requestWechatAuthAlert(source.error, true);
      loadWechatAuthStatus(true);
    }
    sourceStatus(source.error, "error");
  } finally {
    rssState.loading.delete(source.id);
    if (!deferRender) {
      persistSources();
      persistItems();
      requestRenderItems();
    }
  }
}

function applyServerRefreshPayload(payload, options = {}) {
  const remoteSources = Array.isArray(payload.sources) ? payload.sources : [];
  const remoteItems = Array.isArray(payload.items) ? payload.items : [];
  if (remoteSources.length) {
    rssState.sources = mergeSources(rssState.sources, remoteSources);
  }
  const beforeKeys = new Set(rssState.items.keys());
  const nextItems = new Map();
  const newItemsBySource = new Map();
  for (const rawItem of remoteItems) {
    const sourceId = String(rawItem.sourceId || "").trim();
    if (!sourceId) continue;
    const key = rawItem.key || itemKey(sourceId, rawItem);
    const item = { ...rawItem, key, sourceId };
    nextItems.set(key, item);
    if (!beforeKeys.has(key)) {
      if (!newItemsBySource.has(sourceId)) newItemsBySource.set(sourceId, []);
      newItemsBySource.get(sourceId).push(item);
    }
  }
  if (nextItems.size) {
    rssState.items = nextItems;
  }
  if (rssState.activeSourceId !== "all" && !rssState.sources.some((source) => source.id === rssState.activeSourceId)) {
    rssState.activeSourceId = "all";
  }
  persistSources();
  persistItems();
  requestRenderItems();
  if (!options.skipAlerts) {
    for (const [sourceId, items] of newItemsBySource.entries()) {
      const source = rssState.sources.find((item) => item.id === sourceId);
      if (source) sendRssAlerts(source, items);
    }
  }
  return [...newItemsBySource.values()].reduce((sum, items) => sum + items.length, 0);
}

async function refreshAll(options = {}) {
  if (!rssState.sources.length) {
    setStatus("先添加订阅源", "normal");
    scheduleAutoRefresh();
    return;
  }
  if (rssState.refreshingAll) return;
  rssState.refreshingAll = true;
  setStatus(options.auto ? "自动同步已启用" : "同步已触发");
  try {
    try {
      const response = await fetch("/api/rss-refresh-all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fullSync: options.fullSync === true })
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "server rss refresh failed");
      const added = applyServerRefreshPayload(payload, { skipAlerts: false });
      rssState.auto.lastRunAt = Date.now();
      rssState.auto.nextAt = Date.now() + rssState.auto.intervalMs;
      persistAutoSettings();
      if (payload.stats?.authRequired) {
        setStatus("微信公众号授权已失效，扫码授权后再更新", "error");
        loadWechatAuthStatus(true);
        return;
      }
      const errors = Number(payload.stats?.errors || 0);
      const suffix = errors ? ` · ${errors} sources need repair` : "";
      setStatus(`${options.auto ? "自动" : "手动"}更新完成 · 新增 ${added} 条${suffix}`, errors ? "normal" : "success");
      return;
    } catch (serverError) {
      // Fall back to the browser updater if the local refresh endpoint is temporarily unavailable.
    }
    const queue = [...rssState.sources];
    const workers = Array.from({ length: Math.min(3, queue.length) }, async () => {
      while (queue.length) {
        const source = queue.shift();
        if (!source) continue;
        await fetchSource(source, { ...options, deferRender: true, quietStatus: true });
      }
    });
    await Promise.all(workers);
    persistSources();
    persistItems();
    requestRenderItems();
    rssState.auto.lastRunAt = Date.now();
    rssState.auto.nextAt = Date.now() + rssState.auto.intervalMs;
    persistAutoSettings();
    setStatus(`${options.auto ? "自动" : "手动"}更新完成 · ${rssState.sources.length} 个订阅`, "success");
  } finally {
    rssState.refreshingAll = false;
    scheduleAutoRefresh(false);
  }
}

function addSource(source) {
  const identity = sourceIdentity(source);
  const same = rssState.sources.find((item) => sourceIdentity(item) === identity);
  if (same) {
    rssState.activeSourceId = same.id;
    setStatus("订阅已存在，已切换到该源", "normal");
    renderItems();
    return fetchSource(same);
  }
  rssState.sources.unshift(source);
  rssState.activeSourceId = source.id;
  persistSources();
  renderItems();
  scheduleAutoRefresh(true);
  return fetchSource(source);
}

function removeSource(sourceId) {
  rssState.sources = rssState.sources.filter((source) => source.id !== sourceId);
  for (const key of [...rssState.items.keys()]) {
    if (rssState.items.get(key)?.sourceId === sourceId) {
      rssState.items.delete(key);
      rssState.read.delete(key);
    }
  }
  if (rssState.activeSourceId === sourceId) rssState.activeSourceId = "all";
  persistSources();
  persistItems();
  persistRead();
  renderItems();
  scheduleAutoRefresh(true);
}

function openModal(type = "feed") {
  rssState.modalType = type;
  nodes.modal.hidden = false;
  setModalType(type);
  nodes.form.reset();
  setTimeout(() => (type === "wechat" ? nodes.wechatInput : nodes.urlInput)?.focus(), 20);
}

function closeModal() {
  nodes.modal.hidden = true;
}

function setModalType(type) {
  rssState.modalType = type;
  nodes.feedFields.hidden = type !== "feed";
  nodes.wechatFields.hidden = type !== "wechat";
  nodes.typeTabs?.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.rssType === type);
  });
}

function exportOpml() {
  const outlines = rssState.sources.map((source) => {
    const title = escapeHtml(source.title || source.feedUrl);
    const url = escapeHtml(source.type === "wechat" ? (source.seedUrl || source.query || source.feedUrl) : source.feedUrl);
    const category = source.type === "wechat" ? "微信公众号" : "RSS";
    return `    <outline text="${title}" title="${title}" type="rss" xmlUrl="${url}" category="${category}" />`;
  }).join("\n");
  const content = `<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n  <head><title>星云社 RSS 订阅</title></head>\n  <body>\n${outlines}\n  </body>\n</opml>`;
  const blob = new Blob([content], { type: "text/xml;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `xingyunshe-rss-${Date.now()}.opml`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function importOpml(file) {
  if (!file) return;
  const text = await file.text();
  const doc = new DOMParser().parseFromString(text, "text/xml");
  const outlines = [...doc.querySelectorAll("outline[xmlUrl]")];
  let added = 0;
  for (const outline of outlines) {
    const feedUrl = outline.getAttribute("xmlUrl");
    if (!feedUrl || rssState.sources.some((source) => source.feedUrl === feedUrl || source.seedUrl === feedUrl || source.query === feedUrl)) continue;
    const isWechat = /微信|wechat|wewe/i.test(outline.getAttribute("category") || "");
    rssState.sources.push({
      id: uid(isWechat ? "wechat" : "rss"),
      type: isWechat ? "wechat" : "feed",
      title: outline.getAttribute("title") || outline.getAttribute("text") || "",
      feedUrl: isWechat ? `wechat-mp://${stableSourceId(feedUrl)}` : feedUrl,
      seedUrl: isWechat && /^https?:\/\//i.test(feedUrl) ? feedUrl : "",
      query: isWechat && !/^https?:\/\//i.test(feedUrl) ? feedUrl : "",
      mpId: isWechat ? stableSourceId(feedUrl) : "",
      createdAt: Date.now()
    });
    added += 1;
  }
  persistSources();
  renderItems();
  setStatus(`已导入 ${added} 个订阅`, added ? "success" : "normal");
}

function bindEvents() {
  nodes.addBtn?.addEventListener("click", () => openModal("feed"));
  nodes.closeModal?.addEventListener("click", closeModal);
  nodes.cancelBtn?.addEventListener("click", closeModal);
  nodes.modal?.addEventListener("click", (event) => {
    if (event.target === nodes.modal) closeModal();
  });
  nodes.typeTabs?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-rss-type]");
    if (button) setModalType(button.dataset.rssType);
  });
  nodes.form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const type = rssState.modalType;
      const title = nodes.titleInput.value.trim();
      closeModal();
      if (type === "wechat") {
        setStatus("解析公众号");
        const source = await resolveWechatSource(nodes.wechatInput.value.trim(), title);
        addSource(source);
      } else {
        addSource({
          id: uid("rss"),
          type,
          title,
          feedUrl: normalizeUrl(nodes.urlInput.value),
          createdAt: Date.now()
        });
      }
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
  });
  nodes.allSource?.addEventListener("click", () => {
    rssState.activeSourceId = "all";
    renderItems();
  });
  nodes.sourceList?.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-remove-source]");
    if (removeButton) {
      removeSource(removeButton.dataset.removeSource);
      return;
    }
    const sourceButton = event.target.closest("[data-source-id]");
    if (sourceButton) {
      rssState.activeSourceId = sourceButton.dataset.sourceId;
      renderItems();
    }
  });
  nodes.search?.addEventListener("input", () => {
    rssState.query = nodes.search.value.trim();
    renderItems();
  });
  nodes.viewFilter?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-filter]");
    if (!button) return;
    rssState.viewFilter = button.dataset.filter;
    nodes.viewFilter.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    renderItems();
  });
  nodes.itemList?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-item-key]");
    if (!row) return;
    rssState.read.add(row.dataset.itemKey);
    persistRead();
    row.classList.remove("unread");
    row.classList.add("read");
    updateMetrics();
    renderSources();
  });
  nodes.refreshAll?.addEventListener("click", () => refreshAll({ manual: true }));
  nodes.wechatLogin?.addEventListener("click", beginWechatLogin);
  nodes.autoToggle?.addEventListener("change", () => {
    rssState.auto.enabled = nodes.autoToggle.checked;
    persistAutoSettings();
    scheduleAutoRefresh(true);
  });
  nodes.autoInterval?.addEventListener("change", () => {
    rssState.auto.intervalMs = Number(nodes.autoInterval.value) || 180000;
    persistAutoSettings();
    scheduleAutoRefresh(true);
  });
  nodes.exportOpml?.addEventListener("click", exportOpml);
  nodes.importBtn?.addEventListener("click", () => nodes.opmlInput?.click());
  nodes.opmlInput?.addEventListener("change", () => importOpml(nodes.opmlInput.files?.[0]));
}

async function init() {
  tickClock();
  setInterval(tickClock, 1000);
  loadState();
  bindEvents();
  renderItems();
  await hydrateSourcesFromServer();
  await hydrateItemsFromServer();
  loadWechatAuthStatus(true);
  setInterval(() => loadWechatAuthStatus(false), 60_000);
  updateAutoUi();
  setInterval(updateAutoUi, 1000);
  scheduleAutoRefresh(false);
}

init();
