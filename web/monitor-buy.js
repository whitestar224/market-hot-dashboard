(() => {
  const core = window.MonitorBuyCore;
  if (!core) return;
  const e = core.escape;
  const SESSION_MODE = "safe-zodiac-local-v1";
  const SESSION_STABLES = {
    1: [
      {symbol: "USDT", address: "0xdac17f958d2ee523a2206206994597c13d831ec7", decimals: 6},
      {symbol: "USDC", address: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", decimals: 6}
    ],
    56: [{symbol: "USDT", address: "0x55d398326f99059ff775485246999027b3197955", decimals: 18}],
    8453: [{symbol: "USDC", address: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", decimals: 6}]
  };
  let dialog, target, config, preview, busy = false, executing = false, generation = 0;
  let activeKey = "", walletIdentity = "", lastButton, sourceChains = [], executor, connectingForBuy = false;
  let executionMode = "wallet";
  function selectedWalletIdentity() {
    const wallets = window.XingyunMonitorWallets, key = wallets.activeKey(), owners = wallets.snapshot(key);
    return JSON.stringify([key, String(owners.evm || "").toLowerCase(), String(owners.solana || "")]);
  }
  let quoteAbort, progressTimer, progressState, executionOrder, autoQuoteTimer, quoteTask, pendingBuy;
  const preparedTargets = new Map();
  const recordsKey = "xingyunMonitorBuyOrdersV1";
  const orderRecords = () => { try { return JSON.parse(localStorage.getItem(recordsKey) || "[]"); } catch (_) { return []; } };
  function saveOrder(value) {
    try { localStorage.setItem(recordsKey, JSON.stringify([value, ...orderRecords().filter(x => x.orderId !== value.orderId)].slice(0, 20))); } catch (_) {}
  }
  async function api(action, data = {}, onProgress) {
    if (action === "quote" && onProgress) {
      quoteAbort = new AbortController();
      const response = await fetch("/api/monitor-buy/quote-stream", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data), signal: AbortSignal.any([quoteAbort.signal, AbortSignal.timeout(180000)]) });
      if (!response.ok) { const error = await response.json(); throw Error(error.error || "报价服务暂不可用"); }
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = "", result;
      const consume = line => {
        if (!line.trim()) return;
        const item = JSON.parse(line);
        if (item.type === "progress") onProgress(item);
        else if (item.type === "result") result = item.payload;
        else if (item.type === "error") throw Object.assign(Error(item.error), { stage: item.stage, code: item.code });
      };
      try {
        for (;;) {
          const { done, value } = await reader.read();
          buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
          const lines = buffer.split("\n"); buffer = lines.pop(); lines.forEach(consume);
          if (done) break;
        }
        consume(buffer);
        if (!result?.ok) throw Error("报价连接中断，没有提交交易，请重新检查余额");
        return result;
      } finally { void reader.cancel().catch(() => {}); reader.releaseLock(); }
    }
    const response = await fetch(`/api/monitor-buy/${action}`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data), signal: AbortSignal.timeout(action === "session-execute" ? 240000 : action === "quote" || action === "balances" ? 180000 : 35000) });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw Error(payload.error || "买入服务暂不可用");
    return payload;
  }
  const amountLabel = (value, decimals = 8) => Number(value).toLocaleString("zh-CN", { maximumFractionDigits: decimals });
  function walletConfirmationNote(key = activeKey) {
    return key === "binance"
      ? "Binance Wallet 若提示“签名已发送至移动 App”，说明当前扩展使用手机关联确认；确认位置由钱包决定，本站无法绕过。若希望在电脑确认，可在这里改用已连接且支持本机签名的 OKX、MetaMask 或 Bitget。"
      : "点击买入后本站不再弹出二次确认，交易会直接交给当前钱包；最终签名位置与确认方式由钱包自身决定。";
  }
  const sessionChain = () => config?.sessionBuy?.chains?.find(item => Number(item.chainId) === Number(target?.chainId));
  const sessionReady = () => Boolean(config?.sessionBuy?.active && sessionChain());
  const currentLimit = () => executionMode === SESSION_MODE
    ? Number(config?.sessionBuy?.maxOrderUsdt || 1000) : Number(config?.maxOrderUsd || 0);
  function modeNote() {
    if (executionMode !== SESSION_MODE) return walletConfirmationNote(activeKey);
    const status = config?.sessionBuy || {};
    return `免费免确认买入：单笔最多 ${status.maxOrderUsdt || 1000} U，本次 24 小时剩余 ${status.remainingUsdt || 0} U；成交资产只进入 Safe。`;
  }
  const notice = (text, error = false) => { const node = dialog.querySelector("[data-buy-message]"); node.textContent = text; node.classList.toggle("is-error", error); };
  function updateProgress(percent, stage, text) {
    const node = dialog?.querySelector("[data-buy-progress]");
    if (!node) return;
    const started = progressState?.started || Date.now();
    const phaseStarted = progressState?.stage === stage ? progressState.phaseStarted : Date.now();
    progressState = { percent: Math.max(progressState?.percent || 0, Math.min(100, percent)), stage, text, started, phaseStarted };
    node.hidden = !executing && stage !== 'loading'; node.classList.remove("is-paused"); node.classList.toggle("is-complete", percent === 100);
    const meter = node.querySelector("[role=progressbar]");
    meter.setAttribute("aria-valuenow", progressState.percent);
    meter.setAttribute("aria-valuetext", `${progressState.percent}% · ${text}`);
    meter.setAttribute("aria-busy", String(percent < 100));
    node.querySelector("[data-progress-value]").textContent = `${progressState.percent}%`;
    node.querySelector("[data-progress-ring]").style.strokeDashoffset = 100 - progressState.percent;
    node.querySelector("[data-progress-label]").textContent = text;
    clearInterval(progressTimer);
    const tick = () => {
      if (!progressState) return;
      const elapsed = Math.floor((Date.now() - started) / 1000);
      const phaseElapsed = Math.floor((Date.now() - phaseStarted) / 1000);
      node.querySelector("[data-progress-elapsed]").textContent = `总用时 ${elapsed} 秒 · 当前步骤 ${phaseElapsed} 秒${phaseElapsed >= 15 && percent < 100 ? " · 仍在等待响应，可关闭窗口" : ""}（非到账倒计时）`;
      if (stage === "wallet" && Date.now() - progressState.phaseStarted >= 45000) {
        node.querySelector("[data-progress-label]").textContent = "钱包尚未响应，未收到交易哈希";
        notice("钱包未返回交易结果。请查看钱包是否有确认请求或交易记录；没有请求也不代表可以重复扣款。可查询原订单，不会自动重发。", true);
      }
    };
    tick();
    if (percent < 100) progressTimer = setInterval(tick, 1000);
  }
  function stopProgress() { clearInterval(progressTimer); progressTimer = null; }
  function pauseQuoteProgress(error) {
    stopProgress();
    const node = dialog?.querySelector("[data-buy-progress]");
    if (!node) return;
    node.hidden = true; // Keep one concise error, not a duplicate error card.
    node.classList.add("is-paused");
    node.querySelector("[role=progressbar]").setAttribute("aria-valuetext", "报价已停止，未发起钱包请求");
    node.querySelector("[role=progressbar]").setAttribute("aria-busy", "false");
    node.querySelector("[data-progress-value]").textContent = "已停止";
    const reason = ["TimeoutError", "AbortError"].includes(error?.name)
      ? "报价请求超时，未发起钱包请求"
      : error?.message || "报价连接中断，未发起钱包请求";
    node.querySelector("[data-progress-label]").textContent = reason;
    node.querySelector("[data-progress-elapsed]").textContent = `停止步骤：${error?.stage || progressState?.text || "获取报价"}。本次未发起钱包请求，可重新报价或关闭，不会自动扣款。`;
    const retry = dialog.querySelector("[data-buy-quote-button]");
    if (retry) retry.textContent = "重新获取报价";
  }
  function resetProgress() { stopProgress(); progressState = null; }
  function invalidate() {
    if (executing) return;
    quoteAbort?.abort(); quoteAbort = null; resetProgress();
    if (autoQuoteTimer) clearTimeout(autoQuoteTimer);
    autoQuoteTimer = null; quoteTask = null; pendingBuy = null;
    generation += 1; preview = null;
    dialog?.querySelector("[data-buy-quote]")?.replaceChildren();
    const progress = dialog?.querySelector("[data-buy-progress]");
    if (progress) progress.hidden = true;
    if (dialog?.querySelector("[data-buy-confirm]")) setBusy(false);
  }
  function setBusy(value) {
    busy = value;
    const isSessionMode = typeof executionMode !== "undefined" && typeof SESSION_MODE !== "undefined" && executionMode === SESSION_MODE;
    const limitValue = typeof currentLimit === "function" ? currentLimit() : Number(config?.maxOrderUsd || 0);
    dialog.querySelectorAll("[data-buy-input], [data-buy-quote-button], [data-buy-connect], [data-buy-network-choice], [data-buy-retry-open], [data-buy-session-action]").forEach(node => { node.disabled = value || executing; });
    const amount = dialog.querySelector('[data-buy-amount]');
    if (amount) amount.disabled = executing;
    const primary = dialog.querySelector("[data-buy-confirm]");
    const validAmount = Number(amount?.value) > 0 && Number(amount?.value) <= limitValue;
    const walletLabel = window.XingyunMonitorWallets?.adapter?.(activeKey)?.label || "默认钱包";
    primary.disabled = executing || Boolean(pendingBuy) || !validAmount || Boolean(dialog.querySelector('[data-buy-form]')?.hidden);
    primary.textContent = isSessionMode
      ? (pendingBuy || executing ? "正在限额买入…" : "免确认直接买入")
      : pendingBuy ? `正在准备并唤起 ${walletLabel}…` : executing ? `请在 ${walletLabel} 确认` : `用 ${walletLabel} 买入`;
    dialog.querySelector("[data-buy-close]").disabled = false;
  }
  function updateModeControls() {
    if (!dialog || !config) return;
    if (executionMode === SESSION_MODE && !sessionReady()) executionMode = "wallet";
    dialog.querySelectorAll('[data-buy-mode]').forEach(node => { node.checked = node.value === executionMode; });
    const amount = dialog.querySelector('[data-buy-amount]');
    if (amount) amount.max = String(currentLimit());
    const limit = dialog.querySelector('[data-buy-amount-limit]');
    if (limit) limit.textContent = `USDT · 上限 ${currentLimit()}`;
    const walletOnly = dialog.querySelector('[data-buy-wallet-only]');
    if (walletOnly) walletOnly.hidden = executionMode === SESSION_MODE;
    const connect = dialog.querySelector('[data-buy-connect]');
    if (connect) connect.hidden = executionMode === SESSION_MODE || Boolean(window.XingyunMonitorWallets?.snapshot(activeKey)[target.chainId === core.SOLANA ? 'solana' : 'evm']);
    const note = dialog.querySelector('[data-buy-mode-note]');
    if (note) note.textContent = modeNote();
    const footer = dialog.querySelector('[data-buy-footer-note]');
    if (footer) footer.textContent = executionMode === SESSION_MODE
      ? "点击一次即由限额副账户成交 · 只限同链 · 单笔 1000 U · 本次授权累计 10000 U · 资产进入 Safe"
      : "付款顺序：同链稳定币 → 原生币 → 其他可路由币，必要时跨链 · 自动滑点最高 20% · 钱包仍须本人签名";
    setBusy(busy);
  }
  function renderSessionPanel() {
    const node = dialog?.querySelector('[data-buy-session-panel]');
    if (!node || !config) return;
    const status = config.sessionBuy || { active: false, reason: "状态不可用" };
    const chain = sessionChain();
    const supported = (config.networks || []).filter(item => (config.binanceExecutionChains || []).includes(item.id));
    const selectedNetwork = supported.find(item => item.id === target.chainId) || supported[0];
    const stableOptions = SESSION_STABLES[selectedNetwork?.id] || [];
    const defaultStable = stableOptions[0];
    const expires = status.expiresAt ? new Date(status.expiresAt * 1000).toLocaleString("zh-CN", { hour12: false }) : "--";
    const summary = status.active
      ? `已启用 · 本次剩余 ${e(status.remainingUsdt)} U · 到期 ${e(expires)}`
      : e(status.reason || "尚未启用");
    const setup = !status.keyCreated ? `<button type="button" data-buy-session-action data-session-create>创建本机限额副账户</button>
      <p>这里只生成独立副账户，不读取主钱包私钥，也不会产生链上交易。</p>` : !status.active ? `<div class="monitor-buy-session-address"><small>副账户公开地址</small><code>${e(status.sessionAddress)}</code><button type="button" data-buy-session-action data-session-copy="${e(status.sessionAddress)}">复制</button></div>
      <p>先在 Safe/Zodiac 做一次链上安装，再把地址填到下面核验。安装和撤销需要钱包确认及链上 Gas，平时买入不再逐笔确认。</p>
      <div class="monitor-buy-session-fields"><label>公链<select data-session-chain>${supported.map(item => `<option value="${item.id}" ${item.id === target.chainId ? "selected" : ""}>${e(item.name)}</option>`).join("")}</select></label>
      <label>稳定币<select data-session-symbol>${stableOptions.map((item, index) => `<option ${index ? "" : "selected"}>${item.symbol}</option>`).join("")}</select></label>
      <label>Safe 地址<input data-session-safe spellcheck="false" placeholder="0x…"></label>
      <label>Roles 地址<input data-session-roles spellcheck="false" placeholder="0x…"></label>
      <label>稳定币 CA<input readonly data-session-stable spellcheck="false" value="${e(defaultStable?.address || "")}" placeholder="固定白名单"></label>
      <label>稳定币精度<input readonly type="number" data-session-decimals value="${e(defaultStable?.decimals ?? "")}"></label></div>
      <div class="monitor-buy-session-actions"><button type="button" data-buy-session-action data-session-open>打开 Safe/Zodiac 设置</button><button type="button" data-buy-session-action data-session-copy-plan>复制权限参数</button><button type="button" data-buy-session-action data-session-verify>核验并启用 24 小时</button><button type="button" data-buy-session-action data-session-refresh>刷新状态</button></div>` : `<dl><div><dt>执行 Safe</dt><dd>${e(chain?.safeAddress || "--")}</dd></div><div><dt>固定稳定币</dt><dd>${e(chain?.stableSymbol || "--")} · ${e(chain?.stableAddress || "--")}</dd></div><div><dt>本次累计额度</dt><dd>${e(status.reservedUsdt)} / ${e(status.maxSessionUsdt)} U</dd></div><div><dt>单笔上限</dt><dd>${e(status.maxOrderUsdt)} U</dd></div></dl>
      <div class="monitor-buy-session-actions"><button type="button" data-buy-session-action data-session-refresh>重新核验状态</button><button type="button" data-buy-session-action data-session-disable>停用并撤销</button></div>`;
    node.innerHTML = `<div class="monitor-buy-session-title"><strong>免费免确认买入</strong><span class="${status.active ? "is-active" : ""}">${summary}</span></div>${setup}`;
    updateModeControls();
  }
  function syncSessionStableFields() {
    const chainId = Number(dialog?.querySelector('[data-session-chain]')?.value);
    const symbolNode = dialog?.querySelector('[data-session-symbol]');
    if (!symbolNode) return;
    const rows = SESSION_STABLES[chainId] || [];
    const previous = symbolNode.value;
    symbolNode.innerHTML = rows.map(item => `<option ${item.symbol === previous ? "selected" : ""}>${item.symbol}</option>`).join("");
    const stable = rows.find(item => item.symbol === symbolNode.value) || rows[0];
    const address = dialog.querySelector('[data-session-stable]');
    const decimals = dialog.querySelector('[data-session-decimals]');
    if (address) address.value = stable?.address || "";
    if (decimals) decimals.value = stable?.decimals ?? "";
  }
  async function refreshSession() {
    const status = await api("session-status");
    config.sessionBuy = status;
    renderSessionPanel();
    return status;
  }
  async function open(row, trigger) {
    if (executing) { if (!dialog.open) dialog.showModal(); return; }
    if (!row?.identityKey || !core.validTarget(row) || row.identityExpiresAt <= Date.now()) return;
    invalidate(); resetProgress(); busy = false;
    target = row; lastButton = trigger; generation += 1; preview = null;
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.className = "monitor-buy-dialog";
      document.body.appendChild(dialog);
      dialog.addEventListener("cancel", () => { if (!executing) invalidate(); });
      dialog.addEventListener("close", () => lastButton?.focus());
      dialog.addEventListener("input", event => {
        if (event.target.matches("[data-buy-input]")) invalidate();
        if (event.target.matches("[data-buy-amount]")) scheduleAutoQuote();
      });
      dialog.addEventListener("click", onClick);
      dialog.addEventListener("change", onChange);
    }
    dialog.innerHTML = `<header><div class="monitor-buy-heading"><div><small>ONCHAIN BUY · 同链稳定币优先</small><h2>买入 ${e(target.symbol)}</h2></div><button type="button" class="monitor-buy-header-ca" data-buy-copy-ca title="点击复制完整 CA" aria-label="复制完整合约地址"><small>${e(target.chainLabel || target.chainId)} · CA</small><span data-buy-header-ca>${e(target.address)}</span></button></div><button type="button" data-buy-close aria-label="关闭买入">×</button></header>
      <div data-buy-loading>正在读取钱包和公链能力…</div><p data-buy-message role="status"></p>
      <button type="button" data-buy-retry-open hidden>重试加载</button>
      <section class="monitor-buy-progress" data-buy-progress hidden><div class="monitor-buy-progress-meter" role="progressbar" aria-label="买入步骤进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><svg viewBox="0 0 64 64" aria-hidden="true"><circle class="progress-track" cx="32" cy="32" r="27"/><circle data-progress-ring cx="32" cy="32" r="27" pathLength="100"/><circle class="progress-spinner" cx="32" cy="32" r="31"/></svg><b data-progress-value>0%</b></div><div><strong data-progress-label></strong><small data-progress-elapsed></small></div></section>
      <div data-buy-form hidden></div><section data-buy-quote></section>
       <footer><button type="button" data-buy-confirm disabled>买入</button><small data-buy-footer-note>付款顺序：同链稳定币 → 原生币 → 其他可路由币，必要时跨链 · 自动滑点最高 20% · 钱包仍须本人签名</small></footer>`;
    // Opening is its own busy phase: passive wallet restore must not cancel it.
    setBusy(true);
    if (!dialog.open) dialog.showModal();
    updateProgress(5, "loading", "读取钱包与公链能力");
    const version = generation;
    try {
      const capabilities = config || await api("capabilities");
      if (version !== generation) return;
      config = capabilities;
      executionMode = config.sessionBuy?.active && config.sessionBuy?.chains?.some(item => Number(item.chainId) === Number(target.chainId))
        ? SESSION_MODE : "wallet";
      const wallets = window.XingyunMonitorWallets;
      if (!wallets) throw Error("钱包模块尚未初始化，请刷新页面");
      activeKey = wallets.activeKey();
      walletIdentity = selectedWalletIdentity();
      wallets.warmBalances?.(target.chainId);
      prepareTarget(target);
      sourceChains = [...new Set([...(config.defaultChains || []), target.chainId].filter(Boolean))];
      const list = wallets.list();
      const form = dialog.querySelector("[data-buy-form]");
      form.innerHTML = `<fieldset class="monitor-buy-mode"><legend>买入方式</legend><label><input type="radio" data-buy-mode value="wallet" ${executionMode === "wallet" ? "checked" : ""}> 默认钱包确认</label><label class="monitor-buy-session-choice"><input type="radio" data-buy-mode value="${SESSION_MODE}" ${executionMode === SESSION_MODE ? "checked" : ""} ${sessionReady() ? "" : "disabled"}> 免费免确认${sessionReady() ? "" : " · 需先设置"}</label><p data-buy-mode-note>${e(modeNote())}</p></fieldset>
        <section class="monitor-buy-session-panel" data-buy-session-panel></section>
        <label class="monitor-buy-amount">你想买多少？<span data-buy-amount-limit>USDT · 上限 ${e(currentLimit())}</span><input type="number" inputmode="decimal" min="0.01" max="${e(currentLimit())}" step="0.01" data-buy-input data-buy-amount placeholder="输入 USDT 金额"></label>
        <button type="button" class="monitor-buy-connect" data-buy-connect ${executionMode === SESSION_MODE || wallets.snapshot(activeKey)[target.chainId === core.SOLANA ? 'solana' : 'evm'] ? 'hidden' : ''}>连接全局钱包</button>
        <details class="monitor-buy-settings"><summary>钱包与交易设置</summary><div data-buy-wallet-only ${executionMode === SESSION_MODE ? "hidden" : ""}><label>全局执行钱包<select data-buy-input data-buy-wallet>${list.map(item => `<option value="${e(item.key)}" ${item.key === activeKey ? "selected" : ""}>${e(item.label)}${item.installed ? "" : " · 未检测到"}</option>`).join("")}</select></label><p class="monitor-buy-wallet-note" data-buy-wallet-confirm-note>${e(walletConfirmationNote(activeKey))}</p></div>
        <div class="monitor-buy-target-fields"><label>已核验公链<input readonly data-buy-target-chain-label value="${e(target.chainLabel || config.networks.find(chain => chain.id === target.chainId)?.name || target.chainId)}"></label>
        <label>已核验 CA<input readonly data-buy-target-address value="${e(target.address)}" spellcheck="false"></label></div>
        <details class="monitor-buy-networks"><summary>付款链范围（可调整）</summary><div>${config.networks.map(chain => `<label><input type="checkbox" data-buy-network-choice value="${chain.id}" ${sourceChains.includes(chain.id) ? "checked" : ""}>${e(chain.name)}</label>`).join("")}</div></details>
        </details><button type="button" data-buy-quote-button>获取报价</button>
        <details class="monitor-buy-orders"><summary>本机最近买入记录</summary><div>${orderRecords().map(item => `<p><b>${e(item.symbol)}</b> · ${e(item.status || "已发起")} <button type="button" data-buy-track="${e(item.orderId)}">查询状态</button></p>`).join("") || "暂无记录"}</div></details>`;
      form.hidden = false;
      renderSessionPanel();
      dialog.querySelector("[data-buy-loading]").hidden = true;
      notice("");
      dialog.querySelector("[data-buy-amount]").focus();
      updateProgress(100, "ready", "买入面板已就绪");
      dialog.querySelector('[data-buy-progress]').hidden = true;
    } catch (error) { if (version === generation) {
      resetProgress();
      dialog.querySelector("[data-buy-progress]").hidden = true;
      dialog.querySelector("[data-buy-loading]").hidden = true;
      dialog.querySelector("[data-buy-retry-open]").hidden = false;
      notice(error.message || "买入面板加载失败，请重试。", true);
    } } finally { if (version === generation) setBusy(false); }
  }
  async function connect() {
    const targetChain = target.chainId;
    const wallet = window.XingyunMonitorWallets;
    await wallet.connect(activeKey, targetChain === core.SOLANA, sourceChains.includes(core.SOLANA));
    walletIdentity = selectedWalletIdentity();
    wallet.warmBalances?.(targetChain);
    return wallet.snapshot(activeKey);
  }
  function scheduleAutoQuote() {
    if (autoQuoteTimer) clearTimeout(autoQuoteTimer);
    const version = generation, value = Number(dialog.querySelector('[data-buy-amount]').value);
    const limitValue = typeof currentLimit === "function" ? currentLimit() : Number(config?.maxOrderUsd || 0);
    if (!(value > 0 && value <= limitValue) || executing) return;
    autoQuoteTimer = setTimeout(() => {
      autoQuoteTimer = null;
      if (version !== generation || !dialog.open || executing) return;
      void requestQuote();
    }, 450);
  }
  function prepareTarget(row) {
    if (!row?.identityKey || !core.validTarget(row) || row.identityExpiresAt <= Date.now() || typeof window.fetch !== 'function') return;
    const key = `${row.chainId}:${row.address}`, previous = preparedTargets.get(key) || 0;
    if (Date.now() - previous < 15000) return;
    preparedTargets.set(key, Date.now());
    if (preparedTargets.size > 64) preparedTargets.delete(preparedTargets.keys().next().value);
    window.XingyunMonitorWallets?.warmBalances?.(row.chainId);
    void api('prepare', {target: row}).catch(() => preparedTargets.delete(key));
  }
  async function requestBuy() {
    if (executing || pendingBuy) return;
    const isSessionMode = typeof executionMode !== "undefined" && typeof SESSION_MODE !== "undefined" && executionMode === SESSION_MODE;
    const request = {version: generation};
    if (autoQuoteTimer) clearTimeout(autoQuoteTimer);
    autoQuoteTimer = null; pendingBuy = request; setBusy(busy);
    try {
      const shared = window.XingyunMonitorWallets;
      const namespace = target.chainId === core.SOLANA ? 'solana' : 'evm';
      if (!isSessionMode && !shared.snapshot(activeKey)[namespace]) {
        const walletLabel = shared.adapter?.(activeKey)?.label || '默认钱包';
        notice(`正在连接 ${walletLabel}；连接后会继续准备路线并直接请求交易确认。`);
        connectingForBuy = true;
        try { await connect(); } finally { connectingForBuy = false; }
        if (pendingBuy !== request || request.version !== generation || !dialog.open) return;
        walletIdentity = selectedWalletIdentity();
        const connectButton = dialog.querySelector('[data-buy-connect]');
        if (connectButton) connectButton.hidden = true;
        // A passive quote may have started just before this explicit connect.
        // Let it settle, then retry below if it did not produce a bound quote.
        if (quoteTask) await quoteTask;
      }
      const result = preview && preview.expiresAt - Date.now() > 15000 ? preview : await requestQuote();
      if (pendingBuy !== request || request.version !== generation || !dialog.open || !result) return;
      // Only an explicit Buy click reaches this point; background preparation cannot.
      await confirm();
    } catch (error) {
      if (pendingBuy === request && request.version === generation) {
        const cancelled = Number(error.code) === 4001 || /reject|denied|取消/i.test(error.message || "");
        notice(cancelled ? "你取消了默认钱包连接，未获取报价，也未提交交易。" : `${error.message || "默认钱包连接失败"}；未提交交易。`, true);
      }
    } finally {
      if (pendingBuy === request) { pendingBuy = null; setBusy(busy); }
    }
  }
  function onChange(event) {
    if (event.target.matches('[data-buy-amount]')) return; // input already invalidates and schedules once.
    if (event.target.matches('[data-buy-risk-ack]')) {
      const panel = dialog.querySelector('[data-buy-risk]');
      panel?.classList.remove('is-required');
      event.target.setAttribute('aria-invalid', 'false');
      notice(event.target.checked ? '风险已确认，可以继续本次买入。' : '买入前需要确认尚未完成的合约安全检测风险。', !event.target.checked);
      return;
    }
    if (event.target.matches('[data-buy-mode]')) {
      executionMode = event.target.value;
      invalidate(); updateModeControls(); scheduleAutoQuote(); return;
    }
    if (event.target.matches('[data-session-chain], [data-session-symbol]')) {
      syncSessionStableFields(); return;
    }
    if (event.target.matches("[data-buy-wallet]")) {
      activeKey = event.target.value; window.XingyunMonitorWallets.setActive(activeKey);
      const note = dialog.querySelector("[data-buy-wallet-confirm-note]");
      if (note) note.textContent = walletConfirmationNote(activeKey);
    }
    if (event.target.matches("[data-buy-network-choice]")) sourceChains = [...dialog.querySelectorAll("[data-buy-network-choice]:checked")].map(node => Number(node.value));
    if (event.target.matches("[data-buy-input], [data-buy-network-choice]")) invalidate();
  }
  function requireSecurityAcknowledgement() {
    const input = dialog.querySelector('[data-buy-risk-ack]');
    const panel = dialog.querySelector('[data-buy-risk]');
    panel?.classList.add('is-required');
    input?.setAttribute('aria-invalid', 'true');
    panel?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    input?.focus?.({ preventScroll: true });
    notice('请勾选黄色的“合约安全风险确认”后再买入；没有勾选不会发起钱包交易。', true);
  }
  async function onClick(event) {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.hasAttribute("data-buy-close")) { if (!executing) invalidate(); dialog.close(); return; }
    if (button.hasAttribute('data-buy-copy-ca')) {
      try { await navigator.clipboard.writeText(target.address); button.title = 'CA 已复制'; }
      catch (_) { notice('复制失败，可直接选中标题旁的完整 CA 复制。', true); }
      return;
    }
    if (button.dataset.buyTrack) { await track(button.dataset.buyTrack); return; }
    if (button.hasAttribute('data-buy-confirm')) { await requestBuy(); return; }
    if (button.hasAttribute('data-session-copy')) {
      try { await navigator.clipboard.writeText(button.dataset.sessionCopy); notice('副账户公开地址已复制。'); }
      catch (_) { notice('复制失败，请手动复制公开地址。', true); }
      return;
    }
    if (busy || executing) return;
    if (button.hasAttribute('data-session-create')) {
      setBusy(true); try { await api('session-create'); await refreshSession(); notice('限额副账户已在本机创建；私钥不会发送到网页。下一步完成一次 Safe/Zodiac 安装。'); }
      catch (error) { notice(error.message, true); } finally { setBusy(false); } return;
    }
    if (button.hasAttribute('data-session-open')) {
      window.open('https://roles.gnosisguild.org', '_blank', 'noopener,noreferrer'); return;
    }
    if (button.hasAttribute('data-session-copy-plan')) {
      setBusy(true); try {
        const plan = await api('session-plan');
        const setup = {sessionAddress: config.sessionBuy.sessionAddress, chainId: Number(dialog.querySelector('[data-session-chain]').value),
          safeAddress: dialog.querySelector('[data-session-safe]').value.trim(), rolesAddress: dialog.querySelector('[data-session-roles]').value.trim(),
          stableAddress: dialog.querySelector('[data-session-stable]').value.trim(), stableSymbol: dialog.querySelector('[data-session-symbol]').value,
          stableDecimals: Number(dialog.querySelector('[data-session-decimals]').value), roleKey: plan.roleKey,
          allowanceKey: plan.allowanceKey, routerAddress: plan.routerAddress, maxOrderUsdt: plan.maxOrderUsdt,
          maxSessionUsdt: plan.maxSessionUsdt, durationHours: 24, refill: false, permissions: plan.permissions};
        await navigator.clipboard.writeText(JSON.stringify(setup, null, 2)); notice('Safe/Zodiac 权限参数已复制。');
      } catch (error) { notice(error.message || '复制设置参数失败', true); } finally { setBusy(false); } return;
    }
    if (button.hasAttribute('data-session-verify')) {
      const payload = {chainId: Number(dialog.querySelector('[data-session-chain]').value),
        safeAddress: dialog.querySelector('[data-session-safe]').value.trim(), rolesAddress: dialog.querySelector('[data-session-roles]').value.trim(),
        stableAddress: dialog.querySelector('[data-session-stable]').value.trim(), stableSymbol: dialog.querySelector('[data-session-symbol]').value,
        stableDecimals: Number(dialog.querySelector('[data-session-decimals]').value)};
      setBusy(true); try { config.sessionBuy = await api('session-configure', payload); executionMode = SESSION_MODE; renderSessionPanel(); notice('链上权限已逐项核验，未来 24 小时可免确认买入。'); }
      catch (error) { notice(`${error.message || '链上权限核验失败'}；免确认模式没有启用。`, true); } finally { setBusy(false); } return;
    }
    if (button.hasAttribute('data-session-refresh')) {
      setBusy(true); try { await refreshSession(); notice(config.sessionBuy.reason || '状态已刷新'); }
      catch (error) { notice(error.message, true); } finally { setBusy(false); } return;
    }
    if (button.hasAttribute('data-session-disable')) {
      setBusy(true); try { config.sessionBuy = await api('session-disable'); executionMode = 'wallet'; renderSessionPanel(); notice('免确认买入已立即停用，后台正在尝试撤销链上角色。'); }
      catch (error) { notice(`${error.message || '停用失败'}；请在 Safe 中手动撤销角色。`, true); } finally { setBusy(false); } return;
    }
    if (button.hasAttribute("data-buy-retry-open")) { await open(target, lastButton); return; }
    if (button.hasAttribute("data-buy-connect")) {
      setBusy(true); try { await connect(); button.hidden = true; notice('全局钱包已连接'); scheduleAutoQuote(); } catch (error) { notice(error.message, true); } finally { setBusy(false); } return;
    }
    if (button.hasAttribute("data-buy-quote-button")) {
      invalidate(); await requestQuote(); return;
    }
  }
  function requestQuote() {
    if (quoteTask) return quoteTask; // Typing and Buy share one exact-amount request.
    resetProgress(); preview = null; setBusy(true); updateProgress(5, "wallet-read", "读取已授权的钱包账户");
    dialog.querySelector('[data-buy-quote]')?.replaceChildren();
    const task = (async () => {
      const version = generation;
      try {
        const sessionModeValue = typeof SESSION_MODE === "undefined" ? "safe-zodiac-local-v1" : SESSION_MODE;
        const isSessionMode = typeof executionMode !== "undefined" && executionMode === sessionModeValue;
        const limitValue = typeof currentLimit === "function" ? currentLimit() : Number(config?.maxOrderUsd || 0);
        if (!target.identityKey || !core.validTarget(target) || target.identityExpiresAt <= Date.now()) throw Error("监控身份核验已过期，请返回监控页等待后台更新");
        const amountUsdt = dialog.querySelector("[data-buy-amount]").value;
        if (!(Number(amountUsdt) > 0 && Number(amountUsdt) <= limitValue)) throw Error("请输入有效的 USDT 数量");
        const shared = window.XingyunMonitorWallets;
        const session = isSessionMode && typeof sessionChain === "function" ? sessionChain() : null;
        const wallets = isSessionMode ? {evm: session?.safeAddress} : await shared.currentAddresses(activeKey);
        if (isSessionMode && !(typeof sessionReady === "function" && sessionReady())) throw Error('当前目标链尚未启用免费免确认买入');
        if (!wallets[target.chainId === core.SOLANA ? 'solana' : 'evm']) throw Error('请先连接右上角的全局钱包');
        if (version !== generation) return;
        const requestIdentity = isSessionMode
          ? JSON.stringify([sessionModeValue, config.sessionBuy.sessionAddress, config.sessionBuy.expiresAt]) : selectedWalletIdentity();
        if (!isSessionMode && window.XingyunMonitorWallets.activeKey() !== activeKey) throw Error("当前钱包已变化，请重新获取报价");
        notice(pendingBuy ? "正在准备本次买入…" : "正在后台准备报价，可以直接点击买入。");
        const result = await api("quote", { target, amountUsdt, wallets,
          walletProvider: isSessionMode ? sessionModeValue : activeKey, executionMode: isSessionMode ? sessionModeValue : "wallet",
          slippageBps: 2000, autoSlippage: true,
          sourceChains: isSessionMode ? [target.chainId] : sourceChains }, item => { if (version === generation) { updateProgress(item.percent, item.stage, item.stage); notice(pendingBuy ? `正在准备买入 · ${item.stage}` : '正在后台准备报价，可以直接点击买入。'); } });
        if (version !== generation) return;
        const latestIdentity = isSessionMode
          ? JSON.stringify([sessionModeValue, config.sessionBuy.sessionAddress, config.sessionBuy.expiresAt]) : selectedWalletIdentity();
        if (requestIdentity !== latestIdentity) throw Error("执行账户已变化，请重新获取报价");
        preview = result;
        renderQuote();
        updateProgress(100, "quoted", "余额与路线检查完成，等待你确认");
        const fallback = preview.fundingFallback?.used
          ? ` · ${preview.fundingFallback.reason || "首选付款方式不可用"}，已改用 ${preview.fundingFallback.toChain} ${preview.fundingFallback.toSymbol}${preview.fundingFallback.crossChain ? " 跨链" : ""}` : "";
        notice(`报价已就绪${fallback} · ${preview.executionMode === sessionModeValue ? "免费免确认" : preview.provider === "binance-web3" ? "币安 Web3" : "Relay 跨链"} · 剩余 ${Math.max(0, Math.floor((preview.expiresAt-Date.now())/1000))} 秒。`);
        return result;
      } catch (error) { if (version === generation) {
        preview = null; pauseQuoteProgress(error);
        const reason = ["TimeoutError", "AbortError"].includes(error?.name) ? "报价请求超时" : error?.message || "报价连接中断";
        notice(`${reason}；未发起钱包请求。`, true);
      } }
      finally { if (version === generation) setBusy(false); }
    })();
    quoteTask = task;
    void task.finally(() => { if (quoteTask === task) quoteTask = null; }).catch(() => {});
    return task;
  }
  function renderQuote() {
    const q = preview, incoming = q.details.currencyIn, outgoing = q.details.currencyOut;
    const gas = q.fees.gas || {}, relayer = q.fees.relayer || {};
    const execution = q.executionSummary, approval = execution?.approval;
    const executionTitle = q.executionMode === SESSION_MODE ? "Safe 限额权限与买入去向 · 已逐项核对" : "授权与买入去向 · 已逐项核对";
    const executionReview = q.provider === "binance-web3" && execution ? `<section class="monitor-buy-execution-review">
      <strong>${e(executionTitle)}</strong>
      <dl><div><dt>买入目标</dt><dd>${e(q.target.symbol)} · ${e(execution.targetAddress)}</dd></div>
      <div><dt>执行合约</dt><dd>币安 Web3 路由 · ${e(execution.swapContract)}</dd></div>
      ${approval?.required ? `<div><dt>授权资产</dt><dd>${e(approval.tokenSymbol)} · ${e(approval.tokenAddress)}</dd></div>
      <div><dt>授权给</dt><dd>${e(approval.spender)}</dd></div>
      <div><dt>授权额度</dt><dd>${e(approval.amountFormatted)} ${e(approval.tokenSymbol)}（仅本次${approval.resetRequired ? "，先清零旧额度" : ""}，不是无限授权）</dd></div>` : `<div><dt>代币授权</dt><dd>现有额度足够，本次不新增授权</dd></div>`}
      <div><dt>最终收币</dt><dd>${e(execution.recipient)}</dd></div>
      <div><dt>流动性路径</dt><dd>${execution.dexes?.length ? execution.dexes.map(e).join(" + ") : "聚合器实时选择"}</dd></div></dl>
      <p>授权合约与目标代币 CA 不同是正常的：前者只能按本次额度调用付款币，后者才是最终买到并转入${q.executionMode === SESSION_MODE ? " Safe" : "你钱包"}的代币。</p>
      <p class="monitor-buy-wallet-note">${e(q.executionMode === SESSION_MODE ? modeNote() : walletConfirmationNote(activeKey))}</p></section>` : "";
    const fallbackNotice = q.fundingFallback?.used
      ? `<p class="monitor-buy-payment-fallback">${e(q.fundingFallback.reason || "首选付款方式不可用")}，已改用 <strong>${e(q.fundingFallback.toChain)} ${e(q.fundingFallback.toSymbol)}</strong>${q.fundingFallback.crossChain ? " 跨链" : ""}支付。钱包确认前仍会再次进行链上试运行。</p>` : "";
    dialog.querySelector("[data-buy-quote]").innerHTML = `<div class="monitor-buy-result"><small>${q.executionMode === SESSION_MODE ? "免费免确认 · Safe 同链直买" : q.provider === "binance-web3" ? "币安 Web3 · 同链直买" : "Relay · 跨链 + 买入"}</small>
      ${fallbackNotice}
      <h3>预计收到 ${e(outgoing.amountFormatted)} ${e(q.target.symbol)}</h3>
      <dl><div><dt>支付 ${e(q.conversion.amountUsdt)} USDT 等值</dt><dd>${e(incoming.amountFormatted)} ${e(q.source.symbol)}</dd></div>
      <div><dt>至少到账</dt><dd>${e(amountLabel(Number(outgoing.minimumAmount) / 10 ** outgoing.currency.decimals))} ${e(q.target.symbol)}</dd></div>
      <div><dt>本次滑点 / 上限</dt><dd>${e(Number(q.details.slippageTolerance?.total || 0)/100)}% / ${e(q.slippageBps/100)}%</dd></div>
      <div><dt>源链燃料费估计（另留）</dt><dd>$${e(gas.amountUsd || "待确认")}</dd></div>
      ${gas.maxAmountUsd ? `<div><dt>本次网络费用预算上限</dt><dd>$${e(gas.maxAmountUsd)}（按实时 Gas 计费）</dd></div>` : ''}
      </dl>${executionReview}
      ${!q.security.verified ? `<label class="monitor-buy-risk" data-buy-risk><input type="checkbox" data-buy-risk-ack aria-invalid="false"><span><strong>买入前必须确认合约安全风险</strong>安全检测状态：${e(q.security.label || "尚未完成")}。我理解该代币可能无法卖出或存在恶意权限，仍自行决定继续。</span></label>` : `<p>安全检测：${e(q.security.label || "已检测，仍不代表无风险")}</p>`}
      <details><summary>交易明细</summary><p>${e(q.source.chainLabel)} → ${e(q.target.chainLabel)}</p>
      <dl><div><dt>跨链／路由费（报价已含）</dt><dd>$${e(relayer.amountUsd || "0")}</dd></div>
      <div><dt>价格冲击 / 总损耗</dt><dd>${e(q.details.swapImpact?.percent ?? "--")}% / ${e(q.details.totalImpact?.percent ?? "--")}%</dd></div>
      <div><dt>预计用时</dt><dd>${e(q.details.timeEstimate ?? "--")} 秒（非保证）</dd></div></dl>
      <p class="monitor-buy-contract">${e(q.target.chainLabel)} · ${e(q.target.address)}</p><p class="monitor-buy-contract">收款地址：${e(q.recipient)}</p>
      <p>${e((q.routeReview || q.aiReview || {}).reason || '')}</p>
      ${q.coverage.errors.length ? `<p>${q.coverage.errors.map(e).join("<br>")}</p>` : ''}</details>
      </div>`;
  }
  async function confirm() {
    if (!preview || Date.now() >= preview.expiresAt) { invalidate(); notice("报价已过期，请重新报价", true); return; }
    const accepted = Boolean(dialog.querySelector("[data-buy-risk-ack]")?.checked);
    if (!preview.security.verified && !accepted) { requireSecurityAcknowledgement(); return; }
    if (preview.executionMode === SESSION_MODE) {
      executing = true; resetProgress(); setBusy(true); updateProgress(5, "session-authorize", "核对 Safe、限额和链上角色");
      const current = preview;
      executionOrder = current.orderId;
      const checkPending = document.createElement("button"); checkPending.type = "button"; checkPending.dataset.buyTrack = current.orderId; checkPending.textContent = "查询本次免确认买入状态（不会重新买入）";
      dialog.querySelector("[data-buy-quote]").appendChild(checkPending);
      try {
        saveOrder({ orderId: current.orderId, symbol: current.target.symbol, status: "限额副账户执行中", hashes: [] });
        notice("限额已经锁定，正在模拟并提交；不会唤起手机钱包，也不会自动重发。");
        updateProgress(25, "session-preflight", "模拟 Safe 限额交易");
        const result = await api("session-execute", {orderId: current.orderId, quoteHash: current.quoteHash});
        config.sessionBuy = result.sessionBuy || config.sessionBuy;
        const record = orderRecords().find(item => item.orderId === current.orderId) || {};
        saveOrder({...record, orderId: current.orderId, symbol: current.target.symbol, status: "Safe 已到账", hashes: result.txHashes || []});
        updateProgress(100, "success", "已核对 Safe 的目标币到账");
        notice(`免确认买入完成 · 本次授权剩余 ${config.sessionBuy?.remainingUsdt ?? "--"} U。`);
      } catch (error) {
        notice(`${error.message || "免确认执行状态不确定"}；不会自动重发，请查询原订单。`, true);
      } finally {
        executing = false; executionOrder = null; stopProgress(); invalidate(); renderSessionPanel(); setBusy(false);
        const area = dialog.querySelector("[data-buy-quote]");
        const check = document.createElement("button"); check.type = "button"; check.dataset.buyTrack = current.orderId; check.textContent = "查询本次免确认买入状态"; area.appendChild(check);
      }
      return;
    }
    executing = true; resetProgress(); setBusy(true); updateProgress(5, "authorize", "核对报价与执行账户");
    const current = preview;
    const executionKey = activeKey;
    let walletTransactionRequested = false;
    executionOrder = current.orderId;
    const checkPending = document.createElement("button"); checkPending.type = "button"; checkPending.dataset.buyTrack = current.orderId; checkPending.textContent = "查询本次买入状态（不会重新买入）";
    dialog.querySelector("[data-buy-quote]").appendChild(checkPending);
    try {
      const wallets = window.XingyunMonitorWallets;
      const owners = await wallets.currentAddresses(executionKey);
      const execution = await api("authorize", { orderId: current.orderId, quoteHash: current.quoteHash,
        walletProvider: executionKey, wallets: owners, acceptUnknownSecurity: accepted });
      core.validateExecution(execution, current);
      saveOrder({ orderId: current.orderId, symbol: current.target.symbol, status: "等待钱包签名", hashes: [] });
      notice("请在钱包核对并确认。签名或提交后，请勿重复点击买入。");
      executor ||= import("./assets/monitor-buy-executor.js?v=8");
      const { executeBuy } = await executor;
      await executeBuy({ execution, preview: current, networks: config.networks, api,
        wallet: { provider: namespace => wallets.provider(namespace, executionKey), currentAddresses: () => wallets.currentAddresses(executionKey) },
        onStage: (percent, stage, text) => { if (stage === 'wallet') walletTransactionRequested = true; updateProgress(percent, stage, text); notice(text); },
        onHash: hash => { const record = orderRecords().find(x => x.orderId === current.orderId) || {};
          saveOrder({ ...record, orderId: current.orderId, symbol: current.target.symbol, status: "已提交 · 等待到账", hashes: [...new Set([...(record.hashes || []), hash])] }); },
        onProgress: progress => { if (progress.error) notice("执行状态待核对，请查询原订单，不要重复买入", true); }
      });
      await track(current.orderId);
    } catch (error) {
      const cancelled = Number(error.code) === 4001 || /reject|denied|取消/i.test(error.message || "");
      notice(cancelled ? "你取消了钱包确认。若前面已有步骤提交，请查询原订单状态。" :
        `${error.message || "执行状态不确定"}；${walletTransactionRequested ? "不自动重试扣款，请查询原订单。" : "未发起钱包交易请求，可以重新报价。"}`, true);
    } finally {
      executing = false; executionOrder = null; stopProgress(); invalidate(); setBusy(false);
      const area = dialog.querySelector("[data-buy-quote]");
      const check = document.createElement("button"); check.type = "button"; check.dataset.buyTrack = current.orderId; check.textContent = "查询本次买入 / 退款状态"; area.appendChild(check);
    }
  }
  async function track(orderId) {
    try {
      notice("正在查询原订单，不会重新买入…");
      const result = await api("status", { orderId });
      result.relayStatus = result.executionStatus || result.relayStatus;
      const labels = { success: "目标链买入已完成", refund: "已进入退款状态，请核对退款交易与余额", failure: "执行失败，请核对退款或待处理原因",
        waiting: "等待检测到付款", depositing: "源链付款确认中", pending: "跨链／目标链处理中", submitted: "已提交，等待到账",
        "approval-only": "仅检测到授权交易，尚未确认买入；请核对原订单",
        "settlement-unverified": "交易已确认，但目标币实际到账尚未核实，请查看交易记录" };
      const hasHash = [...(result.txHashes || []), ...(result.inTxHashes || []), ...(result.outTxHashes || [])].length > 0;
      const status = !hasHash && result.state === "signing" && [undefined, "waiting"].includes(result.relayStatus)
        ? "尚未收到交易哈希，无法确认已上链。请核对钱包是否有请求或交易；不要重复买入"
        : labels[result.relayStatus] || "尚未确认最终结果，请稍后继续查询";
      if (result.relayStatus === "success" && hasHash && (!executionOrder || executionOrder === orderId)) updateProgress(100, "success", "已核对目标链到账交易");
      const record = orderRecords().find(x => x.orderId === orderId);
      if (record) saveOrder({ ...record, status });
      notice(`${status}${result.failReason ? ` · ${result.failReason}` : ""}${result.refundFailReason ? ` · 退款待处理：${result.refundFailReason}` : ""}`);
      let hashes = dialog.querySelector("[data-buy-hashes]");
      if (!hashes) { hashes = document.createElement("p"); hashes.dataset.buyHashes = ""; hashes.className = "monitor-buy-contract"; dialog.querySelector("[data-buy-message]").after(hashes); }
      hashes.textContent = [...new Set([...(result.txHashes || []), ...(result.inTxHashes || []), ...(result.outTxHashes || [])])].map(hash => `交易哈希：${hash}`).join("\n");
    } catch (error) { notice(`状态查询暂不可用：${error.message}。不要重复提交。`, true); }
  }
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-monitor-buy]");
    if (!button || button.disabled) return;
    event.preventDefault(); event.stopPropagation();
    try { open(JSON.parse(decodeURIComponent(button.dataset.monitorBuy)), button); } catch (_) {}
  }, true);
  for (const type of ['pointerover', 'focusin']) document.addEventListener(type, event => {
    const button = event.target.closest?.('[data-monitor-buy]');
    if (!button || button.disabled) return;
    try { prepareTarget(JSON.parse(decodeURIComponent(button.dataset.monitorBuy))); } catch (_) {}
  }, true);
  window.addEventListener("xingyun:wallet-change", () => {
    if (!dialog?.open || executing) return;
    if (typeof executionMode !== "undefined" && typeof SESSION_MODE !== "undefined" && executionMode === SESSION_MODE) return; // Safe session quotes are not bound to the browser wallet.
    // There is no quote/account binding to invalidate before a form has loaded.
    // Also preserve the real initialization error while its retry is visible.
    if (dialog.querySelector("[data-buy-form]")?.hidden) return;
    const wallets = window.XingyunMonitorWallets;
    const identity = selectedWalletIdentity();
    if (connectingForBuy) {
      walletIdentity = identity;
      activeKey = wallets.activeKey();
      const select = dialog.querySelector("[data-buy-wallet]");
      if (select) select.value = activeKey;
      return;
    }
    if (identity === walletIdentity) return; // Passive focus refresh must preserve errors and valid quotes.
    walletIdentity = identity;
    activeKey = wallets.activeKey();
    const select = dialog.querySelector("[data-buy-wallet]");
    if (select) select.value = activeKey;
    invalidate();
    notice("钱包账户已变化，请重新获取报价。");
  });
  window.MonitorBuy = { open, button: core.button };
})();
