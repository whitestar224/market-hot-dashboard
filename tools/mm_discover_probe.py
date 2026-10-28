"""探针：给定一个代币，看它在链上被哪些做市商碰过（只读，不写库）。

用户 2026-10-21：「把过往龙头的做市商找出来，加进做市商监控」。在拿到清单之前先把
「怎么算这只币的做市商」钉死，两条证据同时看：

1. **持币名册**：``/api/v2/tokens/<ca>/holders`` 里，Blockscout 已经给地址挂了标签的
   （``Wintermute 3`` / ``GSR Markets`` / ``Amber Group`` …）—— 这条能发现名册外的新做市商。
2. **对手方流水**：``/api/v2/tokens/<ca>/transfers`` 里与内置 77 条地址簿有交集的
   —— 这条能证明「这家确实在动这只币」，比名册更硬。

用法：
    python tools/mm_discover_probe.py robinhood 0x39dbed3a2bd333467115de4566f8d8b0e4a4f2f1
    python tools/mm_discover_probe.py ethereum 0x1f9840... --pages 2

注意：**只读**。要入库走 ``market_maker_monitor.store.save_address()``，别在探针里写。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import market_maker_monitor as mm  # noqa: E402

#: 标签里出现这些词就当作做市商/自营交易台（大小写不敏感）。
MM_NAME_HINTS = (
    "wintermute", "jump trading", "jump crypto", "gsr", "dwf", "amber group",
    "b2c2", "cumberland", "flow traders", "jane street", "selini", "pulsar",
    "dexterity", "tower research", "alameda", "mgnr", "kronos", "presto",
    "tokka labs", "symbolic capital", "dwf labs", "folkvang", "gravity",
)


def get_json(url: str, params: dict | None = None) -> dict:
    """Blockscout 必须带浏览器 UA + Accept + Referer，否则 403（v1 还有 CF 挑战页）。"""
    base = url.split("/api/", 1)[0]
    session = mm._session()
    response = session.get(
        url, params=params or {}, headers=mm._blockscout_headers(base), timeout=mm.HTTP_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def holders(chain: str, token: str, pages: int = 1) -> list[dict]:
    base = mm.CHAIN_META[chain]["blockscout"]
    out: list[dict] = []
    params: dict = {}
    for _ in range(max(1, pages)):
        payload = get_json(f"{base}/api/v2/tokens/{token}/holders", params)
        for item in payload.get("items") or []:
            address = item.get("address") or {}
            out.append({
                "address": str(address.get("hash") or ""),
                "name": str(address.get("name") or ""),
                "isContract": bool(address.get("is_contract")),
                "value": item.get("value"),
            })
        params = payload.get("next_page_params") or {}
        if not params:
            break
    return out


def transfers(chain: str, token: str, pages: int = 1) -> list[dict]:
    base = mm.CHAIN_META[chain]["blockscout"]
    out: list[dict] = []
    # 别带 `type=ERC-20`：那是 v1 的参数，v2 会直接回 422 `Unexpected field: type`（踩过）。
    params: dict = {}
    for _ in range(max(1, pages)):
        payload = get_json(f"{base}/api/v2/tokens/{token}/transfers", params)
        for item in payload.get("items") or []:
            out.append({
                "from": str((item.get("from") or {}).get("hash") or "").lower(),
                "fromName": str((item.get("from") or {}).get("name") or ""),
                "to": str((item.get("to") or {}).get("hash") or "").lower(),
                "toName": str((item.get("to") or {}).get("name") or ""),
            })
        params = payload.get("next_page_params") or {}
        if not params:
            break
    return out


def looks_like_market_maker(name: str) -> str:
    lowered = str(name or "").casefold()
    for hint in MM_NAME_HINTS:
        if hint in lowered:
            return hint
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="查一只币的做市商痕迹（只读）")
    parser.add_argument("chain", choices=sorted(mm.CHAINS))
    parser.add_argument("token")
    parser.add_argument("--pages", type=int, default=1)
    args = parser.parse_args()

    chain = mm.normalize_chain(args.chain)
    token = args.token.strip()
    book = {row["address"].casefold(): row for row in mm.MarketMakerStore().addresses()}
    print(f"== {token} @ {mm.chain_label(chain)} · 地址簿 {len(book)} 条 ==")

    print("\n-- 持币名册（有做市商标签的）--")
    rows = holders(chain, token, args.pages)
    print(f"  取到 {len(rows)} 个持币地址")
    tagged = [row for row in rows if looks_like_market_maker(row["name"])]
    for row in tagged:
        known = book.get(row["address"].casefold())
        print(f"  {row['name'][:40]:42s} {row['address']}  value={row['value']}"
              f"  {'[已在地址簿: ' + str(known.get('firm')) + ']' if known else '[名册外]'}")
    if not tagged:
        print("  （没有带做市商标签的持币地址 —— 这条链上没有可读的标签库，或这只币的持有者都没标签）")

    print("\n-- 对手方流水（与地址簿的交集）--")
    flows = transfers(chain, token, args.pages)
    hits: dict[str, dict] = {}
    for row in flows:
        for side, name_key in (("from", "fromName"), ("to", "toName")):
            address = row[side]
            known = book.get(address)
            if not known:
                continue
            slot = hits.setdefault(address, {
                "firm": known["firm"], "tag": known["tag"],
                "label": row[name_key] or known["tag"], "count": 0,
            })
            slot["count"] += 1
    print(f"  取到 {len(flows)} 条转账")
    for address, slot in sorted(hits.items(), key=lambda item: -item[1]["count"]):
        print(f"  {mm.firm_label(slot['firm']):20s} {slot['tag'][:34]:36s} {address}  {slot['count']} 笔")
    if not hits:
        print("   （最近流水里没有地址簿成员 —— 可能没做市，也可能只是这一页没扫到）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
