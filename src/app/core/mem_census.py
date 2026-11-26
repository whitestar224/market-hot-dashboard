"""进程内存画像 —— 给「内存怎么这么高」这类问题定位到具体类型。

线上只有一个进程，没有 REPL、也不该为了看一眼内存就装 profiler，所以留一个
手动诊断端点 ``GET /api/_mem``：

    curl -s --noproxy '*' http://127.0.0.1:8765/api/_mem | python -m json.tool

它做三件事：
1. ``RSS / VMS / 线程数``（线程数要分开看：Windows 上每个线程 1MB 栈是 VMS 大头）；
2. ``gc.get_objects()`` 全堆普查，按**类型**汇总「多少个 + 一共多少 MB」；
3. 顺带报 ``sys.modules`` 里已加载的第三方大件（cv2 / onnxruntime / numpy 这种
   一旦 import 就常驻，RSS 里可能是几百 MB）。

代价：全堆遍历在 ~800MB 进程上约 1~2 秒，**只给手动调用**，别挂到页面轮询上。
"""

from __future__ import annotations

import gc
import os
import sys
from collections import Counter
from typing import Any

# 这几个一 import 就常驻几百 MB，列出来才看得出「谁把内存吃掉了」
_HEAVY_MODULES = (
    "cv2", "numpy", "onnxruntime", "torch", "pandas", "matplotlib",
    "scipy", "PIL", "rapidocr_onnxruntime", "cryptography", "websocket",
)


def _process_memory() -> dict[str, float]:
    try:
        import psutil  # 项目里已在用，失败就退化
        process = psutil.Process(os.getpid())
        info = process.memory_info()
        return {
            "rssMb": round(info.rss / 1048576, 1),
            "vmsMb": round(info.vms / 1048576, 1),
            "privateMb": round(getattr(info, "private", 0) / 1048576, 1),
        }
    except Exception:
        return {}


def _thread_counts() -> dict[str, int]:
    """Python 线程数 + OS 线程数（差距大说明有原生线程池，如 onnxruntime/OpenBLAS）。"""
    python_threads = len([t for t in __import__("threading").enumerate()])
    os_threads = 0
    try:
        import psutil
        os_threads = psutil.Process(os.getpid()).num_threads()
    except Exception:
        pass
    return {"pythonThreads": python_threads, "osThreads": os_threads}


def _module_footprint() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        import psutil
        maps = psutil.Process(os.getpid()).memory_maps()
        for mm in maps:
            path = (mm.path or "").lower()
            if not path.endswith((".pyd", ".dll")):
                continue
            if "site-packages" not in path and "python314" not in path:
                continue
            # psutil 在 Windows 上给的是**字节**（早期版本是 KB，所以别写死 /1024）
            rss_mb = (mm.rss or 0) / 1048576
            if rss_mb < 1:
                continue
            out.append({"path": os.path.basename(path), "rssMb": round(rss_mb, 1)})
    except Exception:
        pass
    out.sort(key=lambda row: -row["rssMb"])
    return out[:12]


def census(top: int = 25) -> dict[str, Any]:
    """全堆普查：按类型汇总对象数与估算占用（``sys.getsizeof`` 不含子对象，作相对参考）。"""
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    total_bytes = 0
    biggest: list[tuple[int, str, str]] = []
    for obj in gc.get_objects():
        name = type(obj).__name__
        counts[name] += 1
        try:
            size = sys.getsizeof(obj, 0)
        except Exception:
            size = 0
        sizes[name] += size
        total_bytes += size
        # 「哪个容器特别大」比「哪个类型多」更能指认占用来源
        if name in ("dict", "list", "set", "tuple", "bytes", "str"):
            try:
                length = len(obj)
            except Exception:
                length = 0
            if length >= 20000:
                biggest.append((length, name, _preview(obj)))
    rows = [
        {"type": name, "count": count, "mb": round(sizes[name] / 1048576, 2)}
        for name, count in counts.most_common(400)
    ]
    by_size = sorted(rows, key=lambda row: -row["mb"])[: max(1, int(top))]
    by_count = sorted(rows, key=lambda row: -row["count"])[: max(1, int(top))]
    biggest.sort(key=lambda row: -row[0])
    return {
        "ok": True,
        "pid": os.getpid(),
        "memory": _process_memory(),
        "threads": _thread_counts(),
        "gcTracked": len(gc.get_objects()),
        "gcCounts": {str(i): gc.get_count()[i] for i in range(3)},
        "trackedTotalMb": round(total_bytes / 1048576, 1),
        "topBySize": by_size,
        "topByCount": by_count,
        "bigContainers": [
            {"len": length, "type": name, "preview": preview}
            for length, name, preview in biggest[:15]
        ],
        "loadedHeavyModules": [name for name in _HEAVY_MODULES if name in sys.modules],
        "nativeMaps": _module_footprint(),
    }


def _preview(obj: Any) -> str:
    """给大容器留一句「它是什么」的线索，别只报个长度。"""
    try:
        if isinstance(obj, dict):
            keys = list(obj.keys())[:3]
            return f"keys={keys}"
        if isinstance(obj, (list, tuple, set)):
            items = list(obj)[:2]
            return f"head={str(items)[:120]}"
        if isinstance(obj, bytes):
            return obj[:60].decode("utf-8", "replace")
        return str(obj)[:100]
    except Exception:
        return ""
