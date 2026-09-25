# -*- coding: utf-8 -*-
"""
DeepSeek 联网投研分类器 v4
核心：过滤蹭名仿盘/rug盘/真实资产(非战壕盘) → 逐个识别叙事 → 标出「值得深查」
年龄不过滤。名字/symbol 本身就是叙事第一信号。
"""
import json
from collections import Counter

SRC = 'deliverables/online-research-ranking-2026-09-24.json'
OUT = 'deliverables/online-research-ranking-v4-2026-09-24.json'

d = json.load(open(SRC, encoding='utf-8'))
ranking = d['ranking']  # 已是去重后的 1139 个

# ===== 蹭名名单（名人 / 知名币 / 公链 / 知名项目）=====
CELEB = {'TRUMP','MELANIA','ELON','MUSK','CZ','BIDEN','HARRIS','OBAMA','PUTIN','TYSON','ZUCK','ZUCKERBERG',
         'DONALD','MAGA','SBF','VITALIK','BUTERIN','GATES','BEZOS','COOK','NAKAMOTO','SATOSHI','SOROS','BUFFETT','MUSKELON'}
FAMOUS_COIN = {'BTC','ETH','SOL','XRP','DOGE','SHIB','PEPE','BONK','WIF','FLOKI','PENGU','USDC','USDT','DAI','BNB',
               'LINK','UNI','AAVE','DOT','ADA','LTC','BCH','TRX','TON','APT','SUI','ARB','OP','TIA','JUP','PYTH',
               'WLD','INJ','SEI','MKR','SNX','COMP','CRV','SUSHI','CAKE','NEIRO','MUBARAK','PNUT','TURBO','GIGA',
               'POPCAT','MOODENG','GOAT','BRETT','MOG','WOJAK','PONKE','BOME','SLERF','WEN','CHILLGUY','SPX','FART'}
CHAIN_NAME = {'SOLANA','ETHEREUM','NEAR','AVALANCHE','POLKADOT','CARDANO','COSMOS','ALGORAND','TEZOS','HEDERA'}
REAL_ASSET = {'MORPHO','AAVE','SHARPLINK','SBET','GAMESTOP','GME','NVIDIA','NVDA','SPDR','SPY','QQQ','COINBASE',
              'ONDO','WSTETH','WEETH','RETH','LIDO','EIGEN','VIRTUAL'}

# ===== 叙事关键词 =====
NAR_KEYWORDS = [
    ('AI/智能体', ['AI','AGENT','GENIUS','BRAIN','GPT','GROK','QWEN','INTELLIGENCE','NEURAL','ARCL','MUSE','AGI',
                   'PROCESSING','UNIT','ATTENTION','MINE','AUTOMATION','BOT','AGENTIC','JEV','LOOM','EINSTEIN']),
    ('游戏/电竞', ['WOW','GAME','MOONKIN','FORTNITE','NINJA','POKEMON','MINECRAFT','SONIC','MARIO','ZELDA','GTA',
                   'STEAM','EPIC','ROBLOX','PUBG','CLASH','GUILD','QUEST','ARCADE']),
    ('科学/物理梗', ['SCHRÖDINGER','SCHRODINGER','FIBONACCI','QUANTUM','ATOM','NEUTRON','PHOTON','RELATIVITY','LAB',
                     'FUND','SCIENCE','DNA','GENE','CELL','FRUIT','FLY','IMMORTAL','RESEARCH']),
    ('动物meme', ['CAT','DOGE','INU','SHIB','FROG','FROGE','RABBIT','SQUIRREL','DUCK','MONKEY','APE','KOALA','PANDA',
                  'BEAR','BULL','FOX','WOLF','DOG','FISH','BIRD','PENGUIN','KANGAROO','LEOPARD','TIGER','LION',
                  'ELEPHANT','HORSE','PIG','RAT','MOUSE','MOOSE','OTTER','SEAL','CAPYBARA','HAMSTER','GUINEA']),
    ('电影/影视', ['TRUMAN','BIGSHORT','SHORT','MATRIX','INCEPTION','HARRY','POTTER','STARWARS','MARVEL','DC','BATMAN',
                   'JOKER','AVATAR','JURASSIC','GODFATHER','BREAKING','SQUID','MOVIE','TICKET','FILM','SHOW']),
    ('政治', ['TRUMP','MAGA','DEMOCRAT','REPUBLIC','ELECTION','VOTE','PRESIDENT','SENATE','CONGRESS','KAMALA']),
    ('金融/股票对标', ['STONK','STOCK','DIVIDEND','DIVIDEND','INDEX','ETF','NASDAQ','WALLSTREET','TRADER','MARKET',
                       'CASH','PAY','BET','LOBBY','POLYMARKET','BOND','FUND','YIELD','VAULT','RESERVE','TREASURY',
                       'BANK','CAPITAL','FINANCE','PROTOCOL','PERP','LEVERAGE','FUTURES']),
    ('社区/文化梗', ['FOMO','MOON','PUMP','HODL','DEGEN','APE','FUD','WAGMI','NGMI','BASED','CHAD','DANK','MEME',
                     'RETARD','COPIUM','HOPIUM','BAG','RUG','REKT','ALPHA','SIGMA','GYATT','RIZZ']),
    ('公益/慈善', ['CHARITY','WELFARE','ANIMAL','RESCUE','SAVE','FOUNDATION','DONATE','HELP','CARE','KIND']),
    ('中文meme', []),  # 中文用单独判断
]

def is_chinese(s):
    return any('\u4e00' <= c <= '\u9fff' for c in (s or ''))

def classify_v4(x):
    """返回 (类别, 是否排除, 叙事标签, 说明)"""
    sym = str(x.get('symbol') or '')
    name = str(x.get('name') or '')
    chain = x.get('chain', '')
    symu = sym.upper()
    nameu = name.upper()
    rug = x.get('rugRatio', 0) or 0
    sm = x.get('smartMoneyHolders', 0) or 0
    kol = x.get('kolHolders', 0) or 0
    og = x.get('isOg')

    # 1) 蹭名仿盘：solana 链 symbol/name 撞知名币/名人/公链
    hit_celeb = symu in CELEB or nameu in CELEB
    hit_coin = symu in FAMOUS_COIN or nameu in FAMOUS_COIN
    hit_chain = symu in CHAIN_NAME or nameu in CHAIN_NAME
    if chain == 'solana' and (hit_celeb or hit_coin or hit_chain) and rug > 0.03:
        return ('蹭名仿盘', True, '蹭名仿盘', f'solana蹭{"名人" if hit_celeb else "币" if hit_coin else "公链"}名, rug{rug:.2f}')

    # 2) rug 盘：rug >= 0.05 一律硬过滤（rug 是安全底线，不能用聪明钱数豁免）
    if rug >= 0.05:
        return ('rug盘', True, 'rug盘', f'rug比例{rug:.2f}过高')

    # 3) 真实资产/项目（非战壕盘，是真实标的代币化）
    if chain == 'eth' and (hit_coin and ('ONDO' in nameu or 'TOKENIZED' in nameu or 'WRAPPED' in nameu or symu in ('CBBTC','NVDAON','SPYON','GME'))):
        return ('真实资产', True, '真实资产/项目', '真实股票/ETF代币化或DeFi协议，非战壕meme')
    if symu in ('MORPHO','EIGEN','LIDO','CBBTC') and chain == 'eth':
        return ('真实资产', True, '真实资产/项目', '真实DeFi协议，非战壕meme')

    # 4) 已知热点库命中（从 v3 继承的精确命中）
    HOTSPOT = {
        'PONS': 'Robinhood股票代币化', 'AI': 'AI+股票配对', 'CASHCAT': 'Robinhood生态',
        'SHROOM': '股票流动性网络', 'MUSEBOOK': 'Muse生态', 'AGRIPPA': '名人AI', 'NAUTILO': 'AI agent',
        'MUSEGRAM': 'Robinhood生态', 'MICRODUCK': 'Robinhood生态',
        '牛马': '中文打工meme', 'NIUMA': '中文打工meme', '牛来': '中文meme', 'MARSCOIN': '股票对标',
        'BUILD': '股票对标', 'BEN': '股票对标', '4STOCK': '股票对标', 'BNC4': '股票对标', 'BNCB': '股票对标',
        'QQOB': '股票对标', 'QQQB': '股票对标', 'CNPY': 'BSC生态', '果蝇': 'CZ点名', '永生果蝇': 'CZ点名',
        '龙虾': 'AI中文meme', '天才': 'genius.fun', '中国人能飞': '中文meme',
        'ZEC': 'Solana股票代币化', 'STONK': 'StonkFun平台', 'PAID': 'Solana热点', 'SI': '特朗普言论',
        'SUPERIOR': '特朗普言论', 'JEANPHIL': 'Solana热点', 'USELESS': 'Solana热点', 'CATE': 'Solana热点',
        'TRUMAN': '电影叙事',
        'FRONG': 'Robinhood龙头meme',
        'CONVICTION': '预测市场',
        'NECTAR': 'CZ点名',
        'JUGGERNAUT': 'Robinhood生态',
    }
    for key in (symu, sym, nameu, name):
        if key in HOTSPOT:
            return (HOTSPOT[key], False, HOTSPOT[key], '联网确认热点')

    # 5) 名字叙事关键词
    if is_chinese(sym) or is_chinese(name):
        return ('中文meme', False, '中文meme', '中文社区meme')
    for label, kws in NAR_KEYWORDS:
        if not kws:
            continue
        for kw in kws:
            if kw in nameu or kw in symu:
                return (label, False, label, f'名字命中「{kw}」')

    # 6) 有背书但叙事不明 → 值得深查
    if sm >= 20 or kol >= 10 or og:
        return ('待深查', False, '待深查', f'聪明钱{sm}/KOL{kol}但叙事不明，值得深查')

    # 7) 无叙事无背书 → 一般
    return ('一般', False, '一般', '无明确叙事')

results = []
for x in ranking:
    cat, exclude, label, note = classify_v4(x)
    x['category'] = cat
    x['exclude'] = exclude
    x['narrative'] = label
    x['narrativeNote'] = note
    results.append(x)

# 重新计算「值得深查」清单（排除的除外，且叙事明确或有背书）
excluded = [x for x in results if x['exclude']]
kept = [x for x in results if not x['exclude']]
deep_dive = [x for x in kept if x['narrative'] not in ('一般',)]

# 排序：非排除的按 score 降序，排除的垫底
results.sort(key=lambda x: (-(not x['exclude']), -x['score']))
for i, x in enumerate(results):
    x['rank'] = i + 1

out = {
    'generatedAt': '2026-09-24T21:53',
    'method': '过滤蹭名仿盘/rug盘/真实资产 → 逐个识别叙事 → 标出值得深查',
    'total': len(results),
    'excluded': len(excluded),
    'kept': len(kept),
    'deepDive': len(deep_dive),
    'categoryDist': dict(Counter(x['category'] for x in results)),
    'narrativeDist': dict(Counter(x['narrative'] for x in kept)),
    'ranking': results,
    'deepDiveList': [x for x in sorted(deep_dive, key=lambda y: -y['score'])],
}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)

print('总数:', out['total'], ' 排除:', out['excluded'], ' 保留:', out['kept'], ' 值得深查:', out['deepDive'])
print()
print('类别分布:', out['categoryDist'])
print()
print('=== 排除项构成 ===')
print(Counter(x['category'] for x in excluded))
print()
print('=== 值得深查 top 50 ===')
for x in out['deepDiveList'][:50]:
    print(f"#{x.get('rank')} {x['symbol']:<16} {x['chain']:<8} 分{x['score']:.0f} sm={x['smartMoneyHolders']:>3} kol={x['kolHolders']:>3} rug={x['rugRatio']:.2f} | {x['narrative']}")
