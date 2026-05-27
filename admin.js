(() => {
  const nodes = {
    status: document.querySelector("#adminStatusText"),
    message: document.querySelector("#adminMessage"),
    avatar: document.querySelector("#adminAvatar"),
    users: document.querySelector("#adminUsers"),
    userCount: document.querySelector("#adminUserCount"),
    runtime: document.querySelector("#adminRuntime"),
    sources: document.querySelector("#adminSources"),
    metricUsers: document.querySelector("#metricUsers"),
    metricSessions: document.querySelector("#metricSessions"),
    metricCache: document.querySelector("#metricCache"),
    metricWechat: document.querySelector("#metricWechat"),
    searchForm: document.querySelector("#adminSearchForm"),
    userSearch: document.querySelector("#userSearch"),
    roleFilter: document.querySelector("#roleFilter"),
    resetSearch: document.querySelector("#resetSearch"),
    createForm: document.querySelector("#adminCreateUser"),
    toggleCreate: document.querySelector("#toggleCreateUser"),
    reloadUsers: document.querySelector("#reloadUsers"),
    refreshCache: document.querySelector("#refreshCache"),
    clearCache: document.querySelector("#clearCache"),
    logout: document.querySelector("#adminLogout")
  };

  let currentUser = null;
  let usersCache = [];
  let filters = { keyword: "", role: "" };

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function setStatus(text, mode = "normal") {
    if (!nodes.status) return;
    nodes.status.textContent = text;
    nodes.status.dataset.mode = mode;
  }

  function setMessage(text, mode = "normal") {
    if (!nodes.message) return;
    nodes.message.textContent = text;
    nodes.message.dataset.mode = mode;
  }

  function formatTime(value) {
    const number = Number(value || 0);
    if (!number) return "--";
    const ms = number > 10_000_000_000 ? number : number * 1000;
    return new Date(ms).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function visibleUsers() {
    const keyword = filters.keyword.trim().toLowerCase();
    return usersCache.filter((user) => {
      const haystack = [
        user.username,
        user.displayName,
        user.emailMasked,
        user.phoneMasked,
        user.authProvider,
        roleLabel(user.role),
        user.exchangeUids?.binance,
        user.exchangeUids?.okx,
        user.exchangeUids?.bitget
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      const matchKeyword = !keyword || haystack.includes(keyword);
      const matchRole = !filters.role || user.role === filters.role;
      return matchKeyword && matchRole;
    });
  }

  function roleLabel(role) {
    return role === "admin" ? "管理员" : "普通用户";
  }

  function authProviderLabel(provider) {
    const key = String(provider || "password").toLowerCase();
    if (key === "google") return "Google";
    if (key === "email") return "邮箱";
    if (key === "phone") return "手机";
    return "密码";
  }

  function renderUserAvatar(user) {
    const name = user.displayName || user.username || "?";
    const src = String(user.avatarUrl || "").trim();
    if (src) {
      return `<span class="ry-user-thumb"><img src="${escapeHtml(src)}" alt="${escapeHtml(name)}" loading="lazy" /></span>`;
    }
    return `<span class="ry-user-thumb">${escapeHtml(String(name).slice(0, 1).toUpperCase())}</span>`;
  }

  function renderBindInfo(user) {
    const badges = [
      `<span class="ry-mini-badge">${escapeHtml(authProviderLabel(user.authProvider))}登录</span>`,
      user.emailBound ? `<span class="ry-mini-badge ok">邮箱</span>` : "",
      user.googleBound ? `<span class="ry-mini-badge ok">Google</span>` : "",
      user.phoneBound ? `<span class="ry-mini-badge ok">手机</span>` : ""
    ]
      .filter(Boolean)
      .join("");
    const lines = [
      user.emailBound ? `邮箱 ${user.emailMasked || "已绑定"}` : "邮箱 未绑定",
      user.googleBound ? "Google 已绑定" : "Google 未绑定",
      user.phoneBound ? `手机 ${user.phoneMasked || "已绑定"}` : "手机 未绑定"
    ];
    return `
      <div class="ry-bind-cell">
        <div class="ry-bind-badges">${badges}</div>
        ${lines.map((line) => `<em>${escapeHtml(line)}</em>`).join("")}
      </div>
    `;
  }

  function renderExchangeInfo(user) {
    const exchangeUids = user.exchangeUids || {};
    const items = [
      ["Binance", exchangeUids.binance],
      ["OKX", exchangeUids.okx],
      ["Bitget", exchangeUids.bitget]
    ].filter(([, value]) => String(value || "").trim());
    if (!items.length) return `<span class="ry-empty-muted">未绑定</span>`;
    return `
      <div class="ry-exchange-cell">
        ${items
          .map(
            ([label, value]) => `
              <span class="ry-exchange-line">
                <b>${escapeHtml(label)}</b>
                <em title="${escapeHtml(value)}">${escapeHtml(value)}</em>
              </span>
            `
          )
          .join("")}
      </div>
    `;
  }

  function renderMetrics(summary) {
    const users = summary?.users || {};
    const cache = summary?.cache || {};
    const wechat = summary?.wechat || {};
    const accountCount = Number(wechat.accountCount ?? wechat.accounts ?? 0);
    nodes.metricUsers.textContent = users.total ?? "--";
    nodes.metricSessions.textContent = users.activeSessions ?? "--";
    nodes.metricCache.textContent = cache.files ?? "--";
    nodes.metricWechat.textContent = wechat.ok === false ? "异常" : accountCount || "--";
  }

  function renderUsers() {
    const rows = visibleUsers();
    if (nodes.userCount) nodes.userCount.textContent = `共 ${rows.length} 条`;
    if (!rows.length) {
      nodes.users.innerHTML = `
        <tr>
          <td colspan="11" class="ry-empty-row">没有匹配的用户。</td>
        </tr>
      `;
      return;
    }
    nodes.users.innerHTML = rows
      .map((user, index) => {
        const isSelf = currentUser && Number(currentUser.id) === Number(user.id);
        return `
          <tr data-id="${escapeHtml(user.id)}">
            <td>${index + 1}</td>
            <td>
              <div class="ry-user-cell">
                ${renderUserAvatar(user)}
                <div>
                  <b>${escapeHtml(user.displayName || user.username)}${isSelf ? "（当前账号）" : ""}</b>
                  <em>@${escapeHtml(user.username)} · ID ${escapeHtml(user.id)}</em>
                </div>
              </div>
            </td>
            <td>
              <select class="ry-table-select admin-role-select" aria-label="角色">
                <option value="user"${user.role === "user" ? " selected" : ""}>普通用户</option>
                <option value="admin"${user.role === "admin" ? " selected" : ""}>管理员</option>
              </select>
            </td>
            <td>${renderBindInfo(user)}</td>
            <td>${renderExchangeInfo(user)}</td>
            <td>${escapeHtml(user.todoTasks || 0)} / 项目 ${escapeHtml(user.todoProjects || 0)}</td>
            <td>${escapeHtml(user.xEnabledSources || user.xSources || 0)} / 总 ${escapeHtml(user.xSources || 0)}</td>
            <td>${escapeHtml(user.activeSessions || 0)}</td>
            <td>${escapeHtml(formatTime(user.updatedAt))}</td>
            <td>${escapeHtml(formatTime(user.createdAt))}</td>
            <td>
              <div class="ry-action-cell">
                <input class="admin-password-input" type="password" placeholder="重置密码" aria-label="重置密码" />
                <button type="button" class="ry-link admin-save-user">保存</button>
                <button type="button" class="ry-link danger admin-delete-user"${isSelf ? " disabled" : ""}>删除</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function renderRuntime(summary) {
    const cache = summary?.cache || {};
    const monitors = summary?.monitors || {};
    const wechat = summary?.wechat || {};
    const items = [
      ["缓存文件", `${cache.files ?? "--"} 个`, formatBytes(cache.bytes)],
      ["缓存更新时间", formatTime(cache.updatedAt), "接口缓存和源缓存"],
      ["站内弹窗监控", monitors.siteAlert ? "运行中" : "未启动", `启动 ${formatTime(monitors.siteAlertStartedAt)}`],
      ["榜单监控间隔", `${monitors.rankMonitorInterval ?? "--"} 秒`, "新上榜和榜首异动"],
      ["公众号授权", wechat.ok === false ? "异常" : `已授权 ${wechat.accountCount ?? wechat.accounts ?? 0} 个`, wechat.error || "授权状态由后台监控"],
      ["授权监控", monitors.wechatAuthMonitor ? "运行中" : "未启动", "失效后会触发桌面提醒"]
    ];
    nodes.runtime.innerHTML = items
      .map(
        ([label, value, hint]) => `
          <article>
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
            <em>${escapeHtml(hint)}</em>
          </article>
        `
      )
      .join("");
  }

  function renderSources(summary) {
    const groups = summary?.sources || [];
    if (!groups.length) {
      nodes.sources.innerHTML = `<div class="ry-empty-row">暂无数据源状态。</div>`;
      return;
    }
    nodes.sources.innerHTML = groups
      .map((group) => {
        const badCount = (group.sources || []).filter((source) => !(source.status === "ok" || source.rows > 0)).length;
        return `
          <details class="ry-source-group" open>
            <summary>
              <b>${escapeHtml(group.key)}</b>
              <span>${escapeHtml(formatTime(group.updatedAt))}</span>
              <em>${badCount ? `${badCount} 个异常` : "正常"}</em>
            </summary>
            <ul>
              ${(group.sources || [])
                .slice(0, 24)
                .map((source) => {
                  const ok = source.status === "ok" || source.rows > 0;
                  return `
                    <li data-state="${ok ? "ok" : "warn"}">
                      <span></span>
                      <b>${escapeHtml(source.title || source.id)}</b>
                      <strong>${escapeHtml(source.rows)} 条</strong>
                      <em>${escapeHtml(source.sourceName || source.status || "--")}</em>
                    </li>
                  `;
                })
                .join("") || `<li data-state="warn"><span></span><b>暂无数据</b><strong>0 条</strong><em>等待刷新</em></li>`}
            </ul>
          </details>
        `;
      })
      .join("");
  }

  async function loadSummary() {
    const summary = await api("/api/admin/summary");
    renderMetrics(summary);
    renderRuntime(summary);
    renderSources(summary);
    setStatus(`已读取 · ${formatTime(summary.updatedAt)}`, "ok");
  }

  async function loadUsers() {
    const payload = await api("/api/admin/users");
    usersCache = payload.users || [];
    renderUsers();
  }

  async function refreshAll() {
    setStatus("刷新中", "loading");
    await Promise.all([loadSummary(), loadUsers()]);
  }

  function bindEvents() {
    nodes.searchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      filters.keyword = nodes.userSearch.value;
      filters.role = nodes.roleFilter.value;
      renderUsers();
    });

    nodes.resetSearch.addEventListener("click", () => {
      nodes.userSearch.value = "";
      nodes.roleFilter.value = "";
      filters = { keyword: "", role: "" };
      renderUsers();
    });

    nodes.toggleCreate.addEventListener("click", () => {
      nodes.createForm.hidden = !nodes.createForm.hidden;
      nodes.toggleCreate.textContent = nodes.createForm.hidden ? "新增" : "收起新增";
    });

    nodes.createForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(nodes.createForm);
      const body = {
        username: String(form.get("username") || "").trim(),
        password: String(form.get("password") || ""),
        role: String(form.get("role") || "user")
      };
      setMessage("正在创建账号...", "loading");
      try {
        const payload = await api("/api/admin/users/create", { method: "POST", body: JSON.stringify(body) });
        usersCache = payload.users || [];
        nodes.createForm.reset();
        nodes.createForm.hidden = true;
        nodes.toggleCreate.textContent = "新增";
        renderUsers();
        setMessage(`已创建账号：${body.username}`, "ok");
        await loadSummary();
      } catch (error) {
        setMessage(error.message || "创建失败", "error");
      }
    });

    nodes.reloadUsers.addEventListener("click", () => {
      loadUsers().then(() => setMessage("用户列表已刷新", "ok")).catch((error) => setMessage(error.message, "error"));
    });

    nodes.users.addEventListener("click", async (event) => {
      const row = event.target.closest("tr[data-id]");
      if (!row) return;
      const id = Number(row.dataset.id || 0);
      if (event.target.closest(".admin-save-user")) {
        const role = row.querySelector(".admin-role-select")?.value || "user";
        const password = row.querySelector(".admin-password-input")?.value || "";
        setMessage("正在保存用户...", "loading");
        try {
          const payload = await api("/api/admin/users/update", {
            method: "POST",
            body: JSON.stringify({ id, role, password })
          });
          usersCache = payload.users || [];
          renderUsers();
          setMessage("用户已更新", "ok");
          await loadSummary();
        } catch (error) {
          setMessage(error.message || "保存失败", "error");
        }
      }
      if (event.target.closest(".admin-delete-user")) {
        const user = usersCache.find((item) => Number(item.id) === id);
        if (!window.confirm(`确认删除 ${user?.username || "该用户"}？该用户的 TodoList 和 X追踪数据也会删除。`)) return;
        setMessage("正在删除用户...", "loading");
        try {
          const payload = await api("/api/admin/users/delete", {
            method: "POST",
            body: JSON.stringify({ id })
          });
          usersCache = payload.users || [];
          renderUsers();
          setMessage("用户已删除", "ok");
          await loadSummary();
        } catch (error) {
          setMessage(error.message || "删除失败", "error");
        }
      }
    });

    nodes.refreshCache.addEventListener("click", async () => {
      setStatus("已提交刷新", "loading");
      try {
        const payload = await api("/api/admin/cache/refresh", { method: "POST", body: "{}" });
        setMessage(`已提交 ${payload.queued?.length || 0} 个缓存刷新任务`, "ok");
        setTimeout(loadSummary, 1600);
      } catch (error) {
        setMessage(error.message || "刷新失败", "error");
      }
    });

    nodes.clearCache.addEventListener("click", async () => {
      if (!window.confirm("只会清理接口缓存，不会删除用户、TodoList、X追踪和RSS数据。确认继续？")) return;
      setStatus("正在清理缓存", "loading");
      try {
        const payload = await api("/api/admin/cache/clear", { method: "POST", body: "{}" });
        setMessage(`已清理 ${payload.deleted || 0} 个缓存文件`, "ok");
        await loadSummary();
      } catch (error) {
        setMessage(error.message || "清理失败", "error");
      }
    });

    nodes.logout.addEventListener("click", async () => {
      await fetch("/api/auth/logout", { method: "POST", cache: "no-store" }).catch(() => {});
      window.location.replace(`/login.html?next=${encodeURIComponent("/admin.html")}`);
    });
  }

  async function init() {
    currentUser = await window.XingyunAuthReady;
    if (!currentUser) return;
    if (currentUser.role !== "admin") {
      setStatus("无后台权限", "error");
      document.querySelector(".ry-content").innerHTML = `<section class="ry-card ry-forbidden">当前账号不是管理员，不能访问后台管理。</section>`;
      return;
    }
    nodes.avatar.textContent = String(currentUser.username || "A").slice(0, 1).toUpperCase();
    bindEvents();
    await refreshAll();
  }

  init().catch((error) => {
    setStatus("后台读取失败", "error");
    setMessage(error.message || "后台读取失败", "error");
  });
})();
