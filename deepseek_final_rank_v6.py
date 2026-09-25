# -*- coding: utf-8 -*-
"""
DeepSeek 最终排名 v6 —— 严格按《链上投研体系 V5.0》炒作潜力账本
不再拼单一总分。按投研体系分列：
  1. 最小注意力单元类型（12类）
  2. 情绪共鸣类型（8种）
  3. 映射直接度（币名/符号能否"一眼知道炒什么"）
  4. 热点强度（是否踩中当下主线，联网确认）
  5. 资金承接（聪明钱/KOL/流动性/成交）
  6. 竞争格局（同题材竞争）
输出「研究优先级」排序（非必涨分），并明确标注证据强度。
"""
import json
from collections import Counter

SRC = 'deliverables/online-research-ranking-v4-2026-09-24.json'
OUT = 'deliverables/deepseek-final-ranking-v6-2026-09-24.json'

d = json.load(open(SRC, encoding='utf-8'))
ranking = d['ranking']
kept = [x for x in ranking if not x.get('exclude')]

# ===== 投研体系 V5.0：最小注意力单元（12类）+ 情绪共鸣（8种）=====
# 每个「叙事标签」→ 它通常对应哪种最小注意力单元 + 哪种情绪共鸣 + 映射直接度
# 映射直接度：币名/符号让市场"一眼知道在炒什么"的程度（高/中/低）

NARRATIVE_FRAMEWORK = {
    # (最小注意力单元类型, 情绪共鸣类型, 映射直接度)
    'Robinhood龙头meme': ('名字或称呼', '身份与归属', '高'),
    '股票流动性网络': ('玩法规则', '参与与共同记忆', '中'),
    'AI agent': ('技术演示', '荒诞与反差', '中'),
    '名人AI': ('人物动作', '可爱与共情', '高'),
    'CZ点名': ('人物动作', '参与与共同记忆', '高'),
    'genius.fun': ('玩法规则', '愿望与自嘲', '中'),
    '电影叙事': ('短话或口头禅', '怀旧与重释', '高'),
    '预测市场': ('玩法规则', '参与与共同记忆', '中'),
    'Robinhood股票代币化': ('真实发行动作', '身份与归属', '中'),
    'Robinhood生态': ('真实发行动作', '身份与归属', '中'),
    'Muse生态': ('人物动作', '可爱与共情', '中'),
    '股票对标': ('真实发行动作', '愿望与自嘲', '中'),
    '金融/股票对标': ('真实发行动作', '愿望与自嘲', '中'),
    'AI+股票配对': ('玩法规则', '荒诞与反差', '中'),
    '中文打工meme': ('身份符号', '愿望与自嘲', '高'),
    '中文meme': ('名字或称呼', '身份与归属', '中'),
    '动物meme': ('名字或称呼', '可爱与共情', '低'),
    'AI/智能体': ('技术演示', '荒诞与反差', '低'),
    '社区/文化梗': ('短话或口头禅', '参与与共同记忆', '低'),
    '科学/物理梗': ('技术演示', '荒诞与反差', '中'),
    '游戏/电竞': ('名字或称呼', '怀旧与重释', '中'),
    '特朗普言论': ('人物动作', '争议与立场', '高'),
    '政治': ('人物动作', '争议与立场', '中'),
    '电影/影视': ('短话或口头禅', '怀旧与重释', '中'),
    'Solana热点': ('市场异动', '参与与共同记忆', '中'),
    '一般': ('无', '无', '低'),
}

# 映射直接度 → 权重
MAP_WEIGHT = {'高': 3, '中': 2, '低': 1, '无': 0}

def score_v6(x):
    """按投研体系炒作潜力账本分列打分，输出结构化结果"""
    sm = x.get('smartMoneyHolders', 0) or 0
    kol = x.get('kolHolders', 0) or 0
    liq = x.get('liquidityUsd', 0) or 0
    vol = x.get('volumeH24Usd', 0) or 0
    og = x.get('isOg')
    nar = x.get('narrative', '一般')

    unit_type, resonance, map_level = NARRATIVE_FRAMEWORK.get(nar, ('无', '无', '低'))

    # 硬信号（资金承接）
    funds = 0
    if sm >= 100: funds += 30
    elif sm >= 50: funds += 25
    elif sm >= 20: funds += 18
    elif sm >= 10: funds += 12
    elif sm >= 5: funds += 7
    elif sm >= 1: funds += 3
    if kol >= 30: funds += 12
    elif kol >= 10: funds += 7
    elif kol >= 3: funds += 3
    if og: funds += 7
    if liq >= 500000: funds += 10
    elif liq >= 200000: funds += 7
    elif liq >= 100000: funds += 4
    elif liq >= 50000: funds += 2
    if vol >= 1000000: funds += 6
    elif vol >= 500000: funds += 3

    # 叙事质量分（映射直接度 + 情绪共鸣存在性）
    narrative_quality = MAP_WEIGHT.get(map_level, 0) * 3  # 0~9
    if resonance != '无':
        narrative_quality += 3  # 有明确情绪共鸣 +3
    # 热点强度：踩中当下主线（Robinhood股票代币化/AI/CZ/名人/政治/中文打工）
    hotline = nar in ('Robinhood龙头meme','股票流动性网络','AI agent','名人AI','CZ点名','genius.fun',
                      '电影叙事','Robinhood股票代币化','Robinhood生态','Muse生态','AI+股票配对',
                      '中文打工meme','特朗普言论','Solana热点','股票对标','金融/股票对标','预测市场')
    if hotline:
        narrative_quality += 3

    return {
        'unitType': unit_type,
        'resonance': resonance,
        'mapLevel': map_level,
        'fundScore': funds,
        'narrativeScore': narrative_quality,
    }

for x in kept:
    r = score_v6(x)
    x.update(r)
    # 研究优先级 = 资金承接 + 叙事质量（分列记录，不合并为"必涨分"）
    x['priority'] = x['fundScore'] + x['narrativeScore']

kept.sort(key=lambda x: -x['priority'])
for i, x in enumerate(kept):
    x['rank'] = i + 1

def grade(p):
    if p >= 55: return 'S'
    if p >= 42: return 'A'
    if p >= 28: return 'B'
    return 'C'
for x in kept:
    x['grade'] = grade(x['priority'])

out = {
    'generatedAt': '2026-09-24T22:05',
    'method': '按《链上投研体系 V5.0》炒作潜力账本：最小注意力单元+情绪共鸣+映射直接度+热点强度 分列，资金承接单独算，不拼必涨总分',
    'framework': '链上投研体系 V5.0 (2026-09-24 11:55)',
    'totalKept': len(kept),
    'gradeDist': dict(Counter(x['grade'] for x in kept)),
    'unitDist': dict(Counter(x['unitType'] for x in kept)),
    'resonanceDist': dict(Counter(x['resonance'] for x in kept)),
    'ranking': kept,
}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)

print('保留:', len(kept))
print('分档:', out['gradeDist'])
print('最小注意力单元分布:', out['unitDist'])
print('情绪共鸣分布:', out['resonanceDist'])
print()
print('=== S级 最终名单（按投研体系）===')
for x in kept:
    if x['grade'] == 'S':
        print(f"#{x['rank']:<3} {x['symbol']:<16} {x['chain']:<9} 优先级{x['priority']:.0f} 资金{x['fundScore']:.0f} 叙事{x['narrativeScore']:.0f} | 单元:{x['unitType']} 共鸣:{x['resonance']} 映射:{x['mapLevel']} | {x['narrative']}")
