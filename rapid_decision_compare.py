"""Use JEV as the rapid-decision authority."""
from __future__ import annotations

import time

from jev_decision import JEV_DECISION_VERSION, analyze_jev_candidates


RAPID_DECISION_VERSION = "jev-primary-v49-router-v1"
PRIORITY_LABELS = ("reject", "watch", "deep-research")


def _current(rows, field, version):
    output = {}
    for row in rows:
        decision = row.get(field) if isinstance(row.get(field), dict) else {}
        if decision.get("version") == version:
            output[f"{row.get('network') or ''}:{row.get('contractAddress') or ''}"] = decision
    return output


def _merge(key, jev):
    if not isinstance(jev, dict) or not jev:
        return None
    priority = jev.get("priority") if jev.get("priority") in PRIORITY_LABELS else "watch"
    probabilities = {
        label: round(float((jev.get("probabilities") or {}).get(label) or 0), 4)
        for label in PRIORITY_LABELS
    }
    return {
        "key": key,
        "version": RAPID_DECISION_VERSION,
        "priority": priority,
        "confidence": probabilities.get(priority) or float(jev.get("confidence") or 0),
        "probabilities": probabilities,
        "goodCandidateProbability": round(float(jev.get("goodCandidateProbability") or 0), 4),
        "agreement": True,
        "comparisonStatus": "jev-only",
        "modelCount": 1,
        "primaryModel": "jev",
        "decisionSource": "jev-primary",
        "jevWeight": 1.0,
        "jev": jev,
        "latencyMs": int(jev.get("latencyMs") or 0),
        "analyzedAt": int(time.time() * 1000),
    }


def analyze_rapid_candidates(rows):
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return {}
    jev = _current(rows, "jevDecision", JEV_DECISION_VERSION)
    missing_jev = [
        row for row in rows
        if f"{row.get('network') or ''}:{row.get('contractAddress') or ''}" not in jev
    ]
    if missing_jev:
        jev.update(analyze_jev_candidates(missing_jev))
    output = {}
    for row in rows:
        key = f"{row.get('network') or ''}:{row.get('contractAddress') or ''}"
        merged = _merge(key, jev.get(key))
        if merged:
            output[key] = merged
    return output
