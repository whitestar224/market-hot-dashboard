"""Arkham 数据客户端 —— 走网页端签名通路，零成本拿实体归因。

## 为什么不用官方 API

Arkham 官方 API 要申请：付费档**起步 $100/月**；x402 按次付费下
``/token/holders`` 是 **$6/次**、``/transfers`` 是 $0.40/行 —— 10 分钟一轮的常驻
监控根本烧不起。而网页端（arkm.com 的 Explorer）本身调的就是同一个 ``api.arkm.com``。

## 网页端的鉴权方式（2026-10-21 实测逆向）

前端**不带 API key**，只带两个自定义头：

    X-Timestamp:  <unix 秒>
    X-Payload:    sha256( key + ":" + sha256( path + ":" + ts + ":" + key ) )

其中 ``key`` 是构建时内联的 ``NEXT_PUBLIC_WEBAPP_CLIENT_KEY``。签名对象只有
**path**（不含 query）—— 实测同一 timestamp 下 ``?groupByEntity=true`` 与
``=false`` 的 X-Payload 完全相同，正是这个结论的直接证据。

对应前端源码（``_next/static/chunks/456qls-shyud9.js``）：

```js
let c=(t,e,n)=>{let i=new URL(t).pathname,o=sha256(`${i}:${e}:${n}`);return sha256(`${n}:${o}`)}
let y=(t,e)=>{let r=env.NEXT_PUBLIC_WEBAPP_CLIENT_KEY,
  n=Math.floor(new Date().getTime()/1e3).toString(),
  i=c(t,n,r); return {"X-Timestamp":n,"X-Payload":i,...e}}
```

已用抓包拿到的 7 组真实 (path, ts, payload) 样本交叉验证，7/7 吻合。

## 铁律

这是逆向网页端签名，Arkham 一旦轮换 ``NEXT_PUBLIC_WEBAPP_CLIENT_KEY`` 就会全线
401/403。此时必须抛 :class:`ArkhamAuthError` 让上层显示「接口不可用」，
**绝不能把失败伪装成「没有集群」**——这个项目反复踩过这个坑（交易所限流时
HTTP 200 + 错误体被渲染成「没有新合约」）。换 key 只需改环境变量
``XINGYUN_ARKHAM_CLIENT_KEY``，不用改代码。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any

import requests

#: EVM 合约：0x + 40 个 hex。
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
#: Solana mint：base58，32~44 位。
_BASE58_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")

#: 网页端用的 API 主机（前端 ``NEXT_PUBLIC_PLATFORM_API_URL``）。
ARKHAM_API_BASE = "https://api.arkm.com"
#: 前端内联的客户端口令。Arkham 换掉它时用环境变量覆盖即可。
ARKHAM_CLIENT_KEY_ENV = "XINGYUN_ARKHAM_CLIENT_KEY"
DEFAULT_CLIENT_KEY = "gh67j345kl6hj5k432"
#: 用桌面浏览器的 UA —— 这个通路本来就是给网页用的，别用 python-requests 的默认 UA。
ARKHAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
#: Arkham 支持的链（``GET /chains`` 实测返回，注意是 ``arbitrum_one`` 不是 ``arbitrum``）。
ARKHAM_CHAINS = frozenset({
    "ethereum", "polygon", "bsc", "optimism", "avalanche", "arbitrum_one",
    "base", "bitcoin", "tron", "flare", "solana", "dogecoin", "zcash",
    "hyperevm", "hypercore", "robinhood",
})
#: 内部链代号 / 展示标签 / 链号 → Arkham slug。
#:
#: **必须和 server.py 的 ``BINANCE_WALLET_CHAIN_META`` 对齐**：币安钱包那边
#: ``1=Ethereum``、``56=BSC``、``8453=Base``、``4663=Robinhood``、``CT_501=Solana``。
#: ``CT_501`` 曾经被这里错映射成 ethereum，结果 Solana 的 base58 合约全被送去
#: 以太坊查，Arkham 一律 400（真机 10 个候选全挂）。
#:
#: 裸链号只收这张表里明确列出的，认不出就返回空（Arkham 对任何路径都回 404，
#: 猜错只会浪费请求，还可能把「BSC 上的币」查成「不存在」）。
ARKHAM_CHAIN_SLUGS = {
    "ethereum": "ethereum", "eth": "ethereum", "以太坊": "ethereum",
    "1": "ethereum",
    "solana": "solana", "sol": "solana", "索拉纳": "solana",
    "501": "solana", "ct_501": "solana",
    "bsc": "bsc", "bnb": "bsc", "币安智能链": "bsc", "bnb smart chain": "bsc",
    "56": "bsc",
    "base": "base", "8453": "base",
    "robinhood": "robinhood", "4663": "robinhood",
    "arbitrum": "arbitrum_one", "arbitrum_one": "arbitrum_one",
    "arbitrum-one": "arbitrum_one", "arb": "arbitrum_one", "42161": "arbitrum_one",
    "polygon": "polygon", "matic": "polygon", "137": "polygon",
    "optimism": "optimism", "op": "optimism", "10": "optimism",
    "avalanche": "avalanche", "avax": "avalanche", "43114": "avalanche",
    "tron": "tron", "trx": "tron",
    "hyperevm": "hyperevm", "hypercore": "hypercore",
}
#: EVM 系链（合约是 0x + 40 hex）；其余按 base58 / 各自格式。
ARKHAM_EVM_CHAINS = frozenset({
    "ethereum", "polygon", "bsc", "optimism", "avalanche", "arbitrum_one",
    "base", "hyperevm", "hypercore", "robinhood",
})
#: 至少间隔多久发一个请求。``/token/holders`` 属标准端点（官方 20 req/s、
#: 高配 100 req/s），但这条通路共用一个前端口令，慢一点更稳。
ARKHAM_MIN_INTERVAL = 0.3
#: 429 / 5xx 的重试退避基数。
ARKHAM_BACKOFF_SECONDS = 1.5
#: 默认超时。实测单请求 1.6–2.4 秒。
ARKHAM_TIMEOUT = 30.0

#: 已认证但**没权限**的状态码 —— 说明口令被轮换，属于系统性故障，必须大声报错。
AUTH_STATUSES = frozenset({401, 403})


class ArkhamError(RuntimeError):
    """Arkham 调用失败（非鉴权类）。"""

    def __init__(self, message: str, *, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class ArkhamAuthError(ArkhamError):
    """签名被拒 —— 网页端口令大概率被轮换了，整个板块应当显示「不可用」。"""


def runtime_dir():
    from pathlib import Path

    return Path(os.getenv("XINGYUN_RUNTIME_DIR") or (Path(__file__).resolve().parent / ".runtime-cache"))


def client_key() -> str:
    return (os.getenv(ARKHAM_CLIENT_KEY_ENV) or "").strip() or DEFAULT_CLIENT_KEY


def sign_payload(path: str, timestamp: str, key: str) -> str:
    """``sha256(key : sha256(path : ts : key))`` —— 与前端 ``constructHeaders`` 一致。"""
    inner = hashlib.sha256(f"{path}:{timestamp}:{key}".encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{key}:{inner}".encode("utf-8")).hexdigest()


def arkham_headers(path: str, *, key: str | None = None, timestamp: str | None = None) -> dict[str, str]:
    """构造一次请求的头。``path`` 里的 query 会被丢掉 —— 签名对象只有 path。

    剥 query 这件事在 :func:`arkham_headers` 里做，而不是只靠 ``arkham_get``
    自己记得先剥：调用方随手传一条带参数的路径是很容易发生的，错了就整条 401。
    """
    secret = key or client_key()
    ts = timestamp or str(int(time.time()))
    clean_path = "/" + str(path or "").lstrip("/").split("?", 1)[0]
    return {
        "X-Timestamp": ts,
        "X-Payload": sign_payload(clean_path, ts, secret),
        "User-Agent": ARKHAM_USER_AGENT,
        "Accept": "application/json",
        "Origin": "https://arkm.com",
        "Referer": "https://arkm.com/",
    }


_PACE = {"last": 0.0}
_PACE_LOCK = threading.Lock()


def _pace() -> None:
    with _PACE_LOCK:
        wait = ARKHAM_MIN_INTERVAL - (time.time() - _PACE["last"])
        if wait > 0:
            time.sleep(wait)
        _PACE["last"] = time.time()


def arkham_get(
    path: str,
    *,
    query: dict[str, Any] | None = None,
    timeout: float = ARKHAM_TIMEOUT,
    retries: int = 2,
    session: requests.Session | None = None,
) -> Any:
    """发起一次签名 GET，返回解析后的 JSON。

    * 401 / 403 → :class:`ArkhamAuthError`（口令被轮换，重试没有意义）
    * 404 → :class:`ArkhamError`（币不在 Arkham 收录里，属正常情况，重试没用）
    * 429 / 5xx → 退避重试，用尽后抛 :class:`ArkhamError`
    """
    clean_path = "/" + str(path or "").lstrip("/").split("?", 1)[0]
    params = {k: v for k, v in (query or {}).items() if v is not None}
    url = f"{ARKHAM_API_BASE}{clean_path}"
    owns_session = session is None
    # ``trust_env`` 是 **Session 的属性**，不是 ``Session.get()`` 的参数 ——
    # 传进 get() 会直接 TypeError（真机 40 个候选全挂在这上面）。自己建的 session
    # 默认就是 trust_env=True（跟随系统代理），这里只在自建时显式写一次以防被全局改过。
    sess = session or requests.Session()
    if owns_session:
        sess.trust_env = True
    last_error: Exception | None = None
    try:
        for attempt in range(retries + 1):
            _pace()
            try:
                response = sess.get(
                    url,
                    params=params or None,
                    headers=arkham_headers(clean_path),
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= retries:
                    raise ArkhamError(f"Arkham 网络异常：{type(exc).__name__}: {exc}") from exc
                time.sleep(ARKHAM_BACKOFF_SECONDS ** (attempt + 1))
                continue

            status = int(response.status_code)
            if status in AUTH_STATUSES:
                raise ArkhamAuthError(
                    f"Arkham 签名被拒（HTTP {status}）—— 网页端口令可能已轮换",
                    status=status,
                    body=response.text[:300],
                )
            if status == 404:
                raise ArkhamError(
                    "Arkham 未收录该标的", status=status, body=response.text[:300]
                )
            if status == 429 or status >= 500:
                last_error = ArkhamError(
                    f"Arkham 限流或服务异常（HTTP {status}）", status=status
                )
                if attempt >= retries:
                    raise last_error
                time.sleep(ARKHAM_BACKOFF_SECONDS ** (attempt + 1))
                continue
            if status == 400:
                # 400 是「你给的东西 Arkham 不认」——重试没用，但要把原文带上，
                # 否则排查时只看到一句「HTTP 400」，分不清是地址格式还是别的。
                raise ArkhamError(
                    f"Arkham 不认这个标的（HTTP 400）：{response.text[:160]}",
                    status=status,
                    body=response.text[:300],
                )
            if status != 200:
                raise ArkhamError(
                    f"Arkham 返回 HTTP {status}", status=status, body=response.text[:300]
                )
            try:
                return response.json()
            except ValueError as exc:
                raise ArkhamError(f"Arkham 返回的不是 JSON：{response.text[:200]}") from exc
        raise ArkhamError(f"Arkham 请求失败：{last_error}")
    finally:
        if owns_session:
            sess.close()


def arkham_chain_slug(value: Any) -> str:
    """内部链代号 / 展示标签 / 链号 → Arkham slug，认不出返回空串。"""
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    slug = ARKHAM_CHAIN_SLUGS.get(text, "")
    if slug:
        return slug
    # 已经就是 Arkham 自己的 slug（含下划线的 arbitrum_one 之类）也直接放行。
    return text if text in ARKHAM_CHAINS else ""


#: 展示用链名。**认不出宁可留空，也绝不回吐裸链号** —— 候选标的的 chain 来自
#: 价格异动（``solana``）、币安钱包（``CT_501``）、交易所（``56``）三套写法，
#: 通用展示表认不出 ``CT_501`` 就会把内部链号原样印到卡片上（踩过）。
ARKHAM_CHAIN_LABELS = {
    "ethereum": "Ethereum",
    "solana": "Solana",
    "bsc": "BSC",
    "base": "Base",
    "robinhood": "Robinhood",
    "arbitrum_one": "Arbitrum",
}


def chain_label(value: Any) -> str:
    """内部链代号 / 展示标签 / 链号 → 展示名，认不出返回空串。"""
    return ARKHAM_CHAIN_LABELS.get(arkham_chain_slug(value), "")


def address_matches_chain(slug: str, address: Any) -> bool:
    """合约地址的形态是否配得上这条链。

    这条检查是花钱买来的：``CT_501``（Solana）被错映射成 ethereum 时，base58 合约
    全被送去以太坊查，Arkham 一律回 400，白白浪费 10 个请求还污染错误信息。
    现在形态不对就在本地拦下，报「合约地址与链不匹配」这种能对症的话。
    """
    text = str(address or "").strip()
    if not text:
        return False
    if slug in ARKHAM_EVM_CHAINS:
        return bool(_EVM_ADDRESS.fullmatch(text))
    if slug == "solana":
        return bool(_BASE58_ADDRESS.fullmatch(text))
    if slug == "bitcoin":
        return 26 <= len(text) <= 62
    # tron / dogecoin / zcash / flare：各自格式差异大，交给上游判。
    return True


def arkham_token_url(pricing_id: Any) -> str:
    """Explorer 页地址。没有 pricingID 时退首页 —— 猜路径只会得到空白页。"""
    ident = str(pricing_id or "").strip()
    if not ident or "/" in ident:
        return "https://arkm.com/explorer"
    return f"https://arkm.com/explorer/token/{urllib.parse.quote(ident, safe='')}"


def _entity_of(address: dict[str, Any]) -> dict[str, Any]:
    entity = address.get("arkhamEntity")
    return entity if isinstance(entity, dict) else {}


def normalize_holders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把 ``/token/holders`` 的响应拍平成持币行（按链分组的信息保留在每行里）。

    每行：``chain / address / entityId / entityName / entityType / label /
    pct（百分数，不是小数）/ usd / balance / isContract``。

    ``pctOfCap`` 在 Arkham 里是 **0~1 的小数**，这里统一乘 100 换成「百分之几」，
    免得下游每次都忘。

    **``pctOfCap`` 可能整个缺失**：Solana 上一大批币（没有价格 / 市值数据）返回的
    行里只有 ``balance``，``pctOfCap`` 和 ``usd`` 都是空/0。这时用
    ``balance ÷ totalSupply[该链]`` 自己算 —— 分母就是 Arkham 同一个响应里给的
    该链总供应，口径一致。不兜底的话这些币的每个持币人占比都是 0，
    会被门槛规则全部丢掉，页面上表现成「这个币没人控盘」，而事实相反。
    """
    rows: list[dict[str, Any]] = []
    top = payload.get("addressTopHolders")
    if not isinstance(top, dict):
        return rows
    supply_map = payload.get("totalSupply") if isinstance(payload.get("totalSupply"), dict) else {}
    for chain, items in top.items():
        if not isinstance(items, list):
            continue
        try:
            chain_supply = float(supply_map.get(chain) or 0.0)
        except (TypeError, ValueError):
            chain_supply = 0.0
        for item in items:
            if not isinstance(item, dict):
                continue
            address = item.get("address")
            if not isinstance(address, dict):
                continue
            addr = str(address.get("address") or "").strip()
            if not addr:
                continue
            entity = _entity_of(address)
            label = address.get("arkhamLabel")
            label_name = str(label.get("name") or "").strip() if isinstance(label, dict) else ""
            try:
                balance = float(item.get("balance") or 0.0)
            except (TypeError, ValueError):
                balance = 0.0
            raw_pct = item.get("pctOfCap")
            if raw_pct is None and chain_supply > 0 and balance > 0:
                raw_pct = balance / chain_supply
            try:
                pct_fraction = float(raw_pct or 0.0)
            except (TypeError, ValueError):
                pct_fraction = 0.0
            try:
                usd = float(item.get("usd") or 0.0)
            except (TypeError, ValueError):
                usd = 0.0
            rows.append({
                "chain": str(address.get("chain") or chain or "").strip(),
                "address": addr,
                "entityId": str(entity.get("id") or "").strip(),
                "entityName": str(entity.get("name") or "").strip(),
                "entityType": str(entity.get("type") or "").strip(),
                "label": label_name,
                "pct": pct_fraction * 100.0,
                "usd": usd,
                "balance": balance,
                "isContract": bool(address.get("contract")),
            })
    return rows


def normalize_entity_holders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """``?groupByEntity=true`` 的实体聚合视图，用于交叉校验。"""
    rows: list[dict[str, Any]] = []
    top = payload.get("entityTopHolders")
    if not isinstance(top, dict):
        return rows
    for chain, items in top.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            entity = item.get("entity")
            if not isinstance(entity, dict):
                continue
            rows.append({
                "chain": str(chain or "").strip(),
                "entityId": str(entity.get("id") or "").strip(),
                "entityName": str(entity.get("name") or "").strip(),
                "entityType": str(entity.get("type") or "").strip(),
                "pct": float(item.get("pctOfCap") or 0.0) * 100.0,
                "usd": float(item.get("usd") or 0.0),
                "balance": float(item.get("balance") or 0.0),
            })
    return rows


def token_identity(payload: dict[str, Any]) -> dict[str, str]:
    token = payload.get("token") if isinstance(payload.get("token"), dict) else {}
    ident = token.get("identifier") if isinstance(token.get("identifier"), dict) else {}
    return {
        "name": str(token.get("name") or "").strip(),
        "symbol": str(token.get("symbol") or "").strip(),
        "pricingId": str(ident.get("pricingID") or "").strip(),
    }


def fetch_token_holders(
    chain: Any,
    contract: Any,
    *,
    session: requests.Session | None = None,
    timeout: float = ARKHAM_TIMEOUT,
) -> dict[str, Any]:
    """按「链 + 合约」取持币人。返回 ``{identity, holders, chains}``。

    链或合约缺失、链不在 Arkham 支持范围 → 返回空 holders 并带 ``reason``，
    调用方据此把它记成「未覆盖」而不是「没有持币人」。
    """
    slug = arkham_chain_slug(chain)
    addr = str(contract or "").strip()
    if not slug:
        return {"identity": {}, "holders": [], "reason": f"Arkham 不支持该链：{chain}"}
    if not addr:
        return {"identity": {}, "holders": [], "reason": "缺少合约地址"}
    if not address_matches_chain(slug, addr):
        return {
            "identity": {}, "holders": [],
            "reason": f"合约地址与链不匹配（{slug}）",
        }
    payload = arkham_get(f"/token/holders/{slug}/{addr}", session=session, timeout=timeout)
    if not isinstance(payload, dict):
        return {"identity": {}, "holders": [], "reason": "Arkham 返回结构异常"}
    holders = normalize_holders(payload)
    chains = sorted(
        {str(k) for k in (payload.get("addressTopHolders") or {})}
    ) if isinstance(payload.get("addressTopHolders"), dict) else []
    return {
        "identity": token_identity(payload),
        "holders": holders,
        "chains": chains,
        "reason": "" if holders else "Arkham 没有该标的的持币人数据",
    }


def search_tokens(query: Any, *, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """``GET /token/search?query=`` —— 按 symbol / 名字模糊搜代币。

    每条：``{identifier: {pricingID}, name, symbol, price,
    deployments: [{chain, address}]}``。``deployments`` 就是「这个币在哪些链上、
    合约是什么」，也是「榜单行只有 symbol 时怎么找到链上合约」的唯一通路 ——
    Arkham 网页的搜索框调的就是这个端点。

    返回顺序是 Arkham 自己按相关性排的（大市值 / 原生部署靠前），**调用方不能直接
    取第一条**：查 ``IOST`` 会顺手带回 ``GHOST``，查 ``ZEC`` 第一条是原生 zcash、
    第二条才是 ``binance-peg-zcash-token``。挑哪条是业务判断，见
    ``top10_holders.pick_search_hit``。
    """
    text = str(query or "").strip()
    if not text:
        return []
    payload = arkham_get("/token/search", query={"query": text}, session=session)
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
