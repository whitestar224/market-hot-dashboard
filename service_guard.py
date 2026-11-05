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


def read_state():
    """状态文件是遥测，坏掉/缺失都不该让调用方崩。"""
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ago_text(stamp_ms):
    try:
        seconds = max(0, int(time.time() - float(stamp_ms) / 1000))
    except (TypeError, ValueError):
        return "时间未知"
    if seconds < 90:
        return f"{seconds} 秒前"
    if seconds < 5400:
        return f"{seconds // 60} 分钟前"
    if seconds < 172800:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def describe_service(host="127.0.0.1", port=8765):
    """把守护状态翻译成一句人话 + 该做什么。

    只读：不改状态、不拉起进程，随时可以安全调用。
    """
    state = read_state()
    status = str(state.get("status") or "")
    pid = state.get("pid")
    supervisor_pid = state.get("supervisorPid")
    restarts = state.get("restarts") or 0
    live = probe(host, port)
    live_pid = live.get("pid") if live else None

    if live and pid and live_pid == pid:
        headline = f"运行中 · 受守护托管 · pid={pid} · 端口 {port} 正常 · 已重启 {restarts} 次"
        advice = ""
    elif live:
        # 端口有响应但不是当前守护的 child：几乎必然是有人手动裸跑了 server.py
        headline = f"⚠ 端口 {port} 被 pid={live_pid} 占用，但它不是守护托管的进程"
        advice = ("  多半是手动裸跑的 python server.py：控制台缓冲区写满后会永久卡死。"
                  "\n  处理：关掉那个黑窗口，再双击「启动后台服务.cmd」。")
    elif status in {"running", "starting", "recovering"} and supervisor_pid:
        headline = f"守护在跑（pid={supervisor_pid}），但端口 {port} 暂无响应，正在恢复"
        advice = "  服务启动约需 30~90 秒，稍后重新查看即可；持续不恢复请翻 .runtime-cache/service/server.log"
    elif status == "stopped":
        headline = f"已停止（{ago_text(state.get('updatedAt'))}）"
        advice = ("  装了开机自启的话，下次登录会自动起来；现在要用就先双击「启动后台服务.cmd」。")
    else:
        headline = "尚未启动过后台守护"
        advice = "  双击「启动后台服务.cmd」；想开机自动起，运行「开机自启-安装.cmd」。"
    return headline + ("\n" + advice if advice else "")


def wait_for_supervisor_exit(seconds=45.0):
    """等旧守护释放单实例锁，最多 seconds 秒。返回是否已释放。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        lock = acquire_lock()
        if lock is not None:
            lock.close()
            return True
        time.sleep(0.5)
    return False


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


def supervise_market_worker(interval=60.0):
    """监督 market-worker 子进程（进程级拆分：12 源抓取下沉）。

    与主进程守护并行运行：随主进程常驻拉起 ``server.py --worker-market``，
    监测退出/停止文件，意外退出立即重启（崩溃即拉起，快照不 stale）。
    仅在 ``XINGYUN_MARKET_WORKER=1`` 时由 supervise() 启动。
    """
    worker_logger = logging.getLogger("market-worker-output")
    worker_logger.setLevel(logging.INFO)
    worker_logger.addHandler(RotatingFileHandler(RUNTIME / "market-worker.log", maxBytes=4_000_000, backupCount=2, encoding="utf-8"))
    restarts = 0
    while not STOP.exists():
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
               "XINGYUN_SERVICE_STOP_FILE": str(STOP), "XINGYUN_MARKET_WORKER": "1"}
        child = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), "--worker-market", "--worker-market-interval", str(interval)],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        def drain(stream):
            with stream:
                for line in iter(lambda: stream.readline(65536), b""):
                    worker_logger.info("%s", line.decode("utf-8", errors="replace").rstrip())
        threading.Thread(target=drain, args=(child.stdout,), daemon=True, name="market-worker-log-drain").start()
        worker_logger.info("market-worker started pid=%s restarts=%s", child.pid, restarts)
        while child.poll() is None and not STOP.exists():
            for _ in range(10):
                if STOP.exists() or child.poll() is not None:
                    break
                time.sleep(1)
        if STOP.exists():
            stop_child(child)
            break
        code = child.poll()
        if code in {0, 98}:
            worker_logger.info("market-worker intentional exit, no restart: %s", code)
            break
        restarts += 1
        worker_logger.warning("market-worker unexpected exit code=%s, restarting (attempt %s)", code, restarts)
        for _ in range(retry_delay(1)):
            if STOP.exists():
                break
            time.sleep(1)
    worker_logger.info("market-worker supervision stopped")


def supervise(host="127.0.0.1", port=8765):
    # 日志先于一切判断建好：开机自启时没有控制台（pythonw），print 等于扔进黑洞，
    # 「为什么没起来」只能靠 guard.log 回答。
    logger = logging.getLogger("service-guard")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        try:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            logger.addHandler(RotatingFileHandler(RUNTIME / "guard.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
        except OSError:
            pass
    lock = acquire_lock()
    if lock is None:
        logger.info("another supervisor already holds the lock; nothing to do")
        print("后台守护已在运行，无需重复启动。")
        return 0
    child = None
    try:
        # A healthy pre-existing process is never killed or falsely claimed as owned.
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1" if host == "0.0.0.0" else host, port)) == 0:
                logger.warning("port %s already in use; not starting a second copy", port)
                print(f"端口 {port} 已有服务；本次未启动第二份。切换后台守护前请先停止原服务。")
                return 98
        STOP.unlink(missing_ok=True)
        server_logger = logging.getLogger("service-output")
        if not server_logger.handlers:
            server_logger.setLevel(logging.INFO)
            server_logger.addHandler(RotatingFileHandler(RUNTIME / "server.log", maxBytes=8_000_000, backupCount=2, encoding="utf-8"))
        failures = restarts = 0
        # 进程级拆分：market 12 源抓取由独立子进程承担（仅当显式 opt-in）。
        # 只启动一次（放在主进程重启循环之外），避免主进程崩溃重启时叠加出
        # 第二个 market-worker 并发写同一份快照。STOP 文件会一并终止 worker 监督线程。
        if os.environ.get("XINGYUN_MARKET_WORKER", "0") in {"1", "true", "yes", "on"}:
            market_interval = float(os.environ.get("XINGYUN_MARKET_WORKER_INTERVAL", "60") or "60")
            threading.Thread(
                target=supervise_market_worker,
                args=(market_interval,),
                daemon=True,
                name="market-worker-supervisor",
            ).start()
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
    parser.add_argument("action", choices=("start", "run", "stop", "restart", "status"), nargs="?", default="start")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--json", action="store_true", help="status 只输出原始 JSON")
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.action in {"start", "run"}:
        return supervise(args.host, args.port)
    if args.action == "restart":
        # 先请旧守护退场（它会带走自己托管的 child），再原地重新托管。
        STOP.touch()
        if not wait_for_supervisor_exit():
            print("旧守护 45 秒内没有退出，本次未重启；请先运行「停止后台服务.cmd」再启动。")
            return 3
        STOP.unlink(missing_ok=True)
        print("已接管，正在重新启动后台守护…")
        return supervise(args.host, args.port)
    if args.action == "stop":
        STOP.touch()
        print("已请求手动停止；守护不会自动重新拉起。")
    elif args.action == "status":
        if args.json:
            print(STATE.read_text(encoding="utf-8") if STATE.exists() else "{}")
        else:
            print(describe_service(args.host, args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
