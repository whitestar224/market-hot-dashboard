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
# 已抽取的模块。每项分两类：
#   reexported —— server.py 仍会引用的名字，必须能在 server 命名空间解析，
#                 且不得再在 server.py 里重复定义
#   internal   —— 只在新模块内部使用的辅助函数，不应出现在 server 命名空间
EXTRACTED = {
    "app.core.paths": {
        "reexported": ["ROOT", "CODEX_HOME"],
        "internal": [],
    },
    "app.core.config": {
        "reexported": [
            "TRUTHY_ENV_VALUES", "PRODUCTION_ENV_VALUES", "raw_env_value", "raw_env_flag",
            "is_raw_production_mode", "is_production_mode", "env_flag", "env_value",
            "expose_dev_code", "cookie_secure_enabled",
        ],
        "internal": [],
    },
    "app.api.seo": {
        "reexported": ["SEO_NOINDEX_PATHS", "SEO_PUBLIC_PAGES", "inject_seo_into_html",
                       "normalized_page_path", "robots_txt", "sitemap_xml"],
        "internal": ["request_base_url", "canonical_url", "strip_runtime_seo_tags",
                     "seo_schema_for_page", "request_base_url_for_schema", "inject_site_footer",
                     "site_footer_html", "refresh_stylesheet_version", "seo_head_block"],
    },
    "app.api.smart_money": {
        "reexported": [
            "global_hotspot_candidate_matches", "global_hotspot_claim_batch",
            "global_hotspot_day_key", "global_hotspot_enrich_candidate",
            "global_hotspot_identity_text", "global_hotspot_match_event",
            "global_hotspot_monitor_loop", "global_hotspot_prompt",
            "global_hotspot_public_url", "global_hotspot_research_rows",
            "global_hotspot_snapshot", "global_hotspot_source_row",
            "global_hotspot_text_list", "normalize_global_hotspot_event",
            "run_global_hotspot_batch",
        ],
        "internal": [],
    },
    "app.core.state": {
        # check_all_identity：把模块导出的全部公开名字逐个与 server 命名空间做
        # `is` 同一性比对。这对锁/缓存是生死线 —— 若出现「两把不同的锁」，
        # 互斥会静默失效（偶发、极难排查），而普通的名字存在性检查发现不了。
        #
        # 「未再导出」不再是无害信息！批次 3 实测踩坑：PRICE_STRUCTURE_REENTRY_*
        # 三个常量搬进 state.py 却没在 server.py 再导出，导致外部测试通过
        # server.NAME 访问时 AttributeError（回归）。教训：凡 server.py 之外
        # （tests / 其他模块）仍通过 `server.` 前缀引用的公开名字，必须再导出。
        # 本检查器无法静态知道外部引用，因此把「未再导出」升级为警告（不打成失败，
        # 因为确有一批真·内部名字只在本模块内用）。真正豁免的少数内部名字列在
        # `internal` 里。
        "check_all_identity": True,
        "reexported": [],
        "internal": ["configured_runtime_dir"],
    },
}

# 被 `global` 重绑定的名字：绝不能出现在任何抽取模块里。
# 原因：函数里的 `global X` 会写到 server 命名空间；若 X 同时被某个模块以
# `from ... import X` 绑定，模块里那份将永远停留在初始值（静默行为改变）。
REBOUND_MUST_STAY = {
    "CODEX_CLI_UNAVAILABLE_UNTIL", "LLM_API_UNAVAILABLE_UNTIL", "ONCHAIN_RESEARCH_INGEST_THREAD",
    "ROTATION_AI_RETRY_AFTER", "X_KOL_OFFICIAL_STREAM_RESPONSE", "DISCORD_NEWSFLASH_BRIDGE_PROCESS",
    "TRENCH_PERSON_ACTIVITY_THREAD", "TRENCH_PERSON_REPLAY_THREAD", "TRENCH_PERSON_REPLAY_PENDING",
    "NEWSFLASH_SEMANTIC_AI_RETRY_AFTER", "ROTATION_AI_INFLIGHT", "RANK_AI_RETRY_AFTER",
    "DESKTOP_ALERT_QUEUE_ACTIVE", "PRICE_STRUCTURE_RECENT_LISTING_INDEX_CACHE",
    "PRICE_WATCH_REALTIME_PROCESS", "NEW_COIN_LOW_MONITOR_ACTIVE", "PRICE_MONITOR_ACTIVITY_SUMMARY",
    "PRICE_WATCH_MONITOR_ACTIVE", "SITE_ALERT_MONITOR_ACTIVE", "SELF_OPTIMIZATION_INTERVAL_SECONDS",
    "NEW_COIN_LOW_ACTIVITY_SUMMARY", "NEW_COIN_LOW_INVENTORY_CACHE", "PRICE_STRUCTURE_MONITOR_ACTIVE",
    "SITE_ALERT_STATE", "WECHAT_AUTH_MONITOR_ACTIVE", "BINANCE_WALLET_4H_STRUCTURE_ACTIVE",
    "SELF_OPTIMIZATION_SUGGESTION_COOLDOWN_SECONDS", "SELF_OPTIMIZATION_MONITOR_ACTIVE",
    "SELF_OPTIMIZATION_STARTUP_DELAY_SECONDS", "DESKTOP_ALERT_ACTIVE_PROCESS_SLOT",
    "DESKTOP_ALERT_ACTIVE_PROCESS", "PRICE_WATCH_SNAPSHOT_CACHE", "NEW_COIN_LOW_ACTIVITY_CACHE",
    "PRICE_STRUCTURE_PREARM_MONITOR_ACTIVE", "CHAT_HOURLY_SUMMARY_STARTED",
    "WECHAT_GROUP_MONITOR_STARTED", "SITE_ALERT_MONITOR_STARTED_AT", "SERVER_RUNTIME_ACTIVE",
    "FIELD_KEY_CACHE", "LOCAL_TRANSLATION_PROCESS", "GMGN_TRENCH_X_POST_NEXT_REQUEST_AT",
    "WECHAT_AUTH_ALERT_LAST_AT", "BINANCE_WALLET_4H_STRUCTURE_LAST_SYNC_AT",
    "TRENCH_PERSON_WATCH_STARTED", "AI_STARTUP_RECONNECT_STARTED", "NEWS_EXPLANATIONS",
    "DESKTOP_ALERT_LAST_LAUNCHED_AT", "DESKTOP_ALERT_LAST_LAUNCHED_PRIORITY",
    "DESKTOP_ALERT_SEEN_LOADED", "BINANCE_FUTURES_STATUS_UPDATED_AT", "PRICE_WATCH_SNAPSHOT_CACHE_AT",
    "PRICE_WATCH_SNAPSHOT_BUILD_STARTED_AT", "PRICE_WATCH_SNAPSHOT_BUILD_THREAD",
    "PRICE_WATCH_SNAPSHOT_STALL_LOGGED", "PRICE_STRUCTURE_WATCH_ROWS_CACHE",
    "PRICE_STRUCTURE_WATCH_ROWS_REFRESHING", "PRICE_STRUCTURE_DISK_CACHE_HYDRATED",
    "PRICE_MONITOR_ACTIVITY_STATES", "PRICE_WATCH_REALTIME_SSL_CONTEXT",
    "X_KOL_OFFICIAL_STREAM_RULE_SIGNATURE", "X_KOL_OFFICIAL_STREAM_STARTED",
    "X_KOL_PRIORITY_STARTED", "SITE_ALERT_STATE_LOADED", "BINANCE_WALLET_HOT_ALERT_MONITOR_ACTIVE",
    "AVE_HOT_ALERT_MONITOR_ACTIVE", "GLOBAL_HOTSPOT_MONITOR_ACTIVE",
}

_CHILD = r'''
import importlib, json, os, sys, warnings
warnings.filterwarnings("ignore")
result = {"ok": False, "module_names": {}, "server_names": {}, "identity": {}, "rebound_leak": {}}
spec = json.loads(os.environ["EXTRACTED_JSON"])
rebound = json.loads(os.environ["REBOUND_JSON"])
try:
    import server
    result["ok"] = True
    result["root"] = str(server.ROOT)
    result["root_is_project"] = (server.ROOT / "index.html").exists() and (server.ROOT / "server.py").exists()
    result["persist_dir"] = str(server.PERSIST_CACHE_DIR)
    for mod, groups in spec.items():
        m = importlib.import_module(mod)
        for n in groups["reexported"] + groups["internal"]:
            result["module_names"][f"{mod}:{n}"] = type(getattr(m, n, None)).__name__
        for n in groups["reexported"]:
            obj = getattr(server, n, "<MISSING>")
            result["server_names"][n] = None if obj == "<MISSING>" else type(obj).__name__
        if groups.get("check_all_identity"):
            # 该模块导出的每个公开名字：只要 server 命名空间里也有，就必须是【同一个对象】。
            # server 没有的说明该名字没被再导出（server 自身不再引用），不算问题。
            for n, obj in vars(m).items():
                if n.startswith("_") or n == "annotations" or isinstance(obj, type(sys)):
                    continue
                if not hasattr(server, n):
                    result["identity"][f"{mod}:{n}"] = "未再导出"
                elif getattr(server, n) is not obj:
                    result["identity"][f"{mod}:{n}"] = "不是同一对象"
                else:
                    result["identity"][f"{mod}:{n}"] = "ok"
            # 重绑定名字不得泄漏进抽取模块
            for n in rebound:
                if hasattr(m, n):
                    result["rebound_leak"][f"{mod}:{n}"] = "泄漏"
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
    env["REBOUND_JSON"] = __import__("json").dumps(sorted(REBOUND_MUST_STAY))
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
        print("[verify] 已抽取模块检查:")
        for mod, groups in EXTRACTED.items():
            miss_mod = [n for n in groups["reexported"] + groups["internal"]
                        if payload["module_names"].get(f"{mod}:{n}") in (None, "NoneType")]
            miss_srv = [n for n in groups["reexported"] if payload["server_names"].get(n) is None]
            if miss_mod:
                failures.append(f"{mod} 内缺失: {miss_mod}")
            if miss_srv:
                failures.append(f"{mod} 的再导出名不在 server 命名空间: {miss_srv}")
            mark = "✓" if not miss_mod and not miss_srv else "✗"
            print(f"    {mark} {mod}: 再导出 {len(groups['reexported'])} 个 / 内部 {len(groups['internal'])} 个")
            if groups.get("check_all_identity"):
                # 三种结果，语义严格区分：
                #   ok          —— server 里也有该名字，且是【同一个对象】（锁/缓存的生死线）
                #   未再导出     —— server 自身不再引用它。这可能是「真·内部名字」（安全），
                #                 也可能是「外部测试/模块仍通过 server.NAME 访问」（回归！）。
                #                 无法静态区分，因此：在 `internal` 白名单里 → 安全跳过；
                #                 不在白名单 → 升级为警告，提示人工确认是否需再导出。
                #   不是同一对象 —— 出现「第二把锁 / 第二份缓存」，互斥静默失效，必须失败
                internal_allowlist = set(groups.get("internal", []))
                scoped = {k: v for k, v in payload.get("identity", {}).items() if k.startswith(mod + ":")}
                not_same = {k: v for k, v in scoped.items() if v == "不是同一对象"}
                not_reexported = {k: v for k, v in scoped.items() if v == "未再导出"}
                total = len(scoped)
                if not_same:
                    failures.append(f"{mod} 同一性检查失败 {len(not_same)} 项: {list(not_same.items())[:5]}")
                    print(f"      ✗ 对象同一性：{total} 项中 {len(not_same)} 项不是同一对象（可能出现第二把锁！）")
                    for k, v in list(not_same.items())[:8]:
                        print(f"          {k} -> {v}")
                else:
                    print(f"      ✓ 对象同一性：{total - len(not_reexported)} 项与 server 命名空间为同一对象")
                # 未再导出的名字：白名单内的安全，其余警告
                for k in sorted(not_reexported):
                    name = k.split(":", 1)[1]
                    if name in internal_allowlist:
                        print(f"          (内部) {name} 仅模块内使用，安全")
                    else:
                        print(f"          ⚠ 未再导出 {name}：若 tests/其他模块仍通过 server.{name} 访问会回归，"
                              f"建议在 server.py 再导出或列入 internal 白名单")

        # 重绑定名字泄漏检查（会导致静默行为改变）
        leak = payload.get("rebound_leak", {})
        if leak:
            failures.append(f"重绑定名字泄漏进抽取模块: {list(leak)[:10]}")
            print(f"[verify] ✗ 重绑定名字泄漏（其 `global X` 写不回去，值会永久停留在初始值）:")
            for k in list(leak)[:10]:
                print(f"      {k}")
        elif payload.get("ok"):
            print("[verify] ✓ 无重绑定名字泄漏进抽取模块")

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
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    moved = {n for g in EXTRACTED.values() for n in g["reexported"] + g["internal"]}
    # check_all_identity 的模块（如 app.core.state）没有逐个登记名字，
    # 其「搬走的名字」只能从子进程返回的同一性键里反推。不补这一步，
    # 这两百多个名字的「残留重复定义」检查就是空转。
    for key in payload.get("identity", {}):
        mod, _, name = key.partition(":")
        if EXTRACTED.get(mod, {}).get("check_all_identity") and name:
            moved.add(name)
    leftovers = sorted(moved & defined)
    if leftovers:
        print(f"[verify] ✗ server.py 仍有重复定义（{len(leftovers)} 个）: {leftovers[:20]}")
        failures.append(f"重复定义 {leftovers[:20]}")
    else:
        print(f"[verify] ✓ server.py 无残留重复定义（核对 {len(moved)} 个已搬走的名字）")


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
