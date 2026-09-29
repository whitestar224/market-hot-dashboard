"""从 server.py 抽取的模块（Phase 3 拆分，批次 6：dragon_wave 反馈域）。

来源: server.py 第 5980-6228 行（6 个 dragon_wave_feedback_* 函数 + 1 个常量）
本文件函数体由 tools/extract_module.py 机械搬移后，仅对「留在 server.py 命名空间
里的运行时状态」做了最小等价改写（见下）。

为什么这里要「懒 import server」
--------------------------------
这些函数引用的 3 个名字仍是 server.py 顶层定义（尚未拆出）：
  safe_float、normalize_dragon_wave_feedback（就在本块上方 5901 行）、load_user_payload

它们定义位置各异。若在模块顶部 `from ... import` 绑定，会因循环导入失败，或拿到
初始化前的空值。正确读法：在【函数体内】`import server`（懒加载）——这些函数只在
运行时被调用，那时 server.py 早已完整加载。

`_server()` 必须回退 `__main__`（见批次 5 坑 8）：`python server.py` 直接运行时
模块注册名是 `__main__` 而非 `server`。

其余名字（常量，如 USER_SCOPE_DRAGON_WAVE_FEEDBACK）已搬到 app.core.state
且是【同一对象】（不重绑定），直接 `from app.core.state import` 绑定即可。
"""

from __future__ import annotations

import math
import sys
from typing import Any

from app.core.state import USER_SCOPE_DRAGON_WAVE_FEEDBACK


def _server():
    """懒加载 server 模块对象，用于读取仍留在 server.py 命名空间里的运行时状态。

    必须在【函数体内】调用（见模块 docstring）。模块导入期调用会因循环导入失败。

    关键坑：`python server.py` 直接运行时，模块注册名是 `__main__` 而非 `server`
    （只有 `import server` 才会注册 `sys.modules["server"]`）。因此这里先查 `server`，
    再回退 `__main__`，两条路径都能拿到同一个、状态实时更新的模块对象。
    """
    return sys.modules.get("server") or sys.modules["__main__"]


def dragon_wave_feedback_feature_tokens(signal: Any) -> list[str]:
    signal = signal if isinstance(signal, dict) else {}
    foundations = sorted({str(item) for item in signal.get("foundationTypes", [])}) if isinstance(signal.get("foundationTypes"), list) else []
    auxiliaries = sorted({str(item) for item in signal.get("auxiliaryTypes", [])}) if isinstance(signal.get("auxiliaryTypes"), list) else []
    confluence = sorted({str(item) for item in signal.get("confluence", [])}) if isinstance(signal.get("confluence"), list) else []
    interval = str(signal.get("interval") or "").strip()
    structure_shape = str(signal.get("structureShape") or "none").strip() or "none"
    tokens = [f"foundation:{item}" for item in foundations]
    tokens.extend(f"auxiliary:{item}" for item in auxiliaries)
    if signal.get("patternKey"):
        tokens.append(f"pattern:{signal['patternKey']}")
    if confluence:
        tokens.append(f"combo:{'+'.join(confluence)}")
    if interval:
        tokens.append(f"interval:{interval}")
        if foundations and "manualReview" not in foundations:
            tokens.append(f"interval-foundation:{interval}|{'+'.join(foundations)}")
            tokens.append(f"interval-auxiliary:{interval}|{'+'.join(auxiliaries) or 'none'}")
            tokens.append(f"interval-shape:{interval}|{structure_shape}")
            tokens.append(f"interval-setup:{interval}|{'+'.join(foundations)}>{'+'.join(auxiliaries) or 'none'}|{structure_shape}")
    context_tokens = sorted({str(item) for item in signal.get("contextTokens", [])}) if isinstance(signal.get("contextTokens"), list) else []
    tokens.extend(f"context:{item}" for item in context_tokens)
    allowed_structure_tags = {
        "horizontalLaunch", "trendlineBreakout", "triangle", "box",
        "fallingWedge", "pivot", "previousHighBreakout", "consolidationBreakout", "ema90Pullback",
        "volumeBreakout", "nearPreviousHighConsolidation", "newCoinNotFalling",
    }
    structure_tags = sorted({str(item) for item in signal.get("manualStructureTags", []) if str(item) in allowed_structure_tags}) if isinstance(signal.get("manualStructureTags"), list) else []
    tokens.extend(f"manual-structure:{item}" for item in structure_tags)
    predicted_tags = sorted({str(item) for item in signal.get("predictedStructureTags", []) if str(item) in allowed_structure_tags}) if isinstance(signal.get("predictedStructureTags"), list) else []
    tokens.extend(f"strategy-structure:{item}" for item in predicted_tags)
    review = signal.get("structureReview") if isinstance(signal.get("structureReview"), dict) else None
    if review:
        for field, prefix in (("matched", "review-match"), ("addedByUser", "review-added"), ("removedByUser", "review-removed")):
            values = sorted({str(item) for item in review.get(field, []) if str(item) in allowed_structure_tags}) if isinstance(review.get(field), list) else []
            tokens.extend(f"{prefix}:{item}" for item in values)
        agreement = _server().safe_float(review.get("agreement"), 0)
        tokens.append(f"review-agreement:{'exact' if review.get('exact') else 'partial' if agreement >= 0.5 else 'low'}")
    def metric_band(name: str, field: str, boundaries: tuple[float, ...], labels: tuple[str, ...]) -> None:
        raw_value = signal.get(field)
        if raw_value is None or isinstance(raw_value, bool):
            return
        number = _server().safe_float(raw_value, float("nan"))
        if not math.isfinite(number):
            return
        index = next((cursor for cursor, boundary in enumerate(boundaries) if number < boundary), len(labels) - 1)
        tokens.append(f"quality:{name}:{labels[index]}")
    metric_band("base-bars", "consolidationBars", (20, 40, 80), ("short", "forming", "mature", "long"))
    metric_band("outer-edge", "outerEdgeScore", (60, 80), ("weak", "clean", "strong"))
    metric_band("ceiling-touches", "ceilingTouches", (2, 3), ("single", "double", "multiple"))
    metric_band("rhythm", "rhythmScore", (60, 75), ("weak", "flowing", "elite"))
    metric_band("certainty", "certaintyScore", (70, 85), ("low", "high", "elite"))
    metric_band("order-flow", "orderFlowScore", (60, 75), ("quiet", "supportive", "strong"))
    metric_band("launch-distance", "launchDistancePercent", (2, 7), ("attached", "near", "far"))
    metric_band("prior-range", "priorRangePercent", (4, 8), ("tight", "controlled", "wide"))
    metric_band("prior-drift", "priorDriftPercent", (2, 6), ("flat", "controlled", "trending"))
    metric_band("prior-volume", "priorVolumeRatio", (1, 1.35), ("dry", "normal", "expanding"))
    metric_band("channel-occupancy", "channelInteriorOccupancy", (0.5, 0.7), ("hollow", "occupied", "full"))
    metric_band("channel-hollow", "channelHollowRatio", (0.25, 0.42), ("low", "moderate", "high"))
    metric_band("channel-transitions", "channelSideTransitions", (2, 5), ("single-side", "rotating", "active"))
    if signal.get("outerEdgeConfirmed") is True:
        tokens.append("quality:outer-edge-confirmed")
    if signal.get("aboveEma90") is True:
        tokens.append("quality:above-ema90")
    if signal.get("breaksPriorHigh") is True:
        tokens.append("quality:breaks-prior-high")
    grade = str(signal.get("manualCertaintyGrade") or "").upper().strip()
    if grade in {"A+", "A", "B"}:
        tokens.append(f"manual-grade:{grade}")
    return list(dict.fromkeys(tokens))


DRAGON_WAVE_FEEDBACK_INDEX_SIGNAL_FIELDS = (
    "time", "interval", "pattern", "patternKey", "price", "triggerPrice", "level",
    "selectedPrice", "status", "score", "certaintyScore", "relativeVolume",
    "structureShape", "manualCandleSelection", "manualSource",
    "open", "high", "low", "close", "volume",
)


def dragon_wave_feedback_display_index(value: Any) -> dict[str, Any]:
    normalized = _server().normalize_dragon_wave_feedback(value)
    records: dict[str, Any] = {}
    for key, record in normalized["records"].items():
        signal = record.get("signal") if isinstance(record.get("signal"), dict) else {}
        compact_signal = {
            field: signal[field]
            for field in DRAGON_WAVE_FEEDBACK_INDEX_SIGNAL_FIELDS
            if field in signal
        }
        compact_signal["feedbackTokens"] = dragon_wave_feedback_feature_tokens({
            **signal,
            "interval": record.get("interval") or signal.get("interval"),
        })[:32]
        records[key] = {
            field: record.get(field)
            for field in (
                "key", "decision", "createdAt", "updatedAt", "pair", "interval", "venue",
                "certaintyGrade", "structureTags", "predictedStructureTags",
            )
            if field in record
        }
        records[key]["signal"] = compact_signal
    return {
        "version": 1,
        "updatedAt": normalized.get("updatedAt", 0),
        "records": records,
    }


def dragon_wave_supervised_prototype_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def percentile(values: list[float], ratio: float) -> float:
        ordered = sorted(float(value) for value in values)
        if not ordered:
            return 0
        position = (len(ordered) - 1) * ratio
        lower = int(position)
        upper = min(len(ordered) - 1, lower + 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    def build(selected: list[dict[str, Any]], sample_key: str) -> dict[str, Any]:
        groups: dict[str, dict[str, Any]] = {}
        for row in selected:
            tags = sorted(row.get("structureTags") or [])
            setup_token = next((token for token in row.get("featureTokens", []) if token.startswith("interval-setup:")), "")
            setup_signature = setup_token.split("|", 1)[1] if "|" in setup_token else "none>none|none"
            key = f"{row['interval']}|manual:{'+'.join(tags)}" if tags else f"{row['interval']}|setup:{setup_signature}"
            group = groups.setdefault(key, {
                "interval": row["interval"], "structureTags": tags, "setupSignature": setup_signature,
                "rows": [], "pairs": set(),
            })
            group["rows"].append(row)
            group["pairs"].add(row["pair"])
        prototypes: list[dict[str, Any]] = []
        for key, group in groups.items():
            metric_names = sorted({name for row in group["rows"] for name in row.get("metrics", {}) if name != "manualCertaintyLevel"})
            metrics = {}
            for name in metric_names:
                values = [row["metrics"][name] for row in group["rows"] if name in row.get("metrics", {})]
                metrics[name] = {"low": percentile(values, 0.25), "median": percentile(values, 0.5), "high": percentile(values, 0.75)}
            counts: dict[str, int] = {}
            for row in group["rows"]:
                for token in row.get("featureTokens", []):
                    if token.startswith(("quality:", "context:", "manual-structure:", "strategy-structure:", "review-")):
                        counts[token] = counts.get(token, 0) + 1
            prototypes.append({
                "key": key,
                "interval": group["interval"],
                "structureTags": group["structureTags"],
                "setupSignature": group["setupSignature"],
                "sampleCount": len(group["rows"]),
                "pairCount": len(group["pairs"]),
                "metrics": metrics,
                "sharedReasons": sorted((token for token, count in counts.items() if count / len(group["rows"]) >= 0.6), key=lambda token: (-counts[token], token)),
            })
        prototypes.sort(key=lambda item: (-item["sampleCount"], item["key"]))
        return {sample_key: sum(item["sampleCount"] for item in prototypes), "prototypes": prototypes}

    return {
        "positiveAPlus": build([row for row in rows if row["decision"] == "confirmed" and row["certaintyGrade"] == "A+"], "totalAPlusSamples"),
        "negativeDenied": build([row for row in rows if row["decision"] == "denied"], "totalDeniedSamples"),
        "policy": "causal-feature-combination-only",
    }


def dragon_wave_feedback_optimization(value: Any) -> dict[str, Any]:
    normalized = _server().normalize_dragon_wave_feedback(value)
    metric_fields = (
        "score", "certaintyScore", "rhythmScore", "sentimentScore",
        "orderFlowScore", "consolidationBars", "relativeVolume", "structuralRiskPercent",
        "ceilingAge", "ceilingTouches", "outerEdgeScore",
        "platformTouchGroups", "launchDistancePercent", "compressionRatioAtDecision",
        "channelInteriorOccupancy", "channelMiddleParticipationRatio", "channelHollowRatio", "channelLongestHollowRun", "channelSideTransitions",
        "ema90AtDecision", "atrAtDecision", "priorHighAtDecision", "priorLowAtDecision",
        "priorRangePercent", "priorDriftPercent", "priorVolumeRatio", "selectedPrice",
    )
    rows: list[dict[str, Any]] = []
    for record in sorted(normalized["records"].values(), key=lambda item: (item["updatedAt"], item["key"])):
        if record["decision"] == "cleared":
            continue
        signal = record.get("signal") if isinstance(record.get("signal"), dict) else {}
        metrics = {}
        for field in metric_fields:
            raw_value = signal.get(field)
            if raw_value is None or isinstance(raw_value, bool):
                continue
            number = _server().safe_float(raw_value, float("nan"))
            if math.isfinite(number):
                metrics[field] = number
        certainty_grade = str(record.get("certaintyGrade") or "")
        if certainty_grade:
            metrics["manualCertaintyLevel"] = {"A+": 3, "A": 2, "B": 1}[certainty_grade]
        rows.append({
            "key": record["key"],
            "label": record["optimizationLabel"],
            "role": record["optimizationRole"],
            "decision": record["decision"],
            "certaintyGrade": certainty_grade,
            "structureTags": record.get("structureTags") or [],
            "predictedStructureTags": record.get("predictedStructureTags") or [],
            "structureReview": record.get("structureReview"),
            "pair": record["pair"],
            "interval": record["interval"],
            "time": max(0, int(_server().safe_float(signal.get("time"), 0))),
            "updatedAt": record["updatedAt"],
            "featureTokens": dragon_wave_feedback_feature_tokens({**signal, "interval": record["interval"] or signal.get("interval")}),
            "metrics": metrics,
            "visualSignature": signal.get("visualSignature") if isinstance(signal.get("visualSignature"), dict) else None,
        })
    feature_tokens = {token for row in rows for token in row["featureTokens"]}
    supervised = dragon_wave_supervised_prototype_profile(rows)
    return {
        "datasetVersion": 10,
        "generatedAt": normalized["updatedAt"],
        "causality": "decision-time-features-only",
        "excludedOutcomeFields": ["futureReturn", "maxFavorableExcursion", "maxAdverseExcursion", "futureHigh", "futureLow"],
        "summary": {
            "total": len(rows),
            "positiveCount": sum(row["label"] == 1 for row in rows),
            "negativeCount": sum(row["label"] == -1 for row in rows),
            "pendingCount": sum(row["label"] == 0 for row in rows),
            "labeledCount": sum(row["label"] != 0 for row in rows),
            "featureCount": len(feature_tokens),
            "aPlusPrototypeCount": len(supervised["positiveAPlus"]["prototypes"]),
            "aPlusSampleCount": supervised["positiveAPlus"]["totalAPlusSamples"],
            "deniedPrototypeCount": len(supervised["negativeDenied"]["prototypes"]),
            "deniedPrototypeSampleCount": supervised["negativeDenied"]["totalDeniedSamples"],
            "visualLabeledCount": sum(row["label"] != 0 and bool(row["visualSignature"]) for row in rows),
            "visualAPlusCount": sum(row["decision"] == "confirmed" and row["certaintyGrade"] == "A+" and bool(row["visualSignature"]) for row in rows),
            "visualDeniedCount": sum(row["decision"] == "denied" and bool(row["visualSignature"]) for row in rows),
        },
        "supervisedPrototypeProfile": supervised,
        "rows": rows,
    }


def merge_dragon_wave_feedback(*documents: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {"version": 1, "updatedAt": 0, "records": {}}
    for document in documents:
        normalized = _server().normalize_dragon_wave_feedback(document)
        for key, record in normalized["records"].items():
            existing = merged["records"].get(key)
            if not existing or int(record.get("updatedAt") or 0) >= int(existing.get("updatedAt") or 0):
                merged["records"][key] = record
    return _server().normalize_dragon_wave_feedback(merged)


def dragon_wave_feedback_for_user(user: dict[str, Any] | None) -> dict[str, Any]:
    return _server().normalize_dragon_wave_feedback(
        _server().load_user_payload(user, USER_SCOPE_DRAGON_WAVE_FEEDBACK)
    )
