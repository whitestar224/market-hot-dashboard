"""一次性回填：把已经弹过「三人同时上车」的币写成 sync_episodes 剧集。

否则部署后同一个币只要再来一笔买入，就会把那条三人弹窗**重弹一遍**。
announced 取该币当前在 24h 窗口内的全部买单钱包（就是当时车上的人）。
"""
import json
import os
import sqlite3
import sys
import time

ROOT = r'C:\Users\ZhuanZ1\Desktop\交易\market-hot-dashboard'
sys.path.insert(0, os.path.join(ROOT, 'src'))

from app.monitors.smart_money_monitor import KOL_SYNC_JOIN_WINDOW_SECONDS, KOL_SYNC_WINDOW_SECONDS

mon = sqlite3.connect('%s/.runtime-cache/smart_money_monitor.sqlite' % ROOT, timeout=15)
mon.row_factory = sqlite3.Row
flow = sqlite3.connect('file:%s/.runtime-cache/event_flow.sqlite?mode=ro' % ROOT,
                       uri=True, timeout=15)
flow.row_factory = sqlite3.Row

now = int(time.time() * 1000)
since = now - KOL_SYNC_WINDOW_SECONDS * 1000

seen = set()
for row in flow.execute(
    "select activity_at, payload from flow_events where payload like '%同时上车%' "
    "order by activity_at desc limit 50"
):
    payload = json.loads(row['payload'])
    popup = payload.get('popup') or {}
    chain = str(popup.get('chain') or '').strip()
    token = str(popup.get('contractAddress') or '').strip()
    if not chain or not token or (chain, token) in seen:
        continue
    seen.add((chain, token))
    if row['activity_at'] < since:
        continue
    key = f"{chain}:{token.lower() if token.lower().startswith('0x') else token}"
    exists = mon.execute("select 1 from sync_episodes where token_key=?", (key,)).fetchone()
    if exists:
        continue
    rows = [dict(r) for r in mon.execute(
        "select wallet_address, observed_at from events where chain=? and token_address=? "
        "and observed_at>=?", (chain, token, since)
    )]
    if not rows:
        continue
    announced = sorted({str(r['wallet_address']).lower() for r in rows})
    start = min(int(r['observed_at']) for r in rows)
    mon.execute(
        "insert into sync_episodes (token_key, episode_start, announced_json, symbol, sent_at) "
        "values (?,?,?,?,?)",
        (key, start, json.dumps(announced, ensure_ascii=False),
         str(popup.get('symbol') or ''), int(row['activity_at'])),
    )
    print('回填 %-40s 车上 %d 人  symbol=%s' % (key[:40], len(announced), popup.get('symbol')))
mon.commit()
print('sync_episodes 现有行数：', mon.execute('select count(*) c from sync_episodes').fetchone()['c'])
mon.close()
