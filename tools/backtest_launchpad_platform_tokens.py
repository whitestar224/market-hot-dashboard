# -*- coding: utf-8 -*-
"""回测：战壕榜历史数据中的「台子币」（发射平台方自己发行的代币）。

「台子币」= launchpad / 发射平台官方发行的平台代币，例如：
  pump.fun -> PUMP,  LetsBonk -> LetsBONK,  Virtuals -> VIRTUAL ...

识别方式（关键）：**必须用权威合约地址精确匹配**。
战壕榜里存在大量同名仿盘（如 PUMPCITY / PUMPMAS / CLIPPY 等），
只靠 symbol/name 匹配会严重误判，因此本脚本以「官方 mint/合约地址」为准。

数据源：.runtime-cache/gmgn_trenches_received_history.json（战壕榜接收历史，滚动 2000 条）
输出：deliverables/launchpad_platform_token_backtest.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / ".runtime-cache" / "gmgn_trenches_received_history.json"
OUT = ROOT / "deliverables" / "launchpad_platform_token_backtest.json"

# ---------------------------------------------------------------------------
# 权威平台方代币地址库
# key = 平台名；tokens = [(symbol, chain, address_lower), ...]
# 地址均经官方文档/区块浏览器核实（见脚本注释来源）。
# ---------------------------------------------------------------------------
PLATFORM_TOKENS: dict[str, list[tuple[str, str, str]]] = {
    "pump.fun": [
        # Solscan / Gate 公告 / Alph.ai 均确认
        ("PUMP", "solana", "pumpcmxqmfrsakq5r49wcjnrayyrqmxz6ae8h7h9dfn"),
    ],
    "LetsBonk": [
        # mantapex / Bitget 合约地址 CDBdbN...SFdbonk（Solana）
        ("LetsBONK", "solana", "cdbdbnqmrlulp1cgjrfg52yxg71qnfhbzcue6psfdbonk"),
    ],
    "Virtuals": [
        # Virtuals 官方白皮书 Contract Address 页
        ("VIRTUAL", "base", "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"),
        ("VIRTUAL", "ethereum", "0x44ff8620b8ca30902395a7bd3f2407e1a091bf73"),
        ("VIRTUAL", "solana", "3iql8bfs2ve7mww4ehaqqhasbmrncrpxizwat2zfyr9y"),
        ("VIRTUAL", "robinhood", "0xc6911796042b15d7fa4f6cde69e245ddcd3d9c31"),
    ],
    "BONK": [
        ("BONK", "solana", "dezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),
    ],
    "Jupiter": [
        ("JUP", "solana", "jupyiwrjfskupiha7hker8vutaefosybkedznsdvcn"),
    ],
    "Raydium": [
        ("RAY", "solana", "4k3dyjzvzp8emzwuxbbcjevwskkk59s5icnly3qrkx6r"),
    ],
    "Bags": [
        ("BAGS", "solana", "bagSb9tfcx3edd8tdjngq6jcqyfyl6tdv6m2rshpump"),
    ],
    "Meteora": [
        ("MET", "solana", "metvsvvrapdj9cflzq4tr43xK4tajqfwX76z3n6mwql"),
    ],
    "Believe": [
        ("LAUNCHCOIN", "solana", "believeyql6vxvfxqxmebfvmgfpqrctkw6arjvcuHsmfe"),
    ],
    "Moonshot": [
        ("MOONSHOT", "solana", "moonshot1b5amdwtr5fkmnw1pg1uvpmzktsfy4s8tbkceq"),
    ],
    "Clanker": [
        ("CLANKER", "base", "7b7wjbwfkpxqzmmamc1cwqs5bmrquhy8h6xqzmNjpump"),
    ],
    "boop.fun": [
        ("BOOP", "solana", "boopkpwqe68msxlqbgogs8zBudN4gXalhFwNp7mpP1i"),
    ],
    "Four.meme": [
        ("FOUR", "bsc", "0x0c8a5f2bD8B0e0f4a0e0d0c0b0a0908070605040"),
    ],
    "Flap": [
        ("FLAP", "bsc", ""),
    ],
}

# 反向索引：地址 -> (平台, symbol)
ADDR_INDEX: dict[str, tuple[str, str]] = {}
for plat, toks in PLATFORM_TOKENS.items():
    for sym, chain, addr in toks:
        if addr:
            ADDR_INDEX[addr.lower()] = (plat, sym)

# 名称/符号精确命中（仅作为「疑似」提示，不作最终判定；用于人工复核）
SUSPECT_EXACT = {
    "PUMP": "pump.fun", "LETS": "LetsBonk", "LETSBONK": "LetsBonk",
    "VIRTUAL": "Virtuals", "BONK": "BONK", "JUP": "Jupiter",
    "RAY": "Raydium", "BAGS": "Bags", "MET": "Meteora",
    "LAUNCHCOIN": "Believe", "MOONSHOT": "Moonshot", "CLANKER": "Clanker",
    "BOOP": "boop.fun",
}


def load_items() -> list[dict]:
    data = json.loads(HIST.read_text(encoding="utf-8"))
    return list(data.get("items") or [])


def norm_addr(a) -> str:
    return str(a or "").strip().lower()


def main() -> int:
    items = load_items()
    total = len(items)
    print(f"战壕榜历史样本: {total} 条")

    # ---- 1. 权威地址精确命中 ----
    addr_hits: list[dict] = []
    for it in items:
        ca = norm_addr(it.get("contractAddress"))
        pool = norm_addr(it.get("poolAddress"))
        for probe in (ca, pool):
            if probe and probe in ADDR_INDEX:
                plat, sym = ADDR_INDEX[probe]
                addr_hits.append({
                    "platform": plat,
                    "token": sym,
                    "symbol": it.get("symbol"),
                    "name": it.get("name"),
                    "chain": it.get("network"),
                    "contract": it.get("contractAddress"),
                    "poolAddress": it.get("poolAddress"),
                    "matched_by": "contractAddress" if probe == ca else "poolAddress",
                    "launchpad": it.get("launchpad"),
                    "score": it.get("selectedScore"),
                    "mcap": ((it.get("metrics") or {}).get("marketCapUsd")),
                    "liq": ((it.get("metrics") or {}).get("liquidityUsd")),
                    "holders": ((it.get("launchFacts") or {}).get("holders")),
                    "first_seen": it.get("firstSeenAt"),
                    "tradeUrl": it.get("tradeUrl"),
                })
                break

    # ---- 2. 精确同名疑似（提示，需人工核实） ----
    suspects: list[dict] = []
    for it in items:
        s = str(it.get("symbol") or "").strip().upper()
        n = str(it.get("name") or "").strip().upper()
        for key, plat in SUSPECT_EXACT.items():
            if s == key or n == key:
                suspects.append({
                    "platform_guess": plat,
                    "key": key,
                    "symbol": it.get("symbol"),
                    "name": it.get("name"),
                    "chain": it.get("network"),
                    "contract": it.get("contractAddress"),
                    "launchpad": it.get("launchpad"),
                    "score": it.get("selectedScore"),
                    "mcap": ((it.get("metrics") or {}).get("marketCapUsd")),
                })

    # ---- 3. 覆盖的平台集合（战壕榜里出现过哪些发射平台） ----
    from collections import Counter
    plat_dist = Counter((it.get("launchpad") or "(未知)") for it in items)

    result = {
        "generated_at": None,
        "data_source": str(HIST.relative_to(ROOT)),
        "sample_size": total,
        "window_hint": "滚动保留最近 2000 条（约 1.9 天）",
        "authoritative_hits": addr_hits,
        "authoritative_hit_count": len(addr_hits),
        "suspect_exact_name_matches": suspects,
        "suspect_count": len(suspects),
        "launchpad_platform_distribution": dict(plat_dist.most_common()),
        "known_platform_token_index": {
            plat: [{"symbol": s, "chain": c, "address": a} for s, c, a in toks]
            for plat, toks in PLATFORM_TOKENS.items()
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n=== 权威地址精确命中: {len(addr_hits)} 条 ===")
    for h in addr_hits:
        print(f"  {h['platform']:<12} {h['token']:<10} {h['symbol']:<10} "
              f"{h['chain']:<10} {h['contract']}")
    print(f"\n=== 精确同名疑似(需核实): {len(suspects)} 条 ===")
    for s in suspects[:40]:
        print(f"  [{s['platform_guess']:<10}] {s['symbol']:<14} | {s['name']:<26} | "
              f"{s['launchpad']} | {s['contract']}")
    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
