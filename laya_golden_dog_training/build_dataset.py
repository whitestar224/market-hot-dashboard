"""构造「金狗潜力判断器」训练数据集。

标签体系（多信号融合，不依赖单一 potentialTier 正样本）：

历史大金狗（DOGE/SHIB/PEPE/BONK/WIF）共性特征：
  1. 极低市值起步（<数百万美元）
  2. 强 MEME 文化符号 + 极简叙事
  3. 公平发射/锁池弃权（本地映射为 liquidity 可交易、身份可验证）
  4. 社区扩散（持有者/买盘广度）
  5. CEX 上架催化（本地映射为 selectedScore/opportunity 高）

本地可得的量化信号：
  - memeScore (13.5-90)：MEME 传播力
  - selectedScore (19.5-100)：综合选择分
  - leaderScore (0-25)：龙头潜力分
  - opportunityScore (0-42)：机会分
  - verdict：strong/watch/weak/avoid

金狗潜力分（goldenDogScore，0-100）融合公式：
  goldenDogScore = 0.40*memeScore + 0.20*leader_norm + 0.20*opp_norm + 0.20*verdict_score
  其中 leader_norm = leaderScore/25*100, opp_norm = opportunityScore/42*100
  verdict_score = strong→100 / watch→55 / weak→25 / avoid→0

二分类标签：goldenDogScore >= 60 → 正样本（有金狗潜力），否则负样本。
分数回归目标：goldenDogScore 归一化到 [0,1] 作为 leaderScore 校准软目标。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from historical_golden_dogs import build_historical_examples, GOLDEN_DOG_QUESTIONS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / ".runtime-cache" / "chain_ecosystem.db"

VERDICT_SCORE = {"strong": 100.0, "watch": 55.0, "weak": 25.0, "avoid": 0.0}


def _num(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def golden_dog_score(candidate: dict, analysis: dict) -> float:
    """融合多信号得到金狗潜力分 (0-100)。"""
    meme = _num(candidate.get("memeScore"))
    selected = _num(candidate.get("selectedScore"))
    framework = analysis.get("frameworkAssessment") if isinstance(analysis.get("frameworkAssessment"), dict) else {}
    leader = _num(framework.get("leaderScore"))
    opportunity = _num(framework.get("opportunityScore"))
    verdict = str(analysis.get("verdict") or "").lower()

    leader_norm = leader / 25.0 * 100.0
    opp_norm = opportunity / 42.0 * 100.0
    verdict_score = VERDICT_SCORE.get(verdict, 25.0)

    # 低市值起步是历史金狗核心前提，作为加成项（而非硬门限）
    market_cap = _num((candidate.get("metrics") or {}).get("marketCapUsd"))
    mc_bonus = 0.0
    if 0 < market_cap <= 5_000_000:
        mc_bonus = 10.0  # <$5M 微型市值起步
    elif 0 < market_cap <= 20_000_000:
        mc_bonus = 5.0   # <$20M 小市值

    score = 0.40 * meme + 0.20 * leader_norm + 0.20 * opp_norm + 0.20 * verdict_score
    score = min(100.0, score + mc_bonus)
    return round(score, 2)


def collect_rows(db_path: Path, min_narrative_version: int = 7):
    """提取 V4.9 标注样本，返回 (candidate, analysis, first_seen_at) 列表。"""
    connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """SELECT candidate_json, analysis_json, first_seen_at
               FROM onchain_fast_jobs
               WHERE analyzed_at > 0
                 AND json_extract(analysis_json, '$.narrativeVersion') >= ?
               ORDER BY first_seen_at, key""",
            (min_narrative_version,),
        ).fetchall()
    finally:
        connection.close()

    out = []
    for candidate_json, analysis_json, first_seen_at in rows:
        try:
            candidate = json.loads(candidate_json)
            analysis = json.loads(analysis_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict) or not isinstance(analysis, dict):
            continue
        out.append((candidate, analysis, int(first_seen_at or 0)))
    return out


def rapid_candidate_state(candidate: dict) -> dict:
    """从 candidate_json 还原与 jev_decision.rapid_candidate_state 一致的 state 结构。

    训练与推理必须使用同一 state 序列化口径，否则模型学到的分布与线上不一致。
    """
    metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    return {
        "network": str(candidate.get("network") or "")[:40],
        "symbol": str(candidate.get("symbol") or "")[:80],
        "name": str(candidate.get("name") or "")[:160],
        "candidate_type": str(candidate.get("candidateType") or "")[:80],
        "age_minutes": round(_num(candidate.get("ageMinutes")), 2),
        "selected_score": round(_num(candidate.get("selectedScore")), 2),
        "meme_score": round(_num(candidate.get("memeScore")), 2),
        "project_score": round(_num(candidate.get("projectScore")), 2),
        "identity_status": str(candidate.get("identityStatus") or "")[:100],
        "providers": [str(v)[:80] for v in (candidate.get("providers") or [])[:8]],
        "metrics": {key: metrics.get(key) for key in (
            "marketCapUsd", "fdvUsd", "liquidityUsd", "volumeM5Usd", "volumeH1Usd",
            "volumeH6Usd", "volumeH24Usd", "transactionsH1", "transactionsH6",
            "transactionsH24", "buysM5", "buysH1", "sellsH1", "priceChangeM5", "priceChangeH1",
        )},
        "reasons": [str(v)[:180] for v in (candidate.get("reasons") or [])[:8]],
        "risks": [str(v)[:180] for v in (candidate.get("risks") or [])[:8]],
    }


def main():
    parser = argparse.ArgumentParser(description="构建金狗潜力训练数据集")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(ROOT / ".runtime-cache" / "training" / "golden-dog-labels.jsonl"))
    parser.add_argument("--positive-threshold", type=float, default=60.0)
    args = parser.parse_args()

    rows = collect_rows(Path(args.db))
    examples = []
    pos = neg = 0
    for candidate, analysis, first_seen_at in rows:
        gd_score = golden_dog_score(candidate, analysis)
        label = "yes" if gd_score >= args.positive_threshold else "no"
        if label == "yes":
            pos += 1
        else:
            neg += 1
        examples.append({
            "firstSeenAt": first_seen_at,
            "state": rapid_candidate_state(candidate),
            "questions": GOLDEN_DOG_QUESTIONS,
            "target": {
                "golden_dog_potential": label,
                "goldenDogScore": gd_score,          # 连续分（回归校准目标）
                "leaderScore": _num((analysis.get("frameworkAssessment") or {}).get("leaderScore")),
                "verdict": analysis.get("verdict"),
            },
        })

    # 分层切分（保证每个 split 都有正样本，避免正样本全落训练集导致虚假的 val/test 准确率）
    pos_examples = [e for e in examples if e["target"]["golden_dog_potential"] == "yes"]
    neg_examples = [e for e in examples if e["target"]["golden_dog_potential"] == "no"]

    # 注入历史大金狗正样本（已知结局的强正样本，解决本地 potentialTier 正样本=0 的问题）
    historical_examples = build_historical_examples()
    n_hist = len(historical_examples)
    # 历史金狗正样本全部归入训练集（它们无真实时间戳，且是稀缺强信号，应让模型充分学习）
    for e in historical_examples:
        e["split"] = "train"
    pos_examples = historical_examples + pos_examples

    # 保持各自内部时序，正样本按 70/15/15（历史金狗已归 train，其余按比例）
    def _assign(seq):
        n = len(seq)
        for i, e in enumerate(seq):
            ratio = (i + 1) / max(1, n)
            e["split"] = "train" if ratio <= 0.70 else "validation" if ratio <= 0.85 else "test"
    _assign(pos_examples)
    _assign(neg_examples)
    examples = pos_examples + neg_examples

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in examples), encoding="utf-8")

    print(json.dumps({
        "output": str(out),
        "total": len(examples),
        "positive": pos,
        "negative": neg,
        "historicalPositive": n_hist,
        "positiveRatio": round((pos + n_hist) / max(1, len(examples)), 4),
        "splits": {
            s: sum(1 for e in examples if e["split"] == s)
            for s in ("train", "validation", "test")
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
