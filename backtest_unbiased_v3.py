# -*- coding: utf-8 -*-
"""
无偏回测 v3（最严格口径）：
正样本 = 后来上币安热榜的币；负样本 = 同时期从未上热榜的币。
两者都用「首次发现(first_seen_at)那一刻」的快照字段，检验早期信号能否预测"后来上热榜"。
这模拟的是：扫链刚发现一个币时，凭当时能看到的字段，能不能预判它会火。
"""
import sqlite3, json, datetime, random

DB = '.runtime-cache/_bt/ce.db'
conn = sqlite3.connect(DB)
conn.execute('PRAGMA busy_timeout=60000')
cur = conn.cursor()

# 热榜答案集
bwh = json.load(open('.runtime-cache/binance_wallet_4h_structure_history.json', encoding='utf-8'))
CHAIN_MAP = {'56':'bsc', '4663':'robinhood', 'CT_501':'solana', '8453':'base'}
hot_ca = {}
for it in bwh.get('items') or []:
    ca = (it.get('contractAddress') or '').lower().strip()
    if ca:
        hot_ca[ca] = {'symbol': str(it.get('symbol') or '').upper(),
                      'chain': CHAIN_MAP.get(it.get('chain'), it.get('chain')),
                      'firstHotAt': it.get('firstSeenAt')}

# 候选 -> id
cur.execute("SELECT id, contract_address, first_seen_at FROM onchain_research_candidates "
            "WHERE contract_address IS NOT NULL AND contract_address != ''")
ca2id = {}
ca2fs = {}
for cid, ca, fs in cur.fetchall():
    ca = ca.lower().strip()
    ca2id[ca] = cid
    ca2fs[ca] = fs

def first_snapshot_metrics(cid):
    """取该候选最早一条快照的 metrics（= 首次发现时点的状态）"""
    cur.execute("SELECT metrics_json FROM onchain_research_snapshots "
                "WHERE candidate_id=? ORDER BY observed_at ASC LIMIT 1", (cid,))
    r = cur.fetchone()
    if not r or not r[0]:
        return None
    return json.loads(r[0])

# 正样本：上热榜币，取首次发现时点快照
pos = []
for ca in hot_ca:
    cid = ca2id.get(ca)
    if not cid:
        continue
    m = first_snapshot_metrics(cid)
    if m is None:
        continue
    pos.append({'symbol': hot_ca[ca]['symbol'], 'chain': hot_ca[ca]['chain'],
                'liq': m.get('liquidityUsd', 0) or 0,
                'vol6': m.get('volumeH6Usd', 0) or 0,
                'vol24': m.get('volumeH24Usd', 0) or 0,
                'buys5': m.get('buysM5', 0) or 0,
                'tx1': m.get('transactionsH1', 0) or 0,
                'buyers1': m.get('buyersH1', 0) or 0})

# 负样本：从未上热榜的候选，取首次发现时点快照（随机抽样对齐数量）
# 注意：ca2id 的 key 是 contract address，value 才是 candidate id
non_hot = [cid for ca, cid in ca2id.items() if ca not in hot_ca]
random.seed(42)
neg_sample = random.sample(non_hot, min(5000, len(non_hot)))
neg = []
for cid in neg_sample:
    m = first_snapshot_metrics(cid)
    if m is None:
        continue
    neg.append({'liq': m.get('liquidityUsd', 0) or 0,
                'vol6': m.get('volumeH6Usd', 0) or 0,
                'vol24': m.get('volumeH24Usd', 0) or 0,
                'buys5': m.get('buysM5', 0) or 0,
                'tx1': m.get('transactionsH1', 0) or 0,
                'buyers1': m.get('buyersH1', 0) or 0})

print(f'正样本(后来上热榜, 有首次发现快照): {len(pos)}')
print(f'负样本(从未上热榜, 有首次发现快照): {len(neg)}')
print()

# 基线：上热榜概率 = 正样本 / (正+负)
base = len(pos) / (len(pos) + len(neg)) * 100
print(f'基线(随机挑一个币上热榜概率): {base:.2f}%  (注意: 负样本是抽样，非全量，基线仅作相对参考)')
print()

def report(signal_name, getter):
    print(f'=== 早期信号「{signal_name}」的预测力（首次发现时点）===')
    pos_vals = sorted(getter(p) for p in pos)
    neg_vals = sorted(getter(n) for n in neg)
    # 分位数
    thresholds = [0, 10000, 50000, 100000, 500000, 1000000]
    for thr in thresholds:
        ph = sum(1 for v in pos_vals if v >= thr)
        nh = sum(1 for v in neg_vals if v >= thr)
        pr = ph/len(pos)*100 if pos else 0
        nr = nh/len(neg)*100 if neg else 0
        lift = pr - nr
        print(f'  首次发现时 {signal_name}>={thr:>8}: 上热榜组 {ph:>3}/{len(pos)}={pr:5.1f}% | 未上热榜组 {nh:>4}/{len(neg)}={nr:5.1f}% | 差 {lift:+.1f}pp')
    print()

report('流动性($)', lambda x: x['liq'])
report('6h成交额($)', lambda x: x['vol6'])
report('24h成交额($)', lambda x: x['vol24'])
report('1h交易笔数', lambda x: x['tx1'])
report('1h买家数', lambda x: x['buyers1'])
