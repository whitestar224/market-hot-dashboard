#!/usr/bin/env python
"""基于 AST 的单体依赖分析器：为 server.py 计算【安全可拆分边界】。

背景
----
server.py 是 55k 行单体，含 1372 个顶层函数、119 个模块级锁、120 个
模块级可变状态、68 处 `global` 重绑定。

拆分它的唯一致命陷阱：
    把函数 A 搬到新模块后，A 里的 `global X` 与裸引用 `X` 会解析到
    【新模块的命名空间】。若 X 仍留在 server.py，A 就会读/写到另一个
    变量上 —— 行为静默改变，且极难发现。

因此正确的拆分单位不是"函数"，而是【依赖闭包子图】：
一组函数 + 它们共同引用的全部模块级名字，整组一起搬走，才能保证
引用解析结果不变。

本工具做的事
------------
1. 解析 AST，收集全部模块级名字（函数/类/常量/锁/缓存）。
2. 对每个顶层函数，算出它引用的模块级名字集合（区分读 / 写），
   以及它调用的其他顶层函数集合。
3. 按名字前缀做初始聚类（域划分），再按依赖关系做传递合并，
   直到每个簇的"外部名字引用"最小化。
4. 输出 JSON 报告：可直接搬走的干净簇、需要特别处理的纠缠簇、
   以及被最多函数共享的"超级全局变量"（这些是拆分的关键风险点）。

用法
----
    python tools/split_analyzer.py --source server.py --out tests/fixtures/refactor-baseline/split-plan.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 作用域分析

BUILTINS = set(dir(__builtins__)) | {
    "self", "cls", "True", "False", "None", "__name__", "__file__", "__doc__",
}


class ScopeCollector(ast.NodeVisitor):
    """收集一个函数体内被"绑定"的局部名字（即非模块级引用的名字）。"""

    def __init__(self) -> None:
        self.bound: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bound.add(node.id)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self.bound.add(node.arg)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)
        self.bound.update(a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs)
        if node.args.vararg:
            self.bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.bound.add(node.args.kwarg.arg)
        for child in node.body:
            self.visit(child)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.bound.update(a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs)
        if node.args.vararg:
            self.bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.bound.add(node.args.kwarg.arg)
        for child in node.body if isinstance(node.body, list) else [node.body]:
            self.visit(child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add((alias.asname or alias.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name)

    def visit_Global(self, node: ast.Global) -> None:
        # global 声明的名字是【写模块级】，不是局部绑定
        for n in node.names:
            self.bound.discard(n)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.generic_visit(node)


def referenced_names(func: ast.AST) -> tuple[set[str], set[str]]:
    """返回 (所有被引用的名字, 被 global 声明为可写的名字)。"""
    collector = ScopeCollector()
    for child in func.body:
        collector.visit(child)
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for a in func.args.args + func.args.kwonlyargs + func.args.posonlyargs:
            collector.bound.add(a.arg)

    loads: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.add(node.id)
    writes: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Global):
            writes.update(node.names)

    free = {n for n in loads if n not in collector.bound}
    return free, writes


# ---------------------------------------------------------------- 模块级符号


def module_level_names(tree: ast.Module) -> dict[str, str]:
    """收集模块级名字 → 种类。"""
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names[node.name] = "function"
        elif isinstance(node, ast.ClassDef):
            names[node.name] = "class"
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names[t.id] = "variable"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = "variable"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    names["*"] = "star_import"
                    continue
                names[(alias.asname or alias.name).split(".")[0]] = "import"
        elif isinstance(node, ast.Try):
            for sub in node.body:
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            names[t.id] = "variable"
    return names


PREFIX_RULES: list[tuple[str, str]] = [
    (r"^(health|service_liveness)", "ops_health"),
    (r"^(robots|sitemap|seo|inject_seo|normalized_page)", "seo"),
    (r"^(dragon_wave|dragon)", "dragon_wave"),
    (r"^(price_watch|price_watch_)", "price_watch"),
    (r"^(price_structure|new_coin_low|prior_high)", "price_structure"),
    (r"^(chain_ecosystem|onchain|chain_store|gmgn)", "onchain"),
    (r"^(newsflash|news_trade|rss)", "news"),
    (r"^(wechat|qq_onebot)", "wechat"),
    (r"^(personal_x|x_kol|x_tweet|xwatch)", "social_x"),
    (r"^(smart_money|global_wallet|global_hotspot)", "smart_money"),
    (r"^(binance)", "binance"),
    (r"^(alert|desktop_alert|notification)", "alert"),
    (r"^(market_|gainers|turnover|rotation|ranking)", "market"),
    (r"^(strategy|exchange_ai|aster)", "strategy"),
    (r"^(auth|session_|todo_)", "auth_config"),
    (r"^(cache|write_json|read_json|read_text|write_text)", "persistence"),
    (r"^(http_|network_|proxy|_http)", "network"),
    (r"^(log|_log|debug)", "logging"),
]


def domain_of(name: str) -> str:
    for pattern, domain in PREFIX_RULES:
        if re.match(pattern, name, re.IGNORECASE):
            return domain
    return "misc"


def analyze(source: Path) -> dict[str, Any]:
    text = source.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(source))

    mod_names = module_level_names(tree)
    funcs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node

    func_free: dict[str, set[str]] = {}
    func_free_globals: dict[str, set[str]] = {}   # 引用到的模块级名字
    func_writes: dict[str, set[str]] = {}
    func_calls: dict[str, set[str]] = {}

    for name, node in funcs.items():
        free, writes = referenced_names(node)
        func_free[name] = free
        func_free_globals[name] = {n for n in free if n in mod_names}
        func_writes[name] = writes
        func_calls[name] = {n for n in free if n in funcs}

    # 全局变量被哪些函数引用
    global_users: dict[str, set[str]] = defaultdict(set)
    for fname, refs in func_free_globals.items():
        for g in refs:
            if mod_names.get(g) in {"variable", "import"}:
                global_users[g].add(fname)

    # 调用图连通分量（仅函数 → 函数）
    def closure_of(seed: set[str]) -> set[str]:
        seen = set(seed)
        stack = list(seed)
        while stack:
            cur = stack.pop()
            for callee in func_calls.get(cur, ()):  # 只沿函数调用闭包
                if callee not in seen:
                    seen.add(callee)
                    stack.append(callee)
        return seen

    # 初始聚类：按域
    clusters: dict[str, set[str]] = defaultdict(set)
    for fname in funcs:
        clusters[domain_of(fname)].add(fname)

    # 统计每簇引用的簇外函数（依赖）
    def external_funcs(members: set[str]) -> set[str]:
        out: set[str] = set()
        for m in members:
            out |= {c for c in func_calls.get(m, ()) if c not in members}
        return out

    report_clusters = []
    for domain, members in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
        ext_funcs = external_funcs(members)
        ext_domains = sorted({domain_of(f) for f in ext_funcs})
        globals_used: set[str] = set()
        for m in members:
            globals_used |= {g for g in func_free_globals.get(m, ()) if mod_names.get(g) in {"variable", "import"}}
        writers = sorted({w for m in members for w in func_writes.get(m, ())})
        report_clusters.append({
            "domain": domain,
            "functionCount": len(members),
            "functions": sorted(members)[:40],
            "externalFunctionCount": len(ext_funcs),
            "externalFunctions": sorted(ext_funcs)[:40],
            "externalDomains": ext_domains,
            "globalsUsedCount": len(globals_used),
            "globalsUsed": sorted(globals_used)[:60],
            "globalWriters": writers,
            "selfContained": len(ext_funcs) == 0 and not writers,
        })

    hot_globals = sorted(
        ({"name": g, "userCount": len(u), "kind": mod_names.get(g), "sampleUsers": sorted(u)[:8]}
         for g, u in global_users.items()),
        key=lambda d: -d["userCount"],
    )

    # ---- 关键：区分「可安全 from X import NAME 的单例」与「会被重绑定的名字」
    # 重绑定 = 任何函数里出现 `global NAME`（写模块级作用域）。
    # 这类名字搬到别的模块后必须改成 `state.NAME = ...` 属性访问，否则写丢。
    rebound: dict[str, set[str]] = defaultdict(set)
    for fname, writes in func_writes.items():
        for w in writes:
            if w in mod_names:
                rebound[w].add(fname)
    rebound_list = sorted(
        ({"name": n, "writerCount": len(f), "sampleWriters": sorted(f)[:10],
          "referencedBy": len(global_users.get(n, ()))}
         for n, f in rebound.items()),
        key=lambda d: (-d["writerCount"], -d["referencedBy"]),
    )

    # 模块级赋值次数 > 1 的名字（可能被重绑定/增量更新）
    assign_counts: dict[str, int] = defaultdict(int)
    for node in tree.body:
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name):
                assign_counts[t.id] += 1
    multi_assigned = sorted(
        ({"name": n, "assignCount": c} for n, c in assign_counts.items() if c > 1),
        key=lambda d: -d["assignCount"],
    )

    return {
        "source": str(source),
        "totalLines": text.count("\n") + 1,
        "topLevelFunctions": len(funcs),
        "topLevelNames": len(mod_names),
        "moduleLevelVariables": sum(1 for v in mod_names.values() if v == "variable"),
        "globalDeclarations": sum(1 for n in tree.body if isinstance(n, ast.Global))
                              + sum(len(list(ast.walk(f))) for f in funcs.values()),
        "clusters": sorted(report_clusters, key=lambda c: -c["functionCount"]),
        "hotGlobals": hot_globals[:60],
        "reboundNames": rebound_list,
        "multiAssignedNames": multi_assigned,
        "unclassifiedFunctions": sorted(clusters.get("misc", set()))[:80],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="单体依赖分析与拆分边界计算")
    parser.add_argument("--source", default="server.py")
    parser.add_argument("--out", default="tests/fixtures/refactor-baseline/split-plan.json")
    args = parser.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(f"源文件不存在: {src}", file=sys.stderr)
        return 2

    report = analyze(src)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"源文件      : {report['source']}  ({report['totalLines']} 行)")
    print(f"顶层函数    : {report['topLevelFunctions']}")
    print(f"模块级变量  : {report['moduleLevelVariables']}")
    print(f"报告已写入  : {out}")
    print()
    print(f"{'域':<18}{'函数数':>7}{'外部依赖':>9}{'引用全局':>9}{'可独立':>8}")
    print("-" * 52)
    for c in report["clusters"]:
        print(f"{c['domain']:<18}{c['functionCount']:>7}{c['externalFunctionCount']:>9}"
              f"{c['globalsUsedCount']:>9}{'是' if c['selfContained'] else '否':>8}")
    print()
    print("共享最广的模块级变量（拆分关键风险点）:")
    for g in report["hotGlobals"][:15]:
        print(f"  {g['userCount']:>4} 个函数引用  {g['name']}")
    print()
    print(f"会被重绑定的名字（必须改为 state.NAME 属性访问）: {len(report['reboundNames'])} 个")
    for r in report["reboundNames"][:20]:
        print(f"  {r['writerCount']:>3} 处写 / {r['referencedBy']:>4} 个函数引用  {r['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
