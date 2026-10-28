#!/usr/bin/env python
"""GMGN「关注钱包交易动态」探针 —— 判断能否用账号的关注列表替代手工填钱包。

背景
----
被夹检测（sandwich_detector.py）目前要求手工填钱包地址。
GMGN 提供了一个更贴近「账号下的交易记录」的接口：

    gmgn-cli track follow-wallet --chain <chain>   →  GET /v1/trade/follow_wallet

本机实测（2026-10-14）该路由在 **免费档**（响应体里的 ``tier: "free"``）恒返回
``429 RATE_LIMIT_EXCEEDED``，而同一 IP 上的 ``/v1/user/*`` 路由（如
``portfolio activity``、``track follow-tokens``）**正常返回** —— 说明不是全局断网、
也不是凭据问题，而是这条重路由拿不到配额（免费档漏桶 capacity=5，重路由需要整桶几乎为空；
正在运行的看板服务在持续抽水）。

用法
----
    python tools/gmgn_follow_wallet_probe.py                # 默认 bsc
    python tools/gmgn_follow_wallet_probe.py --chain eth --limit 5
    python tools/gmgn_follow_wallet_probe.py --all-chains    # 逐链试探

为了拿到真实响应，**先让看板服务停下来**（停止后台服务.cmd / taskkill），
把 GMGN 的 IP 配额让出来，再跑本脚本。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

GMGN_CLI_INDEX = Path(
    os.getenv("GMGN_CLI_INDEX_JS", r"C:\Users\ZhuanZ1\AppData\Roaming\npm\node_modules\gmgn-cli\dist\index.js")
)
CHAINS = ("bsc", "eth", "base", "robinhood")
MASK_KEYS = {"wallet", "tx_hash", "from_address", "to_address", "maker", "address"}


def mask(value: object) -> str:
    """地址/hash 只留头尾，避免把完整地址写进日志。"""
    text = str(value or "")
    return f"{text[:10]}…{text[-6:]}" if len(text) > 20 else text


def describe_row(row: dict) -> str:
    parts = []
    for key in sorted(row):
        value = row[key]
        if key in MASK_KEYS:
            shown = mask(value)
        elif isinstance(value, dict):
            inner = {k: value.get(k) for k in ("address", "symbol", "name") if value.get(k)}
            shown = json.dumps(inner, ensure_ascii=False)[:60] or "{}"
        else:
            shown = json.dumps(value, ensure_ascii=False)[:60]
        parts.append(f"      {key}: {shown}")
    return "\n".join(parts)


def probe(chain: str, limit: int) -> dict:
    node = shutil.which("node") or "node"
    cmd = [
        node, str(GMGN_CLI_INDEX), "track", "follow-wallet",
        "--chain", chain, "--limit", str(limit), "--raw",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"chain": chain, "status": "transport-error", "detail": f"{type(exc).__name__}: {exc}"}

    if result.returncode != 0:
        return {"chain": chain, "status": "cli-error", "detail": (result.stderr or result.stdout).strip()[:400]}

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"chain": chain, "status": "bad-json", "detail": (result.stdout or "")[:300]}

    return {"chain": chain, "status": "ok", "payload": payload}


def report(outcome: dict) -> bool:
    chain = outcome["chain"]
    status = outcome["status"]
    if status != "ok":
        detail = outcome.get("detail", "")
        marker = "限频/封禁" if "RATE_LIMIT" in detail else "失败"
        print(f"[{chain}] ✗ {marker}: {detail[:220]}")
        return False

    payload = outcome["payload"]
    if not isinstance(payload, dict):
        print(f"[{chain}] ✓ 但返回不是对象: {type(payload).__name__}")
        return True

    print(f"[{chain}] ✓ 顶层字段: {sorted(payload)}")
    rows = None
    for key in ("list", "records", "trades", "activities", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            rows = value
            print(f"[{chain}]   列表字段 = {key!r}，共 {len(value)} 条")
            break
    if rows is None:
        print(f"[{chain}]   未识别到列表字段，原文: {json.dumps(payload, ensure_ascii=False)[:400]}")
        return True
    if not rows:
        print(f"[{chain}]   列表为空 —— 该账号在这条链上没有关注钱包或没有成交")
        return True

    row = rows[0]
    if not isinstance(row, dict):
        print(f"[{chain}]   首行不是对象: {row!r}")
        return True
    for key in ("wallet", "tx_hash", "side", "timestamp", "amount_usd", "token"):
        print(f"[{chain}]   关键字段 {key!r}: {'有' if key in row else '缺'}")
    print(f"[{chain}]   首行全字段:")
    print(describe_row(row))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="GMGN 关注钱包交易动态探针")
    parser.add_argument("--chain", default="bsc", help="链（默认 bsc）")
    parser.add_argument("--limit", type=int, default=3, help="拉取条数（默认 3）")
    parser.add_argument("--all-chains", action="store_true", help="逐链试探")
    args = parser.parse_args()

    if not GMGN_CLI_INDEX.exists():
        print(f"✗ 找不到 gmgn-cli: {GMGN_CLI_INDEX}（可用 GMGN_CLI_INDEX_JS 覆盖）")
        return 2

    chains = CHAINS if args.all_chains else (args.chain,)
    ok = False
    for chain in chains:
        outcome = probe(chain, args.limit)
        ok = report(outcome) or ok
        print()
    if not ok:
        now = datetime.datetime.now()
        print("全部失败。若为 RATE_LIMIT：先把看板服务停掉腾出 IP 配额，或升级 GMGN 套餐。")
        print(f"（本机当前时间 {now:%Y-%m-%d %H:%M:%S}，用于对照响应里的 reset_at）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
