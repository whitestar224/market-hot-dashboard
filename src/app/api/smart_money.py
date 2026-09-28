"""从 server.py 抽取的模块（Phase 3 拆分，批次 5：smart_money / 全网热点域）。

来源: server.py 第 49521-49975 行（15 个 global_hotspot_* 函数）
本文件函数体由 tools/extract_module.py 机械搬移后，仅对「留在 server.py 命名空间
里的运行时状态」做了最小等价改写（见下）。

为什么这里要「懒 import server」
--------------------------------
这 15 个函数是纯函数/单例读取函数，但它们引用的 16 个名字仍是 server.py 顶层定义
（尚未拆出）的函数或单例：
  clean_feed_text、safe_float、unique_values、read_json_cache、write_json_cache、
  event_monitor_source_rows、js_stable_key、alert_event_ms、codex_cli_chat、
  deepseek_extract_json、trigger_api_refresh、build_event_monitor_core_payload、
  event_monitor_hot_entities、news_trade_dex_search_rows、safe_error_text、
  ONCHAIN_FAST_RESEARCH（FastResearch 单例）

这些名字在原文件中定义位置各异、且 ONCHAIN_FAST_RESEARCH / write_json_cache 等
依赖更晚的初始化。若在模块顶部 `from ... import` 绑定，会因循环导入失败，或拿到
初始化前的空值。正确读法：在【函数体内】`import server`（懒加载）——这些函数只在
运行时被调用，那时 server.py 早已完整加载，`sys.modules["server"]` 就是那个唯一的、
状态实时更新的模块对象。

其余名字（锁/常量，如 GLOBAL_HOTSPOT_LOCK、GLOBAL_HOTSPOT_STATE_PATH、
DESKTOP_ALERT_MILITARY_PATTERN、SERVER_SHUTDOWN_EVENT）已搬到 app.core.state
且是【同一对象】（不重绑定），直接 `from app.core.state import` 绑定即可。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from onchain_fast_research import NEWS_TRIGGER_VERSION, promote_resonance_priority

from app.core.state import (
    DESKTOP_ALERT_MILITARY_PATTERN,
    GLOBAL_HOTSPOT_DAILY_MAX_BATCHES,
    GLOBAL_HOTSPOT_INTERVAL_SECONDS,
    GLOBAL_HOTSPOT_LOCK,
    GLOBAL_HOTSPOT_STATE_PATH,
    SERVER_SHUTDOWN_EVENT,
)


def _server():
    """懒加载 server 模块对象，用于读取仍留在 server.py 命名空间里的运行时状态。

    必须在【函数体内】调用（见模块 docstring）。模块导入期调用会因循环导入失败。

    关键坑：`python server.py` 直接运行时，模块注册名是 `__main__` 而非 `server`
    （只有 `import server` 才会注册 `sys.modules["server"]`）。因此这里先查 `server`，
    再回退 `__main__`，两条路径都能拿到同一个、状态实时更新的模块对象。
    """
    return sys.modules.get("server") or sys.modules["__main__"]


def global_hotspot_day_key(now_ms: int | None = None) -> str:
    timestamp = int(now_ms or time.time() * 1000) / 1000
    return datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def global_hotspot_snapshot(path: Path | None = None) -> dict[str, Any]:
    payload = _server().read_json_cache(path or GLOBAL_HOTSPOT_STATE_PATH)
    return {
        **payload,
        "events": [row for row in payload.get("events", []) if isinstance(row, dict)],
        "sourceRows": [row for row in payload.get("sourceRows", []) if isinstance(row, dict)],
        "candidateRows": [row for row in payload.get("candidateRows", []) if isinstance(row, dict)],
    }


def global_hotspot_claim_batch(
    *,
    path: Path | None = None,
    now_ms: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Reserve one hourly batch before any AI call; failed attempts remain counted."""
    current_ms = int(now_ms or time.time() * 1000)
    state_path = path or GLOBAL_HOTSPOT_STATE_PATH
    with GLOBAL_HOTSPOT_LOCK:
        state = global_hotspot_snapshot(state_path)
        day = global_hotspot_day_key(current_ms)
        if str(state.get("day") or "") != day:
            state["day"] = day
            state["attemptsToday"] = 0
        attempts = int(_server().safe_float(state.get("attemptsToday"), 0))
        last_attempt = int(_server().safe_float(state.get("lastAttemptAt"), 0))
        if attempts >= GLOBAL_HOTSPOT_DAILY_MAX_BATCHES:
            return False, state
        if last_attempt and current_ms - last_attempt < GLOBAL_HOTSPOT_INTERVAL_SECONDS * 1000:
            return False, state
        state.update({
            "version": 1,
            "day": day,
            "attemptsToday": attempts + 1,
            "lastAttemptAt": current_ms,
            "nextAllowedAt": current_ms + GLOBAL_HOTSPOT_INTERVAL_SECONDS * 1000,
            "running": True,
        })
        _server().write_json_cache(state_path, state)
        return True, state


def global_hotspot_public_url(value: Any) -> str:
    url = _server().clean_feed_text(value, 800).strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return ""
    return url


def global_hotspot_text_list(value: Any, *, limit: int, width: int) -> list[str]:
    rows = value if isinstance(value, list) else []
    return _server().unique_values(
        [_server().clean_feed_text(item, width) for item in rows if _server().clean_feed_text(item, width)]
    )[:limit]


def normalize_global_hotspot_event(raw: Any, *, now_ms: int | None = None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    current_ms = int(now_ms or time.time() * 1000)
    title = _server().clean_feed_text(raw.get("title"), 240)
    summary = _server().clean_feed_text(raw.get("summary") or raw.get("body"), 1200)
    thesis = _server().clean_feed_text(raw.get("memeThesis") or raw.get("meme_thesis"), 600)
    if len(title) < 4 or len(summary) < 8:
        return None
    if DESKTOP_ALERT_MILITARY_PATTERN.search(f"{title} {summary}"):
        return None
    evidence: list[dict[str, str]] = []
    for item in raw.get("evidence") if isinstance(raw.get("evidence"), list) else []:
        if not isinstance(item, dict):
            continue
        url = global_hotspot_public_url(item.get("url"))
        if not url:
            continue
        evidence.append({
            "url": url,
            "title": _server().clean_feed_text(item.get("title") or item.get("source") or urlparse(url).netloc, 180),
        })
        if len(evidence) >= 5:
            break
    if not evidence:
        return None
    entities = global_hotspot_text_list(raw.get("entityNames") or raw.get("entities"), limit=8, width=80)
    search_terms = global_hotspot_text_list(raw.get("searchTerms") or raw.get("search_terms"), limit=8, width=80)
    if not entities:
        entities = _server().event_monitor_hot_entities(title, summary)[:8]
    if not search_terms:
        search_terms = entities[:]
    if not entities or not search_terms:
        return None
    occurred_at = _server().alert_event_ms(raw.get("occurredAt") or raw.get("occurred_at")) or current_ms
    if occurred_at > current_ms + 10 * 60_000:
        occurred_at = current_ms
    if current_ms - occurred_at > 48 * 60 * 60_000:
        return None
    primary_url = evidence[0]["url"]
    identity = _server().js_stable_key("global-hotspot", primary_url, entities[0])
    return {
        "id": identity,
        "title": title,
        "summary": summary,
        "entityNames": entities,
        "searchTerms": search_terms,
        "memeThesis": thesis,
        "hotnessScore": max(0, min(100, int(_server().safe_float(raw.get("hotnessScore"), 0)))),
        "occurredAt": occurred_at,
        "capturedAt": current_ms,
        "primaryUrl": primary_url,
        "evidence": evidence,
    }


def global_hotspot_identity_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value or "").casefold())


def global_hotspot_candidate_matches(event: dict[str, Any], candidate: dict[str, Any]) -> bool:
    candidate_names = {
        global_hotspot_identity_text(candidate.get("name")),
        global_hotspot_identity_text(candidate.get("symbol")),
    } - {""}
    terms = global_hotspot_text_list(
        [*(event.get("entityNames") or []), *(event.get("searchTerms") or [])],
        limit=16,
        width=80,
    )
    for term in terms:
        normalized = global_hotspot_identity_text(term)
        if len(normalized) < 2:
            continue
        if normalized in candidate_names:
            return True
        if len(normalized) >= 4 and any(normalized in name or name in normalized for name in candidate_names if len(name) >= 3):
            return True
    return False


def global_hotspot_source_row(event: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    primary = candidates[0] if candidates else {}
    contract_note = (
        f"链上已找到同名候选 {primary.get('symbol')}，链 {primary.get('chain')}，CA {primary.get('contractAddress')}；"
        if primary else "暂未找到可核验链上候选；"
    )
    return {
        "id": event["id"],
        "sourceType": "web-hotspot",
        "source": "全网热点搜索",
        "sourceLabel": "HOT",
        "title": event["title"],
        "body": _server().clean_feed_text(
            f"全网热点事件：{event['summary']} Meme映射：{event.get('memeThesis') or '等待链上题材映射'}。"
            f"{contract_note}同名不代表事件人物或机构官方发行。",
            1800,
        ),
        "url": event["primaryUrl"],
        "timestamp": event["occurredAt"],
        "capturedAt": event["capturedAt"],
        "eventRootEntity": (event.get("entityNames") or [""])[0],
        "symbol": primary.get("symbol") or "",
        "hotnessScore": event.get("hotnessScore") or 0,
        "claimStatus": "source-reported",
        "evidence": event.get("evidence") or [],
    }


def global_hotspot_research_rows(
    events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    observed_at: int,
) -> list[dict[str, Any]]:
    event_map = {str(event.get("id") or ""): event for event in events}
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        event = event_map.get(str(candidate.get("hotspotEventId") or ""))
        if not event:
            continue
        rows.append({
            "network": _server().clean_feed_text(candidate.get("chain"), 40).casefold(),
            "contractAddress": _server().clean_feed_text(candidate.get("contractAddress"), 96),
            "symbol": _server().clean_feed_text(candidate.get("symbol"), 40),
            "name": _server().clean_feed_text(candidate.get("name") or candidate.get("symbol"), 100),
            "firstSeenAt": observed_at,
            "observedAt": observed_at,
            "providers": ["global-hotspot", "dexscreener"],
            "dexId": "dexscreener",
            "tradeUrl": _server().clean_feed_text(candidate.get("tradeUrl") or candidate.get("url"), 600),
            "metrics": {
                "liquidityUsd": _server().safe_float(candidate.get("liquidityUsd")),
                "marketCapUsd": _server().safe_float(candidate.get("marketCapUsd")),
                "volumeH1Usd": 0,
                "transactionsH1": 0,
                "volumeH24Usd": _server().safe_float(candidate.get("volume24hUsd")),
            },
            "reasons": [f"外部线索：全网热点「{event.get('title')}」的同名链上候选，身份仍需复核"],
            "researchEvidence": {
                "source": "全网热点",
                "url": event.get("primaryUrl") or "",
                "publishedAt": event.get("occurredAt") or observed_at,
                "text": f"{event.get('summary')} {event.get('memeThesis')}",
                "identityStatus": "event-name-contract-unverified",
                "identityNote": "链上合约与热点实体同名，不代表由事件人物或机构发行",
            },
        })
    return rows


def global_hotspot_prompt(existing_titles: list[str], *, now_ms: int) -> list[dict[str, str]]:
    current_time = datetime.fromtimestamp(now_ms / 1000, tz=timezone(timedelta(hours=8))).isoformat()
    return [{
        "role": "system",
        "content": (
            "你是全网热点发现研究员。只搜索公开网页，寻找最近12小时内已形成明显传播、但给出的已有新闻标题可能漏掉的"
            "人物、AI、科技、互联网、文化、动物或大众事件，并判断是否存在可识别的Meme映射。"
            "排除战争军事、纯币价波动、旧闻和无可打开证据的内容。不要因为同名就声称官方发行代币。"
            "只返回JSON：{events:[{title,summary,entityNames,searchTerms,memeThesis,hotnessScore,occurredAt,evidence:[{title,url}]}]}。"
            "最多5件；每件至少1个可打开的http/https证据链接；occurredAt用Unix毫秒；中文解释，专名可保留英文。"
        ),
    }, {
        "role": "user",
        "content": json.dumps({
            "currentChinaTime": current_time,
            "existingNewsTitles": existing_titles[:80],
            "task": "发现现有来源之外的全网热点，并给出可用于链上同名Meme搜索的实体名和关键词。",
        }, ensure_ascii=False, separators=(",", ":")),
    }]


def run_global_hotspot_batch(
    *,
    now_ms: int | None = None,
    existing_titles: list[str] | None = None,
) -> dict[str, Any]:
    current_ms = int(now_ms or time.time() * 1000)
    claimed, state = global_hotspot_claim_batch(now_ms=current_ms)
    if not claimed:
        return {**state, "ok": True, "skipped": True}
    previous = global_hotspot_snapshot()
    try:
        titles = existing_titles if isinstance(existing_titles, list) else [
            _server().clean_feed_text(row.get("title"), 220)
            for row in _server().event_monitor_source_rows()[:120]
            if isinstance(row, dict) and row.get("title")
        ]
        response = _server().codex_cli_chat(
            global_hotspot_prompt(titles, now_ms=current_ms),
            lane="global-hotspot",
            web_search=True,
            model_override="gpt-5.6-luna",
            reasoning_effort_override="minimal",
            timeout_seconds=150,
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = _server().deepseek_extract_json(content)
        events = [
            event for raw in (parsed.get("events") if isinstance(parsed.get("events"), list) else [])
            if (event := normalize_global_hotspot_event(raw, now_ms=current_ms)) is not None
        ]
        new_candidates: list[dict[str, Any]] = []
        new_sources: list[dict[str, Any]] = []
        for event in events:
            query = " ".join((event.get("searchTerms") or event.get("entityNames") or [])[:3])
            candidates = [
                {**row, "hotspotEventId": event["id"], "eventRootEntity": (event.get("entityNames") or [""])[0]}
                for row in _server().news_trade_dex_search_rows(query)
                if global_hotspot_candidate_matches(event, row)
            ][:8]
            new_candidates.extend(candidates)
            new_sources.append(global_hotspot_source_row(event, candidates))

        cutoff = current_ms - 48 * 60 * 60_000
        event_map = {
            str(event.get("id") or ""): event
            for event in previous.get("events", [])
            if int(_server().safe_float(event.get("occurredAt"), 0)) >= cutoff
        }
        event_map.update({event["id"]: event for event in events})
        source_map = {
            str(row.get("id") or ""): row
            for row in previous.get("sourceRows", [])
            if int(_server().safe_float(row.get("timestamp"), 0)) >= cutoff
        }
        source_map.update({str(row.get("id") or ""): row for row in new_sources})
        candidate_map: dict[str, dict[str, Any]] = {}
        for row in [*previous.get("candidateRows", []), *new_candidates]:
            event_id = str(row.get("hotspotEventId") or "")
            if event_id not in event_map:
                continue
            contract = _server().clean_feed_text(row.get("contractAddress"), 96)
            chain = _server().clean_feed_text(row.get("chain"), 40).casefold()
            identity = f"{event_id}:{chain}:{contract if chain == 'solana' else contract.casefold()}"
            current = candidate_map.get(identity)
            if not current or _server().safe_float(row.get("liquidityUsd")) > _server().safe_float(current.get("liquidityUsd")):
                candidate_map[identity] = row
        final_events = sorted(event_map.values(), key=lambda row: int(row.get("occurredAt") or 0), reverse=True)[:40]
        payload = {
            **state,
            "ok": True,
            "running": False,
            "lastSuccessAt": current_ms,
            "updatedAt": current_ms,
            "lastError": "",
            "provider": response.get("_provider") or "codex-cli",
            "events": final_events,
            "sourceRows": list(source_map.values())[:80],
            "candidateRows": list(candidate_map.values())[:120],
        }
        _server().write_json_cache(GLOBAL_HOTSPOT_STATE_PATH, payload)
        research_rows = global_hotspot_research_rows(final_events, payload["candidateRows"], observed_at=current_ms)
        if research_rows:
            _server().ONCHAIN_FAST_RESEARCH.buffer_ingest(research_rows)
        _server().trigger_api_refresh("event-monitor-core", _server().build_event_monitor_core_payload)
        return payload
    except Exception as exc:
        failed = {
            **previous,
            **state,
            "ok": False,
            "running": False,
            "updatedAt": current_ms,
            "lastError": _server().safe_error_text(str(exc)),
        }
        _server().write_json_cache(GLOBAL_HOTSPOT_STATE_PATH, failed)
        return failed


def global_hotspot_match_event(
    candidate: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    now_ms: int,
) -> dict[str, Any] | None:
    for event in snapshot.get("events") if isinstance(snapshot.get("events"), list) else []:
        if not isinstance(event, dict) or now_ms - int(_server().safe_float(event.get("occurredAt"), 0)) > 24 * 60 * 60_000:
            continue
        if _server().safe_float(event.get("hotnessScore"), 0) < 60:
            continue
        if global_hotspot_candidate_matches(event, candidate):
            return event
    return None


def global_hotspot_enrich_candidate(
    candidate: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    now_ms: int | None = None,
    allow_narrative_fallback: bool = False,
) -> dict[str, Any]:
    row = dict(candidate)
    if not _server().clean_feed_text(row.get("contractAddress"), 96):
        return row
    current_ms = int(now_ms or time.time() * 1000)
    event = global_hotspot_match_event(row, snapshot or global_hotspot_snapshot(), now_ms=current_ms)
    if not event:
        return row
    row["researchEvidence"] = {
        "source": "全网热点",
        "url": event.get("primaryUrl") or "",
        "publishedAt": event.get("occurredAt") or current_ms,
        "text": f"{event.get('summary')} {event.get('memeThesis')}",
        "identityStatus": "event-name-contract-unverified",
        "identityNote": "事件实体与链上名称强匹配，但不代表事件方官方发行",
    }
    row["reasons"] = list(dict.fromkeys([
        f"外部线索：全网热点「{event.get('title')}」与币名强匹配，需继续核验身份",
        *(row.get("reasons") or []),
    ]))[:5]
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    # Event/name matching is intentionally stricter than a news item carrying
    # an exact CA; this blocks generic words from promoting an unrelated clone.
    liquid = _server().safe_float(metrics.get("liquidityUsd"), 0) >= 20_000
    active = _server().safe_float(metrics.get("volumeH1Usd"), 0) >= 10_000 or _server().safe_float(metrics.get("transactionsH1"), 0) >= 50
    h1_missing = not any(_server().safe_float(metrics.get(key), 0) > 0 for key in (
        "volumeH1Usd", "transactionsH1", "buysH1", "sellsH1",
    ))
    context = row.get("narrativeContext") if isinstance(row.get("narrativeContext"), dict) else {}
    original_x = row.get("xOriginal") if isinstance(row.get("xOriginal"), dict) else {}
    has_narrative_material = bool(
        _server().clean_feed_text(row.get("gmgnNarrative"), 2400)
        or _server().clean_feed_text(context.get("description"), 500)
        or any(_server().clean_feed_text(value, 900) for value in (context.get("websites") or []))
        or any(_server().clean_feed_text(value, 900) for value in (context.get("socials") or []))
        or _server().clean_feed_text(original_x.get("url") or original_x.get("text"), 900)
    )
    narrative_fallback = bool(
        allow_narrative_fallback
        and row.get("narrativeFallbackEligible")
        and h1_missing
        and has_narrative_material
        and (
            _server().safe_float(metrics.get("volumeH24Usd"), 0) >= 20_000
            or _server().safe_float(metrics.get("transactionsH24"), 0) >= 50
        )
    )
    if narrative_fallback:
        active = True
        row["narrativeFallbackApplied"] = True
        row["reasons"] = list(dict.fromkeys([
            "1小时成交数据尚未形成：改用 GMGN 叙事资料与热点共振送入 V4.9 复核",
            *(row.get("reasons") or []),
        ]))[:5]
    if liquid and active:
        row = promote_resonance_priority(row, source="全网热点共振")
        row["newsObservedAt"] = current_ms
        row["newsSignal"] = {
            "version": NEWS_TRIGGER_VERSION,
            "tier": "news-triggered",
            "source": "全网热点",
            "title": event.get("title") or "全网热点事件",
            "url": event.get("primaryUrl") or "",
            "publishedAt": event.get("occurredAt") or current_ms,
            "observedAt": current_ms,
            "identityStatus": "event-name-contract-unverified",
            "reason": (
                "1小时成交数据尚未形成，GMGN 叙事与全网热点已共振，AI 正在核验事件与合约关系"
                if narrative_fallback
                else "全网热点与币名强匹配，已先进入精选视野，AI 正在核验事件与合约关系"
            ),
        }
    return row


def global_hotspot_monitor_loop() -> None:
    while not SERVER_SHUTDOWN_EVENT.is_set():
        try:
            payload = run_global_hotspot_batch()
            next_allowed = int(_server().safe_float(payload.get("nextAllowedAt"), 0))
            attempts_today = int(_server().safe_float(payload.get("attemptsToday"), 0))
            if attempts_today >= GLOBAL_HOTSPOT_DAILY_MAX_BATCHES:
                wait_seconds = GLOBAL_HOTSPOT_INTERVAL_SECONDS
            else:
                wait_seconds = max(
                    30,
                    min(
                        GLOBAL_HOTSPOT_INTERVAL_SECONDS,
                        (next_allowed - int(time.time() * 1000)) / 1000,
                    ),
                )
        except Exception as exc:
            print(f"Global hotspot monitor failed: {_server().safe_error_text(str(exc))}", file=sys.stderr)
            wait_seconds = GLOBAL_HOTSPOT_INTERVAL_SECONDS
        if SERVER_SHUTDOWN_EVENT.wait(wait_seconds):
            return
