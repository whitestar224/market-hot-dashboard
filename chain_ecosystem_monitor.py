from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import requests

from binance_agentic import (
    MEME_RUSH_CHAIN_IDS,
    fetch_binance_meme_rush,
    normalize_binance_meme_rush,
)
from gmgn_agentic import (
    GmgnRateLimitError,
    GmgnUnsupportedNetworkError,
    annotate_gmgn_non_og_exceptions,
    fetch_gmgn_migrated_trenches,
    fetch_gmgn_non_og_market_rank,
    gmgn_api_key_status,
    gmgn_cooldown_status,
    gmgn_trench_passes_chain_filters,
    normalize_gmgn_migrated_trenches,
)


CHAIN_STAGES = ("early_watch", "mainnet_focus", "tradable_ecosystem")
HIGH_VALUE_ALERT_TYPES = frozenset(
    {"stage_upgrade", "new_market", "leader_change", "market_surge"}
)
PROJECT_TOKEN_STAGES = (
    "potential",
    "announced",
    "contract_confirmed",
    "trading",
    "paused",
    "invalid",
)

DEFAULT_MARKETS: tuple[dict[str, str], ...] = (
    {"key": "chain_token", "level": "L0", "name": "公链代币", "description": "Chain Token / Gas Token"},
    {"key": "dex", "level": "L1", "name": "生态协议代币", "description": "DEX / Lending / Perp / LST"},
    {"key": "infrastructure", "level": "L1", "name": "基础设施代币", "description": "Oracle / Bridge / Wallet / RPC"},
    {"key": "meme", "level": "L2", "name": "MEME", "description": "社区币 / 动物币 / 热点币"},
    {"key": "launchpad", "level": "L2", "name": "发射平台", "description": "Fair Launch / Pump / 早期发行"},
    {"key": "nft", "level": "L2", "name": "NFT", "description": "PFP / 艺术 / 游戏 NFT"},
    {"key": "gamefi", "level": "L2", "name": "GameFi", "description": "链游 / 游戏资产"},
    {"key": "ai_depin_rwa", "level": "L2", "name": "AI / DePIN / RWA", "description": "生态重点叙事代币"},
    {"key": "stablecoin", "level": "L3", "name": "稳定币市场", "description": "USDC / USDT / 原生稳定币"},
    {"key": "dex_liquidity", "level": "L3", "name": "DEX 流动性", "description": "LP / Pool / 聚合交易"},
    {"key": "lending", "level": "L3", "name": "借贷市场", "description": "Supply / Borrow / 收益策略"},
    {"key": "derivatives", "level": "L3", "name": "衍生品市场", "description": "Perp / Options / Prediction"},
    {"key": "points", "level": "L3", "name": "空投 / 积分市场", "description": "Points / OTC Allocation"},
    {"key": "bridge_assets", "level": "L3", "name": "跨链市场", "description": "Bridge Assets / 跨链流动性"},
    {"key": "identity", "level": "L3", "name": "域名 / 身份", "description": "域名 / 身份 / SBT"},
    {"key": "validators", "level": "L3", "name": "节点 / 验证市场", "description": "Node / Validator / Restaking"},
)

POTENTIAL_WEIGHTS = {
    "officialProgress": 0.30,
    "ecosystemRole": 0.20,
    "development": 0.20,
    "fundingPartners": 0.15,
    "community": 0.15,
}
TRADED_WEIGHTS = {
    "liquidity": 0.25,
    "activity": 0.25,
    "adoption": 0.20,
    "priceStrength": 0.15,
    "ecosystemCentrality": 0.10,
    "evidenceConfidence": 0.05,
}

ONCHAIN_RESEARCH_SCORE_VERSION = "golden-dog-v3-cryptod-evidence"
ONCHAIN_RESEARCH_DEFAULT_NETWORKS = ("eth", "solana", "robinhood", "arc", "base", "bsc")
ONCHAIN_RESEARCH_MEME_HINTS = frozenset(
    {
        "meme", "dog", "doge", "cat", "frog", "pepe", "inu", "shib", "baby",
        "mom", "mother", "dad", "father", "bro", "sister", "wife", "goat",
        "monkey", "ape", "penguin", "chad", "wojak", "mascot", "pump",
        "bonk", "wif", "币", "狗", "猫", "蛙", "吉祥物", "妈妈", "爸爸",
    }
)
DEFAULT_ONCHAIN_LEADER_CASES: tuple[dict[str, Any], ...] = (
    {"key": "meme:eth:shib", "network": "eth", "contractAddress": "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce", "symbol": "SHIB", "name": "Shiba Inu", "category": "meme", "launchDate": "2020-08-01", "reason": "全球共识动物 Meme 龙头"},
    {"key": "meme:eth:pepe", "network": "eth", "contractAddress": "0x6982508145454ce325ddbe47a25d4ec3d2311933", "symbol": "PEPE", "name": "Pepe", "category": "meme", "launchDate": "2023-04-14", "reason": "以太坊 Meme 周期核心龙头"},
    {"key": "meme:eth:floki", "network": "eth", "contractAddress": "0xcf0c122c6b73ff809c693db761e7baebe62b6a2e", "symbol": "FLOKI", "name": "Floki", "category": "meme", "launchDate": "2021-07-01", "reason": "跨周期动物 Meme 龙头"},
    {"key": "meme:solana:bonk", "network": "solana", "contractAddress": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6QXEk9dWQFwe", "symbol": "BONK", "name": "Bonk", "category": "meme", "launchDate": "2022-12-25", "reason": "Solana 复兴周期代表 Meme"},
    {"key": "meme:solana:wif", "network": "solana", "contractAddress": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzL4f8cANM7c", "symbol": "WIF", "name": "dogwifhat", "category": "meme", "launchDate": "2023-11-20", "reason": "Solana 动物 Meme 周期龙头"},
    {"key": "meme:solana:bome", "network": "solana", "contractAddress": "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82", "symbol": "BOME", "name": "BOOK OF MEME", "category": "meme", "launchDate": "2024-03-14", "reason": "Solana 高速发行周期代表龙头"},
    {"key": "project:eth:uni", "network": "eth", "contractAddress": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "symbol": "UNI", "name": "Uniswap", "category": "project", "launchDate": "2020-09-17", "reason": "DEX 项目型链上龙头"},
    {"key": "project:eth:aave", "network": "eth", "contractAddress": "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9", "symbol": "AAVE", "name": "Aave", "category": "project", "launchDate": "2020-10-02", "reason": "借贷协议项目型龙头"},
    {"key": "project:eth:pendle", "network": "eth", "contractAddress": "0x808507121b80c02388fad14726482e061b8da827", "symbol": "PENDLE", "name": "Pendle", "category": "project", "launchDate": "2021-04-28", "reason": "收益交易赛道项目型龙头"},
    {"key": "project:solana:jup", "network": "solana", "contractAddress": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", "symbol": "JUP", "name": "Jupiter", "category": "project", "launchDate": "2024-01-31", "reason": "Solana 聚合交易项目型龙头"},
)

PROVIDER_HEADERS = {
    "User-Agent": "XingyunShe-Chain-Ecosystem/1.0",
    "Accept": "application/json",
}

# GeckoTerminal's free public API is capped at 30 calls/minute. New-pool scans
# run per chain, so coordinate their request starts instead of letting all
# chains burst at the provider together.
_GECKOTERMINAL_RESEARCH_GATE_LOCK = threading.Lock()
_GECKOTERMINAL_RESEARCH_NEXT_AT = 0.0


def _wait_for_geckoterminal_research_slot() -> None:
    global _GECKOTERMINAL_RESEARCH_NEXT_AT
    interval = max(2.05, float(_safe_float(os.environ.get("ONCHAIN_RESEARCH_GECKO_INTERVAL_SECONDS")) or 2.05))
    with _GECKOTERMINAL_RESEARCH_GATE_LOCK:
        delay = _GECKOTERMINAL_RESEARCH_NEXT_AT - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _GECKOTERMINAL_RESEARCH_NEXT_AT = time.monotonic() + interval

DEFILLAMA_CATEGORY_MARKETS = {
    "dexes": "dex",
    "dex": "dex",
    "lending": "lending",
    "leveraged farming": "lending",
    "yield": "lending",
    "derivatives": "derivatives",
    "options": "derivatives",
    "prediction market": "derivatives",
    "bridge": "bridge_assets",
    "cross chain": "bridge_assets",
    "nft marketplace": "nft",
    "nft lending": "nft",
    "gaming": "gamefi",
    "rwa": "ai_depin_rwa",
    "depin": "ai_depin_rwa",
    "ai": "ai_depin_rwa",
    "liquid staking": "infrastructure",
    "oracle": "infrastructure",
}


def _bounded_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return max(0.0, min(100.0, parsed))


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else 0


def _address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text.startswith("0x") or len(text) != 42:
        return ""
    if any(char not in "0123456789abcdef" for char in text[2:]):
        return ""
    return text


def _relationship_address(value: Any) -> str:
    text = str(value or "")
    marker = text.lower().find("0x")
    return _address(text[marker:]) if marker >= 0 else ""


def _onchain_address(value: Any, network: Any = "") -> str:
    """Normalize EVM addresses without destroying case-sensitive Solana mints."""
    text = str(value or "").strip()
    network_key = _chain_key(network)
    prefix_candidates = {
        f"{network_key}_",
        f"{network_key.replace('-', '_')}_",
    } - {"_"}
    lowered = text.lower()
    for prefix in prefix_candidates:
        if lowered.startswith(prefix.lower()):
            text = text[len(prefix):]
            break
    if text.lower().startswith("0x"):
        return _address(text)
    if 20 <= len(text) <= 80 and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", text):
        return text
    return ""


def _timestamp_ms(value: Any) -> int:
    parsed = _safe_float(value)
    if parsed is not None:
        return int(parsed * 1000) if parsed < 10_000_000_000 else int(parsed)
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def _research_day(value: Any) -> str:
    timestamp = int(_safe_float(value) or _now_ms()) / 1000
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def _json_value(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _chain_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return "-".join(part for part in text.replace(" ", "-").split("-") if part)


def _research_network_key(value: Any) -> str:
    key = _chain_key(value)
    return {
        "ethereum": "eth",
        "bnb-chain": "bsc",
        "binance-smart-chain": "bsc",
        "sol": "solana",
        "robinhood-chain": "robinhood",
    }.get(key, key)


def _clean_number(value: float, digits: int = 2) -> int | float:
    rounded = round(value, digits)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def weighted_score(values: Mapping[str, Any], weights: Mapping[str, float]) -> dict[str, Any]:
    available: list[tuple[str, float, float]] = []
    for key, weight in weights.items():
        score = _bounded_score(values.get(key))
        if score is not None and weight > 0:
            available.append((key, score, float(weight)))

    total_weight = sum(weight for _, _, weight in available)
    full_weight = sum(max(0.0, float(weight)) for weight in weights.values())
    if total_weight <= 0 or full_weight <= 0:
        return {"score": 0, "confidence": 0, "components": {}}

    components = {
        key: {
            "value": _clean_number(score),
            "effectiveWeight": _clean_number(weight / total_weight * 100),
        }
        for key, score, weight in available
    }
    result = sum(score * weight for _, score, weight in available) / total_weight
    confidence = total_weight / full_weight * 100
    return {
        "score": _clean_number(result),
        "confidence": _clean_number(confidence),
        "components": components,
    }


def score_potential_project(values: Mapping[str, Any]) -> dict[str, Any]:
    return weighted_score(values, POTENTIAL_WEIGHTS)


def score_traded_project(values: Mapping[str, Any]) -> dict[str, Any]:
    return weighted_score(values, TRADED_WEIGHTS)


def rank_market_projects(rows: Iterable[Mapping[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    normalized = [dict(row) for row in rows]

    def sort_key(row: Mapping[str, Any]) -> tuple[float, float, str]:
        score = _bounded_score(row.get("score")) or 0.0
        confidence = _bounded_score(row.get("confidence"))
        if confidence is None:
            confidence = _bounded_score(row.get("evidenceConfidence")) or 0.0
        return (-score, -confidence, str(row.get("projectId") or row.get("id") or ""))

    return sorted(normalized, key=sort_key)[: max(0, int(limit))]


def _get_json(
    url: str,
    *,
    session: Any = None,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    timeout: tuple[float, float] = (5, 15),
    attempts: int = 2,
) -> Any:
    client = session or requests
    request_headers = dict(PROVIDER_HEADERS)
    request_headers.update(dict(headers or {}))
    last_error: Exception | None = None
    for _ in range(max(1, min(2, int(attempts)))):
        try:
            response = client.get(url, headers=request_headers, params=dict(params or {}), timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, TimeoutError, ValueError) as exc:
            last_error = exc
            response = getattr(exc, "response", None)
            # Retrying a provider throttle immediately only amplifies the
            # outage. The monitor scheduler applies a longer bounded backoff
            # and the 48-hour new-pool feed is picked up on the next pass.
            if int(getattr(response, "status_code", 0) or 0) == 429:
                break
    if last_error:
        raise last_error
    raise RuntimeError("provider request failed")


def fetch_geckoterminal_network(
    network: str,
    *,
    session: Any = None,
    page: int = 1,
) -> Mapping[str, Any]:
    network_id = str(network or "").strip()
    if not network_id:
        raise ValueError("GeckoTerminal network is required")
    return _get_json(
        f"https://api.geckoterminal.com/api/v2/networks/{network_id}/pools",
        session=session,
        params={"page": max(1, min(10, int(page)))},
        headers={"Accept": "application/vnd.api+json;version=20230203"},
    )


def fetch_geckoterminal_pools(
    network: str,
    *,
    session: Any = None,
    pages: int = 3,
) -> Mapping[str, Any]:
    """Read several discovery pages while preserving GeckoTerminal's response shape."""
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max(1, min(10, int(pages))) + 1):
        try:
            payload = fetch_geckoterminal_network(network, session=session, page=page)
        except Exception:
            if combined:
                break
            raise
        rows = payload.get("data") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            identity = str(row.get("id") or "")
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            combined.append(dict(row))
        links = payload.get("links") if isinstance(payload, Mapping) else {}
        if isinstance(links, Mapping) and not links.get("next"):
            break
    return {"data": combined}


def fetch_geckoterminal_new_pools(
    network: str,
    *,
    session: Any = None,
    pages: int = 3,
    known_pool_ids: Iterable[str] = (),
    request_gate: Any = None,
) -> Mapping[str, Any]:
    """Read the provider's recent-pool feed, retaining included token metadata."""
    network_id = str(network or "").strip()
    if not network_id:
        raise ValueError("GeckoTerminal network is required")
    combined: list[dict[str, Any]] = []
    included: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    known = set(known_pool_ids)
    for page in range(1, max(1, min(10, int(pages))) + 1):
        try:
            if request_gate:
                request_gate()
            payload = _get_json(
                f"https://api.geckoterminal.com/api/v2/networks/{network_id}/new_pools",
                session=session,
                params={"page": page, "include": "base_token,dex"},
                headers={"Accept": "application/vnd.api+json;version=20230203"},
                timeout=8,
                attempts=1,
            )
        except Exception:
            if combined:
                break
            raise
        rows = payload.get("data") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            identity = str(row.get("id") or "")
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            combined.append(dict(row))
        for row in payload.get("included") if isinstance(payload, Mapping) and isinstance(payload.get("included"), list) else []:
            if isinstance(row, Mapping) and row.get("id"):
                included[str(row["id"])] = dict(row)
        # This endpoint normally omits links; absence does not end pagination.
        if known and any(str(row.get("id") or "") in known for row in rows if isinstance(row, Mapping)):
            break
    return {"data": combined, "included": list(included.values())}


def fetch_dexscreener_assets(
    chain_id: str,
    token_addresses: Iterable[str],
    *,
    session: Any = None,
) -> dict[str, Any]:
    chain = str(chain_id or "").strip()
    if not chain:
        raise ValueError("DEX Screener chain id is required")
    seen: set[str] = set()
    addresses: list[str] = []
    for raw_address in list(token_addresses)[:30]:
        address = _onchain_address(raw_address, chain)
        if not address or address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    if not addresses:
        return {"pairs": []}
    payload = _get_json(
        f"https://api.dexscreener.com/tokens/v1/{chain}/{','.join(addresses)}",
        session=session,
        timeout=8,
        attempts=1,
    )
    rows = payload.get("pairs") if isinstance(payload, Mapping) else payload
    pairs = [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
    return {"pairs": pairs}


def fetch_dexscreener_token(token_address: str, *, session: Any = None) -> dict[str, Any]:
    """Resolve one externally discovered contract without requiring a chain guess."""
    address = _onchain_address(token_address)
    if not address:
        return {"pairs": []}
    payload = _get_json(
        f"https://api.dexscreener.com/latest/dex/tokens/{address}",
        session=session,
        timeout=8,
        attempts=1,
    )
    rows = payload.get("pairs") if isinstance(payload, Mapping) else []
    return {"pairs": [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []}


def normalize_onchain_new_pools(
    payload: Any,
    network: str,
    *,
    observed_at: int | None = None,
) -> list[dict[str, Any]]:
    """Normalize one new-pool page into immutable as-observed candidate facts."""
    observed = int(observed_at or _now_ms())
    network_key = _chain_key(network)
    if not network_key:
        return []
    included_rows = payload.get("included") if isinstance(payload, Mapping) else []
    included = {
        str(row.get("id")): row
        for row in included_rows if isinstance(included_rows, list) and isinstance(row, Mapping) and row.get("id")
    }
    rows: list[dict[str, Any]] = []
    for item in payload.get("data") if isinstance(payload, Mapping) and isinstance(payload.get("data"), list) else []:
        if not isinstance(item, Mapping):
            continue
        attributes = item.get("attributes") if isinstance(item.get("attributes"), Mapping) else {}
        relationships = item.get("relationships") if isinstance(item.get("relationships"), Mapping) else {}
        base_relation = relationships.get("base_token") if isinstance(relationships.get("base_token"), Mapping) else {}
        base_data = base_relation.get("data") if isinstance(base_relation.get("data"), Mapping) else {}
        base_id = str(base_data.get("id") or "")
        token_row = included.get(base_id) if isinstance(included.get(base_id), Mapping) else {}
        token_attributes = token_row.get("attributes") if isinstance(token_row.get("attributes"), Mapping) else {}
        dex_relation = relationships.get("dex") if isinstance(relationships.get("dex"), Mapping) else {}
        dex_data = dex_relation.get("data") if isinstance(dex_relation.get("data"), Mapping) else {}
        contract_address = _onchain_address(base_id, network_key)
        pool_address = _onchain_address(attributes.get("address"), network_key)
        # Pool addresses may use formats outside token-address validation. They
        # remain display-only and never participate in token identity.
        if not pool_address:
            pool_address = str(attributes.get("address") or "").strip()[:120]
        if not contract_address or not pool_address:
            continue
        name_parts = [part.strip() for part in str(attributes.get("name") or "").split("/")]
        symbol = str(token_attributes.get("symbol") or (name_parts[0] if name_parts else "")).strip().upper()[:40]
        name = str(token_attributes.get("name") or (name_parts[0] if name_parts else symbol)).strip()[:160]
        volume = attributes.get("volume_usd") if isinstance(attributes.get("volume_usd"), Mapping) else {}
        price_change = attributes.get("price_change_percentage") if isinstance(attributes.get("price_change_percentage"), Mapping) else {}
        transactions = attributes.get("transactions") if isinstance(attributes.get("transactions"), Mapping) else {}

        def window(name: str) -> Mapping[str, Any]:
            value = transactions.get(name)
            return value if isinstance(value, Mapping) else {}

        m5 = window("m5")
        h1 = window("h1")
        h6 = window("h6")
        h24 = window("h24")
        created_ms = _timestamp_ms(attributes.get("pool_created_at"))
        rows.append(
            {
                "network": network_key,
                "provider": "geckoterminal",
                "providers": ["geckoterminal"],
                "contractAddress": contract_address,
                "poolAddress": pool_address,
                "dexId": str(dex_data.get("id") or "")[:80],
                "symbol": symbol,
                "name": name or symbol or contract_address,
                "firstSeenAt": observed,
                "observedAt": observed,
                "poolCreatedAt": created_ms,
                "tradeUrl": f"https://www.geckoterminal.com/{network_key}/pools/{pool_address}",
                "metrics": {
                    "priceUsd": _safe_float(attributes.get("base_token_price_usd")),
                    "liquidityUsd": _safe_float(attributes.get("reserve_in_usd")),
                    "fdvUsd": _safe_float(attributes.get("fdv_usd")),
                    "marketCapUsd": _safe_float(attributes.get("market_cap_usd")),
                    "volumeM5Usd": _safe_float(volume.get("m5")),
                    "volumeH1Usd": _safe_float(volume.get("h1")),
                    "volumeH6Usd": _safe_float(volume.get("h6")),
                    "volumeH24Usd": _safe_float(volume.get("h24")),
                    "priceChangeM5": _safe_float(price_change.get("m5")),
                    "priceChangeH1": _safe_float(price_change.get("h1")),
                    "buysM5": _safe_int(m5.get("buys")),
                    "sellsM5": _safe_int(m5.get("sells")),
                    "buyersM5": _safe_int(m5.get("buyers")),
                    "sellersM5": _safe_int(m5.get("sellers")),
                    "buysH1": _safe_int(h1.get("buys")),
                    "sellsH1": _safe_int(h1.get("sells")),
                    "buyersH1": _safe_int(h1.get("buyers")),
                    "sellersH1": _safe_int(h1.get("sellers")),
                    "transactionsH1": _safe_int(h1.get("buys")) + _safe_int(h1.get("sells")),
                    "transactionsH6": _safe_int(h6.get("buys")) + _safe_int(h6.get("sells")),
                    "transactionsH24": _safe_int(h24.get("buys")) + _safe_int(h24.get("sells")),
                },
            }
        )
    return rows


def normalize_onchain_dexscreener(payload: Any, network: str, *, observed_at: int | None = None) -> list[dict[str, Any]]:
    observed = int(observed_at or _now_ms())
    rows: list[dict[str, Any]] = []
    data = payload.get("pairs") if isinstance(payload, Mapping) else payload
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, Mapping):
            continue
        item_network = _research_network_key(item.get("chainId") or network)
        expected_network = _research_network_key(network)
        if not item_network or (expected_network and item_network != expected_network):
            continue
        base = item.get("baseToken") if isinstance(item.get("baseToken"), Mapping) else {}
        quote = item.get("quoteToken") if isinstance(item.get("quoteToken"), Mapping) else {}
        address = _onchain_address(base.get("address"), item_network)
        pool = str(item.get("pairAddress") or "").strip()[:120]
        if not address or not pool:
            continue
        liquidity = item.get("liquidity") if isinstance(item.get("liquidity"), Mapping) else {}
        volume = item.get("volume") if isinstance(item.get("volume"), Mapping) else {}
        price_change = item.get("priceChange") if isinstance(item.get("priceChange"), Mapping) else {}
        txns = item.get("txns") if isinstance(item.get("txns"), Mapping) else {}
        m5 = txns.get("m5") if isinstance(txns.get("m5"), Mapping) else {}
        h1 = txns.get("h1") if isinstance(txns.get("h1"), Mapping) else {}
        h6 = txns.get("h6") if isinstance(txns.get("h6"), Mapping) else {}
        h24 = txns.get("h24") if isinstance(txns.get("h24"), Mapping) else {}
        info = item.get("info") if isinstance(item.get("info"), Mapping) else {}
        rows.append({
            "network": item_network,
            "provider": "dexscreener",
            "providers": ["dexscreener"],
            "contractAddress": address,
            "poolAddress": pool,
            "dexId": str(item.get("dexId") or "")[:80],
            "symbol": str(base.get("symbol") or "").upper()[:40],
            "name": str(base.get("name") or base.get("symbol") or address)[:160],
            "quoteAsset": {
                "symbol": str(quote.get("symbol") or "").upper()[:40],
                "name": str(quote.get("name") or quote.get("symbol") or "")[:160],
                "address": _onchain_address(quote.get("address"), item_network) or str(quote.get("address") or "")[:160],
            },
            "firstSeenAt": observed,
            "observedAt": observed,
            "poolCreatedAt": _timestamp_ms(item.get("pairCreatedAt")),
            "tradeUrl": str(item.get("url") or "")[:800],
            "narrativeContext": {
                "description": str(info.get("description") or item.get("description") or "")[:1200],
                "websites": [str(link.get("url") or "")[:800] for link in (info.get("websites") or [])[:4] if isinstance(link, Mapping)],
                "socials": [str(link.get("url") or "")[:800] for link in (info.get("socials") or [])[:4] if isinstance(link, Mapping)],
            },
            "metrics": {
                "priceUsd": _safe_float(item.get("priceUsd")),
                "liquidityUsd": _safe_float(liquidity.get("usd")),
                "fdvUsd": _safe_float(item.get("fdv")),
                "marketCapUsd": _safe_float(item.get("marketCap")),
                "volumeM5Usd": _safe_float(volume.get("m5")),
                "volumeH1Usd": _safe_float(volume.get("h1")),
                "volumeH6Usd": _safe_float(volume.get("h6")),
                "volumeH24Usd": _safe_float(volume.get("h24")),
                "priceChangeM5": _safe_float(price_change.get("m5")),
                "priceChangeH1": _safe_float(price_change.get("h1")),
                "buysM5": _safe_int(m5.get("buys")),
                "sellsM5": _safe_int(m5.get("sells")),
                "buysH1": _safe_int(h1.get("buys")),
                "sellsH1": _safe_int(h1.get("sells")),
                "transactionsH1": _safe_int(h1.get("buys")) + _safe_int(h1.get("sells")),
                "transactionsH6": _safe_int(h6.get("buys")) + _safe_int(h6.get("sells")),
                "transactionsH24": _safe_int(h24.get("buys")) + _safe_int(h24.get("sells")),
            },
        })
    return rows


def scan_onchain_research_contract(
    store: Any,
    contract_address: Any,
    *,
    source: str = "external",
    source_text: str = "",
    observed_at: int | None = None,
    fetcher: Any = None,
    candidate_sink: Any = None,
) -> dict[str, Any]:
    """Immediately seed the incremental scanner from a chat/X contract mention."""
    observed = _now_ms()  # A message timestamp cannot backdate a current quote.
    contract = _onchain_address(contract_address)
    if not contract:
        return {"ok": False, "discovered": 0, "error": "invalid contract"}
    fetch = fetcher or fetch_dexscreener_token
    source_key = re.sub(r"[^0-9a-z_-]+", "-", str(source or "external").strip().lower()).strip("-")[:40] or "external"
    try:
        payload = fetch(contract)
        normalized = normalize_onchain_dexscreener(payload, "", observed_at=observed)
        target = contract.lower() if contract.lower().startswith("0x") else contract
        rows = [
            row for row in normalized
            if _onchain_address(row.get("contractAddress"), row.get("network")) == target
            and _research_network_key(row.get("network")) in ONCHAIN_RESEARCH_DEFAULT_NETWORKS
        ]
        enriched: list[dict[str, Any]] = []
        clue = re.sub(r"\s+", " ", str(source_text or "")).strip()[:100]
        for row in merge_onchain_research_rows(rows):
            providers = list(dict.fromkeys([*(row.get("providers") or []), source_key]))
            reasons = list(row.get("reasons") or [])
            reasons.insert(0, f"外部线索：{clue}" if clue else f"外部线索：{source}")
            enriched.append(evaluate_onchain_candidate({
                **row,
                "providers": providers,
                "reasons": list(dict.fromkeys(reasons))[:4],
            }, now_ms=observed))
        store.save_onchain_research_scan(
            enriched,
            observed_at=observed,
            source_status={f"contract-{source_key}": "ok"},
        )
        if candidate_sink and enriched:
            candidate_sink(enriched)
        return {"ok": True, "discovered": len(enriched), "items": enriched}
    except Exception as exc:
        store.save_onchain_research_scan(
            [],
            observed_at=observed,
            source_status={f"contract-{source_key}": "error"},
            errors=[f"contract-{source_key}: {str(exc)[:180]}"],
        )
        return {"ok": False, "discovered": 0, "error": str(exc)[:180]}


def merge_onchain_research_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge providers by chain + contract while favoring the deepest observed pool."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    max_metric_keys = {
        "liquidityUsd", "fdvUsd", "marketCapUsd", "volumeM5Usd", "volumeH1Usd",
        "volumeH6Usd", "volumeH24Usd", "buysM5", "sellsM5", "buyersM5",
        "sellersM5", "buysH1", "sellsH1", "buyersH1", "sellersH1",
        "transactionsH1", "transactionsH6", "transactionsH24",
    }
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        network = _chain_key(raw.get("network"))
        address = _onchain_address(raw.get("contractAddress"), network)
        if not network or not address:
            continue
        key = (network, address.lower() if address.lower().startswith("0x") else address)
        item = dict(raw)
        item["network"] = network
        item["contractAddress"] = address
        item["providers"] = list(dict.fromkeys(str(value) for value in (raw.get("providers") or [raw.get("provider")]) if value))
        item["metrics"] = dict(raw.get("metrics") or {})
        if key not in merged:
            merged[key] = item
            continue
        target = merged[key]
        old_liquidity = float(_safe_float((target.get("metrics") or {}).get("liquidityUsd")) or 0)
        new_liquidity = float(_safe_float(item["metrics"].get("liquidityUsd")) or 0)
        target["providers"] = list(dict.fromkeys([*(target.get("providers") or []), *(item.get("providers") or [])]))
        if item.get("narrativeContext"):
            target["narrativeContext"] = item["narrativeContext"]
        for field in ("gmgnNarrative", "gmgnNarrativeSource", "xOriginal", "imageUrl"):
            if item.get(field):
                target[field] = item[field]
        first_seen_values = [
            value
            for value in (
                _safe_int(target.get("firstSeenAt")),
                _safe_int(item.get("firstSeenAt")),
                _safe_int(target.get("poolCreatedAt")),
                _safe_int(item.get("poolCreatedAt")),
                _safe_int(target.get("observedAt")),
                _safe_int(item.get("observedAt")),
            )
            if value > 0
        ]
        target["firstSeenAt"] = min(first_seen_values) if first_seen_values else 0
        target["observedAt"] = max(_safe_int(target.get("observedAt")), _safe_int(item.get("observedAt")))
        created_values = [value for value in (_safe_int(target.get("poolCreatedAt")), _safe_int(item.get("poolCreatedAt"))) if value > 0]
        target["poolCreatedAt"] = min(created_values) if created_values else 0
        for metric, value in item["metrics"].items():
            parsed = _safe_float(value)
            current = _safe_float(target["metrics"].get(metric))
            if parsed is None:
                continue
            if metric in max_metric_keys:
                target["metrics"][metric] = max(parsed, current or 0)
            elif current is None or new_liquidity >= old_liquidity:
                target["metrics"][metric] = parsed
        if new_liquidity > old_liquidity:
            for field in ("poolAddress", "dexId", "tradeUrl"):
                if item.get(field):
                    target[field] = item[field]
        for field in ("symbol", "name"):
            if not target.get(field) and item.get(field):
                target[field] = item[field]
    return list(merged.values())


def _log_score(value: Any, floor: float, ceiling: float) -> float:
    number = float(_safe_float(value) or 0)
    if number <= floor:
        return 0.0
    if number >= ceiling:
        return 100.0
    return (math.log1p(number) - math.log1p(floor)) / (math.log1p(ceiling) - math.log1p(floor)) * 100


def onchain_wallet_profile(row: Mapping[str, Any]) -> dict[str, Any]:
    """Classify holder evidence without treating a wallet label as a buy signal."""
    facts = row.get("launchFacts") if isinstance(row.get("launchFacts"), Mapping) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}

    def value(name: str, fallback: str | None = None) -> float:
        raw = facts.get(name)
        if raw is None:
            raw = metrics.get(fallback or name)
        return float(_safe_float(raw) or 0)

    holders = int(value("holders"))
    smart_holders = int(value("smartMoneyHolders"))
    pro_holders = int(value("proHolders"))
    kol_holders = int(value("kolHolders"))
    independent_holders = smart_holders + pro_holders
    smart_percent = value("smartMoneyHoldingPercent")
    pro_percent = value("proHoldingPercent")
    kol_percent = value("kolHoldingPercent")
    new_wallet_percent = value("newWalletHoldingPercent")
    bundler_percent = value("bundlerHoldingPercent")
    top10_percent = value("top10Percent", "top10HolderPercent")
    wash_trading = bool(facts.get("washTrading") or metrics.get("washTrading"))
    coverage = bool(
        holders or independent_holders or kol_holders or smart_percent or pro_percent
        or kol_percent or new_wallet_percent or bundler_percent or top10_percent or wash_trading
    )
    if not coverage:
        return {
            "classification": "unavailable", "score": 50, "coverage": False,
            "summary": "暂无钱包分型数据，不能用聪明钱叙事加分",
            "independentHolders": 0, "riskSignals": [],
        }

    score = 50.0
    score += min(20.0, independent_holders * 3.0)
    score += min(8.0, _log_score(holders, 20, 2_000) * 0.08)
    if independent_holders >= 3 and 0 < smart_percent + pro_percent <= 20:
        score += 8.0
    risk_signals: list[str] = []
    if top10_percent >= 60:
        score -= min(25.0, (top10_percent - 50) * 0.6)
        risk_signals.append("头部持仓集中")
    if kol_percent >= 20:
        score -= min(18.0, (kol_percent - 15) * 0.7)
        risk_signals.append("KOL持仓偏高")
    if new_wallet_percent >= 35:
        score -= min(18.0, (new_wallet_percent - 25) * 0.5)
        risk_signals.append("新钱包占比偏高")
    if bundler_percent >= 12:
        score -= min(22.0, (bundler_percent - 8) * 0.8)
        risk_signals.append("关联打包钱包聚集")
    if wash_trading:
        score -= 35.0
        risk_signals.append("刷量标签")
    score = max(0.0, min(100.0, score))
    promotional_risk = wash_trading or len(risk_signals) >= 3 or (kol_percent >= 25 and independent_holders == 0)
    if promotional_risk:
        classification = "promotional-cluster-risk"
        summary = "KOL/新钱包/关联钱包聚集，疑似推广或刷量结构"
    elif independent_holders >= 3 and score >= 62:
        classification = "independent-validation"
        summary = f"{independent_holders}个专业/聪明钱地址参与，仅作交叉验证"
    else:
        classification = "mixed"
        summary = "钱包结构有样本但独立性不足，继续观察持有行为"
    return {
        "classification": classification,
        "score": round(score, 1),
        "coverage": True,
        "summary": summary,
        "holders": holders,
        "independentHolders": independent_holders,
        "smartMoneyHolders": smart_holders,
        "proHolders": pro_holders,
        "kolHolders": kol_holders,
        "top10Percent": top10_percent or None,
        "riskSignals": risk_signals,
    }


def evaluate_onchain_candidate(row: Mapping[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
    """Score only facts visible at the observation time; no future outcome fields are accepted."""
    now = int(now_ms or row.get("observedAt") or _now_ms())
    metrics = dict(row.get("metrics") or {})
    wallet_profile = onchain_wallet_profile({**dict(row), "metrics": metrics})
    created_at = int(_safe_float(row.get("poolCreatedAt")) or _safe_float(row.get("firstSeenAt")) or now)
    age_minutes = max(0.0, (now - created_at) / 60_000)
    liquidity = float(_safe_float(metrics.get("liquidityUsd")) or 0)
    volume_h1 = float(_safe_float(metrics.get("volumeH1Usd")) or _safe_float(metrics.get("volumeH24Usd")) or 0)
    volume_h6 = float(_safe_float(metrics.get("volumeH6Usd")) or _safe_float(metrics.get("volumeH24Usd")) or 0)
    tx_h1 = int(_safe_float(metrics.get("transactionsH1")) or 0)
    buys_h1 = int(_safe_float(metrics.get("buysH1")) or 0)
    sells_h1 = int(_safe_float(metrics.get("sellsH1")) or 0)
    buyers_m5 = int(_safe_float(metrics.get("buyersM5")) or 0)
    fdv = float(_safe_float(metrics.get("marketCapUsd")) or _safe_float(metrics.get("fdvUsd")) or 0)
    reasons: list[str] = [
        str(value)[:140]
        for value in (row.get("reasons") or [])
        if str(value).startswith("外部线索：")
    ][:2]
    risks: list[str] = []
    decision = "watch"
    if age_minutes >= 30 and liquidity < 1_000:
        decision, risks = "filtered", ["池龄超过30分钟且流动性不足$1K"]
    elif age_minutes >= 60 and tx_h1 < 5 and volume_h1 < 1_000:
        decision, risks = "filtered", ["一小时交易活跃度过低"]
    elif tx_h1 >= 20 and sells_h1 > max(8, buys_h1 * 4):
        decision, risks = "filtered", ["卖出笔数显著异常"]
    elif fdv >= 5_000_000 and liquidity > 0 and liquidity / fdv < 0.001:
        decision, risks = "filtered", ["流动性与估值严重失衡"]

    liquidity_score = _log_score(liquidity, 750, 250_000)
    activity_score = max(_log_score(tx_h1, 2, 250), _log_score(volume_h1, 500, 250_000))
    total_sides = buys_h1 + sells_h1
    buy_score = 50.0 if total_sides <= 0 else max(0.0, min(100.0, buys_h1 / total_sides * 125))
    breadth_score = max(_log_score(buyers_m5, 1, 50), min(100.0, activity_score * 0.8))
    turnover = volume_h1 / liquidity if liquidity > 0 else 0
    velocity_score = min(100.0, _log_score(turnover, 0.05, 8.0))
    persistence_score = min(100.0, _log_score(volume_h6, 2_000, 800_000))
    freshness_score = 100.0 if age_minutes <= 120 else max(15.0, 100.0 - (age_minutes - 120) / 8)
    provider_count = min(2, len(
        set(row.get("providers") or [])
        & {"dexscreener", "binance-meme-rush", "gmgn-trenches"}
    ))
    evidence_score = min(100.0, 35.0 + provider_count * 25.0 + (15.0 if row.get("dexId") else 0.0))
    identity_text = f"{row.get('symbol') or ''} {row.get('name') or ''} {row.get('dexId') or ''}".lower()
    meme_hint_count = sum(1 for hint in ONCHAIN_RESEARCH_MEME_HINTS if hint in identity_text)
    meme_hint_score = min(100.0, 35.0 + meme_hint_count * 25.0)
    meme_score = (
        liquidity_score * 0.18 + activity_score * 0.22 + buy_score * 0.17
        + breadth_score * 0.18 + velocity_score * 0.12 + freshness_score * 0.08
        + meme_hint_score * 0.05
    )
    project_score = (
        liquidity_score * 0.27 + activity_score * 0.20 + buy_score * 0.12
        + persistence_score * 0.15 + evidence_score * 0.16 + freshness_score * 0.10
    )
    if wallet_profile["coverage"]:
        # Wallet labels are secondary corroboration only.  Their bounded impact
        # cannot rescue a weak market, while obvious clusters can demote noise.
        wallet_adjustment = (float(wallet_profile["score"]) - 50.0) * 0.12
        meme_score = max(0.0, min(100.0, meme_score + wallet_adjustment))
        project_score = max(0.0, min(100.0, project_score + wallet_adjustment * 0.7))
    candidate_type = "meme" if meme_score >= project_score else "project"
    selected_score = max(meme_score, project_score)
    present = sum(1 for key in ("liquidityUsd", "volumeH1Usd", "transactionsH1", "buysH1", "sellsH1") if metrics.get(key) is not None)
    confidence = min(100, 25 + present * 11 + provider_count * 10)
    if liquidity >= 5_000:
        reasons.append("流动性达到早期研究门槛")
    if tx_h1 >= 15:
        reasons.append("一小时交易开始形成广度")
    if total_sides and buys_h1 > sells_h1 * 1.35:
        reasons.append("主动买入笔数占优")
    if volume_h1 >= max(5_000, liquidity * 0.5):
        reasons.append("早期成交扩散较快")
    if meme_hint_count:
        reasons.append("名称或发行场景具备 Meme 传播特征")
    if wallet_profile["classification"] == "independent-validation":
        reasons.append(wallet_profile["summary"])
    elif wallet_profile["classification"] == "promotional-cluster-risk":
        risks.insert(0, "疑似刷量或关联钱包聚集")
    if decision != "filtered":
        if age_minutes < 20 and (liquidity < 2_500 or tx_h1 < 5):
            decision = "warming"
            reasons.append("仍在最早期观察窗口")
        elif selected_score >= 58 and liquidity >= 5_000 and tx_h1 >= 12:
            decision = "shortlisted"
        elif age_minutes >= 90 and selected_score < 35:
            decision = "filtered"
            risks.append("综合质量未达到研究门槛")
    if provider_count < 2:
        risks.append("目前仅有单一数据源确认")
    return {
        **dict(row),
        "metrics": metrics,
        "scoreVersion": ONCHAIN_RESEARCH_SCORE_VERSION,
        "candidateType": candidate_type,
        "memeScore": round(meme_score, 1),
        "projectScore": round(project_score, 1),
        "selectedScore": round(selected_score, 1),
        "confidence": int(round(confidence)),
        "walletProfile": wallet_profile,
        "decision": decision,
        "ageMinutes": round(age_minutes, 1),
        "reasons": reasons[:4],
        "risks": list(dict.fromkeys(risks))[:4],
    }


def scan_onchain_research(
    store: Any,
    *,
    networks: Iterable[str] | None = None,
    observed_at: int | None = None,
    new_pool_fetcher: Any = None,
    dexscreener_fetcher: Any = None,
    binance_launch_fetcher: Any = None,
    gmgn_trenches_fetcher: Any = None,
    pages: int | None = None,
    candidate_sink: Any = None,
    include_non_trench_sources: bool = False,
) -> dict[str, Any]:
    """Run one bounded research pass over the GMGN Trenches candidate pool.

    The research page and opportunity picker intentionally share the same
    universe as the Trenches board.  Binance Meme Rush can still be requested
    explicitly by internal callers for diagnostics, but it is excluded from
    normal research so a separate launch feed cannot create opportunities that
    never appeared on the board.
    """
    observed = int(observed_at or _now_ms())
    selected_networks = list(dict.fromkeys(
        _chain_key(value) for value in (networks or ONCHAIN_RESEARCH_DEFAULT_NETWORKS) if _chain_key(value)
    ))
    fetch_dex = dexscreener_fetcher or fetch_dexscreener_assets
    fetch_binance = binance_launch_fetcher or fetch_binance_meme_rush
    # new_pool_fetcher remains a compatibility alias for callers/tests written
    # before the scanner moved from generic new pools to GMGN completed trenches.
    fetch_gmgn = gmgn_trenches_fetcher or new_pool_fetcher or fetch_gmgn_migrated_trenches
    enrich_limit = max(0, min(300, int(_safe_float(os.environ.get("ONCHAIN_RESEARCH_ENRICH_LIMIT")) or 90)))
    all_rows: list[dict[str, Any]] = []
    status: dict[str, str] = {}
    errors: list[str] = []
    for network in selected_networks:
        network_rows: list[dict[str, Any]] = []
        binance_rows: list[dict[str, Any]] = []
        network_ok = False
        provider_status: dict[str, str] = {}
        if include_non_trench_sources and network in MEME_RUSH_CHAIN_IDS:
            try:
                binance_payload = fetch_binance(network, rank_types=(30,))
                binance_received_at = int(observed_at or _now_ms())
                binance_rows = normalize_binance_meme_rush(
                    binance_payload,
                    network,
                    observed_at=binance_received_at,
                )
                binance_rows = [row for row in binance_rows if row.get("launchStage") == "migrated"]
                network_rows.extend(binance_rows)
                network_ok = True
                provider_status[f"{network}/binance-meme-rush"] = "ok"
                # Binance's launch feed is the low-latency first screen.  It is
                # persisted before the slower multi-page provider is touched.
                fast_screen = [
                    evaluate_onchain_candidate(row, now_ms=binance_received_at)
                    for row in merge_onchain_research_rows(binance_rows)
                ]
                store.save_onchain_research_scan(
                    fast_screen,
                    observed_at=binance_received_at,
                    source_status={f"{network}/binance-meme-rush": "ok"},
                )
                if candidate_sink and fast_screen:
                    candidate_sink(fast_screen)
            except Exception as exc:
                errors.append(f"{network}/binance-meme-rush: {str(exc)[:180]}")
                provider_status[f"{network}/binance-meme-rush"] = "error"
        else:
            provider_status[f"{network}/binance-meme-rush"] = (
                "scope-excluded" if not include_non_trench_sources else "unsupported"
            )
        try:
            payload = fetch_gmgn(network)
            received_at = int(observed_at or _now_ms())
            normalized_primary = normalize_gmgn_migrated_trenches(payload, network, observed_at=received_at)
            # The same profile gate used by the board is applied before any
            # enrichment or scoring.  This prevents DexScreener/secondary
            # evidence from creating a candidate that is not a trench item.
            primary = [
                row for row in normalized_primary
                if gmgn_trench_passes_chain_filters(row)
            ]
            network_rows.extend(primary)
            network_ok = True
            provider_status[f"{network}/gmgn-trenches"] = "ok"
            # Persist and enqueue the first screen before slower enrichment.
            initial = [evaluate_onchain_candidate(row, now_ms=received_at) for row in merge_onchain_research_rows(primary)]
            store.save_onchain_research_scan(
                initial,
                observed_at=received_at,
                source_status={f"{network}/gmgn-trenches": "ok"},
            )
            if candidate_sink and initial:
                candidate_sink(initial)
            enriched: list[dict[str, Any]] = []
            # Enrich only the already-filtered GMGN trench rows.  Secondary
            # providers add evidence to a trench candidate; they never add a
            # new candidate to the research universe.
            binance_enrich_limit = enrich_limit // 2 if primary and binance_rows else enrich_limit
            primary_enrich_limit = enrich_limit - binance_enrich_limit if binance_rows else enrich_limit
            enrich_rows = [
                *binance_rows[:binance_enrich_limit],
                *primary[:primary_enrich_limit],
            ]
            addresses = list(dict.fromkeys(
                row["contractAddress"] for row in enrich_rows if row.get("contractAddress")
            ))
            for offset in range(0, len(addresses), 30):
                batch = addresses[offset: offset + 30]
                if not batch:
                    continue
                try:
                    enriched_payload = fetch_dex(network, batch)
                    enriched.extend(normalize_onchain_dexscreener(enriched_payload, network, observed_at=observed))
                except Exception as exc:
                    errors.append(f"{network}/dexscreener: {str(exc)[:180]}")
                    break
            evaluated = [
                evaluate_onchain_candidate(row, now_ms=received_at)
                for row in merge_onchain_research_rows([*network_rows, *enriched])
            ]
            all_rows.extend(evaluated)
            if candidate_sink and evaluated:
                candidate_sink(evaluated)
            status[network] = "ok"
        except Exception as exc:
            errors.append(f"{network}/gmgn-trenches: {str(exc)[:180]}")
            provider_status[f"{network}/gmgn-trenches"] = "error"
            if network_rows:
                received_at = int(observed_at or _now_ms())
                evaluated = [
                    evaluate_onchain_candidate(row, now_ms=received_at)
                    for row in merge_onchain_research_rows(network_rows)
                ]
                all_rows.extend(evaluated)
                if candidate_sink and evaluated:
                    candidate_sink(evaluated)
            status[network] = "ok" if network_ok else "error"
        status.update(provider_status)
    store.save_onchain_research_scan(
        all_rows,
        observed_at=observed,
        source_status=status,
        errors=errors,
        candidate_scope="gmgn-trenches" if not include_non_trench_sources else "",
    )
    return {
        "ok": any(value == "ok" for value in status.values()),
        "observedAt": observed,
        "networks": selected_networks,
        "sourceStatus": status,
        "errors": errors,
        "discovered": len(all_rows),
        "shortlisted": sum(1 for row in all_rows if row.get("decision") == "shortlisted"),
        "candidateScope": "gmgn-trenches" if not include_non_trench_sources else "gmgn-trenches+optional-launch-feed",
    }


def fetch_live_onchain_trenches(
    *,
    networks: Iterable[str] | None = None,
    source: str = "",
    page: int = 1,
    page_size: int = 24,
    query: str = "",
    observed_at: int | None = None,
    item_filter: Callable[[Mapping[str, Any]], bool] | None = None,
    per_network_limit: int | None = None,
    include_non_og_exceptions: bool = False,
) -> dict[str, Any]:
    """Read the current opened/migrated tape directly from GMGN and Binance."""
    observed = int(observed_at or _now_ms())
    selected_networks = list(dict.fromkeys(
        _chain_key(value)
        for value in (networks or ONCHAIN_RESEARCH_DEFAULT_NETWORKS)
        if _chain_key(value) in ONCHAIN_RESEARCH_DEFAULT_NETWORKS
    )) or list(ONCHAIN_RESEARCH_DEFAULT_NETWORKS)
    source_key = str(source or "").strip().casefold()
    if source_key not in {"gmgn", "binance"}:
        source_key = ""
    jobs: dict[Any, tuple[str, str]] = {}
    rows: list[dict[str, Any]] = []
    source_status: dict[str, str] = {}
    errors: list[str] = []
    retry_after_seconds = 0
    rank_networks = [network for network in selected_networks if network != "arc"] if include_non_og_exceptions else []
    job_count = len(selected_networks) + len(rank_networks) + sum(
        1 for network in selected_networks if network in MEME_RUSH_CHAIN_IDS
    )
    with ThreadPoolExecutor(max_workers=max(1, min(9, job_count))) as executor:
        for network in selected_networks:
            if source_key != "binance":
                jobs[executor.submit(fetch_gmgn_migrated_trenches, network)] = (network, "gmgn")
                if network in rank_networks:
                    jobs[executor.submit(fetch_gmgn_non_og_market_rank, network)] = (network, "gmgn-rank")
            if source_key != "gmgn" and network in MEME_RUSH_CHAIN_IDS:
                jobs[executor.submit(fetch_binance_meme_rush, network, rank_types=(30,))] = (network, "binance")
            elif source_key == "binance" and network not in MEME_RUSH_CHAIN_IDS:
                source_status[f"{network}/binance-meme-rush"] = "unsupported"
        for future in as_completed(jobs):
            network, provider = jobs[future]
            provider_name = "gmgn-trenches" if provider in {"gmgn", "gmgn-rank"} else "binance-meme-rush"
            status_key = f"{network}/{provider_name}"
            try:
                payload = future.result()
                gmgn_meta = payload.get("_gmgnMeta") if provider in {"gmgn", "gmgn-rank"} and isinstance(payload, Mapping) else {}
                if not isinstance(gmgn_meta, Mapping):
                    gmgn_meta = {}
                normalized = (
                    normalize_gmgn_migrated_trenches(payload, network, observed_at=observed)
                    if provider in {"gmgn", "gmgn-rank"}
                    else normalize_binance_meme_rush(payload, network, observed_at=observed)
                )
                if provider == "binance":
                    normalized = [row for row in normalized if row.get("launchStage") == "migrated"]
                rows.extend(normalized)
                # The rank supplement is part of the same GMGN chain source;
                # keep the public status at six chains instead of exposing a
                # second pseudo-provider in the UI.
                source_status[status_key] = "ok"
                retry_after_seconds = max(
                    retry_after_seconds,
                    int(_safe_float(gmgn_meta.get("retryAfterSeconds")) or 0),
                )
            except GmgnRateLimitError as exc:
                source_status[status_key] = "rate_limited"
                retry_after_seconds = max(retry_after_seconds, exc.retry_after_seconds)
                errors.append(f"{status_key}: {str(exc)[:180]}")
            except GmgnUnsupportedNetworkError as exc:
                source_status[status_key] = "unsupported"
                errors.append(f"{status_key}: {str(exc)[:180]}")
            except Exception as exc:
                source_status[status_key] = "error"
                errors.append(f"{status_key}: {str(exc)[:180]}")

    merged = merge_onchain_research_rows(rows)
    if include_non_og_exceptions:
        annotate_gmgn_non_og_exceptions(merged)
    search_text = str(query or "").strip().casefold()[:80]
    items: list[dict[str, Any]] = []
    for row in merged:
        if search_text and search_text not in " ".join((
            str(row.get("symbol") or ""),
            str(row.get("name") or ""),
            str(row.get("contractAddress") or ""),
        )).casefold():
            continue
        evaluated = evaluate_onchain_candidate(row, now_ms=observed)
        providers = [str(value) for value in row.get("providers") or []]
        contract = str(row.get("contractAddress") or "").strip()
        network = str(row.get("network") or "").strip().lower()
        chain_id = {
            "eth": 1,
            "bsc": 56,
            "base": 8453,
            "solana": 792703809,
            "robinhood": 4663,
        }.get(network)
        address_ok = bool(
            chain_id and contract and (
                (chain_id == 792703809 and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", contract))
                or (chain_id != 792703809 and re.fullmatch(r"0x[0-9a-fA-F]{40}", contract))
            )
        )
        items.append({
            **row,
            **evaluated,
            "providers": providers,
            "trenchSources": [
                label
                for provider, label in (
                    ("gmgn-trenches", "GMGN"),
                    ("binance-meme-rush", "Binance"),
                )
                if provider in providers
            ],
            "launchStage": "opened",
            # The user chose GMGN as the contract authority for this page. No
            # extra on-chain CA lookup is performed; syntax checking only keeps
            # malformed/empty provider data out of the transaction builder.
            "buyIdentity": {
                "status": "verified" if address_ok else "unresolved",
                "reason": "CA 直接来自 GMGN API" if address_ok else "GMGN CA 或目标链暂不支持买入",
                "source": "gmgn-api",
                "expiresAt": observed + 10 * 60 * 1000,
                "target": {
                    "symbol": str(row.get("symbol") or row.get("name") or "标的")[:80],
                    "chainId": chain_id,
                    "address": contract if chain_id == 792703809 else contract.lower(),
                    "kind": "token",
                },
            },
        })
    unfiltered_total = len(items)
    if item_filter is not None:
        filtered_items: list[dict[str, Any]] = []
        for row in items:
            try:
                if item_filter(row):
                    filtered_items.append(row)
            except Exception:
                continue
        items = filtered_items
    items.sort(key=lambda row: (
        int(_safe_float(row.get("poolCreatedAt")) or 0),
        int(_safe_float(row.get("observedAt")) or observed),
        float(_safe_float(row.get("selectedScore")) or 0),
    ), reverse=True)
    if per_network_limit is not None:
        cap = max(1, int(per_network_limit))
        network_counts: dict[str, int] = {}
        balanced_items: list[dict[str, Any]] = []
        for row in items:
            network = _chain_key(row.get("network") or row.get("chain"))
            if network_counts.get(network, 0) >= cap:
                continue
            network_counts[network] = network_counts.get(network, 0) + 1
            balanced_items.append(row)
        items = balanced_items
    counts = {
        "gmgn": sum(1 for row in items if "GMGN" in (row.get("trenchSources") or [])),
        "binance": sum(1 for row in items if "Binance" in (row.get("trenchSources") or [])),
    }
    total = len(items)
    # Public UI routes still clamp to 60. Internal history ingestion can retain
    # all six GMGN batches (up to 80 per chain) without extra provider calls.
    bounded_page_size = max(12, min(480, int(page_size or 24)))
    pages = max(1, math.ceil(total / bounded_page_size))
    page_number = min(max(1, int(page or 1)), pages)
    offset = (page_number - 1) * bounded_page_size
    supported_responses = [status for status in source_status.values() if status != "unsupported"]
    api_status = gmgn_api_key_status()
    cooldown = gmgn_cooldown_status()
    retry_after_seconds = max(retry_after_seconds, int(cooldown.get("retryAfterSeconds") or 0))
    return {
        # An explicitly selected Binance-only chain can be unsupported without
        # meaning that the live service failed.  Return an empty, healthy tape
        # so the UI can explain the capability boundary instead of showing a
        # misleading transport error.
        "ok": any(status == "ok" for status in supported_responses) or (
            bool(source_status) and not supported_responses
        ),
        "live": True,
        "stage": "opened",
        "items": items[offset: offset + bounded_page_size],
        "unfilteredTotal": unfiltered_total,
        "total": total,
        "page": page_number,
        "pageSize": bounded_page_size,
        "pages": pages,
        "updatedAt": observed,
        "counts": counts,
        "networks": list(ONCHAIN_RESEARCH_DEFAULT_NETWORKS),
        "sourceStatus": source_status,
        "errors": errors[:12],
        "rateLimited": bool(cooldown.get("active")) or any(
            status == "rate_limited" for status in source_status.values()
        ),
        "retryAfterSeconds": retry_after_seconds,
        "cooldownUntil": int(cooldown.get("retryAt") or 0),
        "gmgnApi": api_status,
        "filters": {
            "network": selected_networks[0] if len(selected_networks) == 1 else "",
            "source": source_key,
            "query": str(query or "").strip()[:80],
            "personalGmgnPresetApplied": False,
        },
    }


def fetch_defillama_protocols(*, session: Any = None) -> list[dict[str, Any]]:
    payload = _get_json("https://api.llama.fi/protocols", session=session)
    return [dict(row) for row in payload if isinstance(row, Mapping)] if isinstance(payload, list) else []


def fetch_github_repository(
    repository: str,
    *,
    session: Any = None,
    token: str | None = None,
) -> Mapping[str, Any]:
    repo = str(repository or "").strip().strip("/")
    if repo.count("/") != 1:
        raise ValueError("GitHub repository must be owner/name")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    github_token = str(token if token is not None else os.getenv("GITHUB_TOKEN", "")).strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return _get_json(f"https://api.github.com/repos/{repo}", session=session, headers=headers)


def fetch_blockscout_chain(
    base_url: str,
    *,
    session: Any = None,
    pages: int = 3,
) -> Mapping[str, Any]:
    root = str(base_url or "").strip().rstrip("/")
    if not root.startswith("https://"):
        raise ValueError("Blockscout base URL must use HTTPS")
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    params: dict[str, Any] = {"type": "ERC-20"}
    next_page: Mapping[str, Any] | None = None
    for _ in range(max(1, min(10, int(pages)))):
        try:
            payload = _get_json(f"{root}/api/v2/tokens", session=session, params=params)
        except Exception:
            if combined:
                break
            raise
        rows = payload.get("items") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list):
            break
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            identity = str(row.get("address_hash") or row.get("address") or "").lower()
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            combined.append(dict(row))
        next_page = payload.get("next_page_params") if isinstance(payload, Mapping) else None
        if not isinstance(next_page, Mapping) or not next_page:
            break
        params = {"type": "ERC-20", **dict(next_page)}
    return {"items": combined, "next_page_params": dict(next_page or {})}


def _extract_opensea_ranking_collections(document: str, chain: str) -> list[dict[str, Any]]:
    """Extract the server-rendered ranking payload used by OpenSea's public chain page."""
    decoder = json.JSONDecoder()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    marker = '{"score":'
    while True:
        start = document.find(marker, offset)
        if start < 0:
            break
        try:
            item, length = decoder.raw_decode(document[start:])
        except json.JSONDecodeError:
            offset = start + len(marker)
            continue
        offset = start + max(length, len(marker))
        collection = item.get("collection") if isinstance(item, Mapping) else None
        if not isinstance(collection, Mapping):
            continue
        item_chain = collection.get("chain") if isinstance(collection.get("chain"), Mapping) else {}
        if _chain_key(item_chain.get("identifier")) != _chain_key(chain):
            continue
        slug = _slug(collection.get("slug") or collection.get("name"))
        if not slug or slug in seen:
            continue
        seen.add(slug)
        rows.append(dict(item))
    return rows


def fetch_opensea_collections(
    chain: str,
    *,
    session: Any = None,
    api_key: str | None = None,
    limit: int = 100,
) -> Mapping[str, Any]:
    """Fetch OpenSea rankings through the official API, with its public SSR page as a no-key fallback."""
    chain_key = _chain_key(chain)
    if not chain_key:
        raise ValueError("OpenSea chain is required")
    client = session or requests
    configured_key = str(
        api_key
        if api_key is not None
        else os.getenv("OPENSEA_API_KEY") or os.getenv("XINGYUN_OPENSEA_API_KEY") or ""
    ).strip()
    api_error: Exception | None = None
    if configured_key:
        try:
            payload = _get_json(
                "https://api.opensea.io/api/v2/collections/top",
                session=client,
                headers={"X-API-KEY": configured_key},
                params={"chains": chain_key, "limit": max(1, min(100, int(limit)))},
            )
            rows = payload.get("collections") if isinstance(payload, Mapping) else None
            if isinstance(rows, list) and rows:
                return {"collections": rows, "sourceMode": "api"}
        except Exception as exc:
            api_error = exc

    page_url = f"https://opensea.io/collections/chain/{chain_key}"
    try:
        response = client.get(
            page_url,
            headers={**PROVIDER_HEADERS, "Accept": "text/html,application/xhtml+xml"},
            timeout=(5, 25),
        )
        response.raise_for_status()
        document = str(getattr(response, "text", "") or "")
        if not document and getattr(response, "content", b""):
            document = bytes(response.content).decode("utf-8", errors="replace")
        rows = _extract_opensea_ranking_collections(document, chain_key)
        if not rows:
            raise RuntimeError("OpenSea page did not expose collection rankings")
        return {"collections": rows[: max(1, min(100, int(limit)))], "sourceMode": "public-page"}
    except Exception:
        if api_error is not None:
            raise api_error
        raise


def _base_evidence(
    provider: str,
    evidence_type: str,
    observed_at: int,
    *,
    url: str = "",
    confidence: float = 0,
    title: str = "",
) -> dict[str, Any]:
    return {
        "source": provider,
        "evidenceType": evidence_type,
        "observedAt": int(observed_at),
        "url": str(url or "")[:800],
        "confidence": _clean_number(float(_bounded_score(confidence) or 0)),
        "title": str(title or "")[:400],
    }


def _normalize_geckoterminal(payload: Any, chain: Mapping[str, Any], observed_at: int) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else []
    rows: list[dict[str, Any]] = []
    chain_key = _chain_key(chain.get("providerNetwork") or chain.get("geckoterminalNetwork") or chain.get("slug"))
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, Mapping):
            continue
        attributes = item.get("attributes") if isinstance(item.get("attributes"), Mapping) else {}
        relationships = item.get("relationships") if isinstance(item.get("relationships"), Mapping) else {}
        pool_address = _address(attributes.get("address"))
        if not pool_address:
            continue
        base_relation = relationships.get("base_token") if isinstance(relationships.get("base_token"), Mapping) else {}
        base_data = base_relation.get("data") if isinstance(base_relation.get("data"), Mapping) else {}
        dex_relation = relationships.get("dex") if isinstance(relationships.get("dex"), Mapping) else {}
        dex_data = dex_relation.get("data") if isinstance(dex_relation.get("data"), Mapping) else {}
        contract_address = _relationship_address(base_data.get("id"))
        liquidity = _safe_float(attributes.get("reserve_in_usd"))
        volume = attributes.get("volume_usd") if isinstance(attributes.get("volume_usd"), Mapping) else {}
        transactions = attributes.get("transactions") if isinstance(attributes.get("transactions"), Mapping) else {}
        transactions_24h = transactions.get("h24") if isinstance(transactions.get("h24"), Mapping) else {}
        tx_count = _safe_int(transactions_24h.get("buys")) + _safe_int(transactions_24h.get("sells"))
        name = str(attributes.get("name") or item.get("id") or "Unknown pool")[:160]
        rows.append(
            {
                "provider": "geckoterminal",
                "providers": ["geckoterminal"],
                "subjectType": "asset",
                "externalId": str(item.get("id") or pool_address),
                "observedAt": int(observed_at),
                "chainKey": chain_key,
                "projectName": name.split("/")[0].strip() or name,
                "symbol": name.split("/")[0].strip().upper()[:40],
                "contractAddress": contract_address,
                "poolAddress": pool_address,
                "marketKey": "dex_liquidity",
                "tokenStage": "trading" if (liquidity or 0) > 0 and tx_count > 0 else "contract_confirmed",
                "metrics": {
                    "priceUsd": _safe_float(attributes.get("base_token_price_usd")),
                    "liquidityUsd": liquidity,
                    "volume24hUsd": _safe_float(volume.get("h24")),
                    "transactions24h": tx_count or None,
                    "poolCreatedAt": attributes.get("pool_created_at"),
                },
                "evidence": [
                    _base_evidence(
                        "geckoterminal",
                        "trading_pool",
                        observed_at,
                        confidence=85,
                        title=f"{name} pool on {dex_data.get('id') or 'DEX'}",
                    )
                ],
            }
        )
    return rows


def _normalize_dexscreener(payload: Any, chain: Mapping[str, Any], observed_at: int) -> list[dict[str, Any]]:
    data = payload.get("pairs") if isinstance(payload, Mapping) else payload
    rows: list[dict[str, Any]] = []
    configured_chain = _chain_key(chain.get("providerNetwork") or chain.get("dexscreenerChain") or chain.get("slug"))
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, Mapping):
            continue
        pool_address = _address(item.get("pairAddress"))
        base = item.get("baseToken") if isinstance(item.get("baseToken"), Mapping) else {}
        contract_address = _address(base.get("address"))
        if not pool_address or not contract_address:
            continue
        liquidity_obj = item.get("liquidity") if isinstance(item.get("liquidity"), Mapping) else {}
        volume_obj = item.get("volume") if isinstance(item.get("volume"), Mapping) else {}
        txns_obj = item.get("txns") if isinstance(item.get("txns"), Mapping) else {}
        txns_24h = txns_obj.get("h24") if isinstance(txns_obj.get("h24"), Mapping) else {}
        liquidity = _safe_float(liquidity_obj.get("usd"))
        tx_count = _safe_int(txns_24h.get("buys")) + _safe_int(txns_24h.get("sells"))
        chain_key = _chain_key(item.get("chainId") or configured_chain)
        name = str(base.get("name") or base.get("symbol") or contract_address)[:160]
        rows.append(
            {
                "provider": "dexscreener",
                "providers": ["dexscreener"],
                "subjectType": "asset",
                "externalId": pool_address,
                "observedAt": int(observed_at),
                "chainKey": chain_key,
                "projectName": name,
                "symbol": str(base.get("symbol") or "")[:40].upper(),
                "contractAddress": contract_address,
                "poolAddress": pool_address,
                "marketKey": "dex_liquidity",
                "tokenStage": "trading" if (liquidity or 0) > 0 and tx_count > 0 else "contract_confirmed",
                "metrics": {
                    "priceUsd": _safe_float(item.get("priceUsd")),
                    "liquidityUsd": liquidity,
                    "volume24hUsd": _safe_float(volume_obj.get("h24")),
                    "transactions24h": tx_count or None,
                    "poolCreatedAt": item.get("pairCreatedAt"),
                },
                "evidence": [
                    _base_evidence(
                        "dexscreener",
                        "trading_pair",
                        observed_at,
                        url=str(item.get("url") or ""),
                        confidence=85,
                        title=f"{name} pair on {item.get('dexId') or 'DEX'}",
                    )
                ],
            }
        )
    return rows


def _normalize_defillama(payload: Any, chain: Mapping[str, Any], observed_at: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    chain_names = {
        _chain_key(chain.get("name")),
        _chain_key(chain.get("slug")),
        _chain_key(chain.get("defillamaChain")),
    } - {""}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, Mapping):
            continue
        networks = {_chain_key(value) for value in (item.get("chains") or [])}
        if chain_names and not chain_names.intersection(networks):
            continue
        category = str(item.get("category") or "").strip().lower()
        market_key = DEFILLAMA_CATEGORY_MARKETS.get(category)
        if not market_key:
            continue
        slug = _slug(item.get("slug") or item.get("name"))
        name = str(item.get("name") or slug)[:160]
        rows.append(
            {
                "provider": "defillama",
                "providers": ["defillama"],
                "subjectType": "project",
                "externalId": str(item.get("id") or slug),
                "observedAt": int(observed_at),
                "chainKey": _chain_key(chain.get("slug") or chain.get("name")),
                "projectSlug": slug,
                "projectName": name,
                "officialUrl": str(item.get("url") or "")[:800],
                "contractAddress": "",
                "poolAddress": "",
                "marketKey": market_key,
                "metrics": {
                    "tvlUsd": _safe_float(item.get("tvl")),
                    "tvlChange1d": _safe_float(item.get("change_1d")),
                },
                "evidence": [
                    _base_evidence(
                        "defillama",
                        "protocol_tvl",
                        observed_at,
                        url=f"https://defillama.com/protocol/{slug}" if slug else "",
                        confidence=75,
                        title=f"{name} TVL and category",
                    )
                ],
            }
        )
    return rows


def _normalize_github(payload: Any, chain: Mapping[str, Any], observed_at: int) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or not payload.get("full_name"):
        return []
    stars = _safe_int(payload.get("stargazers_count"))
    forks = _safe_int(payload.get("forks_count"))
    development = min(100.0, 20.0 + math.log10(stars + 1) * 16.0 + math.log10(forks + 1) * 10.0)
    name = str(payload.get("full_name") or "").split("/")[-1]
    return [
        {
            "provider": "github",
            "providers": ["github"],
            "subjectType": "project",
            "externalId": str(payload.get("id") or payload.get("full_name")),
            "observedAt": int(observed_at),
            "chainKey": _chain_key(chain.get("slug") or chain.get("name")),
            "projectSlug": _slug(name),
            "projectName": name,
            "githubRepo": str(payload.get("full_name") or "")[:300],
            "officialUrl": str(payload.get("html_url") or "")[:800],
            "contractAddress": "",
            "poolAddress": "",
            "marketKey": "",
            "metrics": {
                "development": _clean_number(development),
                "stars": stars,
                "forks": forks,
                "openIssues": _safe_int(payload.get("open_issues_count")),
                "pushedAt": payload.get("pushed_at"),
            },
            "evidence": [
                _base_evidence(
                    "github",
                    "repository_activity",
                    observed_at,
                    url=str(payload.get("html_url") or ""),
                    confidence=70,
                    title=f"{payload.get('full_name')} repository activity",
                )
            ],
        }
    ]


def _normalize_blockscout(payload: Any, chain: Mapping[str, Any], observed_at: int) -> list[dict[str, Any]]:
    data = payload.get("items") if isinstance(payload, Mapping) else []
    rows: list[dict[str, Any]] = []
    chain_key = _chain_key(
        chain.get("providerNetwork")
        or chain.get("geckoterminalNetwork")
        or chain.get("dexscreenerChain")
        or chain.get("slug")
        or chain.get("name")
    )
    explorer = str(chain.get("explorerUrl") or "").rstrip("/")
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, Mapping):
            continue
        contract_address = _address(item.get("address_hash") or item.get("address"))
        if not contract_address:
            continue
        name = str(item.get("name") or item.get("symbol") or contract_address)[:160]
        rows.append(
            {
                "provider": "blockscout",
                "providers": ["blockscout"],
                "subjectType": "asset",
                "externalId": contract_address,
                "observedAt": int(observed_at),
                "chainKey": chain_key,
                "projectName": name,
                "symbol": str(item.get("symbol") or "")[:40].upper(),
                "contractAddress": contract_address,
                "poolAddress": "",
                "marketKey": "",
                "tokenStage": "contract_confirmed",
                "metrics": {
                    "holders": _safe_int(item.get("holders_count")),
                    "priceUsd": _safe_float(item.get("exchange_rate")),
                },
                "evidence": [
                    _base_evidence(
                        "blockscout",
                        "explorer_contract",
                        observed_at,
                        url=f"{explorer}/token/{contract_address}" if explorer else "",
                        confidence=80,
                        title=f"{name} verified explorer contract",
                    )
                ],
            }
        )
    return rows


def _normalize_opensea(payload: Any, chain: Mapping[str, Any], observed_at: int) -> list[dict[str, Any]]:
    data = payload.get("collections") if isinstance(payload, Mapping) else payload
    rows: list[dict[str, Any]] = []
    configured_chain = _chain_key(chain.get("geckoterminalNetwork") or chain.get("slug") or chain.get("name"))
    if configured_chain == "robinhood-chain":
        configured_chain = "robinhood"
    for raw in data if isinstance(data, list) else []:
        if not isinstance(raw, Mapping):
            continue
        collection = raw.get("collection") if isinstance(raw.get("collection"), Mapping) else raw
        slug = _slug(collection.get("slug") or collection.get("collection") or collection.get("name"))
        name = str(collection.get("name") or slug).strip()[:160]
        if not slug or not name:
            continue
        chain_row = collection.get("chain") if isinstance(collection.get("chain"), Mapping) else {}
        item_chain = _chain_key(chain_row.get("identifier") or collection.get("chain"))
        if item_chain and configured_chain and item_chain != configured_chain:
            continue
        stats = collection.get("stats") if isinstance(collection.get("stats"), Mapping) else {}
        one_day = stats.get("oneDay") if isinstance(stats.get("oneDay"), Mapping) else stats.get("one_day")
        one_day = one_day if isinstance(one_day, Mapping) else {}
        seven_days = stats.get("sevenDays") if isinstance(stats.get("sevenDays"), Mapping) else stats.get("seven_days")
        seven_days = seven_days if isinstance(seven_days, Mapping) else {}
        floor = collection.get("floorPrice") if isinstance(collection.get("floorPrice"), Mapping) else {}
        price = floor.get("pricePerItem") if isinstance(floor.get("pricePerItem"), Mapping) else floor
        price = price if isinstance(price, Mapping) else {}
        token = price.get("token") if isinstance(price.get("token"), Mapping) else {}

        def volume_values(period: Mapping[str, Any]) -> tuple[float | None, float | None]:
            volume = period.get("volume") if isinstance(period.get("volume"), Mapping) else {}
            native = volume.get("native") if isinstance(volume.get("native"), Mapping) else {}
            return _safe_float(native.get("unit") or volume.get("native")), _safe_float(volume.get("usd"))

        volume_24h_native, volume_24h_usd = volume_values(one_day)
        volume_7d_native, volume_7d_usd = volume_values(seven_days)
        floor_native = _safe_float(token.get("unit") or price.get("native"))
        floor_symbol = str(token.get("symbol") or price.get("symbol") or "")[:16]
        floor_usd = _safe_float(price.get("usd"))
        sales_24h = _safe_int(one_day.get("sales"))
        sales_7d = _safe_int(seven_days.get("sales"))
        official_url = f"https://opensea.io/collection/{slug}"
        rows.append(
            {
                "provider": "opensea",
                "providers": ["opensea"],
                "subjectType": "project",
                "externalId": str(collection.get("id") or slug),
                "observedAt": int(observed_at),
                "chainKey": configured_chain,
                "projectSlug": slug,
                "projectName": name,
                "officialUrl": official_url,
                "description": "OpenSea NFT collection",
                "contractAddress": "",
                "poolAddress": "",
                "marketKey": "nft",
                "tokenStage": "trading" if sales_24h > 0 or (volume_24h_usd or 0) > 0 else "potential",
                "metrics": {
                    "floorPriceNative": floor_native,
                    "floorPriceSymbol": floor_symbol,
                    "floorPriceUsd": floor_usd,
                    "volume24hNative": volume_24h_native,
                    "volume24hUsd": volume_24h_usd,
                    "volume7dNative": volume_7d_native,
                    "volume7dUsd": volume_7d_usd,
                    "transactions24h": sales_24h or None,
                    "sales7d": sales_7d or None,
                    "holders": _safe_int(stats.get("ownerCount") or stats.get("num_owners")) or None,
                    "totalSupply": _safe_int(stats.get("totalSupply") or stats.get("total_supply")) or None,
                    "listedItems": _safe_int(stats.get("listedItemCount") or stats.get("listed_items")) or None,
                    "openSeaScore": _safe_float(raw.get("score")),
                },
                "evidence": [
                    _base_evidence(
                        "opensea",
                        "nft_market_activity",
                        observed_at,
                        url=official_url,
                        confidence=92,
                        title=f"{name} floor, sales and collection analytics on OpenSea",
                    )
                ],
            }
        )
    return rows


def normalize_provider_rows(
    provider: str,
    payload: Any,
    chain: Mapping[str, Any],
    *,
    observed_at: int | None = None,
) -> list[dict[str, Any]]:
    observed = int(observed_at or _now_ms())
    normalizers = {
        "geckoterminal": _normalize_geckoterminal,
        "dexscreener": _normalize_dexscreener,
        "defillama": _normalize_defillama,
        "github": _normalize_github,
        "blockscout": _normalize_blockscout,
        "opensea": _normalize_opensea,
    }
    normalizer = normalizers.get(str(provider or "").strip().lower())
    if not normalizer:
        raise ValueError("unsupported chain ecosystem provider")
    return normalizer(payload, chain, observed)


def _entity_key(row: Mapping[str, Any]) -> str:
    chain_key = _chain_key(row.get("chainKey"))
    pool = _address(row.get("poolAddress"))
    contract = _address(row.get("contractAddress"))
    project = _slug(row.get("projectSlug") or row.get("projectName"))
    if contract:
        return f"{chain_key}:token:{contract}"
    if pool:
        return f"{chain_key}:pool:{pool}"
    return f"{chain_key}:project:{project or row.get('externalId') or ''}"


def merge_provider_entities(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for source_row in sorted((dict(row) for row in rows), key=lambda row: int(row.get("observedAt") or 0)):
        key = _entity_key(source_row)
        if not key.rsplit(":", 1)[-1]:
            continue
        current = merged.get(key)
        if current is None:
            current = dict(source_row)
            current["providers"] = sorted(set(source_row.get("providers") or [source_row.get("provider")]))
            current["metrics"] = dict(source_row.get("metrics") or {})
            current["evidence"] = list(source_row.get("evidence") or [])
            merged[key] = current
            continue
        current["observedAt"] = max(int(current.get("observedAt") or 0), int(source_row.get("observedAt") or 0))
        current["providers"] = sorted(
            set(current.get("providers") or []) | set(source_row.get("providers") or [source_row.get("provider")])
        )
        for field in (
            "projectSlug",
            "projectName",
            "symbol",
            "officialUrl",
            "githubRepo",
            "contractAddress",
            "poolAddress",
            "marketKey",
        ):
            if source_row.get(field):
                current[field] = source_row[field]
        stage_order = {stage: index for index, stage in enumerate(PROJECT_TOKEN_STAGES)}
        incoming_stage = source_row.get("tokenStage")
        current_stage = current.get("tokenStage")
        if incoming_stage and stage_order.get(str(incoming_stage), -1) > stage_order.get(str(current_stage), -1):
            current["tokenStage"] = incoming_stage
        for metric, value in (source_row.get("metrics") or {}).items():
            if value is not None:
                current["metrics"][metric] = value
        evidence_seen = {
            (item.get("source"), item.get("evidenceType"), item.get("url"), item.get("title"))
            for item in current.get("evidence") or []
        }
        for evidence in source_row.get("evidence") or []:
            evidence_key = (
                evidence.get("source"),
                evidence.get("evidenceType"),
                evidence.get("url"),
                evidence.get("title"),
            )
            if evidence_key not in evidence_seen:
                current["evidence"].append(evidence)
                evidence_seen.add(evidence_key)
    return sorted(merged.values(), key=lambda row: (_chain_key(row.get("chainKey")), _entity_key(row)))


def infer_market_classifications(entity: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Infer one or more taxonomy markets from provider-backed project metadata."""
    fields = (
        entity.get("projectName"),
        entity.get("symbol"),
        entity.get("projectSlug"),
        entity.get("description"),
        entity.get("category"),
    )
    text = " ".join(str(value or "") for value in fields).lower()
    symbol = str(entity.get("symbol") or "").strip().upper()
    results: dict[str, dict[str, Any]] = {}

    def add(market_key: str, confidence: float, reason: str) -> None:
        current = results.get(market_key)
        if current is None or confidence > float(current["confidence"]):
            results[market_key] = {
                "marketKey": market_key,
                "confidence": _clean_number(confidence),
                "reason": reason,
            }

    explicit = str(entity.get("marketKey") or "").strip()
    known_keys = {market["key"] for market in DEFAULT_MARKETS}
    if explicit in known_keys:
        add(explicit, 92, "数据源已提供细分市场类别")

    protocol_keys = {"dex", "lending", "derivatives", "bridge_assets", "validators"}
    if explicit in protocol_keys:
        add("dex", 82, "生态协议可归入 L1 协议代币市场")

    def contains(*patterns: str) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    if contains(r"\bbridge\b", r"cross[ -]?chain", "跨链"):
        add("bridge_assets", 88, "名称或类别包含跨链桥特征")
        add("infrastructure", 84, "跨链桥属于生态基础设施")
        add("dex", 78, "跨链协议属于 L1 生态协议")
    if contains(r"\boracle\b", r"\bwallet\b", r"\brpc\b", r"\bindexer\b", r"\bexplorer\b", "预言机", "钱包"):
        add("infrastructure", 84, "名称或类别包含基础设施特征")
    if contains(r"\blending\b", r"\blender\b", r"\bcredit\b", r"\bborrow", r"\bloan\b", r"\bmorpho\b", r"\btermmax\b", r"\bvault\b", "借贷"):
        add("lending", 88, "名称或类别包含借贷特征")
        add("dex", 80, "借贷协议属于 L1 生态协议")
    if contains(r"\bperp", r"\bderivative", r"\boption", r"\bpredict", r"\bbet(?:s|ting)?\b", "衍生", "预测"):
        add("derivatives", 86, "名称或类别包含衍生品特征")
        add("dex", 78, "衍生品协议属于 L1 生态协议")
    if contains(r"\bvalidator", r"\brestaking\b", r"\bstaking\b", r"\bnode\b", "验证节点", "再质押"):
        add("validators", 84, "名称或类别包含节点或质押特征")
        add("infrastructure", 76, "节点服务属于生态基础设施")

    stable_symbols = {"USDC", "USDT", "USDE", "USDG", "DAI", "PYUSD", "USDS", "FRAX"}
    if symbol in stable_symbols or contains(r"\bstable\s?coin\b", "稳定币"):
        add("stablecoin", 92, "稳定币名称或符号匹配")
    if contains(r"robinhood token", r"stock token", r"tokeni[sz]ed", r"real[ -]?world", r"\brwa\b", "代币化股票", "现实资产"):
        add("ai_depin_rwa", 92, "名称或类别包含 RWA / 代币化资产特征")
    if contains(r"\bdepin\b", r"\bai agent", r"\bby virtuals\b", r"\bartificial intelligence\b", "人工智能"):
        add("ai_depin_rwa", 84, "名称或类别包含 AI / DePIN 特征")

    if contains(r"\bmeme\b", r"\bdoge\b", r"\bshib\b", r"\bpepe\b", r"\bwojak\b", r"\bwif\b", r"\blambo\b", r"\byolo\b", r"\bkitsu\b", r"\bpanda\b", r"\bloxi\b", r"\bcat\b"):
        add("meme", 82, "名称或符号包含 MEME 社区特征")
    if contains(r"\blaunchpad\b", r"fair[ -]?launch", r"\bpump\b", r"token launch", "发射平台", "公平发射"):
        add("launchpad", 84, "名称或类别包含发射平台特征")
    if contains(r"\bnft\b", r"\bpfp\b", r"collectible", "数字藏品"):
        add("nft", 86, "名称或类别包含 NFT 特征")
    if contains(r"\bgamefi\b", r"\bgaming\b", r"\bgame\b", r"play[ -]?to[ -]?earn", r"\bhoodz\b", "链游"):
        add("gamefi", 86, "名称或类别包含链游特征")
    if contains(r"\bpoints?\b", r"\bairdrop\b", r"otc allocation", "积分", "空投"):
        add("points", 82, "名称或类别包含积分或空投特征")
    if contains(r"\bidentity\b", r"name service", r"\bdomain\b", r"\bsbt\b", "身份", "域名"):
        add("identity", 84, "名称或类别包含身份或域名特征")

    return sorted(results.values(), key=lambda row: (-float(row["confidence"]), row["marketKey"]))


def safe_provider_fetch(
    store: "ChainEcosystemStore",
    chain_id: int,
    provider: str,
    fetcher,
    *,
    previous_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    checked_at = _now_ms()
    try:
        rows = fetcher()
        normalized = [dict(row) for row in rows] if isinstance(rows, list) else rows
        store.update_source_health(chain_id, provider, ok=True, checked_at=checked_at)
        return {"rows": normalized, "stale": False, "warning": ""}
    except Exception as exc:
        store.update_source_health(
            chain_id,
            provider,
            ok=False,
            error=str(exc)[:500],
            checked_at=checked_at,
        )
        return {
            "rows": [dict(row) for row in (previous_rows or [])],
            "stale": True,
            "warning": f"{provider} 暂不可用，保留上次可信数据",
        }


def is_valid_trading_pool(metrics: Mapping[str, Any] | None) -> bool:
    values = metrics or {}
    liquidity = _safe_float(values.get("liquidityUsd")) or 0.0
    transactions = _safe_int(values.get("transactions24h"))
    return liquidity >= 25_000 and transactions >= 10


def is_valid_market_activity(entity: Mapping[str, Any]) -> bool:
    metrics = entity.get("metrics") if isinstance(entity.get("metrics"), Mapping) else {}
    if str(entity.get("marketKey") or "") == "nft":
        return (_safe_float(metrics.get("volume24hUsd")) or 0.0) >= 100 or _safe_int(
            metrics.get("transactions24h")
        ) >= 1
    return is_valid_trading_pool(metrics)


def resolve_chain_stage(current_stage: str, evidence: Iterable[Mapping[str, Any]]) -> str:
    stage_order = {stage: index for index, stage in enumerate(CHAIN_STAGES)}
    current = current_stage if current_stage in stage_order else "early_watch"
    rows = [dict(row) for row in evidence]
    official_mainnet = any(
        row.get("evidenceType") in {"official_mainnet_announcement", "public_mainnet"}
        and str(row.get("source") or "") in {"official", "manual"}
        and float(_bounded_score(row.get("confidence")) or 0) >= 80
        for row in rows
    )
    public_mainnet = any(
        row.get("evidenceType") == "public_mainnet"
        and float(_bounded_score(row.get("confidence")) or 0) >= 80
        for row in rows
    )
    valid_pool = any(
        row.get("evidenceType") in {"trading_pool", "trading_pair"}
        and float(_bounded_score(row.get("confidence")) or 0) >= 70
        and is_valid_trading_pool(row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {})
        for row in rows
    )
    candidate = "tradable_ecosystem" if public_mainnet and valid_pool else "mainnet_focus" if official_mainnet else current
    return candidate if stage_order[candidate] > stage_order[current] else current


def resolve_project_token_stage(
    project: Mapping[str, Any],
    evidence: Iterable[Mapping[str, Any]],
) -> str:
    current = str(project.get("tokenStage") or "potential")
    if current in {"paused", "invalid"}:
        return current
    rows = [dict(row) for row in evidence]
    announced = any(
        row.get("evidenceType") in {"token_announcement", "token_launch_announcement"}
        and str(row.get("source") or "official") in {"official", "manual"}
        and float(_bounded_score(row.get("confidence")) or 0) >= 80
        for row in rows
    )
    contract_confirmed = any(
        row.get("evidenceType") in {"official_contract", "explorer_contract"}
        and float(_bounded_score(row.get("confidence")) or 0) >= 70
        for row in rows
    )
    valid_pool = any(
        row.get("evidenceType") in {"trading_pool", "trading_pair"}
        and float(_bounded_score(row.get("confidence")) or 0) >= 70
        and is_valid_trading_pool(row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {})
        for row in rows
    )
    active_nft_market = any(
        row.get("evidenceType") == "nft_market_activity"
        and float(_bounded_score(row.get("confidence")) or 0) >= 70
        and (
            (_safe_float((row.get("metrics") or {}).get("volume24hUsd")) or 0) >= 100
            or _safe_int((row.get("metrics") or {}).get("transactions24h")) >= 1
        )
        for row in rows
        if isinstance(row.get("metrics"), Mapping)
    )
    if active_nft_market:
        return "trading"
    if contract_confirmed and valid_pool:
        return "trading"
    if contract_confirmed:
        return "contract_confirmed"
    if announced:
        return "announced"
    return current if current in PROJECT_TOKEN_STAGES else "potential"


def classify_project_markets(
    project: Mapping[str, Any],
    evidence: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for row in evidence:
        market_key = str(row.get("marketKey") or "").strip()
        if not market_key:
            continue
        source = str(row.get("source") or "")
        confidence = float(_bounded_score(row.get("confidence")) or 0)
        candidate = candidates.setdefault(
            market_key,
            {"marketKey": market_key, "sources": set(), "confidence": 0.0, "official": False},
        )
        candidate["sources"].add(source)
        candidate["confidence"] = max(candidate["confidence"], confidence)
        candidate["official"] = candidate["official"] or source in {"official", "manual"}
    confirmed_keys = {market["key"] for market in DEFAULT_MARKETS}
    result: list[dict[str, Any]] = []
    for market_key, candidate in candidates.items():
        known = market_key in confirmed_keys
        confirmed = known or candidate["official"] or len(candidate["sources"] - {""}) >= 2
        result.append(
            {
                "marketKey": market_key,
                "confidence": _clean_number(candidate["confidence"]),
                "sources": sorted(candidate["sources"] - {""}),
                "dynamic": not known,
                "reviewStatus": "confirmed" if confirmed else "pending",
                "projectId": project.get("id"),
            }
        )
    return sorted(result, key=lambda row: (row["reviewStatus"] != "confirmed", row["marketKey"]))


def _event(
    event_type: str,
    dedupe_key: str,
    title: str,
    *,
    chain_id: int,
    observed_at: int,
    market_key: str = "",
    project_id: int | None = None,
    confidence: float = 90,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "eventType": event_type,
        "dedupeKey": dedupe_key,
        "title": title,
        "chainId": int(chain_id),
        "marketKey": market_key,
        "projectId": int(project_id) if project_id else None,
        "confidence": _clean_number(float(confidence)),
        "observedAt": int(observed_at),
        "details": dict(details or {}),
    }


def detect_high_value_alerts(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not current.get("complete"):
        return []
    previous_payload = dict(previous or {})
    if previous_payload and not previous_payload.get("complete"):
        return []
    chain = current.get("chain") if isinstance(current.get("chain"), Mapping) else {}
    previous_chain = previous_payload.get("chain") if isinstance(previous_payload.get("chain"), Mapping) else {}
    chain_id = int(chain.get("id") or previous_chain.get("id") or 0)
    observed_at = int(current.get("observedAt") or _now_ms())
    events: list[dict[str, Any]] = []

    stage_order = {stage: index for index, stage in enumerate(CHAIN_STAGES)}
    previous_stage = str(previous_chain.get("stage") or "early_watch")
    current_stage = str(chain.get("stage") or previous_stage)
    if previous_payload and stage_order.get(current_stage, -1) > stage_order.get(previous_stage, -1):
        events.append(
            _event(
                "stage_upgrade",
                f"chain:{chain_id}:stage:{current_stage}",
                f"公链阶段升级为 {current_stage}",
                chain_id=chain_id,
                observed_at=observed_at,
                confidence=100,
                details={"from": previous_stage, "to": current_stage},
            )
        )

    previous_markets = previous_payload.get("markets") if isinstance(previous_payload.get("markets"), Mapping) else {}
    current_markets = current.get("markets") if isinstance(current.get("markets"), Mapping) else {}
    if previous_payload:
        for market_key in sorted(set(current_markets) - set(previous_markets)):
            events.append(
                _event(
                    "new_market",
                    f"chain:{chain_id}:market:{market_key}",
                    f"发现新细分市场：{market_key}",
                    chain_id=chain_id,
                    observed_at=observed_at,
                    market_key=market_key,
                    details={"marketKey": market_key},
                )
            )

    for market_key in sorted(set(current_markets).intersection(previous_markets)):
        before = previous_markets.get(market_key) if isinstance(previous_markets.get(market_key), Mapping) else {}
        after = current_markets.get(market_key) if isinstance(current_markets.get(market_key), Mapping) else {}
        before_leader = before.get("leader") if isinstance(before.get("leader"), Mapping) else {}
        after_leader = after.get("leader") if isinstance(after.get("leader"), Mapping) else {}
        before_id = int(before_leader.get("projectId") or 0)
        after_id = int(after_leader.get("projectId") or 0)
        score_margin = float(after_leader.get("score") or 0) - float(before_leader.get("score") or 0)
        if before_id and after_id and before_id != after_id and int(after.get("leaderStreak") or 0) >= 2 and score_margin >= 5:
            events.append(
                _event(
                    "leader_change",
                    f"chain:{chain_id}:market:{market_key}:leader:{after_id}",
                    f"{market_key} 龙头发生变化",
                    chain_id=chain_id,
                    observed_at=observed_at,
                    market_key=market_key,
                    project_id=after_id,
                    details={"previousProjectId": before_id, "scoreMargin": _clean_number(score_margin)},
                )
            )

    previous_projects = previous_payload.get("projects") if isinstance(previous_payload.get("projects"), Mapping) else {}
    current_projects = current.get("projects") if isinstance(current.get("projects"), Mapping) else {}
    for project_key, project_value in current_projects.items():
        project = project_value if isinstance(project_value, Mapping) else {}
        before = previous_projects.get(project_key) if isinstance(previous_projects.get(project_key), Mapping) else {}
        project_id = int(project.get("id") or project_key or 0)
        name = str(project.get("name") or f"项目 {project_id}")
        previous_metrics = before.get("metrics") if isinstance(before.get("metrics"), Mapping) else {}
        metrics = project.get("metrics") if isinstance(project.get("metrics"), Mapping) else {}
        volume = _safe_float(metrics.get("volume24hUsd")) or 0.0
        volume_median = (
            _safe_float(previous_metrics.get("volumeMedian24hUsd"))
            or _safe_float(previous_metrics.get("volume24hUsd"))
            or 0.0
        )
        liquidity = _safe_float(metrics.get("liquidityUsd")) or 0.0
        previous_liquidity = _safe_float(previous_metrics.get("liquidityUsd")) or 0.0
        transactions = _safe_float(metrics.get("transactions24h")) or 0.0
        previous_transactions = _safe_float(previous_metrics.get("transactions24h")) or 0.0
        volume_surge = volume >= 100_000 and volume_median > 0 and volume >= volume_median * 2.5
        liquidity_surge = (
            previous_liquidity > 0
            and liquidity >= previous_liquidity * 1.5
            and liquidity - previous_liquidity >= 25_000
        )
        transaction_surge = (
            previous_transactions > 0
            and transactions >= previous_transactions * 3
            and transactions - previous_transactions >= 100
        )
        if before and (volume_surge or liquidity_surge or transaction_surge):
            events.append(
                _event(
                    "market_surge",
                    f"chain:{chain_id}:project:{project_id}:surge:{observed_at}",
                    f"{name} 流动性或成交显著放大",
                    chain_id=chain_id,
                    observed_at=observed_at,
                    project_id=project_id,
                    details={
                        "volumeSurge": volume_surge,
                        "liquiditySurge": liquidity_surge,
                        "transactionSurge": transaction_surge,
                    },
                )
            )
    return events


ROBINHOOD_MAINNET_ANNOUNCEMENT = (
    "https://robinhood.com/us/en/newsroom/"
    "robinhood-accelerates-global-expansion-robinhood-chain-mainnet-stock-tokens-agentic-trading/"
)


def seed_robinhood_chain(store: "ChainEcosystemStore") -> dict[str, Any]:
    chain = store.upsert_chain(
        {
            "slug": "robinhood-chain",
            "name": "Robinhood Chain",
            "stage": "tradable_ecosystem",
            "chainType": "Ethereum L2 / Arbitrum",
            "chainId": "4663",
            "gasSymbol": "ETH",
            "officialUrl": "https://robinhood.com/",
            "docsUrl": "https://docs.robinhood.com/chain/",
            "rpcUrl": "https://rpc.mainnet.chain.robinhood.com/",
            "explorerUrl": "https://robinhoodchain.blockscout.com",
            "geckoterminalNetwork": "robinhood",
        }
    )
    store.add_evidence(
        chain["id"],
        "chain",
        chain["id"],
        {
            "source": "official",
            "evidenceType": "public_mainnet",
            "url": ROBINHOOD_MAINNET_ANNOUNCEMENT,
            "title": "Robinhood Chain Public Mainnet",
            "summary": "Robinhood announced the public mainnet on 2026-07-01.",
            "confidence": 100,
            "observedAt": 1782864000000,
        },
    )
    store.add_evidence(
        chain["id"],
        "chain",
        chain["id"],
        {
            "source": "official",
            "evidenceType": "official_docs",
            "url": "https://docs.robinhood.com/chain/",
            "title": "Robinhood Chain developer documentation",
            "confidence": 100,
        },
    )
    return chain


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_text(value: Any, limit: int = 32_000) -> str:
    text = json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return json.dumps({"truncated": True, "preview": text[: limit - 80]}, ensure_ascii=False, separators=(",", ":"))


def _json_value(value: Any, fallback: Any = None) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _slug(value: Any) -> str:
    cleaned = "-".join(str(value or "").strip().lower().replace("_", "-").split())
    return "".join(char for char in cleaned if char.isalnum() or char == "-").strip("-")[:120]


class _RefreshWriter:
    def __init__(self, store: "ChainEcosystemStore", conn: sqlite3.Connection):
        self.store = store
        self.conn = conn

    def save_ranking_snapshot(
        self,
        chain_id: int,
        market_key: str,
        project_id: int,
        *,
        observed_at: int,
        rank: int,
        score: float,
        confidence: float = 0,
        metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.store._save_ranking_snapshot(
            self.conn,
            chain_id,
            market_key,
            project_id,
            observed_at=observed_at,
            rank=rank,
            score=score,
            confidence=confidence,
            metrics=metrics,
        )


class ChainEcosystemStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def recent_research_pool_ids(self, network: str) -> list[str]:
        conn = self._connect()
        try:
            return [f"{network}_{row[0]}" for row in conn.execute(
                "SELECT pool_address FROM onchain_research_candidates WHERE network = ? ORDER BY last_seen_at DESC LIMIT 120",
                (network,),
            ) if row[0]]
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS chains (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        slug TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        stage TEXT NOT NULL DEFAULT 'early_watch',
                        chain_type TEXT NOT NULL DEFAULT '',
                        network_chain_id TEXT NOT NULL DEFAULT '',
                        gas_symbol TEXT NOT NULL DEFAULT '',
                        official_url TEXT NOT NULL DEFAULT '',
                        docs_url TEXT NOT NULL DEFAULT '',
                        rpc_url TEXT NOT NULL DEFAULT '',
                        explorer_url TEXT NOT NULL DEFAULT '',
                        geckoterminal_network TEXT NOT NULL DEFAULT '',
                        scan_enabled INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS stage_transitions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        project_id INTEGER,
                        previous_stage TEXT NOT NULL DEFAULT '',
                        next_stage TEXT NOT NULL,
                        evidence_id INTEGER,
                        observed_at INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS markets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        market_key TEXT NOT NULL,
                        level TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        is_dynamic INTEGER NOT NULL DEFAULT 0,
                        review_status TEXT NOT NULL DEFAULT 'confirmed',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(chain_id, market_key),
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS projects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        slug TEXT NOT NULL,
                        name TEXT NOT NULL,
                        token_stage TEXT NOT NULL DEFAULT 'potential',
                        official_url TEXT NOT NULL DEFAULT '',
                        github_repo TEXT NOT NULL DEFAULT '',
                        description TEXT NOT NULL DEFAULT '',
                        manual INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(chain_id, slug),
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS project_markets (
                        project_id INTEGER NOT NULL,
                        market_id INTEGER NOT NULL,
                        relation_role TEXT NOT NULL DEFAULT 'member',
                        confidence REAL NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT '',
                        review_status TEXT NOT NULL DEFAULT 'confirmed',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(project_id, market_id),
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        FOREIGN KEY(market_id) REFERENCES markets(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS assets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        project_id INTEGER,
                        contract_address TEXT NOT NULL,
                        symbol TEXT NOT NULL DEFAULT '',
                        name TEXT NOT NULL DEFAULT '',
                        pool_address TEXT NOT NULL DEFAULT '',
                        token_status TEXT NOT NULL DEFAULT 'contract_confirmed',
                        first_trade_at INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(chain_id, contract_address),
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
                    );

                    CREATE TABLE IF NOT EXISTS evidence (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        subject_type TEXT NOT NULL,
                        subject_id TEXT NOT NULL,
                        evidence_type TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL,
                        source_url TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL DEFAULT '',
                        external_id TEXT NOT NULL DEFAULT '',
                        confidence REAL NOT NULL DEFAULT 0,
                        observed_at INTEGER NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        fingerprint TEXT NOT NULL UNIQUE,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS ranking_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        market_id INTEGER NOT NULL,
                        project_id INTEGER NOT NULL,
                        observed_at INTEGER NOT NULL,
                        rank INTEGER NOT NULL,
                        score REAL NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0,
                        metrics_json TEXT NOT NULL DEFAULT '{}',
                        complete INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        UNIQUE(chain_id, market_id, project_id, observed_at),
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE,
                        FOREIGN KEY(market_id) REFERENCES markets(id) ON DELETE CASCADE,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS alert_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        market_id INTEGER,
                        project_id INTEGER,
                        event_type TEXT NOT NULL,
                        dedupe_key TEXT NOT NULL UNIQUE,
                        severity TEXT NOT NULL DEFAULT 'high',
                        confidence REAL NOT NULL DEFAULT 0,
                        title TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        observed_at INTEGER NOT NULL,
                        delivered_at INTEGER NOT NULL DEFAULT 0,
                        acknowledged_at INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS source_health (
                        chain_id INTEGER NOT NULL,
                        provider TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        last_checked_at INTEGER NOT NULL DEFAULT 0,
                        last_success_at INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT '',
                        failure_streak INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(chain_id, provider),
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS chain_scan_state (
                        chain_id INTEGER PRIMARY KEY,
                        baseline_ready INTEGER NOT NULL DEFAULT 0,
                        last_completed_at INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS manual_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id INTEGER NOT NULL,
                        actor_id INTEGER NOT NULL DEFAULT 0,
                        action TEXT NOT NULL,
                        subject_type TEXT NOT NULL,
                        subject_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(chain_id) REFERENCES chains(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS onchain_research_candidates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        network TEXT NOT NULL,
                        contract_address TEXT NOT NULL,
                        pool_address TEXT NOT NULL DEFAULT '',
                        dex_id TEXT NOT NULL DEFAULT '',
                        symbol TEXT NOT NULL DEFAULT '',
                        name TEXT NOT NULL DEFAULT '',
                        candidate_type TEXT NOT NULL DEFAULT 'project',
                        decision TEXT NOT NULL DEFAULT 'warming',
                        score_version TEXT NOT NULL DEFAULT '',
                        meme_score REAL NOT NULL DEFAULT 0,
                        project_score REAL NOT NULL DEFAULT 0,
                        selected_score REAL NOT NULL DEFAULT 0,
                        confidence REAL NOT NULL DEFAULT 0,
                        first_seen_at INTEGER NOT NULL,
                        pool_created_at INTEGER NOT NULL DEFAULT 0,
                        last_seen_at INTEGER NOT NULL,
                        research_day TEXT NOT NULL,
                        trade_url TEXT NOT NULL DEFAULT '',
                        providers_json TEXT NOT NULL DEFAULT '[]',
                        metrics_json TEXT NOT NULL DEFAULT '{}',
                        reasons_json TEXT NOT NULL DEFAULT '[]',
                        risks_json TEXT NOT NULL DEFAULT '[]',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(network, contract_address)
                    );

                    CREATE TABLE IF NOT EXISTS onchain_research_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        candidate_id INTEGER NOT NULL,
                        observed_at INTEGER NOT NULL,
                        observed_bucket INTEGER NOT NULL,
                        decision TEXT NOT NULL,
                        candidate_type TEXT NOT NULL,
                        score_version TEXT NOT NULL,
                        meme_score REAL NOT NULL DEFAULT 0,
                        project_score REAL NOT NULL DEFAULT 0,
                        selected_score REAL NOT NULL DEFAULT 0,
                        confidence REAL NOT NULL DEFAULT 0,
                        metrics_json TEXT NOT NULL DEFAULT '{}',
                        reasons_json TEXT NOT NULL DEFAULT '[]',
                        risks_json TEXT NOT NULL DEFAULT '[]',
                        created_at INTEGER NOT NULL,
                        UNIQUE(candidate_id, observed_bucket),
                        FOREIGN KEY(candidate_id) REFERENCES onchain_research_candidates(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS onchain_research_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        observed_at INTEGER NOT NULL,
                        completed_at INTEGER NOT NULL,
                        discovered_count INTEGER NOT NULL DEFAULT 0,
                        shortlisted_count INTEGER NOT NULL DEFAULT 0,
                        source_status_json TEXT NOT NULL DEFAULT '{}',
                        error_json TEXT NOT NULL DEFAULT '[]'
                    );

                    CREATE TABLE IF NOT EXISTS onchain_leader_cases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        case_key TEXT NOT NULL UNIQUE,
                        network TEXT NOT NULL,
                        contract_address TEXT NOT NULL DEFAULT '',
                        symbol TEXT NOT NULL,
                        name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        launch_at INTEGER NOT NULL DEFAULT 0,
                        reason TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_chain_projects_stage ON projects(chain_id, token_stage);
                    CREATE INDEX IF NOT EXISTS idx_chain_evidence_subject ON evidence(chain_id, subject_type, subject_id, observed_at);
                    CREATE INDEX IF NOT EXISTS idx_chain_rank_latest ON ranking_snapshots(chain_id, market_id, observed_at, rank);
                    CREATE INDEX IF NOT EXISTS idx_chain_alert_time ON alert_events(chain_id, observed_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_onchain_candidate_day_score ON onchain_research_candidates(research_day, decision, selected_score DESC);
                    CREATE INDEX IF NOT EXISTS idx_onchain_snapshot_candidate_time ON onchain_research_snapshots(candidate_id, observed_at);
                    CREATE INDEX IF NOT EXISTS idx_onchain_runs_time ON onchain_research_runs(observed_at DESC);
                    """
                )
                now = _now_ms()
                conn.executemany(
                    """
                    INSERT INTO onchain_leader_cases (
                        case_key, network, contract_address, symbol, name, category,
                        launch_at, reason, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(case_key) DO UPDATE SET
                        network = excluded.network,
                        contract_address = excluded.contract_address,
                        symbol = excluded.symbol,
                        name = excluded.name,
                        category = excluded.category,
                        launch_at = excluded.launch_at,
                        reason = excluded.reason,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (
                            row["key"], row["network"], row["contractAddress"], row["symbol"],
                            row["name"], row["category"], _timestamp_ms(f"{row['launchDate']}T00:00:00Z"),
                            row["reason"], now, now,
                        )
                        for row in DEFAULT_ONCHAIN_LEADER_CASES
                    ],
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _onchain_candidate_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "network": row["network"],
            "contractAddress": row["contract_address"],
            "poolAddress": row["pool_address"],
            "dexId": row["dex_id"],
            "symbol": row["symbol"],
            "name": row["name"],
            "candidateType": row["candidate_type"],
            "decision": row["decision"],
            "scoreVersion": row["score_version"],
            "memeScore": _clean_number(float(row["meme_score"])),
            "projectScore": _clean_number(float(row["project_score"])),
            "selectedScore": _clean_number(float(row["selected_score"])),
            "confidence": _clean_number(float(row["confidence"])),
            "firstSeenAt": int(row["first_seen_at"]),
            "poolCreatedAt": int(row["pool_created_at"]),
            "lastSeenAt": int(row["last_seen_at"]),
            "ageMinutes": round(max(0, int(row["last_seen_at"]) - int(row["pool_created_at"] or row["first_seen_at"])) / 60_000, 1),
            "researchDay": row["research_day"],
            "tradeUrl": row["trade_url"],
            "providers": _json_value(row["providers_json"], []),
            "metrics": _json_value(row["metrics_json"], {}),
            "reasons": _json_value(row["reasons_json"], []),
            "risks": _json_value(row["risks_json"], []),
        }

    def save_onchain_research_scan(
        self,
        candidates: Iterable[Mapping[str, Any]],
        *,
        observed_at: int | None = None,
        source_status: Mapping[str, Any] | None = None,
        errors: Iterable[str] | None = None,
        candidate_scope: str = "",
    ) -> dict[str, Any]:
        """Persist one incremental scan and its as-observed factor snapshots."""
        observed = int(observed_at or _now_ms())
        normalized = [dict(row) for row in candidates if isinstance(row, Mapping)]
        with self._lock:
            conn = self._connect()
            try:
                for item in normalized:
                    network = _chain_key(item.get("network"))
                    address = _onchain_address(item.get("contractAddress"), network)
                    if not network or not address:
                        continue
                    identity_address = address.lower() if address.lower().startswith("0x") else address
                    existing_context = conn.execute(
                        """
                        SELECT providers_json, reasons_json
                        FROM onchain_research_candidates
                        WHERE network = ? AND contract_address = ?
                        """,
                        (network, identity_address),
                    ).fetchone()
                    if existing_context:
                        external_providers = [
                            value for value in _json_value(existing_context["providers_json"], [])
                            if str(value) not in {"geckoterminal", "dexscreener"}
                        ]
                        external_reasons = [
                            value for value in _json_value(existing_context["reasons_json"], [])
                            if str(value).startswith("外部线索：")
                        ]
                        item["providers"] = list(dict.fromkeys([
                            *(item.get("providers") or []), *external_providers,
                        ]))
                        item["reasons"] = list(dict.fromkeys([
                            *external_reasons, *(item.get("reasons") or []),
                        ]))[:4]
                    first_seen = int(_safe_float(item.get("firstSeenAt")) or observed)
                    item_observed = int(_safe_float(item.get("observedAt")) or observed)
                    pool_created = int(_safe_float(item.get("poolCreatedAt")) or 0)
                    decision = str(item.get("decision") or "warming")[:30]
                    values = (
                        network, identity_address, str(item.get("poolAddress") or "")[:120],
                        str(item.get("dexId") or "")[:80], str(item.get("symbol") or "")[:40],
                        str(item.get("name") or "")[:160], str(item.get("candidateType") or "project")[:20],
                        decision, str(item.get("scoreVersion") or ONCHAIN_RESEARCH_SCORE_VERSION)[:60],
                        float(_safe_float(item.get("memeScore")) or 0),
                        float(_safe_float(item.get("projectScore")) or 0),
                        float(_safe_float(item.get("selectedScore")) or 0),
                        float(_safe_float(item.get("confidence")) or 0),
                        first_seen, pool_created, item_observed, _research_day(first_seen),
                        str(item.get("tradeUrl") or "")[:800],
                        json.dumps(item.get("providers") or [], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item.get("metrics") or {}, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item.get("reasons") or [], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item.get("risks") or [], ensure_ascii=False, separators=(",", ":")),
                        observed, observed,
                    )
                    conn.execute(
                        """
                        INSERT INTO onchain_research_candidates (
                            network, contract_address, pool_address, dex_id, symbol, name,
                            candidate_type, decision, score_version, meme_score, project_score,
                            selected_score, confidence, first_seen_at, pool_created_at, last_seen_at,
                            research_day, trade_url, providers_json, metrics_json, reasons_json,
                            risks_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(network, contract_address) DO UPDATE SET
                            pool_address = CASE WHEN excluded.pool_address <> '' THEN excluded.pool_address ELSE onchain_research_candidates.pool_address END,
                            dex_id = CASE WHEN excluded.dex_id <> '' THEN excluded.dex_id ELSE onchain_research_candidates.dex_id END,
                            symbol = CASE WHEN excluded.symbol <> '' THEN excluded.symbol ELSE onchain_research_candidates.symbol END,
                            name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE onchain_research_candidates.name END,
                            candidate_type = excluded.candidate_type,
                            decision = excluded.decision,
                            score_version = excluded.score_version,
                            meme_score = excluded.meme_score,
                            project_score = excluded.project_score,
                            selected_score = excluded.selected_score,
                            confidence = excluded.confidence,
                            first_seen_at = MIN(onchain_research_candidates.first_seen_at, excluded.first_seen_at),
                            pool_created_at = CASE
                                WHEN onchain_research_candidates.pool_created_at = 0 THEN excluded.pool_created_at
                                WHEN excluded.pool_created_at = 0 THEN onchain_research_candidates.pool_created_at
                                ELSE MIN(onchain_research_candidates.pool_created_at, excluded.pool_created_at)
                            END,
                            last_seen_at = MAX(onchain_research_candidates.last_seen_at, excluded.last_seen_at),
                            research_day = CASE WHEN excluded.first_seen_at < onchain_research_candidates.first_seen_at THEN excluded.research_day ELSE onchain_research_candidates.research_day END,
                            trade_url = CASE WHEN excluded.trade_url <> '' THEN excluded.trade_url ELSE onchain_research_candidates.trade_url END,
                            providers_json = excluded.providers_json,
                            metrics_json = excluded.metrics_json,
                            reasons_json = excluded.reasons_json,
                            risks_json = excluded.risks_json,
                            updated_at = excluded.updated_at
                        WHERE excluded.last_seen_at >= onchain_research_candidates.last_seen_at
                        """,
                        values,
                    )
                    candidate_row = conn.execute(
                        "SELECT id FROM onchain_research_candidates WHERE network = ? AND contract_address = ?",
                        (network, identity_address),
                    ).fetchone()
                    if not candidate_row:
                        continue
                    bucket_size = 86_400_000 if decision == "filtered" else 300_000
                    bucket = item_observed // bucket_size * bucket_size
                    conn.execute(
                        """
                        INSERT INTO onchain_research_snapshots (
                            candidate_id, observed_at, observed_bucket, decision, candidate_type,
                            score_version, meme_score, project_score, selected_score, confidence,
                            metrics_json, reasons_json, risks_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(candidate_id, observed_bucket) DO UPDATE SET
                            observed_at = excluded.observed_at,
                            decision = excluded.decision,
                            candidate_type = excluded.candidate_type,
                            score_version = excluded.score_version,
                            meme_score = excluded.meme_score,
                            project_score = excluded.project_score,
                            selected_score = excluded.selected_score,
                            confidence = excluded.confidence,
                            metrics_json = excluded.metrics_json,
                            reasons_json = excluded.reasons_json,
                            risks_json = excluded.risks_json
                        """,
                        (
                            int(candidate_row["id"]), item_observed, bucket, decision,
                            str(item.get("candidateType") or "project")[:20],
                            str(item.get("scoreVersion") or ONCHAIN_RESEARCH_SCORE_VERSION)[:60],
                            float(_safe_float(item.get("memeScore")) or 0),
                            float(_safe_float(item.get("projectScore")) or 0),
                            float(_safe_float(item.get("selectedScore")) or 0),
                            float(_safe_float(item.get("confidence")) or 0),
                            json.dumps(item.get("metrics") or {}, ensure_ascii=False, separators=(",", ":")),
                            json.dumps(item.get("reasons") or [], ensure_ascii=False, separators=(",", ":")),
                            json.dumps(item.get("risks") or [], ensure_ascii=False, separators=(",", ":")),
                            observed,
                        ),
                    )
                # The normal research pass has a deliberately narrow universe:
                # GMGN Trenches only.  Keep older Binance/Dex/external records
                # for auditability, but mark them filtered when a scoped pass
                # completes so they cannot re-enter the active opportunity set.
                # Contract-mention and diagnostic callers omit this flag and
                # therefore retain their independent behaviour.
                if str(candidate_scope or "").strip().casefold() == "gmgn-trenches":
                    conn.execute(
                        """
                        UPDATE onchain_research_candidates
                        SET decision = 'filtered', updated_at = ?
                        WHERE providers_json NOT LIKE '%\"gmgn-trenches\"%'
                          AND decision <> 'filtered'
                        """,
                        (observed,),
                    )
                conn.execute(
                    """
                    INSERT INTO onchain_research_runs (
                        observed_at, completed_at, discovered_count, shortlisted_count,
                        source_status_json, error_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observed, _now_ms(), len(normalized),
                        sum(1 for row in normalized if row.get("decision") == "shortlisted"),
                        json.dumps(dict(source_status or {}), ensure_ascii=False, separators=(",", ":")),
                        json.dumps(list(errors or []), ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                conn.execute(
                    "DELETE FROM onchain_research_runs WHERE observed_at < ?",
                    (observed - 30 * 86_400_000,),
                )
                conn.commit()
            finally:
                conn.close()
        observer = getattr(self, "identity_observer", None)
        if observer:
            try:
                observer([row for row in normalized if row.get("decision") != "filtered"])
            except Exception:
                pass  # Identity preflight must not interrupt scan persistence.
        return {"saved": len(normalized), "observedAt": observed}

    def _onchain_benchmark(self, conn: sqlite3.Connection) -> dict[str, Any]:
        cases = conn.execute(
            "SELECT * FROM onchain_leader_cases WHERE enabled = 1 ORDER BY category, launch_at, id"
        ).fetchall()
        results: list[dict[str, Any]] = []
        for case in cases:
            candidate = conn.execute(
                "SELECT id FROM onchain_research_candidates WHERE network = ? AND contract_address = ?",
                (case["network"], case["contract_address"]),
            ).fetchone()
            snapshot = None
            if candidate and int(case["launch_at"]):
                snapshot = conn.execute(
                    """
                    SELECT decision, selected_score, observed_at
                    FROM onchain_research_snapshots
                    WHERE candidate_id = ? AND observed_at BETWEEN ? AND ?
                    ORDER BY observed_at ASC LIMIT 1
                    """,
                    (int(candidate["id"]), int(case["launch_at"]), int(case["launch_at"]) + 86_400_000),
                ).fetchone()
            results.append({
                "key": case["case_key"],
                "network": case["network"],
                "symbol": case["symbol"],
                "name": case["name"],
                "category": case["category"],
                "launchAt": int(case["launch_at"]),
                "reason": case["reason"],
                "replayable": bool(snapshot),
                "hit": bool(snapshot and snapshot["decision"] == "shortlisted"),
                "firstDecision": snapshot["decision"] if snapshot else "awaiting_snapshot",
                "firstScore": _clean_number(float(snapshot["selected_score"])) if snapshot else None,
            })
        replayable = [row for row in results if row["replayable"]]
        hits = [row for row in replayable if row["hit"]]
        category_stats = {}
        for category in ("meme", "project"):
            group = [row for row in replayable if row["category"] == category]
            category_stats[category] = {
                "caseCount": sum(1 for row in results if row["category"] == category),
                "replayable": len(group),
                "hits": sum(1 for row in group if row["hit"]),
                "recallPct": round(sum(1 for row in group if row["hit"]) / len(group) * 100, 1) if group else None,
            }
        return {
            "targetRecallPct": 80,
            "caseCount": len(results),
            "replayable": len(replayable),
            "hits": len(hits),
            "recallPct": round(len(hits) / len(replayable) * 100, 1) if replayable else None,
            "status": "validated" if replayable and len(hits) / len(replayable) >= 0.8 else "collecting" if not replayable else "below_target",
            "categories": category_stats,
            "cases": results,
        }

    def onchain_identity_rows(self) -> list[dict[str, Any]]:
        """Lightweight CA intake; never rebuild research scores or historical benchmarks."""
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(
                "SELECT network, contract_address AS contractAddress, symbol FROM onchain_research_candidates "
                "WHERE decision != 'filtered' AND last_seen_at >= ? "
                "ORDER BY last_seen_at DESC",
                (_now_ms() - 86_400_000,),
            ).fetchall()]
        finally:
            conn.close()

    def onchain_research_payload(
        self,
        *,
        now_ms: int | None = None,
        limit: int = 8,
        research_day: str | None = None,
    ) -> dict[str, Any]:
        now = int(now_ms or _now_ms())
        requested_day = str(research_day or "").strip()
        day = requested_day if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", requested_day) else _research_day(now)
        # `limit` is kept for older callers. The durable fast-research store
        # supplies every AI-qualified result; this source query only needs a
        # bounded compatibility sample instead of serializing the full day.
        conn = self._connect()
        try:
            available_days = [
                str(row["research_day"])
                for row in conn.execute(
                    """
                    SELECT research_day
                    FROM onchain_research_candidates
                    WHERE research_day <> ''
                    GROUP BY research_day
                    ORDER BY research_day DESC
                    LIMIT 30
                    """
                ).fetchall()
            ]
            decision_rows = conn.execute(
                """SELECT decision,COUNT(*) AS count FROM onchain_research_candidates
                WHERE research_day=?
                GROUP BY decision""", (day,)
            ).fetchall()
            decisions = {name: 0 for name in ("filtered", "warming", "watch", "shortlisted")}
            for decision_row in decision_rows:
                if decision_row["decision"] in decisions:
                    decisions[decision_row["decision"]] = int(decision_row["count"] or 0)
            rows = conn.execute(
                """
                SELECT * FROM onchain_research_candidates
                WHERE research_day = ? AND decision = 'shortlisted'
                ORDER BY selected_score DESC, confidence DESC, first_seen_at ASC
                LIMIT 200
                """,
                (day,),
            ).fetchall()
            candidates = [self._onchain_candidate_row(row) for row in rows]
            shortlisted = candidates
            market_providers = {"geckoterminal", "dexscreener"}
            source_backed = [
                row for row in shortlisted
                if any(str(provider) not in market_providers for provider in (row.get("providers") or []))
            ]
            selected = []
            selected_identities: set[tuple[str, str]] = set()
            for row in [*source_backed, *shortlisted]:
                identity = (str(row.get("network") or ""), str(row.get("contractAddress") or ""))
                if identity in selected_identities:
                    continue
                selected_identities.add(identity)
                selected.append(row)
            latest_run = conn.execute(
                "SELECT * FROM onchain_research_runs ORDER BY observed_at DESC, id DESC LIMIT 1"
            ).fetchone()
            recent_runs = conn.execute(
                """
                SELECT observed_at, source_status_json, error_json
                FROM onchain_research_runs
                WHERE observed_at >= ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 64
                """,
                (now - 15 * 60_000,),
            ).fetchall()
            source_status: dict[str, str] = {}
            source_last_success: dict[str, int] = {}
            recent_errors: list[str] = []
            for run in recent_runs:
                run_status = _json_value(run["source_status_json"], {})
                if isinstance(run_status, Mapping):
                    for network, status in run_status.items():
                        network_key = _chain_key(network)
                        if network_key not in ONCHAIN_RESEARCH_DEFAULT_NETWORKS:
                            continue
                        if network_key and network_key not in source_status:
                            source_status[network_key] = str(status or "error")
                        if network_key and str(status or "") == "ok":
                            source_last_success[network_key] = max(
                                source_last_success.get(network_key, 0),
                                int(run["observed_at"] or 0),
                            )
                for error in _json_value(run["error_json"], []):
                    error_text = str(error or "").strip()
                    if error_text and error_text not in recent_errors:
                        recent_errors.append(error_text)
            for network, status in list(source_status.items()):
                if status == "error" and now - source_last_success.get(network, 0) <= 10 * 60_000:
                    source_status[network] = "degraded"
            return {
                "scoreVersion": ONCHAIN_RESEARCH_SCORE_VERSION,
                "day": day,
                "currentDay": _research_day(now),
                "availableDays": available_days,
                "networks": list(ONCHAIN_RESEARCH_DEFAULT_NETWORKS),
                "funnel": {
                    "discovered": sum(decisions.values()),
                    "filtered": decisions["filtered"],
                    "warming": decisions["warming"],
                    "quantified": decisions["watch"] + decisions["shortlisted"],
                    "selected": decisions["shortlisted"],
                },
                "selected": selected,
                "fastResearchManaged": True,
                # Observation rows stay in SQLite; the attention UI only needs
                # the aggregate and no longer serializes thousands of entries.
                "watching": [],
                "watchingCount": decisions["watch"] + decisions["warming"],
                # Noise remains persisted for outcome replay and recall audits,
                # but is intentionally not sent to the attention-facing UI.
                "filtered": [],
                "hiddenNoiseCount": decisions["filtered"],
                "updatedAt": int(latest_run["completed_at"]) if latest_run else 0,
                "sourceStatus": source_status,
                "errors": recent_errors[:12],
                "benchmark": self._onchain_benchmark(conn),
            }
        finally:
            conn.close()

    def upsert_chain(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        slug = _slug(payload.get("slug") or payload.get("name"))
        name = str(payload.get("name") or "").strip()[:160]
        stage = str(payload.get("stage") or "early_watch").strip()
        if not slug or not name:
            raise ValueError("chain slug and name are required")
        if stage not in CHAIN_STAGES:
            raise ValueError("unsupported chain stage")
        now = int(payload.get("updatedAt") or _now_ms())
        values = (
            slug,
            name,
            stage,
            str(payload.get("chainType") or "")[:80],
            str(payload.get("chainId") or "")[:80],
            str(payload.get("gasSymbol") or "")[:24].upper(),
            str(payload.get("officialUrl") or "")[:600],
            str(payload.get("docsUrl") or "")[:600],
            str(payload.get("rpcUrl") or "")[:600],
            str(payload.get("explorerUrl") or "")[:600],
            str(payload.get("geckoterminalNetwork") or "")[:120],
            1 if payload.get("scanEnabled", True) else 0,
            now,
            now,
        )
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO chains (
                        slug, name, stage, chain_type, network_chain_id, gas_symbol,
                        official_url, docs_url, rpc_url, explorer_url, geckoterminal_network,
                        scan_enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        name = excluded.name,
                        stage = excluded.stage,
                        chain_type = excluded.chain_type,
                        network_chain_id = excluded.network_chain_id,
                        gas_symbol = excluded.gas_symbol,
                        official_url = CASE WHEN excluded.official_url <> '' THEN excluded.official_url ELSE chains.official_url END,
                        docs_url = CASE WHEN excluded.docs_url <> '' THEN excluded.docs_url ELSE chains.docs_url END,
                        rpc_url = CASE WHEN excluded.rpc_url <> '' THEN excluded.rpc_url ELSE chains.rpc_url END,
                        explorer_url = CASE WHEN excluded.explorer_url <> '' THEN excluded.explorer_url ELSE chains.explorer_url END,
                        geckoterminal_network = CASE WHEN excluded.geckoterminal_network <> '' THEN excluded.geckoterminal_network ELSE chains.geckoterminal_network END,
                        scan_enabled = excluded.scan_enabled,
                        updated_at = excluded.updated_at
                    """,
                    values,
                )
                row = conn.execute("SELECT * FROM chains WHERE slug = ?", (slug,)).fetchone()
                self._seed_markets(conn, int(row["id"]), now)
                conn.commit()
                return self._chain_row(row)
            finally:
                conn.close()

    def _seed_markets(self, conn: sqlite3.Connection, chain_id: int, now: int) -> None:
        conn.executemany(
            """
            INSERT INTO markets (
                chain_id, market_key, level, name, description, is_dynamic,
                review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, 'confirmed', ?, ?)
            ON CONFLICT(chain_id, market_key) DO UPDATE SET
                level = excluded.level,
                name = excluded.name,
                description = excluded.description,
                updated_at = excluded.updated_at
            """,
            [
                (chain_id, market["key"], market["level"], market["name"], market["description"], now, now)
                for market in DEFAULT_MARKETS
            ],
        )

    @staticmethod
    def _chain_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "slug": row["slug"],
            "name": row["name"],
            "stage": row["stage"],
            "chainType": row["chain_type"],
            "chainId": row["network_chain_id"],
            "gasSymbol": row["gas_symbol"],
            "officialUrl": row["official_url"],
            "docsUrl": row["docs_url"],
            "rpcUrl": row["rpc_url"],
            "explorerUrl": row["explorer_url"],
            "geckoterminalNetwork": row["geckoterminal_network"],
            "scanEnabled": bool(row["scan_enabled"]),
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
        }

    def list_chains(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM chains ORDER BY updated_at DESC, id").fetchall()
            return [self._chain_row(row) for row in rows]
        finally:
            conn.close()

    def get_chain(self, identifier: int | str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            if isinstance(identifier, int) or str(identifier).isdigit():
                row = conn.execute("SELECT * FROM chains WHERE id = ?", (int(identifier),)).fetchone()
            else:
                row = conn.execute("SELECT * FROM chains WHERE slug = ?", (_slug(identifier),)).fetchone()
            return self._chain_row(row) if row else None
        finally:
            conn.close()

    def update_chain_stage(
        self,
        chain_id: int,
        next_stage: str,
        *,
        evidence_id: int | None = None,
        observed_at: int | None = None,
    ) -> dict[str, Any]:
        if next_stage not in CHAIN_STAGES:
            raise ValueError("unsupported chain stage")
        observed = int(observed_at or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM chains WHERE id = ?", (int(chain_id),)).fetchone()
                if not row:
                    raise ValueError("chain not found")
                current_stage = str(row["stage"])
                if CHAIN_STAGES.index(next_stage) > CHAIN_STAGES.index(current_stage):
                    conn.execute(
                        "UPDATE chains SET stage = ?, updated_at = ? WHERE id = ?",
                        (next_stage, observed, int(chain_id)),
                    )
                    conn.execute(
                        """
                        INSERT INTO stage_transitions (
                            chain_id, previous_stage, next_stage, evidence_id, observed_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (int(chain_id), current_stage, next_stage, evidence_id, observed, _now_ms()),
                    )
                conn.commit()
                updated = conn.execute("SELECT * FROM chains WHERE id = ?", (int(chain_id),)).fetchone()
                return self._chain_row(updated)
            finally:
                conn.close()

    def list_markets(self, chain_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM markets WHERE chain_id = ? ORDER BY level, id",
                (int(chain_id),),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "chainId": int(row["chain_id"]),
                    "key": row["market_key"],
                    "level": row["level"],
                    "name": row["name"],
                    "description": row["description"],
                    "dynamic": bool(row["is_dynamic"]),
                    "reviewStatus": row["review_status"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def upsert_dynamic_market(
        self,
        chain_id: int,
        market_key: str,
        name: str,
        *,
        level: str = "L2",
        description: str = "",
        review_status: str = "pending",
    ) -> dict[str, Any]:
        key = _slug(market_key).replace("-", "_")
        clean_name = str(name or "").strip()[:160]
        if not key or not clean_name or level not in {"L0", "L1", "L2", "L3"}:
            raise ValueError("invalid dynamic market")
        now = _now_ms()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO markets (
                        chain_id, market_key, level, name, description, is_dynamic,
                        review_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(chain_id, market_key) DO UPDATE SET
                        name = excluded.name,
                        level = excluded.level,
                        description = excluded.description,
                        review_status = excluded.review_status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(chain_id),
                        key,
                        level,
                        clean_name,
                        str(description)[:600],
                        str(review_status)[:40],
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return next(row for row in self.list_markets(chain_id) if row["key"] == key)

    def upsert_project(self, chain_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        slug = _slug(payload.get("slug") or payload.get("name"))
        name = str(payload.get("name") or "").strip()[:160]
        token_stage = str(payload.get("tokenStage") or "potential")
        if not slug or not name:
            raise ValueError("project slug and name are required")
        if token_stage not in PROJECT_TOKEN_STAGES:
            raise ValueError("unsupported project token stage")
        now = int(payload.get("updatedAt") or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO projects (
                        chain_id, slug, name, token_stage, official_url, github_repo,
                        description, manual, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chain_id, slug) DO UPDATE SET
                        name = excluded.name,
                        token_stage = excluded.token_stage,
                        official_url = CASE WHEN excluded.official_url <> '' THEN excluded.official_url ELSE projects.official_url END,
                        github_repo = CASE WHEN excluded.github_repo <> '' THEN excluded.github_repo ELSE projects.github_repo END,
                        description = CASE WHEN excluded.description <> '' THEN excluded.description ELSE projects.description END,
                        manual = MAX(projects.manual, excluded.manual),
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(chain_id),
                        slug,
                        name,
                        token_stage,
                        str(payload.get("officialUrl") or "")[:600],
                        str(payload.get("githubRepo") or "")[:300],
                        str(payload.get("description") or "")[:1200],
                        1 if payload.get("manual") else 0,
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM projects WHERE chain_id = ? AND slug = ?",
                    (int(chain_id), slug),
                ).fetchone()
                conn.commit()
                return self._project_row(row)
            finally:
                conn.close()

    @staticmethod
    def _project_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "chainId": int(row["chain_id"]),
            "slug": row["slug"],
            "name": row["name"],
            "tokenStage": row["token_stage"],
            "officialUrl": row["official_url"],
            "githubRepo": row["github_repo"],
            "description": row["description"],
            "manual": bool(row["manual"]),
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
        }

    def list_projects(self, chain_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM projects WHERE chain_id = ? ORDER BY updated_at DESC, id",
                (int(chain_id),),
            ).fetchall()
            return [self._project_row(row) for row in rows]
        finally:
            conn.close()

    def link_project_market(
        self,
        project_id: int,
        market_key: str,
        *,
        confidence: float = 0,
        source: str = "",
        role: str = "member",
        review_status: str = "confirmed",
    ) -> dict[str, Any]:
        now = _now_ms()
        with self._lock:
            conn = self._connect()
            try:
                project = conn.execute("SELECT chain_id FROM projects WHERE id = ?", (int(project_id),)).fetchone()
                if not project:
                    raise ValueError("project not found")
                market = conn.execute(
                    "SELECT id FROM markets WHERE chain_id = ? AND market_key = ?",
                    (int(project["chain_id"]), str(market_key)),
                ).fetchone()
                if not market:
                    raise ValueError("market not found")
                conn.execute(
                    """
                    INSERT INTO project_markets (
                        project_id, market_id, relation_role, confidence, source,
                        review_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, market_id) DO UPDATE SET
                        relation_role = excluded.relation_role,
                        confidence = excluded.confidence,
                        source = excluded.source,
                        review_status = excluded.review_status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(project_id),
                        int(market["id"]),
                        str(role)[:40],
                        float(_bounded_score(confidence) or 0),
                        str(source)[:80],
                        str(review_status)[:40],
                        now,
                        now,
                    ),
                )
                conn.commit()
                return self.list_project_markets(project_id)[0]
            finally:
                conn.close()

    def list_project_markets(self, project_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT pm.*, m.market_key, m.level, m.name
                FROM project_markets pm
                JOIN markets m ON m.id = pm.market_id
                WHERE pm.project_id = ?
                ORDER BY m.level, m.id
                """,
                (int(project_id),),
            ).fetchall()
            return [
                {
                    "projectId": int(row["project_id"]),
                    "marketId": int(row["market_id"]),
                    "marketKey": row["market_key"],
                    "level": row["level"],
                    "name": row["name"],
                    "role": row["relation_role"],
                    "confidence": row["confidence"],
                    "source": row["source"],
                    "reviewStatus": row["review_status"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def list_project_markets_for_chain(self, chain_id: int) -> dict[int, list[dict[str, Any]]]:
        """Load every project/market relation for one chain in a single query."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT pm.*, m.market_key, m.level, m.name
                FROM project_markets pm
                JOIN markets m ON m.id = pm.market_id
                JOIN projects p ON p.id = pm.project_id
                WHERE p.chain_id = ?
                ORDER BY pm.project_id, m.level, m.id
                """,
                (int(chain_id),),
            ).fetchall()
            grouped: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(int(row["project_id"]), []).append({
                    "projectId": int(row["project_id"]),
                    "marketId": int(row["market_id"]),
                    "marketKey": row["market_key"],
                    "level": row["level"],
                    "name": row["name"],
                    "role": row["relation_role"],
                    "confidence": row["confidence"],
                    "source": row["source"],
                    "reviewStatus": row["review_status"],
                })
            return grouped
        finally:
            conn.close()

    def chain_summary_counts(self) -> dict[int, dict[str, int | bool]]:
        """Return project and confirmed-market counts without N+1 project reads."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    c.id AS chain_id,
                    COUNT(DISTINCT p.id) AS project_count,
                    COUNT(DISTINCT CASE WHEN pm.review_status = 'confirmed' THEN pm.market_id END) AS market_count,
                    MAX(CASE WHEN pm.review_status = 'confirmed' AND m.market_key = 'chain_token' THEN 1 ELSE 0 END) AS has_chain_token
                FROM chains c
                LEFT JOIN projects p ON p.chain_id = c.id
                LEFT JOIN project_markets pm ON pm.project_id = p.id
                LEFT JOIN markets m ON m.id = pm.market_id
                GROUP BY c.id
                """
            ).fetchall()
            return {
                int(row["chain_id"]): {
                    "projectCount": int(row["project_count"] or 0),
                    "marketCount": int(row["market_count"] or 0),
                    "hasChainToken": bool(row["has_chain_token"]),
                }
                for row in rows
            }
        finally:
            conn.close()

    def upsert_asset(self, chain_id: int, project_id: int | None, payload: Mapping[str, Any]) -> dict[str, Any]:
        contract = str(payload.get("contractAddress") or "").strip().lower()[:160]
        if not contract:
            raise ValueError("contract address is required")
        now = int(payload.get("updatedAt") or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO assets (
                        chain_id, project_id, contract_address, symbol, name, pool_address,
                        token_status, first_trade_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chain_id, contract_address) DO UPDATE SET
                        project_id = COALESCE(excluded.project_id, assets.project_id),
                        symbol = CASE WHEN excluded.symbol <> '' THEN excluded.symbol ELSE assets.symbol END,
                        name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE assets.name END,
                        pool_address = CASE WHEN excluded.pool_address <> '' THEN excluded.pool_address ELSE assets.pool_address END,
                        token_status = excluded.token_status,
                        first_trade_at = CASE WHEN assets.first_trade_at > 0 THEN assets.first_trade_at ELSE excluded.first_trade_at END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(chain_id),
                        int(project_id) if project_id else None,
                        contract,
                        str(payload.get("symbol") or "")[:40].upper(),
                        str(payload.get("name") or "")[:160],
                        str(payload.get("poolAddress") or "").strip().lower()[:160],
                        str(payload.get("status") or payload.get("tokenStage") or "contract_confirmed")[:40],
                        int(payload.get("firstTradeAt") or 0),
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM assets WHERE chain_id = ? AND contract_address = ?",
                    (int(chain_id), contract),
                ).fetchone()
                conn.commit()
                return {
                    "id": int(row["id"]),
                    "chainId": int(row["chain_id"]),
                    "projectId": int(row["project_id"]) if row["project_id"] else None,
                    "contractAddress": row["contract_address"],
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "poolAddress": row["pool_address"],
                    "status": row["token_status"],
                    "firstTradeAt": int(row["first_trade_at"]),
                }
            finally:
                conn.close()

    def list_assets(self, chain_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM assets WHERE chain_id = ? ORDER BY updated_at DESC, id",
                (int(chain_id),),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "chainId": int(row["chain_id"]),
                    "projectId": int(row["project_id"]) if row["project_id"] else None,
                    "contractAddress": row["contract_address"],
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "poolAddress": row["pool_address"],
                    "status": row["token_status"],
                    "firstTradeAt": int(row["first_trade_at"]),
                    "updatedAt": int(row["updated_at"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def add_evidence(
        self,
        chain_id: int,
        subject_type: str,
        subject_id: str | int,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        observed_at = int(payload.get("observedAt") or _now_ms())
        identity = {
            "chainId": int(chain_id),
            "subjectType": str(subject_type),
            "subjectId": str(subject_id),
            "evidenceType": str(payload.get("evidenceType") or ""),
            "source": str(payload.get("source") or "manual"),
            "url": str(payload.get("url") or ""),
            "externalId": str(payload.get("externalId") or ""),
            "title": str(payload.get("title") or ""),
            "summary": str(payload.get("summary") or ""),
        }
        fingerprint = str(payload.get("fingerprint") or "").strip() or hashlib.sha256(
            _json_text(identity).encode("utf-8")
        ).hexdigest()
        now = _now_ms()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO evidence (
                        chain_id, subject_type, subject_id, evidence_type, source,
                        source_url, title, summary, external_id, confidence,
                        observed_at, payload_json, fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        confidence = MAX(evidence.confidence, excluded.confidence),
                        observed_at = MAX(evidence.observed_at, excluded.observed_at),
                        payload_json = excluded.payload_json
                    """,
                    (
                        int(chain_id),
                        str(subject_type)[:40],
                        str(subject_id)[:160],
                        identity["evidenceType"][:80],
                        identity["source"][:80],
                        identity["url"][:800],
                        identity["title"][:400],
                        identity["summary"][:1600],
                        identity["externalId"][:240],
                        float(_bounded_score(payload.get("confidence")) or 0),
                        observed_at,
                        _json_text(payload.get("payload") or payload.get("metrics") or {}),
                        fingerprint,
                        now,
                    ),
                )
                row = conn.execute("SELECT * FROM evidence WHERE fingerprint = ?", (fingerprint,)).fetchone()
                conn.commit()
                return {
                    "id": int(row["id"]),
                    "fingerprint": row["fingerprint"],
                    "source": row["source"],
                    "url": row["source_url"],
                    "confidence": row["confidence"],
                    "observedAt": int(row["observed_at"]),
                }
            finally:
                conn.close()

    def list_evidence(
        self,
        chain_id: int,
        *,
        subject_type: str | None = None,
        subject_id: str | int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            sql = "SELECT * FROM evidence WHERE chain_id = ?"
            params: list[Any] = [int(chain_id)]
            if subject_type:
                sql += " AND subject_type = ?"
                params.append(str(subject_type))
            if subject_id is not None:
                sql += " AND subject_id = ?"
                params.append(str(subject_id))
            sql += " ORDER BY observed_at DESC, id DESC LIMIT ?"
            params.append(max(1, min(1000, int(limit))))
            rows = conn.execute(sql, params).fetchall()
            return [self._evidence_row(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _evidence_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "chainId": int(row["chain_id"]),
            "subjectType": row["subject_type"],
            "subjectId": row["subject_id"],
            "evidenceType": row["evidence_type"],
            "source": row["source"],
            "url": row["source_url"],
            "title": row["title"],
            "summary": row["summary"],
            "externalId": row["external_id"],
            "confidence": row["confidence"],
            "observedAt": int(row["observed_at"]),
            "payload": _json_value(row["payload_json"], {}),
        }

    def list_project_evidence_for_chain(
        self,
        chain_id: int,
        *,
        limit_per_project: int = 40,
    ) -> dict[int, list[dict[str, Any]]]:
        """Load the newest evidence for every project with one window query."""
        per_project = max(1, min(200, int(limit_per_project)))
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT e.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY e.subject_id
                               ORDER BY e.observed_at DESC, e.id DESC
                           ) AS project_row_number
                    FROM evidence e
                    WHERE e.chain_id = ? AND e.subject_type = 'project'
                )
                WHERE project_row_number <= ?
                ORDER BY subject_id, observed_at DESC, id DESC
                """,
                (int(chain_id), per_project),
            ).fetchall()
            grouped: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                try:
                    project_id = int(row["subject_id"])
                except (TypeError, ValueError):
                    continue
                grouped.setdefault(project_id, []).append(self._evidence_row(row))
            return grouped
        finally:
            conn.close()

    def update_source_health(
        self,
        chain_id: int,
        provider: str,
        *,
        ok: bool,
        error: str = "",
        checked_at: int | None = None,
    ) -> dict[str, Any]:
        checked = int(checked_at or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT * FROM source_health WHERE chain_id = ? AND provider = ?",
                    (int(chain_id), str(provider)),
                ).fetchone()
                last_success = checked if ok else int(existing["last_success_at"] if existing else 0)
                failure_streak = 0 if ok else int(existing["failure_streak"] if existing else 0) + 1
                conn.execute(
                    """
                    INSERT INTO source_health (
                        chain_id, provider, status, last_checked_at, last_success_at,
                        last_error, failure_streak, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chain_id, provider) DO UPDATE SET
                        status = excluded.status,
                        last_checked_at = excluded.last_checked_at,
                        last_success_at = excluded.last_success_at,
                        last_error = excluded.last_error,
                        failure_streak = excluded.failure_streak,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(chain_id),
                        str(provider)[:80],
                        "ok" if ok else "error",
                        checked,
                        last_success,
                        "" if ok else str(error)[:500],
                        failure_streak,
                        checked,
                    ),
                )
                conn.commit()
                return self.list_source_health(chain_id, provider=provider)[0]
            finally:
                conn.close()

    def list_source_health(self, chain_id: int, *, provider: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            sql = "SELECT * FROM source_health WHERE chain_id = ?"
            params: list[Any] = [int(chain_id)]
            if provider:
                sql += " AND provider = ?"
                params.append(str(provider))
            sql += " ORDER BY provider"
            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "chainId": int(row["chain_id"]),
                    "provider": row["provider"],
                    "status": row["status"],
                    "lastCheckedAt": int(row["last_checked_at"]),
                    "lastSuccessAt": int(row["last_success_at"]),
                    "lastError": row["last_error"],
                    "failureStreak": int(row["failure_streak"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def add_manual_audit(
        self,
        chain_id: int,
        action: str,
        subject_type: str,
        subject_id: str | int,
        payload: Mapping[str, Any],
        *,
        actor_id: int = 0,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        created = int(created_at or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO manual_audit (
                        chain_id, actor_id, action, subject_type, subject_id, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(chain_id),
                        int(actor_id),
                        str(action)[:80],
                        str(subject_type)[:40],
                        str(subject_id)[:160],
                        _json_text(payload),
                        created,
                    ),
                )
                conn.commit()
                return {"id": int(cursor.lastrowid), "createdAt": created}
            finally:
                conn.close()

    def list_manual_audit(self, chain_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM manual_audit WHERE chain_id = ? ORDER BY created_at DESC, id DESC",
                (int(chain_id),),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "actorId": int(row["actor_id"]),
                    "action": row["action"],
                    "subjectType": row["subject_type"],
                    "subjectId": row["subject_id"],
                    "payload": _json_value(row["payload_json"], {}),
                    "createdAt": int(row["created_at"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def _save_ranking_snapshot(
        self,
        conn: sqlite3.Connection,
        chain_id: int,
        market_key: str,
        project_id: int,
        *,
        observed_at: int,
        rank: int,
        score: float,
        confidence: float = 0,
        metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        market = conn.execute(
            "SELECT id FROM markets WHERE chain_id = ? AND market_key = ?",
            (int(chain_id), str(market_key)),
        ).fetchone()
        if not market:
            raise ValueError("market not found")
        conn.execute(
            """
            INSERT INTO ranking_snapshots (
                chain_id, market_id, project_id, observed_at, rank, score,
                confidence, metrics_json, complete, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(chain_id, market_id, project_id, observed_at) DO UPDATE SET
                rank = excluded.rank,
                score = excluded.score,
                confidence = excluded.confidence,
                metrics_json = excluded.metrics_json,
                complete = 1
            """,
            (
                int(chain_id),
                int(market["id"]),
                int(project_id),
                int(observed_at),
                int(rank),
                float(score),
                float(confidence),
                _json_text(metrics or {}),
                _now_ms(),
            ),
        )
        return {
            "chainId": int(chain_id),
            "marketKey": str(market_key),
            "projectId": int(project_id),
            "observedAt": int(observed_at),
            "rank": int(rank),
            "score": _clean_number(float(score)),
            "confidence": _clean_number(float(confidence)),
        }

    def save_ranking_snapshot(
        self,
        chain_id: int,
        market_key: str,
        project_id: int,
        *,
        observed_at: int,
        rank: int,
        score: float,
        confidence: float = 0,
        metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                result = self._save_ranking_snapshot(
                    conn,
                    chain_id,
                    market_key,
                    project_id,
                    observed_at=observed_at,
                    rank=rank,
                    score=score,
                    confidence=confidence,
                    metrics=metrics,
                )
                conn.commit()
                return result
            finally:
                conn.close()

    @contextmanager
    def refresh_transaction(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield _RefreshWriter(self, conn)
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()
            finally:
                conn.close()

    def latest_complete_snapshot(self, chain_id: int, market_key: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            market = conn.execute(
                "SELECT id FROM markets WHERE chain_id = ? AND market_key = ?",
                (int(chain_id), str(market_key)),
            ).fetchone()
            if not market:
                return None
            latest = conn.execute(
                """
                SELECT MAX(observed_at) AS observed_at
                FROM ranking_snapshots
                WHERE chain_id = ? AND market_id = ? AND complete = 1
                """,
                (int(chain_id), int(market["id"])),
            ).fetchone()
            observed_at = int(latest["observed_at"] or 0)
            if not observed_at:
                return None
            rows = conn.execute(
                """
                SELECT rs.*, p.slug, p.name, p.token_stage
                FROM ranking_snapshots rs
                JOIN projects p ON p.id = rs.project_id
                WHERE rs.chain_id = ? AND rs.market_id = ? AND rs.observed_at = ? AND rs.complete = 1
                ORDER BY rs.rank, rs.score DESC, p.slug
                """,
                (int(chain_id), int(market["id"]), observed_at),
            ).fetchall()
            return {
                "marketKey": str(market_key),
                "observedAt": observed_at,
                "rows": [
                    {
                        "projectId": int(row["project_id"]),
                        "slug": row["slug"],
                        "name": row["name"],
                        "tokenStage": row["token_stage"],
                        "rank": int(row["rank"]),
                        "score": _clean_number(float(row["score"])),
                        "confidence": _clean_number(float(row["confidence"])),
                        "metrics": _json_value(row["metrics_json"], {}),
                    }
                    for row in rows
                ],
            }
        finally:
            conn.close()

    def latest_rankings(self, chain_id: int) -> dict[str, dict[str, Any]]:
        """Load the latest complete snapshot for every market in one read."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                WITH latest AS (
                    SELECT market_id, MAX(observed_at) AS observed_at
                    FROM ranking_snapshots
                    WHERE chain_id = ? AND complete = 1
                    GROUP BY market_id
                )
                SELECT m.market_key, rs.*, p.slug, p.name, p.token_stage
                FROM latest
                JOIN ranking_snapshots rs
                  ON rs.market_id = latest.market_id
                 AND rs.observed_at = latest.observed_at
                 AND rs.chain_id = ?
                 AND rs.complete = 1
                JOIN markets m ON m.id = rs.market_id
                JOIN projects p ON p.id = rs.project_id
                ORDER BY m.level, m.id, rs.rank, rs.score DESC, p.slug
                """,
                (int(chain_id), int(chain_id)),
            ).fetchall()
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                market_key = str(row["market_key"])
                snapshot = result.setdefault(
                    market_key,
                    {"marketKey": market_key, "observedAt": int(row["observed_at"]), "rows": []},
                )
                snapshot["rows"].append(
                    {
                        "projectId": int(row["project_id"]),
                        "slug": row["slug"],
                        "name": row["name"],
                        "tokenStage": row["token_stage"],
                        "rank": int(row["rank"]),
                        "score": _clean_number(float(row["score"])),
                        "confidence": _clean_number(float(row["confidence"])),
                        "metrics": _json_value(row["metrics_json"], {}),
                    }
                )
            return result
        finally:
            conn.close()

    def recent_market_leaders(self, chain_id: int, market_key: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Return one Top1 row per recent complete snapshot, newest first."""
        conn = self._connect()
        try:
            market = conn.execute(
                "SELECT id FROM markets WHERE chain_id = ? AND market_key = ?",
                (int(chain_id), str(market_key)),
            ).fetchone()
            if not market:
                return []
            rows = conn.execute(
                """
                SELECT rs.project_id, rs.score, rs.confidence, rs.observed_at, p.slug, p.name
                FROM ranking_snapshots rs
                JOIN projects p ON p.id = rs.project_id
                WHERE rs.chain_id = ? AND rs.market_id = ? AND rs.rank = 1 AND rs.complete = 1
                ORDER BY rs.observed_at DESC, rs.id DESC
                LIMIT ?
                """,
                (int(chain_id), int(market["id"]), max(2, min(30, int(limit)))),
            ).fetchall()
            return [
                {
                    "projectId": int(row["project_id"]),
                    "slug": row["slug"],
                    "name": row["name"],
                    "score": _clean_number(float(row["score"])),
                    "confidence": _clean_number(float(row["confidence"])),
                    "observedAt": int(row["observed_at"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def recent_market_leaders_for_chain(self, chain_id: int, *, limit_per_market: int = 8) -> dict[str, list[dict[str, Any]]]:
        """Load recent Top1 history for every market without an N+1 read loop."""
        row_limit = max(2, min(30, int(limit_per_market)))
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                WITH recent AS (
                    SELECT
                        m.market_key,
                        rs.project_id,
                        rs.score,
                        rs.confidence,
                        rs.observed_at,
                        rs.id,
                        p.slug,
                        p.name,
                        ROW_NUMBER() OVER (
                            PARTITION BY rs.market_id
                            ORDER BY rs.observed_at DESC, rs.id DESC
                        ) AS recent_row_number
                    FROM ranking_snapshots rs
                    JOIN markets m ON m.id = rs.market_id
                    JOIN projects p ON p.id = rs.project_id
                    WHERE rs.chain_id = ? AND rs.rank = 1 AND rs.complete = 1
                )
                SELECT * FROM recent
                WHERE recent_row_number <= ?
                ORDER BY market_key, observed_at DESC, id DESC
                """,
                (int(chain_id), row_limit),
            ).fetchall()
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(str(row["market_key"]), []).append(
                    {
                        "projectId": int(row["project_id"]),
                        "slug": row["slug"],
                        "name": row["name"],
                        "score": _clean_number(float(row["score"])),
                        "confidence": _clean_number(float(row["confidence"])),
                        "observedAt": int(row["observed_at"]),
                    }
                )
            return grouped
        finally:
            conn.close()

    def get_scan_state(self, chain_id: int) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM chain_scan_state WHERE chain_id = ?",
                (int(chain_id),),
            ).fetchone()
            return {
                "chainId": int(chain_id),
                "baselineReady": bool(row["baseline_ready"]) if row else False,
                "lastCompletedAt": int(row["last_completed_at"]) if row else 0,
                "updatedAt": int(row["updated_at"]) if row else 0,
            }
        finally:
            conn.close()

    def mark_scan_complete(self, chain_id: int, *, completed_at: int | None = None) -> dict[str, Any]:
        completed = int(completed_at or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO chain_scan_state (chain_id, baseline_ready, last_completed_at, updated_at)
                    VALUES (?, 1, ?, ?)
                    ON CONFLICT(chain_id) DO UPDATE SET
                        baseline_ready = 1,
                        last_completed_at = MAX(chain_scan_state.last_completed_at, excluded.last_completed_at),
                        updated_at = excluded.updated_at
                    """,
                    (int(chain_id), completed, _now_ms()),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_scan_state(chain_id)

    def upsert_alert(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        dedupe_key = str(payload.get("dedupeKey") or "").strip()[:500]
        event_type = str(payload.get("eventType") or "").strip()[:80]
        title = str(payload.get("title") or "").strip()[:400]
        if not dedupe_key or not event_type or not title:
            raise ValueError("alert identity is required")
        chain_id = int(payload.get("chainId") or 0)
        observed_at = int(payload.get("observedAt") or _now_ms())
        market_id = None
        with self._lock:
            conn = self._connect()
            try:
                market_key = str(payload.get("marketKey") or "")
                if market_key:
                    market = conn.execute(
                        "SELECT id FROM markets WHERE chain_id = ? AND market_key = ?",
                        (chain_id, market_key),
                    ).fetchone()
                    market_id = int(market["id"]) if market else None
                conn.execute(
                    """
                    INSERT INTO alert_events (
                        chain_id, market_id, project_id, event_type, dedupe_key,
                        severity, confidence, title, details_json, observed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        confidence = MAX(alert_events.confidence, excluded.confidence),
                        title = excluded.title,
                        details_json = excluded.details_json,
                        observed_at = MAX(alert_events.observed_at, excluded.observed_at)
                    """,
                    (
                        chain_id,
                        market_id,
                        int(payload.get("projectId")) if payload.get("projectId") else None,
                        event_type,
                        dedupe_key,
                        str(payload.get("severity") or "high")[:30],
                        float(_bounded_score(payload.get("confidence")) or 0),
                        title,
                        _json_text(payload.get("details") or {}),
                        observed_at,
                        _now_ms(),
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM alert_events WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
                return self._alert_row(conn, row)
            finally:
                conn.close()

    @staticmethod
    def _alert_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        market_key = ""
        if row["market_id"]:
            market = conn.execute("SELECT market_key FROM markets WHERE id = ?", (int(row["market_id"]),)).fetchone()
            market_key = str(market["market_key"]) if market else ""
        return {
            "id": int(row["id"]),
            "chainId": int(row["chain_id"]),
            "marketKey": market_key,
            "projectId": int(row["project_id"]) if row["project_id"] else None,
            "eventType": row["event_type"],
            "dedupeKey": row["dedupe_key"],
            "severity": row["severity"],
            "confidence": row["confidence"],
            "title": row["title"],
            "details": _json_value(row["details_json"], {}),
            "observedAt": int(row["observed_at"]),
            "deliveredAt": int(row["delivered_at"]),
            "acknowledgedAt": int(row["acknowledged_at"]),
        }

    def list_alerts(self, chain_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM alert_events WHERE chain_id = ? AND event_type <> 'token_trading' ORDER BY observed_at DESC, id DESC LIMIT ?",
                (int(chain_id), max(1, min(500, int(limit)))),
            ).fetchall()
            return [self._alert_row(conn, row) for row in rows]
        finally:
            conn.close()

    def list_pending_alerts(self, chain_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM alert_events
                WHERE chain_id = ? AND delivered_at = 0 AND event_type <> 'token_trading'
                ORDER BY observed_at, id
                LIMIT ?
                """,
                (int(chain_id), max(1, min(500, int(limit)))),
            ).fetchall()
            return [self._alert_row(conn, row) for row in rows]
        finally:
            conn.close()

    def mark_alert_delivered(self, alert_id: int, *, delivered_at: int | None = None) -> dict[str, Any]:
        now = int(delivered_at or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE alert_events SET delivered_at = CASE WHEN delivered_at = 0 THEN ? ELSE delivered_at END WHERE id = ?",
                    (now, int(alert_id)),
                )
                row = conn.execute("SELECT * FROM alert_events WHERE id = ?", (int(alert_id),)).fetchone()
                if not row:
                    raise ValueError("alert not found")
                conn.commit()
                return self._alert_row(conn, row)
            finally:
                conn.close()

    def acknowledge_alert(self, alert_id: int, *, acknowledged_at: int | None = None) -> dict[str, Any]:
        now = int(acknowledged_at or _now_ms())
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE alert_events SET acknowledged_at = ? WHERE id = ?",
                    (now, int(alert_id)),
                )
                row = conn.execute("SELECT * FROM alert_events WHERE id = ?", (int(alert_id),)).fetchone()
                if not row:
                    raise ValueError("alert not found")
                conn.commit()
                return self._alert_row(conn, row)
            finally:
                conn.close()


def _log_metric_score(value: Any, *, floor: float, ceiling: float) -> float | None:
    parsed = _safe_float(value)
    if parsed is None or parsed <= 0:
        return None
    if ceiling <= floor:
        return None
    position = (math.log10(parsed) - math.log10(floor)) / (math.log10(ceiling) - math.log10(floor))
    return max(0.0, min(100.0, position * 100.0))


def _traded_score_inputs(entity: Mapping[str, Any]) -> dict[str, Any]:
    metrics = entity.get("metrics") if isinstance(entity.get("metrics"), Mapping) else {}
    liquidity = _log_metric_score(metrics.get("liquidityUsd"), floor=25_000, ceiling=25_000_000)
    volume = _log_metric_score(metrics.get("volume24hUsd"), floor=10_000, ceiling=50_000_000)
    transactions = _log_metric_score(metrics.get("transactions24h"), floor=10, ceiling=100_000)
    activity_parts = [value for value in (volume, transactions) if value is not None]
    tvl = _log_metric_score(metrics.get("tvlUsd"), floor=50_000, ceiling=1_000_000_000)
    holders = _log_metric_score(metrics.get("holders"), floor=10, ceiling=1_000_000)
    adoption_parts = [value for value in (tvl, holders) if value is not None]
    price_change = _safe_float(metrics.get("tvlChange1d"))
    price_strength = max(0.0, min(100.0, 50.0 + price_change * 2.5)) if price_change is not None else None
    evidence = entity.get("evidence") if isinstance(entity.get("evidence"), list) else []
    confidence = max(
        (float(_bounded_score(row.get("confidence")) or 0) for row in evidence if isinstance(row, Mapping)),
        default=0.0,
    )
    provider_count = len(set(entity.get("providers") or []))
    return {
        "liquidity": liquidity,
        "activity": sum(activity_parts) / len(activity_parts) if activity_parts else None,
        "adoption": sum(adoption_parts) / len(adoption_parts) if adoption_parts else None,
        "priceStrength": price_strength,
        "ecosystemCentrality": min(100.0, 35.0 + provider_count * 20.0),
        "evidenceConfidence": confidence or None,
    }


def build_ranking_snapshot(
    chain_id: int,
    observed_at: int,
    *,
    store: ChainEcosystemStore,
    candidates: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entity in candidates:
        market_key = str(entity.get("marketKey") or "")
        project_id = int(entity.get("projectId") or 0)
        metrics = entity.get("metrics") if isinstance(entity.get("metrics"), Mapping) else {}
        if not market_key or not project_id or not is_valid_market_activity(entity):
            continue
        score = score_traded_project(_traded_score_inputs(entity))
        grouped.setdefault(market_key, []).append(
            {
                "projectId": project_id,
                "name": str(entity.get("projectName") or entity.get("name") or ""),
                "symbol": str(entity.get("symbol") or ""),
                "score": score["score"],
                "confidence": score["confidence"],
                "scoreBreakdown": score["components"],
                "metrics": dict(metrics),
            }
        )

    result: dict[str, dict[str, Any]] = {}
    with store.refresh_transaction() as refresh:
        for market_key, rows in grouped.items():
            ranked = rank_market_projects(rows)
            previous = store.latest_complete_snapshot(chain_id, market_key)
            previous_leader = (previous or {}).get("rows", [{}])[0] if (previous or {}).get("rows") else {}
            for index, row in enumerate(ranked, start=1):
                row["rank"] = index
                refresh.save_ranking_snapshot(
                    chain_id,
                    market_key,
                    row["projectId"],
                    observed_at=int(observed_at),
                    rank=index,
                    score=float(row["score"]),
                    confidence=float(row["confidence"]),
                    metrics={"market": row["metrics"], "scoreBreakdown": row["scoreBreakdown"]},
                )
            leader = ranked[0] if ranked else {}
            result[market_key] = {
                "leader": leader,
                "leaderStreak": 2 if leader and int(previous_leader.get("projectId") or 0) == int(leader.get("projectId") or 0) else 1,
                "top": ranked,
                "observedAt": int(observed_at),
            }
    return result


def discover_chain_ecosystem(
    chain: Mapping[str, Any],
    store: ChainEcosystemStore,
    providers: Mapping[str, Any],
) -> dict[str, Any]:
    observed_at = _now_ms()
    chain_id = int(chain.get("id") or 0)
    if not chain_id:
        raise ValueError("chain id is required")
    provider_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = True
    for provider_name, fetcher in providers.items():
        result = safe_provider_fetch(store, chain_id, provider_name, fetcher)
        if result["stale"]:
            complete = False
            warnings.append(result["warning"])
            continue
        payload = result["rows"]
        if isinstance(payload, list) and (not payload or isinstance(payload[0], Mapping) and payload[0].get("provider")):
            normalized = payload
        else:
            normalized = normalize_provider_rows(provider_name, payload, chain, observed_at=observed_at)
        provider_rows.extend(dict(row) for row in normalized)

    entities = merge_provider_entities(provider_rows)
    ranking_candidates: list[dict[str, Any]] = []
    chain_evidence: list[dict[str, Any]] = []
    for entity in entities:
        name = str(entity.get("projectName") or entity.get("symbol") or "").strip()
        if not name:
            continue
        project = store.upsert_project(
            chain_id,
            {
                "slug": entity.get("projectSlug") or name,
                "name": name,
                "tokenStage": "potential",
                "officialUrl": entity.get("officialUrl"),
                "githubRepo": entity.get("githubRepo"),
                "description": entity.get("description"),
            },
        )
        evidence_rows: list[dict[str, Any]] = []
        for evidence in entity.get("evidence") or []:
            if not isinstance(evidence, Mapping):
                continue
            enriched = {**dict(evidence), "metrics": dict(entity.get("metrics") or {})}
            stored = store.add_evidence(
                chain_id,
                "project",
                project["id"],
                {**enriched, "payload": {"metrics": enriched["metrics"]}},
            )
            evidence_rows.append(enriched)
            if enriched.get("evidenceType") in {"public_mainnet", "official_mainnet_announcement"}:
                chain_evidence.append(enriched)
        token_stage = resolve_project_token_stage(project, evidence_rows)
        project = store.upsert_project(chain_id, {**project, "tokenStage": token_stage})
        classification_evidence: list[dict[str, Any]] = []
        for inferred in infer_market_classifications(entity):
            classification_row = {
                **_base_evidence(
                    "classifier",
                    "market_classification",
                    observed_at,
                    confidence=float(inferred["confidence"]),
                    title=str(inferred["reason"]),
                ),
                "marketKey": inferred["marketKey"],
                "metrics": dict(entity.get("metrics") or {}),
            }
            store.add_evidence(
                chain_id,
                "project",
                project["id"],
                {
                    **classification_row,
                    "payload": {
                        "marketKey": inferred["marketKey"],
                        "reason": inferred["reason"],
                        "metrics": classification_row["metrics"],
                    },
                },
            )
            classification_evidence.append(classification_row)
        classifications = classify_project_markets(
            project,
            [
                *[{**row, "marketKey": entity.get("marketKey")} for row in evidence_rows],
                *classification_evidence,
            ],
        )
        confirmed_market_keys: list[str] = []
        for classification in classifications:
            if classification["reviewStatus"] == "confirmed" and classification["marketKey"]:
                try:
                    store.link_project_market(
                        project["id"],
                        classification["marketKey"],
                        confidence=float(classification["confidence"]),
                        source=",".join(classification["sources"]),
                    )
                    confirmed_market_keys.append(str(classification["marketKey"]))
                except ValueError:
                    warnings.append(f"未知市场 {classification['marketKey']} 已进入待校验")
        contract_address = entity.get("contractAddress")
        if contract_address:
            store.upsert_asset(
                chain_id,
                project["id"],
                {
                    "contractAddress": contract_address,
                    "symbol": entity.get("symbol"),
                    "name": name,
                    "poolAddress": entity.get("poolAddress"),
                    "status": token_stage,
                    "firstTradeAt": observed_at if token_stage == "trading" else 0,
                },
            )
        if token_stage == "trading":
            for market_key in sorted(set(confirmed_market_keys)):
                ranking_candidates.append({**entity, "projectId": project["id"], "marketKey": market_key})

    if complete:
        rankings = build_ranking_snapshot(
            chain_id,
            observed_at,
            store=store,
            candidates=ranking_candidates,
        )
        next_stage = resolve_chain_stage(str(chain.get("stage") or "early_watch"), chain_evidence)
        if next_stage != chain.get("stage"):
            chain = store.update_chain_stage(chain_id, next_stage, observed_at=observed_at)
    else:
        rankings = {}
    return {
        "complete": complete,
        "observedAt": observed_at,
        "chain": dict(chain),
        "entities": entities,
        "rankings": rankings,
        "warnings": sorted(set(warnings)),
    }


_DEFAULT_MONITOR: Any = None


def configure_chain_ecosystem_monitor(monitor: Any) -> None:
    global _DEFAULT_MONITOR
    _DEFAULT_MONITOR = monitor


def refresh_chain_ecosystem(chain_id: int | str, force: bool = False) -> dict[str, Any]:
    if _DEFAULT_MONITOR is None:
        raise RuntimeError("chain ecosystem monitor is not configured")
    return _DEFAULT_MONITOR.refresh(chain_id, force=force)


def chain_ecosystem_payload(chain_id: int | str | None = None) -> dict[str, Any]:
    if _DEFAULT_MONITOR is None:
        raise RuntimeError("chain ecosystem monitor is not configured")
    return _DEFAULT_MONITOR.payload(chain_id)


def safe_monitor_error(error: Exception | str) -> str:
    text = str(error or "chain ecosystem operation failed")
    text = re.sub(r"(?i)\b(?:token|secret|api[_-]?key|authorization)\s*[=:]\s*[^\s,;]+", "credential=[redacted]", text)
    text = re.sub(r"(?i)\b[A-Z]:\\[^\r\n]+", "[local path]", text)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    return re.sub(r"\s+", " ", text).strip()[:240]


def _https_url(value: Any, *, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    if not text.startswith("https://"):
        raise ValueError("URL must use HTTPS")
    return text[:800]


class ChainEcosystemMonitor:
    def __init__(
        self,
        store: ChainEcosystemStore,
        *,
        provider_factory=None,
        submitter=None,
        alert_sink=None,
        stale_after_ms: int = 10 * 60 * 1000,
        refresh_interval_seconds: int | None = None,
    ):
        self.store = store
        self.provider_factory = provider_factory or self._default_provider_factory
        self.submitter = submitter or self._submit_thread
        self.alert_sink = alert_sink
        self.research_candidate_sink = None
        self.stale_after_ms = max(60_000, int(stale_after_ms))
        configured_interval = refresh_interval_seconds
        if configured_interval is None:
            configured_interval = int(_safe_float(os.environ.get("CHAIN_ECOSYSTEM_REFRESH_SECONDS")) or 300)
        self.refresh_interval_ms = max(60_000, int(configured_interval) * 1000)
        self.research_refresh_interval_ms = max(
            60_000,
            int(_safe_float(os.environ.get("ONCHAIN_RESEARCH_REFRESH_SECONDS")) or 60) * 1000,
        )
        self._refresh_lock = threading.Lock()
        self._refreshing: set[int] = set()
        self._next_due_at: dict[int, int] = {}
        self._research_refreshing = False
        self._research_network_inflight: set[str] = set()
        self._research_next_due_at = 0
        self._research_network_cursor = 0
        self._research_network_failure_streak: dict[str, int] = {}
        self._research_network_next_due_at: dict[str, int] = {}
        self._research_contract_inflight: set[str] = set()
        self._research_contract_seen_at: dict[str, int] = {}
        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None

    @staticmethod
    def _submit_thread(target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True, name="chain-ecosystem-refresh")
        thread.start()
        return thread

    def initialize(self) -> None:
        self.store.initialize()
        seed_robinhood_chain(self.store)
        configure_chain_ecosystem_monitor(self)

    def start(self) -> bool:
        """Start one idempotent scheduler for every enabled chain."""
        with self._refresh_lock:
            if self._loop_thread and self._loop_thread.is_alive():
                return False
            self._stop_event.clear()
            self._loop_thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="chain-ecosystem-monitor",
            )
            self._loop_thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_cycle()
            except Exception:
                pass
            self._stop_event.wait(2.0)

    def run_cycle(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Schedule due chains and apply bounded exponential backoff after provider failures."""
        now = int(now_ms if now_ms is not None else _now_ms())
        scheduled: list[int] = []
        for chain in self.store.list_chains():
            chain_id = int(chain["id"])
            if not chain.get("scanEnabled") or now < int(self._next_due_at.get(chain_id, 0)):
                continue
            health = self.store.list_source_health(chain_id)
            failure_streak = max((int(row.get("failureStreak") or 0) for row in health), default=0)
            backoff_multiplier = 2 ** min(4, failure_streak)
            self._next_due_at[chain_id] = now + self.refresh_interval_ms * backoff_multiplier
            if self.schedule_refresh(chain_id):
                scheduled.append(chain_id)
        research_scheduled = self.schedule_research_refresh(now_ms=now)
        return {"scheduled": scheduled, "researchScheduled": research_scheduled, "checkedAt": now}

    def _research_networks(self) -> list[str]:
        configured = str(os.environ.get("ONCHAIN_RESEARCH_NETWORKS") or "").strip()
        values = re.split(r"[,;\s]+", configured) if configured else list(ONCHAIN_RESEARCH_DEFAULT_NETWORKS)
        return list(dict.fromkeys(_chain_key(value) for value in values if _chain_key(value)))

    def _research_refresh_worker(self, network: str = "") -> None:
        networks = self._research_networks()
        if not network and networks:
            with self._refresh_lock:
                now = _now_ms()
                for offset in range(len(networks)):
                    index = (self._research_network_cursor + offset) % len(networks)
                    candidate = networks[index]
                    if now >= int(self._research_network_next_due_at.get(candidate, 0)):
                        network = candidate
                        self._research_network_cursor = (index + 1) % len(networks)
                        break
        try:
            if not network:
                return
            result = scan_onchain_research(self.store, networks=[network], candidate_sink=self.research_candidate_sink)
            with self._refresh_lock:
                if result.get("ok"):
                    self._research_network_failure_streak[network] = 0
                    self._research_network_next_due_at[network] = _now_ms() + self.research_refresh_interval_ms
                else:
                    streak = min(5, int(self._research_network_failure_streak.get(network, 0)) + 1)
                    self._research_network_failure_streak[network] = streak
                    self._research_network_next_due_at[network] = (
                        _now_ms() + self.research_refresh_interval_ms * (2 ** streak)
                    )
        finally:
            with self._refresh_lock:
                self._research_network_inflight.discard(network)
                self._research_refreshing = bool(self._research_network_inflight)

    def schedule_research_refresh(self, *, now_ms: int | None = None) -> bool:
        now = int(now_ms if now_ms is not None else _now_ms())
        scheduled = []
        with self._refresh_lock:
            for network in self._research_networks():
                if network in self._research_network_inflight or now < self._research_network_next_due_at.get(network, 0):
                    continue
                self._research_network_inflight.add(network)
                self._research_network_next_due_at[network] = now + self.research_refresh_interval_ms
                scheduled.append(network)
            self._research_refreshing = bool(self._research_network_inflight)
        for network in scheduled:
            try:
                self.submitter(self._research_refresh_worker, network)
            except Exception:
                with self._refresh_lock:
                    self._research_network_inflight.discard(network)
                    self._research_refreshing = bool(self._research_network_inflight)
                raise
        return bool(scheduled)

    def _research_contract_worker(
        self,
        contract_address: str,
        source: str,
        source_text: str,
        observed_at: int,
    ) -> None:
        identity = _onchain_address(contract_address)
        try:
            scan_onchain_research_contract(
                self.store,
                contract_address,
                source=source,
                source_text=source_text,
                observed_at=observed_at,
                candidate_sink=self.research_candidate_sink,
            )
        finally:
            with self._refresh_lock:
                self._research_contract_inflight.discard(identity)

    def schedule_contract_research(
        self,
        contract_address: Any,
        *,
        source: str = "external",
        source_text: str = "",
        observed_at: int | None = None,
    ) -> bool:
        """Debounce repeated mentions while allowing a new contract to scan immediately."""
        contract = _onchain_address(contract_address)
        if not contract:
            return False
        identity = contract
        now = _now_ms()
        with self._refresh_lock:
            last_seen = int(self._research_contract_seen_at.get(identity, 0))
            if identity in self._research_contract_inflight or now - last_seen < 5 * 60_000:
                return False
            self._research_contract_inflight.add(identity)
            self._research_contract_seen_at[identity] = now
            if len(self._research_contract_seen_at) > 2000:
                cutoff = now - 24 * 60 * 60_000
                self._research_contract_seen_at = {
                    key: value for key, value in self._research_contract_seen_at.items() if value >= cutoff
                }
        try:
            self.submitter(
                self._research_contract_worker,
                contract,
                str(source or "external")[:80],
                str(source_text or "")[:500],
                int(observed_at or now),
            )
        except Exception:
            with self._refresh_lock:
                self._research_contract_inflight.discard(identity)
            raise
        return True

    def _default_provider_factory(self, chain: Mapping[str, Any]) -> dict[str, Any]:
        providers: dict[str, Any] = {"defillama": lambda: fetch_defillama_protocols()}
        network = str(chain.get("geckoterminalNetwork") or "").strip()
        if network:
            providers["geckoterminal"] = lambda: fetch_geckoterminal_pools(network, pages=3)
        if network == "robinhood" or str(chain.get("slug") or "") == "robinhood-chain":
            providers["opensea"] = lambda: fetch_opensea_collections("robinhood", limit=100)
        explorer = str(chain.get("explorerUrl") or "").strip()
        if explorer:
            providers["blockscout"] = lambda: fetch_blockscout_chain(explorer)
        assets = self.store.list_assets(int(chain["id"]))
        addresses = [row["contractAddress"] for row in assets if row.get("contractAddress")]
        provider_chain = str(chain.get("geckoterminalNetwork") or chain.get("slug") or "")
        if provider_chain and addresses:
            providers["dexscreener"] = lambda: fetch_dexscreener_assets(provider_chain, addresses)
        projects = [row for row in self.store.list_projects(int(chain["id"])) if row.get("githubRepo")]
        if projects:
            def fetch_github_rows():
                rows: list[dict[str, Any]] = []
                for project in projects[:20]:
                    payload = fetch_github_repository(project["githubRepo"])
                    rows.extend(normalize_provider_rows("github", payload, chain))
                return rows

            providers["github"] = fetch_github_rows
        return providers

    def _resolve_chain(self, identifier: int | str | None) -> dict[str, Any] | None:
        if identifier is not None and str(identifier).strip():
            return self.store.get_chain(identifier)
        chains = self.store.list_chains()
        return chains[0] if chains else None

    def payload(
        self,
        chain_id: int | str | None = None,
        *,
        research_day: str | None = None,
        profile_reads: bool = False,
    ) -> dict[str, Any]:
        def load(label: str, callback):
            started = time.perf_counter()
            result = callback()
            if profile_reads:
                print(
                    f"Chain ecosystem payload read: {label}={time.perf_counter() - started:.3f}s",
                    flush=True,
                )
            return result

        chains = load("chains", self.store.list_chains)
        selected = load("selected", lambda: self._resolve_chain(chain_id))
        if not selected:
            return {
                "ok": True,
                "chains": [],
                "selectedChain": None,
                "markets": [],
                "projects": [],
                "potentialProjects": [],
                "alerts": [],
                "sourceHealth": [],
                "warnings": ["还没有添加公链"],
                "updatedAt": 0,
                "stale": False,
                "refreshing": False,
                "dailyResearch": self.store.onchain_research_payload(research_day=research_day),
            }
        selected_id = int(selected["id"])
        projects = load("projects", lambda: self.store.list_projects(selected_id))
        assets = load("assets", lambda: self.store.list_assets(selected_id))
        relations_by_project = load("relations", lambda: self.store.list_project_markets_for_chain(selected_id))
        evidence_by_project = load(
            "project-evidence",
            lambda: self.store.list_project_evidence_for_chain(selected_id, limit_per_project=40),
        )
        assets_by_project: dict[int, list[dict[str, Any]]] = {}
        for asset in assets:
            if asset.get("projectId"):
                assets_by_project.setdefault(int(asset["projectId"]), []).append(asset)
        project_rows: list[dict[str, Any]] = []
        for project in projects:
            relations = relations_by_project.get(int(project["id"]), [])
            evidence = evidence_by_project.get(int(project["id"]), [])
            development = None
            for row in evidence:
                payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
                metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else payload
                if metrics.get("development") is not None:
                    development = metrics.get("development")
                    break
            progress_by_stage = {"potential": 20, "announced": 55, "contract_confirmed": 80, "trading": 100}
            potential_score = score_potential_project(
                {
                    "officialProgress": progress_by_stage.get(project["tokenStage"], 0),
                    "ecosystemRole": min(100, 30 + len(relations) * 25) if relations else None,
                    "development": development,
                }
            )
            project_rows.append(
                {
                    **project,
                    "markets": relations,
                    "assets": assets_by_project.get(project["id"], []),
                    "evidence": evidence[:8],
                    "evidenceCount": len(evidence),
                    "potentialScore": potential_score,
                }
            )
        projects_by_id = {row["id"]: row for row in project_rows}
        market_candidates: dict[str, list[dict[str, Any]]] = {}
        stage_priority = {"trading": 4, "contract_confirmed": 3, "announced": 2, "potential": 1}
        for project in project_rows:
            project_assets = project.get("assets") if isinstance(project.get("assets"), list) else []
            symbol = str((project_assets[0] if project_assets else {}).get("symbol") or "")
            for relation in project.get("markets") or []:
                if not isinstance(relation, Mapping) or relation.get("reviewStatus") != "confirmed":
                    continue
                market_key = str(relation.get("marketKey") or "")
                if not market_key:
                    continue
                market_candidates.setdefault(market_key, []).append(
                    {
                        "projectId": project["id"],
                        "name": project["name"],
                        "symbol": symbol,
                        "tokenStage": project["tokenStage"],
                        "officialUrl": project.get("officialUrl", ""),
                        "potentialScore": project["potentialScore"],
                        "evidenceCount": int(project.get("evidenceCount") or 0),
                        "confidence": _clean_number(float(_bounded_score(relation.get("confidence")) or 0)),
                        "source": relation.get("source", ""),
                    }
                )
        for rows in market_candidates.values():
            rows.sort(
                key=lambda row: (
                    -stage_priority.get(str(row.get("tokenStage")), 0),
                    -float((row.get("potentialScore") or {}).get("score") or 0),
                    -float(row.get("confidence") or 0),
                    str(row.get("name") or "").lower(),
                )
            )
            del rows[5:]
        gas_symbol = str(selected.get("gasSymbol") or "").strip().upper()
        if gas_symbol:
            market_candidates.setdefault("chain_token", []).insert(
                0,
                {
                    "projectId": 0,
                    "name": f"{gas_symbol} Gas Token",
                    "symbol": gas_symbol,
                    "tokenStage": "trading",
                    "officialUrl": selected.get("docsUrl") or selected.get("officialUrl") or "",
                    "potentialScore": {"score": 100, "confidence": 100},
                    "evidenceCount": 1,
                    "confidence": 100,
                    "source": "official_chain_config",
                },
            )
        latest_rankings = load("latest-rankings", lambda: self.store.latest_rankings(selected_id))
        recent_leaders = load(
            "recent-leaders",
            lambda: self.store.recent_market_leaders_for_chain(selected_id, limit_per_market=8),
        )
        market_definitions = load("markets", lambda: self.store.list_markets(selected_id))
        market_rows: list[dict[str, Any]] = []
        for market in market_definitions:
            snapshot = latest_rankings.get(market["key"])
            top: list[dict[str, Any]] = []
            for ranking in (snapshot or {}).get("rows", []):
                project = projects_by_id.get(ranking["projectId"], {})
                metrics_payload = ranking.get("metrics") if isinstance(ranking.get("metrics"), Mapping) else {}
                top.append(
                    {
                        **ranking,
                        "symbol": ((project.get("assets") or [{}])[0].get("symbol") if project.get("assets") else ""),
                        "officialUrl": project.get("officialUrl", ""),
                        "marketMetrics": metrics_payload.get("market", metrics_payload),
                        "scoreBreakdown": metrics_payload.get("scoreBreakdown", {}),
                    }
                )
            leaders = recent_leaders.get(market["key"], []) if top else []
            leader_streak = 0
            if leaders:
                leader_id = int(leaders[0].get("projectId") or 0)
                for leader in leaders:
                    if int(leader.get("projectId") or 0) != leader_id:
                        break
                    leader_streak += 1
            market_rows.append(
                {
                    **market,
                    "top": top,
                    "candidates": market_candidates.get(market["key"], []),
                    "leaderStreak": leader_streak,
                    "observedAt": int((snapshot or {}).get("observedAt") or 0),
                }
            )
        source_health = load("source-health", lambda: self.store.list_source_health(selected_id))
        warnings = [f"{row['provider']} 数据延迟：{row['lastError']}" for row in source_health if row["status"] != "ok"]
        if not source_health:
            warnings.append("等待首次自动扫描")
        evidence = load(
            "chain-evidence",
            lambda: self.store.list_evidence(selected_id, subject_type="chain", subject_id=selected_id, limit=20),
        )
        updated_candidates = [int(selected.get("updatedAt") or 0)]
        updated_candidates.extend(int(row.get("lastCheckedAt") or 0) for row in source_health)
        updated_candidates.extend(int(row.get("observedAt") or 0) for row in evidence)
        updated_candidates.extend(int(row.get("observedAt") or 0) for row in market_rows)
        updated_at = max(updated_candidates or [0])
        stale = bool(source_health) and (
            any(row["status"] != "ok" for row in source_health)
            or (_now_ms() - max((row["lastSuccessAt"] for row in source_health), default=0) > self.stale_after_ms)
        )
        chain_counts = load("chain-counts", self.store.chain_summary_counts)
        chain_summaries = []
        for chain in chains:
            counts = chain_counts.get(int(chain["id"]), {})
            market_count = int(counts.get("marketCount") or 0)
            if chain.get("gasSymbol") and not counts.get("hasChainToken"):
                market_count += 1
            chain_summaries.append(
                {
                    **chain,
                    "projectCount": int(counts.get("projectCount") or 0),
                    "marketCount": market_count,
                }
            )
        with self._refresh_lock:
            refreshing = selected_id in self._refreshing
            research_refreshing = self._research_refreshing
        all_potential_projects = sorted(
            (row for row in project_rows if row["tokenStage"] != "trading"),
            key=lambda row: (-float(row["potentialScore"]["score"]), row["name"]),
        )
        visible_limit = max(
            20,
            min(200, int(_safe_float(os.environ.get("CHAIN_ECOSYSTEM_VISIBLE_PROJECTS")) or 80)),
        )
        visible_potential_projects = all_potential_projects[:visible_limit]
        visible_project_ids = {int(row["id"]) for row in visible_potential_projects}
        for market in market_rows:
            for group in (market.get("top") or [], market.get("candidates") or []):
                for row in group:
                    project_id = int(_safe_float(row.get("projectId")) or 0)
                    if project_id:
                        visible_project_ids.add(project_id)
        visible_projects = [row for row in project_rows if int(row["id"]) in visible_project_ids]
        return {
            "ok": True,
            "chains": chain_summaries,
            "selectedChain": {**selected, "evidence": evidence},
            "markets": market_rows,
            "projects": visible_projects,
            "projectCount": len(project_rows),
            "potentialProjects": visible_potential_projects,
            "potentialProjectCount": len(all_potential_projects),
            "alerts": load("alerts", lambda: self.store.list_alerts(selected_id)),
            "sourceHealth": source_health,
            "warnings": warnings,
            "updatedAt": updated_at,
            "stale": stale,
            "refreshing": refreshing,
            "dailyResearch": {
                **load("daily-research", lambda: self.store.onchain_research_payload(research_day=research_day)),
                "refreshing": research_refreshing,
            },
        }

    @staticmethod
    def _state_from_payload(payload: Mapping[str, Any], *, complete: bool) -> dict[str, Any]:
        selected = payload.get("selectedChain") if isinstance(payload.get("selectedChain"), Mapping) else {}
        markets: dict[str, dict[str, Any]] = {}
        project_metrics: dict[str, dict[str, Any]] = {}
        for market in payload.get("markets") or []:
            if not isinstance(market, Mapping) or not (market.get("top") or market.get("candidates")):
                continue
            top = market.get("top") if isinstance(market.get("top"), list) else []
            markets[str(market.get("key"))] = {
                "leader": top[0] if top else {},
                "leaderStreak": int(market.get("leaderStreak") or 1),
            }
            for ranking in top:
                if not isinstance(ranking, Mapping) or not ranking.get("projectId"):
                    continue
                project_key = str(ranking["projectId"])
                target = project_metrics.setdefault(project_key, {})
                values = ranking.get("marketMetrics") if isinstance(ranking.get("marketMetrics"), Mapping) else {}
                for key, value in values.items():
                    parsed = _safe_float(value)
                    if parsed is not None and (key not in target or parsed > float(target[key])):
                        target[key] = parsed
        projects: dict[str, dict[str, Any]] = {}
        for project in payload.get("projects") or []:
            if not isinstance(project, Mapping):
                continue
            project_key = str(project.get("id"))
            projects[project_key] = {**dict(project), "metrics": project_metrics.get(project_key, {})}
        return {
            "complete": complete,
            "observedAt": int(payload.get("updatedAt") or _now_ms()),
            "chain": {"id": selected.get("id"), "stage": selected.get("stage")},
            "markets": markets,
            "projects": projects,
        }

    def _refresh_worker(self, chain_id: int) -> None:
        try:
            chain = self.store.get_chain(chain_id)
            if not chain:
                return
            before_payload = self.payload(chain_id)
            scan_state = self.store.get_scan_state(chain_id)
            result = discover_chain_ecosystem(chain, self.store, self.provider_factory(chain))
            after_payload = self.payload(chain_id)
            previous_state = self._state_from_payload(before_payload, complete=True)
            current_state = self._state_from_payload(after_payload, complete=bool(result.get("complete")))
            current_state["observedAt"] = int(result.get("observedAt") or current_state["observedAt"])
            if result.get("complete"):
                self._apply_leader_history(chain_id, previous_state, current_state)
                if scan_state.get("baselineReady"):
                    for alert in detect_high_value_alerts(previous_state, current_state):
                        self.store.upsert_alert(alert)
                else:
                    for alert in self.store.list_pending_alerts(chain_id):
                        self.store.mark_alert_delivered(alert["id"])
                self.store.mark_scan_complete(chain_id, completed_at=current_state["observedAt"])
                self.deliver_pending_alerts(chain_id)
        finally:
            with self._refresh_lock:
                self._refreshing.discard(int(chain_id))

    def _apply_leader_history(
        self,
        chain_id: int,
        previous_state: dict[str, Any],
        current_state: dict[str, Any],
    ) -> None:
        """Confirm a new Top1 for two scans while retaining the prior incumbent for comparison."""
        current_markets = current_state.get("markets") if isinstance(current_state.get("markets"), dict) else {}
        previous_markets = previous_state.get("markets") if isinstance(previous_state.get("markets"), dict) else {}
        for market_key, current_market in current_markets.items():
            leaders = self.store.recent_market_leaders(chain_id, market_key, limit=8)
            if not leaders:
                continue
            current_id = int(leaders[0].get("projectId") or 0)
            streak = 0
            for leader in leaders:
                if int(leader.get("projectId") or 0) != current_id:
                    break
                streak += 1
            current_market["leaderStreak"] = streak
            if streak >= 2 and len(leaders) > streak:
                previous_market = previous_markets.setdefault(market_key, {})
                previous_market["leader"] = leaders[streak]

    def deliver_pending_alerts(self, chain_id: int) -> int:
        if not self.alert_sink:
            return 0
        delivered = 0
        for alert in self.store.list_pending_alerts(chain_id):
            if alert.get("eventType") not in HIGH_VALUE_ALERT_TYPES:
                continue
            try:
                result = self.alert_sink(alert)
            except Exception:
                continue
            accepted = result is not False
            if isinstance(result, Mapping):
                accepted = bool(result.get("ok"))
            if accepted:
                self.store.mark_alert_delivered(alert["id"])
                delivered += 1
        return delivered

    def schedule_refresh(self, chain_id: int) -> bool:
        with self._refresh_lock:
            if int(chain_id) in self._refreshing:
                return False
            self._refreshing.add(int(chain_id))
        try:
            self.submitter(self._refresh_worker, int(chain_id))
        except Exception:
            with self._refresh_lock:
                self._refreshing.discard(int(chain_id))
            raise
        return True

    def refresh(
        self,
        chain_id: int | str,
        *,
        force: bool = False,
        research_day: str | None = None,
    ) -> dict[str, Any]:
        chain = self._resolve_chain(chain_id)
        if not chain:
            raise ValueError("chain not found")
        if force:
            self.schedule_refresh(int(chain["id"]))
        payload = self.payload(chain["id"], research_day=research_day)
        payload["refreshScheduled"] = bool(force)
        return payload

    def apply_action(self, payload: Mapping[str, Any], *, actor_id: int = 0) -> dict[str, Any]:
        if not actor_id:
            raise PermissionError("authentication required")
        action = str(payload.get("action") or "").strip().lower()
        if action == "add_chain":
            name = str(payload.get("name") or "").strip()[:160]
            if not name:
                raise ValueError("chain name is required")
            network_chain_id = str(payload.get("chainId") or "").strip()
            if network_chain_id and (not network_chain_id.isdigit() or len(network_chain_id) > 24):
                raise ValueError("invalid chain id")
            chain = self.store.upsert_chain(
                {
                    "slug": payload.get("slug") or name,
                    "name": name,
                    "stage": payload.get("stage") or "early_watch",
                    "chainType": payload.get("chainType"),
                    "chainId": network_chain_id,
                    "gasSymbol": payload.get("gasSymbol"),
                    "officialUrl": _https_url(payload.get("officialUrl")),
                    "docsUrl": _https_url(payload.get("docsUrl")),
                    "rpcUrl": _https_url(payload.get("rpcUrl")),
                    "explorerUrl": _https_url(payload.get("explorerUrl")),
                    "geckoterminalNetwork": payload.get("geckoterminalNetwork"),
                }
            )
            self.store.add_manual_audit(chain["id"], action, "chain", chain["id"], dict(payload), actor_id=actor_id)
            # Persist first and answer immediately. Building the complete payload
            # can contend with a long-running discovery scan, so it must never
            # hold the user's submit request open.
            refresh_scheduled = self.schedule_refresh(int(chain["id"]))
            return {
                "ok": True,
                "chain": chain,
                "refreshScheduled": refresh_scheduled,
            }
        chain_id = int(payload.get("chainId") or 0)
        chain = self.store.get_chain(chain_id)
        if not chain:
            raise ValueError("chain not found")
        if action == "add_project":
            name = str(payload.get("name") or "").strip()[:160]
            if not name:
                raise ValueError("project name is required")
            project = self.store.upsert_project(
                chain_id,
                {
                    "slug": payload.get("slug") or name,
                    "name": name,
                    "tokenStage": payload.get("tokenStage") or "potential",
                    "officialUrl": _https_url(payload.get("officialUrl")),
                    "githubRepo": payload.get("githubRepo"),
                    "description": payload.get("description"),
                    "manual": True,
                },
            )
            market_key = str(payload.get("marketKey") or "")
            if market_key:
                self.store.link_project_market(project["id"], market_key, confidence=100, source="manual")
            self.store.add_manual_audit(chain_id, action, "project", project["id"], dict(payload), actor_id=actor_id)
            return {"ok": True, "project": project, "payload": self.payload(chain_id)}
        if action == "add_evidence":
            subject_type = str(payload.get("subjectType") or "").strip()
            subject_id = payload.get("subjectId")
            if subject_type not in {"chain", "project", "asset", "market"} or subject_id is None:
                raise ValueError("invalid evidence subject")
            evidence = self.store.add_evidence(
                chain_id,
                subject_type,
                subject_id,
                {
                    "source": payload.get("source") or "manual",
                    "evidenceType": payload.get("evidenceType"),
                    "url": _https_url(payload.get("url")),
                    "title": payload.get("title"),
                    "summary": payload.get("summary"),
                    "confidence": payload.get("confidence") or 100,
                    "payload": payload.get("details") or {},
                },
            )
            self.store.add_manual_audit(chain_id, action, subject_type, subject_id, dict(payload), actor_id=actor_id)
            return {"ok": True, "evidence": evidence, "payload": self.payload(chain_id)}
        if action == "confirm_market":
            market_key = str(payload.get("marketKey") or "")
            current = next((row for row in self.store.list_markets(chain_id) if row["key"] == market_key), None)
            if not current:
                raise ValueError("market not found")
            market = self.store.upsert_dynamic_market(
                chain_id,
                market_key,
                current["name"],
                level=current["level"],
                description=current["description"],
                review_status="confirmed",
            )
            self.store.add_manual_audit(chain_id, action, "market", market["id"], dict(payload), actor_id=actor_id)
            return {"ok": True, "market": market, "payload": self.payload(chain_id)}
        if action == "correct_relation":
            relation = self.store.link_project_market(
                int(payload.get("projectId") or 0),
                str(payload.get("marketKey") or ""),
                confidence=100,
                source="manual",
            )
            self.store.add_manual_audit(chain_id, action, "project", payload.get("projectId"), dict(payload), actor_id=actor_id)
            return {"ok": True, "relation": relation, "payload": self.payload(chain_id)}
        if action == "ack_alert":
            alert = self.store.acknowledge_alert(int(payload.get("alertId") or 0))
            self.store.add_manual_audit(chain_id, action, "alert", alert["id"], {}, actor_id=actor_id)
            return {"ok": True, "alert": alert, "payload": self.payload(chain_id)}
        if action == "refresh":
            return self.refresh(chain_id, force=True)
        raise ValueError("unsupported action")
