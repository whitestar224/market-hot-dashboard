"""OpenTwitter (6551) REST client — deleted-tweet tracking, KOL follower
identification, follower/unfollower events, profiles, search.

Same 6551 Bearer token as OpenNews (env OPENNEWS_TOKEN); REST base
https://ai.6551.io (override with TWITTER_API_BASE).

Feed contract (consumed by server.site_alert_feeds):
- twitter_watch_feed_payload() -> deleted-tweet snapshots for the configured
  watch accounts (env OPENTWITTER_WATCH_ACCOUNTS, comma separated handles)
- parse_twitter_watch_events(payload) -> alert event dicts

Rug-precursor value: project accounts batch-deleting tweets is a classic
pre-rug signal that new-tweet-only monitoring cannot see.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

import requests

API_BASE = os.getenv("TWITTER_API_BASE", os.getenv("OPENNEWS_API_BASE", "https://ai.6551.io")).rstrip("/")

FETCH_TTL_S = 120
BACKOFF_AFTER_FAILS = 3
BACKOFF_COOLDOWN_S = 600
DELETED_SCAN_LIMIT = 30

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, Any]] = {}
_FAIL: dict[str, Any] = {"count": 0, "until": 0.0}


def opentwitter_enabled() -> bool:
    if (os.getenv("OPENTWITTER_ENABLED", "1") or "").strip().lower() in {"0", "false", "no"}:
        return False
    return bool((os.getenv("OPENNEWS_TOKEN", "") or "").strip())


def _token() -> str:
    return (os.getenv("OPENNEWS_TOKEN", "") or "").strip()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "User-Agent": "xingyunshe-market-hot/1.0 (OpenTwitter read-only)",
    }


def _post(path: str, body: dict[str, Any], *, timeout: float = 15.0) -> Any:
    response = requests.post(f"{API_BASE}{path}", json=body, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def _backed_off() -> bool:
    with _CACHE_LOCK:
        return time.time() < float(_FAIL.get("until") or 0.0)


def _record_failure() -> None:
    with _CACHE_LOCK:
        count = int(_FAIL.get("count") or 0) + 1
        _FAIL["count"] = count
        if count >= BACKOFF_AFTER_FAILS:
            _FAIL["until"] = time.time() + BACKOFF_COOLDOWN_S
            _FAIL["count"] = 0


def _record_success() -> None:
    with _CACHE_LOCK:
        _FAIL["count"] = 0
        _FAIL["until"] = 0.0


def _cached(key: str, builder):
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < FETCH_TTL_S:
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


# ---------------------------------------------------------------------------
# Public client functions (also usable by trench person-signal scoring)
# ---------------------------------------------------------------------------

def twitter_user_info(username: str) -> dict[str, Any]:
    """POST /open/twitter_user_info"""
    return _post("/open/twitter_user_info", {"username": _clean_handle(username)})


def twitter_user_tweets(username: str, *, max_results: int = 20) -> dict[str, Any]:
    """POST /open/twitter_user_tweets"""
    return _post("/open/twitter_user_tweets", {
        "username": _clean_handle(username), "maxResults": max(1, min(max_results, 100)),
        "product": "Latest", "includeReplies": False, "includeRetweets": False,
    })


def twitter_search(query: str, *, max_results: int = 20, min_likes: int = 0) -> dict[str, Any]:
    """POST /open/twitter_search"""
    body: dict[str, Any] = {"query": query[:200], "maxResults": max(1, min(max_results, 100)), "product": "Latest"}
    if min_likes > 0:
        body["minLikes"] = min_likes
    return _post("/open/twitter_search", body)


def twitter_deleted_tweets(username: str, *, max_results: int = DELETED_SCAN_LIMIT) -> list[dict[str, Any]]:
    """POST /open/twitter_deleted_tweets — rug-precursor evidence."""
    data = _cached(
        f"deleted:{_clean_handle(username).lower()}:{max_results}",
        lambda: _post("/open/twitter_deleted_tweets", {
            "username": _clean_handle(username), "maxResults": max(1, min(max_results, 100)),
        }),
    )
    return _extract_list(data)


def twitter_kol_followers(username: str) -> list[dict[str, Any]]:
    """POST /open/twitter_kol_followers — which KOLs follow this account."""
    data = _cached(
        f"kolfollowers:{_clean_handle(username).lower()}",
        lambda: _post("/open/twitter_kol_followers", {"username": _clean_handle(username)}),
    )
    return _extract_list(data)


def twitter_follower_events(username: str, *, is_follow: bool = True, max_results: int = 20) -> list[dict[str, Any]]:
    """POST /open/twitter_follower_events — follow (or unfollow) events."""
    data = _cached(
        f"followerevents:{_clean_handle(username).lower()}:{int(is_follow)}",
        lambda: _post("/open/twitter_follower_events", {
            "username": _clean_handle(username), "isFollow": bool(is_follow),
            "maxResults": max(1, min(max_results, 100)),
        }),
    )
    return _extract_list(data)


def _clean_handle(username: str) -> str:
    return re.sub(r"^@+", "", str(username or "").strip())


def _extract_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("list") or data.get("items") or data.get("data") or data.get("tweets") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


# ---------------------------------------------------------------------------
# Feed contract: watch-account deleted tweets -> alert events
# ---------------------------------------------------------------------------

def watch_accounts() -> list[str]:
    raw = (os.getenv("OPENTWITTER_WATCH_ACCOUNTS", "") or "").strip()
    handles: list[str] = []
    for part in re.split(r"[,;\s]+", raw):
        handle = _clean_handle(part)
        if handle and handle.lower() not in {existing.lower() for existing in handles}:
            handles.append(handle)
    return handles


def twitter_watch_feed_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {"fetchedAt": int(time.time() * 1000), "accounts": [], "disabled": False}
    if not opentwitter_enabled():
        payload["disabled"] = True
        return payload
    accounts = watch_accounts()
    if not accounts:
        payload["disabled"] = True
        return payload
    for handle in accounts[:12]:
        deleted = twitter_deleted_tweets(handle, max_results=DELETED_SCAN_LIMIT)
        payload["accounts"].append({"username": handle, "deleted": deleted})
    return payload


def _stable_key(prefix: str, *parts: Any) -> str:
    body = "|".join(
        re.sub(r"\s+", " ", str(part or "").strip().lower())[:180]
        for part in parts
        if part is not None and str(part or "").strip()
    )
    return f"{prefix}:{body}"


def _tweet_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("idStr") or row.get("tweetId") or "").strip()


def _tweet_text(row: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(row.get("text") or row.get("fullText") or "")).strip()


def parse_twitter_watch_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("disabled"):
        return []
    events: list[dict[str, Any]] = []
    for account in payload.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        handle = str(account.get("username") or "").strip()
        deleted = account.get("deleted") if isinstance(account.get("deleted"), list) else []
        fresh = [row for row in deleted if _tweet_id(row)]
        if not fresh:
            continue
        latest_text = _tweet_text(fresh[0])[:120]
        events.append({
            "key": _stable_key("x-deleted", handle, *(sorted(_tweet_id(row) for row in fresh))),
            "kind": "X 删推预警",
            "source": f"X @{handle}",
            "sourceLabel": "XD",
            "sourceType": "opentwitter",
            "title": f"@{handle} 删除了 {len(fresh)} 条推文",
            "body": f"最近删除：{latest_text}" if latest_text else "项目方批量删推是经典 rug 前兆，建议核查。",
            "url": f"https://x.com/{handle}" if handle else "https://x.com",
            "time": int(payload.get("fetchedAt") or time.time() * 1000),
            "priority": "风控预警",
            "queuePriority": 92,
            "deletedCount": len(fresh),
            "speech": f"X 删推预警，{handle} 删除了 {len(fresh)} 条推文。",
        })
    return events
