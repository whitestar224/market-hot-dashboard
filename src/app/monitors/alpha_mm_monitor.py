"""做市商控盘提醒（涨幅榜里的 Alpha 币）。

用户 2026-10-20 定稿的五条判据，逐条落地：

1. **项目方控盘 ≥ 90%** —— GoPlus 前 10 持币地址合计，**剔除 DEX 池、销毁地址、
   交易所热钱包**（`holders_control`）。不剔除的话 Binance 8 这种 17% 的托管钱包会把
   「控盘」虚报成 90%+（BMT 实测：原始 90.42%，剔完只剩 71.6%）。
2. **必须是 alpha 合约** —— 币在 Binance Alpha 收录清单里，且已经上了永续合约
   （涨幅榜行上 `marketType == "futures"` 或来自 OKX 合约榜）。Alpha 现货 + 永续
   同时存在，才是「项目方控盘拉盘、合约双向收割」的形态。
3. **流通市值 ≤ 3000 万美元** —— Alpha 官方 `marketCap` 优先，缺失退 DexScreener。
4. **拉盘后暴跌过的排除** —— GeckoTerminal 日线历史高点回撤 ≥ 60% 视为「拉过又砸了」，
   **永久排除**（写墓碑 `blocked`，后面再涨也不报）。
5. **上涨过程中的支撑位买入** —— 入选时弹一次；之后只在「上涨结构 + 回踩支撑」时再弹买点：
   结构 = 收盘 > MA20 > MA60 且 MA20 上行；支撑 = max(MA20, 近 15 日最低价) 里离现价
   最近且现价在其上方的那一条；回踩到 2% 以内且没破位（-3%）才算买点。

设计约定（与项目其它 monitor 一致）：
- 网络访问全部可注入（`fetchers`），单测不需要真打接口；
- 每枚币的 GoPlus 持仓 / 日线指标各自 TTL 缓存，避免每轮重打；
- 抓取失败**不算通过**：控盘度拿不到就是「未核验」，不弹窗（宁可漏报，不可假报）。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Iterable, Sequence

import requests

STATE_PATH = os.path.join(".runtime-cache", "alpha_mm_control_state.json")

# ── 判据阈值 ────────────────────────────────────────────────────────────────
MM_MIN_CONTROL_PCT = 90.0
MM_MAX_MARKET_CAP_USD = 30_000_000.0
MM_MAX_DRAWDOWN_PCT = 60.0
MM_SUPPORT_ZONE_PCT = 2.0          # 现价距支撑 ≤2% 视为「回踩到位」
MM_SUPPORT_BREAK_PCT = 3.0         # 跌破支撑 3% 视为破位，不再提示买点
MM_MIN_DAY_CANDLES = 30            # 日线不足这个数就不做形态判断
MM_WATCH_SECONDS = 14 * 24 * 3600  # 入选后观察 14 天，超时不再算支撑买点
MM_SEED_COOLDOWN_SECONDS = 7 * 24 * 3600
MM_BUY_COOLDOWN_SECONDS = 6 * 3600
MM_HOLDER_TTL_SECONDS = 6 * 3600
MM_HOLDER_ERROR_TTL_SECONDS = 15 * 60  # 限流/失败也缓存 15 分钟，别每轮都去撞
MM_METRICS_TTL_SECONDS = 1800
MM_HTTP_TIMEOUT = 8

MM_KEY_PREFIX_SEED = "alpha-mm-seed:"
MM_KEY_PREFIX_BUY = "alpha-mm-buy:"

# ── 涨幅榜里只看加密榜（股票榜不参与） ──────────────────────────────────────
MM_GAINERS_SOURCES = (
    "binance-gainers",
    "binance-futures-gainers",
    "okx-gainers",
    "bitget-gainers",
    "okx-dex-gainers",
)
MM_PERP_SOURCES = ("binance-futures-gainers", "okx-gainers")

# ── 热门榜（成交额 / 热度榜）里只看加密榜 ────────────────────────────────────
# 用户 2026-10-21 追加：热门榜里满足五条的标的也算候选。热门榜只负责「被很多人看过、
# 成交额靠前」，后面四条闸（Alpha ∩ 永续 / 市值 / 控盘 / 回撤）一道都不少，
# 所以放宽候选源不会让弹窗变吵。
# 港股/美股/A 股那三个榜不参与（和涨幅榜同一个口径）。
MM_HOT_SOURCES = (
    "binance",        # 币安成交额榜
    "okx-turnover",   # OKX 成交额榜
    "okx-dex",        # OKX DEX 24 小时热门榜（自带链与合约地址）
    "bitget",         # Bitget 成交额榜
    "aicoin",         # AIcoin 热门榜
)

# ── 持币地址剔除名单 ────────────────────────────────────────────────────────
# 只放**确定**的：零地址/销毁、币安托管热钱包、Uniswap V4 pool manager。
# 认不出来的合约地址一律留着——那通常是项目方/做市商的归集钱包，正是我们要的控盘。
MM_EXCLUDED_HOLDERS = {
    "0x0000000000000000000000000000000000000000": "零地址",
    "0x000000000000000000000000000000000000dead": "销毁地址",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "币安托管钱包",
    "0x28c6c06298d514db089934071355e5743bf21d60": "币安托管钱包",
    "0x8894e0a0c962cb723c1976a4421c95949be2d4e3": "币安托管钱包",
    "0x28e2ea090877bf75740558f6bfb36a5ffee9e9df": "Uniswap V4 池管理器",
}
# GoPlus 的 tag 命中这些词说明是池子/路由，不是持有人。
MM_POOL_TAG_PATTERN = re.compile(
    r"(pancake|uniswap|sushi|biswap|dodo|wombat|curve|balancer|raydium|orca|meteora|"
    r"liquidity|pool|lp\b|router|pair|vault|farm|staking)",
    re.I,
)
MM_BURN_PATTERN = re.compile(r"^0x0{40}$|^0x0{36}dead$|^1nc1nerator", re.I)

# GoPlus 支持并给出 holders 的链；其余链（Robinhood / Sui / Tron …）拿不到控盘度。
MM_GOPLUS_CHAINS = {
    "bsc": ("56", ""),
    "bnb": ("56", ""),
    "56": ("56", ""),
    "eth": ("1", ""),
    "ethereum": ("1", ""),
    "1": ("1", ""),
    "base": ("8453", ""),
    "8453": ("8453", ""),
    "solana": ("501", "solana"),
    "sol": ("501", "solana"),
    "501": ("501", "solana"),
    # 币安钱包那套链号是 ``CT_501``，chain_key 会压成 ct501。
    "ct501": ("501", "solana"),
}
# DexScreener / GeckoTerminal 认的网络名（与 server.price_structure_geckoterminal_network 对齐）。
MM_NETWORK_ALIASES = {
    "bsc": "bsc",
    "bnb": "bsc",
    "eth": "eth",
    "ethereum": "eth",
    "base": "base",
    "solana": "solana",
    "sol": "solana",
    "robinhood": "robinhood",
    "robinhoodchain": "robinhood",
    "4663": "robinhood",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def goplus_api_key() -> str:
    return (
        os.getenv("GOPLUS_API_KEY", "").strip()
        or os.getenv("XINGYUN_GOPLUS_API_KEY", "").strip()
    )


def goplus_batch_size() -> int:
    """一次请求能带上几个合约：有 key 才能批量，匿名只能一个个来。"""
    override = int(_env_float("XINGYUN_ALPHA_MM_GOPLUS_BATCH_SIZE", 0))
    if override > 0:
        return override
    return MM_GOPLUS_BATCH_WITH_KEY if goplus_api_key() else MM_GOPLUS_BATCH_ANONYMOUS


def _goplus_headers() -> dict[str, str]:
    """有 key 就带上（免费 key = 20 req/min，比匿名额度稳得多）。

    在 `启动后台服务.cmd` 里加一行 `set GOPLUS_API_KEY=xxx` 即可生效。
    """
    key = goplus_api_key()
    return {**_HEADERS, **({"Authorization": key} if key else {})}

# GoPlus 免费额度按 IP 计，且 8765 自己的新闻交易也在这条线上抽水。
# 实测超限时 HTTP 仍是 200，body 是 {"code":4029,"message":"too many requests"}。
# 匿名额度实测：**一次只处理 1 个合约**（批量里的其余地址直接不回），且大约 1 次/分钟。
# 有 key 时批量才真的生效，所以批量大小按「有没有 key」分档（`goplus_batch_size`）。
MM_GOPLUS_MIN_INTERVAL = 60.0
MM_GOPLUS_MAX_WAIT_SECONDS = 70.0
MM_GOPLUS_BACKOFF_SECONDS = 180.0
MM_GOPLUS_BATCH_WITH_KEY = 8
MM_GOPLUS_BATCH_ANONYMOUS = 1
_GOPLUS_PACE: dict[str, float] = {"last": 0.0, "backoffUntil": 0.0}
_GOPLUS_PACE_LOCK = threading.Lock()


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _text(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "").replace("$", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^0-9A-Z]", "", text)[:24]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def chain_key(value: Any) -> str:
    """把任意链标识压成 `bsc` / `eth` / `base` / `solana` / `robinhood` 这类短名。"""
    text = re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())
    return text


# 币安 Alpha 的**代币交易页**是 ``/zh-CN/alpha/<链 slug>/<合约>``（用户 2026-10-21 给的
# JCT 实例：``/zh-CN/alpha/bsc/0xea37…``）。slug 只收录实测确认过的链——币安前端是 SPA，
# 任何路径都回 202 空壳（``bsc`` / ``56`` / ``bnb`` 三种 slug 抓下来字节数一模一样），
# 状态码和内容都判断不了对错，猜错的后果是「点了打开空白页」，比退回首页更糟。
MM_ALPHA_CHAIN_SLUGS = {"bsc": "bsc", "bnb": "bsc", "56": "bsc"}
MM_ALPHA_HOME_URL = "https://www.binance.com/zh-CN/alpha"


def binance_alpha_token_url(chain: Any, contract: Any) -> str:
    """该币在币安 Alpha 的交易页；链 slug 没实测过就退回 Alpha 首页。"""
    address = str(contract or "").strip()
    slug = MM_ALPHA_CHAIN_SLUGS.get(chain_key(chain), "")
    if slug and address:
        return f"{MM_ALPHA_HOME_URL}/{slug}/{address}"
    return MM_ALPHA_HOME_URL


def goplus_chain(value: Any) -> tuple[str, str]:
    """返回 `(chain_id, kind)`；kind 为空表示走 EVM 接口，`solana` 走 Solana 接口。"""
    return MM_GOPLUS_CHAINS.get(chain_key(value), ("", ""))


def network_name(value: Any) -> str:
    key = chain_key(value)
    return MM_NETWORK_ALIASES.get(key, "")


def excluded_holder(address: Any, tag: Any = "", pool_addresses: Iterable[str] = ()) -> str:
    """返回剔除原因；空串表示这个地址算作真实持有者。"""
    raw = str(address or "").strip()
    key = raw.casefold()
    if not raw:
        return "空地址"
    if key in MM_EXCLUDED_HOLDERS:
        return MM_EXCLUDED_HOLDERS[key]
    if MM_BURN_PATTERN.match(raw):
        return "销毁地址"
    if key in {str(item or "").strip().casefold() for item in pool_addresses}:
        return "DEX 池"
    if MM_POOL_TAG_PATTERN.search(str(tag or "")):
        return f"池子({_text(tag, 20)})"
    return ""


def holders_control(
    holders: Sequence[dict[str, Any]],
    *,
    pool_addresses: Iterable[str] = (),
    top_n: int = 10,
) -> dict[str, Any]:
    """前 N 个「真实持有人」的合计占比（%）。

    `holders` 是 GoPlus 的形状：`{"address", "percent"(0~1 的小数), "tag", "is_locked"}`。
    上游偶尔把总量按流通量算，合计会超过 100%（DOOD 实测 107%），这里封顶到 100。
    """
    pools = tuple(pool_addresses)
    kept: list[tuple[str, float]] = []
    dropped: list[dict[str, Any]] = []
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        address = str(holder.get("address") or holder.get("holder_address") or "").strip()
        percent = _num(holder.get("percent") or holder.get("hold_percent"), 0.0)
        if percent > 1.5:  # 有的链直接给百分数
            percent = percent / 100.0
        reason = excluded_holder(address, holder.get("tag"), pools)
        if reason:
            dropped.append({"address": address, "percent": round(percent * 100, 4), "reason": reason})
            continue
        if percent <= 0:
            continue
        kept.append((address, percent))
        if len(kept) >= max(1, top_n):
            break
    total = min(100.0, round(sum(percent for _, percent in kept) * 100.0, 2))
    return {
        "controlPct": total,
        "kept": len(kept),
        "dropped": dropped,
        "top": [{"address": address, "percent": round(percent * 100, 4)} for address, percent in kept[:3]],
    }


# ── 网络抓取 ────────────────────────────────────────────────────────────────
def _get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = MM_HTTP_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> Any:
    response = requests.get(url, params=params or {}, headers=headers or _HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _goplus_fetch(
    chain: Any, contracts: Sequence[str], *, timeout: float = MM_HTTP_TIMEOUT,
) -> dict[str, dict[str, Any]]:
    """一次请求核验多条合约（GoPlus 的 `contract_addresses` 支持逗号分隔）。

    免费额度按 IP 计、超限时 HTTP 仍是 200 + `{"code":4029}`，而且本机看板自己在同一条
    线上抽水。所以这里做三件事：**批量**（一轮一条链一次请求）、**全局最小间隔**、
    **限流后退避**，并且把「限流」当错误而不是「没有持仓」——宁可这轮不报，也不许把
    限流读成 0% 控盘（否则就会出现「控盘 0%」的假结论）。
    """
    wanted = [str(contract or "").strip() for contract in contracts if str(contract or "").strip()]
    if not wanted:
        return {}
    chain_id, kind = goplus_chain(chain)
    if not chain_id:
        reason = f"GoPlus 不支持 {chain or '该链'} 的持仓核验"
        return {contract.casefold(): {"unsupported": True, "reason": reason} for contract in wanted}
    with _GOPLUS_PACE_LOCK:
        if time.time() < _GOPLUS_PACE["backoffUntil"]:
            return {c.casefold(): {"error": "GoPlus 限流中，稍后重试"} for c in wanted}
        wait = MM_GOPLUS_MIN_INTERVAL - (time.time() - _GOPLUS_PACE["last"])
    if wait > 0:
        time.sleep(min(wait, MM_GOPLUS_MAX_WAIT_SECONDS))
    if kind == "solana":
        url = "https://api.gopluslabs.io/api/v1/solana/token_security"
    else:
        url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    with _GOPLUS_PACE_LOCK:
        _GOPLUS_PACE["last"] = time.time()
    payload = _get_json(
        url, params={"contract_addresses": ",".join(wanted)}, timeout=timeout,
        headers=_goplus_headers(),
    )
    code = str(payload.get("code")) if isinstance(payload, dict) else ""
    if code and code not in {"1"}:
        if code == "4029":
            with _GOPLUS_PACE_LOCK:
                _GOPLUS_PACE["backoffUntil"] = time.time() + MM_GOPLUS_BACKOFF_SECONDS
            return {c.casefold(): {"error": "GoPlus 限流"} for c in wanted}
        message = f"GoPlus {code} {_text(payload.get('message'), 40)}"
        return {c.casefold(): {"error": message} for c in wanted}
    result = payload.get("result") if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        return {c.casefold(): {"error": "GoPlus 返回结构异常"} for c in wanted}
    normalized: dict[str, dict[str, Any]] = {}
    for contract in wanted:
        data = result.get(contract.casefold()) or result.get(contract) or {}
        if not data and len(result) == 1 and len(wanted) == 1:
            data = next(iter(result.values()))
        if not isinstance(data, dict) or not data:
            normalized[contract.casefold()] = {"error": "GoPlus 暂无该合约结果"}
            continue
        normalized[contract.casefold()] = {
            "holders": data.get("holders") if isinstance(data.get("holders"), list) else [],
            "holderCount": int(_num(data.get("holder_count"), 0)) or None,
            "creatorPercent": round(_num(data.get("creator_percent"), 0.0) * 100.0, 4),
            "source": "GoPlus",
        }
    return normalized


def fetch_token_security(chain: Any, contract: str, *, timeout: float = MM_HTTP_TIMEOUT) -> dict[str, Any]:
    """单条核验（保持单发形状，方便测试注入与手工探针）。"""
    contract = str(contract or "").strip()
    if not contract:
        return {"error": "缺少合约地址"}
    data = _goplus_fetch(chain, [contract], timeout=timeout).get(contract.casefold())
    return data if isinstance(data, dict) else {"error": "GoPlus 返回结构异常"}


def fetch_token_security_batch(
    chain: Any, contracts: Sequence[str], *, timeout: float = MM_HTTP_TIMEOUT,
) -> dict[str, dict[str, Any]]:
    """批量核验，返回 `{合约小写: 单条结果}`。"""
    return _goplus_fetch(chain, contracts, timeout=timeout)


def control_from_security(
    raw: dict[str, Any] | None, *, pool_addresses: Iterable[str] = (), now: float = 0.0,
) -> dict[str, Any]:
    """把一次持仓核验结果折成控盘度；失败一律带 `error`（绝不静默变成 0%）。"""
    if not isinstance(raw, dict):
        return {"error": "持仓核验返回异常", "at": now}
    if raw.get("unsupported"):
        return {"error": _text(raw.get("reason") or "链不支持持仓核验", 80), "at": now}
    if raw.get("error"):
        return {"error": _text(raw.get("error"), 80), "at": now}
    holders = raw.get("holders") if isinstance(raw.get("holders"), list) else []
    if not holders:
        return {"error": "GoPlus 没有返回持币列表", "at": now}
    result = holders_control(holders, pool_addresses=pool_addresses)
    result.update({
        "at": now,
        "holderCount": raw.get("holderCount"),
        "creatorPercent": round(_num(raw.get("creatorPercent"), 0.0), 4),
    })
    return result


def control_cache_settled(entry: Any, now: float) -> bool:
    """缓存是否仍然有效（成功按 6 小时、失败按 15 分钟），有效就不再打接口。"""
    if not isinstance(entry, dict):
        return False
    age = now - _num(entry.get("at"), 0)
    if entry.get("error"):
        return age < MM_HOLDER_ERROR_TTL_SECONDS
    return age < MM_HOLDER_TTL_SECONDS


def fetch_dex_pairs(network: str, contract: str, *, timeout: float = MM_HTTP_TIMEOUT) -> list[dict[str, Any]]:
    """DexScreener 的该合约全部交易对：拿池子地址（剔持仓用）+ 市值兜底。"""
    if not network or not contract:
        return []
    payload = _get_json(
        f"https://api.dexscreener.com/token-pairs/v1/{network}/{contract}", timeout=timeout
    )
    if isinstance(payload, dict):
        pairs = payload.get("pairs")
        return pairs if isinstance(pairs, list) else []
    return payload if isinstance(payload, list) else []


def fetch_day_candles(network: str, pool_address: str, *, timeout: float = MM_HTTP_TIMEOUT) -> list[list[float]]:
    """GeckoTerminal 日线（新→旧），用于历史高点回撤与均线/支撑。"""
    if not network or not pool_address:
        return []
    payload = _get_json(
        f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/ohlcv/day",
        params={"aggregate": 1, "limit": 500},
        timeout=timeout,
    )
    data = payload.get("data") if isinstance(payload, dict) else {}
    attributes = data.get("attributes") if isinstance(data, dict) else {}
    rows = attributes.get("ohlcv_list") if isinstance(attributes, dict) else None
    if not isinstance(rows, list):
        return []
    clean: list[list[float]] = []
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            clean.append([_num(item) for item in row[:5]])
    return clean


def parse_day_candles(rows: Sequence[Sequence[float]]) -> list[tuple[float, float, float, float, float]]:
    """统一成 `(ts, open, high, low, close)` 并按时间正序。"""
    candles = [
        (float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        for row in rows
        if len(row) >= 5 and float(row[4]) > 0
    ]
    candles.sort(key=lambda item: item[0])
    return candles


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def candle_metrics(candles: Sequence[tuple[float, float, float, float, float]]) -> dict[str, Any]:
    """历史高点回撤 + 上涨结构 + 支撑位，一次算全。"""
    closes = [item[4] for item in candles]
    highs = [item[2] for item in candles]
    lows = [item[3] for item in candles]
    close = closes[-1] if closes else 0.0
    peak = max(highs) if highs else 0.0
    drawdown = round((peak - close) / peak * 100.0, 2) if peak > 0 else 100.0
    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else 0.0
    ma60 = _mean(closes[-60:]) if len(closes) >= 60 else 0.0
    ma20_prev = _mean(closes[-25:-5]) if len(closes) >= 25 else 0.0
    low15 = min(lows[-15:]) if len(lows) >= 15 else (min(lows) if lows else 0.0)
    trend_up = bool(ma20 and ma60 and close > ma20 > ma60 and ma20 >= ma20_prev)
    return {
        "close": close,
        "peak": peak,
        "drawdownPct": drawdown,
        "ma20": round(ma20, 10),
        "ma60": round(ma60, 10),
        "low15": round(low15, 10),
        "trendUp": trend_up,
        "candles": len(closes),
        "peakAt": int(
            max(candles, key=lambda item: item[2])[0] * 1000
        ) if candles else 0,
    }


def support_state(metrics: dict[str, Any]) -> dict[str, Any]:
    """支撑位判定。

    支撑候选 = MA20 与「近 15 日最低价」。取其中**离现价最近的那条**（也就是较高的那条）：
    - 现价在它上方 ≤2% → 回踩到位（买点区间）；
    - 现价跌破它 ≥3% → 破位，不再提示买点。
    """
    close = _num(metrics.get("close"), 0.0)
    ma20 = _num(metrics.get("ma20"), 0.0)
    low15 = _num(metrics.get("low15"), 0.0)
    levels = [(ma20, "MA20"), (low15, "近15日低点")]
    levels = [(level, label) for level, label in levels if level > 0]
    if not close or not levels:
        return {"support": 0.0, "label": "", "distancePct": 0.0, "inBuyZone": False, "broken": False}
    support, label = max(levels, key=lambda item: item[0])
    if ma20 > 0 and low15 > 0 and abs(ma20 - low15) < 1e-12:
        label = "MA20/近15日低点"
    distance = (close - support) / support * 100.0
    return {
        "support": round(support, 10),
        "label": label if close >= support else "",
        "distancePct": round(distance, 2),
        "inBuyZone": 0 <= distance <= MM_SUPPORT_ZONE_PCT,
        "broken": distance <= -MM_SUPPORT_BREAK_PCT,
    }


# ── 候选源 ──────────────────────────────────────────────────────────────────
def board_candidates(
    payload: dict[str, Any],
    source_ids: Iterable[str],
    *,
    origin: str = "",
) -> list[dict[str, Any]]:
    """从任意榜单 payload 里抽候选：「同一个币取涨幅最大的那条」，并标出是否来自合约榜。

    涨幅榜与热门榜的**行结构是同一套**（``symbol`` / ``change`` / ``chain`` /
    ``contractAddress``），所以两路共用这一个抽取器，只是来源白名单不同。
    链与合约缺失时不用慌：``run_round`` 里的 ``resolve_market`` 会拿 Alpha 索引补。
    """
    wanted = {str(item) for item in source_ids}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list):
        return []
    merged: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "")
        if source_id not in wanted:
            continue
        rows = source.get("rows") if isinstance(source.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _symbol(row.get("symbol"))
            if not symbol:
                continue
            change_pct = _num(row.get("change"), 0.0)
            perp = str(row.get("marketType") or "") == "futures" or source_id in MM_PERP_SOURCES
            candidate = {
                "symbol": symbol,
                "name": _text(row.get("name") or symbol, 60),
                "changePct": change_pct,
                "price": _num(row.get("price"), 0.0),
                "turnoverUsd": _num(row.get("amount"), 0.0),
                "chain": _text(row.get("chain") or row.get("chainLabel"), 24),
                "contract": _text(row.get("contractAddress"), 96),
                "sourceId": source_id,
                "board": _text(source.get("title") or source_id, 40),
                "perp": perp,
                "perpSources": [source_id] if perp else [],
                "origin": origin,
                "origins": [origin] if origin else [],
            }
            previous = merged.get(symbol)
            if previous is None:
                merged[symbol] = candidate
                continue
            previous["perp"] = previous["perp"] or perp
            if perp and source_id not in previous["perpSources"]:
                previous["perpSources"].append(source_id)
            if change_pct > previous["changePct"]:
                keep_perp = previous["perp"]
                keep_perp_sources = previous["perpSources"]
                keep_origins = previous.get("origins") or []
                candidate["perp"] = keep_perp
                candidate["perpSources"] = keep_perp_sources
                candidate["origins"] = keep_origins
                merged[symbol] = candidate
            elif origin and origin not in (previous.get("origins") or []):
                previous.setdefault("origins", []).append(origin)
    return sorted(merged.values(), key=lambda item: -item["changePct"])


def gainers_candidates(gainers_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """涨幅榜候选（加密榜；股票榜不参与）。"""
    return board_candidates(gainers_payload, MM_GAINERS_SOURCES, origin="涨幅榜")


def hot_candidates(hot_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """热门榜候选（成交额 / 热度榜里的加密榜）。

    用户 2026-10-21 拍板加入：热门榜里满足五条判据的标的也要进做市监控。
    热门榜的行不一定带涨幅（成交额榜按金额排），``_num`` 会取到 0 —— 这不影响
    任何一道闸，只影响「同一个币取哪条」的取舍（涨幅榜那条会赢）。
    """
    return board_candidates(hot_payload, MM_HOT_SOURCES, origin="热门榜")


def alpha_metadata(index: dict[str, Any], symbol: str) -> dict[str, Any]:
    tokens = index.get("tokens") if isinstance(index, dict) else None
    if not isinstance(tokens, dict):
        return {}
    entry = tokens.get(symbol.upper())
    return entry if isinstance(entry, dict) else {}


def alpha_status_label(meta: dict[str, Any] | None) -> str:
    """这条币和 Alpha 的关系：「在列」还是「曾经收录、后来转正/下架」。

    用户 2026-10-20 明确要求**下架后转现货的也要纳入**（典型：BMT 先上 Alpha，
    后来转币安现货并从 Alpha 移除），所以这里只用来标注，不作为排除条件。
    `fullyDelisted` 不能当退市判据——BMT 也是 True，但它现在的交易在币安现货。
    """
    if not isinstance(meta, dict) or not meta:
        return ""
    if not meta.get("offline"):
        return "Alpha 在列"
    if meta.get("listingCex"):
        return "Alpha 已下架 · 已转币安现货"
    return "Alpha 已下架"


def alpha_gainers_candidates(
    alpha_index: dict[str, Any], *, min_change_pct: float = 20.0,
) -> list[dict[str, Any]]:
    """Alpha 板块自己的涨幅榜（用户在币安 App 里看到的那个 24h 涨幅）。

    主站涨幅榜只收 10 行，Alpha 币常常先在自己的板块里涨起来，所以这一路是补充候选源；
    后面的「有永续 + 市值 + 控盘 + 回撤」四道闸一道都不少，不会因此变吵。
    **已下架（offline）的不进这一路**——Alpha 板块里根本看不到它们，它们只能从主站涨幅榜进来。
    """
    tokens = alpha_index.get("tokens") if isinstance(alpha_index, dict) else None
    if not isinstance(tokens, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for symbol, meta in tokens.items():
        if not isinstance(meta, dict) or meta.get("offline"):
            continue
        change = _num(meta.get("change24h"), 0.0)
        if change < min_change_pct:
            continue
        candidates.append({
            "symbol": _symbol(symbol),
            "name": _text(meta.get("name") or symbol, 60),
            "changePct": change,
            "price": _num(meta.get("price"), 0.0),
            "turnoverUsd": _num(meta.get("volume24h"), 0.0),
            "chain": _text(meta.get("chain"), 24),
            "contract": _text(meta.get("contract"), 96),
            "sourceId": "alpha-list",
            "board": "Binance Alpha 24h 涨幅",
            "perp": False,
            "perpSources": [],
        })
    return candidates


def merge_candidates(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一个币只留一条：涨幅取最大，`perp` 与来源（榜单出处）取并集。"""
    merged: dict[str, dict[str, Any]] = {}

    def union_origins(target: dict[str, Any], extra: Iterable[Any]) -> None:
        bucket = [str(item) for item in (target.get("origins") or []) if item]
        if target.get("origin") and target["origin"] not in bucket:
            bucket.append(str(target["origin"]))
        for item in extra or ():
            if item and str(item) not in bucket:
                bucket.append(str(item))
        if bucket:
            target["origins"] = bucket
            target["origin"] = " / ".join(bucket)

    for group in groups:
        for candidate in group or ():
            symbol = candidate.get("symbol")
            if not symbol:
                continue
            previous = merged.get(symbol)
            if previous is None:
                merged[symbol] = dict(candidate)
                union_origins(merged[symbol], [])
                continue
            previous["perp"] = bool(previous.get("perp") or candidate.get("perp"))
            for source in candidate.get("perpSources") or []:
                if source not in previous["perpSources"]:
                    previous["perpSources"].append(source)
            if _num(candidate.get("changePct"), 0.0) > _num(previous.get("changePct"), 0.0):
                keep_perp = previous["perp"]
                keep_sources = previous["perpSources"]
                keep_board = candidate.get("board") or previous.get("board")
                merged[symbol] = dict(candidate)
                merged[symbol]["perp"] = keep_perp
                merged[symbol]["perpSources"] = keep_sources
                merged[symbol]["board"] = keep_board
                union_origins(merged[symbol], previous.get("origins") or [])
            else:
                union_origins(previous, candidate.get("origins") or [])
    return sorted(merged.values(), key=lambda item: -_num(item.get("changePct"), 0.0))


# ── 状态读写 ────────────────────────────────────────────────────────────────
def load_state(path: str = STATE_PATH) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any], path: str = STATE_PATH) -> None:
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    temp = f"{path}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temp, path)


# ── 只读复用：给别的板块（价格异动「爆红」等）拿「控盘达标」名单 ─────────────
_CONTROL_TARGETS_CACHE: dict[str, Any] = {"path": "", "mtime": -1.0, "at": 0.0, "value": {}}
_CONTROL_TARGETS_LOCK = threading.Lock()
#: 名单缓存时长。状态文件 5 分钟才写一次，30 秒足够新；弹窗与页面都要查它，
#: 每次去读 10KB JSON 太浪费（价格异动一页 120 行）。
CONTROL_TARGETS_TTL_SECONDS = 30.0


def control_targets(
    state_path: str = STATE_PATH,
    *,
    now: float | None = None,
    max_age: float = MM_WATCH_SECONDS,
    refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """观察池里**仍然满足**硬判据的标的：``{SYMBOL: {...}}``。

    给别的板块只读用（价格异动要据此「爆红」+ 标 Alpha）。**这里绝不重判**：
    控盘度、流通市值、「Alpha 收录 + 有永续」这三条都是 `run_round()` 写进 ``watch``
    的结论——两边各判一次，口径一改必然分叉（用户会看到「名册里有、弹窗不报」）。

    这里只做两件 **run_round 不管的事**：

    ① **阈值复核**：常量或环境变量改过之后，历史条目要按**当前**阈值重过一遍；
    ② **新鲜度与墓碑**：超过 ``max_age`` 的旧条目不认（市值早就变了），
       ``blocked`` 里的（拉盘后暴跌 ≥60%）永久不认。

    取值一律用 ``entry["controlPct"]`` / ``entry["marketCap"]``，缺字段就当不在名单。
    """
    path = state_path
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    now = time.time() if now is None else now
    with _CONTROL_TARGETS_LOCK:
        cached = dict(_CONTROL_TARGETS_CACHE)
        if (
            not refresh
            and cached["path"] == path
            and cached["mtime"] == mtime
            and now - _num(cached["at"], 0.0) < CONTROL_TARGETS_TTL_SECONDS
        ):
            return dict(cached["value"])

    state = load_state(path)
    watch = state.get("watch") if isinstance(state.get("watch"), dict) else {}
    blocked = state.get("blocked") if isinstance(state.get("blocked"), dict) else {}
    min_control = _env_float("XINGYUN_ALPHA_MM_MIN_CONTROL_PCT", MM_MIN_CONTROL_PCT)
    max_mcap = _env_float("XINGYUN_ALPHA_MM_MAX_MCAP_USD", MM_MAX_MARKET_CAP_USD)
    blocked_keys = {_symbol(key) for key in blocked}
    targets: dict[str, dict[str, Any]] = {}
    for symbol, entry in watch.items():
        if not isinstance(entry, dict):
            continue
        key = _symbol(symbol)
        if not key or key in blocked_keys:
            continue
        at = _num(entry.get("at"), 0.0)
        if not at or now - at > max_age:
            continue
        control = _num(entry.get("controlPct"), 0.0)
        market_cap = _num(entry.get("marketCap"), 0.0)
        # 市值拿不到（<=0）不能当「小市值」放行，那是最容易造出假爆红的一步。
        if control < min_control or market_cap <= 0 or market_cap > max_mcap:
            continue
        targets[key] = {
            "symbol": key,
            "name": _text(entry.get("name"), 60),
            "chain": _text(entry.get("chain"), 24),
            "contract": _text(entry.get("contract"), 96),
            "network": _text(entry.get("network"), 24),
            "url": _text(entry.get("url"), 400),
            "controlPct": control,
            "marketCap": market_cap,
            "changePct": _num(entry.get("changePct"), 0.0),
            "drawdownPct": _num(entry.get("drawdownPct"), 0.0),
            "origin": _text(entry.get("origin"), 60),
            "at": at,
            # Alpha 合约身份由入选条件保证（见模块文档第 2 条）。
            "alphaListed": True,
        }
    with _CONTROL_TARGETS_LOCK:
        _CONTROL_TARGETS_CACHE.update(
            {"path": path, "mtime": mtime, "at": now, "value": dict(targets)}
        )
    return targets


# ── 主流程 ──────────────────────────────────────────────────────────────────
def _seed_payload(cand: dict[str, Any], *, metrics: dict[str, Any], control: dict[str, Any],
                  support: dict[str, Any], market_cap: float, now_ms: int,
                  alpha_status: str = "", url: str = "") -> dict[str, Any]:
    symbol = cand["symbol"]
    body_parts = [
        cand.get("name") if cand.get("name") and cand["name"].upper() != symbol else "",
        f"{cand.get('chain') or '链上'} 链",
        alpha_status,
        # 从哪个榜挑出来的（涨幅榜 / 热门榜 / Alpha 板块）：用户要知道是哪个榜先动。
        (f"出自{cand['origin']}" if cand.get("origin") else ""),
        f"控盘 {control['controlPct']:.1f}%",
        f"流通市值 {market_cap / 1e4:.0f} 万美元",
        f"24h {cand['changePct']:+.1f}%",
        f"历史高点回撤 {metrics['drawdownPct']:.0f}%",
    ]
    if support.get("support"):
        body_parts.append(f"支撑 {support['label']}（回踩 {support['distancePct']:+.1f}%）")
    body_parts.append("项目方高控盘 + 有永续合约，拉盘前先想清楚能不能跑")
    return {
        "key": f"{MM_KEY_PREFIX_SEED}{symbol}",
        "kind": "做市商控盘·入选",
        "source": "Alpha 控盘监控",
        "sourceLabel": "MM",
        "sourceId": "alpha-mm-control",
        "board": "",
        "title": f"做市商控盘入选：{symbol}",
        "body": " / ".join(part for part in body_parts if part),
        "chain": cand.get("chain") or "",
        "url": url or cand.get("url") or binance_alpha_token_url(
            cand.get("chain"), cand.get("contract")),
        "time": now_ms,
        "expiresAt": now_ms + 6 * 3600 * 1000,
        "priority": "做市商控盘",
        "queuePriority": 82,
        "speech": (
            f"做市商控盘提醒，{symbol}，控盘 {control['controlPct']:.0f}%，"
            f"流通市值 {market_cap / 1e4:.0f} 万美元，24 小时涨幅 {cand['changePct']:.0f}%。"
            "高控盘加合约，别追高。"
        ),
        "mmControlPct": control["controlPct"],
        "mmMarketCapUsd": market_cap,
        "alphaPhase": alpha_status,
    }


def _buy_payload(cand: dict[str, Any], *, metrics: dict[str, Any], control: dict[str, Any],
                 support: dict[str, Any], now_ms: int, url: str = "") -> dict[str, Any]:
    symbol = cand["symbol"]
    body = (
        f"{metrics['close']:.8g} 回踩 {support['label']} 支撑 {support['support']:.8g}"
        f"（{support['distancePct']:+.2f}%） / 结构向上（收盘>MA20>MA60）"
        f" / 控盘 {control['controlPct']:.1f}% / ATH 回撤 {metrics['drawdownPct']:.0f}%"
        " / 破了支撑就撤"
    )
    return {
        "key": f"{MM_KEY_PREFIX_BUY}{symbol}:{support['support']:.8g}",
        "kind": "做市商控盘·支撑买点",
        "source": "Alpha 控盘监控",
        "sourceLabel": "MM",
        "sourceId": "alpha-mm-control",
        "board": "",
        "title": f"控盘币支撑买点：{symbol}",
        "body": body,
        "chain": cand.get("chain") or "",
        "url": url or cand.get("url") or binance_alpha_token_url(
            cand.get("chain"), cand.get("contract")),
        "time": now_ms,
        "expiresAt": now_ms + 6 * 3600 * 1000,
        "priority": "做市商控盘买点",
        "queuePriority": 86,
        "speech": (
            f"{symbol} 回踩 {support['label']} 支撑，"
            f"上涨结构里的买点，控盘 {control['controlPct']:.0f}%。"
        ),
        "mmControlPct": control["controlPct"],
        "mmSupportPrice": support["support"],
    }


def run_round(
    *,
    gainers_payload: dict[str, Any],
    alpha_index: dict[str, Any],
    alert: Callable[[dict[str, Any]], dict[str, Any]],
    log: Callable[[str], None] | None = None,
    state_path: str = STATE_PATH,
    now: float | None = None,
    fetchers: dict[str, Callable[..., Any]] | None = None,
    perp_symbols: Iterable[str] | None = None,
    hot_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """跑一轮：涨幅榜 + 热门榜 → Alpha ∩ 有合约 → 市值/控盘/回撤 → 入选提醒；观察池 → 支撑买点。"""
    now = time.time() if now is None else now
    now_ms = int(now * 1000)
    fetchers = fetchers or {}
    pairs_fetcher = fetchers.get("pairs", fetch_dex_pairs)
    candles_fetcher = fetchers.get("candles", fetch_day_candles)
    if isinstance(fetchers.get("securityBatch"), Callable):
        security_batch = fetchers["securityBatch"]
    elif isinstance(fetchers.get("security"), Callable):
        # 测试/探针注入的单发实现：就地包成批量形状，逐个调用
        single = fetchers["security"]
        security_batch = lambda chain, contracts: (  # noqa: E731
            {str(contract).casefold(): single(chain, contract) for contract in contracts}
        )
    else:
        security_batch = fetch_token_security_batch
    # GoPlus 免费额度按 IP 计：一轮最多发这么多次**请求**（同一链上的候选共享一次），
    # 剩下的留到下一轮（持仓缓存 6 小时 / 失败负缓存 15 分钟）。
    control_budget = int(_env_float("XINGYUN_ALPHA_MM_CONTROL_PER_ROUND", 2))
    control_requests = 0

    def emit(message: str) -> None:
        if log:
            log(message)

    max_mcap = _env_float("XINGYUN_ALPHA_MM_MAX_MCAP_USD", MM_MAX_MARKET_CAP_USD)
    min_control = _env_float("XINGYUN_ALPHA_MM_MIN_CONTROL_PCT", MM_MIN_CONTROL_PCT)
    max_drawdown = _env_float("XINGYUN_ALPHA_MM_MAX_DRAWDOWN_PCT", MM_MAX_DRAWDOWN_PCT)

    state = load_state(state_path)
    seeds = state.setdefault("seeds", {})
    buys = state.setdefault("buys", {})
    blocked = state.setdefault("blocked", {})
    watch = state.setdefault("watch", {})
    holder_cache = state.setdefault("holders", {})
    metrics_cache = state.setdefault("metrics", {})

    stats = {"candidates": 0, "alpha": 0, "alphaOffline": 0, "perp": 0, "marketCap": 0,
             "control": 0, "drawdown": 0, "seeded": 0, "buyPoints": 0,
             "blocked": 0, "skipped": []}

    def resolve_market(cand: dict[str, Any]) -> dict[str, Any]:
        """补齐链/合约/池子/市值：Alpha 索引优先，OKX DEX 榜自带合约。"""
        meta = alpha_metadata(alpha_index, cand["symbol"])
        chain = cand.get("chain") or _text(meta.get("chain"), 24)
        contract = cand.get("contract") or _text(meta.get("contract"), 96)
        market_cap = _num(meta.get("marketCap"), 0.0)
        network = network_name(chain)
        cache_key = f"{network}:{chain_key(contract)}"
        pairs: list[dict[str, Any]] = []
        if network and contract:
            try:
                pairs = pairs_fetcher(network, contract) or []
            except Exception as exc:  # 网络问题不该让整轮失败
                emit(f"pairs failed {cand['symbol']}: {_text(exc, 80)}")
                pairs = []
        pools = {str(pair.get("pairAddress") or "").strip().casefold()
                 for pair in pairs if isinstance(pair, dict)}
        pools.discard("")
        if market_cap <= 0:
            for pair in pairs:
                if isinstance(pair, dict):
                    market_cap = max(market_cap, _num(pair.get("marketCap"), 0.0))
        best_pool = ""
        best_liquidity = -1.0
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            liquidity = _num((pair.get("liquidity") or {}).get("usd"), 0.0)
            if liquidity > best_liquidity:
                best_liquidity = liquidity
                best_pool = str(pair.get("pairAddress") or "").strip()
        return {
            "chain": chain,
            "chainKey": chain_key(chain),
            "network": network,
            "contract": contract,
            "marketCap": market_cap,
            "pools": pools,
            "pool": best_pool,
            "cacheKey": cache_key,
            "name": _text(meta.get("name") or cand.get("name") or cand["symbol"], 60),
            "url": binance_alpha_token_url(chain, contract),
        }

    def metrics_for(cand: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        cache_key = market["cacheKey"]
        cached = metrics_cache.get(cache_key)
        if isinstance(cached, dict) and now - _num(cached.get("at"), 0) < MM_METRICS_TTL_SECONDS:
            return cached
        if not market["network"] or not market["pool"]:
            return {"error": "没有可用的链上池子"}
        try:
            rows = candles_fetcher(market["network"], market["pool"]) or []
        except Exception as exc:
            emit(f"candles failed {cand['symbol']}: {_text(exc, 80)}")
            return {"error": "日线抓取失败"}
        candles = parse_day_candles(rows)
        if len(candles) < MM_MIN_DAY_CANDLES:
            return {"error": f"日线不足（{len(candles)} 根）"}
        metrics = candle_metrics(candles)
        metrics["at"] = now
        metrics_cache[cache_key] = metrics
        return metrics

    # 1) 涨幅榜 + 热门榜 → Alpha ∩ 有合约 → 市值闸：全是本地判断，先把候选筛干净再动网络
    prepared: list[dict[str, Any]] = []
    min_change = _env_float("XINGYUN_ALPHA_MM_ALPHA_BOARD_MIN_GAIN_PCT", 20.0)
    perp_set = {_symbol(item) for item in (perp_symbols or ()) if _symbol(item)}
    candidates = merge_candidates(
        gainers_candidates(gainers_payload),
        hot_candidates(hot_payload or {}),
        alpha_gainers_candidates(alpha_index, min_change_pct=min_change),
    )
    for cand in candidates:
        stats["candidates"] += 1
        symbol = cand["symbol"]
        if symbol in blocked:
            stats["blocked"] += 1
            continue
        meta = alpha_metadata(alpha_index, symbol)
        if not meta:
            continue
        stats["alpha"] += 1
        if meta.get("offline"):
            stats["alphaOffline"] += 1
        if not cand["perp"] and symbol in perp_set:
            # 主站涨幅榜没抓到它，但币安 U 本位确实有这个合约：条件 2 满足
            cand["perp"] = True
            cand["perpSources"].append("binance-perp-list")
        if not cand["perp"]:
            stats["skipped"].append(f"{symbol}: 没有永续合约")
            continue
        stats["perp"] += 1
        market = resolve_market(cand)
        if market["chain"] and not cand.get("chain"):
            cand["chain"] = market["chain"]
        if market["name"]:
            cand["name"] = market["name"]
        if not market["chain"] or not market["contract"]:
            stats["skipped"].append(f"{symbol}: 缺链或合约地址")
            continue
        if market["marketCap"] <= 0:
            stats["skipped"].append(f"{symbol}: 流通市值未知")
            continue
        if market["marketCap"] > max_mcap:
            stats["skipped"].append(f"{symbol}: 流通市值 {market['marketCap'] / 1e4:.0f} 万 > 阈值")
            continue
        stats["marketCap"] += 1
        prepared.append({"cand": cand, "market": market})

    # 1b) 控盘度：按链**批量**核验（一条链一轮一次请求）。GoPlus 免费额度只够这个用法。
    pending_by_chain: dict[str, list[dict[str, Any]]] = {}
    for item in prepared:
        if control_cache_settled(holder_cache.get(item["market"]["cacheKey"]), now):
            continue
        pending_by_chain.setdefault(item["market"]["chainKey"], []).append(item)
    for chain_name, items in pending_by_chain.items():
        batch_size = 100 if isinstance(fetchers.get("securityBatch"), Callable) else goplus_batch_size()
        chunks = [items[index:index + max(1, batch_size)] for index in range(0, len(items), max(1, batch_size))]
        limited = False
        for chunk in chunks:
            if control_requests >= max(1, control_budget):
                # 只打标记，不进 skipped：1c 会统一给出一条原因，避免同一币重复两条
                for item in chunk:
                    item["deferred"] = "控盘核验配额留到下一轮"
                continue
            control_requests += 1
            contracts = [item["market"]["contract"] for item in chunk]
            try:
                results = security_batch(chunk[0]["market"]["chain"], contracts) or {}
            except Exception as exc:
                emit(f"security batch failed {chain_name}: {_text(exc, 80)}")
                results = {}
            for item in chunk:
                market = item["market"]
                raw = (
                    results.get(str(market["contract"]).casefold())
                    or results.get(market["contract"])
                    or {"error": "持仓核验无结果"}
                )
                entry = control_from_security(raw, pool_addresses=market["pools"], now=now)
                holder_cache[market["cacheKey"]] = entry
                if "限流" in str(entry.get("error") or ""):
                    limited = True
        if limited:
            emit("GoPlus 限流，本轮停止持仓核验")
            break

    # 1c) 逐条评估：控盘 → 回撤 → 入选
    for item in prepared:
        cand, market = item["cand"], item["market"]
        symbol = cand["symbol"]
        control = holder_cache.get(market["cacheKey"])
        if not isinstance(control, dict):
            stats["skipped"].append(
                f"{symbol}: {item.get('deferred') or '控盘核验本轮未执行'}"
            )
            continue
        if control.get("error"):
            stats["skipped"].append(f"{symbol}: 控盘度未核验（{control['error']}）")
            continue
        if _num(control.get("controlPct"), 0.0) < min_control:
            stats["skipped"].append(
                f"{symbol}: 控盘 {_num(control.get('controlPct'), 0.0):.1f}% < {min_control:.0f}%"
            )
            continue
        stats["control"] += 1

        metrics = metrics_for(cand, market)
        if metrics.get("error"):
            stats["skipped"].append(f"{symbol}: {metrics['error']}")
            continue
        if _num(metrics.get("drawdownPct"), 100.0) >= max_drawdown:
            blocked[symbol] = {
                "at": now,
                "reason": f"历史高点回撤 {metrics['drawdownPct']:.0f}%（≥{max_drawdown:.0f}%）",
                "peak": metrics.get("peak"),
                "drawdownPct": metrics.get("drawdownPct"),
            }
            stats["blocked"] += 1
            emit(f"{symbol} 拉盘后暴跌已排除：回撤 {metrics['drawdownPct']:.0f}%")
            continue
        stats["drawdown"] += 1

        support = support_state(metrics)
        watch[symbol] = {
            "at": now,
            "name": cand["name"],
            "chain": market["chain"],
            "contract": market["contract"],
            "network": market["network"],
            "pool": market["pool"],
            "cacheKey": market["cacheKey"],
            "marketCap": market["marketCap"],
            "controlPct": _num(control.get("controlPct"), 0.0),
            "changePct": cand["changePct"],
            "url": market["url"],
            # 这个标的当初是从哪个榜被挑出来的（涨幅榜 / 热门榜 / Alpha 板块），
            # 做市监控板块要照它原样展示，方便回看「是哪个榜先动」。
            "origin": cand.get("origin") or "",
            "origins": cand.get("origins") or [],
            "board": cand.get("board") or "",
            "drawdownPct": _num(metrics.get("drawdownPct"), 0.0),
            "trendUp": bool(metrics.get("trendUp")),
            "support": support.get("support") or 0.0,
            "supportLabel": support.get("label") or "",
            "inBuyZone": bool(support.get("inBuyZone")),
            "broken": bool(support.get("broken")),
            "ma20": _num(metrics.get("ma20"), 0.0),
            "ma60": _num(metrics.get("ma60"), 0.0),
            "price": _num(metrics.get("close"), 0.0),
            "peak": _num(metrics.get("peak"), 0.0),
        }
        last_seed = _num((seeds.get(symbol) or {}).get("at"), 0.0) if isinstance(seeds.get(symbol), dict) else 0.0
        if now - last_seed < MM_SEED_COOLDOWN_SECONDS:
            continue
        payload = _seed_payload(cand, metrics=metrics, control=control,
                                support=support, market_cap=market["marketCap"], now_ms=now_ms,
                                alpha_status=alpha_status_label(meta), url=market["url"])
        result = alert(payload) or {}
        if result.get("ok"):
            seeds[symbol] = {"at": now, "controlPct": payload["mmControlPct"],
                             "marketCap": market["marketCap"], "changePct": cand["changePct"]}
            stats["seeded"] += 1
            emit(f"{symbol} 入选提醒已投递（控盘 {payload['mmControlPct']:.1f}%）")
        else:
            stats["skipped"].append(f"{symbol}: 入选提醒被拒（{_text(result.get('reason') or result.get('error'), 40)}）")

    # 2) 观察池 → 回踩支撑买点
    for symbol, entry in list(watch.items()):
        if not isinstance(entry, dict):
            watch.pop(symbol, None)
            continue
        if now - _num(entry.get("at"), 0) > MM_WATCH_SECONDS:
            watch.pop(symbol, None)
            continue
        last_buy = buys.get(symbol) if isinstance(buys.get(symbol), dict) else {}
        if now - _num(last_buy.get("at"), 0) < MM_BUY_COOLDOWN_SECONDS:
            continue
        market = {
            "chain": entry.get("chain"),
            "chainKey": chain_key(entry.get("chain")),
            "network": entry.get("network"),
            "contract": entry.get("contract"),
            "marketCap": _num(entry.get("marketCap"), 0.0),
            "pools": set(),
            "pool": entry.get("pool"),
            "cacheKey": entry.get("cacheKey"),
            "name": entry.get("name"),
            "url": entry.get("url"),
        }
        cand = {"symbol": symbol, "name": entry.get("name"), "chain": entry.get("chain"),
                "url": entry.get("url"), "changePct": _num(entry.get("changePct"), 0.0)}
        metrics = metrics_for(cand, market)
        if metrics.get("error"):
            continue
        if _num(metrics.get("drawdownPct"), 100.0) >= max_drawdown:
            blocked[symbol] = {"at": now, "reason": "历史高点回撤超阈值",
                              "drawdownPct": metrics.get("drawdownPct")}
            watch.pop(symbol, None)
            continue
        if not metrics.get("trendUp"):
            continue
        support = support_state(metrics)
        if not support.get("support") or not support.get("inBuyZone"):
            continue
        if last_buy.get("key") == f"{support['support']:.8g}" and now - _num(last_buy.get("at"), 0) < 24 * 3600:
            continue
        control = {"controlPct": _num(entry.get("controlPct"), 0.0)}
        payload = _buy_payload(cand, metrics=metrics, control=control, support=support,
                               now_ms=now_ms, url=(watch.get(symbol) or {}).get("url") or "")
        result = alert(payload) or {}
        if result.get("ok"):
            buys[symbol] = {"at": now, "key": f"{support['support']:.8g}",
                            "support": support["support"], "label": support["label"]}
            stats["buyPoints"] += 1
            emit(f"{symbol} 支撑买点已投递（{support['label']} {support['support']:.8g}）")

    # 3) 收尾：瘦身 + 落盘
    state["updatedAt"] = now_ms
    _prune_cache(holder_cache, now, MM_HOLDER_TTL_SECONDS * 4)
    _prune_cache(metrics_cache, now, MM_METRICS_TTL_SECONDS * 4)
    if len(seeds) > 500:
        state["seeds"] = dict(sorted(seeds.items(), key=lambda item: -_num(item[1].get("at"), 0))[:500])
    if len(blocked) > 500:
        state["blocked"] = dict(sorted(blocked.items(), key=lambda item: -_num(item[1].get("at"), 0))[:500])
    if len(watch) > 60:
        state["watch"] = dict(sorted(watch.items(), key=lambda item: -_num(item[1].get("at"), 0))[:60])
    try:
        save_state(state, state_path)
    except OSError as exc:
        emit(f"state save failed: {_text(exc, 60)}")
    stats["watch"] = len(state.get("watch") or {})
    return stats


def _prune_cache(cache: dict[str, Any], now: float, max_age: float) -> None:
    for key in [key for key, value in cache.items()
                if not isinstance(value, dict) or now - _num(value.get("at"), 0) > max_age]:
        cache.pop(key, None)
