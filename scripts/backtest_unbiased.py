# -*- coding: utf-8 -*-
"""无偏回测：用 onchain_research_snapshots 的历史分时快照，在每个币「首次上热榜之前」的时点，
用当时的早期字段判断能否发现它，消除前视偏差。"""
import sqlite3, json, datetime

DB = '.runtime-cache/_bt/ce.db'
conn = sqlite3.connect(DB)
conn.execute('PRAGMA busy_timeout=60000')
cur = conn.cursor()

def ts(ms):
    return datetime.datetime.fromtimestamp(ms/1000).strftime('%m-%d %H:%M') if ms else 'None'

# 1. 热榜币（未来答案）
bwh = json.load(open('.runtime-cache/binance_wallet_4h_structure_history.json', encoding='utf-8'))
CHAIN_MAP = {'56':'bsc', '4663':'robinhood', 'CT_501':'solana', '8453':'base'}
hot = {}
for it in bwh.get('items') or []:
    ca = (it.get('contractAddress') or '').lower().strip()
    if ca:
        hot[ca] = {
            'symbol': str(it.get('symbol') or '').upper(),
            'chain': CHAIN_MAP.get(it.get('chain'), it.get('chain')),
            'firstHotAt': it.get('firstSeenAt'),
        }
print('热榜币(有CA):', len(hot))

# 2. 数据库候选
cur.execute("SELECT contract_address, symbol, network, first_seen_at, pool_created_at "
            "FROM onchain_research_candidates WHERE contract_address IS NOT NULL AND contract_address != ''")
cand = {}
for ca, sym, net, fs, pc in cur.fetchall():
    ca = ca.lower().strip()
    cand[ca] = {'symbol': sym, 'network': net, 'firstSeenAt': fs, 'poolCreatedAt': pc}

inter = set(hot) & set(cand)
print('CA 精确交集:', len(inter))

# 3. 对每个交集币：取「上热榜之前」的快照
# 建 candidate_id 映射
cur.execute("SELECT id, contract_address FROM onchain_research_candidates WHERE contract_address IS NOT NULL AND contract_address != ''")
ca2id = {}
for cid, ca in cur.fetchall():
    ca2id[ca.lower().strip()] = cid

print('可映射到 id 的交集:', len(inter & set(ca2id)))
print()

# 逐币取事前快照
results = []
for ca in inter:
    h = hot[ca]
    c = cand.get(ca)
    cid = ca2id.get(ca)
    if not cid:
        continue
    hot_at = h['firstHotAt']
    # 取上热榜之前的快照（observed_at < firstHotAt），按时间排序
    cur.execute(
        "SELECT observed_at, metrics_json FROM onchain_research_snapshots "
        "WHERE candidate_id=? AND observed_at < ? ORDER BY observed_at ASC LIMIT 1",
        (cid, hot_at)
    )
    row = cur.fetchone()
    if not row:
        # 没有事前快照（可能上热榜太快，或首次记录晚于热榜）
        results.append((h['symbol'], h['chain'], hot_at, None, None))
        continue
    obs_at, mj = row
    m = json.loads(mj) if mj else {}
    results.append((h['symbol'], h['chain'], hot_at, obs_at, m))

# 4. 统计
print('=== 无偏回测结果（仅用上热榜前的最早快照）===')
print(f'交集 {len(inter)} 个，其中:')
with_prior = [r for r in results if r[3] is not None]
without_prior = [r for r in results if r[3] is None]
print(f'  有「上热榜前」快照: {len(with_prior)} 个')
print(f'  无「上热榜前」快照(记录晚于上热榜): {len(without_prior)} 个')
print()

# 看有事前快照的币，当时流动性/买盘信号如何
print('=== 有事前快照的币：上热榜前的早期流动性/成交 ===')
for sym, chain, hot_at, obs_at, m in sorted(with_prior, key=lambda r: r[3]):
    liq = m.get('liquidityUsd', 0) or 0
    vol6 = m.get('volumeH6Usd', 0) or 0
    buys = m.get('buysM5', 0) or 0
    print(f'{sym:<14} {chain:<9} 热榜前快照@{ts(obs_at)} 流动性=${liq:>10.0f} 6h成交=${vol6:>9.0f} 5m买盘={buys}')
