#!/usr/bin/env python
"""做市商控盘提醒的离线探针（只看不弹）。

用法：
    C:/Python314/python.exe tools/alpha_mm_probe.py            # 用盘上缓存跑一轮
    C:/Python314/python.exe tools/alpha_mm_probe.py --fresh    # 强制重拉涨幅榜与 Alpha 清单

它复用 8765 服务同一套缓存与判据，但 `alert` 换成「只记录不投递」的桩，
所以**不会**给桌面弹任何窗。输出：每个候选在各道闸门的去留原因 + 命中清单。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import alpha_mm_monitor as mm  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="强制重拉涨幅榜与 Alpha 清单")
    parser.add_argument("--state", default=".runtime-cache/alpha_mm_probe_state.json")
    args = parser.parse_args()

    import server  # 延迟导入：server 很重，且会起若干单例对象

    gainers = server.cached_api_payload(
        "gainers-rankings",
        server.gainers_rankings_payload,
        60,
        force_refresh=args.fresh,
        allow_sync_rebuild=True,
    )
    index = server.binance_alpha_token_index(force_refresh=args.fresh)
    print(f"涨幅榜源：{len(gainers.get('sources') or [])} 个｜Alpha 收录 {len(index.get('tokens') or {})} 个")

    sent: list[dict] = []

    def alert(payload):
        sent.append(payload)
        return {"ok": True, "dryRun": True}

    stats = mm.run_round(
        gainers_payload=gainers,
        alpha_index=index,
        alert=alert,
        log=lambda message: print(f"  · {message}"),
        state_path=args.state,
        perp_symbols=server.binance_perp_base_assets(),
    )
    print()
    print("闸门：", json.dumps({key: stats.get(key) for key in (
        "candidates", "alpha", "alphaOffline", "perp", "marketCap", "control", "drawdown",
        "seeded", "buyPoints", "blocked", "watch",
    )}, ensure_ascii=False))
    if stats.get("skipped"):
        print("\n未通过的候选：")
        for item in stats["skipped"]:
            print("  -", item)
    if sent:
        print("\n本该弹的提醒：")
        for payload in sent:
            print(f"  [{payload['kind']}] {payload['title']}")
            print(f"    {payload['body']}")
    else:
        print("\n本轮没有满足全部条件的币。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
