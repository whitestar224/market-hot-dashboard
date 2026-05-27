(() => {
  const REQUIRED_PAGES = new Set(["todo.html", "xwatch.html", "admin.html"]);
  let currentUser = null;
  let googleEnabled = false;
  let modal = null;
  let modelSettingsModal = null;
  let modelSettingsData = null;
  let modelOptionsCache = new Map();
  let avatarDataUrl = "";
  let emailTimer = null;
  let emailCountdown = 0;
  let accountMenuDocumentBound = false;

  function currentPageName() {
    return (window.location.pathname.split("/").pop() || "index.html").toLowerCase();
  }

  function authRequired() {
    return document.body?.dataset.authRequired === "true" || REQUIRED_PAGES.has(currentPageName());
  }

  function loginHref() {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    return `/login.html?next=${next}`;
  }

  function navNode() {
    return document.querySelector(".page-nav");
  }

  function removeExistingPill() {
    document.querySelectorAll(".auth-session-pill").forEach((node) => node.remove());
    document.querySelectorAll(".auth-account-menu[data-auth-menu]").forEach((node) => node.remove());
    navNode()?.classList.remove("has-auth-menu");
  }

  function closeAccountMenus(except = null) {
    const exceptId = except?.dataset?.menuId || "";
    document.querySelectorAll(".auth-user-menu.is-open").forEach((node) => {
      if (node === except) return;
      node.classList.remove("is-open");
      node.querySelector(".auth-user-trigger")?.setAttribute("aria-expanded", "false");
    });
    document.querySelectorAll(".auth-account-menu.is-open").forEach((node) => {
      if (node.dataset.menuId === exceptId) return;
      node.classList.remove("is-open");
    });
  }

  function userName(user = currentUser) {
    return user?.displayName || user?.username || "已登录";
  }

  function userAccountId(user = currentUser) {
    const raw = String(user?.username || user?.id || "").trim();
    return raw || "local-account";
  }

  function initials(name) {
    const text = String(name || "星").trim();
    return Array.from(text).slice(0, 2).join("").toUpperCase();
  }

  function setMessage(text, mode = "normal") {
    const node = modal?.querySelector("[data-profile-message]");
    if (!node) return;
    node.textContent = text;
    node.dataset.mode = mode;
  }

  async function api(url, body) {
    const response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function renderAvatar(container, user = currentUser, large = false) {
    if (!container) return;
    const name = userName(user);
    container.textContent = "";
    container.classList.toggle("is-large", large);
    if (user?.avatarUrl) {
      const image = document.createElement("img");
      image.src = user.avatarUrl;
      image.alt = name;
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => {
        image.remove();
        container.textContent = initials(name);
      });
      container.appendChild(image);
    } else {
      container.textContent = initials(name);
    }
  }

  function renderLoginPill() {
    const nav = navNode();
    if (!nav || document.querySelector(".auth-session-pill")) return;
    const link = document.createElement("a");
    link.className = "nav-link auth-session-pill auth-login-pill";
    link.href = loginHref();
    link.textContent = "登录";
    nav.appendChild(link);
  }

  function renderAuthPill(user) {
    const nav = navNode();
    if (!nav) return;
    removeExistingPill();
    const name = userName(user);
    nav.classList.add("has-auth-menu");
    const pill = document.createElement("div");
    const menuId = `auth-menu-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    pill.className = "auth-session-pill auth-user-menu";
    pill.dataset.menuId = menuId;

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "auth-user-trigger";
    trigger.title = name;
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-label", `${name}，打开账户菜单`);
    const avatar = document.createElement("span");
    avatar.className = "auth-avatar";
    renderAvatar(avatar, user);
    trigger.appendChild(avatar);

    const menu = document.createElement("div");
    menu.className = "auth-account-menu";
    menu.dataset.authMenu = "true";
    menu.dataset.menuId = menuId;
    menu.setAttribute("role", "menu");
    menu.innerHTML = `
      <div class="auth-account-head">
        <span class="auth-account-avatar" data-account-avatar></span>
        <div class="auth-account-meta">
          <b data-account-name></b>
          <button class="auth-account-id" data-account-copy type="button">
            <span data-account-id></span>
            <span class="profile-copy-icon" aria-hidden="true"></span>
          </button>
        </div>
      </div>
      <div class="auth-menu-divider"></div>
      <button class="auth-menu-item" data-open-profile type="button" role="menuitem">
        <span>个人资料</span>
      </button>
      <button class="auth-menu-item" data-open-profile type="button" role="menuitem">
        <span>账号绑定</span>
      </button>
      <button class="auth-menu-item" data-open-model-settings type="button" role="menuitem">
        <span>模型设置</span>
      </button>
      <div class="auth-menu-divider"></div>
      <button class="auth-menu-item auth-menu-logout" data-menu-logout type="button" role="menuitem">
        <span>退出登录</span>
      </button>
    `;
    renderAvatar(menu.querySelector("[data-account-avatar]"), user);
    menu.querySelector("[data-account-name]").textContent = name;
    menu.querySelector("[data-account-id]").textContent = userAccountId(user);

    const placeMenu = () => {
      const rect = trigger.getBoundingClientRect();
      const gap = 10;
      const menuWidth = menu.offsetWidth || 268;
      const menuHeight = menu.offsetHeight || 360;
      let left = rect.right - menuWidth;
      let top = rect.bottom + gap;
      left = Math.max(12, Math.min(left, window.innerWidth - menuWidth - 12));
      if (top + menuHeight > window.innerHeight - 12) {
        top = Math.max(12, rect.top - menuHeight - gap);
      }
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    };

    let closeTimer = null;
    const setOpen = (open) => {
      clearTimeout(closeTimer);
      pill.classList.toggle("is-open", open);
      menu.classList.toggle("is-open", open);
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        requestAnimationFrame(placeMenu);
      }
    };
    const closeSoon = () => {
      clearTimeout(closeTimer);
      closeTimer = setTimeout(() => setOpen(false), 140);
    };
    pill.addEventListener("mouseenter", () => setOpen(true));
    pill.addEventListener("mouseleave", closeSoon);
    menu.addEventListener("mouseenter", () => clearTimeout(closeTimer));
    menu.addEventListener("mouseleave", closeSoon);
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      closeAccountMenus(pill);
      setOpen(true);
    });
    menu.querySelectorAll("[data-open-profile]").forEach((item) => {
      item.addEventListener("click", () => {
        setOpen(false);
        openProfileModal();
      });
    });
    menu.querySelector("[data-open-model-settings]")?.addEventListener("click", () => {
      setOpen(false);
      openModelSettingsModal();
    });
    menu.querySelector("[data-account-copy]").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(userAccountId(user));
      } catch {}
    });
    menu.querySelector("[data-menu-logout]").addEventListener("click", async () => {
      await fetch("/api/auth/logout", { method: "POST", cache: "no-store" }).catch(() => {});
      window.location.replace("/login.html");
    });
    if (!accountMenuDocumentBound) {
      accountMenuDocumentBound = true;
      document.addEventListener("click", (event) => {
        if (!event.target.closest?.(".auth-user-menu, .auth-account-menu")) closeAccountMenus();
      }, { capture: true });
      window.addEventListener("resize", () => closeAccountMenus());
      window.addEventListener("scroll", () => closeAccountMenus(), { passive: true, capture: true });
    }

    pill.appendChild(trigger);
    nav.appendChild(pill);
    document.body.appendChild(menu);
  }

  function setModelMessage(text, mode = "normal") {
    const node = modelSettingsModal?.querySelector("[data-model-message]");
    if (!node) return;
    node.textContent = text;
    node.dataset.mode = mode;
  }

  function createModelSettingsModal() {
    if (modelSettingsModal) return modelSettingsModal;
    modelSettingsModal = document.createElement("section");
    modelSettingsModal.className = "profile-modal-layer model-settings-layer";
    modelSettingsModal.setAttribute("aria-modal", "true");
    modelSettingsModal.setAttribute("role", "dialog");
    modelSettingsModal.hidden = true;
    modelSettingsModal.innerHTML = `
      <div class="profile-modal-card model-settings-card">
        <button class="profile-modal-close" type="button" aria-label="关闭">×</button>
        <div class="profile-modal-hero model-settings-hero">
          <div class="profile-identity">
            <span class="profile-modal-kicker">MODEL / API / RANK INSIGHT</span>
            <h2>模型设置</h2>
            <p>配置榜单 AI 解析所用的大模型和 API Key，保存后刷新解析缓存会按新模型重新生成。</p>
          </div>
        </div>
        <form class="profile-modal-form model-settings-form">
          <section class="profile-info-section">
            <h3>大模型配置</h3>
            <div class="profile-info-card model-settings-panel">
              <label class="model-settings-field">
                <span>模型类型</span>
                <select data-model-provider></select>
                <small data-model-note></small>
              </label>
              <label class="model-settings-field">
                <span>API Base URL</span>
                <input data-model-base-url type="url" autocomplete="off" placeholder="https://api.example.com/v1" />
              </label>
              <label class="model-settings-field">
                <span>模型名称</span>
                <div class="model-name-row">
                  <select data-model-name></select>
                  <button data-model-refresh-models type="button">刷新</button>
                </div>
                <input data-model-name-custom type="text" autocomplete="off" placeholder="输入自定义模型名" hidden />
                <small data-model-list-state></small>
              </label>
              <label class="model-settings-field">
                <span>API Key</span>
                <input data-model-api-key type="password" autocomplete="off" placeholder="留空则保持已保存的 Key" />
                <small data-model-key-state></small>
              </label>
              <div class="model-settings-inline">
                <label class="model-settings-field">
                  <span>温度</span>
                  <input data-model-temperature type="number" min="0" max="2" step="0.1" />
                </label>
                <label class="model-settings-field">
                  <span>输出 Tokens</span>
                  <input data-model-max-tokens type="number" min="256" max="8192" step="128" />
                </label>
                <label class="model-settings-field">
                  <span>解析行数</span>
                  <input data-model-max-rows type="number" min="1" max="80" step="1" />
                </label>
              </div>
            </div>
          </section>
          <p class="profile-modal-message" data-model-message></p>
          <div class="profile-modal-actions model-settings-actions">
            <button class="profile-cancel" type="button">取消</button>
            <button class="model-clear-key" data-model-clear-key type="button">清除 Key</button>
            <button class="profile-save" type="submit">保存</button>
          </div>
        </form>
      </div>
    `;
    document.body.appendChild(modelSettingsModal);
    bindModelSettingsModalEvents();
    return modelSettingsModal;
  }

  function fillModelProviderOptions() {
    const select = modelSettingsModal.querySelector("[data-model-provider]");
    const presets = modelSettingsData?.presets || {};
    select.innerHTML = "";
    Object.entries(presets).forEach(([key, preset]) => {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = preset.name || key;
      select.appendChild(option);
    });
  }

  function modelPreset(provider = "") {
    const key = provider || modelSettingsModal?.querySelector("[data-model-provider]")?.value || "deepseek";
    return modelSettingsData?.presets?.[key] || {};
  }

  function isSupportedModelOption(provider, model) {
    const value = String(model || "").trim().toLowerCase();
    if (!value) return false;
    const blocked = ["embedding", "moderation", "tts", "whisper", "audio", "image", "vision-preview", "realtime", "transcribe", "search-preview", "deprecated", "legacy"];
    if (blocked.some((token) => value.includes(token))) return false;
    if (provider === "openai") return value.startsWith("gpt-5");
    return true;
  }

  function preferredModelName(provider, selectedModel = "") {
    const preset = modelPreset(provider);
    const selected = String(selectedModel || "").trim();
    if (isSupportedModelOption(provider, selected)) return selected;
    return String(preset.model || "").trim();
  }

  function setModelListState(text, mode = "normal") {
    const node = modelSettingsModal?.querySelector("[data-model-list-state]");
    if (!node) return;
    node.textContent = text || "";
    node.dataset.mode = mode;
  }

  function localModelOptions(provider, selectedModel = "") {
    const preset = modelPreset(provider);
    const models = [];
    [selectedModel, preset.model, ...(Array.isArray(preset.models) ? preset.models : [])].forEach((item) => {
      const value = String(item || "").trim();
      if (value && isSupportedModelOption(provider, value) && !models.includes(value)) models.push(value);
    });
    return models;
  }

  function applyModelPreset(force = false) {
    const provider = modelSettingsModal.querySelector("[data-model-provider]").value || "deepseek";
    const preset = modelPreset(provider);
    const baseInput = modelSettingsModal.querySelector("[data-model-base-url]");
    modelSettingsModal.querySelector("[data-model-note]").textContent = preset.note || "";
    if (force || !baseInput.value.trim()) baseInput.value = preset.baseUrl || "";
    fillModelNameOptions(preferredModelName(provider, (force ? preset.model : currentModelName()) || preset.model || ""));
    if (force) {
      fetchModelOptions(true).catch((error) => {
        setModelListState(error.message || "模型列表读取失败，已使用内置列表", "error");
      });
    }
  }

  function currentModelName() {
    const customInput = modelSettingsModal.querySelector("[data-model-name-custom]");
    const modelSelect = modelSettingsModal.querySelector("[data-model-name]");
    return customInput.hidden ? modelSelect.value : customInput.value.trim();
  }

  function fillModelNameOptions(selectedModel = "", overrideModels = null) {
    const provider = modelSettingsModal.querySelector("[data-model-provider]").value || "deepseek";
    const preset = modelPreset(provider);
    const modelSelect = modelSettingsModal.querySelector("[data-model-name]");
    const customInput = modelSettingsModal.querySelector("[data-model-name-custom]");
    const sourceModels = Array.isArray(overrideModels) ? overrideModels : preset.models;
    const models = [];
    (Array.isArray(sourceModels) ? sourceModels : []).forEach((item) => {
      const value = String(item || "").trim();
      if (value && isSupportedModelOption(provider, value) && !models.includes(value)) models.push(value);
    });
    const selected = preferredModelName(provider, selectedModel || preset.model || models[0] || "");
    modelSelect.innerHTML = "";
    models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      modelSelect.appendChild(option);
    });
    const customOption = document.createElement("option");
    customOption.value = "__custom__";
    customOption.textContent = "自定义模型...";
    modelSelect.appendChild(customOption);
    if (selected && !models.includes(selected)) {
      modelSelect.value = "__custom__";
      customInput.hidden = false;
      customInput.value = selected;
    } else {
      modelSelect.value = selected || models[0] || "__custom__";
      customInput.hidden = modelSelect.value !== "__custom__";
      customInput.value = "";
    }
  }

  async function fetchModelOptions(force = false) {
    if (!modelSettingsModal || !modelSettingsData) return;
    const provider = modelSettingsModal.querySelector("[data-model-provider]").value || "deepseek";
    const baseUrl = modelSettingsModal.querySelector("[data-model-base-url]").value.trim();
    const selected = preferredModelName(provider, currentModelName() || modelPreset(provider).model || "");
    const cacheKey = `${provider}|${baseUrl}`;
    if (!force && modelOptionsCache.has(cacheKey)) {
      const cached = modelOptionsCache.get(cacheKey);
      fillModelNameOptions(selected, cached.models);
      setModelListState(cached.label || "", cached.mode || "normal");
      return;
    }
    setModelListState("正在读取最新模型列表...", "loading");
    const payload = await api("/api/auth/model-options", {
      provider,
      baseUrl,
      model: selected,
      apiKey: modelSettingsModal.querySelector("[data-model-api-key]").value.trim()
    });
    const models = Array.isArray(payload.models) && payload.models.length
      ? payload.models
      : localModelOptions(provider, selected);
    const isRemote = payload.source === "remote";
    const label = isRemote
      ? `已从接口读取 ${models.length} 个模型`
      : `使用内置模型列表${payload.reason ? `（${payload.reason}）` : ""}`;
    const mode = isRemote ? "success" : "normal";
    modelOptionsCache.set(cacheKey, { models, label, mode });
    fillModelNameOptions(selected, models);
    setModelListState(label, mode);
  }

  async function loadModelSettings() {
    modelSettingsData = await getJson("/api/auth/model-settings");
    modelOptionsCache = new Map();
    return modelSettingsData;
  }

  function fillModelSettingsModal() {
    const settings = modelSettingsData?.settings || {};
    fillModelProviderOptions();
    modelSettingsModal.querySelector("[data-model-provider]").value = settings.provider || "deepseek";
    modelSettingsModal.querySelector("[data-model-base-url]").value = settings.baseUrl || "";
    fillModelNameOptions(settings.model || "");
    modelSettingsModal.querySelector("[data-model-api-key]").value = "";
    modelSettingsModal.querySelector("[data-model-temperature]").value = String(settings.temperature ?? 0.2);
    modelSettingsModal.querySelector("[data-model-max-tokens]").value = String(settings.maxTokens || 1800);
    modelSettingsModal.querySelector("[data-model-max-rows]").value = String(settings.maxRows || 36);
    const keyState = modelSettingsModal.querySelector("[data-model-key-state]");
    keyState.textContent = settings.hasApiKey
      ? `已保存 ${settings.maskedApiKey || "API Key"}，留空保存时会继续沿用`
      : "未保存 API Key，会使用环境变量兜底；环境变量也为空时解析会停用";
    applyModelPreset(false);
    fetchModelOptions(false).catch((error) => {
      setModelListState(error.message || "模型列表读取失败，已使用内置列表", "error");
    });
    setModelMessage("");
  }

  async function openModelSettingsModal() {
    if (!currentUser) {
      window.location.href = loginHref();
      return;
    }
    createModelSettingsModal();
    modelSettingsModal.hidden = false;
    document.body.classList.add("profile-modal-open");
    setModelMessage("正在读取模型配置...", "loading");
    try {
      await loadModelSettings();
      fillModelSettingsModal();
      modelSettingsModal.querySelector("[data-model-provider]").focus();
    } catch (error) {
      setModelMessage(error.message || "模型配置读取失败", "error");
    }
  }

  function closeModelSettingsModal() {
    if (!modelSettingsModal) return;
    modelSettingsModal.hidden = true;
    document.body.classList.remove("profile-modal-open");
  }

  function collectModelSettingsPayload(clearApiKey = false) {
    return {
      provider: modelSettingsModal.querySelector("[data-model-provider]").value,
      baseUrl: modelSettingsModal.querySelector("[data-model-base-url]").value.trim(),
      model: currentModelName(),
      apiKey: modelSettingsModal.querySelector("[data-model-api-key]").value.trim(),
      temperature: Number(modelSettingsModal.querySelector("[data-model-temperature]").value || 0.2),
      maxTokens: Number(modelSettingsModal.querySelector("[data-model-max-tokens]").value || 1800),
      maxRows: Number(modelSettingsModal.querySelector("[data-model-max-rows]").value || 36),
      clearApiKey
    };
  }

  async function saveModelSettings(clearApiKey = false) {
    setModelMessage(clearApiKey ? "正在清除 API Key..." : "正在保存模型配置...", "loading");
    const payload = await api("/api/auth/model-settings", collectModelSettingsPayload(clearApiKey));
    modelSettingsData = payload;
    fillModelSettingsModal();
    setModelMessage(clearApiKey ? "API Key 已清除。" : "模型配置已保存，后续榜单解析会使用新配置。", "success");
  }

  function bindModelSettingsModalEvents() {
    modelSettingsModal.addEventListener("click", (event) => {
      if (event.target === modelSettingsModal || event.target.closest(".profile-modal-close") || event.target.closest(".profile-cancel")) {
        closeModelSettingsModal();
      }
    });
    modelSettingsModal.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeModelSettingsModal();
    });
    modelSettingsModal.querySelector("[data-model-provider]").addEventListener("change", () => applyModelPreset(true));
    modelSettingsModal.querySelector("[data-model-base-url]").addEventListener("change", () => {
      fetchModelOptions(true).catch((error) => {
        setModelListState(error.message || "模型列表读取失败，已使用内置列表", "error");
      });
    });
    modelSettingsModal.querySelector("[data-model-refresh-models]")?.addEventListener("click", () => {
      fetchModelOptions(true).catch((error) => {
        setModelListState(error.message || "模型列表读取失败，已使用内置列表", "error");
      });
    });
    modelSettingsModal.querySelector("[data-model-name]").addEventListener("change", () => {
      const customInput = modelSettingsModal.querySelector("[data-model-name-custom]");
      customInput.hidden = modelSettingsModal.querySelector("[data-model-name]").value !== "__custom__";
      if (!customInput.hidden) customInput.focus();
    });
    modelSettingsModal.querySelector("[data-model-clear-key]").addEventListener("click", async () => {
      try {
        await saveModelSettings(true);
      } catch (error) {
        setModelMessage(error.message || "清除失败", "error");
      }
    });
    modelSettingsModal.querySelector(".model-settings-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveModelSettings(false);
      } catch (error) {
        setModelMessage(error.message || "保存失败", "error");
      }
    });
  }

  function createProfileModal() {
    if (modal) return modal;
    modal = document.createElement("section");
    modal.className = "profile-modal-layer";
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("role", "dialog");
    modal.hidden = true;
    modal.innerHTML = `
      <div class="profile-modal-card">
        <button class="profile-modal-close" type="button" aria-label="关闭">×</button>
        <div class="profile-modal-hero">
          <label class="profile-avatar-picker">
            <span class="profile-modal-avatar" data-profile-avatar>星</span>
            <span>点击修改头像</span>
            <input data-profile-avatar-input type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden />
          </label>
          <div class="profile-identity">
            <span class="profile-modal-kicker">ACCOUNT / BINDING / SECURITY</span>
            <h2 data-profile-welcome>欢迎</h2>
            <button class="profile-account-id" data-profile-copy type="button" title="复制账号 ID">
              <span data-profile-id></span>
              <span class="profile-copy-icon" aria-hidden="true"></span>
            </button>
          </div>
        </div>
        <form class="profile-modal-form">
          <section class="profile-info-section">
            <h3>账号资料</h3>
            <div class="profile-info-card">
              <div class="profile-info-row profile-edit-row">
                <div>
                  <b>昵称</b>
                  <input data-profile-name type="text" maxlength="20" autocomplete="name" />
                  <small data-name-count>0/20</small>
                </div>
                <span class="profile-row-action">编辑</span>
              </div>
              <div class="profile-info-row">
                <div>
                  <b>Google 验证</b>
                  <span data-google-state>未绑定</span>
                </div>
                <button data-google-bind type="button"><span class="provider-logo provider-google">G</span><span data-google-label>绑定 Google</span></button>
              </div>
              <div class="profile-info-row profile-email-block">
                <div>
                  <b>邮箱验证</b>
                  <span data-email-state>未绑定</span>
                </div>
                <div class="profile-email-fields">
                  <div class="profile-email-row">
                    <input data-email-input type="email" autocomplete="email" placeholder="邮箱地址" />
                    <button data-email-send type="button">发送</button>
                  </div>
                  <div class="profile-email-row">
                    <input data-email-code type="text" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="验证码" />
                    <button data-email-bind type="button">绑定</button>
                  </div>
                </div>
              </div>
              <div class="profile-info-row profile-exchange-block">
                <div>
                  <b>交易所账号 UID</b>
                  <span data-exchange-state>未绑定</span>
                </div>
                <div class="profile-exchange-fields">
                  <label>
                    <span>Binance</span>
                    <input data-exchange-uid="binance" type="text" inputmode="text" autocomplete="off" placeholder="Binance UID" />
                  </label>
                  <label>
                    <span>OKX</span>
                    <input data-exchange-uid="okx" type="text" inputmode="text" autocomplete="off" placeholder="OKX UID" />
                  </label>
                  <label>
                    <span>Bitget</span>
                    <input data-exchange-uid="bitget" type="text" inputmode="text" autocomplete="off" placeholder="Bitget UID" />
                  </label>
                </div>
              </div>
              <div class="profile-info-row profile-model-block">
                <div>
                  <b>大模型设置</b>
                  <span data-profile-model-state>用于榜单 AI 解析</span>
                </div>
                <button data-open-model-from-profile type="button">配置模型</button>
              </div>
            </div>
          </section>

          <p class="profile-modal-message" data-profile-message></p>
          <div class="profile-modal-actions">
            <button class="profile-cancel" type="button">取消</button>
            <button class="profile-save" type="submit">保存</button>
          </div>
        </form>
      </div>
    `;
    document.body.appendChild(modal);
    bindProfileModalEvents();
    return modal;
  }

  function fillProfileModal() {
    const nameInput = modal.querySelector("[data-profile-name]");
    const emailState = modal.querySelector("[data-email-state]");
    const googleState = modal.querySelector("[data-google-state]");
    const googleLabel = modal.querySelector("[data-google-label]");
    const googleBind = modal.querySelector("[data-google-bind]");
    avatarDataUrl = currentUser?.avatarUrl || "";
    nameInput.value = userName();
    modal.querySelector("[data-profile-welcome]").textContent = `欢迎，${userName()}`;
    modal.querySelector("[data-profile-id]").textContent = userAccountId();
    emailState.textContent = currentUser?.emailBound ? `${currentUser.emailMasked || "已绑定"}` : "未绑定";
    modal.querySelector(".profile-email-block")?.classList.toggle("is-email-bound", Boolean(currentUser?.emailBound));
    googleState.textContent = currentUser?.googleBound ? "已绑定" : "未绑定";
    googleLabel.textContent = currentUser?.googleBound ? "重新绑定 Google" : "绑定 Google";
    googleBind.disabled = !googleEnabled;
    const exchangeUids = currentUser?.exchangeUids || {};
    modal.querySelector('[data-exchange-uid="binance"]').value = exchangeUids.binance || "";
    modal.querySelector('[data-exchange-uid="okx"]').value = exchangeUids.okx || "";
    modal.querySelector('[data-exchange-uid="bitget"]').value = exchangeUids.bitget || "";
    const exchangeCount = ["binance", "okx", "bitget"].filter((key) => exchangeUids[key]).length;
    modal.querySelector("[data-exchange-state]").textContent = exchangeCount ? `已绑定 ${exchangeCount} 个交易所` : "未绑定";
    const modelState = modal.querySelector("[data-profile-model-state]");
    if (modelState) {
      modelState.textContent = "点击配置模型类型、API Key 和解析参数";
      loadModelSettings()
        .then((payload) => {
          const settings = payload?.settings || {};
          modelState.textContent = `${settings.providerName || settings.provider || "大模型"} / ${settings.model || "未设置"}${settings.hasApiKey ? " / 已保存 Key" : " / 未保存 Key"}`;
        })
        .catch(() => {
          modelState.textContent = "点击配置模型类型、API Key 和解析参数";
        });
    }
    renderAvatar(modal.querySelector("[data-profile-avatar]"), currentUser, true);
    updateCounts();
    setMessage("");
  }

  function updateCounts() {
    const nameInput = modal.querySelector("[data-profile-name]");
    modal.querySelector("[data-name-count]").textContent = `${Array.from(nameInput.value || "").length}/20`;
  }

  function openProfileModal() {
    if (!currentUser) {
      window.location.href = loginHref();
      return;
    }
    createProfileModal();
    fillProfileModal();
    modal.hidden = false;
    document.body.classList.add("profile-modal-open");
    modal.querySelector("[data-profile-name]").focus();
  }

  function closeProfileModal() {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("profile-modal-open");
  }

  function startEmailCountdown(seconds = 60) {
    const button = modal.querySelector("[data-email-send]");
    emailCountdown = seconds;
    clearInterval(emailTimer);
    const tick = () => {
      if (emailCountdown <= 0) {
        button.disabled = false;
        button.textContent = "发送";
        clearInterval(emailTimer);
        return;
      }
      button.disabled = true;
      button.textContent = `${emailCountdown}s`;
      emailCountdown -= 1;
    };
    tick();
    emailTimer = setInterval(tick, 1000);
  }

  function bindProfileModalEvents() {
    modal.addEventListener("click", (event) => {
      if (event.target === modal || event.target.closest(".profile-modal-close") || event.target.closest(".profile-cancel")) {
        closeProfileModal();
      }
    });
    modal.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeProfileModal();
    });
    modal.querySelector("[data-profile-name]").addEventListener("input", updateCounts);
    modal.querySelector("[data-profile-copy]").addEventListener("click", async () => {
      const id = userAccountId();
      try {
        await navigator.clipboard.writeText(id);
        setMessage("账号 ID 已复制。", "success");
      } catch {
        setMessage(id, "loading");
      }
    });
    modal.querySelector("[data-profile-avatar-input]").addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      if (!/^image\/(png|jpe?g|webp|gif)$/i.test(file.type) || file.size > 260000) {
        setMessage("头像请使用 260KB 以内的 png / jpg / webp 图片。", "error");
        return;
      }
      avatarDataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      renderAvatar(modal.querySelector("[data-profile-avatar]"), { ...currentUser, avatarUrl: avatarDataUrl }, true);
      setMessage("头像已预览，保存后生效。", "loading");
    });
    modal.querySelector("[data-email-send]").addEventListener("click", async () => {
      const email = modal.querySelector("[data-email-input]").value.trim();
      const button = modal.querySelector("[data-email-send]");
      button.disabled = true;
      setMessage("正在发送验证码...", "loading");
      try {
        const payload = await api("/api/auth/email-code/send", { email });
        startEmailCountdown(60);
        if (payload.sent) {
          setMessage("验证码已发送到邮箱，5 分钟内有效。", "success");
        } else if (payload.devCode) {
          setMessage(`当前未配置邮箱发送服务。本地验证码：${payload.devCode}`, "loading");
        } else {
          setMessage("当前未配置邮箱发送服务，验证码无法发送到邮箱。", "error");
        }
      } catch (error) {
        button.disabled = false;
        setMessage(error.message || "验证码发送失败", "error");
      }
    });
    modal.querySelector("[data-email-bind]").addEventListener("click", async () => {
      setMessage("正在绑定邮箱...", "loading");
      try {
        const payload = await api("/api/auth/email/bind", {
          email: modal.querySelector("[data-email-input]").value.trim(),
          code: modal.querySelector("[data-email-code]").value.trim()
        });
        currentUser = payload.user;
        window.XingyunCurrentUser = currentUser;
        renderAuthPill(currentUser);
        fillProfileModal();
        setMessage("邮箱已绑定。", "success");
      } catch (error) {
        setMessage(error.message || "邮箱绑定失败", "error");
      }
    });
    modal.querySelector("[data-google-bind]").addEventListener("click", () => {
      if (!googleEnabled) {
        setMessage("Google 登录还没有配置，无法绑定。", "error");
        return;
      }
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/api/auth/google/start?mode=bind&next=${next}`;
    });
    modal.querySelector("[data-open-model-from-profile]")?.addEventListener("click", () => {
      closeProfileModal();
      openModelSettingsModal();
    });
    modal.querySelector(".profile-modal-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      setMessage("正在保存...", "loading");
      try {
        const payload = await api("/api/auth/profile", {
          displayName: modal.querySelector("[data-profile-name]").value.trim(),
          avatarUrl: avatarDataUrl,
          exchangeUids: {
            binance: modal.querySelector('[data-exchange-uid="binance"]').value.trim(),
            okx: modal.querySelector('[data-exchange-uid="okx"]').value.trim(),
            bitget: modal.querySelector('[data-exchange-uid="bitget"]').value.trim()
          }
        });
        currentUser = payload.user;
        window.XingyunCurrentUser = currentUser;
        renderAuthPill(currentUser);
        fillProfileModal();
        setMessage("资料已保存。", "success");
      } catch (error) {
        setMessage(error.message || "保存失败", "error");
      }
    });
  }

  async function refreshAuthStatus() {
    const response = await fetch("/api/auth/status", { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    googleEnabled = Boolean(payload.googleEnabled);
    if (response.ok && payload.authenticated === true) {
      currentUser = payload.user || null;
      window.XingyunCurrentUser = currentUser;
      renderAuthPill(currentUser);
      return currentUser;
    }
    currentUser = null;
    window.XingyunCurrentUser = null;
    if (authRequired()) {
      window.location.replace(loginHref());
    } else {
      renderLoginPill();
    }
    return null;
  }

  async function loadAuthStatus() {
    try {
      const user = await refreshAuthStatus();
      const params = new URLSearchParams(window.location.search);
      if (user && params.get("profile") === "1") {
        openProfileModal();
        if (params.get("bound") === "google") {
          setMessage("Google 账号已绑定。", "success");
        } else if (params.get("error")) {
          setMessage(params.get("error") || "绑定失败", "error");
        }
        params.delete("profile");
        params.delete("bound");
        params.delete("error");
        const nextQuery = params.toString();
        history.replaceState(null, "", `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}`);
      }
      return user;
    } catch (error) {
      if (authRequired()) {
        window.location.replace(loginHref());
      } else {
        renderLoginPill();
      }
      return null;
    }
  }

  window.XingyunAuthReady = loadAuthStatus();
})();
