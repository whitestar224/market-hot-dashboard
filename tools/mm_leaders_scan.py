"""把「龙头清单」逐只翻成「做市商」，并（可选）把新发现的地址写进做市商监控。

用户 2026-10-21：「把自选表里过往龙头那一栏的所有龙头，把它们对应的做市商找出来，
然后加入到做市商监控里」。

## 为什么先查本地而不是先扫链

做市商监控本身就在轮询 77 个地址、把「这个厂商动过哪些标的」落进 `mm_positions`
（**536 个标的、1015 条持仓**）。这是现成的、带笔数和美元额的真证据，比反过来去翻
某只币最近 50 笔转账靠谱得多 —— 那一页里通常全是路由器和池子（实测 PEPE 的最近
50 笔里没有一个做市商标签）。

所以顺序是：**本地持仓 → 链上标签（补漏）**，两条来源分开标注，不混为一谈。

## 三步

1. **ticker → 符号**：剥掉交易所前缀与计价货币（`BINANCE:PEPEUSDT` → `PEPE`）。
2. **证据**
   * **本地持仓**（强）：`mm_positions` 按 symbol 命中，带 `transfers` / `usd_in+usd_out`。
   * **链上标签**（弱，只有 ethereum / robinhood 能读）：Blockscout 持币名册 + 转账
     对手方里带 `Wintermute 3` / `GSR Markets` 这类名字的地址。**名册外的也收** ——
     这是发现新做市商的入口。
3. **入库**：`--write` 才写，且只写已知 8 家（`MM_FIRMS` 的键）。名册外单独列出 ——
   新增厂家要连带改 `MM_FIRMS`（页面下拉框按它生成），不在批量脚本里偷偷加。

    python tools/mm_leaders_scan.py --from-tv .runtime-cache/_tv_watchlist.json --group 过往龙头
    python tools/mm_leaders_scan.py --symbols BTC,ETH,PEPE --write
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import arkham_client as ARKHAM  # noqa: E402
import market_maker_monitor as mm  # noqa: E402
from mm_discover_probe import holders as bs_holders  # noqa: E402
from mm_discover_probe import transfers as bs_transfers  # noqa: E402

RUNTIME = ROOT / ".runtime-cache"
AUTH_DB = RUNTIME / "xingyunshe_auth.db"
MM_DB = RUNTIME / "market_maker_monitor.sqlite"
OUT_JSON = RUNTIME / "_mm_leaders_scan.json"

#: 能被 Blockscout 读到标签的链（地址名由 Blockscout 维护）。
LABEL_CHAINS = frozenset(mm.CHAINS)
#: DexScreener 的 chainId → 本项目的链名。
DEX_CHAINS = {
    "ethereum": "ethereum", "bsc": "bsc", "solana": "solana",
    "base": "base", "arbitrum": "arbitrum_one",
}
#: 认得出但**不在** `MM_FIRMS` 里的做市商 —— 只报告，不入库。
EXTRA_MM_HINTS = (
    "dexterity", "symbolic capital", "tokka", "presto", "mgnr", "kronos",
    "selini", "pulsar", "folkvang", "gravity", "tower research", "jane street",
    "alameda", "wintermute", "jump", "gsr", "dwf", "amber", "b2c2",
    "cumberland", "flow traders", "galaxy digital", "defiance",
)
#: 名称关键词 → `MM_FIRMS` 的键。**顺序有意义**：先具体后宽泛。
FIRM_ALIASES: tuple[tuple[str, str], ...] = (
    ("wintermute", "wintermute"),
    ("jump trading", "jump"), ("jump crypto", "jump"), ("jump", "jump"),
    ("dwf", "dwf"),
    ("gsr", "gsr"),
    ("amber", "amber"),
    ("b2c2", "b2c2"),
    ("cumberland", "cumberland"), ("drw", "cumberland"),
    ("flow traders", "flow"),
)


def normalize_symbol(raw: Any) -> str:
    """`BINANCE:PEPEUSDT` / `PEPEUSDT.P` / `pepe` → `PEPE`。

    后缀可能叠两层（`PEPEUSDT.P` = USDT 计价 + 永续标记），所以要循环剥到不动为止；
    只剥一次会留下 `PEPEUSDT`。
    """
    text = str(raw or "").strip().upper()
    if ":" in text:
        text = text.split(":", 1)[1]
    suffixes = ("PERPETUAL", "PERP", ".P", "USDT", "USDC", "FDUSD", "BUSD", "1!", "USD")
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text.strip("-_ ")


def match_firm(name: Any) -> str:
    """地址名/实体名 → `MM_FIRMS` 的键，认不出返回空串。"""
    lowered = " ".join(str(name or "").casefold().split())
    if not lowered:
        return ""
    for alias, firm in FIRM_ALIASES:
        if alias in lowered:
            return firm
    return ""


def looks_like_extra_mm(name: Any) -> bool:
    lowered = " ".join(str(name or "").casefold().split())
    return bool(lowered) and any(hint in lowered for hint in EXTRA_MM_HINTS)


# -- 证据 1：本地持仓（强） ---------------------------------------------------- #
def local_positions(symbol: str) -> list[dict[str, Any]]:
    if not MM_DB.exists():
        return []
    conn = sqlite3.connect(f"file:{MM_DB.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT firm, chain, token, symbol, transfers, usd_in, usd_out,"
            " last_seen_at, price_usd FROM mm_positions"
            " WHERE UPPER(symbol) = ? ORDER BY (usd_in + usd_out) DESC",
            (symbol.upper(),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "firm": str(row["firm"]), "chain": str(row["chain"]),
            "contract": str(row["token"]), "transfers": int(row["transfers"] or 0),
            "usd": float(row["usd_in"] or 0) + float(row["usd_out"] or 0),
            "lastSeenAt": int(row["last_seen_at"] or 0),
            "evidence": "position",
        }
        for row in rows
    ]


# -- ticker → 链上合约 --------------------------------------------------------- #
def resolve_dex(symbol: str, *, session) -> dict[str, str] | None:
    """DexScreener 搜索，按**市值**挑，不按流动性。

    用外部源当裁判而不是先查监控池：符号会撞车（`PEPE` 在 Solana 上有一堆同名币），
    池里那条未必是真龙头。

    两个坑：
    * **流动性字段会撒谎**：搜 `CAKE` 排第一的是一条 Solana 假币，`liquidity` = $2.5B、
      恰好等于它的市值 —— 真实池子不可能持有全部流通。所以要先过一条
      「流动性 < 市值的 40%」的合理性闸，再把流动性当次要指标。
    * 市值噪声比流动性小得多（`PEPE` 排第一就是 ETH 上那个 $1.9B 的真币）。
    """
    try:
        response = session.get(
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": symbol}, timeout=20,
        )
        response.raise_for_status()
        pairs = (response.json() or {}).get("pairs") or []
    except Exception:
        return None
    best_key: tuple[float, float] | None = None
    best: dict[str, str] | None = None
    for pair in pairs:
        chain = DEX_CHAINS.get(str(pair.get("chainId") or "").casefold())
        base = pair.get("baseToken") or {}
        if not chain or str(base.get("symbol") or "").upper() != symbol.upper():
            continue
        address = str(base.get("address") or "")
        if not ARKHAM.address_matches_chain(ARKHAM.arkham_chain_slug(chain), address):
            continue
        market_cap = float(pair.get("marketCap") or 0)
        liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
        if market_cap < 100_000:
            continue
        if liquidity >= market_cap * 0.4:
            continue
        key = (market_cap, liquidity)
        if best_key is None or key > best_key:
            best_key = key
            best = {"chain": chain, "contract": address, "via": "dexscreener"}
    return best


def resolve_pool(symbol: str) -> dict[str, str] | None:
    """兜底：本项目监控池里已经带着链和合约。"""
    if not AUTH_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{AUTH_DB.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT onchain_chain, onchain_contract_address FROM price_watch_assets"
            " WHERE UPPER(symbol) = ? AND COALESCE(onchain_contract_address, '') != ''"
            " ORDER BY COALESCE(updated_at, 0) DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    chain = ARKHAM.arkham_chain_slug(row["onchain_chain"])
    if not chain:
        return None
    return {"chain": chain, "contract": str(row["onchain_contract_address"]), "via": "pool"}


# -- 证据 2：链上（Arkham 地址集合 ∩ 持币人；Blockscout 标签补漏） ----------------- #
def onchain_evidence(chain: str, contract: str, *, pages: int = 2,
                     directory: dict | None = None) -> dict[str, Any]:
    """→ ``{"hits": [...], "notes": [...]}``，`hits` 里 firm 为空表示名册外。

    主证据是 **Arkham 的实体地址集合**：`/intelligence/entity/<id>/addresses` 给出
    Wintermute 8907 个地址（覆盖 20 条链），拿持币人去交集就是「这家持不持这只币」，
    BSC / Solana 一样成立 —— 而 Blockscout 标签只有 ethereum / robinhood 有。
    """
    import mm_entity_directory as mdir

    hits: list[dict[str, Any]] = []
    notes: list[str] = []
    seen: set[str] = set()
    directory = directory or mdir.ensure()

    def add(firm: str, address: str, tag: str, evidence: str, extra: str, pct: float = 0.0) -> None:
        key = str(address or "").casefold()
        if not key or key in seen:
            return
        seen.add(key)
        hits.append({"firm": firm, "address": address, "tag": tag,
                     "evidence": evidence, "extra": extra, "pct": round(pct, 4)})

    try:
        payload = ARKHAM.fetch_token_holders(ARKHAM.arkham_chain_slug(chain), contract)
        rows = payload.get("holders") or []
        for row in rows:
            address = str(row.get("address") or "")
            firm = mdir.firm_of(chain, address, data=directory)
            if not firm:
                firm = match_firm(row.get("entityName")) or match_firm(row.get("label"))
            name = row.get("entityName") or row.get("label") or ""
            pct = float(row.get("pct") or 0)
            if firm:
                add(firm, address, f"{name} {pct:.2f}%".strip(), "arkham", "Arkham 持币", pct)
            elif str(row.get("entityType") or "").casefold() == "market-maker" or looks_like_extra_mm(name):
                add("", address, f"{name} {pct:.2f}%".strip(), "arkham", "Arkham 持币·名册外", pct)
        if not rows:
            notes.append(f"Arkham 没给持币人：{payload.get('reason') or '未知'}")
    except Exception as exc:
        notes.append(f"Arkham 失败：{type(exc).__name__}: {str(exc)[:60]}")

    if chain not in LABEL_CHAINS:
        notes.append(f"{mm.chain_label(chain)} 没有 Blockscout 标签，只用了 Arkham 地址集合")
        return {"hits": hits, "notes": notes}

    try:
        for row in bs_holders(chain, contract, pages=1):
            firm = match_firm(row["name"])
            if firm:
                add(firm, row["address"], row["name"], "tag", "Blockscout 持币名册")
            elif looks_like_extra_mm(row["name"]):
                add("", row["address"], row["name"], "tag", "名册外")
    except Exception as exc:
        notes.append(f"Blockscout 持币人失败：{type(exc).__name__}")

    try:
        flows: dict[str, list[Any]] = {}
        for row in bs_transfers(chain, contract, pages=pages):
            for side, name_key in (("from", "fromName"), ("to", "toName")):
                address = row[side]
                name = row[name_key] or ""
                firm = match_firm(name)
                if not address or not (firm or looks_like_extra_mm(name)):
                    continue
                slot = flows.setdefault(address, [name, 0])
                if name and not slot[0]:
                    slot[0] = name
                slot[1] += 1
        for address, (name, count) in sorted(flows.items(), key=lambda kv: -kv[1][1]):
            add(match_firm(name), address, name, "tag",
                f"Blockscout 转账对手方 {count} 笔" + ("" if match_firm(name) else "·名册外"))
        if not flows:
            notes.append(f"最近 {pages * 50} 笔转账里没有做市商标签")
    except Exception as exc:
        notes.append(f"Blockscout 流水失败：{type(exc).__name__}")
    return {"hits": hits, "notes": notes}


def load_tickers(args) -> list[tuple[str, str]]:
    """→ [(分组名, ticker)]。"""
    if args.symbols:
        return [("命令行", item.strip()) for item in args.symbols.split(",") if item.strip()]
    path = Path(args.from_tv)
    if not path.exists():
        raise SystemExit(f"找不到 {path} —— 先跑 tools/tv_watchlist_probe.mjs")
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups = payload.get("flat") or []
    if args.group:
        picked = [g for g in groups if args.group in str(g.get("name") or "")]
        if not picked:
            raise SystemExit(
                f"没有包含「{args.group}」的分组；现有分组：{[g.get('name') for g in groups]}"
            )
        groups = picked
    return [
        (str(group.get("name") or ""), ticker)
        for group in groups
        for ticker in group.get("symbols") or []
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="龙头清单 → 做市商（只读，--write 才入库）")
    parser.add_argument("--from-tv", default=str(RUNTIME / "_tv_watchlist.json"))
    parser.add_argument("--group", default="")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pages", type=int, default=2, help="Blockscout 流水翻几页（50 笔/页）")
    parser.add_argument("--no-onchain", action="store_true", help="只用本地持仓，不扫链")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    tickers = load_tickers(args)
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"== 清单 {len(tickers)} 个条目 ==")

    store = mm.MarketMakerStore()
    book = {row["address"].casefold(): row for row in store.addresses()}
    session = mm._session()
    import mm_entity_directory as mdir
    directory = mdir.ensure()
    report: list[dict[str, Any]] = []
    proposed: dict[tuple[str, str], dict[str, str]] = {}

    for group, ticker in tickers:
        symbol = normalize_symbol(ticker)
        entry: dict[str, Any] = {
            "group": group, "ticker": ticker, "symbol": symbol,
            "chain": "", "contract": "", "via": "",
            "positions": local_positions(symbol), "labels": [], "notes": [],
        }
        firms = sorted({row["firm"] for row in entry["positions"]})

        if not args.no_onchain and not firms:
            resolved = resolve_dex(symbol, session=session) or resolve_pool(symbol)
            if resolved:
                entry.update(resolved)
                scan = onchain_evidence(resolved["chain"], resolved["contract"],
                                        pages=args.pages, directory=directory)
                entry["labels"] = scan["hits"]
                entry["notes"] += scan["notes"]
                firms = sorted({hit["firm"] for hit in scan["hits"] if hit["firm"]})
                for hit in scan["hits"]:
                    # 只能写监控支持的链（`MM_CHAINS` 目前是 ethereum / robinhood）——
                    # `save_address` 对别的链会抛「不支持的链」，BSC/Solana 只能报告。
                    if resolved["chain"] not in mm.CHAINS:
                        continue
                    if hit["firm"] and hit["address"] and hit["address"].casefold() not in book:
                        proposed[(resolved["chain"], hit["address"].casefold())] = {
                            "chain": resolved["chain"], "address": hit["address"],
                            "firm": hit["firm"], "tag": hit["tag"],
                        }
            else:
                entry["notes"].append("没解析到链上合约（可能是指数/外汇/股票 ticker）")

        extra = [hit for hit in entry["labels"] if not hit["firm"]]
        marker = "、".join(mm.firm_label(f) for f in firms) or "—"
        suffix = f"（名册外 {len(extra)}）" if extra else ""
        chain_note = f" {mm.chain_label(entry['chain'])}" if entry["chain"] else ""
        print(f"  {symbol:12s}{chain_note:12s} {marker}{suffix}")
        for note in entry["notes"]:
            print(f"      · {note}")
        report.append(entry)

    # 汇总：做市商 → 覆盖了哪些龙头
    by_firm: dict[str, list[str]] = {}
    for entry in report:
        for firm in {row["firm"] for row in entry["positions"]} | {
            hit["firm"] for hit in entry["labels"] if hit["firm"]
        }:
            by_firm.setdefault(firm, []).append(entry["symbol"])
    print("\n== 按做市商汇总 ==")
    for firm, symbols in sorted(by_firm.items(), key=lambda kv: -len(kv[1])):
        print(f"  {mm.firm_label(firm):14s} {len(symbols):3d} 个：{'、'.join(sorted(set(symbols))[:14])}")
    hit_count = sum(1 for entry in report if entry["positions"] or entry["labels"])
    print(f"\n命中 {hit_count}/{len(report)} 个；未命中的做市商痕迹可能只是地址簿没覆盖那条链。")

    print(f"\n== 建议入库 {len(proposed)} 条（名册外的不写）==")
    for (chain, _addr), row in sorted(proposed.items()):
        print(f"  {mm.firm_label(row['firm']):14s} {chain:10s} {row['address']}  {row['tag'][:34]}")

    if args.write and proposed:
        for row in proposed.values():
            store.save_address(
                row["chain"], row["address"], row["firm"],
                tag=row["tag"][:60], evidence="tag", source_url="",
            )
        print(f"已写入 {len(proposed)} 条。")

    OUT_JSON.write_text(json.dumps({
        "generatedAt": int(time.time() * 1000),
        "tickers": len(tickers),
        "tokens": report,
        "byFirm": {firm: sorted(set(symbols)) for firm, symbols in by_firm.items()},
        "proposed": list(proposed.values()),
        "written": bool(args.write),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细 → {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
