(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MonitorBuyCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const SOLANA = 792703809;
  const aliases = { eth: 1, ethereum: 1, bsc: 56, bnb: 56, base: 8453, sol: SOLANA, solana: SOLANA,
    "501": SOLANA, robinhood: 4663, "robinhood-chain": 4663, arbitrum: 42161, optimism: 10, polygon: 137, avalanche: 43114 };
  const escape = (v) => String(v ?? "").replace(/[&<>"']/g, x => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[x]));
  function chainId(value) {
    const key = String(value || "").trim().toLowerCase().replace(/^ct_/, "");
    const id = aliases[key] || Number(key);
    return id === 501 ? SOLANA : Number.isSafeInteger(id) && id > 0 ? id : null;
  }
  function identity(row = {}, extra = {}) {
    let chain = extra.chainId || extra.chain || row.onchainChain || row.onchain_chain || row.network || row.chain || row.chainId;
    let contract = row.contractAddress || row.onchainContractAddress || row.onchain_contract_address || row.tokenAddress || row.address || "";
    for (const link of [row.tradeUrl, row.url, row.officialUrl, ...(Object.values(row.tradeUrls || {}))]) {
      if (chain && contract) break;
      try {
        const url = new URL(link);
        if (url.hostname === "web3.binance.com") {
          const match = url.pathname.match(/\/token\/([^/]+)\/([^/]+)/);
          if (match) { chain ||= match[1]; contract ||= match[2]; }
        } else if (url.hostname === "web3.okx.com") {
          const match = url.pathname.match(/\/token\/([^/]+)\/([^/]+)/);
          if (match) { chain ||= match[1]; contract ||= match[2]; }
        }
      } catch (_) { /* A ticker is never a contract address. */ }
    }
    const id = chainId(chain);
    if (id && id !== SOLANA) contract = String(contract).toLowerCase();
    return { symbol: String(row.symbol || row.name || "标的").slice(0, 80), chainId: id,
      address: String(contract).slice(0, 180), kind: extra.kind || row.kind || "token" };
  }
  function validTarget(target) {
    return target.kind !== "nft" && target.chainId && (target.chainId === SOLANA
      ? /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(target.address)
      : /^0x[0-9a-fA-F]{40}$/.test(target.address));
  }
  function button(row, extra = {}) {
    const target = identity(row, extra);
    const checked = row?.buyIdentity;
    const bound = checked?.target;
    const ready = checked?.status === "verified" && checked.expiresAt > Date.now() && validTarget(bound || {})
      && target.chainId === bound.chainId && target.address === bound.address && target.kind !== "nft";
    if (!ready) return `<button type="button" class="monitor-buy-button" disabled aria-label="${escape(target.symbol)} CA 未就绪" title="${escape(checked?.reason || "后台核验链与 CA，完成后开放买入")}">${["unresolved", "conflict"].includes(checked?.status) ? "CA 待核实" : "CA 核验中"}</button>`;
    return `<button type="button" class="monitor-buy-button" data-monitor-buy="${escape(encodeURIComponent(JSON.stringify({ ...bound, kind: "token", identityExpiresAt: checked.expiresAt })))}" aria-label="买入 ${escape(target.symbol)}" title="监控时已核验链与 CA，输入金额即可报价">买入</button>`;
  }
  function stableJson(value) {
    if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
    if (value !== null && typeof value === "object") return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${stableJson(value[k])}`).join(",")}}`;
    return JSON.stringify(value);
  }
  function validateExecution(result, preview) {
    const intent = result.intent, quote = result.quote, details = quote?.details;
    if (!intent || !details || !preview || Date.now() >= result.expiresAt) throw Error("报价已过期，请重新报价");
    const equals = (a, b, chain) => chain === SOLANA ? a === b : String(a).toLowerCase() === String(b).toLowerCase();
    const incoming = details.currencyIn, outgoing = details.currencyOut;
    if (intent.target.chainId !== preview.target.chainId || !equals(intent.target.address, preview.target.address, intent.target.chainId)
      || !equals(details.recipient, preview.recipient, intent.target.chainId)
      || !equals(details.sender, preview.source.owner, intent.source.chainId)
      || incoming.currency.chainId !== preview.source.chainId
      || !equals(incoming.currency.address, preview.source.address, intent.source.chainId)
      || outgoing.currency.chainId !== preview.target.chainId
      || !equals(outgoing.currency.address, preview.target.address, intent.target.chainId)
      || incoming.amount !== preview.details.currencyIn.amount
      || outgoing.minimumAmount !== preview.details.currencyOut.minimumAmount) throw Error("交易与所确认报价不一致");
    const limit = Number(preview.slippageBps ?? 500), actual = Number(details.slippageTolerance?.total ?? 10001);
    if (!Number.isInteger(limit) || limit < 0 || limit > 2000 || Number(intent.slippageBps ?? 500) !== limit
      || !Number.isFinite(actual) || actual < 0 || actual > limit
      || BigInt(outgoing.minimumAmount) < BigInt(outgoing.amount) * BigInt(10000-limit) / 10000n) throw Error("实际滑点超过本次已确认上限");
    if (quote.steps.some(step => step.kind !== "transaction")) throw Error("禁止额外离线签名授权");
    const provider = quote.provider || "relay";
    if (!["relay", "binance-web3"].includes(provider) || provider !== (preview.provider || "relay")) throw Error("交易服务与报价不一致");
    if (provider === "binance-web3") {
      const router = "0xb44446b0c8e56988c34f7ff73ae904982b5fdda5", zero = "0x" + "0".repeat(40);
      const raw = quote.raw, tx = raw?.tx;
      if (![1,56,8453].includes(intent.source.chainId) || intent.source.chainId !== intent.target.chainId
        || raw?.executionMode !== "SWAP" || raw.rfq || tx?.signatureData
        || !equals(tx.from, intent.sender) || !equals(tx.to, router)
        || !equals(intent.sender, intent.recipient) || !tx.data?.startsWith("0xad43f73d")
        || Number(tx.slippagePercent) < 0 || Number(tx.slippagePercent) > limit/100
        || !Number.isFinite(Number(tx.slippagePercent))
        || tx.minReceiveAmount !== outgoing.minimumAmount) throw Error("币安交易校验未通过");
      const swap = { from: String(tx.from).toLowerCase(), to: router, data: tx.data.toLowerCase(), value: tx.value, chainId: intent.source.chainId };
      if (quote.steps.length < 1 || quote.steps.length > 3 || quote.steps.at(-1).id !== "swap"
        || stableJson(quote.steps.at(-1).items) !== stableJson([{data: swap}])) throw Error("币安买入步骤不匹配");
      for (const [index, step] of quote.steps.slice(0,-1).entries()) {
        const amount = quote.steps.length === 3 && index === 0 ? 0n : BigInt(intent.amount);
        const data = {from: intent.sender, to: intent.source.address, chainId: intent.source.chainId, value:"0",
          data:"0x095ea7b3"+router.slice(2).padStart(64,"0")+amount.toString(16).padStart(64,"0")};
        if (intent.source.address === zero || step.id !== "approval" || stableJson(step.items) !== stableJson([{data}])) throw Error("只允许本次金额的精确授权");
      }
      const summary = quote.execution, shown = preview.executionSummary;
      if (!summary || !shown || stableJson(summary) !== stableJson(shown)
        || !equals(summary.swapContract, router) || !equals(summary.targetAddress, intent.target.address)
        || !equals(summary.recipient, intent.recipient)
        || summary.receiverMode !== "connected-wallet" || summary.verification !== "pinned-router-exact-calldata"
        || summary.approval?.required !== (quote.steps.length > 1)
        || summary.approval?.resetRequired !== (quote.steps.length === 3)
        || !equals(summary.approval?.tokenAddress, intent.source.address)
        || !equals(summary.approval?.spender, router) || summary.approval?.amount !== intent.amount
        || summary.approval?.exactAmount !== true) throw Error("授权对象、买入目标或收款地址与已展示内容不一致");
    }
    return true;
  }
  return { SOLANA, escape, chainId, identity, validTarget, button, stableJson, validateExecution };
});
