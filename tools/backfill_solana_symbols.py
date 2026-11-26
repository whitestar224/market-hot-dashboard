"""把 ``events`` 表里 symbol='UNKNOWN' 的 Solana 事件补回真实符号。

背景（2026-11-12）：``SmartMoneyMonitor.solana_token_metadata`` 以前只查稳定币表，
查不到一律返回 ``"UNKNOWN"``，跑了一个多月，库里 2732 条 Solana 事件的符号全是这个
字面量。卡片标题就成了「KOL cowboy 买入 UNKNOWN · $807」，用户投诉「SOL链的买入记录
总是显示UNKNOWN」。

源头已在 ``smart_money_monitor.py`` 修好（DexScreener ``baseToken.symbol`` → 链上
``getAsset`` → 铸币缩写兜底）。本脚本只负责把**历史行**洗一遍。

用法::

    C:/Python314/python.exe tools/backfill_solana_symbols.py --dry-run
    C:/Python314/python.exe tools/backfill_solana_symbols.py

``--dry-run`` 只打印「能补 / 补不到」的统计，不写库。补不到的（刚发的盘、已下架的盘）
保持 UNKNOWN —— **绝不编一个名字出来**，宁可显示铸币缩写由前端兜底。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()

DB = ROOT / ".runtime-cache" / "smart_money_monitor.sqlite"
BATCH = 30          # DexScreener /tokens/v1 单次上限
PAUSE = 1.2         # 批间隔，免费档限流 300 req/min，这个节奏绰绰有余


def fetch_batch(session: requests.Session, mints: list[str], retries: int = 4) -> dict[str, str]:
    """一次查一批铸币，返回 ``{mint: symbol}``。失败重试，绝不让整轮崩掉。"""
    url = "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints)
    for attempt in range(retries):
        try:
            response = session.get(url, headers={"Accept": "application/json"}, timeout=(8, 25))
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                return {}
            found: dict[str, str] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                base = row.get("baseToken") or {}
                mint = str(base.get("address") or "")
                symbol = str(base.get("symbol") or "").strip()
                if mint and symbol and symbol.upper() != "UNKNOWN":
                    # 同一个 mint 可能有多条池子，取第一个非空的即可
                    found.setdefault(mint, symbol)
            return found
        except Exception as exc:  # noqa: BLE001
            wait = 1.5 * (attempt + 1)
            print(f"    重试 {attempt + 1}/{retries}（{type(exc).__name__}: {str(exc)[:80]}）")
            time.sleep(wait)
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 Solana 事件缺失的代币符号")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--db", default=str(DB), help="数据库路径")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    mints = [
        row["token_address"]
        for row in cursor.execute(
            "select token_address, count(*) as n from events"
            " where chain='solana' and (symbol is null or symbol='' or symbol='UNKNOWN')"
            " group by token_address order by n desc"
        )
        if row["token_address"]
    ]
    total_rows = cursor.execute(
        "select count(*) from events where chain='solana'"
        " and (symbol is null or symbol='' or symbol='UNKNOWN')"
    ).fetchone()[0]

    print(f"待补：{len(mints)} 个去重铸币 / {total_rows} 条事件")
    if not mints:
        print("没有需要补的，收工。")
        return 0

    session = requests.Session()
    session.trust_env = True  # 跟随系统代理（README 铁律）

    resolved: dict[str, str] = {}
    for start in range(0, len(mints), BATCH):
        chunk = mints[start:start + BATCH]
        found = fetch_batch(session, chunk)
        resolved.update(found)
        done = min(start + BATCH, len(mints))
        print(f"  {done}/{len(mints)} 批内解析 {len(found)}/{len(chunk)}  累计 {len(resolved)}")
        time.sleep(PAUSE)

    print(f"\n解析成功 {len(resolved)} / {len(mints)}，缺 {len(mints) - len(resolved)} 个")
    if not resolved:
        print("一个都没解析出来，不写库。")
        return 1

    if args.dry_run:
        sample = list(resolved.items())[:15]
        print("样例（dry-run，未写库）：")
        for mint, symbol in sample:
            print(f"  {mint} -> {symbol}")
        return 0

    updated = 0
    for mint, symbol in resolved.items():
        cursor.execute(
            "update events set symbol=? where chain='solana' and token_address=?"
            " and (symbol is null or symbol='' or symbol='UNKNOWN')",
            (symbol[:40], mint),
        )
        updated += cursor.rowcount
    conn.commit()
    print(f"已更新 {updated} 条事件。")

    left = cursor.execute(
        "select count(*) from events where chain='solana'"
        " and (symbol is null or symbol='' or symbol='UNKNOWN')"
    ).fetchone()[0]
    print(f"仍为 UNKNOWN 的剩余 {left} 条（这些铸币在 DexScreener 上查不到，保持原样）")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
