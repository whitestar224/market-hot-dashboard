(() => {
  const CACHE_KEY = "xingyunshe:xwatch:feed:v5";
  const SOURCES_KEY = "xingyunshe:xwatch:sources:v1";
  const FRESH_WINDOW_MS = 24 * 60 * 60 * 1000;

  const state = {
    sources: [],
    items: [],
    sourceStates: [],
    provider: "--",
    hasToken: false,
    query: "",
    activeSource: "all",
    saving: false,
    editingId: "",
    user: null,
    translations: {},
    translationTexts: {},
    translationLoading: new Set(),
    feedLoading: false,
    stream: null,
    streamReady: false
  };

  const $ = (id) => document.getElementById(id);
  const nodes = {
    clock: $("clock"),
    status: $("xwatchStatus"),
    sourceCount: $("xwatchSourceCount"),
    postCount: $("xwatchPostCount"),
    freshCount: $("xwatchFreshCount"),
    provider: $("xwatchProvider"),
    form: $("xwatchForm"),
    handle: $("xwatchHandle"),
    name: $("xwatchName"),
    keywords: $("xwatchKeywords"),
    submit: $("xwatchSubmit"),
    cancelEdit: $("xwatchCancelEdit"),
    sourceList: $("xwatchSourceList"),
    save: $("xwatchSave"),
    timeline: $("xwatchTimeline"),
    refresh: $("xwatchRefresh"),
    search: $("xwatchSearch"),
    sourceFilter: $("xwatchSourceFilter")
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[char]);
  }

  function normalizeHandle(value) {
    let raw = String(value || "").trim();
    if (/^https?:\/\//i.test(raw)) {
      try {
        const url = new URL(raw);
        raw = url.pathname.split("/").filter(Boolean)[0] || "";
      } catch {
        raw = "";
      }
    }
    return raw.replace(/^@+/, "").replace(/[^A-Za-z0-9_]/g, "").slice(0, 15);
  }

  function sourceId(handle) {
    return `x:${normalizeHandle(handle).toLowerCase()}`;
  }

  function fallbackAvatar(handle) {
    const normalized = normalizeHandle(handle);
    return normalized ? `https://unavatar.io/x/${encodeURIComponent(normalized)}` : "";
  }

  function avatarMarkup(avatar, initials) {
    const safeInitials = escapeHtml((initials || "X").slice(0, 2).toUpperCase());
    if (!avatar) return `<span>${safeInitials}</span>`;
    return `
      <img src="${escapeHtml(avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer"
        onerror="this.style.display='none';this.nextElementSibling.hidden=false" />
      <span hidden>${safeInitials}</span>
    `;
  }

  function splitKeywords(value) {
    if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean).slice(0, 16);
    return String(value || "")
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 16);
  }

  function timeLabel(value) {
    const numeric = Number(value);
    const time = Number.isFinite(numeric) && numeric > 0 ? numeric : Date.parse(value);
    if (!Number.isFinite(time) || time <= 0) return "--";
    const diff = Date.now() - time;
    if (diff >= 0 && diff < 60_000) return "刚刚";
    if (diff >= 0 && diff < 3_600_000) return `${Math.max(1, Math.floor(diff / 60_000))}分钟前`;
    if (diff >= 0 && diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时前`;
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).format(new Date(time));
  }

  function formatMetric(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "";
    if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
    if (number >= 10_000) return `${(number / 10_000).toFixed(1)}万`;
    if (number >= 1000) return `${(number / 1000).toFixed(1)}K`;
    return String(number);
  }

  function looksEnglish(value) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (text.length < 12) return false;
    const latin = (text.match(/[A-Za-z]/g) || []).length;
    const cjk = (text.match(/[\u3400-\u9fff]/g) || []).length;
    const words = (text.match(/[A-Za-z]{2,}/g) || []).length;
    return latin >= 18 && words >= 5 && latin >= Math.max(18, cjk * 4);
  }

  function translationKey(item, scope) {
    return `${item.id || item.url || item.publishedAt || "x"}:${scope}`;
  }

  function translationControls(item, scope, text) {
    if (!looksEnglish(text)) return "";
    const key = translationKey(item, scope);
    state.translationTexts[key] = String(text || "");
    const entry = state.translations[key] || {};
    const loading = state.translationLoading.has(key);
    const label = loading ? "翻译中" : entry.text ? "已翻译" : "翻译";
    return `
      <div class="xwatch-translate">
        <button type="button" data-action="translate" data-translation-key="${escapeHtml(key)}" ${loading ? "disabled" : ""}>${label}</button>
        ${entry.error ? `<span class="xwatch-translate-error">${escapeHtml(entry.error)}</span>` : ""}
      </div>
      ${entry.text ? `<div class="xwatch-translation"><b>译文</b><span>${escapeHtml(entry.text)}</span></div>` : ""}
    `;
  }

  async function translateXText(key) {
    const text = state.translationTexts[key];
    if (!text || state.translationLoading.has(key)) return;
    state.translationLoading.add(key);
    state.translations[key] = { ...(state.translations[key] || {}), error: "" };
    renderTimeline();
    try {
      const payload = await apiJson("/api/x-kol-translate", {
        method: "POST",
        body: JSON.stringify({ text })
      });
      if (!payload.ok) throw new Error(payload.error || "翻译失败");
      state.translations[key] = payload.translation
        ? { text: payload.translation, cached: Boolean(payload.cached) }
        : { error: "这条内容不需要翻译" };
    } catch (error) {
      state.translations[key] = { error: error.message || "翻译失败" };
    } finally {
      state.translationLoading.delete(key);
      renderTimeline();
    }
  }

  function status(text, mode = "ok") {
    if (!nodes.status) return;
    nodes.status.textContent = text;
    nodes.status.dataset.mode = mode;
  }

  function userKey(base) {
    return state.user?.id ? `${base}:user:${state.user.id}` : base;
  }

  function readUserStorage(base, fallbackValue) {
    const key = userKey(base);
    const raw = localStorage.getItem(key);
    if (raw !== null) return raw;
    if (state.user?.role === "admin") {
      const legacy = localStorage.getItem(base);
      if (legacy !== null) {
        return legacy;
      }
    }
    return fallbackValue;
  }

  async function apiJson(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store",
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function loadLocalSources() {
    try {
      const parsed = JSON.parse(readUserStorage(SOURCES_KEY, "[]") || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function saveLocalSources() {
    // X追踪源现在以数据库为准；旧 localStorage 只在首次加载时用于迁移。
  }

  function loadCachedFeed() {
    try {
      const raw = sessionStorage.getItem(userKey(CACHE_KEY));
      if (!raw) return false;
      const cached = JSON.parse(raw);
      if (!cached || Date.now() - Number(cached.savedAt || 0) > 15 * 60 * 1000) return false;
      applyFeedPayload(cached.payload || {}, { cached: true });
      return state.items.length > 0;
    } catch {
      return false;
    }
  }

  function saveCachedFeed(payload) {
    if (!payload || payload.pending || !Array.isArray(payload.items)) return;
    try {
      sessionStorage.setItem(userKey(CACHE_KEY), JSON.stringify({
        savedAt: Date.now(),
        payload: {
          ok: true,
          items: payload.items.slice(0, 240),
          sources: Array.isArray(payload.sources) ? payload.sources : [],
          provider: payload.provider || "--",
          hasToken: Boolean(payload.hasToken),
          updatedAt: payload.updatedAt || Date.now()
        }
      }));
    } catch {
      // 会话缓存只负责加速首屏，空间不足时直接跳过。
    }
  }

  function mergeSources(serverSources, { includeLocal = false } = {}) {
    const fromServer = Array.isArray(serverSources) ? serverSources : [];
    const local = includeLocal ? loadLocalSources() : [];
    const map = new Map();
    [...local, ...fromServer].forEach((source) => {
      const handle = normalizeHandle(source.handle || source.username || source.url);
      if (!handle) return;
      map.set(sourceId(handle), {
        id: sourceId(handle),
        handle,
        displayName: String(source.displayName || source.name || handle).trim() || handle,
        keywords: splitKeywords(source.keywords),
        enabled: source.enabled !== false,
        avatar: source.avatar || source.avatarUrl || fallbackAvatar(handle),
        createdAt: Number(source.createdAt) || Date.now()
      });
    });
    state.sources = [...map.values()];
  }

  async function loadSources() {
    const payload = await apiJson("/api/x-kol-sources");
    const serverSources = Array.isArray(payload.sources) ? payload.sources : [];
    if (!serverSources.length && !payload.exists) {
      const legacySources = loadLocalSources();
      if (legacySources.length) {
        mergeSources(legacySources);
        await saveSources({ silent: true });
      } else {
        mergeSources(serverSources);
      }
    } else {
      mergeSources(serverSources);
    }
    state.hasToken = Boolean(payload.hasToken);
    render();
  }

  async function saveSources({ silent = false } = {}) {
    state.saving = true;
    renderSources();
    try {
      const payload = await apiJson("/api/x-kol-sources", {
        method: "POST",
        body: JSON.stringify({ sources: state.sources })
      });
      mergeSources(payload.sources);
      if (!silent) status("追踪列表已保存");
    } catch (error) {
      status(error.message || "保存失败", "error");
    } finally {
      state.saving = false;
      renderSources();
    }
  }

  async function loadFeed({ refresh = false } = {}) {
    if (state.feedLoading) return;
    state.feedLoading = true;
    if (!refresh && !state.items.length) loadCachedFeed();
    if (!state.items.length) status(refresh ? "后台更新中" : "读取动态中", "loading");
    try {
      const payload = await apiJson(`/api/x-kol-feed${refresh ? "?refresh=1" : ""}`);
      applyFeedPayload(payload);
    } catch (error) {
      status(error.message || "动态读取失败", "error");
      render();
    } finally {
      state.feedLoading = false;
    }
  }

  function applyFeedPayload(payload, { cached = false } = {}) {
    if (!payload || payload.pending) {
      if (!state.items.length) status("实时连接已建立，正在等待上游数据", "loading");
      return;
    }
    const incomingItems = Array.isArray(payload.items) ? payload.items : [];
    if (incomingItems.length || !state.items.length) state.items = incomingItems;
    state.sourceStates = Array.isArray(payload.sources) ? payload.sources : [];
    mergeLiveSourceState(state.sourceStates);
    state.provider = payload.provider || "--";
    state.hasToken = Boolean(payload.hasToken);
    if (!cached) saveCachedFeed(payload);
    const limited = state.sourceStates.some((source) => source.limited);
    const upstreamMode = payload.upstreamMode || "";
    const realtimeLabel = upstreamMode === "official-stream"
      ? "X 实时流"
      : upstreamMode === "official-api"
        ? "实时连接 · X API"
        : upstreamMode === "rss-resilient"
          ? "实时连接 · RSS自动换源"
        : payload.realtime
          ? "实时连接 · RSS回退"
          : limited
            ? "RSS受限"
            : "追踪中";
    const recovering = state.sourceStates.some((source) => source.status === "recovering");
    status(cached ? "缓存内容 · 正在同步" : realtimeLabel, cached || upstreamMode.includes("rss") || limited || recovering ? "warn" : "ok");
    render();
  }

  function connectRealtimeFeed() {
    if (!state.user || typeof EventSource === "undefined") return;
    state.stream?.close();
    const stream = new EventSource("/api/x-kol-stream");
    state.stream = stream;
    stream.addEventListener("open", () => {
      state.streamReady = true;
      status("实时连接", "ok");
    });
    stream.addEventListener("feed", (event) => {
      try {
        applyFeedPayload(JSON.parse(event.data));
      } catch {
        status("实时数据解析失败", "error");
      }
    });
    stream.addEventListener("status", () => {
      if (!state.items.length) status("实时连接已建立，正在等待上游数据", "loading");
    });
    stream.addEventListener("error", () => {
      state.streamReady = false;
      status("实时连接重连中", "warn");
    });
  }

  function sourceState(id) {
    return state.sourceStates.find((source) => source.id === id) || {};
  }

  function mergeLiveSourceState(liveSources) {
    if (!Array.isArray(liveSources) || !liveSources.length) return;
    const liveById = new Map(liveSources.filter(Boolean).map((source) => [source.id, source]));
    state.sources = state.sources.map((source) => {
      const live = liveById.get(source.id);
      if (!live) return { ...source, avatar: source.avatar || fallbackAvatar(source.handle) };
      return {
        ...source,
        avatar: live.avatar || source.avatar || fallbackAvatar(source.handle)
      };
    });
  }

  function renderMetrics() {
    const now = Date.now();
    nodes.sourceCount.textContent = String(state.sources.length);
    nodes.postCount.textContent = String(state.items.length);
    nodes.freshCount.textContent = String(state.items.filter((item) => now - Number(item.publishedAt || 0) <= FRESH_WINDOW_MS).length);
    nodes.provider.textContent = state.hasToken ? "API" : (state.provider === "rss" ? "RSS" : state.provider || "--");
  }

  function renderSourceFilter() {
    const current = state.activeSource;
    nodes.sourceFilter.innerHTML = [
      '<option value="all">全部KOL</option>',
      ...state.sources.map((source) => `<option value="${escapeHtml(source.id)}">${escapeHtml(source.displayName || source.handle)}</option>`)
    ].join("");
    nodes.sourceFilter.value = state.sources.some((source) => source.id === current) ? current : "all";
    state.activeSource = nodes.sourceFilter.value;
  }

  function renderSources() {
    if (!state.sources.length) {
      nodes.sourceList.innerHTML = `
        <div class="xwatch-empty">
          <b>还没有追踪对象</b>
          <span>填入 X 用户名后保存。</span>
        </div>
      `;
      return;
    }
    nodes.sourceList.innerHTML = state.sources.map((source) => {
      const live = sourceState(source.id);
      const statusClass = live.status === "error" ? "error" : live.status === "recovering" ? "recovering" : source.enabled ? "ok" : "muted";
      const displayName = source.displayName || live.displayName || source.handle;
      const avatar = live.avatar || source.avatar || fallbackAvatar(source.handle);
      const initials = (displayName || source.handle || "X").slice(0, 2).toUpperCase();
      const isEditing = source.id === state.editingId;
      return `
        <article class="xwatch-source-row${isEditing ? " is-editing" : ""}" data-source-id="${escapeHtml(source.id)}" data-state="${statusClass}">
          <button class="xwatch-source-avatar" type="button" data-action="filter" title="只看这个KOL">
            ${avatarMarkup(avatar, initials)}
          </button>
          <div class="xwatch-source-main">
            <b>${escapeHtml(displayName)}</b>
            <em>@${escapeHtml(source.handle)} · ${escapeHtml(live.provider || state.provider || "--")} · ${escapeHtml(live.itemsReturned ?? 0)}/${escapeHtml(live.fetchLimit ?? "--")}</em>
            <input data-action="keywords" value="${escapeHtml((source.keywords || []).join(", "))}" placeholder="关注词，不裁剪动态" />
            ${live.error ? `<small>${escapeHtml(live.error)}</small>` : ""}
          </div>
          <label class="xwatch-switch" title="启用/暂停">
            <input data-action="toggle" type="checkbox" ${source.enabled ? "checked" : ""} />
            <span></span>
          </label>
          <div class="xwatch-source-actions">
            <button class="xwatch-edit" type="button" data-action="edit" title="编辑">编辑</button>
            <button class="xwatch-delete" type="button" data-action="delete" title="删除">×</button>
          </div>
        </article>
      `;
    }).join("");
    nodes.save.textContent = state.saving ? "保存中" : "保存";
  }

  function filteredItems() {
    const query = state.query.trim().toLowerCase();
    return state.items.filter((item) => {
      if (state.activeSource !== "all" && item.sourceId !== state.activeSource) return false;
      if (!query) return true;
      return [
        item.text,
        item.title,
        item.sourceName,
        item.handle
      ].join(" ").toLowerCase().includes(query);
    });
  }

  function renderTimeline() {
    const items = filteredItems();
    if (!state.sources.length) {
      nodes.timeline.innerHTML = `
        <div class="empty-state">
          <b>添加KOL后开始追踪</b>
          <span>后台监控会把新动态推送到 Windows 弹窗。</span>
        </div>
      `;
      return;
    }
    if (!items.length) {
      nodes.timeline.innerHTML = `
        <div class="empty-state">
          <b>没有匹配的动态</b>
          <span>可以换一个KOL或关键词。</span>
        </div>
      `;
      return;
    }
    nodes.timeline.innerHTML = items.map((item) => {
      const metrics = item.metrics || {};
      const metricHtml = [
        ["赞", metrics.like],
        ["转", metrics.repost],
        ["评", metrics.reply],
        ["引", metrics.quote],
        ["看", metrics.view]
      ].map(([label, value]) => {
        const formatted = formatMetric(value);
        return formatted ? `<span>${label} ${escapeHtml(formatted)}</span>` : "";
      }).join("");
      const keywordHtml = Array.isArray(item.matchedKeywords) && item.matchedKeywords.length
        ? item.matchedKeywords.map((word) => `<span class="xwatch-keyword">${escapeHtml(word)}</span>`).join("")
        : "";
      const typeLabel = item.entryType && item.entryType !== "tweet" ? item.entryType : "";
      const quote = item.quote && item.quote.text ? item.quote : null;
      const quoteHtml = quote ? `
        <div class="xwatch-quote-card">
        <a class="xwatch-quote-link" href="${escapeHtml(quote.url || item.url || "#")}" target="_blank" rel="noreferrer noopener">
          <div class="xwatch-quote-head">
            <b>${escapeHtml(quote.authorName || (quote.handle ? `@${quote.handle}` : "引用动态"))}</b>
            ${quote.handle ? `<em>@${escapeHtml(quote.handle)}</em>` : ""}
            ${quote.kind ? `<span>${escapeHtml(quote.kind)}</span>` : ""}
          </div>
          <p>${escapeHtml(quote.text)}</p>
        </a>
        ${translationControls(item, "quote", quote.text)}
        </div>
      ` : "";
      const matchingSource = state.sources.find((source) => source.id === item.sourceId);
      const avatar = item.avatar || matchingSource?.avatar || fallbackAvatar(item.handle);
      const initials = (item.sourceName || item.handle || "X").slice(0, 2).toUpperCase();
      return `
        <article class="xwatch-post">
          <div class="xwatch-post-avatar">
            ${avatarMarkup(avatar, initials)}
          </div>
          <div class="xwatch-post-body">
            <header>
              <div>
                <b>${escapeHtml(item.sourceName || item.handle || "KOL")}</b>
                <em>@${escapeHtml(item.handle || "")}</em>
              </div>
              <time>${escapeHtml(timeLabel(item.publishedAt))}</time>
            </header>
            <p>${escapeHtml(item.text || item.title || "")}</p>
            ${translationControls(item, "main", item.text || item.title || "")}
            ${quoteHtml}
            <footer>
              <div class="xwatch-post-metrics">${keywordHtml}${typeLabel ? `<span>${escapeHtml(typeLabel)}</span>` : ""}${metricHtml || "<span>动态</span>"}</div>
              <a href="${escapeHtml(item.url || `https://x.com/${item.handle || ""}`)}" target="_blank" rel="noreferrer noopener">打开X</a>
            </footer>
          </div>
        </article>
      `;
    }).join("");
  }

  function render() {
    renderMetrics();
    renderSourceFilter();
    renderSources();
    renderTimeline();
    renderEditState();
  }

  function updateClock() {
    nodes.clock.textContent = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    }).format(new Date());
  }

  function updateSource(id, patch) {
    state.sources = state.sources.map((source) => source.id === id ? { ...source, ...patch } : source);
    saveLocalSources();
    render();
  }

  function getSource(id) {
    return state.sources.find((source) => source.id === id);
  }

  function renderEditState() {
    const editing = Boolean(state.editingId);
    if (nodes.submit) nodes.submit.textContent = editing ? "保存修改" : "添加追踪";
    if (nodes.cancelEdit) nodes.cancelEdit.hidden = !editing;
    nodes.form?.classList.toggle("is-editing", editing);
  }

  function beginEdit(id) {
    const source = getSource(id);
    if (!source) return;
    state.editingId = id;
    nodes.handle.value = source.handle || "";
    nodes.name.value = source.displayName || "";
    nodes.keywords.value = (source.keywords || []).join(", ");
    render();
    nodes.handle.focus();
  }

  function cancelEdit() {
    state.editingId = "";
    nodes.form.reset();
    render();
  }

  nodes.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const handle = normalizeHandle(nodes.handle.value);
    if (!handle) {
      status("请输入X用户名", "error");
      nodes.handle.focus();
      return;
    }
    const id = sourceId(handle);
    const previous = state.editingId ? getSource(state.editingId) : state.sources.find((source) => source.id === id);
    const next = {
      ...(previous || {}),
      id,
      handle,
      displayName: nodes.name.value.trim() || previous?.displayName || handle,
      keywords: splitKeywords(nodes.keywords.value),
      enabled: previous?.enabled !== false,
      avatar: previous?.handle && normalizeHandle(previous.handle).toLowerCase() === handle.toLowerCase()
        ? (previous.avatar || fallbackAvatar(handle))
        : fallbackAvatar(handle),
      createdAt: Number(previous?.createdAt) || Date.now()
    };
    const filtered = state.editingId ? state.sources.filter((source) => source.id !== state.editingId && source.id !== id) : state.sources.filter((source) => source.id !== id);
    state.sources = [...filtered, next];
    state.editingId = "";
    nodes.form.reset();
    saveLocalSources();
    await saveSources({ silent: true });
    await loadFeed({ refresh: true });
  });

  nodes.timeline.addEventListener("click", (event) => {
    const button = event.target.closest('[data-action="translate"]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    translateXText(button.dataset.translationKey);
  });

  nodes.sourceList.addEventListener("click", async (event) => {
    const row = event.target.closest(".xwatch-source-row");
    if (!row) return;
    const id = row.dataset.sourceId;
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "delete") {
      state.sources = state.sources.filter((source) => source.id !== id);
      if (state.editingId === id) cancelEdit();
      saveLocalSources();
      await saveSources();
      await loadFeed({ refresh: true });
    }
    if (action === "edit") {
      beginEdit(id);
    }
    if (action === "filter") {
      state.activeSource = id;
      render();
    }
  });

  nodes.sourceList.addEventListener("change", async (event) => {
    const row = event.target.closest(".xwatch-source-row");
    if (!row) return;
    const id = row.dataset.sourceId;
    const action = event.target.dataset.action;
    if (action === "toggle") {
      updateSource(id, { enabled: event.target.checked });
      await saveSources({ silent: true });
      await loadFeed({ refresh: true });
    }
    if (action === "keywords") {
      updateSource(id, { keywords: splitKeywords(event.target.value) });
      await saveSources({ silent: true });
      await loadFeed({ refresh: true });
    }
  });

  nodes.save.addEventListener("click", async () => {
    await saveSources();
    await loadFeed({ refresh: true });
  });

  nodes.cancelEdit?.addEventListener("click", cancelEdit);

  nodes.refresh.addEventListener("click", () => loadFeed({ refresh: true }));

  nodes.search.addEventListener("input", (event) => {
    state.query = event.target.value;
    renderTimeline();
  });

  nodes.sourceFilter.addEventListener("change", (event) => {
    state.activeSource = event.target.value;
    renderTimeline();
  });

  async function boot() {
    state.user = window.XingyunAuthReady ? await window.XingyunAuthReady : window.XingyunCurrentUser;
    if (!state.user) return;
    updateClock();
    setInterval(updateClock, 1000);

    mergeSources([]);
    const hasCachedFeed = loadCachedFeed();
    render();
    connectRealtimeFeed();
    void loadSources().catch((error) => status(error.message || "追踪列表读取失败", "error"));
    window.setTimeout(() => {
      if (!state.streamReady || !hasCachedFeed) void loadFeed();
    }, hasCachedFeed ? 1200 : 350);
  }

  boot().catch((error) => {
    status(error.message || "初始化失败", "error");
    render();
  });

  setInterval(() => {
    if (!state.user || document.hidden) return;
    if (!state.streamReady) loadFeed();
  }, 5_000);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.user && (!state.stream || state.stream.readyState === EventSource.CLOSED)) {
      connectRealtimeFeed();
    }
  });
})();
