# -*- coding: utf-8 -*-
"""
DeepSeek 最终排名 v5
深查后的 1139 币（已过滤 rug/蹭名/真实资产），按「叙事热度 × 硬信号」最终排序，
输出 DeepSeek 觉得值得看的名单。
"""
import json
from collections import Counter

SRC = 'deliverables/online-research-ranking-v4-2026-09-24.json'
OUT = 'deliverables/deepseek-final-ranking-2026-09-24.json'

d = json.load(open(SRC, encoding='utf-8'))
ranking = d['ranking']

# 叙事热度权重（联网确认的热点主线，5=最强主线）
NAR_HEAT = {
    # 最强主线：Robinhood 股票代币化 + CZ/名人/AI agent
    'Robinhood股票代币化': 5, '股票流动性网络': 5, 'Robinhood龙头meme': 5,
    'Robinhood生态': 4, 'Muse生态': 4, '名人AI': 5, 'CZ点名': 5, 'genius.fun': 4,
    'AI agent': 4, 'AI+股票配对': 4,
    # BSC 股票对标主线
    '股票对标': 4, '金融/股票对标': 3, '预测市场': 3,
    # 中文社区
    '中文meme': 3, '中文打工meme': 3,
    # 中热度
    '电影叙事': 3, 'Solana热点': 3, 'AI/智能体': 2, '游戏/电竞': 2, '科学/物理梗': 2,
    '特朗普言论': 2, '政治': 2, '动物meme': 2, '电影/影视': 2,
    # 低热度
    '社区/文化梗': 1,
}

def final_score(x):
    """最终分 = 硬信号 + 叙事热度加成。年龄不过滤。"""
    sm = x.get('smartMoneyHolders', 0) or 0
    kol = x.get('kolHolders', 0) or 0
    liq = x.get('liquidityUsd', 0) or 0
    vol = x.get('volumeH24Usd', 0) or 0
    og = x.get('isOg')
    top10 = x.get('top10Percent', 0) or 0
    nar = x.get('narrative', '一般')
    heat = NAR_HEAT.get(nar, 0)

    s = 0.0
    # 硬信号（聪明钱是核心）
    if sm >= 100: s += 30
    elif sm >= 50: s += 25
    elif sm >= 20: s += 18
    elif sm >= 10: s += 12
    elif sm >= 5: s += 7
    elif sm >= 1: s += 3
    # KOL
    if kol >= 30: s += 12
    elif kol >= 10: s += 7
    elif kol >= 3: s += 3
    # OG
    if og: s += 7
    # 流动性
    if liq >= 500000: s += 10
    elif liq >= 200000: s += 7
    elif liq >= 100000: s += 4
    elif liq >= 50000: s += 2
    # 成交
    if vol >= 1000000: s += 6
    elif vol >= 500000: s += 3
    # holder 结构
    if 5 <= top10 <= 25: s += 3
    # 叙事热度（主线加成最高 +15）
    s += heat * 3
    return s

# 处理「待深查」：robinhood 链归入股票代币化生态，其余保留
for x in ranking:
    if x.get('exclude'):
        continue
    if x['narrative'] == '待深查':
        if x['chain'] == 'robinhood':
            x['narrative'] = 'Robinhood股票代币化'
            x['narrativeNote'] = 'robinhood链股票代币化生态（名字无关键词，按链归类）'
        else:
            x['narrative'] = '一般'
            x['narrativeNote'] = '有背书但叙事不明，需人工细看'

# 只对保留的（非排除）排序
kept = [x for x in ranking if not x.get('exclude')]
for x in kept:
    x['finalScore'] = round(final_score(x), 1)

kept.sort(key=lambda x: -x['finalScore'])
for i, x in enumerate(kept):
    x['rank'] = i + 1

def grade(s):
    if s >= 55: return 'S'
    if s >= 40: return 'A'
    if s >= 25: return 'B'
    return 'C'
for x in kept:
    x['grade'] = grade(x['finalScore'])

out = {
    'generatedAt': '2026-09-24T22:02',
    'method': '深查后：过滤 rug/蹭名/真实资产 → 叙事热度权重 × 硬信号 最终排名，年龄不过滤',
    'totalKept': len(kept),
    'gradeDist': dict(Counter(x['grade'] for x in kept)),
    'ranking': kept,
}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)

print('保留(非排除):', len(kept))
print('分档:', out['gradeDist'])
print()
print('=== DeepSeek 最终「值得看」S级 ===')
for x in kept:
    if x['grade'] == 'S':
        print(f"#{x['rank']:<3} {x['symbol']:<16} {x['chain']:<9} 分{x['finalScore']:.0f} sm={x['smartMoneyHolders']:>3} kol={x['kolHolders']:>3} og={x['isOg']} liq={x['liquidityUsd']:>9.0f} | {x['narrative']}")
