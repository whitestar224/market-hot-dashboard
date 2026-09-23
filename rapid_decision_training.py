"""Build leak-resistant V4.9 labels for JEV calibration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from jev_decision import rapid_candidate_state
from onchain_research_framework import FRAMEWORK_VERSION
from rapid_decision_schema import RAPID_PRIORITY_QUESTIONS


def target_from_analysis(analysis):
    if not isinstance(analysis, dict) or int(analysis.get("narrativeVersion") or 0) < 7:
        return ""
    verdict = str(analysis.get("verdict") or "").lower()
    framework = analysis.get("frameworkAssessment") if isinstance(analysis.get("frameworkAssessment"), dict) else {}
    if verdict == "strong" and framework.get("version") == FRAMEWORK_VERSION:
        return "deep-research"
    if verdict == "watch":
        return "watch"
    if verdict in {"weak", "avoid"}:
        return "reject"
    return ""


def readiness_from_counts(counts):
    counts = {label: int(counts.get(label) or 0) for label in ("deep-research", "watch", "reject")}
    total = sum(counts.values())
    minimum_class = min(counts.values()) if counts else 0
    return {
        "labeled": total,
        "classCounts": counts,
        "shadowReady": total >= 30 and minimum_class >= 5,
        "calibratorReady": total >= 300 and minimum_class >= 30,
        "split": "按 firstSeenAt 顺序 70%训练 / 15%验证 / 15%测试，禁止随机穿越",
        "note": "达标前仅做影子对比，不替换 V4.9 深研和弹窗门槛",
    }


def collect_examples(db_path):
    connection = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("""SELECT key,first_seen_at,candidate_json,analysis_json
            FROM onchain_fast_jobs
            WHERE analyzed_at>0 AND json_extract(analysis_json,'$.narrativeVersion')>=7
            ORDER BY first_seen_at,key""").fetchall()
    finally:
        connection.close()
    examples = []
    for key, first_seen_at, candidate_json, analysis_json in rows:
        try:
            candidate, analysis = json.loads(candidate_json), json.loads(analysis_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        target = target_from_analysis(analysis)
        if not target:
            continue
        examples.append({
            "id": hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:20],
            "firstSeenAt": int(first_seen_at),
            "state": rapid_candidate_state(candidate),
            "questions": RAPID_PRIORITY_QUESTIONS,
            "target": {"research_priority": target},
            "jev": candidate.get("jevDecision") or {},
            "rapid": candidate.get("rapidDecision") or {},
            "teacher": {
                "verdict": analysis.get("verdict"),
                "evidenceStatus": analysis.get("evidenceStatus"),
                "frameworkVersion": (analysis.get("frameworkAssessment") or {}).get("version"),
            },
        })
    for index, example in enumerate(examples):
        ratio = (index + 1) / max(1, len(examples))
        example["split"] = "train" if ratio <= 0.70 else "validation" if ratio <= 0.85 else "test"
    return examples


def main():
    parser = argparse.ArgumentParser(description="Export chronological V4.9 rapid-decision training data")
    parser.add_argument("--db", default=".runtime-cache/chain_ecosystem.db")
    parser.add_argument("--out", default=".runtime-cache/training/onchain-v48-rapid.jsonl")
    args = parser.parse_args()
    examples = collect_examples(args.db)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in examples), encoding="utf-8")
    counts = {label: sum(row["target"]["research_priority"] == label for row in examples)
              for label in ("deep-research", "watch", "reject")}
    print(json.dumps({"output": str(output), **readiness_from_counts(counts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
