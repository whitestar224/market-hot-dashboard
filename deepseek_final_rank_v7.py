# -*- coding: utf-8 -*-
"""
DeepSeek 最终排名 v7 —— 严格按《链上投研体系 V5.0》资产类别分流
核心修正：项目/协议/机制类 与 纯meme类 分两条评分路径，不能混。

投研体系 V5.0 关键规则：
  - 优先权重：Liquidity / Incentive / PONS > Meme Firstness
  - 四维新颖度：Asset / Protocol / Mechanism / Gameplay Novelty 各 1-10
  - 炒作潜力账本（meme 类）：热点强度、映射直接度、传播性、情绪共鸣、资金承接、竞争格局
"""
import json
from collections import Counter

SRC = 'deliverables/online-research-ranking-v4-2026-09-24.json'
OUT = 'deliverables/deepseek-final-ranking-v7-2026-09-24.json'

d = json.load(open(SRC, encoding='utf-8'))
ranking = d['ranking']
kept = [x for x in ranking if not x.get('exclude')]

# ===== 资产类别分流 =====
# 项目/协议/机制类（有真实产品/平台/协议/经济机制，走 Novelty+Liquidity+Incentive 路径）
# 纯 meme 类（走 最小注意力单元+情绪共鸣+映射直接度 路径）

PROJECT_NARRATIVES = {
    '预测市场',      # CONVICTION - BSC预测市场
    '股票流动性网络', # SHROOM - 配对做市+回购销毁
    '股票对标',      # BEN/BUILD - 交易税分红机制
    '金融/股票对标',
    'AI agent',     # NAUTILO - AI协作平台
    'genius.fun',   # 天才 - 平台
    'Robinhood生态', # MUSEGRAM/URANUS - robinhood链项目
    'Robinhood股票代币化',
    'AI+股票配对',
    'Muse生态',
    'Robinhood龙头meme',  # FRONG - 实为meme，但已上CEX有产品支撑，归项目类
    'CZ点名',       # NECTAR - 果蝇脑回购销毁机制
}

# meme 类叙事
MEME_NARRATIVES = {
    '名人AI', '电影叙事', '中文meme', '中文打工meme', '动物meme', '社区/文化梗',
    '特朗普言论', '政治', '电影/影视', '科学/物理梗', '游戏/电竞', 'Solana热点', '一般',
}

# ===== meme 类：最小注意力单元 + 情绪共鸣 + 映射直接度 =====
MEME_FRAMEWORK = {
    'Robinhood龙头meme': ('名字或称呼', '身份与归属', '高'),
    'AI agent': ('技术演示', '荒诞与反差', '中'),
    '名人AI': ('人物动作', '可爱与共情', '高'),
    'CZ点名': ('人物动作', '参与与共同记忆', '高'),
    'genius.fun': ('玩法规则', '愿望与自嘲', '中'),
    '电影叙事': ('短话或口头禅', '怀旧与重释', '高'),
    '预测市场': ('玩法规则', '参与与共同记忆', '中'),
    '中文打工meme': ('身份符号', '愿望与自嘲', '高'),
    '中文meme': ('名字或称呼', '身份与归属', '中'),
    '动物meme': ('名字或称呼', '可爱与共情', '低'),
    '社区/文化梗': ('短话或口头禅', '参与与共同记忆', '低'),
    '科学/物理梗': ('技术演示', '荒诞与反差', '中'),
    '游戏/电竞': ('名字或称呼', '怀旧与重释', '中'),
    '特朗普言论': ('人物动作', '争议与立场', '高'),
    '政治': ('人物动作', '争议与立场', '中'),
    '电影/影视': ('短话或口头禅', '怀旧与重释', '中'),
    'Solana热点': ('市场异动', '参与与共同记忆', '中'),
    '一般': ('无', '无', '低'),
}
MAP_WEIGHT = {'高': 3, '中': 2, '低': 1, '无': 0}

def classify_asset(x):
    nar = x.get('narrative', '一般')
    if nar in PROJECT_NARRATIVES:
        return 'project'
    return 'meme'

def score_v7(x):
    sm = x.get('smartMoneyHolders', 0) or 0
    kol = x.get('kolHolders', 0) or 0
    liq = x.get('liquidityUsd', 0) or 0
    vol = x.get('volumeH24Usd', 0) or 0
    og = x.get('isOg')
    nar = x.get('narrative', '一般')
    asset_class = classify_asset(x)

    # 资金承接（Liquidity + 买方），项目类与meme类都算，但项目类更看重流动性
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

    if asset_class == 'project':
        # 项目类：Liquidity + Incentive 优先（投研体系：Liquidity/Incentive/PONS > Meme Firstness）
        # 流动性权重放大（项目类核心是流动性深度）
        liquidity_score = 0
        if liq >= 500000: liquidity_score = 10
        elif liq >= 200000: liquidity_score = 8
        elif liq >= 100000: liquidity_score = 6
        elif liq >= 50000: liquidity_score = 3
        # 机制/激励（有交易税分红、回购销毁、配对做市等经济机制 → 加）
        mechanism = 0
        if nar in ('股票流动性网络', 'CZ点名', '股票对标', '金融/股票对标', 'AI+股票配对'):
            mechanism = 6  # 有明确经济机制
        elif nar in ('预测市场', 'genius.fun', 'AI agent', 'Robinhood生态', 'Muse生态'):
            mechanism = 4  # 有产品/平台
        elif nar == 'Robinhood龙头meme':
            mechanism = 3  # 已上CEX有产品支撑
        else:
            mechanism = 2
        quality = liquidity_score + mechanism
        return {
            'assetClass': '项目/协议',
            'qualityScore': quality,
            'liquidityScore': liquidity_score,
            'mechanismScore': mechanism,
            'unitType': '', 'resonance': '', 'mapLevel': '',
            'fundScore': funds,
        }
    else:
        # meme 类：最小注意力单元 + 情绪共鸣 + 映射直接度
        unit_type, resonance, map_level = MEME_FRAMEWORK.get(nar, ('无', '无', '低'))
        narrative_quality = MAP_WEIGHT.get(map_level, 0) * 3
        if resonance != '无':
            narrative_quality += 3
        hotline = nar in ('名人AI', 'CZ点名', '电影叙事', '中文打工meme', '特朗普言论', 'Solana热点', 'genius.fun')
        if hotline:
            narrative_quality += 3
        return {
            'assetClass': 'meme',
            'qualityScore': narrative_quality,
            'liquidityScore': 0, 'mechanismScore': 0,
            'unitType': unit_type, 'resonance': resonance, 'mapLevel': map_level,
            'fundScore': funds,
        }

for x in kept:
    r = score_v7(x)
    x.update(r)
    x['priority'] = x['fundScore'] + x['qualityScore']

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
    'generatedAt': '2026-09-24T22:10',
    'method': '按《链上投研体系 V5.0》资产类别分流：项目/协议类走 Liquidity+Incentive+机制新颖度，meme类走 最小注意力单元+情绪共鸣+映射直接度；资金承接单独算',
    'framework': '链上投研体系 V5.0 (2026-09-24 11:55)',
    'totalKept': len(kept),
    'gradeDist': dict(Counter(x['grade'] for x in kept)),
    'assetClassDist': dict(Counter(x['assetClass'] for x in kept)),
    'ranking': kept,
}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)

print('保留:', len(kept))
print('分档:', out['gradeDist'])
print('资产类别:', out['assetClassDist'])
print()
print('=== S级 最终名单（按投研体系 V5.0，资产类别分流）===')
for x in kept:
    if x['grade'] == 'S':
        u = f'单元:{x["unitType"]} 共鸣:{x["resonance"]} 映射:{x["mapLevel"]}' if x['assetClass']=='meme' else f'流动性:{x["liquidityScore"]} 机制:{x["mechanismScore"]}'
        print(f"#{x['rank']:<3} {x['symbol']:<16} {x['chain']:<9} 优先级{x['priority']:.0f} 资金{x['fundScore']:.0f} 质量{x['qualityScore']:.0f} | {x['assetClass']} | {u} | {x['narrative']}")
