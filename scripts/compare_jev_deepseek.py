"""JEV vs DeepSeek 对比分析.

输入:
- JEV 结果: .runtime-cache/jev_batch_results.jsonl (每行一条, key=network:contractAddress)
- 评分器 promising: deliverables/trench-board-promising-history-2026-09-24.json (含 chain/symbol/score/...)
- DeepSeek 结果: deliverables/deepseek-final-ranking-2026-09-24.json (S/A/B/C + finalScore)

对齐键: chain:symbol (DeepSeek 无 contractAddress, 只能按链+symbol 对齐)

对比维度:
1. JEV priority (deep-research/watch/reject) vs DeepSeek grade (S/A/B/C)
2. JEV goodCandidateProbability vs DeepSeek finalScore
3. 一致率 / 分歧点分析
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / ".runtime-cache" / "jev_batch_results.jsonl"
PROMISING_PATH = ROOT / "deliverables" / "trench-board-promising-history-2026-09-24.json"
DEEPSEEK_FINAL = ROOT / "deliverables" / "deepseek-final-ranking-2026-09-24.json"
OUT_PATH = ROOT / "deliverables" / "jev-vs-deepseek-2026-09-27.json"


def key_of(chain, symbol):
    return f"{str(chain or '').lower()}:{str(symbol or '').upper().strip()}"


def load_jev():
    out = {}
    if RESULTS_PATH.exists():
        for line in open(RESULTS_PATH, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            # key = network:contractAddress
            out[o.get("key", "")] = o
    return out


def load_promising():
    d = json.load(open(PROMISING_PATH, encoding="utf-8"))
    return d["promising"]


def load_deepseek():
    d = json.load(open(DEEPSEEK_FINAL, encoding="utf-8"))
    ranking = d["ranking"] or []
    by_key = {}
    for r in ranking:
        k = key_of(r.get("chain"), r.get("symbol"))
        by_key.setdefault(k, r)
    return by_key, d


def grade_to_tier(grade):
    return {"S": "强", "A": "强", "B": "中", "C": "弱"}.get(grade, "未知")


def priority_to_tier(priority):
    return {"deep-research": "强", "watch": "中", "reject": "弱"}.get(priority, "未知")


def main():
    jev = load_jev()
    promising = load_promising()
    ds, ds_meta = load_deepseek()

    print(f"JEV 结果: {len(jev)} 条")
    print(f"评分器 promising: {len(promising)} 条")
    print(f"DeepSeek final: {len(ds)} 条")

    # 建立 promising 的 chain:symbol -> promising 索引 (含 CA)
    p_by_key = {}
    for p in promising:
        k = key_of(p.get("chain"), p.get("symbol"))
        # 保留多条时取 score 最高的
        if k not in p_by_key or p.get("score", 0) > p_by_key[k].get("score", 0):
            p_by_key[k] = p

    # 建立 jev 的 CA -> 结果, 并通过 promising 反查 chain:symbol
    # jev key = network:contractAddress
    jev_ca_to_chain_symbol = {}
    for p in promising:
        chain = str(p.get("chain") or "").lower()
        ca = str(p.get("contractAddress") or "")
        if ca:
            jev_ca_to_chain_symbol[f"{chain}:{ca}"] = key_of(chain, p.get("symbol"))

    # 交叉对齐
    rows = []
    # 遍历 promising, 每个都尝试对齐 JEV 和 DeepSeek
    for k, p in p_by_key.items():
        chain = str(p.get("chain") or "").lower()
        ca = str(p.get("contractAddress") or "")
        jev_res = jev.get(f"{chain}:{ca}")
        ds_res = ds.get(k)
        if jev_res is None and ds_res is None:
            continue
        rows.append({
            "key": k,
            "chain": chain,
            "symbol": p.get("symbol"),
            "name": p.get("name"),
            "score": p.get("score"),
            "candidateType": p.get("candidateType"),
            "smartMoney": p.get("smartMoneyHolders"),
            "kol": p.get("kolHolders"),
            "liquidity": p.get("liquidityUsd"),
            "jevPriority": jev_res.get("priority") if jev_res else None,
            "jevConfidence": jev_res.get("confidence") if jev_res else None,
            "jevGoodProb": jev_res.get("goodCandidateProbability") if jev_res else None,
            "dsGrade": ds_res.get("grade") if ds_res else None,
            "dsScore": ds_res.get("finalScore") if ds_res else None,
            "dsNarrative": ds_res.get("narrative") if ds_res else None,
            "dsCategory": ds_res.get("category") if ds_res else None,
        })

    both = [r for r in rows if r["jevPriority"] and r["dsGrade"]]
    only_jev = [r for r in rows if r["jevPriority"] and not r["dsGrade"]]
    only_ds = [r for r in rows if r["dsGrade"] and not r["jevPriority"]]

    print(f"\n对齐统计:")
    print(f"  两者都有: {len(both)}")
    print(f"  仅 JEV: {len(only_jev)}")
    print(f"  仅 DeepSeek: {len(only_ds)}")

    # JEV priority 分布
    print(f"\nJEV priority 分布 (对齐集): {dict(Counter(r['jevPriority'] for r in both))}")
    print(f"DeepSeek grade 分布 (对齐集): {dict(Counter(r['dsGrade'] for r in both))}")

    # 交叉表: JEV priority vs DeepSeek grade
    print(f"\n=== 交叉表 JEV priority × DeepSeek grade ===")
    cross = defaultdict(Counter)
    for r in both:
        cross[r["jevPriority"]][r["dsGrade"]] += 1
    grades = ["S", "A", "B", "C"]
    prios = ["deep-research", "watch", "reject"]
    header = "            " + "  ".join(f"{g:>4}" for g in grades)
    print(header)
    for pr in prios:
        line = f"{pr:>12} " + "  ".join(f"{cross[pr].get(g,0):>4}" for g in grades)
        print(line)

    # 一致率: JEV deep-research 命中 DeepSeek S/A 的比例
    dr = [r for r in both if r["jevPriority"] == "deep-research"]
    dr_hit_sa = [r for r in dr if r["dsGrade"] in ("S", "A")]
    print(f"\nJEV deep-research 数: {len(dr)}, 其中 DeepSeek S/A 命中 {len(dr_hit_sa)} ({len(dr_hit_sa)/len(dr)*100:.1f}%)" if dr else "\nJEV deep-research 数: 0")

    # DeepSeek S/A 被 JEV 判为什么
    sa = [r for r in both if r["dsGrade"] in ("S", "A")]
    sa_jev = Counter(r["jevPriority"] for r in sa)
    print(f"DeepSeek S/A 数: {len(sa)}, JEV 对其判断: {dict(sa_jev)}")

    # 输出
    out = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": {
            "promising": len(promising),
            "jevJudged": len(jev),
            "deepseekRanked": len(ds),
            "aligned": len(both),
            "onlyJev": len(only_jev),
            "onlyDeepseek": len(only_ds),
        },
        "crossTable": {pr: dict(cross[pr]) for pr in prios},
        "jevPriorityDist": dict(Counter(r["jevPriority"] for r in both)),
        "dsGradeDist": dict(Counter(r["dsGrade"] for r in both)),
        "rows": rows,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写 {OUT_PATH}")


if __name__ == "__main__":
    main()
