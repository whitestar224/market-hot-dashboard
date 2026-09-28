#!/usr/bin/env python3
"""chain_ecosystem.db 旧数据淘汰 + VACUUM 压缩脚本

用法（需先停服务，避免写锁冲突）：
    C:/Python314/python.exe prune_chain_ecosystem.py [--dry-run] [--backup]

策略（保留最近 14 天窗口）：
  1. onchain_fast_jobs:         删 first_seen_at < cut14 且 status IN (screened, unavailable)
  2. onchain_research_candidates: 删 first_seen_at < cut14 且 decision='filtered'
  3. onchain_research_runs:     删 observed_at < cut14
  4. onchain_research_snapshots: 整表清空（历史快照，已备份到 backtest-backup/backtest-data.tar.gz）

安全措施：
  - --backup 先把整个 DB 复制到 .runtime-cache/backtest-backup/chain_ecosystem.pre-prune.db
  - 每张表用分批 DELETE（LIMIT）避免长时间持锁
  - 最后 VACUUM 压缩文件
  - --dry-run 只统计不删除
"""
import sqlite3
import datetime
import os
import shutil
import sys
import time

DB_PATH = r"C:\Users\ZhuanZ1\Desktop\交易\market-hot-dashboard\.runtime-cache\chain_ecosystem.db"
BACKUP_DIR = r"C:\Users\ZhuanZ1\Desktop\交易\market-hot-dashboard\backtest-backup"
RETENTION_DAYS = 14
BATCH_SIZE = 5000  # 每批删除行数


def now_ms() -> int:
    return int(time.time() * 1000)


def main():
    dry_run = "--dry-run" in sys.argv
    do_backup = "--backup" in sys.argv
    cut14 = now_ms() - RETENTION_DAYS * 86400 * 1000
    print(f"[prune] DB={DB_PATH}")
    print(f"[prune] retention={RETENTION_DAYS}d, cut14={cut14} ({datetime.datetime.fromtimestamp(cut14/1000):%Y-%m-%d %H:%M:%S})")
    print(f"[prune] dry_run={dry_run}, backup={do_backup}")

    # 1. 备份
    if do_backup and not dry_run:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        bak = os.path.join(BACKUP_DIR, "chain_ecosystem.pre-prune.db")
        print(f"[prune] 备份到 {bak} ...")
        # 用 sqlite backup API 保证一致性（比直接 copy 文件安全，因 WAL 模式下 copy 会丢数据）
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(bak)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        bak_size = os.path.getsize(bak) / 1024 / 1024
        print(f"[prune] 备份完成 {bak_size:.1f} MB")

    if dry_run:
        print("[prune] === DRY RUN，仅统计 ===")
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=60)
        cur = conn.cursor()
        plan = {
            "onchain_fast_jobs (screened/unavailable < cut14)": (
                "SELECT COUNT(*) FROM onchain_fast_jobs WHERE first_seen_at<? AND status IN ('screened','unavailable')",
                (cut14,),
            ),
            "onchain_research_candidates (filtered < cut14)": (
                "SELECT COUNT(*) FROM onchain_research_candidates WHERE first_seen_at<? AND decision='filtered'",
                (cut14,),
            ),
            "onchain_research_runs (< cut14)": (
                "SELECT COUNT(*) FROM onchain_research_runs WHERE observed_at<?",
                (cut14,),
            ),
            "onchain_research_snapshots (全表)": (
                "SELECT COUNT(*) FROM onchain_research_snapshots", (),
            ),
        }
        for name, (sql, params) in plan.items():
            n = cur.execute(sql, params).fetchone()[0]
            print(f"  {name}: {n}")
        conn.close()
        print("[prune] DRY RUN 完成，未做任何删除")
        return

    # 2. 执行删除
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    def delete_batched(label, table, where, params):
        """分批删除（SQLite DELETE 不支持 LIMIT，用 rowid IN (SELECT ... LIMIT) 替代）"""
        total = cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
        if total == 0:
            print(f"[prune] {label}: 0 行，跳过")
            return 0
        print(f"[prune] {label}: 待删 {total} 行")
        deleted = 0
        while True:
            cur.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} LIMIT {BATCH_SIZE})",
                params,
            )
            n = cur.rowcount
            deleted += n
            conn.commit()
            if n < BATCH_SIZE:
                break
            print(f"  ... 已删 {deleted}/{total}")
            time.sleep(0.05)
        print(f"[prune] {label}: 完成，删 {deleted} 行")
        return deleted

    total_deleted = 0

    total_deleted += delete_batched(
        "fast_jobs screened/unavailable",
        "onchain_fast_jobs",
        "first_seen_at<? AND status IN ('screened','unavailable')",
        (cut14,),
    )

    total_deleted += delete_batched(
        "candidates filtered",
        "onchain_research_candidates",
        "first_seen_at<? AND decision='filtered'",
        (cut14,),
    )

    total_deleted += delete_batched(
        "runs",
        "onchain_research_runs",
        "observed_at<?",
        (cut14,),
    )

    # snapshots 整表清空
    snap_total = cur.execute("SELECT COUNT(*) FROM onchain_research_snapshots").fetchone()[0]
    if snap_total:
        print(f"[prune] snapshots: 待删 {snap_total} 行（整表）")
        cur.execute("DELETE FROM onchain_research_snapshots")
        conn.commit()
        print(f"[prune] snapshots: 完成，删 {snap_total} 行")
        total_deleted += snap_total

    print(f"[prune] 总计删除 {total_deleted} 行")

    # 3. VACUUM 压缩
    size_before = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"[prune] VACUUM 前 DB 大小 {size_before:.1f} MB，开始压缩...")
    t0 = time.time()
    conn.execute("VACUUM")
    conn.commit()
    elapsed = time.time() - t0
    size_after = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"[prune] VACUUM 完成（{elapsed:.1f}s）：{size_before:.1f} MB -> {size_after:.1f} MB（-{(size_before-size_after):.1f} MB）")

    # 4. 校验
    print("[prune] 校验完整性 ...")
    cur.execute("PRAGMA integrity_check")
    result = cur.fetchone()[0]
    print(f"[prune] integrity_check: {result}")
    conn.close()
    print("[prune] 全部完成")


if __name__ == "__main__":
    main()
