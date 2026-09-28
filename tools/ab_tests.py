#!/usr/bin/env python
"""比对两轮 `tools/run_tests.py` 日志的失败集（重构验证 gate 4 的判定器）。

为什么不能只看「全绿」
----------------------
这套测试里存在**先于重构就已损坏**的用例（例如 test_service_guard 里
`calls["ThreadingHTTPServer"]` —— 代码早已改名 `BoundedThreadingHTTPServer`，
测试没跟着改）。如果拿「必须全绿」当验收线，就会把一个陈旧测试误判成重构回归，
反过来也可能为了让它变绿而去改运行代码，那才是真正危险的。

因此判定标准是**失败集合的差集**：
    新版新增失败   → 回归（必须修）
    旧版有新版无   → 改善（记录下来）
    两边都失败     → 既有问题（不在本批次范围内）

用法
----
    python tools/ab_tests.py --old .runtime-cache/tests-old.log \
                             --new .runtime-cache/tests-new.log
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SUMMARY_RE = re.compile(r"@@SUMMARY@@\s*(?P<body>.*)")
ID_RE = re.compile(r"@@(?P<kind>FAIL|ERROR|SKIP)@@\s*(?P<id>.+)")


def _parse(path: Path) -> dict:
    kinds: dict[str, set[str]] = {"FAIL": set(), "ERROR": set(), "SKIP": set()}
    summary: dict[str, int] = {}
    stopped_early = False
    text = path.read_text(encoding="utf-8", errors="replace")

    for line in text.splitlines():
        m = ID_RE.search(line)
        if m:
            kinds[m.group("kind")].add(m.group("id").strip())
            continue
        m = SUMMARY_RE.search(line)
        if m:
            for token in m.group("body").split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    try:
                        summary[k] = int(v)
                    except ValueError:
                        pass
    # 看门狗触发 / 进程被杀：没有摘要行
    if not summary:
        stopped_early = True
    return {"kinds": kinds, "summary": summary, "stoppedEarly": stopped_early, "path": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="比对两轮测试日志的失败集")
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--label-old", default="旧版")
    parser.add_argument("--label-new", default="新版")
    args = parser.parse_args()

    old_path, new_path = Path(args.old), Path(args.new)
    for p in (old_path, new_path):
        if not p.exists():
            print(f"[ab-tests] ✗ 日志不存在: {p}")
            return 2

    old = _parse(old_path)
    new = _parse(new_path)
    print(f"[ab-tests] {args.label_old}: {old['path']}  {old['summary'] or '无摘要'}")
    print(f"[ab-tests] {args.label_new}: {new['path']}  {new['summary'] or '无摘要'}")
    print()

    if old["stoppedEarly"] or new["stoppedEarly"]:
        side = args.label_old if old["stoppedEarly"] else args.label_new
        print(f"[ab-tests] ⚠ {side}那一轮没有产出摘要行（可能被看门狗中断），"
              f"失败集比对不可靠，请先看日志尾部。")

    regressions: list[str] = []
    improvements: list[str] = []
    for kind in ("ERROR", "FAIL"):
        o, n = old["kinds"][kind], new["kinds"][kind]
        for tid in sorted(n - o):
            regressions.append(f"{kind} {tid}")
        for tid in sorted(o - n):
            improvements.append(f"{kind} {tid}")

    both = sorted((old["kinds"]["ERROR"] | old["kinds"]["FAIL"])
                  & (new["kinds"]["ERROR"] | new["kinds"]["FAIL"]))

    if both:
        print(f"[ab-tests] 两边都失败（既有问题，{len(both)} 项）—— 与本次重构无关:")
        for tid in both[:40]:
            print(f"    = {tid}")
        if len(both) > 40:
            print(f"    ... 另有 {len(both) - 40} 项")
        print()

    if improvements:
        print(f"[ab-tests] 旧版失败/报错、新版已消失（{len(improvements)} 项）:")
        for tid in improvements:
            print(f"    ↑ {tid}")
        print()

    if regressions:
        print(f"[ab-tests] ✗ 新版新增失败/报错（{len(regressions)} 项）—— 这是回归:")
        for tid in regressions:
            print(f"    ↓ {tid}")
        print()
        print("[ab-tests] 结果: 存在回归 ✗")
        return 1

    print("[ab-tests] 结果: 无新增失败（失败集≤旧版）✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
