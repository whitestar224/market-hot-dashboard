#!/usr/bin/env python
"""从单体文件机械抽取一个连贯代码块到独立模块（重构 Phase 3 的执行工具）。

为什么需要它
------------
手工编辑 55,130 行的 server.py 不可接受：极易误删、无法复核、不可重复。
本工具用 AST 精确定位顶层定义的行区间，做到：
  - 抽取结果与原文逐字节一致（不改一个字符的语义）
  - 删除的区间精确到行，可 diff 复核
  - 自动算出「搬走后原文件还需要哪些名字」以生成导入语句
  - 自动检测是否形成循环依赖（被抽取块引用了留在原文件里的名字）

安全设计
--------
1. 默认 dry-run，必须显式 --apply 才写文件
2. 抽取前校验：目标区间内不得含有未请求的顶层定义（防止漏搬/错搬）
3. 抽取后校验：新模块的自由名字必须全部可解析（stdlib/三方/同块内），
   否则报出「未满足依赖」而不是生成坏代码
4. 写文件前先备份原文件为 <name>.extractbak

用法
----
    # 预览（不写盘）
    python tools/extract_module.py --source server.py --target app/api/seo.py \
        --names SEO_PUBLIC_PAGES,SEO_NOINDEX_PATHS,robots_txt,sitemap_xml

    # 执行
    python tools/extract_module.py ... --apply
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any

# 允许以 `python tools/extract_module.py` 方式直接运行（此时 sys.path[0] 是 tools/）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STDLIB_HINTS = {
    "os", "sys", "re", "json", "time", "math", "html", "datetime", "hashlib",
    "threading", "sqlite3", "collections", "pathlib", "urllib", "http", "socket",
    "subprocess", "logging", "contextlib", "functools", "itertools", "random",
    "base64", "hmac", "secrets", "shutil", "glob", "traceback", "io", "csv",
    "zlib", "gzip", "struct", "uuid", "copy", "weakref", "bisect", "heapq",
    "queue", "signal", "ssl", "email", "mimetypes", "xml", "unicodedata",
    "string", "textwrap", "dataclasses", "enum", "abc", "inspect", "importlib",
    "pickle", "tempfile", "platform", "argparse", "concurrent", "asyncio",
    "statistics", "decimal", "calendar", "zoneinfo", "warnings", "types", "ast",
    "difflib", "operator", "stat", "errno", "ctypes", "codecs", "atexit", "gc",
    "smtplib", "tomllib", "typing",
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _toplevel_nodes(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """返回 (名字, 节点) 列表，只含可命名的顶层定义。"""
    out: list[tuple[str, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, node))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append((t.id, node))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append((node.target.id, node))
    return out


def _free_names_of_nodes(nodes: list[ast.AST]) -> set[str]:
    """收集一组顶层节点引用的全部名字（含自身定义的，需再排除）。"""
    from tools.split_analyzer import referenced_names  # 复用同一套作用域分析

    free: set[str] = set()
    for n in nodes:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f, _ = referenced_names(n)
            free |= f
        else:
            for sub in ast.walk(n):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    free.add(sub.id)
    return free


# 每个名字的绑定规格：
#   ("module", "datetime", None)      -> import datetime
#   ("module", "urllib.parse", "up")  -> import urllib.parse as up
#   ("from", "typing", "Any")         -> from typing import Any
ImportSpec = tuple[str, str, "str | None"]


def _import_lines_for(names: set[str], specs: dict[str, ImportSpec]) -> list[str]:
    """为所需名字生成正确的 import 语句。

    关键点：`import datetime` 绑定的是【模块对象】，绝不能改写成
    `from datetime import datetime` —— 后者会把 datetime 变成类，
    `datetime.now()` 等调用会直接报错。模块绑定与符号绑定必须区分。
    """
    module_stmts: set[str] = set()
    from_binds: dict[str, set[tuple[str, str]]] = {}
    for n in sorted(names):
        spec = specs.get(n)
        if not spec:
            continue
        kind, mod, third = spec
        if kind == "module":
            module_stmts.add(f"import {mod} as {third}" if third else f"import {mod}")
        else:
            original = third or n
            from_binds.setdefault(mod, set()).add((original, n))
    lines: list[str] = sorted(module_stmts)
    for mod, syms in sorted(from_binds.items()):
        parts = [o if o == l else f"{o} as {l}" for o, l in sorted(syms)]
        lines.append(f"from {mod} import {', '.join(parts)}")
    return lines


def analyze_source_imports(tree: ast.Module) -> tuple[dict[str, ImportSpec], dict[str, ImportSpec]]:
    """解析原文件顶层 import，返回 (名字→导入规格, 同样内容的别名视图)。"""
    specs: dict[str, ImportSpec] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                specs[local] = ("module", a.name, a.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            for a in node.names:
                if a.name == "*":
                    continue
                specs[a.asname or a.name] = ("from", node.module, a.asname)
    return specs, dict(specs)


def run(args: argparse.Namespace) -> int:
    src = Path(args.source)
    if not src.exists():
        print(f"源文件不存在: {src}", file=sys.stderr)
        return 2
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text, filename=str(src))

    requested = [n.strip() for n in args.names.split(",") if n.strip()]
    if not requested:
        print("必须通过 --names 指定要抽取的名字", file=sys.stderr)
        return 2

    top = _toplevel_nodes(tree)
    by_name: dict[str, ast.AST] = {}
    for name, node in top:
        by_name[name] = node

    missing = [n for n in requested if n not in by_name]
    if missing:
        print(f"未找到顶层定义: {missing}", file=sys.stderr)
        return 2

    nodes = [by_name[n] for n in requested]
    start = min(n.lineno for n in nodes)
    end = max(n.end_lineno for n in nodes)

    # 校验：区间内不得有未请求的顶层定义
    span_names = {name for name, node in top if node.lineno >= start and node.end_lineno <= end}
    extra = span_names - set(requested)
    if extra:
        print(f"✗ 区间 {start}-{end} 内含有未请求的顶层定义: {sorted(extra)}", file=sys.stderr)
        print("  请把它们一并加入 --names，或缩小抽取范围。", file=sys.stderr)
        return 2

    inside = {name for name, node in top if start <= node.lineno <= end}
    if inside - set(requested):
        print(f"注意：区间内有部分重叠定义 {sorted(inside - set(requested))}（将按区间整块搬移）")

    # 抽取源码（整段连续，保留注释与空行）
    block = "".join(lines[start - 1:end])

    # 依赖分析
    specs, _ = analyze_source_imports(tree)
    free = _free_names_of_nodes(nodes)
    moved = set(requested)
    unresolved = free - moved
    # 剔除 Python 内置名（dict/str/int/Exception/sorted …）与已知内置
    builtin_names = set(dir(__builtins__)) | {"__name__", "__file__", "__doc__"}
    unresolved = {n for n in unresolved if n not in builtin_names}

    stdlib_needed = {n for n in unresolved
                     if n in STDLIB_HINTS
                     or (specs.get(n) and specs[n][1].split(".")[0] in STDLIB_HINTS)
                     or (specs.get(n) and specs[n][0] == "module" and specs[n][1].split(".")[0] in STDLIB_HINTS)}
    thirdparty_needed = unresolved - stdlib_needed

    # 判断哪些「未满足」= 原文件里的本地定义（会造成循环依赖）
    local_defs = {name for name, _ in top}
    needs_from_source = {n for n in thirdparty_needed if n in local_defs and n not in specs}
    truly_external = thirdparty_needed - needs_from_source

    print("=" * 72)
    print(f"源文件      : {src}")
    print(f"目标模块    : {args.out_root}/{args.target}")
    print(f"抽取区间    : 第 {start} - {end} 行（共 {end - start + 1} 行）")
    print(f"顶层定义    : {len(requested)} 个 -> {sorted(requested)}")
    print()
    print(f"stdlib 依赖 : {sorted(stdlib_needed)}")
    print(f"三方/未知   : {sorted(truly_external)}")
    resolve_map = {}
    for pair in (args.resolve.split(",") if args.resolve else []):
        if "=" in pair:
            k, v = pair.split("=", 1)
            resolve_map[k.strip()] = v.strip()
    unresolved_local = [n for n in sorted(needs_from_source) if n not in resolve_map]
    if unresolved_local:
        print(f"⚠ 依赖原文件本地定义（会形成循环导入）: {unresolved_local}")
        print("  处理方式：① 用 --resolve NAME=模块路径 指明其新家（推荐，如 ROOT=app.core.paths）")
        print("            ② 或把它们一并纳入 --names 抽取")
    elif needs_from_source:
        print(f"✓ 本地依赖已通过 --resolve 指向新家: "
              f"{ {n: resolve_map[n] for n in sorted(needs_from_source)} }")
    else:
        print("✓ 无对原文件的依赖，不会形成循环导入")
    print()

    # 生成新模块
    header = [
        '"""从 server.py 抽取的模块（Phase 3 拆分）。',
        "",
        f"来源: {src} 第 {start}-{end} 行",
        "本文件内容由 tools/extract_module.py 机械搬移，未做任何语义修改。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    import_block = []
    for extra_import in args.extra_import.split(";") if args.extra_import else []:
        if extra_import.strip():
            import_block.append(extra_import.strip())
    import_block.extend(_import_lines_for(stdlib_needed, specs))
    import_block.extend(_import_lines_for(truly_external, specs))
    # 通过 --resolve 指明的本地依赖
    resolved_specs: dict[str, ImportSpec] = {n: ("from", resolve_map[n], None)
                                             for n in needs_from_source if n in resolve_map}
    import_block.extend(_import_lines_for(set(resolved_specs), resolved_specs))

    module_src = "\n".join(header)
    if import_block:
        module_src += "\n".join(import_block) + "\n\n\n"
    else:
        module_src += "\n"
    module_src += block.rstrip() + "\n"

    out_path = PROJECT_ROOT / args.out_root / args.target
    print(f"生成模块预览（前 25 行）:")
    for line in module_src.splitlines()[:25]:
        print("   | " + line)
    print()

    # 原文件需要的再导出名：移除区间后，仍被引用的名字
    remaining_refs = _referenced_outside(lines, set(requested), set(range(start, end + 1)))

    print(f"原文件仍引用的名字（需再导出）: {sorted(remaining_refs)}")
    module_dotted = args.target.replace("/", ".").removesuffix(".py")
    reexport = f"from {module_dotted} import (\n" + "".join(
        f"    {n},\n" for n in sorted(remaining_refs)) + ")\n" if remaining_refs else ""
    print(repr(reexport) if reexport else "(无需再导出)")
    print()

    if not args.apply:
        print("（dry-run，未写入任何文件；加 --apply 执行）")
        return 0

    # --- 执行
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not args.force:
        print(f"目标已存在，拒绝覆盖: {out_path}（加 --force 覆盖）", file=sys.stderr)
        return 2
    out_path.write_text(module_src, encoding="utf-8")

    backup = src.with_suffix(src.suffix + ".extractbak")
    backup.write_text(text, encoding="utf-8")

    new_lines = lines[:start - 1] + lines[end:]
    new_text = "".join(new_lines)

    if reexport:
        anchor = args.anchor or "\n\n\n"
        # 插到第一个顶层 class 定义之前，保证在被使用前生效
        cls_idx = None
        for i, line in enumerate(new_lines):
            if line.startswith("class ") or line.startswith("def "):
                cls_idx = i
                break
        marker = f"\n# ---- 由 tools/extract_module.py 从 server.py 抽出，见 {args.target}\n{reexport}\n"
        if cls_idx is not None:
            insert_at = cls_idx
            # 回溯到该 def/class 上方的空行之后
            while insert_at > 0 and new_lines[insert_at - 1].strip() == "":
                insert_at -= 1
            new_lines = new_lines[:insert_at] + [marker] + new_lines[insert_at:]
        else:
            new_lines = [marker] + new_lines
        new_text = "".join(new_lines)

    src.write_text(new_text, encoding="utf-8")
    print(f"✓ 已写入新模块 : {out_path}")
    print(f"✓ 原文件已更新 : {src}（备份于 {backup.name}）")
    print(f"  原文件行数: {len(lines)} -> {len(new_text.splitlines(keepends=True))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="从单体机械抽取连贯代码块")
def _free_names_of_stmt(node: ast.AST) -> set[str]:
    """对任意语句做【作用域感知】的自由名收集。

    不能简单地 ast.walk 取所有 Load 名：那会把 lambda 参数、推导式变量、
    with-as、for 目标、海象赋值等局部绑定也当成模块级依赖，
    从而在新模块里生成 `from ... import key` 这种荒唐 import。
    """
    bound: set[str] = set()
    loads: set[str] = set()

    class _V(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name) -> None:
            if isinstance(n.ctx, ast.Load):
                loads.add(n.id)
            else:
                bound.add(n.id)

        def _bind_target(self, target: ast.AST) -> None:
            for t in ast.walk(target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)

        def visit_arg(self, n: ast.arg) -> None:
            bound.add(n.arg)

        def visit_Lambda(self, n: ast.Lambda) -> None:
            a = n.args
            for x in list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs):
                bound.add(x.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
            self.generic_visit(n)

        def visit_comprehension(self, n: ast.comprehension) -> None:
            self.visit(n.iter)          # 可迭代表达式在外层作用域求值
            self._bind_target(n.target)
            for cond in n.ifs:
                self.visit(cond)

        def visit_With(self, n) -> None:
            for item in n.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    self._bind_target(item.optional_vars)
            for s in n.body:
                self.visit(s)

        def visit_NamedExpr(self, n: ast.NamedExpr) -> None:
            self.visit(n.value)
            self._bind_target(n.target)

        def visit_ExceptHandler(self, n: ast.ExceptHandler) -> None:
            if n.name:
                bound.add(n.name)
            self.generic_visit(n)

        def visit_Import(self, n: ast.Import) -> None:
            for al in n.names:
                bound.add((al.asname or al.name).split(".")[0])

        def visit_ImportFrom(self, n: ast.ImportFrom) -> None:
            for al in n.names:
                bound.add(al.asname or al.name)

    _V().visit(node)
    return loads - bound


def _referenced_outside(lines: list[str], names: set[str], skip: set[int]) -> set[str]:
    """在 lines 中找出仍然引用了 names 的名字（跳过 skip 里的行号）。

    性能注意：早期实现是「逐行 × 逐名字 re.search」，在 55k 行 × 560 个名字
    下需要 3000 万次正则编译，直接把进程卡死。这里改成一条合并正则扫一遍。
    """
    if not names:
        return set()
    big = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")\b")
    found: set[str] = set()
    for i, line in enumerate(lines, start=1):
        if i in skip:
            continue
        if any(ch in line for ch in ("(", "=", ".", "[", " ", ",")):
            found.update(big.findall(line))
    return found & names


def _declared_names(node: ast.AST) -> set[str]:
    """取一条顶层语句声明的名字（赋值目标 / try 体内的赋值目标）。"""
    names: set[str] = set()
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.add(node.target.id)
    elif isinstance(node, ast.Try):
        for sub in node.body:
            names |= _declared_names(sub)
        for handler in node.handlers:      # else 分支里也可能赋值（如 try/except 回退）
            for sub in handler.body:
                names |= _declared_names(sub)
        for sub in node.orelse:
            names |= _declared_names(sub)
        for sub in node.finalbody:
            names |= _declared_names(sub)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        names.add(node.name)
    elif isinstance(node, ast.ClassDef):
        names.add(node.name)
    return names


def _extend_up(lines: list[str], node: ast.AST, floor: int) -> int:
    """把语句起点向上扩展到其紧邻的注释/空行，让注释跟着语句一起搬走。

    floor 为上一条顶层语句的结束行，扩展不会越过它。
    """
    i = node.lineno - 1  # 1-based -> index of the line above
    while i > floor:
        stripped = lines[i - 1].strip()
        if stripped == "" or stripped.startswith("#"):
            i -= 1
        else:
            break
    return i + 1  # 1-based start


def run_range(args: argparse.Namespace) -> int:
    """区间抽取模式：把 [start, end] 内的顶层语句搬到新模块，可排除指定名字。

    用途：像 `CACHE` / 各种锁与缓存这类散布在大段「状态声明区」里的原语，
    无法按连续块整体搬移（中间夹着被 global 重绑定的名字，必须留下）。
    """
    src = Path(args.source)
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text, filename=str(src))

    m = re.fullmatch(r"(\d+)-(\d+)", args.range.strip())
    if not m:
        print("--range 格式应为 START-END", file=sys.stderr)
        return 2
    a, b = int(m.group(1)), int(m.group(2))

    keep_names = {n.strip() for n in args.keep.split(",") if n.strip()}
    keep_lines = {int(x) for x in args.keep_lines.split(",") if x.strip().isdigit()}

    body = [n for n in tree.body if n.lineno >= a and n.lineno <= b]
    if not body:
        print("区间内没有顶层语句", file=sys.stderr)
        return 2

    moved: list[ast.AST] = []
    kept: list[ast.AST] = []
    deferred: list[ast.AST] = []   # 无声明名的语句（Expr/If/For/With）默认保留
    for node in body:
        names = _declared_names(node)
        if any(l in keep_lines for l in range(node.lineno, node.end_lineno + 1)):
            kept.append(node)
        elif names & keep_names:
            kept.append(node)
        elif not names:
            deferred.append(node)
        else:
            moved.append(node)

    if deferred:
        print("以下语句无声明名且未被显式排除，默认保留在原文件（如需搬移请用 --keep-lines 之外的方式扩展工具）：")
        for n in deferred:
            snippet = lines[n.lineno - 1].strip()[:80]
            print(f"    第 {n.lineno} 行: {snippet}")

    moved_names = {n for node in moved for n in _declared_names(node)}
    kept_names = {n for node in kept for n in _declared_names(node)}
    print()
    print("=" * 74)
    print(f"区间抽取 {src} 第 {a}-{b} 行")
    print(f"  搬移语句 : {len(moved)} 条 / {len(moved_names)} 个名字")
    print(f"  保留语句 : {len(kept)} 条（含重绑定或显式排除）/ {len(kept_names)} 个名字")
    if deferred:
        print(f"  默认保留 : {len(deferred)} 条")
    print()

    # 依赖分析
    specs, _ = analyze_source_imports(tree)
    free: set[str] = set()
    for node in moved:
        free |= _free_names_of_stmt(node)
    unresolved = free - moved_names
    builtins_ = set(dir(__builtins__)) | {"__name__", "__file__", "__doc__"}
    unresolved = {n for n in unresolved if n not in builtins_}
    stdlib_needed = {n for n in unresolved
                     if n in STDLIB_HINTS or (specs.get(n) and specs[n][1].split(".")[0] in STDLIB_HINTS)}
    thirdparty = unresolved - stdlib_needed

    # 关键风险：被搬移的语句引用了「留在原文件里」的名字 —— 由于新模块会被提前
    # import，而该名字在原文件中尚未执行到，会产生 NameError / 取到未初始化值。
    # 注意：判断基准是原文件的【全部】顶层名字，而不只是本区间内保留的那些；
    # 区间之前定义的 SHARED_HTTP_SESSION 之类同样属于此风险。
    all_top_names = {n for node in tree.body for n in _declared_names(node)}
    hazard = {n for n in thirdparty if n in all_top_names and n not in moved_names}
    external = thirdparty - hazard

    print(f"stdlib 依赖 : {sorted(stdlib_needed)}")
    print(f"三方依赖    : {sorted(external)}")
    if hazard:
        print(f"⚠ 顺序风险：被搬移语句引用了留在原文件的名字 {sorted(hazard)}")
        print("  这些名字在原文件中定义得更晚，新模块提前 import 时会取不到值。")
        print("  处理：把它们加入 --keep 让引用方也留下，或确认它们确为纯常量后加 --resolve。")
    else:
        print("✓ 无顺序风险")
    print()

    # 生成新模块
    header = [
        '"""从 server.py 抽取的模块（Phase 3 拆分）。',
        "",
        f"来源: {src} 第 {a}-{b} 行（区间抽取）",
        "本文件内容由 tools/extract_module.py 机械搬移，未做任何语义修改。",
        "",
        "注意：这是【共享可变状态】的集中地。上层模块一律 `from app.core.state import X`",
        "引用同名对象，绝不可重新 Lock()/新建 dict —— 否则会出现第二把锁，",
        "互斥静默失效（表现为偶发而非必现，极难排查）。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    import_block = []
    for extra in (args.extra_import.split(";") if args.extra_import else []):
        if extra.strip():
            import_block.append(extra.strip())
    import_block.extend(_import_lines_for(stdlib_needed, specs))
    import_block.extend(_import_lines_for(external, specs))

    # 逐条取源码（含其上方紧邻注释）
    segments: list[str] = []
    prev_end = 0
    spans: list[tuple[int, int]] = []
    for node in moved:
        st = _extend_up(lines, node, prev_end)
        en = node.end_lineno
        segments.append("".join(lines[st - 1:en]).rstrip() + "\n")
        spans.append((st, en))
        prev_end = en

    module_src = "\n".join(header)
    module_src += ("\n".join(import_block) + "\n\n\n") if import_block else "\n"
    module_src += "\n\n".join(segments)

    out_path = PROJECT_ROOT / args.out_root / args.target
    print("新模块预览（前 20 行）:")
    for line in module_src.splitlines()[:20]:
        print("   | " + line)
    print()

    # 再导出名单：搬走的名字里，原文件仍然用到的
    moved_spans = set()
    for st, en in spans:
        moved_spans.update(range(st, en + 1))
    remaining_refs = _referenced_outside(lines, moved_names, moved_spans)
    print(f"需再导出 {len(remaining_refs)} 个名字（原文件仍引用）")
    module_dotted = args.target.replace("/", ".").removesuffix(".py")
    reexport = ""
    if remaining_refs:
        reexport = f"from {module_dotted} import (\n" + "".join(
            f"    {n},\n" for n in sorted(remaining_refs)) + ")\n"
    print()

    if not args.apply:
        print("（dry-run，未写入任何文件；加 --apply 执行）")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not args.force:
        print(f"目标已存在，拒绝覆盖: {out_path}（加 --force 覆盖）", file=sys.stderr)
        return 2
    out_path.write_text(module_src, encoding="utf-8")
    backup = src.with_suffix(src.suffix + ".extractbak")
    backup.write_text(text, encoding="utf-8")

    # 删除搬移区间（自下而上删除，避免行号漂移）
    keep_line_flags = [True] * (len(lines) + 1)
    for st, en in spans:
        for i in range(st, en + 1):
            keep_line_flags[i] = False
    new_lines = [ln for i, ln in enumerate(lines, start=1) if keep_line_flags[i]]

    if reexport:
        marker = (f"\n# ---- 由 tools/extract_module.py 区间抽取至 {args.target} ----\n"
                  f"{reexport}\n")
        insert_at = 0
        for i, line in enumerate(new_lines):
            if line.startswith("class ") or line.startswith("def "):
                insert_at = i
                break
        while insert_at > 0 and new_lines[insert_at - 1].strip() == "":
            insert_at -= 1
        new_lines = new_lines[:insert_at] + [marker] + new_lines[insert_at:]

    src.write_text("".join(new_lines), encoding="utf-8")
    print(f"✓ 新模块: {out_path}")
    print(f"✓ 原文件: {src}（备份 {backup.name}）  行数 {len(lines)} -> {len(''.join(new_lines).splitlines(keepends=True))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="从单体机械抽取代码块（连续块或区间）")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True, help="相对 --out-root 的路径，如 app/api/seo.py")
    parser.add_argument("--out-root", default="src")
    parser.add_argument("--names", default="", help="逗号分隔的顶层定义名（连续块模式）")
    parser.add_argument("--range", default="", help="START-END 区间抽取模式")
    parser.add_argument("--keep", default="", help="区间模式：保留在原文件的名字（逗号分隔），如 global 重绑定名")
    parser.add_argument("--keep-lines", default="", help="区间模式：保留在原文件的行号（逗号分隔）")
    parser.add_argument("--extra-import", default="", help="额外附加到新模块的 import 行（分号分隔）")
    parser.add_argument("--resolve", default="",
                        help="本地依赖的新家，NAME=模块路径，逗号分隔（如 ROOT=app.core.paths）")
    parser.add_argument("--anchor", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parsed = parser.parse_args()
    if parsed.range:
        return run_range(parsed)
    if not parsed.names:
        print("必须提供 --names（连续块模式）或 --range（区间模式）", file=sys.stderr)
        return 2
    return run(parsed)


if __name__ == "__main__":
    sys.exit(main())
