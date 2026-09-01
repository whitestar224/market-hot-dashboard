(() => {
  const grid = document.querySelector("#priceWatchGrid");
  const form = document.querySelector("#watchAddForm");
  const symbolInput = document.querySelector("#watchSymbol");
  const refreshButton = document.querySelector("#watchRefresh");
  const statusNode = document.querySelector("#watchStatus");
  const modeButtons = [...document.querySelectorAll("[data-watch-mode]")];
  const headingLabel = document.querySelector("#watchHeadingLabel");
  const headingTitle = document.querySelector("#watchHeadingTitle");
  const headingDescription = document.querySelector("#watchHeadingDescription");
  const metricNodes = {
    total: document.querySelector("#watchTotal"),
    auto: document.querySelector("#watchAuto"),
    manual: document.querySelector("#watchManual"),
    near: document.querySelector("#watchNear"),
    oversold: document.querySelector("#watchOversold"),
    oversoldNear: document.querySelector("#watchOversoldNear")
  };
  let loading = false;
  let items = [];
  let structureItems = [];
  let newLowStructureItems = [];
  let structureLoading = false;
  let mappingItems = [];
  let mappingSummary = {};
  let mappingLoaded = false;
  let asterItems = [];
  let eventItems = [];
  let newsTradeItems = [];
  const NEWS_TRADE_PAGE_SIZE = 10;
  const STRUCTURE_SYNC_INTERVAL_MS = 3_000;
  const STRUCTURE_INTERVALS = [
    { key: "1m", label: "1m", name: "1分钟" },
    { key: "5m", label: "5m", name: "5分钟" },
    { key: "15m", label: "15m", name: "15分钟" },
    { key: "1h", label: "1h", name: "1小时" },
    { key: "4h", label: "4h", name: "4小时" },
    { key: "1d", label: "日", name: "日线" },
  ];
  let newsTradePage = 1;
  let newsTradeSearchState = { query: "", loading: false, preview: null, error: "", message: "" };
  let newsTradeExecutionNotice = null;
  let eventSummary = {};
  let eventExecution = { configured: false, liveEnabled: false, maxOrderUsdt: 200, missingConfiguration: [] };
  const announcedEvmWalletProviders = new Map();
  const boundEvmWalletProviders = new WeakSet();
  let okxWalletState = {
    providerKey: "",
    evmAddress: "",
    evmChainId: "",
    solanaAddress: "",
    connecting: false,
    listenersBound: false,
    solanaListenersBound: false
  };
  window.addEventListener("eip6963:announceProvider", (event) => {
    const detail = event?.detail;
    if (!detail?.provider?.request) return;
    const key = String(detail.info?.uuid || detail.info?.rdns || detail.info?.name || announcedEvmWalletProviders.size);
    announcedEvmWalletProviders.set(key, detail);
    initializeOkxWallet().catch(() => {});
  });
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  let eventLoaded = false;
  let wechatMonitorPayload = { monitors: [], opportunities: [], summary: {} };
  let personalXPayload = { account: null, items: [], summary: {}, pending: true };
  let personalXLoaded = false;
  let personalXStream = null;
  let personalXStreamReady = false;
  let chainEcosystemPayload = {
    chains: [],
    selectedChain: null,
    markets: [],
    projects: [],
    potentialProjects: [],
    alerts: [],
    sourceHealth: [],
    warnings: []
  };
  let chainEcosystemLoaded = false;
  let chainActionLoading = false;
  let chainEcosystemRequestId = 0;
  let selectedChainSlug = new URLSearchParams(window.location.search).get("chain") || "";
  let lastStructureLoadAt = 0;
  let lastNewLowStructureLoadAt = 0;
  let lastMappingLoadAt = 0;
  let lastAsterLoadAt = 0;
  let lastEventLoadAt = 0;
  let lastWechatLoadAt = 0;
  let lastChainEcosystemLoadAt = 0;
  let currentSummary = {};
  const requestedMode = new URLSearchParams(window.location.search).get("mode");
  const supportedModes = ["prior", "oversold", "structure", "newlow", "mapping", "aster", "events", "news", "wechat", "personalx", "chains"];
  let currentMode = supportedModes.includes(requestedMode) ? requestedMode : "prior";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function walletProviderLabel(providerKey) {
    return providerKey === "binance" ? "Binance Wallet" : "OKX Wallet";
  }

  function matchesWalletProvider(providerKey, provider, info = {}) {
    const identity = `${info?.rdns || ""} ${info?.name || ""}`.toLowerCase();
    if (providerKey === "binance") {
      return Boolean(provider?.isBinance || provider?.isBinanceWallet || /binance/.test(identity));
    }
    return Boolean(provider?.isOkxWallet || /(^|[.\s])okx([.\s]|$)/.test(identity));
  }

  function okxWalletProvider(namespace = "evm", requestedProvider = "") {
    const providerKey = requestedProvider || okxWalletState.providerKey || "okx";
    if (namespace === "solana") return providerKey === "okx" ? window.okxwallet?.solana || null : null;
    if (providerKey === "okx" && window.okxwallet?.request) return window.okxwallet;
    if (providerKey === "binance") {
      if (window.binancew3w?.ethereum?.request) return window.binancew3w.ethereum;
      if (window.BinanceChain?.request) return window.BinanceChain;
    }
    const injected = Array.isArray(window.ethereum?.providers)
      ? window.ethereum.providers
      : (window.ethereum?.request ? [window.ethereum] : []);
    for (const provider of injected) {
      if (provider?.request && matchesWalletProvider(providerKey, provider)) return provider;
    }
    for (const detail of announcedEvmWalletProviders.values()) {
      if (matchesWalletProvider(providerKey, detail.provider, detail.info)) return detail.provider;
    }
    return null;
  }

  function installedWalletProviders() {
    return ["okx", "binance"].filter((providerKey) => Boolean(okxWalletProvider("evm", providerKey)));
  }

  function shortWalletAddress(address) {
    const value = String(address || "");
    return value.length > 14 ? `${value.slice(0, 7)}…${value.slice(-5)}` : value;
  }

  function okxWalletChainLabel(chainId) {
    const normalized = String(chainId || "").toLowerCase();
    return ({
      "0x1": "Ethereum",
      "0x38": "BNB Chain",
      "0x2105": "Base",
      "0xa4b1": "Arbitrum",
      "0x1237": "Robinhood Chain"
    })[normalized] || (normalized ? `Chain ${normalized}` : "链待确认");
  }

  function okxWalletToolbarTemplate() {
    const installedProviders = installedWalletProviders();
    const providerKey = okxWalletState.providerKey || installedProviders[0] || "okx";
    const providerLabel = walletProviderLabel(providerKey);
    const installed = Boolean(installedProviders.length || okxWalletProvider("solana", "okx"));
    const address = okxWalletState.evmAddress || okxWalletState.solanaAddress;
    const chain = okxWalletState.evmAddress
      ? okxWalletChainLabel(okxWalletState.evmChainId)
      : (okxWalletState.solanaAddress ? "Solana" : "未连接");
    return `
      <section class="news-trade-wallet ${address ? "is-connected" : ""}">
        <span class="news-trade-wallet-mark">${providerKey === "binance" ? "BN" : "OKX"}</span>
        <span class="news-trade-wallet-copy">
          <b>${address ? `${escapeHtml(providerLabel)} · ${escapeHtml(chain)} · ${escapeHtml(shortWalletAddress(address))}` : `${escapeHtml(providerLabel)} 授权`}</b>
          <em>${address ? "地址已由钱包授权；每笔交易仍需在钱包中确认" : "支持 OKX / Binance Wallet，只读取公开地址和当前链"}</em>
        </span>
        ${installed
          ? `<span class="news-trade-wallet-actions">
              <button type="button" data-wallet-connect data-wallet-provider="${providerKey}" data-wallet-switch-account="${address ? "true" : "false"}" ${okxWalletState.connecting ? "disabled" : ""}>${okxWalletState.connecting ? "等待钱包…" : (address ? "切换账户" : `连接 ${escapeHtml(providerLabel)}`)}</button>
              ${installedProviders.filter((key) => key !== providerKey).map((key) => `<button class="is-secondary" type="button" data-wallet-connect data-wallet-provider="${key}" ${okxWalletState.connecting ? "disabled" : ""}>改用 ${escapeHtml(walletProviderLabel(key))}</button>`).join("")}
            </span>`
          : `<span class="news-trade-wallet-actions"><a href="https://web3.okx.com/wallet/download" target="_blank" rel="noreferrer noopener">安装 OKX</a><a href="https://www.binance.com/en/web3wallet" target="_blank" rel="noreferrer noopener">安装 Binance</a></span>`}
      </section>`;
  }

  function renderOkxWalletChange() {
    if (currentMode === "news" && eventLoaded) renderEventMonitor();
  }

  async function initializeOkxWallet() {
    const installedProviders = installedWalletProviders();
    for (const providerKey of installedProviders) {
      const evmProvider = okxWalletProvider("evm", providerKey);
      try {
        const [accounts, chainId] = await Promise.all([
          evmProvider.request({ method: "eth_accounts" }),
          evmProvider.request({ method: "eth_chainId" })
        ]);
        const address = Array.isArray(accounts) ? String(accounts[0] || "") : "";
        if (address && (!okxWalletState.evmAddress || okxWalletState.providerKey === providerKey)) {
          okxWalletState.providerKey = providerKey;
          okxWalletState.evmAddress = address;
          okxWalletState.evmChainId = String(chainId || "").toLowerCase();
        }
      } catch (_) {
        // Passive detection must never interrupt the monitor page.
      }
      if (!boundEvmWalletProviders.has(evmProvider) && typeof evmProvider.on === "function") {
        evmProvider.on("accountsChanged", (accounts) => {
          okxWalletState.providerKey = providerKey;
          okxWalletState.evmAddress = Array.isArray(accounts) ? String(accounts[0] || "") : "";
          renderOkxWalletChange();
        });
        evmProvider.on("chainChanged", (chainId) => {
          okxWalletState.providerKey = providerKey;
          okxWalletState.evmChainId = String(chainId || "").toLowerCase();
          renderOkxWalletChange();
        });
        boundEvmWalletProviders.add(evmProvider);
      }
    }
    if (!okxWalletState.providerKey && installedProviders.length) okxWalletState.providerKey = installedProviders[0];
    const solanaProvider = okxWalletProvider("solana", "okx");
    if (solanaProvider?.isConnected && solanaProvider.publicKey) {
      okxWalletState.solanaAddress = solanaProvider.publicKey.toString();
    }
    if (solanaProvider && !okxWalletState.solanaListenersBound && typeof solanaProvider.on === "function") {
      solanaProvider.on("accountChanged", (publicKey) => {
        okxWalletState.solanaAddress = publicKey ? publicKey.toString() : "";
        renderOkxWalletChange();
      });
      solanaProvider.on("disconnect", () => {
        okxWalletState.solanaAddress = "";
        renderOkxWalletChange();
      });
      okxWalletState.solanaListenersBound = true;
    }
    renderOkxWalletChange();
  }

  async function connectOkxWallet(opportunity = null, options = {}) {
    const namespace = opportunity?.chain === "sol" ? "solana" : "evm";
    const providerKey = namespace === "solana" ? "okx" : (options.providerKey || okxWalletState.providerKey || installedWalletProviders()[0] || "okx");
    const providerLabel = walletProviderLabel(providerKey);
    const provider = okxWalletProvider(namespace, providerKey);
    if (!provider) throw new Error(`未检测到 ${providerLabel}；请安装插件后在浏览器中打开本页`);
    okxWalletState.connecting = true;
    renderOkxWalletChange();
    try {
      if (namespace === "solana") {
        if (options.switchAccount && typeof provider.disconnect === "function") {
          try { await provider.disconnect(); } catch (_) {}
        }
        const result = await provider.connect();
        okxWalletState.providerKey = "okx";
        okxWalletState.solanaAddress = String(result?.publicKey || provider.publicKey || "");
        if (!okxWalletState.solanaAddress) throw new Error("OKX Wallet 未返回 Solana 地址");
        return;
      }
      const previousAddress = okxWalletState.providerKey === providerKey ? okxWalletState.evmAddress : "";
      if (options.switchAccount) {
        try {
          await provider.request({ method: "wallet_requestPermissions", params: [{ eth_accounts: {} }] });
        } catch (error) {
          if (Number(error?.code) === 4001) throw error;
          if (![-32601, 4200].includes(Number(error?.code))) throw error;
        }
      }
      const accounts = await provider.request({ method: "eth_requestAccounts" });
      okxWalletState.providerKey = providerKey;
      okxWalletState.evmAddress = Array.isArray(accounts) ? String(accounts[0] || "") : "";
      okxWalletState.evmChainId = String(await provider.request({ method: "eth_chainId" }) || "").toLowerCase();
      if (!okxWalletState.evmAddress) throw new Error(`${providerLabel} 未返回账户地址`);
      if (options.switchAccount && previousAddress && previousAddress.toLowerCase() === okxWalletState.evmAddress.toLowerCase()) {
        statusNode.textContent = `${providerLabel} 仍在使用原账户；请在钱包弹窗或扩展中选择另一账户`;
      }
      const targetChainId = Number(opportunity?.chainId);
      const targetHex = Number.isInteger(targetChainId) && targetChainId > 0 ? `0x${targetChainId.toString(16)}` : "";
      if (targetHex && okxWalletState.evmChainId !== targetHex) {
        try {
          await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: targetHex }] });
          okxWalletState.evmChainId = String(await provider.request({ method: "eth_chainId" }) || targetHex).toLowerCase();
        } catch (error) {
          statusNode.textContent = Number(error?.code) === 4902
            ? `${providerLabel} 尚未添加 ${opportunity?.chainLabel || "目标链"}，请在钱包中添加后再确认交易`
            : `钱包已连接，请在 ${providerLabel} 中切换到 ${opportunity?.chainLabel || "目标链"}`;
        }
      }
    } finally {
      okxWalletState.connecting = false;
      renderOkxWalletChange();
    }
  }

  function okxWalletAuthorization(opportunity) {
    const solana = opportunity?.chain === "sol";
    return {
      walletProvider: okxWalletState.providerKey || "okx",
      walletNamespace: solana ? "solana" : "evm",
      walletAddress: solana ? okxWalletState.solanaAddress : okxWalletState.evmAddress,
      walletChainId: solana ? "501" : okxWalletState.evmChainId
    };
  }

  function compactPrice(value) {
    const price = Number(value);
    if (!Number.isFinite(price) || price <= 0) return "--";
    if (price >= 1000) return `$${price.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
    if (price >= 1) return `$${price.toLocaleString("en-US", { maximumFractionDigits: 4 })}`;
    return `$${price.toLocaleString("en-US", { maximumSignificantDigits: 7 })}`;
  }

  function structureTone(pattern) {
    if (/横盘起飞|箱体|拐点|回踩|再启动/.test(pattern)) return "bullish";
    if (/三角|降楔|趋势线/.test(pattern)) return "focus";
    if (/盘整|预备/.test(pattern)) return "range";
    return "muted";
  }

  function structureFrameTemplate(frame) {
    const pattern = frame.pattern || "数据不足";
    const tone = structureTone(pattern);
    const confidence = Number(frame.confidence) || 0;
    const levelText = frame.support || frame.resistance
      ? `${compactPrice(frame.support)} / ${compactPrice(frame.resistance)}`
      : "--";
    return `
      <div class="price-structure-row is-${tone}" title="${escapeHtml(frame.summary || "")}">
        <b class="price-structure-time">${escapeHtml(frame.label || frame.key || "--")}</b>
        <span class="price-structure-pattern">${escapeHtml(pattern)}</span>
        <span class="price-structure-stage">${escapeHtml(frame.stage || "观察")}<em>${confidence ? `${confidence}%` : "--"}</em></span>
        <span class="price-structure-level"><em>支撑 / 压力</em><b>${levelText}</b></span>
      </div>`;
  }

  function structureIntervalState(item, interval) {
    const state = item.structureIntervalStates?.[interval.key];
    if (state && typeof state === "object") return state;
    if (interval.key === "1m") {
      return {
        enabled: Boolean(item.structure1mEnabled),
        mode: item.structure1mMode || "auto-off",
        label: item.structure1mLabel || "按原规则关闭",
      };
    }
    return { enabled: true, mode: "default-on", label: "默认开启" };
  }

  function structureIntervalControls(item) {
    const visibleIntervals = currentMode === "newlow"
      ? STRUCTURE_INTERVALS.filter((interval) => ["1h", "4h"].includes(interval.key))
      : STRUCTURE_INTERVALS;
    const buttons = visibleIntervals.map((interval) => {
      const state = structureIntervalState(item, interval);
      const enabled = Boolean(state.enabled);
      const title = `${interval.name}结构预判与播报${enabled ? "已开启" : "已关闭"} · ${state.label || "手动设置"}`;
      return `<button class="price-structure-interval-toggle ${enabled ? "is-on" : "is-off"}" type="button" title="${escapeHtml(title)}" aria-label="${escapeHtml(item.symbol)} ${escapeHtml(interval.name)}结构播报${enabled ? "已开启" : "已关闭"}" aria-pressed="${enabled}" data-structure-interval="${interval.key}" data-symbol="${escapeHtml(item.symbol)}" data-enabled="${enabled}"><span>${interval.label}</span><b>${enabled ? "开" : "关"}</b><i></i></button>`;
    }).join("");
    return `<div class="price-structure-intervals" role="group" aria-label="${escapeHtml(item.symbol)} 各周期结构播报开关"><em>周期播报</em>${buttons}</div>`;
  }

  function structureCardTemplate(item) {
    const newLowCard = currentMode === "newlow";
    const primaryName = newLowCard ? (item.name || item.symbol) : item.symbol;
    const secondaryName = newLowCard ? item.symbol : (item.name || item.symbol);
    const icon = item.icon
      ? `<img src="${escapeHtml(item.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.price-watch-icon').classList.add('is-fallback');this.remove()" />`
      : "";
    const frames = Array.isArray(item.frames) ? item.frames : [];
    const adaptiveContext = item.adaptiveContext && typeof item.adaptiveContext === "object"
      ? item.adaptiveContext
      : null;
    const lowContext = item.lowPositionContext && typeof item.lowPositionContext === "object"
      ? item.lowPositionContext
      : null;
    const membershipSources = newLowCard
      ? (Array.isArray(item.newCoinSources) ? item.newCoinSources.filter((source) => ["Binance", "OKX", "Gate", "HTX"].includes(source)) : [])
      : (Array.isArray(item.structureMembershipSources) ? item.structureMembershipSources : []);
    const membershipLabel = membershipSources.length
      ? `入池 · ${newLowCard ? `新币池(${membershipSources.join(",")})` : membershipSources.join("/")}`
      : "";
    const dataProviderLabel = /aster/i.test(item.provider || "")
      ? "K线 · Aster备用"
      : (item.provider ? `K线 · ${item.provider}` : "等待行情");
    const providerLabel = [
      membershipLabel,
      dataProviderLabel,
      adaptiveContext?.label ? `临盘应变 · ${adaptiveContext.label}` : "",
      lowContext?.qualified ? `上市 ${Math.round(Number(lowContext.ageDays) || 0)}天 · 较高点回撤 ${Number(lowContext.drawdownPct || 0).toFixed(0)}%` : "",
    ].filter(Boolean).join(" · ");
    const content = frames.length
      ? frames.map(structureFrameTemplate).join("")
      : `<div class="price-structure-error"><b>多周期行情暂不可用</b><span>${escapeHtml(item.error || "请稍后刷新")}</span></div>`;
    return `
      <article class="price-structure-card${newLowCard ? " is-new-low" : ""}" data-symbol="${escapeHtml(item.symbol)}">
        <header>
          <span class="price-watch-icon">${icon}<b>${escapeHtml(item.symbol.slice(0, 2))}</b></span>
          <span class="price-watch-asset">
            <strong>${escapeHtml(primaryName)}</strong>
            <em>${escapeHtml(secondaryName)}</em>
          </span>
          ${newLowCard ? "" : `<span class="price-structure-price">${compactPrice(item.currentPrice)}</span>`}
          ${newLowCard ? "" : `<span class="price-watch-origin" title="${escapeHtml(adaptiveContext?.sourceText || "")}">${escapeHtml(providerLabel)}</span>`}
          <button class="price-watch-remove" type="button" title="从${newLowCard ? "新币低位" : "多周期"}结构监控剔除 ${escapeHtml(item.symbol)}" aria-label="从${newLowCard ? "新币低位" : "多周期"}结构监控剔除 ${escapeHtml(item.symbol)}" data-exclude-structure="${escapeHtml(item.symbol)}">×</button>
        </header>
        ${structureIntervalControls(item)}
        <div class="price-structure-rows">${content}</div>
      </article>`;
  }

  function renderStructures() {
    const newLow = currentMode === "newlow";
    const visibleStructureItems = newLow ? newLowStructureItems : structureItems;
    grid.classList.add("is-structure");
    grid.classList.remove("is-mapping", "is-aster", "is-events", "is-wechat", "is-chains");
    if (!visibleStructureItems.length) {
      grid.innerHTML = `
        <div class="price-watch-empty">
          <b>${newLow ? "正在轮询近一年新币低位结构" : "正在识别 AICoin 热门币结构"}</b>
          <span>${newLow ? "不依赖热榜和成交量，按后台低频轮询逐个识别深跌后的低位横盘、收敛与抬高低点。" : "同步读取 1分钟、5分钟、15分钟、1小时、4小时和日线行情。"}</span>
        </div>`;
      return;
    }
    grid.innerHTML = (newLow
      ? newLowStructureItems.map(structureCardTemplate)
      : structureItems.map(structureCardTemplate)).join("");
  }

  function mappingChangeClass(value) {
    const change = Number(value);
    if (!Number.isFinite(change) || change === 0) return "is-flat";
    return change > 0 ? "is-up" : "is-down";
  }

  function mappingIcon(item) {
    const symbol = String(item.symbol || "--");
    const image = item.icon
      ? `<img src="${escapeHtml(item.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()" />`
      : "";
    return `<span class="rotation-token-icon">${image}<b>${escapeHtml(symbol.slice(0, 2))}</b></span>`;
  }

  function mappingCandidateTemplate(candidate, leaderSymbol) {
    const reasons = Array.isArray(candidate.reasons) ? candidate.reasons.join(" · ") : "同题材候选";
    const href = candidate.url ? `href="${escapeHtml(candidate.url)}" target="_blank" rel="noreferrer noopener"` : "";
    return `
      <a class="rotation-candidate-row" ${href}>
        ${mappingIcon(candidate)}
        <span class="rotation-candidate-name">
          <strong>${escapeHtml(candidate.symbol || "--")}</strong>
          <em>${escapeHtml(candidate.name || candidate.symbol || "--")} · ${escapeHtml(candidate.exchange || "等待行情")}</em>
        </span>
        <span class="rotation-candidate-reason">${escapeHtml(reasons)}</span>
        <span class="rotation-candidate-market">
          <strong>${escapeHtml(candidate.price || "--")}</strong>
          <em class="${mappingChangeClass(candidate.changeValue)}">${escapeHtml(candidate.change || "--")}</em>
        </span>
        <span class="rotation-score"><b>${escapeHtml(candidate.score || "--")}</b><em>映射分</em></span>
        <span class="rotation-lag">较 ${escapeHtml(leaderSymbol)} ${Number(candidate.lagPct) > 0 ? `落后 ${Number(candidate.lagPct).toFixed(1)}%` : "同步活跃"}</span>
      </a>`;
  }

  function mappingCardTemplate(item) {
    const leader = item.leader || {};
    const familyLabels = Array.isArray(item.familyLabels) ? item.familyLabels : [];
    const themes = Array.isArray(item.themes) ? item.themes : [];
    const candidates = Array.isArray(item.candidates) ? item.candidates : [];
    const impulseGain = Number(leader.impulseGainPct);
    const impulseLabel = Number.isFinite(impulseGain) ? `主升 +${impulseGain.toFixed(1)}%` : "主升幅度已确认";
    return `
      <article class="rotation-map-card">
        <header class="rotation-map-header">
          <span><b>${escapeHtml(item.family || "题材家族")}</b><em>${escapeHtml(item.chain || "多链")}</em></span>
          <small>${escapeHtml(item.disclaimer || "题材映射不代表必然补涨")}</small>
        </header>
        <div class="rotation-map-flow">
          <div class="rotation-stage is-leader">
            <em>01 · 本尊</em>
            <span class="rotation-leader-line">${mappingIcon(leader)}<b>${escapeHtml(leader.symbol || "--")}</b></span>
            <span class="rotation-leader-gain">${escapeHtml(impulseLabel)}</span>
            <small>${escapeHtml(leader.source || "热门榜")} #${escapeHtml(leader.rank || "-")} · ${escapeHtml(leader.change || "--")}</small>
          </div>
          <i aria-hidden="true">→</i>
          <div class="rotation-stage">
            <em>02 · 家族</em>
            <div class="rotation-tags">${familyLabels.map((label) => `<span>${escapeHtml(label)}</span>`).join("")}</div>
          </div>
          <i aria-hidden="true">→</i>
          <div class="rotation-stage">
            <em>03 · 题材映射</em>
            <div class="rotation-tags is-theme">${themes.map((theme) => `<span>${escapeHtml(theme)}</span>`).join("")}</div>
          </div>
          <i aria-hidden="true">→</i>
          <div class="rotation-stage is-count">
            <em>04 · 潜在补涨</em>
            <b>${candidates.length || "…"}</b>
            <small>${candidates.length ? "仅保留有真实行情的候选" : "同题材与同生态候选持续分析中"}</small>
          </div>
        </div>
        <div class="rotation-candidate-list">
          ${candidates.length
            ? candidates.map((candidate) => mappingCandidateTemplate(candidate, leader.symbol || "龙头")).join("")
            : `<div class="rotation-candidate-empty"><b>已纳入实时龙头监控</b><span>主升幅度超过 300%，正在持续分析同题材、同生态和资金扩散候选。</span></div>`}
        </div>
      </article>`;
  }

  function renderMappings() {
    grid.classList.remove("is-structure", "is-aster", "is-events", "is-wechat", "is-personal-x", "is-chains");
    grid.classList.add("is-mapping");
    if (!mappingLoaded) {
      grid.innerHTML = `
        <div class="price-watch-empty">
          <b>正在建立龙头补涨映射</b>
          <span>核对本尊、家族、同题材叙事和真实合约行情。</span>
        </div>`;
      return;
    }
    if (!mappingItems.length) {
      const scanned = Number(mappingSummary.hotScanned) || 0;
      grid.innerHTML = `
        <div class="price-watch-empty">
          <b>当前没有达到 300% 主升阈值的热门币</b>
          <span>已扫描 ${escapeHtml(scanned)} 个热门标的，后台会继续实时分析。</span>
        </div>`;
      return;
    }
    grid.innerHTML = mappingItems.map(mappingCardTemplate).join("");
  }

  function asterDate(value) {
    const time = Number(value);
    if (!time) return "时间待确认";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).format(new Date(time));
  }

  function asterContractTemplate(item) {
    const pending = item.contractStatus === "PENDING_TRADING" || item.status === "待上线";
    const official = Boolean(item.officialAnnouncement);
    const officialX = item.officialChannel === "x";
    const assetLabel = Array.isArray(item.symbols) && item.symbols.length
      ? item.symbols.join(" · ")
      : (item.baseAsset || item.symbol || "--");
    return `
      <a class="aster-contract-card ${pending ? "is-pending" : "is-trading"}" href="${escapeHtml(item.url || "https://www.asterdex.com/en")}" target="_blank" rel="noreferrer noopener">
        <span class="aster-contract-mark">AS</span>
        <span class="aster-contract-name">
          <strong>${escapeHtml(assetLabel)}</strong>
          <em>${officialX ? "Aster 官方 X 上新" : official ? "Aster 官网上新公告" : "上新发现 · 待公告页同步"}${item.subtitle ? ` · ${escapeHtml(item.subtitle)}` : ""}</em>
        </span>
        <span class="aster-contract-status">
          <b>${officialX ? "官方 X" : official ? "官方公告" : pending ? "待上线" : "已上线"}</b>
          <em>${official ? "发布" : "上线"} ${asterDate(item.onboardDate || item.date)}</em>
        </span>
        <span class="aster-contract-action">${officialX ? "查看 X" : official ? "查看公告" : "打开 Aster"}</span>
      </a>`;
  }

  function renderAsterContracts() {
    grid.classList.remove("is-structure", "is-mapping", "is-events", "is-wechat", "is-personal-x", "is-chains");
    grid.classList.add("is-aster");
    if (!asterItems.length) {
      grid.innerHTML = `
        <div class="price-watch-empty">
          <b>暂无新的 Aster 永续合约公告</b>
          <span>后台仍在持续比对公开合约接口；新合约首次出现后会立即生成公告卡并播报。</span>
        </div>`;
      return;
    }
    grid.innerHTML = asterItems.map(asterContractTemplate).join("");
  }

  function eventScoreTone(score) {
    const value = Number(score) || 0;
    if (value >= 84) return "is-critical";
    if (value >= 72) return "is-strong";
    return "is-watch";
  }

  function newsTradePhaseMeta(item) {
    const allowed = new Set(["understanding", "pre-fermentation", "fermented", "expired"]);
    const code = allowed.has(String(item?.newsTradePhase || "")) ? String(item.newsTradePhase) : "understanding";
    const labels = {
      "understanding": "0–6h 先手理解",
      "pre-fermentation": "6–24h 预发酵",
      "fermented": "已发酵 · 仅复盘",
      "expired": "超过24h · 已错过"
    };
    return { code, label: String(item?.newsTradePhaseLabel || labels[code]) };
  }

  function newsTradeStageMeta(item) {
    const labels = {
      "budding": "萌芽",
      "accelerating": "加速传播",
      "breakout": "破圈",
      "onchain-mapping": "链上映射",
      "peak": "高潮",
      "decline": "衰退"
    };
    const code = String(item?.eventStage || "budding");
    return { code, label: String(item?.eventStageLabel || labels[code] || "萌芽") };
  }

  function newsTradeScoreTagsTemplate(item) {
    const eventPoints = item?.eventHeatBreakdown?.points && typeof item.eventHeatBreakdown.points === "object"
      ? item.eventHeatBreakdown.points
      : {};
    const eventWeights = item?.eventHeatBreakdown?.weights && typeof item.eventHeatBreakdown.weights === "object"
      ? item.eventHeatBreakdown.weights
      : {};
    const tags = [
      ["大瓜", Number(eventPoints.bigGossip) || 0, Number(eventWeights.bigGossip) || 20],
      ["增长速度", Number(eventPoints.velocity) || 0, Number(eventWeights.velocity) || 20],
      ["跨平台", Number(eventPoints.crossPlatform) || 0, Number(eventWeights.crossPlatform) || 12],
      ["新奇反差", Number(eventPoints.noveltyContrast) || 0, Number(eventWeights.noveltyContrast) || 12],
      ["群体参与", Number(eventPoints.participation) || 0, Number(eventWeights.participation) || 12],
      ["符号传播", Number(eventPoints.symbolizability) || 0, Number(eventWeights.symbolizability) || 12],
      ["后续剧情", Number(eventPoints.followupStory) || 0, Number(eventWeights.followupStory) || 12]
    ];
    return `<div class="news-trade-score-tags">${tags.map(([label, score, weight]) => `
      <span class="${score > 0 ? "is-hit" : ""}" title="${escapeHtml(label)} ${score.toFixed(1)} / ${weight}"><em>${escapeHtml(label)}</em><b>${score.toFixed(score % 1 ? 1 : 0)}<i>/${weight}</i></b></span>
    `).join("")}</div>`;
  }

  function newsTradeIntelligenceTemplate(item) {
    const stage = newsTradeStageMeta(item);
    const metrics = item?.topicMetrics && typeof item.topicMetrics === "object" ? item.topicMetrics : {};
    const counts = metrics?.contentCounts && typeof metrics.contentCounts === "object" ? metrics.contentCounts : {};
    const platforms = Array.isArray(metrics.platforms) ? metrics.platforms : [];
    const newsKeywords = Array.isArray(item?.newsKeywords) ? item.newsKeywords.slice(0, 12) : [];
    const labels = Array.from(new Set([
      ...(Array.isArray(item?.counterConsensusProfile?.labels) ? item.counterConsensusProfile.labels : []),
      ...(Array.isArray(item?.curiosityProfile?.labels) ? item.curiosityProfile.labels : [])
    ])).slice(0, 9);
    const catalyst = item?.latestCatalyst && typeof item.latestCatalyst === "object" ? item.latestCatalyst : {};
    const watchReasons = Array.isArray(item?.watchReasons) ? item.watchReasons : [];
    const invalidations = Array.isArray(item?.invalidationConditions) ? item.invalidationConditions : [];
    const sources = Array.isArray(item?.informationSources) ? item.informationSources.slice(0, 8) : [];
    const tier = item?.candidateTier === "trade-candidate" ? "交易候选" : "事件观察";
    return `
      <section class="news-trade-intelligence">
        <div class="news-trade-dual-score">
          <span class="is-event"><b>${Math.round(Number(item?.eventHeatScore) || 0)}</b><em>事件热度 / 100</em></span>
          <span class="is-onchain"><b>${Math.round(Number(item?.onchainTradeScore) || 0)}</b><em>链上可交易 / 100</em></span>
          <span class="is-tier ${item?.candidateTier === "trade-candidate" ? "is-ready" : ""}"><b>${escapeHtml(tier)}</b><em>${escapeHtml(item?.eventType || "事件驱动")}</em></span>
        </div>
        <div class="news-trade-intel-grid">
          <span><em>事件阶段</em><b>${escapeHtml(stage.label)}</b></span>
          <span><em>热度增速</em><b>${Math.round(Number(metrics.velocityScore) || 0)} / 100</b></span>
          <span><em>内容窗口</em><b>${Number(counts["1h"]) || 0} / ${Number(counts["6h"]) || 0} / ${Number(counts["24h"]) || 0}</b><small>1h / 6h / 24h</small></span>
          <span><em>传播平台</em><b>${escapeHtml(platforms.join(" · ") || "待扩散")}</b></span>
          <span class="is-wide"><em>核心传播逻辑</em><b>${escapeHtml(item?.corePropagationLogic || "等待更多传播链路证据")}</b></span>
          <span class="is-wide"><em>最新催化</em><b>${escapeHtml(catalyst.title || "暂无新剧情")}</b></span>
        </div>
        ${newsKeywords.length ? `<div class="news-trade-keywords"><em>新闻关键词</em>${newsKeywords.map((keyword) => `<span>${escapeHtml(keyword)}</span>`).join("")}</div>` : ""}
        ${labels.length ? `<div class="news-trade-model-tags">${labels.map((label) => `<span>${escapeHtml(label)}</span>`).join("")}</div>` : ""}
        <div class="news-trade-judgement">
          <span><em>关注理由</em><b>${escapeHtml(watchReasons.join(" / ") || "等待事件或链上质量继续提升")}</b></span>
          <span><em>失效条件</em><b>${escapeHtml(invalidations.slice(0, 3).join(" / ") || "暂无")}</b></span>
        </div>
        ${sources.length ? `<details class="news-trade-sources"><summary>信息来源 ${sources.length} 条 · 更新 ${relativeTime(item?.updatedAt)}</summary>${sources.map((source) => `
          <a href="${escapeHtml(source?.url || "#")}" target="_blank" rel="noreferrer noopener"><b>${escapeHtml(source?.source || "来源")}</b><span>${escapeHtml(source?.title || "未命名信息")}</span><em>${escapeHtml(source?.claimStatus || "source-reported")} · 发布 ${relativeTime(source?.publishedAt || source?.timestamp)} · 抓取 ${relativeTime(source?.capturedAt)}</em></a>
        `).join("")}</details>` : ""}
      </section>`;
  }

  function newsTradeCandidateTemplate(candidate, index, context = {}) {
    const primary = index === 0;
    const symbol = String(candidate?.symbol || candidate?.name || "待确认");
    const name = String(candidate?.name || "");
    const score = Math.round(Number(candidate?.combinedWeight ?? candidate?.candidateScore) || 0);
    const onchainScore = Math.round(Number(candidate?.onchainTradeScore) || 0);
    const transactions = Number(candidate?.transactions24h) || 0;
    const contract = String(candidate?.contractAddress || "");
    const contractLabel = contract ? `${contract.slice(0, 7)}…${contract.slice(-5)}` : "合约待确认";
    const tradeUrls = candidate?.tradeUrls && typeof candidate.tradeUrls === "object" ? candidate.tradeUrls : {};
    const routeLinks = Object.entries(tradeUrls)
      .map(([venue, url]) => [venue, safeExternalUrl(url)])
      .filter(([, url]) => Boolean(url))
      .slice(0, primary ? 3 : 1);
    const eventId = String(context.eventId || "");
    const systemRecommended = Boolean(context.executionEligible);
    const security = candidate?.security && typeof candidate.security === "object" ? candidate.security : {};
    const securityStatus = ["safe", "warning", "danger", "pending"].includes(String(security.status || ""))
      ? String(security.status)
      : "pending";
    const securityVerified = Boolean(security.verified);
    const securityBlocked = Boolean(security.hardBlocked || securityStatus === "danger");
    const securityScore = Number.isFinite(Number(security.score)) ? Math.round(Number(security.score)) : "--";
    const buyTaxValue = Number(security.buyTaxPct);
    const sellTaxValue = Number(security.sellTaxPct);
    const buyTax = Number.isFinite(buyTaxValue) ? `${buyTaxValue.toFixed(2)}%` : "待核验";
    const sellTax = Number.isFinite(sellTaxValue) ? `${sellTaxValue.toFixed(2)}%` : "待核验";
    const securityReasons = securityBlocked
      ? (Array.isArray(security.hardBlockReasons) ? security.hardBlockReasons : [])
      : (Array.isArray(security.warnings) ? security.warnings : []);
    const securityLabel = securityBlocked
      ? "禁止买入"
      : securityStatus === "safe"
        ? `安全 ${securityScore}`
        : securityStatus === "warning"
          ? `注意 ${securityScore}`
          : "核验中";
    const securityTitle = [
      security.label || (securityVerified ? "安全检查已完成" : "正在核验合约与退出能力"),
      `买入税 ${buyTax}`,
      `卖出税 ${sellTax}`,
      security.provider || "",
      ...securityReasons.slice(0, 2),
    ].filter(Boolean).join(" · ");
    const visibleRisks = (Array.isArray(candidate?.riskFlags) ? candidate.riskFlags : [])
      .filter((risk) => !(
        securityVerified
        && securityStatus === "safe"
        && /安全待核验|卖出能力需复核|合约待核验/.test(String(risk || ""))
      ))
      .slice(0, primary ? 2 : 1);
    const securityNote = securityStatus === "warning" || securityBlocked
      ? securityReasons.slice(0, primary ? 2 : 1)
      : [];
    const candidateEligible = Boolean(
      eventId
      && candidate?.tradeReady
      && contract
      && !securityBlocked
    );
    const disabledReason = !contract
      ? "合约待确认"
      : (!candidate?.tradeReady ? "交易路径待确认" : (securityBlocked ? "安全检查未通过" : "等待安全校验"));
    const buyLabel = candidateEligible ? (securityVerified ? "买入" : "检查后买入") : disabledReason;
    return `
      <div class="news-trade-candidate ${primary ? "is-primary" : "is-backup"}">
        <span class="news-trade-candidate-rank">${primary ? "TOP1 主标" : `备选 ${index + 1}`}</span>
        <span class="news-trade-candidate-identity">
          <span class="news-trade-candidate-name">
            <b>${escapeHtml(symbol)}</b>
            <small class="news-trade-security is-${escapeHtml(securityStatus)}" title="${escapeHtml(securityTitle)}"><i></i>${escapeHtml(securityLabel)}</small>
          </span>
          <em>${escapeHtml(name && name !== symbol ? name : (candidate?.chainLabel || "链待确认"))}</em>
        </span>
        <span class="news-trade-candidate-metric"><b>${score}</b><em>综合权重</em></span>
        <span class="news-trade-candidate-metric"><b>${compactUsd(candidate?.liquidityUsd)}</b><em>池子</em></span>
        <span class="news-trade-candidate-metric"><b>${compactUsd(candidate?.volume24hUsd)}</b><em>24H成交</em></span>
        <span class="news-trade-candidate-metric"><b>${onchainScore || "--"}</b><em>链上交易分</em></span>
        ${eventId ? `<button
          type="button"
          class="news-trade-candidate-buy ${!systemRecommended ? "is-manual" : ""}"
          data-news-trade-prepare="${escapeHtml(eventId)}"
          data-news-trade-contract="${escapeHtml(contract)}"
          data-news-trade-chain="${escapeHtml(candidate?.chain || candidate?.chainId || "")}"
          ${candidateEligible ? "" : "disabled"}
          title="${escapeHtml(candidateEligible
            ? (systemRecommended ? `输入金额后准备买入 ${symbol}` : `系统不主动推荐；仍可按你的手动意图买入 ${symbol}`)
            : disabledReason)}"
        >${escapeHtml(buyLabel)}</button>` : ""}
        <span class="news-trade-candidate-foot">
          <span class="news-trade-candidate-meta">${escapeHtml(candidate?.chainLabel || "链待确认")} · ${escapeHtml(contractLabel)}${candidate?.holderCount ? ` · ${Math.round(Number(candidate.holderCount)).toLocaleString("en-US")} 持币` : ""}${transactions ? ` · ${Math.round(transactions).toLocaleString("en-US")} 笔` : ""} · ${escapeHtml(candidate?.associationLabel || "未确认关联")}</span>
          <span class="news-trade-candidate-foot-actions">
            ${securityNote.length ? `<span class="news-trade-security-note is-${escapeHtml(securityStatus)}">${escapeHtml(securityNote.join(" / "))}</span>` : ""}
            ${visibleRisks.length ? `<span class="news-trade-candidate-risks">${visibleRisks.map((risk) => `<i>${escapeHtml(risk)}</i>`).join("")}</span>` : ""}
            ${routeLinks.length ? `<span class="news-trade-candidate-routes">${routeLinks.map(([venue, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(venue)}</a>`).join("")}</span>` : ""}
          </span>
        </span>
      </div>`;
  }

  function newsTradeTargetsTemplate(candidates, context = {}) {
    const rows = Array.isArray(candidates) ? candidates.slice(0, 3) : [];
    if (!rows.length) return "";
    const primary = rows[0] || {};
    const primarySymbol = String(primary.symbol || primary.name || "待确认");
    return `
      <section class="news-trade-target-zone">
        <header class="news-trade-target-head">
          <span><em>涉及标的 / TARGETS</em><strong>${escapeHtml(primarySymbol)}</strong></span>
          <b>${escapeHtml(primary.chainLabel || "链待确认")} · ${rows.length} 个候选</b>
        </header>
        <div class="news-trade-candidates">${rows.map((candidate, index) => newsTradeCandidateTemplate(candidate, index, context)).join("")}</div>
      </section>`;
  }

  function newsTradeSearchTemplate() {
    const preview = newsTradeSearchState.preview && typeof newsTradeSearchState.preview === "object"
      ? newsTradeSearchState.preview
      : null;
    const topic = Array.isArray(preview?.topics) ? preview.topics[0] : null;
    const candidates = Array.isArray(topic?.memeCandidates) ? topic.memeCandidates.slice(0, 3) : [];
    const duplicate = Boolean(topic?.duplicate);
    const previewId = String(preview?.previewId || "");
    const phase = newsTradePhaseMeta(topic);
    return `
      <section class="news-trade-search-panel">
        <form class="news-trade-search-form" data-news-trade-search>
          <span class="news-trade-search-mark">搜</span>
          <label>
            <b>主动发现遗漏热点</b>
            <input name="query" type="search" value="${escapeHtml(newsTradeSearchState.query)}" placeholder="搜索热点、人物、作品或事件，例如：牛来" autocomplete="off">
          </label>
          <button type="submit" ${newsTradeSearchState.loading ? "disabled" : ""}>${newsTradeSearchState.loading ? "正在理解…" : "搜索并理解"}</button>
        </form>
        <div class="news-trade-score-legend"><b>评分体系</b><span>事件热度</span><span>大瓜</span><span>增长速度</span><span>跨平台</span><span>新奇反差</span><span>群体参与</span><span>符号传播</span><span>后续剧情</span><span>新奇猎奇</span><span>争议性</span><span>讨论度</span><span>传奇性</span><span>名字寓意</span><span>链上质量</span></div>
        ${newsTradeSearchState.error ? `<p class="news-trade-search-message is-error">${escapeHtml(newsTradeSearchState.error)}</p>` : ""}
        ${topic ? `
          <div class="news-trade-search-preview">
            <header>
              <span><em>主题卡预览</em><b>${escapeHtml(topic.title || "搜索热点")}</b></span>
              <span class="news-trade-phase is-${escapeHtml(phase.code)}">${escapeHtml(phase.label)}</span>
              <span class="news-trade-search-score"><b>${Math.round(Number(topic.topicScore ?? topic.score) || 0)}</b><em>主题权重</em></span>
            </header>
            <p>${escapeHtml(topic.thesis || preview?.message || "已找到对应的链上候选。")}</p>
            ${newsTradeScoreTagsTemplate(topic)}
            ${newsTradeTargetsTemplate(candidates)}
            <footer>
              <span>核验 ${Number(preview?.results?.length) || Number(topic.searchResultCount) || 0} 条信息 · ${Number(topic.sourceCount) || 1} 个来源</span>
              <button type="button" data-news-trade-search-add="${escapeHtml(previewId)}" ${!previewId ? "disabled" : ""}>${duplicate ? "补充到现有主题" : "加入主题监控"}</button>
            </footer>
          </div>
        ` : (preview ? `<p class="news-trade-search-message">${escapeHtml(preview.message || "没有生成可交易主题卡。")}</p>` : `
          <p class="news-trade-search-hint">优先检索已有 BlockBeats / X 缓存；信息不足时补充公开新闻搜索。确认后才会加入监控。</p>
        `)}
        ${newsTradeSearchState.message && topic ? `<p class="news-trade-search-message is-success">${escapeHtml(newsTradeSearchState.message)}</p>` : ""}
      </section>`;
  }

  function newsTradePaginationTemplate(total) {
    const pageCount = Math.max(1, Math.ceil(total / NEWS_TRADE_PAGE_SIZE));
    if (pageCount <= 1) return "";
    const start = Math.max(1, Math.min(newsTradePage - 2, pageCount - 4));
    const end = Math.min(pageCount, start + 4);
    const pages = [];
    for (let page = start; page <= end; page += 1) pages.push(page);
    return `
      <nav class="news-trade-pagination" aria-label="News Trade 主题分页">
        <span>第 ${newsTradePage} / ${pageCount} 页 · 共 ${total} 个主题</span>
        <div>
          <button type="button" data-news-trade-page="${newsTradePage - 1}" ${newsTradePage <= 1 ? "disabled" : ""}>上一页</button>
          ${pages.map((page) => `<button type="button" data-news-trade-page="${page}" class="${page === newsTradePage ? "is-active" : ""}">${page}</button>`).join("")}
          <button type="button" data-news-trade-page="${newsTradePage + 1}" ${newsTradePage >= pageCount ? "disabled" : ""}>下一页</button>
        </div>
      </nav>`;
  }

  function newsTradeExecutionNoticeTemplate() {
    const notice = newsTradeExecutionNotice && typeof newsTradeExecutionNotice === "object"
      ? newsTradeExecutionNotice
      : null;
    if (!notice) return "";
    const security = notice.security && typeof notice.security === "object" ? notice.security : {};
    const cost = notice.cost && typeof notice.cost === "object" ? notice.cost : {};
    const blocked = Boolean(notice.blocked || cost.blocked || security.hardBlocked);
    const reasons = Array.isArray(notice.reasons) ? notice.reasons : [];
    const warnings = Array.isArray(cost.warnings) ? cost.warnings : [];
    const money = (value) => Number.isFinite(Number(value)) ? `$${Number(value).toFixed(2)}` : "待实时报价";
    const percent = (value) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : "待实时报价";
    return `
      <section class="news-trade-execution-notice ${blocked ? "is-blocked" : (warnings.length ? "is-warning" : "is-ready")}">
        <header>
          <span><em>买入前成本与安全提醒</em><b>${escapeHtml(notice.symbol || "所选标的")}</b></span>
          <strong>${blocked ? "暂停买入" : "等待最终报价确认"}</strong>
          <button type="button" data-news-trade-notice-close aria-label="关闭提醒">×</button>
        </header>
        <div class="news-trade-execution-metrics">
          <span><em>安全评分</em><b>${Number.isFinite(Number(security.score)) ? Math.round(Number(security.score)) : "--"}</b><small>${escapeHtml(security.label || "安全待核验")}</small></span>
          <span><em>网络 / 跨链费</em><b>${money((Number(cost.networkFeeUsd) || 0) + (Number(cost.bridgeFeeUsd) || 0))}</b><small>预估</small></span>
          <span><em>池费 / 买入税</em><b>${percent((Number(cost.poolFeePct) || 0) + (Number(cost.buyTaxPct) || 0))}</b><small>${money((Number(cost.poolFeeUsd) || 0) + (Number(cost.tokenTaxUsd) || 0))}</small></span>
          <span><em>价格冲击</em><b>${percent(cost.priceImpactPct)}</b><small>按当前池深估算</small></span>
          <span><em>建议滑点上限</em><b>${percent(cost.recommendedSlippagePct)}</b><small>提交前再次确认</small></span>
          <span class="is-minimum"><em>最低可得金额</em><b>${money(cost.minimumReceivedUsdEquivalent)}</b><small>代币等值下限</small></span>
          <span><em>预计总成本</em><b>${money(cost.totalEstimatedCostUsd)}</b><small>${percent(cost.totalEstimatedCostPct)}</small></span>
        </div>
        ${(reasons.length || warnings.length) ? `<div class="news-trade-execution-risks">${[...reasons, ...warnings].slice(0, 5).map((item) => `<i>${escapeHtml(item)}</i>`).join("")}</div>` : ""}
        <p>以上为买入前预估；最终确认时必须重新取得聚合器报价，手续费、滑点、价格冲击和最低可得数量变化都会再次提醒。</p>
      </section>`;
  }

  function eventMonitorCardTemplate(item, newsMode = false) {
    const assets = Array.isArray(item.assets) ? item.assets : [];
    const confirmations = Array.isArray(item.confirmation) ? item.confirmation : [];
    const evidence = Array.isArray(item.evidence) ? item.evidence : [];
    const score = Number(item.topicScore ?? item.score) || 0;
    const href = escapeHtml(item.url || "./price-watch.html?mode=events");
    const memeOpportunity = item.memeOpportunity && typeof item.memeOpportunity === "object" ? item.memeOpportunity : null;
    const memeCandidates = Array.isArray(item.memeCandidates) && item.memeCandidates.length
      ? item.memeCandidates.slice(0, 3)
      : (memeOpportunity ? [memeOpportunity] : []);
    const memeRisks = Array.isArray(memeOpportunity?.riskFlags) ? memeOpportunity.riskFlags : [];
    const venueUrls = memeOpportunity?.tradeUrls && typeof memeOpportunity.tradeUrls === "object" ? memeOpportunity.tradeUrls : {};
    const directTradeUrl = safeExternalUrl(venueUrls["OKX DEX"] || venueUrls.GMGN || "");
    const contract = String(memeOpportunity?.contractAddress || "");
    const contractLabel = contract ? `${contract.slice(0, 8)}…${contract.slice(-6)}` : "合约待确认";
    const phase = newsTradePhaseMeta(item);
    // Event phase controls system recommendations, while a valid route controls
    // whether the user may manually prepare a trade.
    const executionEligible = Boolean(memeOpportunity?.tradeReady);
    const inactiveActionLabel = phase.code === "fermented"
      ? "仅复盘"
      : (phase.code === "expired" ? "已错过" : "事件观察");
    const enteredAt = Number(item.enteredAt || item.firstSeenAt || item.timestamp) || 0;
    return `
      <article class="event-monitor-card ${newsMode ? `is-news-trade is-phase-${phase.code}` : ""} ${eventScoreTone(score)}">
        <header class="event-monitor-topline">
          <span class="event-monitor-kind"><b>${escapeHtml(item.templateName || "事件驱动")}</b><em>${escapeHtml(item.source || "市场信息")}</em></span>
          <span class="event-monitor-score"><b>${score}</b><em>置信分</em></span>
        </header>
        <div class="event-monitor-main">
          <div class="event-monitor-title-line">
            ${newsMode ? `<span class="event-monitor-verified">NEWS TRADE</span>` : ""}
            ${newsMode ? `<span class="news-trade-phase is-${escapeHtml(phase.code)}">${escapeHtml(phase.label)}</span>` : ""}
            <h3>${escapeHtml(item.title || "市场事件")}</h3>
            ${newsMode && Number(item.newsCount || item.sourceCount) > 1 ? `<span class="news-trade-source-count">${Number(item.newsCount || item.sourceCount)} 条合并</span>` : ""}
          </div>
          ${assets.length ? `<div class="event-monitor-assets">${assets.slice(0, 6).map((asset) => `<span>${escapeHtml(asset)}</span>`).join("")}</div>` : ""}
          <p class="event-monitor-thesis">${escapeHtml(item.thesis || "等待更多确认信息")}</p>
          ${newsMode ? newsTradeScoreTagsTemplate(item) : ""}
          ${newsMode ? newsTradeIntelligenceTemplate(item) : ""}
          ${newsMode && memeCandidates.length ? newsTradeTargetsTemplate(memeCandidates, {
            eventId: item.id,
            executionEligible: Boolean(item.executionEligible),
            inactiveActionLabel
          }) : (memeOpportunity ? `
            <div class="event-meme-opportunity">
              <span><b>${escapeHtml(memeOpportunity.chainLabel || "链待确认")}</b><em>${escapeHtml(contractLabel)}</em></span>
              <span><b>${compactUsd(memeOpportunity.marketCapUsd)}</b><em>事件市值</em></span>
              <span><b>${compactUsd(memeOpportunity.liquidityUsd)}</b><em>链上流动性</em></span>
              <span><b>${compactUsd(memeOpportunity.volume24hUsd)}</b><em>24H成交</em></span>
            </div>
            ${memeRisks.length ? `<div class="event-meme-risks">${memeRisks.slice(0, 4).map((risk) => `<span>${escapeHtml(risk)}</span>`).join("")}</div>` : ""}
          ` : "")}
        </div>
        <div class="event-monitor-signals">
          ${confirmations.slice(0, 4).map((label) => `<span>${escapeHtml(label)}</span>`).join("")}
          ${evidence.slice(0, 3).map((label) => `<span class="is-evidence">${escapeHtml(label)}</span>`).join("")}
        </div>
        <footer class="event-monitor-footer">
          <span><b>${escapeHtml(item.sourceLabel || "EV")}</b>${newsMode ? `入池 ${relativeTime(enteredAt)}` : relativeTime(item.timestamp)}</span>
          <span class="event-monitor-actions">
            ${memeOpportunity && !memeCandidates.length ? `<button type="button" data-news-trade-prepare="${escapeHtml(item.id)}" ${executionEligible ? "" : "disabled"}>${executionEligible ? "买入" : inactiveActionLabel}</button>` : ""}
            ${directTradeUrl ? `<a href="${escapeHtml(directTradeUrl)}" target="_blank" rel="noreferrer noopener">交易页</a>` : ""}
            <a href="${href}" target="_blank" rel="noreferrer noopener">打开来源</a>
          </span>
        </footer>
      </article>`;
  }

  function renderEventMonitor() {
    const newsMode = currentMode === "news";
    const pageCount = Math.max(1, Math.ceil(newsTradeItems.length / NEWS_TRADE_PAGE_SIZE));
    newsTradePage = Math.max(1, Math.min(newsTradePage, pageCount));
    const pageStart = (newsTradePage - 1) * NEWS_TRADE_PAGE_SIZE;
    const visibleItems = newsMode
      ? newsTradeItems.slice(pageStart, pageStart + NEWS_TRADE_PAGE_SIZE)
      : eventItems;
    const searchToolbar = newsMode ? newsTradeSearchTemplate() : "";
    const walletToolbar = newsMode ? okxWalletToolbarTemplate() : "";
    const executionNotice = newsMode ? newsTradeExecutionNoticeTemplate() : "";
    grid.classList.remove("is-structure", "is-mapping", "is-aster", "is-wechat", "is-personal-x", "is-chains");
    grid.classList.add("is-events");
    if (!eventLoaded) {
      grid.innerHTML = `
        ${searchToolbar}
        ${walletToolbar}
        ${executionNotice}
        <div class="price-watch-empty">
          <b>正在核对事件来源与市场确认</b>
          <span>只把新发生且具备时效性的事件纳入监控。</span>
        </div>`;
      return;
    }
    if (!visibleItems.length) {
      grid.innerHTML = `
        ${searchToolbar}
        ${walletToolbar}
        ${executionNotice}
        <div class="price-watch-empty">
          <b>${newsMode ? "当前没有高置信 News Trade 候选" : "当前没有新的二级事件"}</b>
          <span>${newsMode ? "需要同时具备明确标的、时效性以及原始或行情确认。" : "律动快讯、交易所公告和 X KOL 动态会继续在后台筛选。"}</span>
        </div>`;
      return;
    }
    grid.innerHTML = `${searchToolbar}${walletToolbar}${executionNotice}${visibleItems.map((item) => eventMonitorCardTemplate(item, newsMode)).join("")}${newsMode ? newsTradePaginationTemplate(newsTradeItems.length) : ""}`;
  }

  function wechatStatusTone(status) {
    if (status === "connected") return "is-connected";
    if (status === "baseline_ready") return "is-ready";
    if (status === "baseline_pending") return "is-pending";
    if (status === "stopped") return "is-stopped";
    return "is-offline";
  }

  function wechatMonitorTemplate(item) {
    const groupName = escapeHtml(item.groupName || "未命名群聊");
    const platform = item.platform === "qq" ? "qq" : "wechat";
    const platformLabel = platform === "qq" ? "Q群" : "微信";
    const senderFilter = String(item.senderFilter || "").trim();
    const forwardTarget = String(item.forwardTarget || "").trim();
    const error = String(item.error || "").trim();
    const forwardError = String(item.lastForwardError || "").trim();
    const detail = [
      item.statusLabel || "等待连接",
      relativeTime(item.lastSeenAt),
      senderFilter ? `只看 ${senderFilter}` : "全部成员",
      item.forwardToWechat ? `转微信：${forwardTarget || "文件传输助手"}` : ""
    ].filter(Boolean).join(" · ");
    return `
      <article class="wechat-monitor-source ${wechatStatusTone(item.status)}">
        <div class="wechat-monitor-source-main">
          <span class="wechat-monitor-source-icon ${platform === "qq" ? "is-qq" : ""}">${platform === "qq" ? "Q" : "微"}</span>
          <span>
            <b>${groupName}<small>${platformLabel}</small></b>
            <em>${escapeHtml(detail)}</em>
          </span>
        </div>
        ${error ? `<p>${escapeHtml(error)}</p>` : ""}
        ${forwardError ? `<p>微信转发等待重试：${escapeHtml(forwardError)}</p>` : ""}
        <div class="wechat-monitor-source-actions">
          <button type="button" data-wechat-toggle="${groupName}" data-wechat-platform="${platform}" data-wechat-sender="${escapeHtml(senderFilter)}" data-wechat-forward="${item.forwardToWechat ? "1" : "0"}" data-wechat-forward-target="${escapeHtml(forwardTarget)}" data-wechat-enabled="${item.enabled ? "1" : "0"}">${item.enabled ? "暂停" : "继续"}</button>
          ${item.fixed ? `<button type="button" disabled>固定监控</button>` : `<button type="button" data-wechat-remove="${groupName}">移除</button>`}
        </div>
      </article>`;
  }

  function wechatOpportunityTemplate(item) {
    const symbols = Array.isArray(item.symbols) ? item.symbols.filter(Boolean) : [];
    const symbolStates = new Map(
      (Array.isArray(item.symbolStates) ? item.symbolStates : [])
        .filter((state) => state?.symbol)
        .map((state) => [String(state.symbol).toUpperCase(), state])
    );
    const catalysts = Array.isArray(item.catalysts) ? item.catalysts.filter(Boolean) : [];
    const risks = Array.isArray(item.risks) ? item.risks.filter(Boolean) : [];
    const confidence = Math.max(0, Math.min(100, Number(item.confidence) || 0));
    return `
      <article class="wechat-opportunity-card ${item.urgency === "high" ? "is-urgent" : ""}">
        <header>
          <span><b>${item.platform === "qq" ? "Q群 · " : "微信 · "}${escapeHtml(item.groupName || "群聊")}</b><em>${escapeHtml(item.sender || "群成员")}</em></span>
          <time>${relativeTime(item.capturedAt)}</time>
        </header>
        <div class="wechat-opportunity-title">
          <span>${escapeHtml(item.category || "市场线索")}</span>
          <b>${confidence}</b><em>置信度</em>
        </div>
        <p class="wechat-opportunity-message">${escapeHtml(item.content || "")}</p>
        ${symbols.length ? `<div class="wechat-opportunity-symbols">${symbols.map((symbol) => {
          const state = symbolStates.get(String(symbol).toUpperCase()) || {};
          const statusClass = state.manuallyRemoved ? "is-removed" : state.dead ? "is-dead" : state.active ? "is-active" : "is-pending";
          const statusLabel = state.manuallyRemoved ? "已手动移除" : state.dead ? "30天无行情" : state.active ? "持续监控" : "等待行情";
          return `<span class="wechat-opportunity-symbol ${statusClass}">
            <strong>${escapeHtml(symbol)}</strong>
            <em>${statusLabel}</em>
            ${state.active ? `<button type="button" title="停止监控 ${escapeHtml(symbol)}" aria-label="停止监控 ${escapeHtml(symbol)}" data-wechat-opportunity-remove="${escapeHtml(symbol)}">×</button>` : ""}
          </span>`;
        }).join("")}</div>` : ""}
        ${item.thesis ? `<p class="wechat-opportunity-thesis">${escapeHtml(item.thesis)}</p>` : ""}
        ${catalysts.length ? `<div class="wechat-opportunity-points"><b>催化</b>${catalysts.slice(0, 4).map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</div>` : ""}
        ${risks.length ? `<div class="wechat-opportunity-points is-risk"><b>风险</b>${risks.slice(0, 3).map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</div>` : ""}
        <footer><span>${escapeHtml(item.actionHint || "等待更多确认")}</span><em>${escapeHtml(item.analysisSource || "rules")}</em></footer>
      </article>`;
  }

  function renderWechatMonitor() {
    const monitors = Array.isArray(wechatMonitorPayload.monitors) ? wechatMonitorPayload.monitors : [];
    const opportunities = Array.isArray(wechatMonitorPayload.opportunities) ? wechatMonitorPayload.opportunities : [];
    const summary = wechatMonitorPayload.summary || {};
    const privacy = wechatMonitorPayload.collector?.privacy || "只在本机内存识别已打开的微信或 QQ 窗口，截图不保存、不上传";
    grid.classList.remove("is-structure", "is-mapping", "is-aster", "is-events", "is-personal-x", "is-chains");
    grid.classList.add("is-wechat");
    grid.innerHTML = `
      <section class="wechat-monitor-console">
        <aside class="wechat-monitor-sidebar">
          <form class="wechat-monitor-form" data-wechat-form>
            <label><span>群聊平台</span><select name="platform"><option value="wechat">微信</option><option value="qq">Q群</option></select></label>
            <label><span>群聊名称</span><input name="groupName" maxlength="80" autocomplete="off" placeholder="例如：地表最强bsc eth" required /></label>
            <label><span>指定发言 ID（Q群必填）</span><input name="senderFilter" maxlength="80" autocomplete="off" placeholder="例如：鲸鱼🐳PP" /></label>
            <label class="wechat-monitor-forward"><input type="checkbox" name="forwardToWechat" checked /><span>新消息转发到微信文件传输助手</span></label>
            <button type="submit">添加监控</button>
          </form>
          <p class="wechat-monitor-privacy">${escapeHtml(privacy)}</p>
          <div class="wechat-monitor-source-list">
            ${monitors.length ? monitors.map(wechatMonitorTemplate).join("") : `<div class="wechat-monitor-source-empty"><b>还没有目标群聊</b><span>先打开微信或 QQ 和目标群，再填写群名建立基线。</span></div>`}
          </div>
        </aside>
        <section class="wechat-opportunity-feed">
          <header class="wechat-opportunity-feed-head">
            <div><p class="section-label">CHAT / OPPORTUNITY / PERSISTENT</p><h3>长期机会池</h3></div>
            <div class="wechat-monitor-summary">
              <span><b>${Number(summary.active) || 0}</b>群监控</span>
              <span><b>${Number(summary.connected) || 0}</b>已连接</span>
              <span><b>${Number(summary.opportunities) || opportunities.length}</b>条机会</span>
              <span><b>${Number(summary.watchingSymbols) || 0}</b>币持续监控</span>
            </div>
          </header>
          <div class="wechat-opportunity-list">
            ${opportunities.length ? opportunities.map(wechatOpportunityTemplate).join("") : `<div class="price-watch-empty"><b>等待新的有效机会</b><span>首次连接只建立基线，不会把历史聊天误报为新机会。</span></div>`}
          </div>
        </section>
      </section>`;
  }

  function personalXTransportLabel(value) {
    if (value === "official-stream") return "X 官方实时流";
    if (value === "official-api" || value === "official-api-recovery") return "X API";
    if (value === "priority-rss-race" || value === "rss-fast-fallback" || value === "rss") return "RSS 降级";
    return value ? String(value) : "连接中";
  }

  function personalXPostTemplate(item) {
    const text = item.fullText || item.text || item.title || "";
    const quote = item.quote && typeof item.quote === "object" ? item.quote : null;
    const metrics = item.metrics && typeof item.metrics === "object" ? item.metrics : {};
    const postUrl = safeExternalUrl(item.url);
    const typeLabels = { tweet: "原创", quoted: "引用", replied_to: "回复", retweeted: "转推" };
    const metricRows = [
      ["浏览", metrics.view],
      ["喜欢", metrics.like],
      ["转发", metrics.repost],
      ["回复", metrics.reply]
    ].filter(([, value]) => Number(value) > 0);
    return `
      <article class="personal-x-post">
        <span class="personal-x-time"><b>${escapeHtml(relativeTime(item.publishedAt))}</b><em>${escapeHtml(typeLabels[item.entryType] || "动态")}</em></span>
        <div class="personal-x-post-body">
          <header><span><b>${escapeHtml(item.sourceName || personalXPayload.account?.displayName || "白星")}</b><em>@${escapeHtml(item.handle || personalXPayload.account?.handle || "whitestar224")}</em></span><small>${escapeHtml(personalXTransportLabel(item.provider))}</small></header>
          <p>${escapeHtml(text)}</p>
          ${quote ? `<div class="personal-x-quote"><span>${escapeHtml(quote.kind || "引用")} · ${escapeHtml(quote.authorName || quote.handle || "原作者")}</span><p>${escapeHtml(quote.text || "")}</p></div>` : ""}
          <footer>
            <span class="personal-x-metrics">${metricRows.length ? metricRows.map(([label, value]) => `<em>${label} ${Number(value).toLocaleString("zh-CN")}</em>`).join("") : "<em>原始动态</em>"}</span>
            ${postUrl ? `<a href="${escapeHtml(postUrl)}" target="_blank" rel="noreferrer noopener">查看原动态</a>` : ""}
          </footer>
        </div>
      </article>`;
  }

  function personalXTacticalSignalTemplate(item) {
    const signalType = item.signalType === "main-wave-expected" ? "main-wave-expected" : "watch";
    const sourceLabel = item.sourceKind === "qq"
      ? `Q群 · ${item.sourceName || "群聊"}`
      : item.sourceKind === "wechat"
        ? `微信 · ${item.sourceName || "群聊"}`
        : `X · ${item.sourceName || "个人账号"}`;
    const remainingMinutes = Math.max(1, Math.ceil((Number(item.signalRemainingSeconds) || 0) / 60));
    return `
      <span class="personal-x-tactical-signal is-${escapeHtml(signalType)}" title="${escapeHtml(`${sourceLabel} · ${item.sourceText || "临盘信号"}`)}">
        <b>${escapeHtml(item.symbol || "--")}</b>
        <em>${escapeHtml(item.signalLabel || (signalType === "watch" ? "重点看" : "有主升浪预期"))}</em>
        <small>${remainingMinutes} 分</small>
      </span>`;
  }

  function renderPersonalXMonitor() {
    const payload = personalXPayload || {};
    const account = payload.account || {};
    const summary = payload.summary || {};
    const rows = Array.isArray(payload.items) ? payload.items : [];
    const tacticalSignals = Array.isArray(payload.tacticalSignals) ? payload.tacticalSignals : [];
    const handle = account.handle || "whitestar224";
    const profileUrl = safeExternalUrl(`https://x.com/${handle}`);
    const avatarUrl = safeExternalUrl(account.avatar);
    const transport = personalXTransportLabel(payload.upstreamMode || summary.transport);
    const connected = personalXStreamReady || summary.connected;
    grid.classList.remove("is-structure", "is-mapping", "is-aster", "is-events", "is-wechat", "is-personal-x", "is-chains");
    grid.classList.add("is-personal-x");
    if (!personalXLoaded && payload.pending) {
      grid.innerHTML = `<div class="price-watch-empty"><b>正在连接个人 X 实时流</b><span>复用现有秒级监控通道，不建立第二套抓取。</span></div>`;
      return;
    }
    grid.innerHTML = `
      <section class="personal-x-console">
        <header class="personal-x-account">
          <span class="personal-x-avatar ${avatarUrl ? "" : "is-fallback"}">${avatarUrl ? `<img src="${escapeHtml(avatarUrl)}" alt="" referrerpolicy="no-referrer" onerror="this.parentElement.classList.add('is-fallback');this.remove()" />` : ""}<b>X</b></span>
          <span class="personal-x-identity"><p class="section-label">PERSONAL X / REALTIME INPUT</p><h3>${escapeHtml(account.displayName || "白星")}</h3><em>@${escapeHtml(handle)}</em></span>
          <div class="personal-x-health">
            <span class="${connected ? "is-live" : "is-connecting"}"><i></i><b>${connected ? "秒级连接" : "正在连接"}</b><em>${escapeHtml(transport)}</em></span>
            <span><b>${rows.length}</b><em>保留动态</em></span>
            <span><b>${summary.lastPublishedAt ? escapeHtml(relativeTime(summary.lastPublishedAt)) : "—"}</b><em>最近发布</em></span>
          </div>
          ${profileUrl ? `<a class="personal-x-profile-link" href="${escapeHtml(profileUrl)}" target="_blank" rel="noreferrer noopener">打开 X 主页</a>` : ""}
        </header>
        <div class="personal-x-content-grid">
          <section class="personal-x-tactical-panel">
            <header><h3>临盘应变信号</h3><em>${tacticalSignals.length} 个生效</em></header>
            <div class="personal-x-tactical-list">${tacticalSignals.length
              ? tacticalSignals.map(personalXTacticalSignalTemplate).join("")
              : `<span class="personal-x-tactical-empty">暂无生效</span><span class="personal-x-tactical-type is-watch">重点看</span><span class="personal-x-tactical-type is-main-wave-expected">有主升浪预期</span>`}
            </div>
          </section>
          <section class="personal-x-feed">
            <header><span><p class="section-label">RAW POSTS / SSE</p><h3>个人动态流</h3></span><em>${payload.historyRestored ? `已恢复并保留 ${rows.length} 条历史动态 · 等待新动态` : "原始内容直出 · 实时去重 · 自动降级"}</em></header>
            <div class="personal-x-post-list">${rows.length ? rows.map(personalXPostTemplate).join("") : `<div class="chain-section-empty"><b>等待新的个人动态</b><span>${escapeHtml(payload.message || payload.error || "实时连接已经建立，新动态会直接出现在这里。")}</span></div>`}</div>
          </section>
        </div>
      </section>`;
  }

  function safeExternalUrl(value) {
    try {
      const url = new URL(String(value || ""), window.location.href);
      return url.protocol === "https:" ? url.href : "";
    } catch (_) {
      return "";
    }
  }

  function compactUsd(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return "--";
    if (number >= 1_000_000_000) return `$${(number / 1_000_000_000).toFixed(2)}B`;
    if (number >= 1_000_000) return `$${(number / 1_000_000).toFixed(2)}M`;
    if (number >= 1_000) return `$${(number / 1_000).toFixed(1)}K`;
    return `$${number.toFixed(number >= 10 ? 0 : 2)}`;
  }

  function compactNative(value, symbol = "") {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return "--";
    const formatted = number >= 1
      ? number.toLocaleString("en-US", { maximumFractionDigits: 3 })
      : number.toLocaleString("en-US", { maximumSignificantDigits: 4 });
    return `${formatted}${symbol ? ` ${symbol}` : ""}`;
  }

  function chainStageMeta(stage) {
    if (stage === "tradable_ecosystem") return ["tradable", "可交易生态"];
    if (stage === "mainnet_focus") return ["mainnet", "主网重点"];
    return ["early", "早期观察"];
  }

  function tokenStageLabel(stage) {
    if (stage === "trading") return "已交易";
    if (stage === "contract_confirmed") return "合约已确认";
    if (stage === "announced") return "已公布发行计划";
    if (stage === "paused") return "暂时停止";
    if (stage === "invalid") return "已失效";
    return "潜在发行";
  }

  function chainSidebarTemplate(chains, selectedSlug) {
    const stageOrder = ["early_watch", "mainnet_focus", "tradable_ecosystem"];
    return stageOrder.map((stage) => {
      const rows = chains.filter((chain) => chain.stage === stage);
      if (!rows.length) return "";
      const [, stageLabel] = chainStageMeta(stage);
      return `
        <section class="chain-stage-group">
          <header><b>${stageLabel}</b><span>${rows.length}</span></header>
          ${rows.map((chain) => `
            <button class="chain-sidebar-row ${chain.slug === selectedSlug ? "active" : ""}" type="button" data-chain-select="${escapeHtml(chain.slug)}">
              <span class="chain-sidebar-symbol">${escapeHtml((chain.gasSymbol || chain.name || "链").slice(0, 2))}</span>
              <span><b>${escapeHtml(chain.name || "未命名公链")}</b><em>${escapeHtml(chain.chainType || `Chain ID ${chain.chainId || "待确认"}`)}</em></span>
              <small>${Number(chain.marketCount) || 0}<em>市场</em></small>
            </button>`).join("")}
        </section>`;
    }).join("");
  }

  function chainRankingTemplate(row, index, marketKey = "") {
    const metrics = row.marketMetrics && typeof row.marketMetrics === "object" ? row.marketMetrics : {};
    const href = safeExternalUrl(row.officialUrl);
    const isNft = marketKey === "nft";
    const primaryMetric = isNft
      ? compactNative(metrics.floorPriceNative, metrics.floorPriceSymbol)
      : compactUsd(metrics.liquidityUsd);
    const content = `
      <span class="chain-rank-index">${index + 1}</span>
      <span class="chain-rank-asset"><b>${escapeHtml(isNft ? row.name : row.symbol || row.name || "--")}</b><em>${escapeHtml(isNft ? "OpenSea · NFT" : row.name || "等待项目资料")}</em></span>
      <span class="chain-rank-metrics"><b>${primaryMetric}</b><em>${isNft ? "地板价" : "流动性"}</em></span>
      <span class="chain-rank-metrics"><b>${compactUsd(metrics.volume24hUsd)}</b><em>24H成交</em></span>
      <span class="chain-rank-score"><b>${Number(row.score) || 0}</b><em>生态分</em></span>`;
    return href
      ? `<a class="chain-ranking-row ${index === 0 ? "is-leader" : ""}" href="${escapeHtml(href)}" target="_blank" rel="noreferrer noopener">${content}</a>`
      : `<div class="chain-ranking-row ${index === 0 ? "is-leader" : ""}">${content}</div>`;
  }

  function chainDiscoveryTemplate(row) {
    const href = safeExternalUrl(row.officialUrl);
    const score = Number(row.potentialScore?.score) || 0;
    const content = `
      <span class="chain-discovery-mark">发现</span>
      <span class="chain-discovery-asset"><b>${escapeHtml(row.symbol || row.name || "--")}</b><em>${escapeHtml(row.name || "等待项目资料")}</em></span>
      <span class="chain-discovery-stage"><b>${escapeHtml(tokenStageLabel(row.tokenStage))}</b><em>${Number(row.evidenceCount) || 0} 条证据</em></span>
      <span class="chain-discovery-score"><b>${score}</b><em>潜力分</em></span>`;
    return href
      ? `<a class="chain-discovery-row" href="${escapeHtml(href)}" target="_blank" rel="noreferrer noopener">${content}</a>`
      : `<div class="chain-discovery-row">${content}</div>`;
  }

  function chainMarketTemplate(market) {
    const top = Array.isArray(market.top) ? market.top.slice(0, 5) : [];
    const candidates = Array.isArray(market.candidates) ? market.candidates.slice(0, 5) : [];
    return `
      <article class="chain-market-card ${top.length ? "has-ranking" : candidates.length ? "has-discovery" : "is-empty"}">
        <header>
          <span><b>${escapeHtml(market.name || market.key || "细分市场")}</b><em>${escapeHtml(market.description || market.key || "")}</em></span>
          <small>${top.length ? `TOP ${top.length}` : candidates.length ? `发现 ${candidates.length}` : "待发现"}</small>
        </header>
        <div class="chain-ranking-list">
          ${top.length
            ? top.map((row, index) => chainRankingTemplate(row, index, market.key)).join("")
            : candidates.length
              ? `${candidates.map(chainDiscoveryTemplate).join("")}<div class="chain-discovery-note">已发现可信候选，满足交易证据后进入 Top 5</div>`
              : `<div class="chain-market-empty"><b>等待可信标的</b><span>不会用推测或低流动性资产填榜。</span></div>`}
        </div>
      </article>`;
  }

  function chainPotentialTemplate(project) {
    const score = project.potentialScore || {};
    const markets = Array.isArray(project.markets) ? project.markets : [];
    const evidence = Array.isArray(project.evidence) ? project.evidence : [];
    const officialUrl = safeExternalUrl(project.officialUrl);
    return `
      <article class="chain-potential-card">
        <header><span><b>${escapeHtml(project.name || "未命名项目")}</b><em>${tokenStageLabel(project.tokenStage)}</em></span><strong>${Number(score.score) || 0}</strong></header>
        <div class="chain-potential-tags">${markets.length ? markets.map((row) => `<span>${escapeHtml(row.name || row.marketKey)}</span>`).join("") : "<span>待分类</span>"}</div>
        <p>${escapeHtml(project.description || "等待更多官方进度、代码与生态证据。")}</p>
        <footer>
          <span>${evidence.length} 条证据 · 覆盖度 ${Number(score.confidence) || 0}%</span>
          ${officialUrl ? `<a href="${escapeHtml(officialUrl)}" target="_blank" rel="noreferrer noopener">官方入口</a>` : ""}
        </footer>
      </article>`;
  }

  function chainAlertTemplate(alert) {
    const typeLabels = {
      stage_upgrade: "阶段升级",
      new_market: "新市场",
      leader_change: "龙头变化",
      market_surge: "量能放大"
    };
    return `
      <article class="chain-alert-row ${alert.acknowledgedAt ? "is-read" : ""}">
        <span class="chain-alert-kind">${escapeHtml(typeLabels[alert.eventType] || "生态变化")}</span>
        <span><b>${escapeHtml(alert.title || "公链生态变化")}</b><em>${relativeTime(alert.observedAt)} · 置信度 ${Number(alert.confidence) || 0}</em></span>
        ${alert.acknowledgedAt ? "<small>已确认</small>" : `<button type="button" data-chain-alert-ack="${Number(alert.id) || 0}">确认</button>`}
      </article>`;
  }

  function renderChainEcosystem() {
    const payload = chainEcosystemPayload || {};
    const chains = Array.isArray(payload.chains) ? payload.chains : [];
    const chain = payload.selectedChain || null;
    const markets = Array.isArray(payload.markets) ? payload.markets : [];
    const potentialProjects = Array.isArray(payload.potentialProjects) ? payload.potentialProjects : [];
    const alerts = Array.isArray(payload.alerts) ? payload.alerts : [];
    const sourceHealth = Array.isArray(payload.sourceHealth) ? payload.sourceHealth : [];
    const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
    grid.classList.remove("is-structure", "is-mapping", "is-aster", "is-events", "is-wechat", "is-personal-x", "is-chains");
    grid.classList.add("is-chains");
    if (!chainEcosystemLoaded) {
      grid.innerHTML = `<div class="price-watch-empty"><b>正在读取公链生态图谱</b><span>加载生命周期、细分市场、潜在发行项目和 Top 5 快照。</span></div>`;
      return;
    }
    if (!chain) {
      grid.innerHTML = `<div class="price-watch-empty"><b>还没有公链</b><span>登录后可通过人工入口添加首条观察链。</span></div>`;
      return;
    }
    selectedChainSlug = chain.slug || selectedChainSlug;
    const [stageTone, stageLabel] = chainStageMeta(chain.stage);
    const evidence = Array.isArray(chain.evidence) ? chain.evidence : [];
    const officialEvidence = evidence.find((row) => safeExternalUrl(row.url));
    const groupedMarkets = ["L0", "L1", "L2", "L3"].map((level) => ({
      level,
      markets: markets.filter((market) => market.level === level)
    }));
    const confirmedSources = sourceHealth.filter((row) => row.status === "ok").length;
    grid.innerHTML = `
      <section class="chain-ecosystem-console">
        <aside class="chain-ecosystem-sidebar">
          <header class="chain-sidebar-head"><span><b>公链雷达</b><em>${chains.length} 条链 · 三阶段</em></span><button type="button" data-chain-add-toggle>＋</button></header>
          <form class="chain-add-form" data-chain-form hidden>
            <label><span>公链名称</span><input name="name" maxlength="80" required placeholder="例如：New Chain" /></label>
            <label><span>官方网址</span><input name="officialUrl" type="url" required placeholder="https://..." /></label>
            <button type="submit" ${chainActionLoading ? "disabled" : ""}>${chainActionLoading ? "正在加入观察…" : "加入早期观察"}</button>
          </form>
          <div class="chain-sidebar-list">${chainSidebarTemplate(chains, chain.slug)}</div>
        </aside>
        <section class="chain-ecosystem-main">
          <header class="chain-overview-card">
            <div class="chain-overview-title">
              <span class="chain-overview-mark">${escapeHtml((chain.gasSymbol || chain.name).slice(0, 2))}</span>
              <span><p class="section-label">CHAIN / EVIDENCE / MARKET TREE</p><h3>${escapeHtml(chain.name)}</h3><em>${escapeHtml(chain.chainType || "公链生态")}${chain.chainId ? ` · Chain ID ${escapeHtml(chain.chainId)}` : ""}</em></span>
            </div>
            <div class="chain-overview-status">
              <span class="chain-stage-badge is-${stageTone}">${stageLabel}</span>
              <span><b>${sourceHealth.length ? `${confirmedSources}/${sourceHealth.length}` : "—"}</b><em>来源可用</em></span>
              <span><b>${markets.filter((market) => market.top?.length || market.candidates?.length).length}</b><em>已发现市场</em></span>
              <span><b>${potentialProjects.length}</b><em>潜在发行</em></span>
            </div>
            <div class="chain-overview-actions">
              ${officialEvidence ? `<a href="${escapeHtml(safeExternalUrl(officialEvidence.url))}" target="_blank" rel="noreferrer noopener">查看官方证据</a>` : ""}
              <button type="button" data-chain-refresh>重新扫描</button>
            </div>
          </header>

          ${warnings.length ? `<div class="chain-warning-strip ${payload.stale ? "is-stale" : ""}">${warnings.map((warning) => `<span>${escapeHtml(warning)}</span>`).join("")}</div>` : ""}

          <section class="chain-market-tree">
            ${groupedMarkets.map((group) => `
              <section class="chain-market-level">
                <header><span>${group.level}</span><div><b>${group.level === "L0" ? "公链资产" : group.level === "L1" ? "核心协议与设施" : group.level === "L2" ? "叙事交易市场" : "专业细分市场"}</b><em>${group.markets.length} 个基础市场</em></div></header>
                <div class="chain-market-grid">${group.markets.map(chainMarketTemplate).join("")}</div>
              </section>`).join("")}
          </section>

          <section class="chain-lower-grid">
            <section class="chain-potential-pool">
              <header class="chain-section-head"><span><p class="section-label">POTENTIAL ISSUANCE</p><h3>潜在发行池</h3></span><em>${potentialProjects.length} 个项目</em></header>
              <form class="chain-project-form" data-chain-project-form>
                <input name="name" maxlength="100" required placeholder="新增项目名称" />
                <select name="marketKey"><option value="">待分类</option>${markets.map((market) => `<option value="${escapeHtml(market.key)}">${escapeHtml(market.name)}</option>`).join("")}</select>
                <input name="officialUrl" type="url" placeholder="官方网址（可选）" />
                <button type="submit">加入项目</button>
              </form>
              <div class="chain-potential-list">${potentialProjects.length ? potentialProjects.map(chainPotentialTemplate).join("") : `<div class="chain-section-empty"><b>暂无潜在发行项目</b><span>自动发现和人工补充都会进入这里。</span></div>`}</div>
            </section>
            <section class="chain-alert-timeline">
              <header class="chain-section-head"><span><p class="section-label">HIGH-VALUE ALERTS</p><h3>高价值预警</h3></span><em>${alerts.length} 条</em></header>
              <div class="chain-alert-list">${alerts.length ? alerts.slice(0, 20).map(chainAlertTemplate).join("") : `<div class="chain-section-empty"><b>暂无高价值变化</b><span>只记录阶段升级、新市场、龙头变化和量能放大。</span></div>`}</div>
            </section>
          </section>
        </section>
      </section>`;
  }

  function relativeTime(value) {
    const time = Number(value);
    if (!time) return "等待首次更新";
    const seconds = Math.max(0, Math.round((Date.now() - time) / 1000));
    if (seconds < 60) return "刚刚更新";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    return `${Math.floor(seconds / 3600)} 小时前`;
  }

  function statusMeta(item) {
    if (item.status === "near") return ["near", "接近前高"];
    if (item.status === "forming") return ["forming", "等待回调 / 盘整"];
    if (item.status === "breakout") return ["breakout", "已突破前高"];
    if (item.status === "unavailable") return ["unavailable", "行情暂不可用"];
    if (item.status === "pending") return ["pending", "等待价格数据"];
    return ["normal", "监控中"];
  }

  function oversoldStatusMeta(item) {
    if (item.oversoldStatus === "near") return ["near", "接近低位阶段高点"];
    if (String(item.fibStatus || "").startsWith("near-")) {
      return ["near", `接近 Fib ${item.fibStatus.replace("near-", "")}`];
    }
    if (item.oversoldStatus === "watching") return ["watching", "低位结构已确认"];
    if (item.fibStatus === "between-levels") return ["watching", "已进入 Fib 0.5 - 0.618 回撤区间"];
    if (item.fibStatus === "below-0.618") return ["watching", "主升浪回撤至 Fib 0.618 下方"];
    if (["above-0.5", "above-0.618"].includes(item.fibStatus)) return ["breakout", "已收复 Fib 0.5"];
    if (item.oversoldStatus === "forming") return ["forming", "等待阶段高点回测"];
    if (item.fibStatus === "forming") return ["forming", "等待回撤至 Fib 0.5 下方"];
    if (item.oversoldStatus === "breakout") return ["breakout", "已越过阶段高点"];
    if (item.oversoldStatus === "unavailable" && item.fibStatus === "unavailable") return ["unavailable", "行情暂不可用"];
    return ["normal", "超跌观察中"];
  }

  function cardTemplate(item) {
    const [statusClass, statusLabel] = statusMeta(item);
    const distance = Number(item.distancePct);
    const hasDistance = Number.isFinite(distance) && item.status !== "unavailable";
    const distanceText = item.status === "breakout"
      ? "已越过 7 日前高"
      : item.status === "forming" && hasDistance
        ? `距前高 ${distance.toFixed(2)}% · 结构未确认`
      : hasDistance
        ? `距前高 ${distance.toFixed(2)}%`
        : "等待计算距离";
    const progress = hasDistance ? Math.max(0, Math.min(100, 100 - distance)) : 0;
    const sourceLabel = item.origin === "new-contract"
      ? (item.newContractSource || "交易所新合约")
      : item.personalXPriority ? "个人 X 提及" : item.manual ? "手动" : "AICoin 新进";
    const icon = item.icon
      ? `<img src="${escapeHtml(item.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.price-watch-icon').classList.add('is-fallback');this.remove()" />`
      : "";
    const signalClass = item.isLaterEpisode
      ? "is-later"
      : item.isConfirmedEpisode
        ? "is-confirmed"
        : "is-candidate";
    const signal = item.signalLabel
      ? `<span class="price-watch-signal ${signalClass}">${escapeHtml(item.signalLabel)}</span>`
      : "";
    const confirmButton = item.confirmable
      ? `<button class="price-watch-confirm" type="button" data-confirm="${escapeHtml(item.symbol)}" data-episode="${Number(item.latestAlertEpisode) || 0}">确认有效</button>`
      : "";
    return `
      <article class="price-watch-card is-${statusClass}" data-symbol="${escapeHtml(item.symbol)}">
        <header>
          <span class="price-watch-icon">${icon}<b>${escapeHtml(item.symbol.slice(0, 2))}</b></span>
          <span class="price-watch-asset">
            <strong>${escapeHtml(item.symbol)}</strong>
            <em>${escapeHtml(item.name || item.symbol)}</em>
          </span>
          <span class="price-watch-origin" title="${escapeHtml(item.personalXSourceText || "")}">${sourceLabel}</span>
          <button class="price-watch-remove" type="button" title="从前高监控池剔除 ${escapeHtml(item.symbol)}" aria-label="从前高监控池剔除 ${escapeHtml(item.symbol)}" data-exclude-prior-high="${escapeHtml(item.symbol)}">×</button>
        </header>
        <div class="price-watch-values">
          <span><em>当前价格</em><b>${compactPrice(item.currentPrice)}</b></span>
          <span><em>最近 7 日前高</em><b>${compactPrice(item.weekHigh)}</b></span>
        </div>
        <div class="price-watch-distance">
          <div><b>${distanceText}</b><em>${escapeHtml(item.provider || relativeTime(item.lastCheckedAt))}</em></div>
          <span class="price-watch-progress"><i style="width:${progress.toFixed(2)}%"></i><u></u></span>
        </div>
        <footer>
          <span class="price-watch-state">${statusLabel}</span>
          ${signal}
          ${confirmButton}
          <time>${relativeTime(item.lastCheckedAt)}</time>
        </footer>
      </article>
    `;
  }

  function oversoldCardTemplate(item) {
    const [statusClass, statusLabel] = oversoldStatusMeta(item);
    const oldCandidate = Boolean(item.oversoldCandidate);
    const fibCandidate = Boolean(item.fibCandidate);
    const drawdown = Number(item.oversoldDrawdownPct);
    const oldDistance = Number(item.oversoldDistancePct);
    const fibUses05 = ["below-0.5", "near-0.5"].includes(item.fibStatus);
    const fibLevel = fibUses05 ? "0.5" : "0.618";
    const fibTarget = fibUses05 ? Number(item.fibLevel05) : Number(item.fibLevel0618);
    const fibDistance = fibUses05 ? Number(item.fibDistance05Pct) : Number(item.fibDistance0618Pct);
    const hasOldDistance = oldCandidate && Number.isFinite(oldDistance) && Number(item.oversoldRangeHigh) > 0;
    const hasFibDistance = fibCandidate && Number.isFinite(fibDistance) && fibTarget > 0;
    const distance = hasOldDistance ? oldDistance : fibDistance;
    const hasDistance = hasOldDistance || hasFibDistance;
    const distanceText = hasOldDistance && item.oversoldStatus === "breakout"
      ? "已越过低位阶段高点"
      : hasOldDistance
        ? `距低位阶段高点 ${oldDistance.toFixed(2)}%`
        : hasFibDistance && Number(item.currentPrice) >= fibTarget
          ? `已收复 Fib ${fibLevel}`
          : hasFibDistance
            ? `距 Fib ${fibLevel} ${Math.max(0, fibDistance).toFixed(2)}%`
            : "等待反弹结构形成";
    const progress = hasDistance ? Math.max(0, Math.min(100, 100 - distance)) : 0;
    const sourceLabel = item.origin === "new-contract"
      ? (item.newContractSource || "交易所新合约")
      : item.personalXPriority ? "个人 X 提及" : item.manual ? "手动" : "AICoin 新进";
    const icon = item.icon
      ? `<img src="${escapeHtml(item.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.price-watch-icon').classList.add('is-fallback');this.remove()" />`
      : "";
    const valueColumns = oldCandidate
      ? `
          <span><em>当前价格</em><b>${compactPrice(item.currentPrice)}</b></span>
          <span><em>低位阶段高点</em><b>${compactPrice(item.oversoldRangeHigh)}</b></span>
          <span><em>下跌最低点</em><b>${compactPrice(item.oversoldRangeLow)}</b></span>`
      : `
          <span><em>当前价格</em><b>${compactPrice(item.currentPrice)}</b></span>
          <span><em>主升浪启动点</em><b>${compactPrice(item.fibLaunchLow)}</b></span>
          <span><em>波段高点</em><b>${compactPrice(item.fibSwingHigh)}</b></span>`;
    const fibStrip = fibCandidate
      ? `<div class="price-watch-fib-strip">
          <span><em>日线主升浪</em><b>${compactPrice(item.fibLaunchLow)} → ${compactPrice(item.fibSwingHigh)}</b></span>
          <span><em>Fib 0.5</em><b>${compactPrice(item.fibLevel05)}</b></span>
          <span><em>Fib 0.618</em><b>${compactPrice(item.fibLevel0618)}</b></span>
        </div>`
      : "";
    const contextText = oldCandidate
      ? `自 7 日高点回撤 ${Number.isFinite(drawdown) ? drawdown.toFixed(2) : "--"}%`
      : `近期主升浪涨幅 ${Number(item.fibImpulseGainPct || 0).toFixed(2)}%`;
    return `
      <article class="price-watch-card is-oversold is-${statusClass}" data-symbol="${escapeHtml(item.symbol)}">
        <header>
          <span class="price-watch-icon">${icon}<b>${escapeHtml(item.symbol.slice(0, 2))}</b></span>
          <span class="price-watch-asset">
            <strong>${escapeHtml(item.symbol)}</strong>
            <em>${escapeHtml(item.name || item.symbol)}</em>
          </span>
          <span class="price-watch-origin">${sourceLabel}</span>
          <button class="price-watch-remove" type="button" title="移除 ${escapeHtml(item.symbol)}" aria-label="移除 ${escapeHtml(item.symbol)}" data-remove="${escapeHtml(item.symbol)}">×</button>
        </header>
        <div class="price-watch-values oversold-values">
          ${valueColumns}
        </div>
        ${fibStrip}
        <div class="price-watch-distance">
          <div><b>${distanceText}</b><em>${contextText}</em></div>
          <span class="price-watch-progress"><i style="width:${progress.toFixed(2)}%"></i><u></u></span>
        </div>
        <footer>
          <span class="price-watch-state">${statusLabel}</span>
          <time>${relativeTime(item.lastCheckedAt)}</time>
        </footer>
      </article>
    `;
  }

  function updateModePresentation() {
    const oversold = currentMode === "oversold";
    const structure = currentMode === "structure";
    const newLow = currentMode === "newlow";
    const mapping = currentMode === "mapping";
    const aster = currentMode === "aster";
    const events = currentMode === "events";
    const news = currentMode === "news";
    const wechat = currentMode === "wechat";
    const personalx = currentMode === "personalx";
    const chains = currentMode === "chains";
    form.hidden = wechat || personalx || chains;
    modeButtons.forEach((button) => {
      const active = button.dataset.watchMode === currentMode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    if (structure) {
      headingLabel.textContent = "MULTI-TIMEFRAME / STRUCTURE MAP";
      headingTitle.textContent = "AICoin 热门币多周期结构";
      headingDescription.innerHTML = `<b>1分钟</b> · 5分钟 · 15分钟 · 1小时 · 4小时 · 日线 · 使用龙头起爆策略识别A+买点与多周期共振 · 后台监控池与页面每 3 秒同步 · 仅保留 24H 成交额不低于 <b>1000 万美元</b>的标的`;
      return;
    }
    if (newLow) {
      headingLabel.textContent = "NEW COIN / LOW POSITION / STRUCTURE";
      headingTitle.textContent = "近一年新币低位结构";
      headingDescription.innerHTML = `按跨交易所<b>首次上架</b>计算近 365 天币龄 · 仅监控 <b>1小时 / 4小时</b> · 24H 全网合约成交额低于 <b>1000 万美元</b>自动剔除，恢复后自动加回`;
      return;
    }
    if (mapping) {
      headingLabel.textContent = "LEADER / FAMILY / NARRATIVE / LAGGARD";
      headingTitle.textContent = "龙头补涨映射";
      headingDescription.innerHTML = `<b>本尊</b> → 家族 → 题材映射 → 潜在补涨 · 只展示有真实行情的候选`;
      return;
    }
    if (aster) {
      headingLabel.textContent = "ASTER / LISTING ANNOUNCEMENTS";
      headingTitle.textContent = "Aster 合约上新公告";
      headingDescription.innerHTML = `保留最近 <b>30 天</b> · 最新在前 · 官网公告 + 官方 X 上新 · 合约接口首见高速补充`;
      return;
    }
    if (events) {
      headingLabel.textContent = "EVENT DRIVEN / SECONDARY";
      headingTitle.textContent = "二级事件驱动监控";
      headingDescription.innerHTML = `信息延迟 · 价值锚/政策 · 瞬时重定价 · 公告延迟 · 市场错价 · 盘口/基差`;
      return;
    }
    if (news) {
      headingLabel.textContent = "NEWS TRADE / HOT TOPICS / ONCHAIN MEME";
      headingTitle.textContent = "热点主题与链上 MEME";
      headingDescription.innerHTML = `<b>事件热度</b> · 叙事相关 · 池子/流动性 · Top1 主标 + 2 个备选`;
      return;
    }
    if (wechat) {
      headingLabel.textContent = "CHAT / LOCAL GROUP / OPPORTUNITY";
      headingTitle.textContent = "群聊机会监控";
      headingDescription.innerHTML = `已识别机会持续监控 · 仅<b>手动移除</b>或连续 30 天无合约行情才停止`;
      return;
    }
    if (personalx) {
      headingLabel.textContent = "PERSONAL X / REALTIME / RAW INPUT";
      headingTitle.textContent = "个人 X 秒级监控";
      headingDescription.innerHTML = `<b>@whitestar224</b> · 复用现有 API 实时流 · 原始动态独立展示`;
      return;
    }
    if (chains) {
      headingLabel.textContent = "CHAIN / ECOSYSTEM / MARKET TREE / TOP 5";
      headingTitle.textContent = "公链生态监控";
      headingDescription.innerHTML = `<b>早期观察</b> → 主网重点 → 可交易生态 · L0-L3 市场与潜在发行池`;
      return;
    }
    headingLabel.textContent = oversold ? "OVERSOLD / LOW RANGE / REBOUND" : "ACTIVE WATCHLIST";
    headingTitle.textContent = oversold ? "热门币超跌反弹监控" : "最近 7 日前高监控";
    headingDescription.innerHTML = oversold
      ? `低位震荡阶段高点 + 前段涨幅超过 <b>100%</b> 的近期日线主升浪 Fib <b>0.5 / 0.618</b> · 24H 成交额不低于 <b>1000 万美元</b>`
      : `现价低于前高且距离不超过 <b>3%</b> · 24H 成交额不低于 <b>1000 万美元</b> · 30 天未再上榜自动移除`;
  }

  function render(payload) {
    items = Array.isArray(payload.items) ? payload.items : [];
    if (payload.summary) currentSummary = payload.summary;
    const summary = currentSummary;
    Object.entries(metricNodes).forEach(([key, node]) => {
      const priorSummaryKey = ({ total: "priorHighTotal", auto: "priorHighAuto", manual: "priorHighManual" })[key];
      const value = currentMode === "prior" && priorSummaryKey && summary[priorSummaryKey] != null
        ? summary[priorSummaryKey]
        : summary[key];
      if (node) node.textContent = Number(value || 0).toLocaleString("zh-CN");
    });
    if (currentMode === "structure" || currentMode === "newlow") {
      renderStructures();
      return;
    }
    if (currentMode === "mapping") {
      renderMappings();
      return;
    }
    if (currentMode === "aster") {
      renderAsterContracts();
      return;
    }
    if (currentMode === "events" || currentMode === "news") {
      renderEventMonitor();
      return;
    }
    if (currentMode === "wechat") {
      renderWechatMonitor();
      return;
    }
    if (currentMode === "personalx") {
      renderPersonalXMonitor();
      return;
    }
    if (currentMode === "chains") {
      renderChainEcosystem();
      return;
    }
    grid.classList.remove("is-structure", "is-mapping", "is-aster", "is-events", "is-wechat", "is-personal-x", "is-chains");
    const visibleItems = currentMode === "oversold"
      ? items.filter((item) => item.oversoldCandidate || item.fibCandidate)
      : items.filter((item) => item.priorHighEnabled !== false);
    if (!visibleItems.length) {
      grid.innerHTML = `
        <div class="price-watch-empty">
          <b>${currentMode === "oversold" ? "暂无符合条件的超跌币种" : "暂无监控币种"}</b>
          <span>${currentMode === "oversold" ? "当前还没有满足低位震荡反弹或近期主升浪 Fib 回撤条件的币种。" : "AICoin 热门榜出现新币后会自动加入，也可以在上方手动添加。"}</span>
        </div>`;
      return;
    }
    grid.innerHTML = visibleItems
      .map(currentMode === "oversold" ? oversoldCardTemplate : cardTemplate)
      .join("");
  }

  function setBusy(busy, text = "") {
    loading = busy;
    refreshButton.disabled = busy;
    if (text) statusNode.textContent = text;
  }

  async function getPayload(refresh = false) {
    const response = await fetch(`/api/price-watch${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "价格监控数据读取失败");
    return payload;
  }

  async function getStructurePayload(refresh = false) {
    const response = await fetch(`/api/price-structures${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "多周期结构读取失败");
    return payload;
  }

  async function getNewLowStructurePayload(refresh = false) {
    const response = await fetch(`/api/new-coin-low-structures${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "新币低位结构读取失败");
    return payload;
  }

  async function postStructureAction(action, symbol, extra = {}) {
    const response = await fetch("/api/price-structures", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, symbol, ...extra })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "多周期结构操作失败");
    return payload;
  }

  async function getMappingPayload(refresh = false) {
    const response = await fetch(`/api/rotation-map${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "补涨映射读取失败");
    return payload;
  }

  async function getAsterPayload(refresh = false) {
    const response = await fetch(`/api/aster-contracts${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "Aster 新合约读取失败");
    return payload;
  }

  async function getEventPayload(refresh = false) {
    const response = await fetch(`/api/event-monitor${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "事件监控读取失败");
    return payload;
  }

  async function prepareNewsTrade(eventId, amountUsdt, opportunity) {
    const response = await fetch("/api/news-trade/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        eventId,
        amountUsdt,
        manualIntent: true,
        candidateContract: opportunity?.contractAddress || "",
        candidateChain: opportunity?.chain || opportunity?.chainId || "",
        ...okxWalletAuthorization(opportunity)
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "买入路线准备失败");
    return payload;
  }

  async function postNewsTradeSearch(action, extra = {}) {
    const response = await fetch("/api/news-trade/search", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "热点主题搜索失败");
    return payload;
  }

  async function getWechatMonitorPayload(refresh = false) {
    const response = await fetch(`/api/wechat-group-monitor${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) throw new Error("请先登录后配置群聊机会监控");
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "群聊监控读取失败");
    return payload;
  }

  async function getPersonalXPayload(refresh = false) {
    const response = await fetch(`/api/personal-x-monitor${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "个人 X 实时流读取失败");
    return payload;
  }

  function applyPersonalXPayload(payload) {
    personalXPayload = payload && typeof payload === "object" ? payload : { account: null, items: [], summary: {} };
    personalXLoaded = true;
    if (currentMode === "personalx") renderPersonalXMonitor();
  }

  function connectPersonalXStream() {
    if (typeof EventSource === "undefined") return;
    if (personalXStream && personalXStream.readyState !== EventSource.CLOSED) return;
    personalXStream = new EventSource("/api/personal-x-stream");
    personalXStream.addEventListener("open", () => {
      personalXStreamReady = true;
      if (currentMode === "personalx") {
        renderPersonalXMonitor();
        statusNode.textContent = "@whitestar224 秒级实时流已连接";
      }
    });
    const applyEvent = (event) => {
      try {
        applyPersonalXPayload(JSON.parse(event.data));
      } catch (_) {
        if (currentMode === "personalx") statusNode.textContent = "个人 X 实时数据解析失败";
      }
    };
    personalXStream.addEventListener("feed", applyEvent);
    personalXStream.addEventListener("status", applyEvent);
    personalXStream.addEventListener("error", () => {
      personalXStreamReady = false;
      if (currentMode === "personalx") {
        renderPersonalXMonitor();
        statusNode.textContent = "个人 X 实时流正在自动重连";
      }
    });
  }

  async function postWechatMonitorAction(action, extra = {}) {
    const response = await fetch("/api/wechat-group-monitor", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra })
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) throw new Error("请先登录后配置群聊机会监控");
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "群聊监控操作失败");
    return payload;
  }

  async function getChainEcosystemPayload(refresh = false, chainSlug = selectedChainSlug) {
    const url = new URL("/api/chain-ecosystem", window.location.origin);
    if (refresh) url.searchParams.set("refresh", "1");
    if (chainSlug) url.searchParams.set("chain", chainSlug);
    const response = await fetch(`${url.pathname}${url.search}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || (payload.ok === false && !payload.selectedChain)) {
      throw new Error(payload.error || "公链生态读取失败");
    }
    return payload;
  }

  async function postChainEcosystemAction(action, extra = {}) {
    const response = await fetch("/api/chain-ecosystem", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra })
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) throw new Error("请先登录后再人工补充公链生态证据");
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "公链生态操作失败");
    return payload;
  }

  async function loadStructures({ refresh = false, quiet = false, live = false } = {}) {
    if (structureLoading) return;
    const newLow = currentMode === "newlow";
    const currentItems = newLow ? newLowStructureItems : structureItems;
    const lastLoadedAt = newLow ? lastNewLowStructureLoadAt : lastStructureLoadAt;
    const cacheFresh = !live && currentItems.length && Date.now() - lastLoadedAt < STRUCTURE_SYNC_INTERVAL_MS;
    if (!refresh && cacheFresh) {
      if (["structure", "newlow"].includes(currentMode)) renderStructures();
      return;
    }
    structureLoading = true;
    if (!currentItems.length && ["structure", "newlow"].includes(currentMode)) renderStructures();
    try {
      const payload = newLow ? await getNewLowStructurePayload(refresh) : await getStructurePayload(refresh);
      if (newLow) {
        newLowStructureItems = Array.isArray(payload.items) ? payload.items : [];
        lastNewLowStructureLoadAt = Date.now();
      } else {
        structureItems = Array.isArray(payload.items) ? payload.items : [];
        lastStructureLoadAt = Date.now();
      }
      if (["structure", "newlow"].includes(currentMode)) renderStructures();
      const signalCount = Number(payload.summary?.signals) || 0;
      if (["structure", "newlow"].includes(currentMode)) {
        statusNode.textContent = newLow
          ? `活跃新币 ${Number(payload.summary?.inventory) || 0} 个 · 已剔除不活跃 ${Number(payload.summary?.inactiveExcluded) || 0} 个 · 已轮询 ${Number(payload.summary?.scanned) || 0} 个 · 低位候选 ${Number(payload.summary?.candidates) || 0} 个`
          : (refresh
            ? `六个周期的龙头策略已重新扫描 · 当前 ${signalCount} 个起爆信号`
            : `后台并发监控 · 已剔除低成交额 ${Number(payload.summary?.inactiveExcluded) || 0} 个 · 当前 ${signalCount} 个起爆信号`);
      }
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
      if (!currentItems.length && ["structure", "newlow"].includes(currentMode)) {
        grid.innerHTML = `<div class="price-watch-empty"><b>${newLow ? "新币低位结构" : "多周期结构"}暂不可用</b><span>${escapeHtml(error.message)}</span></div>`;
      }
    } finally {
      structureLoading = false;
    }
  }

  async function loadMappings({ refresh = false, quiet = false } = {}) {
    const cacheFresh = mappingLoaded && Date.now() - lastMappingLoadAt < 60_000;
    if (!refresh && cacheFresh) {
      renderMappings();
      return;
    }
    if (!mappingLoaded) renderMappings();
    try {
      const payload = await getMappingPayload(refresh);
      mappingItems = Array.isArray(payload.maps) ? payload.maps : [];
      mappingSummary = payload.summary && typeof payload.summary === "object" ? payload.summary : {};
      mappingLoaded = true;
      lastMappingLoadAt = Date.now();
      renderMappings();
      const scanned = Number(mappingSummary.hotScanned) || 0;
      const leaders = Number(mappingSummary.leaders) || mappingItems.length;
      statusNode.textContent = `已实时扫描 ${scanned} 个热门标的，监控 ${leaders} 个 300% 以上主升龙头；每 60 秒更新`;
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
      if (!mappingLoaded) {
        grid.innerHTML = `<div class="price-watch-empty"><b>补涨映射暂不可用</b><span>${escapeHtml(error.message)}</span></div>`;
      }
    }
  }

  async function loadAsterContracts({ refresh = false, quiet = false } = {}) {
    const cacheFresh = asterItems.length && Date.now() - lastAsterLoadAt < 12_000;
    if (!refresh && cacheFresh) {
      renderAsterContracts();
      return;
    }
    if (!asterItems.length) renderAsterContracts();
    try {
      const payload = await getAsterPayload(refresh);
      asterItems = Array.isArray(payload.items) ? payload.items : [];
      lastAsterLoadAt = Date.now();
      renderAsterContracts();
      statusNode.textContent = refresh ? "Aster 合约上新公告已重新核对" : "Aster 上新公告每 12 秒在后台比对";
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
      if (!asterItems.length) {
        grid.innerHTML = `<div class="price-watch-empty"><b>Aster 上新公告暂不可用</b><span>${escapeHtml(error.message)}</span></div>`;
      }
    }
  }

  async function loadEventMonitor({ refresh = false, quiet = false } = {}) {
    const cacheFresh = eventLoaded && Date.now() - lastEventLoadAt < 15_000;
    if (!refresh && cacheFresh) {
      renderEventMonitor();
      return;
    }
    if (!eventLoaded) renderEventMonitor();
    try {
      const payload = await getEventPayload(refresh);
      eventItems = Array.isArray(payload.events) ? payload.events : [];
      newsTradeItems = Array.isArray(payload.newsTrades) ? payload.newsTrades : [];
      eventSummary = payload.summary && typeof payload.summary === "object" ? payload.summary : {};
      eventExecution = payload.execution && typeof payload.execution === "object" ? payload.execution : eventExecution;
      eventLoaded = true;
      lastEventLoadAt = Date.now();
      renderEventMonitor();
      const eventCount = Number(eventSummary.events) || eventItems.length;
      const newsCount = Number(eventSummary.newsTrades) || newsTradeItems.length;
      statusNode.textContent = currentMode === "news"
        ? `已筛出 ${newsCount} 条高置信候选；仅新事件进入提醒队列`
        : `已核对 ${eventCount} 条二级事件，其中 ${newsCount} 条达到 News Trade 条件`;
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
      if (!eventLoaded) {
        grid.innerHTML = `<div class="price-watch-empty"><b>事件监控暂不可用</b><span>${escapeHtml(error.message)}</span></div>`;
      }
    }
  }

  async function loadWechatMonitor({ refresh = false, quiet = false } = {}) {
    const cacheFresh = lastWechatLoadAt && Date.now() - lastWechatLoadAt < 4_000;
    if (!refresh && cacheFresh) {
      renderWechatMonitor();
      return;
    }
    try {
      wechatMonitorPayload = await getWechatMonitorPayload(refresh);
      lastWechatLoadAt = Date.now();
      renderWechatMonitor();
      const summary = wechatMonitorPayload.summary || {};
      statusNode.textContent = `可见群聊 ${Number(summary.connected) || 0}/${Number(summary.active) || 0} 已连接 · 提及币种持续进入结构监控${Number(summary.forwardPending) ? ` · ${Number(summary.forwardPending)} 条待转微信` : ""}`;
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
      if (!lastWechatLoadAt) {
        grid.classList.add("is-wechat");
        grid.innerHTML = `<div class="price-watch-empty"><b>群聊机会监控暂不可用</b><span>${escapeHtml(error.message)}</span></div>`;
      }
    }
  }

  async function loadPersonalXMonitor({ refresh = false, quiet = false } = {}) {
    if (personalXLoaded && !refresh) {
      renderPersonalXMonitor();
      connectPersonalXStream();
      return;
    }
    if (!personalXLoaded) renderPersonalXMonitor();
    try {
      applyPersonalXPayload(await getPersonalXPayload(refresh));
      connectPersonalXStream();
      const count = Array.isArray(personalXPayload.items) ? personalXPayload.items.length : 0;
      statusNode.textContent = `@${personalXPayload.account?.handle || "whitestar224"} 秒级监控中 · 当前保留 ${count} 条动态`;
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
      if (!personalXLoaded) {
        grid.classList.add("is-personal-x");
        grid.innerHTML = `<div class="price-watch-empty"><b>个人 X 实时流暂不可用</b><span>${escapeHtml(error.message)}</span></div>`;
      }
    }
  }

  async function loadChainEcosystem({ refresh = false, quiet = false } = {}) {
    const cacheFresh = chainEcosystemLoaded && Date.now() - lastChainEcosystemLoadAt < 60_000;
    if (!refresh && cacheFresh) {
      renderChainEcosystem();
      return;
    }
    if (!chainEcosystemLoaded) renderChainEcosystem();
    const requestId = ++chainEcosystemRequestId;
    try {
      const payload = await getChainEcosystemPayload(refresh);
      if (requestId !== chainEcosystemRequestId) return;
      chainEcosystemPayload = payload;
      chainEcosystemLoaded = true;
      lastChainEcosystemLoadAt = Date.now();
      selectedChainSlug = chainEcosystemPayload.selectedChain?.slug || selectedChainSlug;
      renderChainEcosystem();
      const activeMarkets = (chainEcosystemPayload.markets || []).filter((market) => market.top?.length || market.candidates?.length).length;
      const potential = (chainEcosystemPayload.potentialProjects || []).length;
      statusNode.textContent = chainEcosystemPayload.stale
        ? `正在显示最后可信快照 · ${activeMarkets} 个已发现市场 · ${potential} 个潜在发行项目`
        : `${activeMarkets} 个已发现市场 · ${potential} 个潜在发行项目 · 每 60 秒检查页面快照`;
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
      if (!chainEcosystemLoaded) {
        grid.classList.add("is-chains");
        grid.innerHTML = `<div class="price-watch-empty"><b>公链生态暂不可用</b><span>${escapeHtml(error.message)}</span></div>`;
      }
    }
  }

  async function postAction(action, symbol = "", extra = {}) {
    const response = await fetch("/api/price-watch", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, symbol, ...extra })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "操作失败");
    return payload;
  }

  async function load({ refresh = false, quiet = false } = {}) {
    if (loading) return;
    setBusy(true, refresh
      ? (["structure", "newlow"].includes(currentMode) ? (currentMode === "newlow" ? "后台推进近一年新币低位结构轮询…" : "后台运行龙头策略六周期扫描…") : currentMode === "mapping" ? "后台重建补涨映射…" : currentMode === "aster" ? "后台核对 Aster 合约上新公告…" : ["events", "news"].includes(currentMode) ? "后台核对事件来源与确认依据…" : currentMode === "wechat" ? "后台检查当前可见群聊…" : currentMode === "personalx" ? "后台唤醒个人 X 实时通道…" : currentMode === "chains" ? "后台重新扫描公链生态证据…" : "后台核对 7 日价格数据…")
      : statusNode.textContent);
    try {
      if (currentMode === "wechat") {
        await loadWechatMonitor({ refresh, quiet });
        return;
      }
      if (currentMode === "personalx") {
        await loadPersonalXMonitor({ refresh, quiet });
        return;
      }
      if (currentMode === "chains") {
        await loadChainEcosystem({ refresh, quiet });
        return;
      }
      const payload = await getPayload(refresh);
      render(payload);
      if (["structure", "newlow"].includes(currentMode)) {
        await loadStructures({ refresh, quiet });
        return;
      }
      if (currentMode === "mapping") {
        await loadMappings({ refresh, quiet });
        return;
      }
      if (currentMode === "aster") {
        await loadAsterContracts({ refresh, quiet });
        return;
      }
      if (currentMode === "events" || currentMode === "news") {
        await loadEventMonitor({ refresh, quiet });
        return;
      }
      statusNode.textContent = currentMode === "oversold"
        ? (refresh ? "超跌结构、主升浪起点与 Fib 价位已更新" : "接近低位阶段高点或主升浪 Fib 0.5 / 0.618 时提醒")
        : (refresh ? "价格与最近 7 日前高已更新" : "现价低于最近 7 日前高且距离不超过 3% 时提醒");
    } catch (error) {
      if (!quiet) statusNode.textContent = error.message;
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const symbol = symbolInput.value.trim();
    if (!symbol || loading) return;
    setBusy(true, `正在添加 ${symbol.toUpperCase()}…`);
    try {
      const payload = await postAction("add", symbol);
      render(payload);
      symbolInput.value = "";
      statusNode.textContent = `${symbol.toUpperCase()} 已加入，价格将在后台更新`;
    } catch (error) {
      statusNode.textContent = error.message;
    } finally {
      setBusy(false);
    }
  });

  grid.addEventListener("submit", async (event) => {
    const newsTradeSearchForm = event.target.closest("[data-news-trade-search]");
    if (newsTradeSearchForm) {
      event.preventDefault();
      if (newsTradeSearchState.loading) return;
      const query = new FormData(newsTradeSearchForm).get("query")?.toString().trim() || "";
      if (query.length < 2) {
        newsTradeSearchState = { ...newsTradeSearchState, query, error: "请输入至少 2 个字的热点关键词", message: "" };
        renderEventMonitor();
        return;
      }
      newsTradeSearchState = { query, loading: true, preview: null, error: "", message: "" };
      renderEventMonitor();
      try {
        const preview = await postNewsTradeSearch("preview", { query });
        newsTradeSearchState = { query, loading: false, preview, error: "", message: "" };
        renderEventMonitor();
        statusNode.textContent = preview.topics?.length
          ? `已理解“${query}”并生成主题卡预览，确认后才会加入监控`
          : (preview.message || `未找到“${query}”对应的可交易主题`);
      } catch (error) {
        newsTradeSearchState = { query, loading: false, preview: null, error: error.message, message: "" };
        renderEventMonitor();
        statusNode.textContent = error.message;
      }
      return;
    }

    const chainForm = event.target.closest("[data-chain-form]");
    if (chainForm) {
      event.preventDefault();
      if (chainActionLoading) return;
      const data = new FormData(chainForm);
      const name = data.get("name")?.toString().trim();
      const officialUrl = data.get("officialUrl")?.toString().trim();
      if (!name || !officialUrl) return;
      chainActionLoading = true;
      chainEcosystemRequestId += 1;
      const submitButton = chainForm.querySelector("button[type='submit']");
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "正在加入观察…";
      }
      statusNode.textContent = `正在把 ${name} 写入观察池，完成后后台自动扫描…`;
      try {
        const result = await postChainEcosystemAction("add_chain", { name, officialUrl });
        const addedChain = result.chain || {};
        const previousChains = Array.isArray(chainEcosystemPayload.chains) ? chainEcosystemPayload.chains : [];
        const optimisticChain = { ...addedChain, evidence: [], projectCount: 0, marketCount: 0 };
        chainEcosystemPayload = result.payload || {
          ...chainEcosystemPayload,
          chains: [optimisticChain, ...previousChains.filter((row) => row.slug !== optimisticChain.slug)],
          selectedChain: optimisticChain,
          markets: [],
          projects: [],
          potentialProjects: [],
          alerts: [],
          sourceHealth: [],
          warnings: ["已加入早期观察，首次生态扫描正在后台进行"],
          refreshing: Boolean(result.refreshScheduled),
          stale: false
        };
        chainEcosystemLoaded = true;
        selectedChainSlug = result.chain?.slug || selectedChainSlug;
        lastChainEcosystemLoadAt = 0;
        renderChainEcosystem();
        statusNode.textContent = `${name} 已加入早期观察，首次扫描已在后台运行`;
        window.setTimeout(() => loadChainEcosystem({ quiet: true }), 2_000);
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        chainActionLoading = false;
        const currentSubmit = grid.querySelector("[data-chain-form] button[type='submit']");
        if (currentSubmit) {
          currentSubmit.disabled = false;
          currentSubmit.textContent = "加入早期观察";
        }
      }
      return;
    }

    const projectForm = event.target.closest("[data-chain-project-form]");
    if (projectForm) {
      event.preventDefault();
      if (loading || !chainEcosystemPayload.selectedChain) return;
      const data = new FormData(projectForm);
      const name = data.get("name")?.toString().trim();
      if (!name) return;
      setBusy(true, `正在添加 ${name} 的项目证据入口…`);
      try {
        const result = await postChainEcosystemAction("add_project", {
          chainId: chainEcosystemPayload.selectedChain.id,
          name,
          marketKey: data.get("marketKey")?.toString() || "",
          officialUrl: data.get("officialUrl")?.toString().trim() || ""
        });
        chainEcosystemPayload = result.payload || chainEcosystemPayload;
        chainEcosystemLoaded = true;
        lastChainEcosystemLoadAt = Date.now();
        renderChainEcosystem();
        statusNode.textContent = `${name} 已进入潜在发行池`;
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const wechatForm = event.target.closest("[data-wechat-form]");
    if (!wechatForm) return;
    event.preventDefault();
    const chatData = new FormData(wechatForm);
    const groupName = chatData.get("groupName")?.toString().trim();
    const platform = chatData.get("platform")?.toString() === "qq" ? "qq" : "wechat";
    const senderFilter = chatData.get("senderFilter")?.toString().trim() || "";
    const forwardToWechat = platform === "qq" && chatData.get("forwardToWechat") === "on";
    if (!groupName || loading) return;
    if (platform === "qq" && !senderFilter) {
      statusNode.textContent = "Q 群监控需要填写指定发言 ID";
      return;
    }
    setBusy(true, `正在为 ${groupName} 建立群聊基线…`);
    try {
      wechatMonitorPayload = await postWechatMonitorAction("add", {
        groupName,
        platform,
        senderFilter,
        forwardToWechat,
        forwardTarget: forwardToWechat ? "文件传输助手" : "",
        enabled: true
      });
      lastWechatLoadAt = Date.now();
      renderWechatMonitor();
      statusNode.textContent = `已添加 ${groupName}；打开目标群后会先建立基线，旧消息不会触发提醒`;
    } catch (error) {
      statusNode.textContent = error.message;
    } finally {
      setBusy(false);
    }
  });

  grid.addEventListener("click", async (event) => {
    const newsTradeSearchAdd = event.target.closest("[data-news-trade-search-add]");
    if (newsTradeSearchAdd && !newsTradeSearchAdd.disabled && !newsTradeSearchState.loading) {
      const previewId = newsTradeSearchAdd.dataset.newsTradeSearchAdd || "";
      newsTradeSearchState = { ...newsTradeSearchState, loading: true, error: "", message: "正在加入主题监控…" };
      renderEventMonitor();
      try {
        const result = await postNewsTradeSearch("add", { previewId });
        const preview = newsTradeSearchState.preview || {};
        const topics = Array.isArray(preview.topics)
          ? preview.topics.map((topic, index) => index === 0 ? { ...topic, duplicate: true } : topic)
          : [];
        newsTradeSearchState = {
          ...newsTradeSearchState,
          loading: false,
          preview: { ...preview, topics },
          message: result.message || "主题已加入 News Trade 监控。"
        };
        await loadEventMonitor({ refresh: true, quiet: true });
        renderEventMonitor();
        statusNode.textContent = result.message || "主题已加入 News Trade 监控";
      } catch (error) {
        newsTradeSearchState = { ...newsTradeSearchState, loading: false, error: error.message, message: "" };
        renderEventMonitor();
        statusNode.textContent = error.message;
      }
      return;
    }

    const newsTradePageButton = event.target.closest("[data-news-trade-page]");
    if (newsTradePageButton && !newsTradePageButton.disabled) {
      const pageCount = Math.max(1, Math.ceil(newsTradeItems.length / NEWS_TRADE_PAGE_SIZE));
      newsTradePage = Math.max(1, Math.min(Number(newsTradePageButton.dataset.newsTradePage) || 1, pageCount));
      renderEventMonitor();
      grid.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    const structureIntervalButton = event.target.closest("[data-structure-interval]");
    if (structureIntervalButton && !loading) {
      const symbol = structureIntervalButton.dataset.symbol;
      const interval = structureIntervalButton.dataset.structureInterval;
      const intervalMeta = STRUCTURE_INTERVALS.find((item) => item.key === interval);
      const intervalName = intervalMeta?.name || interval;
      const enabled = structureIntervalButton.dataset.enabled !== "true";
      setBusy(true, `正在${enabled ? "开启" : "关闭"} ${symbol} 的${intervalName}结构播报…`);
      try {
        const payload = await postStructureAction("set_interval", symbol, { interval, enabled });
        const targetItems = currentMode === "newlow" ? newLowStructureItems : structureItems;
        const updatedItems = targetItems.map((item) => item.symbol === symbol ? {
          ...item,
          structureIntervalOverrides: payload.structureIntervalOverrides,
          structureIntervalStates: payload.structureIntervalStates,
          structure1mOverride: payload.structure1mOverride,
          structure1mEnabled: payload.structure1mEnabled,
          structure1mMode: payload.structure1mMode,
          structure1mLabel: payload.structure1mLabel,
        } : item);
        if (currentMode === "newlow") {
          newLowStructureItems = updatedItems;
          lastNewLowStructureLoadAt = Date.now();
        } else {
          structureItems = updatedItems;
          lastStructureLoadAt = Date.now();
        }
        renderStructures();
        statusNode.textContent = payload.message || `${symbol} ${intervalName}结构播报已${enabled ? "开启" : "关闭"}`;
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const structureExcludeButton = event.target.closest("[data-exclude-structure]");
    if (structureExcludeButton && !loading) {
      const symbol = structureExcludeButton.dataset.excludeStructure;
      setBusy(true, `正在从多周期结构监控剔除 ${symbol}…`);
      try {
        await postStructureAction("exclude", symbol);
        if (currentMode === "newlow") {
          newLowStructureItems = newLowStructureItems.filter((item) => item.symbol !== symbol);
          lastNewLowStructureLoadAt = Date.now();
        } else {
          structureItems = structureItems.filter((item) => item.symbol !== symbol);
          lastStructureLoadAt = Date.now();
        }
        renderStructures();
        statusNode.textContent = currentMode === "newlow"
          ? `${symbol} 已从新币低位结构监控剔除`
          : `${symbol} 已从多周期结构监控剔除；离榜后再次上榜会自动恢复`;
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const okxWalletConnect = event.target.closest("[data-wallet-connect]");
    if (okxWalletConnect && !okxWalletState.connecting) {
      const providerKey = okxWalletConnect.dataset.walletProvider || okxWalletState.providerKey || "okx";
      const switchAccount = okxWalletConnect.dataset.walletSwitchAccount === "true";
      try {
        await connectOkxWallet(null, { providerKey, switchAccount });
        if (!switchAccount || !statusNode.textContent.includes("仍在使用原账户")) {
          statusNode.textContent = `${walletProviderLabel(providerKey)} 已授权公开地址；真实交易仍会逐笔弹出钱包确认`;
        }
      } catch (error) {
        statusNode.textContent = Number(error?.code) === 4001 ? "你已取消本次钱包授权" : error.message;
      }
      return;
    }

    const newsTradeNoticeClose = event.target.closest("[data-news-trade-notice-close]");
    if (newsTradeNoticeClose) {
      newsTradeExecutionNotice = null;
      renderEventMonitor();
      return;
    }

    const newsTradePrepare = event.target.closest("[data-news-trade-prepare]");
    if (newsTradePrepare && !loading) {
      const eventId = newsTradePrepare.dataset.newsTradePrepare || "";
      const newsItem = newsTradeItems.find((item) => String(item.id || "") === eventId);
      const requestedContract = String(newsTradePrepare.dataset.newsTradeContract || "").toLowerCase();
      const requestedChain = String(newsTradePrepare.dataset.newsTradeChain || "").toLowerCase();
      const candidates = Array.isArray(newsItem?.memeCandidates) ? newsItem.memeCandidates : [];
      const opportunity = candidates.find((candidate) => {
        const contractMatches = !requestedContract
          || String(candidate?.contractAddress || "").toLowerCase() === requestedContract;
        const chainMatches = !requestedChain
          || [candidate?.chain, candidate?.chainId].some((value) => String(value || "").toLowerCase() === requestedChain);
        return contractMatches && chainMatches;
      }) || newsItem?.memeOpportunity || null;
      try {
        await connectOkxWallet(opportunity, { providerKey: okxWalletState.providerKey || "okx" });
      } catch (error) {
        statusNode.textContent = Number(error?.code) === 4001 ? "你已取消本次钱包授权" : error.message;
        return;
      }
      const maximum = Number(eventExecution.maxOrderUsdt) || 200;
      const manualNotice = newsItem?.executionEligible
        ? ""
        : `\n系统当前标记：${newsItem?.newsTradePhaseLabel || "非主动推荐阶段"}，本次将按你的手动意图继续。`;
      const input = window.prompt(`输入本次买入金额（USDT，单笔上限 ${maximum}）${manualNotice}`, String(Math.min(100, maximum)));
      if (input === null) return;
      const amountUsdt = Number(input);
      if (!Number.isFinite(amountUsdt) || amountUsdt <= 0) {
        statusNode.textContent = "请输入有效的 USDT 买入金额";
        return;
      }
      setBusy(true, "正在核对资金、跨链与链上买入路线…");
      try {
        const prepared = await prepareNewsTrade(eventId, amountUsdt, opportunity);
        const missing = Array.isArray(prepared.missingConfiguration) ? prepared.missingConfiguration : [];
        const walletAuthorization = prepared.walletAuthorization || {};
        const blockingReasons = Array.isArray(prepared.blockingReasons) ? prepared.blockingReasons : [];
        newsTradeExecutionNotice = {
          eventId,
          symbol: opportunity?.symbol || opportunity?.name || "所选标的",
          security: prepared.securityCheck || {},
          cost: prepared.costEstimate || {},
          blocked: Boolean(prepared.executionBlocked),
          reasons: blockingReasons
        };
        renderEventMonitor();
        const manualPrefix = prepared.systemRecommended === false ? "系统不主动推荐；已按你的手动意图继续。" : "";
        statusNode.textContent = prepared.executionBlocked
          ? `买入已暂停：${blockingReasons.join("、") || "安全或交易成本超过阈值"}`
          : (missing.length
          ? `${manualPrefix}钱包已连接，买入预演已生成；还需配置：${missing.join("、")}`
          : (walletAuthorization.chainMatches
            ? `${manualPrefix}已生成买入路线；手续费、滑点、价格冲击和最低可得金额已显示，等待最终确认`
            : `钱包已连接，请先切换到 ${opportunity?.chainLabel || "目标链"} 后再确认买入`));
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const chainAddToggle = event.target.closest("[data-chain-add-toggle]");
    if (chainAddToggle) {
      const chainForm = grid.querySelector("[data-chain-form]");
      if (chainForm) chainForm.hidden = !chainForm.hidden;
      return;
    }

    const chainSelect = event.target.closest("[data-chain-select]");
    if (chainSelect && !loading) {
      selectedChainSlug = chainSelect.dataset.chainSelect || "";
      const url = new URL(window.location.href);
      if (selectedChainSlug) url.searchParams.set("chain", selectedChainSlug);
      else url.searchParams.delete("chain");
      window.history.replaceState({}, "", url);
      lastChainEcosystemLoadAt = 0;
      setBusy(true, "正在切换公链生态…");
      try {
        await loadChainEcosystem();
      } finally {
        setBusy(false);
      }
      return;
    }

    const chainRefresh = event.target.closest("[data-chain-refresh]");
    if (chainRefresh && !loading) {
      await load({ refresh: true });
      return;
    }

    const alertAck = event.target.closest("[data-chain-alert-ack]");
    if (alertAck && !loading && chainEcosystemPayload.selectedChain) {
      setBusy(true, "正在确认预警…");
      try {
        const result = await postChainEcosystemAction("ack_alert", {
          chainId: chainEcosystemPayload.selectedChain.id,
          alertId: Number(alertAck.dataset.chainAlertAck) || 0
        });
        chainEcosystemPayload = result.payload || chainEcosystemPayload;
        lastChainEcosystemLoadAt = Date.now();
        renderChainEcosystem();
        statusNode.textContent = "预警已确认，历史记录仍然保留";
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const wechatToggle = event.target.closest("[data-wechat-toggle]");
    if (wechatToggle && !loading) {
      const groupName = wechatToggle.dataset.wechatToggle;
      const enabled = wechatToggle.dataset.wechatEnabled !== "1";
      const platform = wechatToggle.dataset.wechatPlatform === "qq" ? "qq" : "wechat";
      const senderFilter = wechatToggle.dataset.wechatSender || "";
      const forwardToWechat = wechatToggle.dataset.wechatForward === "1";
      const forwardTarget = wechatToggle.dataset.wechatForwardTarget || "";
      setBusy(true, `${enabled ? "正在继续" : "正在暂停"} ${groupName}…`);
      try {
        wechatMonitorPayload = await postWechatMonitorAction("save", {
          groupName,
          platform,
          senderFilter,
          forwardToWechat,
          forwardTarget,
          enabled
        });
        lastWechatLoadAt = Date.now();
        renderWechatMonitor();
        statusNode.textContent = `${groupName} 已${enabled ? "继续" : "暂停"}读取；已识别机会仍会保留并持续监控`;
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const wechatRemove = event.target.closest("[data-wechat-remove]");
    if (wechatRemove && !loading) {
      const groupName = wechatRemove.dataset.wechatRemove;
      setBusy(true, `正在移除 ${groupName} 的群聊连接…`);
      try {
        wechatMonitorPayload = await postWechatMonitorAction("remove", { groupName });
        lastWechatLoadAt = Date.now();
        renderWechatMonitor();
        statusNode.textContent = `${groupName} 已从群聊列表移除；历史机会与对应币种监控未删除`;
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const opportunityRemove = event.target.closest("[data-wechat-opportunity-remove]");
    if (opportunityRemove && !loading) {
      const symbol = opportunityRemove.dataset.wechatOpportunityRemove;
      setBusy(true, `正在停止 ${symbol} 的长期机会监控…`);
      try {
        await postAction("remove", symbol);
        lastWechatLoadAt = 0;
        await loadWechatMonitor({ refresh: false });
        statusNode.textContent = `${symbol} 已手动移出长期监控；机会记录仍会保留`;
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const confirmButton = event.target.closest("[data-confirm]");
    if (confirmButton && !loading) {
      const symbol = confirmButton.dataset.confirm;
      const episode = Number(confirmButton.dataset.episode) || 0;
      setBusy(true, `正在确认 ${symbol} 的首次有效突破…`);
      confirmButton.disabled = true;
      try {
        const payload = await postAction("confirm", symbol, { episode });
        render(payload);
        statusNode.textContent = `${symbol} 已标记为首次有效突破；后续信号将显示为“非首次突破”`;
      } catch (error) {
        statusNode.textContent = error.message;
        confirmButton.disabled = false;
      } finally {
        setBusy(false);
      }
      return;
    }

    const priorHighExcludeButton = event.target.closest("[data-exclude-prior-high]");
    if (priorHighExcludeButton && !loading) {
      const symbol = priorHighExcludeButton.dataset.excludePriorHigh;
      setBusy(true, `正在从前高监控池剔除 ${symbol}…`);
      try {
        const payload = await postAction("exclude_prior_high", symbol);
        render(payload);
        statusNode.textContent = `${symbol} 已从 AICoin 前高监控池剔除；离榜后再次上榜会自动恢复`;
      } catch (error) {
        statusNode.textContent = error.message;
      } finally {
        setBusy(false);
      }
      return;
    }

    const removeButton = event.target.closest("[data-remove]");
    if (!removeButton || loading) return;
    const symbol = removeButton.dataset.remove;
    setBusy(true, `正在移除 ${symbol}…`);
    try {
      const payload = await postAction("remove", symbol);
      render(payload);
      statusNode.textContent = `${symbol} 已移除`;
    } catch (error) {
      statusNode.textContent = error.message;
    } finally {
      setBusy(false);
    }
  });

  refreshButton.addEventListener("click", () => load({ refresh: true }));
  modeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const requested = button.dataset.watchMode;
      currentMode = supportedModes.includes(requested) ? requested : "prior";
      const url = new URL(window.location.href);
      if (currentMode !== "prior") url.searchParams.set("mode", currentMode);
      else url.searchParams.delete("mode");
      window.history.replaceState({}, "", url);
      updateModePresentation();
      render({ items });
      if (["structure", "newlow"].includes(currentMode)) {
        statusNode.textContent = currentMode === "newlow"
          ? "正在读取近一年新币低位结构后台轮询结果"
          : "正在用龙头策略扫描 AICoin 热门币六周期行情";
        loadStructures();
      } else if (currentMode === "mapping") {
        statusNode.textContent = "正在建立龙头、家族与题材补涨映射";
        loadMappings();
      } else if (currentMode === "aster") {
        statusNode.textContent = "正在读取 Aster 永续合约列表";
        loadAsterContracts();
      } else if (currentMode === "events" || currentMode === "news") {
        statusNode.textContent = currentMode === "news" ? "正在筛选高置信 News Trade 候选" : "正在核对二级事件来源";
        loadEventMonitor();
      } else if (currentMode === "wechat") {
        statusNode.textContent = "正在检查当前可见的微信与 Q 群";
        loadWechatMonitor();
      } else if (currentMode === "personalx") {
        statusNode.textContent = "正在连接 @whitestar224 秒级实时流";
        loadPersonalXMonitor();
      } else if (currentMode === "chains") {
        statusNode.textContent = "正在读取公链生命周期与 L0-L3 生态市场";
        loadChainEcosystem();
      } else {
        statusNode.textContent = currentMode === "oversold"
          ? "接近低位阶段高点或主升浪 Fib 0.5 / 0.618 时提醒"
          : "现价低于最近 7 日前高且距离不超过 3% 时提醒";
      }
    });
  });
  updateModePresentation();
  initializeOkxWallet();
  load();
  window.setInterval(() => {
    if (currentMode === "events" || currentMode === "news") {
      loadEventMonitor({ quiet: true });
      return;
    }
    if (currentMode === "wechat") return;
    if (currentMode === "personalx") {
      if (!personalXStreamReady) loadPersonalXMonitor({ refresh: true, quiet: true });
      else connectPersonalXStream();
      return;
    }
    if (currentMode === "chains") return;
    load({ quiet: true });
  }, 30_000);
  window.setInterval(() => {
    if (["structure", "newlow"].includes(currentMode)) loadStructures({ live: true, quiet: true });
  }, STRUCTURE_SYNC_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && ["structure", "newlow"].includes(currentMode)) {
      loadStructures({ live: true, quiet: true });
    }
  });
  window.setInterval(() => {
    if (currentMode === "wechat") loadWechatMonitor({ quiet: true });
  }, 5_000);
  window.setInterval(() => {
    if (currentMode === "chains") loadChainEcosystem({ quiet: true });
  }, 60_000);
})();
