#!/usr/bin/env python
"""Golden Master 基线采集与结构比对工具（重构安全网）。

为什么需要它
------------
"重构不改变任何功能"必须有客观判据。本项目行情数据每秒都在变，
逐值比对必然误报；因此本工具只比对【响应结构指纹】：
HTTP 状态码、Content-Type、JSON 合法性、键集合与类型树。
任何结构差异都说明对外契约被破坏，即判定为回归。

数值型字段只记录类型（int/float/str），不记录具体值；
列表项类型做并集合并，降低"采集时恰好为空"造成的假阴性。

用法
----
    # 采集基线（对当前运行中的服务）
    python tools/refactor_baseline.py capture --base-url http://127.0.0.1:8765

    # 校验重构后的服务（通常起在另一个端口）
    python tools/refactor_baseline.py verify --base-url http://127.0.0.1:8899

退出码
------
    0 = 结构与基线完全一致
    1 = 检测到回归（存在结构差异）
    2 = 工具自身错误（无法连接 / 基线不存在等）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = PROJECT_ROOT / "tests" / "fixtures" / "refactor-baseline" / "golden-master.json"

# 采集目标：仅只读 GET 端点。带必填参数的端点会如实记录其状态码（如 400），
# 这同样是对外契约的一部分。
GET_ENDPOINTS: list[tuple[str, str]] = [
    ("health", "/api/health"),
    ("market-hot", "/api/market-hot"),
    ("newsflash", "/api/newsflash"),
    ("price-watch", "/api/price-watch"),
    ("price-structures", "/api/price-structures"),
    ("event-flow", "/api/event-flow"),
    ("event-monitor", "/api/event-monitor"),
    ("highlight-rankings", "/api/gainers-rankings"),
    ("turnover-rankings", "/api/turnover-rankings"),
    ("new-coin-rankings", "/api/new-coin-rankings"),
    ("rotation-map", "/api/rotation-map"),
    ("chain-ecosystem", "/api/chain-ecosystem"),
    ("binance-wallet-hot", "/api/binance-wallet-hot"),
    ("smart-money-monitor", "/api/smart-money-monitor"),
    ("wechat-group-monitor", "/api/wechat-group-monitor"),
    ("personal-x-monitor", "/api/personal-x-monitor"),
    ("x-kol-feed", "/api/x-kol-feed"),
    ("x-kol-sources", "/api/x-kol-sources"),
    ("gmgn-hot-search", "/api/gmgn-hot-search"),
    ("global-hotspots", "/api/global-hotspots"),
    ("listing-events", "/api/listing-events"),
    ("onchain-trenches", "/api/onchain-trenches"),
    ("onchain-research-mode", "/api/onchain-research-mode"),
    ("strategy-board", "/api/strategy-board"),
    ("strategy-exchanges", "/api/strategy-exchanges"),
    ("exchange-ai-narratives", "/api/exchange-ai-narratives"),
    ("aster-contracts", "/api/aster-contracts"),
    ("new-coin-low-structures", "/api/new-coin-low-structures"),
    ("alert-inbox", "/api/alert-inbox"),
    ("desktop-alert", "/api/desktop-alert"),
    ("automation-briefs", "/api/automation-briefs"),
    ("todo-state", "/api/todo-state"),
    ("rss-sources", "/api/rss-sources"),
    ("rss-items", "/api/rss-items"),
    ("dragon-wave-precomputed", "/api/dragon-wave-precomputed"),
    ("dragon-wave-feedback", "/api/dragon-wave-feedback"),
    ("self-optimization-status", "/api/self-optimization-status"),
    ("service-liveness", "/api/service-liveness"),
    ("wechat-account-status", "/api/wechat-account-status"),
    ("site-alert-monitor-status", "/api/site-alert-monitor-status"),
]

# 结构性键：这些键的值随市场变化，但键本身必须存在。
# 采集时不记录它们的值，只记录类型。
VOLATILE_HINT_KEYS = frozenset({"updatedAt", "generatedAt", "timestamp", "ts", "serverTime"})

MAX_DEPTH = 8
LIST_SHAPE_SAMPLE = 24  # 列表项形状最多合并前 N 个元素，避免超大数组拖慢
REQUEST_TIMEOUT = 25  # 可被 --timeout 覆盖；部分端点当前基线即 >25s


def shape_of(value: Any, depth: int = 0) -> Any:
    """把任意 JSON 值折叠成【结构指纹】，丢弃易变的具体数值。"""
    if depth > MAX_DEPTH:
        return "..."
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return {str(k): shape_of(v, depth + 1) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        merged: dict[str, Any] = {}
        has_complex = False
        union_scalars: set[str] = set()
        for item in list(value)[:LIST_SHAPE_SAMPLE]:
            s = shape_of(item, depth + 1)
            if isinstance(s, dict):
                has_complex = True
                for k, v in s.items():
                    if k in merged and merged[k] != v:
                        # 同一键在不同元素里类型不同 → 标记为混合
                        if merged[k] != "mixed":
                            merged[k] = "mixed"
                    else:
                        merged.setdefault(k, v)
            else:
                union_scalars.add(str(s))
        if has_complex:
            return [merged]
        if union_scalars:
            return [sorted(union_scalars)]
        return []
    return type(value).__name__


def merge_shape(a: Any, b: Any) -> Any:
    """跨采样合并两个结构指纹（取并集），降低"恰好为空"造成的漂移。"""
    if a is None:
        return b
    if b is None:
        return a
    if a == b:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge_shape(out.get(k), v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        if not a:
            return b
        if not b:
            return a
        return [merge_shape(a[0], b[0])]
    return "mixed"


def fetch(base_url: str, path: str, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json,*/*", "Connection": "close"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
    except Exception as exc:  # 连接失败 / 超时
        return {
            "status": None,
            "contentType": None,
            "json": False,
            "shape": None,
            "bytes": 0,
            "error": type(exc).__name__,
            "elapsedMs": int((time.perf_counter() - started) * 1000),
        }

    elapsed = int((time.perf_counter() - started) * 1000)
    shape: Any = None
    is_json = False
    if "json" in (ctype or "").lower() or raw[:1] in (b"{", b"["):
        try:
            shape = shape_of(json.loads(raw.decode("utf-8", errors="replace")))
            is_json = True
        except Exception:
            shape = None
    return {
        "status": status,
        "contentType": (ctype or "").split(";")[0].strip() or None,
        "json": is_json,
        "shape": shape,
        "bytes": len(raw),
        "error": None,
        "elapsedMs": elapsed,
    }


def collect(base_url: str, samples: int, workers: int,
            timeout: int = REQUEST_TIMEOUT, only: set[str] | None = None) -> dict[str, Any]:
    targets = [(n, p) for n, p in GET_ENDPOINTS if not only or n in only]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {name: (path, pool.submit(_sample_endpoint, base_url, path, samples, timeout))
                   for name, path in targets}
        out: dict[str, Any] = {}
        for name, (path, fut) in futures.items():
            payload = fut.result()
            out[name] = {"path": path, **payload}
    return out


def _sample_endpoint(base_url: str, path: str, samples: int, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    first = fetch(base_url, path, timeout)
    shape = first.get("shape")
    statuses = {first.get("status")}
    for _ in range(max(0, samples - 1)):
        again = fetch(base_url, path, timeout)
        statuses.add(again.get("status"))
        shape = merge_shape(shape, again.get("shape"))
    return {
        "status": sorted(s for s in statuses if s is not None) or [None],
        "contentType": first.get("contentType"),
        "json": first.get("json"),
        "shape": shape,
        "bytes": first.get("bytes"),
        "error": first.get("error"),
        "elapsedMs": first.get("elapsedMs"),
    }


def _is_ok(entry: dict[str, Any]) -> bool:
    status = entry.get("status")
    if isinstance(status, list):
        return 200 in status
    return status == 200


def cmd_capture(args: argparse.Namespace) -> int:
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    print(f"[capture] 目标服务: {args.base_url}  采样: {args.samples}  超时: {args.timeout}s"
          f"{'  子集: ' + ','.join(sorted(only)) if only else ''}")
    data = collect(args.base_url, args.samples, args.workers, args.timeout, only)

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.merge and path.exists() and only:
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing["endpoints"].update(data)
        existing["endpointCount"] = len(existing["endpoints"])
        existing["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        existing["baseUrl"] = args.base_url
        out = existing
    else:
        out = {
            "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "baseUrl": args.base_url,
            "endpointCount": len(data),
            "endpoints": data,
        }
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    ok = sum(1 for e in data.values() if _is_ok(e))
    print(f"[capture] 已写入 {path}")
    print(f"[capture] 可用(200): {ok}/{len(data)}")
    for name, e in sorted(data.items()):
        flag = "OK  " if _is_ok(e) else "SLOW"
        print(f"  {flag} {name:32s} status={e['status']} bytes={e['bytes']} {e['elapsedMs']}ms json={e['json']}")
    return 0


def diff_shapes(a: Any, b: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if a == b:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = f"{prefix}.{k}" if prefix else str(k)
            if k not in a:
                out.append(f"{p}: 新增字段")
            elif k not in b:
                out.append(f"{p}: 字段丢失")
            else:
                out.extend(diff_shapes(a[k], b[k], p))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if bool(a) != bool(b):
            out.append(f"{prefix}: 列表空/非空状态变化")
        elif a and b:
            out.extend(diff_shapes(a[0], b[0], f"{prefix}[]"))
        return out
    out.append(f"{prefix}: 类型 {a!r} -> {b!r}")
    return out


def cmd_verify(args: argparse.Namespace) -> int:
    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(f"[verify] 基线不存在: {baseline_path}", file=sys.stderr)
        return 2
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    print(f"[verify] 基线采集于 {baseline.get('capturedAt')} ({baseline.get('baseUrl')})")
    print(f"[verify] 目标服务: {args.base_url}  超时: {args.timeout}s")
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    current = collect(args.base_url, args.samples, args.workers, args.timeout, only)

    regressions: list[str] = []
    warnings: list[str] = []
    checked = 0
    for name, base in sorted(baseline["endpoints"].items()):
        if only and name not in only:
            continue
        cur = current.get(name)
        if cur is None:
            regressions.append(f"{name}: 端点缺失")
            continue
        base_ok = _is_ok(base)
        cur_ok = _is_ok(cur)
        if base_ok and not cur_ok:
            regressions.append(f"{name}: 状态 {base.get('status')} -> {cur.get('status')} (原本可用)")
            continue
        if base.get("json") and not cur.get("json"):
            regressions.append(f"{name}: 原本返回 JSON，现在不是")
            continue
        if base_ok and base.get("shape") is not None and cur.get("shape") is not None:
            checked += 1
            regressions.extend(diff_shapes(base["shape"], cur["shape"], name))
        elif not base_ok:
            warnings.append(f"{name}: 基线即非可用(status={base.get('status')})，仅比对状态码")

    print()
    print(f"[verify] 完成结构比对的端点: {checked}")
    if warnings:
        print(f"[verify] 基线本身不可用的端点（仅状态码比对）: {len(warnings)}")
        for w in warnings[:10]:
            print(f"  - {w}")
        print()
    if regressions:
        print(f"[verify] 检测到 {len(regressions)} 处结构差异（回归）:")
        for r in regressions[:200]:
            print(f"  !! {r}")
        if len(regressions) > 200:
            print(f"  ... 其余 {len(regressions) - 200} 处省略")
        return 1
    print("[verify] 通过：所有端点结构指纹与基线一致。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="重构 Golden Master 基线工具")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="采集基线")
    cap.add_argument("--base-url", default="http://127.0.0.1:8765")
    cap.add_argument("--output", default=str(DEFAULT_BASELINE))
    cap.add_argument("--samples", type=int, default=2, help="每端点采样次数（合并形状，默认 2）")
    cap.add_argument("--workers", type=int, default=4)
    cap.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT, help="单请求超时秒数")
    cap.add_argument("--only", default="", help="仅采集指定端点（逗号分隔），配合 --merge 增量补齐")
    cap.add_argument("--merge", action="store_true", help="把结果合并进已有基线（需配合 --only）")
    cap.set_defaults(func=cmd_capture)

    ver = sub.add_parser("verify", help="与基线比对")
    ver.add_argument("--base-url", default="http://127.0.0.1:8765")
    ver.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ver.add_argument("--samples", type=int, default=2)
    ver.add_argument("--workers", type=int, default=4)
    ver.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT)
    ver.add_argument("--only", default="", help="仅比对该子集")
    ver.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 2


if __name__ == "__main__":
    sys.exit(main())
