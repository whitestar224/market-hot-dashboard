"""One-off: purge TradFi contracts out of the coin monitor pools.

Two buckets, decided per row:
  * the TradFi listing was the ONLY reason the symbol is monitored -> delete the
    row and its symbol-level state;
  * the symbol still has another live source (on-chain contract, AiCoin, wallet
    hot list, GMGN, personal X, ...) -> only clear the new-contract columns, so
    a genuine on-chain token that shares a ticker with a stock keeps working.

Writes an audit file next to the runtime cache so the change is reversible.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("XINGYUNSHE_DESKTOP_ALERTS", "0")

import requests  # noqa: E402

import server  # noqa: E402

HEADERS = {"User-Agent": "Mozilla/5.0"}
OTHER_SOURCE_COLUMNS = (
    "aicoin_first_seen_at",
    "binance_wallet_hot_first_seen_at",
    "ave_hot_first_seen_at",
    "gmgn_hot_search_first_seen_at",
    "gainers_first_seen_at",
    "okx_gainers_last_seen_at",
    "binance_gainers_last_seen_at",
    "binance_futures_gainers_last_seen_at",
    "personal_x_mentioned_at",
    "onchain_contract_address",
    "manual_pinned",
)


def venue_tradfi_symbols() -> set[str]:
    symbols: set[str] = set()
    crypto: set[str] = set()

    exchange = requests.get(
        "https://fapi.binance.com/fapi/v1/exchangeInfo", headers=HEADERS, timeout=25
    ).json()
    for item in exchange.get("symbols") or []:
        if item.get("quoteAsset") != "USDT":
            continue
        symbol = server.clean_price_watch_symbol(item.get("baseAsset"))
        if not symbol:
            continue
        (symbols if server.binance_contract_is_tradfi(item) else crypto).add(symbol)

    instruments = requests.get(
        "https://www.okx.com/api/v5/public/instruments",
        params={"instType": "SWAP"},
        headers=HEADERS,
        timeout=25,
    ).json().get("data") or []
    for item in instruments:
        inst_id = str(item.get("instId") or "")
        if not inst_id.endswith("-USDT-SWAP"):
            continue
        symbol = server.clean_price_watch_symbol(inst_id.split("-")[0])
        if not symbol:
            continue
        if server.okx_contract_is_tradfi(item):
            symbols.add(symbol)
        elif str(item.get("instCategory") or "") == "1":
            crypto.add(symbol)

    contracts = requests.get(
        "https://api.gateio.ws/api/v4/futures/usdt/contracts", headers=HEADERS, timeout=25
    ).json()
    for item in contracts:
        symbol = server.clean_price_watch_symbol(str(item.get("name") or "").removesuffix("_USDT"))
        if not symbol:
            continue
        (symbols if server.gate_contract_is_tradfi(item) else crypto).add(symbol)

    htx = requests.get(
        "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info", headers=HEADERS, timeout=25
    ).json().get("data") or []
    for item in htx:
        symbol = server.clean_price_watch_symbol(item.get("symbol"))
        if symbol and server.htx_contract_is_tradfi(item):
            symbols.add(symbol)

    return {symbol for symbol in symbols if symbol not in crypto}


def symbol_state_tables(conn: sqlite3.Connection) -> list[str]:
    """Monitor bookkeeping only.

    ``price_structure_exclusions`` records what the user removed on purpose and
    ``strategy_orders`` records real orders; neither may be touched by a purge
    of pooled symbols.
    """
    protected = {"price_structure_exclusions", "strategy_orders"}
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    return [
        table
        for table in tables
        if table not in protected
        and any(
            column[1] == "symbol"
            for column in conn.execute(f"PRAGMA table_info({table})")
        )
    ]


def main() -> None:
    now_ms = int(time.time() * 1000)
    tradfi = venue_tradfi_symbols()
    print(f"TradFi symbols reported by Binance/OKX/Gate/HTX: {len(tradfi)}")

    conn = sqlite3.connect(str(server.AUTH_DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    with conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM price_watch_assets WHERE new_contract_listed_at > 0"
            )
        ]
        targets = [row for row in rows if row["symbol"] in tradfi]

        delete_symbols: list[str] = []
        trimmed: list[dict[str, object]] = []
        for row in targets:
            held = [column for column in OTHER_SOURCE_COLUMNS if row.get(column)]
            if held:
                trimmed.append({"symbol": row["symbol"], "keptSources": held})
            else:
                delete_symbols.append(row["symbol"])

        state_tables = symbol_state_tables(conn)
        for symbol in delete_symbols:
            for table in state_tables:
                conn.execute(f"DELETE FROM {table} WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM price_watch_assets WHERE symbol = ?", (symbol,))
        for entry in trimmed:
            conn.execute(
                """
                UPDATE price_watch_assets
                SET new_contract_listed_at = 0,
                    new_contract_source = '',
                    new_contract_pair = '',
                    updated_at = ?
                WHERE symbol = ?
                """,
                (now_ms, entry["symbol"]),
            )

    audit = {
        "ranAt": now_ms,
        "tradfiSymbolCount": len(tradfi),
        "newContractRowsScanned": len(rows),
        "tradfiRowsFound": len(targets),
        "deleted": sorted(delete_symbols),
        "trimmed": sorted(trimmed, key=lambda item: str(item["symbol"])),
    }
    audit_path = server.NEW_COIN_LOW_LISTING_HISTORY_PATH.parent / f"tradfi-purge-{now_ms}.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tradfi rows in the new-contract pool: {len(targets)}")
    print(f"  deleted rows (TradFi was the only source): {len(delete_symbols)}")
    print(f"  trimmed rows (kept other live sources):    {len(trimmed)}")
    print(f"  audit: {audit_path}")
    for entry in audit["trimmed"]:
        print(f"    kept {entry['symbol']}: {', '.join(entry['keptSources'])}")
    conn.close()


if __name__ == "__main__":
    main()
