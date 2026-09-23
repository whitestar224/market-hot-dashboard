"""Deterministic helpers for the full on-chain research framework (V4.9).

The scanner uses this module for two separate ledgers:

* opportunity / leader discovery, which decides whether a candidate deserves
  deeper research or a research alert;
* audit / execution risk, which must never be hidden by a strong narrative.

The AI may enrich the snapshot, but these helpers deliberately remain stdlib
only so every discovered contract receives a stable fact layer first.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


FRAMEWORK_VERSION = "xmind-v4.9-hotspot-derived-ca-three-ledgers-1"
FRAMEWORK_SOURCE_SHA256 = "6CDCCB939C0A6E691D378F4E48C6A5BD275003012A9D15416ED0ADC003A2A456"
VALID_STAGES = {"S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "SX"}
VALID_ATTENTION_STATES = {f"A{index}" for index in range(9)}
VALID_TRANSITION_TRIGGERS = {
    "TR_ATTN_JUMP", "TR_PRIMARY_EVENT", "TR_MAPPING_SWARM", "TR_BUYER_RESPONSE",
    "TR_LEADER_CONCENTRATION", "TR_LEADER_ROTATION", "TR_DORMANT_REACTIVATION", "TR_RISK_FLIP",
}
VALID_POTENTIAL_TIERS = {"leader", "golden-dog", "watch", "none"}
VALID_PERMISSIONS = {"ALLOW", "CAUTION", "BLOCK", "UNKNOWN"}
VALID_LONG_TERM_STAGES = {f"LT{index}" for index in range(7)}
VALID_SURVIVAL_LABELS = {"strong-hold", "divergence", "decay", "dormant", "reactivating", "unknown"}
VALID_PERSON_CATALYST_STATUSES = {"none", "watch", "confirmed", "ambiguous", "rejected"}
VALID_MARKET_MAINLINE_STATUSES = {"active", "uncertain", "none"}
VALID_MARKET_MAINLINE_PHASES = {
    "emerging", "accelerating", "consensus", "crowded", "rotating", "fading", "unclear",
}
VALID_MARKET_MAINLINE_RELATIONS = {
    "core-leader", "core-member", "branch-expansion", "catch-up", "independent-catalyst",
    "counter-trend", "theme-rub", "uncertain",
}
VALID_HOTSPOT_RELATIONS = {
    "official", "community-recognized", "independent-hotspot", "parody",
    "copy", "impersonation", "unrelated", "uncertain",
}
VALID_HOTSPOT_PRIORITIES = {"P0", "P1", "P2", "P3", "NONE"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _score(value: Any, maximum: int = 100) -> int:
    return max(0, min(maximum, int(round(_number(value)))))


def _text(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


def _list(value: Any, *, limit: int = 8, item_limit: int = 240) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else ([value] if value else [])
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _text(item, item_limit)
        marker = text.casefold()
        if not text or marker in seen:
            continue
        seen.add(marker)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _asset_class(row: Mapping[str, Any]) -> str:
    explicit = _text(row.get("assetClass"), 60).lower()
    if explicit:
        return explicit
    return "contract-token" if _text(row.get("contractAddress"), 160) else "non-ca-event"


def _source_claim(row: Mapping[str, Any]) -> str:
    context = _mapping(row.get("narrativeContext"))
    evidence = _mapping(row.get("researchEvidence"))
    for value in (
        row.get("gmgnNarrative"),
        context.get("description"),
        evidence.get("content"),
        evidence.get("title"),
        row.get("name"),
    ):
        text = _text(value, 800)
        if text:
            return text
    return ""


def _stage(row: Mapping[str, Any]) -> str:
    metrics = _mapping(row.get("metrics"))
    address = _text(row.get("contractAddress"), 160)
    liquidity = _number(metrics.get("liquidityUsd"))
    volume_h1 = _number(metrics.get("volumeH1Usd"))
    buys_h1 = int(_number(metrics.get("buysH1")))
    buyers_m5 = int(_number(metrics.get("buyersM5")))
    tx_h1 = int(_number(metrics.get("transactionsH1")))
    price_h1 = abs(_number(metrics.get("priceChangeH1")))
    evidence = _mapping(row.get("researchEvidence"))
    context = _mapping(row.get("narrativeContext"))

    stage = "S2" if address else "S1"
    if liquidity > 0 or row.get("tradeUrl") or row.get("poolAddress"):
        stage = "S3"
    if buys_h1 > 0 or tx_h1 > 0:
        stage = "S4"
    distributed = bool(
        evidence.get("identityStatus")
        or evidence.get("source")
        or context.get("socials")
        or context.get("websites")
        or len(row.get("providers") or []) > 1
    )
    if distributed and stage in {"S3", "S4"}:
        stage = "S5"
    accelerating = buyers_m5 >= 10 or tx_h1 >= 80 or volume_h1 >= 50_000 or price_h1 >= 20
    if accelerating and stage in {"S4", "S5"}:
        stage = "S6"
    return stage


def _audit(row: Mapping[str, Any]) -> dict[str, Any]:
    metrics = _mapping(row.get("metrics"))
    wallet = _mapping(row.get("walletProfile"))
    address = _text(row.get("contractAddress"), 160)
    liquidity = _number(metrics.get("liquidityUsd"))
    top10 = _number(wallet.get("top10Percent"), -1)
    coverage = bool(wallet.get("coverage"))
    independent = int(_number(wallet.get("independentHolders")))
    flags: list[str] = []
    security = _mapping(row.get("securityAudit"))
    fatal_reason = _text(
        row.get("fatalExecutionRisk")
        or security.get("fatalReason")
        or security.get("confirmedHoneypotReason")
        or security.get("confirmedTransferBlockReason"),
        300,
    )

    if not address:
        return {
            "auditStatus": "fatal",
            "executionPermission": "BLOCK",
            "riskFlags": ["缺少可核验合约地址"],
            "hardBlockReason": "缺少可执行合约地址",
            "unknownIsSafe": False,
        }
    if liquidity <= 0:
        flags.append("未确认可用流动性")
    elif liquidity < 3_000:
        flags.append("流动性过低")
    if top10 >= 80:
        flags.append("前十大持仓集中度过高")
    if not coverage:
        flags.append("持仓与资金根未完整审计")

    if fatal_reason:
        flags.insert(0, fatal_reason)
        permission = "BLOCK"
        status = "fatal"
    elif liquidity < 1_000 or top10 >= 95:
        permission = "CAUTION"
        status = "critical"
    elif liquidity >= 25_000 and coverage and independent >= 3 and top10 < 70:
        permission = "ALLOW"
        status = "checked"
    elif liquidity > 0 and (coverage or independent > 0):
        permission = "CAUTION"
        status = "partial"
    else:
        permission = "UNKNOWN"
        status = "unknown"
    return {
        "auditStatus": status,
        "executionPermission": permission,
        "riskFlags": flags[:8],
        "hardBlockReason": fatal_reason if permission == "BLOCK" else "",
        "unknownIsSafe": False,
    }


def _attention_state(row: Mapping[str, Any], stage: str) -> str:
    """Infer the highest observable V4.9 attention state without inventing facts."""
    metrics = _mapping(row.get("metrics"))
    evidence = _mapping(row.get("researchEvidence"))
    context = _mapping(row.get("narrativeContext"))
    wallet = _mapping(row.get("walletProfile"))
    providers = _list(row.get("providers") or row.get("provider"), limit=12, item_limit=80)
    source_claim = _source_claim(row)
    active = bool(
        _number(metrics.get("volumeH1Usd")) >= 3_000
        or _number(metrics.get("transactionsH1")) >= 8
        or _number(metrics.get("buyersM5")) >= 3
    )
    explicit_reactivation = bool(
        row.get("reactivationObservedAt")
        or row.get("dormantReactivation")
        or _text(row.get("attentionTransitionTrigger"), 50).upper() == "TR_DORMANT_REACTIVATION"
    )
    if explicit_reactivation and active and source_claim:
        return "A8"
    if (
        _text(row.get("sameSymbolRole"), 40) == "leader-candidate"
        or (stage == "S6" and int(_number(wallet.get("independentHolders"))) >= 3 and len(providers) >= 2)
    ):
        return "A7"
    if stage in {"S4", "S5", "S6", "S7", "S8"} and active:
        return "A6"
    if _text(row.get("contractAddress"), 160):
        return "A5"
    channel_count = len(providers) + len(context.get("socials") or [])
    if channel_count >= 3:
        return "A4"
    if len(providers) >= 2 or (evidence.get("source") and context.get("socials")):
        return "A3"
    if evidence.get("source") or providers:
        return "A2"
    if source_claim:
        return "A1"
    return "A0"


def _transition_trigger(attention_state: str, previous_state: str, row: Mapping[str, Any]) -> str:
    if attention_state == "A8":
        return "TR_DORMANT_REACTIVATION"
    if _text(row.get("leaderRotation"), 80):
        return "TR_LEADER_ROTATION"
    if attention_state == "A7":
        return "TR_LEADER_CONCENTRATION"
    if attention_state == "A6":
        return "TR_BUYER_RESPONSE"
    if attention_state == "A5":
        return "TR_MAPPING_SWARM" if int(_number(row.get("sameSymbolContractCount"), 1)) > 1 else "TR_PRIMARY_EVENT"
    if attention_state in {"A3", "A4"}:
        return "TR_ATTN_JUMP"
    if attention_state in {"A1", "A2"}:
        return "TR_PRIMARY_EVENT"
    if previous_state and previous_state != attention_state:
        return "TR_RISK_FLIP"
    return ""


def _long_term_stage(row: Mapping[str, Any], attention_state: str) -> str:
    """Infer only what the observed facts support; later AI reviews may advance it."""
    metrics = _mapping(row.get("metrics"))
    created_at = int(_number(row.get("poolCreatedAt") or row.get("firstSeenAt")))
    observed_at = int(_number(row.get("observedAt")))
    age_hours = max(0.0, (observed_at - created_at) / 3_600_000) if created_at and observed_at else 0.0
    active = bool(_number(metrics.get("volumeH1Usd")) >= 3_000 or _number(metrics.get("transactionsH1")) >= 8)
    if row.get("thesisBroken"):
        return "LT6"
    if row.get("reactivationObservedAt") or attention_state == "A8":
        return "LT4"
    if age_hours <= 1:
        return "LT0"
    if age_hours <= 24:
        return "LT1"
    if active:
        return "LT2"
    return "LT3"


def _survival_label(row: Mapping[str, Any], long_term_stage: str) -> str:
    metrics = _mapping(row.get("metrics"))
    volume_h1 = _number(metrics.get("volumeH1Usd"))
    tx_h1 = int(_number(metrics.get("transactionsH1")))
    buys_h1 = int(_number(metrics.get("buysH1")))
    sells_h1 = int(_number(metrics.get("sellsH1")))
    liquidity = _number(metrics.get("liquidityUsd"))
    if long_term_stage == "LT4":
        return "reactivating"
    if long_term_stage == "LT3":
        return "dormant"
    if liquidity <= 0 or (volume_h1 <= 0 and tx_h1 <= 1):
        return "unknown"
    if buys_h1 >= max(5, sells_h1) and volume_h1 >= 10_000 and liquidity >= 10_000:
        return "strong-hold"
    if buys_h1 and sells_h1 > buys_h1 * 1.5:
        return "decay"
    return "divergence"


def _person_catalyst_snapshot(row: Mapping[str, Any], observed_at: int) -> dict[str, Any]:
    """Keep person/official activity as evidence, never as automatic endorsement."""
    signal = _mapping(row.get("personSignal"))
    if not signal:
        return {
            "status": "none", "sourceKind": "", "sourceTier": "", "person": "", "handle": "",
            "role": "", "action": "", "identityStatus": "", "identityAmbiguous": False,
            "semanticVersion": "", "semanticConfidence": 0.0, "semanticReason": "",
            "postUrl": "", "publishedAt": 0, "ageMinutes": 0.0, "authorText": "", "quoteText": "",
        }
    published_at = int(_number(signal.get("publishedAt")))
    if 0 < published_at < 10_000_000_000:
        published_at *= 1000
    semantic_version = _text(signal.get("semanticDecisionVersion"), 100)
    semantic_related = signal.get("semanticRelated") is True
    ambiguous = bool(signal.get("identityAmbiguous"))
    status = (
        "ambiguous" if ambiguous
        else "confirmed" if semantic_related and semantic_version
        else "rejected" if signal.get("semanticRelated") is False and semantic_version
        else "watch"
    )
    age_minutes = max(0.0, (observed_at - published_at) / 60_000) if observed_at and published_at else 0.0
    return {
        "status": status,
        "sourceKind": _text(signal.get("sourceCategory"), 40),
        "sourceTier": _text(signal.get("sourceTier"), 40),
        "person": _text(signal.get("personName"), 100),
        "handle": _text(signal.get("personHandle"), 80),
        "role": _text(signal.get("personRole"), 120),
        "action": _text(signal.get("actionType") or signal.get("actionLabel"), 40),
        "identityStatus": _text(signal.get("identityStatus"), 100),
        "identityAmbiguous": ambiguous,
        "semanticVersion": semantic_version,
        "semanticConfidence": max(0.0, min(1.0, _number(signal.get("semanticConfidence")))),
        "semanticReason": _text(signal.get("semanticReason"), 400),
        "postUrl": _text(signal.get("postUrl"), 900),
        "publishedAt": published_at,
        "ageMinutes": round(age_minutes, 2),
        "authorText": _text(signal.get("postText"), 1200),
        "quoteText": _text(signal.get("quoteText"), 900),
    }


def _market_mainline_snapshot(row: Mapping[str, Any], observed_at: int) -> dict[str, Any]:
    """Carry batch-level market context without inventing a theme from one token."""
    raw = _mapping(row.get("marketMainline"))
    status = _text(raw.get("status"), 24).lower()
    if status not in VALID_MARKET_MAINLINE_STATUSES:
        status = "uncertain"
    phase = _text(raw.get("phase"), 24).lower().replace("_", "-")
    if phase not in VALID_MARKET_MAINLINE_PHASES:
        phase = "unclear"
    relation = _text(raw.get("candidateRelation"), 40).lower().replace("_", "-")
    if relation not in VALID_MARKET_MAINLINE_RELATIONS:
        relation = "uncertain"
    return {
        "status": status,
        "asOf": int(_number(raw.get("asOf") or observed_at)),
        "primaryThemes": _list(raw.get("primaryThemes"), limit=3, item_limit=160),
        "phase": phase,
        "leaders": _list(raw.get("leaders"), limit=8, item_limit=180),
        "capitalAttention": _text(raw.get("capitalAttention"), 600),
        "evidence": _list(raw.get("evidence"), limit=8, item_limit=300),
        "candidateRelation": relation,
        "relationReason": _text(
            raw.get("relationReason") or "等待跨资产、资金、事件与注意力证据完成批次主线判断",
            600,
        ),
        "nextTrigger": _text(raw.get("nextTrigger"), 400),
        "invalidation": _text(raw.get("invalidation"), 400),
    }


def _hotspot_opportunity_snapshot(row: Mapping[str, Any], attention_state: str) -> dict[str, Any]:
    """Keep official identity, hotspot opportunity and execution as separate ledgers."""
    raw = _mapping(row.get("hotspotOpportunity"))
    signal = _mapping(row.get("newsSignal"))
    evidence = _mapping(row.get("researchEvidence"))
    relation = _text(raw.get("relation") or row.get("hotspotRelation"), 40).lower().replace("_", "-")
    identity_status = _text(evidence.get("identityStatus"), 100).lower()
    if relation not in VALID_HOTSPOT_RELATIONS:
        relation = (
            "official" if "official" in identity_status
            else "independent-hotspot" if signal or row.get("resonancePriority")
            else "uncertain"
        )
    override = bool(raw.get("override") or row.get("hotspotOverride") or _number(row.get("resonancePriority")) >= 90)
    priority = _text(raw.get("priority") or row.get("hotspotPriority"), 12).upper()
    if priority not in VALID_HOTSPOT_PRIORITIES:
        priority = "P1" if override else "P2" if relation not in {"unrelated", "uncertain"} else "NONE"
    mapping = _mapping(raw.get("mappingFit"))
    return {
        "eventId": _text(raw.get("eventId") or signal.get("eventId") or row.get("eventId"), 300),
        "eventName": _text(raw.get("eventName") or signal.get("eventName") or signal.get("title"), 240),
        "relation": relation,
        "officialClaimStatus": _text(raw.get("officialClaimStatus") or identity_status or "unconfirmed", 120),
        "officialDenialStatus": _text(raw.get("officialDenialStatus"), 160),
        "falseOfficialClaim": bool(raw.get("falseOfficialClaim") or row.get("falseOfficialClaim")),
        "attentionState": _text(raw.get("attentionState") or attention_state, 8).upper(),
        "attentionVelocity": _score(raw.get("attentionVelocity")),
        "crossSourceCount": int(_number(raw.get("crossSourceCount"))),
        "crossPlatformCount": int(_number(raw.get("crossPlatformCount"))),
        "mappingFit": {
            "name": _score(mapping.get("name")),
            "visual": _score(mapping.get("visual")),
            "semantic": _score(mapping.get("semantic")),
            "time": _score(mapping.get("time")),
            "culture": _score(mapping.get("culture")),
        },
        "candidateCountSameEvent": max(0, int(_number(raw.get("candidateCountSameEvent") or row.get("sameSymbolContractCount")))),
        "currentLeader": _text(raw.get("currentLeader"), 240),
        "priority": priority,
        "override": override,
        "identityConclusion": _text(raw.get("identityConclusion") or "官方性仅作身份标签，仍待来源核验", 500),
        "opportunityConclusion": _text(raw.get("opportunityConclusion") or "等待热点强度、映射贴合与市场承接共同确认", 600),
        "executionConclusion": _text(raw.get("executionConclusion") or "执行许可独立审计，热点强度不构成安全证明", 500),
        "nextTrigger": _text(raw.get("nextTrigger"), 400),
        "invalidation": _text(raw.get("invalidation"), 400),
    }


def build_candidate_framework_snapshot(
    row: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
    observed_at: int | None = None,
) -> dict[str, Any]:
    """Build the deterministic V4.9 fact layer while retaining earlier foundations."""
    metrics = _mapping(row.get("metrics"))
    evidence = _mapping(row.get("researchEvidence"))
    context = _mapping(row.get("narrativeContext"))
    wallet = _mapping(row.get("walletProfile"))
    quote = _mapping(row.get("quoteAsset"))
    stage = _stage(row)
    attention_state = _attention_state(row, stage)
    previous_stage = _text((previous or {}).get("currentStage"), 8)
    previous_attention_state = _text((previous or {}).get("attentionState"), 8).upper()
    if previous_attention_state not in VALID_ATTENTION_STATES:
        previous_attention_state = ""
    now = int(_number(observed_at or row.get("observedAt")))
    source_claim = _source_claim(row)
    providers = _list(row.get("providers") or row.get("provider"), limit=12, item_limit=80)
    identity_status = _text(evidence.get("identityStatus"), 100)
    source_of_truth = _text(evidence.get("source") or row.get("provider"), 80)
    if not source_of_truth and providers:
        source_of_truth = providers[0]
    audit = _audit(row)
    transition_trigger = _transition_trigger(attention_state, previous_attention_state, row)
    event_id = _text(
        row.get("eventId")
        or _mapping(row.get("newsSignal")).get("eventId")
        or evidence.get("url")
        or f"{row.get('network') or 'unknown'}:{row.get('symbol') or row.get('name') or 'event'}:{now}",
        300,
    )
    candidate_locator = _text(row.get("contractAddress"), 160)
    leader_role = _text(row.get("sameSymbolRole"), 60) or "unranked-candidate"
    hard_block_reason = _text(audit.get("hardBlockReason"), 500)
    long_term_stage = _long_term_stage(row, attention_state)
    survival_label = _survival_label(row, long_term_stage)
    family_role = (
        "First Real Leader" if leader_role == "leader-candidate" and attention_state == "A7"
        else "Fork/Clone" if leader_role == "challenger"
        else "First Tradable" if row.get("poolAddress") or row.get("tradeUrl")
        else "Canonical Source Candidate" if source_claim
        else "Mechanism Origin Candidate"
    )
    created_at = int(_number(row.get("poolCreatedAt") or row.get("firstSeenAt")))
    age_hours = max(0.0, (now - created_at) / 3_600_000) if created_at and now else 0.0
    expansion_window = "0-1h" if age_hours <= 1 else "1-6h" if age_hours <= 6 else "6-24h" if age_hours <= 24 else "24h+"
    person_catalyst = _person_catalyst_snapshot(row, now)
    market_mainline = _market_mainline_snapshot(row, now)
    hotspot_opportunity = _hotspot_opportunity_snapshot(row, attention_state)

    return {
        "version": FRAMEWORK_VERSION,
        "sourceFrameworkSha256": FRAMEWORK_SOURCE_SHA256,
        "observedAt": now,
        "assetKey": f"{_text(row.get('network'), 40)}:{_text(row.get('contractAddress'), 160)}",
        "protocolKey": _text(row.get("dexId") or row.get("protocol"), 100),
        "projectKey": _text(row.get("projectKey") or row.get("name") or row.get("symbol"), 160),
        "chainContext": {
            "network": _text(row.get("network"), 40),
            "archetype": _text(row.get("chainArchetype") or "public-chain-contract-market", 100),
            "capitalFormation": _text(row.get("capitalFormation") or "DEX liquidity + attention", 140),
        },
        "assetIdentity": {
            "assetClass": _asset_class(row),
            "locator": _text(row.get("contractAddress"), 160),
            "canonicality": identity_status or ("contract-observed" if row.get("contractAddress") else "unknown"),
            "underlyingOrigin": _text(row.get("underlyingOrigin") or "native-contract", 100),
            "representation": _text(row.get("representation") or "fungible-token", 100),
            "reserveStatus": _text(row.get("reserveStatus") or "not-applicable-or-unknown", 100),
        },
        "quoteAsset": {
            "symbol": _text(quote.get("symbol"), 40).upper(),
            "name": _text(quote.get("name"), 120),
            "address": _text(quote.get("address"), 160),
            "type": _text(quote.get("type") or "pool-quote", 80),
            "liquidityUsd": _number(metrics.get("liquidityUsd")),
        },
        "earliestVenue": {
            "dexId": _text(row.get("dexId"), 80),
            "poolAddress": _text(row.get("poolAddress"), 160),
            "tradeUrl": _text(row.get("tradeUrl"), 800),
            "poolCreatedAt": int(_number(row.get("poolCreatedAt"))),
            "firstSeenAt": int(_number(row.get("firstSeenAt"))),
        },
        "currentStage": stage,
        "previousStage": previous_stage if previous_stage in VALID_STAGES else "",
        "stateChanged": bool(previous_stage and previous_stage != stage),
        "transitionAt": now if previous_stage and previous_stage != stage else 0,
        "attentionState": attention_state,
        "attentionTransition": {
            "previousState": previous_attention_state,
            "newState": attention_state,
            "changed": bool(previous_attention_state and previous_attention_state != attention_state),
            "transitionAt": now if previous_attention_state and previous_attention_state != attention_state else 0,
            "trigger": transition_trigger,
            "evidence": source_claim,
            "confidence": _score(row.get("confidence")),
            "observer": source_of_truth or "scanner",
        },
        "objectLedger": {
            "eventId": event_id,
            "attentionUnit": _text(row.get("minAttentionUnit") or row.get("symbol") or row.get("name"), 160),
            "narrativeCluster": _text(row.get("narrativeCluster") or row.get("candidateType") or "unclassified", 120),
            "assetCandidate": candidate_locator,
            "leaderRelation": leader_role,
            "executionObject": candidate_locator if candidate_locator else "",
        },
        "firstness": {
            "firstObservedAt": int(_number(row.get("firstSeenAt") or row.get("poolCreatedAt") or now)),
            "firstTradableAt": int(_number(row.get("poolCreatedAt") or row.get("firstSeenAt"))),
            "firstOfficialClaim": identity_status,
            "ledgerStatus": "partial" if row.get("contractAddress") else "unknown",
        },
        "blueBoxP0": {
            "qualifies": bool("official" in identity_status.lower() and row.get("contractAddress")),
            "status": "confirmed" if "official" in identity_status.lower() else "unverified",
            "reason": identity_status or "尚无官方首个资产证据",
        },
        "attention": {
            "minimumUnit": _text(row.get("minAttentionUnit") or row.get("symbol") or row.get("name"), 160),
            "emotionalHook": _text(row.get("emotionalHook"), 300),
            "attentionHook": _text(row.get("attentionHook") or source_claim, 400),
            "sourceClaim": source_claim,
            "websites": _list(context.get("websites"), limit=4, item_limit=800),
            "socials": _list(context.get("socials"), limit=6, item_limit=800),
        },
        "hotspotCatalyst": {
            "identityStatus": identity_status,
            "source": source_of_truth,
            "claim": _text(evidence.get("content") or evidence.get("title"), 500),
        },
        "personCatalyst": person_catalyst,
        "marketMainline": market_mainline,
        "hotspotOpportunity": hotspot_opportunity,
        "paidBuyers": {
            "buysM5": int(_number(metrics.get("buysM5"))),
            "buyersM5": int(_number(metrics.get("buyersM5"))),
            "buysH1": int(_number(metrics.get("buysH1"))),
            "buyersH1": int(_number(metrics.get("buyersH1"))),
            "independentHolders": int(_number(wallet.get("independentHolders"))),
            "classification": _text(wallet.get("classification"), 120),
        },
        "acceleration": {
            "volumeM5Usd": _number(metrics.get("volumeM5Usd")),
            "volumeH1Usd": _number(metrics.get("volumeH1Usd")),
            "transactionsH1": int(_number(metrics.get("transactionsH1"))),
            "priceChangeM5": _number(metrics.get("priceChangeM5")),
            "priceChangeH1": _number(metrics.get("priceChangeH1")),
        },
        "distribution": {
            "providers": providers,
            "channelCount": len(providers) + len(context.get("socials") or []),
            "inherited": bool(evidence.get("source") or row.get("gmgnNarrativeSource")),
        },
        "mechanism": {
            "tax": _text(row.get("taxMechanism"), 180),
            "vault": _text(row.get("vaultMechanism"), 180),
            "reward": _text(row.get("rewardMechanism"), 180),
            "bridge": _text(row.get("bridgeMechanism"), 180),
            "redemption": _text(row.get("redemptionMechanism"), 180),
        },
        "identityTimeSupplyAudit": {
            "identity": identity_status or "contract-observed",
            "time": "pool-time-observed" if row.get("poolCreatedAt") else "unknown",
            "supply": _text(row.get("supplyAudit") or "unknown", 160),
        },
        "actionWindow": {
            "status": "active-research" if stage in {"S5", "S6"} else "observe",
            "reason": "来源/分发与交易加速度开始共振" if stage == "S6" else "等待下一状态迁移",
        },
        "narrativeRadar": {
            "story": source_claim,
            "minimumUnit": _text(row.get("minAttentionUnit") or row.get("symbol") or row.get("name"), 160),
            "attentionState": attention_state,
            "transitionReason": transition_trigger,
            "preAssetWatch": bool(attention_state in {"A3", "A4"} and not candidate_locator),
        },
        "leaderRadar": {
            "candidateCount": max(1, int(_number(row.get("sameSymbolContractCount"), 1))),
            "currentRole": leader_role,
            "currentLeader": candidate_locator if leader_role == "leader-candidate" else "",
            "shortWindow": "1m/5m/15m/1h",
            "longWindow": "6h/24h/3d/7d",
            "rotationAllowed": True,
        },
        "metaFamily": {
            "canonicalSource": source_of_truth,
            "familyRole": family_role,
            "expansionWindow": expansion_window,
            "candidateCount": max(1, int(_number(row.get("sameSymbolContractCount"), 1))),
            "currentLeader": candidate_locator if leader_role == "leader-candidate" else "",
        },
        "survival": {
            "label": survival_label,
            "windows": "1h/3h/6h/24h",
            "marketCapRetention": _text(row.get("marketCapRetention") or "待形成历史序列", 120),
            "incrementalVolume": _number(metrics.get("volumeH1Usd")),
            "newBuyerProxy": int(_number(metrics.get("buyersH1") or metrics.get("buyersM5"))),
            "liquidityUsd": _number(metrics.get("liquidityUsd")),
            "mechanismStrength": _score(row.get("mechanismStrength")),
            "carrierStrength": _score(row.get("carrierStrength") or row.get("selectedScore")),
            "leaderDurability": _text(row.get("leaderDurability") or "待跨窗口验证", 160),
        },
        "thesisMemory": {
            "coreThesis": source_claim,
            "terminalVision": _text(row.get("terminalVision"), 400),
            "milestones": _list(row.get("thesisMilestones"), limit=5, item_limit=240),
            "tokenValueCapture": _text(row.get("tokenValueCapture") or "待验证", 300),
            "invalidation": _text(row.get("thesisInvalidation") or hard_block_reason or "买方、流动性与叙事同时衰减", 300),
            "reactivationConditions": _list(row.get("reactivationConditions"), limit=5, item_limit=240),
        },
        "longTermStage": long_term_stage,
        "lifelines": {
            "project": _text(row.get("projectLifeline") or ("已出现来源主张" if source_claim else "待验证"), 240),
            "narrative": _text(row.get("narrativeLifeline") or f"注意力 {attention_state}", 240),
            "token": _text(row.get("tokenLifeline") or ("已有可交易CA" if candidate_locator else "尚无可交易映射"), 240),
            "liquidity": _text(row.get("liquidityLifeline") or f"流动性 ${_number(metrics.get('liquidityUsd')):,.0f}", 240),
        },
        "reactivationEvidence": _list(row.get("reactivationEvidence"), limit=6, item_limit=240),
        "ledgers": {
            "narrativeDiscovery": source_claim or "尚无可核验叙事来源",
            "attentionTransition": f"{previous_attention_state or '未建档'} → {attention_state}",
            "mappingFit": "已观察到链上CA，映射仍需来源核验" if candidate_locator else "尚无链上资产映射",
            "leaderElection": leader_role,
            "execution": audit.get("executionPermission") or "UNKNOWN",
            "riskConfidence": audit.get("auditStatus") or "unknown",
        },
        "researchRetention": {
            "policy": "KEEP",
            "reason": "风险只限制执行，不删除事件、映射或候选研究记录",
        },
        "audit": {**audit, "hardBlockReason": hard_block_reason},
        "sourceOfTruth": source_of_truth,
        "uncertainty": _list(row.get("uncertainty") or audit.get("riskFlags"), limit=8),
    }


def _has_source_backed_identity(row: Mapping[str, Any]) -> bool:
    evidence = _mapping(row.get("researchEvidence"))
    status = _text(evidence.get("identityStatus"), 100).lower()
    content = _text(evidence.get("content") or evidence.get("title"), 600)
    source = _text(evidence.get("source"), 80)
    if "contract" in status or "exact" in status or status in {"ca-match", "x-ca-explicit"}:
        return True
    return bool(source and content and _text(row.get("contractAddress"), 160) in content)


def promote_framework_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    """Promote source-backed emerging narratives into full-framework review.

    This is a research-queue promotion, not a buy decision. Thin/non-tradable
    pools still remain gated by minimum observable activity and the risk ledger.
    """
    result = deepcopy(dict(row))
    metrics = _mapping(result.get("metrics"))
    liquidity = _number(metrics.get("liquidityUsd"))
    volume_h1 = _number(metrics.get("volumeH1Usd"))
    tx_h1 = int(_number(metrics.get("transactionsH1")))
    activity_ok = liquidity >= 5_000 and (volume_h1 >= 3_000 or tx_h1 >= 8)
    source_backed = _has_source_backed_identity(result)
    blocked = _mapping(result.get("frameworkSnapshot")).get("audit", {}).get("auditStatus") == "fatal"
    if source_backed and activity_ok and not blocked and result.get("decision") not in {"selected", "shortlisted"}:
        result["decision"] = "shortlisted"
        result["selectedScore"] = max(60, _score(result.get("selectedScore")))
        reasons = _list(result.get("reasons"), limit=8, item_limit=240)
        reasons.append("V4.9 来源确证候选：进入热点机会、人物催化、Meta 扩散、生存率与复燃复核")
        result["reasons"] = _list(reasons, limit=8, item_limit=240)
        result["frameworkPromoted"] = True
    else:
        result["frameworkPromoted"] = False
    return result


def normalize_framework_assessment(value: Any) -> dict[str, Any]:
    """Normalize the model's V4.9 hotspot, mainline, person, Meta and long-term assessment."""
    raw = _mapping(value)
    tier = _text(raw.get("potentialTier"), 30).lower().replace("_", "-")
    if tier not in VALID_POTENTIAL_TIERS:
        tier = "none"
    permission = _text(raw.get("executionPermission"), 20).upper()
    if permission not in VALID_PERMISSIONS:
        permission = "UNKNOWN"
    stage = _text(raw.get("currentStage"), 8).upper()
    if stage not in VALID_STAGES:
        stage = ""
    novelty = _mapping(raw.get("novelty"))
    attention_state = _text(raw.get("attentionState"), 8).upper()
    if attention_state not in VALID_ATTENTION_STATES:
        attention_state = ""
    previous_attention_state = _text(raw.get("previousAttentionState"), 8).upper()
    if previous_attention_state not in VALID_ATTENTION_STATES:
        previous_attention_state = ""
    transition_trigger = _text(raw.get("transitionTrigger"), 40).upper()
    if transition_trigger not in VALID_TRANSITION_TRIGGERS:
        transition_trigger = ""
    long_term_stage = _text(raw.get("longTermStage"), 8).upper()
    if long_term_stage not in VALID_LONG_TERM_STAGES:
        long_term_stage = ""
    survival_label = _text(raw.get("survivalLabel"), 40).lower().replace("_", "-")
    if survival_label not in VALID_SURVIVAL_LABELS:
        survival_label = "unknown"
    thesis_memory = _mapping(raw.get("thesisMemory"))
    lifelines = _mapping(raw.get("lifelines"))
    person_catalyst = _mapping(raw.get("personCatalyst"))
    person_status = _text(person_catalyst.get("status"), 30).lower()
    if person_status not in VALID_PERSON_CATALYST_STATUSES:
        person_status = ""
    market_mainline = _mapping(raw.get("marketMainline"))
    hotspot = _mapping(raw.get("hotspotOpportunity"))
    hotspot_relation = _text(hotspot.get("relation"), 40).lower().replace("_", "-")
    if hotspot_relation not in VALID_HOTSPOT_RELATIONS:
        hotspot_relation = "uncertain"
    hotspot_priority = _text(hotspot.get("priority"), 12).upper()
    if hotspot_priority not in VALID_HOTSPOT_PRIORITIES:
        hotspot_priority = "NONE"
    mapping_fit = _mapping(hotspot.get("mappingFit"))
    mainline_status = _text(market_mainline.get("status"), 24).lower()
    if mainline_status not in VALID_MARKET_MAINLINE_STATUSES:
        mainline_status = ""
    mainline_phase = _text(market_mainline.get("phase"), 24).lower().replace("_", "-")
    if mainline_phase not in VALID_MARKET_MAINLINE_PHASES:
        mainline_phase = ""
    candidate_relation = _text(market_mainline.get("candidateRelation"), 40).lower().replace("_", "-")
    if candidate_relation not in VALID_MARKET_MAINLINE_RELATIONS:
        candidate_relation = ""
    return {
        "version": FRAMEWORK_VERSION,
        "potentialTier": tier,
        "candidatePath": _text(raw.get("candidatePath"), 120),
        "currentStage": stage,
        "stateTransition": _text(raw.get("stateTransition"), 240),
        "eventId": _text(raw.get("eventId"), 300),
        "narrativeId": _text(raw.get("narrativeId"), 200),
        "attentionState": attention_state,
        "previousAttentionState": previous_attention_state,
        "attentionTransition": _text(raw.get("attentionTransition"), 500),
        "transitionTrigger": transition_trigger,
        "narrativeDiscovery": _text(raw.get("narrativeDiscovery"), 700),
        "mappingFit": _text(raw.get("mappingFit"), 700),
        "leaderElection": _text(raw.get("leaderElection"), 700),
        "candidateSet": _list(raw.get("candidateSet"), limit=12, item_limit=240),
        "currentLeader": _text(raw.get("currentLeader"), 240),
        "leaderRelation": _text(raw.get("leaderRelation"), 500),
        "dormantReactivation": _text(raw.get("dormantReactivation"), 500),
        "metaFamily": _text(raw.get("metaFamily"), 700),
        "metaRole": _text(raw.get("metaRole"), 120),
        "metaExpansion": _text(raw.get("metaExpansion"), 700),
        "survivalLabel": survival_label,
        "survivalAssessment": _text(raw.get("survivalAssessment"), 700),
        "mechanismStrength": _score(raw.get("mechanismStrength")),
        "carrierStrength": _score(raw.get("carrierStrength")),
        "longTermStage": long_term_stage,
        "thesisMemory": {
            "coreThesis": _text(thesis_memory.get("coreThesis"), 600),
            "terminalVision": _text(thesis_memory.get("terminalVision"), 500),
            "milestones": _list(thesis_memory.get("milestones"), limit=5, item_limit=240),
            "tokenValueCapture": _text(thesis_memory.get("tokenValueCapture"), 500),
            "invalidation": _text(thesis_memory.get("invalidation"), 500),
            "reactivationConditions": _list(thesis_memory.get("reactivationConditions"), limit=5, item_limit=240),
        },
        "lifelines": {
            "project": _text(lifelines.get("project"), 400),
            "narrative": _text(lifelines.get("narrative"), 400),
            "token": _text(lifelines.get("token"), 400),
            "liquidity": _text(lifelines.get("liquidity"), 400),
        },
        "reactivationEvidence": _list(raw.get("reactivationEvidence"), limit=6, item_limit=240),
        "personCatalyst": {
            "status": person_status,
            "sourceImpact": _score(person_catalyst.get("sourceImpact")),
            "actionStrength": _score(person_catalyst.get("actionStrength")),
            "semanticRelation": _text(person_catalyst.get("semanticRelation"), 500),
            "identityMapping": _text(person_catalyst.get("identityMapping"), 500),
            "marketImpact": _text(person_catalyst.get("marketImpact"), 600),
            "evidence": _text(person_catalyst.get("evidence"), 700),
            "nextTrigger": _text(person_catalyst.get("nextTrigger"), 400),
            "invalidation": _text(person_catalyst.get("invalidation"), 400),
        },
        "marketMainline": {
            "status": mainline_status,
            "asOf": int(_number(market_mainline.get("asOf"))),
            "primaryThemes": _list(market_mainline.get("primaryThemes"), limit=3, item_limit=160),
            "phase": mainline_phase,
            "leaders": _list(market_mainline.get("leaders"), limit=8, item_limit=180),
            "capitalAttention": _text(market_mainline.get("capitalAttention"), 600),
            "evidence": _list(market_mainline.get("evidence"), limit=8, item_limit=300),
            "candidateRelation": candidate_relation,
            "relationReason": _text(market_mainline.get("relationReason"), 600),
            "nextTrigger": _text(market_mainline.get("nextTrigger"), 400),
            "invalidation": _text(market_mainline.get("invalidation"), 400),
        },
        "hotspotOpportunity": {
            "eventId": _text(hotspot.get("eventId"), 300),
            "eventName": _text(hotspot.get("eventName"), 240),
            "relation": hotspot_relation,
            "officialClaimStatus": _text(hotspot.get("officialClaimStatus"), 120),
            "officialDenialStatus": _text(hotspot.get("officialDenialStatus"), 160),
            "falseOfficialClaim": bool(hotspot.get("falseOfficialClaim")),
            "attentionState": _text(hotspot.get("attentionState"), 8).upper(),
            "attentionVelocity": _score(hotspot.get("attentionVelocity")),
            "crossSourceCount": max(0, int(_number(hotspot.get("crossSourceCount")))),
            "crossPlatformCount": max(0, int(_number(hotspot.get("crossPlatformCount")))),
            "mappingFit": {key: _score(mapping_fit.get(key)) for key in ("name", "visual", "semantic", "time", "culture")},
            "candidateCountSameEvent": max(0, int(_number(hotspot.get("candidateCountSameEvent")))),
            "currentLeader": _text(hotspot.get("currentLeader"), 240),
            "priority": hotspot_priority,
            "override": bool(hotspot.get("override")),
            "identityConclusion": _text(hotspot.get("identityConclusion"), 500),
            "opportunityConclusion": _text(hotspot.get("opportunityConclusion"), 600),
            "executionConclusion": _text(hotspot.get("executionConclusion"), 500),
            "nextTrigger": _text(hotspot.get("nextTrigger"), 400),
            "invalidation": _text(hotspot.get("invalidation"), 400),
        },
        "deploySource": _text(raw.get("deploySource"), 300),
        "chainContext": _text(raw.get("chainContext"), 500),
        "assetIdentity": _text(raw.get("assetIdentity"), 500),
        "minAttentionUnit": _text(raw.get("minAttentionUnit"), 240),
        "emotionalHook": _text(raw.get("emotionalHook"), 300),
        "attentionHook": _text(raw.get("attentionHook"), 300),
        "narrativeType": _text(raw.get("narrativeType"), 120),
        "novelty": {
            "asset": _score(novelty.get("asset")),
            "protocol": _score(novelty.get("protocol")),
            "mechanism": _score(novelty.get("mechanism")),
            "gameplay": _score(novelty.get("gameplay")),
        },
        "firstness": _text(raw.get("firstness"), 500),
        "distribution": _text(raw.get("distribution"), 500),
        "paidBuyers": _text(raw.get("paidBuyers"), 500),
        "acceleration": _text(raw.get("acceleration"), 500),
        "smartMoney": _text(raw.get("smartMoney"), 500),
        "quoteMigration": _text(raw.get("quoteMigration"), 400),
        "mechanism": _text(raw.get("mechanism"), 500),
        "historicalAnalogues": _list(raw.get("historicalAnalogues"), limit=5, item_limit=100),
        "falsifiers": _list(raw.get("falsifiers"), limit=6, item_limit=240),
        "p0OfficialAsset": _text(raw.get("p0OfficialAsset"), 400),
        "crossRegimeHandoff": _text(raw.get("crossRegimeHandoff"), 400),
        "motherCaseSignals": _text(raw.get("motherCaseSignals"), 400),
        "identityTimeSupplyAudit": _text(raw.get("identityTimeSupplyAudit"), 500),
        "actionWindow": _text(raw.get("actionWindow"), 400),
        "narrativeDiscoveryScore": _score(raw.get("narrativeDiscoveryScore", raw.get("discoveryScore"))),
        "attentionTransitionScore": _score(raw.get("attentionTransitionScore")),
        "mappingFitScore": _score(raw.get("mappingFitScore")),
        "leaderElectionScore": _score(raw.get("leaderElectionScore", raw.get("leaderScore"))),
        "confidenceScore": _score(raw.get("confidenceScore")),
        "discoveryScore": _score(raw.get("discoveryScore")),
        "stateTransitionBonus": _score(raw.get("stateTransitionBonus"), 20),
        "metaScore": _score(raw.get("metaScore")),
        "opportunityScore": _score(raw.get("opportunityScore")),
        "leaderScore": _score(raw.get("leaderScore")),
        "executionScore": _score(raw.get("executionScore")),
        "riskScore": _score(raw.get("riskScore")),
        "executionPermission": permission,
        "auditStatus": _text(raw.get("auditStatus"), 80).lower() or "unknown",
        "riskTags": _list(raw.get("riskTags"), limit=12, item_limit=160),
        "hardBlockReason": _text(raw.get("hardBlockReason"), 500),
        "leaderReason": _text(raw.get("leaderReason"), 600),
        "primaryDriver": _text(raw.get("primaryDriver"), 600),
        "nextTrigger": _text(raw.get("nextTrigger") or raw.get("nextTransition"), 500),
        "nextTransition": _text(raw.get("nextTransition") or raw.get("nextTrigger"), 500),
        "invalidation": _text(raw.get("invalidation"), 500),
    }


def framework_assessment_complete(analysis: Mapping[str, Any] | None) -> bool:
    """Only a materially complete V4.9 review may become a formal recommendation."""
    raw = _mapping(_mapping(analysis).get("frameworkAssessment"))
    if _text(raw.get("version"), 80) != FRAMEWORK_VERSION:
        return False
    normalized = normalize_framework_assessment(raw)
    required = (
        normalized["attentionState"], normalized["attentionTransition"],
        normalized["transitionTrigger"], normalized["narrativeDiscovery"],
        normalized["mappingFit"], normalized["leaderElection"],
        normalized["nextTrigger"], normalized["invalidation"],
        normalized["metaFamily"], normalized["metaExpansion"],
        normalized["survivalAssessment"], normalized["longTermStage"],
        normalized["thesisMemory"]["coreThesis"], normalized["thesisMemory"]["tokenValueCapture"],
        normalized["thesisMemory"]["invalidation"],
        normalized["lifelines"]["project"], normalized["lifelines"]["narrative"],
        normalized["lifelines"]["token"], normalized["lifelines"]["liquidity"],
        normalized["personCatalyst"]["status"],
        normalized["marketMainline"]["status"], normalized["marketMainline"]["phase"],
        normalized["marketMainline"]["candidateRelation"], normalized["marketMainline"]["relationReason"],
        normalized["hotspotOpportunity"]["relation"], normalized["hotspotOpportunity"]["priority"],
        normalized["hotspotOpportunity"]["identityConclusion"],
        normalized["hotspotOpportunity"]["opportunityConclusion"],
        normalized["hotspotOpportunity"]["executionConclusion"],
    )
    person = normalized["personCatalyst"]
    person_complete = person["status"] == "none" or bool(
        person["status"] in VALID_PERSON_CATALYST_STATUSES - {"none"}
        and person["semanticRelation"] and person["identityMapping"]
        and person["marketImpact"] and person["evidence"]
    )
    mainline = normalized["marketMainline"]
    mainline_complete = bool(
        mainline["status"] in VALID_MARKET_MAINLINE_STATUSES
        and mainline["phase"] in VALID_MARKET_MAINLINE_PHASES
        and mainline["candidateRelation"] in VALID_MARKET_MAINLINE_RELATIONS
        and mainline["relationReason"]
        and (
            mainline["status"] != "active"
            or (mainline["primaryThemes"] and mainline["evidence"] and mainline["capitalAttention"])
        )
    )
    return bool(
        all(required) and person_complete and mainline_complete
        and normalized["executionPermission"] in VALID_PERMISSIONS
    )


def golden_leader_alert_decision(row: Mapping[str, Any], analysis: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a research-alert decision while keeping execution risk separate."""
    ai = _mapping(analysis)
    framework = normalize_framework_assessment(ai.get("frameworkAssessment"))
    tier = framework["potentialTier"]
    verdict = _text(ai.get("verdict"), 30).lower()
    evidence = _text(ai.get("evidenceStatus"), 30).lower()
    confidence = _score(ai.get("confidence"))
    refs = _list(ai.get("evidenceRefs"), limit=8, item_limit=800)
    opportunity = framework["opportunityScore"]
    leader = framework["leaderScore"]
    has_explanation = bool(framework["primaryDriver"] and framework["leaderReason"])
    framework_complete = framework_assessment_complete(ai)
    lifecycle_ok = framework["longTermStage"] != "LT6" and framework["survivalLabel"] not in {"decay", "dormant"}
    reactivation_ok = framework["longTermStage"] not in {"LT4", "LT5"} or len(framework["reactivationEvidence"]) >= 2
    score_ok = opportunity >= 78 and (
        (tier == "leader" and leader >= 78)
        or (tier == "golden-dog" and leader >= 68)
    )
    eligible = bool(
        tier in {"leader", "golden-dog"}
        and verdict == "strong"
        and evidence == "supported"
        and confidence >= 75
        and refs
        and has_explanation
        and score_ok
        and framework_complete
        and lifecycle_ok
        and reactivation_ok
    )
    permission = framework["executionPermission"]
    label = "龙头潜力" if tier == "leader" else ("大金狗潜力" if tier == "golden-dog" else "继续观察")
    reasons: list[str] = []
    if tier not in {"leader", "golden-dog"}:
        reasons.append("模型未明确判定为大金狗或龙头潜力")
    if verdict != "strong" or evidence != "supported" or confidence < 75:
        reasons.append("结论、证据或置信度未达到弹窗门槛")
    if not has_explanation:
        reasons.append("缺少主要驱动或同题材领先依据")
    if not score_ok:
        reasons.append("机会分或龙头分未达到门槛")
    if not framework_complete:
        reasons.append("尚未完成 V4.9 热点三账、市场主线、人物催化、Meta 扩散、生存率、四条生命线与龙头复核")
    if not lifecycle_ok:
        reasons.append("长期阶段或生存率已不满足主动提醒门槛")
    if not reactivation_ok:
        reasons.append("复燃缺少至少两条独立生命线证据")
    return {
        "eligible": eligible,
        "label": label,
        "potentialTier": tier,
        "opportunityScore": opportunity,
        "leaderScore": leader,
        "executionScore": framework["executionScore"],
        "riskScore": framework["riskScore"],
        "executionPermission": permission,
        "actionable": bool(eligible and permission == "ALLOW"),
        "reason": "；".join(reasons),
        "symbol": _text(row.get("symbol") or row.get("name"), 80),
    }


FULL_FRAMEWORK_PROMPT = """
使用用户提供的《链上投研体系 V4.9》完整框架；此前的身份、机制、Firstness、Quote Migration、P0、注意力跃迁、双雷达、Meta扩散、生存率和四条生命线继续有效，但不能只做单币扫链打分：
1. 先建立六类对象并禁止混写：链外事件 Event、注意力单元 Attention Unit、叙事簇 Narrative Cluster、资产候选 Asset Candidate、龙头关系 Leader Relation、可执行对象 Execution Object。事件热不等于某个 CA 已获官方确认。
2. 叙事发现采用并集：新闻/社交原文、新发行/新池、钱包行为、产品机制、自动发射平台等来源都可入研究档案。研究层高召回；风险只能限制执行，不能删除事件、映射或候选记录。
3. 标注链外注意力 A0-A8：A0冷事实、A1注意力种子、A2单源扩散、A3跨源扩散、A4公共注意力跃迁、A5链上映射、A6市场响应、A7共识龙头、A8退潮/复燃；写明前态、新态、触发器、证据、置信度和观察者。
4. transitionTrigger 只能从 TR_ATTN_JUMP、TR_PRIMARY_EVENT、TR_MAPPING_SWARM、TR_BUYER_RESPONSE、TR_LEADER_CONCENTRATION、TR_LEADER_ROTATION、TR_DORMANT_REACTIVATION、TR_RISK_FLIP 中选择。
5. 叙事雷达必须回答：故事是什么、最小注意力单元、当前注意力状态、为何跃迁、所有候选 CA、最早映射、链上是否承接和 Next Trigger。A3/A4 尚无 CA 时保留 Pre-Asset Watch，不强行配币。
6. 龙头雷达用 1m/5m/15m/1h 与 6h/24h/3d/7d 双窗口比较候选，允许挑战者接管与龙头轮动；非 OG、重复头像/社交、批量发射、自动工具、开发者多发币都只是标签或软扣分，不能直接淘汰。
7. Firstness 拆分为 First Created、First Mapped、First Tradable、First Attention-Matched、First Paid-Buyer Leader、First Distribution Leader、First Cultural Leader、First Real Leader；“最早看到”不能写成“官方首个”。
8. 资产年龄不等于注意力年龄。老池若出现新的链外跃迁、买方响应或分发扩散，要按 dormant reactivation 重新评估，而不是按币龄淘汰。
9. 六本账分别输出：叙事发现、注意力跃迁、映射适配、龙头竞选、执行、风险与置信度。必须明确给出执行许可 ALLOW/CAUTION/BLOCK/UNKNOWN；UNKNOWN 不等于安全。只有已确认的致命执行风险才给 BLOCK，并写 hardBlockReason，普通风险写 riskTags。
10. 同时保留 V4.3 的公链语境、资产身份、S0-S8/SX 链上状态、报价资产、机制路由、首批付费买家/资金根、加速度、历史类比、跨形态接力、母案例和 Action Window。
11. potentialTier 只有在证据充分时才能给 leader 或 golden-dog；必须说明为何领先同题材候选。链外很热但链上未承接时不强行选龙头；结构性机会即使故事不漂亮也可进入研究。
12. 建立 Meta 家族而不是只看一个币：追溯 Mechanism Origin、Canonical Source、First Official、First Tradable、First Ecosystem Child、First Real Leader 与 Fork/Clone；按 0-1h、1-6h、6-24h、24h+ 记录扩散和龙头轮动。
13. 生存率必须看 1h/3h/6h/24h 的市值保持、增量成交、新买家、榜单持续、注意力速度、流动性和退出深度，给 strong-hold/divergence/decay/dormant/reactivating；机制强不等于承载币强，分别给 mechanismStrength 与 carrierStrength。
14. 每个新资产首日建立 Thesis Memory：为什么值得跟踪、终局想象、3-5 个里程碑、代币价值承接、失效条件、复燃条件。长期阶段只能为 LT0发现、LT1发行热潮、LT2冷却、LT3休眠但逻辑完整、LT4复燃、LT5二级重估、LT6逻辑破坏。
15. 持续跟踪 Project/Narrative/Token/Liquidity 四条生命线，价格只是结果线。复燃必须至少有两条独立生命线，或事实与资金同时转强；只有价格反弹不能判复燃，项目变强也不能自动推出代币价值承接。
16. 新币 0-24h 高频复核，D3/D7/D30+ 降频并由重大催化唤醒；不得把长期跟踪写成无限抄底。
17. 人物/官方原始动作必须独立建立 personCatalyst：区分作者正文、引用内容、转发与可核验关注；名称/CA/官方X只负责召回，最终语义以快速AI确证为准。不得把评论区、被引用者的话、同名普通词或泛行业讨论冒充人物点名。人物动作只提升Narrative/研究优先级，不自动等于官方背书、龙头或可执行。
18. personCatalyst.status 必须为 none/watch/confirmed/ambiguous/rejected；非none时给 sourceImpact、actionStrength、semanticRelation、identityMapping、marketImpact、evidence、nextTrigger、invalidation。
19. 每一批先在同一 asOf 下判断当前市场正在交易的 1–3 条主线，不得从单个候选的名字或项目自述反推市场主线。至少综合多资产相对强弱、成交/流动性迁移、链上资金、独立事件/产品催化和跨平台注意力；证据不足时 status=uncertain，禁止用泛称 AI/Meme/DeFi 填空。
20. marketMainline.phase 只能为 emerging/accelerating/consensus/crowded/rotating/fading/unclear；candidateRelation 只能为 core-leader/core-member/branch-expansion/catch-up/independent-catalyst/counter-trend/theme-rub/uncertain。主线不是白名单：独立强催化可入选；属于主线也不能绕过身份、龙头、买盘、生存率和执行风险。拥挤或轮动阶段必须区分真龙、补涨、资金迁移和末端蹭热。
21. 每条整体结论仍必须给 nextTrigger 和 invalidation。
22. 对每个 CA 反查最近 0–24h、24–72h、3–7d 热点，同时从 H4/H5 公共注意力跃迁反向展开全部竞争 CA。官方性只回答“谁发行/是否认领”，不能一票否决热点炒作潜力；非官方、非OG、自动工具或社区部署本身均不是低价值或诈骗结论。
23. 新增 hotspotOpportunity 三账分离：Identity Ledger 写 official/community-recognized/independent-hotspot/parody/copy/impersonation；Opportunity Ledger 写热点强度、名称/视觉/语义/时间/文化贴合、跨源扩散与市场承接；Execution Ledger 独立写可卖性、税、权限、LP、持仓和致命风险。
24. H4/H5 + 高 Mapping Fit + 真实买盘/传播/入口承接时，可令 independent-hotspot CA hotspot_override 到 P0/P1 并参与 Current Real Leader 竞争；这只提高研究优先级，绝不能越过 Fatal Risk。官方晚发币时重开竞争，不机械接管龙头。
25. 同一热点并列维护 First Created、First Mapped、Official、Best Attention Match、Volume Leader、Market Cap Leader、Current Real Leader。热点大但链上弱时保留 Hotspot Watch，不得硬选龙；只有关键词碰瓷且无真实热点、贴合与承接时才静默。
26. hotspotOpportunity 必须给 relation、priority(P0/P1/P2/P3/NONE)、override、identityConclusion、opportunityConclusion、executionConclusion、nextTrigger、invalidation。输出 version 必须严格为 xmind-v4.9-hotspot-derived-ca-three-ledgers-1。
""".strip()


FRAMEWORK_OUTPUT_SCHEMA = {
    "version": FRAMEWORK_VERSION,
    "potentialTier": "leader|golden-dog|watch|none",
    "candidatePath": "候选路径",
    "currentStage": "S0-S8/SX",
    "stateTransition": "链上阶段迁移",
    "eventId": "链外事件ID或来源标识",
    "narrativeId": "叙事簇标识",
    "attentionState": "A0-A8",
    "previousAttentionState": "A0-A8或空",
    "attentionTransition": "前态→新态、证据、置信度、观察者",
    "transitionTrigger": "TR_ATTN_JUMP|TR_PRIMARY_EVENT|TR_MAPPING_SWARM|TR_BUYER_RESPONSE|TR_LEADER_CONCENTRATION|TR_LEADER_ROTATION|TR_DORMANT_REACTIVATION|TR_RISK_FLIP",
    "narrativeDiscovery": "叙事发现账：故事、最小单元、来源覆盖和证据缺口",
    "mappingFit": "映射适配账：事件如何映射当前CA、竞争CA和官方性边界",
    "leaderElection": "龙头竞选账：双窗口比较、领先依据、挑战者与轮动条件",
    "candidateSet": ["链:CA · 候选角色/依据"],
    "currentLeader": "当前龙头；未形成就明确写未形成",
    "leaderRelation": "当前候选与龙头/挑战者关系及可能轮动",
    "dormantReactivation": "资产年龄与注意力年龄；是否属于休眠复燃",
    "metaFamily": "Meta 家族来源、成员、Canonical Source 与当前龙头",
    "metaRole": "Mechanism Origin|Canonical Source|First Official|First Tradable|First Ecosystem Child|First Real Leader|Fork/Clone",
    "metaExpansion": "0-1h/1-6h/6-24h/24h+ 扩散、分叉和轮动结论",
    "survivalLabel": "strong-hold|divergence|decay|dormant|reactivating|unknown",
    "survivalAssessment": "1h/3h/6h/24h 的市值保持、增量成交、新买家、持续排名、注意力速度、流动性与退出深度",
    "mechanismStrength": 0,
    "carrierStrength": 0,
    "longTermStage": "LT0|LT1|LT2|LT3|LT4|LT5|LT6",
    "thesisMemory": {
        "coreThesis": "为什么值得持续跟踪",
        "terminalVision": "终局想象",
        "milestones": ["未来 3-5 个可验证里程碑"],
        "tokenValueCapture": "代币如何承接项目/叙事价值；纯 Meme 也要写明承接机制",
        "invalidation": "什么变化会破坏原逻辑",
        "reactivationConditions": ["什么事实会重新唤醒研究"],
    },
    "lifelines": {
        "project": "项目生命线及证据",
        "narrative": "叙事生命线及证据",
        "token": "代币生命线及证据",
        "liquidity": "流动性生命线及证据",
    },
    "reactivationEvidence": ["复燃的独立事实/资金证据；非复燃可为空"],
    "personCatalyst": {
        "status": "none|watch|confirmed|ambiguous|rejected",
        "sourceImpact": 0,
        "actionStrength": 0,
        "semanticRelation": "原始动作是否真的指向这个具体加密资产；区分作者正文与引用内容",
        "identityMapping": "人物动作如何映射到链、CA、官方X；同名多CA必须写歧义",
        "marketImpact": "对叙事/分发/注意力的潜在影响；不得直接写成官方背书或买入结论",
        "evidence": "原帖、动作、发布时间和语义确证依据",
        "nextTrigger": "人物催化升级还需出现什么事实或链上承接",
        "invalidation": "什么会证明只是同名误配、一次性噪声或错误归因",
    },
    "marketMainline": {
        "status": "active|uncertain|none",
        "asOf": 0,
        "primaryThemes": ["同一批次识别的1–3条当下市场主线；数据不足可为空"],
        "phase": "emerging|accelerating|consensus|crowded|rotating|fading|unclear",
        "leaders": ["主线代表资产/项目及领先依据"],
        "capitalAttention": "成交、流动性、链上资金和跨平台注意力如何迁移",
        "evidence": ["支持主线判断的跨资产/资金/事件证据；不得只写候选自身文案"],
        "candidateRelation": "core-leader|core-member|branch-expansion|catch-up|independent-catalyst|counter-trend|theme-rub|uncertain",
        "relationReason": "候选与市场主线的具体关系；若独立催化或主线未知也要解释",
        "nextTrigger": "主线或候选关系升级所需事实",
        "invalidation": "主线判断或关系判断的失效条件",
    },
    "hotspotOpportunity": {
        "eventId": "热点事件ID",
        "eventName": "热点事件的一句话名称",
        "relation": "official|community-recognized|independent-hotspot|parody|copy|impersonation|unrelated|uncertain",
        "officialClaimStatus": "官方认领状态；只描述身份",
        "officialDenialStatus": "官方否认状态",
        "falseOfficialClaim": False,
        "attentionState": "A0-A8",
        "attentionVelocity": 0,
        "crossSourceCount": 0,
        "crossPlatformCount": 0,
        "mappingFit": {"name": 0, "visual": 0, "semantic": 0, "time": 0, "culture": 0},
        "candidateCountSameEvent": 0,
        "currentLeader": "当前热点龙头；未形成就明确写未形成",
        "priority": "P0|P1|P2|P3|NONE",
        "override": False,
        "identityConclusion": "它是谁发的、是否认领或冒充",
        "opportunityConclusion": "为什么现在可能值得看；不得因非官方直接降为低价值",
        "executionConclusion": "独立执行许可与致命风险结论",
        "nextTrigger": "热点、映射或承接升级条件",
        "invalidation": "热点机会失效条件",
    },
    "deploySource": "发行/自动发射来源，仅作来源或行为标签",
    "chainContext": "公链语境和资本形成方式",
    "assetIdentity": "资产身份、CA/协议/项目归属和报价资产",
    "minAttentionUnit": "最小注意力单元",
    "emotionalHook": "情绪入口",
    "attentionHook": "注意力入口",
    "narrativeType": "叙事类型",
    "novelty": {"asset": 0, "protocol": 0, "mechanism": 0, "gameplay": 0},
    "firstness": "Firstness 台账结论",
    "distribution": "分发继承与渠道",
    "paidBuyers": "付费买家和资金根",
    "acceleration": "成交/持有人/流动性加速度",
    "smartMoney": "聪明钱/KOL/关键人物",
    "quoteMigration": "报价迁移和主导报价资产",
    "mechanism": "税/金库/奖励/桥/赎回/RWA/可编程机制",
    "historicalAnalogues": ["历史类比"],
    "falsifiers": ["可证伪条件"],
    "p0OfficialAsset": "Blue Box P0 首个官方资产结论",
    "crossRegimeHandoff": "跨形态/跨制度接力",
    "motherCaseSignals": "母案例信号",
    "identityTimeSupplyAudit": "身份、时间与供应审计",
    "actionWindow": "当前行动窗口；无窗口要明确说明",
    "narrativeDiscoveryScore": 0,
    "attentionTransitionScore": 0,
    "mappingFitScore": 0,
    "leaderElectionScore": 0,
    "confidenceScore": 0,
    "discoveryScore": 0,
    "stateTransitionBonus": 0,
    "metaScore": 0,
    "opportunityScore": 0,
    "leaderScore": 0,
    "executionScore": 0,
    "riskScore": 0,
    "executionPermission": "ALLOW|CAUTION|BLOCK|UNKNOWN",
    "auditStatus": "checked|partial|critical|fatal|unknown",
    "riskTags": ["研究风险标签；不可因软标签删除候选"],
    "hardBlockReason": "仅已确认致命执行风险填写，否则为空",
    "leaderReason": "为何领先同题材候选",
    "primaryDriver": "当前最主要驱动",
    "nextTransition": "下一链上阶段迁移",
    "nextTrigger": "下一注意力/映射/龙头触发条件",
    "invalidation": "失效条件",
}
