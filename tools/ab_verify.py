#!/usr/bin/env python
"""同条件 A/B 对照：把「上一已验证提交的 server.py」与「当前工作区 server.py」
放到同一隔离运行时下各起一个临时实例，采集对外结构指纹后逐项比对。

为什么必须同条件
----------------
`golden-master.json` 是对着**线上热服务**（8765 端口、热库、热缓存）采的。
拿冷启动的临时实例去比它，必然大面积「回归」——那是环境差异，不是代码差异。
本工具让新旧两版跑在**同一空库、同一起点**上，只暴露代码差异。

判定语义（严格区分三态）
------------------------
  两边都是 200 且形状一致          → 通过
  两边都不是 200，且状态/错误类别相同 → 通过（冷启动本就取不到数据，属正常）
  一边 200 一边不是                → 先**交替重探**若干轮再定论（冷启动抖动很常见）
  两边都是 200 但形状有差异        → **回归**（对外契约被改变）

设计要点
--------
* 两个实例**并发运行**，各自独立 `XINGYUN_RUNTIME_DIR` + 随机空闲端口，互不干扰；
* 采集后对「一边通一边不通」的端点做**交替重探**，把冷启动抖动与真实回归分开；
* 结束时按**进程树**清理（Windows 用 taskkill /T），不留孤儿进程占 CPU；
* 绝不触碰线上 8765 服务。

用法
----
    python tools/ab_verify.py                          # 全量 40 端点，对 HEAD 比
    python tools/ab_verify.py --only health,market-hot,price-watch
    python tools/ab_verify.py --old-ref HEAD --timeout 40 --keep
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import refactor_baseline as rb  # noqa: E402  (复用端点表 / 形状采集 / diff 逻辑)

# 旧版入口必须落在**项目根目录**：server.py 里有 `Path(__file__).parent / "src"`
# 这类 __file__ 派生路径，放到子目录会让它静默指向错误位置（这是拆分中最隐蔽的坑之一，
# 本工具自身就先踩了一次）。文件名以点开头，避免被 lint / glob 误拾。
OLD_ENTRY = Path(".__ab_server_old.py")

# 环境级（而非代码级）失败：出现这些错误说明是超时/断连抖动，
# 不能当成「代码把端点弄坏了」。
ENV_LEVEL_ERRORS = frozenset({
    "TimeoutError", "URLError", "socket.timeout", "ConnectionResetError",
    "ConnectionAbortedError", "ConnectionRefusedError", "RemoteDisconnected",
    "IncompleteRead", "BadStatusLine", "HTTPException", "OSError",
})

# 「逐字节比对」的目标：静态页面与 SEO 生成物。
# 这些内容由磁盘上的 html/js/css 决定，两版文件完全相同 → 服务端注入的
# SEO 头、页脚、样式版本号也应当【逐字节一致】。任何差异都是真实回归。
STATIC_PATHS: list[tuple[str, str]] = [
    ("page-index", "/index.html"),
    ("page-newsflash", "/newsflash.html"),
    ("page-briefs", "/briefs.html"),
    ("page-listings", "/listings.html"),
    ("page-rss", "/rss.html"),
    ("page-xwatch", "/xwatch.html"),
    ("page-price-watch", "/price-watch.html"),
    ("page-dragon-wave", "/dragon-wave.html"),
    ("page-event-flow", "/event-flow.html"),
    ("page-strategy", "/strategy.html"),
    ("page-todo", "/todo.html"),
    ("page-profile", "/profile.html"),
    ("page-legal", "/legal.html"),
    ("page-newboards", "/newboards.html"),
    ("page-gainers", "/gainers.html"),
    ("page-turnover", "/turnover.html"),
    ("page-root", "/"),
    ("seo-robots", "/robots.txt"),
    ("seo-sitemap", "/sitemap.xml"),
]


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _no_proxy_opener():
    """回环请求必须绕开系统代理（Windows 上 urllib 还读注册表，NO_PROXY 未必生效）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=30)
    else:
        try:
            os.killpg(os.getpgid(pid), 15)
        except Exception:
            pass


class Instance:
    """一个临时 server 实例。"""

    def __init__(self, entry: Path, label: str, runtime: Path):
        self.entry = entry
        self.label = label
        self.port = _free_port()
        self.runtime = runtime
        self.proc: subprocess.Popen | None = None
        self.log_path = runtime.parent / f"{runtime.name}.server.log"
        self._fh = None

    # -- 生命周期 -----------------------------------------------------------
    def start(self) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["XINGYUN_RUNTIME_DIR"] = str(self.runtime)
        env["PORT"] = str(self.port)
        env["PYTHONIOENCODING"] = "utf-8"
        # 入口文件若不与同目录模块同级，Python 会把【入口所在目录】放进 sys.path[0]，
        # `import quiet_http_server` 之类会找不到。显式补 PYTHONPATH=项目根，
        # 使 sys.path 语义与「直接在根目录跑 server.py」完全一致。
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
        # 遵守项目铁律：外网抓取一律跟随系统代理，本工具不做任何代理干预。
        self._fh = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.entry), "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=self._fh, stderr=subprocess.STDOUT,
        )

    def wait_ready(self, timeout: float) -> bool:
        """轮询静态首页：serve_forever 之前连接会被拒，200 即代表初始化完成。"""
        opener = _no_proxy_opener()
        url = f"http://127.0.0.1:{self.port}/"
        deadline = time.time() + timeout
        last = "尚未连接"
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                print(f"[ab] ✗ {self.label} 进程已退出（码 {self.proc.returncode}）")
                return False
            try:
                with opener.open(url, timeout=3) as resp:
                    if resp.status == 200:
                        print(f"[ab] {self.label} 就绪（端口 {self.port}）")
                        return True
                    last = f"status={resp.status}"
            except Exception as exc:
                last = type(exc).__name__
            time.sleep(1.0)
        print(f"[ab] ✗ {self.label} 就绪超时（{timeout:.0f}s），最后状态: {last}")
        return False

    def stop(self) -> None:
        if self.proc:
            if self.proc.poll() is None:
                _kill_tree(self.proc.pid)
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            # 父进程退出不代表孙进程退出，再按树清一次（幂等）
            try:
                _kill_tree(self.proc.pid)
            except Exception:
                pass
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass

    def log_tail(self, n: int = 1800) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-n:]
        except Exception:
            return ""


# --------------------------------------------------------------------------- #
# 旧版源码准备
# --------------------------------------------------------------------------- #
def _load_old_source(ref: str) -> Path:
    spec = ref if ":" in ref else f"{ref}:server.py"
    out = PROJECT_ROOT / OLD_ENTRY
    proc = subprocess.run(["git", "show", spec], cwd=str(PROJECT_ROOT), capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"git show {spec} 失败：{proc.stderr.decode('utf-8', 'replace')[:400]}")
    out.write_bytes(proc.stdout)
    print(f"[ab] 旧版来源 {spec} → {OLD_ENTRY}（{len(proc.stdout.splitlines())} 行）")
    return OLD_ENTRY


# --------------------------------------------------------------------------- #
# 比对
# --------------------------------------------------------------------------- #
def _classify(entry: dict) -> str:
    if entry.get("error"):
        return str(entry["error"])
    status = entry.get("status")
    if isinstance(status, list):
        return "status=" + ",".join(str(s) for s in status)
    return f"status={status}"


# --------------------------------------------------------------------------- #
# 静态页面逐字节比对（gate 2）
# --------------------------------------------------------------------------- #
def _fetch_bytes(port: int, path: str, timeout: int) -> dict:
    """原样取回响应体（不解码、不计形状），用于逐字节比对。"""
    opener = _no_proxy_opener()
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with opener.open(url, timeout=timeout) as resp:
            return {"status": resp.status, "body": resp.read()}
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        return {"status": exc.code, "body": raw}
    except Exception as exc:
        return {"status": None, "body": b"", "error": type(exc).__name__}


def _first_diff(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def _compare_static(old_port: int, new_port: int, timeout: int) -> tuple[list[str], list[str]]:
    same: list[str] = []
    bad: list[str] = []
    for name, path in STATIC_PATHS:
        a = _fetch_bytes(old_port, path, timeout)
        b = _fetch_bytes(new_port, path, timeout)
        if a.get("error") or b.get("error"):
            bad.append(f"{name} {path}: 请求失败（旧={a.get('error') or a['status']} "
                       f"新={b.get('error') or b['status']}）")
            continue
        if a["status"] != b["status"]:
            bad.append(f"{name} {path}: 状态码 {a['status']} → {b['status']}")
            continue
        if a["body"] != b["body"]:
            pos = _first_diff(a["body"], b["body"])
            ctx_a = a["body"][max(0, pos - 70):pos + 70].decode("utf-8", "replace")
            ctx_b = b["body"][max(0, pos - 70):pos + 70].decode("utf-8", "replace")
            bad.append(
                f"{name} {path}: 内容不同（旧 {len(a['body'])}B / 新 {len(b['body'])}B，首差异 @{pos}）\n"
                f"        旧: ...{ctx_a}...\n        新: ...{ctx_b}..."
            )
            continue
        same.append(f"{name} {path}: {len(a['body'])}B 逐字节一致")
    return same, bad


def _is_env_level(entry: dict) -> bool:
    return bool(entry.get("error")) and str(entry["error"]) in ENV_LEVEL_ERRORS


def _disagreements(old: dict, new: dict) -> list[str]:
    out = []
    for name in old:
        if name not in new:
            continue
        if rb._is_ok(old[name]) != rb._is_ok(new[name]):
            out.append(name)
    return sorted(out)


def _reprobe(old: Instance, new: Instance, names: list[str], path_of: dict,
             rounds: int, timeout: int) -> dict[str, tuple[dict, dict]]:
    """对「一边通一边不通」的端点交替重探，直到两边状态类别一致或耗尽轮次。"""
    fixed: dict[str, tuple[dict, dict]] = {}
    for name in names:
        path = path_of[name]
        for attempt in range(1, rounds + 1):
            a = rb.fetch(f"http://127.0.0.1:{old.port}", path, timeout)
            b = rb.fetch(f"http://127.0.0.1:{new.port}", path, timeout)
            same_class = rb._is_ok(a) == rb._is_ok(b)
            print(f"[ab]   重探 {name} 第{attempt}轮: 旧={_classify(a)} 新={_classify(b)}"
                  f"{'  → 一致' if same_class else ''}")
            if same_class:
                fixed[name] = (a, b)
                break
        else:
            fixed[name] = (a, b)  # 保留最后一轮结果
    return fixed


def _compare(old: dict, new: dict) -> tuple[list[str], list[str], list[str], int, int]:
    regressions: list[str] = []
    warnings: list[str] = []
    passed: list[str] = []
    a_ok = b_ok = 0

    for name in sorted(old):
        if name not in new:
            regressions.append(f"{name}: 新版端点消失")
            continue
        a, b = old[name], new[name]
        ok_a, ok_b = rb._is_ok(a), rb._is_ok(b)
        a_ok += 1 if ok_a else 0
        b_ok += 1 if ok_b else 0

        if ok_a and ok_b:
            diffs = rb.diff_shapes(a.get("shape"), b.get("shape"))
            if diffs:
                regressions.append(f"{name}: 结构差异 {len(diffs)} 处 → {'; '.join(diffs[:6])}")
            else:
                passed.append(f"{name}: 结构一致（{a.get('elapsedMs')}ms → {b.get('elapsedMs')}ms）")
        elif not ok_a and not ok_b:
            ea, eb = _classify(a), _classify(b)
            if ea == eb:
                passed.append(f"{name}: 两边同状态 {ea}（冷启动无数据，正常）")
            elif _is_env_level(a) or _is_env_level(b):
                warnings.append(f"{name}: 重探后仍不一致 {ea} / {eb}（疑似抖动，请复核）")
            else:
                warnings.append(f"{name}: 错误类别变化 {ea} → {eb}")
        elif ok_b and not ok_a:
            if _is_env_level(a):
                warnings.append(f"{name}: 旧版网络级失败({_classify(a)}) → 新版 200（疑似抖动，非改善）")
            else:
                passed.append(f"{name}: 旧版失效({_classify(a)}) → 新版 200（改善）")
        else:
            if _is_env_level(b):
                warnings.append(f"{name}: 旧版 200 → 新版网络级失败({_classify(b)})（疑似抖动，请复核）")
            else:
                regressions.append(f"{name}: 旧版 200 → 新版失效({_classify(b)})")

    return regressions, warnings, passed, a_ok, b_ok


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="同条件 A/B 结构指纹对照")
    parser.add_argument("--old-ref", default="HEAD", help="旧版来源，默认 HEAD（上一已验证提交）")
    parser.add_argument("--entry", default="server.py", help="新版入口，默认工作区 server.py")
    parser.add_argument("--only", default="", help="仅比对指定端点（逗号分隔）")
    parser.add_argument("--samples", type=int, default=1, help="每端点采样次数（默认 1）")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=40, help="单请求超时秒数")
    parser.add_argument("--ready-timeout", type=int, default=240, help="等待实例就绪秒数")
    parser.add_argument("--reprobe-rounds", type=int, default=3, help="不一致端点重探轮次")
    parser.add_argument("--skip-static", action="store_true", help="跳过静态页面逐字节比对")
    parser.add_argument("--report", default="", help="把结果 JSON 写到指定路径")
    parser.add_argument("--keep", action="store_true", help="保留临时运行时目录与日志")
    args = parser.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    old_entry = _load_old_source(args.old_ref)

    work = Path(tempfile.mkdtemp(prefix="ab-verify-"))
    old_inst = Instance(old_entry, "旧版", work / "old-rt")
    new_inst = Instance(Path(args.entry), "新版", work / "new-rt")

    targets = [(n, p) for n, p in rb.GET_ENDPOINTS if not only or n in only]
    path_of = dict(targets)
    print(f"[ab] 隔离工作目录: {work}")
    print(f"[ab] 旧版端口 {old_inst.port} / 新版端口 {new_inst.port}")
    print(f"[ab] 端点 {len(targets)} 个 / 单请求超时 {args.timeout}s"
          f"{' / 子集 ' + ','.join(sorted(only)) if only else ''}")

    failures: list[str] = []
    old_data: dict = {}
    new_data: dict = {}
    static_result: dict[str, list[str]] = {"same": [], "bad": []}
    try:
        print("[ab] 并发启动新旧实例 ...")
        old_inst.start()
        new_inst.start()

        ready: dict[str, bool] = {}

        def _wait(inst: Instance) -> None:
            ready[inst.label] = inst.wait_ready(args.ready_timeout)

        t1 = threading.Thread(target=_wait, args=(old_inst,))
        t2 = threading.Thread(target=_wait, args=(new_inst,))
        t1.start(); t2.start(); t1.join(); t2.join()

        for inst in (old_inst, new_inst):
            if not ready.get(inst.label):
                failures.append(f"{inst.label}启动失败")
                tail = inst.log_tail()
                if tail:
                    print(f"[ab] ---- {inst.label} 日志尾部 ----")
                    print(tail)

        if not failures:
            print("[ab] 并发采集结构指纹 ...")
            collected: dict[str, dict] = {}

            def _collect(inst: Instance) -> None:
                collected[inst.label] = rb.collect(
                    f"http://127.0.0.1:{inst.port}", args.samples, args.workers, args.timeout, only)

            t1 = threading.Thread(target=_collect, args=(old_inst,))
            t2 = threading.Thread(target=_collect, args=(new_inst,))
            t1.start(); t2.start(); t1.join(); t2.join()

            old_data = collected.get("旧版", {})
            new_data = collected.get("新版", {})
            print(f"[ab] 旧版可用 {sum(1 for e in old_data.values() if rb._is_ok(e))}/{len(old_data)}"
                  f" / 新版可用 {sum(1 for e in new_data.values() if rb._is_ok(e))}/{len(new_data)}")

            dis = _disagreements(old_data, new_data)
            if dis:
                print(f"[ab] 有 {len(dis)} 个端点状态不一致，交替重探：{','.join(dis)}")
                fixed = _reprobe(old_inst, new_inst, dis, path_of, args.reprobe_rounds, args.timeout)
                for name, (a, b) in fixed.items():
                    # 重探结果更可信，覆盖首采结果
                    old_data[name] = {**old_data[name], **a}
                    new_data[name] = {**new_data[name], **b}

            if not args.skip_static:
                print("[ab] 逐字节比对静态页面与 SEO 生成物 ...")
                static_same, static_bad = _compare_static(old_inst.port, new_inst.port, args.timeout)
                print()
                print("[ab] ---- 静态页面（gate 2）----")
                for line in static_same:
                    print(f"  ✓ {line}")
                for line in static_bad:
                    print(f"  ✗ {line}")
                static_result = {"same": static_same, "bad": static_bad}
    finally:
        print("[ab] 清理临时实例 ...")
        old_inst.stop()
        new_inst.stop()

    if failures:
        print()
        for f in failures:
            print(f"[ab] ✗ {f}")
        print("[ab] 结果: 无法完成对照")
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        return 2

    regressions, warnings, passed, a_ok, b_ok = _compare(old_data, new_data)
    # 静态页面差异属于最硬的回归证据（页面被改）
    for line in static_result["bad"]:
        regressions.append(f"[静态] {line}")

    print()
    print("[ab] ---- API 端点结果（gate 3）----")
    for line in passed:
        print(f"  ✓ {line}")
    for line in warnings:
        print(f"  ⚠ {line}")
    for line in regressions:
        print(f"  ✗ {line}")

    summary = {
        "oldRef": args.old_ref,
        "endpoints": len(old_data),
        "oldAvailable": f"{a_ok}/{len(old_data)}",
        "newAvailable": f"{b_ok}/{len(new_data)}",
        "passed": len(passed), "warnings": len(warnings), "regressions": len(regressions),
        "regressionDetail": regressions,
        "warningDetail": warnings,
        "staticSame": len(static_result["same"]),
        "staticBad": static_result["bad"],
        "oldErrors": {k: _classify(v) for k, v in old_data.items() if not rb._is_ok(v)},
        "newErrors": {k: _classify(v) for k, v in new_data.items() if not rb._is_ok(v)},
    }
    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ab] 报告已写入 {rp}")

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"[ab] 临时目录保留于 {work}")

    print()
    print(f"[ab] 可用性: 旧 {summary['oldAvailable']} → 新 {summary['newAvailable']}")
    print(f"[ab] 静态页面: {summary['staticSame']} 项逐字节一致"
          f"{' / ' + str(len(static_result['bad'])) + ' 项不一致' if static_result['bad'] else ''}")
    print(f"[ab] API 通过 {len(passed)} / 警告 {len(warnings)} / 回归 {len(regressions)}")
    if regressions:
        print("[ab] 结果: 存在回归 ✗")
        return 1
    print("[ab] 结果: 通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
