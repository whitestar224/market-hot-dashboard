"""Recall candidates between important-source X activity and GMGN trench coins.

Strong identity edges are labeled here, while the runtime can also request a broad
lexical candidate set.  No broad candidate becomes a relationship until the Jev
semantic classifier confirms that the post actually points to the exact token.
Network access and persistence live in ``server.py`` so this module stays cheap to
unit test.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable


DEFAULT_IMPORTANT_PEOPLE: tuple[dict[str, Any], ...] = (
    # Core crypto founders, exchange leaders, investors and market-moving voices.
    {"handle": "cz_binance", "name": "CZ", "role": "Binance 创始人", "tier": "primary", "aliases": ("cz", "changpeng zhao", "赵长鹏", "binance", "bnb")},
    {"handle": "VitalikButerin", "name": "Vitalik Buterin", "role": "Ethereum 联合创始人", "tier": "primary", "aliases": ("vitalik", "buterin", "ethereum", "eth")},
    {"handle": "sunyuchentron", "name": "Justin Sun", "role": "TRON 创始人", "tier": "primary", "aliases": ("justin sun", "孙宇晨", "tron", "trx")},
    {"handle": "aeyakovenko", "name": "Anatoly Yakovenko", "role": "Solana 联合创始人", "tier": "primary", "aliases": ("anatoly", "yakovenko", "solana", "sol")},
    {"handle": "rajgokal", "name": "Raj Gokal", "role": "Solana 联合创始人", "tier": "primary", "aliases": ("raj gokal", "solana", "sol")},
    {"handle": "sandeepnailwal", "name": "Sandeep Nailwal", "role": "Polygon 联合创始人", "tier": "primary", "aliases": ("sandeep", "nailwal", "polygon", "matic")},
    {"handle": "StaniKulechov", "name": "Stani Kulechov", "role": "Aave 创始人", "tier": "primary", "aliases": ("stani", "kulechov", "aave", "lens")},
    {"handle": "haydenzadams", "name": "Hayden Adams", "role": "Uniswap 创始人", "tier": "primary", "aliases": ("hayden adams", "uniswap", "uni")},
    {"handle": "SergeyNazarov", "name": "Sergey Nazarov", "role": "Chainlink 联合创始人", "tier": "primary", "aliases": ("sergey nazarov", "chainlink", "link")},
    {"handle": "CryptoHayes", "name": "Arthur Hayes", "role": "BitMEX 联合创始人", "tier": "primary", "aliases": ("arthur hayes", "hayes", "bitmex")},
    {"handle": "saylor", "name": "Michael Saylor", "role": "Strategy 执行董事长", "tier": "primary", "aliases": ("michael saylor", "saylor", "strategy", "microstrategy", "bitcoin", "btc")},
    {"handle": "bgarlinghouse", "name": "Brad Garlinghouse", "role": "Ripple CEO", "tier": "primary", "aliases": ("brad garlinghouse", "ripple", "xrp")},
    {"handle": "paoloardoino", "name": "Paolo Ardoino", "role": "Tether CEO", "tier": "primary", "aliases": ("paolo ardoino", "tether", "usdt")},
    {"handle": "jack", "name": "Jack Dorsey", "role": "Block 联合创始人", "tier": "primary", "aliases": ("jack dorsey", "block", "square", "bitcoin", "btc")},
    {"handle": "balajis", "name": "Balaji Srinivasan", "role": "科技投资人与加密创业者", "tier": "primary", "aliases": ("balaji", "srinivasan", "network state")},
    {"handle": "pmarca", "name": "Marc Andreessen", "role": "a16z 联合创始人", "tier": "primary", "aliases": ("marc andreessen", "a16z", "a16z crypto")},
    {"handle": "CathieDWood", "name": "Cathie Wood", "role": "ARK Invest CEO", "tier": "primary", "aliases": ("cathie wood", "ark invest", "ark")},
    {"handle": "IOHK_Charles", "name": "Charles Hoskinson", "role": "Cardano 联合创始人", "tier": "primary", "aliases": ("charles hoskinson", "cardano", "ada")},
    {"handle": "gavofyork", "name": "Gavin Wood", "role": "Polkadot / Web3 Foundation 创始人", "tier": "primary", "aliases": ("gavin wood", "polkadot", "dot", "kusama")},
    {"handle": "el33th4xor", "name": "Emin Gün Sirer", "role": "Ava Labs 联合创始人兼 CEO", "tier": "primary", "aliases": ("emin gun sirer", "emin gün sirer", "avalanche", "avax", "ava labs")},
    {"handle": "ilblackdragon", "name": "Illia Polosukhin", "role": "NEAR Protocol 联合创始人", "tier": "primary", "aliases": ("illia polosukhin", "near", "near protocol")},
    {"handle": "EliBenSasson", "name": "Eli Ben-Sasson", "role": "StarkWare 联合创始人兼 CEO", "tier": "primary", "aliases": ("eli ben-sasson", "starkware", "starknet", "strk")},
    {"handle": "AlexGluchowski", "name": "Alex Gluchowski", "role": "Matter Labs / zkSync 联合创始人", "tier": "primary", "aliases": ("alex gluchowski", "matter labs", "zksync", "zk")},
    {"handle": "weremeow", "name": "Meow", "role": "Jupiter 联合创始人", "tier": "primary", "aliases": ("meow", "jupiter", "jup")},
    {"handle": "yatsiu", "name": "Yat Siu", "role": "Animoca Brands 联合创始人", "tier": "primary", "aliases": ("yat siu", "animoca", "sandbox", "sand")},

    # Technology leaders whose product launches or attention can create a meme/narrative catalyst.
    {"handle": "elonmusk", "name": "Elon Musk", "role": "Tesla / xAI CEO", "tier": "primary", "aliases": ("elon", "musk", "马斯克", "tesla", "spacex", "xai", "grok")},
    {"handle": "sama", "name": "Sam Altman", "role": "OpenAI CEO", "tier": "primary", "aliases": ("sam altman", "altman", "奥特曼", "openai")},
    {"handle": "finkd", "name": "Mark Zuckerberg", "role": "Meta 创始人兼 CEO", "tier": "primary", "aliases": ("zuckerberg", "zuck", "扎克伯格", "meta", "llama", "agrippa")},
    {"handle": "JTLonsdale", "name": "Joe Lonsdale", "role": "Palantir 联合创始人", "tier": "primary", "aliases": ("joe lonsdale", "lonsdale", "palantir", "monitor")},
    {"handle": "vladtenev", "name": "Vlad Tenev", "role": "Robinhood 联合创始人兼 CEO", "tier": "primary", "aliases": ("vlad", "tenev", "robinhood", "hood")},
    {"handle": "DavidSacks", "name": "David Sacks", "role": "科技投资人 / 美国 AI 与加密事务顾问", "tier": "primary", "aliases": ("david sacks", "sacks", "paypal mafia", "crypto czar")},
    {"handle": "satyanadella", "name": "Satya Nadella", "role": "Microsoft CEO", "tier": "secondary", "aliases": ("satya nadella", "microsoft", "azure", "copilot")},
    {"handle": "sundarpichai", "name": "Sundar Pichai", "role": "Alphabet / Google CEO", "tier": "secondary", "aliases": ("sundar pichai", "google", "alphabet", "gemini")},
    {"handle": "tim_cook", "name": "Tim Cook", "role": "Apple CEO", "tier": "secondary", "aliases": ("tim cook", "apple")},
    {"handle": "LisaSu", "name": "Lisa Su", "role": "AMD CEO", "tier": "secondary", "aliases": ("lisa su", "amd")},
    {"handle": "JeffBezos", "name": "Jeff Bezos", "role": "Amazon 创始人", "tier": "secondary", "aliases": ("jeff bezos", "amazon", "aws", "blue origin")},
    {"handle": "BillGates", "name": "Bill Gates", "role": "Microsoft 联合创始人", "tier": "secondary", "aliases": ("bill gates", "gates", "microsoft")},
    {"handle": "demishassabis", "name": "Demis Hassabis", "role": "Google DeepMind CEO", "tier": "secondary", "aliases": ("demis hassabis", "deepmind", "gemini")},
    {"handle": "mustafasuleyman", "name": "Mustafa Suleyman", "role": "Microsoft AI CEO", "tier": "secondary", "aliases": ("mustafa suleyman", "microsoft ai", "inflection")},
    {"handle": "bchesky", "name": "Brian Chesky", "role": "Airbnb 联合创始人兼 CEO", "tier": "secondary", "aliases": ("brian chesky", "airbnb")},

    # Political and public figures with a demonstrated ability to move crypto narratives.
    {"handle": "realDonaldTrump", "name": "Donald Trump", "role": "美国总统 / 公众人物", "tier": "primary", "aliases": ("donald trump", "trump", "特朗普", "maga")},
    {"handle": "JDVance", "name": "JD Vance", "role": "美国副总统 / 公众人物", "tier": "secondary", "aliases": ("jd vance", "vance")},
    {"handle": "VivekRamaswamy", "name": "Vivek Ramaswamy", "role": "美国政治与商业人物", "tier": "secondary", "aliases": ("vivek", "ramaswamy")},
    {"handle": "SenLummis", "name": "Cynthia Lummis", "role": "美国参议员 / 加密政策人物", "tier": "secondary", "aliases": ("cynthia lummis", "lummis", "bitcoin reserve")},
    {"handle": "EricTrump", "name": "Eric Trump", "role": "商业与政治人物", "tier": "secondary", "aliases": ("eric trump", "world liberty", "wlfi")},
    {"handle": "DonaldJTrumpJr", "name": "Donald Trump Jr.", "role": "商业与政治人物", "tier": "secondary", "aliases": ("donald trump jr", "trump jr", "world liberty", "wlfi")},
    {"handle": "tedcruz", "name": "Ted Cruz", "role": "美国参议员 / 加密政策人物", "tier": "secondary", "aliases": ("ted cruz", "cruz", "bitcoin")},
    {"handle": "RepFrenchHill", "name": "French Hill", "role": "美国众议员 / 金融政策人物", "tier": "secondary", "aliases": ("french hill", "financial services", "crypto policy")},
    {"handle": "SenatorTimScott", "name": "Tim Scott", "role": "美国参议员 / 金融政策人物", "tier": "secondary", "aliases": ("tim scott", "senate banking", "crypto policy")},
)


# Explicit removals win over built-ins and user-labelled source imports.  This
# keeps an account out of the shared person watcher even if it still exists in
# an older saved X-tracking list.
EXCLUDED_IMPORTANT_HANDLES = frozenset({"brian_armstrong"})

# Individual alerts are intentionally much narrower than the ordinary X
# tracking list.  Keep only people whose own statement can credibly move a
# major asset, ecosystem, meme sector, ETF expectation or crypto policy.
# Official project/exchange/chain accounts remain because an official mention
# or repost of a meme can itself be a market-moving catalyst.
MARKET_MOVING_PERSON_HANDLES = frozenset({
    "cz_binance", "vitalikbuterin", "sunyuchentron", "aeyakovenko",
    "sandeepnailwal", "stanikulechov", "haydenzadams", "sergeynazarov",
    "cryptohayes", "saylor", "bgarlinghouse", "paoloardoino", "jack",
    "balajis", "pmarca", "cathiedwood", "iohk_charles", "gavofyork",
    "el33th4xor", "weremeow", "elonmusk", "sama", "finkd", "jtlonsdale",
    "vladtenev", "davidsacks", "realdonaldtrump", "jdvance", "senlummis",
    "erictrump", "donaldjtrumpjr", "novogratz", "raoulgmi", "cobie",
    "blknoiz06", "muststopmurad", "gcrclassic", "adam3us", "jespow",
    "runekek", "andrecronjetech", "matthuang", "ericbalchunas", "jseyff",
    "nayibbukele", "jmilei", "mcuban", "chamath", "garrytan",
})


DEFAULT_IMPORTANT_PROJECTS: tuple[dict[str, Any], ...] = (
    {"handle": "binance", "name": "Binance", "role": "头部交易平台官方", "category": "project_official", "tier": "official", "aliases": ("binance", "bnb")},
    {"handle": "coinbase", "name": "Coinbase", "role": "头部交易平台官方", "category": "project_official", "tier": "official", "aliases": ("coinbase", "base")},
    {"handle": "solana", "name": "Solana", "role": "Solana 生态官方", "category": "project_official", "tier": "official", "aliases": ("solana", "sol")},
    {"handle": "ethereum", "name": "Ethereum", "role": "Ethereum 生态官方", "category": "project_official", "tier": "official", "aliases": ("ethereum", "eth")},
    {"handle": "base", "name": "Base", "role": "Base 生态官方", "category": "project_official", "tier": "official", "aliases": ("base", "base chain")},
    {"handle": "RobinhoodApp", "name": "Robinhood", "role": "Robinhood 官方", "category": "project_official", "tier": "official", "aliases": ("robinhood", "hood")},
    {"handle": "a16zcrypto", "name": "a16z crypto", "role": "头部加密投资机构官方", "category": "project_official", "tier": "official", "aliases": ("a16z", "a16z crypto")},
    {"handle": "Ripple", "name": "Ripple", "role": "Ripple 官方", "category": "project_official", "tier": "official", "aliases": ("ripple", "xrp")},
    {"handle": "Tether_to", "name": "Tether", "role": "Tether 官方", "category": "project_official", "tier": "official", "aliases": ("tether", "usdt")},
    {"handle": "Uniswap", "name": "Uniswap Labs", "role": "Uniswap 官方", "category": "project_official", "tier": "official", "aliases": ("uniswap", "uni")},
    {"handle": "AaveAave", "name": "Aave", "role": "Aave 官方", "category": "project_official", "tier": "official", "aliases": ("aave", "lens")},
    {"handle": "chainlink", "name": "Chainlink", "role": "Chainlink 官方", "category": "project_official", "tier": "official", "aliases": ("chainlink", "link")},
    {"handle": "HyperliquidX", "name": "Hyperliquid", "role": "Hyperliquid 官方", "category": "project_official", "tier": "official", "aliases": ("hyperliquid", "hype")},
    {"handle": "arbitrum", "name": "Arbitrum", "role": "Arbitrum 官方", "category": "project_official", "tier": "official", "aliases": ("arbitrum", "arb")},
    {"handle": "Optimism", "name": "Optimism", "role": "Optimism 官方", "category": "project_official", "tier": "official", "aliases": ("optimism", "op", "superchain")},
    {"handle": "0xPolygon", "name": "Polygon", "role": "Polygon 官方", "category": "project_official", "tier": "official", "aliases": ("polygon", "matic", "pol")},
    {"handle": "SuiNetwork", "name": "Sui", "role": "Sui 官方", "category": "project_official", "tier": "official", "aliases": ("sui", "sui network")},
    {"handle": "Aptos", "name": "Aptos", "role": "Aptos 官方", "category": "project_official", "tier": "official", "aliases": ("aptos", "apt")},
    {"handle": "avax", "name": "Avalanche", "role": "Avalanche 官方", "category": "project_official", "tier": "official", "aliases": ("avalanche", "avax")},
    {"handle": "NEARProtocol", "name": "NEAR Protocol", "role": "NEAR 官方", "category": "project_official", "tier": "official", "aliases": ("near", "near protocol")},
    {"handle": "ton_blockchain", "name": "TON", "role": "TON 生态官方", "category": "project_official", "tier": "official", "aliases": ("ton", "telegram", "ton blockchain")},
    {"handle": "BNBCHAIN", "name": "BNB Chain", "role": "BNB Chain 官方", "category": "project_official", "tier": "official", "aliases": ("bnb", "bnb chain", "bsc")},
    {"handle": "pumpdotfun", "name": "Pump.fun", "role": "Pump.fun 官方", "category": "project_official", "tier": "official", "aliases": ("pump", "pump.fun", "pumpfun")},
    {"handle": "JupiterExchange", "name": "Jupiter", "role": "Jupiter 官方", "category": "project_official", "tier": "official", "aliases": ("jupiter", "jup")},
    {"handle": "Polkadot", "name": "Polkadot", "role": "Polkadot 官方", "category": "project_official", "tier": "official", "aliases": ("polkadot", "dot", "kusama")},
    {"handle": "Starknet", "name": "Starknet", "role": "Starknet 官方", "category": "project_official", "tier": "official", "aliases": ("starknet", "strk", "starkware")},
    {"handle": "zksync", "name": "zkSync", "role": "zkSync 官方", "category": "project_official", "tier": "official", "aliases": ("zksync", "matter labs", "zk")},
    {"handle": "SeiNetwork", "name": "Sei", "role": "Sei Network 官方", "category": "project_official", "tier": "official", "aliases": ("sei", "sei network")},
    {"handle": "berachain", "name": "Berachain", "role": "Berachain 官方", "category": "project_official", "tier": "official", "aliases": ("berachain", "bera")},
    {"handle": "monad_xyz", "name": "Monad", "role": "Monad 官方", "category": "project_official", "tier": "official", "aliases": ("monad", "mon")},
)


# Broader discovery roster.  These sources remain tiered and are consumed by a
# single serial poller, so roster growth never becomes concurrent fan-out.
EXTENDED_SOURCE_ROWS: tuple[tuple[str, str, str, str, str, tuple[str, ...]], ...] = (
    # Crypto founders, investors, researchers, analysts and market-moving voices (55).
    ("APompliano", "Anthony Pompliano", "加密投资人与市场人物", "notable", "primary", ("pompliano", "pomp", "bitcoin")),
    ("novogratz", "Mike Novogratz", "Galaxy Digital 创始人兼 CEO", "founder", "primary", ("mike novogratz", "galaxy digital")),
    ("RaoulGMI", "Raoul Pal", "Real Vision 联合创始人", "founder", "primary", ("raoul pal", "real vision", "global macro investor")),
    ("cdixon", "Chris Dixon", "a16z crypto 创始合伙人", "founder", "primary", ("chris dixon", "a16z crypto")),
    ("hosseeb", "Haseeb Qureshi", "Dragonfly 管理合伙人", "notable", "primary", ("haseeb qureshi", "dragonfly")),
    ("KyleSamani", "Kyle Samani", "Multicoin Capital 联合创始人", "founder", "primary", ("kyle samani", "multicoin")),
    ("TusharJain_", "Tushar Jain", "Multicoin Capital 联合创始人", "founder", "primary", ("tushar jain", "multicoin")),
    ("zhusu", "Zhu Su", "加密市场人物", "notable", "secondary", ("zhu su", "three arrows", "3ac")),
    ("cobie", "Cobie", "加密市场人物", "notable", "primary", ("cobie", "up only")),
    ("Arthur_0x", "Arthur Cheong", "DeFiance Capital 创始人", "founder", "primary", ("arthur cheong", "defiance capital")),
    ("DegenSpartan", "Degen Spartan", "DeFi 市场人物", "notable", "secondary", ("degen spartan",)),
    ("0xSisyphus", "Sisyphus", "加密市场人物", "notable", "secondary", ("sisyphus",)),
    ("CryptoCred", "CryptoCred", "加密交易研究者", "notable", "secondary", ("crypto cred",)),
    ("CryptoKaleo", "Kaleo", "加密交易员", "notable", "secondary", ("kaleo",)),
    ("Pentosh1", "Pentoshi", "加密交易员", "notable", "secondary", ("pentoshi",)),
    ("TheFlowHorse", "The Flow Horse", "加密交易员", "notable", "secondary", ("flow horse",)),
    ("HsakaTrades", "Hsaka", "加密交易员", "notable", "secondary", ("hsaka",)),
    ("blknoiz06", "Ansem", "加密市场人物", "notable", "primary", ("ansem",)),
    ("MustStopMurad", "Murad", "Meme 市场研究者", "notable", "primary", ("murad", "memecoin supercycle")),
    ("GCRClassic", "GCR", "加密市场人物", "notable", "primary", ("gcr",)),
    ("inversebrah", "Inversebrah", "加密市场人物", "notable", "secondary", ("inversebrah",)),
    ("CL207", "CL", "加密市场人物", "notable", "secondary", ("cl207",)),
    ("wazzcrypto", "Wazz", "链上与加密市场研究者", "notable", "secondary", ("wazz",)),
    ("DefiIgnas", "Ignas", "DeFi 研究者", "notable", "secondary", ("defi ignas", "ignas")),
    ("Route2FI", "Route 2 FI", "DeFi 研究者", "notable", "secondary", ("route 2 fi", "route2fi")),
    ("0xngmi", "0xngmi", "DefiLlama 创始人", "founder", "primary", ("defillama", "0xngmi")),
    ("NaniXBT", "Nani", "加密市场人物", "notable", "secondary", ("nanixbt",)),
    ("ErikVoorhees", "Erik Voorhees", "ShapeShift 创始人", "founder", "primary", ("erik voorhees", "shapeshift")),
    ("NickSzabo4", "Nick Szabo", "密码学与数字资产研究者", "notable", "primary", ("nick szabo", "smart contracts")),
    ("adam3us", "Adam Back", "Blockstream 联合创始人兼 CEO", "founder", "primary", ("adam back", "blockstream")),
    ("udiWertheimer", "Udi Wertheimer", "Bitcoin 生态市场人物", "notable", "secondary", ("udi wertheimer", "taproot wizards")),
    ("nic__carter", "Nic Carter", "Castle Island Ventures 合伙人", "notable", "primary", ("nic carter", "castle island")),
    ("AriDavidPaul", "Ari Paul", "BlockTower Capital 创始人", "founder", "primary", ("ari paul", "blocktower")),
    ("jespow", "Jesse Powell", "Kraken 联合创始人", "founder", "primary", ("jesse powell", "kraken")),
    ("fluffypony", "Riccardo Spagni", "Monero 前核心维护者", "notable", "secondary", ("riccardo spagni", "monero", "xmr")),
    ("RuneKek", "Rune Christensen", "MakerDAO 联合创始人", "founder", "primary", ("rune christensen", "makerdao", "sky")),
    ("AntonioMJuliano", "Antonio Juliano", "dYdX 创始人", "founder", "primary", ("antonio juliano", "dydx")),
    ("KainWarwick", "Kain Warwick", "Synthetix 创始人", "founder", "primary", ("kain warwick", "synthetix", "snx")),
    ("AndreCronjeTech", "Andre Cronje", "Sonic / Yearn 创始人", "founder", "primary", ("andre cronje", "sonic", "fantom", "yearn")),
    ("samczsun", "samczsun", "Paradigm 研究合伙人", "notable", "primary", ("samczsun", "paradigm")),
    ("matthuang", "Matt Huang", "Paradigm 联合创始人", "founder", "primary", ("matt huang", "paradigm")),
    ("FEhrsam", "Fred Ehrsam", "Coinbase / Paradigm 联合创始人", "founder", "primary", ("fred ehrsam", "coinbase", "paradigm")),
    ("danrobinson", "Dan Robinson", "Paradigm 研究合伙人", "notable", "primary", ("dan robinson", "paradigm")),
    ("hasufl", "Hasu", "Lido / Flashbots 战略与研究人物", "notable", "primary", ("hasu", "lido", "flashbots")),
    ("tarunchitra", "Tarun Chitra", "Gauntlet 创始人兼 CEO", "founder", "primary", ("tarun chitra", "gauntlet")),
    ("robertleshner", "Robert Leshner", "Compound / Superstate 创始人", "founder", "primary", ("robert leshner", "compound", "superstate")),
    ("dankrad", "Dankrad Feist", "Ethereum 研究员", "notable", "primary", ("dankrad feist", "ethereum", "danksharding")),
    ("drakefjustin", "Justin Drake", "Ethereum 研究员", "notable", "primary", ("justin drake", "ethereum")),
    ("TimBeiko", "Tim Beiko", "Ethereum 协议支持负责人", "notable", "primary", ("tim beiko", "ethereum")),
    ("sassal0x", "Sassal", "Ethereum 教育与研究人物", "notable", "secondary", ("sassal", "ethereum")),
    ("RyanSAdams", "Ryan Sean Adams", "Bankless 联合创始人", "founder", "primary", ("ryan sean adams", "bankless")),
    ("LucaNetz", "Luca Netz", "Pudgy Penguins CEO", "notable", "primary", ("luca netz", "pudgy penguins", "pengu")),
    ("EricBalchunas", "Eric Balchunas", "Bloomberg ETF 分析师", "notable", "primary", ("eric balchunas", "etf", "bloomberg")),
    ("JSeyff", "James Seyffart", "Bloomberg ETF 分析师", "notable", "primary", ("james seyffart", "etf", "bloomberg")),
    ("NateGeraci", "Nate Geraci", "ETF Store 总裁", "notable", "secondary", ("nate geraci", "etf store", "etf")),

    # Technology, AI and high-impact business leaders (30).
    ("naval", "Naval Ravikant", "AngelList 联合创始人", "founder", "primary", ("naval", "angellist")),
    ("paulg", "Paul Graham", "Y Combinator 联合创始人", "founder", "primary", ("paul graham", "y combinator", "yc")),
    ("gdb", "Greg Brockman", "OpenAI 联合创始人", "founder", "primary", ("greg brockman", "openai")),
    ("karpathy", "Andrej Karpathy", "AI 研究者与创业者", "notable", "primary", ("andrej karpathy", "karpathy", "ai")),
    ("ylecun", "Yann LeCun", "Meta 首席 AI 科学家", "notable", "primary", ("yann lecun", "meta ai")),
    ("AndrewYNg", "Andrew Ng", "DeepLearning.AI 创始人", "founder", "primary", ("andrew ng", "deeplearning.ai")),
    ("geoffreyhinton", "Geoffrey Hinton", "AI 研究者", "notable", "primary", ("geoffrey hinton", "ai")),
    ("ilyasut", "Ilya Sutskever", "SSI 联合创始人", "founder", "primary", ("ilya sutskever", "ssi", "safe superintelligence")),
    ("EmmettShear", "Emmett Shear", "Twitch 联合创始人", "founder", "secondary", ("emmett shear", "twitch")),
    ("tobi", "Tobi Lütke", "Shopify 联合创始人兼 CEO", "founder", "primary", ("tobi lutke", "shopify")),
    ("dhh", "David Heinemeier Hansson", "Basecamp 联合创始人", "founder", "secondary", ("david heinemeier hansson", "basecamp", "rails")),
    ("levie", "Aaron Levie", "Box 联合创始人兼 CEO", "founder", "secondary", ("aaron levie", "box")),
    ("patrickc", "Patrick Collison", "Stripe 联合创始人兼 CEO", "founder", "primary", ("patrick collison", "stripe")),
    ("collision", "John Collison", "Stripe 联合创始人", "founder", "secondary", ("john collison", "stripe")),
    ("brianacton", "Brian Acton", "Signal Foundation 联合创始人", "founder", "secondary", ("brian acton", "signal", "whatsapp")),
    ("jankoum", "Jan Koum", "WhatsApp 联合创始人", "founder", "secondary", ("jan koum", "whatsapp")),
    ("ev", "Evan Williams", "Twitter / Medium 联合创始人", "founder", "secondary", ("evan williams", "twitter", "medium")),
    ("kevinrose", "Kevin Rose", "Digg 创始人", "founder", "secondary", ("kevin rose", "digg", "proof")),
    ("reidhoffman", "Reid Hoffman", "LinkedIn 联合创始人", "founder", "primary", ("reid hoffman", "linkedin", "inflection")),
    ("benioff", "Marc Benioff", "Salesforce 联合创始人兼 CEO", "founder", "secondary", ("marc benioff", "salesforce")),
    ("MichaelDell", "Michael Dell", "Dell Technologies 创始人兼 CEO", "founder", "secondary", ("michael dell", "dell")),
    ("ArvindKrishna", "Arvind Krishna", "IBM CEO", "notable", "secondary", ("arvind krishna", "ibm")),
    ("daniel_ek", "Daniel Ek", "Spotify 联合创始人兼 CEO", "founder", "secondary", ("daniel ek", "spotify")),
    ("vkhosla", "Vinod Khosla", "Khosla Ventures 创始人", "founder", "primary", ("vinod khosla", "khosla ventures")),
    ("mcuban", "Mark Cuban", "科技投资人与企业家", "notable", "primary", ("mark cuban", "cuban")),
    ("chamath", "Chamath Palihapitiya", "Social Capital 创始人", "founder", "primary", ("chamath", "social capital")),
    ("bgurley", "Bill Gurley", "Benchmark 合伙人", "notable", "secondary", ("bill gurley", "benchmark")),
    ("fredwilson", "Fred Wilson", "Union Square Ventures 联合创始人", "founder", "secondary", ("fred wilson", "usv")),
    ("garrytan", "Garry Tan", "Y Combinator CEO", "notable", "primary", ("garry tan", "y combinator", "yc")),
    ("MrBeast", "MrBeast", "全球内容创作者与商业人物", "celebrity", "secondary", ("mrbeast", "jimmy donaldson")),

    # Political, regulatory and public-policy sources (25).
    ("WhiteHouse", "The White House", "美国白宫官方", "notable", "secondary", ("white house", "白宫")),
    ("POTUS", "President of the United States", "美国总统官方账号", "notable", "secondary", ("potus", "president")),
    ("USTreasury", "U.S. Treasury", "美国财政部官方", "notable", "secondary", ("treasury", "us treasury")),
    ("SECGov", "U.S. SEC", "美国证券交易委员会官方", "notable", "primary", ("sec", "securities and exchange commission")),
    ("CFTC", "CFTC", "美国商品期货交易委员会官方", "notable", "primary", ("cftc", "commodities")),
    ("federalreserve", "Federal Reserve", "美联储官方", "notable", "primary", ("federal reserve", "fed")),
    ("SenWarren", "Elizabeth Warren", "美国参议员 / 金融政策人物", "notable", "secondary", ("elizabeth warren", "warren")),
    ("RepMaxineWaters", "Maxine Waters", "美国众议员 / 金融政策人物", "notable", "secondary", ("maxine waters", "financial services")),
    ("PatrickMcHenry", "Patrick McHenry", "美国金融政策人物", "notable", "secondary", ("patrick mchenry", "financial services")),
    ("SenatorHagerty", "Bill Hagerty", "美国参议员 / 加密政策人物", "notable", "secondary", ("bill hagerty", "hagerty", "stablecoin")),
    ("SenGillibrand", "Kirsten Gillibrand", "美国参议员 / 加密政策人物", "notable", "secondary", ("kirsten gillibrand", "gillibrand")),
    ("SenBooker", "Cory Booker", "美国参议员", "notable", "secondary", ("cory booker", "booker")),
    ("RepTomEmmer", "Tom Emmer", "美国众议员 / 加密政策人物", "notable", "secondary", ("tom emmer", "emmer")),
    ("WarrenDavidson", "Warren Davidson", "美国众议员 / 加密政策人物", "notable", "secondary", ("warren davidson", "davidson")),
    ("RepRoKhanna", "Ro Khanna", "美国众议员 / 科技政策人物", "notable", "secondary", ("ro khanna", "silicon valley")),
    ("nayibbukele", "Nayib Bukele", "萨尔瓦多总统 / Bitcoin 政策人物", "notable", "primary", ("nayib bukele", "el salvador", "bitcoin")),
    ("JMilei", "Javier Milei", "阿根廷总统 / 公众人物", "notable", "primary", ("javier milei", "milei", "argentina")),
    ("PierrePoilievre", "Pierre Poilievre", "加拿大政治人物", "notable", "secondary", ("pierre poilievre", "canada")),
    ("EmmanuelMacron", "Emmanuel Macron", "法国总统 / 公众人物", "notable", "secondary", ("emmanuel macron", "macron", "france")),
    ("narendramodi", "Narendra Modi", "印度总理 / 公众人物", "notable", "secondary", ("narendra modi", "modi", "india")),
    ("ZelenskyyUa", "Volodymyr Zelenskyy", "乌克兰总统 / 公众人物", "notable", "secondary", ("zelenskyy", "ukraine")),
    ("AOC", "Alexandria Ocasio-Cortez", "美国众议员 / 公众人物", "notable", "secondary", ("alexandria ocasio-cortez", "aoc")),
    ("BarackObama", "Barack Obama", "美国前总统 / 公众人物", "notable", "secondary", ("barack obama", "obama")),
    ("RobertKennedyJr", "Robert F. Kennedy Jr.", "美国政治与公共人物", "notable", "secondary", ("robert kennedy jr", "rfk jr", "kennedy")),
    ("GovRonDeSantis", "Ron DeSantis", "美国政治人物", "notable", "secondary", ("ron desantis", "desantis", "florida")),

    # Exchanges, protocols, infrastructure, wallets and major ecosystems (50).
    ("krakenfx", "Kraken", "头部交易平台官方", "project_official", "official", ("kraken",)),
    ("okx", "OKX", "头部交易平台官方", "project_official", "official", ("okx",)),
    ("Bybit_Official", "Bybit", "头部交易平台官方", "project_official", "official", ("bybit",)),
    ("bitgetglobal", "Bitget", "交易平台官方", "project_official", "official", ("bitget",)),
    ("MEXC_Official", "MEXC", "交易平台官方", "project_official", "official", ("mexc",)),
    ("gate_io", "Gate", "交易平台官方", "project_official", "official", ("gate", "gate.io")),
    ("kucoincom", "KuCoin", "交易平台官方", "project_official", "official", ("kucoin",)),
    ("cryptocom", "Crypto.com", "交易平台官方", "project_official", "official", ("crypto.com", "cro")),
    ("Gemini", "Gemini", "交易平台官方", "project_official", "official", ("gemini",)),
    ("Bitstamp", "Bitstamp", "交易平台官方", "project_official", "official", ("bitstamp",)),
    ("HTX_Global", "HTX", "交易平台官方", "project_official", "official", ("htx", "huobi")),
    ("cosmos", "Cosmos", "Cosmos 生态官方", "project_official", "official", ("cosmos", "atom")),
    ("injective", "Injective", "Injective 生态官方", "project_official", "official", ("injective", "inj")),
    ("CelestiaOrg", "Celestia", "Celestia 官方", "project_official", "official", ("celestia", "tia")),
    ("LineaBuild", "Linea", "Linea 官方", "project_official", "official", ("linea",)),
    ("Scroll_ZKP", "Scroll", "Scroll 官方", "project_official", "official", ("scroll",)),
    ("MantaNetwork", "Manta Network", "Manta 官方", "project_official", "official", ("manta",)),
    ("Mantle_Official", "Mantle", "Mantle 官方", "project_official", "official", ("mantle", "mnt")),
    ("Blast_L2", "Blast", "Blast L2 官方", "project_official", "official", ("blast",)),
    ("AbstractChain", "Abstract", "Abstract Chain 官方", "project_official", "official", ("abstract",)),
    ("megaeth_labs", "MegaETH", "MegaETH 官方", "project_official", "official", ("megaeth",)),
    ("StoryProtocol", "Story", "Story Protocol 官方", "project_official", "official", ("story protocol", "story", "ip")),
    ("movementlabsxyz", "Movement", "Movement 官方", "project_official", "official", ("movement", "move")),
    ("fuel_network", "Fuel", "Fuel Network 官方", "project_official", "official", ("fuel",)),
    ("eigenlayer", "EigenLayer", "EigenLayer 官方", "project_official", "official", ("eigenlayer", "eigen")),
    ("babylonlabs_io", "Babylon", "Babylon Labs 官方", "project_official", "official", ("babylon",)),
    ("symbioticfi", "Symbiotic", "Symbiotic 官方", "project_official", "official", ("symbiotic",)),
    ("wormhole", "Wormhole", "Wormhole 官方", "project_official", "official", ("wormhole", "w")),
    ("LayerZero_Core", "LayerZero", "LayerZero 官方", "project_official", "official", ("layerzero", "zro")),
    ("StargateFinance", "Stargate", "Stargate 官方", "project_official", "official", ("stargate", "stg")),
    ("axelar", "Axelar", "Axelar 官方", "project_official", "official", ("axelar", "axl")),
    ("zeta_blockchain", "ZetaChain", "ZetaChain 官方", "project_official", "official", ("zetachain", "zeta")),
    ("SonicLabs", "Sonic Labs", "Sonic 官方", "project_official", "official", ("sonic", "fantom")),
    ("FantomFDN", "Fantom Foundation", "Fantom 官方", "project_official", "official", ("fantom", "ftm")),
    ("Celo", "Celo", "Celo 官方", "project_official", "official", ("celo",)),
    ("Algorand", "Algorand", "Algorand 官方", "project_official", "official", ("algorand", "algo")),
    ("hedera", "Hedera", "Hedera 官方", "project_official", "official", ("hedera", "hbar")),
    ("MultiversX", "MultiversX", "MultiversX 官方", "project_official", "official", ("multiversx", "egld")),
    ("Filecoin", "Filecoin", "Filecoin 官方", "project_official", "official", ("filecoin", "fil")),
    ("ArweaveEco", "Arweave", "Arweave 生态官方", "project_official", "official", ("arweave", "ar")),
    ("CurveFinance", "Curve Finance", "Curve 官方", "project_official", "official", ("curve", "crv")),
    ("pendle_fi", "Pendle", "Pendle 官方", "project_official", "official", ("pendle",)),
    ("LidoFinance", "Lido", "Lido 官方", "project_official", "official", ("lido", "ldo")),
    ("Rocket_Pool", "Rocket Pool", "Rocket Pool 官方", "project_official", "official", ("rocket pool", "rpl")),
    ("compoundfinance", "Compound", "Compound 官方", "project_official", "official", ("compound", "comp")),
    ("MorphoLabs", "Morpho", "Morpho 官方", "project_official", "official", ("morpho",)),
    ("ethena_labs", "Ethena Labs", "Ethena 官方", "project_official", "official", ("ethena", "ena", "usde")),
    ("ether_fi", "ether.fi", "ether.fi 官方", "project_official", "official", ("ether.fi", "etherfi", "ethfi")),
    ("MetaMask", "MetaMask", "头部钱包官方", "project_official", "official", ("metamask",)),
    ("phantom", "Phantom", "头部钱包官方", "project_official", "official", ("phantom",)),
)


EXTENDED_IMPORTANT_SOURCES: tuple[dict[str, Any], ...] = tuple(
    {
        "handle": handle,
        "name": name,
        "role": role,
        "category": category,
        "tier": tier,
        "aliases": aliases,
    }
    for handle, name, role, category, tier, aliases in EXTENDED_SOURCE_ROWS
)

GENERIC_TOKEN_TERMS = {
    "ai", "binance", "coin", "crypto", "dao", "defi", "dog", "doge", "eth", "ethereum",
    "finance", "high", "hood", "importance", "live", "market", "markets", "meme", "meta",
    "agent", "model", "models", "monitor", "native", "official", "open", "pepe", "project",
    "public", "scout", "series", "sol", "solana", "staying", "stock", "the", "token",
    "web3", "world",
}

CRYPTO_REFERENCE_RE = re.compile(
    r"(?:\$[A-Za-z0-9]{2,12}|\b(?:airdrop|blockchain|buy|ca|chain|coin|contract|crypto|dex|"
    r"ethereum|launch|liquidity|listed|listing|market\s*cap|memecoin|mint|pump|sell|solana|"
    r"token|trade|trading|wallet)\b|0x[a-fA-F0-9]{12,})",
    re.I,
)


def normalize_handle(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith(("http://", "https://")):
        match = re.search(r"(?:x|twitter)\.com/([^/?#]+)", raw, re.I)
        raw = match.group(1) if match else ""
    return re.sub(r"[^A-Za-z0-9_]", "", raw.lstrip("@"))[:15]


def token_identity(row: dict[str, Any]) -> str:
    network = str(row.get("network") or row.get("chain") or "").strip().lower()
    contract = str(row.get("contractAddress") or row.get("address") or "").strip()
    if not network or not contract:
        return ""
    return f"{network}:{contract if network == 'solana' else contract.casefold()}"


def important_person_sources(saved_sources: Iterable[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Return high-impact individuals plus official crypto/project accounts."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    defaults = (*DEFAULT_IMPORTANT_PEOPLE, *DEFAULT_IMPORTANT_PROJECTS, *EXTENDED_IMPORTANT_SOURCES)
    for raw in (*defaults, *tuple(saved_sources)):
        if not isinstance(raw, dict):
            continue
        handle = normalize_handle(raw.get("handle") or raw.get("url"))
        folded_handle = handle.casefold()
        if not handle or folded_handle in EXCLUDED_IMPORTANT_HANDLES or folded_handle in seen:
            continue
        category = str(raw.get("category") or "notable").strip().lower()
        is_official = category == "project_official"
        if not is_official and folded_handle not in MARKET_MOVING_PERSON_HANDLES:
            continue
        if category not in {"celebrity", "notable", "founder", "project_official"}:
            continue
        seen.add(folded_handle)
        aliases = raw.get("aliases") if isinstance(raw.get("aliases"), (list, tuple)) else ()
        rows.append({
            "id": f"trench-person:{handle.casefold()}",
            "handle": handle,
            "displayName": str(raw.get("displayName") or raw.get("name") or handle).strip()[:80],
            "personRole": str(raw.get("personRole") or raw.get("role") or "重要人物").strip()[:80],
            "category": category if category in {"celebrity", "notable", "founder", "project_official"} else "notable",
            "watchTier": str(raw.get("watchTier") or raw.get("tier") or "secondary").strip().lower(),
            "aliases": [str(value).strip().casefold() for value in aliases if str(value).strip()][:16],
            "keywords": [],
            "enabled": raw.get("enabled") is not False,
        })
    return rows


def _post_text(item: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    # Parsers may keep an expanded ``fullText`` that concatenates the author's
    # sentence and the quoted card. Attribution must use the author's own text.
    main = str(item.get("text") or item.get("title") or item.get("fullText") or "").strip()
    quoted = str(quote.get("text") or "").strip()
    return main, quoted, quote


def _official_x(row: dict[str, Any]) -> tuple[str, str, str]:
    original = row.get("xOriginal") if isinstance(row.get("xOriginal"), dict) else {}
    context = row.get("narrativeContext") if isinstance(row.get("narrativeContext"), dict) else {}
    links = context.get("links") if isinstance(context.get("links"), dict) else {}
    url = str(original.get("url") or links.get("twitter") or "").strip()
    handle = normalize_handle(original.get("handle") or url)
    status_id = str(original.get("statusId") or "").strip()
    return handle, status_id, url


def _distinctive_name_terms(row: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    symbol = str(row.get("symbol") or "").strip()
    name = str(row.get("name") or "").strip()
    if re.search(r"[\u3400-\u9fff]", symbol) and len(symbol) >= 2:
        terms.append(symbol.casefold())
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{4,31}", symbol):
        folded = token.casefold()
        if folded not in GENERIC_TOKEN_TERMS:
            terms.append(folded)

    if re.search(r"[\u3400-\u9fff]", name) and len(name) >= 2:
        terms.append(name.casefold())
    name_tokens = [
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,31}", name)
        if token.casefold() not in GENERIC_TOKEN_TERMS
    ]
    if len(name_tokens) == 1 and len(name_tokens[0]) >= 5:
        terms.append(name_tokens[0])
    elif len(name_tokens) >= 2:
        # Multiword names must match as a phrase; a generic component such as
        # "Agent", "Markets" or "Dog" cannot identify a contract by itself.
        terms.append(" ".join(name_tokens[:6]))
    return list(dict.fromkeys(terms))[:8]


def _bounded_term(text: str, term: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", term):
        return term.casefold() in text.casefold()
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I))


def _semantic_exact_name_term(row: dict[str, Any], text: str) -> str:
    """Require the author's sentence to point at an asset, not an ordinary noun."""
    for term in _distinctive_name_terms(row):
        if re.search(r"[\u3400-\u9fff]", term):
            if term.casefold() in text.casefold():
                return term
            continue
        match = re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I)
        if not match:
            continue
        context = text[max(0, match.start() - 100) : min(len(text), match.end() + 100)]
        if CRYPTO_REFERENCE_RE.search(context):
            return term
        literal = match.group(0)
        # Distinctive proper names can be early signals before a post says
        # "token" or "contract". Lowercase ordinary usage cannot.
        if literal[:1].isupper() and term.casefold() not in GENERIC_TOKEN_TERMS:
            return term
    return ""


def _lexical_candidate_term(row: dict[str, Any], text: str) -> str:
    """Recall-only lookup; the downstream AI, never this function, decides meaning."""
    symbol = str(row.get("symbol") or "").strip()
    name = str(row.get("name") or "").strip()
    terms: list[str] = []
    for value in (symbol, name):
        if len(value) >= 2:
            terms.append(value)
        terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,31}|[\u3400-\u9fff]{2,16}", value))
    for term in dict.fromkeys(part.strip() for part in terms if part.strip()):
        if _bounded_term(text, term):
            return term
    return ""


def _action(item: dict[str, Any], quote: dict[str, Any]) -> tuple[str, str]:
    explicit = str(item.get("actionType") or item.get("relationshipType") or "").strip().lower()
    if explicit in {"follow", "followed", "following"}:
        return "follow", "新关注"
    entry = str(item.get("entryType") or "").strip().lower()
    if entry in {"retweeted", "retweet", "reposted"}:
        return "repost", "转发"
    if quote:
        return "quote", "引用"
    if entry in {"replied_to", "reply", "replied"}:
        return "reply", "回复"
    return "mention", "点名"


def match_person_post(
    row: dict[str, Any],
    item: dict[str, Any],
    source: dict[str, Any],
    *,
    semantic_prefilter: bool = True,
) -> dict[str, Any] | None:
    """Return one evidence record, or ``None`` when identity is too weak."""
    identity = token_identity(row)
    if not identity:
        return None
    entry_type = str(item.get("entryType") or "").strip().lower()
    if entry_type in {"replied_to", "reply", "replied", "comment"}:
        return None
    main, quoted, quote = _post_text(item)
    text = " ".join(part for part in (main, quoted, str(item.get("url") or ""), str(quote.get("url") or "")) if part)
    if not text:
        return None
    action_type, action_label = _action(item, quote)
    contract = str(row.get("contractAddress") or "").strip()
    symbol = str(row.get("symbol") or "").strip()
    official_handle, official_status, official_url = _official_x(row)
    target_handle = normalize_handle(item.get("targetHandle") or item.get("subjectHandle") or item.get("followedHandle"))

    match_type = ""
    confidence = 0
    evidence_status = ""
    if action_type == "follow" and official_handle and target_handle.casefold() == official_handle.casefold():
        match_type, confidence, evidence_status = "verified-follow", 99, "person-x-follow-verified"
    elif contract and contract.casefold() in text.casefold():
        match_type, confidence, evidence_status = "exact-contract", 100, "person-x-contract-explicit"
    else:
        quote_handle = normalize_handle(quote.get("handle") or quote.get("url"))
        # A bare word matching the account name is not enough.  GMGN projects
        # sometimes link a third-party post (for example @binance), so only an
        # explicit @mention/link/status/quote can establish the social edge.
        handle_pattern = rf"(?<![A-Za-z0-9_])@{re.escape(official_handle)}(?![A-Za-z0-9_])" if official_handle else ""
        official_match = bool(
            official_handle
            and (
                quote_handle.casefold() == official_handle.casefold()
                or (handle_pattern and re.search(handle_pattern, text, re.I))
                or (official_status and official_status in text)
                or (official_url and official_url.casefold() in text.casefold())
            )
        )
        if official_match:
            match_type, confidence, evidence_status = "official-x", 97, "person-x-official-handle"
        elif symbol and re.search(rf"(?<![A-Za-z0-9])\${re.escape(symbol)}(?![A-Za-z0-9])", text, re.I):
            match_type, confidence, evidence_status = "cashtag", 94, "person-x-cashtag"
        else:
            # A quote/repost is useful only when it carries a strong identity
            # edge (CA, official X or cashtag). Ordinary words in somebody
            # else's quoted text must not become a celebrity/KOL "mention".
            # Runtime person monitoring deliberately uses the broad lexical
            # path so every possible relationship reaches the AI semantic
            # classifier.  The strict path remains useful for validating old
            # pre-AI records and for cheap standalone callers.
            term = (
                _semantic_exact_name_term(row, main)
                if semantic_prefilter
                else _lexical_candidate_term(row, " ".join(part for part in (main, quoted) if part))
            )
            if term:
                match_type, confidence, evidence_status = "exact-name", 90, "person-x-name-unverified"
            else:
                return None

    person_handle = normalize_handle(source.get("handle") or item.get("handle"))
    tweet_id = str(item.get("tweetId") or item.get("id") or "").strip()
    published_at = int(float(item.get("publishedAt") or 0))
    if 0 < published_at < 10_000_000_000:
        published_at *= 1000
    signal_key = hashlib.sha256(
        f"{identity}|{person_handle.casefold()}|{tweet_id}".encode("utf-8")
    ).hexdigest()[:32]
    return {
        "key": f"trench-person:{signal_key}",
        "tokenIdentity": identity,
        "network": str(row.get("network") or row.get("chain") or "").strip().lower(),
        "contractAddress": contract,
        "tokenSymbol": symbol[:60],
        "tokenName": str(row.get("name") or symbol).strip()[:180],
        "personHandle": person_handle,
        "personName": str(source.get("displayName") or item.get("sourceName") or person_handle).strip()[:80],
        "personRole": str(source.get("personRole") or "重要人物").strip()[:80],
        "sourceCategory": str(source.get("category") or "notable").strip().lower()[:40],
        "sourceTier": str(source.get("watchTier") or "secondary").strip().lower()[:40],
        "actionType": action_type,
        "actionLabel": action_label,
        "matchType": match_type,
        "semanticScope": "author-main" if match_type == "exact-name" else "strong-identity",
        "confidence": confidence,
        "identityStatus": evidence_status,
        "postId": tweet_id[:80],
        "postUrl": str(item.get("url") or "").strip()[:900],
        "postText": main[:1800],
        "quoteText": quoted[:1200],
        "publishedAt": published_at,
        "sourceProvider": str(item.get("provider") or "public-x").strip()[:40],
    }


def person_relevance_score(source: dict[str, Any], rows: Iterable[dict[str, Any]]) -> int:
    aliases = [str(value).casefold() for value in (source.get("aliases") or []) if str(value).strip()]
    if not aliases:
        return 0
    score = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        context = row.get("narrativeContext") if isinstance(row.get("narrativeContext"), dict) else {}
        haystack = " ".join(str(value or "") for value in (
            row.get("symbol"), row.get("name"), row.get("gmgnNarrative"), context.get("description"),
        )).casefold()
        score = max(score, max((len(alias) for alias in aliases if alias and alias in haystack), default=0))
    return score
