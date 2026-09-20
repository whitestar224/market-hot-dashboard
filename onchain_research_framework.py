"""Deterministic helpers for the full on-chain research framework (V4.4).

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


FRAMEWORK_VERSION = "xmind-v4.4-attention-dual-radar-1"
FRAMEWORK_SOURCE_SHA256 = "040aa291b0c419c64fc27cb3a93c4c22068557077276242f4b644fef27305576"
VALID_STAGES = {"S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "SX"}
VALID_ATTENTION_STATES = {f"A{index}" for index in range(9)}
VALID_TRANSITION_TRIGGERS = {
    "TR_ATTN_JUMP", "TR_PRIMARY_EVENT", "TR_MAPPING_SWARM", "TR_BUYER_RESPONSE",
    "TR_LEADER_CONCENTRATION", "TR_LEADER_ROTATION", "TR_DORMANT_REACTIVATION", "TR_RISK_FLIP",
}
VALID_POTENTIAL_TIERS = {"leader", "golden-dog", "watch", "none"}
VALID_PERMISSIONS = {"ALLOW", "CAUTION", "BLOCK", "UNKNOWN"}


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
    """Infer the highest observable V4.4 attention state without inventing facts."""
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


def build_candidate_framework_snapshot(
    row: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
    observed_at: int | None = None,
) -> dict[str, Any]:
    """Build the deterministic V4.4 fact layer while retaining V4.3 foundations."""
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
        reasons.append("V4.4 来源确证候选：进入注意力跃迁与双雷达复核")
        result["reasons"] = _list(reasons, limit=8, item_limit=240)
        result["frameworkPromoted"] = True
    else:
        result["frameworkPromoted"] = False
    return result


def normalize_framework_assessment(value: Any) -> dict[str, Any]:
    """Normalize the model's V4.4 dual-radar and six-ledger assessment."""
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
    """Only a materially complete V4.4 review may become a formal recommendation."""
    raw = _mapping(_mapping(analysis).get("frameworkAssessment"))
    if _text(raw.get("version"), 80) != FRAMEWORK_VERSION:
        return False
    normalized = normalize_framework_assessment(raw)
    required = (
        normalized["attentionState"], normalized["attentionTransition"],
        normalized["transitionTrigger"], normalized["narrativeDiscovery"],
        normalized["mappingFit"], normalized["leaderElection"],
        normalized["nextTrigger"], normalized["invalidation"],
    )
    return bool(all(required) and normalized["executionPermission"] in VALID_PERMISSIONS)


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
        reasons.append("尚未完成 V4.4 注意力跃迁、映射与龙头双雷达复核")
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
使用用户提供的《链上投研体系 V4.4》完整框架；V4.3 的身份、机制、Firstness、Quote Migration、P0 和审计基础继续有效，但不能只做单币扫链打分：
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
12. 每条必须给 nextTrigger 和 invalidation。输出 version 必须严格为 xmind-v4.4-attention-dual-radar-1。
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
