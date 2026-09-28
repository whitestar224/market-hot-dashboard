#!/usr/bin/env python
"""统一 Python 测试入口（重构验证 gate 4）。

为什么不用 pytest
-----------------
本项目的测试全部是 stdlib `unittest` + `import server` 风格，且本机未安装 pytest。
直接 `python -m unittest discover` 即可，不引入新依赖、不改运行行为。

为什么要看门狗
--------------
部分测试会发起真实网络请求，网络异常时可能长时间阻塞。用 faulthandler 在
超时后打印所有线程栈并强制退出，避免验证流程无限挂起（挂起时你还能看到卡在哪）。

为什么必须隔离
--------------
`AUTH_DB_PATH` / `PERSIST_CACHE_DIR` 都派生自 `XINGYUN_RUNTIME_DIR`。
默认把它指向临时目录，测试就绝不会写到线上运行数据里。

用法
----
    python tools/run_tests.py                       # 全量
    python tools/run_tests.py --root <其他检出目录>   # 在 git worktree 里跑同一套测试
    python tools/run_tests.py --fail-fast --pattern "test_price_watch_*.py"

输出末尾的 `@@SUMMARY@@` / `@@FAIL@@` / `@@ERROR@@` 行是机器可读的，
`tools/ab_tests.py` 靠它做新旧两版的失败集比对。
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import sys
import tempfile
import unittest
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="统一 Python 测试入口")
    parser.add_argument("--root", default=".", help="项目根（默认当前目录）")
    parser.add_argument("--pattern", default="test_*.py", help="测试文件匹配模式")
    parser.add_argument("--start-dir", default="tests", help="测试目录")
    parser.add_argument("--watchdog", type=float, default=2400.0,
                        help="整轮看门狗秒数，超时打印线程栈并退出（0=关闭）")
    parser.add_argument("--runtime-dir", default="", help="指定运行期目录（默认新建临时目录）")
    parser.add_argument("--verbosity", type=int, default=2)
    parser.add_argument("--exclude", default="", help="排除的测试 ID 子串（逗号分隔），用于跳过已知死锁/陈旧用例")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    start = root / args.start_dir
    if not start.is_dir():
        print(f"[tests] ✗ 测试目录不存在: {start}")
        return 2

    os.chdir(root)
    # -m unittest 时 sys.path[0] 是原 cwd；这里显式补项目根，保证 `import server` 可解析
    sys.path.insert(0, str(root))

    runtime = args.runtime_dir or tempfile.mkdtemp(prefix="mhd-tests-")
    os.environ["XINGYUN_RUNTIME_DIR"] = runtime
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    print(f"[tests] root              = {root}")
    print(f"[tests] XINGYUN_RUNTIME_DIR = {runtime}")
    print(f"[tests] pattern           = {args.pattern}")

    if args.watchdog > 0:
        faulthandler.dump_traceback_later(args.watchdog, exit=True)
        print(f"[tests] 看门狗            = {args.watchdog:.0f}s")

    loader = unittest.TestLoader()
    try:
        # 注意：不要传 top_level_dir=root。`tests/` 没有 __init__.py（是隐式命名空间包），
        # unittest 的 discover 会以「是否含 __init__.py」判定包，传 top_level_dir 就报
        # "Start directory is not importable"。让 top_level 默认等于 start_dir 即可，
        # `import server` 由上面加进 sys.path 的项目根兜住。
        suite = loader.discover(start_dir=str(start), pattern=args.pattern)
    except Exception as exc:  # 收集期就炸（例如 import server 失败）
        print(f"[tests] ✗ 收集测试失败: {type(exc).__name__}: {exc}")
        print(f"@@SUMMARY@@ total=0 failures=0 errors=1 skipped=0")
        print(f"@@ERROR@@ <collection> {type(exc).__name__}: {exc}")
        return 1

    # 排除已知死锁/陈旧用例（新旧两版用同样的排除集，才能做失败集差集）
    exclude_substrs = [s.strip() for s in args.exclude.split(",") if s.strip()]
    if exclude_substrs:
        def _flatten(suite_obj):
            for item in suite_obj:
                if isinstance(item, unittest.TestSuite):
                    yield from _flatten(item)
                else:
                    yield item

        kept = unittest.TestSuite()
        dropped = []
        for case in _flatten(suite):
            tid = case.id()
            if any(sub in tid for sub in exclude_substrs):
                dropped.append(tid)
            else:
                kept.addTest(case)
        suite = kept
        print(f"[tests] 排除 {len(dropped)} 个用例（{args.exclude}）:")
        for tid in dropped:
            print(f"    - {tid}")

    total = suite.countTestCases()
    print(f"[tests] 收集到 {total} 个用例，开始执行 ...\n")

    runner = unittest.TextTestRunner(verbosity=args.verbosity, stream=sys.stdout)
    result = runner.run(suite)

    print()
    for test, _ in result.failures:
        print(f"@@FAIL@@ {test.id()}")
    for test, _ in result.errors:
        print(f"@@ERROR@@ {test.id()}")
    for test, _ in result.skipped:
        print(f"@@SKIP@@ {test.id()}")
    print(f"@@SUMMARY@@ total={result.testsRun} failures={len(result.failures)} "
          f"errors={len(result.errors)} skipped={len(result.skipped)}")

    if args.watchdog > 0:
        faulthandler.cancel_dump_traceback_later()

    print(f"[tests] 结果: {'通过 ✓' if result.wasSuccessful() else '失败 ✗'}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
