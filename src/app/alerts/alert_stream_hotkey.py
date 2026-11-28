#!/usr/bin/env python3
"""事件流浮窗的常驻热键宿主 —— 让 Alt+Q 在任何时候都能把窗口叫回来。

## 为什么要有这个进程

热键原来是在浮窗自己的 Tk 主循环里轮询的（``alert_stream_overlay._poll_hotkey``），
进程一退出，热键就跟着死了。于是「点叉叉关掉窗口 → 按 Alt+Q 想再叫出来」这件事
永远做不到，只能去双击 `.cmd`。用户 2026-11-28：「我要的是快捷键启动，之前不是
alt+q 吗」。

所以把热键从「窗口的附属品」拆出来，放进一个**没有界面、只轮询按键**的常驻进程：

* 浮窗进程还在 → 宿主**什么都不做**，交给浮窗自己轮询同一个组合键做显隐 / 收起
  （两个都动手会一次按键触发两次，窗口闪一下就还原）；
* 一个浮窗进程都没有（点了叉叉 / 被任务管理器结束）→ 宿主按 Alt+Q 把它拉起来，
  窗口按上次保存的位置出现。

宿主自己是 ``pythonw`` 单实例（独立互斥量），日志写进浮窗同一个 log 文件，
前缀 ``[hotkey]``。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

# 独立运行入口的 sys.path 兜底：这个文件会被 server.py / .cmd 当成【脚本】直接跑。
_SRC_DIR = Path(__file__).resolve().parents[2]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from app.alerts import alert_stream_overlay as overlay  # noqa: E402

HOST_SCRIPT = Path(__file__).resolve()
OVERLAY_SCRIPT = HOST_SCRIPT.with_name("alert_stream_overlay.py")
#: 与浮窗的互斥量分开：宿主和窗口本来就应该同时存在。
MUTEX_NAME = "Local\\xys-alert-stream-hotkey"
POLL_SECONDS = 0.05

_DETACHED_FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
)


def log_line(message: str) -> None:
    overlay.log_line(f"[hotkey] {message}")


_MUTEX_HANDLE = None


def acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def host_pids() -> list[int]:
    """已经在跑的宿主进程（不含自己）。"""
    try:
        import psutil
    except Exception:
        return []
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.pid == os.getpid():
                continue
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if "alert_stream_hotkey.py" in cmdline:
                pids.append(proc.pid)
        except Exception:
            continue
    return pids


def _hotkey_mutex_taken() -> bool:
    """有没有别人已经建过这个互斥量（只查询，不持有）。"""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return False
        try:
            return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def ensure_running() -> dict:
    """供 server.py / 浮窗启动时调用：没有宿主就拉一个。**绝不抛异常**。"""
    if os.name != "nt":
        return {"started": False, "reason": "only supported on Windows", "pid": None}
    existing = host_pids()
    # 只查进程列表不够：一个服务启动会连着调两次（spawn_detached + 窗口 main），
    # 两次都看不见"刚起来还没写进进程表"的那个，就会白拉起第二个、再自己退出。
    # 互斥量是立即可见的，补上这一道。
    if existing or _hotkey_mutex_taken():
        return {
            "started": False,
            "reason": "already running",
            "pid": existing[0] if existing else None,
            "pids": existing,
        }
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = str(pythonw) if pythonw.is_file() else "pythonw.exe"
    try:
        child = subprocess.Popen(
            [launcher, str(HOST_SCRIPT)],
            cwd=str(HOST_SCRIPT.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_DETACHED_FLAGS,
            close_fds=True,
        )
    except Exception as exc:
        return {"started": False, "reason": f"spawn failed: {exc}", "pid": None}
    log_line(f"host spawned pid={child.pid}")
    return {"started": True, "reason": "", "pid": child.pid}


def fire(port: int) -> dict:
    """热键按下时该做的事：窗口在跑就交给它，没有窗口才拉起。"""
    if overlay.overlay_processes(OVERLAY_SCRIPT):
        # 有进程（包括被 Alt+Q 收起、窗口不可见的那种）→ 浮窗自己会响应这次按键。
        return {"action": "delegate"}
    result = overlay.spawn_detached(source="hotkey", port=port)
    log_line(f"spawn from hotkey -> {result}")
    return {"action": "spawn", **result}


def main() -> int:
    parser = argparse.ArgumentParser(description="星云社事件流浮窗 · 常驻热键宿主")
    parser.add_argument("--hotkey", default=os.getenv("XYS_ALERT_STREAM_HOTKEY") or overlay.DEFAULT_HOTKEY)
    parser.add_argument("--port", type=int, default=overlay.service_port())
    parser.add_argument("--fire-once", action="store_true", help="按一次就退出，用于诊断")
    args = parser.parse_args()

    try:
        modifier_vks, main_vk, label = overlay.parse_hotkey(args.hotkey)
    except Exception as exc:
        log_line(f"bad hotkey {args.hotkey!r}: {exc}")
        print(f"hotkey host: {exc}", file=sys.stderr)
        return 2

    if args.fire_once:
        print(fire(args.port))
        return 0

    if not acquire_single_instance():
        log_line("another host is already running")
        return 0

    log_line(f"host ready hotkey={label} port={args.port} overlay={OVERLAY_SCRIPT.name}")
    held = False
    while True:
        time.sleep(POLL_SECONDS)
        try:
            down = overlay.hotkey_is_down(modifier_vks, main_vk)
        except Exception:
            down = False
        if down and not held:
            held = True
            try:
                fire(args.port)
            except Exception as exc:
                log_line(f"fire failed: {type(exc).__name__}: {exc}")
        elif not down:
            held = False


if __name__ == "__main__":
    raise SystemExit(main())
