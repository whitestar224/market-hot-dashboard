"""把「地址集群」弹窗的最终 payload 抓下来看，不弹窗。

`send_arkham_cluster_desktop_alert()` 里那 20 多个字段（key / queuePriority /
chain / title / body / speech）没法靠单测盯住 —— 单测只覆盖 monitor 的判定，
真正上屏的文案在 server.py 里拼。这里把 `launch_desktop_alert` 换成一个收集器，
于是能拿到**渲染前的真值**，一条都不会漏。

    python tools/arkham_alert_render_probe.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

EVENT = {
    "clusterId": "entity:pump-fun",
    "kind": "entity",
    "name": "pump.fun",
    "clusterType": "meme",
    "significantPct": 5.0,
    "tokens": [
        {"symbol": "PUMP", "pct": 41.28, "chain": "solana",
         "contract": "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",
         "url": "https://arkm.com/explorer/token/pump-fun"},
        {"symbol": "SER", "pct": 21.23, "chain": "solana", "contract": "SerToken1111111111111111111111111111111111"},
        {"symbol": "CLOUT", "pct": 19.36, "chain": "solana", "contract": "CLOUT111111111111111111111111111111111"},
        {"symbol": "POKMON", "pct": 14.41, "chain": "solana", "contract": "POKMON11111111111111111111111111111111"},
    ],
}

captured: dict[str, object] = {}
server.launch_desktop_alert = lambda payload: captured.update(payload) or {"ok": True, "skipped": True}
server.ARKHAM_CLUSTER_MONITOR.store.set_alerts_enabled(True)

result = server.send_arkham_cluster_desktop_alert(EVENT)
print(json.dumps({"result": result, "payload": captured}, ensure_ascii=False, indent=2))
