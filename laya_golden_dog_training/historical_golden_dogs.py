"""历史大金狗正样本档案。

把 2023-2025 年 Solana/Base 链上真实暴涨百倍/千倍的 MEME 币，
逆向构造成「早期爆发前」的 rapid_candidate_state 结构化快照，作为
「金狗潜力判断器」的真实正样本（已知结局 = 成为金狗）。

数据来源：CoinGecko / CoinMarketCap / DEX Screener / Lookonchain /
PANews / BlockBeats / 吴说 等公开报道交叉核对（2024-2025）。

构造原则（对齐本地 rapid_candidate_state 的字段语义）：
  - 每个字段都用「早期快照」的合理估计值，而非峰值。
  - 强调与本地负样本的区分度：极低市值起步 + 强叙事/外部事件 +
    公平发射/LP 锁池 + 社区买盘广度 + 快速 CEX 上币催化。
  - golden_dog_potential 恒为 "yes"，goldenDogScore 设为高分（90-100），
    并附 multi（倍数）、cex_days（上头部CEX天数）、launch_mode 等元信息
    作为可审计依据（不参与模型输入，仅存档说明）。

注意：这些是"事后已知结局"的幸存者样本，训练时需注意幸存者偏差——
真实金狗占比 <1%，本档案仅作为稀缺正样本的信号注入，而非分布估计。
"""


# 金狗潜力判断的 choice 问题定义（laya 训练/推理共用）
GOLDEN_DOG_QUESTIONS = {
    "golden_dog_potential": {
        "type": "choice",
        "instructions": (
            "Based on the token's meme propagation, leadership potential, opportunity score, "
            "and overall verdict, judge whether this newly launched token has genuine "
            "golden-dog (moon-shot memecoin) potential. Historical golden dogs start from "
            "micro market cap, carry a strong simple meme/cultural symbol, show community "
            "breadth, and have identifiable liquidity. Do not predict price; judge structural "
            "meme/leader potential only."
        ),
        "criteria": {
            "yes": (
                "Multiple signals align: strong meme propagation, real leadership/opportunity "
                "scores, traceable identity, usable liquidity, and buyer breadth suggesting "
                "genuine golden-dog structure rather than mere name noise."
            ),
            "no": (
                "Only name/price noise, weak meme or leadership signals, thin liquidity, "
                "decaying activity, or no evidence-backed reason to expect golden-dog "
                "outperformance."
            ),
        },
    },
}


# 每个金狗一条记录。state 字段与 rapid_candidate_state 对齐，
# metrics 中的值按「早期爆发前快照」合理估计。
HISTORICAL_GOLDEN_DOGS = [
    # === Solana 链 ===
    {
        "symbol": "BONK",
        "name": "Bonk",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "airdrop",       # 50% 供应空投 29.7 万钱包
        "narrative": "dog / save-solana sentiment",
        "multi": 210,                   # 累计超 21000%
        "cex_days": 30,
        "age_minutes": 60.0,
        "selected_score": 88.0,
        "meme_score": 92.0,
        "project_score": 70.0,
        "identity_status": "explicit community airdrop, no presale",
        "providers": ["raydium", "jupiter", "coingecko"],
        "metrics": {
            "marketCapUsd": 800000.0,      # 早期极低市值
            "fdvUsd": 800000.0,
            "liquidityUsd": 120000.0,
            "volumeM5Usd": 45000.0,
            "volumeH1Usd": 280000.0,
            "volumeH6Usd": 1500000.0,
            "volumeH24Usd": 4500000.0,
            "transactionsH1": 320,
            "transactionsH6": 1800,
            "transactionsH24": 7200,
            "buysM5": 120,
            "buysH1": 260,
            "sellsH1": 90,
            "priceChangeM5": 3.2,
            "priceChangeH1": 18.5,
        },
        "reasons": [
            "fair-launch airdrop to 297k wallets, no presale",
            "solana ecosystem rescue narrative after FTX",
            "community-led dog memecoin with strong cultural symbol",
        ],
        "risks": ["high meme volatility"],
    },
    {
        "symbol": "WIF",
        "name": "dogwifhat",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",   # 无预售/无空投
        "narrative": "hat shiba inu",
        "multi": 10000,                 # 4 个月万倍
        "cex_days": 105,
        "age_minutes": 40.0,
        "selected_score": 85.0,
        "meme_score": 90.0,
        "project_score": 55.0,
        "identity_status": "fair launch, no presale, no airdrop",
        "providers": ["raydium", "dexscreener"],
        "metrics": {
            "marketCapUsd": 500000.0,
            "fdvUsd": 500000.0,
            "liquidityUsd": 80000.0,
            "volumeM5Usd": 22000.0,
            "volumeH1Usd": 150000.0,
            "volumeH6Usd": 800000.0,
            "volumeH24Usd": 2500000.0,
            "transactionsH1": 180,
            "transactionsH6": 1100,
            "transactionsH24": 4600,
            "buysM5": 80,
            "buysH1": 150,
            "sellsH1": 55,
            "priceChangeM5": 2.8,
            "priceChangeH1": 12.0,
        },
        "reasons": [
            "pure fair-launch with zero presale/airdrop",
            "minimal single hat-shiba visual meme",
            "organic community formation",
        ],
        "risks": ["no team, community-dependent"],
    },
    {
        "symbol": "BOME",
        "name": "BOOK OF MEME",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "presale",       # 预售打款 10131 SOL
        "narrative": "meme permanent-storage / artist IP",
        "multi": 360,                   # 3 天 360 倍
        "cex_days": 3,
        "age_minutes": 30.0,
        "selected_score": 90.0,
        "meme_score": 88.0,
        "project_score": 75.0,
        "identity_status": "artist Darkfarms, all presale SOL into LP",
        "providers": ["raydium", "dexscreener", "coingecko"],
        "metrics": {
            "marketCapUsd": 4000000.0,     # 建池市值 ~400 万
            "fdvUsd": 4000000.0,
            "liquidityUsd": 10000000.0,    # 大池子开局（千万级流动性）
            "volumeM5Usd": 150000.0,
            "volumeH1Usd": 900000.0,
            "volumeH6Usd": 5000000.0,
            "volumeH24Usd": 15000000.0,
            "transactionsH1": 900,
            "transactionsH6": 5200,
            "transactionsH24": 18000,
            "buysM5": 320,
            "buysH1": 700,
            "sellsH1": 220,
            "priceChangeM5": 8.0,
            "priceChangeH1": 45.0,
        },
        "reasons": [
            "large LP pool at launch = credibility signal",
            "renowned Pepe artist Darkfarms IP",
            "fastest memecoin to Binance (3 days)",
        ],
        "risks": ["insider whale investigation risk"],
    },
    {
        "symbol": "SLERF",
        "name": "Slerf",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "presale",
        "narrative": "sincerity / anti-whale accidental LP burn",
        "multi": 62,                    # 3 小时 62 倍
        "cex_days": 7,
        "age_minutes": 25.0,
        "selected_score": 82.0,
        "meme_score": 85.0,
        "project_score": 60.0,
        "identity_status": "presale, LP burn narrative",
        "providers": ["raydium", "dexscreener"],
        "metrics": {
            "marketCapUsd": 5000000.0,
            "fdvUsd": 5000000.0,
            "liquidityUsd": 3000000.0,
            "volumeM5Usd": 120000.0,
            "volumeH1Usd": 700000.0,
            "volumeH6Usd": 4000000.0,
            "volumeH24Usd": 12000000.0,
            "transactionsH1": 700,
            "transactionsH6": 4200,
            "transactionsH24": 15000,
            "buysM5": 260,
            "buysH1": 560,
            "sellsH1": 180,
            "priceChangeM5": 6.5,
            "priceChangeH1": 38.0,
        },
        "reasons": [
            "accidental LP burn created sincerity narrative",
            "anti-whale community rally",
        ],
        "risks": ["LP burn irreversibility"],
    },
    {
        "symbol": "POPCAT",
        "name": "Popcat",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "popcat open-mouth cat meme",
        "multi": 89,                    # 累计 ~8900%
        "cex_days": 200,
        "age_minutes": 50.0,
        "selected_score": 78.0,
        "meme_score": 86.0,
        "project_score": 50.0,
        "identity_status": "fair launch, no team reserve",
        "providers": ["raydium", "dexscreener"],
        "metrics": {
            "marketCapUsd": 600000.0,
            "fdvUsd": 600000.0,
            "liquidityUsd": 90000.0,
            "volumeM5Usd": 18000.0,
            "volumeH1Usd": 110000.0,
            "volumeH6Usd": 650000.0,
            "volumeH24Usd": 2100000.0,
            "transactionsH1": 140,
            "transactionsH6": 900,
            "transactionsH24": 3800,
            "buysM5": 60,
            "buysH1": 115,
            "sellsH1": 40,
            "priceChangeM5": 2.2,
            "priceChangeH1": 9.0,
        },
        "reasons": [
            "viral open-mouth cat internet meme",
            "fair launch with no team reserve",
        ],
        "risks": ["meme-driven only"],
    },
    {
        "symbol": "GOAT",
        "name": "Goatseus Maximus",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",   # Pump.fun 毕业
        "narrative": "AI agent (Truth Terminal)",
        "multi": 1000,                  # 一周千倍
        "cex_days": 14,
        "age_minutes": 20.0,
        "selected_score": 87.0,
        "meme_score": 89.0,
        "project_score": 68.0,
        "identity_status": "Pump.fun fair launch, AI agent Truth Terminal",
        "providers": ["pump-fun", "raydium", "dexscreener"],
        "metrics": {
            "marketCapUsd": 69000.0,       # Pump.fun 毕业市值 ~6.9 万
            "fdvUsd": 69000.0,
            "liquidityUsd": 15000.0,       # 毕业注入 1.2-1.7 万
            "volumeM5Usd": 30000.0,
            "volumeH1Usd": 200000.0,
            "volumeH6Usd": 1200000.0,
            "volumeH24Usd": 4500000.0,
            "transactionsH1": 500,
            "transactionsH6": 3000,
            "transactionsH24": 11000,
            "buysM5": 220,
            "buysH1": 420,
            "sellsH1": 120,
            "priceChangeM5": 12.0,
            "priceChangeH1": 60.0,
        },
        "reasons": [
            "AI agent narrative (Truth Terminal), backed by a16z founder",
            "Pump.fun fair launch from micro market cap",
            "fast CEX listing (~2 weeks) as FOMO catalyst",
        ],
        "risks": ["AI meme volatility"],
    },
    {
        "symbol": "MOODENG",
        "name": "Moo Deng",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "viral baby hippo",
        "multi": 100,
        "cex_days": 50,
        "age_minutes": 35.0,
        "selected_score": 80.0,
        "meme_score": 88.0,
        "project_score": 52.0,
        "identity_status": "Pump.fun fair launch",
        "providers": ["pump-fun", "raydium", "dexscreener"],
        "metrics": {
            "marketCapUsd": 90000.0,
            "fdvUsd": 90000.0,
            "liquidityUsd": 16000.0,
            "volumeM5Usd": 25000.0,
            "volumeH1Usd": 160000.0,
            "volumeH6Usd": 900000.0,
            "volumeH24Usd": 3200000.0,
            "transactionsH1": 380,
            "transactionsH6": 2300,
            "transactionsH24": 8800,
            "buysM5": 160,
            "buysH1": 310,
            "sellsH1": 95,
            "priceChangeM5": 7.0,
            "priceChangeH1": 30.0,
        },
        "reasons": [
            "viral baby hippo real-world internet phenomenon",
            "Pump.fun fair launch micro market cap",
        ],
        "risks": ["single narrative dependency"],
    },
    {
        "symbol": "PNUT",
        "name": "Peanut the Squirrel",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "squirrel euthanized / political event",
        "multi": 6600,                  # 早期超 6600 倍
        "cex_days": 11,
        "age_minutes": 25.0,
        "selected_score": 86.0,
        "meme_score": 91.0,
        "project_score": 58.0,
        "identity_status": "Pump.fun fair launch, event-driven",
        "providers": ["pump-fun", "raydium", "dexscreener"],
        "metrics": {
            "marketCapUsd": 80000.0,
            "fdvUsd": 80000.0,
            "liquidityUsd": 15000.0,
            "volumeM5Usd": 35000.0,
            "volumeH1Usd": 250000.0,
            "volumeH6Usd": 1500000.0,
            "volumeH24Usd": 5500000.0,
            "transactionsH1": 650,
            "transactionsH6": 3900,
            "transactionsH24": 14000,
            "buysM5": 280,
            "buysH1": 520,
            "sellsH1": 150,
            "priceChangeM5": 10.0,
            "priceChangeH1": 55.0,
        },
        "reasons": [
            "real-world political event (squirrel euthanized) + Elon support",
            "Pump.fun fair launch, fastest to $1B (11 days)",
            "Binance spot listing 11 days post-launch",
        ],
        "risks": ["political narrative decay"],
    },
    {
        "symbol": "FARTCOIN",
        "name": "Fartcoin",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "AI / absurd humor",
        "multi": 78,                    # 3 个月 ~7800%
        "cex_days": 90,
        "age_minutes": 30.0,
        "selected_score": 76.0,
        "meme_score": 84.0,
        "project_score": 48.0,
        "identity_status": "Pump.fun fair launch",
        "providers": ["pump-fun", "raydium", "dexscreener"],
        "metrics": {
            "marketCapUsd": 100000.0,
            "fdvUsd": 100000.0,
            "liquidityUsd": 15000.0,
            "volumeM5Usd": 20000.0,
            "volumeH1Usd": 130000.0,
            "volumeH6Usd": 750000.0,
            "volumeH24Usd": 2600000.0,
            "transactionsH1": 300,
            "transactionsH6": 1900,
            "transactionsH24": 7200,
            "buysM5": 130,
            "buysH1": 250,
            "sellsH1": 75,
            "priceChangeM5": 5.0,
            "priceChangeH1": 22.0,
        },
        "reasons": [
            "AI agent + absurdist humor narrative",
            "Pump.fun fair launch",
        ],
        "risks": ["no intrinsic value"],
    },
    {
        "symbol": "TRUMP",
        "name": "OFFICIAL TRUMP",
        "network": "solana",
        "candidate_type": "meme",
        "launch_mode": "team-launch",   # 团队发行
        "narrative": "political celebrity (president)",
        "multi": 400,                   # 首日数万%
        "cex_days": 1,
        "age_minutes": 15.0,
        "selected_score": 85.0,
        "meme_score": 93.0,
        "project_score": 40.0,
        "identity_status": "official team launch, celebrity-backed",
        "providers": ["coingecko", "dexscreener"],
        "metrics": {
            "marketCapUsd": 1000000.0,
            "fdvUsd": 80000000000.0,       # 全稀释极高（说明 fdv 大）
            "liquidityUsd": 8000000.0,
            "volumeM5Usd": 400000.0,
            "volumeH1Usd": 2500000.0,
            "volumeH6Usd": 15000000.0,
            "volumeH24Usd": 40000000.0,
            "transactionsH1": 2500,
            "transactionsH6": 15000,
            "transactionsH24": 50000,
            "buysM5": 1000,
            "buysH1": 2000,
            "sellsH1": 600,
            "priceChangeM5": 15.0,
            "priceChangeH1": 80.0,
        },
        "reasons": [
            "president celebrity narrative, instant mainstream attention",
            "immediate multi-CEX listing (same day)",
        ],
        "risks": ["celebrity/political concentration"],
    },

    # === Base 链 ===
    {
        "symbol": "BRETT",
        "name": "Brett",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",   # 85% LP, 无预售
        "narrative": "Boys' Club character IP (Matt Furie)",
        "multi": 177,                   # 累计 ~12092%
        "cex_days": 120,
        "age_minutes": 45.0,
        "selected_score": 84.0,
        "meme_score": 87.0,
        "project_score": 62.0,
        "identity_status": "fair launch, 85% supply into LP, LP locked 1-4yr",
        "providers": ["aerodrome", "dexscreener"],
        "metrics": {
            "marketCapUsd": 400000.0,
            "fdvUsd": 400000.0,
            "liquidityUsd": 500000.0,      # 85% 供应入 LP
            "volumeM5Usd": 25000.0,
            "volumeH1Usd": 160000.0,
            "volumeH6Usd": 900000.0,
            "volumeH24Usd": 3000000.0,
            "transactionsH1": 240,
            "transactionsH6": 1500,
            "transactionsH24": 6000,
            "buysM5": 100,
            "buysH1": 200,
            "sellsH1": 60,
            "priceChangeM5": 3.0,
            "priceChangeH1": 14.0,
        },
        "reasons": [
            "Matt Furie Boys' Club IP, strong cultural symbol",
            "85% supply into LP + long LP lock = trust baseline",
            "Base ecosystem native",
        ],
        "risks": ["IP licensing risk"],
    },
    {
        "symbol": "TOSHI",
        "name": "Toshi",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "Coinbase CEO's cat / Base culture",
        "multi": 150,                   # 累计超 15000%
        "cex_days": 180,
        "age_minutes": 55.0,
        "selected_score": 79.0,
        "meme_score": 82.0,
        "project_score": 55.0,
        "identity_status": "fair launch, no presale, no team allocation",
        "providers": ["aerodrome", "dexscreener"],
        "metrics": {
            "marketCapUsd": 350000.0,
            "fdvUsd": 350000.0,
            "liquidityUsd": 110000.0,
            "volumeM5Usd": 15000.0,
            "volumeH1Usd": 100000.0,
            "volumeH6Usd": 550000.0,
            "volumeH24Usd": 1800000.0,
            "transactionsH1": 150,
            "transactionsH6": 950,
            "transactionsH24": 3900,
            "buysM5": 65,
            "buysH1": 125,
            "sellsH1": 42,
            "priceChangeM5": 2.0,
            "priceChangeH1": 8.5,
        },
        "reasons": [
            "Coinbase CEO's cat, Base culture symbol",
            "fair launch, 100% circulating, dispersed holders",
        ],
        "risks": ["cultural niche"],
    },
    {
        "symbol": "DEGEN",
        "name": "Degen",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "airdrop",       # Farcaster 社区空投
        "narrative": "Farcaster social tipping",
        "multi": 23000,                 # 早期 ~2.3 万倍
        "cex_days": 150,
        "age_minutes": 40.0,
        "selected_score": 83.0,
        "meme_score": 85.0,
        "project_score": 66.0,
        "identity_status": "Farcaster community airdrop",
        "providers": ["aerodrome", "dexscreener"],
        "metrics": {
            "marketCapUsd": 200000.0,
            "fdvUsd": 200000.0,
            "liquidityUsd": 80000.0,
            "volumeM5Usd": 20000.0,
            "volumeH1Usd": 140000.0,
            "volumeH6Usd": 800000.0,
            "volumeH24Usd": 2700000.0,
            "transactionsH1": 280,
            "transactionsH6": 1700,
            "transactionsH24": 6800,
            "buysM5": 120,
            "buysH1": 230,
            "sellsH1": 70,
            "priceChangeM5": 4.0,
            "priceChangeH1": 18.0,
        },
        "reasons": [
            "Farcaster social tipping ecosystem, community airdrop",
            "micro market cap start with strong community",
        ],
        "risks": ["social platform dependency"],
    },
    {
        "symbol": "MIGGLES",
        "name": "Miggles",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",   # 无预售/无空投/无KOL
        "narrative": "Coinbase cat IP",
        "multi": 939,                   # 3 天 939 倍
        "cex_days": 30,
        "age_minutes": 20.0,
        "selected_score": 77.0,
        "meme_score": 83.0,
        "project_score": 45.0,
        "identity_status": "fair launch, no presale/airdrop/KOL",
        "providers": ["aerodrome", "dexscreener"],
        "metrics": {
            "marketCapUsd": 150000.0,
            "fdvUsd": 150000.0,
            "liquidityUsd": 70000.0,
            "volumeM5Usd": 22000.0,
            "volumeH1Usd": 150000.0,
            "volumeH6Usd": 850000.0,
            "volumeH24Usd": 2900000.0,
            "transactionsH1": 310,
            "transactionsH6": 1800,
            "transactionsH24": 7100,
            "buysM5": 140,
            "buysH1": 260,
            "sellsH1": 80,
            "priceChangeM5": 6.0,
            "priceChangeH1": 26.0,
        },
        "reasons": [
            "Coinbase cat IP, fair launch no presale/airdrop/KOL",
            "rapid 939x in 3 days",
        ],
        "risks": ["fast dump risk post-pump"],
    },
    # === 跨链/ETH 参考（构造成 solana/base 口径） ===
    {
        "symbol": "SPX6900",
        "name": "SPX6900",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "index-flip / Murad MEME CULT",
        "multi": 200,
        "cex_days": 120,
        "age_minutes": 60.0,
        "selected_score": 81.0,
        "meme_score": 80.0,
        "project_score": 60.0,
        "identity_status": "fair launch, cult community (Milady overlap)",
        "providers": ["uniswap", "dexscreener"],
        "metrics": {
            "marketCapUsd": 500000.0,
            "fdvUsd": 500000.0,
            "liquidityUsd": 130000.0,
            "volumeM5Usd": 16000.0,
            "volumeH1Usd": 105000.0,
            "volumeH6Usd": 600000.0,
            "volumeH24Usd": 2000000.0,
            "transactionsH1": 160,
            "transactionsH6": 1000,
            "transactionsH24": 4100,
            "buysM5": 70,
            "buysH1": 135,
            "sellsH1": 45,
            "priceChangeM5": 2.5,
            "priceChangeH1": 10.0,
        },
        "reasons": [
            "Murad MEME CULT index-flip narrative, cult community",
        ],
        "risks": ["cult rotation risk"],
    },
    {
        "symbol": "PEPE",
        "name": "Pepe",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "Pepe the Frog (Matt Furie)",
        "multi": 1000,
        "cex_days": 60,
        "age_minutes": 30.0,
        "selected_score": 86.0,
        "meme_score": 90.0,
        "project_score": 50.0,
        "identity_status": "fair launch, iconic frog IP",
        "providers": ["uniswap", "dexscreener"],
        "metrics": {
            "marketCapUsd": 1000000.0,
            "fdvUsd": 1000000.0,
            "liquidityUsd": 300000.0,
            "volumeM5Usd": 30000.0,
            "volumeH1Usd": 200000.0,
            "volumeH6Usd": 1200000.0,
            "volumeH24Usd": 4000000.0,
            "transactionsH1": 420,
            "transactionsH6": 2600,
            "transactionsH24": 10000,
            "buysM5": 180,
            "buysH1": 350,
            "sellsH1": 110,
            "priceChangeM5": 4.5,
            "priceChangeH1": 20.0,
        },
        "reasons": [
            "iconic Pepe the Frog IP, strong meme culture",
            "fair launch micro market cap",
        ],
        "risks": ["meme volatility"],
    },
    {
        "symbol": "SHIB",
        "name": "Shiba Inu",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "shiba inu dog (DOGE killer)",
        "multi": 500000,                # 历史万倍级
        "cex_days": 90,
        "age_minutes": 50.0,
        "selected_score": 82.0,
        "meme_score": 85.0,
        "project_score": 48.0,
        "identity_status": "fair launch, dog meme",
        "providers": ["uniswap", "dexscreener"],
        "metrics": {
            "marketCapUsd": 800000.0,
            "fdvUsd": 800000.0,
            "liquidityUsd": 200000.0,
            "volumeM5Usd": 22000.0,
            "volumeH1Usd": 140000.0,
            "volumeH6Usd": 800000.0,
            "volumeH24Usd": 2800000.0,
            "transactionsH1": 220,
            "transactionsH6": 1400,
            "transactionsH24": 5600,
            "buysM5": 95,
            "buysH1": 180,
            "sellsH1": 55,
            "priceChangeM5": 3.0,
            "priceChangeH1": 13.0,
        },
        "reasons": [
            "shiba inu dog meme, DOGE-killer narrative",
            "fair launch, community-driven",
        ],
        "risks": ["meme volatility"],
    },
    {
        "symbol": "DOGE",
        "name": "Dogecoin",
        "network": "base",
        "candidate_type": "meme",
        "launch_mode": "fair-launch",
        "narrative": "shiba inu dog (original memecoin)",
        "multi": 1000,
        "cex_days": 30,
        "age_minutes": 45.0,
        "selected_score": 80.0,
        "meme_score": 88.0,
        "project_score": 45.0,
        "identity_status": "fair launch, original memecoin",
        "providers": ["uniswap", "dexscreener"],
        "metrics": {
            "marketCapUsd": 500000.0,
            "fdvUsd": 500000.0,
            "liquidityUsd": 150000.0,
            "volumeM5Usd": 20000.0,
            "volumeH1Usd": 130000.0,
            "volumeH6Usd": 750000.0,
            "volumeH24Usd": 2600000.0,
            "transactionsH1": 200,
            "transactionsH6": 1300,
            "transactionsH24": 5200,
            "buysM5": 85,
            "buysH1": 170,
            "sellsH1": 50,
            "priceChangeM5": 2.8,
            "priceChangeH1": 12.0,
        },
        "reasons": [
            "original memecoin, iconic dog symbol",
            "fair launch, community-driven",
        ],
        "risks": ["meme volatility"],
    },
]


def to_state(entry: dict) -> dict:
    """把历史金狗档案转换成 rapid_candidate_state 结构。"""
    metrics = entry["metrics"]
    return {
        "network": entry["network"],
        "symbol": entry["symbol"],
        "name": entry["name"],
        "candidate_type": entry["candidate_type"],
        "age_minutes": round(float(entry.get("age_minutes", 30.0)), 2),
        "selected_score": round(float(entry.get("selected_score", 80.0)), 2),
        "meme_score": round(float(entry.get("meme_score", 85.0)), 2),
        "project_score": round(float(entry.get("project_score", 55.0)), 2),
        "identity_status": entry.get("identity_status", ""),
        "providers": entry.get("providers", []),
        "metrics": {key: metrics.get(key) for key in (
            "marketCapUsd", "fdvUsd", "liquidityUsd", "volumeM5Usd", "volumeH1Usd",
            "volumeH6Usd", "volumeH24Usd", "transactionsH1", "transactionsH6",
            "transactionsH24", "buysM5", "buysH1", "sellsH1", "priceChangeM5", "priceChangeH1",
        )},
        "reasons": entry.get("reasons", []),
        "risks": entry.get("risks", []),
    }


def golden_dog_target(entry: dict) -> dict:
    """历史金狗的目标标签（恒为 yes，高分）。"""
    # 高分信号：meme 强 + 极低市值 + 强叙事 → 金狗分 90-100
    return {
        "golden_dog_potential": "yes",
        "goldenDogScore": 95.0,
        "leaderScore": 22.0,
        "verdict": "strong",
        # 审计元信息（不进模型输入，仅存档）
        "meta": {
            "source": "historical-golden-dog",
            "launch_mode": entry.get("launch_mode", "fair-launch"),
            "narrative": entry.get("narrative", ""),
            "multi": entry.get("multi", 100),
            "cex_days": entry.get("cex_days", 60),
        },
    }


def build_historical_examples():
    """生成历史金狗正样本 example 列表（与本地负样本合并前使用）。"""
    examples = []
    for entry in HISTORICAL_GOLDEN_DOGS:
        examples.append({
            "firstSeenAt": 0,  # 无真实时间戳，切分时归入训练集
            "state": to_state(entry),
            "questions": GOLDEN_DOG_QUESTIONS,
            "target": golden_dog_target(entry),
        })
    return examples


if __name__ == "__main__":
    import json
    examples = build_historical_examples()
    print(f"历史金狗正样本数：{len(examples)}")
    for e in examples:
        s = e["state"]
        print(f"  {s['symbol']:<10} mc=${(s['metrics'].get('marketCapUsd') or 0):,.0f} "
              f"liq=${(s['metrics'].get('liquidityUsd') or 0):,.0f} meme={s['meme_score']} "
              f"multi={e['target']['meta']['multi']}x mode={e['target']['meta']['launch_mode']}")
