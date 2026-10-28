#!/usr/bin/env python3
"""监控页「Dev 发币」子板块：盯住给定的 Dev 地址，一有新币发出来就提醒。

## 要解决什么问题

一个 Dev 地址（开发者/庄家钱包）发新币的那一刻，是这个币最早、也最有价值的信号：
还没上任何榜单、没有讨论、没有流动性，但盘子是谁做的已经确定了。所以这里盯的不是
「链上出现了什么新币」（那是战壕榜的活），而是**「我关心的这个地址又发币了」**。

## 怎么判断「发币」

链上有两条路造出代币合约，两条都必须覆盖 —— 实测 Robinhood 链近 260 个区块里
**一个** EOA 直接部署的合约都没有：这条链的币全部由发射台合约代发。只看「EOA 自己
部署」等于什么都抓不到。

  1. **直接部署**（`via="deploy"`）：出账 tx 的 ``created_contract`` 非空。
     拿一次地址交易列表就能看出来，便宜。
  2. **工厂/发射台代发**（`via="factory"`）：tx 收件人是个合约，真正的 CREATE 发生在
     tx **内部**（由发射台执行）。要逐笔看该 tx 的 internal transactions 里有没有
     ``type in ("create", "create2")``。贵（每笔一次请求），所以只对「还没检查过的
     出账 tx」做，并把检查过的 txHash 永久记档，绝不重复查。

## 「级别」标签怎么来的

用户口径（2026-10-15 拍板，单位美元市值）：

  顶级       ≥ 1 亿        —— 发过大几千万/过亿的盘子
  精品   1000 万 ~ 1 亿     —— 一两千万左右
  优质    100 万 ~ 1000 万  —— 1000 万以内
  普通 / 流水  < 100 万      —— 绝大多数

口径是**历史峰值市值**，不是当前市值：一个发过亿级盘子、现在只剩几百万的 Dev，
按当前市值会被判成「优质」，那和用户的判断完全相反。

峰值怎么算：DexScreener 给当前价与当前市值 → 反推流通量；GeckoTerminal 的日线 OHLCV
给历史最高价 → ``峰值市值 = 历史最高价 × 流通量``。两条都拿不到就退回当前市值，
并在行里标明 ``peakSource``（不编数字）。

## 为什么单独一个库

和 ``sandwich_detector`` 一样开自己的 SQLite：Dev 地址配置、发币记录、已查过的 tx
都跟 auth_db / 聪明钱库无关，挤进同一个库只会互相抢锁（``AUTH_DB_LOCK`` 的教训）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
#: 支持的四条链（用户 2026-10-15 拍板「四链都做」）。
#: BSC 没有可用的 Blockscout 实例，只能走 BscScan（需要 BSCSCAN_API_KEY）。
EVM_CHAINS = ("bsc", "ethereum", "base", "robinhood")

CHAIN_META: dict[str, dict[str, Any]] = {
    "bsc": {
        "label": "BSC",
        "native": "BNB",
        "indexer": "bscscan",
        "chainId": 56,
        "dexscreener": "bsc",
        "geckoterminal": "bsc",
        "explorerTx": "https://bscscan.com/tx/{}",
        "explorerToken": "https://bscscan.com/token/{}",
        "gmgn": "bsc",
    },
    "ethereum": {
        "label": "Ethereum",
        "native": "ETH",
        "indexer": "blockscout",
        "blockscout": "https://eth.blockscout.com",
        "chainId": 1,
        "dexscreener": "ethereum",
        "geckoterminal": "eth",
        "explorerTx": "https://etherscan.io/tx/{}",
        "explorerToken": "https://etherscan.io/token/{}",
        "gmgn": "eth",
    },
    "base": {
        "label": "Base",
        "native": "ETH",
        "indexer": "blockscout",
        "blockscout": "https://base.blockscout.com",
        "chainId": 8453,
        "dexscreener": "base",
        "geckoterminal": "base",
        "explorerTx": "https://basescan.org/tx/{}",
        "explorerToken": "https://basescan.org/token/{}",
        "gmgn": "base",
    },
    "robinhood": {
        "label": "Robinhood",
        "native": "ETH",
        "indexer": "blockscout",
        "blockscout": "https://robinhoodchain.blockscout.com",
        "chainId": 4663,
        "dexscreener": "robinhood",
        "geckoterminal": "robinhood",
        "explorerTx": "https://robinhoodchain.blockscout.com/tx/{}",
        "explorerToken": "https://robinhoodchain.blockscout.com/token/{}",
        "gmgn": "robinhood",
    },
}

CHAIN_ALIASES = {
    "bsc": "bsc", "bnb": "bsc", "binance": "bsc", "56": "bsc", "币安": "bsc",
    "eth": "ethereum", "ethereum": "ethereum", "1": "ethereum", "以太坊": "ethereum",
    "base": "base", "8453": "base",
    "robinhood": "robinhood", "hood": "robinhood", "4663": "robinhood",
}
EVM_ADDRESS_RE = re.compile(r"^0[xX][0-9a-fA-F]{40}$")
#: tx 的 ``to`` 为空 = 合约创建（EVM 的定义，与索引器无关）。
CREATE_TX_TYPES = ("create", "create2", "create3")

#: 级别阈值（美元），从高到低。``minUsd`` 是这一档的下界。
DEV_TIER_STEPS: tuple[tuple[str, str, float], ...] = (
    ("legendary", "顶级", 100_000_000.0),
    ("premium", "精品", 10_000_000.0),
    ("quality", "优质", 1_000_000.0),
    ("flow", "普通 / 流水", 0.0),
)
DEV_TIER_LABELS = {tier: label for tier, label, _ in DEV_TIER_STEPS}
DEV_TIER_MINS = {tier: floor for tier, _, floor in DEV_TIER_STEPS}

DEFAULT_POLL_SECONDS = 120.0
#: 单个 Dev 一轮最多做多少次「逐笔内部交易」查询（每次 1 个请求，是唯一的贵操作）。
DEFAULT_INTERNAL_LOOKUPS = 12
#: 首次回扫翻几页地址交易列表（每页 50 条）。回溯深度与请求数线性相关。
DEFAULT_BACKFILL_PAGES = 6
#: 首次回扫最多补做多少次内部交易查询（历史发射台发币只能这样挖出来，但有上限）。
DEFAULT_BACKFILL_INTERNAL_LOOKUPS = 40
#: 一轮最多富化多少个代币的市场数据。
DEFAULT_MARKET_LOOKUPS = 8
#: 市场数据多久之后允许刷新一次（峰值只增不减，刷新是为了拿到更高的顶）。
MARKET_REFRESH_SECONDS = 30 * 60
DEFAULT_RESULTS_LIMIT = 120
#: 排队的代币多久之内算「刚发出来」（弹窗描述用）。
FRESH_SECONDS = 6 * 3600
HTTP_TIMEOUT = (5, 20)
ALERTS_SETTING_KEY = "alertsEnabled"
BSCSCAN_KEY_ENV = "BSCSCAN_API_KEY"
BSCSCAN_BASE_ENV = "BSCSCAN_API_BASE"
#: BscScan 自家 V1 接口（api.bscscan.com/api）已于 2025-12 下线，官方接任者就是
#: Etherscan V2 —— 必须带 chainid 才能指定链。还按老地址打，只会拿到官网 HTML。
DEFAULT_BSCSCAN_BASE = "https://api.etherscan.io/v2/api"
BSCSCAN_CHAIN_ID = 56
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def normalize_chain(value: Any) -> str:
    key = str(value or "").strip().lower()
    return CHAIN_ALIASES.get(key, key if key in EVM_CHAINS else "")


def chain_label(value: Any) -> str:
    """链的人话名字（``robinhood`` → ``Robinhood``）；不认识就把原值还回去。"""
    key = normalize_chain(value)
    if key:
        return str(CHAIN_META[key]["label"])
    return clean_text(value, 20)


def chain_options() -> list[dict[str, str]]:
    """给前端表单用的链清单（含「该链此刻能不能查到」的说明）。"""
    rows = []
    for chain in EVM_CHAINS:
        meta = CHAIN_META[chain]
        available, note = chain_availability(chain)
        rows.append({
            "id": chain,
            "label": meta["label"],
            "native": meta["native"],
            "available": available,
            "note": note,
        })
    return rows


def chain_availability(chain: Any) -> tuple[bool, str]:
    """这条链现在能不能查？返回 ``(可用, 不可用的原因)``。

    BSC 走 BscScan，**配了 key 也不等于能查**（见 ``_bsc_capability``），所以这里
    要真探一次；探不到就把原因原样交给前端，让用户加地址前就知道，而不是加完之
    后永远等不到提醒（那种「静默失败」最难排查）。
    """
    key = normalize_chain(chain)
    if not key:
        return False, "不支持的链"
    if CHAIN_META[key]["indexer"] != "bscscan":
        return True, ""
    if not bscscan_api_key():
        return False, "未配置 BSCSCAN_API_KEY，BSC 查不到该地址的合约创建"
    return _bsc_capability()


#: ``(可用, 原因, 记录时间, 是否定论)``。定论（可用 / 免费档不支持）永久缓存，
#: 网络抖动这类非定论结论只缓存一会儿，下次建快照再探。
_BSC_PROBE: tuple[bool, str, float, bool] | None = None
_BSC_PROBE_LOCK = threading.Lock()
BSC_PROBE_RETRY_SECONDS = 600.0


def _bsc_capability() -> tuple[bool, str]:
    """BscScan 到底能不能查 BSC？只能真打一次才知道。

    实测（2026-10-16，本机 key 在 ETH / Polygon 上正常）：
    BscScan 自家 V1 接口（``api.bscscan.com/api``）已下线，任何请求都返回官网
    HTML；Etherscan V2 把 BSC（chainid=56）划进**付费链**，免费档一律回
    ``Free API access is not supported for this chain``。所以「有 key」和「能查」
    是两件事 —— 不探测，用户加完 BSC 地址会一直白等。
    """
    global _BSC_PROBE
    now = time.time()
    cached = _BSC_PROBE
    if cached and (cached[3] or now - cached[2] < BSC_PROBE_RETRY_SECONDS):
        return cached[0], cached[1]
    with _BSC_PROBE_LOCK:
        cached = _BSC_PROBE
        if cached and (cached[3] or now - cached[2] < BSC_PROBE_RETRY_SECONDS):
            return cached[0], cached[1]
        try:
            # 最省的一次调用：随便问一个空地址的一笔交易。免费档能用就回空集（不报错），
            # 不能用就回那条「付费链」提示 —— 一次就能分辨。
            _bscscan_get({
                "module": "account", "action": "txlist",
                "address": "0x" + "0" * 40, "page": 1, "offset": 1, "sort": "desc",
            })
            _BSC_PROBE = (True, "", now, True)
        except IndexerError as exc:
            text = str(exc)
            if "Free API access is not supported" in text:
                _BSC_PROBE = (
                    False,
                    "BscScan 免费档不支持 BSC（Etherscan V2 把 chainid=56 划为付费链）",
                    now,
                    True,
                )
            else:
                # 网络抖动 / key 无效：不下定论，只短暂缓存，下次快照再探。
                return False, text[:160]
        return _BSC_PROBE[0], _BSC_PROBE[1]


def bscscan_api_key() -> str:
    return str(os.getenv(BSCSCAN_KEY_ENV, "") or "").strip()


def bscscan_base() -> str:
    return str(os.getenv(BSCSCAN_BASE_ENV, "") or "").strip() or DEFAULT_BSCSCAN_BASE


def truthy(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "off", "no", "disabled"}


def is_evm_address(value: Any) -> bool:
    return bool(EVM_ADDRESS_RE.match(str(value or "").strip()))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # NaN 归零


def clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def runtime_dir() -> Path:
    configured = str(os.getenv("XINGYUN_RUNTIME_DIR", "") or "").strip()
    root = Path(__file__).resolve().parent
    return Path(configured).expanduser().resolve() if configured else root / ".runtime-cache"


def default_db_path() -> Path:
    return runtime_dir() / "dev_launch_monitor.sqlite"


def explorer_tx_url(chain: str, tx_hash: Any) -> str:
    template = str((CHAIN_META.get(chain) or {}).get("explorerTx") or "")
    return template.format(tx_hash) if template and tx_hash else ""


def explorer_token_url(chain: str, token: Any) -> str:
    template = str((CHAIN_META.get(chain) or {}).get("explorerToken") or "")
    return template.format(token) if template and token else ""


def gmgn_token_url(chain: str, token: Any) -> str:
    """GMGN 站内交易页：跟榜单页/热搜弹窗同口径（用户在 GMGN 上下单）。"""
    meta = CHAIN_META.get(chain) or {}
    gmgn_chain = str(meta.get("gmgn") or "").strip()
    if not gmgn_chain or not token:
        return ""
    return f"https://gmgn.ai/{gmgn_chain}/token/{token}"


def money_text(value: Any) -> str:
    """美元速记：$1.20M / $8.50K / $120。用于卡片与弹窗正文。"""
    amount = to_float(value)
    if amount <= 0:
        return ""
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.2f}K"
    return f"${amount:,.0f}"


def tier_for_market_cap(peak_usd: Any) -> dict[str, Any]:
    """按历史峰值市值定级。

    ``0``（没发过任何有市值的币）落在最后一档 —— 那正是用户说的「绝大多数」。
    返回整档对象而不是字符串，前端才好直接拿 label 与阈值做展示。
    """
    peak = max(0.0, to_float(peak_usd))
    for tier, label, floor in DEV_TIER_STEPS:
        if peak >= floor:
            return {
                "tier": tier, "label": label, "minUsd": floor,
                "peakMarketCapUsd": round(peak, 2), "peakMarketCapText": money_text(peak) or "$0",
            }
    return {
        "tier": "flow", "label": DEV_TIER_LABELS["flow"], "minUsd": 0.0,
        "peakMarketCapUsd": round(peak, 2), "peakMarketCapText": money_text(peak) or "$0",
    }


# --------------------------------------------------------------------------- #
# 索引器：地址 → 它发过哪些币
# --------------------------------------------------------------------------- #
_SESSION = None
_SESSION_LOCK = threading.Lock()


def _session():
    """懒建 requests.Session —— import 本模块不该产生网络依赖。

    ``trust_env=True`` 是默认值且必须保留：外网抓取一律跟随系统代理，代理端口会变。
    """
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            import requests

            _SESSION = requests.Session()
        return _SESSION


def _blockscout_headers(base: str) -> dict[str, str]:
    """Blockscout 的公共实例前面挂着 Cloudflare。

    实测（2026-10-15）：不带浏览器 UA / Referer 一律 403，`/api?module=...` 那套老
    v1 接口更是直接吃 Cloudflare 挑战页（返回 HTML）；只有 ``/api/v2/...`` 带齐
    UA + Accept + Referer 才稳定 200。
    """
    return {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
        "Referer": base.rstrip("/") + "/",
    }


class IndexerError(RuntimeError):
    """索引器不可用。调用方一律降级，绝不把「查不到」当成「没发过币」。"""


def _blockscout_get(chain: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    base = str(CHAIN_META[chain].get("blockscout") or "")
    if not base:
        raise IndexerError(f"{chain} 没有 Blockscout 实例")
    try:
        response = _session().get(
            f"{base}{path}",
            params=dict(params or {}),
            headers=_blockscout_headers(base),
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise IndexerError(f"{chain} Blockscout 请求失败：{str(exc)[:120]}") from exc
    if not isinstance(payload, dict):
        raise IndexerError(f"{chain} Blockscout 返回了非对象响应")
    return payload


def _bscscan_get(params: dict[str, Any]) -> Any:
    key = bscscan_api_key()
    if not key:
        raise IndexerError("未配置 BSCSCAN_API_KEY")
    query = {"chainid": BSCSCAN_CHAIN_ID, "apikey": key, **params}
    try:
        response = _session().get(
            bscscan_base(), params=query,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        raise IndexerError(f"BscScan 请求失败：{str(exc)[:120]}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        # 旧 V1 地址会落回官网 HTML，json() 就炸成「Expecting value: line 1 column 1」
        # —— 那句话对用户毫无信息量，这里必须说清是什么响应。
        raise IndexerError(
            f"BscScan 返回了非 JSON 响应（HTTP {response.status_code}）："
            "接口地址可能已下线，请检查 BSCSCAN_API_BASE"
        ) from exc
    if not isinstance(payload, dict):
        raise IndexerError("BscScan 返回了非对象响应")
    if str(payload.get("status")) == "0":
        message = str(payload.get("message") or "")
        result = str(payload.get("result") or "")
        # 「没有找到记录」是正常空集，不是错误 —— Etherscan 系接口把它也标成 status=0。
        if "No transactions found" in result or "No records found" in result:
            return []
        # message 常常只是一句没信息量的 NOTOK，真正的原因（例如「免费档不支持这条链」）
        # 在 result 里。两段都带上，否则前端只能给用户看一句 NOTOK。
        detail = message
        if result and result != message:
            detail = f"{message}: {result}" if message else result
        raise IndexerError(f"BscScan：{detail}"[:240])
    return payload.get("result") or []


def _address_hash(value: Any) -> str:
    """索引器的 ``from``/``to``/``created_contract`` 既可能是字符串也可能是对象。"""
    if isinstance(value, dict):
        value = value.get("hash")
    text = str(value or "").strip()
    return text.lower() if is_evm_address(text) else ""


def _iso_to_ms(value: Any) -> int:
    """Blockscout 的 ``timestamp`` 是 ISO8601（``2026-10-15T13:18:07.000000Z``）。"""
    text = str(value or "").strip()
    if not text:
        return 0
    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})",
        text,
    )
    if not match:
        return 0
    try:
        import calendar

        parts = [int(part) for part in match.groups()]
        return int(calendar.timegm((*parts, 0, 0, 0)) * 1000)
    except (TypeError, ValueError):
        return 0


def outgoing_transactions(chain: str, dev: str, *, pages: int = 1, page_size: int = 50) -> list[dict[str, Any]]:
    """Dev 的出账交易（它自己发起的），按时间倒序。

    返回统一形状：``{hash, to, createdAt, blockNumber, toIsContract, createdContract}``。
    两个索引器的字段完全不同（见各分支注释），这里一次收敛掉，上层不再关心是谁提供的。
    """
    chain_key = normalize_chain(chain)
    dev_key = str(dev or "").strip().lower()
    if not chain_key or not is_evm_address(dev_key):
        return []
    meta = CHAIN_META[chain_key]
    rows: list[dict[str, Any]] = []

    if meta["indexer"] == "blockscout":
        params: dict[str, Any] = {"filter": "from"}
        for _ in range(max(1, min(20, int(pages)))):
            payload = _blockscout_get(chain_key, f"/api/v2/addresses/{dev_key}/transactions", params)
            for item in payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                tx_hash = str(item.get("hash") or "").strip().lower()
                if not tx_hash:
                    continue
                to_obj = item.get("to") if isinstance(item.get("to"), dict) else {}
                rows.append({
                    "hash": tx_hash,
                    "to": _address_hash(item.get("to")),
                    "createdAt": _iso_to_ms(item.get("timestamp")),
                    "blockNumber": int(to_float(item.get("block_number"))),
                    "toIsContract": bool(to_obj.get("is_contract")),
                    # to 为空 = 这笔在部署合约；Blockscout 直接把新合约放在 created_contract。
                    "createdContract": _address_hash(item.get("created_contract")),
                    "method": clean_text(item.get("method"), 60),
                })
            next_page = payload.get("next_page_params")
            if not isinstance(next_page, dict) or not next_page:
                break
            params = {"filter": "from", **next_page}
        return rows

    for page in range(1, max(1, min(20, int(pages))) + 1):
        raw = _bscscan_get({
            "module": "account", "action": "txlist", "address": dev_key,
            "startblock": 0, "endblock": 99999999, "sort": "desc",
            "page": page, "offset": max(1, min(200, int(page_size))),
        })
        if not isinstance(raw, list):
            break
        for item in raw:
            if not isinstance(item, dict):
                continue
            tx_hash = str(item.get("hash") or "").strip().lower()
            if not tx_hash:
                continue
            to_addr = str(item.get("to") or "").strip()
            rows.append({
                "hash": tx_hash,
                "to": to_addr.lower() if is_evm_address(to_addr) else "",
                "createdAt": int(to_float(item.get("timeStamp"))) * 1000,
                "blockNumber": int(to_float(item.get("blockNumber"))),
                # Etherscan 系的 txlist 不带 is_contract；「to 非空但没 input 数据」是 EOA 转账，
                # 有 input 才可能是在调合约。宁可多查几笔，也不能漏掉发射台调用。
                "toIsContract": bool(to_addr) and clean_text(item.get("input"), 20) not in {"", "0x"},
                "createdContract": _address_hash(item.get("contractAddress")),
                "method": "",
            })
        if len(raw) < int(page_size):
            break
    return rows


def transaction_creations(chain: str, tx_hash: str) -> list[str]:
    """一笔 tx **内部**创建出来的合约（发射台/工厂代发的币）。

    为什么必须查这一层：Robinhood 链实测近 260 个区块没有一个 EOA 直接部署的合约，
    币全部由发射台代发 —— CREATE 由发射台合约执行，所以它出现在这笔 tx 的
    internal transactions 里，而不在 tx 自己的 ``created_contract`` 上。
    """
    chain_key = normalize_chain(chain)
    tx_key = str(tx_hash or "").strip().lower()
    if not chain_key or len(tx_key) != 66:
        return []
    if CHAIN_META[chain_key]["indexer"] == "blockscout":
        payload = _blockscout_get(
            chain_key, f"/api/v2/transactions/{tx_key}/internal-transactions"
        )
        found: list[str] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() not in CREATE_TX_TYPES:
                continue
            created = _address_hash(item.get("created_contract"))
            if created and created not in found:
                found.append(created)
        return found

    raw = _bscscan_get({"module": "account", "action": "txlistinternal", "txhash": tx_key})
    found = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() not in CREATE_TX_TYPES:
            continue
        created = _address_hash(item.get("contractAddress"))
        if created and created not in found:
            found.append(created)
    return found


# --------------------------------------------------------------------------- #
# 市场数据：当前市值 + 历史峰值市值
# --------------------------------------------------------------------------- #
def token_metadata(chain: str, address: str) -> dict[str, Any]:
    """这个地址是不是一个 ERC-20？顺带把 symbol / name / 市值带回来。

    **为什么必须有这道闸**：实测一笔 Robinhood 发射台调用（`launchAndBuy`）会在同一次
    交易里 ``create2`` 出**两个**合约 —— 一个是代币（QUIVER），另一个是它的包装/持仓
    合约。只按 ``created_contract`` 登记，用户会为「发了一个币」收到两条提醒，其中一条
    连合约地址都不是币。实测判据很干净：真代币 ``/api/v2/tokens/{addr}`` 返回 200 +
    ``type == "ERC-20"``，非代币合约返回 404。

    返回 ``{"isToken": True/False/None, ...}``：``None`` = 索引器没给出结论（网络抖动、
    该链没有这个接口）。**拿不准时按「是」处理**——漏报一个真发币比多显示一张卡片贵得多。
    """
    chain_key = normalize_chain(chain)
    address_key = str(address or "").strip().lower()
    if not chain_key or not is_evm_address(address_key):
        return {"isToken": False}

    if CHAIN_META[chain_key]["indexer"] == "blockscout":
        try:
            payload = _blockscout_get(chain_key, f"/api/v2/tokens/{address_key}")
        except IndexerError as exc:
            # 404 是「这个地址不是代币」的确定回答 —— 必须把它和「请求失败」分开，
            # 否则每次网络抖动都会放一个非代币合约进库。
            if "404" in str(exc):
                return {"isToken": False}
            payload = None
        if isinstance(payload, dict):
            token_type = clean_text(payload.get("type"), 20).upper()
            symbol = clean_text(payload.get("symbol"), 40)
            if token_type and token_type != "ERC-20":
                return {"isToken": False}
            if token_type == "ERC-20" or symbol:
                return {
                    "isToken": True,
                    "symbol": symbol,
                    "name": clean_text(payload.get("name"), 80),
                    "priceUsd": to_float(payload.get("exchange_rate")),
                    "marketCapUsd": to_float(payload.get("circulating_market_cap")),
                    "supply": to_float(payload.get("total_supply")),
                    "holders": int(to_float(payload.get("holders_count"))),
                    "source": "blockscout",
                }
            return {"isToken": None}

    pairs = _dexscreener_pairs(chain_key, address_key)
    if pairs:
        return {"isToken": True, "source": "dexscreener"}

    # 最后一条兜底：直接问链上有没有 ``symbol()``。BSC 没有 Blockscout，遇到极新的币
    # （DexScreener 还没收录）时只能靠它判「这到底是不是个币」。
    probe = _rpc_symbol(address_key, chain_key)
    return {"isToken": probe, "source": "rpc" if probe is not None else ""}


def _rpc_symbol(address: str, chain: str) -> bool | None:
    """ERC-20 的 ``symbol()`` 能不能调通。``None`` = 问不到（RPC 不可用）。"""
    try:
        from sandwich_detector import rpc
    except Exception:
        return None
    try:
        result = rpc(chain, "eth_call", [{"to": address, "data": "0x95d89b41"}, "latest"])
    except Exception:
        # 合约没有 symbol() 会 revert —— 这正是我们要的「不是币」的答案，
        # 但 RPC 整体不可用和 revert 在异常里长得一样，所以只在拿到字符串时才敢下结论。
        return None
    text = str(result or "")
    return len(text) >= 130


def _dexscreener_pairs(chain: str, token: str) -> list[dict[str, Any]]:
    slug = str((CHAIN_META.get(chain) or {}).get("dexscreener") or "")
    if not slug:
        return []
    try:
        response = _session().get(
            f"https://api.dexscreener.com/token-pairs/v1/{slug}/{token}",
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
    except Exception:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("pairs"), list):
        return [row for row in payload["pairs"] if isinstance(row, dict)]
    return []


def token_market(chain: str, token: str) -> dict[str, Any]:
    """代币当前市场快照。取「市值最大的那个池子」——一个币可能有多个池子。

    ``supply`` 由 ``市值 / 单价`` 反推，用来把历史最高价折回市值。拿不到就是 0，
    上层会退回「峰值 = 当前市值」并标明来源。
    """
    token_key = str(token or "").strip()
    pairs = _dexscreener_pairs(chain, token_key)
    best: dict[str, Any] = {}
    best_cap = -1.0
    for pair in pairs:
        cap = max(to_float(pair.get("marketCap")), to_float(pair.get("fdv")))
        if cap > best_cap:
            best_cap, best = cap, pair
    price = to_float(best.get("priceUsd"))
    market_cap = max(0.0, best_cap) if best else 0.0
    base = best.get("baseToken") if isinstance(best.get("baseToken"), dict) else {}
    quote = best.get("quoteToken") if isinstance(best.get("quoteToken"), dict) else {}
    base_address = str(base.get("address") or "")
    # 池子的 baseToken 未必是我们的目标代币（也可能是反向对），名称要认对。
    is_base = base_address.lower() == token_key.lower()
    symbol = clean_text((base if is_base else quote).get("symbol"), 40)
    name = clean_text((base if is_base else quote).get("name"), 80)
    out = {
        "symbol": symbol,
        "name": name,
        "priceUsd": price,
        "marketCapUsd": market_cap,
        "liquidityUsd": to_float((best.get("liquidity") or {}).get("usd")) if isinstance(best.get("liquidity"), dict) else 0.0,
        "poolAddress": str(best.get("pairAddress") or ""),
        "source": "dexscreener" if best else "",
        "supply": (market_cap / price) if price > 0 and market_cap > 0 else 0.0,
    }
    if out["marketCapUsd"] > 0 or out["priceUsd"] > 0:
        return out

    # DexScreener 没收录（极新的币常见）→ 退回索引器自带的市值口径。
    if CHAIN_META.get(normalize_chain(chain), {}).get("indexer") == "blockscout":
        try:
            payload = _blockscout_get(normalize_chain(chain), f"/api/v2/tokens/{token_key}")
        except IndexerError:
            payload = {}
        cap = to_float(payload.get("circulating_market_cap"))
        price = to_float(payload.get("exchange_rate"))
        supply = to_float(payload.get("total_supply"))
        if cap > 0 or price > 0:
            return {
                "symbol": clean_text(payload.get("symbol"), 40) or out["symbol"],
                "name": clean_text(payload.get("name"), 80) or out["name"],
                "priceUsd": price,
                "marketCapUsd": cap,
                "liquidityUsd": 0.0,
                "poolAddress": "",
                "source": "blockscout",
                "supply": supply,
            }
    return out


def peak_market_cap(chain: str, token: str, market: dict[str, Any]) -> dict[str, Any]:
    """历史峰值市值 = GeckoTerminal 日线最高价 × 流通量。

    ``peakSource`` 如实标出来源：``geckoterminal`` = 真回扫过历史，``current`` = 只拿
    到当前市值（新币还没有日线、或 GT 没收录这个池子）。前端据此提示「这条还没回扫」，
    而不是让用户以为发过亿级盘子的 Dev 被判成「优质」是算法错了。
    """
    current = max(0.0, to_float(market.get("marketCapUsd")))
    price = max(0.0, to_float(market.get("priceUsd")))
    supply = max(0.0, to_float(market.get("supply")))
    pool = str(market.get("poolAddress") or "").strip()
    network = str((CHAIN_META.get(normalize_chain(chain)) or {}).get("geckoterminal") or "")
    if not pool or not network or price <= 0 or supply <= 0:
        return {"peakUsd": current, "peakPrice": price, "source": "current" if current > 0 else ""}

    try:
        response = _session().get(
            f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool}/ohlcv/day",
            params={"limit": 1000, "currency": "usd"},
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json;version=20230302"},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            return {"peakUsd": current, "peakPrice": price, "source": "current" if current > 0 else ""}
        payload = response.json()
        candles = (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
    except Exception:
        return {"peakUsd": current, "peakPrice": price, "source": "current" if current > 0 else ""}

    highs = [to_float(row[2]) for row in candles if isinstance(row, (list, tuple)) and len(row) >= 3]
    highs = [value for value in highs if value > 0]
    if not highs:
        return {"peakUsd": current, "peakPrice": price, "source": "current" if current > 0 else ""}
    peak_price = max(highs)
    return {
        "peakUsd": max(current, peak_price * supply),
        "peakPrice": peak_price,
        "source": "geckoterminal",
    }


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
class DevLaunchStore:
    """Dev 地址 + 发币记录 + 已查过的 tx。独立库，不与 auth_db / 聪明钱库抢锁。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self._lock = threading.Lock()
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def _session(self):
        """短连接会话；退出即提交并**显式 close()**（见 sandwich_detector 的同款注释）。"""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._lock, self._session() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS dev_wallets (
                    chain TEXT NOT NULL,
                    address TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    scanned_at INTEGER NOT NULL DEFAULT 0,
                    tokens_scanned INTEGER NOT NULL DEFAULT 0,
                    tier_hint TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, address)
                );
                CREATE TABLE IF NOT EXISTS dev_tokens (
                    chain TEXT NOT NULL,
                    token TEXT NOT NULL,
                    dev_address TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    via TEXT NOT NULL DEFAULT '',
                    launch_tx TEXT NOT NULL DEFAULT '',
                    launched_at INTEGER NOT NULL DEFAULT 0,
                    first_seen_at INTEGER NOT NULL DEFAULT 0,
                    market_checked_at INTEGER NOT NULL DEFAULT 0,
                    price_usd REAL NOT NULL DEFAULT 0,
                    market_cap_usd REAL NOT NULL DEFAULT 0,
                    peak_market_cap_usd REAL NOT NULL DEFAULT 0,
                    peak_source TEXT NOT NULL DEFAULT '',
                    market_source TEXT NOT NULL DEFAULT '',
                    pool_address TEXT NOT NULL DEFAULT '',
                    liquidity_usd REAL NOT NULL DEFAULT 0,
                    announced_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, token, dev_address)
                );
                CREATE INDEX IF NOT EXISTS idx_dev_tokens_seen ON dev_tokens (first_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_dev_tokens_dev ON dev_tokens (chain, dev_address);
                CREATE TABLE IF NOT EXISTS dev_seen_txs (
                    chain TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    dev_address TEXT NOT NULL,
                    scanned_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, tx_hash)
                );
                CREATE TABLE IF NOT EXISTS dev_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

    # -- Dev 地址 ------------------------------------------------------------ #
    def wallets(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM dev_wallets"
        if enabled_only:
            sql += " WHERE enabled = 1"
        with self._lock, self._session() as conn:
            rows = conn.execute(sql + " ORDER BY chain, address").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item.get("enabled"))
            item["tier"] = self.wallet_tier(item["chain"], item["address"])
            out.append(item)
        return out

    def wallet(self, chain: Any, address: Any) -> dict[str, Any] | None:
        chain_key = normalize_chain(chain)
        address_key = str(address or "").strip().lower()
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT * FROM dev_wallets WHERE chain = ? AND address = ?",
                (chain_key, address_key),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["enabled"] = bool(item.get("enabled"))
        item["tier"] = self.wallet_tier(item["chain"], item["address"])
        return item

    def save_wallet(self, chain: Any, address: Any, label: Any = None, enabled: Any = None) -> dict[str, Any]:
        """新增/更新一个 Dev 地址。``label``/``enabled`` 传 ``None`` = 这一项不动。"""
        chain_key = normalize_chain(chain)
        address_key = str(address or "").strip().lower()
        if not chain_key:
            raise ValueError("链无效，仅支持 bsc / ethereum / base / robinhood")
        if not is_evm_address(address_key):
            raise ValueError("Dev 地址格式无效（需要 0x 开头的 40 位十六进制）")
        now = int(time.time() * 1000)
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT label, enabled FROM dev_wallets WHERE chain = ? AND address = ?",
                (chain_key, address_key),
            ).fetchone()
            if row:
                next_label = row["label"] if label is None else clean_text(label, 40)
                next_enabled = bool(row["enabled"]) if enabled is None else truthy(enabled, default=True)
                conn.execute(
                    "UPDATE dev_wallets SET label = ?, enabled = ?, updated_at = ? "
                    "WHERE chain = ? AND address = ?",
                    (next_label, 1 if next_enabled else 0, now, chain_key, address_key),
                )
            else:
                next_label = clean_text(label, 40)
                next_enabled = truthy(enabled, default=True)
                conn.execute(
                    """INSERT INTO dev_wallets
                       (chain, address, label, enabled, scanned_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 0, ?, ?)""",
                    (chain_key, address_key, next_label, 1 if next_enabled else 0, now, now),
                )
        return {"chain": chain_key, "address": address_key,
                "label": next_label, "enabled": next_enabled}

    def remove_wallet(self, chain: Any, address: Any) -> bool:
        """删除地址：连带删掉它的发币记录与 tx 检查记录。

        留着记录会有害 —— 用户删了再加同一个 Dev，如果旧记录还在，历史那批币会被
        当成「早就见过」，于是**一条新币提醒都不会弹**（他会以为功能坏了）。
        """
        chain_key = normalize_chain(chain)
        address_key = str(address or "").strip().lower()
        with self._lock, self._session() as conn:
            cursor = conn.execute(
                "DELETE FROM dev_wallets WHERE chain = ? AND address = ?",
                (chain_key, address_key),
            )
            conn.execute(
                "DELETE FROM dev_tokens WHERE chain = ? AND dev_address = ?",
                (chain_key, address_key),
            )
            conn.execute(
                "DELETE FROM dev_seen_txs WHERE chain = ? AND dev_address = ?",
                (chain_key, address_key),
            )
            return bool(cursor.rowcount)

    def mark_wallet_scanned(self, chain: str, address: str, *, token_count: int) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "UPDATE dev_wallets SET scanned_at = ?, tokens_scanned = ?, updated_at = ? "
                "WHERE chain = ? AND address = ?",
                (int(time.time() * 1000), int(token_count), int(time.time() * 1000),
                 chain, address),
            )

    # -- 设置 --------------------------------------------------------------- #
    def setting(self, key: str, default: Any = "") -> Any:
        with self._lock, self._session() as conn:
            row = conn.execute("SELECT value FROM dev_settings WHERE key = ?", (str(key),)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> str:
        text = str(value)
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO dev_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(key), text),
            )
        return text

    def alerts_enabled(self) -> bool:
        return truthy(self.setting(ALERTS_SETTING_KEY, "1"), default=True)

    def set_alerts_enabled(self, value: Any) -> bool:
        enabled = truthy(value, default=True)
        self.set_setting(ALERTS_SETTING_KEY, "1" if enabled else "0")
        return enabled

    # -- 发币记录 ------------------------------------------------------------ #
    def upsert_token(self, row: dict[str, Any]) -> bool:
        """登记一个新币，返回「是不是第一次见到」。已有记录只补名字，不动时间线。"""
        now = int(time.time() * 1000)
        # ``dev_wallets.address`` 恒为小写（save_wallet 归一过），所以 ``dev_tokens`` 的
        # ``dev_address`` 也必须跟着归一 —— 否则调用方传一次 checksum 大小写（这正是
        # 区块浏览器里复制的形态），同一个 Dev 就会被劈成两半：发币记录挂在混合大小写下，
        # 而 wallet_tier 用小写去查 → 级别与峰值全变 0，界面上看起来「功能坏了」。
        # 代币地址同理（EVM 地址大小写不敏感），不然 known_token 会漏判成新币、重复弹窗。
        dev_key = str(row.get("dev_address") or "").strip().lower()
        token_key = str(row.get("token") or "").strip().lower()
        with self._lock, self._session() as conn:
            exists = conn.execute(
                "SELECT symbol, name FROM dev_tokens WHERE chain = ? AND token = ? AND dev_address = ?",
                (row["chain"], token_key, dev_key),
            ).fetchone()
            if exists:
                symbol = clean_text(row.get("symbol"), 40) or exists["symbol"]
                name = clean_text(row.get("name"), 80) or exists["name"]
                if symbol != exists["symbol"] or name != exists["name"]:
                    conn.execute(
                        "UPDATE dev_tokens SET symbol = ?, name = ? "
                        "WHERE chain = ? AND token = ? AND dev_address = ?",
                        (symbol, name, row["chain"], token_key, dev_key),
                    )
                return False
            conn.execute(
                """INSERT INTO dev_tokens
                   (chain, token, dev_address, symbol, name, via, launch_tx, launched_at, first_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["chain"], token_key, dev_key,
                 clean_text(row.get("symbol"), 40), clean_text(row.get("name"), 80),
                 clean_text(row.get("via"), 20), clean_text(row.get("launch_tx"), 90),
                 int(row.get("launched_at") or 0), int(row.get("first_seen_at") or now)),
            )
            return True

    def known_token(self, chain: str, token: str) -> bool:
        """这个币在任何 Dev 名下登记过吗？用于避免同一个币被两个 Dev 重复弹窗。"""
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT 1 FROM dev_tokens WHERE chain = ? AND token = ? LIMIT 1",
                (chain, str(token or "").strip().lower()),
            ).fetchone()
        return bool(row)

    def tokens_needing_market(self, limit: int) -> list[dict[str, Any]]:
        cutoff = int(time.time() * 1000) - MARKET_REFRESH_SECONDS * 1000
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM dev_tokens WHERE market_checked_at < ? "
                "ORDER BY launched_at DESC LIMIT ?",
                (cutoff, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_token_market(self, chain: str, token: str, dev_address: str, market: dict[str, Any]) -> None:
        # 同 upsert_token：token / dev_address 必须归一，否则这一步会静默 UPDATE 0 行
        # （界面表现是「级别停在普通 / 流水」）。
        token_key = str(token or "").strip().lower()
        dev_key = str(dev_address or "").strip().lower()
        with self._lock, self._session() as conn:
            conn.execute(
                """UPDATE dev_tokens SET
                     symbol = COALESCE(NULLIF(?, ''), symbol),
                     name = COALESCE(NULLIF(?, ''), name),
                     price_usd = ?, market_cap_usd = ?,
                     peak_market_cap_usd = MAX(peak_market_cap_usd, ?),
                     peak_source = ?, market_source = ?, pool_address = ?, liquidity_usd = ?,
                     market_checked_at = ?
                   WHERE chain = ? AND token = ? AND dev_address = ?""",
                (clean_text(market.get("symbol"), 40), clean_text(market.get("name"), 80),
                 to_float(market.get("priceUsd")), to_float(market.get("marketCapUsd")),
                 to_float(market.get("peakUsd")), clean_text(market.get("peakSource"), 24),
                 clean_text(market.get("source"), 24), clean_text(market.get("poolAddress"), 90),
                 to_float(market.get("liquidityUsd")), int(time.time() * 1000),
                 chain, token_key, dev_key),
            )

    def mark_announced(self, chain: str, token: str, dev_address: str) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "UPDATE dev_tokens SET announced_at = ? "
                "WHERE chain = ? AND token = ? AND dev_address = ?",
                (int(time.time() * 1000), chain,
                 str(token or "").strip().lower(),
                 str(dev_address or "").strip().lower()),
            )

    def wallet_tier(self, chain: str, address: str) -> dict[str, Any]:
        """该 Dev 的级别 = 它发过的所有币里**峰值市值最大的那一个**。"""
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(MAX(peak_market_cap_usd), 0) AS peak "
                "FROM dev_tokens WHERE chain = ? AND dev_address = ?",
                (chain, str(address or "").strip().lower()),
            ).fetchone()
        tier = tier_for_market_cap(row["peak"] if row else 0)
        tier["tokenCount"] = int(row["n"]) if row else 0
        return tier

    def results(self, limit: int = DEFAULT_RESULTS_LIMIT) -> list[dict[str, Any]]:
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM dev_tokens ORDER BY first_seen_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            labels = {
                (row["chain"], row["address"]): row["label"]
                for row in conn.execute("SELECT chain, address, label FROM dev_wallets").fetchall()
            }
        out: list[dict[str, Any]] = []
        now_ms = int(time.time() * 1000)
        for row in rows:
            item = dict(row)
            tier = tier_for_market_cap(item.get("peak_market_cap_usd"))
            item.update({
                "tier": tier["tier"],
                "tierLabel": tier["label"],
                "peakMarketCapText": tier["peakMarketCapText"],
                "marketCapText": money_text(item.get("market_cap_usd")),
                "devLabel": labels.get((item["chain"], item["dev_address"]), ""),
                "chainLabel": (CHAIN_META.get(item["chain"]) or {}).get("label") or item["chain"],
                "explorerUrl": explorer_token_url(item["chain"], item["token"]),
                "launchTxUrl": explorer_tx_url(item["chain"], item.get("launch_tx")),
                # GMGN 站内交易页：跟热搜榜弹窗同一条路（浏览器已登录就带登录态）。
                "url": gmgn_token_url(item["chain"], item["token"]),
                "fresh": now_ms - int(item.get("first_seen_at") or 0) <= FRESH_SECONDS * 1000,
            })
            out.append(item)
        return out

    def counts(self) -> dict[str, int]:
        with self._lock, self._session() as conn:
            tokens = conn.execute(
                "SELECT COUNT(*) AS n, "
                "COALESCE(SUM(CASE WHEN peak_source = 'geckoterminal' THEN 1 ELSE 0 END), 0) AS grounded "
                "FROM dev_tokens"
            ).fetchone()
            wallets = conn.execute("SELECT COUNT(*) AS n FROM dev_wallets").fetchone()
            txs = conn.execute("SELECT COUNT(*) AS n FROM dev_seen_txs").fetchone()
        return {
            "wallets": int(wallets["n"]) if wallets else 0,
            "tokens": int(tokens["n"]) if tokens else 0,
            "peaksGrounded": int(tokens["grounded"]) if tokens else 0,
            "txsChecked": int(txs["n"]) if txs else 0,
        }

    # -- 已检查过的 tx ------------------------------------------------------- #
    def tx_scanned(self, chain: str, tx_hash: str) -> bool:
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT 1 FROM dev_seen_txs WHERE chain = ? AND tx_hash = ?",
                (chain, tx_hash),
            ).fetchone()
        return bool(row)

    def mark_tx_scanned(self, chain: str, tx_hash: str, dev_address: str) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO dev_seen_txs (chain, tx_hash, dev_address, scanned_at) "
                "VALUES (?, ?, ?, ?)",
                (chain, tx_hash, dev_address, int(time.time() * 1000)),
            )


# --------------------------------------------------------------------------- #
# 扫描器
# --------------------------------------------------------------------------- #
class DevLaunchMonitor:
    """按节奏扫每个 Dev 地址的出账交易，发现新合约就登记 + 回调上层（弹窗）。"""

    def __init__(
        self,
        store: DevLaunchStore | None = None,
        *,
        interval: float | None = None,
        internal_lookups: int | None = None,
        alert_callback=None,
    ):
        self.store = store or DevLaunchStore()
        self.interval = float(interval if interval is not None else
                              (os.getenv("DEV_LAUNCH_POLL_SECONDS") or DEFAULT_POLL_SECONDS))
        self.internal_lookups = int(internal_lookups or
                                    (os.getenv("DEV_LAUNCH_INTERNAL_LOOKUPS") or DEFAULT_INTERNAL_LOOKUPS))
        self.on_launch = alert_callback
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # 一轮要打好几十个外部请求，手动「立即扫描」撞上定时循环时后到的必须放弃。
        self._cycle_lock = threading.Lock()
        self.last_cycle_at = 0.0
        self.last_error = ""

    # -- 生命周期 ----------------------------------------------------------- #
    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="dev-launch-monitor", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        self.stop()

    def set_alert_callback(self, callback) -> None:
        self.on_launch = callback

    def alerts_enabled(self) -> bool:
        return self.store.alerts_enabled()

    def set_alerts_enabled(self, value: Any) -> bool:
        return self.store.set_alerts_enabled(value)

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        # 启动先歇一拍：不和 server 起来时的一堆监控线程抢网络与配额。
        self.stop_event.wait(25)
        while not self.stop_event.is_set():
            try:
                self.run_cycle()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            self.stop_event.wait(self.interval)

    # -- 一轮 --------------------------------------------------------------- #
    def run_cycle(self) -> dict[str, int]:
        if not self._cycle_lock.acquire(blocking=False):
            return {"discovered": 0, "enriched": 0, "busy": 1}
        discovered = enriched = 0
        try:
            for wallet in self.store.wallets(enabled_only=True):
                if self.stop_event.is_set():
                    break
                try:
                    discovered += self.scan_wallet(wallet)
                except IndexerError as exc:
                    # 索引器不可用只记错误，不把「查不到」记成「这个 Dev 没发过币」：
                    # scanned_at 不推进，下一轮还会从同一起点重扫。
                    self.last_error = f"{wallet['chain']} {wallet['address'][:10]}… {exc}"[:200]
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            enriched = self.enrich_markets()
            self.last_cycle_at = time.time()
            return {"discovered": discovered, "enriched": enriched}
        finally:
            self._cycle_lock.release()

    def scan_wallet(self, wallet: dict[str, Any]) -> int:
        """扫一个 Dev 地址，返回本轮新登记的币数。"""
        chain = wallet["chain"]
        dev = wallet["address"]
        first_scan = not int(wallet.get("scanned_at") or 0)
        pages = DEFAULT_BACKFILL_PAGES if first_scan else 1
        internal_budget = (
            DEFAULT_BACKFILL_INTERNAL_LOOKUPS if first_scan else self.internal_lookups
        )

        txs = outgoing_transactions(chain, dev, pages=pages)
        discovered = 0
        for tx in txs:
            if not tx.get("hash"):
                continue
            # 1) 直接部署：这笔自己在造合约。
            if tx.get("createdContract"):
                discovered += self._register(
                    chain, dev, tx["createdContract"], tx, via="deploy",
                    announce=not first_scan,
                )
                self.store.mark_tx_scanned(chain, tx["hash"], dev)
                continue
            if self.store.tx_scanned(chain, tx["hash"]):
                continue
            # 2) 工厂/发射台代发：CREATE 在这笔 tx 内部。太贵，所以只查「发给合约、
            #    且从没查过」的那几笔，并且一轮有配额上限。
            if not tx.get("toIsContract") or internal_budget <= 0:
                continue
            internal_budget -= 1
            created = transaction_creations(chain, tx["hash"])
            self.store.mark_tx_scanned(chain, tx["hash"], dev)
            for token in created:
                discovered += self._register(
                    chain, dev, token, tx, via="factory",
                    announce=not first_scan,
                )

        if first_scan:
            self.store.mark_wallet_scanned(chain, dev, token_count=discovered)
        return discovered

    def _register(
        self,
        chain: str,
        dev: str,
        token: str,
        tx: dict[str, Any],
        *,
        via: str,
        announce: bool,
    ) -> int:
        """登记一个新币；``announce`` 为真时回调弹窗。返回 1/0（是否首次见到）。

        登记前先过一次「这是不是 ERC-20」——一次发射台调用会 create2 出代币 + 包装
        合约两个地址，不过滤就等于一次发币弹两条窗（见 ``token_metadata``）。
        """
        meta = token_metadata(chain, token)
        if meta.get("isToken") is False:
            return 0
        inserted = self.store.upsert_token({
            "chain": chain,
            "token": token,
            "dev_address": dev,
            "symbol": clean_text(meta.get("symbol"), 40),
            "name": clean_text(meta.get("name"), 80),
            "via": via,
            "launch_tx": tx.get("hash") or "",
            "launched_at": int(tx.get("createdAt") or 0),
        })
        if not inserted:
            return 0
        if not announce or self.on_launch is None:
            return 1
        try:
            self.on_launch({
                "chain": chain,
                "dev_address": dev,
                "token": token,
                "via": via,
                "launch_tx": tx.get("hash") or "",
                "launched_at": int(tx.get("createdAt") or 0),
                "label": (self.store.wallet(chain, dev) or {}).get("label") or "",
            })
        except Exception:
            # 弹窗通道异常绝不能影响扫描循环。
            pass
        return 1

    def enrich_markets(self, *, limit: int = DEFAULT_MARKET_LOOKUPS) -> int:
        """补/刷新代币的市场数据，峰值只增不减。"""
        done = 0
        for row in self.store.tokens_needing_market(limit):
            if self.stop_event.is_set():
                break
            market = token_market(row["chain"], row["token"])
            peak = peak_market_cap(row["chain"], row["token"], market)
            market.update({"peakUsd": peak["peakUsd"], "peakSource": peak["source"]})
            self.store.update_token_market(row["chain"], row["token"], row["dev_address"], market)
            done += 1
        return done

    def scan_wallet_now(self, chain: Any, address: Any) -> dict[str, Any]:
        """添加地址后立刻回扫一次：用户马上就能看到级别和历史发币，不用等下一轮。"""
        wallet = self.store.wallet(chain, address)
        if not wallet:
            return {"ok": False, "error": "该地址不在监控列表里"}
        available, note = chain_availability(wallet["chain"])
        if not available:
            return {"ok": False, "error": note}
        try:
            discovered = self.scan_wallet(wallet)
        except IndexerError as exc:
            return {"ok": False, "error": str(exc)[:200]}
        self.enrich_markets(limit=max(DEFAULT_MARKET_LOOKUPS, discovered))
        tier = self.store.wallet_tier(wallet["chain"], wallet["address"])
        return {"ok": True, "discovered": discovered, "tier": tier["label"],
                "tokenCount": tier["tokenCount"]}

    # -- 快照 --------------------------------------------------------------- #
    def status(self) -> dict[str, Any]:
        return {
            "running": self.running(),
            "intervalSeconds": self.interval,
            "lastCycleAt": int(self.last_cycle_at * 1000) if self.last_cycle_at else 0,
            "lastError": self.last_error,
            "counts": self.store.counts(),
            "bscIndexerReady": bool(bscscan_api_key()),
        }

    def payload(self, *, results_limit: int = DEFAULT_RESULTS_LIMIT) -> dict[str, Any]:
        """监控页「Dev 发币」子板块的完整快照。计数一律以库为准，前端不二次统计。"""
        now_ms = int(time.time() * 1000)
        wallets = self.store.wallets()
        counts = self.store.counts()
        last_cycle_at = int(self.last_cycle_at * 1000) if self.last_cycle_at else 0
        running = self.running()
        return {
            "ok": True,
            "now": now_ms,
            "running": running,
            "intervalSeconds": self.interval,
            "lastCycleAt": last_cycle_at,
            "lastError": self.last_error,
            "chains": chain_options(),
            "tiers": [
                {"id": tier, "label": label, "minUsd": floor}
                for tier, label, floor in DEV_TIER_STEPS
            ],
            "wallets": wallets,
            "results": self.store.results(results_limit),
            "summary": {
                "running": running,
                "alertsEnabled": self.store.alerts_enabled(),
                "wallets": len(wallets),
                "enabledWallets": sum(1 for wallet in wallets if wallet["enabled"]),
                "tokens": int(counts.get("tokens") or 0),
                "peaksGrounded": int(counts.get("peaksGrounded") or 0),
                "txsChecked": int(counts.get("txsChecked") or 0),
                "tiers": {
                    tier: sum(1 for wallet in wallets if wallet["tier"]["tier"] == tier)
                    for tier, _label, _floor in DEV_TIER_STEPS
                },
                "lastCycleAt": last_cycle_at,
                "intervalSeconds": self.interval,
                "lastError": self.last_error,
                "bscIndexerReady": bool(bscscan_api_key()),
            },
        }


def main() -> int:
    """排查用：``--status`` 看状态，``--once`` 跑一轮，``--add`` / ``--tier`` 手工核验。"""
    import argparse

    parser = argparse.ArgumentParser(description="Dev 发币监控")
    parser.add_argument("--status", action="store_true", help="打印状态与统计")
    parser.add_argument("--once", action="store_true", help="跑一轮")
    parser.add_argument("--add", nargs=2, metavar=("CHAIN", "ADDRESS"), help="加入一个 Dev 地址并回扫")
    parser.add_argument("--tier", nargs=1, metavar=("ADDRESS",), help="打印某地址的级别")
    args = parser.parse_args()

    monitor = DevLaunchMonitor()
    if args.status:
        print(json.dumps(monitor.status(), ensure_ascii=False, indent=2))
        return 0
    if args.once:
        print(json.dumps(monitor.run_cycle(), ensure_ascii=False))
        return 0
    if args.add:
        chain, address = args.add
        monitor.store.save_wallet(chain, address)
        print(json.dumps(monitor.scan_wallet_now(chain, address), ensure_ascii=False, indent=2))
        return 0
    if args.tier:
        address = str(args.tier[0]).strip().lower()
        rows = [w for w in monitor.store.wallets() if w["address"] == address]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
