"""把 Arkham 归因到的做市商地址导入做市商监控地址簿。

原地址簿是人工攒的 77 条、几乎全在以太坊，做市商页面里「这个厂商在动哪些标的」
其实只覆盖了极小一角。Arkham 的 `/intelligence/entity/<id>/addresses` 给的是
**厂家维度的全链地址**（Wintermute 8907 个 / 20 条链，Cumberland 25351 个），
这是「按实体归因」的地址，比人工收集准得多。

## 为什么不一口气全导

轮询是 `MM_ADDRESSES_PER_CYCLE`（默认 30）/ `MM_POLL_SECONDS`（默认 300 秒）——
30 个地址一轮、5 分钟一轮，77 条地址大约 13 分钟转完一圈。塞进上千条会把一圈拖到
一整天，老地址的新鲜度反而变差。所以这里**限流**：每个厂家每条链最多 `--per-firm-chain`
个（默认 12），总量控制在两三百条以内。

    python tools/mm_seed_addresses.py                    # 干跑，只打印数量
    python tools/mm_seed_addresses.py --write            # 真写
    python tools/mm_seed_addresses.py --chains ethereum  # 只导某条链

弹窗风险：新地址会产出新的 `mm_positions`，但进场弹窗还要过**三方共振**闸
（涨幅榜 ∩ AIcoin），所以不会因为这次导入突然刷屏。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import market_maker_monitor as mm  # noqa: E402
import mm_entity_directory as mdir  # noqa: E402

#: 这些地址永远不是做市商的自有地址：
#: * 零地址 / 黑洞；
#: * 前缀一堆 0 的「预编译 / 系统地址」—— Robinhood 是 Orbit 链，预编译就长这样
#:   （`0x0000000000000000000000000000000000000064`），Arkham 会把它们也列进实体。
#: 实测 187 条候选里只有 7 条命中，剔掉不影响覆盖。
JUNK_ADDRESS = re.compile(r"^0x0{6,}|^0x0{40}$|^0x[0-9a-f]*dead$")


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 Arkham 做市商地址到监控地址簿")
    parser.add_argument("--chains", default=",".join(mm.CHAINS))
    parser.add_argument("--per-firm-chain", type=int, default=12)
    parser.add_argument("--firms", default="", help="只导这些厂家（逗号分隔），默认全部")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    wanted_chains = [mm.normalize_chain(item) for item in args.chains.split(",") if item.strip()]
    wanted_chains = [c for c in wanted_chains if c]
    unsupported = [c for c in wanted_chains if c not in mm.CHAINS]
    if unsupported:
        # 导入也没用：poll_address 只认 CHAIN_META 里的链。
        raise SystemExit(f"这些链没法轮询（只支持 {mm.CHAINS}）：{unsupported}")

    firms = [f for f in (args.firms.split(",") if args.firms else mdir.FIRM_ENTITIES)]
    firms = [f.strip() for f in firms if f.strip() in mdir.FIRM_ENTITIES] or list(mdir.FIRM_ENTITIES)

    directory = mdir.ensure()
    if directory.get("errors"):
        print(f"注意：目录有 {len(directory['errors'])} 个厂家抓取失败：{directory['errors'][:3]}")

    store = mm.MarketMakerStore()
    existing = {(row["chain"], row["address"].casefold()) for row in store.addresses()}
    print(f"现有地址簿 {len(existing)} 条；目标链 {wanted_chains}；每厂家每链 ≤{args.per_firm_chain}")

    planned: list[dict[str, str]] = []
    skipped_junk = 0
    for firm in firms:
        for chain in wanted_chains:
            addresses = sorted(mdir.addresses_of(firm, chain, data=directory))
            added = 0
            for address in addresses:
                if JUNK_ADDRESS.match(address):
                    skipped_junk += 1
                    continue
                if (chain, address) in existing:
                    continue
                planned.append({"firm": firm, "chain": chain, "address": address})
                existing.add((chain, address))
                added += 1
                if added >= args.per_firm_chain:
                    break
            if added:
                print(f"  {mm.firm_label(firm):14s} {chain:10s} +{added}（该链 Arkham 共 {len(addresses)}）")

    print(f"\n合计待写入 {len(planned)} 条（剔除预编译/零地址 {skipped_junk} 条）")
    if not planned:
        return 0
    if not args.write:
        print("干跑结束；加 --write 才真正写入。")
        return 0

    for row in planned:
        store.save_address(
            row["chain"], row["address"], row["firm"],
            tag="Arkham 实体归因",
            evidence="tag",
            source_url=f"https://arkm.com/explorer/entity/{mdir.FIRM_ENTITIES[row['firm']]}",
        )
    total = len(store.addresses())
    print(f"已写入。地址簿现在 {total} 条；轮完一圈约 "
          f"{round(total / max(1, mm.DEFAULT_ADDRESSES_PER_CYCLE) * mm.DEFAULT_POLL_SECONDS / 60)} 分钟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
