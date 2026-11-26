"""裸跑状态下自己完成重启：掐掉旧的 server.py 并立刻拉起新版。

为什么需要这个脚本（2026-11-13）：``tools/restart_service.py`` 在**没守卫**时会
主动拒绝动手（杀了没人拉，服务会一直断）。但那正是用户最需要我自己干的场合 ——
他 2026-11-12 明确说过「做好后你自己帮我重启，不要每次都让我提醒」。

本脚本的取舍：
* 空窗期大约 4~8 秒（kill → 端口释放 → spawn → 首字节）。可以接受，因为
  Solana 是逐签名扫、EVM 是最近 240 块≈108 秒，短暂停机不会丢单。
* **失败就明确报错**，绝不假装成功。起来之后会连打三次 HTTP 确认真的在服务，
  而不是只看端口 LISTENING（TCP 能连、零字节回 也是「监听中」）。
* 新进程用 DETACHED_PROCESS，父进程是本脚本；脚本退出后它继续活着（2026-11-13
  实测派生进程已不再被 12 秒回收）。

用法::

    C:/Python314/python.exe tools/force_restart.py --status
    C:/Python314/python.exe tools/force_restart.py
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = "C:/Python314/python.exe"
PORT = 8765
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

sys.path.insert(0, str(ROOT))


def _psutil():
    import psutil

    return psutil


def find_server_pids() -> list[int]:
    psutil = _psutil()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
        except Exception:
            continue
        joined = " ".join(cmdline)
        if "server.py" in joined and "--worker-market" not in joined and "python" in joined:
            pids.append(proc.info["pid"])
    return pids


def find_worker_pids() -> list[int]:
    psutil = _psutil()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            joined = " ".join(proc.info["cmdline"] or [])
        except Exception:
            continue
        if "server.py" in joined and "--worker-market" in joined:
            pids.append(proc.info["pid"])
    return pids


def port_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def http_ok(path: str = "/", timeout: float = 6.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as response:
            body = response.read(64)
            return True, f"{response.status} {len(body)}B"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


def snapshot() -> int:
    psutil = _psutil()
    print("=== 当前 ===")
    print(f"  端口 {PORT}      : {'LISTENING' if port_open() else '关闭'}")
    print(f"  HTTP 探测       : {http_ok()[1]}")
    print(f"  受守卫管理      : {(ROOT / 'status.json').exists()}")
    for pid in find_server_pids():
        try:
            proc = psutil.Process(pid)
            started = time.strftime("%m-%d %H:%M:%S", time.localtime(proc.create_time()))
            print(f"  pid {pid:<8} {started}  {' '.join(proc.cmdline())[:70]}")
        except Exception:
            pass
    workers = find_worker_pids()
    print(f"  market worker   : {workers or '没有'}")
    return 0


def kill_pid(pid: int, label: str) -> None:
    psutil = _psutil()
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        print(f"  已停 {label} pid {pid}")
    except Exception as exc:  # noqa: BLE001
        print(f"  停 {label} pid {pid} 失败：{exc}")


def wait_free(deadline_seconds: float = 15.0) -> bool:
    end = time.time() + deadline_seconds
    while time.time() < end:
        if not port_open():
            return True
        time.sleep(0.3)
    return not port_open()


def spawn(*extra_args: str) -> int:
    """拉起一个新进程，父是本脚本；脚本退出后它继续跑。"""
    cmd = [PYTHON, str(ROOT / "server.py"), *extra_args]
    log_path = ROOT / (".runtime-cache/smart_money_restart.log" if not extra_args
                       else ".runtime-cache/smart_money_restart_worker.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 绝不裸跑：stdout/stderr 必须离开控制台缓冲，否则缓冲写满后
    # sys.stderr.write 会永久阻塞（2026-10-10 那次的病根）。
    handle = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        creationflags=DETACHED,
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=handle,
        close_fds=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    print(f"  已拉起 {' '.join(extra_args) or 'HTTP'} pid {proc.pid}（日志 {log_path.name}）")
    return proc.pid


def main() -> int:
    parser = argparse.ArgumentParser(description="强制重启（裸跑也动手）")
    parser.add_argument("--status", action="store_true", help="只看现状")
    parser.add_argument("--worker", action="store_true", help="额外拉起 --worker-market")
    parser.add_argument("--skip-http-check", action="store_true")
    args = parser.parse_args()

    if args.status:
        return snapshot()

    print("=== 重启前 ===")
    snapshot()

    server_pids = find_server_pids()
    worker_pids = find_worker_pids()
    if not server_pids:
        print("\n[注意] 没找到在跑的 server.py，将直接拉起新的。")

    print("\n=== 动手 ===")
    for pid in worker_pids:
        kill_pid(pid, "worker")
    for pid in server_pids:
        kill_pid(pid, "HTTP")

    if not wait_free():
        print(f"\n[失败] {PORT} 端口 15 秒内没释放，可能有别的进程占着。")
        print("       请跑 `netstat -ano | grep :8765` 查 pid 后手动处理。")
        return 1
    print(f"  端口 {PORT} 已释放，开始拉起")

    pid = spawn()
    if args.worker:
        spawn("--worker-market")

    print("\n=== 自检 ===")
    for attempt in range(12):
        time.sleep(1.0)
        ok, detail = http_ok(timeout=8.0)
        print(f"  +{attempt + 1}s  HTTP {detail}")
        if ok:
            for _ in range(2):
                time.sleep(1.0)
                ok2, detail2 = http_ok(timeout=8.0)
                print(f"  +{attempt + 3}s  HTTP {detail2}")
                if not ok2:
                    print("[失败] 首字节后又不响应了，看日志。")
                    return 1
            print(f"\n[成功] 新进程 pid {pid} 正在服务 {PORT}。")
            return 0

    print(f"\n[失败] 12 秒内 HTTP 一直没起来（pid {pid}）。看 .runtime-cache/smart_money_restart.log")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
