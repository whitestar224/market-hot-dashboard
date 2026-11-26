# -*- coding: utf-8 -*-
"""
DeepSeek 联网投研综合评分器（v3）
把联网确认的热点叙事 + 硬信号 + 风险 融合，对全部 promising 去重币做综合排序。
年龄不作为过滤条件（用户明确要求）。
"""
import json
from collections import Counter

SRC = 'deliverables/trench-board-promising-history-2026-09-24.json'
OUT_JSON = 'deliverables/online-research-ranking-2026-09-24.json'

d = json.load(open(SRC, encoding='utf-8'))
rows = d['promising']

# 去重 symbol+chain
seen = set(); uniq = []
for r in rows:
    k = (r.get('symbol'), r.get('chain'))
    if k in seen:
        continue
    seen.add(k)
    uniq.append(r)

# ============ 联网确认的热点库（2026-09-24） ============
# symbol(大写) -> (叙事主线, 热度权重, 说明)
HOTSPOT = {
    # Robinhood Chain 股票代币化主线
    'PONS': ('Robinhood股票代币化', 5, 'Robinhood链龙头，市值$4.7亿，股票代币化核心'),
    'AI': ('AI+股票配对', 5, 'Artificial Inu，AI与NVDA配对龙头，市值$3.3亿'),
    'CASHCAT': ('Robinhood生态', 4, 'Robinhood链早期memecoin，市值$2亿'),
    'BONER': ('Robinhood生态', 3, 'Robinhood链memecoin，市值$5070万'),
    'SHROOM': ('股票流动性网络', 5, 'MUSHROOM，meme与美股代币配对做市+回购销毁，7日+240%'),
    'MUSEBOOK': ('Muse生态', 4, 'Muse AI概念，musebook，扎克伯格Muse生态'),
    'AGRIPPA': ('名人AI', 5, '扎克伯格Muse AI助手命名Agrippa，扎克伯格关注账号后涨30x'),
    'NAUTILO': ('AI agent', 4, '开源AI协作平台Genie，OG发币'),
    'MUSEGRAM': ('Robinhood生态', 3, 'robinhood链OG项目'),
    'MICRODUCK': ('Robinhood生态', 3, 'Robinhood链memecoin，24h+40%'),
    'NVDA': ('股票代币', 4, '英伟达股票代币，链上AUM第一'),
    'SPY': ('股票代币', 4, '标普500指数代币'),
    'SPX': ('股票代币', 3, '标普500指数代币'),
    'ZFORGE': ('Robinhood生态', 3, 'ZCFORGE质押叙事'),
    # BSC 股票 meme（Flap/Four.meme/Genius）
    '牛马': ('中文打工meme', 5, 'NIUMA，反996俚语，配对BABA，中文社区爆款'),
    'NIUMA': ('中文打工meme', 5, '牛马，反996俚语，配对BABA'),
    '牛来': ('中文meme', 4, '牛来牛来，牛市来了谐音，市值$1.18亿'),
    'MARSCOIN': ('股票对标', 5, '火星/SpaceX叙事，配对SPCXB，币安已上市，287倍案例'),
    'BUILD': ('股票对标', 4, '配对BNC4/CEA Industries，交易税累积BNC'),
    'BEN': ('股票对标', 4, '配对QQQB纳指ETF，交易税分红机制'),
    '4STOCK': ('股票对标', 3, 'Four.meme股票代币'),
    'BNC4': ('股票对标', 3, 'CZ"IPO上链"后Four.meme首发，追踪CEA Industries'),
    'BNCB': ('股票对标', 3, 'BSC股票代币'),
    'QQOB': ('股票对标', 3, 'QQQB纳指ETF对标'),
    'QQQB': ('股票对标', 3, '纳指100ETF代币'),
    'CNPY': ('BSC生态', 3, 'BNB Chain热门，日成交$9493万'),
    '果蝇': ('CZ点名', 5, '永生果蝇，CZ发推点名后828倍'),
    '永生果蝇': ('CZ点名', 5, 'CZ发推"永生果蝇很酷"后暴涨828倍'),
    '龙虾': ('AI中文meme', 4, 'OpenClaw红色龙虾，HTX周涨138%'),
    '天才': ('genius.fun', 4, 'Genius Foundation平台，CZ关联'),
    '中国人能飞': ('中文meme', 3, '中文社区meme，日成交$1.9M'),
    # Solana 股票代币化
    'ZEC': ('Solana股票代币化', 4, 'Zcash命名，Solana链股票代币化龙头，日成交$5000万+'),
    'STONK': ('StonkFun平台', 4, 'StonkFun平台代币，Solana龙头'),
    'PAID': ('Solana热点', 3, 'Solana链，曾+21873%，30个内部账户警示'),
    'SI': ('特朗普言论', 4, 'Super Inu，特朗普"super intelligence"言论后+300%'),
    'SUPERIOR': ('特朗普言论', 3, 'Superior Inu'),
    'JEANPHIL': ('Solana热点', 3, 'Jean Phil，已上MEXC'),
    'USELESS': ('Solana热点', 3, 'USELESS COIN，市值$2.26亿'),
    'CATE': ('Solana热点', 3, 'Catecoin，币安涨幅榜'),
    # 政治/电影
    'TRUMAN': ('电影叙事', 4, '楚门的世界AI直播，已上KCEX现货'),
    # 蹭名仿盘（负向，识别用）
    'XMR': ('蹭名仿盘', -5, '蹭Monero门罗币名，rug0.07'),
    'NEAR': ('蹭名仿盘', -5, '蹭NEAR公链名，rug0.13'),
    'ELON': ('蹭名仿盘', -5, '蹭马斯克名，rug0.10'),
    'SOL': ('蹭名仿盘', -5, '蹭Solana公链名，rug0.16'),
    'BTC': ('蹭名仿盘', -5, '蹭比特币名'),
    'ETH': ('蹭名仿盘', -5, '蹭以太坊名'),
    'DOGE': ('蹭名仿盘', -5, '蹭狗狗币名'),
    'XRP': ('蹭名仿盘', -5, '蹭瑞波名'),
    'BNB': ('蹭名仿盘', -5, '蹭BNB名'),
}

def is_chinese(s):
    return any('\u4e00' <= c <= '\u9fff' for c in (s or ''))

def classify(r):
    """返回 (主线标签, 热点权重, 说明)"""
    sym = str(r.get('symbol') or '')
    name = str(r.get('name') or '')
    chain = r.get('chain', '')
    symu = sym.upper()
    # 精确命中热点库
    for key in (symu, sym, name.upper(), name):
        if key in HOTSPOT:
            return HOTSPOT[key]
    # 蹭名判断：solana 链且 symbol 是知名公链/币名
    famous = {'XMR','NEAR','SOL','ELON','BTC','ETH','DOGE','XRP','ADA','DOT','LINK','UNI','LTC','BCH','ETC','TRX','SHIB','PEPE','WIF','BONK','TURBO','PNUT','MUBARAK'}
    if chain == 'solana' and symu in famous:
        return ('蹭名仿盘', -5, f'solana链蹭{symu}名')
    # 股票代币化（robinhood 链默认主线）
    if chain == 'robinhood':
        return ('Robinhood股票代币化', 2, 'robinhood链股票代币化生态')
    # 中文 meme
    if is_chinese(sym) or is_chinese(name):
        return ('中文meme', 2, '中文社区meme')
    # BSC 股票对标（名称含 stock/bnb/stock 关键字）
    nl = name.lower()
    if chain == 'bsc' and any(k in nl for k in ('stock', 'standard', 'square', 'bnb', 'binance')):
        return ('股票对标', 2, 'BSC股票对标叙事')
    return ('待验证', 0, '叙事未识别')

def score(r):
    """综合评分 = 硬信号 + 热点加成 + 风险扣分（年龄不过滤）"""
    sm = r.get('smartMoneyHolders', 0) or 0
    kol = r.get('kolHolders', 0) or 0
    liq = r.get('liquidityUsd', 0) or 0
    vol = r.get('volumeH24Usd', 0) or 0
    og = bool(r.get('isOg'))
    rug = r.get('rugRatio', 0) or 0
    top10 = r.get('top10Percent', 0) or 0
    holders = r.get('holders', 0) or 0
    ageh = (r.get('ageMinutes', 0) or 0) / 60

    label, weight, note = classify(r)
    s = 0.0

    # 硬信号
    if sm >= 100: s += 30
    elif sm >= 50: s += 25
    elif sm >= 20: s += 18
    elif sm >= 10: s += 12
    elif sm >= 5: s += 7
    elif sm >= 1: s += 3

    if kol >= 30: s += 12
    elif kol >= 10: s += 7
    elif kol >= 3: s += 3

    if og: s += 7

    if liq >= 500000: s += 10
    elif liq >= 200000: s += 7
    elif liq >= 100000: s += 4
    elif liq >= 50000: s += 2

    if vol >= 1000000: s += 6
    elif vol >= 500000: s += 3

    if 5 <= top10 <= 25: s += 3

    # 热点加成（联网叙事）
    s += weight * 3

    # 风险扣分
    if rug > 0.2: s -= 22
    elif rug > 0.1: s -= 14
    elif rug > 0.05: s -= 7
    if top10 > 40: s -= 6
    if sm == 0 and kol == 0 and not og and weight <= 0: s -= 6  # 无任何背书且无热点
    if label == '蹭名仿盘': s -= 15

    return s, label, weight, note

results = []
for r in uniq:
    s, label, weight, note = score(r)
    results.append({
        'symbol': r.get('symbol'), 'name': r.get('name'), 'chain': r.get('chain'),
        'candidateType': r.get('candidateType'), 'isOg': bool(r.get('isOg')),
        'smartMoneyHolders': r.get('smartMoneyHolders'), 'kolHolders': r.get('kolHolders'),
        'liquidityUsd': r.get('liquidityUsd'), 'marketCapUsd': r.get('marketCapUsd'),
        'volumeH24Usd': r.get('volumeH24Usd'), 'holders': r.get('holders'),
        'top10Percent': r.get('top10Percent'), 'rugRatio': r.get('rugRatio'),
        'maxTax': r.get('maxTax'), 'ageHours': round((r.get('ageMinutes',0) or 0)/60, 1),
        'narrative': label, 'hotWeight': weight, 'narrativeNote': note,
        'redFlags': r.get('redFlags'), 'signals': r.get('signals'),
        'score': round(s, 1),
    })

results.sort(key=lambda x: -x['score'])
for i, x in enumerate(results):
    x['rank'] = i + 1

# 分档
def grade(s):
    if s >= 45: return 'S'
    if s >= 30: return 'A'
    if s >= 15: return 'B'
    return 'C'
for x in results:
    x['grade'] = grade(x['score'])

out = {
    'generatedAt': '2026-09-24T21:40',
    'method': '联网热点叙事 + 硬信号 + 风险 综合，年龄不过滤',
    'total': len(results),
    'gradeDist': dict(Counter(x['grade'] for x in results)),
    'narrativeDist': dict(Counter(x['narrative'] for x in results)),
    'ranking': results,
}
json.dump(out, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False)

print('总去重币:', len(results))
print('分档:', out['gradeDist'])
print('叙事分布:', out['narrativeDist'])
print()
print('=== S级 名单 ===')
for x in results:
    if x['grade'] == 'S':
        print(f"#{x['rank']:<3} {x['symbol']:<14} {x['chain']:<9} 分{x['score']:.0f} sm={x['smartMoneyHolders']:>3} kol={x['kolHolders']:>3} rug={x['rugRatio']:.2f} | {x['narrative']}")
