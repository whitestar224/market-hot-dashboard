"""Conservative attention-window policy over existing evidence; no I/O or AI calls.

Publication time and distinct catalysts, never polling/analysis time, renew a window.
The thresholds are display-policy defaults, not calibrated return predictions.
"""
import hashlib
import math
import re

HOUR = 3_600_000
WINDOW_MS = 6 * HOUR
FRESH_EVIDENCE_MS = HOUR
STAGES = {"ignition", "rising", "peak", "cooling", "expired", "unknown"}


def numeric(value, default=0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def attention_evidence(topic):
    """Collapse copied titles across URLs; a later repost cannot reset its age."""
    related = topic.get("relatedNews") if isinstance(topic.get("relatedNews"), list) else []
    rows = [topic, topic.get("latestCatalyst") or {}, *related[:24]]
    unique = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()[:240]
        stamp = int(numeric(row.get("timestamp")))
        if not title or stamp <= 0:
            continue
        normalized = re.sub(r"[\W_]+", "", title.casefold())
        if not normalized:
            continue
        key = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        if key not in unique or stamp < unique[key]["timestamp"]:
            unique[key] = {"id": key, "title": title, "timestamp": stamp}
    return sorted(unique.values(), key=lambda row: row["timestamp"], reverse=True)[:24]


def attention_window(topic, analysis, *, now_ms, opportunity):
    result = {"version": 1, "eligible": False, "stage": "unknown", "priority": 0,
              "catalystAt": 0, "expiresAt": 0, "reason": "尚无可确认的当前机会"}

    def reject(reason):
        return {**result, "eligible": False, "reason": reason}

    if not opportunity or topic.get("aiAnalysisStatus") != "ready" or not topic.get("sourceActive", True):
        return reject("尚未通过有效标的与 AI 机会门槛")
    evidence = [row for row in attention_evidence(topic) if row["timestamp"] <= now_ms + 60_000]
    if not evidence:
        return reject("缺少可核对的原始催化时间")
    stage = str(analysis.get("attentionStage") or "unknown")
    stage = stage if stage in STAGES else "unknown"
    result["stage"] = stage
    if stage == "unknown" or not analysis.get("catalystEvidenceId"):
        return reject("AI 尚未确认情绪阶段或本轮催化")
    if stage in {"peak", "cooling", "expired"}:
        return reject("AI 判断已过较好的情绪窗口，暂不进入事件流")
    late = str(analysis.get("actionHint") or "") + " " + str(analysis.get("thesis") or "")
    if re.search(r"仅复盘|已错过|情绪退潮|热度衰退|高位派发|等待回落|不宜追|不建议追|不追高", late):
        return reject("当前应对已转为观察或回避追高")
    key = str(analysis.get("catalystEvidenceId") or "")
    # Only an explicitly cited input catalyst can anchor a current opportunity.
    anchor = next((row for row in evidence if row["id"] == key), None)
    if not anchor:
        return reject("AI 所引用的催化不在本次证据中")
    catalyst_at = min(now_ms, anchor["timestamp"])
    result.update(catalystAt=catalyst_at, evidenceKey=anchor["id"])
    latest = max(row["timestamp"] for row in evidence)
    expires_at = min(catalyst_at + WINDOW_MS, latest + FRESH_EVIDENCE_MS)
    result["expiresAt"] = expires_at
    if now_ms >= expires_at:
        return reject("催化或近期传播证据已陈旧，等待真正的新变化")
    fresh = [row for row in evidence if 0 <= now_ms - row["timestamp"] < HOUR]
    prior = [row for row in evidence if HOUR <= now_ms - row["timestamp"] < WINDOW_MS]
    growth = len(fresh) / max(.2, len(prior) / 5)
    heat = min(100, max(0, numeric(topic.get("eventHeatScore"))))
    strength = min(100, max(0, numeric(analysis.get("narrativeStrength"))))
    confidence = min(100, max(0, numeric(analysis.get("confidence"))))
    if not str(analysis.get("catalyst") or "").strip() or not str(analysis.get("thesis") or "").strip():
        return reject("缺少具体催化与叙事依据")
    # High gains alone neither qualify nor disqualify a token. Freshness,
    # growing attention and a concrete AI thesis must concur.
    rising = len(fresh) >= 2 and growth >= 1.3 and heat >= 60 and strength >= 60
    ignition = (stage == "ignition" and bool(key) and now_ms - catalyst_at < 20 * 60_000
                and bool(fresh) and heat >= 65 and strength >= 75 and confidence >= 75)
    if not (rising or ignition):
        return reject("尚未同时确认新催化、热度与正在扩散的情绪")
    candidates = [row for row in (topic.get("memeCandidates") or []) if isinstance(row, dict)]
    if isinstance(topic.get("memeOpportunity"), dict):
        candidates.append(topic["memeOpportunity"])
    primary = str(analysis.get("primarySymbol") or next(iter(analysis.get("symbols") or []), "")).casefold()
    for candidate in candidates:
        if str(candidate.get("symbol") or "").casefold() != primary:
            continue
        security = candidate.get("security") if isinstance(candidate.get("security"), dict) else {}
        if security.get("hardBlocked") or any(flag in {"疑似貔貅盘", "合约高风险"} for flag in candidate.get("riskFlags") or []):
            return reject("对应标的命中已有合约风险拦截")
        change = candidate.get("priceChange") if isinstance(candidate.get("priceChange"), dict) else {}
        day = numeric(candidate.get("change24hPercent", change.get("h24")))
        if day <= -30:
            # A revival can qualify, but an old narrative + a high AI score is
            # not evidence of renewed demand after a severe selloff.
            hour = numeric(candidate.get("change1hPercent", change.get("h1")))
            minute = numeric(candidate.get("change5mPercent", change.get("m5")))
            if hour <= 0 or minute <= 0:
                return reject("大幅回落后尚无短周期重新走强证据，不作为当下机会")
    result.update(eligible=True, stage=stage if stage != "unknown" else "rising",
                  priority=round(heat * .35 + strength * .25 + confidence * .2 + min(20, len(fresh) * 5)),
                  reason="新催化正在聚焦注意力" if ignition else "近期独立内容增加，催化仍处于扩散窗口")
    return result
