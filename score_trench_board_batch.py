"""战壕榜批量本地评分 — 零 AI 消耗, 纯本地规则.

读取 .runtime-cache/api_gmgn-trenches-hot-board-v8.json (1854 条),
套用 GMGN contract-dd 0-100 安全评分框架 + 聪明钱/情绪信号,
筛出「promising」(值得进 DeepSeek 深度研判) 的标的。

不调 gmgn-cli (数据已在缓存), 不调 DeepSeek。
用法: python score_trench_board_batch.py [--top N] [--out path.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# 复用 AIXBT 情绪判断 (不调 AI, 只读免费 topics 端点)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gmgn_local_trench_analyzer import _aixbt_topic_attention_signal  # noqa: E402

CACHE_PATH = Path(__file__).resolve().parent / ".runtime-cache" / "api_gmgn-trenches-hot-board-v8.json"
MARKET_HOT_PATH = Path(__file__).resolve().parent / ".runtime-cache" / "api_market-hot.json"


def _load_binance_wallet_hot_symbols() -> set[str]:
    """读取币安钱包热门榜的 symbol 集合 (最强热度背书: 链外注意力已公共化)."""
    try:
        d = json.load(open(MARKET_HOT_PATH, encoding="utf-8"))
        for s in d.get("sources") or []:
            if s.get("id") == "binance-wallet-hot":
                return {str(r.get("symbol") or "").upper() for r in (s.get("rows") or []) if r.get("symbol")}
    except Exception:
        pass
    return set()

# 本地评分阈值
AI_ASSIST_MIN_LOCAL_SCORE = int(os.getenv("GMGN_LOCAL_AI_ASSIST_MIN_SCORE", "60"))
AI_ASSIST_REQUIRE_SIGNAL = os.getenv("GMGN_LOCAL_AI_ASSIST_REQUIRE_SIGNAL", "1") == "1"


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _flag(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"true", "1", "yes"}


def score_trench_row(row: dict[str, Any], bw_hot_symbols: set[str] | None = None) -> dict[str, Any]:
    """对单条战壕榜 row 做本地评分 (数据来自缓存, 零 AI)."""
    lf = row.get("launchFacts") or {}
    m = row.get("metrics") or {}
    fs = row.get("filterSignals") or {}
    wp = row.get("walletProfile") or {}

    symbol = str(row.get("symbol") or "")
    name = str(row.get("name") or symbol)
    chain = str(row.get("network") or row.get("chain") or "").lower()
    # 生态币 = 项目方刚发的币 (candidateType=project), 区别于散户 meme (candidateType=meme)
    is_project = str(row.get("candidateType") or "") == "project"
    is_og = _flag((row.get("launchFacts") or {}).get("isOg")) or _flag((row.get("filterSignals") or {}).get("isOg"))
    nc = row.get("narrativeContext") or {}
    websites = nc.get("websites") if isinstance(nc.get("websites"), list) else []
    socials = nc.get("socials") if isinstance(nc.get("socials"), list) else []

    score = 100
    red_flags: list[str] = []
    signals: list[str] = []       # 强信号: 可单独撑起 promising
    soft_signals: list[str] = []  # 弱信号: 仅补充展示, 不能单独作为 AI 依据

    # --- 硬闸 ---
    honeypot = _flag(lf.get("honeypot")) or _flag(fs.get("honeypot"))
    wash = _flag(lf.get("washTrading")) or _flag(m.get("washTrading")) or _flag(fs.get("washTrading"))
    if honeypot:
        red_flags.append("疑似 Honeypot")
        score = 0
    if wash:
        red_flags.append("疑似对刷 wash trading")
        score = max(0, score - 40)

    # --- 合约安全 (税 / 开源 / rug 标签) ---
    buy_tax = _num(lf.get("buyTaxPercent"))
    sell_tax = _num(lf.get("sellTaxPercent"))
    max_tax = max(buy_tax, sell_tax)
    if max_tax > 10:
        score -= 25
        red_flags.append(f"交易税过高 {max_tax:.0f}%")
    elif max_tax > 5:
        score -= 10
        red_flags.append(f"交易税偏高 {max_tax:.0f}%")

    open_source = fs.get("openSource")
    if open_source is False:
        score -= 6
        red_flags.append("非开源合约")

    rug_ratio = _num(lf.get("rugRatio"))
    if rug_ratio >= 0.5:
        score = min(score, 59)
        red_flags.append(f"GMGN rug 标签 {rug_ratio:.2f}")
    elif rug_ratio >= 0.3:
        score = min(score, 79)
        red_flags.append(f"GMGN rug 偏高 {rug_ratio:.2f}")

    # --- 流动性 (复合判定: migrated 币流动性字段常失真, 交易活跃度才是真信号) ---
    liquidity = _num(m.get("liquidityUsd"))
    vol24 = _num(m.get("volumeH24Usd"))
    tx24 = int(_num(m.get("transactionsH24")))
    if liquidity < 5_000:
        if vol24 >= 100_000 or tx24 >= 1_000:
            # 高成交量/高笔数 = 活跃币, 流动性字段失真 (GMGN 未追踪迁移后新池)
            red_flags.append(f"流动性字段失真(${liquidity:,.0f}) 但日成交${vol24:,.0f}/{tx24}笔")
        else:
            # 真死池: 流动性低 且 交易清淡
            score -= 25
            red_flags.append(f"死池流动性 ${liquidity:,.0f}")
    elif liquidity < 10_000:
        score -= 15
        red_flags.append(f"流动性偏低 ${liquidity:,.0f}")
    elif liquidity < 50_000:
        score -= 6

    # --- 持仓结构 ---
    top10 = _num(m.get("top10HolderPercent") or lf.get("top10Percent"))
    if top10 > 50:
        score -= 25
        red_flags.append(f"前10持仓集中 {top10:.0f}%")
    elif top10 > 30:
        score -= 14
        red_flags.append(f"前10持仓 {top10:.0f}%")
    elif top10 > 20:
        score -= 6

    holders = int(_num(m.get("holders") or lf.get("holders")))
    if holders and holders < 200:
        score -= 12
        red_flags.append(f"持有人极少 {holders}")
    elif holders and holders < 500:
        score -= 5

    # --- 老鼠仓/捆绑/夹子/机器人 ---
    stat_checks = [
        ("ratTraderRate", 5, 12, "老鼠仓占比过高"),
        ("bundlerHoldingPercent", 30, 20, "捆绑持仓占比过高"),
        ("insiderPercent", 5, 15, "内部人持仓过高"),
        ("botWalletPercent", 70, 12, "机器人持仓占比过高"),
        ("freshWalletPercent", 50, 8, "新钱包占比过高"),
    ]
    for field, threshold, penalty, label in stat_checks:
        value = _num(lf.get(field))
        if value > threshold:
            score -= penalty
            red_flags.append(f"{label} {value:.0f}%")

    # --- 正向信号 ---
    smart_holders = int(_num(m.get("smartMoneyHolders") or lf.get("smartMoneyHolders")))
    kol_holders = int(_num(m.get("kolHolders") or lf.get("kolHolders")))
    if smart_holders > 0:
        signals.append(f"聪明钱持仓 {smart_holders}")
    if kol_holders > 0:
        signals.append(f"KOL 持仓 {kol_holders}")
    # 生态币的项目方背书信号 (meme 没有这些)
    if is_project:
        if is_og:
            signals.append("OG 项目方发币")
        if websites:
            signals.append(f"有官网({len(websites)})")
        elif socials:
            signals.append(f"有社交({len(socials)})")
    # 刚迁移早期: 流动性已到位但还没聪明钱/成交 (项目方刚建好池, 最早期 alpha 窗口)
    # 无偏回测已坐实: 「首次被扫链发现那一刻」流动性≥$50K 命中率 67.1% (对照 2.5%), 24h成交≥$50K 命中 80.9% ——
    # 流动性/成交是【最早】的信号, 比聪明钱/KOL 更早, 必须作为首要正向信号 (而非聪明钱的补充)。
    # 流动性 >= $50K, 或 >= $25K(holders 缺失但市值+流动性证明真实资金), 或 >= $10K 且有 holders = 项目方认真做盘
    holder_count_v = int(_num(m.get("holders") or lf.get("holders")))
    early_liquidity = (
        liquidity >= 50_000
        or (liquidity >= 25_000 and holder_count_v == 0)
        or (liquidity >= 10_000 and holder_count_v >= 100)
    )
    # 无偏回测最强信号阈值: 流动性≥$50K 或 24h成交≥$50K 单独即可撑起 promising (不依赖聪明钱/KOL)
    early_liquidity_strong = liquidity >= 50_000 or vol24 >= 50_000
    if early_liquidity and not red_flags:
        signals.append(f"早期流动性就绪 ${liquidity:,.0f}/{holder_count_v}人")
    elif early_liquidity_strong:
        # 无偏回测坐实流动性/成交是最早且最强的信号, 即使有非致命红旗(如持有人少)也不该被淹没
        signals.append(f"早期流动性就绪 ${liquidity:,.0f} (日成交${vol24:,.0f})")
    # 交易活跃度本身是信号 (成熟热门币 SM 常为 0, 但成交活跃就是热度证据)
    # 两档: 高活跃(可单独撑 promising) / 普通活跃(仅补充, 不能单独作为 AI 依据)
    # 小盘高换手: 小市值 + 持有人少 + 高频交易 = 战壕 OG 币典型形态 (如 STAMP 39人/1666笔)
    turnover_ratio = tx24 / max(holder_count_v, 1)
    high_activity = vol24 >= 500_000 or tx24 >= 3_000 or (tx24 >= 1_500 and turnover_ratio >= 40)
    mid_activity = vol24 >= 100_000 or tx24 >= 1_000
    if high_activity:
        if turnover_ratio >= 20 and holder_count_v and holder_count_v <= 80:
            signals.append(f"小盘高换手 {holder_count_v}人/{tx24}笔(换手{turnover_ratio:.0f}x)")
        else:
            signals.append(f"高活跃 日成交${vol24:,.0f}/{tx24}笔")
    elif mid_activity:
        soft_signals.append(f"交易活跃 日成交${vol24:,.0f}/{tx24}笔")
    # 币安钱包热榜交叉命中 = 最强链外注意力背书
    if bw_hot_symbols and symbol.upper() in bw_hot_symbols:
        signals.append("币安钱包热榜在榜")

    # AIXBT 情绪/注意力 (免费, 命中热点才加)
    narrative = _aixbt_topic_attention_signal(symbol, name)
    if narrative.get("matched"):
        if narrative.get("attention") == "formed":
            signals.append(f"AIXBT注意力聚焦({narrative.get('dominant','')})")
        else:
            signals.append(f"AIXBT注意力汇聚中({narrative.get('dominant','')})")

    score = max(0, min(100, score))

    # --- 结论 ---
    # 致命红旗: meme 币对老鼠仓/捆绑/集中/rug 零容忍; 生态币(项目方发币)这些是刚发币的正常形态,
    # 只有真致命项(Honeypot/对刷/交易税过高/死池)才一票否决, 其余降为提示性红旗
    if is_project:
        fatal_terms = ("Honeypot", "对刷", "交易税过高", "死池")
    else:
        fatal_terms = ("Honeypot", "对刷", "交易税过高", "rug 标签", "老鼠仓", "捆绑", "内部人", "前10持仓集中", "死池")
    fatal_flags = any(f in "".join(red_flags) for f in fatal_terms)
    if score <= 0 or fatal_flags:
        verdict = "avoid"
    elif score < 45 or (red_flags and score < 55):
        verdict = "avoid"
    elif score < 70:
        verdict = "weak"
    else:
        verdict = "watch"

    # 生态币: 项目方背书(OG/官网/社交)本身即可作为「有苗头」依据, 不必强求聪明钱信号
    has_project_backing = is_project and (is_og or websites or socials)
    promising = (
        verdict == "watch"
        and not fatal_flags
        and score >= AI_ASSIST_MIN_LOCAL_SCORE
        and (bool(signals) or has_project_backing or not AI_ASSIST_REQUIRE_SIGNAL)
    )

    # 信号分层: 强 = 外部注意力证据 (聪明钱/KOL/币安热榜/AIXBT/OG项目方) + 无偏回测最强信号(早期流动性/成交≥$50K),
    #          中 = 放量/形态, 弱 = 其他
    sig_blob = "、".join(signals)
    nar_matched = bool(narrative.get("matched"))
    if (smart_holders >= 10 or kol_holders >= 5 or nar_matched or "币安钱包热榜" in sig_blob
            or "OG 项目方" in sig_blob or early_liquidity_strong):
        tier = "强"
    elif smart_holders >= 3 or kol_holders >= 2 or "高活跃" in sig_blob or "小盘高换手" in sig_blob or has_project_backing:
        tier = "中"
    else:
        tier = "弱"

    return {
        "symbol": symbol,
        "name": name,
        "chain": chain,
        "contractAddress": str(row.get("contractAddress") or ""),
        "candidateType": "project" if is_project else "meme",
        "isProject": is_project,
        "isOg": is_og,
        "score": score,
        "verdict": verdict,
        "promising": promising,
        "tier": tier,
        "redFlags": red_flags,
        "signals": signals + soft_signals,
        "narrative": narrative,
        "marketCapUsd": _num(m.get("marketCapUsd")),
        "liquidityUsd": liquidity,
        "volumeH24Usd": _num(m.get("volumeH24Usd")),
        "holders": holders,
        "top10Percent": top10,
        "smartMoneyHolders": smart_holders,
        "kolHolders": kol_holders,
        "rugRatio": rug_ratio,
        "maxTax": round(max_tax, 1),
        "launchStage": str(lf.get("stage") or row.get("launchStage") or ""),
        "ageMinutes": _num(row.get("ageMinutes")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=0, help="只输出前 N 个 promising")
    parser.add_argument("--out", type=str, default="", help="输出 JSON 路径")
    parser.add_argument("--db", type=str, default="", help="额外读数据库历史成员 (chain_ecosystem.db 副本路径)")
    parser.add_argument("--include-history", action="store_true", help="合并数据库历史战壕榜成员 (已滑出当前榜的币)")
    args = parser.parse_args()

    data = json.load(open(CACHE_PATH, encoding="utf-8"))
    rows = data.get("rows") or []
    bw_hot = _load_binance_wallet_hot_symbols()

    # 合并数据库历史成员 (已滑出当前滚动榜但仍在 onchain_fast_jobs 里的战壕榜成员)
    if args.include_history:
        db_path = args.db or ".runtime-cache/tmp_ecosystem_snapshot/chain_ecosystem.db"
        try:
            import sqlite3 as _sq
            conn = _sq.connect(db_path, timeout=30)
            conn.row_factory = _sq.Row
            hist_rows = conn.execute(
                "SELECT candidate_json FROM onchain_fast_jobs "
                "WHERE json_extract(candidate_json,'$.gmgnTrenchBoardMember')=1 "
                "ORDER BY first_seen_at DESC"
            ).fetchall()
            conn.close()
            # 按 contract 去重合并 (快照里已有的优先)
            seen_ca = {str(r.get("contractAddress") or "") for r in rows if r.get("contractAddress")}
            merged = 0
            for r in hist_rows:
                c = json.loads(r["candidate_json"]) if isinstance(r["candidate_json"], str) else r["candidate_json"]
                ca = str(c.get("contractAddress") or c.get("contract") or "")
                if ca and ca not in seen_ca:
                    seen_ca.add(ca)
                    rows.append(c)
                    merged += 1
            print(f"合并数据库历史成员: +{merged} 个 (快照 {len(data.get('rows') or [])} + 历史)", flush=True)
        except Exception as exc:
            print(f"[警告] 读历史成员失败, 只用快照: {str(exc)[:120]}", flush=True)

    print(f"战壕榜共 {len(rows)} 条，开始本地评分（零 AI）... 币安钱包热榜参考 {len(bw_hot)} 个 symbol", flush=True)

    scored = []
    for row in rows:
        s = score_trench_row(row, bw_hot)
        scored.append(s)

    # 统计
    from collections import Counter
    verdict_dist = Counter(s["verdict"] for s in scored)
    promising = [s for s in scored if s["promising"]]
    print(f"\n=== 本地评分结果 ===")
    print(f"  总评分: {len(scored)}")
    print(f"  verdict 分布: {dict(verdict_dist)}")
    print(f"  promising (值得进 DeepSeek): {len(promising)}")

    # 按分数排序
    promising.sort(key=lambda s: (-s["score"], -s["smartMoneyHolders"] - s["kolHolders"]))
    if args.top:
        promising = promising[:args.top]

    print(f"\n=== 值得 DeepSeek 深度研判的标的 ({len(promising)}) ===")
    for i, s in enumerate(promising, 1):
        sig = "、".join(s["signals"]) or "无显性信号"
        print(f"{i:>3}. {s['symbol']:14} {s['chain']:10} 分={s['score']:>3} "
              f"市值=${s['marketCapUsd']:,.0f} 流动性=${s['liquidityUsd']:,.0f} "
              f"SM={s['smartMoneyHolders']} KOL={s['kolHolders']}")
        print(f"     信号: {sig}")

    if args.out:
        out = {
            "scoredCount": len(scored),
            "promisingCount": len(promising),
            "verdictDist": dict(verdict_dist),
            "promising": promising,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.out}")


if __name__ == "__main__":
    main()
