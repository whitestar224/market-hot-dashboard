"""One-shot: render the current cross-source backfill preview for the user."""
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/ZhuanZ1/Desktop/交易/market-hot-dashboard")
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def fmt(ms: int) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ms / 1000))


history_path = ROOT / ".runtime-cache" / "gmgn_trenches_received_history.json"
history = json.loads(history_path.read_text(encoding="utf-8"))
items = [row for row in history["items"] if isinstance(row, dict)]

now_ms = int(time.time() * 1000)
board = server.gmgn_trench_board_rows(items, current_rows=[])
window = board[: server.GMGN_TRENCH_RESPONSE_MAX_ROWS]
window_start = min(int(row.get("poolCreatedAt") or 0) for row in window)
known = {
    identity
    for source in (board, items)
    for raw in source
    if (identity := server.gmgn_trench_history_identity(raw))
}

backfill = server.gmgn_trench_external_backfill_rows(
    window_start_ms=window_start,
    window_end_ms=now_ms,
    known_identities=known,
    limit=server.GMGN_TRENCH_BACKFILL_MAX_ROWS,
)
merged = server.gmgn_trench_merge_backfill_rows(
    board, backfill, cap=server.GMGN_TRENCH_RESPONSE_MAX_ROWS
)
in_board = [row for row in merged if row.get("backfilled")]
dropped = len(backfill) - len(in_board)

lines = [
    "# 战壕榜跨源补录 · 预览（dry-run）",
    "",
    f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now_ms / 1000))}",
    "",
    "## 口径",
    "",
    f"- 榜单窗口：最新 **{len(merged)}** 条，覆盖 **{fmt(int(window[-1].get('poolCreatedAt') or 0))} → {fmt(int(window[0].get('poolCreatedAt') or 0))}**"
    f"（约 {(int(window[0].get('poolCreatedAt') or 0) - window_start) / 3.6e6:.1f} 小时）",
    f"- 最新一条本机接收时间：**{fmt(max(int(row.get('receivedAt') or 0) for row in items))}**"
    f"（落后现在 {(now_ms - max(int(row.get('receivedAt') or 0) for row in items)) / 3.6e6:.1f} 小时）",
    f"- 窗口起点（补录门槛）= 第 300 条的开盘时间 **{fmt(window_start)}**",
    f"- 已知 GMGN 身份（榜 + 2000 条历史）：**{len(known)}** 个，命中即不补录",
    f"- 开关 `GMGN_TRENCH_BACKFILL_MAX_ROWS` = **{server.GMGN_TRENCH_BACKFILL_MAX_ROWS}**（0 = 关闭）",
    "",
    "## 结果",
    "",
    f"- 外部线索候选（本期可补录）：**{len(backfill)}** 条",
    f"- 实际进榜：**{len(in_board)}** 条（另有 {dropped} 条被 clone/头像同族去重挡下）",
    f"- 进榜后榜内构成：**{len(merged) - len(in_board)}** 条 GMGN + **{len(in_board)}** 条补录",
    "",
]

if in_board:
    by_chain = Counter(row["chainLabel"] for row in in_board)
    by_origin = Counter(row["originLabel"] for row in in_board)
    lines += [
        "### 按链",
        "",
        "| 链 | 条数 |",
        "| --- | --- |",
        *[f"| {chain} | {count} |" for chain, count in by_chain.most_common()],
        "",
        "### 按来源",
        "",
        "| 来源 | 条数 |",
        "| --- | --- |",
        *[f"| {origin} | {count} |" for origin, count in by_origin.most_common()],
        "",
        "### 明细",
        "",
        "| 榜内排名 | 开盘时间 | 链 | 代号 | 来源 | 判断 | MC |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in in_board:
        lines.append(
            f"| {row['rank']} | {fmt(row['poolCreatedAt'])} | {row['chainLabel']} | "
            f"{row['symbol']} | {row['originLabel']} | {row['decision']} | "
            f"${row['marketCapUsd']:,.0f} |"
        )
    lines.append("")
else:
    lines += ["本期没有可补录的条目。", ""]

lines += [
    "## 说明",
    "",
    "- 补录只在**当期窗口内**生效，用来消除「GMGN 上游只保留 ~19 分钟、一旦采集空档就永久漏采」的单点依赖。",
    "- 它**不会回溯补账**：已经滚出 300 条的旧币（例如 10-04 06:58 迁移的 SPLICE）仍然不会出现。",
    "- 补录条目在榜上带「补录」角标，鼠标悬停显示来源；历史条带会写明「含 N 条外部线索补录」。",
    "- 补录条目**不会**重新走 GMGN 投研通道（它们本来就在投研库里，带着 Ave/币安钱包等 provider）。",
]

out = ROOT / "deliverables" / "trench-backfill-preview.md"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(out)
print(f"backfill={len(backfill)} in_board={len(in_board)} window_start={fmt(window_start)}")
