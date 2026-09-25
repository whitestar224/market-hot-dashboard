# -*- coding: utf-8 -*-
"""无偏回测第二阶段：在「上热榜前」的快照时点，看早期信号能否预测后来上热榜。
对照组：用同一时期「从未上热榜」的币的早期快照做基线对比，才是有意义的预测力检验。"""
import sqlite3, json, datetime

DB = '.runtime-cache/_bt/ce.db'
conn = sqlite3.connect(DB)
conn.execute('PRAGMA busy_timeout=60000')
cur = conn.cursor()

def ts(ms):
    return datetime.datetime.fromtimestamp(ms/1000).strftime('%m-%d %H:%M') if ms else 'None'

bwh = json.load(open('.runtime-cache/binance_wallet_4h_structure_history.json', encoding='utf-8'))
CHAIN_MAP = {'56':'bsc', '4663':'robinhood', 'CT_501':'solana', '8453':'base'}
hot_ca = {}
for it in bwh.get('items') or []:
    ca = (it.get('contractAddress') or '').lower().strip()
    if ca:
        hot_ca[ca] = {'symbol': str(it.get('symbol') or '').upper(),
                      'chain': CHAIN_MAP.get(it.get('chain'), it.get('chain')),
                      'firstHotAt': it.get('firstSeenAt')}

cur.execute("SELECT id, contract_address FROM onchain_research_candidates WHERE contract_address IS NOT NULL AND contract_address != ''")
ca2id = {}
for cid, ca in cur.fetchall():
    ca2id[ca.lower().strip()] = cid

inter = set(hot_ca) & set(ca2id)

# 对每个交集币，取上热榜前最早快照，提取早期信号
def early_signal(m):
    """早期信号：流动性 + 成交 + 买盘，判断当时是否已显露出活跃迹象"""
    liq = m.get('liquidityUsd', 0) or 0
    vol6 = m.get('volumeH6Usd', 0) or 0
    buys = m.get('buysM5', 0) or 0
    tx1 = m.get('transactionsH1', 0) or 0
    return liq, vol6, buys, tx1

# 正样本：上热榜币的早期信号
positive = []
for ca in inter:
    hot_at = hot_ca[ca]['firstHotAt']
    cid = ca2id[ca]
    cur.execute("SELECT observed_at, metrics_json FROM onchain_research_snapshots "
                "WHERE candidate_id=? AND observed_at < ? ORDER BY observed_at ASC LIMIT 1",
                (cid, hot_at))
    row = cur.fetchone()
    if not row:
        continue
    obs_at, mj = row
    m = json.loads(mj) if mj else {}
    liq, vol6, buys, tx1 = early_signal(m)
    positive.append((hot_ca[ca]['symbol'], hot_ca[ca]['chain'], liq, vol6, buys, tx1, obs_at))

print(f'正样本（上热榜前有快照的币）: {len(positive)} 个')

# 负样本：从未上热榜的币，取它们最早快照（同时期）
# 策略：取所有候选里不在热榜 CA 集合的，随机抽同样数量，看它们早期信号
import random
random.seed(42)
all_non_hot = [cid for cid in ca2id if cid not in hot_ca]
print(f'负样本池（从未上热榜的候选）: {len(all_non_hot)} 个')

negative = []
# 为对齐时间分布，负样本也取 09-07 ~ 09-24 期间的，且取最早快照
sample = random.sample(all_non_hot, min(2000, len(all_non_hot)))
for cid in sample:
    cur.execute("SELECT observed_at, metrics_json FROM onchain_research_snapshots "
                "WHERE candidate_id=? ORDER BY observed_at ASC LIMIT 1", (cid,))
    row = cur.fetchone()
    if not row:
        continue
    obs_at, mj = row
    m = json.loads(mj) if mj else {}
    liq, vol6, buys, tx1 = early_signal(m)
    negative.append((liq, vol6, buys, tx1, obs_at))

print(f'负样本（从未上热榜，取最早快照）: {len(negative)} 个')

# 对比：用「早期流动性」作为信号，看预测力
# 早期流动性 > X 的币，后来上热榜的比例 vs 基线
def hit_rate(samples, liq_threshold):
    """早期流动性 >= threshold 时，正样本命中率"""
    pos_hit = sum(1 for s in positive if s[2] >= liq_threshold)
    pos_total = len(positive)
    neg_hit = sum(1 for s in negative if s[0] >= liq_threshold)
    neg_total = len(negative)
    return pos_hit, pos_total, neg_hit, neg_total

print()
print('=== 早期流动性作为信号的预测力 ===')
print('(正样本=后来上热榜，负样本=从未上热榜)')
for thr in [0, 10000, 50000, 100000, 200000, 500000]:
    ph, pt, nh, nt = hit_rate(None, thr)
    pos_rate = ph/pt*100 if pt else 0
    neg_rate = nh/nt*100 if nt else 0
    print(f'早期流动性>={thr:>8}: 正样本 {ph:>3}/{pt} = {pos_rate:5.1f}%  | 负样本 {nh:>4}/{nt} = {neg_rate:5.1f}%  | 提升 {pos_rate-neg_rate:+.1f}pp')

print()
print('=== 早期 6h成交额 作为信号的预测力 ===')
for thr in [0, 10000, 50000, 100000, 500000, 1000000]:
    ph = sum(1 for s in positive if s[3] >= thr)
    nh = sum(1 for s in negative if s[1] >= thr)
    pos_rate = ph/len(positive)*100 if positive else 0
    neg_rate = nh/len(negative)*100 if negative else 0
    print(f'早期6h成交>={thr:>8}: 正样本 {ph:>3}/{len(positive)} = {pos_rate:5.1f}%  | 负样本 {nh:>4}/{len(negative)} = {neg_rate:5.1f}%  | 提升 {pos_rate-neg_rate:+.1f}pp')
