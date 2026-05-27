(() => {
  const params = new URLSearchParams(window.location.search);
  const avatar = document.querySelector("#profileAvatar");
  const displayName = document.querySelector("#profileDisplayName");
  const username = document.querySelector("#profileUsername");
  const phoneState = document.querySelector("#phoneState");
  const googleState = document.querySelector("#googleState");
  const role = document.querySelector("#profileRole");
  const message = document.querySelector("#profileMessage");
  const bindPhoneForm = document.querySelector("#bindPhoneForm");
  const bindPhone = document.querySelector("#bindPhone");
  const bindPhoneCode = document.querySelector("#bindPhoneCode");
  const bindSendCode = document.querySelector("#bindSendCode");
  const bindPhoneSubmit = document.querySelector("#bindPhoneSubmit");
  const bindGoogle = document.querySelector("#bindGoogle");
  const logout = document.querySelector("#profileLogout");
  let currentUser = null;
  let googleEnabled = false;
  let countdown = 0;
  let timer = null;

  function initials(name) {
    return Array.from(String(name || "星").trim()).slice(0, 2).join("").toUpperCase();
  }

  function setMessage(text, mode = "normal") {
    message.textContent = text;
    message.dataset.mode = mode;
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

  function renderAvatar(user) {
    const name = user?.displayName || user?.username || "星云社";
    avatar.textContent = "";
    if (user?.avatarUrl) {
      const image = document.createElement("img");
      image.src = user.avatarUrl;
      image.alt = name;
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => {
        image.remove();
        avatar.textContent = initials(name);
      });
      avatar.appendChild(image);
    } else {
      avatar.textContent = initials(name);
    }
  }

  function renderUser(user) {
    currentUser = user || {};
    const name = currentUser.displayName || currentUser.username || "--";
    renderAvatar(currentUser);
    displayName.textContent = name;
    username.textContent = `@${currentUser.username || "--"}`;
    role.textContent = currentUser.role === "admin" ? "管理员" : "普通用户";
    phoneState.textContent = currentUser.phoneBound ? `已绑定 ${currentUser.phoneMasked || ""}` : "未绑定";
    phoneState.classList.toggle("is-bound", Boolean(currentUser.phoneBound));
    googleState.textContent = currentUser.googleBound ? "已绑定" : "未绑定";
    googleState.classList.toggle("is-bound", Boolean(currentUser.googleBound));
    bindGoogle.textContent = "";
    const logo = document.createElement("span");
    logo.className = "provider-logo provider-google";
    logo.textContent = "G";
    bindGoogle.append(logo, document.createTextNode(currentUser.googleBound ? "重新绑定 Google" : "绑定 Google"));
    bindGoogle.disabled = !googleEnabled;
  }

  async function refreshStatus() {
    const response = await fetch("/api/auth/status", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.authenticated) {
      window.location.replace(`/login.html?next=${encodeURIComponent("/profile.html")}`);
      return;
    }
    googleEnabled = Boolean(payload.googleEnabled);
    renderUser(payload.user);
  }

  function startCountdown(seconds = 60) {
    countdown = seconds;
    clearInterval(timer);
    const tick = () => {
      if (countdown <= 0) {
        bindSendCode.disabled = false;
        bindSendCode.textContent = "发送";
        clearInterval(timer);
        return;
      }
      bindSendCode.disabled = true;
      bindSendCode.textContent = `${countdown}s`;
      countdown -= 1;
    };
    tick();
    timer = setInterval(tick, 1000);
  }

  bindSendCode.addEventListener("click", async () => {
    bindSendCode.disabled = true;
    setMessage("正在发送验证码...", "loading");
    try {
      const payload = await api("/api/auth/phone-code/send", { phone: bindPhone.value.trim() });
      startCountdown(60);
      const suffix = payload.devCode ? ` 本地验证码：${payload.devCode}` : "";
      setMessage(`验证码已发送，5 分钟内有效。${suffix}`, payload.devCode ? "loading" : "normal");
      bindPhoneCode.focus();
    } catch (error) {
      bindSendCode.disabled = false;
      setMessage(error.message || "验证码发送失败", "error");
    }
  });

  bindPhoneForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    bindPhoneSubmit.disabled = true;
    setMessage("正在绑定手机号...", "loading");
    try {
      const payload = await api("/api/auth/phone/bind", {
        phone: bindPhone.value.trim(),
        code: bindPhoneCode.value.trim()
      });
      renderUser(payload.user);
      bindPhoneCode.value = "";
      setMessage("手机号已绑定。", "success");
    } catch (error) {
      setMessage(error.message || "手机号绑定失败", "error");
    } finally {
      bindPhoneSubmit.disabled = false;
    }
  });

  bindGoogle.addEventListener("click", () => {
    if (!googleEnabled) {
      setMessage("Google 登录还没有配置，无法绑定。", "error");
      return;
    }
    const next = encodeURIComponent("/profile.html");
    window.location.href = `/api/auth/google/start?mode=bind&next=${next}`;
  });

  logout.addEventListener("click", async () => {
    logout.disabled = true;
    await fetch("/api/auth/logout", { method: "POST", cache: "no-store" }).catch(() => {});
    window.location.replace("/login.html");
  });

  window.XingyunAuthReady
    .then((user) => {
      if (user) renderUser(user);
      return refreshStatus();
    })
    .then(() => {
      if (params.get("bound") === "google") {
        setMessage("Google 账号已绑定。", "success");
        history.replaceState(null, "", "/profile.html");
      } else if (params.get("error")) {
        setMessage(params.get("error") || "", "error");
        history.replaceState(null, "", "/profile.html");
      }
    })
    .catch((error) => setMessage(error.message || "无法读取用户信息", "error"));
})();
