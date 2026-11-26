"""一次性把融资快照里「缺详情」的项目全部补上详情/标签/生态/成立时间。

日常节奏由 ``rootdata_projects._enrich_fundraising_details`` 每轮补几个（防 WAF 封禁），
这个脚本是把存量一次性追平用的：带间隔串行抓、撞到验证码立即停、已抓的跳过，所以
可以安全地反复跑，每次续上一批。

用法：
    C:\\Python314\\python.exe tools\\rootdata_backfill_details.py --batch 5 --sleep 3
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources import rootdata_projects as rp  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="融资项目详情一次性补全")
    parser.add_argument("--batch", type=int, default=5, help="每轮最多抓几个（默认 5）")
    parser.add_argument("--sleep", type=float, default=3.0, help="每个详情页之间的间隔秒数（默认 3）")
    args = parser.parse_args()

    payload = rp.read_fundraising_snapshot()
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    if not events:
        print("融资快照里没有事件，先跑 --pull。")
        return 1

    def has_detail(event: dict) -> bool:
        return bool(event.get("intro") or event.get("tags") or event.get("ecosystem") or event.get("founded"))

    missing = [event for event in events if not has_detail(event)]
    if not missing:
        print(f"全部 {len(events)} 条都已有详情，无需补全。")
        return 0

    print(f"共 {len(events)} 条，缺详情 {len(missing)} 条，本轮最多补 {args.batch} 条。")

    # 与服务端 ``refresh()`` 的融资分支共用同一把跨进程锁：服务每 15 分钟重抓融资列表
    # 会整份重写快照，如果跟本脚本的「读→抓详情→写回」交错，会把刚补的详情覆盖掉。
    # 拿锁后才能安全地读-改-写（锁有 180s TTL，本脚本单轮抓取远超这个时长的概率很低，
    # 若撞上服务 refresh 拿锁失败，那轮服务会自动跳过，不影响我们写回）。
    if not rp._acquire_fundraising_lock(ttl_seconds=max(300.0, args.batch * (args.sleep + 12.0))):
        print("服务端正在刷新融资列表，稍后再跑（避免覆盖竞态）。")
        return 1
    try:
        # 拿锁后重读一次，防止上面读完之后服务又刷新了一轮。
        payload = rp.read_fundraising_snapshot()
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        missing = [event for event in events if not has_detail(event)]
        if not missing:
            print(f"拿锁后复查：全部 {len(events)} 条都已有详情，无需补全。")
            return 0

        proxy = rp._working_proxy()  # noqa: SLF001
        if not proxy:
            print("没有可用代理，稍后再试。")
            return 1

        patched = 0
        for event in missing[: args.batch]:
            url = rp._text(event.get("url"), 400)
            if not url.startswith("https://cn.rootdata.com/projects/detail/"):
                continue
            name = rp._text(event.get("name"), 40)
            try:
                detail = rp.parse_project_detail(rp._fetch_detail(proxy, url))
            except rp.RootdataBlocked:
                print(f"[停] 撞到验证码，立即停手。已补 {patched} 条，剩 {len(missing) - patched} 条稍后再续。")
                break
            except Exception as exc:
                print(f"[跳过] {name}: {type(exc).__name__} {str(exc)[:60]}")
                continue
            event.update(detail)
            print(f"[补] {name}: 标签={detail.get('tags')} 生态={detail.get('ecosystem')} 成立={detail.get('founded')}")
            patched += 1
            if patched < args.batch:
                time.sleep(args.sleep)

        if patched:
            # 只更新 events，不动 seenKeys（去重账本跟详情无关）。
            rp.write_fundraising_snapshot(events, list(payload.get("seenKeys") or []))
            print(f"已写回快照，本轮补 {patched} 条。")

        remaining = sum(1 for event in events if not has_detail(event))
        print(f"剩余缺详情：{remaining} 条。")
        return 0
    finally:
        rp._release_fundraising_lock()


if __name__ == "__main__":
    sys.exit(main())
