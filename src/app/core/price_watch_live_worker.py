from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

import server


STOP = False


def request_stop(*_args) -> None:
    global STOP
    STOP = True


def parent_is_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        try:
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def write_health(payload: dict) -> None:
    path = Path(server.PERSIST_CACHE_DIR) / "price_watch_live_worker.json"
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    server.start_network_proxy_adapter()

    # The parent creates and migrates the database before launching us. Running
    # migrations again here can collide with its startup writes and used to make
    # the live worker exit before its first quote cycle.
    started_at = int(time.time() * 1000)
    write_health({
        "ok": True,
        "status": "starting",
        "pid": os.getpid(),
        "parentPid": args.parent_pid,
        "startedAt": started_at,
        "updatedAt": started_at,
    })

    while not STOP and parent_is_alive(args.parent_pid):
        cycle_started = time.monotonic()
        now_ms = int(time.time() * 1000)
        try:
            result = server.price_watch_realtime_monitor_once()
            write_health({
                "ok": True,
                "status": "running",
                "pid": os.getpid(),
                "parentPid": args.parent_pid,
                "startedAt": started_at,
                "updatedAt": int(time.time() * 1000),
                "monitored": int(result.get("monitored") or 0),
                "quoted": int(result.get("quoted") or 0),
                "updated": int(result.get("updated") or 0),
                "alerts": len(result.get("alerts") or []),
                "cycleMs": int((time.monotonic() - cycle_started) * 1000),
            })
        except Exception as exc:
            write_health({
                "ok": False,
                "status": "retrying",
                "pid": os.getpid(),
                "parentPid": args.parent_pid,
                "startedAt": started_at,
                "updatedAt": int(time.time() * 1000),
                "error": f"{type(exc).__name__}: {exc}"[:300],
            })
        elapsed = time.monotonic() - cycle_started
        wait_seconds = max(0.05, server.PRICE_WATCH_REALTIME_INTERVAL_SECONDS - elapsed)
        deadline = time.monotonic() + wait_seconds
        while not STOP and parent_is_alive(args.parent_pid) and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))

    write_health({
        "ok": True,
        "status": "stopped",
        "pid": os.getpid(),
        "parentPid": args.parent_pid,
        "startedAt": started_at,
        "updatedAt": int(time.time() * 1000),
    })
    server.shutdown_shared_executors()
    server.stop_network_proxy_adapter()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
