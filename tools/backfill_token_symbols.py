"""把 events 里「缩写合约」的 symbol 一次性补成真名字（只处理 Solana）。

背景：新发盘的买单落库那一刻，DexScreener 还没有交易对（``{"pairs": null}``）、
DAS 也查不到资产，只能写 ``J1D7e7…eWfv`` 这种铸币缩写。服务里已经有
``SmartMoneyMonitor.backfill_truncated_symbols()`` 每 2 分钟补 12 个，但历史积压
（实测 468 个铸币 / 1304 条事件）靠它要跑一个多小时，所以留这个一次性工具。

    C:/Python314/python.exe tools/backfill_token_symbols.py                  # 只报告
    C:/Python314/python.exe tools/backfill_token_symbols.py --apply          # 真写库
    C:/Python314/python.exe tools/backfill_token_symbols.py --apply --limit 50 --workers 4

取名走服务里同一套链路（DexScreener → Jupiter lite → 链上 getAsset），不另写一遍，
免得两处判据漂移。写库只动 ``symbol`` 一列、只改同一个铸币下的缩写行，可反复跑。
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smart_money_monitor as SMM  # noqa: E402

DEFAULT_DB = ROOT / ".runtime-cache" / "smart_money_monitor.sqlite"


def resolve(monitor: SMM.SmartMoneyMonitor, mint: str) -> tuple[str, str, str]:
    """返回 ``(mint, symbol, name)``；取不到名字时 symbol 为空串。"""
    try:
        meta = monitor.solana_token_metadata(mint)
    except Exception as exc:  # 单个铸币失败不能拖垮整批
        return mint, "", f"error: {exc}"
    symbol = str(meta.get("symbol") or "").strip()
    if not symbol or symbol.upper() == "UNKNOWN" or "…" in symbol:
        return mint, "", str(meta.get("name") or "")
    return mint, symbol, str(meta.get("name") or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个铸币（0 = 全部）")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true", help="不加就只报告，不写库")
    args = parser.parse_args()

    monitor = SMM.SmartMoneyMonitor(Path(args.db))
    store = monitor.store
    try:
        total_events = store.conn.execute(
            "SELECT count(*) FROM events WHERE chain = 'solana' AND symbol LIKE '%…%'"
        ).fetchone()[0]
        rows = store.truncated_symbol_rows(20000, chain="solana")
        if args.limit > 0:
            rows = rows[: args.limit]
        mints = [str(row.get("token_address") or "") for row in rows if row.get("token_address")]
        print(f"缩写事件 {total_events} 条 / 待查铸币 {len(mints)} 个（本次处理 {len(mints)}）")
        if not mints:
            return 0

        resolved: list[tuple[str, str]] = []
        missed: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for done, (mint, symbol, note) in enumerate(pool.map(lambda m: resolve(monitor, m), mints), 1):
                if symbol:
                    resolved.append((mint, symbol))
                else:
                    missed.append((mint, note))
                if done % 20 == 0 or done == len(mints):
                    print(f"  进度 {done}/{len(mints)} … 可补 {len(resolved)}", flush=True)

        print(f"\n可补名字 {len(resolved)} 个铸币，查不到 {len(missed)} 个")
        for mint, symbol in resolved[:12]:
            print(f"  {mint[:10]}… → {symbol}")
        if len(resolved) > 12:
            print(f"  …另有 {len(resolved) - 12} 个")

        if not args.apply:
            print("\n（dry-run，没有写库；加 --apply 生效）")
            return 0

        changed = 0
        for mint, symbol in resolved:
            changed += store.rename_token_symbol(mint, symbol)
        print(f"\n已写库：{changed} 条事件的 symbol 换成真名字。")
        return 0
    finally:
        monitor.close()


if __name__ == "__main__":
    raise SystemExit(main())
