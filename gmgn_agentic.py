"""Read-only GMGN Agent data used by the migrated-trenches scanner.

This module deliberately exposes market discovery only.  It never loads a GMGN
private key and never calls swap, order, cooking, or other write endpoints.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests


GMGN_OPENAPI_BASE = "https://openapi.gmgn.ai"
GMGN_TRENCHES_PATH = "/v1/trenches"
# The user explicitly connected GMGN API management for this project. Only the
# API key is read; GMGN_PRIVATE_KEY/signing material is never loaded.
GMGN_PUBLIC_READONLY_API_KEY = "gmgn_basesolbscethmonadtron"
GMGN_TRENCHES_NETWORKS = {
    "solana": "sol",
    "bsc": "bsc",
    "base": "base",
    "eth": "eth",
    "robinhood": "robinhood",
    "arc": "arc",
}
# These profiles mirror the six saved risk filters supplied by the user. GMGN's
# internal quote-address type IDs are intentionally not pinned in the upstream
# request: new native categories must be received automatically instead of
# disappearing before risk checks. Optional negative signals reject an item
# only when GMGN explicitly reports the bad condition; positive requirements
# remain strict.
GMGN_TRENCH_FILTER_PROFILE_VERSION = 3
# These are native GMGN filter field names (not token/launchpad allow-lists).
# Keep them shared by all six monitored chains so API results and historical
# rows cannot bypass the saved Trenches profile.
GMGN_REQUIRED_TRENCH_FILTERS = ("has_social",)
# The saved GMGN profile no longer enables “仅看 OG”.  Request the complete
# social-enabled tape, then apply the market-cap floor and the chain-specific
# safety switches locally so both OG and non-OG opportunities are eligible.
GMGN_UPSTREAM_TRENCH_FILTERS = ("has_social",)
GMGN_TRENCH_MIN_MARKET_CAP_USD = 10_000.0
# A non-OG token may override the saved checkbox only when its live market
# data is materially stronger than the OG baseline in the same GMGN batch.
# These are adaptive thresholds, not a symbol/contract allow-list.
GMGN_NON_OG_EXCEPTION_MIN_SCORE = 72.0
# “比当前 OG 基准更好” is intentionally a strict comparison with only a
# small rounding cushion. The absolute floor above still prevents ordinary
# non-OG rows from entering solely because the batch has a weak OG sample.
GMGN_NON_OG_EXCEPTION_MARGIN = 0.5
# GMGN's “头像不重复” switch is useful for suppressing clone floods, but a
# single popular artwork can legitimately be reused by a small launch wave.
# Keep the first three same-image/name entries and only filter the fourth and
# later occurrence.  This is deliberately a count, not a symbol whitelist.
GMGN_MAX_DUPLICATE_FAMILY_SIZE = 3
GMGN_TRENCH_CHAIN_FILTERS: dict[str, dict[str, Any]] = {
    "solana": {
        "imageNotDuplicate": True,
        "requireSocial": True,
        "minMarketCapUsd": GMGN_TRENCH_MIN_MARKET_CAP_USD,
        "excludeLaunchpads": ("uxento", "rapidlaunch"),
    },
    "bsc": {
        "imageNotDuplicate": True,
        "requireSocial": True,
        "minMarketCapUsd": GMGN_TRENCH_MIN_MARKET_CAP_USD,
        "excludeLaunchpads": ("uxento", "rapidlaunch"),
    },
    "robinhood": {
        "imageNotDuplicate": True,
        "notHoneypot": True,
        "requireSocial": True,
        "minMarketCapUsd": GMGN_TRENCH_MIN_MARKET_CAP_USD,
        "excludeLaunchpads": ("uxento", "rapidlaunch"),
    },
    # The native saved profile only enables “not honeypot” here.  The UI's
    # “not open source”, “not renounced”, and “burn pool” switches are
    # independent optional controls; treating them as mandatory made the
    # Base chain disappear even when GMGN returned valid OG rows.
    "base": {
        "imageNotDuplicate": True,
        "notHoneypot": True,
        "requireSocial": True,
        "minMarketCapUsd": GMGN_TRENCH_MIN_MARKET_CAP_USD,
    },
    "eth": {
        "imageNotDuplicate": True,
        "requireSocial": True,
        "minMarketCapUsd": GMGN_TRENCH_MIN_MARKET_CAP_USD,
    },
    "arc": {
        "imageNotDuplicate": True,
        "notHoneypot": True,
        "requireSocial": True,
        "minMarketCapUsd": GMGN_TRENCH_MIN_MARKET_CAP_USD,
        "excludeLaunchpads": ("uxento", "rapidlaunch"),
    },
}
# GMGN's Trenches OpenAPI currently returns an empty Arc tape while its
# official market-rank route and web Trenches page contain the live Arc list.
# These are the same explicit risk switches used by the saved Arc profile;
# quote-asset IDs and launchpad allow-lists are deliberately not pinned.
GMGN_ARC_RANK_FILTERS = (
    "not_honeypot",
    "not_image_dup",
    "has_social",
)
# Send the complete native profile to the rank route as well.  The market-cap
# floor remains a local check because the public route does not expose one
# stable cross-chain parameter for that threshold.
GMGN_ARC_UPSTREAM_RANK_FILTERS = GMGN_ARC_RANK_FILTERS
GMGN_READONLY_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "xingyunshe-market-hot/1.0 (GMGN read-only trenches)",
}

_GMGN_RATE_LOCK = threading.Lock()
_GMGN_REQUEST_LOCK = threading.Lock()
_GMGN_CACHE_LOCK = threading.Lock()
_GMGN_NEXT_REQUEST_AT = 0.0
_GMGN_COOLDOWN_UNTIL = 0.0  # Unix timestamp persisted across service restarts.
_GMGN_RATE_LIMIT_STREAK = 0
_GMGN_RESPONSE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_GMGN_RATE_STATE_LOADED = False
_GMGN_RUNTIME_DIR = Path(
    os.getenv("XINGYUN_RUNTIME_CACHE_DIR")
    or Path(__file__).resolve().parent / ".runtime-cache"
).expanduser().resolve()
_GMGN_PERSIST_DIR = _GMGN_RUNTIME_DIR / "gmgn-readonly"
_GMGN_RATE_STATE_PATH = _GMGN_RUNTIME_DIR / "gmgn-rate-state.json"
_GMGN_HTTP_SESSION = requests.Session()
# 跟随系统代理 (trust_env=True): 系统级 HTTP(S)_PROXY 由用户统一管理
# (clash/Proton 混用会切换), GMGN 抓取不应固化直连或某个固定代理。
# 2026-09-25 实测: 直连超时而系统代理健康, 固化直连导致战壕榜全链停更。
_GMGN_HTTP_SESSION.trust_env = True


class GmgnRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(f"GMGN API 请求过多，约 {self.retry_after_seconds} 秒后自动恢复")


class GmgnUnsupportedNetworkError(RuntimeError):
    pass


def _gmgn_proxy_url() -> str:
    value = str(os.getenv("GMGN_PROXY_URL") or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("GMGN_PROXY_URL 必须是有效的 HTTP/HTTPS 独享代理地址")
    return value


def gmgn_api_key_status() -> dict[str, Any]:
    proxy_url = _gmgn_proxy_url()
    mode = str(os.getenv("GMGN_READONLY_KEY_MODE") or "public").strip().casefold()
    if mode not in {"public", "personal"}:
        mode = "public"
    personal_key = _gmgn_config_api_key() if mode == "personal" else ""
    return {
        "configured": bool(personal_key) if mode == "personal" else True,
        "source": "personal" if personal_key else "personal-missing" if mode == "personal" else "public-readonly",
        "authMode": mode,
        "personalKeyUsed": bool(personal_key),
        "transport": "dedicated-proxy" if proxy_url else "direct-or-tun",
        "dedicatedEgressConfigured": bool(proxy_url),
    }


def _gmgn_config_api_key() -> str:
    configured = str(os.getenv("GMGN_API_KEY") or "").strip()
    if configured:
        return configured
    config_path = Path.home() / ".config" / "gmgn" / ".env"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == "GMGN_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def _gmgn_api_key() -> str:
    mode = str(os.getenv("GMGN_READONLY_KEY_MODE") or "public").strip().casefold()
    if mode != "personal":
        return GMGN_PUBLIC_READONLY_API_KEY
    personal_key = _gmgn_config_api_key()
    if not personal_key:
        raise RuntimeError("GMGN 已选择个人 Key 模式，但尚未配置 GMGN_API_KEY")
    return personal_key


def _env_enabled(name: str, default: bool) -> bool:
    value = str(os.getenv(name) or "").strip().casefold()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _persist_rate_state_enabled() -> bool:
    return _env_enabled("GMGN_PERSIST_RATE_STATE", True)


def _persist_response_cache_enabled() -> bool:
    return _env_enabled("GMGN_READONLY_PERSIST_CACHE", True)


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _persistent_cache_path(cache_key: str) -> Path:
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return _GMGN_PERSIST_DIR / f"{digest}.json"


def _load_rate_state() -> None:
    global _GMGN_RATE_STATE_LOADED, _GMGN_COOLDOWN_UNTIL, _GMGN_RATE_LIMIT_STREAK
    with _GMGN_RATE_LOCK:
        if _GMGN_RATE_STATE_LOADED:
            return
        _GMGN_RATE_STATE_LOADED = True
    if not _persist_rate_state_enabled():
        return
    payload = _read_json_file(_GMGN_RATE_STATE_PATH)
    try:
        retry_at = max(0.0, float(payload.get("retryAt") or 0))
        streak = max(0, int(payload.get("streak") or 0))
    except (TypeError, ValueError):
        return
    with _GMGN_RATE_LOCK:
        _GMGN_COOLDOWN_UNTIL = max(_GMGN_COOLDOWN_UNTIL, retry_at)
        _GMGN_RATE_LIMIT_STREAK = max(_GMGN_RATE_LIMIT_STREAK, streak)


def _persist_rate_state() -> None:
    if not _persist_rate_state_enabled():
        return
    with _GMGN_RATE_LOCK:
        payload = {
            "version": 1,
            "retryAt": _GMGN_COOLDOWN_UNTIL,
            "streak": _GMGN_RATE_LIMIT_STREAK,
            "updatedAt": int(time.time()),
        }
    _write_json_file(_GMGN_RATE_STATE_PATH, payload)


def reset_gmgn_runtime_state(*, clear_persistent: bool = False) -> None:
    """Reset cache and cooldown; primarily used by deterministic tests."""
    global _GMGN_NEXT_REQUEST_AT, _GMGN_COOLDOWN_UNTIL, _GMGN_RATE_LIMIT_STREAK, _GMGN_RATE_STATE_LOADED
    with _GMGN_CACHE_LOCK:
        _GMGN_RESPONSE_CACHE.clear()
    with _GMGN_RATE_LOCK:
        _GMGN_NEXT_REQUEST_AT = 0.0
        _GMGN_COOLDOWN_UNTIL = 0.0
        _GMGN_RATE_LIMIT_STREAK = 0
        _GMGN_RATE_STATE_LOADED = True
    if clear_persistent:
        for path in [_GMGN_RATE_STATE_PATH, *list(_GMGN_PERSIST_DIR.glob("*.json"))]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def gmgn_cooldown_status() -> dict[str, Any]:
    _load_rate_state()
    with _GMGN_RATE_LOCK:
        retry_at = float(_GMGN_COOLDOWN_UNTIL)
    remaining = max(0, int(retry_at - time.time() + 0.999))
    return {
        "active": remaining > 0,
        "retryAt": int(retry_at) if remaining > 0 else 0,
        "retryAfterSeconds": remaining,
    }


def _response_status(response: Any) -> int:
    try:
        return int(response.status_code)
    except (AttributeError, TypeError, ValueError):
        return 0


def _retry_after_seconds(response: Any) -> int:
    global _GMGN_RATE_LIMIT_STREAK
    now = time.time()
    payload: Mapping[str, Any] = {}
    try:
        candidate = response.json()
        if isinstance(candidate, Mapping):
            payload = candidate
    except (TypeError, ValueError):
        pass
    reset_values: list[Any] = [payload.get("reset_at")]
    try:
        reset_values.append(response.headers.get("X-RateLimit-Reset"))
    except (AttributeError, TypeError):
        pass
    for value in reset_values:
        try:
            reset_at = float(value)
        except (TypeError, ValueError):
            continue
        if reset_at > now:
            return max(1, min(900, int(reset_at - now + 0.999)))
    header = ""
    try:
        header = str(response.headers.get("Retry-After") or "")
    except (AttributeError, TypeError):
        pass
    if header.isdigit():
        return max(5, min(900, int(header)))
    text = str(payload.get("message") or payload.get("reason") or getattr(response, "text", "") or "")
    match = re.search(r"(\d{1,4})\s*(?:s|sec|second|秒)", text, re.IGNORECASE)
    if match:
        return max(5, min(900, int(match.group(1))))
    return min(300, 45 * (2 ** min(_GMGN_RATE_LIMIT_STREAK, 3)))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _first_number(source: Mapping[str, Any], *keys: str) -> float | None:
    """Read the first finite numeric value from a set of GMGN aliases.

    GMGN has used both snake_case and camelCase names for the same filter
    signal across its REST and websocket payloads.  Keeping the alias lookup
    in one place prevents a missing field from silently disabling a filter.
    """
    for key in keys:
        if key not in source:
            continue
        parsed = _number(source.get(key))
        if parsed is not None:
            return parsed
    return None


def _integer(value: Any) -> int:
    parsed = _number(value)
    return int(parsed) if parsed is not None else 0


def _percent(value: Any) -> float | None:
    parsed = _number(value)
    if parsed is None:
        return None
    return parsed * 100 if -1 <= parsed <= 1 else parsed


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "on"}


def _optional_flag(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().casefold() in {"", "unknown", "null", "none", "-1"}:
        return None
    return _flag(value)


def _row_social_count(row: Mapping[str, Any], signals: Mapping[str, Any]) -> int:
    """Return the number of usable social/project links known for a row.

    Normalized rows store this in ``launchFacts.socialCount`` and
    ``narrativeContext.socials``.  The fallback fields keep the predicate
    compatible with rows produced by older adapters while still treating an
    explicitly reported zero as zero (rather than accidentally passing it).
    """
    for source in (
        signals.get("socialCount"),
        (row.get("launchFacts") or {}).get("socialCount")
        if isinstance(row.get("launchFacts"), Mapping) else None,
    ):
        parsed = _number(source)
        if parsed is not None:
            return max(0, int(parsed))
    context = row.get("narrativeContext")
    if isinstance(context, Mapping):
        socials = context.get("socials")
        if isinstance(socials, (list, tuple, set)):
            return sum(1 for value in socials if str(value or "").strip())
        links = context.get("links")
        if isinstance(links, Mapping):
            return sum(1 for value in links.values() if str(value or "").strip())
    socials = row.get("socials")
    if isinstance(socials, (list, tuple, set)):
        return sum(1 for value in socials if str(value or "").strip())
    return 0


def _row_market_cap_usd(row: Mapping[str, Any]) -> float | None:
    """Read the actual market-cap field without silently substituting FDV."""
    metrics = row.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    for value in (
        metrics.get("marketCapUsd"),
        row.get("marketCapUsd"),
        row.get("market_cap"),
        row.get("marketCap"),
    ):
        parsed = _number(value)
        if parsed is not None:
            return max(0.0, parsed)
    return None


def gmgn_trench_performance_score(row: Mapping[str, Any]) -> float:
    """Score observable market strength for the non-OG exception.

    This deliberately uses only fields visible in the live trench snapshot:
    liquidity, first-hour turnover, transaction breadth, buy/sell balance and
    holder count.  It is not a prediction score and cannot promote a token
    that has no explicit ``is_og`` value.
    """
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    facts = row.get("launchFacts") if isinstance(row.get("launchFacts"), Mapping) else {}

    def metric(*keys: str) -> float:
        for key in keys:
            parsed = _number(metrics.get(key))
            if parsed is None:
                parsed = _number(facts.get(key))
            if parsed is not None:
                return max(0.0, parsed)
        return 0.0

    def log_component(value: float, reference: float) -> float:
        if value <= 0:
            return 0.0
        return min(100.0, (math.log1p(value) / math.log1p(reference)) * 100.0)

    liquidity = metric("liquidityUsd")
    volume_h1 = metric("volumeH1Usd", "volumeH24Usd")
    transactions_h1 = metric("transactionsH1", "transactionsH24")
    holders = metric("holders", "holderCount")
    buys = metric("buysH1", "buysH24")
    sells = metric("sellsH1", "sellsH24")
    total_sides = buys + sells
    buy_balance = 50.0 if total_sides <= 0 else min(100.0, buys / total_sides * 100.0)
    return round(
        log_component(liquidity, 100_000) * 0.28
        + log_component(volume_h1, 500_000) * 0.28
        + log_component(transactions_h1, 500) * 0.20
        + buy_balance * 0.14
        + log_component(holders, 2_000) * 0.10,
        2,
    )


def annotate_gmgn_non_og_exceptions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare non-OG candidates with the best OG in their own chain batch."""
    by_network: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        network = str(row.get("network") or row.get("chain") or "").strip().casefold()
        if network == "sol":
            network = "solana"
        by_network.setdefault(network, []).append(row)
    for grouped_rows in by_network.values():
        og_scores = [
            gmgn_trench_performance_score(row)
            for row in grouped_rows
            if isinstance(row.get("filterSignals"), Mapping)
            and row["filterSignals"].get("isOg") is True
        ]
        og_baseline = max(og_scores) if og_scores else None
        for row in grouped_rows:
            signals = row.get("filterSignals")
            if not isinstance(signals, dict) or signals.get("isOg") is not False:
                continue
            score = gmgn_trench_performance_score(row)
            stronger_than_og = og_baseline is None or score >= og_baseline + GMGN_NON_OG_EXCEPTION_MARGIN
            exception = score >= GMGN_NON_OG_EXCEPTION_MIN_SCORE and stronger_than_og
            signals["nonOgException"] = exception
            if exception:
                warnings = row.get("filterWarnings")
                if not isinstance(warnings, list):
                    warnings = []
                    row["filterWarnings"] = warnings
                warning = "非 OG · 市场数据强于当前 OG 基准"
                if warning not in warnings:
                    warnings.append(warning)
    return rows


def _gmgn_non_og_exception(row: Mapping[str, Any]) -> bool:
    signals = row.get("filterSignals")
    if not isinstance(signals, Mapping):
        return False
    return signals.get("nonOgException") is True


def gmgn_trench_passes_chain_filters(row: Mapping[str, Any]) -> bool:
    """Apply the saved GMGN UI filter for one chain to a normalized row."""
    network = str(row.get("network") or row.get("chain") or "").strip().casefold()
    if network == "sol":
        network = "solana"
    profile = GMGN_TRENCH_CHAIN_FILTERS.get(network)
    if not profile:
        return False
    signals = row.get("filterSignals")
    if not isinstance(signals, Mapping):
        # Historical rows written before profile version 1 were not guaranteed
        # to have passed the user's filters and must not leak back into the UI.
        return False
    if int(_number(signals.get("profileVersion")) or 0) != GMGN_TRENCH_FILTER_PROFILE_VERSION:
        return False

    if profile.get("requireSocial") and _row_social_count(row, signals) < 1:
        return False
    min_market_cap = _number(profile.get("minMarketCapUsd"))
    if min_market_cap is not None:
        market_cap = _row_market_cap_usd(row)
        # “MC 大于 10K” is strict: an unknown value or exactly $10K does not
        # meet the saved filter.
        if market_cap is None or market_cap <= min_market_cap:
            return False

    if profile.get("imageNotDuplicate"):
        duplicate_count = _number(signals.get("imageDuplicateCount"))
        # Native GMGN's ``img_not_duplicate`` predicate is
        # ``Number(image_dup) <= 1``: the first occurrence is allowed, while
        # the second and later tokens sharing the artwork are filtered.
        if duplicate_count is not None and duplicate_count > GMGN_MAX_DUPLICATE_FAMILY_SIZE:
            return False
        name_duplicate_count = _number(signals.get("nameDuplicateCount"))
        if name_duplicate_count is not None and name_duplicate_count > GMGN_MAX_DUPLICATE_FAMILY_SIZE:
            return False
    if profile.get("excludeDeveloperWashTrading") and signals.get("washTrading") is True:
        return False
    # GMGN exposes two independent controls: "过滤老鼠仓刷量" is a boolean
    # classification, while "老鼠仓持仓" is a numeric Min/Max metric.  A
    # non-zero holding ratio alone must therefore remain visible.
    rat_wash_trading = signals.get("ratWashTrading")
    if rat_wash_trading is None:
        rat_wash_trading = signals.get("ratTrading")  # legacy history rows
    if profile.get("excludeRatWashTrading") and rat_wash_trading is True:
        return False
    if profile.get("notHoneypot") and signals.get("honeypot") is True:
        return False
    if profile.get("openSource") and signals.get("openSource") is not True:
        return False
    if profile.get("ownerRenounced") and signals.get("ownerRenounced") is not True:
        return False
    if profile.get("burnedPool") and str(signals.get("burnStatus") or "").strip().casefold() != "burn":
        return False

    excluded_launchpads = tuple(str(value).casefold() for value in profile.get("excludeLaunchpads") or ())
    if excluded_launchpads:
        origin = " ".join((
            str(row.get("launchpad") or ""),
            str(row.get("dexId") or ""),
            str(signals.get("creationTool") or ""),
        )).casefold().replace("_", "").replace("-", "")
        if any(value.replace("_", "").replace("-", "") in origin for value in excluded_launchpads):
            return False
    return True


def _web_url(value: Any) -> str:
    url = _text(value, 1200).strip()
    return url if url.startswith(("https://", "http://")) else ""


def _image_identity(value: Any) -> str:
    """Return a stable identity for an explicitly supplied token image URL.

    CDN query strings are often cache-busters, not different artwork.  The
    image duplicate filter should therefore compare the host/path while
    leaving empty or non-HTTP placeholders out of the batch check.
    """
    url = _web_url(value)
    if not url:
        return ""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/") or "/"
    if not host or not path:
        return ""
    return f"{host}{path.casefold()}"


def _timestamp_ms(*values: Any) -> int:
    for value in values:
        parsed = _integer(value)
        if parsed:
            return parsed if parsed >= 10_000_000_000 else parsed * 1000
    return 0


def _text(value: Any, limit: int) -> str:
    raw = str(value or "")
    return "".join(character for character in raw if character in "\t\n\r" or ord(character) >= 32)[:limit]


def _localized_text(value: Any, limit: int = 1800) -> str:
    """Read one localized GMGN text field without treating metadata as code."""
    if isinstance(value, str):
        return _text(value, limit).strip()
    if not isinstance(value, Mapping):
        return ""
    for key in (
        "cn", "zh_cn", "zh-CN", "zh", "aiSummaryCn", "summary_cn",
        "content_cn", "text_cn", "content", "text", "summary", "narrative",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return _text(candidate, limit).strip()
    return ""


def _gmgn_native_narrative(raw: Mapping[str, Any]) -> str:
    """Return only an explicitly named GMGN AI narrative field.

    The regular project description is intentionally excluded: presenting a
    deployer-written description as GMGN AI would be misleading.  Some GMGN
    API tiers use camelCase while others use snake_case, so both are accepted.
    """
    keys = (
        "ai_narrative", "aiNarrative", "ai_narrative_text", "aiNarrativeText",
        "ai_summary", "aiSummary", "gmgn_ai_narrative", "gmgnAiNarrative",
    )
    for key in keys:
        narrative = _localized_text(raw.get(key))
        if narrative:
            return narrative
    for container_key in ("meta_info", "metaInfo", "ai_info", "aiInfo"):
        container = raw.get(container_key)
        if not isinstance(container, Mapping):
            continue
        for key in keys:
            narrative = _localized_text(container.get(key))
            if narrative:
                return narrative
    return ""


def _gmgn_x_original(raw: Mapping[str, Any], twitter_url: str) -> dict[str, Any]:
    """Preserve GMGN's original-X identity without making another request."""
    handle = _text(raw.get("twitter_handle") or raw.get("twitter_username"), 80).strip().lstrip("@")
    match = re.search(r"(?:x|twitter)\.com/([^/?#]+)/status/(\d+)", twitter_url, re.IGNORECASE)
    if match:
        handle = handle or match.group(1).lstrip("@")
    status_id = match.group(2) if match else ""
    text = ""
    for key in ("twitter_text", "tweet_text", "x_post_text", "original_tweet_text"):
        text = _localized_text(raw.get(key), 2800)
        if text:
            break
    return {
        "url": twitter_url,
        "handle": handle,
        "statusId": status_id,
        "isTweet": bool(_flag(raw.get("twitter_is_tweet")) or status_id),
        "text": text,
        "publishedAt": _timestamp_ms(raw.get("tweet_publish_time")),
        "followers": _integer(raw.get("x_user_follower")),
    }


def _wait_for_readonly_slot() -> None:
    """Serialize the free read-only requests so six-chain startup does not burst."""
    global _GMGN_NEXT_REQUEST_AT
    configured_interval = os.getenv("GMGN_READONLY_MIN_INTERVAL_SECONDS")
    if configured_interval in {None, ""}:
        configured_interval = os.getenv("GMGN_TRENCHES_MIN_INTERVAL_SECONDS", "1.0")
    interval = max(1.0, float(configured_interval or 1.0))
    with _GMGN_RATE_LOCK:
        remaining = _GMGN_COOLDOWN_UNTIL - time.time()
        if remaining > 0:
            raise GmgnRateLimitError(int(remaining + 0.999))
        delay = _GMGN_NEXT_REQUEST_AT - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _GMGN_NEXT_REQUEST_AT = time.monotonic() + interval


def _cache_entry(
    cache_key: str,
    *,
    allow_persistent: bool = False,
) -> tuple[float, dict[str, Any]] | None:
    with _GMGN_CACHE_LOCK:
        cached = _GMGN_RESPONSE_CACHE.get(cache_key)
    if cached:
        return cached[0], dict(cached[1])
    if not allow_persistent or not _persist_response_cache_enabled():
        return None
    stored = _read_json_file(_persistent_cache_path(cache_key))
    if not isinstance(stored.get("payload"), Mapping):
        return None
    try:
        updated_at = float(stored.get("updatedAt") or 0)
    except (TypeError, ValueError):
        return None
    if updated_at <= 0:
        return None
    value = dict(stored["payload"])
    with _GMGN_CACHE_LOCK:
        _GMGN_RESPONSE_CACHE[cache_key] = (updated_at, value)
    return updated_at, dict(value)


def _store_cache_entry(
    cache_key: str,
    payload: Mapping[str, Any],
    updated_at: float,
    *,
    allow_persistent: bool = False,
) -> None:
    value = dict(payload)
    value.pop("_gmgnMeta", None)
    with _GMGN_CACHE_LOCK:
        _GMGN_RESPONSE_CACHE[cache_key] = (updated_at, value)
    if allow_persistent and _persist_response_cache_enabled():
        _write_json_file(
            _persistent_cache_path(cache_key),
            {"version": 1, "updatedAt": updated_at, "payload": value},
        )


def _payload_with_meta(
    payload: Mapping[str, Any],
    *,
    cached: bool,
    stale: bool,
    updated_at: float,
    error: str = "",
) -> dict[str, Any]:
    result = dict(payload)
    result["_gmgnMeta"] = {
        **gmgn_api_key_status(),
        **gmgn_cooldown_status(),
        "cached": cached,
        "stale": stale,
        "updatedAt": int(updated_at * 1000),
        "error": str(error or "")[:180],
    }
    return result


def _record_rate_limit(response: Any) -> int:
    global _GMGN_COOLDOWN_UNTIL, _GMGN_RATE_LIMIT_STREAK
    retry_after = _retry_after_seconds(response)
    with _GMGN_RATE_LOCK:
        _GMGN_RATE_LIMIT_STREAK += 1
        _GMGN_COOLDOWN_UNTIL = max(_GMGN_COOLDOWN_UNTIL, time.time() + retry_after)
    _persist_rate_state()
    return retry_after


def _clear_rate_limit() -> None:
    global _GMGN_COOLDOWN_UNTIL, _GMGN_RATE_LIMIT_STREAK
    changed = False
    with _GMGN_RATE_LOCK:
        if _GMGN_COOLDOWN_UNTIL or _GMGN_RATE_LIMIT_STREAK:
            changed = True
        _GMGN_COOLDOWN_UNTIL = 0.0
        _GMGN_RATE_LIMIT_STREAK = 0
    if changed:
        _persist_rate_state()


def gmgn_readonly_post(
    path: str,
    *,
    cache_key: str,
    body: Mapping[str, Any],
    params: Mapping[str, Any] | None = None,
    cache_ttl_seconds: float = 60,
    stale_ttl_seconds: float | None = None,
    persist_cache: bool = False,
    force_refresh: bool = False,
    session: Any = None,
    _method: str = "POST",
) -> dict[str, Any]:
    """Run one cached, single-flight GMGN read-only request.

    Response persistence is opt-in per route.  Real-time trenches therefore
    never read a disk fallback, while lower-frequency boards can explicitly
    retain their last successful response across a service restart.
    """
    ttl = max(1.0, float(cache_ttl_seconds or 60))
    configured_stale = max(
        ttl,
        float(os.getenv("GMGN_READONLY_STALE_SECONDS", "600") or 600),
    )
    stale_ttl = max(
        ttl,
        float(stale_ttl_seconds) if stale_ttl_seconds is not None else ttl,
    )
    if persist_cache and stale_ttl_seconds is None:
        stale_ttl = configured_stale

    def usable_cached(*, fresh_only: bool) -> dict[str, Any] | None:
        cached = _cache_entry(cache_key, allow_persistent=persist_cache)
        if not cached:
            return None
        updated_at, value = cached
        age = max(0.0, time.time() - updated_at)
        if age <= ttl and not force_refresh:
            return _payload_with_meta(value, cached=True, stale=False, updated_at=updated_at)
        if not fresh_only and age <= stale_ttl:
            return _payload_with_meta(value, cached=True, stale=True, updated_at=updated_at)
        return None

    fresh = usable_cached(fresh_only=True)
    if fresh is not None:
        return fresh
    cooldown = gmgn_cooldown_status()
    if cooldown["active"]:
        stale = usable_cached(fresh_only=False)
        if stale is not None:
            return stale
        raise GmgnRateLimitError(cooldown["retryAfterSeconds"])

    # Different GMGN routes share one outbound gate. Waiting callers re-check
    # the cache after the leader finishes, so identical requests collapse to a
    # single upstream call while different chains are deliberately staggered.
    with _GMGN_REQUEST_LOCK:
        fresh = usable_cached(fresh_only=True)
        if fresh is not None:
            return fresh
        cooldown = gmgn_cooldown_status()
        if cooldown["active"]:
            stale = usable_cached(fresh_only=False)
            if stale is not None:
                return stale
            raise GmgnRateLimitError(cooldown["retryAfterSeconds"])

        try:
            _wait_for_readonly_slot()
            client = session or _GMGN_HTTP_SESSION
            request_params = dict(params or {})
            request_params.update({
                "timestamp": int(time.time()),
                "client_id": str(uuid.uuid4()),
            })
            request_kwargs: dict[str, Any] = {
                "params": request_params,
                "headers": {**GMGN_READONLY_HEADERS, "X-APIKEY": _gmgn_api_key()},
                "timeout": (6, 30),
            }
            method = str(_method or "POST").strip().upper()
            if method == "POST":
                request_kwargs["json"] = dict(body)
            elif method != "GET":
                raise ValueError(f"Unsupported GMGN read-only method: {method}")
            proxy_url = _gmgn_proxy_url()
            if proxy_url:
                request_kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
            response = (
                client.get(GMGN_OPENAPI_BASE + path, **request_kwargs)
                if method == "GET"
                else client.post(GMGN_OPENAPI_BASE + path, **request_kwargs)
            )
            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError("GMGN 返回了无法解析的数据") from exc
            code = payload.get("code") if isinstance(payload, Mapping) else None
            if _response_status(response) == 429 or str(code) == "429":
                retry_after = _record_rate_limit(response)
                stale = usable_cached(fresh_only=False)
                if stale is not None:
                    return _payload_with_meta(
                        stale,
                        cached=True,
                        stale=True,
                        updated_at=float(stale.get("_gmgnMeta", {}).get("updatedAt", 0)) / 1000 or time.time(),
                        error=f"rate limited; retry in {retry_after}s",
                    )
                raise GmgnRateLimitError(retry_after)
            response.raise_for_status()
            if not isinstance(payload, Mapping):
                raise RuntimeError("GMGN 返回格式无效")
            result = dict(payload)
            if code in (0, "0", None):
                result.pop("_gmgnMeta", None)
                updated_at = time.time()
                _store_cache_entry(
                    cache_key,
                    result,
                    updated_at,
                    allow_persistent=persist_cache,
                )
                _clear_rate_limit()
                return _payload_with_meta(result, cached=False, stale=False, updated_at=updated_at)
            return result
        except GmgnRateLimitError:
            raise
        except Exception as exc:
            stale = usable_cached(fresh_only=False)
            if stale is not None:
                meta = stale.get("_gmgnMeta") if isinstance(stale.get("_gmgnMeta"), Mapping) else {}
                return _payload_with_meta(
                    stale,
                    cached=True,
                    stale=True,
                    updated_at=float(meta.get("updatedAt") or 0) / 1000 or time.time(),
                    error=str(exc),
                )
            raise


def gmgn_readonly_get(
    path: str,
    *,
    cache_key: str,
    params: Mapping[str, Any] | None = None,
    cache_ttl_seconds: float = 60,
    stale_ttl_seconds: float | None = None,
    persist_cache: bool = False,
    force_refresh: bool = False,
    session: Any = None,
) -> dict[str, Any]:
    """Run one cached GMGN public read-only GET through the shared rate gate."""
    return gmgn_readonly_post(
        path,
        cache_key=cache_key,
        body={},
        params=params,
        cache_ttl_seconds=cache_ttl_seconds,
        stale_ttl_seconds=stale_ttl_seconds,
        persist_cache=persist_cache,
        force_refresh=force_refresh,
        session=session,
        _method="GET",
    )


def fetch_gmgn_arc_opened_rank(
    *,
    limit: int = 80,
    session: Any = None,
) -> dict[str, Any]:
    """Read Arc's opened tape from GMGN's live rank route.

    Arc is served here because GMGN's public Trenches route currently returns
    an empty list for that chain even though the native Trenches UI is live.
    The rank is requested by creation time and then reshaped into the same
    completed-category envelope consumed by the dashboard.
    """
    request_limit = max(1, min(100, int(limit)))
    result = gmgn_readonly_get(
        "/v1/market/rank",
        cache_key=f"trenches:arc:opened-rank:{request_limit}",
        params={
            "chain": "arc",
            "interval": "24h",
            "limit": request_limit,
            "order_by": "creation_timestamp",
            "direction": "desc",
            "filters": list(GMGN_ARC_UPSTREAM_RANK_FILTERS),
        },
        cache_ttl_seconds=max(30.0, float(os.getenv("GMGN_TRENCHES_CACHE_TTL_SECONDS", "60") or 60)),
        session=session,
    )
    # The rank route currently adds one transport envelope around the usual
    # GMGN business payload, while older deployments/tests return that payload
    # directly. Accept both shapes without changing other read-only routes.
    inner = result.get("data")
    api_payload = (
        dict(inner)
        if isinstance(inner, Mapping)
        and "code" in inner
        and isinstance(inner.get("data"), Mapping)
        else result
    )
    code = api_payload.get("code", 0)
    if code not in (0, "0", None):
        message = str(api_payload.get("message") or api_payload.get("reason") or code)
        raise RuntimeError(f"GMGN Arc 已开盘请求失败：{message[:180]}")
    data = api_payload.get("data") if isinstance(api_payload.get("data"), Mapping) else {}
    rank_rows = data.get("rank") if isinstance(data, Mapping) else []
    completed: list[dict[str, Any]] = []
    for raw in rank_rows if isinstance(rank_rows, list) else []:
        if not isinstance(raw, Mapping) or not _flag(raw.get("launchpad_status")):
            continue
        # Arc's rank endpoint sometimes appends long-lived tokens whose
        # creation/open timestamps are both zero.  Treating that zero as the
        # poll time makes an old token look like a fresh launch (for example,
        # ARGUS appeared as “4m” despite being much older).  The new-coin tape
        # must require a real timestamp; unknown-age rows belong in market
        # discovery, not in the Trenches chronology.
        timestamp = 0
        for key in ("open_timestamp", "creation_timestamp", "created_timestamp"):
            parsed_timestamp = _number(raw.get(key))
            if parsed_timestamp and parsed_timestamp > 0:
                timestamp = int(parsed_timestamp)
                break
        if timestamp <= 0:
            continue
        mapped = dict(raw)
        mapped["created_timestamp"] = timestamp
        mapped["open_timestamp"] = timestamp
        mapped.setdefault("usd_market_cap", raw.get("market_cap"))
        mapped.setdefault("volume_24h", raw.get("volume"))
        mapped.setdefault("swaps_24h", raw.get("swaps"))
        mapped.setdefault("buys_24h", raw.get("buys"))
        mapped.setdefault("sells_24h", raw.get("sells"))
        mapped.setdefault("bundler_trader_amount_rate", raw.get("bundler_rate"))
        mapped.setdefault("twitter", raw.get("twitter_username"))
        completed.append(mapped)
    return {
        "code": 0,
        "data": {"completed": completed, "near_completion": [], "new_creation": []},
        "message": api_payload.get("message") or "success",
        "reason": api_payload.get("reason") or "",
        "_gmgnMeta": {
            **(result.get("_gmgnMeta") if isinstance(result.get("_gmgnMeta"), Mapping) else {}),
            "sourceRoute": "market-rank",
        },
    }


def fetch_gmgn_migrated_trenches(
    network: str,
    *,
    limit: int = 80,
    launchpad_platforms: tuple[str, ...] = (),
    upstream_unique_only: bool = False,
    min_market_cap_usd: float | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Fetch only GMGN's completed/graduated launchpad category."""
    network_key = str(network or "").strip().lower()
    chain = GMGN_TRENCHES_NETWORKS.get(network_key)
    if not chain:
        return {"data": {"completed": []}, "network": network_key, "unsupported": True}
    if network_key == "arc":
        return fetch_gmgn_arc_opened_rank(limit=limit, session=session)

    upstream_filters = ["offchain", "onchain", *GMGN_UPSTREAM_TRENCH_FILTERS]
    if upstream_unique_only:
        # GMGN applies this before its 60-row response ceiling.  A separate
        # broad request is still merged by the caller so the user's local
        # allowance for the first 2-3 same-artwork rows remains intact.
        upstream_filters.append("img_not_duplicate")
    section: dict[str, Any] = {
        # Match the native saved filter: retain both market origins and require
        # at least one project social channel. OG is intentionally unrestricted.
        "filters": upstream_filters,
        "launchpad_platform_v2": True,
        "limit": max(1, min(80, int(limit))),
    }
    platforms = tuple(dict.fromkeys(
        str(value or "").strip()
        for value in launchpad_platforms
        if str(value or "").strip()
    ))
    if platforms:
        # Do not keep a static platform allow-list.  The live market-rank
        # discovery lane supplies currently active platform names, including
        # platforms that GMGN's default Trenches set has not adopted yet.
        section["launchpad_platform"] = list(platforms)
    if min_market_cap_usd is not None:
        section["min_marketcap"] = max(0.0, float(min_market_cap_usd))
    request_variant = ""
    if platforms or upstream_unique_only or min_market_cap_usd is not None:
        request_variant = hashlib.sha256(json.dumps(
            {
                "platforms": platforms,
                "unique": bool(upstream_unique_only),
                "minMarketCap": section.get("min_marketcap"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")).hexdigest()[:12]
    cache_key = f"trenches:{chain}:{section['limit']}"
    if request_variant:
        cache_key = f"{cache_key}:{request_variant}"
    result = gmgn_readonly_post(
        GMGN_TRENCHES_PATH,
        cache_key=cache_key,
        params={"chain": chain},
        body={"version": "v2", "completed": section},
        cache_ttl_seconds=max(30.0, float(os.getenv("GMGN_TRENCHES_CACHE_TTL_SECONDS", "60") or 60)),
        session=session,
    )
    code = result.get("code", 0)
    if code not in (0, "0", None):
        message = str(result.get("message") or result.get("reason") or code)
        if re.search(r"unsupported|invalid\s+chain|chain\s+not", message, re.IGNORECASE):
            raise GmgnUnsupportedNetworkError(f"GMGN 当前未开放 {network_key} 战壕接口")
        raise RuntimeError(f"GMGN 战壕请求失败：{message[:180]}")
    return result


def fetch_gmgn_recent_market_rank(
    network: str,
    *,
    limit: int = 100,
    min_created: str = "",
    max_created: str = "",
    session: Any = None,
) -> dict[str, Any]:
    """Read GMGN's newest launched-token window as a Trenches supplement.

    ``/v1/trenches`` exposes only the latest completed page.  During a busy
    launch wave a token can leave that page before the next local poll, while
    GMGN's web view still shows it in the user's accumulated scroll history.
    The creation-time market rank is therefore merged with the native
    completed page *before* the local MC/social/risk profile is applied.  This
    route is a discovery supplement only: it does not relax any board filter
    and it is not restricted by OG status.
    """
    network_key = str(network or "").strip().lower()
    chain = GMGN_TRENCHES_NETWORKS.get(network_key)
    if not chain or network_key == "arc":
        return {"code": 0, "data": {"completed": []}, "network": network_key}
    request_limit = max(1, min(100, int(limit)))
    def supported_age(value: str) -> str:
        age = str(value or "").strip().lower()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hd])", age)
        if not match:
            return age
        amount = float(match.group(1))
        minutes = amount * (1440 if match.group(2) == "d" else 60)
        rendered = str(int(minutes)) if minutes.is_integer() else f"{minutes:g}"
        return f"{rendered}m"

    # GMGN accepts seconds/minutes here; hour/day suffixes are silently
    # ignored by some deployments. Normalize them before building the cache
    # key and request so busy-chain backfill windows remain disjoint.
    min_age = supported_age(min_created)
    max_age = supported_age(max_created)
    age_key = f"{min_age or 'newest'}:{max_age or 'any'}"
    params: dict[str, Any] = {
        "chain": chain,
        "interval": "24h",
        "limit": request_limit,
        "order_by": "creation_timestamp",
        "direction": "desc",
        "filters": ["has_social"],
        "min_marketcap": GMGN_TRENCH_MIN_MARKET_CAP_USD,
    }
    if min_age:
        params["min_created"] = min_age
    if max_age:
        params["max_created"] = max_age
    result = gmgn_readonly_get(
        "/v1/market/rank",
        cache_key=f"trenches:recent-rank:{chain}:{request_limit}:{age_key}",
        params=params,
        cache_ttl_seconds=max(30.0, float(os.getenv("GMGN_TRENCHES_CACHE_TTL_SECONDS", "60") or 60)),
        session=session,
    )
    inner = result.get("data")
    api_payload = (
        dict(inner)
        if isinstance(inner, Mapping)
        and "code" in inner
        and isinstance(inner.get("data"), Mapping)
        else result
    )
    code = api_payload.get("code", 0)
    if code not in (0, "0", None):
        message = str(api_payload.get("message") or api_payload.get("reason") or code)
        raise RuntimeError(f"GMGN {network_key} 最近开盘补充请求失败：{message[:180]}")
    data = api_payload.get("data") if isinstance(api_payload.get("data"), Mapping) else {}
    rank_rows = data.get("rank") if isinstance(data, Mapping) else []
    completed: list[dict[str, Any]] = []
    for raw in rank_rows if isinstance(rank_rows, list) else []:
        if not isinstance(raw, Mapping) or not _flag(raw.get("launchpad_status")):
            continue
        timestamp = 0
        for key in ("open_timestamp", "creation_timestamp", "created_timestamp"):
            parsed_timestamp = _number(raw.get(key))
            if parsed_timestamp and parsed_timestamp > 0:
                timestamp = int(parsed_timestamp)
                break
        if timestamp <= 0:
            continue
        mapped = dict(raw)
        mapped["created_timestamp"] = timestamp
        mapped["open_timestamp"] = timestamp
        mapped.setdefault("usd_market_cap", raw.get("market_cap"))
        mapped.setdefault("volume_24h", raw.get("volume"))
        mapped.setdefault("swaps_24h", raw.get("swaps"))
        mapped.setdefault("buys_24h", raw.get("buys"))
        mapped.setdefault("sells_24h", raw.get("sells"))
        mapped.setdefault("bundler_trader_amount_rate", raw.get("bundler_rate"))
        mapped.setdefault("twitter", raw.get("twitter_username"))
        completed.append(mapped)
    return {
        "code": 0,
        "data": {"completed": completed, "near_completion": [], "new_creation": []},
        "message": api_payload.get("message") or "success",
        "reason": api_payload.get("reason") or "",
        "_gmgnMeta": {
            **(result.get("_gmgnMeta") if isinstance(result.get("_gmgnMeta"), Mapping) else {}),
            "sourceRoute": "market-rank-recent-opened",
        },
    }


def fetch_gmgn_non_og_market_rank(
    network: str,
    *,
    limit: int = 100,
    session: Any = None,
) -> dict[str, Any]:
    """Backward-compatible alias for the generalized recent-open supplement."""
    return fetch_gmgn_recent_market_rank(network, limit=limit, session=session)


def normalize_gmgn_migrated_trenches(
    payload: Any,
    network: str,
    *,
    observed_at: int,
) -> list[dict[str, Any]]:
    """Map GMGN completed rows into the dashboard's immutable research schema."""
    network_key = str(network or "").strip().lower()
    gmgn_chain = GMGN_TRENCHES_NETWORKS.get(network_key)
    if not gmgn_chain:
        return []
    envelope = payload.get("data") if isinstance(payload, Mapping) else None
    data = envelope if isinstance(envelope, Mapping) else payload if isinstance(payload, Mapping) else {}
    raw_rows = data.get("completed") if isinstance(data, Mapping) else []
    if not isinstance(raw_rows, list):
        return []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_index, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            continue
        contract = _text(raw.get("address") or raw.get("token_address"), 160).strip()
        identity = contract.lower() if contract.lower().startswith("0x") else contract
        if not identity or identity in seen:
            continue
        seen.add(identity)
        created_at = _timestamp_ms(
            raw.get("open_timestamp"), raw.get("complete_timestamp"), raw.get("created_timestamp"),
            raw.get("creation_timestamp"),
        )
        symbol = _text(raw.get("symbol"), 40).strip().upper()
        twitter = _text(raw.get("twitter") or raw.get("twitter_username"), 800).strip()
        if twitter and not twitter.startswith(("http://", "https://")):
            twitter = f"https://x.com/{twitter.lstrip('/@')}"
        website = _text(raw.get("website"), 800).strip()
        telegram = _text(raw.get("telegram"), 800).strip()
        instagram = _text(raw.get("instagram"), 800).strip()
        tiktok = _text(raw.get("tiktok"), 800).strip()
        bitbucket = _text(raw.get("bitbucket"), 800).strip()
        discord = _text(raw.get("discord"), 800).strip()
        facebook = _text(raw.get("facebook"), 800).strip()
        github = _text(raw.get("github"), 800).strip()
        linkedin = _text(raw.get("linkedin"), 800).strip()
        medium = _text(raw.get("medium"), 800).strip()
        reddit = _text(raw.get("reddit"), 800).strip()
        youtube = _text(raw.get("youtube"), 800).strip()
        verify_status = _text(raw.get("verify_status"), 80).strip()
        gmgn_narrative = _gmgn_native_narrative(raw)
        x_original = _gmgn_x_original(raw, twitter)
        image_url = _web_url(raw.get("logo") or raw.get("logo_url") or raw.get("image") or raw.get("icon"))
        smart_count = _integer(raw.get("smart_degen_count"))
        kol_count = _integer(raw.get("renowned_count"))
        top10 = _percent(raw.get("top_10_holder_rate") or raw.get("top10_holder_rate"))
        bundler = _percent(raw.get("bundler_trader_amount_rate") or raw.get("bundler_rate"))
        insider = _percent(raw.get("suspected_insider_hold_rate") or raw.get("rat_trader_amount_rate"))
        creator_holding = _percent(
            raw.get("creator_balance_rate") or raw.get("creator_hold_rate") or raw.get("dev_team_hold_rate")
        )
        sniper_holding = _percent(raw.get("top70_sniper_hold_rate"))
        fresh_wallet = _percent(raw.get("fresh_wallet_rate"))
        bluechip = _percent(raw.get("bluechip_owner_percentage"))
        bot_rate = _percent(raw.get("bot_degen_rate"))
        buy_tax = _percent(raw.get("buy_tax"))
        sell_tax = _percent(raw.get("sell_tax"))
        wash_trading = _flag(raw.get("is_wash_trading"))
        wash_trading_filter = _optional_flag(raw.get("is_wash_trading"))
        honeypot_filter = _optional_flag(raw.get("is_honeypot"))
        # REST, websocket and older cached rows use several names for the
        # native ``image_dup`` signal.  Prefer an explicitly reported value;
        # the batch-level URL check below fills in the signal when GMGN did
        # not include it in a snapshot.
        nested_filter_signals = raw.get("filterSignals")
        nested_filter_signals = nested_filter_signals if isinstance(nested_filter_signals, Mapping) else {}
        image_duplicate_count = _first_number(
            raw,
            "image_dup",
            "dup_image",
            "image_duplicate_count",
            "image_dup_count",
            "imageDup",
            "imageDuplicateCount",
        )
        if image_duplicate_count is None:
            image_duplicate_count = _first_number(
                nested_filter_signals,
                "image_dup",
                "dup_image",
                "image_duplicate_count",
                "image_dup_count",
                "imageDup",
                "imageDuplicateCount",
            )
        name_duplicate_count = _first_number(
            raw,
            "name_dup",
            "name_duplicate_count",
            "nameDup",
            "nameDuplicateCount",
        )
        if name_duplicate_count is None:
            name_duplicate_count = _first_number(
                nested_filter_signals,
                "name_dup",
                "name_duplicate_count",
                "nameDup",
                "nameDuplicateCount",
            )
        is_og = _optional_flag(raw.get("is_og") if "is_og" in raw else raw.get("isOg"))
        if is_og is None:
            is_og = _optional_flag(nested_filter_signals.get("is_og") if "is_og" in nested_filter_signals else nested_filter_signals.get("isOg"))
        rat_trader_rate = _number(raw.get("rat_trader_amount_rate"))
        rat_trading_filter = _optional_flag(raw.get("is_rat_trading"))
        filter_warnings: list[str] = []
        if image_duplicate_count is not None and image_duplicate_count > GMGN_MAX_DUPLICATE_FAMILY_SIZE:
            filter_warnings.append(f"图片重复 {int(image_duplicate_count)}")
        if name_duplicate_count is not None and name_duplicate_count > GMGN_MAX_DUPLICATE_FAMILY_SIZE:
            filter_warnings.append(f"名称重复 {int(name_duplicate_count)}")
        if wash_trading_filter is True:
            filter_warnings.append("GMGN 标记疑似刷量")
        if rat_trader_rate is not None and rat_trader_rate > 0:
            rate_percent = rat_trader_rate * 100 if rat_trader_rate <= 1 else rat_trader_rate
            filter_warnings.append(f"老鼠仓占比 {rate_percent:.2f}%")
        if rat_trading_filter is True:
            filter_warnings.append("GMGN 标记老鼠仓刷量")
        if honeypot_filter is True:
            filter_warnings.append("GMGN 标记疑似蜜罐")
        socials = [value for value in (
            twitter, telegram, website, instagram, tiktok, bitbucket, discord,
            facebook, github, linkedin, medium, reddit, youtube, verify_status,
        ) if value]
        rows.append({
            "network": network_key,
            "provider": "gmgn-trenches",
            "providers": ["gmgn-trenches"],
            "contractAddress": contract,
            "poolAddress": _text(raw.get("pool_address"), 160).strip(),
            "dexId": _text(raw.get("exchange"), 80).strip(),
            "launchpad": _text(raw.get("launchpad_platform"), 80).strip(),
            "launchStage": "migrated",
            "symbol": symbol,
            "name": _text(raw.get("name") or symbol or contract, 160).strip(),
            "imageUrl": image_url,
            "firstSeenAt": int(observed_at),
            "observedAt": int(observed_at),
            "poolCreatedAt": created_at or int(observed_at),
            "tradeUrl": f"https://gmgn.ai/{gmgn_chain}/token/{contract}",
            "gmgnNarrative": gmgn_narrative,
            "gmgnNarrativeSource": "GMGN AI" if gmgn_narrative else "",
            "gmgnSourceIndex": source_index,
            "xOriginal": x_original,
            "filterWarnings": filter_warnings,
            "filterSignals": {
                "profileVersion": GMGN_TRENCH_FILTER_PROFILE_VERSION,
                "isOg": is_og,
                "nonOgException": False,
                "imageDuplicateCount": image_duplicate_count,
                "nameDuplicateCount": name_duplicate_count,
                "washTrading": wash_trading_filter,
                "ratTraderRate": rat_trader_rate,
                "ratWashTrading": rat_trading_filter,
                "honeypot": honeypot_filter,
                "openSource": _optional_flag(raw.get("open_source") if "open_source" in raw else raw.get("is_open_source")),
                "ownerRenounced": _optional_flag(raw.get("owner_renounced") if "owner_renounced" in raw else raw.get("is_renounced")),
                "burnStatus": _text(raw.get("burn_status"), 40).strip().casefold(),
                "quoteAddressType": _integer(raw.get("quote_address_type")),
                "creationTool": _text(raw.get("creation_tool"), 100).strip(),
                "socialCount": len(socials),
            },
            "narrativeContext": {
                "description": _text(raw.get("description") or raw.get("narrative") or "GMGN 战壕 · 已迁移", 1200),
                "websites": [website] if website else [],
                "socials": socials,
                "links": {
                    "twitter": twitter,
                    "telegram": telegram,
                    "website": website,
                    "instagram": instagram,
                    "tiktok": tiktok,
                    "bitbucket": bitbucket,
                    "discord": discord,
                    "facebook": facebook,
                    "github": github,
                    "linkedin": linkedin,
                    "medium": medium,
                    "reddit": reddit,
                    "youtube": youtube,
                },
            },
            "launchFacts": {
                "stage": "migrated",
                "isOg": is_og,
                "holders": _integer(raw.get("holder_count")),
                "top10Percent": top10,
                "smartMoneyHolders": smart_count,
                "kolHolders": kol_count,
                "bundlerHoldingPercent": bundler,
                "insiderPercent": insider,
                "creatorHoldingPercent": creator_holding,
                "creatorTokenStatus": _text(raw.get("creator_token_status"), 40),
                "sniperCount": _integer(raw.get("sniper_count")),
                "sniperHoldingPercent": sniper_holding,
                "freshWalletPercent": fresh_wallet,
                "bluechipOwnerPercent": bluechip,
                "botWalletPercent": bot_rate,
                "botWalletCount": _integer(raw.get("bot_degen_count")),
                "washTrading": wash_trading,
                "rugRatio": _number(raw.get("rug_ratio")),
                "honeypot": _flag(raw.get("is_honeypot")),
                "buyTaxPercent": buy_tax,
                "sellTaxPercent": sell_tax,
                "totalFeeUsd": _number(raw.get("total_fee") or raw.get("total_fee_usd")),
                "gasFeeUsd": _number(raw.get("gas_fee")),
                "dexAd": _flag(raw.get("dexscr_ad")),
                "dexUpdatedLinks": _flag(raw.get("dexscr_update_link")),
                "dexTrendingBar": _flag(raw.get("dexscr_trending_bar")),
                "dexBoostFeeUsd": _number(raw.get("dexscr_boost_fee")),
                "cto": _flag(raw.get("cto_flag")),
                "socialCount": len(socials),
                "xFollowers": _integer(raw.get("x_user_follower")),
            },
            "metrics": {
                "priceUsd": _number(raw.get("price")),
                "liquidityUsd": _number(raw.get("liquidity")),
                "fdvUsd": _number(raw.get("usd_market_cap") or raw.get("market_cap")),
                "marketCapUsd": _number(raw.get("usd_market_cap") or raw.get("market_cap")),
                "volumeH1Usd": _number(raw.get("volume_1h")),
                "volumeH24Usd": _number(raw.get("volume_24h")),
                "netBuyH24Usd": _number(raw.get("net_buy_24h")),
                "priceChangeM1": _number(raw.get("price_change_percent1m")),
                "priceChangeM5": _number(raw.get("price_change_percent5m")),
                "priceChangeH1": _number(raw.get("price_change_percent1h")),
                "transactionsH1": _integer(raw.get("swaps_1h")),
                "transactionsH24": _integer(raw.get("swaps_24h")),
                "buysH24": _integer(raw.get("buys_24h")),
                "sellsH24": _integer(raw.get("sells_24h")),
                "holders": _integer(raw.get("holder_count")),
                "top10HolderPercent": top10,
                "smartMoneyHolders": smart_count,
                "kolHolders": kol_count,
                "bundlerHoldingPercent": bundler,
                "washTrading": wash_trading,
                "totalFeeUsd": _number(raw.get("total_fee") or raw.get("total_fee_usd")),
                "hotLevel": _number(raw.get("hot_level")),
                "viewCount": _integer(raw.get("view_count") or raw.get("view_num")),
            },
        })
    # GMGN normally provides ``image_dup`` for this check, but it is absent in
    # a number of live Arc/launchpad snapshots.  When two rows in the same
    # response carry the exact same explicit artwork URL, treat them as the
    # same image family.  This mirrors the native ``img_not_duplicate`` filter
    # without hard-coding symbols or contract addresses.  Query-string cache
    # busters are ignored by ``_image_identity``.
    image_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    name_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        identity = _image_identity(row.get("imageUrl"))
        if identity:
            image_groups.setdefault((network_key, identity), []).append(row)
        # Count exact symbol/name clones independently of the artwork URL.
        # Some launchpads return a different CDN URL for the same meme, while
        # others reuse one image across a small family of related tickers.
        def _name_identity(value: Any) -> str:
            text = _text(value, 180).casefold()
            return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)
        symbol_identity = _name_identity(row.get("symbol"))
        name_identity = _name_identity(row.get("name"))
        if symbol_identity and name_identity:
            name_groups.setdefault((network_key, symbol_identity, name_identity), []).append(row)
    for grouped_rows in image_groups.values():
        if len(grouped_rows) < 2:
            continue
        duplicate_count = len(grouped_rows)
        for row in grouped_rows:
            signals = row.get("filterSignals")
            if not isinstance(signals, dict):
                continue
            reported = _number(signals.get("imageDuplicateCount"))
            if reported is None or reported < duplicate_count:
                signals["imageDuplicateCount"] = duplicate_count
            warnings = row.get("filterWarnings")
            if not isinstance(warnings, list):
                warnings = []
                row["filterWarnings"] = warnings
            warning = f"图片重复 {duplicate_count}"
            if warning not in warnings:
                warnings.append(warning)
    for grouped_rows in name_groups.values():
        if len(grouped_rows) < 2:
            continue
        duplicate_count = len(grouped_rows)
        for row in grouped_rows:
            signals = row.get("filterSignals")
            if not isinstance(signals, dict):
                continue
            reported = _number(signals.get("nameDuplicateCount"))
            if reported is None or reported < duplicate_count:
                signals["nameDuplicateCount"] = duplicate_count
            warnings = row.get("filterWarnings")
            if not isinstance(warnings, list):
                warnings = []
                row["filterWarnings"] = warnings
            warning = f"名称重复 {duplicate_count}"
            if warning not in warnings:
                warnings.append(warning)
    return annotate_gmgn_non_og_exceptions(rows)
