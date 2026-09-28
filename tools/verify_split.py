#!/usr/bin/env python
"""拆分批次验证器：确认 server.py 在抽取后仍能正常导入，且关键不变量成立。

为什么需要它
------------
Golden Master（tools/refactor_baseline.py）验证的是【对外 HTTP 契约】，
但它需要启动完整服务。本脚本做更早、更轻的一道防线：只导入 server.py，
验证模块级代码能跑通 —— 能第一时间抓出：

  1. 循环导入（搬到新模块后又反向 import server）
  2. 名字缺失（定义删了但导入没补）
  3. ROOT 解析错误（__file__ 深度变化导致，静默但致命）
  4. 抽取后残留的重复定义（同名函数既在 server.py 又在 app.* 里）

用法
----
    python tools/verify_split.py                 # 默认隔离运行时目录
    python tools/verify_split.py --keep-runtime  # 保留临时运行时目录便于排查

注意：导入 server.py 会执行模块级副作用（创建各类 Store、做会话维护），
因此默认把 XINGYUN_RUNTIME_DIR 指向临时目录，避免触碰线上运行数据。
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 已抽取到 app.* 的名字 -> 期望来源模块。每完成一个批次就往这里追加。
EXTRACTED = {
    "app.core.paths": ["ROOT", "CODEX_HOME"],
    "app.core.config": [
        "TRUTHY_ENV_VALUES", "PRODUCTION_ENV_VALUES", "raw_env_value", "raw_env_flag",
        "is_raw_production_mode", "is_production_mode", "env_flag", "env_value",
        "expose_dev_code", "cookie_secure_enabled",
    ],
}

_CHILD = r'''
import json, os, sys, warnings
warnings.filterwarnings("ignore")
result = {"ok": False}
try:
    import server
    result["ok"] = True
    result["root"] = str(server.ROOT)
    result["root_is_project"] = (server.ROOT / "index.html").exists() and (server.ROOT / "server.py").exists()
    result["persist_dir"] = str(server.PERSIST_CACHE_DIR)
    found = {}
    for mod, names in json.loads(os.environ["EXTRACTED_JSON"]).items():
        for n in names:
            obj = getattr(server, n, "<MISSING>")
            found[n] = None if obj == "<MISSING>" else type(obj).__name__
    result["names"] = found
except Exception as exc:
    import traceback
    result["error"] = f"{type(exc).__name__}: {exc}"
    result["traceback"] = traceback.format_exc()[-2500:]
print("@@RESULT@@" + json.dumps(result, ensure_ascii=False))
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-runtime", action="store_true")
    args = parser.parse_args()

    runtime_dir = tempfile.mkdtemp(prefix="refactor-verify-")
    env = dict(os.environ)
    env["XINGYUN_RUNTIME_DIR"] = runtime_dir
    env["EXTRACTED_JSON"] = __import__("json").dumps(EXTRACTED)
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"[verify] 项目根      : {PROJECT_ROOT}")
    print(f"[verify] 隔离运行时  : {runtime_dir}")
    print("[verify] 导入 server.py ...")

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        print("[verify] ✗ 导入超时（300s），可能存在死锁或阻塞式初始化")
        return 1

    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@RESULT@@"):
            payload = __import__("json").loads(line[len("@@RESULT@@"):])
    if payload is None:
        print("[verify] ✗ 子进程未返回结果")
        print("--- stdout ---")
        print((proc.stdout or "")[-3000:])
        print("--- stderr ---")
        print((proc.stderr or "")[-3000:])
        return 1

    failures: list[str] = []
    if not payload.get("ok"):
        print(f"[verify] ✗ 导入失败: {payload.get('error')}")
        print(payload.get("traceback", ""))
        failures.append("导入失败")
    else:
        print("[verify] ✓ server.py 导入成功")
        if payload.get("root_is_project"):
            print(f"[verify] ✓ ROOT 解析正确: {payload['root']}")
        else:
            print(f"[verify] ✗ ROOT 解析错误: {payload['root']}（应为项目根）")
            failures.append("ROOT 解析错误")
        rd = str(payload.get("persist_dir", ""))
        if runtime_dir.replace("\\", "/") in rd.replace("\\", "/"):
            print(f"[verify] ✓ 运行时隔离生效: {rd}")
        else:
            print(f"[verify] ⚠ 运行时未隔离: {rd}")
        print("[verify] 已抽取名字可用性:")
        for mod, names in EXTRACTED.items():
            line = []
            for n in names:
                t = payload["names"].get(n)
                if t is None:
                    failures.append(f"{n} 不在 server 命名空间")
                    line.append(f"{n}=缺失")
                else:
                    line.append(f"{n}={t}")
            print(f"    {mod}: " + ", ".join(line))

    # 残留定义检查：抽取过的名字不应再在 server.py 里定义
    import ast
    src = (PROJECT_ROOT / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    defined = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
    moved = {n for names in EXTRACTED.values() for n in names}
    leftovers = sorted(moved & defined)
    if leftovers:
        print(f"[verify] ✗ server.py 仍有重复定义: {leftovers}")
        failures.append(f"重复定义 {leftovers}")
    else:
        print("[verify] ✓ server.py 无残留重复定义")

    if not args.keep_runtime:
        shutil.rmtree(runtime_dir, ignore_errors=True)

    print()
    if failures:
        print(f"[verify] 结果: 失败（{len(failures)} 项）")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("[verify] 结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
