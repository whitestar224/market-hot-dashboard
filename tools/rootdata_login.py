"""RootData 项目热门榜：首次授权 / 手动刷新 / 查看快照状态。

用法（在项目根目录）：
    C:\\Python314\\python.exe tools\\rootdata_login.py --login   # 弹窗，人工拖一次滑块
    C:\\Python314\\python.exe tools\\rootdata_login.py --pull    # 静默抓一次（验证码还热着）
    C:\\Python314\\python.exe tools\\rootdata_login.py --status  # 只看快照年龄

``--login`` 会打开一个**独立配置**的 Chrome 窗口（不看你的浏览器配置，也不会
影响你正在用的 Chrome），页面出来后如果在等验证码，拖一下滑块即可，脚本会自己
发现列表渲染出来并抓走数据。票据存在 ``.runtime-cache/chrome-rootdata``，之后服务
就能自己定时刷新。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rootdata_projects as rp  # noqa: E402


def _status() -> int:
    payload = rp.read_snapshot()
    age = rp.snapshot_age_seconds(payload)
    if not payload:
        print("还没有快照：" + str(rp.SNAPSHOT_PATH))
        print("先跑 --login 授权一次。")
        return 1
    projects = payload.get("projects") or []
    print(f"快照：{rp.SNAPSHOT_PATH}")
    print(f"抓取时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(payload.get('fetchedAtMs', 0) / 1000))}"
          f"（{age / 60:.1f} 分钟前）")
    print(f"来源：{payload.get('source')} · 项目数：{len(projects)}")
    for row in projects[:5]:
        print(f"  #{row.get('rank')} {row.get('name')} 热度={row.get('popularity')} 成长={row.get('growth')}")
    return 0


def _pull(headless: bool, dump_bodies: bool = True) -> int:
    print(f"打开 {rp.ROOTDATA_BOARD_URL}")
    result = rp.pull_from_browser(headless=headless, dump_bodies=dump_bodies)
    projects = rp.ranked_projects([dict(row) for row in result["projects"]])
    rp.write_snapshot(projects, source=str(result.get("source") or ""))
    print(f"抓取成功（来源 {result.get('source')}）· 保留 {len(projects)} 个项目")
    for row in projects[:8]:
        print(f"  #{row.get('rank')} {row.get('name')} 热度={row.get('popularity')} 成长={row.get('growth')}")
    return 0 if projects else 2


def _login() -> int:
    print("启动 RootData 专用浏览器（独立配置，不影响你正在用的 Chrome）…")
    if not rp.ensure_browser(headless=False, timeout=40):
        print("Chrome 调试口没起来（端口 9222 被占？）")
        return 3
    print("如果窗口里出现滑块验证码，请拖一下；脚本最多等 3 分钟。")
    try:
        return _pull(headless=False)
    except Exception as exc:
        print(f"抓取失败：{exc}")
        print("窗口先别关，拖完滑块再跑一次 --pull 即可。")
        return 4


def main() -> int:
    parser = argparse.ArgumentParser(description="RootData 项目热门榜取数")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--login", action="store_true", help="弹窗授权并抓一次")
    group.add_argument("--pull", action="store_true", help="静默抓一次")
    group.add_argument("--status", action="store_true", help="查看快照状态")
    args = parser.parse_args()
    if args.status:
        return _status()
    if args.pull:
        return _pull(headless=False)
    return _login()


if __name__ == "__main__":
    raise SystemExit(main())
