"""一次性探针：用 GMGN wallet_activity 给一个 EVM 地址做成交画像。

不是常驻工具，只用来给新钱包定弹窗阈值（与 CryptoCharming / leslie 那两次同一手法）。
口径与 `gmgn_agentic.py` 一致：``X-APIKEY`` 头 + query 里带 ``timestamp`` / ``client_id``。
"""
from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import time
import uuid

import requests

BASE = "https://openapi.gmgn.ai"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
}


def api_key() -> str:
    env = dict(
        line.split("=", 1)
        for line in pathlib.Path(os.path.expanduser("~/.config/gmgn/.env"))
        .read_text(encoding="utf-8", errors="ignore")
        .splitlines()
        if "=" in line
    )
    return env["GMGN_API_KEY"].strip()


def fetch(chain: str, address: str, *, pages: int = 3, sleep: float = 8.0) -> list[dict]:
    session = requests.Session()
    session.trust_env = False
    rows: list[dict] = []
    cursor = ""
    for page in range(pages):
        params = {
            "chain": chain,
            "wallet_address": address,
            "timestamp": int(time.time()),
            "client_id": str(uuid.uuid4()),
        }
        if cursor:
            params["cursor"] = cursor
        response = session.get(
            BASE + "/v1/user/wallet_activity",
            params=params,
            headers={**HEADERS, "X-APIKEY": api_key()},
            timeout=30,
        )
        if response.status_code != 200:
            print(f"  [{chain}] p{page} HTTP {response.status_code} {response.text[:160]}", file=sys.stderr)
            break
        payload = response.json()
        if str(payload.get("code")) not in {"0", "None"}:
            print(f"  [{chain}] p{page} code={payload.get('code')} {str(payload.get('message'))[:120]}", file=sys.stderr)
            break
        batch = payload.get("data", {}).get("activities") or []
        rows.extend(batch)
        cursor = str((payload.get("data") or {}).get("next") or "")
        print(f"  [{chain}] p{page}: +{len(batch)}（累计 {len(rows)}）", file=sys.stderr)
        if not cursor or not batch:
            break
        time.sleep(sleep)
    return rows


def to_usd(row: dict) -> float:
    for key in ("cost_usd", "buy_cost_usd", "quote_amount"):
        try:
            value = float(row.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 0.0


def profile(chain: str, rows: list[dict]) -> None:
    buys = [to_usd(r) for r in rows if str(r.get("event_type")).lower() == "buy"]
    buys = [x for x in buys if x > 0]
    stamps = sorted(int(r.get("timestamp") or 0) for r in rows if r.get("timestamp"))
    span = (stamps[-1] - stamps[0]) / 3600.0 if len(stamps) > 1 else 0
    print(f"\n=== {chain}: {len(rows)} 条活动，{len(buys)} 笔买入，跨度 {span:.1f}h")
    if not buys:
        return
    buys.sort()
    q = statistics.quantiles(buys, n=10) if len(buys) >= 10 else []
    if q:
        print(f"    中位 ${statistics.median(buys):.0f}  p75 ${q[7]:.0f}  p90 ${q[8]:.0f}  最大 ${buys[-1]:.0f}")
    else:
        print(f"    中位 ${statistics.median(buys):.0f}  （样本不足 10 笔，不给分位）  最大 ${buys[-1]:.0f}")
    print("    逐笔：" + ", ".join(f"{x:.0f}" for x in buys))
    for thr in (50, 100, 150, 200, 300, 500):
        print(f"      ≥{thr}U: {sum(1 for x in buys if x >= thr)} 笔")


def main() -> int:
    address = sys.argv[1] if len(sys.argv) > 1 else "0x5e784c1a19cef648eda4178b5a74e4963b4dc428"
    chains = sys.argv[2].split(",") if len(sys.argv) > 2 else ["bsc", "ethereum", "base", "robinhood"]
    pages = int(os.getenv("PROBE_PAGES", "3") or 3)
    out: dict[str, list[dict]] = {}
    for chain in chains:
        rows = fetch(chain, address, pages=pages)
        out[chain] = rows
        profile(chain, rows)
        time.sleep(4)
    path = pathlib.Path(f".runtime-cache/_wallet_activity_{address[:10]}.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
