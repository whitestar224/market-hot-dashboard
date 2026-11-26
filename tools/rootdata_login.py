"""RootData 取数：手动刷新 / 查看快照状态。

用法（在项目根目录）：

    C:\\Python314\\python.exe tools\\rootdata_login.py --pull     # 现在就抓一次
    C:\\Python314\\python.exe tools\\rootdata_login.py --status   # 只看快照年龄
    C:\\Python314\\python.exe tools\\rootdata_login.py --proxy    # 只看哪个代理能用

**不需要登录，也不需要浏览器。** 2026-11-07 实测：``www.rootdata.com`` 才挂在
腾讯云 WAF 后面，``cn.rootdata.com`` 是纯服务端渲染，只要走一个能出网的代理就能
拿到完整 HTML。取数逻辑全在 ``rootdata_projects``，本文件只是一层命令行壳子。

``--pull`` 会用 ``rootdata_projects`` 的跨进程锁文件，和正在跑的服务互斥：
服务那边正好在抓的话，这里会提示「另一进程正在刷新」，等 15 分钟即可。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources import rootdata_projects as rp  # noqa: E402


def _minutes(age: float) -> str:
    return "从未抓过" if age == float("inf") else f"{age / 60:.1f} 分钟前"


def _status() -> int:
    projects = rp.read_snapshot()
    funding = rp.read_fundraising_snapshot()
    if not projects and not funding:
        print("还没有任何快照。跑一次 --pull 试试。")
        return 1
    print(f"项目榜：{rp.PROJECTS_SNAPSHOT}")
    print(f"  抓取于 {_minutes(rp.snapshot_age_seconds(projects))} · {len(projects.get('projects') or [])} 个项目")
    for row in (projects.get("projects") or [])[:5]:
        print(f"    #{row.get('rank')} {row.get('name')} 热度={row.get('popularity')} 成长={row.get('growth')}")
    print(f"融资：{rp.FUNDRAISING_SNAPSHOT}")
    print(
        f"  抓取于 {_minutes(rp.fundraising_age_seconds(funding))} · "
        f"{len(funding.get('events') or [])} 条事件 · 已读账本 {len(funding.get('seenKeys') or [])} 条"
    )
    for row in (funding.get("events") or [])[:5]:
        print(
            f"    {row.get('name')} {row.get('round')} · {row.get('amount') or '金额未披露'}"
            f" · 投资方 {' / '.join((row.get('investors') or [])[:2]) or '--'}"
        )
    state = rp.refresh_state()
    if state.get("message"):
        print(f"最近一次后台刷新：{state.get('message')}")
    return 0


def _proxy() -> int:
    try:
        proxy = rp._working_proxy()  # noqa: SLF001 - 这是排障入口，就是要看内部判定
    except Exception as exc:
        print(f"没有可用代理：{exc}")
        return 2
    print(f"可用代理：{proxy}")
    return 0


def _pull() -> int:
    started = time.time()
    try:
        result = rp.refresh()
    except Exception as exc:
        print(f"抓取失败：{exc}")
        return 3
    print(f"抓取完成（{time.time() - started:.1f}s）：{result}")
    if isinstance(result.get("fundraising"), str):
        print("融资页这一轮被另一个进程占着（服务正在刷新），过一会儿再看 --status。")
    return _status()


def main() -> int:
    parser = argparse.ArgumentParser(description="RootData 项目榜 + 一级融资取数")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--pull", action="store_true", help="现在抓一次（项目榜 + 融资）")
    group.add_argument("--status", action="store_true", help="查看快照状态")
    group.add_argument("--proxy", action="store_true", help="探活代理")
    # 兼容旧入口（曾经要过一次滑块验证码，现在不需要了）。
    group.add_argument("--login", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.status:
        return _status()
    if args.proxy:
        return _proxy()
    return _pull()


if __name__ == "__main__":
    raise SystemExit(main())
