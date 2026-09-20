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
        # Unknown listing dates on another venue are negative evidence, not proof
        # that a recently dated listing elsewhere is the first one.
        stamp, owner = min(entries)
        if key in proofs and (not stamp or stamp < proofs[key]["listedAt"]):
            proofs.pop(key, None)
        if (covered and state.get("ready") and key not in seen and stamp
                and 0 <= now_ms - stamp <= FRESH_MS):
            proofs[key] = {"sourceId": owner, "listedAt": stamp,
                           "key": f"first-listing:{key}:{stamp}", "expiresAt": stamp + FRESH_MS}
        seen.setdefault(key, now_ms)
    return {"version": 1, "ready": covered, "updatedAt": now_ms,
            "coverage": sorted(complete), "seen": seen, "proofs": proofs}, proofs
