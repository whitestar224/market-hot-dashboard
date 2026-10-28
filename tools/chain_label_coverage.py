"""离线量一遍：真实投递过的弹窗里，有多少条能标出链。

只读 alert_delivery.sqlite 的 payload 列，逐条过 annotate_desktop_alert_chain()。
注意：本进程的 ALPHA_LISTED_CHAINS 是空的（那是服务运行时才刷的），所以 Alpha 兜底
那一路在这里偏保守——实测数会比线上低。
"""
import json
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, ".")

import server

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 4000

conn = sqlite3.connect("file:.runtime-cache/alert_delivery.sqlite?mode=ro", uri=True)
rows = conn.execute(
    "SELECT payload FROM deliveries ORDER BY created DESC LIMIT ?", (LIMIT,)
).fetchall()

total = 0
skipped = 0
hit = 0
by_source = Counter()
by_source_hit = Counter()
labels = Counter()
misses = Counter()
for (raw,) in rows:
    try:
        payload = json.loads(raw)
    except Exception:
        skipped += 1
        continue
    # 老 payload 里可能烘着当时算出来的 chainLabel（甚至是我们后来判定为错的那种），
    # 剥掉它才能量到「现在的规则」会算成什么。
    payload.pop("chainLabel", None)
    item = server.normalize_desktop_alert(payload)
    server.annotate_desktop_alert_chain(item)
    source = item.get("sourceId") or item.get("source") or "?"
    has_symbol = bool(server.desktop_alert_asset_symbol(item))
    total += 1
    label = item.get("chainLabel") or ""
    if label:
        hit += 1
        by_source_hit[source] += 1
        labels[label] += 1
    else:
        misses[source] += 1
    if has_symbol:
        by_source[source] += 1

print(f"样本 {total} 条（解析失败 {skipped}）")
print(f"标出链 {hit} 条 = {hit / max(1, total):.0%}")
print("\n链分布：", dict(labels.most_common()))
print("\n按来源（有币标记 / 总条数 / 标出链）：")
for source, count in by_source.most_common():
    print(f"  {source:<28} {count:>5} / {count + misses[source]:>5} / {by_source_hit[source]:>5}")
print("\n未标出链的来源：", dict(misses.most_common(12)))
