// Binance same-chain and Relay cross-chain share a durable single-send wallet boundary.
import { createClient } from "@relayprotocol/relay-sdk";
import { createPublicClient, custom, http, defineChain } from "viem";
import { Connection, PublicKey, TransactionInstruction, TransactionMessage, VersionedTransaction } from "@solana/web3.js";
import { Buffer } from "buffer";

export async function executeBuy({ execution, preview, networks, wallet, api, onHash, onProgress, onStage = () => {} }) {
  const core = window.MonitorBuyCore;
  core.validateExecution(execution, preview);
  const { intent, quote } = execution;
  const isBinance = quote.provider === "binance-web3";
  if (isBinance) {
    const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(core.stableJson(quote)));
    const actual = [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, "0")).join("");
    if (actual !== preview.quoteHash) throw Error("币安交易内容与已确认报价不一致");
  }
  const chain = networks.find(x => x.id === intent.source.chainId);
  if (!chain) throw Error("付款链信息缺失");
  const isSolana = chain.id === core.SOLANA;
  const approved = new Map(quote.steps.flatMap(s => s.items.map(i => [core.stableJson(i.data), i.data])));
  const client = isBinance ? null : createClient({ baseApiUrl: "https://api.relay.link", source: "xingyunshe-monitor",
    pollingInterval: 3000, confirmationPollingInterval: 3000, maxPollingAttemptsBeforeTimeout: 100,
    websocket: { enabled: false }, chains: networks.map(n => ({ id: n.id, name: n.name, displayName: n.name,
      vmType: n.vmType, httpRpcUrl: n.rpcUrl, currency: n.currency })) });
  const provider = wallet.provider(isSolana ? "solana" : "evm");
  if (!provider) throw Error("所选钱包不支持付款链签名");
  const viemChain = !isSolana ? defineChain({ id: chain.id, name: chain.name,
    nativeCurrency: chain.currency, rpcUrls: { default: { http: [chain.rpcUrl] } } }) : null;
  // Binance same-chain transactions are submitted by the active wallet, so
  // observe their receipts through that same EIP-1193 provider. Some public
  // BSC endpoints prune receipt history or require an archive token, which
  // must never strand the flow between an exact approval and the swap.
  const publicClient = !isSolana ? createPublicClient({ chain: viemChain,
    transport: isBinance ? custom(provider, { retryCount: 0 }) : http(chain.rpcUrl, { retryCount: 0, timeout: 10000 }) }) : null;
  const solConnection = isSolana ? new Connection(chain.rpcUrl, { commitment: "confirmed", disableRetryOnRateLimit: true }) : null;
  const sent = new Set();
  let gasBudgetUsed = 0n;

  async function ensureAccount() {
    const owners = await wallet.currentAddresses();
    const equals = (a, b, sol) => sol ? a === b : String(a).toLowerCase() === String(b).toLowerCase();
    if (!equals(owners[isSolana ? "solana" : "evm"], intent.sender, isSolana)
      || !equals(owners[intent.target.chainId === core.SOLANA ? "solana" : "evm"], intent.recipient, intent.target.chainId === core.SOLANA)) {
      throw Error("钱包账户发生变化，已停止后续签名");
    }
    if (Date.now() >= execution.expiresAt) throw Error("报价已过期；不继续提交，已有交易请查询状态");
  }
  async function switchChain(id) {
    if (id !== intent.source.chainId) throw Error("禁止切换到订单以外的付款链");
    if (isSolana) return;
    const expected = `0x${id.toString(16)}`;
    const current = await provider.request({ method: "eth_chainId" });
    if (Number(current) === id) return;
    try { await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: expected }] }); }
    catch (error) {
      if (Number(error.code) !== 4902) throw error;
      await provider.request({ method: "wallet_addEthereumChain", params: [{ chainId: expected, chainName: chain.name,
        nativeCurrency: { name: chain.currency.name, symbol: chain.currency.symbol, decimals: chain.currency.decimals },
        rpcUrls: [chain.rpcUrl], ...(chain.explorerUrl ? { blockExplorerUrls: [chain.explorerUrl] } : {}) }] });
      await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: expected }] });
    }
    if (Number(await provider.request({ method: "eth_chainId" })) !== id) throw Error("钱包尚未切换到付款链");
  }
  async function claim(data) {
    const key = core.stableJson(data);
    if (!approved.has(key) || sent.has(key)) throw Error("交易数据变更或重复签名请求，已停止");
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key));
    const fingerprint = [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, "0")).join("");
    await api("claim-step", { orderId: preview.orderId, fingerprint });
    sent.add(key);
  }
  async function remember(hash) {
    const validHash = typeof hash === "string" && (isSolana ? /^[1-9A-HJ-NP-Za-km-z]{64,90}$/.test(hash) : /^0x[0-9a-fA-F]{64}$/.test(hash));
    if (!validHash) throw Error("钱包未返回有效交易哈希，不能确认已提交；请核对原订单，不会自动重发");
    onHash(hash); // Persist locally before optional network reporting.
    onStage(75, "submitted", "钱包已返回交易哈希，等待链上确认");
    // Reporting must not delay receipt observation; never retry the actual transfer.
    void Promise.resolve().then(() => api("progress", { orderId: preview.orderId, txHashes: [hash] })).catch(() => {});
    return hash;
  }

  const adapter = {
    vmType: isSolana ? "svm" : "evm",
    address: async () => intent.sender,
    getChainId: async () => isSolana ? chain.id : Number(await provider.request({ method: "eth_chainId" })),
    switchChain,
    supportsAtomicBatch: async () => false,
    handleSignMessageStep: async () => { throw Error("本次买入不允许离线签名或无限授权"); },
    handleSendTransactionStep: async (id, item) => {
      if (id !== chain.id) throw Error("付款链与订单不一致");
      onStage(25, "preflight", "并行检查实时余额、燃料费和交易模拟");
      await ensureAccount();
      const data = item.data;
      if (!isSolana) {
        await switchChain(id);
        const hex = x => `0x${BigInt(x || 0).toString(16)}`;
        const tx = { from: intent.sender, to: data.to, data: data.data, value: hex(data.value), chainId: hex(id) };
        // Read-only preflight before the durable single-send claim.
        const simulation = isBinance && data.data.startsWith("0xad43f73d")
          ? api("preflight", { orderId: preview.orderId }) : Promise.resolve();
        const walletSimulation = isBinance && data.data.startsWith("0xad43f73d")
          ? provider.request({ method: "eth_call", params: [tx, "pending"] }).catch(() => {
              throw Error("当前兑换路线链上试运行失败，已停止且未唤起钱包；请重新获取稳定币报价");
            }) : Promise.resolve();
        const [estimate, balance, gasPrice, held] = (await Promise.all([
          provider.request({ method: "eth_estimateGas", params: [tx] }),
          provider.request({ method: "eth_getBalance", params: [intent.sender, "latest"] }),
          provider.request({ method: "eth_gasPrice" }),
          intent.source.address !== "0x0000000000000000000000000000000000000000"
            ? provider.request({ method: "eth_call", params: [{ to: intent.source.address,
              data: "0x70a08231" + intent.sender.slice(2).padStart(64, "0") }, "latest"] }) : Promise.resolve(intent.amount),
          simulation,
          walletSimulation
        ])).slice(0,4).map(BigInt);
        if (held < BigInt(intent.amount)) throw Error("付款币余额已变化，请重新报价");
        if (estimate < 21000n || estimate > 5000000n || gasPrice <= 0n || gasPrice > 1000000000000n) throw Error("网络燃料费估计异常，请重新报价");
        if (balance < BigInt(data.value || 0) + estimate * gasPrice * 12n / 10n) throw Error("原生币余额不足以支付金额和燃料费");
        tx.gas = hex(estimate * 12n / 10n);
        if (isBinance) {
          const quoted = BigInt(quote.raw.tx.gasPrice);
          const fast = quoted > gasPrice ? quoted : gasPrice;
          const cost = estimate * fast * 12n / 10n;
          const budget = BigInt(quote.fees?.gas?.maxAmount || (BigInt(quote.raw.tx.gas) * quoted * 3n));
          if (gasBudgetUsed + cost > budget) throw Error("实时 Gas 超过本次费用预算，请重新报价");
          // Exact approvals also leave gas for the remaining approval and swap.
          const stepIndex = quote.steps.findIndex(s => s.items.some(i => core.stableJson(i.data) === core.stableJson(data)));
          const pending = quote.steps.slice(stepIndex+1).reduce((sum,s) => sum + (s.id === 'swap' ? BigInt(quote.raw.tx.gas) : 100000n),0n);
          const reserve = cost + pending * fast * 12n / 10n;
          if (balance < BigInt(data.value || 0) + reserve) throw Error("请预留足够原生币支付本次及后续步骤的 Gas，或减少买入金额");
          tx.gasPrice = hex(fast);
          gasBudgetUsed += cost;
        }
        await ensureAccount();
        await switchChain(id);
        await claim(data);
        onStage(55, "wallet", "等待钱包显示确认并返回哈希；尚不能确认上链");
        // No retry on eth_sendTransaction, including transport timeout or ambiguous result.
        const submitted = provider.request({ method: "eth_sendTransaction", params: [tx] });
        if (!isBinance) return remember(await submitted);
        // Persist even a late wallet response; timeout never replays the send.
        let timer;
        try {
          return await Promise.race([Promise.resolve(submitted).then(remember), new Promise((_, reject) => {
            timer = setTimeout(() => reject(Error("钱包尚未返回哈希，请核对原请求；不会自动重发")), 90000);
          })]);
        } finally { clearTimeout(timer); }
      }
      const instructions = data.instructions.map(instruction => {
        for (const key of instruction.keys || []) {
          if (key.isSigner && key.pubkey !== intent.sender) throw Error("Solana 指令要求未授权签名者");
        }
        return new TransactionInstruction({ programId: new PublicKey(instruction.programId),
          keys: instruction.keys.map(key => ({ ...key, pubkey: new PublicKey(key.pubkey) })),
          data: Buffer.from(instruction.data.replace(/^0x/, ""), "hex") });
      });
      const lookup = await Promise.all((data.addressLookupTableAddresses || []).map(async key => {
        const table = await solConnection.getAddressLookupTable(new PublicKey(key));
        if (!table.value) throw Error("Solana 地址查找表不可用");
        return table.value;
      }));
      const latest = await solConnection.getLatestBlockhash("confirmed");
      const transaction = new VersionedTransaction(new TransactionMessage({ payerKey: new PublicKey(intent.sender),
        recentBlockhash: latest.blockhash, instructions }).compileToV0Message(lookup));
      const simulation = await solConnection.simulateTransaction(transaction, { sigVerify: false });
      if (simulation.value.err) throw Error("Solana 交易模拟未通过，不提交交易");
      await ensureAccount();
      await claim(data);
      if (typeof provider.signTransaction !== "function") throw Error("当前 Solana 钱包不支持安全的交易签名接口");
      const originalMessage = Buffer.from(transaction.message.serialize());
      onStage(55, "wallet", "等待钱包签名；尚未发送到链上");
      const signed = await provider.signTransaction(transaction);
      if (!Buffer.from(signed.message.serialize()).equals(originalMessage)) throw Error("钱包返回的交易内容发生变化");
      // Broadcast signed bytes once; no automatic re-sign or re-send on failure.
      onStage(65, "broadcast", "钱包已签名，正在提交到链上");
      const hash = await solConnection.sendRawTransaction(signed.serialize(), { skipPreflight: false, maxRetries: 0 });
      return remember(hash);
    },
    handleConfirmTransactionStep: async (hash, id, onReplaced, onCancelled) => {
      if (id !== chain.id) throw Error("确认链不匹配");
      onStage(80, "confirming", "交易已提交，正在查询源链确认");
      if (!isSolana) {
        const receipt = await publicClient.waitForTransactionReceipt({ hash, timeout: 180000, pollingInterval: 3000,
          onReplaced: replacement => { if (replacement.reason === "cancelled") onCancelled(); else onReplaced(replacement.transaction.hash); } });
        if (receipt.status !== "success") throw Error("源链交易失败，请查看交易记录");
        onStage(90, "settlement", intent.source.chainId === intent.target.chainId ? "源链确认完成，核对目标币到账" : "源链确认完成，等待跨链到账");
        return receipt;
      }
      const deadline = Date.now() + 180000;
      while (Date.now() < deadline) {
        const status = (await solConnection.getSignatureStatuses([hash], { searchTransactionHistory: true })).value[0];
        if (status?.err) throw Error("Solana 源链交易失败");
        if (["confirmed", "finalized"].includes(status?.confirmationStatus)) { onStage(90, "settlement", "源链确认完成，核对目标币到账"); return { txHash: hash, blockNumber: status.slot, blockHash: "" }; }
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
      throw Error("源链确认暂未完成；只查询原订单，不重复买入");
    }
  };
  // SDK checks the active chain before calling the wallet send boundary.
  onStage(15, "account", "核对执行钱包与付款网络");
  await ensureAccount();
  await switchChain(chain.id);
  await ensureAccount();
  if (isBinance) {
    // No Relay SDK call, deposit, solver or intent polling for a Binance swap.
    for (const step of quote.steps) {
      const item = step.items[0];
      const hash = await adapter.handleSendTransactionStep(chain.id, item);
      onStage(80, "confirming", step.id === "approval" ? "授权已提交，等待链上确认" : "买入已提交，等待链上确认");
      const receipt = await publicClient.waitForTransactionReceipt({ hash, timeout: 90000, pollingInterval: 1500,
        onReplaced: replacement => {
          const tx = replacement.transaction;
          if (replacement.reason === "cancelled" || String(tx.from).toLowerCase() !== String(intent.sender).toLowerCase()
            || String(tx.to).toLowerCase() !== item.data.to || tx.input?.toLowerCase() !== item.data.data
            || BigInt(tx.value) !== BigInt(item.data.value)) throw Error("钱包替换了原交易，请查询原订单，不继续买入");
          void remember(tx.hash);
        } });
      if (receipt.status !== "success") throw Error(step.id === "approval" ? "授权交易失败，未继续买入" : "买入交易失败，请查询原交易");
      // A receipt for approve is not a purchase. After it, fresh simulation and
      // expiry checks are mandatory before the separately confirmed swap.
    }
    onStage(95, "settlement", "买入交易已确认，核对目标币实际到账");
    return { status: "submitted" };
  }
  return client.actions.execute({ quote, wallet: adapter, onProgress });
}
