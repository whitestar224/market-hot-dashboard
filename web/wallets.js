(() => {
  if (window.XingyunWallets) return;
  const WALLET_ADAPTERS = Object.freeze([
    { key: "binance", label: "Binance Wallet", mark: "BN", authorizationProvider: "binance", installUrl: "https://www.binance.com/en/web3wallet" },
    { key: "okx", label: "OKX Wallet", mark: "OKX", authorizationProvider: "okx", installUrl: "https://web3.okx.com/wallet/download" },
    { key: "metamask", label: "MetaMask", mark: "MM", authorizationProvider: "metamask", installUrl: "https://metamask.io/download/" },
    { key: "bitget", label: "Bitget Wallet", mark: "BG", authorizationProvider: "bitget", installUrl: "https://web3.bitget.com/en/wallet-download" },
  ]);
  const prefix = "xingyunWalletV1:";
  const read = key => { try { return localStorage.getItem(prefix + key); } catch (_) { return null; } };
  const write = (key, value) => { try { localStorage.setItem(prefix + key, value); } catch (_) {} };
  const legacy = () => { try { return localStorage.getItem("newsTradeActiveWallet"); } catch (_) { return null; } };
  const uiState = { activeProviderKey: read("active") || legacy() || "binance", connectingProviderKey: "", panelOpen: false };
  const sessions = new Map(), announcements = new Map(), bindings = new WeakMap(), inflight = new Map(), connecting = new Map();
  const disabled = new Set();
  let panel, navButton, channel;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const short = address => address ? `${address.slice(0, 7)}…${address.slice(-5)}` : "";
  const topbarUtilitiesNode = () => {
    const topbar = document.querySelector(".topbar");
    if (!topbar || !topbar.querySelector(".page-nav")) return null;
    let utilities = topbar.querySelector(".topbar-utilities");
    if (!utilities) {
      utilities = document.createElement("div");
      utilities.className = "topbar-utilities";
      utilities.setAttribute("aria-label", "账户与钱包");
      topbar.appendChild(utilities);
    }
    return utilities;
  };
  const timed = (promise, milliseconds = 8000) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("钱包暂未响应，请检查钱包是否解锁；不会自动重复请求")), milliseconds);
    Promise.resolve(promise).then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
  });
  function matches(key, provider, info = {}) {
    const identity = `${info.rdns || ""} ${info.name || ""}`.toLowerCase();
    if (key === "binance") return Boolean(provider?.isBinance || provider?.isBinanceWallet || /binance/.test(identity));
    if (key === "okx") return Boolean(provider?.isOkxWallet || /(^|[.\s])okx([.\s]|$)/.test(identity));
    if (key === "bitget") return Boolean(provider?.isBitKeep || provider?.isBitgetWallet || /bitget|bitkeep/.test(identity));
    if (key === "metamask") return Boolean(/metamask/.test(identity) || (provider?.isMetaMask && !matches("binance", provider) && !matches("okx", provider) && !matches("bitget", provider)));
    return false;
  }
  function adapters() {
    return [...WALLET_ADAPTERS, ...[...announcements.entries()].filter(([, detail]) => !WALLET_ADAPTERS.some(a => matches(a.key, detail.provider, detail.info))).map(([key, detail]) => ({ key, label: String(detail.info?.name || "浏览器钱包"), mark: "W3", authorizationProvider: "injected", installUrl: "" }))];
  }
  function adapter(key) { return adapters().find(item => item.key === key) || { key, label: "钱包", mark: "W3", authorizationProvider: "injected" }; }
  function provider(namespace = "evm", key = uiState.activeProviderKey) {
    if (namespace === "solana") return ({ okx: window.okxwallet?.solana, binance: window.binancew3w?.solana, bitget: window.bitgetWallet?.solana || window.bitkeep?.solana })[key] || null;
    const direct = ({ binance: window.binancew3w?.ethereum || window.BinanceChain, okx: window.okxwallet?.ethereum || window.okxwallet, bitget: window.bitgetWallet?.ethereum || window.bitkeep?.ethereum })[key];
    if (direct?.request) return direct;
    if (announcements.get(key)?.provider?.request) return announcements.get(key).provider;
    for (const detail of announcements.values()) if (matches(key, detail.provider, detail.info)) return detail.provider;
    const injected = window.ethereum?.providers || (window.ethereum ? [window.ethereum] : []);
    return injected.find(item => item?.request && matches(key, item)) || null;
  }
  function session(key) {
    if (!sessions.has(key)) {
      sessions.set(key, { providerKey: key, evmAddress: "", evmChainId: "", solanaAddress: "", revision: 0, disconnectVersion: 0 });
      if (read(`disabled:${key}`) === "1") disabled.add(key);
    }
    return sessions.get(key);
  }
  const snapshot = key => { const s = session(key); return { evm: s.evmAddress, solana: s.solanaAddress }; };
  const connected = () => adapters().map(a => session(a.key)).filter(s => s.evmAddress || s.solanaAddress);
  let warmTimer, warmRequest, warmIdentity = "", warmSignature = "", warmStarted = 0, warmChain = 0;
  function warmBalances(preferredChain) {
    if (typeof window.fetch !== "function") return;
    clearTimeout(warmTimer);
    const key = uiState.activeProviderKey, owners = snapshot(key);
    const identity = JSON.stringify([key, owners.evm.toLowerCase(), owners.solana]);
    if (identity !== warmIdentity) {
      warmRequest?.abort(); warmRequest = null;
      warmIdentity = identity; warmSignature = ""; warmStarted = 0; warmChain = 0;
    }
    if (disabled.has(key) || (!owners.evm && !owners.solana)) return;
    if (Number.isSafeInteger(Number(preferredChain)) && Number(preferredChain) > 0) warmChain = Number(preferredChain);
    const chain = warmChain || Number(session(key).evmChainId) || (owners.solana && !owners.evm ? 792703809 : 56);
    if (document.hidden) return;
    const body = JSON.stringify({ walletProvider: key, wallets: owners, preferredChain: chain });
    // Read-only, active-wallet-only preparation. Never request permission or sign here.
    if (body !== warmSignature || Date.now() - warmStarted >= 20000) {
      warmRequest?.abort();
      const controller = new AbortController(); warmRequest = controller;
      warmSignature = body; warmStarted = Date.now();
      const timeout = setTimeout(() => controller.abort(), 8000);
      window.fetch("/api/monitor-buy/balances-warm", { method: "POST", headers: { "Content-Type": "application/json" }, body, signal: controller.signal })
        .catch(() => {})
        .finally(() => { clearTimeout(timeout); if (warmRequest === controller) warmRequest = null; });
    }
    warmTimer = setTimeout(() => warmBalances(), 20000);
  }
  function notify(key, broadcast = false) {
    render();
    warmBalances();
    window.dispatchEvent(new CustomEvent("xingyun:wallet-change", { detail: { key, activeKey: uiState.activeProviderKey } }));
    if (broadcast) channel?.postMessage({ key });
  }
  function setActive(key) {
    if (!adapters().some(item => item.key === key)) throw Error("钱包尚未检测到");
    uiState.activeProviderKey = key; write("active", key); notify(key, true);
  }
  function bind(key, object, namespace) {
    if (!object?.on) return;
    const bound = bindings.get(object) || new Set();
    if (bound.has(`${key}:${namespace}`)) return;
    bound.add(`${key}:${namespace}`); bindings.set(object, bound);
    const update = patch => { const s = session(key); s.revision++; Object.assign(s, disabled.has(key) ? { evmAddress: "", solanaAddress: "" } : patch); notify(key, true); };
    if (namespace === "evm") {
      object.on("accountsChanged", accounts => update({ evmAddress: String(accounts?.[0] || "") }));
      object.on("chainChanged", chainId => update({ evmChainId: String(chainId || "").toLowerCase() }));
      object.on("disconnect", () => update({ evmAddress: "", evmChainId: "" }));
    } else {
      object.on("accountChanged", publicKey => update({ solanaAddress: String(publicKey || "") }));
      object.on("disconnect", () => update({ solanaAddress: "" }));
    }
  }
  async function refreshOne(key) {
    if (inflight.has(key)) return inflight.get(key);
    const job = (async () => {
      const s = session(key), evm = provider("evm", key), sol = provider("solana", key);
      bind(key, evm, "evm"); bind(key, sol, "solana");
      const revision = s.revision;
      if (disabled.has(key)) { s.evmAddress = s.solanaAddress = ""; notify(key); return; }
      try {
        const [accounts, chainId] = evm ? await timed(Promise.all([evm.request({ method: "eth_accounts" }), evm.request({ method: "eth_chainId" })])) : [[], ""];
        if (revision !== s.revision || disabled.has(key)) return;
        s.evmAddress = String(accounts?.[0] || ""); s.evmChainId = String(chainId || "").toLowerCase();
      } catch (_) { if (revision === s.revision) s.evmAddress = s.evmChainId = ""; }
      if (revision === s.revision && !disabled.has(key)) s.solanaAddress = String(sol?.isConnected !== false ? sol?.publicKey || "" : "");
      notify(key);
    })();
    inflight.set(key, job);
    try { return await job; } finally { if (inflight.get(key) === job) inflight.delete(key); }
  }
  async function initialize() {
    await Promise.all(adapters().map(item => refreshOne(item.key)));
    if (!read("active") && !legacy()) {
      const first = connected()[0];
      if (first && first.providerKey !== uiState.activeProviderKey) setActive(first.providerKey);
    }
  }
  async function connect(key = uiState.activeProviderKey, needsSolana = false, _checkSolana = false, options = {}) {
    if (connecting.has(key)) return connecting.get(key);
    const job = (async () => {
      const s = session(key), evm = provider("evm", key), sol = provider("solana", key);
      if (needsSolana && !sol) throw Error("当前钱包未提供 Solana 接口，请选择支持的钱包");
      if (!evm && !sol) throw Error(`未检测到 ${adapter(key).label}，请安装并解锁钱包`);
      const wasDisabled = disabled.has(key);
      const connectRevision = ++s.revision;
      const disconnectVersion = s.disconnectVersion;
      let evmAddress = s.evmAddress, evmChainId = s.evmChainId, solanaAddress = s.solanaAddress;
      uiState.connectingProviderKey = key; notify(key);
      try {
        if (evm) {
          if (options.switchAccount) {
            try { await evm.request({ method: "wallet_requestPermissions", params: [{ eth_accounts: {} }] }); }
            catch (error) { if (![-32601, 4200].includes(Number(error.code))) throw error; }
          }
          let [accounts, chainId] = await timed(Promise.all([
            evm.request({ method: "eth_accounts" }), evm.request({ method: "eth_chainId" })
          ]));
          if (!accounts?.length) {
            accounts = await evm.request({ method: "eth_requestAccounts" });
            chainId = await timed(evm.request({ method: "eth_chainId" }));
          }
          evmAddress = String(accounts?.[0] || "");
          evmChainId = String(chainId || "").toLowerCase();
          if (!evmAddress) throw Error("钱包未返回已授权账户");
        }
        // Optional source chains must not open a second, unnecessary Solana prompt.
        if (sol && (needsSolana || !evm)) {
          const result = sol.publicKey && sol.isConnected !== false ? { publicKey: sol.publicKey } : await sol.connect();
          solanaAddress = String(result?.publicKey || sol.publicKey || "");
          if (!solanaAddress) throw Error("钱包未返回 Solana 地址");
        }
        bind(key, evm, "evm"); bind(key, sol, "solana");
        if (options.targetChainId && evm && Number(evmChainId) !== Number(options.targetChainId)) {
          await evm.request({ method: "wallet_switchEthereumChain", params: [{ chainId: `0x${Number(options.targetChainId).toString(16)}` }] });
          evmChainId = String(await timed(evm.request({ method: "eth_chainId" })));
        }
        if (disconnectVersion !== s.disconnectVersion) throw Error("连接期间钱包已在本站断开，请重新连接");
        disabled.delete(key); s.revision++;
        Object.assign(s, { evmAddress, evmChainId, solanaAddress });
        write(`disabled:${key}`, "0");
        setActive(key); return snapshot(key);
      } catch (error) {
        if (wasDisabled && s.revision === connectRevision) { disabled.add(key); s.evmAddress = s.solanaAddress = ""; }
        throw error;
      } finally { uiState.connectingProviderKey = ""; notify(key, true); }
    })();
    connecting.set(key, job);
    try { return await job; } finally { connecting.delete(key); }
  }
  function disconnect(key) {
    const s = session(key); s.revision++; s.disconnectVersion++; disabled.add(key); write(`disabled:${key}`, "1");
    s.evmAddress = s.solanaAddress = s.evmChainId = "";
    notify(key, true);
  }
  async function currentAddresses(key = uiState.activeProviderKey) {
    session(key);
    if (disabled.has(key)) throw Error("钱包已在本站断开，请重新连接");
    const evm = provider("evm", key), sol = provider("solana", key);
    const revision = session(key).revision;
    const accounts = evm ? await timed(evm.request({ method: "eth_accounts" })) : [];
    if (disabled.has(key) || revision !== session(key).revision) throw Error("钱包状态已变化，请重新核对账户");
    return { evm: String(accounts?.[0] || ""), solana: String(sol?.isConnected !== false ? sol?.publicKey || "" : "") };
  }
  function render() {
    const active = session(uiState.activeProviderKey), list = connected();
    const utilities = topbarUtilitiesNode();
    if (utilities && !navButton) {
      navButton = document.createElement("button"); navButton.type = "button"; navButton.className = "nav-link global-wallet-trigger";
      navButton.dataset.walletPanelToggle = ""; utilities.appendChild(navButton);
    }
    if (navButton) navButton.textContent = list.length ? `钱包 · ${list.length} 已连接` : "钱包管理";
    if (!panel?.open) return;
    panel.querySelector("[data-global-wallet-list]").innerHTML = adapters().map(a => {
      const s = session(a.key), address = s.evmAddress || s.solanaAddress, installed = provider("evm", a.key) || provider("solana", a.key);
      return `<article class="global-wallet-card ${a.key === active.providerKey ? "is-active" : ""}"><span class="global-wallet-mark">${escape(a.mark)}</span><div><b>${escape(a.label)}</b><small>${address ? escape(short(address)) + " · 全站已连接" : installed ? "已检测到插件" : "未检测到插件"}</small></div><div class="global-wallet-actions">${address ? `<button data-global-wallet-select="${escape(a.key)}">${a.key === active.providerKey ? "当前钱包" : "设为当前"}</button><button data-global-wallet-disconnect="${escape(a.key)}">断开</button>` : installed ? `<button data-global-wallet-connect="${escape(a.key)}" ${connecting.has(a.key) ? "disabled" : ""}>${connecting.has(a.key) ? "等待钱包…" : "连接"}</button>` : a.installUrl ? `<a href="${escape(a.installUrl)}" target="_blank" rel="noopener noreferrer">安装</a>` : ""}${address ? `<button data-global-wallet-connect="${escape(a.key)}" data-switch-account>切换账户</button>` : ""}</div></article>`;
    }).join("");
  }
  function openPanel() {
    if (!panel) {
      panel = document.createElement("dialog"); panel.className = "global-wallet-dialog";
      panel.innerHTML = `<header><div><small>GLOBAL WALLET · 全站共享</small><h2>管理钱包</h2></div><button data-global-wallet-close aria-label="关闭钱包管理">×</button></header><p>切换页面无需重复连接。默认币安钱包，其次 OKX；多个钱包可同时保持连接。</p><div data-global-wallet-list></div><p data-global-wallet-message role="status">连接只读取公开账户。每笔交易仍需你在钱包确认，不保存私钥或签名。本站断开会同步到其他页面；彻底撤销站点权限请在钱包中操作。</p>`;
      document.body.appendChild(panel);
      panel.addEventListener("click", async event => {
        const b = event.target.closest("button"); if (!b || b.disabled) return;
        if (b.hasAttribute("data-global-wallet-close")) { panel.close(); return; }
        const message = panel.querySelector("[data-global-wallet-message]");
        try {
          if (b.dataset.globalWalletDisconnect) disconnect(b.dataset.globalWalletDisconnect);
          if (b.dataset.globalWalletSelect) setActive(b.dataset.globalWalletSelect);
          if (b.dataset.globalWalletConnect) { const key = b.dataset.globalWalletConnect; b.disabled = true; await connect(key, !provider("evm", key), false, { switchAccount: b.hasAttribute("data-switch-account") }); message.textContent = "钱包已连接，全站共享。交易仍需逐笔确认。"; }
        } catch (error) { message.textContent = Number(error.code) === 4001 ? "你取消了连接，已有钱包保持不变。" : error.message; }
        finally { render(); }
      });
    }
    panel.showModal(); render(); initialize();
  }
  function receive(key) {
    uiState.activeProviderKey = read("active") || uiState.activeProviderKey;
    if (key && key !== "active") {
      const s = session(key); s.revision++;
      if (read(`disabled:${key}`) === "1") { s.disconnectVersion++; disabled.add(key); s.evmAddress = s.solanaAddress = ""; }
      else disabled.delete(key);
      refreshOne(key);
    } else initialize();
    notify(key);
  }
  window.addEventListener("storage", event => { if (event.key?.startsWith(prefix)) { const key = event.key.slice(prefix.length); receive(key.startsWith("disabled:") ? key.slice(9) : "active"); } });
  try { channel = new BroadcastChannel("xingyun-wallet-v1"); channel.onmessage = event => receive(event.data?.key); } catch (_) {}
  window.addEventListener("eip6963:announceProvider", event => {
    const detail = event?.detail; if (!detail?.provider?.request) return;
    const key = `injected:${String(detail.info?.rdns || detail.info?.name || detail.info?.uuid || "unknown")}`;
    if (announcements.get(key)?.provider === detail.provider) return;
    announcements.set(key, detail); initialize();
  });
  document.addEventListener("click", event => { if (event.target.closest("[data-wallet-panel-toggle]")) { event.preventDefault(); event.stopPropagation(); openPanel(); } }, true);
  window.addEventListener("focus", () => initialize());
  window.addEventListener("pageshow", () => initialize());
  document.addEventListener("visibilitychange", () => warmBalances());
  window.addEventListener("pagehide", () => { clearTimeout(warmTimer); warmRequest?.abort(); });
  window.XingyunWallets = { adapters, adapter, provider, session, connected, snapshot, currentAddresses, connect, disconnect, setActive, initialize, openPanel, warmBalances, uiState,
    list: () => adapters().map(a => ({ ...a, installed: Boolean(provider("evm", a.key) || provider("solana", a.key)), ...snapshot(a.key) })), activeKey: () => uiState.activeProviderKey };
  window.XingyunMonitorWallets = window.XingyunWallets;
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  initialize(); render();
  document.addEventListener("DOMContentLoaded", render, { once: true });
})();
