"""Binance same-chain adapter. Read/build/simulate only; wallets keep all keys.

The reviewed EVM router envelope is pinned to its live selector implementation.
Unknown routes/RFQ/Solana programs fail closed, never fall back to Relay swaps.
Both the encoded minimum and simulated *wallet* balance effects are checked.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import re
import time

PROVIDER = "binance-web3"
QUOTE_TTL_SECONDS = 180
ROUTER = "0xb44446b0c8e56988c34f7ff73ae904982b5fdda5"
FACET = "0xa9fa1b56f4d7bd25375c2d40b4c8e36a9509e603"
FACET_SHA256 = "924e9da536e78a9ce92f633058a62907ee5834738947432d186e02b212a0d574"
SELECTOR = "0xad43f73d"
CHAINS = frozenset({1, 56, 8453})
ZERO = "0x" + "0" * 40
NATIVE = "0x" + "e" * 40
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def dec(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise ValueError("币安报价数值无效") from None


def uint(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,78}", str(value)):
        raise ValueError("币安交易金额格式无效")
    result = int(value)
    if result >= 2**256:
        raise ValueError("币安交易金额越界")
    return result


def addr(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("币安交易地址无效")
    return value.lower()


def token_addr(value):
    result = addr(value)
    return ZERO if result == NATIVE else result


def approval(intent, amount):
    return {"from": intent["sender"], "to": intent["source"]["address"], "chainId": intent["source"]["chainId"],
            "value": "0", "data": "0x095ea7b3" + ROUTER[2:].rjust(64, "0") + format(amount, "064x")}


def route_names(route):
    """Return display-only DEX names; execution safety never depends on labels."""
    result = []
    for item in route.get("dexRouterList") or []:
        if not isinstance(item, dict):
            continue
        name = "".join(char for char in str(item.get("dexName") or item.get("name") or "").strip()
                       if char.isprintable())[:80]
        if name and name not in result:
            result.append(name)
    return result[:8]


def validate_raw(raw, intent):
    """Recheck API fields AND the pinned router envelope, not ticker matching."""
    try:
        source, target = intent["source"], intent["target"]
        chain = source["chainId"]
        if chain not in CHAINS or chain != target["chainId"]:
            raise ValueError("币安当前安全执行器仅开放 BNB、Ethereum、Base 同链兑换；其他路线尚未验证")
        if addr(target["address"]) == ZERO:
            raise ValueError("当前买入入口用于代币买入，原生币兑回尚未开放")
        if addr(intent["sender"]) != addr(intent["recipient"]):
            raise ValueError("币安同链买入必须回到当前执行钱包")
        if raw.get("executionMode") != "SWAP" or raw.get("rfq"):
            raise ValueError("不支持 RFQ 或额外离线授权，未发起钱包请求")
        route, tx = raw["routerResult"], raw["tx"]
        if str(route["binanceChainId"]) != str(chain) or route["vendorName"] != "LiquidMesh":
            raise ValueError("币安路由网络或执行服务不匹配")
        if tx.get("signatureData") or addr(tx["to"]) != ROUTER or addr(tx["from"]) != addr(intent["sender"]):
            raise ValueError("币安交易包含未知合约、签名或发送账户")
        for item, expected in ((route["fromToken"], source), (route["toToken"], target)):
            if token_addr(item["tokenContractAddress"]) != addr(expected["address"]) or int(item["decimal"]) != expected["decimals"]:
                raise ValueError("币安返回的链与 CA 或精度不一致")
            if item.get("isHoneyPot") is True or dec(item.get("taxRate", 0)) != 0:
                raise ValueError("代币存在蜜罐或转账税风险，暂不自动执行")
        amount, out, minimum = uint(intent["amount"]), uint(route["toTokenAmount"]), uint(tx["minReceiveAmount"])
        slip = dec(tx["slippagePercent"])
        if not 0 <= slip <= min(Decimal(20), dec(intent["slippageBps"])/100):
            raise ValueError("币安实际滑点超过已确认上限")
        if not 0 < minimum <= out or minimum < int(dec(out) * (100-slip) / 100):
            raise ValueError("币安最低到账量不足，已停止")
        if uint(route["fromTokenAmount"]) != amount or uint(tx["value"]) != (amount if addr(source["address"]) == ZERO else 0):
            raise ValueError("币安实际扣款与输入金额不一致")
        if route.get("feeAmount") not in (None, "0", 0) or route.get("actualSwapAmount") not in (None, str(amount)):
            raise ValueError("币安报价包含未确认的额外扣费")
        if abs(dec(route["priceImpactPercent"])) > 3:
            raise ValueError("币安报价价格冲击超过 3%")
        input_usd = dec(amount) / 10**source["decimals"] * dec(route["fromToken"]["tokenUnitPrice"])
        if input_usd <= 0 or abs(input_usd-dec(intent["amountUsd"])) > dec(intent["amountUsd"])*Decimal("0.03"):
            raise ValueError("付款币价格变化超过 3%，请重新报价")
        output_usd = dec(out) / 10**target["decimals"] * dec(route["toToken"]["tokenUnitPrice"])
        if output_usd <= 0 or (input_usd-output_usd)/input_usd > Decimal("0.08"):
            raise ValueError("币安报价总损耗超过 8% 或目标币价格不可用")
        data = tx["data"]
        if not isinstance(data, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2}){324,65536}", data) or not data.lower().startswith(SELECTOR):
            raise ValueError("币安返回了尚未验证的交易调用")
        words = [int(data[10+i*64:74+i*64], 16) for i in range(10)]
        # The pinned envelope sends to msg.sender (zero receiver), and binds
        # source, exact input, target and minimum before its nested route bytes.
        expected = [0, int(addr(source["address"]),16), amount, int(addr(target["address"]),16), minimum]
        if [words[1], *words[3:7]] != expected or words[9] != 320:
            raise ValueError("币安合约调用中的收款、币种、金额或滑点保护不匹配")
        size = int(data[650:714], 16)
        if size < 4 or len(data) != 10 + 64*11 + ((size+31)//32)*64:
            raise ValueError("币安交易编码长度不匹配")
        gas, gas_price = uint(tx["gas"]), uint(tx["gasPrice"])
        if not 21000 <= gas <= 5_000_000 or not 0 < gas_price <= 10**12:
            raise ValueError("币安燃料费估计异常")
        if not 0 <= dec(route["tradeFee"]) <= min(Decimal(5), dec(intent["amountUsd"])*Decimal("0.2")):
            raise ValueError("币安预计燃料费过高，已停止")
        return raw
    except (KeyError, TypeError, IndexError, OverflowError):
        raise ValueError("币安返回的交易结构不完整，未发起钱包请求") from None


def normalize(raw, intent, approvals=()):
    validate_raw(raw, intent)
    r, tx = raw["routerResult"], raw["tx"]
    source, target = intent["source"], intent["target"]
    out = uint(r["toTokenAmount"])
    input_usd = dec(intent["amountUsd"])
    output_usd = dec(out)/10**target["decimals"]*dec(r["toToken"]["tokenUnitPrice"])
    swap = {"from": addr(tx["from"]), "to": addr(tx["to"]), "data": tx["data"].lower(),
            "value": str(uint(tx["value"])), "chainId": source["chainId"]}
    steps = [{"id": "approval", "kind": "transaction", "items": [{"data": a}]} for a in approvals]
    steps.append({"id": "swap", "kind": "transaction", "items": [{"data": swap}]})
    gas_amount = uint(tx['gas'])*uint(tx['gasPrice'])
    gas_fee = {"amount": str(gas_amount), "amountUsd": str(dec(r.get('tradeFee','0')))}
    # Independent native price binds a monetary fee ceiling, not a multiple of
    # an occasionally tiny vendor estimate. Older quotes keep their old guard.
    native_price = dec(source.get('nativePriceUsd') or (source.get('priceUsd') if addr(source['address']) == ZERO else 0))
    if native_price > 0:
        budget = min(Decimal(5), input_usd * Decimal('0.2'))
        gas_fee.update(maxAmount=str(int(budget / native_price * 10**18)), maxAmountUsd=str(budget),
                       reserveAmount=str((uint(tx['gas']) + len(approvals)*100000)*uint(tx['gasPrice'])*2))
    execution = {"swapContract": ROUTER, "targetAddress": addr(target["address"]),
        "recipient": addr(intent["recipient"]), "receiverMode": "connected-wallet",
        "approval": {"required": bool(approvals), "resetRequired": len(approvals) == 2,
            "tokenAddress": addr(source["address"]), "tokenSymbol": str(source.get("symbol") or "")[:40],
            "spender": ROUTER, "amount": str(uint(intent["amount"])),
            "amountFormatted": str(dec(intent["amount"])/10**source["decimals"]), "exactAmount": True},
        "dexes": route_names(r), "verification": "pinned-router-exact-calldata"}
    return {"provider": PROVIDER, "raw": raw, "steps": steps, "execution": execution,
        "details": {"sender": intent["sender"], "recipient": intent["recipient"],
            "currencyIn": {"currency": source, "amount": intent["amount"], "amountUsd": str(input_usd),
                "amountFormatted": str(dec(intent["amount"])/10**source["decimals"])},
            "currencyOut": {"currency": target, "amount": str(out), "minimumAmount": str(uint(tx["minReceiveAmount"])),
                "amountFormatted": str(dec(out)/10**target["decimals"])},
            "slippageTolerance": {"total": str(dec(tx["slippagePercent"])*100)},
            "swapImpact": {"percent": str(dec(r["priceImpactPercent"]))},
            "totalImpact": {"percent": str((input_usd-output_usd)/input_usd*100)}, "timeEstimate": None},
        "fees": {"gas": gas_fee, "relayer": {"amountUsd": "0"}}}


def validate_quote(quote, intent):
    approvals = []
    steps = quote.get("steps", [])
    if not 1 <= len(steps) <= 3:
        raise ValueError("币安交易步骤不匹配")
    for index, step in enumerate(steps[:-1]):
        amount = 0 if len(steps) == 3 and index == 0 else uint(intent["amount"])
        expected = approval(intent, amount)
        if step != {"id": "approval", "kind": "transaction", "items": [{"data": expected}]} or addr(intent["source"]["address"]) == ZERO:
            raise ValueError("币安授权不匹配，只允许本次金额的精确授权")
        approvals.append(expected)
    expected = normalize(quote["raw"], intent, approvals)
    if quote != expected:
        raise ValueError("币安执行步骤与原始报价不一致")
    return quote


def validate_simulation(sim, intent, minimum):
    if not isinstance(sim, dict) or sim.get("status") != "SUCCESS" or sim.get("failReason"):
        raise ValueError("币安交易模拟未通过，请重新报价；未发起买入请求")
    balances = sim.get("balanceChanges")
    allowances = sim.get("allowanceChanges")
    if not isinstance(balances, list) or not isinstance(allowances, list):
        raise ValueError("币安模拟缺少资金变化明细，已停止")
    owner = addr(intent["sender"])
    source, target = addr(intent["source"]["address"]), addr(intent["target"]["address"])
    changes = {}
    for change in balances:
        if addr(change["owner"]) != owner:
            continue
        token = token_addr(change["contractAddress"])
        amount = dec(change["change"])
        if amount != amount.to_integral_value():
            raise ValueError("币安模拟金额单位无效")
        changes[token] = changes.get(token, 0) + int(amount)
        if amount < 0 and token != source:
            raise ValueError("交易模拟会扣除非付款资产，已拒绝")
    if changes.get(source) != -uint(intent["amount"]) or changes.get(target, 0) < uint(minimum):
        raise ValueError("模拟扣款或目标币到账与本次报价不一致")
    for change in allowances:
        if addr(change["owner"]) == owner and (addr(change["tokenAddress"]) != source or
                addr(change["spender"]) != ROUTER or uint(change["postAmount"]) > uint(change["preAmount"])):
            raise ValueError("交易模拟包含未确认的额外授权")
    return True


class BinanceBuyAdapter:
    def __init__(self, client, rpc):
        self.client, self.rpc = client, rpc

    def verify_router(self, network):
        resolved = self.rpc(network, "eth_call", [{"to": ROUTER, "data": "0xcdffacc6"+SELECTOR[2:].ljust(64,"0")}, "latest"])
        if not isinstance(resolved, str) or resolved.lower() != "0x"+FACET[2:].rjust(64,"0"):
            raise ValueError("币安路由合约已升级或无法核验，暂停执行")
        code = self.rpc(network, "eth_getCode", [FACET, "latest"])
        if not isinstance(code,str) or not re.fullmatch(r"0x(?:[a-fA-F0-9]{2})+",code) or hashlib.sha256(bytes.fromhex(code[2:])).hexdigest() != FACET_SHA256:
            raise ValueError("币安路由实现校验不通过")

    def build(self, intent, network):
        if intent["source"]["chainId"] not in CHAINS:
            raise ValueError("该链的币安安全执行器尚未开放，不会改用旧同链通道")
        source = intent["source"]
        params = {
            "binanceChainId": str(source["chainId"]), "amount": intent["amount"],
            "fromTokenAddress": NATIVE if addr(source["address"]) == ZERO else source["address"],
            "toTokenAddress": NATIVE if addr(intent["target"]["address"]) == ZERO else intent["target"]["address"],
            "userWalletAddress": intent["sender"], "vendor": "LiquidMesh",
            "approveTransaction": "false", "priceImpactProtectionPercent": "3", "gasLevel": "fast"}
        # Live API rejects autoSlippage + slippagePercent together (40001),
        # despite the documentation describing the latter as overridden.
        if intent.get("autoSlippage") is True:
            params.update(autoSlippage="true", maxAutoSlippagePercent=str(dec(intent["slippageBps"])/100))
        else:
            params.update(autoSlippage="false", slippagePercent=str(dec(intent["slippageBps"])/100))
        raw = self.client.request("GET", "/api/v1/dex/aggregator/quote-and-swap", params)
        validate_raw(raw, intent)
        self.verify_router(network)
        approvals = []
        if addr(source["address"]) != ZERO:
            held = self.rpc(network, "eth_call", [{"to": source["address"], "data": "0xdd62ed3e"+
                addr(intent["sender"])[2:].rjust(64,"0")+ROUTER[2:].rjust(64,"0")}, "latest"])
            allowance = int(held,16)
            if intent.get("forceExactApproval") is True:
                # Session mode meters every order through the on-chain Roles
                # allowance. Reusing a pre-existing approval would bypass that
                # meter, so reset any residue and approve this order exactly.
                if allowance:
                    approvals.append(approval(intent, 0))
                approvals.append(approval(intent, uint(intent["amount"])))
            elif allowance < uint(intent["amount"]):
                if allowance:
                    approvals.append(approval(intent, 0))
                approvals.append(approval(intent, uint(intent["amount"])))
        quote = normalize(raw, intent, approvals)
        # A stablecoin may need approval first. Its swap is simulated after the
        # confirmed exact approval, before claiming/sending the swap step.
        if not approvals:
            self.simulate(quote, intent)
            quote["_simulatedAt"] = time.time()
        # The quote remains protected by its exact calldata, minimum receive
        # amount and the user's slippage ceiling. Give a phone-linked wallet
        # enough time to confirm an approval and then the swap; starting the
        # clock after quote construction also avoids consuming validity while
        # the read-only Binance request is still running.
        quote["_intent"], quote["_expires"] = intent, time.time()+QUOTE_TTL_SECONDS
        return quote

    def simulate(self, quote, intent):
        tx = quote["steps"][-1]["items"][0]["data"]
        result = self.client.request("POST", "/api/v1/dex/pre-transaction/simulate", body={
            "binanceChainId": str(intent["source"]["chainId"]), "evmTx": {k:tx[k] for k in ("from","to","value","data")}})
        return validate_simulation(result, intent, quote["details"]["currencyOut"]["minimumAmount"])

    def chain_simulate(self, quote, intent, network):
        """Execute the exact pinned swap as a read-only call on an independent node."""
        validate_quote(quote, intent)
        tx = quote["steps"][-1]["items"][0]["data"]
        result = self.rpc(network, "eth_call", [{"from": intent["sender"], "to": tx["to"],
            "data": tx["data"], "value": hex(uint(tx["value"]))}, "pending"])
        if not isinstance(result, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", result):
            raise ValueError("链上试运行没有返回有效结果，已停止且未唤起钱包")
        return True

    def status(self, record, network):
        intent, quote = record["intent"], record["quote"]
        swap = quote["steps"][-1]["items"][0]["data"]
        result = {"provider": PROVIDER, "executionStatus": "waiting", "outTxHashes": [], "inTxHashes": []}
        def matches(transaction, expected):
            try:
                return (addr(transaction.get("from")) == addr(intent["sender"])
                    and addr(transaction.get("to")) == addr(expected["to"])
                    and transaction.get("input", "").lower() == expected["data"].lower()
                    and int(transaction.get("value", "0x0"), 16) == uint(expected["value"]))
            except (ValueError, TypeError):
                return False
        for tx_hash in record["txHashes"]:
            transaction = self.rpc(network, "eth_getTransactionByHash", [tx_hash])
            if not transaction:
                continue
            if not matches(transaction, swap):
                if (not result["inTxHashes"] and any(matches(transaction, step["items"][0]["data"])
                        for step in quote["steps"][:-1])):
                    result["executionStatus"] = "approval-only"
                continue
            result["inTxHashes"].append(tx_hash)
            receipt = self.rpc(network, "eth_getTransactionReceipt", [tx_hash])
            if not receipt:
                result["executionStatus"] = "submitted"
                continue
            if (receipt.get("transactionHash", "").lower() != tx_hash.lower()
                    or not receipt.get("blockHash") or receipt.get("blockHash") != transaction.get("blockHash")):
                result["executionStatus"] = "settlement-unverified"
                continue
            if int(receipt.get("status", "0x0"),16) != 1:
                result["executionStatus"] = "failure"
                continue
            # Approval receipts and unrelated hashes NEVER mean a buy succeeded.
            received = 0
            for log in receipt.get("logs", []):
                topics = log.get("topics", [])
                if (not log.get("removed") and addr(log.get("address")) == addr(intent["target"]["address"])
                        and len(topics) == 3 and topics[0].lower() == TRANSFER):
                    value = int(log.get("data", "0x0"),16)
                    if "0x"+topics[2][-40:].lower() == addr(intent["recipient"]): received += value
                    if "0x"+topics[1][-40:].lower() == addr(intent["recipient"]): received -= value
            if received >= uint(quote["details"]["currencyOut"]["minimumAmount"]):
                result.update(executionStatus="success", outTxHashes=[tx_hash])
                break
            result["executionStatus"] = "settlement-unverified"
        return result
