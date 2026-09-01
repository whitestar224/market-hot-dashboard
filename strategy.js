(() => {
  const state = {
    payload: null,
    selected: "",
    preview: null,
    filter: "all",
    busy: false,
    breakEvenKey: "",
    exchangeSettings: [],
    activeExchange: "binance",
    exchangeSettingsBusy: false,
    sizingMode: "risk"
  };
  const nodes = {
    signals: document.querySelector("#strategySignals"),
    positions: document.querySelector("#strategyPositionsList"),
    status: document.querySelector("#strategyStatus"),
    refresh: document.querySelector("#strategyRefresh"),
    equity: document.querySelector("#accountEquity"),
    totalCapital: document.querySelector("#totalCapital"),
    riskExposurePct: document.querySelector("#riskExposurePct"),
    dailyLoss: document.querySelector("#dailyLoss"),
    mode: document.querySelector("#tradeMode"),
    orderMargin: document.querySelector("#orderMargin"),
    marginRatio: document.querySelector("#marginRatio"),
    marginAvailable: document.querySelector("#marginAvailable"),
    leverage: document.querySelector("#leverage"),
    stop: document.querySelector("#stopLossPrice"),
    breakout: document.querySelector("#breakoutPrice"),
    exchange: document.querySelector("#strategyExchange"),
    calculate: document.querySelector("#calculatePosition"),
    stage: document.querySelector("#stageOrder"),
    preview: document.querySelector("#riskPreview"),
    riskSymbol: document.querySelector("#riskSymbol"),
    riskTier: document.querySelector("#riskTier"),
    orderDialog: document.querySelector("#orderConfirmDialog"),
    orderSummary: document.querySelector("#orderConfirmSummary"),
    orderAck: document.querySelector("#orderAcknowledgement"),
    confirmOrder: document.querySelector("#confirmOrderButton"),
    breakEvenDialog: document.querySelector("#breakEvenDialog"),
    breakEvenSummary: document.querySelector("#breakEvenSummary"),
    breakEvenAck: document.querySelector("#breakEvenAcknowledgement"),
    confirmBreakEven: document.querySelector("#confirmBreakEvenButton"),
    exchangeApiSettings: document.querySelector("#exchangeApiSettings"),
    exchangeApiDialog: document.querySelector("#exchangeApiDialog"),
    exchangeApiTabs: document.querySelector("#exchangeApiTabs"),
    exchangeApiStateDot: document.querySelector("#exchangeApiStateDot"),
    exchangeApiState: document.querySelector("#exchangeApiState"),
    exchangeApiHint: document.querySelector("#exchangeApiHint"),
    exchangeApiKey: document.querySelector("#exchangeApiKey"),
    exchangeApiSecret: document.querySelector("#exchangeApiSecret"),
    exchangeApiPassphraseField: document.querySelector("#exchangeApiPassphraseField"),
    exchangeApiPassphrase: document.querySelector("#exchangeApiPassphrase"),
    exchangeApiMessage: document.querySelector("#exchangeApiMessage"),
    saveExchangeApi: document.querySelector("#saveExchangeApi"),
    removeExchangeApi: document.querySelector("#removeExchangeApi"),
    positionModeLabel: document.querySelector("#positionModeLabel")
  };

  const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const number = (value) => Number(value) || 0;
  const money = (value, digits = 2) => `$${number(value).toLocaleString("en-US", { maximumFractionDigits: digits })}`;
  const price = (value) => {
    const current = number(value);
    if (!current) return "--";
    if (current >= 1000) return money(current, 2);
    if (current >= 1) return money(current, 4);
    return `$${current.toLocaleString("en-US", { maximumSignificantDigits: 7 })}`;
  };

  function setBusy(value, text = "") {
    state.busy = value;
    nodes.refresh.disabled = value;
    nodes.calculate.disabled = value || !state.selected;
    if (text) nodes.status.textContent = text;
  }

  async function request(method = "GET", body = null, refresh = false) {
    const response = await fetch(`/api/strategy-board${refresh ? "?refresh=1" : ""}`, {
      method,
      cache: "no-store",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      const error = new Error(payload.error || "策略数据读取失败");
      error.status = response.status;
      error.loginUrl = payload.loginUrl;
      throw error;
    }
    return payload;
  }

  async function exchangeSettingsRequest(method = "GET", body = null) {
    const response = await fetch("/api/strategy-exchanges", {
      method,
      cache: "no-store",
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      const error = new Error(payload.error || "交易 API 配置读取失败");
      error.status = response.status;
      error.loginUrl = payload.loginUrl;
      throw error;
    }
    return payload;
  }

  function currentExchangeSetting() {
    return state.exchangeSettings.find((item) => item.id === state.activeExchange) || {
      id: state.activeExchange,
      label: state.activeExchange.toUpperCase(),
      configured: false,
      available: false,
      source: "none",
      keyHint: ""
    };
  }

  function setExchangeSettingsBusy(value) {
    state.exchangeSettingsBusy = value;
    nodes.saveExchangeApi.disabled = value;
    nodes.removeExchangeApi.disabled = value || !currentExchangeSetting().configured;
    nodes.exchangeApiTabs.querySelectorAll("button").forEach((button) => { button.disabled = value; });
  }

  function renderExchangeSettings() {
    const setting = currentExchangeSetting();
    nodes.exchangeApiTabs.querySelectorAll("[data-exchange-tab]").forEach((button) => {
      const active = button.dataset.exchangeTab === state.activeExchange;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    nodes.exchangeApiKey.value = "";
    nodes.exchangeApiSecret.value = "";
    nodes.exchangeApiPassphrase.value = "";
    nodes.exchangeApiPassphraseField.hidden = !setting.requiresPassphrase;
    const retained = setting.configured ? "已保存，留空则保留" : "请输入";
    nodes.exchangeApiKey.placeholder = `${retained} API Key`;
    nodes.exchangeApiSecret.placeholder = `${retained} API Secret`;
    nodes.exchangeApiPassphrase.placeholder = setting.passphraseConfigured
      ? "已保存，留空则保留 API 密码"
      : "输入创建 OKX API 时设置的密码";
    nodes.exchangeApiStateDot.className = setting.available ? "is-ready" : "";
    nodes.exchangeApiState.textContent = setting.available
      ? setting.source === "server" ? "服务端配置可用" : "已加密保存"
      : setting.configured ? "配置不完整" : "未配置";
    nodes.exchangeApiHint.textContent = setting.keyHint ? `Key ${setting.keyHint}` : "";
    const missing = Array.isArray(setting.missingFields) && setting.missingFields.length
      ? `缺少：${setting.missingFields.join("、")}。`
      : "";
    nodes.exchangeApiMessage.textContent = missing || (setting.source === "server"
      ? "当前使用服务端备用配置。保存后将优先使用当前账号配置。"
      : setting.configured
        ? "密钥按当前账号隔离保存，页面不会回显完整内容。"
        : "仅用于交易准备和账户级配置，不会在页面中公开。");
    nodes.exchangeApiMessage.className = "strategy-api-message";
    nodes.removeExchangeApi.disabled = state.exchangeSettingsBusy || !setting.configured;
  }

  async function loadExchangeSettings() {
    setExchangeSettingsBusy(true);
    nodes.exchangeApiMessage.textContent = "正在读取配置";
    try {
      const payload = await exchangeSettingsRequest();
      state.exchangeSettings = Array.isArray(payload.exchanges) ? payload.exchanges : [];
      renderExchangeSettings();
      return true;
    } catch (error) {
      if (error.status === 401) {
        window.location.href = error.loginUrl || "/login.html?next=/strategy.html";
        return false;
      }
      nodes.exchangeApiMessage.textContent = error.message;
      nodes.exchangeApiMessage.className = "strategy-api-message is-error";
      return false;
    } finally {
      setExchangeSettingsBusy(false);
    }
  }

  async function saveExchangeSettings(action = "save") {
    if (state.exchangeSettingsBusy) return;
    setExchangeSettingsBusy(true);
    nodes.exchangeApiMessage.textContent = action === "remove" ? "正在删除配置" : "正在加密保存";
    nodes.exchangeApiMessage.className = "strategy-api-message";
    try {
      const payload = await exchangeSettingsRequest("POST", {
        action,
        exchange: state.activeExchange,
        apiKey: nodes.exchangeApiKey.value,
        apiSecret: nodes.exchangeApiSecret.value,
        apiPassphrase: nodes.exchangeApiPassphrase.value
      });
      state.exchangeSettings = Array.isArray(payload.exchanges) ? payload.exchanges : [];
      renderExchangeSettings();
      nodes.exchangeApiMessage.textContent = action === "remove" ? "当前账号配置已删除" : "配置已加密保存";
      nodes.exchangeApiMessage.className = "strategy-api-message is-success";
      await load(false, true);
    } catch (error) {
      if (error.status === 401) {
        window.location.href = error.loginUrl || "/login.html?next=/strategy.html";
        return;
      }
      nodes.exchangeApiMessage.textContent = error.message;
      nodes.exchangeApiMessage.className = "strategy-api-message is-error";
    } finally {
      setExchangeSettingsBusy(false);
    }
  }

  function updateMetrics(summary = {}) {
    const values = { strategyWatching: summary.watching, strategyStructured: summary.structured, strategyAwaiting: summary.awaitingConfirmation, strategyReady: summary.ready, strategyPositions: summary.openPositions };
    Object.entries(values).forEach(([id, value]) => { const node = document.querySelector(`#${id}`); if (node) node.textContent = number(value); });
  }

  function phaseLabel(item) {
    if (item?.strategySignal && item?.strategySignalInvalidReason) return "买点失效";
    if (item?.strategySignal) return "买点触发";
    if (item?.strategyPending) return "起爆预判";
    if (item?.strategyFrame) return "结构形成";
    return ["等待监控", "策略观察", "结构形成", "起爆预判", "买点触发"][Math.max(0, Math.min(4, number(item?.phase)))] || "监控中";
  }

  function phaseBar(item) {
    const phase = number(item.phase);
    const label = phaseLabel(item);
    return `<span class="strategy-phase" title="${escapeHtml(label)}">${[1, 2, 3, 4].map((step) => `<i class="${step <= phase ? "is-done" : ""}${step === phase ? " is-current" : ""}"></i>`).join("")}</span><em>${escapeHtml(label)}</em>`;
  }

  function signalRow(item) {
    const icon = item.icon ? `<img src="${escapeHtml(item.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()" />` : "";
    const exchanges = Array.isArray(item.exchanges) ? item.exchanges : [];
    const recommended = exchanges.find((entry) => entry.recommended) || exchanges[0] || {};
    const engineSignal = item.strategySignal || item.strategyPending || {};
    const engineLabel = item.strategyEngine === "dragon-wave-engine"
      ? [item.strategyInterval, item.strategyGrade, item.strategyPattern].filter(Boolean).join(" · ") || "龙头起爆引擎"
      : item.setupType || "前高监控";
    const action = item.strategyReady
      ? `<button class="strategy-row-action is-ready" type="button" data-stage="${escapeHtml(item.symbol)}">试算</button>`
      : item.strategyPending
        ? `<button class="strategy-row-action" type="button" data-select="${escapeHtml(item.symbol)}">查看预判</button>`
      : item.isFirstCandidate
        ? `<button class="strategy-row-action" type="button" data-confirm-signal="${escapeHtml(item.symbol)}" data-episode="${number(item.latestAlertEpisode)}">确认有效</button>`
        : `<button class="strategy-row-action" type="button" data-select="${escapeHtml(item.symbol)}">查看</button>`;
    return `<article class="strategy-signal-row ${state.selected === item.symbol ? "is-selected" : ""}" data-signal="${escapeHtml(item.symbol)}">
      <span class="strategy-coin"><i>${icon}<b>${escapeHtml(item.symbol.slice(0, 2))}</b></i><span><strong>${escapeHtml(item.symbol)}</strong><em>${escapeHtml(item.name || item.symbol)}</em></span></span>
      <span class="strategy-stage-cell">${phaseBar(item)}</span>
      <span class="strategy-price-cell"><strong>${price(item.currentPrice)}</strong><em title="${escapeHtml(item.strategyStopLossReason || "")}">${price(engineSignal.triggerPrice || item.weekHigh)} · 止损 ${price(item.strategyStopLossPrice)}</em></span>
      <span class="strategy-exchange-cell"><strong>${escapeHtml(engineLabel)}</strong><em>${escapeHtml(item.provider || recommended.label || "待行情")}</em></span>
      <span>${action}</span>
    </article>`;
  }

  function renderSignals() {
    const all = Array.isArray(state.payload?.signals) ? state.payload.signals : [];
    const filtered = all.filter((item) => state.filter === "ready" ? item.strategyReady : state.filter === "confirm" ? (item.strategyPredicted || item.isFirstCandidate) : true);
    nodes.signals.innerHTML = filtered.length ? filtered.map(signalRow).join("") : `<div class="strategy-empty"><b>当前筛选没有信号</b></div>`;
  }

  function selectedExchangeAccount() {
    const exchange = String(nodes.exchange.value || "").toLowerCase();
    const accounts = Array.isArray(state.payload?.exchangeAccounts) ? state.payload.exchangeAccounts : [];
    return accounts.find((item) => String(item.exchange || "").toLowerCase() === exchange) || null;
  }

  function availableMarginInfo() {
    const account = selectedExchangeAccount();
    const rawBalance = account?.availableBalance;
    const hasRealBalance = account?.ok === true && rawBalance !== null && rawBalance !== "" && Number.isFinite(Number(rawBalance));
    if (hasRealBalance) {
      return {
        value: Math.max(0, Number(rawBalance)),
        source: account.label || String(account.exchange || "").toUpperCase(),
        real: true,
        error: ""
      };
    }
    if (account?.credentialsStored || account?.configured) {
      return {
        value: 0,
        source: account.label || String(account.exchange || "").toUpperCase(),
        real: false,
        error: account.error || "真实账户余额读取失败"
      };
    }
    const equity = Math.max(0, number(nodes.totalCapital.value || nodes.equity.value));
    const positions = Array.isArray(state.payload?.positions) ? state.payload.positions : [];
    const occupied = positions.reduce((sum, item) => sum + Math.max(0, number(item.margin)), 0);
    return {
      value: nodes.mode.value === "live" && account?.configured ? 0 : Math.max(0, equity - occupied),
      source: "模拟资金",
      real: false,
      error: account?.error || ""
    };
  }

  function availableMargin() {
    return availableMarginInfo().value;
  }

  function updateMarginGuide() {
    const info = availableMarginInfo();
    const available = info.value;
    const margin = Math.max(0, number(nodes.orderMargin.value));
    const ratio = available > 0 ? Math.min(100, margin / available * 100) : 0;
    const selectedAccount = selectedExchangeAccount();
    nodes.marginAvailable.textContent = info.real
      ? `${info.source} 可用 ${available.toLocaleString("en-US", { maximumFractionDigits: 2 })} USDT`
      : selectedAccount?.credentialsStored || selectedAccount?.configured
        ? selectedAccount?.error?.includes("不完整") ? "OKX 配置不完整" : "真实账户余额读取失败"
        : `模拟可用 ${available.toLocaleString("en-US", { maximumFractionDigits: 2 })} USDT`;
    nodes.marginAvailable.title = info.error || (info.real ? "交易所合约账户实时可用余额" : "按策略账户资金估算");
    nodes.marginAvailable.classList.toggle("is-error", Boolean((selectedAccount?.credentialsStored || selectedAccount?.configured) && !info.real));
    nodes.marginRatio.disabled = Boolean((selectedAccount?.credentialsStored || selectedAccount?.configured) && !info.real);
    nodes.orderMargin.max = info.real ? String(available) : "";
    nodes.marginRatio.value = String(ratio);
    document.querySelectorAll("[data-margin-ratio]").forEach((button) => {
      button.classList.toggle("is-active", Math.abs(number(button.dataset.marginRatio) - ratio) < 1);
    });
  }

  function useMarginRatio(value) {
    const ratio = Math.max(0, Math.min(100, number(value)));
    const margin = availableMargin() * ratio / 100;
    state.sizingMode = "margin";
    nodes.marginRatio.value = String(ratio);
    nodes.orderMargin.value = margin > 0 ? String(Number(margin.toFixed(4))) : "";
    updateMarginGuide();
  }

  function renderPreview(preview) {
    state.preview = preview;
    if (preview.exchangeAccount) {
      const accounts = Array.isArray(state.payload?.exchangeAccounts) ? [...state.payload.exchangeAccounts] : [];
      const index = accounts.findIndex((item) => item.exchange === preview.exchangeAccount.exchange);
      if (index >= 0) accounts[index] = preview.exchangeAccount;
      else accounts.push(preview.exchangeAccount);
      if (state.payload) state.payload.exchangeAccounts = accounts;
    }
    nodes.riskSymbol.textContent = `${preview.symbol} · ${preview.exchange.toUpperCase()}`;
    nodes.riskTier.textContent = preview.tier?.label || "--";
    nodes.leverage.value = preview.leverage;
    nodes.totalCapital.value = preview.accountEquity;
    nodes.equity.value = preview.accountEquity;
    nodes.riskExposurePct.value = preview.requestedRiskPct;
    nodes.stop.value = preview.stopLossPrice;
    nodes.breakout.value = preview.breakoutPrice;
    nodes.exchange.value = preview.exchange;
    nodes.orderMargin.value = preview.marginRequired;
    updateMarginGuide();
    nodes.preview.classList.remove("is-empty");
    const strategySignal = [preview.strategySignalInterval, preview.strategySignalGrade, preview.strategySignalPattern].filter(Boolean).join(" · ");
    nodes.preview.innerHTML = `${strategySignal ? `<div><span>策略信号</span><b>${escapeHtml(strategySignal)}</b></div>` : ""}<div><span>实际最大风险</span><b>${money(preview.riskBudget)} · ${number(preview.riskPct).toFixed(2)}%</b></div><div><span>保证金 / 杠杆</span><b>${money(preview.marginRequired)} / ${preview.leverage}x</b></div><div><span>下单数量</span><b>${number(preview.quantity).toLocaleString("en-US", { maximumFractionDigits: 8 })}</b></div><div><span>突破位 / 止损</span><b>${price(preview.breakoutPrice)} / ${price(preview.stopLossPrice)}</b></div><div><span>止损依据</span><b>${escapeHtml(preview.stopLossSourceLabel || "入场价下方3%")} · ${number(preview.stopDistancePct).toFixed(2)}%</b></div>${preview.stopLossReason ? `<p class="risk-stop-reason">${escapeHtml(preview.stopLossReason)}</p>` : ""}${preview.warning ? `<p class="risk-warning">${escapeHtml(preview.warning)}</p>` : ""}${preview.blocked ? `<p class="risk-blocked">${escapeHtml(preview.blockedReason)}</p>` : ""}`;
    nodes.stage.disabled = preview.blocked || !preview.signalConfirmed;
    nodes.stage.textContent = preview.signalConfirmed ? "人工确认下单" : (preview.strategySignalPending ? "等待买点触发" : "先确认有效突破");
  }

  function orderPayload(action = "preview") {
    return { action, symbol: state.selected, totalCapital: nodes.totalCapital.value, accountEquity: nodes.totalCapital.value || nodes.equity.value, riskExposurePct: nodes.riskExposurePct.value, currentGrossExposure: 0, dailyLoss: nodes.dailyLoss.value, orderMargin: nodes.orderMargin.value, sizingMode: state.sizingMode, leverage: nodes.leverage.value, exchange: nodes.exchange.value, mode: nodes.mode.value };
  }

  async function calculate() {
    if (!state.selected || state.busy) return;
    setBusy(true, `正在计算 ${state.selected} 风险敞口`);
    try {
      const preview = await request("POST", orderPayload());
      renderPreview(preview);
      nodes.status.textContent = "仓位已由服务端计算";
    } catch (error) { nodes.status.textContent = error.message; }
    finally { setBusy(false); }
  }

  function selectSignal(symbol, calculateNow = false) {
    state.selected = symbol;
    state.preview = null;
    nodes.riskSymbol.textContent = symbol;
    nodes.riskTier.textContent = "等待试算";
    nodes.preview.className = "risk-preview is-empty";
    nodes.calculate.disabled = false;
    nodes.stage.disabled = true;
    const item = state.payload?.signals?.find((entry) => entry.symbol === symbol);
    nodes.breakout.value = number(item?.strategySignal?.triggerPrice || item?.strategyPending?.triggerPrice || item?.weekHigh) || "";
    nodes.stop.value = number(item?.strategyStopLossPrice) || "";
    nodes.preview.innerHTML = item?.strategySignalInvalidReason
      ? `<span>${escapeHtml(item.strategySignalInvalidReason)}</span>`
      : item?.strategyStopLossPrice
      ? `<span>${escapeHtml(item.strategyStopLossSourceLabel || "计划止损")} · ${price(item.strategyStopLossPrice)} · ${number(item.strategyStopDistancePct).toFixed(2)}%</span>`
      : "<span>点击计算仓位</span>";
    const preferred = item?.exchanges?.find((entry) => entry.recommended) || item?.exchanges?.[0];
    if (preferred) nodes.exchange.value = preferred.id;
    updateMarginGuide();
    renderSignals();
    if (calculateNow) calculate();
  }

  function positionRow(item) {
    const pnlClass = number(item.unrealizedPnl) >= 0 ? "is-up" : "is-down";
    const symbol = String(item.symbol || "");
    const sideLabel = String(item.side || "long").toLowerCase() === "short" ? "空" : "多";
    const isExchangePosition = item.source === "exchange";
    const breakEven = isExchangePosition
      ? `<span class="position-live-source">API 读取</span>`
      : item.breakEvenAt
        ? `<span class="position-breakeven-set">已保本</span>`
        : `<button class="position-breakeven" type="button" data-break-even="${escapeHtml(item.key)}" ${item.canBreakEven ? "" : "disabled"}>保本</button>`;
    const stopPrice = number(item.stopLossPrice) > 0 ? price(item.stopLossPrice) : "--";
    const stopDetail = item.breakEvenAt
      ? "成本上方"
      : number(item.stopLossPrice) > 0
        ? "结构止损"
        : number(item.liquidationPrice) > 0
          ? `强平 ${price(item.liquidationPrice)}`
          : "未读取止损单";
    return `<article class="strategy-position-row"><span class="strategy-coin"><i>${item.icon ? `<img src="${escapeHtml(item.icon)}" alt="" />` : ""}<b>${escapeHtml(symbol.slice(0, 2))}</b></i><span><strong>${escapeHtml(symbol)} · ${sideLabel}</strong><em>${escapeHtml(String(item.exchange || "").toUpperCase())} / ${escapeHtml(item.mode === "paper" ? "模拟" : "真实")}</em></span></span><span><strong>${price(item.entryPrice)}</strong><em>${price(item.markPrice)}</em></span><span><strong>${number(item.quantity).toLocaleString("en-US", { maximumFractionDigits: 8 })}</strong></span><span><strong>${money(item.margin)}</strong><em>${number(item.leverage)}x</em></span><span class="${pnlClass}"><strong>${money(item.unrealizedPnl)}</strong><em>${number(item.unrealizedPnlPct).toFixed(2)}%</em></span><span><strong>${stopPrice}</strong><em>${escapeHtml(stopDetail)}</em></span><span>${breakEven}</span></article>`;
  }

  function renderPositions() {
    const positions = Array.isArray(state.payload?.positions) ? state.payload.positions : [];
    const sync = Array.isArray(state.payload?.positionSync) ? state.payload.positionSync : [];
    const configured = sync.filter((item) => item.configured);
    const failures = configured.filter((item) => !item.ok);
    const synced = configured.filter((item) => item.ok);
    if (nodes.positionModeLabel) {
      nodes.positionModeLabel.classList.toggle("is-error", failures.length > 0);
      nodes.positionModeLabel.textContent = failures.length
        ? "真实仓同步失败"
        : synced.length
          ? `${synced.map((item) => item.label).join(" / ")} · ${synced.reduce((sum, item) => sum + number(item.count), 0)} 仓`
          : "模拟仓";
    }
    const notices = failures.map((item) => `<div class="strategy-position-sync is-error"><strong>${escapeHtml(item.label)}持仓读取失败</strong><span>${escapeHtml(item.message)}</span></div>`).join("");
    const empty = `<div class="strategy-empty"><b>${state.payload?.authenticated ? (failures.length ? "真实持仓暂未同步" : "当前合约账户暂无持仓") : "登录后查看并管理仓位"}</b></div>`;
    nodes.positions.innerHTML = notices + (positions.length ? positions.map(positionRow).join("") : empty);
  }

  function render(payload) {
    state.payload = payload;
    updateMetrics(payload.summary);
    nodes.mode.querySelector('[value="live"]').disabled = !payload.liveTradingEnabled;
    if (!payload.liveTradingEnabled && nodes.mode.value === "live") nodes.mode.value = "paper";
    renderSignals();
    renderPositions();
    updateMarginGuide();
  }

  async function load(refresh = false, quiet = false) {
    if (state.busy) return;
    setBusy(true, refresh ? "正在刷新价格与结构" : "");
    try { render(await request("GET", null, refresh)); if (!quiet) nodes.status.textContent = "信号已同步"; }
    catch (error) { if (!quiet) nodes.status.textContent = error.message; }
    finally { setBusy(false); }
  }

  nodes.signals.addEventListener("click", async (event) => {
    const confirm = event.target.closest("[data-confirm-signal]");
    if (confirm) {
      const symbol = confirm.dataset.confirmSignal;
      setBusy(true, `正在确认 ${confirm.dataset.confirmSignal}`);
      try {
        const response = await fetch("/api/price-watch", { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "confirm", symbol, episode: number(confirm.dataset.episode) }) });
        const payload = await response.json();
        if (!response.ok || payload.ok === false) throw new Error(payload.error || "确认失败");
        setBusy(false);
        await load(true);
        selectSignal(symbol);
        await calculate();
      } catch (error) { nodes.status.textContent = error.message; }
      finally { setBusy(false); }
      return;
    }
    const target = event.target.closest("[data-stage], [data-select], [data-signal]");
    const symbol = target?.dataset.stage || target?.dataset.select || target?.dataset.signal;
    if (symbol) selectSignal(symbol, Boolean(target?.dataset.stage));
  });

  document.querySelectorAll("[data-signal-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.signalFilter;
    document.querySelectorAll("[data-signal-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
    renderSignals();
  }));
  nodes.calculate.addEventListener("click", calculate);
  nodes.orderMargin.addEventListener("input", () => { state.sizingMode = "margin"; updateMarginGuide(); });
  nodes.marginRatio.addEventListener("input", () => useMarginRatio(nodes.marginRatio.value));
  nodes.marginRatio.addEventListener("change", () => { if (state.selected) calculate(); });
  document.querySelector(".risk-margin-marks").addEventListener("click", (event) => {
    const button = event.target.closest("[data-margin-ratio]");
    if (!button) return;
    useMarginRatio(button.dataset.marginRatio);
    if (state.selected) calculate();
  });
  nodes.equity.addEventListener("input", () => { nodes.totalCapital.value = nodes.equity.value; state.sizingMode = "risk"; updateMarginGuide(); });
  nodes.totalCapital.addEventListener("input", () => { nodes.equity.value = nodes.totalCapital.value; state.sizingMode = "risk"; updateMarginGuide(); });
  nodes.riskExposurePct.addEventListener("input", () => { state.sizingMode = "risk"; });
  nodes.mode.addEventListener("change", () => { updateMarginGuide(); if (state.selected) calculate(); });
  [nodes.equity, nodes.totalCapital, nodes.riskExposurePct, nodes.dailyLoss, nodes.orderMargin, nodes.leverage, nodes.exchange].forEach((node) => node.addEventListener("change", () => { updateMarginGuide(); if (state.selected) calculate(); }));
  nodes.refresh.addEventListener("click", () => load(true));

  nodes.exchangeApiSettings.addEventListener("click", async () => {
    state.activeExchange = "binance";
    nodes.exchangeApiDialog.showModal();
    await loadExchangeSettings();
  });

  nodes.exchangeApiTabs.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-exchange-tab]");
    if (!tab || state.exchangeSettingsBusy) return;
    state.activeExchange = tab.dataset.exchangeTab;
    renderExchangeSettings();
  });

  nodes.saveExchangeApi.addEventListener("click", () => saveExchangeSettings("save"));
  nodes.removeExchangeApi.addEventListener("click", () => {
    const setting = currentExchangeSetting();
    if (!setting.configured || !window.confirm(`删除当前账号的 ${setting.label} API 配置？`)) return;
    saveExchangeSettings("remove");
  });

  nodes.stage.addEventListener("click", () => {
    const p = state.preview;
    if (!p || p.blocked) return;
    nodes.orderSummary.innerHTML = `<div><span>标的</span><b>${escapeHtml(p.symbol)} · 做多</b></div><div><span>交易所</span><b>${escapeHtml(p.exchange.toUpperCase())}</b></div><div><span>策略</span><b>${escapeHtml([p.strategySignalInterval, p.strategySignalGrade, p.strategySignalPattern].filter(Boolean).join(" · ") || p.setupType || "突破策略")}</b></div><div><span>保证金 / 杠杆</span><b>${money(p.marginRequired)} / ${p.leverage}x</b></div><div><span>下单数量</span><b>${number(p.quantity).toLocaleString("en-US", { maximumFractionDigits: 8 })}</b></div><div><span>突破位 / 止损</span><b>${price(p.breakoutPrice)} / ${price(p.stopLossPrice)}</b></div><div><span>止损依据</span><b>${escapeHtml(p.stopLossSourceLabel || "入场价下方3%")} · ${number(p.stopDistancePct).toFixed(2)}%</b></div><div><span>实际最大风险</span><b>${money(p.riskBudget)} · ${number(p.riskPct).toFixed(2)}%</b></div>`;
    nodes.orderAck.value = "";
    nodes.orderDialog.showModal();
  });

  nodes.confirmOrder.addEventListener("click", async () => {
    nodes.confirmOrder.disabled = true;
    try {
      const payload = await request("POST", { ...orderPayload("submit"), acknowledgement: nodes.orderAck.value });
      nodes.orderDialog.close(); render(payload); nodes.status.textContent = `${state.selected} 模拟仓已建立`;
    } catch (error) {
      if (error.status === 401) window.location.href = error.loginUrl || "/login.html?next=/strategy.html";
      else nodes.orderSummary.insertAdjacentHTML("beforeend", `<p class="confirm-error">${escapeHtml(error.message)}</p>`);
    } finally { nodes.confirmOrder.disabled = false; }
  });

  nodes.positions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-break-even]");
    if (!button || button.disabled) return;
    const position = state.payload?.positions?.find((item) => item.key === button.dataset.breakEven);
    if (!position) return;
    state.breakEvenKey = position.key;
    nodes.breakEvenSummary.innerHTML = `<div><span>仓位</span><b>${escapeHtml(position.symbol)} · 多</b></div><div><span>入场 / 标记</span><b>${price(position.entryPrice)} / ${price(position.markPrice)}</b></div><div><span>当前浮盈</span><b class="is-up">${money(position.unrealizedPnl)}</b></div><div><span>新止损</span><b>入场价 + 手续费缓冲</b></div>`;
    nodes.breakEvenAck.value = "";
    nodes.breakEvenDialog.showModal();
  });

  nodes.confirmBreakEven.addEventListener("click", async () => {
    nodes.confirmBreakEven.disabled = true;
    try {
      const payload = await request("POST", { action: "break_even", positionKey: state.breakEvenKey, acknowledgement: nodes.breakEvenAck.value });
      nodes.breakEvenDialog.close(); render(payload); nodes.status.textContent = "保本止损已设置";
    } catch (error) { nodes.breakEvenSummary.insertAdjacentHTML("beforeend", `<p class="confirm-error">${escapeHtml(error.message)}</p>`); }
    finally { nodes.confirmBreakEven.disabled = false; }
  });

  load();
  window.setInterval(() => load(false, true), 20_000);
})();
