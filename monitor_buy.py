"""User-initiated, non-custodial monitor buys. Never signs or broadcasts funds."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from urllib.parse import urlparse

import requests
from monitor_balances import BalanceReadCache
from binance_web3 import BinanceWeb3Client
from binance_buy import BinanceBuyAdapter, PROVIDER as BINANCE_PROVIDER, validate_quote as validate_binance_quote
from binance_agentic import agentic_wallet_capabilities
from session_buy import (ALLOWANCE_KEY as SESSION_ALLOWANCE_KEY, AmbiguousBroadcastError,
                         BINANCE_ROUTER as SESSION_ROUTER, MODE as SESSION_MODE,
                         MAX_ORDER_USDT as SESSION_MAX_ORDER_USDT, MAX_SESSION_USDT as SESSION_MAX_TOTAL_USDT,
                         PINNED_STABLECOINS as SESSION_STABLECOINS,
                         ROLE_KEY as SESSION_ROLE_KEY, RpcCallReverted, SessionBuyService)

RELAY = "https://api.relay.link"
ROLES_SUBGRAPH = "https://gnosisguild.squids.live/roles:production/api/graphql"
ROLES_CHAIN_PREFIX = {1: "eth", 56: "bnb", 8453: "base"}
TRUSTED_RPC_URLS = {
    # Already used elsewhere in this project as the free BSC fallback. Prefer
    # it here because Relay's current publicnode URL rejects receipt history.
    56: ("https://bsc-dataseed.binance.org",),
}
IDENTITY_EVM_NETWORKS = {
    # CA verification only performs read-only ERC-20 metadata calls. Keeping
    # these high-volume monitor chains independent from Relay's directory
    # avoids leaving a visible card pending when the directory is slow or has
    # not indexed a newly launched token yet.
    56: {"id": 56, "name": "bsc", "displayName": "BNB Smart Chain", "vmType": "evm",
         "httpRpcUrl": "https://bsc-dataseed.binance.org"},
    8453: {"id": 8453, "name": "base", "displayName": "Base", "vmType": "evm",
           "httpRpcUrl": "https://mainnet.base.org"},
    4663: {"id": 4663, "name": "robinhood", "displayName": "Robinhood Chain", "vmType": "evm",
           "httpRpcUrl": "https://rpc.mainnet.chain.robinhood.com/"},
}
SOLANA = 792703809
ZERO = "0x" + "0" * 40
SOL_NATIVE = "11111111111111111111111111111111"
STABLES = frozenset({"USDT", "USDC", "USDC.E", "USDG", "DAI", "PYUSD", "USDE", "AUSD", "MUSD", "PUSD"})
MAX_DIRECTORY_TOKENS_PER_CHAIN = 24
DEFAULT_CHAINS = (4663, 56, 8453, 1, SOLANA, 42161, 10, 137, 43114)
DEFAULT_MAX_ORDER_USDT = Decimal("1000")
ALIASES = {"eth": 1, "ethereum": 1, "bsc": 56, "bnb": 56, "base": 8453,
           "sol": SOLANA, "solana": SOLANA, "501": SOLANA, "robinhood": 4663,
           "robinhood-chain": 4663, "arbitrum": 42161, "arb": 42161,
           "optimism": 10, "op": 10, "polygon": 137, "avalanche": 43114}


class FundingUnavailable(ValueError):
    def __init__(self, code, message, diagnostics=None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or []


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("金额必须是有限数值")
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("金额格式不正确") from None


def chain_id(value):
    key = str(value or "").strip().lower()
    if key in ALIASES:
        return ALIASES[key]
    try:
        result = int(key, 16) if key.startswith("0x") else int(key)
        return SOLANA if result == 501 else result
    except ValueError:
        raise ValueError("标的所在公链尚未确认") from None


def address(value, chain):
    raw = str(value or "").strip()
    pattern = r"[1-9A-HJ-NP-Za-km-z]{32,44}" if chain == SOLANA else r"0x[0-9a-fA-F]{40}"
    if not re.fullmatch(pattern, raw):
        raise ValueError("钱包或代币合约地址不正确，不能按币名猜测")
    if chain == SOLANA and len(base58_bytes(raw)) != 32:
        raise ValueError("Solana 地址必须解码为 32 字节")
    return raw if chain == SOLANA else raw.lower()


def decode_abi_text(value):
    """Decode a standard ABI string or the legacy bytes32 token metadata form."""
    raw = str(value or "")
    if not re.fullmatch(r"0x[0-9a-fA-F]+", raw) or len(raw) % 2:
        raise ValueError("代币名称元数据格式不正确")
    data = bytes.fromhex(raw[2:])
    if len(data) == 32:
        encoded = data.rstrip(b"\0")
    else:
        if len(data) < 64:
            raise ValueError("代币名称元数据不完整")
        offset = int.from_bytes(data[:32], "big")
        if offset < 0 or offset + 32 > len(data):
            raise ValueError("代币名称元数据偏移不正确")
        length = int.from_bytes(data[offset:offset + 32], "big")
        if not 0 < length <= 120 or offset + 32 + length > len(data):
            raise ValueError("代币名称元数据长度不正确")
        encoded = data[offset + 32:offset + 32 + length]
    try:
        result = encoded.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ValueError("代币名称元数据编码不正确") from None
    if not result or len(result) > 60 or any(ord(char) < 32 for char in result):
        raise ValueError("代币名称元数据不可信")
    return result


def base58_bytes(value):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    result = 0
    for char in value:
        result = result * 58 + alphabet.index(char)
    return b"\0" * (len(value)-len(value.lstrip("1"))) + result.to_bytes((result.bit_length()+7)//8, "big")


def same_address(a, b, chain):
    return address(a, chain) == address(b, chain)


def native(token):
    return token["address"] == (SOL_NATIVE if token["chainId"] == SOLANA else ZERO)


def funding_kind(token):
    if native(token):
        return "native"
    return "stable" if str(token.get("symbol", "")).upper() in STABLES else "other"


def funding_tier(token, destination):
    # Prefer an already-held stablecoin on the destination chain. This avoids
    # spending the gas asset when the wallet can fund the same direct purchase.
    # A bounded list of Relay-routable tokens is only scanned after stable and
    # native funding fail; same-chain alternatives still precede any bridge.
    offset = 0 if token["chainId"] == destination else 3
    return offset + {"stable": 0, "native": 1, "other": 2}[funding_kind(token)]


def units(usd, price, decimals):
    if not 0 <= int(decimals) <= 36 or number(price) <= 0:
        raise ValueError("付款币价格或精度无效")
    return int((number(usd) / number(price) * 10 ** int(decimals)).to_integral_value(rounding=ROUND_DOWN))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_solana_instructions(data, intent, network, protocol):
    # Only the narrow, decoded native deposit instruction is enabled. Unknown SPL
    # programs/authority changes/extra transfers are rejected rather than blindly signed.
    instructions = data.get("instructions")
    if not native(intent["source"]) or not isinstance(instructions, list) or len(instructions) != 1:
        raise ValueError("当前 Solana 安全执行器仅开放可完整解码的 SOL 原生币付款路线")
    instruction = instructions[0]
    info = network.get("protocol", {}).get("v2", {})
    expected = "0d9e0ddf5fd51c06" + int(intent["amount"]).to_bytes(8, "little").hex() + protocol["orderId"][2:].lower()
    if instruction.get("programId") != info.get("depository") or str(instruction.get("data", "")).lower() != expected:
        raise ValueError("Solana 程序、数量或订单不匹配")
    keys = instruction.get("keys") or []
    if len(keys) != 5 or [key.get("pubkey") for key in keys[1:]] != [intent["sender"], intent["sender"], info.get("depositoryVault"), SOL_NATIVE]:
        raise ValueError("Solana 付款、退款或保管账户不一致")
    if [key.get("isSigner") for key in keys] != [False, True, False, False, False] or [key.get("isWritable") for key in keys] != [False, True, False, True, False]:
        raise ValueError("Solana 指令账户权限超出本次付款范围")
    for key in keys:
        address(key.get("pubkey"), SOLANA)
    if len(data.get("addressLookupTableAddresses") or []) > 4:
        raise ValueError("Solana 地址表过多")


def validate_quote(quote, intent, networks):
    """Bind provider output and transaction steps to the user's exact intent; fail closed."""
    details = quote.get("details") or {}
    source, target = intent["source"], intent["target"]
    if intent["sender"] in {ZERO, SOL_NATIVE} or intent["recipient"] in {ZERO, SOL_NATIVE}:
        raise ValueError("不能使用销毁地址作为钱包")
    incoming, outgoing = details.get("currencyIn") or {}, details.get("currencyOut") or {}
    for item, expected in ((incoming, source), (outgoing, target)):
        currency = item.get("currency") or {}
        if chain_id(currency.get("chainId")) != expected["chainId"] or not same_address(currency.get("address"), expected["address"], expected["chainId"]):
            raise ValueError("报价中的链或代币与所选标的不一致")
    if not same_address(details.get("sender"), intent["sender"], source["chainId"]) or not same_address(details.get("recipient"), intent["recipient"], target["chainId"]):
        raise ValueError("报价的付款人或收款人不一致")
    if int(incoming.get("amount", 0)) != int(intent["amount"]):
        raise ValueError("报价扣款金额发生变化")
    out_amount, minimum = int(outgoing.get("amount", 0)), int(outgoing.get("minimumAmount", 0))
    raw_slip = (details.get("slippageTolerance") or {}).get("total")
    if raw_slip is None:
        raise ValueError("Relay 未返回滑点容忍度，无法核验本条路线")
    slip = number(raw_slip)
    if slip < 0 or slip > intent["slippageBps"]:
        raise ValueError(f"Relay 返回的滑点容忍度 {slip/100:g}% 超过本次 {intent['slippageBps']/100:g}% 上限")
    if out_amount <= 0 or minimum <= 0 or minimum > out_amount:
        raise ValueError("Relay 预计到账或最低到账数据无效")
    if minimum < out_amount * (10000 - intent["slippageBps"]) // 10000:
        raise ValueError(f"Relay 最低到账量未满足本次 {intent['slippageBps']/100:g}% 保护，已拒绝路线（并非已发生亏损）")
    if abs(number((details.get("swapImpact") or {}).get("percent", 0))) > 3:
        raise ValueError("价格冲击超过 3%，已暂停买入")
    if abs(number((details.get("totalImpact") or {}).get("percent", 0))) > 8:
        raise ValueError("路线总损耗超过 8%，已暂停买入")
    fees = quote.get("fees") or {}
    if number((fees.get("app") or {}).get("amount", 0)) != 0:
        raise ValueError("报价含有未授权的平台附加费")

    # Require a bound protocol order, not arbitrary swap/call data.
    protocol = (quote.get("protocol") or {}).get("v2") or {}
    order = protocol.get("orderData") or {}
    output = order.get("output") or {}
    payments = output.get("payments") or []
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(protocol.get("orderId", ""))) or len(payments) != 1 or output.get("calls"):
        raise ValueError("路由尚不能提供可核验的纯买入订单，不允许任意合约调用")
    payment = payments[0]
    output_network = networks[target["chainId"]]
    if str(output.get("chainId")) not in {str(target["chainId"]), str(output_network.get("name")), str((output_network.get("protocol") or {}).get("v2", {}).get("chainId"))}:
        raise ValueError("跨链订单目标网络不一致")
    if not same_address(payment.get("recipient"), intent["recipient"], target["chainId"]) or not same_address(payment.get("currency"), target["address"], target["chainId"]) or int(payment.get("minimumAmount", 0)) < minimum:
        raise ValueError("跨链订单的到账保护不一致")
    network = networks[source["chainId"]]
    def network_matches(value, key):
        info = networks[key]
        return str(value) in {str(key), str(info.get("name")), str((info.get("protocol") or {}).get("v2", {}).get("chainId"))}
    inputs = order.get("inputs") or []
    bound_payment = protocol.get("paymentDetails") or {}
    deposit_contract = (network.get("protocol") or {}).get("v2", {}).get("depository")
    if len(inputs) != 1 or order.get("fees") or not deposit_contract:
        raise ValueError("跨链订单缺少唯一、可核验的付款协议")
    for payment_in in (inputs[0].get("payment") or {}, bound_payment):
        if not network_matches(payment_in.get("chainId"), source["chainId"]) or not same_address(payment_in.get("currency"), source["address"], source["chainId"]) or int(payment_in.get("amount", 0)) != int(intent["amount"]):
            raise ValueError("协议付款币种或金额不一致")
    if not same_address(bound_payment.get("depository"), deposit_contract, source["chainId"]):
        raise ValueError("协议收款合约不在可信目录")
    for entry in inputs:
        for refund in entry.get("refunds") or []:
            matches = [key for key in (source["chainId"], target["chainId"]) if network_matches(refund.get("chainId"), key)]
            if not matches:
                raise ValueError("退款网络不属于本次订单")
            refund_chain = matches[0]
            expected_recipient = intent["sender"] if refund_chain == source["chainId"] else intent["recipient"]
            if not same_address(refund.get("recipient"), expected_recipient, refund_chain):
                raise ValueError("退款地址不是本次钱包")

    known_targets = set()
    def collect_contracts(node):
        if isinstance(node, dict):
            for value in node.values():
                collect_contracts(value)
        elif isinstance(node, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", node):
            known_targets.add(node.lower())
    collect_contracts(network.get("contracts") or {})
    collect_contracts(network.get("protocol") or {})
    order_id = protocol["orderId"][2:].lower()
    transaction_count, total_value, deposits = 0, 0, 0
    for step in quote.get("steps") or []:
        if step.get("kind") != "transaction":
            raise ValueError("此路线要求额外离线签名，当前不开放该授权方式")
        for item in step.get("items") or []:
            if item.get("status") == "complete":
                raise ValueError("新报价包含已执行步骤")
            transaction_count += 1
            data = item.get("data") or {}
            check = item.get("check") or {}
            if check and (check.get("method") != "GET" or not re.fullmatch(r"/intents/status(?:/v[123])?\?requestId=0x[0-9a-fA-F]{64}", str(check.get("endpoint", "")))):
                raise ValueError("报价包含不可信状态回调")
            if source["chainId"] == SOLANA:
                validate_solana_instructions(data, intent, network, protocol)
                deposits += 1
                continue
            if chain_id(data.get("chainId")) != source["chainId"] or not same_address(data.get("from"), intent["sender"], source["chainId"]):
                raise ValueError("待签交易的钱包或链不一致")
            destination = address(data.get("to"), source["chainId"])
            calldata = str(data.get("data") or "0x").lower()
            value = int(str(data.get("value") or "0"), 16) if str(data.get("value") or "").startswith("0x") else int(data.get("value") or 0)
            total_value += value
            if value < 0:
                raise ValueError("原生币扣款不能为负数")
            if calldata.startswith("0x095ea7b3"):
                if len(calldata) != 138 or destination != source["address"] or value:
                    raise ValueError("代币授权对象不正确")
                spender = "0x" + calldata[34:74]
                if spender != deposit_contract.lower():
                    raise ValueError("授权地址不属于所选路由")
                # Limit even a provider-requested unlimited approval to this input amount.
                data["data"] = calldata[:74] + format(int(intent["amount"]), "064x")
            else:
                expected = ("0x49290c1c" + intent["sender"][2:].lower().rjust(64, "0") + order_id) if native(source) else (
                    "0xe8017952" + intent["sender"][2:].lower().rjust(64, "0") + source["address"][2:].lower().rjust(64, "0") + format(int(intent["amount"]), "064x") + order_id)
                if destination != deposit_contract.lower() or calldata != expected or value != (int(intent["amount"]) if native(source) else 0):
                    raise ValueError("待签交易的合约、函数、币种、数量或订单不匹配")
                deposits += 1
    if deposits != 1 or not 1 <= transaction_count <= 3 or total_value > (int(intent["amount"]) if native(source) else 0):
        raise ValueError("交易步骤或原生币扣款超过本次范围")
    request_ids = [step.get("requestId") for step in quote.get("steps", []) if step.get("requestId")]
    if not request_ids or not all(re.fullmatch(r"0x[0-9a-fA-F]{64}", value) for value in request_ids):
        raise ValueError("报价缺少可跟踪订单号")
    return quote


class MonitorBuyService:
    def __init__(self, path: Path, security_check=None, ai_review=None):
        self.path = Path(path)
        self.security_check = security_check
        self.ai_review = ai_review  # Legacy constructor compatibility only; never used for execution.
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="monitor-buy")
        # Leaf reads only: never submit coordinators that wait on this same pool.
        self.read_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="buy-read")
        self.warm_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wallet-balance-warm")
        self.balance_reads = BalanceReadCache()
        self.warm_pending = set()
        self.warm_seen = {}
        self.prepare_pending = set()
        self.prepare_seen = {}
        self.gate = threading.BoundedSemaphore(2)
        self.preflight_gate = threading.BoundedSemaphore(2)
        self.lock = threading.RLock()
        self.cache = {}
        self.http_local = threading.local()
        self.ready = False
        self.session = SessionBuyService(self.path.with_name("monitor_buy_session.sqlite"),
                                         self.path.with_name("monitor_buy_session_key.bin"))
        self.session_maintenance_thread = None
        self.session_maintenance_stop = threading.Event()
        from monitor_identity import MonitorIdentityRegistry
        self.identities = MonitorIdentityRegistry(self.path.with_name("monitor_identities.sqlite"), self.resolve)

    def choose_cross_chain(self, quotes):
        """Deterministic selection; external prose and models have no authority."""
        networks, candidates, binding = self.networks(), [], None
        for quote in quotes:
            intent = quote["_intent"]
            self.validate_execution_quote(quote, intent, networks)
            current = (intent["target"]["chainId"], intent["target"]["address"],
                       intent["target"]["decimals"], intent["recipient"],
                       number(intent["amountUsd"]), intent["slippageBps"])
            if binding is not None and current != binding:
                raise ValueError("候选路线不属于同一买入意图")
            binding = current
            if intent["source"]["chainId"] == intent["target"]["chainId"]:
                raise ValueError("跨链候选混入同链交易")
            if quote["_expires"] - time.time() >= 10:
                candidates.append(quote)
        if not candidates:
            raise ValueError("跨链报价已过期，请重新报价")
        def score(quote):
            # Output already includes route fees; do not subtract them twice.
            gas = number((quote.get("fees", {}).get("gas") or {}).get("amountUsd", 0))
            paid = number(quote["_intent"]["amountUsd"])
            if gas < 0 or paid <= 0:
                raise ValueError("路线费用或付款金额无效")
            eta = number(quote["details"].get("timeEstimate", 86400))
            return (-number(quote["details"]["currencyOut"]["minimumAmount"]) / (paid + gas),
                    eta if eta > 0 else Decimal(86400), gas, digest(quote))
        best = min(candidates, key=score)
        return best, {"status": "rule-checked", "method": "deterministic-v1",
                      "reason": "规则校验通过，优先较高最低到账和较低费用",
                      "reviewedAt": int(time.time()*1000), "candidateCount": len(candidates)}

    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=8)
        conn.row_factory = sqlite3.Row
        with self.lock:
            if not self.ready:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, created REAL, expires REAL, state TEXT, data TEXT NOT NULL)")
                conn.commit()
                self.ready = True
        return conn

    def http_session(self):
        # Keep-alive per worker, bounded pools and no automatic request retries.
        if not getattr(self.http_local, "session", None):
            session = requests.Session()
            session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=2, max_retries=0))
            self.http_local.session = session
        return self.http_local.session

    def http(self, path, payload=None, params=None):
        method = "POST" if payload is not None else "GET"
        headers = {"Accept": "application/json", "User-Agent": "xingyunshe-monitor-buy/1"}
        if os.getenv("RELAY_API_KEY"):
            headers["x-api-key"] = os.environ["RELAY_API_KEY"]
        response = self.http_session().request(method, RELAY + path, json=payload, params=params, headers=headers, timeout=(4, 16), allow_redirects=False)
        if not response.ok:
            raise ValueError(f"路由服务暂不可用（HTTP {response.status_code}），未提交交易")
        if len(response.content) > 4_000_000:
            raise ValueError("路由返回数据过大")
        return response.json()

    def binance_adapter(self):
        # Separate keep-alive connection per worker; never share mutable sessions.
        if not getattr(self.http_local, "binance_client", None):
            self.http_local.binance_client = BinanceWeb3Client()
        return BinanceBuyAdapter(self.http_local.binance_client, self.rpc)

    def validate_execution_quote(self, quote, intent, networks):
        provider = quote.get("provider", "relay")
        if provider == BINANCE_PROVIDER:
            return validate_binance_quote(quote, intent)
        if provider != "relay":
            raise ValueError("未知交易服务，已停止")
        return validate_quote(quote, intent, networks)

    def networks(self):
        with self.lock:
            cached = self.cache.get("networks")
            if cached and time.time() - cached[0] < 300:
                return cached[1]
        rows = self.http("/chains").get("chains", [])
        result = {int(row["id"]): row for row in rows if (row.get("vmType") == "evm" or
            (row.get("vmType") == "svm" and int(row["id"]) == SOLANA)) and not row.get("disabled")}
        if not result:
            raise ValueError("未获取到可用公链")
        with self.lock:
            self.cache["networks"] = (time.time(), result)
        return result

    def tokens(self, network):
        result = []
        for raw in [network["currency"], *(network.get("erc20Currencies") or [])]:
            is_native = raw is network["currency"]
            # The chain directory is the bounded allowlist used for wallet
            # discovery. Never scan arbitrary token contracts or unbridgeable
            # directory entries merely because they appeared in a wallet.
            if not is_native and raw.get("supportsBridging") is not True:
                continue
            try:
                token = {"chainId": int(network["id"]), "address": address(raw.get("address"), int(network["id"])),
                         "symbol": str(raw["symbol"]), "name": str(raw.get("name") or raw["symbol"]), "decimals": int(raw["decimals"])}
            except (ValueError, KeyError, TypeError):
                if is_native:
                    raise ValueError("该网络的原生币元数据不能安全解析") from None
                continue
            if not 0 <= token["decimals"] <= 36:
                continue
            if token["address"] not in {item["address"] for item in result}:
                result.append({**token, "fundingKind": funding_kind(token),
                               "directorySource": "relay-chains"})
            if len(result) >= 1 + MAX_DIRECTORY_TOKENS_PER_CHAIN:
                break
        return result

    def capabilities(self):
        networks = self.networks()
        return {"ok": True, "slippageBps": 2000, "autoSlippage": True, "maxOrderUsd": float(os.getenv("NEWS_TRADE_MAX_ORDER_USDT", str(DEFAULT_MAX_ORDER_USDT))),
                 "defaultChains": [item for item in DEFAULT_CHAINS if item in networks], "provider": "Binance Web3 / Relay 跨链",
                 "sameChainProvider": BINANCE_PROVIDER, "crossChainProvider": "relay",
                 "routeSelection": "deterministic-v1", "executionUsesAI": False,
                 "agenticWallet": agentic_wallet_capabilities(),
                 "binanceExecutionChains": [1, 56, 8453], "sessionBuy": self.session_status(),
                "networks": [{"id": key, "name": row.get("displayName") or row["name"], "vmType": row["vmType"],
                              "currency": row["currency"], "tokens": self.tokens(row), "rpcUrl": row["httpRpcUrl"],
                              "explorerUrl": row.get("explorerUrl", "")} for key, row in networks.items()]}

    def session_status(self, _payload=None):
        try:
            return self.session.status()
        except Exception as exc:
            return {"ok": False, "mode": SESSION_MODE, "active": False, "configured": False,
                    "keyCreated": False, "privateKeyExposed": False,
                    "maxOrderUsdt": str(SESSION_MAX_ORDER_USDT),
                    "maxSessionUsdt": str(SESSION_MAX_TOTAL_USDT),
                    "reason": str(exc)[:160] if isinstance(exc, (ValueError, RuntimeError)) else "本机会话保险箱不可用"}

    def session_plan(self, _payload=None):
        status = self.session_status()
        stablecoins = {str(chain): [{"address": address, "symbol": metadata[0], "decimals": metadata[1]}
            for address, metadata in rows.items()] for chain, rows in SESSION_STABLECOINS.items()}
        return {"ok": True, "status": status, "roleKey": SESSION_ROLE_KEY,
                "allowanceKey": SESSION_ALLOWANCE_KEY, "routerAddress": SESSION_ROUTER,
                "maxOrderUsdt": str(SESSION_MAX_ORDER_USDT), "maxSessionUsdt": str(SESSION_MAX_TOTAL_USDT),
                "durationSeconds": 86400, "refill": False, "supportedChains": [1, 56, 8453],
                "supportedStablecoins": stablecoins,
                "permissions": ["固定稳定币精确授权给固定币安路由", "固定路由同链买入且收币到 Safe", "仅撤销本会话角色"],
                "setupRequiresWalletConfirmation": True}

    def session_create(self, _payload=None):
        return self.session.create()

    def session_configure(self, payload):
        key = chain_id(payload.get("chainId"))
        networks = self.networks()
        if key not in networks:
            raise ValueError("设置的公链当前不可用")
        snapshot = self.session_role_snapshot(key, payload.get("rolesAddress"))
        return self.session.verify_and_configure({**payload, "_roleSnapshot": snapshot},
                                                 networks[key], self.session_rpc)

    def session_role_snapshot(self, chain, roles_address):
        prefix = ROLES_CHAIN_PREFIX.get(int(chain))
        roles = address(roles_address, int(chain))
        if not prefix:
            raise ValueError("该链没有可核验的 Roles 权限索引")
        query = """query Role($id: ID!) { role(id: $id) { key members { member { address } } targets { address clearance executionOptions functions { selector executionOptions wildcarded condition { id json } } } lastUpdate } }"""
        try:
            response = self.http_session().post(ROLES_SUBGRAPH, json={"query": query,
                "variables": {"id": f"{prefix}:{roles}:{SESSION_ROLE_KEY}"}, "operationName": "Role"},
                headers={"Accept": "application/json", "User-Agent": "xingyunshe-monitor-buy/1"},
                timeout=(4, 12), allow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 1_000_000:
                raise ValueError("Roles 权限清单返回过大")
            payload = response.json()
        except (OSError, requests.RequestException, json.JSONDecodeError) as exc:
            raise ValueError("Roles 最新权限清单暂时无法核验，免确认模式保持关闭") from exc
        if payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise ValueError("Roles 最新权限清单暂时无法核验，免确认模式保持关闭")
        role = payload["data"].get("role")
        if not isinstance(role, dict):
            raise ValueError("官方索引尚未找到这个角色，请等待链上设置同步")
        return role

    def current_session_role_snapshot(self, chain_id_value):
        config = self.session.require_active(int(chain_id_value))
        return self.session_role_snapshot(int(chain_id_value), config["chain"]["rolesAddress"])

    def _session_maintenance_once(self):
        try:
            return self.session.revoke_if_due(self.networks(), self.session_rpc)
        except Exception as exc:
            print(f"Session buy revoke pending: {str(exc)[:160]}", flush=True)
            return self.session_status()

    def session_disable(self, _payload=None):
        status = self.session.disable_local("用户已停用免确认买入")
        try:
            self.pool.submit(self._session_maintenance_once)
        except RuntimeError:
            pass
        return status

    def start_session_maintenance(self):
        if self.session_maintenance_thread and self.session_maintenance_thread.is_alive():
            return
        def run():
            while not self.session_maintenance_stop.wait(30):
                status = self.session_status()
                if status.get("configured") and not status.get("active") and status.get("revokeState") in {"scheduled", "pending", "failed", "submitted"}:
                    self._session_maintenance_once()
        self.session_maintenance_thread = threading.Thread(target=run, name="session-buy-revoke", daemon=True)
        self.session_maintenance_thread.start()

    def resolve(self, payload):
        key = chain_id(payload.get("chainId") or payload.get("chain") or payload.get("network"))
        fixed_identity_network = IDENTITY_EVM_NETWORKS.get(key)
        network = fixed_identity_network or self.networks().get(key)
        if not network:
            raise ValueError("交易服务尚不支持该链，不能自动跨链买入")
        contract = address(payload.get("address") or payload.get("contractAddress"), key)
        if payload.get("kind") == "nft":
            raise ValueError("NFT 不适用代币兑换买入")
        cache_key = ("target", key, contract)
        with self.lock:
            cached = self.cache.get(cache_key)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        if fixed_identity_network:
            result = self.resolve_evm_metadata(network, key, contract)
            with self.lock:
                if len(self.cache) > 300:
                    networks_cache = self.cache.get("networks")
                    self.cache = {"networks": networks_cache} if networks_cache else {}
                self.cache[cache_key] = (time.time(), result)
            return result
        listed = self.tokens(network)
        matches = [item for item in listed if item["address"] == contract]
        if not matches:
            rows = self.http("/currencies/v2", {"chainIds": [key], "address": contract, "limit": 10})
            matches = [item for item in rows if chain_id(item.get("chainId")) == key and same_address(item.get("address"), contract, key)]
        if len(matches) != 1:
            raise ValueError("无法核实目标币合约元数据，请先核对链和合约")
        item = matches[0]
        result = {"chainId": key, "address": contract, "symbol": str(item["symbol"])[:60],
                  "name": str(item.get("name") or item["symbol"])[:120], "decimals": int(item["decimals"]),
                  "chainLabel": network.get("displayName") or network["name"], "vmType": network["vmType"]}
        with self.lock:
            if len(self.cache) > 300:
                networks_cache = self.cache.get("networks")
                self.cache = {"networks": networks_cache} if networks_cache else {}
            self.cache[cache_key] = (time.time(), result)
        return result

    def resolve_evm_metadata(self, network, chain, contract):
        calls = {
            "decimals": self.read_pool.submit(
                self.rpc, network, "eth_call", [{"to": contract, "data": "0x313ce567"}, "latest"]),
            "symbol": self.read_pool.submit(
                self.rpc, network, "eth_call", [{"to": contract, "data": "0x95d89b41"}, "latest"]),
        }
        try:
            values = {name: future.result() for name, future in calls.items()}
            decimals_raw = str(values["decimals"] or "")
            if not re.fullmatch(r"0x[0-9a-fA-F]{1,64}", decimals_raw):
                raise ValueError("代币精度元数据格式不正确")
            decimals = int(decimals_raw, 16)
            if not 0 <= decimals <= 36:
                raise ValueError("代币精度元数据不可信")
            symbol = decode_abi_text(values["symbol"])
        except Exception:
            for future in calls.values():
                future.cancel()
            raise
        return {"chainId": chain, "address": contract, "symbol": symbol, "name": symbol,
                "decimals": decimals, "chainLabel": network.get("displayName") or network["name"],
                "vmType": "evm", "identityProvider": "链上合约只读核验"}

    def rpc(self, network, method, params):
        if method == "eth_sendRawTransaction":
            raise ValueError("签名交易必须使用单节点、单次广播边界")
        chain = int(network.get("id") or 0)
        urls = list(dict.fromkeys([*TRUSTED_RPC_URLS.get(chain, ()), network["httpRpcUrl"]]))
        # RPC URLs only come from fixed project endpoints or the Relay
        # directory, never request bodies. A failed provider is read-only and
        # may fall through; transaction submission is never performed here.
        for url in urls:
            try:
                parsed = urlparse(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    continue
                if any(not ipaddress.ip_address(result[4][0]).is_global
                       for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443)):
                    continue
                response = self.http_session().post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=(3, 7), allow_redirects=False)
                response.raise_for_status()
                data = response.json()
                error = data.get("error")
                if not error and "result" in data:
                    return data["result"]
                if method in {"eth_call", "eth_estimateGas"} and isinstance(error, dict):
                    message = str(error.get("message") or "").lower()
                    if "revert" in message or "vm execution error" in message:
                        raise RpcCallReverted("链上合约明确拒绝了本次模拟调用")
            except RpcCallReverted:
                raise
            except (OSError, requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
                continue
        raise ValueError("公链 RPC 查询暂不可用")

    def session_rpc(self, network, method, params):
        if method != "eth_sendRawTransaction":
            return self.rpc(network, method, params)
        chain = int(network.get("id") or 0)
        urls = list(dict.fromkeys([*TRUSTED_RPC_URLS.get(chain, ()), network["httpRpcUrl"]]))
        if not urls:
            raise ValueError("没有可用的免确认交易节点")
        url = urls[0]
        parsed = urlparse(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(not ipaddress.ip_address(result[4][0]).is_global
                       for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443))):
            raise ValueError("免确认交易节点配置不安全")
        # Exactly one HTTP request to one fixed node. A transport timeout is
        # ambiguous and must never fall through to another provider.
        response = self.http_session().post(url, json={"jsonrpc": "2.0", "id": 1,
            "method": method, "params": params}, timeout=(3, 15), allow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if data.get("error") or "result" not in data:
            raise ValueError("节点拒绝了免确认交易")
        return data["result"]

    def token_balance(self, network, token, owner):
        if token["chainId"] == SOLANA:
            if native(token):
                return int(self.rpc(network, "getBalance", [owner, {"commitment": "confirmed"}])["value"])
            rows = self.rpc(network, "getTokenAccountsByOwner", [owner, {"mint": token["address"]}, {"encoding": "jsonParsed", "commitment": "confirmed"}])["value"]
            return sum(int(row["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for row in rows)
        if native(token):
            return int(self.rpc(network, "eth_getBalance", [owner, "latest"]), 16)
        return int(self.rpc(network, "eth_call", [{"to": token["address"], "data": "0x70a08231" + owner[2:].rjust(64, "0")}, "latest"]), 16)

    def warm_balances(self, payload):
        owners = payload.get("wallets") or {}
        wallets = {namespace: address(owners[namespace], SOLANA if namespace == "solana" else 1)
                   for namespace in ("evm", "solana") if owners.get(namespace)}
        if not wallets:
            return {"ok": True, "queued": 0}
        preferred = chain_id(payload.get("preferredChain") or 56)
        chains = list(dict.fromkeys([preferred, *DEFAULT_CHAINS]))[:16]
        provider = str(payload.get("walletProvider") or "")[:80]
        work = [(preferred, "stable"), (preferred, "native")]
        work += [(key, kind) for kind in ("stable", "native") for key in chains if key != preferred]
        queued = 0
        for key, kind in work:
            owner = wallets.get("solana" if key == SOLANA else "evm")
            if not owner:
                continue
            identity = (provider, key, owner, kind)
            with self.lock:
                if identity in self.warm_pending or len(self.warm_pending) >= 32 or time.monotonic() - self.warm_seen.get(identity, -100) < 15:
                    continue
                self.warm_pending.add(identity)
                self.warm_seen[identity] = time.monotonic()
                while len(self.warm_seen) > 128:
                    self.warm_seen.pop(next(iter(self.warm_seen)))
            def run(identity=identity, key=key, kind=kind):
                try:
                    self.balances({"wallets": wallets, "walletProvider": provider, "sourceChains": [key]}, token_kind=kind, parallel=False)
                except Exception:
                    pass  # Foreground checks report errors; prefetch never prompts or sends alerts.
                finally:
                    with self.lock:
                        self.warm_pending.discard(identity)
            try:
                self.warm_pool.submit(run)
                queued += 1
            except RuntimeError:
                with self.lock:
                    self.warm_pending.discard(identity)
        return {"ok": True, "queued": queued, "maxAgeSeconds": 20}

    def balances(self, payload, *, token_kind="all", parallel=True):
        networks = self.networks()
        owners = payload.get("wallets") or {}
        chains = list(dict.fromkeys(chain_id(item) for item in payload.get("sourceChains", DEFAULT_CHAINS)))
        if not 1 <= len(chains) <= 16:
            raise ValueError("请选择 1–16 条付款链，避免大量余额请求拖慢服务")
        def one(key):
            network = networks.get(key)
            if not network:
                return [], [f"Chain {key} 暂不支持"]
            owner = owners.get("solana" if key == SOLANA else "evm")
            if not owner:
                return [], []
            owner = address(owner, key)
            tokens = self.tokens(network)
            rows, errors = [], []
            provider = str(payload.get("walletProvider") or "")[:80]
            def balance_of(token):
                return self.balance_reads.get(("balance", provider, key, owner, token["address"]),
                    lambda: self.token_balance(network, token, owner))
            # RPC balance and market price are independent network reads.
            price_read = self.read_pool.submit(self.token_price, key, tokens[0]["address"])
            try:
                gas_balance = balance_of(tokens[0])
            except Exception:
                price_read.cancel()
                return [], [f"{network.get('displayName', key)} 原生币余额暂不可读"]
            if token_kind not in {"all", "stable", "native", "other"}:
                raise ValueError("付款币筛选方式无效")
            selected = [token for token in tokens if token_kind == "all" or funding_kind(token) == token_kind]
            for token in selected:
                try:
                    balance = gas_balance if native(token) else balance_of(token)
                    if balance <= 0:
                        continue
                    price = price_read.result(timeout=25) if native(token) else self.token_price(key, token["address"])
                    if price <= 0:
                        raise ValueError("价格不可用")
                    native_price = price if native(token) else price_read.result(timeout=25)
                    if native_price <= 0:
                        raise ValueError("原生币价格不可用")
                    rows.append({**token, "owner": owner, "balance": str(balance), "priceUsd": str(price),
                                 "balanceFormatted": str(Decimal(balance) / 10 ** token["decimals"]),
                                 "balanceUsd": str(Decimal(balance) / 10 ** token["decimals"] * price),
                                 "nativeBalance": str(gas_balance), "nativePriceUsd": str(native_price), "chainLabel": network.get("displayName") or network["name"]})
                except Exception:
                    errors.append(f"{network.get('displayName', key)} · {token['symbol']} 余额或价格暂不可读")
            price_read.cancel()  # No-op for completed reads; discard unused queued work.
            return rows, errors
        rows, errors = [], []
        results = (future.result() for future in as_completed([self.pool.submit(one, key) for key in chains])) if parallel else (one(key) for key in chains)
        for values, failures in results:
            rows.extend(values)
            errors.extend(failures)
        return {"ok": True, "items": rows, "errors": errors, "checkedChains": chains, "checkedAt": int(time.time() * 1000)}

    def security(self, target):
        if native(target):
            return {"verified": True, "hardBlocked": False, "label": "已核对公链原生币"}
        if self.security_check:
            return self.security_check({"chain": "sol" if target["chainId"] == SOLANA else self.networks()[target["chainId"]]["name"],
                                        "chainId": "501" if target["chainId"] == SOLANA else str(target["chainId"]),
                                        "contractAddress": target["address"], "symbol": target["symbol"]}, synchronous=True)
        return {"verified": False, "hardBlocked": False, "label": "合约安全检测尚未完成"}

    def token_price(self, key, contract):
        return number(self.balance_reads.get(("price", key, contract),
            lambda: self.http("/currencies/token/price", params={
            "address": contract, "chainId": key})["price"], max_age=15))

    def usdt_price(self):
        return self.token_price(1, "0xdac17f958d2ee523a2206206994597c13d831ec7")

    def prepare(self, payload):
        """Bounded, read-only target preparation. Never creates an order."""
        target = self.identities.require(payload.get("target") or {})
        key = (target["chainId"], target["address"])
        with self.lock:
            if key in self.prepare_pending or len(self.prepare_pending) >= 2 or time.monotonic() - self.prepare_seen.get(key, -100) < 15:
                return {"ok": True, "queued": False}
            self.prepare_pending.add(key)
            self.prepare_seen[key] = time.monotonic()
            while len(self.prepare_seen) > 128:
                self.prepare_seen.pop(next(iter(self.prepare_seen)))
        def run():
            try:
                # The existing security service owns its cache and risk expiry.
                self.security(target)
                self.usdt_price()
            except Exception:
                pass  # A real foreground quote reports failures, not fake zeroes.
            finally:
                with self.lock:
                    self.prepare_pending.discard(key)
        try:
            self.pool.submit(run)
        except RuntimeError:
            with self.lock:
                self.prepare_pending.discard(key)
            return {"ok": True, "queued": False}
        return {"ok": True, "queued": True}

    def quote(self, payload, on_progress=None):
        if not self.gate.acquire(blocking=False):
            raise ValueError("已有买入请求正在报价，请稍后重试")
        preparation = []
        quote_started = time.monotonic()
        try:
            def progress(percent, stage):
                if on_progress:
                    on_progress({"percent": percent, "stage": stage})
            # Local, persisted monitor identity only. No CA lookup or symbol search
            # is permitted in the critical quote path.
            target = self.identities.require(payload.get("target") or {})
            progress(12, "获取报价")
            amount_usdt = number(payload.get("amountUsdt"))
            session_mode = payload.get("executionMode") == SESSION_MODE
            order_limit = SESSION_MAX_ORDER_USDT if session_mode else number(os.getenv("NEWS_TRADE_MAX_ORDER_USDT", str(DEFAULT_MAX_ORDER_USDT)))
            if not 0 < amount_usdt <= order_limit:
                raise ValueError("请输入上限内的 USDT 数量")
            slip = int(payload.get("slippageBps", 2000))
            if not 0 <= slip <= 2000:
                raise ValueError("滑点上限不能超过已确认的 20%")
            progress(22, "已复用监控时核验的链与 CA")
            networks = self.networks()
            session_config = self.session.require_active(target["chainId"], amount_usdt) if session_mode else None
            if session_mode:
                chain_config = session_config["chain"]
                owners = {"evm": chain_config["safeAddress"]}
                payload = {**payload, "wallets": owners, "walletProvider": SESSION_MODE,
                           "sourceChains": [target["chainId"]]}
            else:
                chain_config = None
                owners = payload.get("wallets") or {}
            recipient = address(owners.get("solana" if target["chainId"] == SOLANA else "evm"), target["chainId"])
            chains = list(dict.fromkeys(chain_id(item) for item in payload.get("sourceChains", DEFAULT_CHAINS)))
            if not 1 <= len(chains) <= 16:
                raise ValueError("请选择 1–16 条付款链，避免大量余额请求拖慢服务")
            progress(32, "同步读取价格、余额与合约安全状态")
            # Coordinators run on quote workers; leaf price reads use read_pool.
            # No route construction starts until the security result is checked.
            price_read = self.pool.submit(self.usdt_price)
            security_read = self.pool.submit(self.security, target)
            preparation.extend([price_read, security_read])
            first_balance = None
            if target["chainId"] in chains:
                first_balance = self.pool.submit(self.balances,
                    {**payload, "sourceChains": [target["chainId"]]}, token_kind="stable", parallel=False)
                preparation.append(first_balance)
            check = security_read.result(timeout=25)
            if check.get("hardBlocked"):
                raise ValueError("目标合约命中高风险：" + "、".join(check.get("hardBlockReasons") or ["禁止买入"]))
            if session_mode and not check.get("verified"):
                raise ValueError("免确认买入只允许安全检测已完成的代币")
            # Live USDT price: never assume stablecoins remain pegged to $1.
            usdt_price = price_read.result(timeout=25)
            if not Decimal("0.8") <= usdt_price <= Decimal("1.2"):
                raise ValueError("USDT 参考价格异常，已停止自动换算")
            amount_usd = amount_usdt * usdt_price
            if not 0 < amount_usd or (not session_mode and amount_usd > order_limit):
                raise ValueError("金额必须大于 0 且不超过当前单笔上限")
            funds = {"items": [], "errors": [], "checkedChains": []}
            errors, available = [], []
            funding_fallback_reasons = []
            session_gas_balance = (int(self.rpc(networks[target["chainId"]], "eth_getBalance",
                [chain_config["sessionAddress"], "latest"]), 16) if session_mode else None)
            def get_route(source):
                if session_mode:
                    source = {**source, "nativeBalance": session_gas_balance}
                raw_amount = units(amount_usd, source["priceUsd"], source["decimals"])
                if raw_amount <= 0:
                    raise ValueError("金额低于代币最小单位")
                intent = {"source": source, "target": target, "sender": source["owner"], "recipient": recipient,
                          "amount": str(raw_amount), "amountUsd": str(amount_usd), "amountUsdt": str(amount_usdt),
                          "usdtPriceUsd": str(usdt_price), "fundingPriceUsd": source["priceUsd"], "slippageBps": slip,
                          "autoSlippage": payload.get("autoSlippage") is True,
                          "executionMode": SESSION_MODE if session_mode else "wallet",
                          "forceExactApproval": session_mode}
                started = time.time()
                simulated_at = 0
                if source["chainId"] == target["chainId"]:
                    # All NEW same-chain orders use Binance. Relay is only a bridge
                    # path, never a silent fallback for a Binance swap failure.
                    quote = self.binance_adapter().build(intent, networks[source["chainId"]])
                    expires = quote.pop("_expires")
                    quote.pop("_intent", None)
                    simulated_at = quote.pop("_simulatedAt", 0)
                else:
                    quote = self.http("/quote/v2", {"user": source["owner"], "recipient": recipient,
                    "originChainId": source["chainId"], "destinationChainId": target["chainId"],
                    "originCurrency": source["address"], "destinationCurrency": target["address"],
                    "amount": str(raw_amount), "tradeType": "EXACT_INPUT", "slippageTolerance": str(slip),
                    "latePaymentSlippageTolerance": "0", "usePermit": False, "useDepositAddress": False,
                    "forceSolverExecution": True, "includeProtocolData": True, "overridePriceImpact": False,
                        "refundTo": source["owner"], "appFees": [], "ttl": 300})
                    expires = started + 60
                self.validate_execution_quote(quote, intent, networks)
                input_usd = number(quote["details"]["currencyIn"].get("amountUsd", 0))
                if input_usd <= 0 or abs(input_usd-amount_usd) > amount_usd * Decimal("0.03"):
                    raise ValueError("付款币价格变化超过 3%，USDT 换算失效，请重新报价")
                gas = int((quote.get("fees", {}).get("gas") or {}).get("amount", 0))
                # Native reserve includes approval gas, not only the deposit estimate.
                gas = max(int((quote.get("fees", {}).get("gas") or {}).get("reserveAmount", gas * 2)), 3_000_000 if source["chainId"] == SOLANA else 0)
                if int(source["nativeBalance"]) < gas + (raw_amount if native(source) else 0):
                    raise ValueError("原生币不足以同时支付买入金额及燃料费")
                quote["_intent"] = intent
                quote["_expires"] = expires
                quote["_simulatedAt"] = simulated_at
                return quote
            tier_kinds = ("stable", "native", "other", "stable", "native", "other")
            tier_stages = ("读取同链稳定币余额", "读取目标链原生币余额", "查询目标链其他可兑换币",
                           "读取其他链稳定币余额", "检查跨链原生币路线", "查询其他链可跨链币")
            tier_progress = (42, 52, 62, 70, 78, 84)
            route_progress = (49, 57, 67, 75, 82, 88)
            for tier in range(6):
                # A usable same-chain stable route never waits for unrelated RPC endpoints.
                scope = ([target["chainId"]] if target["chainId"] in chains else []) if tier < 3 else [key for key in chains if key != target["chainId"]]
                progress(tier_progress[tier], tier_stages[tier])
                if scope:
                    result = first_balance.result(timeout=25) if tier == 0 and first_balance is not None else self.balances(
                        {**payload, "sourceChains": scope}, token_kind=tier_kinds[tier])
                    funds["items"].extend(result["items"])
                    funds["errors"].extend(result["errors"])
                    funds["checkedChains"] = list(dict.fromkeys([*funds["checkedChains"], *result["checkedChains"]]))
                candidates = [item for item in funds["items"] if number(item["balanceUsd"]) >= amount_usd
                    and (item["chainId"] != SOLANA or native(item))
                    and not (item["chainId"] == target["chainId"] and item["address"] == target["address"])]
                if session_mode:
                    candidates = [item for item in candidates if item["chainId"] == target["chainId"]
                                  and item["address"] == chain_config["stableAddress"]]
                selected = [item for item in candidates if funding_tier(item, target["chainId"]) == tier]
                progress(route_progress[tier], "获取买入路线" if selected else "检查其他付款方式")
                futures = {self.pool.submit(get_route, item): item for item in selected}
                for future in as_completed(futures):
                    try:
                        available.append(future.result())
                    except Exception as exc:
                        source = futures[future]
                        provider = "币安 Web3" if source["chainId"] == target["chainId"] else "Relay 跨链"
                        detail = str(exc)[:160] if isinstance(exc, ValueError) else "报价服务暂不可用"
                        errors.append(f"{provider} · {source.get('chainLabel', source['chainId'])} {source['symbol']}：{detail}")
                if session_mode and tier == 0 and not available:
                    # The no-confirmation role is deliberately limited to its
                    # configured stablecoin. Never broaden those permissions
                    # to native, arbitrary ERC-20 or bridge calls implicitly.
                    break
                if tier in {0, 1} and scope and not available and not session_mode:
                    # Ordinary wallet mode may use the next funding tier, but
                    # every departure from the preferred funding order is shown.
                    read_errors = list(dict.fromkeys(result.get("errors") or []))[:4]
                    if selected:
                        funding_fallback_reasons.append(
                            "同链稳定币余额足够，但稳定币兑换路线未通过" if tier == 0
                            else "目标链原生币余额足够，但原生币兑换路线未通过")
                    elif read_errors:
                        funding_fallback_reasons.append(
                            "同链稳定币余额或价格暂时无法完整核对" if tier == 0
                            else "目标链原生币余额或价格暂时无法完整核对")
                    else:
                        funding_fallback_reasons.append(
                            "同链稳定币余额不足" if tier == 0 else "目标链原生币余额不足")
                if available:
                    break
            if not available:
                reasons = list(dict.fromkeys(errors))[:4]
                read_errors = list(dict.fromkeys(funds["errors"]))[:4]
                if reasons:
                    message = "没有可用买入路线：" + "；".join(reasons)
                    if amount_usdt < Decimal("5"):
                        message += "。本次金额较小，路线可能暂时不满足成交或费用保护；币安只读报价会自动重试 1 次，也可稍后重报或适当提高金额"
                    raise FundingUnavailable("no-valid-route", message, reasons + read_errors)
                if read_errors:
                    raise FundingUnavailable("balance-read-failed", "部分付款链余额或价格读取失败：" + "；".join(read_errors), read_errors)
                raise FundingUnavailable("insufficient-balance", "所选钱包的可用付款币余额不足，或余额都在目标币中")
            def score(quote):
                out = quote["details"]["currencyOut"]
                gas_usd = number((quote.get("fees", {}).get("gas") or {}).get("amountUsd", 0))
                return Decimal(out["minimumAmount"]) / (amount_usd + gas_usd)
            available.sort(key=score, reverse=True)
            best = available[0]
            review = {"status": "not-needed", "method": "deterministic-v1", "reason": "同链路线已通过规则校验"}
            if best["_intent"]["source"]["chainId"] != target["chainId"]:
                progress(91, "按最低到账、费用与预计用时选择跨链路线")
                best, review = self.choose_cross_chain(available)
            intent, expires = best.pop("_intent"), best.pop("_expires")
            simulated_at = best.pop("_simulatedAt", 0)
            source_tier = funding_tier(intent["source"], target["chainId"])
            funding_fallback = ({"used": True, "from": "same-chain-stable",
                "toSymbol": intent["source"]["symbol"], "toChain": intent["source"].get("chainLabel", ""),
                "toKind": funding_kind(intent["source"]),
                "crossChain": intent["source"]["chainId"] != target["chainId"],
                "reason": "；".join(dict.fromkeys(funding_fallback_reasons))}
                if funding_fallback_reasons and source_tier > 0 else None)
            if expires - time.time() < 10:
                raise ValueError("报价获取过慢，已过期；请重新报价")
            progress(96, "最终复核金额、收款地址与最少到账量")
            self.validate_execution_quote(best, intent, networks)  # Independent final binding check.
            if session_mode:
                role_snapshot = self.current_session_role_snapshot(target["chainId"])
                self.session.require_quote_policy(intent, best, networks[target["chainId"]], self.session_rpc,
                                                  role_snapshot)
            review["quoteHash"] = digest(best)
            record = {"quote": best, "intent": intent, "security": check, "quoteHash": digest(best), "routeReview": review,
                      "preflightAt": simulated_at,
                      "steps": {}, "txHashes": [], "walletProvider": str(payload.get("walletProvider") or "")[:80],
                      "executionMode": SESSION_MODE if session_mode else "wallet",
                      "sessionId": session_config.get("sessionId") if session_config else None,
                      "coverage": {"checkedChains": funds["checkedChains"], "errors": funds["errors"], "validRoutes": len(available)}}
            order_id = secrets.token_urlsafe(32)
            with closing(self.db()) as conn, conn:
                conn.execute("DELETE FROM orders WHERE state='quoted' AND expires<?", (time.time()-3600,))
                conn.execute("INSERT INTO orders VALUES(?,?,?,?,?)", (order_id, time.time(), expires, "quoted", json.dumps(record)))
            return {"ok": True, "orderId": order_id, "expiresAt": int(expires * 1000), "target": target,
                    "provider": best.get("provider", "relay"),
                    "source": intent["source"], "recipient": recipient, "details": best["details"], "fees": best.get("fees", {}),
                    "executionSummary": best.get("execution"),
                    "fundingFallback": funding_fallback,
                    "conversion": {"amountUsdt": str(amount_usdt), "usdtPriceUsd": str(usdt_price), "amountUsd": str(amount_usd),
                                   "fundingPriceUsd": intent["source"]["priceUsd"], "fundingAmountUnits": intent["amount"]},
                    # aiReview is a response-only alias for already-open old pages.
                    "security": check, "coverage": record["coverage"], "routeReview": review, "aiReview": review,
                    "timings": {"totalMs": round((time.monotonic()-quote_started)*1000)},
                    "slippageBps": slip, "quoteHash": record["quoteHash"],
                    "executionMode": record["executionMode"],
                    "sessionBuy": self.session.status() if session_mode else None}
        finally:
            for future in preparation:
                future.cancel()
            self.gate.release()

    def order(self, order_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", str(order_id)):
            raise ValueError("订单标识无效")
        with closing(self.db()) as conn:
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise ValueError("订单不存在")
        return dict(row), json.loads(row["data"])

    def authorize(self, payload):
        with self.lock:
            row, record = self.order(payload.get("orderId"))
            if row["state"] != "quoted" or row["expires"] <= time.time():
                raise ValueError("订单已确认或报价已过期，不可重复提交")
            if payload.get("quoteHash") != record["quoteHash"]:
                raise ValueError("页面报价与待签订单不一致")
            if record.get("executionMode") == SESSION_MODE:
                raise ValueError("免确认订单只能走本机受限会话执行器")
            intent = record["intent"]
            self.identities.require(intent["target"])  # Fast local binding check; no remote CA lookup.
            if intent["source"]["chainId"] != intent["target"]["chainId"]:
                review = record.get("routeReview") or {}
                if (review.get("status") != "rule-checked" or review.get("method") != "deterministic-v1"
                        or review.get("quoteHash") != record["quoteHash"] or digest(record["quote"]) != record["quoteHash"]):
                    raise ValueError("跨链订单缺少匹配的规则校验记录，请重新报价")
            owners = payload.get("wallets") or {}
            if not same_address(owners.get("solana" if intent["source"]["chainId"] == SOLANA else "evm"), intent["sender"], intent["source"]["chainId"]) or not same_address(owners.get("solana" if intent["target"]["chainId"] == SOLANA else "evm"), intent["recipient"], intent["target"]["chainId"]):
                raise ValueError("钱包账户已变化，请重新报价")
            if record["walletProvider"] != payload.get("walletProvider"):
                raise ValueError("执行钱包发生变化")
            if not record["security"].get("verified") and payload.get("acceptUnknownSecurity") is not True:
                raise ValueError("尚未完成安全检测，需明确确认风险后才能继续")
            self.validate_execution_quote(record["quote"], intent, self.networks())
            with closing(self.db()) as conn, conn:
                changed = conn.execute("UPDATE orders SET state='signing' WHERE id=? AND state='quoted'", (row["id"],)).rowcount
                if changed != 1:
                    raise ValueError("订单已被其他窗口确认")
            return {"ok": True, "quote": record["quote"], "intent": intent, "expiresAt": int(row["expires"]*1000)}

    def preflight(self, payload):
        if not self.preflight_gate.acquire(blocking=False):
            raise ValueError("买入前检查正在进行，请稍后重试；未发起钱包请求")
        try:
            return self._preflight(payload)
        finally:
            self.preflight_gate.release()

    def _preflight(self, payload):
        # Read-only simulation of a persisted order, never arbitrary browser calldata.
        row, record = self.order(payload.get("orderId"))
        if row["state"] not in {"signing", "submitted"} or row["expires"] <= time.time():
            raise ValueError("报价已过期或订单不能执行，请查询原订单")
        quote, intent = record["quote"], record["intent"]
        if quote.get("provider") != BINANCE_PROVIDER:
            raise ValueError("此订单不使用币安交易模拟")
        validate_binance_quote(quote, intent)
        adapter = self.binance_adapter()
        adapter.verify_router(self.networks()[intent["source"]["chainId"]])
        # Reuse only this immutable order's very recent successful simulation.
        # Stable approvals have no initial simulation and must simulate after approval.
        simulated_at = record.get("preflightAt", 0)
        if not (len(quote["steps"]) == 1 and 0 <= time.time() - simulated_at <= 10):
            adapter.simulate(quote, intent)
            simulated_at = time.time()
        # Independently execute the exact calldata against current chain state
        # before the browser can claim the one-shot wallet send.
        adapter.chain_simulate(quote, intent, self.networks()[intent["source"]["chainId"]])
        with self.lock:
            row, latest = self.order(row["id"])
            if (row["state"] not in {"signing", "submitted"} or row["expires"] <= time.time()
                    or latest["quoteHash"] != record["quoteHash"]):
                raise ValueError("模拟期间报价已失效，请重新报价")
            latest["preflightAt"] = simulated_at  # Never renew the age of a reused result.
            with closing(self.db()) as conn, conn:
                conn.execute("UPDATE orders SET data=? WHERE id=?", (json.dumps(latest), row["id"]))
        return {"ok": True}

    def claim_step(self, payload):
        # Durable claim precedes wallet invocation. Unknown submission results never replay.
        with self.lock:
            row, record = self.order(payload.get("orderId"))
            if row["state"] not in {"signing", "submitted"} or row["expires"] < time.time():
                raise ValueError("订单不能继续签名，请查询原订单状态")
            fingerprint = str(payload.get("fingerprint") or "")
            allowed = {digest(item["data"]) for step in record["quote"]["steps"] for item in step.get("items", [])}
            if fingerprint not in allowed or fingerprint in record["steps"]:
                raise ValueError("交易步骤不匹配或已经发起，请勿重复扣款")
            if record["quote"].get("provider") == BINANCE_PROVIDER:
                ordered = [digest(step["items"][0]["data"]) for step in record["quote"]["steps"]]
                next_step = next((item for item in ordered if item not in record["steps"]), None)
                if fingerprint != next_step:
                    raise ValueError("交易步骤顺序不匹配，已停止")
                swap = record["quote"]["steps"][-1]["items"][0]["data"]
                if fingerprint == digest(swap) and time.time()-record.get("preflightAt", 0) > 15:
                    raise ValueError("买入前的币安资金变化模拟尚未通过或已过期")
            record["steps"][fingerprint] = "requested"
            with closing(self.db()) as conn, conn:
                conn.execute("UPDATE orders SET data=? WHERE id=?", (json.dumps(record), row["id"]))
            return {"ok": True}

    def record_progress(self, payload):
        with self.lock:
            row, record = self.order(payload.get("orderId"))
            if row["state"] == "quoted":
                raise ValueError("订单尚未确认")
            hashes = [str(value) for value in payload.get("txHashes", [])[:8] if re.fullmatch(r"(?:0x[0-9a-fA-F]{64}|[1-9A-HJ-NP-Za-km-z]{64,90})", str(value))]
            record["txHashes"] = list(dict.fromkeys([*record["txHashes"], *hashes]))[:8]
            with closing(self.db()) as conn, conn:
                conn.execute("UPDATE orders SET state=?,data=? WHERE id=?", ("submitted" if record["txHashes"] else row["state"], json.dumps(record), row["id"]))
            return {"ok": True}

    def _update_session_order(self, order_id, state=None, **values):
        with self.lock:
            row, record = self.order(order_id)
            record.update(values)
            next_state = state or row["state"]
            with closing(self.db()) as conn, conn:
                conn.execute("UPDATE orders SET state=?,data=? WHERE id=?", (next_state, json.dumps(record), order_id))
            return record

    @staticmethod
    def _session_received(receipt, intent, minimum):
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        target, recipient = intent["target"]["address"].lower(), intent["recipient"].lower()
        received = 0
        for log in receipt.get("logs") or []:
            topics = log.get("topics") or []
            if (log.get("removed") or str(log.get("address") or "").lower() != target
                    or len(topics) != 3 or str(topics[0]).lower() != transfer_topic):
                continue
            value = int(log.get("data") or "0x0", 16)
            if "0x" + str(topics[2])[-40:].lower() == recipient:
                received += value
            if "0x" + str(topics[1])[-40:].lower() == recipient:
                received -= value
        return received >= int(minimum), received

    def session_execute(self, payload):
        order_id = payload.get("orderId")
        tx_hashes = []
        reservation_state = "failed"
        try:
            with self.lock:
                row, record = self.order(order_id)
                if row["state"] != "quoted" or row["expires"] <= time.time():
                    raise ValueError("订单已执行或报价已过期，不会重复买入")
                if record.get("executionMode") != SESSION_MODE:
                    raise ValueError("这不是免确认买入订单")
                if payload.get("quoteHash") != record["quoteHash"] or digest(record["quote"]) != record["quoteHash"]:
                    raise ValueError("页面报价与免确认订单不一致")
                if not record.get("security", {}).get("verified"):
                    raise ValueError("免确认买入不能跳过代币安全检测")
                intent = record["intent"]
                self.identities.require(intent["target"])
                networks = self.networks()
                network = networks[intent["source"]["chainId"]]
                self.validate_execution_quote(record["quote"], intent, networks)
                role_snapshot = self.current_session_role_snapshot(intent["source"]["chainId"])
                config = self.session.require_quote_policy(intent, record["quote"], network, self.session_rpc,
                                                           role_snapshot)
                if record.get("sessionId") != config.get("sessionId"):
                    raise ValueError("免确认授权已经更换，请重新报价")
                self.session.reserve(order_id, intent["source"]["chainId"], intent["amountUsdt"])
                record["sessionExecutionStatus"] = "reserved"
                record["sessionStepHashes"] = {}
                with closing(self.db()) as conn, conn:
                    changed = conn.execute("UPDATE orders SET state='signing',data=? WHERE id=? AND state='quoted'",
                                           (json.dumps(record), order_id)).rowcount
                    if changed != 1:
                        raise ValueError("订单已被其他窗口执行，不会重复买入")
            chain = config["chain"]
            swap_hash = None
            for step in record["quote"]["steps"]:
                item = step["items"][0]
                if step["id"] == "swap":
                    # Approval has changed Safe state, so simulate the original
                    # pinned Binance call again immediately before signing.
                    self.preflight({"orderId": order_id})
                fingerprint = digest(item["data"])
                self.claim_step({"orderId": order_id, "fingerprint": fingerprint})
                self.session.mark_reservation(order_id, "broadcasting")
                tx_hash = self.session.send_call(network, self.session_rpc, chain, item["data"]["to"],
                                                 int(item["data"].get("value") or 0), item["data"]["data"])
                tx_hashes.append(tx_hash)
                current = self.order(order_id)[1]
                step_hashes = dict(current.get("sessionStepHashes") or {})
                step_hashes[fingerprint] = tx_hash
                values = {"txHashes": list(dict.fromkeys([*(current.get("txHashes") or []), tx_hash])),
                          "sessionStepHashes": step_hashes, "sessionExecutionStatus": "submitted"}
                if step["id"] == "swap":
                    swap_hash = tx_hash
                    values["sessionSwapHash"] = tx_hash
                self._update_session_order(order_id, state="submitted", **values)
                self.session.mark_reservation(order_id, "submitted")
                receipt = self.session.wait_receipt(network, self.session_rpc, tx_hash)
                if step["id"] == "swap":
                    ok, received = self._session_received(receipt, intent,
                        record["quote"]["details"]["currencyOut"]["minimumAmount"])
                    if not ok:
                        self._update_session_order(order_id, sessionExecutionStatus="settlement-unverified",
                                                   sessionReceivedUnits=str(received))
                        raise ValueError("买入交易已确认，但 Safe 的目标币实际到账不足；不会自动重买")
            if not swap_hash:
                raise ValueError("免确认订单缺少买入步骤")
            reservation_state = "success"
            self.session.mark_reservation(order_id, reservation_state)
            self._update_session_order(order_id, state="submitted", sessionExecutionStatus="success")
            return {"ok": True, "status": "success", "orderId": order_id,
                    "txHashes": tx_hashes, "sessionBuy": self.session.status()}
        except AmbiguousBroadcastError as exc:
            reservation_state = "uncertain"
            try:
                self.session.mark_reservation(order_id, reservation_state)
                self._update_session_order(order_id, state="signing", sessionExecutionStatus="uncertain",
                                           sessionError=str(exc)[:160])
            except Exception:
                pass
            raise ValueError(str(exc)) from None
        except Exception as exc:
            reservation_state = "submitted" if tx_hashes else "failed"
            try:
                self.session.mark_reservation(order_id, reservation_state)
                self._update_session_order(order_id, state="submitted" if tx_hashes else "signing",
                                           sessionExecutionStatus="approval-only" if tx_hashes and not self.order(order_id)[1].get("sessionSwapHash") else "failure",
                                           sessionError=str(exc)[:160])
            except Exception:
                pass
            raise ValueError(str(exc)[:220] if isinstance(exc, (ValueError, RuntimeError, TimeoutError))
                             else "免确认买入执行失败；不会自动重发") from None

    def status(self, payload):
        row, record = self.order(payload.get("orderId"))
        result = {"ok": True, "state": row["state"], "target": record["intent"]["target"], "txHashes": record["txHashes"]}
        if record.get("executionMode") == SESSION_MODE:
            execution_status = record.get("sessionExecutionStatus") or "waiting"
            swap_hash = record.get("sessionSwapHash")
            if swap_hash and execution_status != "success":
                network = self.networks()[record["intent"]["source"]["chainId"]]
                receipt = self.rpc(network, "eth_getTransactionReceipt", [swap_hash])
                if receipt:
                    if int(receipt.get("status") or "0x0", 16) != 1:
                        execution_status = "failure"
                    else:
                        ok, received = self._session_received(receipt, record["intent"],
                            record["quote"]["details"]["currencyOut"]["minimumAmount"])
                        execution_status = "success" if ok else "settlement-unverified"
                        self._update_session_order(row["id"], sessionExecutionStatus=execution_status,
                                                   sessionReceivedUnits=str(received))
            result.update(executionStatus=execution_status, sessionBuy=self.session_status(),
                          details=record.get("sessionError", ""), inTxHashes=record["txHashes"],
                          outTxHashes=[swap_hash] if swap_hash else [])
            return result
        if record["quote"].get("provider") == BINANCE_PROVIDER:
            result.update(self.binance_adapter().status(record, self.networks()[record["intent"]["source"]["chainId"]]))
            return result
        if row["state"] != "quoted":
            request_id = record["quote"]["steps"][-1]["requestId"]
            remote = self.http("/intents/status/v3", params={"requestId": request_id})
            for field, expected in (("originChainId", record["intent"]["source"]["chainId"]), ("destinationChainId", record["intent"]["target"]["chainId"])):
                if remote.get(field) is not None and chain_id(remote[field]) != expected:
                    raise ValueError("订单状态的公链不匹配，不能确认到账")
            if remote.get("status") == "success" and not remote.get("txHashes"):
                raise ValueError("服务商尚未提供目标链交易哈希，暂不能确认到账")
            result.update({"relayStatus": remote.get("status"), "details": remote.get("details", ""),
                           "inTxHashes": remote.get("inTxHashes", []), "outTxHashes": remote.get("txHashes", []),
                           "failReason": remote.get("failReason"), "refundFailReason": remote.get("refundFailReason")})
        return result

    def handle(self, action, payload):
        methods = {"capabilities": self.capabilities, "resolve": self.identities.require, "balances": self.balances, "balances-warm": self.warm_balances, "prepare": self.prepare,
                   "quote": self.quote, "authorize": self.authorize, "preflight": self.preflight, "claim-step": self.claim_step,
                   "progress": self.record_progress, "status": self.status,
                   "session-status": self.session_status, "session-plan": self.session_plan,
                   "session-create": self.session_create, "session-configure": self.session_configure,
                   "session-disable": self.session_disable, "session-execute": self.session_execute}
        if action not in methods:
            raise ValueError("未知买入操作")
        if action == "balances":
            if not self.gate.acquire(blocking=False):
                raise ValueError("已有请求读取余额，请稍后重试")
            try:
                return self.balances(payload)
            finally:
                self.gate.release()
        return methods[action]() if action == "capabilities" else methods[action](payload)
