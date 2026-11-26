"""生成 JEV vs DeepSeek 对比 HTML 报告."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
rows = json.load(open(ROOT / ".runtime-cache" / "jev_ds_merged.json", encoding="utf-8"))

grade_dist = Counter(r["grade"] for r in rows)
prio_dist = Counter(r["jevPriority"] for r in rows)
sa = [r for r in rows if r["grade"] in ("S", "A")]
# 交叉表
cross = {}
for r in rows:
    cross.setdefault(r["jevPriority"], {}).setdefault(r["grade"], 0)
    cross[r["jevPriority"]][r["grade"]] += 1

# 排序: 按 dsScore 降序
rows_sorted = sorted(rows, key=lambda r: -(r["dsScore"] or 0))

# 散点数据: jevGoodProb vs dsScore
scatter = [
    {"x": r["jevGoodProb"], "y": r["dsScore"], "symbol": r["symbol"], "grade": r["grade"]}
    for r in rows if r["dsScore"] is not None
]

# TOP 30 表格 (DeepSeek S/A 优先)
top30 = rows_sorted[:30]

# 分歧分析: JEV reject 但 DeepSeek 高分
divergence = [r for r in rows if r["jevPriority"] == "reject" and r["grade"] in ("S", "A", "B")]
# 共识头部: DeepSeek S 且 JEV goodProb 高
consensus = sorted(sa, key=lambda r: -(r["jevGoodProb"] or 0))[:20]

# 生成 JSON 供 HTML 内嵌
data_blob = {
    "gradeDist": dict(grade_dist),
    "prioDist": dict(prio_dist),
    "cross": cross,
    "saCount": len(sa),
    "scatter": scatter,
    "top30": top30,
    "divergence": divergence,
    "consensus": consensus,
}
json.dump(data_blob, open(ROOT / ".runtime-cache" / "jev_ds_report_data.json", "w", encoding="utf-8"), ensure_ascii=False)
print("数据 blob 已生成:", len(scatter), "散点,", len(top30), "top30,", len(divergence), "分歧,", len(consensus), "共识")
