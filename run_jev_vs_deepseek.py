"""JEV 批量判断评分器 promising 标的 + 与 DeepSeek 结果对比。

流程:
1. 读 deliverables/trench-board-promising-history-2026-09-24.json (1405 promising)
2. 按 contractAddress 从 chain_ecosystem.db 重建完整 row (含 metrics/frameworkSnapshot/researchEvidence)
   - 缺失的 153 个用评分器扁平字段降级构造 (frameworkSnapshot/researchEvidence 留空走兜底)
3. 调 jev_decision.analyze_jev_candidates 批量判断 (deep-research/watch/reject + goodCandidateProbability)
   - 断点续跑: 结果落盘 .runtime-cache/jev_batch_results.jsonl, 已判过的跳过
4. 与 DeepSeek 结果 (online-research-ranking-v4-2026-09-24.json / deepseek-final-ranking-2026-09-24.json) 对比

用法: python run_jev_vs_deepseek.py [--concurrency N] [--batch N] [--out path]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 加载 .env
def _load_env():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from jev_decision import analyze_jev_candidates  # noqa: E402

PROMISING_PATH = ROOT / "deliverables" / "trench-board-promising-history-2026-09-24.json"
DB_PATH = ROOT / ".runtime-cache" / "chain_ecosystem.db"
DEEPSEEK_V4 = ROOT / "deliverables" / "online-research-ranking-v4-2026-09-24.json"
DEEPSEEK_FINAL = ROOT / "deliverables" / "deepseek-final-ranking-2026-09-24.json"
RESULTS_PATH = ROOT / ".runtime-cache" / "jev_batch_results.jsonl"


def load_db_rows():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    db_rows = {}
    n = 0
    for r in conn.execute("SELECT candidate_json FROM onchain_fast_jobs WHERE candidate_json IS NOT NULL"):
        try:
            c = json.loads(r["candidate_json"]) if isinstance(r["candidate_json"], str) else r["candidate_json"]
        except Exception:
            continue
        ca = c.get("contractAddress") or c.get("contract")
        if ca:
            db_rows[ca] = c
            n += 1
    conn.close()
    return db_rows


def build_fallback_row(p):
    """用评分器扁平字段构造 JEV 可接受的降级 row。"""
    chain = p.get("chain") or ""
    return {
        "network": chain,
        "contractAddress": p.get("contractAddress") or "",
        "symbol": p.get("symbol") or "",
        "name": p.get("name") or "",
        "candidateType": p.get("candidateType") or "meme",
        "ageMinutes": p.get("ageMinutes") or 0,
        "selectedScore": p.get("score") or 0,
        "metrics": {
            "marketCapUsd": p.get("marketCapUsd"),
            "liquidityUsd": p.get("liquidityUsd"),
            "volumeH24Usd": p.get("volumeH24Usd"),
            "holders": p.get("holders"),
            "top10Percent": p.get("top10Percent"),
        },
        "walletProfile": {
            "coverage": p.get("smartMoneyHolders"),
            "independentHolders": p.get("holders"),
            "top10Percent": p.get("top10Percent"),
        },
        # frameworkSnapshot / researchEvidence 留空, rapid_candidate_state 有兜底
    }


def rebuild_rows(promising, db_rows):
    rebuilt, missing = [], []
    db_lower = {k.lower(): k for k in db_rows}
    for p in promising:
        ca = p.get("contractAddress") or ""
        full = None
        if ca in db_rows:
            full = db_rows[ca]
        elif ca and ca.lower() in db_lower:
            full = db_rows[db_lower[ca.lower()]]
        if full:
            rebuilt.append(full)
        else:
            rebuilt.append(build_fallback_row(p))
            missing.append(p.get("symbol"))
    return rebuilt, missing


def load_done():
    done = {}
    if RESULTS_PATH.exists():
        for line in open(RESULTS_PATH, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                done[o.get("key", "")] = o
            except Exception:
                continue
    return done


def load_deepseek():
    out = {}
    for path in (DEEPSEEK_V4, DEEPSEEK_FINAL):
        if not path.exists():
            continue
        d = json.load(open(path, encoding="utf-8"))
        ranking = d.get("ranking") or []
        for r in ranking:
            ca = r.get("contractAddress")
            if not ca:
                # 有些 ranking 可能只有 symbol/chain, 无 CA, 用 chain:symbol 做弱键
                continue
            out.setdefault(ca, r)
    # 同时建立 symbol 弱键映射 (chain:symbol)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--batch", type=int, default=0, help="只跑前 N 个 (0=全量)")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    os.environ["JEV_DECISION_CONCURRENCY"] = str(args.concurrency)

    print("== 1. 加载评分器 promising ==", flush=True)
    d = json.load(open(PROMISING_PATH, encoding="utf-8"))
    promising = d["promising"]
    print(f"   promising 总数: {len(promising)}", flush=True)

    print("== 2. 重建完整 row ==", flush=True)
    db_rows = load_db_rows()
    print(f"   DB candidate_json 行数: {len(db_rows)}", flush=True)
    rows, missing = rebuild_rows(promising, db_rows)
    print(f"   重建: {len(rows)} (DB 命中 {len(rows)-len(missing)}, 降级 {len(missing)})", flush=True)

    done = load_done()
    print(f"   已有断点结果: {len(done)} 条", flush=True)

    # 找出待判的
    todo = []
    for row in rows:
        key = f"{row.get('network') or ''}:{row.get('contractAddress') or ''}"
        if key in done:
            continue
        todo.append(row)

    if args.batch:
        todo = todo[: args.batch]

    print(f"== 3. JEV 批量判断: 待判 {len(todo)} ==", flush=True)
    t0 = time.time()
    done_count = 0
    # 小批 + 批间 sleep, 避免触发 JEV 429 限流; 失败标的单条重试一次后放弃
    BATCH = 20
    SLEEP_BETWEEN = float(os.getenv("JEV_BATCH_SLEEP_S", "1.5"))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i : i + BATCH]
        res = analyze_jev_candidates(chunk)
        # 单条补重试: 未返回的 key 单独重试一次
        got_keys = set(res.keys())
        want_keys = {f"{r.get('network') or ''}:{r.get('contractAddress') or ''}" for r in chunk}
        retry = [r for r in chunk if f"{r.get('network') or ''}:{r.get('contractAddress') or ''}" not in got_keys]
        if retry:
            res2 = analyze_jev_candidates(retry)
            res.update(res2)
        with open(RESULTS_PATH, "a", encoding="utf-8") as f:
            for k, v in res.items():
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        done_count += len(res)
        elapsed = time.time() - t0
        rate = done_count / elapsed if elapsed > 0 else 0
        eta = (len(todo) - done_count) / rate if rate > 0 else 0
        print(f"   [{done_count}/{len(todo)}] 本轮 {len(res)} 条, {elapsed:.0f}s, 速率 {rate:.2f}/s, ETA {eta/60:.1f}min", flush=True)
        time.sleep(SLEEP_BETWEEN)

    print("== 4. 汇总结果 ==", flush=True)
    all_res = load_done()
    print(f"   总判断数: {len(all_res)}", flush=True)
    dist = Counter(v.get("priority") for v in all_res.values())
    print(f"   priority 分布: {dict(dist)}", flush=True)

    # 保存汇总 JSON
    out_path = args.out or str(ROOT / "deliverables" / "jev-batch-results-2026-09-27.json")
    summary = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "promisingCount": len(promising),
        "judgedCount": len(all_res),
        "missingFallbackCount": len(missing),
        "priorityDist": dict(dist),
        "results": list(all_res.values()),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"   已写 {out_path}", flush=True)


if __name__ == "__main__":
    main()
