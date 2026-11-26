"""OpenNews (6551) REST client — 85+ English news sources, exchange listings,
Hyperliquid whales, derivatives anomalies, with AI impact ratings.

API: POST {base}/open/news_search with Bearer token (OPENNEWS_TOKEN).
Free (no-token) hot endpoint kept as an optional fallback source.

Feed contract (consumed by server.site_alert_feeds):
- opennews_feed_payload() -> serializable dict (raw grouped items)
- parse_opennews_events(payload) -> alert event dicts (stable keys, epoch ms)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

import requests

API_BASE = os.getenv("OPENNEWS_API_BASE", "https://ai.6551.io").rstrip("/")
SEARCH_PATH = "/open/news_search"
FREE_HOT_PATH = "/open/free_hot"

# Engine groups we consume. Keeping the groups separate lets each alert class
# carry its own kind/score threshold without extra round trips.
ENGINE_LISTING = "listing"    # 9 exchange listing announcement feeds
ENGINE_ONCHAIN = "onchain"    # Hyperliquid whale trades / large positions
ENGINE_MARKET = "market"      # price spikes, funding, liquidations, OI
ENGINE_NEWS = "news"          # Bloomberg/Reuters/CoinDesk/... editorial news

FETCH_GROUP_TTL_S = 60
BACKOFF_AFTER_FAILS = 3
BACKOFF_COOLDOWN_S = 600

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, Any]] = {}
_FAIL_STATE: dict[str, Any] = {"count": 0, "until": 0.0}


def opennews_enabled() -> bool:
    if (os.getenv("OPENNEWS_ENABLED", "1") or "").strip().lower() in {"0", "false", "no"}:
        return False
    return bool((os.getenv("OPENNEWS_TOKEN", "") or "").strip())


def _token() -> str:
    return (os.getenv("OPENNEWS_TOKEN", "") or "").strip()


def _backed_off() -> bool:
    with _CACHE_LOCK:
        return time.time() < float(_FAIL_STATE.get("until") or 0.0)


def _record_failure() -> None:
    with _CACHE_LOCK:
        count = int(_FAIL_STATE.get("count") or 0) + 1
        _FAIL_STATE["count"] = count
        if count >= BACKOFF_AFTER_FAILS:
            _FAIL_STATE["until"] = time.time() + BACKOFF_COOLDOWN_S
            _FAIL_STATE["count"] = 0


def _record_success() -> None:
    with _CACHE_LOCK:
        _FAIL_STATE["count"] = 0
        _FAIL_STATE["until"] = 0.0


def opennews_search(
    engine_types: list[str],
    *,
    coins: list[str] | None = None,
    score: int | None = None,
    q: str | None = None,
    has_coin: bool | None = None,
    limit: int = 50,
    page: int = 1,
    timeout: float = 12.0,
) -> list[dict[str, Any]]:
    """POST /open/news_search — one call covers every filter combination."""
    body: dict[str, Any] = {"limit": max(1, min(int(limit), 100)), "page": int(page)}
    if engine_types:
        body["engineTypes"] = engine_types
    if coins:
        body["coins"] = coins
    if score is not None:
        body["score"] = int(score)
    if q:
        body["q"] = str(q)[:120]
    if has_coin is not None:
        body["hasCoin"] = bool(has_coin)
    response = requests.post(
        f"{API_BASE}{SEARCH_PATH}",
        json=body,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "User-Agent": "xingyunshe-market-hot/1.0 (OpenNews read-only)",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("items") or rows.get("rows")
    return [row for row in (rows or []) if isinstance(row, dict)] if isinstance(rows, list) else []


def _cached_group(key: str, builder) -> Any:
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < FETCH_GROUP_TTL_S:
            return cached[1]
        if _backed_off():
            return cached[1] if cached else None
    try:
        value = builder()
    except Exception:
        _record_failure()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        return cached[1] if cached else None
    _record_success()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def _fetch_group(engine_types: list[str], **kwargs) -> list[dict[str, Any]]:
    return _cached_group(
        "search:" + ",".join(engine_types) + ":" + json.dumps(kwargs, sort_keys=True, ensure_ascii=False),
        lambda: opennews_search(engine_types, **kwargs),
    ) or []


def opennews_feed_payload() -> dict[str, Any]:
    """Grouped raw feed for the site-alert loop. Never raises."""
    now_ms = int(time.time() * 1000)
    payload: dict[str, Any] = {"fetchedAt": now_ms, "listing": [], "onchain": [], "market": [], "news": []}
    if not opennews_enabled():
        payload["disabled"] = True
        return payload
    payload["listing"] = _fetch_group([ENGINE_LISTING], limit=30)
    payload["onchain"] = _fetch_group([ENGINE_ONCHAIN], limit=30)
    payload["market"] = _fetch_group([ENGINE_MARKET], limit=30)
    payload["news"] = _fetch_group([ENGINE_NEWS], score=80, limit=30)
    return payload


# ---------------------------------------------------------------------------
# Event building (self-contained so server.py only imports two callables)
# ---------------------------------------------------------------------------

SOURCE_LABELS = {
    "Binance": "BN", "Coinbase": "CB", "OKX": "OK", "Bybit": "BY",
    "Hyperliquid": "HL", "Upbit": "UP", "Bithumb": "BT", "Robinhood": "RH",
    "Aster": "AS", "Bloomberg": "BLM", "Reuters": "RTR", "CoinDesk": "CD",
    "Cointelegraph": "CT", "The Block": "TB", "Decrypt": "DC", "BWEnews": "BW",
}


def _stable_key(prefix: str, *parts: Any) -> str:
    body = "|".join(
        re.sub(r"\s+", " ", str(part or "").strip().lower())[:180]
        for part in parts
        if part is not None and str(part or "").strip()
    )
    return f"{prefix}:{body}"


def _ts_ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    if re.fullmatch(r"\d{13}", text):
        return int(text)
    if re.fullmatch(r"\d{10}", text):
        return int(text) * 1000
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return 0


def _coins_text(item: dict[str, Any]) -> str:
    coins = item.get("coins") if isinstance(item.get("coins"), list) else []
    symbols = []
    for coin in coins:
        if isinstance(coin, dict):
            symbol = str(coin.get("symbol") or "").strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return "/".join(symbols[:4])


def _rating(item: dict[str, Any]) -> dict[str, Any]:
    rating = item.get("aiRating")
    return rating if isinstance(rating, dict) else {}


def _base_event(item: dict[str, Any], *, kind: str, source_label: str, title: str, body: str) -> dict[str, Any] | None:
    item_id = str(item.get("id") or "").strip()
    title_text = str(item.get("text") or "").strip()
    if not item_id or not title_text:
        return None
    signal = str(_rating(item).get("signal") or "").strip().lower()
    score = _rating(item).get("score")
    parts = [f"标的 {_coins_text(item)}" if _coins_text(item) else ""]
    if score:
        parts.append(f"AI影响力 {score}")
    if signal in {"long", "short"}:
        parts.append("信号 " + ("看多" if signal == "long" else "看空"))
    summary = str(_rating(item).get("summary") or "").strip()
    if summary:
        parts.append(summary[:160])
    return {
        "key": _stable_key("opennews", item_id),
        "kind": kind,
        "source": str(item.get("newsType") or "OpenNews"),
        "sourceLabel": source_label,
        "sourceType": "opennews",
        "title": title_text[:160] if kind in {"海外所上新", "HL 巨鲸异动"} else title,
        "body": body or " · ".join(part for part in parts if part),
        "url": str(item.get("link") or "").strip() or "https://www.newsliquid.com",
        "time": _ts_ms(item.get("ts")) or int(time.time() * 1000),
        "priority": kind,
        "aiScore": score or 0,
        "aiSignal": signal,
        "speech": f"{kind}，{title_text[:80]}。",
    }


def parse_opennews_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("disabled"):
        return []
    events: list[dict[str, Any]] = []
    # 1) Exchange listings — strong events, alert regardless of AI score.
    for item in payload.get("listing") or []:
        coins = _coins_text(item)
        source_name = str(item.get("newsType") or "交易所")
        event = _base_event(
            item,
            kind="海外所上新",
            source_label=SOURCE_LABELS.get(source_name, "ON"),
            title=f"{source_name} 上新公告：{coins or '新标的'}",
            body="",
        )
        if event:
            event["queuePriority"] = 88
            events.append(event)
    # 2) Hyperliquid whale activity — medium threshold.
    for item in payload.get("onchain") or []:
        rating = _rating(item)
        if int(rating.get("score") or 0) < 65:
            continue
        source_name = str(item.get("newsType") or "Hyperliquid")
        coins = _coins_text(item)
        event = _base_event(
            item,
            kind="HL 巨鲸异动",
            source_label=SOURCE_LABELS.get(source_name, "HL"),
            title=f"Hyperliquid 巨鲸：{coins or '大单异动'}",
            body="",
        )
        if event:
            events.append(event)
    # 3) Derivatives anomalies — high threshold to stay quiet.
    for item in payload.get("market") or []:
        rating = _rating(item)
        if int(rating.get("score") or 0) < 70:
            continue
        source_name = str(item.get("newsType") or "Market")
        label_map = {"price_change": "价格异动", "funding_rate": "资金费率异常",
                     "large_liquidation": "大额清算", "oi_change": "OI 异动"}
        label = label_map.get(source_name.strip().lower(), "衍生品异动")
        coins = _coins_text(item)
        event = _base_event(
            item,
            kind="衍生品异动",
            source_label=label[:2].upper(),
            title=f"{label}：{coins or '—'}",
            body="",
        )
        if event:
            events.append(event)
    # 4) High-impact editorial news (score>=80 pre-filtered server-side).
    for item in payload.get("news") or []:
        source_name = str(item.get("newsType") or "News")
        event = _base_event(
            item,
            kind="海外快讯",
            source_label=SOURCE_LABELS.get(source_name, "NW"),
            title="",
            body="",
        )
        if event:
            event["title"] = f"海外快讯：{event['title'][:140]}"
            events.append(event)
    return events
