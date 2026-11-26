"""Pump.fun claim-signal monitor (GMGN signal_type 18).

signal_type 18 fires when a Pump.fun creator claims (closes) their token —
a graduation confirmation at best, a rug precursor at worst. The feed only
alerts when the claimed token matches an asset this project already tracks
(structure watch roster or the day's trench intake), so random claims of
untracked tokens stay silent.

Feed contract (consumed by server.site_alert_feeds):
- pump_claim_feed_payload() -> raw signal rows via gmgn-cli
- parse_pump_claim_events(payload) -> alert event dicts

CLI invocation mirrors gmgn_local_trench_analyzer._run_gmgn: node on the
dist entry directly (the .cmd shim is a Windows hang hazard under
subprocess timeouts).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from typing import Any

_GMGN_INDEX_JS = r"C:\Users\ZhuanZ1\AppData\Roaming\npm\node_modules\gmgn-cli\dist\index.js"
SIGNAL_TYPE_CLAIM = 18
FETCH_TTL_S = 75
BACKOFF_AFTER_FAILS = 3
BACKOFF_COOLDOWN_S = 600
CALL_TIMEOUT_S = 25

_FAIL: dict[str, Any] = {"count": 0, "until": 0.0}
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_LOCK = threading.Lock()


def pump_claim_enabled() -> bool:
    return (os.getenv("PUMP_CLAIM_ENABLED", "1") or "").strip().lower() not in {"0", "false", "no"}


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


def _run_gmgn(args: list[str], *, timeout: float = CALL_TIMEOUT_S) -> Any:
    """Run gmgn-cli via node directly; return parsed JSON or None."""
    node = os.getenv("GMGN_NODE_BIN", r"C:\Users\ZhuanZ1\.workbuddy\binaries\node\versions\22.22.2-3\node.exe")
    cmd = [node, _GMGN_INDEX_JS, *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    output = (result.stdout or "").strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def fetch_pump_claim_signals(*, chain: str = "sol") -> list[dict[str, Any]]:
    """signal_type 18 rows: {token_address, signal_type, trigger_at, trigger_mc,
    signal_times, signal_times_by_type, data{symbol, name, launchpad,
    creator_token_status, ...}}."""
    if not pump_claim_enabled() or _backed_off():
        return []
    cache_key = f"claims:{chain}"
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] < FETCH_TTL_S:
            return cached[1]
    data = _run_gmgn(["market", "signal", "--chain", chain, "--signal-type", str(SIGNAL_TYPE_CLAIM)])
    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
    elif isinstance(data, dict):
        raw = data.get("list") or data.get("items") or []
        rows = [row for row in raw if isinstance(row, dict)]
    if rows:
        _record_success()
        with _CACHE_LOCK:
            _CACHE[cache_key] = (now, rows)
        return rows
    # Empty payload is ambiguous (no fresh claims vs API failure): only treat
    # it as failure when the CLI produced nothing parseable at all.
    if data is None:
        _record_failure()
    return cached[1] if cached else []


# ---------------------------------------------------------------------------
# Watch-roster matching: only tracked assets may raise the risk popup
# ---------------------------------------------------------------------------

def _symbol_key(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"[-_/]?(USDT|USDC|USD|SOL)$", "", text)
    text = text.removesuffix("-SWAP").removesuffix("PERP")
    return re.sub(r"[^A-Z0-9.]", "", text)[:32]


def tracked_symbol_keys() -> set[str]:
    """Symbols the project currently tracks: structure watch roster + the
    day's trench intake history (read-only, lock-free consumers only)."""
    keys: set[str] = set()
    try:  # structure watch roster (30s TTL cache, SWR — never blocks)
        from server import price_structure_watch_rows  # noqa: PLC0415

        for row in price_structure_watch_rows():
            if not isinstance(row, dict):
                continue
            key = _symbol_key(row.get("symbol") or row.get("asset"))
            if key:
                keys.add(key)
    except Exception:
        pass
    try:  # today's trench intake (fresh coins the project already received)
        from server import NEW_COIN_LOW_LISTING_HISTORY_PATH, read_json_cache  # noqa: PLC0415

        items = (read_json_cache(NEW_COIN_LOW_LISTING_HISTORY_PATH).get("items") or [])
        day_cutoff = time.time() * 1000 - 48 * 3600 * 1000
        for row in items:
            if not isinstance(row, dict):
                continue
            stamp = float(row.get("newCoinFirstListedAt") or row.get("newCoinListedAt") or 0)
            if stamp and stamp >= day_cutoff:
                key = _symbol_key(row.get("symbol"))
                if key:
                    keys.add(key)
    except Exception:
        pass
    return keys


def _first_claim(row: dict[str, Any]) -> bool:
    by_type = row.get("signal_times_by_type")
    if isinstance(by_type, dict):
        try:
            return int(by_type.get(str(SIGNAL_TYPE_CLAIM), by_type.get(SIGNAL_TYPE_CLAIM, 0))) <= 1
        except (TypeError, ValueError):
            pass
    try:
        return int(row.get("signal_times") or 0) <= 1
    except (TypeError, ValueError):
        return True


def parse_pump_claim_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("disabled"):
        return []
    tracked = tracked_symbol_keys()
    if not tracked:
        return []
    events: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        symbol_key = _symbol_key(data.get("symbol") or row.get("token_address"))
        if symbol_key not in tracked:
            continue
        if not _first_claim(row):
            continue
        address = str(row.get("token_address") or data.get("address") or "").strip()
        symbol = str(data.get("symbol") or symbol_key).strip()
        launchpad = str(data.get("launchpad") or data.get("launchpad_platform") or "Pump.fun").strip()
        status = str(data.get("creator_token_status") or "claim").strip()
        market_cap = row.get("trigger_mc") or row.get("market_cap") or 0
        try:
            mc_text = f"${float(market_cap)/1_000_000:.2f}M" if float(market_cap) >= 1_000_000 else f"${float(market_cap)/1_000:.0f}K"
        except (TypeError, ValueError):
            mc_text = "--"
        trigger_at = int(row.get("trigger_at") or 0)
        events.append({
            "key": f"pump-claim:{symbol_key}:{address}:{trigger_at}".lower(),
            "kind": "Pump 认领预警",
            "source": launchpad,
            "sourceLabel": "PC",
            "sourceType": "pump-claim",
            "title": f"Pump 认领预警：{symbol}（{launchpad}）",
            "body": (
                f"状态 {status} · 触发市值 {mc_text} · "
                "项目方认领/关闭代币：可能是毕业确认，也可能是 rug 前兆，请核查持仓与流动性。"
            ),
            "url": f"https://gmgn.ai/sol/token/{address}" if address else "https://gmgn.ai",
            "time": trigger_at * 1000 if 0 < trigger_at < 10_000_000_000 else int(trigger_at or time.time() * 1000),
            "priority": "风控预警",
            "queuePriority": 95,
            "speech": f"Pump 认领预警，{symbol} 项目方认领代币，请核查持仓。",
        })
    return events


def pump_claim_feed_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {"fetchedAt": int(time.time() * 1000), "rows": [], "disabled": False}
    if not pump_claim_enabled():
        payload["disabled"] = True
        return payload
    payload["rows"] = fetch_pump_claim_signals()
    return payload
