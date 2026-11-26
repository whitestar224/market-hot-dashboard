"""每分钟自动桥接：战壕榜新币 → 投研派送 → 分析回写 → 播报。

该常驻后台线程把原本需要外部专用聊天手动「领取→分析→回填」的链路，
补成每分钟一轮的自动交接，保证链上投研播报不断档：

    1. 扫链   —— 触发 GMGN 战壕榜增量拉取（受缓存保护窗口限制，不会重复联网）。
    2. 派送   —— 若当前无活跃批次，把待研新币打包成一个 durable 批次供聊天领取。
    3. 兜底   —— 若某批次租约到期后仍无聊天领取回填，改用内置分析器
                 analyze_fast_onchain_candidates 分析并回写，确保链路不悬空。

所有动作都幂等且有并发保护：同一 tick 内不会重复派送、重复回写。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

# 桥接循环的默认步长（秒）。可通过环境变量覆盖，便于灰度与压测。
ONCHAIN_BRIDGE_INTERVAL_SECONDS = max(
    10,
    int(float(os.getenv("ONCHAIN_BRIDGE_INTERVAL_SECONDS", "60") or "60")),
)

# 兜底分析的最大批次规模：内置分析器每次只处理少量标的，避免过长阻塞。
BRIDGE_FALLBACK_BATCH_LIMIT = max(
    1,
    int(float(os.getenv("ONCHAIN_BRIDGE_FALLBACK_BATCH_LIMIT", "8") or "8")),
)

# 兜底触发阈值：批次进入 claimed/sent 后超过该毫秒仍无回填，才启用内置分析。
# 默认 12 分钟，略小于聊天租约 20 分钟，给聊天留足领取时间。
BRIDGE_FALLBACK_STALE_MS = max(
    60_000,
    int(float(os.getenv("ONCHAIN_BRIDGE_FALLBACK_STALE_MS", "720000") or "720000")),
)

BRIDGE_LOOP_STARTED = False
BRIDGE_LOOP_LOCK = threading.Lock()

# 由 server.py 启动时注入；避免模块级 import 造成循环依赖。
_runtime: dict[str, Any] = {
    "shutdown_event": None,
    "fast_research": None,
    "analyzer": None,
    "scan": None,
}


def configure_bridge(
    *,
    shutdown_event: threading.Event,
    fast_research: Any,
    analyzer: Any,
    scan: Any,
) -> None:
    """注入运行期依赖。必须在 start_onchain_research_bridge() 之前调用。

    ``scan`` 应为无参、返回 payload 的战壕榜刷新函数
    （server.refresh_gmgn_trenches_hot_board），并交由 trigger_api_refresh
    做缓存保护窗口控制，避免每分钟都真实联网。
    """
    _runtime["shutdown_event"] = shutdown_event
    _runtime["fast_research"] = fast_research
    _runtime["analyzer"] = analyzer
    _runtime["scan"] = scan


def _stale_fallback_batches(fast_research: Any, now_ms: int) -> list[dict[str, Any]]:
    """租约到期仍无人回填的批次（claimed/sent 且超过兜底阈值）。

    仅返回仍可被 complete 的批次：状态在 claimed/sent，且确实已过期。
    正常的聊天领取/回填不受影响，因为那些批次会被及时完成。
    """
    rows = fast_research._query(
        """SELECT * FROM onchain_chatgpt_research_batches
           WHERE status IN ('claimed','sent') ORDER BY created_at LIMIT 1"""
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        claimed_at = int(row.get("claimed_at") or 0)
        # sent 状态没有 claimed_at 保护，用 created_at 兜底判断。
        anchor = claimed_at or int(row.get("created_at") or 0)
        if anchor and (now_ms - anchor) >= BRIDGE_FALLBACK_STALE_MS:
            result.append(row)
    return result


def _run_fallback_analysis(batch: dict[str, Any]) -> bool:
    """对单个过期批次执行内置分析并回写。返回是否成功回写。"""
    fast_research = _runtime["fast_research"]
    analyzer = _runtime["analyzer"]
    batch_id = batch["batch_id"]
    claim_token = batch["claim_token"]
    if not claim_token:
        return False

    item_rows = fast_research._query(
        """SELECT j.candidate_json, j.symbol, j.name
           FROM onchain_chatgpt_research_items i
           JOIN onchain_fast_jobs j ON j.key=i.job_key
           WHERE i.batch_id=? ORDER BY i.position""",
        (batch_id,),
    )
    if not item_rows:
        return False

    candidates: list[dict[str, Any]] = []
    meta: dict[str, dict[str, str]] = {}
    for item_row in item_rows[:BRIDGE_FALLBACK_BATCH_LIMIT]:
        try:
            candidate = item_row["candidate_json"]
            if isinstance(candidate, str):
                candidate = __import__("json").loads(candidate)
        except (TypeError, ValueError):
            continue
        candidates.append(candidate)
        meta[candidate.get("key", "")] = {
            "symbol": item_row.get("symbol") or candidate.get("symbol") or "",
            "name": item_row.get("name") or candidate.get("name") or "",
        }

    if not candidates:
        return False

    try:
        analyses = analyzer(candidates, lane="onchain-hourly")
    except Exception:
        # 内置分析器不可用（如 AI 未启用）时保持静默，批次留给聊天继续领取。
        return False

    if not analyses:
        return False

    # 内置分析器产出完整 analysis，直接交由 complete 回写链路消费。
    items: list[dict[str, Any]] = []
    for key, analysis in analyses.items():
        info = meta.get(key, {})
        item = dict(analysis)
        item.setdefault("key", key)
        item.setdefault("symbol", info.get("symbol", ""))
        # 兜底分析不夸大：仅当分析器明确给出正向且可展示结论时才触发播报，
        # 交给 complete_chatgpt_research_batch 内部的判定函数统一裁决。
        items.append(item)

    if not items:
        return False

    outcome = fast_research.complete_chatgpt_research_batch(
        batch_id, claim_token, items,
        raw_response=__import__("json").dumps(
            {"source": "onchain-research-bridge-fallback", "items": items},
            ensure_ascii=False,
        ),
    )
    return bool(outcome.get("ok"))


def bridge_tick_once() -> dict[str, Any]:
    """执行一轮桥接：扫链 → 派送 → 兜底。返回本轮状态摘要。"""
    fast_research = _runtime["fast_research"]
    scan = _runtime["scan"]
    now_ms = int(time.time() * 1000)

    summary: dict[str, Any] = {"scanned": False, "queued": "", "fallback": 0}

    # 1. 扫链：触发 GMGN 战壕榜增量拉取（受保护窗口限制，命中缓存则跳过联网）。
    try:
        if scan is not None:
            scan()
            summary["scanned"] = True
    except Exception:
        summary["scanned"] = False

    # 2. 派送：仅当确无活跃批次时才调 queue，避免每分钟都抢写锁。
    #    queue_chatgpt_research_batch 内部会 BEGIN IMMEDIATE 并全表扫描，
    #    在 1.2GB 数据库上与 result 回写抢锁，导致 "database is locked"。
    #    这里先做一次只读检查，有活跃批次就直接跳过，不碰写锁。
    try:
        active = fast_research._query(
            "SELECT batch_id FROM onchain_chatgpt_research_batches "
            "WHERE status IN ('pending','claimed','sent') LIMIT 1"
        )
        if not active:
            queued = fast_research.queue_chatgpt_research_batch()
            if isinstance(queued, dict):
                summary["queued"] = queued.get("status", "")
        else:
            summary["queued"] = "busy"
    except Exception:
        summary["queued"] = "error"

    # 3. 兜底：回收租约到期仍未回填的批次。
    try:
        for batch in _stale_fallback_batches(fast_research, now_ms):
            if _run_fallback_analysis(batch):
                summary["fallback"] += 1
    except Exception:
        pass

    return summary


def onchain_research_bridge_loop() -> None:
    """每分钟 tick 一次的常驻桥接循环。"""
    shutdown_event = _runtime["shutdown_event"]
    while shutdown_event is None or not shutdown_event.is_set():
        try:
            bridge_tick_once()
        except Exception:
            # 单轮失败不影响下一轮；异常细节交给服务日志。
            pass
        if shutdown_event is not None and shutdown_event.wait(ONCHAIN_BRIDGE_INTERVAL_SECONDS):
            return


def start_onchain_research_bridge() -> bool:
    """启动每分钟桥接循环（幂等）。"""
    global BRIDGE_LOOP_STARTED
    if not _runtime["fast_research"] or not _runtime["shutdown_event"]:
        return False
    with BRIDGE_LOOP_LOCK:
        if BRIDGE_LOOP_STARTED:
            return True
        BRIDGE_LOOP_STARTED = True
    threading.Thread(
        target=onchain_research_bridge_loop,
        name="onchain-research-bridge",
        daemon=True,
    ).start()
    return True
