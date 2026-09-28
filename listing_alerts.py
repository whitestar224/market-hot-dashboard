"""Pure, conservative first-listing policy over full connected-source inventories.

Top-ten boards are display slices, never evidence that an asset was absent.
The caller atomically saves the returned state before using its stable proofs.
"""
import re

REQUIRED_SOURCES = frozenset({"binance-new", "okx-new", "bitget-new", "gate-new", "htx-new",
                              "aster-new", "hyperliquid-new", "trade-xyz-new", "binance-alpha-new",
                              "binance-spot-inventory", "okx-spot-inventory", "gate-spot-inventory",
                              "htx-spot-inventory", "bitget-futures-inventory", "kucoin-spot-inventory"})
FRESH_MS = 15 * 60_000

# 全量现货/期货 inventory 来源。只有这些来源能权威证明“资产是老币、不是首上市”，
# 因此只有它们才有资格把资产永久写入 seen 静默。上新榜（*-new）只是“最近上新”的
# 展示切片，不能作为“已知老币”的证据。
INVENTORY_SOURCES = frozenset({
    "binance-spot-inventory", "okx-spot-inventory", "gate-spot-inventory",
    "htx-spot-inventory", "bitget-futures-inventory", "kucoin-spot-inventory",
})

# 早鸟/预览区来源（如 Binance Alpha）只是“收录”，不是正式上市。它们抢先收录一个
# 币时，不应抢占 first-listing 的 owner 地位，否则币安/OKX/Bitget 主站正式上新会被
# 过早的时间戳（min(entries) 取到早鸟时间）和 seen 永久静默，导致主所上新弹窗消失。
NON_OWNER_SOURCES = frozenset({"binance-alpha-new"})


def asset_key(value):
    text = str(value or "").strip().upper()
    text = text.removesuffix("-SWAP").removesuffix("PERP")
    text = re.sub(r"[-_/]?(USDT|USDC|USD)$", "", text)
    return text if re.fullmatch(r"[\w.]{1,60}", text) else ""


def number(value):
    try:
        return max(0, int(float(value or 0)))
    except (ValueError, TypeError, OverflowError):
        return 0


def attach_inventory(source, entries):
    inventory = []
    for symbol, stamp in entries:
        key = asset_key(symbol)
        if key:
            inventory.append({"asset": key, "listedAt": number(stamp)})
    return {**source, "listingInventory": inventory, "listingInventoryComplete": bool(inventory)}


def observe_listings(state, sources, *, now_ms, historical=()):
    """Unknown coverage/baseline is silent; keep known assets across delist/relist."""
    seen = dict(state.get("seen") or {})
    proofs = {key: dict(value) for key, value in (state.get("proofs") or {}).items()
              if isinstance(value, dict) and now_ms < number(value.get("expiresAt"))}
    for row in historical:
        if isinstance(row, dict) and (key := asset_key(row.get("symbol"))):
            stamp = number(row.get("newCoinFirstListedAt") or row.get("newCoinListedAt"))
            if stamp and stamp < now_ms - FRESH_MS:
                seen.setdefault(key, stamp)
    complete = {str(s.get("id")) for s in sources if s.get("status") == "ok"
                and s.get("listingInventoryComplete") and s.get("listingInventory")}
    covered = REQUIRED_SOURCES <= complete
    current = {}
    for source in sources:
        source_id = str(source.get("id") or "")
        if source_id not in REQUIRED_SOURCES:
            continue
        for row in source.get("listingInventory") or source.get("rows") or []:
            if not isinstance(row, dict):
                continue
            key = asset_key(row.get("asset") or row.get("symbol"))
            if key:
                current.setdefault(key, []).append((number(row.get("listedAt", row.get("date"))), source_id))
    for key, entries in current.items():
        # 早鸟/预览区（Binance Alpha）的收录时间不参与 first-listing 判定，否则其过早
        # 时间戳会让主所（币安/OKX/Bitget）正式上新永远落在 FRESH_MS 窗口之外。
        owner_entries = [(stamp, source_id) for stamp, source_id in entries
                         if source_id not in NON_OWNER_SOURCES]
        if not owner_entries:
            continue
        # Unknown listing dates on another venue are negative evidence, not proof
        # that a recently dated listing elsewhere is the first one.
        stamp, owner = min(owner_entries)
        if key in proofs and (not stamp or stamp < proofs[key]["listedAt"]):
            proofs.pop(key, None)
        if (covered and state.get("ready") and key not in seen and stamp
                and 0 <= now_ms - stamp <= FRESH_MS):
            proofs[key] = {"sourceId": owner, "listedAt": stamp,
                           "key": f"first-listing:{key}:{stamp}", "expiresAt": stamp + FRESH_MS}
        # 只有完整 inventory（covered）下的全量现货/期货来源才能权威确认“资产是老币”，
        # 才写入 seen 静默。coverage 不完整 / 早鸟来源 / 上新榜里的币都不能永久静默，
        # 否则冷启动轮或早鸟抢先会把主所上新永久吞掉。
        if covered and any(source_id in INVENTORY_SOURCES for _, source_id in entries):
            seen.setdefault(key, now_ms)
    return {"version": 1, "ready": covered, "updatedAt": now_ms,
            "coverage": sorted(complete), "seen": seen, "proofs": proofs}, proofs
