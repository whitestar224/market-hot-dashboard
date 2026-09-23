"""Local dashboard supervisor. Owns only its child; an explicit stop stays stopped."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime-cache" / "service"
STOP = RUNTIME / "manual-stop"
STATE = RUNTIME / "status.json"
LOCK = RUNTIME / "supervisor.lock"
BENCHMARK_LEASE = RUNTIME / "rapid-benchmark-lease.json"


def benchmark_lease_active(path=BENCHMARK_LEASE, now_ms=None):
    """Allow a localhost model benchmark to temporarily outlive probes."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        active = int(payload.get("expiresAt") or 0) > int(now_ms or time.time() * 1000)
        if not active:
            path.unlink(missing_ok=True)
        return active
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def acquire_lock(path=LOCK):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if path.stat().st_size == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        return None
    return stream


def probe(host, port):
    target = "127.0.0.1" if host == "0.0.0.0" else "[::1]" if host == "::" else host
    try:
        with build_opener(ProxyHandler({})).open(f"http://{target}:{port}/api/service-liveness", timeout=3) as response:
            payload = json.loads(response.read(4096))
        return payload if payload.get("ok") and payload.get("service") == "market-hot-dashboard" else None
    except (OSError, ValueError):
        return None


def retry_delay(failures):
    return min(60, 2 ** min(6, max(1, failures)))


def stop_child(child, grace=20):
    if child is None or child.poll() is not None:
        return
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def save_state(**fields):
    tmp = STATE.with_suffix(".tmp")
    payload = json.dumps({"supervisorPid": os.getpid(), "updatedAt": int(time.time()*1000), **fields})
    # Windows readers/antivirus may temporarily deny rename. Status is telemetry,
    # never a reason to terminate supervision and close the child's log pipe.
    for attempt in range(3):
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(STATE)
            return True
        except OSError:
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
    return False


def supervise(host="127.0.0.1", port=8765):
    lock = acquire_lock()
    if lock is None:
        print("后台守护已在运行，无需重复启动。")
        return 0
    child = None
    try:
        # A healthy pre-existing process is never killed or falsely claimed as owned.
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1" if host == "0.0.0.0" else host, port)) == 0:
                print(f"端口 {port} 已有服务；本次未启动第二份。切换后台守护前请先停止原服务。")
                return 98
        STOP.unlink(missing_ok=True)
        logger = logging.getLogger("service-guard")
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(RUNTIME / "guard.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        logger.addHandler(handler)
        server_logger = logging.getLogger("service-output")
        server_logger.setLevel(logging.INFO)
        server_logger.addHandler(RotatingFileHandler(RUNTIME / "server.log", maxBytes=8_000_000, backupCount=2, encoding="utf-8"))
        failures = restarts = 0
        while not STOP.exists():
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                   "XINGYUN_SERVICE_STOP_FILE": str(STOP)}
            child = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--host", host, "--port", str(port)],
                                     cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def drain(stream):
                with stream:
                    for line in iter(lambda: stream.readline(65536), b""):
                        server_logger.info("%s", line.decode("utf-8", errors="replace").rstrip())
            threading.Thread(target=drain, args=(child.stdout,), daemon=True, name="service-log-drain").start()
            started = time.monotonic()
            missed = 0
            logger.info("started pid=%s restarts=%s", child.pid, restarts)
            save_state(status="starting", pid=child.pid, restarts=restarts, port=port)
            while child.poll() is None and not STOP.exists():
                live = probe(host, port)
                good = bool(live and live.get("pid") == child.pid)
                missed = 0 if good else missed + 1
                save_state(status="running" if good else "recovering", pid=child.pid, restarts=restarts, port=port, failedProbes=missed)
                if time.monotonic() - started > 180 and missed >= 6 and not benchmark_lease_active():
                    logger.error("liveness failed repeatedly, restarting owned pid=%s", child.pid)
                    child.terminate()
                    stop_child(child, grace=5)
                    break
                # Interruptible manual stop, without an extra full probe interval.
                for _ in range(10):
                    if STOP.exists() or child.poll() is not None:
                        break
                    time.sleep(1)
            if STOP.exists():
                stop_child(child)
                break
            code = child.poll()
            if code in {0, 98}:
                logger.info("intentional exit or occupied port, no restart: %s", code)
                break
            failures = failures + 1 if time.monotonic() - started < 300 else 1
            restarts += 1
            logger.warning("unexpected exit code=%s, retry=%ss", code, retry_delay(failures))
            for _ in range(retry_delay(failures)):
                if STOP.exists():
                    break
                time.sleep(1)
        save_state(status="stopped", pid=None, restarts=restarts, port=port)
        return 0
    except KeyboardInterrupt:
        STOP.touch()
        stop_child(child)
        save_state(status="stopped", pid=None)
        return 0
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "run", "stop", "status"), nargs="?", default="start")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.action in {"start", "run"}:
        return supervise(args.host, args.port)
    if args.action == "stop":
        STOP.touch()
        print("已请求手动停止；守护不会自动重新拉起。")
    elif args.action == "status":
        print(STATE.read_text(encoding="utf-8") if STATE.exists() else "尚未启动后台守护。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
