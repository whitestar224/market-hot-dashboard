from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests


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

PROVIDER_HEADERS = {
    "User-Agent": "XingyunShe-Chain-Ecosystem/1.0",
    "Accept": "application/json",
}

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


def _chain_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return "-".join(part for part in text.replace(" ", "-").split("-") if part)


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
        address = _address(raw_address)
        if not address or address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    if not addresses:
        return {"pairs": []}
    payload = _get_json(
        f"https://api.dexscreener.com/tokens/v1/{chain}/{','.join(addresses)}",
        session=session,
    )
    rows = payload.get("pairs") if isinstance(payload, Mapping) else payload
    pairs = [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
    return {"pairs": pairs}


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

                    CREATE INDEX IF NOT EXISTS idx_chain_projects_stage ON projects(chain_id, token_stage);
                    CREATE INDEX IF NOT EXISTS idx_chain_evidence_subject ON evidence(chain_id, subject_type, subject_id, observed_at);
                    CREATE INDEX IF NOT EXISTS idx_chain_rank_latest ON ranking_snapshots(chain_id, market_id, observed_at, rank);
                    CREATE INDEX IF NOT EXISTS idx_chain_alert_time ON alert_events(chain_id, observed_at DESC);
                    """
                )
                conn.commit()
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
            return [
                {
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
                for row in rows
            ]
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
        result: dict[str, dict[str, Any]] = {}
        for market in self.list_markets(chain_id):
            snapshot = self.latest_complete_snapshot(chain_id, market["key"])
            if snapshot:
                result[market["key"]] = snapshot
        return result

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
        self.stale_after_ms = max(60_000, int(stale_after_ms))
        configured_interval = refresh_interval_seconds
        if configured_interval is None:
            configured_interval = int(_safe_float(os.environ.get("CHAIN_ECOSYSTEM_REFRESH_SECONDS")) or 300)
        self.refresh_interval_ms = max(60_000, int(configured_interval) * 1000)
        self._refresh_lock = threading.Lock()
        self._refreshing: set[int] = set()
        self._next_due_at: dict[int, int] = {}
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
            wait_seconds = max(5.0, min(30.0, self.refresh_interval_ms / 4000.0))
            self._stop_event.wait(wait_seconds)

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
        return {"scheduled": scheduled, "checkedAt": now}

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

    def payload(self, chain_id: int | str | None = None) -> dict[str, Any]:
        chains = self.store.list_chains()
        selected = self._resolve_chain(chain_id)
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
            }
        selected_id = int(selected["id"])
        projects = self.store.list_projects(selected_id)
        assets = self.store.list_assets(selected_id)
        assets_by_project: dict[int, list[dict[str, Any]]] = {}
        for asset in assets:
            if asset.get("projectId"):
                assets_by_project.setdefault(int(asset["projectId"]), []).append(asset)
        project_rows: list[dict[str, Any]] = []
        for project in projects:
            relations = self.store.list_project_markets(project["id"])
            evidence = self.store.list_evidence(
                selected_id,
                subject_type="project",
                subject_id=project["id"],
                limit=40,
            )
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
        latest_rankings = self.store.latest_rankings(selected_id)
        market_rows: list[dict[str, Any]] = []
        for market in self.store.list_markets(selected_id):
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
            leaders = self.store.recent_market_leaders(selected_id, market["key"], limit=8) if top else []
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
        source_health = self.store.list_source_health(selected_id)
        warnings = [f"{row['provider']} 数据延迟：{row['lastError']}" for row in source_health if row["status"] != "ok"]
        if not source_health:
            warnings.append("等待首次自动扫描")
        evidence = self.store.list_evidence(selected_id, subject_type="chain", subject_id=selected_id, limit=20)
        updated_candidates = [int(selected.get("updatedAt") or 0)]
        updated_candidates.extend(int(row.get("lastCheckedAt") or 0) for row in source_health)
        updated_candidates.extend(int(row.get("observedAt") or 0) for row in evidence)
        updated_candidates.extend(int(row.get("observedAt") or 0) for row in market_rows)
        updated_at = max(updated_candidates or [0])
        stale = bool(source_health) and (
            any(row["status"] != "ok" for row in source_health)
            or (_now_ms() - max((row["lastSuccessAt"] for row in source_health), default=0) > self.stale_after_ms)
        )
        chain_summaries = []
        for chain in chains:
            chain_projects = self.store.list_projects(chain["id"])
            discovered_markets = {
                relation["marketKey"]
                for project in chain_projects
                for relation in self.store.list_project_markets(project["id"])
                if relation.get("reviewStatus") == "confirmed"
            }
            if chain.get("gasSymbol"):
                discovered_markets.add("chain_token")
            chain_summaries.append(
                {
                    **chain,
                    "projectCount": len(chain_projects),
                    "marketCount": len(discovered_markets),
                }
            )
        with self._refresh_lock:
            refreshing = selected_id in self._refreshing
        return {
            "ok": True,
            "chains": chain_summaries,
            "selectedChain": {**selected, "evidence": evidence},
            "markets": market_rows,
            "projects": project_rows,
            "potentialProjects": sorted(
                (row for row in project_rows if row["tokenStage"] != "trading"),
                key=lambda row: (-float(row["potentialScore"]["score"]), row["name"]),
            ),
            "alerts": self.store.list_alerts(selected_id),
            "sourceHealth": source_health,
            "warnings": warnings,
            "updatedAt": updated_at,
            "stale": stale,
            "refreshing": refreshing,
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

    def refresh(self, chain_id: int | str, *, force: bool = False) -> dict[str, Any]:
        chain = self._resolve_chain(chain_id)
        if not chain:
            raise ValueError("chain not found")
        if force:
            self.schedule_refresh(int(chain["id"]))
        payload = self.payload(chain["id"])
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
