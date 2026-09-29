"""从 server.py 抽取的模块（Phase 3 拆分，批次 4：ops_health 域）。

来源: server.py 第 51636-51750 行（runtime_thread_groups + health_payload）
本文件函数体由 tools/extract_module.py 机械搬移后，仅对「留在 server.py 命名空间
里的运行时状态」做了最小等价改写（见下）。

为什么这里要「懒 import server」
--------------------------------
`health_payload` 是纯读函数，但它读的若干名字是 server.py 里的【运行时可变状态】：
  - 9 个 `global` 重绑定名（SITE_ALERT_MONITOR_ACTIVE、PRICE_WATCH_MONITOR_ACTIVE、
    NEW_COIN_LOW_MONITOR_ACTIVE、NEW_COIN_LOW_ACTIVITY_SUMMARY、
    ONCHAIN_RESEARCH_INGEST_THREAD …）：它们的值由 server.py 里各种监控循环
    `global X` + 赋值来更新。若本模块用 `from app.core.state import X` 绑定，
    拿到的会是一份永不更新的副本（静默行为改变）。
  - 若干尚未拆分、仍在 server.py 顶层定义的函数/名字（auth_db、init_auth_db、
    clean_feed_text、safe_float、MONITOR_BUY、DASHBOARD_HTTP_RUNTIME、
    DASHBOARD_HTTP_MAX_ACTIVE、requests）。

正确读法：在【函数体内】`import server`（懒加载）。`health_payload` 只在 HTTP 请求时
被调用，那时 server.py 早已完整加载，`sys.modules["server"]` 里就是那个唯一的、
状态实时更新的模块对象，`server.SITE_ALERT_MONITOR_ACTIVE` 读到的就是当前值。

其余名字（锁/缓存/池/常量，如 AUTH_DB_LOCK、CACHE、PRICE_STRUCTURE_TIMEFRAME_POOL）
已搬到 app.core.state 且是【同一对象】（不重绑定），直接 `from app.core.state import`
绑定即可。
"""

from __future__ import annotations

import re
import sys
import threading
import time
from typing import Any

from app.core.config import env_flag, env_value, is_production_mode
from app.core.state import (
    API_REFRESH_POOL,
    CACHE,
    CHAIN_ECOSYSTEM_MONITOR,
    LOGO_CACHE,
    MARKET_SOURCE_POOL,
    NEWS_TRADE_DISCOVERY_CACHE,
    NEWS_TRADE_SECURITY_CACHE,
    NEW_COIN_LOW_ITEMS,
    NEW_COIN_LOW_LOCK,
    NEW_COIN_LOW_MONITOR_WORKERS,
    ONCHAIN_RESEARCH_INGEST_PENDING,
    ONCHAIN_RESEARCH_INGEST_PENDING_LIMIT,
    PERSIST_CACHE_DIR,
    PRICE_STRUCTURE_ENTRY_DAY_TIMEFRAME_POOL,
    PRICE_STRUCTURE_EVENT_CONTEXTS,
    PRICE_STRUCTURE_EVENT_CONTEXT_LOCK,
    PRICE_STRUCTURE_MOMENTUM_CACHE,
    PRICE_STRUCTURE_MONITOR_WORKERS,
    PRICE_STRUCTURE_ONCHAIN_CANDLE_CACHE,
    PRICE_STRUCTURE_ONCHAIN_POOL_CACHE,
    PRICE_STRUCTURE_PREARM_FORECAST_MINUTES,
    PRICE_STRUCTURE_PREARM_INTERVAL_SECONDS,
    PRICE_STRUCTURE_PREARM_STATUS,
    PRICE_STRUCTURE_PREARM_STATUS_LOCK,
    PRICE_STRUCTURE_STRATEGY_VERSION,
    PRICE_STRUCTURE_TIMEFRAME_POOL,
    THREAD_STACK_SIZE_BYTES,
)


def _server():
    """懒加载 server 模块对象，用于读取仍留在 server.py 命名空间里的运行时状态。

    必须在【函数体内】调用（见模块 docstring）。模块导入期调用会因循环导入失败。

    关键坑：`python server.py` 直接运行时，模块注册名是 `__main__` 而非 `server`
    （只有 `import server` 才会注册 `sys.modules["server"]`）。因此这里先查 `server`，
    再回退 `__main__`，两条路径都能拿到同一个、状态实时更新的模块对象。
    """
    return sys.modules.get("server") or sys.modules["__main__"]


def runtime_thread_groups() -> dict[str, int]:
    groups: dict[str, int] = {}
    for thread in threading.enumerate():
        name = _server().clean_feed_text(thread.name, 100) or "unnamed"
        if "process_request_thread" in name:
            name = "http-request"
        else:
            name = re.sub(r"_\d+$", "", name)
        groups[name] = groups.get(name, 0) + 1
    return dict(sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:24])


def health_payload() -> dict[str, Any]:
    server = _server()
    checks: dict[str, Any] = {}
    ok = True
    try:
        server.init_auth_db()
        # SELECT 1 is a pure read; SQLite WAL is concurrency-safe for readers, so
        # do NOT take AUTH_DB_LOCK here. Taking the global lock on the liveness
        # probe made /api/health hang whenever a background writer held the lock
        # for a long time, which defeated the whole point of a health endpoint.
        with server.auth_db() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["database"] = {"ok": True}
    except Exception as exc:
        ok = False
        checks["database"] = {"ok": False, "error": str(exc)[:180]}

    try:
        PERSIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        probe = PERSIST_CACHE_DIR / ".healthcheck"
        probe.write_text(str(time.time()), encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["runtimeCache"] = {"ok": True, "path": str(PERSIST_CACHE_DIR)}
    except Exception as exc:
        ok = False
        checks["runtimeCache"] = {"ok": False, "error": str(exc)[:180]}

    with PRICE_STRUCTURE_PREARM_STATUS_LOCK:
        prearm_status = dict(PRICE_STRUCTURE_PREARM_STATUS)
    with PRICE_STRUCTURE_EVENT_CONTEXT_LOCK:
        structure_event_context_count = len(PRICE_STRUCTURE_EVENT_CONTEXTS)
    with NEW_COIN_LOW_LOCK:
        new_coin_low_scanned = len(NEW_COIN_LOW_ITEMS)
    new_coin_low_inventory = len(server.NEW_COIN_LOW_INVENTORY_CACHE[1]) if server.NEW_COIN_LOW_INVENTORY_CACHE else 0
    return {
        "ok": ok,
        "service": "xingyunshe-market-hot-dashboard",
        "env": env_value("XINGYUN_ENV", "development") or "development",
        "time": int(time.time() * 1000),
        "version": env_value("XINGYUN_VERSION", "local"),
        "checks": checks,
        "monitors": {
            "siteAlerts": server.SITE_ALERT_MONITOR_ACTIVE,
            "wechatAuth": server.WECHAT_AUTH_MONITOR_ACTIVE,
            "desktopAlerts": not env_flag("XINGYUN_DISABLE_DESKTOP_ALERT", default=is_production_mode()),
            "priceWatch": server.PRICE_WATCH_MONITOR_ACTIVE,
            "structureSignals": server.PRICE_STRUCTURE_MONITOR_ACTIVE,
            "structureMonitorMode": "parallel-priority",
            "structureMonitorWorkers": PRICE_STRUCTURE_MONITOR_WORKERS,
            "structureStrategyVersion": PRICE_STRUCTURE_STRATEGY_VERSION,
            "structurePrearm": server.PRICE_STRUCTURE_PREARM_MONITOR_ACTIVE,
            "structurePrearmIntervalSeconds": PRICE_STRUCTURE_PREARM_INTERVAL_SECONDS,
            "structurePrearmForecastMinutes": PRICE_STRUCTURE_PREARM_FORECAST_MINUTES,
            "structurePrearmStatus": prearm_status,
            "structureEventContexts": structure_event_context_count,
            "newCoinLowStructure": server.NEW_COIN_LOW_MONITOR_ACTIVE,
            "newCoinLowMonitorMode": "parallel-priority",
            "newCoinLowMonitorWorkers": NEW_COIN_LOW_MONITOR_WORKERS,
            "newCoinLowInventory": new_coin_low_inventory,
            "newCoinLowScanned": new_coin_low_scanned,
            "newCoinLowInactiveExcluded": int(server.safe_float(server.NEW_COIN_LOW_ACTIVITY_SUMMARY.get("excluded"), 0)),
            "newCoinLowActivityUnavailable": int(server.safe_float(server.NEW_COIN_LOW_ACTIVITY_SUMMARY.get("unavailable"), 0)),
            "newCoinLowActivityUnavailableExcluded": int(server.safe_float(
                server.NEW_COIN_LOW_ACTIVITY_SUMMARY.get("unavailableExcluded"), 0
            )),
        },
        "runtime": {
            "activeThreads": threading.active_count(),
            "threadGroups": runtime_thread_groups(),
            "threadStackKb": THREAD_STACK_SIZE_BYTES // 1024 if THREAD_STACK_SIZE_BYTES else 0,
            "sharedPools": True,
            "timeframeWorkers": int(getattr(PRICE_STRUCTURE_TIMEFRAME_POOL, "_max_workers", 0)),
            "entryDayTimeframeWorkers": int(
                getattr(PRICE_STRUCTURE_ENTRY_DAY_TIMEFRAME_POOL, "_max_workers", 0)
            ),
            "marketSourceWorkers": int(getattr(MARKET_SOURCE_POOL, "_max_workers", 0)),
            "apiRefreshWorkers": int(getattr(API_REFRESH_POOL, "_max_workers", 0)),
            "chainEcosystemWorkers": CHAIN_ECOSYSTEM_MONITOR.worker_count,
            "httpRequests": {
                "active": int(server.DASHBOARD_HTTP_RUNTIME.get("active", 0)),
                "peak": int(server.DASHBOARD_HTTP_RUNTIME.get("peak", 0)),
                "limit": server.DASHBOARD_HTTP_MAX_ACTIVE,
            },
            "outboundRequests": server.requests.stats(),
            "onchainResearchIngest": {
                "pending": len(ONCHAIN_RESEARCH_INGEST_PENDING),
                "active": bool(
                    server.ONCHAIN_RESEARCH_INGEST_THREAD
                    and server.ONCHAIN_RESEARCH_INGEST_THREAD.is_alive()
                ),
                "limit": ONCHAIN_RESEARCH_INGEST_PENDING_LIMIT,
            },
            "monitorIdentityCache": {
                "cached": len(server.MONITOR_BUY.identities.records),
                "known": len(server.MONITOR_BUY.identities.known_keys),
                "limit": server.MONITOR_BUY.identities.cache_size,
            },
            "cacheEntries": {
                "market": len(CACHE),
                "onchainPools": len(PRICE_STRUCTURE_ONCHAIN_POOL_CACHE),
                "onchainCandles": len(PRICE_STRUCTURE_ONCHAIN_CANDLE_CACHE),
                "momentum": len(PRICE_STRUCTURE_MOMENTUM_CACHE),
                "newsDiscovery": len(NEWS_TRADE_DISCOVERY_CACHE),
                "newsSecurity": len(NEWS_TRADE_SECURITY_CACHE),
                "logos": len(LOGO_CACHE),
            },
        },
    }
