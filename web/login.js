(() => {
  const form = document.querySelector("#authForm");
  const emailForm = document.querySelector("#emailForm");
  const username = document.querySelector("#authUsername");
  const password = document.querySelector("#authPassword");
  const email = document.querySelector("#authEmail");
  const code = document.querySelector("#authCode");
  const submit = document.querySelector("#authSubmit");
  const emailSubmit = document.querySelector("#emailSubmit");
  const sendEmailCode = document.querySelector("#sendEmailCode");
  const toggle = document.querySelector("#authToggle");
  const message = document.querySelector("#authMessage");
  const modeText = document.querySelector("#authModeText");
  const tabs = Array.from(document.querySelectorAll("[data-auth-tab]"));
  const sections = Array.from(document.querySelectorAll("[data-auth-section]"));
  const googleLogin = document.querySelector("#googleLogin");
  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "./todo.html";
  let signupMode = false;
  let googleEnabled = false;
  let emailCountdown = 0;
  let emailTimer = null;

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

  function googleStartUrl() {
    const url = new URL("/api/auth/google/start", window.location.origin);
    url.searchParams.set("next", next);
    const loginHint = email.value.trim() || username.value.trim();
    if (loginHint.includes("@")) {
      url.searchParams.set("login_hint", loginHint);
    }
    return `${url.pathname}${url.search}`;
  }

  function renderGoogleButton() {
    if (!googleLogin) return;
    googleLogin.classList.toggle("is-disabled", !googleEnabled);
    googleLogin.setAttribute("aria-disabled", googleEnabled ? "false" : "true");
    googleLogin.href = googleEnabled ? googleStartUrl() : "#";
  }

  function switchTab(tab) {
    tabs.forEach((node) => node.classList.toggle("active", node.dataset.authTab === tab));
    sections.forEach((node) => node.classList.toggle("active", node.dataset.authSection === tab));
    const labels = {
      password: signupMode ? "注册独立账号" : "私有功能登录",
      email: "邮箱验证码登录"
    };
    modeText.textContent = labels[tab] || "私有功能登录";
  }

  function renderPasswordMode() {
    submit.textContent = signupMode ? "注册并登录" : "登录";
    toggle.textContent = signupMode ? "返回登录" : "注册新账号";
    modeText.textContent = signupMode ? "注册独立账号" : "私有功能登录";
    password.setAttribute("autocomplete", signupMode ? "new-password" : "current-password");
    setMessage("");
  }

  function startEmailCountdown(seconds = 60) {
    emailCountdown = seconds;
    clearInterval(emailTimer);
    const tick = () => {
      if (emailCountdown <= 0) {
        sendEmailCode.disabled = false;
        sendEmailCode.textContent = "发送";
        clearInterval(emailTimer);
        return;
      }
      sendEmailCode.disabled = true;
      sendEmailCode.textContent = `${emailCountdown}s`;
      emailCountdown -= 1;
    };
    tick();
    emailTimer = setInterval(tick, 1000);
  }

  async function loadStatus() {
    const response = await fetch("/api/auth/status", { cache: "no-store" });
    const payload = await response.json();
    if (payload.authenticated) {
      window.location.replace(next);
      return;
    }

    signupMode = false;
    googleEnabled = Boolean(payload.googleEnabled);
    submit.textContent = "登录";
    toggle.hidden = false;
    toggle.textContent = "注册新账号";
    modeText.textContent = "私有功能登录";
    renderGoogleButton();
    setMessage("");

    const error = params.get("error");
    if (error === "google_not_configured") {
      setMessage("Google 登录未配置：请在 .env 中设置 GOOGLE_CLIENT_ID 和 GOOGLE_CLIENT_SECRET。", "error");
    } else if (error === "google_state_invalid") {
      setMessage("Google 登录状态已过期，请重新点击 Google 登录。", "error");
    } else if (error) {
      setMessage(decodeURIComponent(error), "error");
    }
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.authTab));
  });

  username.addEventListener("input", renderGoogleButton);
  email.addEventListener("input", renderGoogleButton);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    toggle.disabled = true;
    setMessage(signupMode ? "正在注册账号..." : "正在登录...", "loading");
    try {
      await api(signupMode ? "/api/auth/signup" : "/api/auth/login", {
        username: username.value.trim(),
        password: password.value
      });
      window.location.replace(next);
    } catch (error) {
      setMessage(error.message || "操作失败", "error");
      submit.disabled = false;
      toggle.disabled = false;
    }
  });

  toggle.addEventListener("click", () => {
    signupMode = !signupMode;
    renderPasswordMode();
  });

  sendEmailCode.addEventListener("click", async () => {
    const value = email.value.trim();
    setMessage("正在发送验证码...", "loading");
    sendEmailCode.disabled = true;
    try {
      const payload = await api("/api/auth/email-code/send", { email: value });
      startEmailCountdown(60);
      if (payload.sent) {
        setMessage("验证码已发送到邮箱，5 分钟内有效。", "success");
      } else if (payload.devCode) {
        setMessage(`当前未配置邮箱发送服务。本地验证码：${payload.devCode}`, "loading");
      } else {
        setMessage("当前未配置邮箱发送服务，验证码无法发送到邮箱。", "error");
      }
      code.focus();
    } catch (error) {
      sendEmailCode.disabled = false;
      setMessage(error.message || "验证码发送失败", "error");
    }
  });

  emailForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    emailSubmit.disabled = true;
    setMessage("正在校验验证码...", "loading");
    try {
      await api("/api/auth/email-code/login", {
        email: email.value.trim(),
        code: code.value.trim()
      });
      window.location.replace(next);
    } catch (error) {
      emailSubmit.disabled = false;
      setMessage(error.message || "邮箱登录失败", "error");
    }
  });

  googleLogin.addEventListener("click", (event) => {
    if (!googleEnabled || googleLogin.classList.contains("is-disabled")) {
      event.preventDefault();
      setMessage("Google 登录还没配置。请在 .env 中设置 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET。", "error");
      return;
    }
    googleLogin.href = googleStartUrl();
  });

  loadStatus().catch((error) => setMessage(error.message || "无法读取登录状态", "error"));
})();
