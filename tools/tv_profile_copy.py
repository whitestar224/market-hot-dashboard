"""把本机 Chrome 默认配置里的**登录态最小子集**复制到独立目录。

两个坑：
1. Chrome 一直开着时，`Default/Network/Cookies` 被它**完全独占**（Windows 上连只读
   打开都是 PermissionError，`cpSync` 直接 EBUSY）。所以要么等用户退出 Chrome，
   要么拿不到 —— `--wait` 就是干这个的：轮询到能读再动手。
2. `Local State` 存着 DPAPI 加密的 cookie 密钥，同一个 Windows 用户能解密，所以复制
   出来的库在另一个 `--user-data-dir` 里仍然可用（这就是免重登的原因）。

    python tools/tv_profile_copy.py [目标目录] [--wait 240]

输出一行 JSON：复制了哪些文件、Cookie 条数。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

SRC = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def copy_plain(dest: Path, rel: str) -> bool:
    src = SRC / rel
    if not src.exists():
        return False
    dst = dest / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
        return True
    except OSError:
        return False


def copy_cookie_db(dest: Path, rel: str) -> int:
    """字节流整份拷（含 -journal/-wal），返回拷贝后库里 cookies 表行数。"""
    src = SRC / rel
    if not src.exists():
        return -1
    dst = dest / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        for suffix in ("-journal", "-wal", "-shm"):
            side = Path(str(src) + suffix)
            if side.exists():
                shutil.copyfile(side, Path(str(dst) + suffix))
    except OSError:
        return -1
    probe = sqlite3.connect(str(dst))
    try:
        return int(probe.execute("SELECT COUNT(*) FROM cookies").fetchone()[0])
    except sqlite3.DatabaseError:
        return -1
    finally:
        probe.close()


def copy_tree(dest: Path, rel: str) -> bool:
    src = SRC / rel
    if not src.exists():
        return False
    dst = dest / rel
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    # leveldb 里的 LOCK / LOG 正在被写，跳过它们少报点错；其余拷不到也无所谓。
    try:
        shutil.copytree(
            src, dst, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("LOCK", "LOG", "LOG.old", "*.tmp"),
            ignore_dangling_symlinks=True,
        )
        return True
    except OSError:
        return False


def locked(path: Path) -> bool:
    """Chrome 开着时 Cookie 库连只读都被拒 —— 用它当「浏览器是否还在」的探针。"""
    try:
        with open(path, "rb"):
            return False
    except OSError:
        return True


def wait_unlocked(path: Path, seconds: float) -> bool:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        if not locked(path):
            return True
        time.sleep(2.0)
    return not locked(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dest", nargs="?", default=".runtime-cache/tv-chrome-profile")
    parser.add_argument("--wait", type=float, default=0.0,
                        help="最多等这么多秒让用户退出 Chrome（默认不等待）")
    args = parser.parse_args()
    dest = Path(args.dest)

    cookie_rel = "Default/Network/Cookies"
    cookie_src = SRC / cookie_rel
    if args.wait and locked(cookie_src):
        print(f"等待 Chrome 退出（最多 {int(args.wait)} 秒）…", file=sys.stderr)
        if not wait_unlocked(cookie_src, args.wait):
            print(json.dumps({"error": "chrome still running"}, ensure_ascii=False))
            return 4
        print("Cookie 库已可读，开始复制。", file=sys.stderr)

    dest.mkdir(parents=True, exist_ok=True)
    report = {
        "dest": str(dest),
        "localState": copy_plain(dest, "Local State"),
        "preferences": copy_plain(dest, "Default/Preferences"),
        "cookies": copy_cookie_db(dest, cookie_rel),
        "localStorage": copy_tree(dest, "Default/Local Storage"),
        "sessionStorage": copy_tree(dest, "Default/Session Storage"),
    }
    print(json.dumps(report, ensure_ascii=False))
    # Cookie 都没拿到就没必要继续了 —— 登录态全靠它。
    return 0 if report["cookies"] > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
