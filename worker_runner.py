"""通用子进程 worker 骨架（微服务化阶段 1 基建，零行为变化）。

这是「进程级拆分」的基础设施：让 CPU 密集 / 易阻塞的后台构建任务从主进程线程
下沉到独立 Python 子进程，通过【本地文件系统】通信（磁盘快照 + 触发文件），
解除 GIL 饿死。本文件当前**不被 server.py import**，仅作为阶段 2/3/4 下沉时的
worker 入口模板；引入前不改变任何现有运行行为。

设计原则（对应 docs/MICROSERVICE-BLUEPRINT.md）：
  1. 结果传递 → 磁盘快照（`write_json_cache` 原子写 tmp+rename）。
  2. 任务触发 → 触发文件轮询（`*.request`），主进程写标记、worker 发现后执行。
  3. 心跳/状态 → `.runtime-cache/service/status.json` 的 `workers.<name>` 段。
  4. 优雅退出 → 监听 stop 文件（`XINGYUN_SERVICE_STOP_FILE`）+ SIGTERM。
  5. 日志 → stderr 由 service_guard 抽到 `server.log`（沿用现有 drain 线程）。

用法（阶段 2 落地时）：
    python worker_runner.py --worker event-monitor-core --interval 60
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 复用 server.py 的持久化与路径原语（这些是纯函数/常量，安全 import）
from server import PERSIST_CACHE_DIR, api_cache_path, write_json_cache  # noqa: E402

SERVICE_DIR = PERSIST_CACHE_DIR / "service"
STATUS_PATH = SERVICE_DIR / "status.json"


def _stop_requested() -> bool:
    """是否收到停止信号：stop 文件 或 环境变量指定的 stop 文件。"""
    stop_file = os.environ.get("XINGYUN_SERVICE_STOP_FILE")
    if stop_file and Path(stop_file).exists():
        return True
    return (SERVICE_DIR / "manual-stop").exists()


def _write_status(name: str, **fields: Any) -> None:
    """把 worker 心跳写进 status.json 的 workers.<name> 段（原子写，尽力而为）。"""
    try:
        SERVICE_DIR.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        if STATUS_PATH.exists():
            try:
                payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
        workers = payload.setdefault("workers", {})
        workers[name] = {
            "pid": os.getpid(),
            "updatedAt": int(time.time() * 1000),
            **fields,
        }
        tmp = STATUS_PATH.with_suffix(".worker.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(STATUS_PATH)
    except OSError:
        # 状态是遥测，绝不该因写状态失败而终止 worker
        pass


def _poll_trigger(name: str, trigger_path: Path | None) -> bool:
    """返回是否应执行一轮。无 trigger 文件时按间隔执行；有则消费后执行。"""
    if trigger_path is None:
        return True
    return trigger_path.exists()


def _consume_trigger(trigger_path: Path | None) -> None:
    if trigger_path is not None and trigger_path.exists():
        try:
            trigger_path.unlink()
        except OSError:
            pass


def run_worker(
    name: str,
    task: Callable[[], Any],
    *,
    interval: float,
    trigger_path: Path | None = None,
) -> int:
    """标准 worker 主循环：心跳 + 轮询触发 + 执行 + 优雅退出。"""
    print(f"[worker:{name}] start pid={os.getpid()} interval={interval}s", flush=True)

    # SIGTERM 优雅退出：置 stop 文件语义等价（不额外建状态）
    stopping = threading.Event()

    def _handle_term(_signum, _frame):
        stopping.set()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_term)

    _write_status(name, status="starting", lastError="")
    while not stopping.is_set() and not _stop_requested():
        try:
            _write_status(name, status="running", lastError="")
            if _poll_trigger(name, trigger_path):
                _consume_trigger(trigger_path)
                task()
                _write_status(name, status="ok", lastError="", lastRunAt=int(time.time() * 1000))
            else:
                _write_status(name, status="idle", lastError="")
        except Exception as exc:
            _write_status(name, status="error", lastError=str(exc)[:180])
            traceback.print_exc()
        # 分片睡眠，便于及时响应 stop
        for _ in range(int(interval)):
            if stopping.is_set() or _stop_requested():
                break
            time.sleep(1)

    _write_status(name, status="stopped", lastError="")
    print(f"[worker:{name}] stopped", flush=True)
    return 0


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地 worker 子进程入口")
    parser.add_argument("--worker", required=True, help="worker 名（写入 status.json）")
    parser.add_argument("--interval", type=float, default=60.0, help="轮询间隔（秒）")
    parser.add_argument("--trigger", default="", help="触发文件路径（可选）")
    return parser


# 阶段 2/3/4 落地时，在下面注册具体的 task 绑定（示例见注释，未启用）：
#   WORKER_TASKS = {
#       "event-monitor-core": lambda: build_event_monitor_core_payload(),
#       "market-hot": market_payload,
#   }
WORKER_TASKS: dict[str, Callable[[], Any]] = {}


def main() -> int:
    args = _build_cli().parse_args()
    task = WORKER_TASKS.get(args.worker)
    if task is None:
        print(f"[worker:{args.worker}] 未注册的 worker（当前仅骨架，阶段 2 起注册 task）", file=sys.stderr)
        return 2
    trigger = Path(args.trigger) if args.trigger else None
    return run_worker(args.worker, task, interval=args.interval, trigger_path=trigger)


if __name__ == "__main__":
    raise SystemExit(main())
