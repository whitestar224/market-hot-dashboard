"""Read-only smart-money wallet buy monitoring for the local dashboard."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable

import requests


DEFAULT_ALERT_THRESHOLD_USD = 10_000.0

# 同一个 (链, 钱包, 代币) **当天只弹第一条**——用户 2026-11-15 16:41 要求：
# 「所有的买入监控，只做某一个标的当天的第一次买入弹窗」。动机是拿「标题数」换
# 「阈值可以调低」：高频 KOL 对同一个币来回加仓，一天能刷几十张，把别人的提醒挤下去。
#
# 只影响**弹窗**，不影响记账：每一笔照样进 events 表、页面上照样逐笔显示，
# 只是第二次以后的弹窗被抑制（details 里标 popupSuppressed=daily-first）。
# 判定依据是 events 表里 alerted_at 落在「本地今天」的那一行 —— 落盘、重启不丢，
# 不需要新表。要关掉：设 SMART_MONEY_BUY_ONCE_PER_TOKEN_PER_DAY=0 并重启。
SMART_MONEY_BUY_ONCE_PER_TOKEN_PER_DAY = os.environ.get(
    "SMART_MONEY_BUY_ONCE_PER_TOKEN_PER_DAY", "1"
).strip().lower() not in ("0", "false", "no", "off")
DEFAULT_SMART_MONEY_SEEDS = (
    {
        "address": "0x6078ee8a93697c6d67863fcbff77141d9ab358b2",
        "nickname": "Inq",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "https://web3.okx.com/portfolio/0x6078ee8a93697c6d67863fcbff77141d9ab358b2/analysis",
        "sourceEvidence": (
            "由 Inq5️⃣连杆提供；OKX 公开钱包标签为 Inq（@inquixit）。"
            "用户 2026-10-28 要求「把这个地址的买入也监控上并弹窗提示」——"
            "该地址早就在库里，但阈值一直是默认的 10000U，实测 1202 笔真实买入里"
            "单笔 ≥10000U 的有 0 笔（中位 $46、最大 $581），等于从未弹过窗。"
            "按实测规模定为 100U：与 CryptoCharming / NΞO 同档，近 30 天日均 5~8 条。"
            "2026-11-15 17:30 随「同币当天只弹第一条」一起降到 50U —— 每日首单之后"
            "弹窗数由「标的数」而不是「笔数」决定，降档只多 0.8 条/天。"
        ),
        # 四条链都留着：Robinhood 是主战场（175/200 笔），但 ETH / Base 也真在买。
        # 用户在面板里随时可以改阈值（改过之后本 seed 不会再覆盖，见
        # SmartMoneyStore.apply_seed_thresholds）。
        "alertThresholdUsd": 50.0,
    },
    {
        "address": "0xa0ed47a5dc1017db87d3807ecd95750b3ea0ff85",
        "nickname": "身份待核验 · a0ed…ff85",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0x24e91130ba7fb21f853d709a1ffb8dee99ba7ba3",
        "nickname": "身份待核验 · 24e9…7ba3",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0x00fe92e51f487407a49ce7f7fab9eaecada119fb",
        "nickname": "身份待核验 · 00fe…19fb",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0xa23103777a932eb2a99826e01d48f62f954504f6",
        "nickname": "身份待核验 · a231…04f6",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9",
        "nickname": "CryptoCharming",
        "sourceName": "用户提供 · Robinhood 链",
        "sourceUrl": "https://robinhoodchain.blockscout.com/address/0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9",
        "sourceEvidence": "用户提供的 Robinhood 链 KOL 地址（CryptoCharming）；链上核验为普通钱包（非合约），GMGN 活动截图佐证",
        # 显式写出四条链：以前缺省走 EVM_CHAINS，改成显式是为了让下面那条
        # 「只管 Elysium」的孪生 seed 不至于被缺省值重复覆盖。
        "chains": ("ethereum", "bsc", "base", "robinhood"),
        # 链上实测该地址单笔成交在数十美元量级（持仓 800~2300U），沿用默认
        # 10000U 阈值等于永不提醒，故按实际规模下调；面板里仍可随时改。
        # 2026-11-15 17:30 100U → 50U（每日首单上线后降档几乎不增噪）。
        "alertThresholdUsd": 50.0,
    },
    {
        # 用户 2026-11-09：「cryptocharming 在 elysium 的买入也要做弹窗」。
        # 单开一条只挂 elysium 的 seed，而不是往上面那条的 chains 里加 —— 上面那条的
        # ``sourceName`` 写的是「以 Robinhood 链 KOL 地址提供」，直接复用会让面板里
        # 出现「Elysium 行的来源写着 Robinhood 链」这种自相矛盾的记录。
        #
        # 链上实测（head ≈ 315 万，该地址在 Elysium 上 nonce 16、余额 14.07 HYPE≈$1028，
        # 首次建仓就在近 4 小时内）：15 笔买单全部用原生 HYPE 支付，
        # 0.5/1/2×3/3×6/4.06/5/10×2 HYPE = $37 / $73 / $146×3 / $219×6 / $297 / $365 / $731×2。
        # 阈值沿用他在其它链的 **100U**（面板里那四行也是 100U），口径统一；
        # 这个量级下 100U 约等于「每笔真建仓都弹」，实测约 3 条/小时。
        # 2026-11-15 17:30 与其它链一起降到 50U（每日首单后 5.8 → 6.0 条/天）。
        "address": "0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9",
        "nickname": "CryptoCharming",
        "sourceName": "用户点名 · Elysium 链",
        "sourceUrl": "https://elysium.kinetiq.xyz/explorer/address/0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9",
        "sourceEvidence": (
            "用户 2026-11-09 点名要求「cryptocharming 在 elysium 的买入也要做弹窗」，"
            "地址沿用他在 Robinhood/BSC/ETH/Base 那个钱包（0xcc29…f5a9）。"
            "链上核验：Elysium 主网（chainId 99901）EOA，nonce 16、余额 14.07 HYPE，"
            "近 4 小时内 15 笔原生 HYPE 买单，单笔 $37~$731 —— 确认是本人在这条链上建仓。"
        ),
        "chains": ("elysium",),
        # 2026-11-15 17:30：与其它四链一起 100U → 50U（每日首单后 5.8 → 6.0 条/天）。
        "alertThresholdUsd": 50.0,
    },
    # ------------------------------------------------------------------
    # CryptoCharming 的两个 Solana 公开钱包（用户 2026-11-12 提供）
    #
    # 起因：用户给了 `https://gmgn.ai/profile/CryptoCharming?chain=sol` 要「公开钱包全部
    # 监控上」。GMGN 不给匿名接口（profile 页被 Cloudflare 拦、`/api/v1/user_profile`
    # 要 web token、KOL 榜只给前 100 且 offset 无效 —— 四条路实测全堵，见
    # MEMORY-gmgn-api.md 第 6 节），最后是用户直接把地址贴过来的。
    #
    # 两支钱包的分工很清楚（GMGN `wallet_activity` 实测，2026-11-12）：
    #   大仓那支：25 天 47 条，买 8 / 卖 39，买入中位 **$1,973**、p25 $531、最大 $4,952
    #             → 低频大额，100U 门槛下 25 天只有 6 张（≈0.2 张/天），等于闭眼放行。
    #   小仓那支：26 天 60 条，买 34 / 卖 26，买入中位 **$100**、p75 $297、最大 $585
    #             → 日常打野，100U → 17 张（≈0.6 张/天），300U 只剩 6 张。
    # 阈值统一沿用他 EVM 那 5 条链的 **100U**：口径一致，且两支加起来才 ≈0.8 张/天。
    # ------------------------------------------------------------------
    {
        "address": "HyYvwH1bU8qb82wbhZvsgyVmjJroAv2W8qVyhccWj79P",
        "nickname": "CryptoCharming",
        "sourceName": "用户点名 · Solana 公开钱包（大仓）",
        "sourceUrl": "https://gmgn.ai/sol/address/HyYvwH1bU8qb82wbhZvsgyVmjJroAv2W8qVyhccWj79P",
        "sourceEvidence": (
            "用户 2026-11-12 从 gmgn.ai/profile/CryptoCharming?chain=sol 复制提供。"
            "GMGN 链上实测：25 天 47 条活动，买 8 / 卖 39，买入中位 $1,973、"
            "p25 $531、p75 $4,951、最大 $4,952 —— 低频大额仓位，与 EVM 那边的"
            "小额打法不同，但确实是同一个人公开的钱包。"
        ),
        # 纯 Solana 钱包必须显式写单链：缺省会铺 EVM_CHAINS 四条，
        # base58 地址在 EVM 上不存在，纯白烧四个扫描窗口。
        "chains": ("solana",),
        "alertThresholdUsd": 100.0,
    },
    {
        "address": "4X43DmYRtzLcyv2YRhC8vaTeqB5BTL6jX7LDtzfz47aC",
        "nickname": "CryptoCharming",
        "sourceName": "用户点名 · Solana 公开钱包（小仓）",
        "sourceUrl": "https://gmgn.ai/sol/address/4X43DmYRtzLcyv2YRhC8vaTeqB5BTL6jX7LDtzfz47aC",
        "sourceEvidence": (
            "用户 2026-11-12 从 gmgn.ai/profile/CryptoCharming?chain=sol 复制提供。"
            "GMGN 链上实测：26 天 60 条活动，买 34 / 卖 26，买入中位 $100、"
            "p25 $98、p75 $297、最大 $585 —— 典型的 $100 一档日常打野。"
        ),
        "chains": ("solana",),
        "alertThresholdUsd": 100.0,
    },
    {
        "address": "0xe28601398a5448d1147e6e8b0e0c6d686f0d216d",
        "nickname": "NΞO",
        "sourceName": "用户提供 · Robinhood 链",
        "sourceUrl": "https://robinhoodchain.blockscout.com/address/0xe28601398a5448d1147e6e8b0e0c6d686f0d216d",
        "sourceEvidence": (
            "NΞO 的 Robinhood 链钱包（用户指认）。链上核验为普通钱包（EOA：无合约代码、"
            "3.64 ETH 余额、nonce 20253，交易高度活跃，非合约）。链上实测单笔买入在 "
            "0.05~0.15 ETH（约 $120~$350）量级，沿用默认 10000U 阈值等于永不提醒，"
            "故按实际规模下调；面板里仍可随时改。"
        ),
        # 该地址先以「身份待核验 · e286…216d」占位入池，之后用户才指认为 NΞO；
        # 声明这对旧标签，重启时把它认领成真名（用户自己改过则不会被动）。
        "legacyLabels": (("身份待核验 · e286…216d", "用户提供 · Robinhood 链"),),
        # 与 CryptoCharming 同口径：该钱包买入以原生 ETH 支付，实测单笔数十到数百美元。
        "alertThresholdUsd": 100.0,
    },
    {
        "address": "0x0a6ebed0155edb4b21d92ad02897a626cd90119e",
        "nickname": "Bonk Guy (Unipcs)",
        "sourceName": "Bonk Guy 公开钱包",
        "sourceUrl": "https://gxnonqlmujmtgczvhvzp.supabase.co/functions/v1/api/v1/traders/unipcs/wallets",
        "sourceEvidence": "Unipcs 公开交易者目录中的当前 EVM 钱包；Robinhood 两种独立代币持仓交叉确认",
    },
    {
        "address": "2heJbC32Tpfcb3nbUb5ER61K11FGZVfVGtVnDm6LDogF",
        "nickname": "Bonk Guy (Unipcs)",
        "sourceName": "Bonk Guy 公开钱包",
        "sourceUrl": "https://gxnonqlmujmtgczvhvzp.supabase.co/functions/v1/api/v1/traders/unipcs/wallets",
        "sourceEvidence": "Unipcs 公开交易者目录中的当前 Solana 钱包，并有历史交易记录",
        "chains": ("solana",),
    },
    {
        "address": "5M8ACGKEXG1ojKDTMH3sMqhTihTgHYMSsZc6W8i7QW3Y",
        "nickname": "Bonk Guy (Unipcs)",
        "sourceName": "Bonk Guy 公开钱包",
        "sourceUrl": "https://www.reddit.com/r/CryptoCurrency/comments/1mh9ct2/ama_i_turned_16k_to_20000000_on_a_single_memecoin/",
        "sourceEvidence": "Bonk Guy 本人公开过的 USELESS 持仓钱包；与当前 Solana 交易钱包分别保留",
        "chains": ("solana",),
    },
    {
        "address": "0xbf004bff64725914ee36d03b87d6965b0ced4903",
        "nickname": "阿峰_Afeng",
        "sourceName": "用户提供 · GMGN KOL",
        "sourceUrl": "https://bscscan.com/address/0xbf004bff64725914ee36d03b87d6965b0ced4903",
        "sourceEvidence": (
            "用户 2026-10-22 指定要监控的 GMGN KOL「阿峰_Afeng」（GMGN 面板余额 15.27 BNB）。"
            "链上核验：BSC 上该地址带 EIP-7702 委托代码（不是普通 EOA）、nonce 24.8 万、"
            "余额 15.2714 BNB —— 与面板读数一致，确认是同一个人那个钱包；"
            "ETH（nonce 1.7 万）与 Base（5.8 千）也有历史活动，但用户要求只盯 BSC。"
            "GMGN 统计单笔买入均值约 $344。阈值演变：2026-10-22 拍板 500U；"
            "2026-11-12 用户看到「币安生活」三 KOL 同买却零弹窗后，把 cowboy / 阿峰 / "
            "antiposition 统一降到 300U（他实际单笔 403U/168U 两档，500U 门槛会整段漏掉）。"
        ),
        # 用户 2026-10-22 拍板「只 BSC」：ETH/Base 上他也在动，但那两边的买单不提醒。
        # 注意 BSC 走的是「按区块扫 from」的兜底路径（公共日志接口拒绝无 address 查询），
        # 所以这里必须是一个真的会自己发交易的地址 —— EIP-7702 委托之后 from 仍是它本人。
        "chains": ("bsc",),
        "alertThresholdUsd": 300.0,
    },
    {
        "address": "0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407",
        "nickname": "cowboy",
        "sourceName": "用户提供 · KOL",
        "sourceUrl": "https://bscscan.com/address/0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407",
        "sourceEvidence": (
            "用户 2026-10-27 指定要监控的 KOL「cowboy」。链上核验（2026-10-27）："
            "三条 EVM 链都是普通 EOA 且都有真实活动 —— BSC nonce 11,164 / 余额 83.41 BNB、"
            "ETH nonce 298 / 1.78 ETH、Base nonce 162 / 1.34 ETH。"
            "监控面给这三条链（没要 Robinhood，他不在那条链上活动）；"
            "提醒门槛：2026-10-27 拍板 500U；2026-11-12 与阿峰/antiposition 一起降到 300U"
            "（他 BSC 上的标准仓就在 300~490U，500U 门槛把日常仓位整段漏掉）；"
            "2026-11-15 17:30 再降到 200U —— 每日首单上线后，他一天 215 笔只落在"
            "十几个标的上，降档只多 0.4 条/天。"
        ),
        # 用户没限定链，但核验显示他只在 BSC / ETH / Base 活动：明确给三条，
        # 别让默认四链（含 robinhood）白占扫描窗口。
        "chains": ("bsc", "ethereum", "base"),
        "alertThresholdUsd": 200.0,
    },
    {
        "address": "0x7e3ba68c49561aae7c23c1d20fef0f1d7615a3ad",
        "nickname": "pons榜5",
        "sourceName": "用户提供 · Robinhood 链",
        "sourceUrl": "https://robinhoodchain.blockscout.com/address/0x7e3ba68c49561aae7c23c1d20fef0f1d7615a3ad",
        "sourceEvidence": (
            "用户 2026-11-01 指定：「这个地址的买入记录也弹窗提示……是 robinhood 上的聪明钱」，"
            "随后 2026-11-04 补正备注为「pons榜5」（Pons 聪明钱榜第 5 名）。"
            "链上核验（2026-11-04）：带 EIP-7702 委托代码的智能账户"
            "（0xef0100…dae32b，与阿峰同款），nonce 748、余额 0.52 ETH。"
            "交易形态很特别：一笔交易里用 0xe9ae5c53 批量执行多笔兑换，"
            "付款方是 DELTA（0xe8ffd7e2…a791，18 位精度）而不是稳定币或 ETH —— "
            "实测 6 笔买入分别花 20~50 万 DELTA 换 11~42 亿个 meme；"
            "最后一笔活动在 2026-10-17，属于「集中打一波就停」的打法。"
            "因为付款方不是稳定币，默认逻辑认不出它买了什么（永远不弹），"
            "故同时把该地址登记进 QUOTE_TOKEN_PAYMENT_WALLETS 走报价兜底。"
        ),
        # 只在 Robinhood 链活动，明确给一条链，别白占另外三条的扫描窗口。
        "chains": ("robinhood",),
        # DELTA 实测约 $0.009（USDG 池 slot0），他每笔花 20~50 万 DELTA ≈
        # $1,800~$4,500。门槛定 1,000U：真实买单全都过、几十美元的试单不炸窗。
        "alertThresholdUsd": 1000.0,
    },
    {
        "address": "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",
        "nickname": "THEO",
        "sourceName": "用户提供 · Solana",
        "sourceUrl": "https://solscan.io/account/Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",
        "sourceEvidence": (
            "用户 2026-11-06 指定：「这个是SOL链上的一个聪明钱…帮我也做一个买入监控弹窗」，"
            "随后补正名称为 THEO。只接 solana 一条链（地址是 base58，不是 EVM）。"
            "接入时本机网络整体不通（连 www.baidu.com 直连都 ConnectTimeout），"
            "所以当时没做链上核验。"
            "2026-11-17 补核：用户给出 gmgn.ai/profile/theonomix，GMGN KOL 榜里"
            "「twitter_username = theonomix」（39,324 关注）只对应这一个地址，身份确认。"
            "成交画像（GMGN wallet_activity，40 条样本仅覆盖 **1.2 小时**）："
            "26 笔买入，绝大多数是 $18~19 的碎单，随后一簇 $107~215，最大 $358；"
            "即最高频的狙击型打法，40 条活动只够 1 小时多一点。"
        ),
        # 不写 chains 会默认铺 4 条 EVM 链，而 base58 地址在 EVM 上根本不存在 ——
        # 只会白白烧掉扫描窗口，还污染健康度统计。
        "chains": ("solana",),
        # 与阿峰 / cowboy 同档。Solana meme 盘上的聪明钱常见 1~3 SOL 一单，
        # 500U 能滤掉几十美元的试单，又不会把时间线刷满。
        # 2026-11-15 17:30 500U → 300U：每日首单上线后他从 0 条/天变成 1 条/天。
        # 2026-11-17 实测最高单笔 $358，300U 在上面那 1.2 小时的 26 笔买入里只命中 1 笔；
        # 再往下调（200U）会一次爆发就 8 张，故维持 300U。
        "alertThresholdUsd": 300.0,
    },
    {
        # 用户的第二支公开钱包（EVM）。同一个人的钱包用同一个名字打头，
        # 弹窗里再靠后缀区分是哪条链上的仓 —— 两支持仓量级差 10 倍，混在一起看不清。
        "address": "0x5e784c1a19cef648eda4178b5a74e4963b4dc428",
        "nickname": "THEO·EVM",
        "sourceName": "用户点名 · GMGN 公开钱包（EVM）",
        "sourceUrl": "https://gmgn.ai/eth/address/0x5e784c1a19cef648eda4178b5a74e4963b4dc428",
        "sourceEvidence": (
            "用户 2026-11-17 给出 https://gmgn.ai/profile/theonomix 并要求「theo 的所有"
            "公开钱包都要监控上」；该主页「公开钱包(2)」= SOL 1 个（Bi4rd5…YdLt，已在库）"
            "+ EVM 1 个（本条，主页显示 $2.31 万稳定币 / $1.19 万原生币）。"
            "GMGN 不给匿名接口拿钱包列表（profile 页 pageProps 为空、user_profile 要 web "
            "token、KOL 榜只覆盖前 100），EVM 这支是用户从主页直接复制提供的。"
            "链上核验（2026-11-17，公共 RPC 直连）：BSC nonce 12,169 / 7.2432 BNB；"
            "ETH nonce 333 / 2.9411 ETH；Base nonce 319 / 0.6439 ETH；"
            "Robinhood nonce 4,274 / 0.0536 ETH —— 四条链都在用，普通 EOA。"
            "成交画像（GMGN wallet_activity，每链 60 条样本）："
            "BSC 30 笔买入、中位 $210、最大 $901，$5~6 碎单 12 笔后直接跳到 $116（干净的空档）；"
            "ETH 41 笔、中位 $401、最大 $1,134；Base 34 笔、中位 $969、最大 $2,122；"
            "Robinhood 11 笔、中位 $177、最大 $398。"
        ),
        # 四条链都真在用，base58 那支的教训反过来用：这里必须显式写全，缺省会漏链。
        "chains": ("bsc", "ethereum", "base", "robinhood"),
        # 100U 卡在 BSC 那个 $6 → $116 的空档上：四条链的碎单（$5/8/19/39）全滤掉，
        # 真实买单留下 BSC 18/30、ETH 33/41、Base 31/34、Robinhood 10/11。
        # 四条链加起来约 1.5 笔/天，叠上「同币当天只弹第一条」后更少。
        # 2026-11-17 用 4 笔真实 BSC 买单跑过 `classify_evm_buy`：SWORDCAT $236.35（GMGN
        # $230.74）、RM $100.31（$115.76）—— 口径差 ±3~15%（我们按 tx 实付 BNB × 现货价，
        # GMGN 多跳单按池价折算），100U 门槛下都判 eligible，$4.55 的碎单正确排除。
        "alertThresholdUsd": 100.0,
    },
    {
        "address": "BQVz7fQ1WsQmSTMY3umdPEPPTm1sdcBcX9sP7o6kPRmB",
        "nickname": "Limfork",
        "sourceName": "用户提供 · Solana",
        "sourceUrl": "https://solscan.io/account/BQVz7fQ1WsQmSTMY3umdPEPPTm1sdcBcX9sP7o6kPRmB",
        "sourceEvidence": (
            "用户 2026-11-07 指定：「SOL链的这个聪明钱也做追踪和弹窗提示……KOL是 Limfork」。"
            "链上核验（2026-11-07 08:xx，走 Clash 7890，publicnode/official 双端点）："
            "余额 264.7 SOL、1036 个有余额的代币账户，2026-08-02 起有历史，"
            "2026-11-06 23:32 有它**自己签名**的成功交易 —— 是真实交易钱包。"
            "**但它被机器人灌垃圾交易**：2026-11-07 02:34~08:11 之间，"
            "它的签名列表被 25000 笔/4 分钟（≈100 笔/秒）的**全部失败**交易占据，"
            "其中它只是被当作只读账户引用（蹭标签）。因此 limit=20 的窗口只覆盖 ~0.2 秒，"
            "真实买入会被挤掉 —— 所以配套把 SOLANA_SIGNATURE_LIMIT_DEFAULT 提到 200 并"
            "在命中上限时放大到 1000（见该常量注释）。"
            "它自己签名的交易极少（抽样 25000 笔里成功 6 笔），阈值暂定 500U，"
            "待积累到足够真实买单后按中位金额校准。"
            "2026-11-15 17:30：7 天样本够了，500U → 250U（每日首单后 1.6 → 3.1 条/天，"
            "他的中位单笔只有 $22.8，500U 把大部分加仓都挡掉了）。"
        ),
        "chains": ("solana",),
        "alertThresholdUsd": 250.0,
    },
)

# ---------------------------------------------------------------------------
# 用户的「追踪钱包」清单（2026-10-27 提供，20 个 EVM 地址）
#
# 需求原话：「这是我的追踪钱包……之前做了一个 3 个 KOL 同车提醒，然后在这个基础上，
# 如果在这个同车标的上还有其他的 KOL 再进来，继续做同车增加的提醒」。
#
# 这 20 个里有 5 个本来就在库里（NΞO / 阿峰 / cowboy / Inq / CryptoCharming），
# 剩下 15 个纯粹为了「同车增加」接入：
#
# * **只记录买入、不对单笔弹窗** —— 阈值给 10 亿 U，等价于「永不弹单笔」，但事件表
#   照样落每一笔，这正是判定「他有没有上这辆车」需要的原料。
# * **扫描面 = BSC / ETH / Base**：15 个地址三条链 nonce 全部非零（BSC 从 135 到
#   46.7 万不等，都是老手），给全三条才不漏。Robinhood 没给 —— 扫描窗口贵，且这批
#   地址是 EVM meme 盘的口味。
# * 提示：这些钱包一旦在面板里把阈值调低，就会开始按单笔弹窗了（面板行为，不是硬编码）。
# ---------------------------------------------------------------------------
KOL_TRACK_RECORD_ONLY_THRESHOLD_USD = 1e9

TRACKED_KOL_AUX_WALLETS: tuple[str, ...] = (
    "0x3391a39a1b508e54a361924a26056c01c1c2c07d",
    "0x4d9644d05fe2123b4eafa8d7fd31b0ea430726f3",
    "0xd59b6a5dc9126ea0ebacd2d8560584b3ce48f62f",
    "0x3472ccf6209925204b9cf352171ee150904fda7e",
    "0x01db37579e55ce13f4504019025e36047bdad845",
    "0x7e8fb0392542812476d9f2d0d71c01d1fa0776c5",
    "0xeb89055e16ae1c1e42ad6770a7344ff5c7b4f31d",
    "0x877af245c61289b24f0c619a4346cdbc68f1aaac",
    "0x7a2363a401b2340c7941dd2eeff0196a5078d2e6",
    "0xfe277aa25a4f65a182c0ec135854794eb7131e79",
    "0xddac928a240bdace3994c2cc0783d4e29a002127",
    # 0xc2c6acd377458010713e733e1b21dd6f670d091c —— 用户 2026-11-20 点名要弹窗
    # （「区块日记的买入记录怎么没有弹出来」），已从这里移出、单开一条 seed（见下方
    # QUKUAI_RIJI_WALLET）。同车观察面仍是 20 人：KOL_SYNC_TRACK_LABELS 补了名字。
    # 这三个用户后来点名要弹窗，已从这份「只记录、不弹窗」清单里移出，
    # 改成下面各自的独立 seed（antiposition / 米神 / 枯坐P小将）：
    #   0xa83b73f5644cde337b61da79589f10ea15548811
    #   0xa7d4ffc4eca3c71af150ce302560a9d04a1d2b9f
    #   0x2ce9d43d1cba6ae31d7f07bfe0098dfa2d833373
)


# ---------------------------------------------------------------------------
# 区块日记（用户 2026-11-20：「0xc2c6acd377458010713e733e1b21dd6f670d091c，
# 区块日记的买入记录怎么没有弹出来」）
#
# 这个地址原本是 20 个「追踪钱包」之一：一直在轮询、每笔都落库，但阈值是 10 亿 U，
# 等价于「永不弹单笔」（历史 394 笔 popup_eligible 全为 0，details 里写着
# alertThresholdUsd=1000000000）。用户点名要看它，于是按 antiposition/米神/枯坐
# 的同一套流程升级：移出 aux 清单 → 单开 seed（认领旧昵称）→ 同车名单补名字。
#
# 阈值推算用的是库里**已经记录**的真实买单（2026-11-20 近 24h，23 笔 BSC）：
#   6 / 57·57·58 / 101·116·131·134 / 200 ← 试探单；286·286·286·286·287·287·289·290·291
#   ← 0.5 BNB 标准档（9 笔）；400·464·465·571·1,163 ← 加大仓（5 笔）。
# 各档命中：≥$300 → 5 笔/天（会把标准档整档挡掉，因为 0.5 BNB ≈ $287）；
#           ≥$285 → 14 笔/天；≥$200 → 13~14 笔/天 ← 用户 2026-11-20 定这档；
#           ≥$100 → 17 笔/天。
# 演变：先按「≥$300 只弹加大仓」开了一天，用户随后要求「改成 200」——
# 他要的是连带 $200 那笔试探单在内、每笔有意义的买入都看得见，噪音知情。
# ---------------------------------------------------------------------------
QUKUAI_RIJI_WALLET = "0xc2c6acd377458010713e733e1b21dd6f670d091c"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": QUKUAI_RIJI_WALLET,
        "nickname": "区块日记",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{QUKUAI_RIJI_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-20：「0xc2c6acd377458010713e733e1b21dd6f670d091c，区块日记的"
            "买入记录怎么没有弹出来」。链上核验：BSC/ETH/Base 三条链 nonce 均非零（老手地址），"
            "原先在「用户追踪钱包清单」里只记录、不弹窗（阈值 10 亿 U，394 笔零弹窗）。"
            "近 24h 23 笔 BSC 买单：试探单 $57~200、标准档 0.5 BNB ≈ $286~291（9 笔）、"
            "加大仓 $400~1,163（5 笔）。阈值 2026-11-20 定 300U（≈5 条/天），"
            "当天改成 200U（≈13 条/天，连 $200 试探单一起放行）。"
        ),
        # 三条链本来就在轮询（清单就是 BSC/ETH/Base），保持扫描面不变
        "chains": ("bsc", "ethereum", "base"),
        "alertThresholdUsd": 200.0,
        # 旧昵称是 aux 清单自动生成的「追踪 · 0xc2c6…091c」；登记成 legacy 标签后
        # 重启自动改名，不用手工 UPDATE（改完才认得出弹窗里是谁）。
        "legacyLabels": (("追踪 · 0xc2c6…091c", "用户追踪钱包清单"),),
    },
)


# ---------------------------------------------------------------------------
# antiposition（用户 2026-11-11 点名：「0xa83b…8811，antiposition，bsc链的聪明钱」）
#
# 这个地址原本在 TRACKED_KOL_AUX_WALLETS 里 —— 那份清单是「只记录、不对单笔弹窗」
# （阈值 10 亿 U），只用来给同车提醒凑人数。现在用户点名单笔也要弹，所以移出那份
# 清单、单开一条 seed。**同车观察面仍是 20 人**：KOL_SYNC_TRACK_LABELS 里单独登记了
# 它的名字，别再从那边删。
#
# 链上核验（2026-11-11）：BSC nonce 477,484、余额 74.0052 BNB，普通 EOA（无 7702 委托）。
#
# 阈值推算用的是库里**已经记录**的真实买单（这个地址一直在被轮询，只是不弹窗）：
# 2026-10-28 19:57 → 11-11 18:45 共 334.8 小时、695 笔 BSC 买单，
#   中位 $522 / p25 $374 / p75 $536 / p90 $577 / 最大 $3,848，
#   绝大多数是 0.76 BNB 一笔的高频狙击（≈2.1 笔/小时、50 笔/天）。
# 各档命中：
#   ≥$500  → 404 笔（58%）= 每天约 29 条，太吵，会把别的提醒全盖住；
#   ≥$700  →  57 笔（8.2%）= 每天约 4 条；
#   ≥$1000 →  25 笔（3.6%）= 每天约 1.8 条 ← 取这档；
#   ≥$2000 →   3 笔（0.4%）= 每天约 0.2 条，太少，错过他加大仓位的动作。
# 用户可在面板里按钱包改（改过之后 seed 不再覆盖）。
# ---------------------------------------------------------------------------
ANTIPOSITION_WALLET = "0xa83b73f5644cde337b61da79589f10ea15548811"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": ANTIPOSITION_WALLET,
        "nickname": "antiposition",
        "sourceName": "用户点名 · BSC 聪明钱",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{ANTIPOSITION_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-11：「这个Kol也加上：0xa83b73f5…8811，antiposition，bsc链的聪明钱」。"
            "链上核验：BSC nonce 477,484、余额 74.0052 BNB、普通 EOA。"
            "买入形态是高频狙击（50 笔/天，单笔多为 0.76 BNB ≈ $520）。"
            "阈值演变：2026-11-11 定 1000U（≈1.8 条/天，500U 会到每天 29 条）；"
            "2026-11-12 用户看到「币安生活」三 KOL 同买零弹窗，要求 cowboy / 阿峰 / "
            "antiposition 统一 300U —— 代价是这一条每天约 35 张，用户知情。"
        ),
        # 用户明确「bsc链的聪明钱」：只挂 BSC。ETH/Base 上它 14 天里各只有 20 笔上下，
        # 挂上去纯白占扫描窗口（BSC 还要走「按区块扫 from」的兜底路径）。
        "chains": ("bsc",),
        "alertThresholdUsd": 300.0,
        # 这个名字以前是「追踪 · 0xa83b…8811」/「用户追踪钱包清单」（还是 aux 清单的时候），
        # 登记成 legacy 标签后重启会自动改名，不用手工 UPDATE。
        "legacyLabels": (("追踪 · 0xa83b…8811", "用户追踪钱包清单"),),
    },
)

# ---------------------------------------------------------------------------
# 米神（用户 2026-11-11：「0xa7d4…2b9f，这个也接上，KOL米神的」）
#
# 和 antiposition 一样，这个地址原本也在 TRACKED_KOL_AUX_WALLETS 里（只记录、不弹窗）。
#
# 链上核验（2026-11-11）：BSC nonce 19,397 / 56.06 BNB、ETH nonce 184 / 0.97 ETH、
# Base nonce 119 / 1.10 ETH，三条链都是普通 EOA。
#
# 阈值推算（库里 107 笔 BSC 买单，2026-10-29 23:14 → 11-11 19:03，307.8 小时≈12.8 天）：
#   中位 $688 / p25 $345 / p75 $713 / p90 $1,431 / 最大 $2,778；约 8 笔/天。
#   BNB 档位分布极干净：0.5×29、1.0×47、1.5×2、2.0×11、3.0×1、4.0×7，另有零星 0.07~0.3。
#   → 0.5 BNB（≈$340）是他的**试探单**，1 BNB（≈$680）才是**建仓单**。
# 所以阈值取 500U：正好卡在 0.34k 与 0.68k 之间，把 29 笔试探单挡在门外，
# 1 BNB 及以上的 68 笔全放行 → 约 5.3 条/天（和 Inq / cowboy 同档，符合 BSC KOL 惯例）。
# 想更清静可以把面板里的阈值调到 1000U（=1.5 BNB 起，约 1.6 条/天），别再往 700U 调 ——
# 1 BNB = $680 会被误伤掉，那是他最主要的建仓档。
#
# **2026-11-12 用户改口径为 300U**：MIRO 那笔 $325.94（0.5 BNB 试探单）因服务停机 +
# 500U 门槛双重漏掉，用户要求「降」。实测 122 笔/13.8 天：300U → 6.1 张/天，
# 500U → 4.5 张/天，只多 1.6 张，换来试探单可见。
# ---------------------------------------------------------------------------
MI_SHEN_WALLET = "0xa7d4ffc4eca3c71af150ce302560a9d04a1d2b9f"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": MI_SHEN_WALLET,
        "nickname": "米神",
        "sourceName": "用户点名 · BSC 聪明钱",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{MI_SHEN_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-11：「0xa7d4ffc4…2b9f，这个也接上，KOL米神的」。"
            "链上核验：BSC nonce 19,397 / 56.06 BNB（另有 ETH 0.97、Base 1.10），普通 EOA。"
            "买单形态：0.5 BNB 试探、1 BNB 建仓（1.5/2/3/4 BNB 是加仓），约 8 笔/天。"
            "阈值演变：2026-11-11 定 500U（只滤掉 0.5 BNB 试探单，约 5.3 条/天）；"
            "2026-11-12 用户看到 MIRO 那笔 $325.94 后要求降到 300U —— 实测 6.1 张/天，"
            "只多 1.6 张，换来看得见 0.5 BNB 试探单。"
        ),
        # 只挂 BSC：ETH 近 15 天 0 笔买入、Base 只有 1 笔，挂上去纯白占扫描窗口。
        "chains": ("bsc",),
        "alertThresholdUsd": 300.0,
        "legacyLabels": (("追踪 · 0xa7d4…2b9f", "用户追踪钱包清单"),),
    },
)

# ---------------------------------------------------------------------------
# leslie（用户 2026-11-11：「0x7942…1f87，kol leslie的」）
#
# 全新地址（库里、追踪清单里都没有），所以阈值只能靠 GMGN 的真实成交定 ——
# ``openapi.gmgn.ai/v1/user/wallet_activity``（带 X-APIKEY + 7890 代理、写 ``cursor``
# 翻页，每页固定 20 条，别连发否则 429）。
#
# 链上核验（2026-11-11）：BSC nonce 44,823 / 0.7474 BNB、ETH nonce 1,061 / 0.0383 ETH、
# Base nonce 985 / 0.0002 ETH，三条都是普通 EOA。
#
# 成交画像（GMGN，2026-11-11）：
#   **BSC**（在动）：20.9 小时内 117 笔买入，中位 $22 / p75 $66 / p90 $68 / 最大 $331。
#     金额是**双峰**的：一大坨 $1~83（1/1/1/3/3/6/6… 这种碎单，机器人式的试仓），
#     然后直接跳到 $180~331 共 11 笔（180/211/232/246/262/269/283/328/329/331）。
#     中间 $84~179 是**空档** —— 阈值就卡在这里，$150 既滤掉全部碎单、又不误伤建仓单。
#   **ETH**（已停）：28 笔，6/23 → 10/6，中位 $1,313 / p75 $1,849 / 最大 $3,934。
#   **Base**（已停）：14 笔，9/5 → 10/4，中位 $607 / 最大 $971。
#   Solana：没有任何活动。
# 他在 ETH/Base 上的真实体量比 BSC 大一个数量级，虽然一个多月没动，还是把这两条挂上
# （只是两个日志 topic 过滤，成本可以忽略）；哪天真回来了不至于漏掉。
#
# 阈值 150U：BSC 侧约 10~12 条/天（他活跃日）。嫌吵就在面板里调到 300U（≈4~5 条/天），
# 那会开始漏掉 $180~260 那批，建议优先调 250U。
# 注意：这条 seed **没有**进 KOL_SYNC_TRACK_LABELS —— 同车提醒的观察面是「用户 10-27
# 给的 20 个追踪钱包」，leslie 是单独点名的，先进监控、不动那个 20 人集合。
# ---------------------------------------------------------------------------
LESLIE_WALLET = "0x7942262a7a5637e6eb0eec9b91b55ec232141f87"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": LESLIE_WALLET,
        "nickname": "leslie",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{LESLIE_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-11：「0x7942262a…1f87，kol leslie的」。"
            "链上核验：BSC nonce 44,823 / 0.75 BNB、ETH 1,061 笔 / 0.04 ETH、Base 985 笔 / 0.0002 ETH。"
            "GMGN 成交：BSC 走小单高频（20.9 小时 117 笔，中位 $22，最大 $331，$84~179 是空档），"
            "ETH 才是他的真实体量（28 笔，中位 $1,313，最大 $3,934，最后一个多月没动）。"
            "阈值 150U 卡在 BSC 的双峰断点上。"
        ),
        "chains": ("bsc", "ethereum", "base"),
        "alertThresholdUsd": 150.0,
    },
)

# ---------------------------------------------------------------------------
# 枯坐P小将（用户 2026-11-11：「0x2ce9…3373，KOL枯坐P小将」）
#
# 也在 TRACKED_KOL_AUX_WALLETS 里（只记录、不弹窗），照前两个的流程升级。
#
# 阈值依据（库里 57 笔 BSC 买单，2026-10-28 20:06 → 11-11 20:27，14.01 天，≈4.1 笔/天）：
#   中位 $475 / p25 $470 / p75 $484 / p90 $897 / 最大 $1,363。
#   BNB 档位：**0.69×40（标准仓 ≈$470）**、1.3×3（$897）、2.0×3（$1,363）、
#             0.15~0.5 BNB×11（$102~350，小仓试水）。
# → 阈值 400U：把他的标准仓（0.69 BNB）和加仓全放行，只滤掉那 11 笔 $102~350 的试水，
#   约 3.3 条/天。定 500U 会把 40 笔标准仓全挡在门外，只剩每天 0.6 条，等于只看加仓。
#
# 三条链都留：ETH / Base 各还有 5 笔真实买入（ETH $315~786、Base $232~847），
# 不像 米神 那样只挂 BSC。
# ---------------------------------------------------------------------------
KUZUO_WALLET = "0x2ce9d43d1cba6ae31d7f07bfe0098dfa2d833373"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": KUZUO_WALLET,
        "nickname": "枯坐P小将",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{KUZUO_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-11：「0x2ce9d43d…3373，KOL枯坐P小将」。"
            "库里 57 笔 BSC 买单实测：0.69 BNB（≈$470）是标准仓（40 笔），"
            "1.3/2 BNB 是加仓，0.15~0.5 BNB 是小仓试水；ETH/Base 各有 5 笔。"
            "阈值 400U：标准仓与加仓全弹，试水单不弹，约 3.3 条/天。"
        ),
        "chains": ("bsc", "ethereum", "base"),
        "alertThresholdUsd": 400.0,
        "legacyLabels": (("追踪 · 0x2ce9…3373", "用户追踪钱包清单"),),
    },
)


# ---------------------------------------------------------------------------
# 金狗挖掘机（用户 2026-11-12：「这个是KOL金狗挖掘机的SOL链地址：J485Y…kLxRr」）
#
# 第一条**纯 Solana** 的 KOL 点名钱包（前面几个都是 EVM 地址），所以 chains 必须显式
# 写 ("solana",) —— 不写会默认给他铺 4 条 EVM，base58 地址在 EVM 上根本不存在，
# 白烧扫描窗口，而且会让 SOLANA_TX_BUDGET_PER_ROUND 被别的链挤掉。
#
# 阈值依据（GMGN wallet_activity，120 笔 / 8.23 天，66 笔买单，≈8.0 笔/天）：
#   $8~9 ×22（机器化的碎单）、$79~99 ×5、$191 ×1、**$244~343 ×35（标准仓）**、
#   $1,730 / $1,799 ×2（重仓）。断点：$99 → $191、$343 → $1,730。
# → 阈值 300U：卡在标准仓的上沿，标准仓里偏重的 17 笔 + 2 笔重仓放行，约 2.3 条/天。
#   想「他一建仓就报」（标准仓 $244 起全收）得降到 250U，约 4.3 条/天；
#   想只看重仓就 350U，但那就只剩 0.2 条/天（等于不报）。
# ---------------------------------------------------------------------------
JINGOU_WALLET = "J485YzQjuJPLYoFEYjrjxd7NAoLHTiyUU63JwK7kLxRr"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": JINGOU_WALLET,
        "nickname": "金狗挖掘机",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/sol/address/{JINGOU_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-12：「KOL金狗挖掘机的SOL链地址」。"
            "GMGN 实测：8.23 天 120 笔（买 66 / 卖 54，≈8 笔/天），标准仓 $244~343（35 笔），"
            "$8~9 的碎单 22 笔（机器化）、$79~99 试水 5 笔、$1,730 / $1,799 重仓 2 笔。"
            "阈值 300U：标准仓上沿 + 重仓，约 2.3 条/天，碎单与试水全部滤掉。"
        ),
        "chains": ("solana",),
        "alertThresholdUsd": 300.0,
    },
)


# ---------------------------------------------------------------------------
# K99 | 1000X GEM（用户 2026-11-12：「K99 | 1000X GEM，SOL链的地址」）
#
# 同样是纯 Solana，chains 只写 ("solana",)。
#
# 这个地址是「撒网型」：235 天 120 笔，长期单笔 $8~20（中位 $20），
# 但 11-12 当天突然密集建仓：18 笔买 / 中位 $85 / p25 $42 / p75 $95 / 最大 $170，
# 金额几乎都压在 0.5 SOL 一档；7 天窗口 42 笔（6 笔/天）、30 天 70 笔（2.3 笔/天）。
# → 阈值 50U：只放行 0.5 SOL 这档真仓，$8~20 的试水全部滤掉。
#   注意他是「同一个币连买很多笔」的打法，靠 5 分钟同币合并窗口收口，
#   否则一次建仓能刷出七八张卡片。
# ---------------------------------------------------------------------------
K99_GEM_WALLET = "qTB6ryTHBU1tGEVg9a1QiRJh5euRAU3bcDdWubUJ4ne"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": K99_GEM_WALLET,
        "nickname": "K99 | 1000X GEM",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/sol/address/{K99_GEM_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-12：「K99 | 1000X GEM，SOL链的地址」。"
            "GMGN 实测 120 笔 / 235 天：长期是 $8~20 的碎单（中位 $20，77 笔买），"
            "但 2026-11-12 当天进入密集建仓（18 笔买，中位 $85、p25 $42、p75 $95、最大 $170，"
            "基本是 0.5 SOL 一档）。阈值 50U 落在「$8~20 试水」与「0.5 SOL 标准仓」之间，"
            "同一标的的连续加仓会被 5 分钟合并窗口收成一条。"
        ),
        "chains": ("solana",),
        "alertThresholdUsd": 50.0,
    },
)


# ---------------------------------------------------------------------------
# Latuche（用户 2026-11-12：「GJA1HEb…SU65，kol：Latuche，sol链的」）
#
# 第三个纯 Solana 钱包，但**节奏完全不同**：前两条是「一天几笔」，他是一条
# 120 笔只覆盖 3.35 小时（2026-11-12 03:13 → 06:34）—— 23 笔买/小时的高频加仓，
# 3.35 小时砸了 $23,856，其中 $17,863（75%）全压在同一个币（Max 5qaP7oKUqK…）上。
#
# 金额只有两档：**$273（≈1.5 SOL）和 $846（≈4.6 SOL）**，p75=$275、max=$846，
# 中间 276~845 几乎没有成交 —— 500U 正好落在空档里，只放行重的那一档。
# 模拟真实合并逻辑（同币 5 分钟窗口）：500U 在 3.35 小时里只弹 **4 张**（≈1.2 张/小时），
# 因为他连发的 846 会自然并进同一窗口，卡片正文还会带「近 5 分钟另 N 笔 +$X」。
# 800U 只少一张（3 张），所以没必要为了安静牺牲那一档。
# ---------------------------------------------------------------------------
LATUCHE_WALLET = "GJA1HEbxGnqBhBifH9uQauzXSB53to5rhDrzmKxhSU65"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": LATUCHE_WALLET,
        "nickname": "Latuche",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/sol/address/{LATUCHE_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-12：「GJA1HEb…SU65，kol：Latuche，sol链的」。"
            "GMGN 实测 120 笔 / 3.35 小时（买 78 / 卖 42）：单笔只有 $273 与 $846 两档，"
            "75% 金额集中在同一个币上，属于高频单币加仓。"
            "阈值 500U 卡在 $275 → $846 的空档，同币 5 分钟合并后约 4 张/3.35 小时。"
        ),
        "chains": ("solana",),
        "alertThresholdUsd": 500.0,
    },
)


# ---------------------------------------------------------------------------
# yukaz（用户 2026-11-12：「0xa05ec35f…742f，bsc链的，kol是yukaz」）
#
# 用户点名只盯 BSC，所以 chains 只写 ("bsc",)（同 米神 / antiposition 的口径）。
# 链上核验：BSC nonce 36,442 / 余额 41.46 BNB、ETH 651 / 0.48 ETH、Base 3,027 / 4.29 ETH ——
# 是真金白银的老手，不是空壳地址。
#
# 阈值依据（GMGN，100 笔 / 1.07 天，63 笔买单 ≈ 59 笔/天，属于机器化高频）：
#   - $7 一档 38 笔（扫链试单，必须滤）
#   - **$400~499 一档 22 笔、分布在 22 个不同币上**（≈0.5 BNB 一个仓，一天撒 22 个标的）
#   - $500 以上只有 3 笔（2 个币），最大 $1,345
# → 阈值 500U：只留顶格的 3 笔，5 分钟同币合并后 **2 张/1.07 天（≈1.9 张/天）**。
#   若降到 400U 去接那 22 个 0.5 BNB 标准仓，实测会变成 **20.6 张/天**（22 个币互不合并，
#   靠同币窗口收不住）—— 那是刷屏，不是提醒。
# ETH（中位 $708 / 最大 $14,689 / 0.2 笔/天）才是他真正的大钱所在，但用户只要 BSC，
# 需要时把 "ethereum" 加进 chains 即可。
# ---------------------------------------------------------------------------
YUKAZ_WALLET = "0xa05ec35f7d1eba823cff2ed26aeaed419683742f"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": YUKAZ_WALLET,
        "nickname": "yukaz",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{YUKAZ_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-12：「0xa05ec35f…742f，bsc链的，kol是yukaz」。"
            "链上核验：BSC nonce 36,442 / 41.46 BNB，ETH 651、Base 3,027 均有真实活动。"
            "GMGN 实测 100 笔 / 1.07 天（买 63，≈59 笔/天）：$7 扫链单 38 笔，"
            "0.5 BNB（$400~499）标准仓 22 笔但分散在 22 个币上，$500 以上仅 3 笔（最大 $1,345）。"
            "阈值 500U：合并后约 1.9 张/天；降到 400U 会变成 20.6 张/天。"
        ),
        "chains": ("bsc",),
        "alertThresholdUsd": 500.0,
    },
)


# ---------------------------------------------------------------------------
# cowboy 的 Solana 钱包（用户 2026-11-12：「cowboy的sol链买入也监控上」）
#
# cowboy 本体是 0xe1e3252b…9407（BSC/ETH/Base，500U），这条是他 Solana 侧的地址，
# 同一个人的第二套链，所以 nickname 保持一致 —— 弹窗里看到的还是「cowboy」。
#
# 阈值演变：用户先定 200U，实测合并后 5.3 张/天，随后改到 300U（1.5 张/天）；
# **2026-11-15 17:30 在「每日首单」上线后又降到 250U（≈1.0 张/天）**。
# 实测代价（GMGN 120 笔 / 5.81 天，买 56 笔 ≈9.6 笔/天，中位 $251 / p25 $85 / 最大 $873）：
#   ≥100U 32 张、≥200U 31 张（5.3 张/天）、≥250U 24 张、**≥300U 9 张（1.5 张/天）**、≥500U 7 张。
# 注意 EVM 那三条链降到了 200U，Solana 侧**故意停在 250U**：200U 会把这里推到 5 张/天。
# ---------------------------------------------------------------------------
COWBOY_SOL_WALLET = "5LCniWCFQFBqcPiZNf1VrRqt4qr73sqfzZHRCYcXS6VS"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": COWBOY_SOL_WALLET,
        "nickname": "cowboy",
        "sourceName": "用户提供 · KOL",
        "sourceUrl": f"https://gmgn.ai/sol/address/{COWBOY_SOL_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-12：「cowboy的sol链买入也监控上」，阈值先定 200U 后改 300U。"
            "与 0xe1e3252b…9407（BSC/ETH/Base，500U）同一人，Solana 侧钱包。"
            "GMGN 实测 120 笔 / 5.81 天（买 56，≈9.6 笔/天），中位 $251、p25 $85、最大 $873；"
            "阈值 300U → 合并后 9 张 / 5.81 天（≈1.5 张/天）；"
            "2026-11-15 17:30 每日首单上线后降到 250U，实测 ≈1.0 张/天。"
        ),
        "chains": ("solana",),
        "alertThresholdUsd": 250.0,
    },
)


# ---------------------------------------------------------------------------
# Solana KOL 组（用户 2026-11-15 连着几次点名）
#
#   「这个是KOL：0xsun的地址，SOL链的：HUpPy…」
#   「sol链KOL gake的地址：DNfuF…，也加上」
#   「SOL链再加几个，冷静：Ay9wnu…；Ansem：AVAZvH…」
#   「还有这个：CyaE1Vx…，Cented7」        ← 当天 12:13 又要求「cented的监控取消掉吧」
#
# 每条都是用户点名时就说死了链（SOL），所以 chains 只写 ("solana",) —— 千万别省成
# 默认的 EVM_CHAINS，那会把同一个地址复制到 bsc/ethereum/base/robinhood 四条链上
# 各轮询一遍（纯属白烧 RPC）。
#
# 身份核对（2026-11-15，GMGN ``/defi/quotation/v1/smartmoney/sol/walletNew/<addr>``，
# 昵称与推特都对得上，不是同名地址）：
#   0xsun  → 0xSun / @0xSunNFT（粉丝 21.1 万）
#   gake   → gake / @Ga__ke（18.5 万）
#   冷静   → 冷静冷静再冷静 / @hexiecs（9.0 万）
#   Ansem  → Ansem / @blknoiz06（96.5 万）
#   4BQ6…  → GMGN 无名无推特（昵称只能先用地址缩写，等用户给真名）
#
# 阈值全部落在 500U，但**每一条的理由都不一样**，逐条写在 evidence 里：
#
#   * gake（买 1 笔/天，p50 $735 / p75 $2,421 / max $7,047）：他本来就是大票打法，
#     500U 放行 68/134 笔，合并后 0.6 张/天。
#   * Ansem（买 2 笔/天，p50 $406 / p75 $1,011 / max $2,550）：500U 合并后 0.8 张/天；
#     降到 300U 只多 0.2 张，不值得为此接一堆小额补仓。
#   * 0xsun / 冷静：这两个地址**当前根本不交易**（GMGN buy_7d/buy_30d 都是 0，
#     SOL 余额 0.12 / 0.001），链上近一个月只有发币与免费分发（SOL 净支出 = 0）。
#     照加，是为了用户点名就要盯：他们一开仓就能弹。但也别指望立刻有动静，
#     而且**收到白给的空投不算买入**（没有付款方，classify_solana_buy 按设计不出事件）。
#
# **cented 已按用户要求撤下（2026-11-15 12:13「cented的监控取消掉吧」）**：
# 他的地址 11-07 就被 X 追踪自动抓成 ``personal-x`` 行入池（昵称空、阈值 10000U，
# 从没弹过），我当天把它改成 500U 之后用户嫌吵要撤。撤法保留在数据里而不是删行：
# 该行 ``enabled=0``（历史事件与游标都留着，面板里随时能重新打开），代码里也不再留
# 他的种子 —— 否则换台机器从零建库时又会把他加回来。
# 注意这类 ``personal-x`` 行的两个特性：``seed_wallets`` 是 INSERT OR IGNORE、
# ``apply_seed_thresholds`` 只认 source_kind='seed'，所以种子的增删都改不动它，
# **必须直接 ``save_wallet``**。
# ---------------------------------------------------------------------------
SOLANA_KOL_WALLETS: tuple[dict[str, Any], ...] = (
    {
        "address": "HUpPyLU8KWisCAr3mzWy2FKT6uuxQ2qGgJQxyTpDoes5",
        "nickname": "0xsun",
        "threshold": 500.0,
        "evidence": (
            "用户 2026-11-15：「这个是KOL：0xsun的地址，SOL链的」。"
            "GMGN 档案：0xSun / @0xSunNFT（21.1 万粉），SOL 余额 0.12；"
            "buy_7d = buy_30d = 0，最后一笔成交停在 2026-07-15（且都是 $0~2 的尘埃单）。"
            "链上复核（2026-10-15 → 11-12 的 60 笔成功交易）：全是发币/免费分发，"
            "没有一条 swap 程序，钱包 SOL 净支出恒为 0。"
            "结论：这个地址目前不交易，加了也不会弹；一旦开仓即以 500U 门槛提醒。"
            "注意他收到的白给分配不算「买入」（无付款方，按设计不产生事件）。"
        ),
    },
    {
        "address": "DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm",
        "nickname": "gake",
        "threshold": 500.0,
        "evidence": (
            "用户 2026-11-15：「sol链KOL gake的地址：DNfuF…，也加上」。"
            "GMGN 档案：gake / @Ga__ke（18.5 万粉），SOL 余额 110.4，"
            "buy_30d 12 / sell_30d 20，30 天 PnL -33.4%（亏着做，是真的自己在打）。"
            "GMGN 240 笔 / 122.5 天：买 134 笔（1 笔/天），p50 $735、p75 $2,421、max $7,047。"
            "阈值 500U → 68 笔 / 合并后 0.6 张/天：他本来就是大票打法，小额单本来就少。"
        ),
    },
    {
        "address": "Ay9wnuZCRTceZJuRpGZnuwYZuWdsviM4cMiCwFoSQiPH",
        "nickname": "冷静",
        "threshold": 500.0,
        "evidence": (
            "用户 2026-11-15：「SOL链再加几个，冷静：Ay9wnu…」。"
            "GMGN 档案：冷静冷静再冷静 / @hexiecs（9.0 万粉）。"
            "**该地址当前没有任何成交**：buy_7d = buy_30d = 0，SOL 余额 0.001，"
            "GMGN 的 wallet_activity 一条都取不到。"
            "照用户点名入库，阈值 500U 与同批 KOL 对齐；真开始交易就会弹。"
            "如果长期无声，多半是地址给错了（同一人的活跃钱包可能在别处），可以再核。"
        ),
    },
    {
        "address": "AVAZvHLR2PcWpDf8BXY4rVxNHYRBytycHkcB5z5QNXYm",
        "nickname": "Ansem",
        "threshold": 500.0,
        "evidence": (
            "用户 2026-11-15：「Ansem：AVAZvH…」。"
            "GMGN 档案：Ansem / @blknoiz06（96.5 万粉），SOL 余额 0.42，"
            "buy_30d 75 / sell_30d 86，30 天 PnL -3.8%。"
            "GMGN 240 笔 / 44.9 天：买 93 笔（2 笔/天），p50 $406、p75 $1,011、max $2,550。"
            "阈值 500U → 合并后 0.8 张/天；300U 只多 0.2 张，不值得为此接小额补仓。"
        ),
    },
    {
        "address": "4BQ6ATUt26GFdiYQfht23iwfKyYD9D7XbL5ATqNGk3xK",
        "nickname": "KOL · 4BQ6A…k3xK",
        "threshold": 500.0,
        "evidence": (
            "用户 2026-11-15：「还有这个：4BQ6ATUt…k3xK」，并确认「也是SOL链的」。"
            "**GMGN 上查不到名字和推特**（name/twitter 全空、0 粉丝），昵称只能先用地址缩写，"
            "用户给出真名后改这里即可（改代码对已入库行无效，要 save_wallet 或面板改名）。"
            "GMGN 档案：SOL 余额 72.6，buy_7d 4,485 / buy_30d 22,336（≈744 笔/天），"
            "累计已实现盈亏 +$153,075，30 天 PnL +4.8% —— 是台机器级的刷量钱包。"
            "GMGN 网页持仓页显示 Max 累计买入 $24.13万 / 卖出 $358万（持仓只剩 $21.68），"
            "LARP 累计买入 $5.56万、Snoopy $3.01万，全是极高频 churn。"
            "**但链上对不上**：RPC 扫最近 200 笔（4.87 小时），69 笔有代币入账、"
            "0 笔有原生 SOL 或稳定币流出，WSOL 净变化 $0~0.4 —— 说明它的成交不是从"
            "这个地址自己的代币账户结算的（机器人/子账户/代付结构）。"
            "因此本地址**大概率长期不弹**：classify_solana_buy 靠余额差分判买，"
            "看不到付款方就不会产出事件。阈值 500U 是兜「真建仓」的那种大仓，"
            "GMGN 自己的单笔样本只有 $7~$207（20 笔），按它们定阈值等于天天刷屏。"
        ),
    },
    {
        "address": "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk",
        "nickname": "jijo",
        "threshold": 300.0,
        "evidence": (
            "用户 2026-11-15：「这个地址，Sol链的jijo加上：4BdKax…FUk」。"
            "GMGN 档案核对一致：jijo / @jijo_exe（3.06 万粉），SOL 余额 633.3，"
            "buy_7d 300 / buy_30d 1,219（≈40 笔/天），累计已实现 +$46,260，30 天 PnL +33% ——"
            "是这组里少见的「真的在交易且赚着钱」的钱包。"
            "GMGN 40 笔 / 1.21 天实测：买 27 笔，p25 $16 / 中位 $78 / p75 $230 / "
            "p90 $273 / max $1,187。"
            "**阈值 300U 而不是同组的 500U**：他的标准仓就在 $200~$300 这一档，"
            "500U 会把标准仓全挡掉只剩 0.8 张/天；300U 合并后约 2.5 张/天，"
            "正好接住「p75 以上」的建仓。嫌吵可在面板调到 500U（降到 0.8 张/天）。"
            "地址早就在库里：2025-04-14 被 X 追踪抓成 personal-x 行（昵称空、阈值 10000U，"
            "攒了 18 条事件但一次没弹过），种子的 INSERT OR IGNORE 改不动它，"
            "首次部署要补一次 save_wallet(nickname, threshold)。"
        ),
    },
    {
        "address": "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT",
        "nickname": "trunoest",
        "threshold": 300.0,
        "evidence": (
            "用户 2026-11-15 13:49：「SOL链 KOL trunoest：ardinRs…b6AT，监控上」。"
            "GMGN 档案核对一致：trunoest / @trunoest（8,906 粉），SOL 余额 570.4，"
            "buy_7d 2,434 / buy_30d 8,525（≈284 笔/天），累计已实现 +$134,869，"
            "30 天 PnL +16.5% —— 高频但确实赚钱。"
            "GMGN 160 笔 / 3.91 小时实测：买 109 笔，只有两档 —— $2 的试探单（p25）与"
            "**$140~$352 的固定仓**（p75 $140 / p90 $210 / p95 $348 / **max $352**）。"
            "也就是说他单笔上限就是 ~5 SOL，**金额天花板压在 500U 以下**。"
            "2026-11-15 16:29 用户拍板「降到 300」：跟着 $140~$352 那档走，"
            "≥300U 合并后约 36.8 张/天 —— 高峰会盖住别人的提醒，这是已知代价；"
            "他真正异常的大仓（>5 SOL）也落在这条线以上，不会漏。"
            "嫌吵就调回 500U（那档等于一笔都不弹，只在从未有过的大仓时响）。"
        ),
    },
    {
        "address": "87rRdssFiTJKY4MGARa4G5vQ31hmR7MxSmhzeaJ5AAxJ",
        "nickname": "Dior",
        "threshold": 300.0,
        "evidence": (
            "用户 2026-11-15 14:17：「这个地址追踪一下：87rRds…AAxJ，SOL链的KOL：Dior」。"
            "GMGN 档案核对一致：Dior / @Dior100x（12.2 万粉），SOL 余额 622.2，"
            "buy_30d 2,276（≈76 笔/天），已实现盈亏近 7 天 +$38,489、30 天 -5.4% ——"
            "大号、高频，最近一波是赚的。"
            "GMGN 220 笔 / 1.66 天实测：买 187 笔，p25 $1 / 中位 $1 / p75 $35 / p90 $72 / "
            "p95 $75，然后**$76~$494 之间一笔都没有**，再往上是 495 / 500 / 936 / 1,424。"
            "也就是「$1 尘埃 + $75 小仓」与「$495+ 真仓」之间有条干净的空白带。"
            "阈值 300U 落在这条空白带里（合并后 2.4 张/天）："
            "只放行 $495 以上那档真仓，把他成百上千笔 $1 的碎单全滤掉。"
            "放到 500U 只少一张（1.8 张/天），没必要为此牺牲那笔 $495。"
        ),
    },
    {
        "address": "7mGZh58meFf8xsES37bc2hdffEmh8aTDcB2i725Zivyg",
        "nickname": "Hyin",
        "threshold": 500.0,
        "evidence": (
            "用户 2026-11-17 10:48：「这是Hyin的SOL和EVM钱包」（EVM 那支另见 THEO 同一批的"
            "独立条目）。链上核验：余额 **542.1 SOL**，最近 5 笔签名全部成功、最后一笔 "
            "11-17 02:30 UTC —— 真实活跃仓。"
            "GMGN 40 条只覆盖 **0.8 小时**（13 笔买入，频率≈16 笔/小时）："
            "179×2 / 355~358×5 / 718×2 / 1,065×2 / 1,079×2，"
            "≥500U 命中 6 笔、落在 4 个不同标的上。"
            "**没有尘埃档**：最低一笔 $179，说明他不做试探单，直接上大仓。"
            "阈值 500U 取在中位（$358）之上：他那 6 笔 $718~1,079 的真仓全留，"
            "$179~358 那批补仓不逐个吵；再往上调到 800 只剩 2 个标的，会漏掉 718 那档。"
        ),
    },
    {
        "address": "BCagckXeMChUKrHEd6fKFA1uiWDtcmCXMsqaheLiUPJd",
        "nickname": "DV",
        "threshold": 100.0,
        "evidence": (
            "用户 2026-11-17 10:50：「DV的所有公开钱包都要监控上」（SOL + EVM 两个地址）。"
            "链上核验：余额 **506.3 SOL**，最近 5 笔签名**全部失败**（典型的抢跑/狙机器人"
            "特征），最后一笔 11-17 02:52 UTC。"
            "GMGN 40 条只覆盖 **0.1 小时**：36 笔买入里 34 笔是 $7~14 的碎单，"
            "只有 2 笔 $278/283。≥100U 命中 **2 笔 / 2 个标的**。"
            "阈值 100U：把他的碎单全滤掉，又不至于漏掉那两笔真仓（300U 以上一笔不剩）。"
        ),
    },
    {
        "address": "7BNaxx6KdUYrjACNQZ9He26NBFoFxujQMAfNLnArLGH5",
        "nickname": "7bn",
        "threshold": 300.0,
        "evidence": (
            "用户 2026-11-17 10:51：「7BNaxx…LGH5，这个是聪明钱7bn，SOL链的」。"
            "链上核验：余额 **200.8 SOL**，最近 5 笔签名全部成功、最后一笔 11-17 02:51 UTC。"
            "GMGN 40 条覆盖 0.4 小时：19 笔买入从 $28 到 $1,064 连续分布，"
            "中位 $149 / p75 $435 / p90 $1,035，没有明显空档。"
            "各档的不同标的数：≥100U 8 个 / ≥200U 5 个 / ≥300U 4 个 / ≥500U 1 个。"
            "阈值 300U：留下 4 个标的（$337/343/435/995/1,035/1,064 那批），"
            "每天大致 3~6 张；调到 500U 只剩 1 个标的，等于把他中等仓的动作全抹掉。"
        ),
    },
    {
        "address": "8zkgFGVZrDLieViwqiXFCydSX6WL5hsxmUu55yBdsNsZ",
        "nickname": "8kz",
        "threshold": 300.0,
        "evidence": (
            "用户 2026-11-17 10:51：「8zkgFG…dsNsZ，sol聪明钱，取名为8kz」。"
            "GMGN 40 条覆盖 0.6 小时：19 笔买入集中在 $40~240（中位 $153），"
            "尾部两笔 $724 / $1,058。≥100U 命中 17 笔 / **16 个标的**（几乎每笔一个新标的），"
            "≥200U 5 个、≥300U 2 个。"
            "阈值 300U：只留 724 / 1,058 那两笔「加大仓」信号。"
            "他 100~240 那档太密（0.6 小时就 15 笔），全放会直接把提醒刷屏。"
        ),
    },
    {
        "address": "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",
        "nickname": "decu",
        "threshold": 200.0,
        "evidence": (
            "用户 2026-11-17 10:54：「4vw54B…x4rw9Ud9，SOL链KOL，decu的」。"
            "GMGN 档案核对一致：SOL KOL 榜第 1 名，decu / **@notdecu（60,616 粉）**，"
            "tags 含 kol / top_followed / wash_trader / arbitrager。余额 634.2 SOL；"
            "最近 5 笔签名全部失败（与 cented 一样是机器人式刷单）。"
            "GMGN 40 条覆盖 1.0 小时：27 笔买入 = $2×9 试探 + $17~18×9 + "
            "$140, 140, 141, 207, 210, 211, 351；≥100U 7 笔 / ≥300U 只有 1 笔。"
            "阈值 200U：卡在 $141 → $207 之间，留 4 笔 / 4 个标的，"
            "把 $2~18 那一坨（18 笔/小时）全滤掉。"
        ),
    },
    {
        "address": "99i9uVA7Q56bY22ajKKUfTZTgTeP5yCtVGsrG9J4pDYQ",
        "nickname": "zrool",
        "threshold": 80.0,
        "evidence": (
            "用户 2026-11-17 11:23：「99i9uVA7…J4pDYQ；KOL:zrool」。"
            "链上核验：余额 **287.9 SOL**，最近 5 笔签名全部成功、最后一笔 11-17 03:05 UTC。"
            "GMGN 60 条只覆盖 **1.9 小时**、47 笔买入 —— 这是个高频小仓："
            "**36 笔都是 $7 的碎单**，真实档在 $54~95（10 笔），最大一笔 $172（Santa）。"
            "各档标的数：≥50U 11 笔/9 个标的、≥80U 6 笔/5 个标的、≥100U 只剩 1 笔。"
            "阈值 80U 的取舍：他的弹药太小（max $172），金额切不出干净的空白带，"
            "只能「滤掉 $7 那批、留 $80 以上那半个真实档」——"
            "1.9 小时里放行 5 个标的（SATUSHI / Coin / 两个 GONE / CUMFERENCE / BULLET 之类）。"
            "嫌吵就调到 100（只接 $172 那种异常大仓，≈个位数/天）；"
            "想看他全部动作就降到 50（≈4~5 个标的/小时，会把面板刷满）。"
            "另注：$7 那档里同一个 symbol 会对应多个不同合约地址（两个 GONE 就是），"
            "所以「同币当天只弹一次」比的是地址，不会被同名仿盘骗过去。"
        ),
    },
)

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + tuple(
    {
        "address": row["address"],
        "nickname": row["nickname"],
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/sol/address/{row['address']}",
        "sourceEvidence": row["evidence"],
        "chains": ("solana",),
        "alertThresholdUsd": row["threshold"],
    }
    for row in SOLANA_KOL_WALLETS
)


def _short_wallet(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + tuple(
    {
        "address": address,
        "nickname": f"追踪 · {_short_wallet(address)}",
        "sourceName": "用户追踪钱包清单",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{address}",
        "sourceEvidence": (
            "用户 2026-10-27 提供的 20 个追踪钱包之一，专门用于「同车增加」提醒。"
            "链上核验：BSC / ETH / Base 三条链 nonce 均非零（老手地址）。"
            "该条 seed 的阈值故意设为 10 亿 U —— 只记录买入，不对单笔弹窗。"
        ),
        "chains": ("bsc", "ethereum", "base"),
        "alertThresholdUsd": KOL_TRACK_RECORD_ONLY_THRESHOLD_USD,
    }
    for address in TRACKED_KOL_AUX_WALLETS
)

# ---------------------------------------------------------------------------
# Elysium 聪明钱（用户 2026-11-09 点名，并明确「只监控他们在 elysium 的」）
#
# Elysium 这里指 **Kinetiq** 的 Arbitrum Orbit L2：结算层是 HyperEVM，主网 chainId
# 99901，原生币 HYPE，出块 ~100ms，RPC ``https://rpc.elysium.kinetiq.xyz``。
# 注意别和 **Vulcan Forged** 那条同名老链搞混（chainId 1338/1339、LAVA 计价、
# RPC 域名 ``*rpc.vulcanforged.com`` 已 DNS 失效）—— 用户原话里同时出现了这两个名字，
# 但「Hyperliquid 生态 L2」只可能指 Kinetiq 这条。
#
# 链上核验（2026-11-09，主网 head ≈ 310 万块）：两个都是 EOA（非合约），主网都在
# 活跃交易，余额 238.5 / 451.0 HYPE（HYPE ≈ $73）。
#
# 阈值：HYPE 单价高、Elysium 上单笔 swap 量级比主网小一个数量级，所以**不能**套用
# 其它链的 10,000U。2026-11-09 实测两个地址近 12 万个区块（≈3.3 小时，出块 10/s）
# 的全部买单金额分布：
#
#   7b3d…8d61：36 / 73×5 / 75 / 256 / 366×8 / 1098 / 1465×3
#   f907…6d4c：0.73×18（0.01 HYPE 试探单）/ 12 / 18 / 24×5 / 37×2 / 73×3 / 98 /
#               122×6 / 146×3 / 171×4 / 220×2 / 244×3 / 366×2 / 494×2 / 732
#
# 取 **500U**（≈6.8 HYPE）：落在这条链「真正建仓」的量级上，约 9 条/3.3 小时
# （≈2~3 条/小时）。定 200U 会到 ~7 条/小时，把 0.73U 那种试探单之外的小额补仓也
# 全弹出来，反而盖住真正要看的那几笔。用户可在面板里按钱包逐个改（改过之后 seed
# 不再覆盖已入库的行）。
# ---------------------------------------------------------------------------
ELYSIUM_SMART_MONEY_WALLETS: tuple[tuple[str, str], ...] = (
    # 昵称带上链名：弹窗标题是「KOL <昵称> 买入 X」，只写「追踪 · 7b3d…8d61」看不出
    # 这是哪条链的人，写成「Elysium 聪明钱 · …」既点名身份又能一眼分清链。
    # 7b3d 这条用户 2026-11-10 给出了真名「名字是casino」，所以直接用它当昵称 ——
    # 占位式的「Elysium 聪明钱 · 7b3d…8d61」在弹窗里等于没有名字。
    ("0x7b3d8e939ee08b52d06ab5e6f85791a6007e8d61", "casino"),
    ("0xf907877022b1e61232aca00661d6fb629b356d4c", "Elysium 聪明钱 · f907…6d4c"),
)

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + tuple(
    {
        "address": address,
        "nickname": nickname,
        "sourceName": "Elysium 聪明钱（用户点名）",
        "sourceUrl": f"https://elysium.kinetiq.xyz/explorer/address/{address}",
        "sourceEvidence": (
            "用户 2026-11-09 直接给出地址，要求「像之前的聪明钱一样做买入弹窗」，"
            "并明确只盯它在 Elysium 链上的动作（不要扩展到 ETH/BSC/Base/Robinhood）。"
            "链上核验：Elysium 主网（chainId 99901）EOA，nonce 非零、余额数百 HYPE，"
            "确有真实交易。阈值 200U 为「HYPE 计价、单笔几十~几百 U」的量级估算。"
        ),
        # 只有这一条链 —— 用户明确要求不要扩散到其它 EVM 链。
        "chains": ("elysium",),
        "alertThresholdUsd": 500.0,
    }
    for address, nickname in ELYSIUM_SMART_MONEY_WALLETS
)
# ---------------------------------------------------------------------------
# 2026-11-17 第三批点名（用户连发 6 条消息，一次给了一整套人）：
#   SENSU（只有 EVM）、Hyin（SOL + EVM）、DV（SOL + EVM）、7bn（SOL）、
#   8kz（SOL）、decu（SOL）。SOL 那几支走 `SOLANA_KOL_WALLETS`，EVM 的在这里。
#
# 两条链分开写的原因跟 THEO 一样：同一个人的两支钱包仓位量级差一个数量级，
# 混在一个昵称里看不出是哪条链上的动作。
#
# 阈值统一 **100U**：这三支在 EVM 上的碎单都落在 $38~96，100U 是四条链的共同空档
# （BSC/ETH/Robinhood/Base 的尘埃档全在 100 以下），而他们真正的建仓都从 $110 起。
# 各链 ≥100U 的不同标的数：SENSU 13/13/10、Hyin 7/8/4、DV 2/5 —— 加起来每天个位数。
# ---------------------------------------------------------------------------
DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": "0xa29f940a90ae3dbf8dce7f95a35d19bc8e4296d5",
        "nickname": "SENSU",
        "sourceName": "用户点名 · KOL（EVM）",
        "sourceUrl": "https://gmgn.ai/bsc/address/0xa29f940a90ae3dbf8dce7f95a35d19bc8e4296d5",
        "sourceEvidence": (
            "用户 2026-11-17 10:48：「0xa29f94…296d5，SENSU也要监控上」。"
            "链上核验：BSC nonce 3,221 / 9.7017 BNB；Robinhood 753 / 1.5718；"
            "ETH 143 / 0.0444；Base 9 / 0.0015 —— 后两条链只是零星活动，四条都挂上。"
            "GMGN 成交画像："
            "BSC 15 笔 / 60.8h（59, 59, 114, 114, 116, 117, 117, 171, 231, 231, 291, "
            "295×4），中位 $171；Robinhood 19 笔 / 565.6h（38, 41, 61, 66, 67, 68 之后"
            "直接到 226~229×4），中位 $229、最大 $1,144；"
            "ETH 11 笔 / 480.6h（67, 224~245×5, 460~474×3, 802），中位 $245。"
            "三链都是「$40~70 试单 → $110 以上真仓」的两段式，100U 正好卡在中间。"
        ),
        "chains": ("bsc", "ethereum", "base", "robinhood"),
        "alertThresholdUsd": 100.0,
    },
    {
        "address": "0xacd7793d92cd870e56db2372b36e41c59ae4fb40",
        "nickname": "Hyin·EVM",
        "sourceName": "用户点名 · KOL（EVM）",
        "sourceUrl": "https://gmgn.ai/bsc/address/0xacd7793d92cd870e56db2372b36e41c59ae4fb40",
        "sourceEvidence": (
            "用户 2026-11-17 10:48：「这是Hyin的SOL和EVM钱包」（SOL 那支另见同名条目）。"
            "链上核验：BSC nonce **10,246** / 28.3615 BNB；Robinhood **11,030** / 3.5627；"
            "Base 7,503 / 1.7123；ETH 1,726 / 8.5509 —— 四条链都是重仓活跃的普通 EOA。"
            "GMGN 成交画像：BSC 12 笔 / 87.9h（58, 95, 96, 96, 132, 195, 203, 301, 587, "
            "605, 1,162×2）；Robinhood 13 笔 / 268h（55×3, 134, 146, 155, 165, 223, 275, "
            "1,651, 2,609×3）；Base 8 笔 / 266h（112, 123×2, 166, 409, 462, 830, 925）。"
            "三链 ≥100U 分别命中 8 / 10 / 8 笔、7 / 8 / 4 个标的 —— 100U 滤掉 $55~96 "
            "那几笔试单，$2,609 那种大仓照样弹。"
        ),
        "chains": ("bsc", "ethereum", "base", "robinhood"),
        "alertThresholdUsd": 100.0,
    },
    {
        "address": "0xbc2255fb3746403cf1858a69cdabd2e9be77c538",
        "nickname": "DV·EVM",
        "sourceName": "用户点名 · KOL（EVM）",
        "sourceUrl": "https://gmgn.ai/bsc/address/0xbc2255fb3746403cf1858a69cdabd2e9be77c538",
        "sourceEvidence": (
            "用户 2026-11-17 10:50：「DV的所有公开钱包都要监控上」（SOL 那支另见同名条目）。"
            "**GMGN 榜核对一致**：BSC KOL 榜在榜，dv / **@vibed333（18,390 粉）**，"
            "tags 含 kol / gmgn_go / top_followed。"
            "链上核验：BSC nonce **13,106** / 9.9867 BNB；Robinhood 9,106 / 9.9221；"
            "Base 1,237 / 2.0383；ETH 129 / 1.3935。"
            "GMGN 成交画像：BSC 27 笔 / 72h 只有两档 —— 前 11 笔 cost_usd 为 0（代币换"
            "代币/未定价），后 16 笔**全是 $310~315 的固定仓**；"
            "Robinhood 10 笔 / 1,010h（66, 69, 71, 76, 145, 150, 176, 178, 190, 552）。"
            "阈值 100U：BSC 那 16 笔固定仓本来就在 310 以上（不受影响），"
            "Robinhood 留 145 以上 6 笔 / 5 个标的。"
        ),
        "chains": ("bsc", "ethereum", "base", "robinhood"),
        "alertThresholdUsd": 100.0,
    },
)

# ---------------------------------------------------------------------------
# TIGER（用户 2026-11-23：「BSC KOL链上买入监控再加一个：KOL TIGER的钱包：
#   0x57825f68fc7b32eaaa1e90db1fd6cd997b5a4035」）
#
# 全新地址（库里 `wallets` 没这一行、`TRACKED_KOL_AUX_WALLETS` 里也没有），
# 所以阈值只能靠 GMGN 的真实成交定 —— 和 leslie / 区块日记同一套流程。
#
# 链上核验（2026-11-23，BSC 公共 RPC）：
#   nonce **13,479** / 余额 **12.8066 BNB**；
#   `eth_getCode` = `0xef0100` + `63c0c19a282a1b52b07dd5a65b58948a07dae32b`
#   —— 23 字节的 **EIP-7702 委托 EOA**（智能钱包），既不是空地址也不是合约。
#   委托不影响扫描：买入交易的 `from` 仍是这个地址本身，日志过滤照旧。
#   ETH 无活动；Base 近 128 天只有 60 笔买（0.5 笔/天），所以**只挂 BSC**。
#
# 成交画像（GMGN openapi `wallet_activity`，BSC 300 笔 /
# 2026-11-20 18:44 → 11-23 16:57 = 2.93 天，买 159 笔 = **54.3 笔/天**）：
#   中位 $72 / p75 $136 / p90 $540 / 最大 $1,629。
#   金额是**双峰**的：$60~136 的试探单 130 笔（每天约 44 笔，机器人式扫单），
#   然后直接跳到 **$520~550 的固定仓 24 笔**（每天约 8 笔）；中间 $200~400 只有 5 笔。
#   各档命中：≥100U → 23.9/天；≥150U → 10.6/天；≥200U → 9.9/天；
#            ≥300U → 8.5/天；≥500U → 8.2/天；≥800U → 0.7/天（只有 2 笔）。
# 阈值 **100U**（用户 2026-11-23 定，先前我给的是 300U）：代价是 ~24 条/天 ——
# 其中约 14 条来自 $100~199 那档（他只买 ~$530 固定仓时的间隙单），其余是固定仓本身。
# 用户要的就是「别漏掉他中等仓的动作」，噪音知情。
# 想收回去：150U（10.6/天）、300U（8.5/天，等于只看固定仓）、800U（0.7/天）。
# ---------------------------------------------------------------------------
TIGER_WALLET = "0x57825f68fc7b32eaaa1e90db1fd6cd997b5a4035"

DEFAULT_SMART_MONEY_SEEDS = DEFAULT_SMART_MONEY_SEEDS + (
    {
        "address": TIGER_WALLET,
        "nickname": "TIGER",
        "sourceName": "用户点名 · KOL",
        "sourceUrl": f"https://gmgn.ai/bsc/address/{TIGER_WALLET}",
        "sourceEvidence": (
            "用户 2026-11-23：「BSC KOL链上买入监控再加一个：KOL TIGER的钱包："
            "0x57825f68fc7b32eaaa1e90db1fd6cd997b5a4035」。"
            "链上核验：BSC nonce 13,479 / 12.8066 BNB；eth_getCode 是 23 字节的 "
            "EIP-7702 委托（0xef0100…），属智能钱包但仍是 EOA 发起交易；ETH 无活动，"
            "Base 128 天只有 60 笔买，故只挂 BSC。"
            "GMGN 成交画像：BSC 159 笔买 / 2.93 天 = 54.3 笔/天，中位 $72 / p90 $540；"
            "双峰 —— $60~136 试探单 130 笔、$520~550 固定仓 24 笔，中间只有 5 笔。"
            "阈值演变：2026-11-23 先按 300U 开（≈8.5 条/天，等于只放行固定仓），"
            "用户当天要求改成 100U（≈23.9 条/天，把 $100~199 的间隙单一起放行，噪音知情）。"
        ),
        # 用户点名的是「BSC KOL」：只挂 BSC。ETH 没活动，Base 一个季度才 60 笔买，
        # 挂上去纯白占扫描窗口（BSC 还要走「按区块扫 from」的兜底路径）。
        "chains": ("bsc",),
        "alertThresholdUsd": 100.0,
    },
)

DEFAULT_SMART_MONEY_SEED_ADDRESSES = tuple(row["address"] for row in DEFAULT_SMART_MONEY_SEEDS)

# ---------------------------------------------------------------------------
# 「KOL 共振」单独提醒（用户 2026-10-27）
#
#   「@cryptocharming @feibo03 @afeng 这三个人如果同时上车了一个币的话，记得单独提醒」
#
# 语义：**同一条链上的同一个合约**，在 :data:`KOL_SYNC_WINDOW_SECONDS` 窗口内被这三
# 个人都买过 → 除各自的买入弹窗外，再单独弹一条「三人同时上车」。这比单笔弹窗更有
# 分量：三个人独立决策却撞到同一个标的上，通常意味着这个币有事。
#
# 几个边界：
#
# * **不看金额门槛**。单笔提醒有 500U/10,000U 的阈值，共振看的是「有没有上车」，一笔
#   小额补仓也算上车。事件表本来就记录所有买单（不只弹窗的那批）。
# * **只看同链同合约**。同名不同合约是两件事，跨链桥接版也不是「同一个币」。
# * **一个币只提醒一次**，:data:`KOL_SYNC_REALERT_SECONDS` 之后才允许再提醒 ——
#   否则第三人买完、第四笔又来，会连着炸好几条。
# * 名单可扩展：`XINGYUN_KOL_SYNC_WALLETS="0xabc…:名字,0xdef…:名字"`（覆盖内置三条）。
# ---------------------------------------------------------------------------
KOL_SYNC_WALLET_LABELS: dict[str, str] = {
    "0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9": "CryptoCharming",
    "0xbf004bff64725914ee36d03b87d6965b0ced4903": "阿峰_Afeng",
    # 用户 2026-10-27 澄清：feibo03 就是 cowboy（X 上 @feibo03 = Cowboy◆BNB 版本之牛），
    # 所以这第三个人就是他本人那个钱包，不存在另一个地址。
    "0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407": "cowboy",
}
#: 三个人都到齐才算「同时上车」。想改成「任两人」就设 `XINGYUN_KOL_SYNC_MIN=2`。
KOL_SYNC_MIN_WALLETS = 3
#: 「同车成立」的第二个入口（2026-11-12 加）：**任意** :data:`KOL_SYNC_MIN_WALLETS` 人
#: 撞上同一个币，其中至少这么多人是核心三人，也算同车。
#:
#: 起因：11-12 16:28「币安生活」上 cowboy $471 + 阿峰 $404 + antiposition $512 在 50 秒
#: 内先后上车（合计 $2,922），却零弹窗 —— 因为三人齐的判定只认 CryptoCharming / 阿峰 /
#: cowboy，antiposition 虽然在同车观察面里但**不计入核心**，于是 core_count 永远卡在 2。
#:
#: 回测 27.6 天：老口径只触发 5 次（0.1 张/天，形同虚设）；本口径 69 次（1.1 张/天），
#: 且命中 CTO $12,656 / SAFU $13,007 / 珍珠币 / BIBIAI 这些多 KOL 共振的大机会。
#: 要求「含核心 ≥2」是为了挡掉纯粹由 17 个辅助追踪钱包凑数的噪音。
KOL_SYNC_MIN_CORE_WALLETS = 2
KOL_SYNC_WINDOW_SECONDS = 24 * 3600
KOL_SYNC_REALERT_SECONDS = 12 * 3600
#: 「同车增加」的有效期：从三人齐那一刻算起，这个窗口内再有人上车就继续提醒。
#: 过了窗口就不再骚扰 —— 一个币的同车故事讲 24 小时够了。
KOL_SYNC_JOIN_WINDOW_SECONDS = 24 * 3600

#: 「任意 N 个追踪钱包同车即成立」的链（目前只有 Solana）。
#:
#: EVM 侧成立要求「含核心 ≥2」（:data:`KOL_SYNC_MIN_CORE_WALLETS`），因为那 20 多个
#: 外围地址是给「同车增加」凑人数的、不配单独成立一次共振。Solana 没有这个层级 ——
#: 用户 2026-11-23：「SOL链的KOL和买入监控也做一个同车提示弹窗」——那边启用的 23 个
#: 钱包**都是**他点名的 KOL（cowboy 的 sol 钱包 / 8kz / Dior / Latuche / cented /
#: zrool / Hyin / DV …），彼此平权，所以只数人数、不设核心。
#:
#: 之所以敢用「≥3 人」这么松的口径，是拿库回测过的（2026-11-23，14 天、23 个启用
#: solana 钱包、24h 窗口、按 mint 聚：「24h 内最多同时几个人买过」的分布）：
#:   1 人 1512 个 mint / 2 人 162 / **3 人 19** / **4 人 2** / ≥5 人 0
#: 也就是说 ≥3 人 ≈ **1.5 条/天**，和 EVM 侧 cluster_ready 的 1.1 条/天同一量级，
#: 不会炸屏。8kz 一个人出现在 19/21 组里（他 14 天 2400 笔买单），但每组另外两人
#: 各不相同，所以照样是「多 KOL 撞同一个新币」的信号。
KOL_SYNC_ANY_TRACKED_CHAINS = ("solana",)

#: 上一条那些链的「钱闸」：至少这么多人、各自的合计 ≥ 这个金额。
#:
#: Solana 的 ``paymentUsd`` 常常算不出来（pump.fun 内盘 bonding curve、非稳定币报价），
#: 会留下 ``$0`` 的记账。只数人数的话，「1 个人真买 + 2 个人 $0」也会算同车 —— 而那种
#: 情况单笔弹窗早就报过了，同车再报一次纯噪音。回测：21 组里 19 组能过（1.36 条/天），
#: 只挡掉「7bn $0 + 8kz $0 + Dior $34k」这类伪同车。
#: 调松就设 ``KOL_SYNC_FUNDED_MIN_USD=0``（退化成纯数人数）。
KOL_SYNC_FUNDED_MIN_BUYERS = 2
KOL_SYNC_FUNDED_MIN_USD = 10.0

#: Solana 的同车观察面直接从 ``wallets`` 表现取（那边的钱包是用户随时在加的），
#: 缓存这么久，免得每笔买入都查一次钱包表。
KOL_SYNC_SOLANA_TRACK_TTL_SECONDS = 60.0

#: 「同车增加」看的人：用户 10-27 给的 20 个追踪钱包（含核心三人），加上后来点名
#: 要进同车监控的（区块日记 / TIGER）= 23 人。
#: 名字对得上的写人名，其余用短地址 —— 用户没给昵称，编一个反而会认错人。
KOL_SYNC_TRACK_LABELS: dict[str, str] = {
    "0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9": "CryptoCharming",
    # 他另外两个 Solana 公开钱包（2026-11-12 用户提供）。同一个人三支钱包，
    # 卡片上要能分辨，所以后两支带编号后缀 —— 同车判定是按**地址**计人数的。
    "HyYvwH1bU8qb82wbhZvsgyVmjJroAv2W8qVyhccWj79P": "CryptoCharming·2",
    "4X43DmYRtzLcyv2YRhC8vaTeqB5BTL6jX7LDtzfz47aC": "CryptoCharming·3",
    "0xbf004bff64725914ee36d03b87d6965b0ced4903": "阿峰_Afeng",
    "0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407": "cowboy",
    "0xe28601398a5448d1147e6e8b0e0c6d686f0d216d": "NΞO",
    "0x6078ee8a93697c6d67863fcbff77141d9ab358b2": "Inq",
    # 用户 2026-11-11 点名要弹窗，已从 TRACKED_KOL_AUX_WALLETS 移出（那份是「只记录」），
    # 但同车提醒的观察面还要算上他，所以在这里单独登记名字 —— 别顺手删掉。
    ANTIPOSITION_WALLET: "antiposition",
    MI_SHEN_WALLET: "米神",
    KUZUO_WALLET: "枯坐P小将",
    # 用户 2026-11-20 点名要弹窗（原本也在 aux 清单里，移出后名字在这里补上，
    # 否则同车观察面会从 20 人变成 19 人）
    QUKUAI_RIJI_WALLET: "区块日记",
    # 用户 2026-11-23 点名加监控的 TIGER（BSC），当天追加「也要加入同车监控中」。
    # 他不在 aux 清单里，所以要在这里单独登记名字，否则同车观察面会漏掉他。
    # **只进观察面、不进核心三人**（:data:`KOL_SYNC_WALLET_LABELS`）—— 核心名单是
    # 用户最初点的那三个人，口径没变。效果：TIGER + 任意两个核心 = 同车成立
    # （cluster_ready 的「含核心 ≥2」够得着），成立后他再上车会单独弹一条「同车增加」；
    # 「TIGER + 1 核心 + 1 外围」仍然静默，那是挡辅助钱包凑数的既有设计。
    # 回归：`tests/test_kol_sync.py::TigerKolSyncParticipationTests`。
    TIGER_WALLET: "TIGER",
    **{address: f"追踪 {_short_wallet(address)}" for address in TRACKED_KOL_AUX_WALLETS},
}


def active_kol_sync_wallets() -> dict[str, str]:
    """共振观察名单（地址小写 → 展示名）。环境变量可整表覆盖，便于临时加人。"""
    raw = str(os.getenv("XINGYUN_KOL_SYNC_WALLETS", "") or "").strip()
    if not raw:
        return {address.lower(): label for address, label in KOL_SYNC_WALLET_LABELS.items()}
    wallets: dict[str, str] = {}
    for piece in raw.split(","):
        address, _, label = piece.strip().partition(":")
        address = address.strip().lower()
        if address:
            wallets[address] = label.strip() or address[:10]
    return wallets or {address.lower(): label for address, label in KOL_SYNC_WALLET_LABELS.items()}


def active_kol_sync_track_wallets() -> dict[str, str]:
    """「同车增加」的观察面：目前 23 人（核心三人 + 20 个外围追踪钱包）。

    比核心三人宽 —— 核心三人负责「判定同车成立」，其余 20 个负责「还有谁上车」。
    同样允许用 `XINGYUN_KOL_SYNC_TRACK_WALLETS`（格式同上）整表覆盖。
    """
    raw = str(os.getenv("XINGYUN_KOL_SYNC_TRACK_WALLETS", "") or "").strip()
    if raw:
        wallets: dict[str, str] = {}
        for piece in raw.split(","):
            address, _, label = piece.strip().partition(":")
            address = address.strip().lower()
            if address:
                wallets[address] = label.strip() or _short_wallet(address)
        if wallets:
            return wallets
    return {
        address.lower(): label for address, label in KOL_SYNC_TRACK_LABELS.items()
    }

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
EVM_CHAINS = ("ethereum", "bsc", "base", "robinhood")
#: 「只对点名地址开启」的 EVM 链。
#:
#: 这些链**不能**写进 ``EVM_CHAINS``：``seed_defaults()`` 里 ``seed.get("chains", EVM_CHAINS)``
#: 会把每个种子钱包都按 EVM_CHAINS 复制一行，多一条链就等于把 30 多个地址全拖进去轮询。
#: 所以放在这里，只由 seed 自己用 ``"chains": ("elysium",)`` 显式点名。
#:
#: Elysium（Kinetiq 的 Arbitrum Orbit L2，结算层是 HyperEVM，主网 chainId 99901，
#: 原生币 HYPE）—— 用户 2026-11-09 点名要盯两个聪明钱地址，且明确「只监控他们在
#: elysium 的」。注意别和 Vulcan Forged 那条同名老链（chainId 1338/1339、LAVA 计价、
#: RPC ``*rpc.vulcanforged.com`` 已 DNS 失效）搞混。
OPT_IN_EVM_CHAINS = ("elysium",)
SUPPORTED_EVM_CHAINS = (*EVM_CHAINS, *OPT_IN_EVM_CHAINS)
SUPPORTED_CHAINS = (*SUPPORTED_EVM_CHAINS, "solana")

# Blocks scanned per poll, per chain.  Chains do not agree on block time:
# Robinhood Chain mines ~10 blocks/second, so the generic 120-block window only
# covers ~12 seconds — a fraction of one poll cycle — and the monitor silently
# drops most buys (health showed "已扫描最近 120 个区块，跳过 198 个旧区块").
# The Robinhood RPC stays cheap on a wide span as long as the ERC-20 transfer
# topic filter is present (30000 blocks ≈ 0.8s, and its per-query span limit is
# exactly 30000), so fast chains get a window that comfortably outlasts a cycle.
EVM_MAX_BLOCKS_PER_POLL_DEFAULT = 120
EVM_MAX_BLOCKS_PER_POLL_BY_CHAIN = {
    "robinhood": 3000,
    # Elysium 出块 ~100ms（10 块/秒），默认 120 块只覆盖 12 秒，而一轮要串行跑 6 条链。
    # 公共 RPC 对不带 address 的 eth_getLogs 卡 2000 块，所以取 1500（≈150 秒）留余量。
    "elysium": 1500,
}
EVM_MAX_BLOCKS_PER_POLL_CEILING = 30000

# Signatures fetched per poll, per Solana wallet.  ``getSignaturesForAddress``
# has no server-side filter, so a busy address buries real trades under spam:
# BQVz7fQ1… took 25000 *failed* transactions in four minutes (~100/second) from
# bots that merely referenced it.  A 20-row window covers ~0.2s of that flood
# and the actual buy gets pushed out, so the window widens on demand.
SOLANA_SIGNATURE_LIMIT_DEFAULT = 200
SOLANA_SIGNATURE_LIMIT_CEILING = 1000

# ``getTransaction`` costs one RPC round-trip per signature and Solana has no
# batch variant for jsonParsed payloads.  A spammed wallet can hand us hundreds
# of *successful* signatures in a single window, and fetching them all in a row
# makes one poll round outlast every other chain: the shared worker thread sits
# inside Solana for minutes, so EVM chains stop being polled and health goes
# stale on all five.  Bound the work per round and checkpoint the cursor at the
# newest signature actually processed, so the rest is picked up next round
# instead of being skipped.
SOLANA_TX_BUDGET_PER_ROUND = 12

#: 每条链的独立轮询节奏（秒），0 = 上一轮跑完立刻再来。
#:
#: 2026-11-17 用户：「有些钱包买入非常不及时，实时性很重要」。病根是 ``poll_once``
#: 用**一个线程串行**跑六条链：Solana 一轮要一百多秒（20+ 个钱包各一次
#: getSignaturesForAddress，其中 5 个灌满的还要再放大一次），排在后面的 BSC / ETH /
#: Base / Robinhood 就只能跟着等，health 里 ``last_checked_at`` 实测相差 90 秒以上。
#:
#: 改成每链一个槽位后，「慢链拖死快链」这件事从结构上消失了，于是 EVM 按自己的
#: 节奏跑（20 秒一轮 ≈ 6 倍于原来的 140 秒一轮），Solana 保持「跑完就再来」。
#: 想调慢/调快直接改环境变量 ``SMART_MONEY_<CHAIN>_INTERVAL_SECONDS``。
DEFAULT_CHAIN_INTERVAL_SECONDS = {
    "solana": 0.0,
    "ethereum": 20.0,
    "bsc": 20.0,
    "base": 20.0,
    "robinhood": 20.0,
    "elysium": 30.0,
}

#: 并行查「每个钱包的最新签名」的线程数。这一步是纯读、每个钱包各自一条游标，
#: 彼此没有依赖；串行做的时候 20+ 个钱包就是 Solana 一轮里的主要开销。
#:
#: **2 是实测出来的上限**（2026-11-18 打官方 RPC，9 个真实钱包）：
#:   并发 1 → 9 个钱包 19.7 秒，0 个 429；并发 3 → 6.6 秒但开始偶发 429；
#:   并发 5 → 5.7 秒但 9 个里 4 个 429。`getSignaturesForAddress` limit=200
#: 是重请求，免费额度的桶很小。取 2：耗时近乎砍半，429 靠「单钱包失败不拖累整链、
#: 下轮重试」兜住。想再快必须先换付费 RPC。
SOLANA_SIGNATURE_LOOKUP_WORKERS = max(
    1,
    min(4, int(os.getenv("SMART_MONEY_SOLANA_SIGNATURE_WORKERS", "2") or "2")),
)


# Blocks scanned per poll when the fast log path is unavailable.  This is a
# CATCH-UP budget: while the monitor keeps up, ``start`` is ``previous + 1`` and
# only the new blocks are fetched, so a larger budget costs nothing in steady
# state — it only decides how far back a lagging poll may reach before it starts
# dropping blocks silently.  BSC mines ~0.45 s/block, so the generic 24-block
# budget covers barely 11 s: any poll cycle slower than that (the monitor walks
# its chains sequentially) leaves an unannounced gap.  Public BSC RPCs refuse
# address-less ``eth_getLogs`` (publicnode: "Please specify an address";
# bnbchain dataseeds: "limit exceeded"), so this fallback IS the BSC path.
EVM_FALLBACK_BLOCKS_DEFAULT = 24
#: BSC 的兜底窗口 = **240 块（≈108 秒）**。2026-10-22 实测：整个轮询要串行走完
#: base→ethereum→bsc→solana，一轮约 75–80 秒（BSC 自己那趟全块扫描就占 ~10 秒），
#: 而 120 块只覆盖 54 秒 —— 于是每轮稳稳丢掉 13~61 个块（跳过的那段永远不会回头补）。
#: 用户 2026-10-22 要盯的 KOL 就在 BSC 上高频交易，按 120 块算大约 1/4 的买单会被漏掉。
#: 240 块对 ~80 秒一轮留了约 25% 余量；再往上加只是线性拉长每次轮询的 RPC 次数。
EVM_FALLBACK_BLOCKS_BY_CHAIN = {"bsc": 240}
EVM_FALLBACK_BLOCKS_CEILING = 600


def evm_fallback_blocks(chain: str) -> int:
    """Catch-up block budget for one chain when ``eth_getLogs`` is unusable.

    ``SMART_MONEY_EVM_FALLBACK_BLOCKS`` still overrides every chain when set.
    """

    override = str(os.getenv("SMART_MONEY_EVM_FALLBACK_BLOCKS", "") or "").strip()
    if override:
        try:
            value = int(float(override))
        except ValueError:
            value = 0
        if value > 0:
            return max(5, min(value, EVM_FALLBACK_BLOCKS_CEILING))
    return max(5, min(
        int(EVM_FALLBACK_BLOCKS_BY_CHAIN.get(chain, EVM_FALLBACK_BLOCKS_DEFAULT)),
        EVM_FALLBACK_BLOCKS_CEILING,
    ))


def evm_max_blocks_per_poll(chain: str) -> int:
    """How many blocks to scan per poll on ``chain``.

    ``SMART_MONEY_EVM_MAX_BLOCKS_PER_POLL`` overrides every chain when set; the
    per-chain table only supplies the default.
    """

    override = str(os.getenv("SMART_MONEY_EVM_MAX_BLOCKS_PER_POLL", "") or "").strip()
    if override:
        try:
            value = int(float(override))
        except ValueError:
            value = 0
        if value > 0:
            return max(5, min(value, EVM_MAX_BLOCKS_PER_POLL_CEILING))
    default = EVM_MAX_BLOCKS_PER_POLL_BY_CHAIN.get(
        chain, EVM_MAX_BLOCKS_PER_POLL_DEFAULT
    )
    return max(5, min(default, EVM_MAX_BLOCKS_PER_POLL_CEILING))
CHAIN_META = {
    "ethereum": {
        "label": "Ethereum", "vm": "evm", "native": "ETH", "dex": "ethereum",
        "explorer": "https://etherscan.io/tx/{}",
        "rpcEnv": "SMART_MONEY_ETHEREUM_RPC_URLS",
        "rpcs": (
            "https://eth.drpc.org", "https://rpc.flashbots.net",
            "https://ethereum-rpc.publicnode.com", "https://rpc.mevblocker.io",
        ),
    },
    "bsc": {
        "label": "BSC", "vm": "evm", "native": "BNB", "dex": "bsc",
        "explorer": "https://bscscan.com/tx/{}",
        "rpcEnv": "SMART_MONEY_BSC_RPC_URLS",
        "rpcs": (
            "https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org",
            "https://bsc-dataseed-public.bnbchain.org",
        ),
    },
    "base": {
        "label": "Base", "vm": "evm", "native": "ETH", "dex": "base",
        "explorer": "https://basescan.org/tx/{}",
        "rpcEnv": "SMART_MONEY_BASE_RPC_URLS",
        "rpcs": ("https://mainnet.base.org", "https://base-rpc.publicnode.com"),
    },
    "robinhood": {
        "label": "Robinhood", "vm": "evm", "native": "ETH", "dex": "robinhood",
        "explorer": "https://robinhoodchain.blockscout.com/tx/{}",
        "rpcEnv": "SMART_MONEY_ROBINHOOD_RPC_URLS",
        "rpcs": ("https://rpc.mainnet.chain.robinhood.com/",),
    },
    # Elysium（Kinetiq / HyperEVM 上的 Arbitrum Orbit L2，主网 chainId 99901）。
    # 只给点名地址用，见 ``OPT_IN_EVM_CHAINS``。
    # * ``dex`` 是 dexscreener 的链 slug —— 实测已收录（``chainId":"elysium"``、
    #   ``dexId":"uniswap"``），代币价格/符号/精度都能走这条。
    # * ``native`` = HYPE，Binance 有 HYPEUSDT，``native_price_usd`` 直接可用。
    # * 公共 RPC 对**不带 address 的** ``eth_getLogs`` 卡 2000 块上限，所以
    #   ``EVM_MAX_BLOCKS_PER_POLL_BY_CHAIN`` 给它 1500，留余量。
    "elysium": {
        "label": "Elysium", "vm": "evm", "native": "HYPE", "dex": "elysium",
        "explorer": "https://elysium.kinetiq.xyz/explorer/tx/{}",
        "rpcEnv": "SMART_MONEY_ELYSIUM_RPC_URLS",
        "rpcs": ("https://rpc.elysium.kinetiq.xyz",),
    },
    "solana": {
        "label": "Solana", "vm": "solana", "native": "SOL", "dex": "solana",
        "explorer": "https://solscan.io/tx/{}",
        "rpcEnv": "SMART_MONEY_SOLANA_RPC_URLS",
        # The official endpoint throttles getTransaction hard under load (2026-11-07:
        # repeated 10054 resets and read timeouts), so a second endpoint is kept as a
        # fallback. publicnode answered both getSignaturesForAddress and getTransaction
        # reliably during the same outage.
        "rpcs": ("https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"),
    },
}
CHAIN_ALIASES = {
    "eth": "ethereum", "ethereum": "ethereum", "以太坊": "ethereum",
    "1": "ethereum",
    "bsc": "bsc", "bnb": "bsc", "bnb chain": "bsc", "bnb smart chain": "bsc",
    "币安智能链": "bsc", "56": "bsc",
    "base": "base", "base chain": "base", "8453": "base",
    "sol": "solana", "solana": "solana", "索拉纳": "solana",
    "robinhood": "robinhood", "robinhood chain": "robinhood", "hood chain": "robinhood",
    "4663": "robinhood",
    # Elysium = Kinetiq 的 HyperEVM L2（主网 99901）。1338/1339 是 Vulcan Forged 那条
    # 同名老链，绝不能映射过来（它 RPC 已失效，且原生币是 LAVA 不是 HYPE）。
    "elysium": "elysium", "elysium chain": "elysium", "elysium mainnet": "elysium",
    "99901": "elysium",
}
EVM_ADDRESS_RE = re.compile(r"(?<![0-9a-fA-F])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
SOLANA_ADDRESS_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
SMART_MONEY_RE = re.compile(
    r"(?:聪明钱|聪明钱包|聪明地址|smart[\s_-]*money|smart[\s_-]*wallet|"
    r"smartmoney|高胜率钱包|高胜率地址|盈利钱包|内幕钱包|insider[\s_-]*wallet)",
    re.I,
)
CONTRACT_LABEL_RE = re.compile(
    r"(?:\bca\b|合约(?:地址)?|代币地址|token[\s_-]*address|contract(?:[\s_-]*address)?)\s*[：:#=-]*\s*$",
    re.I,
)
CHAIN_TEXT_PATTERNS = {
    "ethereum": re.compile(r"(?:ethereum|以太坊|\beth\b)", re.I),
    "bsc": re.compile(r"(?:bnb\s*(?:smart\s*)?chain|币安智能链|\bbsc\b|\bbnb\b)", re.I),
    "base": re.compile(r"(?:base\s*chain|\bbase\b)", re.I),
    "robinhood": re.compile(r"(?:robinhood\s*chain|hood\s*chain|robinhood)", re.I),
    "solana": re.compile(r"(?:solana|索拉纳|\bsol\b)", re.I),
}
# USDG is Robinhood Chain's quote currency.  Without it every USDG-quoted buy
# reads as "received two tokens, paid nothing" and classify_evm_buy returns None.
STABLE_SYMBOLS = {"USDT", "USDC", "USDBC", "DAI", "FDUSD", "USDE", "USD1", "USDO", "USDG"}
# WHYPE = Elysium / HyperEVM 上的 wrapped HYPE，计价时按原生币价格折算。
WRAPPED_NATIVE_SYMBOLS = {"WETH", "WBNB", "WSOL", "WHYPE"}
# 少数聪明钱**不用稳定币/原生币付款**：它们从一个「报价代币」出发去换目标币。
# 典型是 Robinhood 链上的 pons榜5（0x7e3b…a3ad）—— 它每笔都用 DELTA 付款，
# 净流水长成「花掉一个非稳定代币、收到目标代币」，而 :func:`classify_evm_buy`
# 只把「稳定币 / 包装原生币 / 原生币」当付款方，于是这个地址**永远弹不出来**。
#
# 名单内的地址多给两级兜底（名单外的行为一个字都不变，避免 Inq/阿峰 这些
# 「换仓」被误判成买入、把已经调好的弹窗节奏搞乱）：
#
# ① 花掉的那个代币有 DexScreener 报价 → 按它的美元价直接算这笔买入的规模；
# ② 连报价都没有 → 用「收到的代币 × 它的美元价」反推这笔交易的名义规模
#    （买入金额 ≈ 买到的市值，误差来自滑点与手续费，量级够用）。
# 两级都拿不到价就照旧返回 None —— 宁可不弹，也不许写个 0 美元的假弹窗。
QUOTE_TOKEN_PAYMENT_WALLETS: frozenset[str] = frozenset({
    "0x7e3ba68c49561aae7c23c1d20fef0f1d7615a3ad",  # pons榜5 · Robinhood 链
})
# DexScreener 对 Robinhood Chain 基本没有覆盖（该链代币的 ``priceUsd`` 实测恒为 0），
# 所以上面那两级兜底在这条链上会一起哑火。这张表补一个**链上定价**通道：池子一侧是
# 1 美元的 USDG，读 Uniswap V3 的 ``slot0().sqrtPriceX96`` 就能换算出另一侧的美元价。
#
# 校验（2026-11-04）：DELTA/USDG 池 slot0 → $0.00891/DELTA；2026-10-17 该地址在同一个
# 池子里的真实成交是 371.130578 USDG ↔ 30,000 DELTA（$0.01237/DELTA）—— 量级一致。
ONCHAIN_QUOTE_POOLS: dict[tuple[str, str], dict[str, Any]] = {
    ("robinhood", "0xe8ffd7e24187f72afb08d75b1bb13088a989a791"): {  # DELTA
        "pool": "0xfee4638485d736bf7bf7222d2dcb11a298d44d41",  # USDG / DELTA
        "tokenIndex": 1,      # DELTA 是 token1
        "tokenDecimals": 18,
        "quoteDecimals": 6,   # USDG
        "quotePriceUsd": 1.0,
    },
}
SOLANA_STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
}
EXCLUDED_OPERATION_WORDS = {"approve", "approval", "bridge", "liquidity", "add_liquidity", "remove_liquidity"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _local_day_start_ms(now: float | None = None) -> int:
    """本地时间今天 00:00 的毫秒时间戳（「当天第一次买入」的日界线）。

    用本地零点而不是 UTC：用户看的是自己的日历日，凌晨 8 点换天会很怪。
    """
    stamp = time.time() if now is None else float(now)
    local = time.localtime(stamp)
    midnight = time.struct_time((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    return int(time.mktime(midnight) * 1000)


def normalize_chain(value: Any) -> str:
    raw = str(value or "").strip().casefold().replace("_", " ").replace("-", " ")
    raw = re.sub(r"\s+", " ", raw)
    return CHAIN_ALIASES.get(raw, "")


def _decode_base58(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for character in value:
        position = alphabet.find(character)
        if position < 0:
            raise ValueError("invalid base58")
        number = number * 58 + position
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + decoded


def normalize_wallet_address(chain: Any, address: Any) -> str:
    normalized_chain = normalize_chain(chain)
    if not normalized_chain:
        raise ValueError("不支持的钱包链")
    raw = str(address or "").strip()
    if normalized_chain in SUPPORTED_EVM_CHAINS:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", raw, flags=re.I):
            raise ValueError("EVM 钱包地址格式无效")
        return "0x" + raw[2:].lower()
    try:
        decoded = _decode_base58(raw)
    except ValueError as exc:
        raise ValueError("Solana 钱包地址格式无效") from exc
    if not 32 <= len(raw) <= 44 or len(decoded) != 32:
        raise ValueError("Solana 钱包地址格式无效")
    return raw


def _mentioned_chains(window: str) -> list[str]:
    return [chain for chain, pattern in CHAIN_TEXT_PATTERNS.items() if pattern.search(window)]


def extract_smart_money_mentions(
    text: Any,
    *,
    source_name: str = "",
    source_kind: str = "text",
    source_url: str = "",
    observed_at: int | None = None,
) -> list[dict[str, Any]]:
    value = str(text or "")
    if not value or not SMART_MONEY_RE.search(value):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def append_match(match: re.Match[str], address_kind: str) -> None:
        address = match.group(0)
        prefix = value[max(0, match.start() - 48):match.start()]
        window = value[max(0, match.start() - 180):min(len(value), match.end() + 180)]
        if not SMART_MONEY_RE.search(window) or CONTRACT_LABEL_RE.search(prefix):
            return
        mentioned = _mentioned_chains(window)
        if address_kind == "evm":
            chains = [chain for chain in mentioned if chain in EVM_CHAINS] or list(EVM_CHAINS)
        else:
            if mentioned and "solana" not in mentioned:
                return
            chains = ["solana"]
        for chain in chains:
            try:
                normalized = normalize_wallet_address(chain, address)
            except ValueError:
                continue
            key = (chain, normalized)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "chain": chain,
                "address": normalized,
                "nickname": "",
                "sourceName": str(source_name or "自动识别")[:120],
                "sourceKind": str(source_kind or "text")[:40],
                "sourceUrl": str(source_url or "")[:800],
                "sourceEvidence": re.sub(r"\s+", " ", window).strip()[:600],
                "observedAt": int(observed_at or _now_ms()),
            })

    for match in EVM_ADDRESS_RE.finditer(value):
        append_match(match, "evm")
    for match in SOLANA_ADDRESS_RE.finditer(value):
        append_match(match, "solana")
    return rows


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    mapping = {
        "source_name": "sourceName", "source_kind": "sourceKind", "source_url": "sourceUrl",
        "source_evidence": "sourceEvidence", "alert_threshold_usd": "alertThresholdUsd",
        "created_at": "createdAt", "updated_at": "updatedAt", "transaction_hash": "transactionHash",
        "wallet_address": "walletAddress", "wallet_nickname": "walletNickname",
        "token_address": "tokenAddress", "token_amount": "tokenAmount", "payment_asset": "paymentAsset",
        "payment_amount": "paymentAmount", "payment_usd": "paymentUsd", "price_usd": "priceUsd",
        "popup_eligible": "popupEligible", "alerted_at": "alertedAt", "observed_at": "observedAt",
        "block_ref": "blockRef", "event_key": "eventKey",
    }
    for source, target in mapping.items():
        if source in data:
            data[target] = data.pop(source)
    if "enabled" in data:
        data["enabled"] = bool(data["enabled"])
    if "popupEligible" in data:
        data["popupEligible"] = bool(data["popupEligible"])
    if "details_json" in data:
        raw = data.pop("details_json")
        try:
            data["details"] = json.loads(raw or "{}")
        except ValueError:
            data["details"] = {}
    if data.get("chain") in CHAIN_META:
        data["chainLabel"] = CHAIN_META[data["chain"]]["label"]
        if data.get("transactionHash"):
            data["transactionUrl"] = CHAIN_META[data["chain"]]["explorer"].format(data["transactionHash"])
    return data


class SmartMoneyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain TEXT NOT NULL,
                    address TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    source_name TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT 'manual',
                    source_url TEXT NOT NULL DEFAULT '',
                    source_evidence TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    alert_threshold_usd REAL NOT NULL DEFAULT 10000,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(chain, address)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    chain TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    wallet_nickname TEXT NOT NULL DEFAULT '',
                    transaction_hash TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    token_amount REAL NOT NULL DEFAULT 0,
                    payment_asset TEXT NOT NULL DEFAULT '',
                    payment_amount REAL NOT NULL DEFAULT 0,
                    payment_usd REAL NOT NULL DEFAULT 0,
                    price_usd REAL NOT NULL DEFAULT 0,
                    popup_eligible INTEGER NOT NULL DEFAULT 0,
                    alerted_at INTEGER NOT NULL DEFAULT 0,
                    observed_at INTEGER NOT NULL,
                    block_ref TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_smart_money_events_observed
                    ON events(observed_at DESC);
                -- 「同车」判定每笔新事件都要按 (链, 合约, 时间) 查这段窗口，
                -- 追踪钱包扩到 20 人后事件表涨得快，没有复合索引会退化成全表扫。
                CREATE INDEX IF NOT EXISTS idx_smart_money_events_token
                    ON events(chain, token_address, observed_at);
                -- 「同币当天只弹一次」每笔合格买入都要查一遍，必须走索引：
                -- 事件表几十万行时，全表扫会把轮询线程拖住。
                CREATE INDEX IF NOT EXISTS idx_smart_money_events_wallet_token
                    ON events(chain, wallet_address, token_address, alerted_at);
                CREATE TABLE IF NOT EXISTS cursors (
                    cursor_key TEXT PRIMARY KEY,
                    cursor_value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health (
                    chain TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    message TEXT NOT NULL DEFAULT '',
                    cursor_value TEXT NOT NULL DEFAULT '',
                    last_checked_at INTEGER NOT NULL DEFAULT 0,
                    last_ok_at INTEGER NOT NULL DEFAULT 0
                );
            """)
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def upsert_wallet(
        self,
        chain: Any,
        address: Any,
        *,
        nickname: str = "",
        source_name: str = "",
        source_kind: str = "manual",
        source_url: str = "",
        source_evidence: str = "",
        enabled: bool = True,
        alert_threshold_usd: float = DEFAULT_ALERT_THRESHOLD_USD,
        observed_at: int | None = None,
        preserve_controls: bool = False,
    ) -> dict[str, Any]:
        normalized_chain = normalize_chain(chain)
        normalized_address = normalize_wallet_address(normalized_chain, address)
        now = int(observed_at or _now_ms())
        threshold = max(0.0, float(alert_threshold_usd or DEFAULT_ALERT_THRESHOLD_USD))
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO wallets (
                    chain, address, nickname, source_name, source_kind, source_url,
                    source_evidence, enabled, alert_threshold_usd, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, address) DO UPDATE SET
                    nickname = CASE WHEN excluded.nickname != '' THEN excluded.nickname ELSE wallets.nickname END,
                    source_name = CASE WHEN excluded.source_name != '' THEN excluded.source_name ELSE wallets.source_name END,
                    source_kind = CASE WHEN excluded.source_name != '' THEN excluded.source_kind ELSE wallets.source_kind END,
                    source_url = CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE wallets.source_url END,
                    source_evidence = CASE WHEN excluded.source_evidence != '' THEN excluded.source_evidence ELSE wallets.source_evidence END,
                    enabled = CASE WHEN ? = 1 THEN wallets.enabled ELSE excluded.enabled END,
                    alert_threshold_usd = CASE WHEN ? = 1 THEN wallets.alert_threshold_usd ELSE excluded.alert_threshold_usd END,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_chain, normalized_address, str(nickname or "")[:120], str(source_name or "")[:120],
                    str(source_kind or "manual")[:40], str(source_url or "")[:800],
                    re.sub(r"\s+", " ", str(source_evidence or "")).strip()[:1000],
                    1 if enabled else 0, threshold, now, now,
                    1 if preserve_controls else 0, 1 if preserve_controls else 0,
                ),
            )
            self.conn.commit()
            return _row_dict(self.conn.execute(
                "SELECT * FROM wallets WHERE chain = ? AND address = ?",
                (normalized_chain, normalized_address),
            ).fetchone())

    def list_wallets(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM wallets {where} ORDER BY enabled DESC, updated_at DESC, id DESC"
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def wallet(self, chain: Any, address: Any) -> dict[str, Any]:
        normalized_chain = normalize_chain(chain)
        normalized_address = normalize_wallet_address(normalized_chain, address)
        with self.lock:
            return _row_dict(self.conn.execute(
                "SELECT * FROM wallets WHERE chain = ? AND address = ?",
                (normalized_chain, normalized_address),
            ).fetchone())

    def save_wallet(self, identity: int, *, enabled: bool | None = None, nickname: str | None = None,
                    alert_threshold_usd: float | None = None) -> dict[str, Any]:
        updates: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if nickname is not None:
            updates.append("nickname = ?")
            params.append(str(nickname or "")[:120])
        if alert_threshold_usd is not None:
            updates.append("alert_threshold_usd = ?")
            params.append(max(0.0, float(alert_threshold_usd)))
        updates.append("updated_at = ?")
        params.append(_now_ms())
        params.append(int(identity))
        with self.lock:
            self.conn.execute(f"UPDATE wallets SET {', '.join(updates)} WHERE id = ?", params)
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM wallets WHERE id = ?", (int(identity),)).fetchone()
        if row is None:
            raise ValueError("聪明钱地址不存在")
        return _row_dict(row)

    def remove_wallet(self, identity: int) -> bool:
        with self.lock:
            cursor = self.conn.execute("DELETE FROM wallets WHERE id = ?", (int(identity),))
            self.conn.commit()
            return bool(cursor.rowcount)

    def seed_wallets(self, rows: list[dict[str, Any]]) -> int:
        """Insert first-party defaults once without overwriting later user choices."""
        now = _now_ms()
        inserted = 0
        with self.lock:
            for row in rows:
                chain = normalize_chain(row.get("chain"))
                address = normalize_wallet_address(chain, row.get("address"))
                cursor = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO wallets (
                        chain, address, nickname, source_name, source_kind, source_url,
                        source_evidence, enabled, alert_threshold_usd, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        chain, address, str(row.get("nickname") or "")[:120],
                        str(row.get("sourceName") or "预置")[:120],
                        str(row.get("sourceKind") or "seed")[:40],
                        str(row.get("sourceUrl") or "")[:800],
                        str(row.get("sourceEvidence") or "")[:1000],
                        max(0.0, float(row.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD)),
                        now, now,
                    ),
                )
                inserted += int(bool(cursor.rowcount))
            self.conn.commit()
        return inserted

    def apply_seed_thresholds(self, rows: tuple[dict[str, Any], ...]) -> int:
        """把种子里**显式声明**的提醒门槛推到「还没被调过」的种子行。

        ``seed_wallets`` 用的是 ``INSERT OR IGNORE``，所以改代码里的
        ``alertThresholdUsd`` 对已入库的行完全无效（Inq 就是这样一直卡在默认
        10000U 上，永远弹不出来）。直接 UPDATE 又会把用户在面板里手动调过的门槛
        冲掉，于是折中：

        * 只动 ``source_kind = 'seed'`` 的行（用户手工加的地址一律不碰）；
        * 只动「阈值**仍等于**全局默认 :data:`DEFAULT_ALERT_THRESHOLD_USD`」的行 ——
          用户把阈值调成任何别的值就不再匹配，改代码也不会覆盖他的选择；
        * 种子里没写 ``alertThresholdUsd`` 的地址直接跳过（不替用户拍板）。

        返回实际改动的行数。
        """
        now = _now_ms()
        updated = 0
        with self.lock:
            for row in rows:
                if row.get("alertThresholdUsd") is None:
                    continue
                target = max(0.0, float(row["alertThresholdUsd"]))
                if target == DEFAULT_ALERT_THRESHOLD_USD:
                    continue
                cursor = self.conn.execute(
                    """
                    UPDATE wallets
                    SET alert_threshold_usd = ?, updated_at = ?
                    WHERE source_kind = 'seed'
                      AND lower(address) = ?
                      AND alert_threshold_usd = ?
                    """,
                    (target, now, str(row["address"]).casefold(), DEFAULT_ALERT_THRESHOLD_USD),
                )
                updated += max(0, int(cursor.rowcount or 0))
            self.conn.commit()
        return updated

    def repair_legacy_seed_labels(self, rows: tuple[dict[str, Any], ...]) -> int:
        """Apply verified per-wallet labels without touching user-edited records.

        只会重命名「仍是旧占位标签」的种子行：``source_kind = 'seed'`` 且
        ``(nickname, source_name)`` 恰好等于该地址登记过的旧标签之一。用户一旦
        在面板里改过昵称，这对值就对不上，于是**不会被覆盖** —— 这正是本方法
        存在的意义（``seed_wallets`` 用的是 ``INSERT OR IGNORE``，改代码里的
        昵称对已入库的行无效）。

        旧标签默认是两条通用的历史占位（Inq / 其他聪明钱）；个别地址（例如
        先以「身份待核验 · …」入池、之后才由用户指认真实身份的）可在种子上用
        ``legacyLabels`` 自己声明要认领的旧标签对。
        """
        default_legacy_labels = (("Inq5️⃣连杆", "Inq5️⃣连杆"), ("其他聪明钱", "用户预置"))
        updated = 0
        now = _now_ms()
        with self.lock:
            for row in rows:
                legacy_labels = tuple(row.get("legacyLabels") or default_legacy_labels)
                if not legacy_labels:
                    continue
                placeholders = " OR ".join(
                    ["(nickname = ? AND source_name = ?)"] * len(legacy_labels)
                )
                params: list[Any] = [
                    row["nickname"], row["sourceName"], row.get("sourceUrl", ""),
                    row["sourceEvidence"], now, row["address"].casefold(),
                ]
                for nickname, source_name in legacy_labels:
                    params += [nickname, source_name]
                cursor = self.conn.execute(
                    f"""
                    UPDATE wallets
                    SET nickname = ?, source_name = ?, source_url = ?, source_evidence = ?, updated_at = ?
                    WHERE source_kind = 'seed'
                      AND lower(address) = ?
                      AND ({placeholders})
                    """,
                    params,
                )
                updated += max(0, int(cursor.rowcount or 0))
            self.conn.commit()
        return updated

    def cursor(self, key: str) -> str:
        with self.lock:
            row = self.conn.execute("SELECT cursor_value FROM cursors WHERE cursor_key = ?", (key,)).fetchone()
        return str(row[0]) if row else ""

    def set_cursor(self, key: str, value: Any) -> None:
        now = _now_ms()
        with self.lock:
            self.conn.execute(
                "INSERT INTO cursors(cursor_key, cursor_value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(cursor_key) DO UPDATE SET cursor_value=excluded.cursor_value, updated_at=excluded.updated_at",
                (str(key), str(value), now),
            )
            self.conn.commit()

    def update_health(self, chain: str, status: str, message: str = "", cursor: str = "") -> None:
        now = _now_ms()
        ok_at = now if status in {"ok", "baseline"} else 0
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO health(chain,status,message,cursor_value,last_checked_at,last_ok_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(chain) DO UPDATE SET
                    status=excluded.status, message=excluded.message, cursor_value=excluded.cursor_value,
                    last_checked_at=excluded.last_checked_at,
                    last_ok_at=CASE WHEN excluded.last_ok_at > 0 THEN excluded.last_ok_at ELSE health.last_ok_at END
                """,
                (chain, status, str(message or "")[:300], str(cursor or ""), now, ok_at),
            )
            self.conn.commit()

    def health(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM health ORDER BY chain").fetchall()
        return [_row_dict(row) for row in rows]

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        chain = normalize_chain(event.get("chain"))
        wallet = normalize_wallet_address(chain, event.get("walletAddress"))
        tx_hash = str(event.get("transactionHash") or "").strip()
        token = str(event.get("tokenAddress") or "").strip()
        if not tx_hash or not token:
            raise ValueError("聪明钱买入事件缺少交易哈希或代币地址")
        event_key = str(event.get("eventKey") or hashlib.sha256(
            f"{chain}|{tx_hash.casefold()}|{wallet}|{token.casefold()}".encode("utf-8")
        ).hexdigest())
        observed_at = int(event.get("observedAt") or _now_ms())
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        with self.lock:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_key,chain,wallet_address,wallet_nickname,transaction_hash,token_address,symbol,
                    token_amount,payment_asset,payment_amount,payment_usd,price_usd,popup_eligible,
                    alerted_at,observed_at,block_ref,details_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_key, chain, wallet, str(event.get("walletNickname") or "")[:120], tx_hash,
                    token, str(event.get("symbol") or "")[:40], float(event.get("tokenAmount") or 0),
                    str(event.get("paymentAsset") or "")[:40], float(event.get("paymentAmount") or 0),
                    float(event.get("paymentUsd") or 0), float(event.get("priceUsd") or 0),
                    1 if event.get("popupEligible") else 0, 0, observed_at,
                    str(event.get("blockRef") or "")[:100], json.dumps(details, ensure_ascii=False),
                ),
            )
            created = bool(cursor.rowcount)
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM events WHERE event_key = ?", (event_key,)).fetchone()
        return {"created": created, "event": _row_dict(row)}

    def mark_alerted(self, event_key: str, alerted_at: int | None = None) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE events SET alerted_at = ? WHERE event_key = ? AND alerted_at = 0",
                (int(alerted_at or _now_ms()), event_key),
            )
            self.conn.commit()

    def token_alerted_since(
        self, chain: Any, wallet_address: Any, token_address: Any, since_ms: int
    ) -> bool:
        """这个 (链, 钱包, 代币) 在 ``since_ms`` 之后有没有弹过窗。

        只看 ``alerted_at >= since_ms``：那是「真的投递过」的时间戳，被抑制的行
        一直是 0，不会把自己挡住。用于「同一个标的当天只弹第一条」。
        """
        token = str(token_address or "").strip()
        wallet = normalize_wallet_address(normalize_chain(chain), wallet_address)
        if not token or not wallet:
            return False
        with self.lock:
            row = self.conn.execute(
                """
                SELECT 1 FROM events
                 WHERE chain = ? AND wallet_address = ? AND token_address = ?
                   AND alerted_at > 0 AND alerted_at >= ?
                 LIMIT 1
                """,
                (normalize_chain(chain), wallet, token, int(since_ms)),
            ).fetchone()
        return row is not None

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY observed_at DESC, id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def truncated_symbol_rows(self, limit: int = 6, chain: str | None = None) -> list[dict[str, Any]]:
        """symbol 仍是「缩写合约」（`J1D7e7…eWfv` 这种）的去重清单，用来回头补名字。

        真 symbol 里不会出现省略号，所以 ``LIKE '%…%'`` 就是准确判据。新事件在前
        （``ORDER BY max(id) DESC``），因为刚发的新盘最有可能「等几十秒就有索引」。
        """
        sql = "SELECT chain, token_address FROM events WHERE symbol LIKE '%…%'"
        params: list[Any] = []
        if chain:
            sql += " AND chain = ?"
            params.append(str(chain))
        sql += " GROUP BY chain, token_address ORDER BY max(id) DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def rename_token_symbol(self, token_address: str, symbol: str) -> int:
        """把某个铸币上所有「缩写合约」事件改成真名字（Solana 地址大小写敏感，按原样比）。"""
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE events SET symbol = ? WHERE token_address = ? AND symbol LIKE '%…%'",
                (str(symbol)[:40], str(token_address)),
            )
            self.conn.commit()
            return int(cursor.rowcount or 0)

    def buys_for_token(
        self,
        chain: Any,
        token_address: Any,
        *,
        since_ms: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """某个链上合约最近的买单（按时间正序）。给「KOL 共振」统计用。

        ``token_address`` 大小写不敏感：EVM 地址在库里可能是校验和写法，Solana mint
        则是大小写敏感的大小写混合串 —— EVM 走 ``lower()``、base58 原样。
        """
        normalized_chain = normalize_chain(chain)
        token = str(token_address or "").strip()
        if not normalized_chain or not token:
            return []
        is_evm = token.lower().startswith("0x")
        column = "lower(token_address)" if is_evm else "token_address"
        value = token.lower() if is_evm else token
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM events WHERE chain = ? AND {column} = ? AND observed_at >= ? "
                "ORDER BY observed_at ASC, id ASC LIMIT ?",
                (normalized_chain, value, int(since_ms), max(1, min(2000, int(limit)))),
            ).fetchall()
        return [_row_dict(row) for row in rows]


def _hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    raw = str(value or "0")
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except (TypeError, ValueError):
        return 0


def _topic_wallet(topic: Any) -> str:
    value = str(topic or "").lower().removeprefix("0x")
    return "0x" + value[-40:] if len(value) >= 40 else ""


def classify_evm_buy(
    chain: Any,
    wallet_address: Any,
    transaction: dict[str, Any],
    receipt: dict[str, Any],
    token_metadata: Callable[[str], dict[str, Any]],
    *,
    native_price_usd: float = 0.0,
    threshold_usd: float = DEFAULT_ALERT_THRESHOLD_USD,
    allow_quote_payment: bool = False,
) -> dict[str, Any] | None:
    normalized_chain = normalize_chain(chain)
    wallet = normalize_wallet_address(normalized_chain, wallet_address)
    if _hex_int(receipt.get("status")) != 1:
        return None
    operation = str(transaction.get("operation") or receipt.get("operation") or "").casefold()
    if any(word in operation for word in EXCLUDED_OPERATION_WORDS):
        return None
    net: dict[str, int] = {}
    for log in receipt.get("logs") or []:
        if not isinstance(log, dict):
            continue
        topics = log.get("topics") if isinstance(log.get("topics"), list) else []
        if len(topics) < 3 or str(topics[0]).casefold() != TRANSFER_TOPIC:
            continue
        token = str(log.get("address") or "").lower()
        sender = _topic_wallet(topics[1])
        recipient = _topic_wallet(topics[2])
        amount = _hex_int(log.get("data"))
        if sender == wallet:
            net[token] = net.get(token, 0) - amount
        if recipient == wallet:
            net[token] = net.get(token, 0) + amount
    if not net:
        return None
    metadata: dict[str, dict[str, Any]] = {}
    normalized: dict[str, float] = {}
    for token, raw_amount in net.items():
        meta = token_metadata(token) or {}
        metadata[token] = meta
        decimals = max(0, min(36, int(meta.get("decimals", 18) or 18)))
        normalized[token] = raw_amount / (10 ** decimals)

    stable_spends: list[tuple[str, float, float]] = []
    wrapped_spends: list[tuple[str, float, float]] = []
    targets: list[tuple[str, float, dict[str, Any]]] = []
    for token, amount in normalized.items():
        meta = metadata[token]
        symbol = str(meta.get("symbol") or "").upper()
        price = float(meta.get("priceUsd") or 0)
        stable = bool(meta.get("stable")) or (symbol in STABLE_SYMBOLS and (price == 0 or 0.8 <= price <= 1.2))
        wrapped = symbol in WRAPPED_NATIVE_SYMBOLS
        if amount < 0 and stable:
            stable_spends.append((symbol or "USD", abs(amount), abs(amount) * (price or 1.0)))
        elif amount < 0 and wrapped and native_price_usd > 0:
            wrapped_spends.append((symbol, abs(amount), abs(amount) * native_price_usd))
        elif amount > 0 and not stable and not wrapped:
            targets.append((token, amount, meta))
    native_amount = _hex_int(transaction.get("value")) / 10**18
    payment_options = [*stable_spends, *wrapped_spends]
    if native_amount > 0 and native_price_usd > 0:
        payment_options.append((CHAIN_META[normalized_chain]["native"], native_amount, native_amount * native_price_usd))
    if not targets:
        return None
    payment_source = "stable-or-native"
    if not payment_options and allow_quote_payment:
        # 兜底①：花掉的是个「有报价的非稳定代币」（pons榜5 的 DELTA 就属于这类）。
        quote_spends: list[tuple[str, float, float]] = []
        for token, amount in normalized.items():
            if amount >= 0:
                continue
            meta = metadata[token]
            symbol = str(meta.get("symbol") or "").upper()
            price = float(meta.get("priceUsd") or 0)
            if price <= 0 or symbol in STABLE_SYMBOLS or symbol in WRAPPED_NATIVE_SYMBOLS:
                continue
            quote_spends.append((symbol or token[:10], abs(amount), abs(amount) * price))
        if quote_spends:
            payment_options = quote_spends
            payment_source = "quote-token"
    if not payment_options and allow_quote_payment:
        # 兜底②：连付款方的价都没有，就用「买到的那部分市值」当这笔买入的名义规模。
        priced = [
            (token, amount, meta)
            for token, amount, meta in targets
            if float(meta.get("priceUsd") or 0) > 0
        ]
        if priced:
            token, amount, meta = max(priced, key=lambda row: row[1] * float(row[2].get("priceUsd") or 0))
            estimated = amount * float(meta["priceUsd"])
            payment_options = [("按成交额估算", estimated, estimated)]
            payment_source = "target-value"
    if not payment_options:
        return None
    payment_asset, payment_amount, payment_usd = max(payment_options, key=lambda row: row[2])
    target_token, target_amount, target_meta = max(
        targets,
        key=lambda row: row[1] * float(row[2].get("priceUsd") or 0) or row[1],
    )
    tx_hash = str(transaction.get("hash") or receipt.get("transactionHash") or "")
    price_usd = payment_usd / target_amount if target_amount > 0 else 0
    return {
        "chain": normalized_chain,
        "walletAddress": wallet,
        "transactionHash": tx_hash,
        "tokenAddress": target_token,
        "symbol": str(target_meta.get("symbol") or "UNKNOWN")[:40],
        "tokenAmount": target_amount,
        "paymentAsset": payment_asset,
        "paymentAmount": payment_amount,
        "paymentUsd": payment_usd,
        "priceUsd": price_usd,
        "popupEligible": payment_usd >= float(threshold_usd),
        "observedAt": _now_ms(),
        "blockRef": str(receipt.get("blockNumber") or transaction.get("blockNumber") or ""),
        "details": {"classification": "net-token-flow", "readOnly": True, "paymentSource": payment_source},
    }


def _solana_token_balances(rows: Any, wallet: str) -> dict[str, float]:
    balances: dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("owner") or "") != wallet:
            continue
        amount = row.get("uiTokenAmount") if isinstance(row.get("uiTokenAmount"), dict) else {}
        raw = amount.get("uiAmountString")
        try:
            value = float(raw if raw is not None else amount.get("uiAmount") or 0)
        except (TypeError, ValueError):
            value = 0.0
        mint = str(row.get("mint") or "")
        if mint:
            balances[mint] = balances.get(mint, 0.0) + value
    return balances


def classify_solana_buy(
    wallet_address: Any,
    payload: dict[str, Any],
    token_metadata: Callable[[str], dict[str, Any]],
    *,
    native_price_usd: float = 0.0,
    threshold_usd: float = DEFAULT_ALERT_THRESHOLD_USD,
) -> dict[str, Any] | None:
    wallet = normalize_wallet_address("solana", wallet_address)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if not meta or meta.get("err") is not None:
        return None
    operation = str(payload.get("operation") or "").casefold()
    if any(word in operation for word in EXCLUDED_OPERATION_WORDS):
        return None
    before = _solana_token_balances(meta.get("preTokenBalances"), wallet)
    after = _solana_token_balances(meta.get("postTokenBalances"), wallet)
    mints = set(before) | set(after)
    changes = {mint: after.get(mint, 0) - before.get(mint, 0) for mint in mints}
    payment_options: list[tuple[str, float, float]] = []
    targets: list[tuple[str, float, dict[str, Any]]] = []
    for mint, amount in changes.items():
        token_meta = token_metadata(mint) or {}
        raw_symbol = str(token_meta.get("symbol") or "").strip()
        # 元数据没名字时**不要**塞 "UNKNOWN" 进来：那个串会一路 `.upper()` 进入
        # ``stable`` 判定和 :data:`WRAPPED_NATIVE_SYMBOLS` 比较，纯属污染。
        # 真名字在下面 ``target_symbol`` 那里统一兜底（铸币缩写）。
        symbol = ("" if raw_symbol.upper() == "UNKNOWN" else raw_symbol) or SOLANA_STABLE_MINTS.get(mint, "")
        symbol = symbol.upper()
        price = float(token_meta.get("priceUsd") or 0)
        stable = mint in SOLANA_STABLE_MINTS or bool(token_meta.get("stable")) or (
            symbol in STABLE_SYMBOLS and (price == 0 or 0.8 <= price <= 1.2)
        )
        if amount < 0 and stable:
            payment_options.append((symbol or "USD", abs(amount), abs(amount) * (price or 1.0)))
        elif amount > 0 and not stable and symbol not in WRAPPED_NATIVE_SYMBOLS:
            targets.append((mint, amount, token_meta))
    message = (payload.get("transaction") or {}).get("message") or {}
    account_keys = message.get("accountKeys") if isinstance(message, dict) else []
    wallet_index = -1
    for index, key in enumerate(account_keys or []):
        public_key = key.get("pubkey") if isinstance(key, dict) else key
        if str(public_key or "") == wallet:
            wallet_index = index
            break
    pre_balances = meta.get("preBalances") or []
    post_balances = meta.get("postBalances") or []
    native_spend = 0.0
    if 0 <= wallet_index < len(pre_balances) and wallet_index < len(post_balances):
        fee = int(meta.get("fee") or 0) if wallet_index == 0 else 0
        native_spend = max(0, int(pre_balances[wallet_index] or 0) - int(post_balances[wallet_index] or 0) - fee) / 10**9
    if native_spend > 0 and native_price_usd > 0:
        payment_options.append(("SOL", native_spend, native_spend * native_price_usd))
    if not targets or not payment_options:
        return None
    payment_asset, payment_amount, payment_usd = max(payment_options, key=lambda row: row[2])
    target_mint, target_amount, target_meta = max(
        targets,
        key=lambda row: row[1] * float(row[2].get("priceUsd") or 0) or row[1],
    )
    signatures = (payload.get("transaction") or {}).get("signatures") or []
    tx_hash = str(signatures[0] if signatures else payload.get("signature") or "")
    # 符号兜底顺序（2026-11-21）：元数据里的 symbol → 元数据里的 name → 铸币地址缩写。
    # **不再写 "UNKNOWN"** —— 满屏 UNKNOWN 等于没给信息，而 ``9sFGCTjE…pump``
    # 这种缩写至少能让用户一眼认出是哪个盘（也能直接拿去 GMGN 搜）。
    # 但缩写只是**最后**兜底：用户 2026-11-21 截图「zrool 买入 J1D7e7…eWfv」——
    # 新盘在 DexScreener 还没 pair、DAS 也查不到时，Jupiter 索引里其实已经有名字了。
    # 缩写用 head 6 / tail 4，和 server.py 的 ``smart_money_short_address`` 同口径
    # （此处不能反向 import server.py，所以就地展开）。
    target_symbol = str(target_meta.get("symbol") or "").strip()
    if not target_symbol or target_symbol.upper() == "UNKNOWN":
        target_symbol = str(target_meta.get("name") or "").strip()
    if not target_symbol or target_symbol.upper() == "UNKNOWN":
        mint_text = str(target_mint or "").strip()
        target_symbol = f"{mint_text[:6]}…{mint_text[-4:]}" if len(mint_text) > 12 else (mint_text or "未知代币")
    return {
        "chain": "solana", "walletAddress": wallet, "transactionHash": tx_hash,
        "tokenAddress": target_mint, "symbol": target_symbol[:40],
        "tokenAmount": target_amount, "paymentAsset": payment_asset, "paymentAmount": payment_amount,
        "paymentUsd": payment_usd, "priceUsd": payment_usd / target_amount if target_amount else 0,
        "popupEligible": payment_usd >= float(threshold_usd), "observedAt": _now_ms(),
        "blockRef": str(payload.get("slot") or ""),
        "details": {"classification": "solana-balance-delta", "readOnly": True},
    }


class SmartMoneyMonitor:
    def __init__(
        self,
        path: str | Path,
        *,
        request_session: requests.Session | None = None,
        poll_interval_seconds: float | None = None,
        seed_defaults: bool = False,
    ):
        self.store = SmartMoneyStore(path)
        self.session = request_session or requests.Session()
        # 每条链现在跑在自己的线程里（见 ``_worker``），共享一个 requests.Session
        # 会让 urllib3 连接池在并发下互相干扰（同一 Session 的 cookie/headers 是可变
        # 状态）。外部传进来的 session（测试里的假对象）必须原样返回，只有我们自己
        # 建的才按线程分桶。
        self._base_session = self.session
        self._session_injected = request_session is not None
        self._session_local = threading.local()
        self.poll_interval_seconds = max(3.0, float(
            poll_interval_seconds or os.getenv("SMART_MONEY_POLL_INTERVAL_SECONDS", "6") or 6
        ))
        #: 各链自己的轮询节奏（秒）。0 = 「上一轮跑完就立刻再来」。
        #: 以前六条链串行，一条慢全慢：Solana 一轮 ~140 秒，BSC/ETH 就得跟着等
        #: 140 秒才看一眼区块，用户 2026-11-17：「有些钱包买入非常不及时」。
        self.chain_interval_seconds = dict(DEFAULT_CHAIN_INTERVAL_SECONDS)
        for chain in SUPPORTED_CHAINS:
            override = os.getenv(f"SMART_MONEY_{chain.upper()}_INTERVAL_SECONDS", "").strip()
            if override:
                try:
                    self.chain_interval_seconds[chain] = max(0.0, float(override))
                except ValueError:
                    pass
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.poll_lock = threading.Lock()
        # 同一条链同一时刻只允许一个线程在跑（后台调度和手动「立即刷新」可能撞车）。
        self._chain_guard = threading.Lock()
        self._chain_running: set[str] = set()
        self.thread: threading.Thread | None = None
        self.running = False
        self.alert_callback: Callable[[dict[str, Any]], Any] | None = None
        # 「KOL 共振」单独提醒的回调（用户 2026-10-27）。只在三个人**都**买了同一个
        # 代币时触发一次，见 :meth:`_maybe_emit_kol_sync`。
        self.sync_alert_callback: Callable[[dict[str, Any]], Any] | None = None
        self.kol_sync_wallets: dict[str, str] = active_kol_sync_wallets()
        # 「同车增加」看的全量追踪钱包（含核心三人）。测试里可以替换。
        self.kol_sync_track_wallets: dict[str, str] = active_kol_sync_track_wallets()
        # Solana 的观察面来自 wallets 表（见 :meth:`_solana_track_wallets`），(取数时刻, 表)。
        self._solana_track_cache: tuple[float, dict[str, str]] = (0.0, {})
        self._sync_sent: dict[str, float] = {}
        # 每个标的的同车「剧集」：{announced: set(已在车上并已播报过的地址), trioAt, episodeStart}
        self._sync_episodes: dict[str, dict[str, Any]] = {}
        self._sync_lock = threading.Lock()
        # Optional host-supplied gate.  When it returns False the monitor stops
        # taking NEW buys in (the board's intake switch); already recorded buys
        # stay untouched.
        self.intake_guard: Callable[[], bool] | None = None
        self._metadata_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._native_price_cache: dict[str, tuple[float, float]] = {}
        if seed_defaults:
            self.seed_defaults()

    def seed_defaults(self) -> int:
        self.store.repair_legacy_seed_labels(DEFAULT_SMART_MONEY_SEEDS)
        rows = [
            {
                "chain": chain,
                "address": seed["address"],
                "nickname": seed["nickname"],
                "sourceName": seed["sourceName"],
                "sourceKind": "seed",
                "sourceUrl": seed.get("sourceUrl", ""),
                "sourceEvidence": seed["sourceEvidence"],
                "alertThresholdUsd": float(seed.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
            }
            for seed in DEFAULT_SMART_MONEY_SEEDS
            for chain in seed.get("chains", EVM_CHAINS)
        ]
        inserted = self.store.seed_wallets(rows)
        # 阈值不能靠 INSERT OR IGNORE 生效（已存在的行原样保留），单独推一次：
        # 只改「还是默认 10000U」的种子行，用户调过的不动。
        self.store.apply_seed_thresholds(rows)
        return inserted

    def set_alert_callback(self, callback: Callable[[dict[str, Any]], Any] | None) -> None:
        self.alert_callback = callback

    def set_sync_alert_callback(
        self, callback: Callable[[dict[str, Any]], Any] | None
    ) -> None:
        """「KOL 共振」弹窗回调；不设就只统计不提醒（测试/离线脚本默认不设）。"""
        self.sync_alert_callback = callback

    def _solana_track_wallets(self) -> dict[str, str]:
        """Solana 的同车观察面 = ``wallets`` 表里启用的 solana 地址（60 秒缓存）。

        为什么动态取：Solana 那边的钱包是用户随口在加的（「这个 KOL 也加一下」），
        写死成 :data:`KOL_SYNC_TRACK_LABELS` 那样一张常量表必然漂移 —— 加完钱包发现
        同车不认他，就是漏在这一步。EVM 侧仍用显式名单（那边有「核心 / 外围」之分，
        不能把随机新地址算进共振）。

        名字优先取 ``KOL_SYNC_TRACK_LABELS`` 里的：CryptoCharming 的两个 sol 钱包在库里
        都叫 CryptoCharming，卡片上就分不清谁是谁（那份名单写的是 ·2 / ·3），再退回库里的
        nickname。

        注意 key 一律 ``lower()``：这只在内存里当匹配键，**不回写数据库、不做身份判断**
        —— base58 一旦落库成小写就是另一个地址（见 :func:`wallet_key` 那条铁律）。
        判定侧本来就把事件的 ``walletAddress`` 小写后再比，两边一致即可。
        """
        now = time.time()
        cached_at, cached = getattr(self, "_solana_track_cache", (0.0, {}))
        if cached_at and now - cached_at < KOL_SYNC_SOLANA_TRACK_TTL_SECONDS:
            return cached
        explicit = {
            str(address).lower(): str(label)
            for address, label in (self.kol_sync_track_wallets or {}).items()
        }
        try:
            rows = self.store.list_wallets(enabled_only=True)
        except Exception:
            # 读不到库就退回显式名单里的 sol 地址 —— 同车不能因为一次查询失败整条哑掉。
            return {a: l for a, l in explicit.items() if not a.startswith("0x")}
        wallets: dict[str, str] = {}
        for row in rows:
            if normalize_chain(row.get("chain")) != "solana":
                continue
            address = str(row.get("address") or "").strip()
            if not address:
                continue
            name = explicit.get(address.lower()) or str(row.get("nickname") or "").strip()
            wallets[address.lower()] = name or _short_wallet(address)
        # 显式名单里有、库里没有的 sol 地址也要算（测试库 / 换库时）。
        for address, label in explicit.items():
            if not address.startswith("0x"):
                wallets.setdefault(address, label)
        self._solana_track_cache = (now, wallets)
        return wallets

    def _maybe_emit_kol_sync(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """刚记录的一笔买入，能不能触发「同车」提醒。两种模式：

        * ``trio``：核心三人（CryptoCharming / 阿峰 / cowboy）凑齐 → 弹一次；
        * ``join``：同车已成立后，**别的追踪钱包**又上了同一辆车 → 每次新上车都提醒一次
          （用户 2026-10-27：「在这个基础上，如果在这个同车标的上还有其他的 KOL 再进来，
          继续做同车增加的提醒」）。已经在车上的人不重复播报，靠
          ``_sync_episodes[token_key]["announced"]`` 记账。

        判定全部基于**事件表里的真实买入记录**，不看谁弹过窗 —— 金额可能都没过自己的
        门槛，但「都上车了」这件事本身才是信号。

        「成立」的口径**按链分**：EVM 要「含核心 ≥2」（核心三人是用户最初点的
        CryptoCharming / 阿峰 / cowboy）；Solana 不设核心，任意 ``minimum`` 个启用钱包
        撞同一个 mint 就成立，另加一道钱闸挡 ``$0`` 伪同车。见
        :data:`KOL_SYNC_ANY_TRACKED_CHAINS` 的注释（含 14 天回测数据）。
        """
        callback = self.sync_alert_callback
        core = self.kol_sync_wallets
        if callback is None:
            return None
        chain = normalize_chain(event.get("chain"))
        token = str(event.get("tokenAddress") or "").strip()
        if not chain or not token:
            return None
        # 观察面：EVM 走显式名单（核心 + 外围）；Solana 走 wallets 表里启用的那批
        # （用户随时在加钱包，见 :meth:`_solana_track_wallets`）。
        any_tracked = chain in KOL_SYNC_ANY_TRACKED_CHAINS
        track = (
            self._solana_track_wallets() if any_tracked
            else (self.kol_sync_track_wallets or core)
        )
        if not track:
            return None
        token_key = f"{chain}:{token.lower() if token.lower().startswith('0x') else token}"
        now_ms = _now_ms()
        minimum = max(2, int(os.getenv("XINGYUN_KOL_SYNC_MIN", "") or KOL_SYNC_MIN_WALLETS))
        window_ms = max(1, int(os.getenv(
            "XINGYUN_KOL_SYNC_WINDOW_HOURS", "") or KOL_SYNC_WINDOW_SECONDS // 3600
        )) * 3600 * 1000
        rows = self.store.buys_for_token(chain, token, since_ms=now_ms - window_ms)
        on_board: dict[str, dict[str, Any]] = {}
        for row in rows:
            address = str(row.get("walletAddress") or "").strip().lower()
            if address not in track:
                continue
            usd = float(row.get("paymentUsd") or 0)
            slot = on_board.get(address)
            # 同一人买了多笔：金额相加，时间取最早那笔（「什么时候上车」）。
            if slot is None:
                on_board[address] = {
                    "address": address,
                    "label": track[address],
                    "usd": usd,
                    "firstAt": int(row.get("observedAt") or 0),
                    "count": 1,
                }
            else:
                slot["usd"] += usd
                slot["count"] += 1
                slot["firstAt"] = min(slot["firstAt"], int(row.get("observedAt") or 0))
        core_count = sum(1 for address in on_board if address in core)
        # 「同车成立」两个入口，任一命中即成立：
        #   a) 核心三人齐（2026-10-27 老口径）；
        #   b) 任意 minimum 人撞同一个币、其中至少 KOL_SYNC_MIN_CORE_WALLETS 个是核心
        #      （2026-11-12 新口径，见常量注释里「币安生活」的取证）。
        min_core_for_cluster = max(2, int(
            os.getenv("XINGYUN_KOL_SYNC_MIN_CORE", "") or KOL_SYNC_MIN_CORE_WALLETS
        ))
        cluster_ready = len(on_board) >= minimum and core_count >= min_core_for_cluster
        # 「有真金白银买单的人」有几个 —— 只有 Solana 那条口径才用它（见常量注释：
        # 那边 paymentUsd 常算不出来，会留下 $0 记账，光数人数会把伪同车也报出来）。
        funded_buyers = sum(
            1 for item in on_board.values()
            if float(item.get("usd") or 0) >= KOL_SYNC_FUNDED_MIN_USD
        )
        if any_tracked:
            # Solana：不设核心，只数人 + 钱闸（用户 2026-11-23「SOL 链也做一个同车弹窗」）。
            settled = (
                len(on_board) >= minimum
                and funded_buyers >= max(0, KOL_SYNC_FUNDED_MIN_BUYERS)
            )
        else:
            settled = core_count >= minimum or cluster_ready
        episode = self._sync_episodes.get(token_key)
        base_context = {
            "chain": chain,
            "tokenAddress": token,
            "symbol": str(event.get("symbol") or "").strip(),
            "windowHours": window_ms // 3600000,
            "minBuyers": minimum,
            "coreCount": core_count,
            "requiredCore": min_core_for_cluster,
            # 卡片/测试用它区分口径：True = 这条链要求「含核心」，False = 只数人数。
            "requiresCore": not any_tracked,
            "fundedBuyers": funded_buyers,
            "observedAt": now_ms,
        }

        # ---- 1) 同车成立：所有人记为已播报 ----
        if settled:
            if episode is None:
                episode = {
                    "trioAt": now_ms,
                    "episodeStart": min(item["firstAt"] for item in on_board.values()),
                    "announced": set(on_board),
                    "symbol": base_context["symbol"],
                }
                self._sync_episodes[token_key] = episode
                self._prune_sync_episodes(now_ms)
                with self._sync_lock:
                    # 双闸：并发轮询下同一辆车不能两条线程同时判定通过。
                    last = self._sync_sent.get(token_key, 0.0)
                    if last and now_ms - last < KOL_SYNC_REALERT_SECONDS * 1000:
                        return None
                    self._sync_sent[token_key] = now_ms
                ordered = sorted(on_board.values(), key=lambda item: item["firstAt"])
                context = {
                    **base_context,
                    "mode": "trio",
                    "buyers": ordered,
                    "totalUsd": sum(item["usd"] for item in ordered),
                }
                try:
                    callback(context)
                except Exception:
                    pass
                return context
            # 剧集已存在时**不要提前返回** —— 下面还要看有没有新人上车。

        # ---- 2) 同车已成立：看有没有新的人上车 ----
        if episode is None:
            return None
        join_deadline = episode["episodeStart"] + KOL_SYNC_JOIN_WINDOW_SECONDS * 1000
        if now_ms > join_deadline:
            self._sync_episodes.pop(token_key, None)
            return None
        newcomers = [
            item for address, item in on_board.items()
            if address not in episode["announced"]
        ]
        if not newcomers:
            return None
        episode["announced"].update(item["address"] for item in newcomers)
        newcomers.sort(key=lambda item: item["firstAt"])
        context = {
            **base_context,
            "mode": "join",
            "symbol": base_context["symbol"] or episode.get("symbol") or "",
            "buyers": sorted(on_board.values(), key=lambda item: item["firstAt"]),
            "newcomers": newcomers,
            "totalUsd": sum(item["usd"] for item in on_board.values()),
            "onBoardCount": len(on_board),
            "episodeStart": episode["episodeStart"],
        }
        try:
            callback(context)
        except Exception:
            pass
        return context

    def _prune_sync_episodes(self, now_ms: int) -> None:
        """清掉过了「同车增加」有效期的剧集，别让内存里的字典无限长。"""
        cutoff = now_ms - (KOL_SYNC_JOIN_WINDOW_SECONDS + KOL_SYNC_WINDOW_SECONDS) * 1000
        for key, episode in list(self._sync_episodes.items()):
            if episode.get("episodeStart", 0) < cutoff:
                self._sync_episodes.pop(key, None)

    def set_intake_guard(self, guard: Callable[[], bool] | None) -> None:
        self.intake_guard = guard

    def _intake_allowed(self) -> bool:
        guard = self.intake_guard
        if guard is None:
            return True
        try:
            return bool(guard())
        except Exception:
            # A broken guard must never silently stop the monitor.
            return True

    def add_wallet(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.store.upsert_wallet(
            payload.get("chain"), payload.get("address"), nickname=str(payload.get("nickname") or ""),
            source_name=str(payload.get("sourceName") or "手动添加"), source_kind=str(payload.get("sourceKind") or "manual"),
            source_url=str(payload.get("sourceUrl") or ""), source_evidence=str(payload.get("sourceEvidence") or ""),
            enabled=payload.get("enabled") is not False,
            alert_threshold_usd=float(payload.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
        )

    def ingest_text(self, text: Any, **context: Any) -> list[dict[str, Any]]:
        added: list[dict[str, Any]] = []
        for mention in extract_smart_money_mentions(text, **context):
            added.append(self.store.upsert_wallet(
                mention["chain"], mention["address"], nickname=mention.get("nickname") or "",
                source_name=mention.get("sourceName") or "自动识别", source_kind=mention.get("sourceKind") or "text",
                source_url=mention.get("sourceUrl") or "", source_evidence=mention.get("sourceEvidence") or "",
                observed_at=int(mention.get("observedAt") or _now_ms()),
                preserve_controls=True,
            ))
        return added

    def record_buy(self, event: dict[str, Any]) -> dict[str, Any]:
        wallet = self.store.wallet(event.get("chain"), event.get("walletAddress"))
        threshold = float(wallet.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD)
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        eligible = float(event.get("paymentUsd") or 0) >= threshold
        # 「同一个标的当天只弹第一条」：只在**已经合格**的前提下判，判据是今天有没有
        # 因为这个 (链, 钱包, 代币) 弹过窗。注意记账不受影响 —— 页面照样逐笔显示。
        suppressed = (
            eligible
            and SMART_MONEY_BUY_ONCE_PER_TOKEN_PER_DAY
            and self.store.token_alerted_since(
                event.get("chain"),
                event.get("walletAddress"),
                event.get("tokenAddress"),
                _local_day_start_ms(),
            )
        )
        merged_details = {**details, "alertThresholdUsd": threshold}
        if suppressed:
            merged_details["popupSuppressed"] = "daily-first"
        normalized = {
            **event,
            "walletNickname": event.get("walletNickname") or wallet.get("nickname") or wallet.get("sourceName") or "聪明钱",
            "popupEligible": eligible,
            # The popup card prints the line the event cleared.  The threshold is
            # PER WALLET, so the card cannot hardcode it — carry it on the event.
            "details": merged_details,
        }
        result = self.store.record_event(normalized)
        stored = result["event"]
        if result["created"] and stored.get("popupEligible") and not suppressed and self.alert_callback:
            self.alert_callback(stored)
            self.store.mark_alerted(stored["eventKey"])
            stored["alertedAt"] = _now_ms()
        elif suppressed:
            stored["popupSuppressed"] = "daily-first"
        # 「三个人同时上车」是另一件事：金额可能都没过门槛，但要单独提醒一次。
        if result["created"]:
            self._maybe_emit_kol_sync(stored)
        return {"created": result["created"], "event": stored, "popupSuppressed": suppressed}

    def start(self, *, start_worker: bool = True) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self.stop_event.clear()
            if start_worker:
                self.thread = threading.Thread(target=self._worker, daemon=True, name="smart-money-buy-monitor")
                self.thread.start()

    def stop(self) -> None:
        with self.lock:
            if not self.running:
                return
            self.running = False
            self.stop_event.set()
            thread = self.thread
            self.thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def close(self) -> None:
        self.stop()
        self.store.close()

    def _worker(self) -> None:
        """每条链一个独立槽位：慢链（Solana）不能再把快链按在队列里。

        2026-11-17 之前这里是 ``poll_once()`` 一次跑完六条链，Solana 一轮 ~140 秒，
        BSC / ETH / Base / Robinhood 就只能跟着 140 秒看一眼区块。现在每条链按
        ``chain_interval_seconds`` 各自排期，互不等待。
        """
        executors = {
            chain: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"smart-money-{chain}")
            for chain in SUPPORTED_CHAINS
        }
        inflight: dict[str, Any] = {}
        next_due: dict[str, float] = {}
        try:
            while not self.stop_event.is_set():
                tick_started = time.monotonic()
                try:
                    if self._intake_allowed():
                        wallets = self.store.list_wallets(enabled_only=True)
                        by_chain = {
                            chain: [row for row in wallets if row["chain"] == chain]
                            for chain in SUPPORTED_CHAINS
                        }
                        for chain, rows in by_chain.items():
                            if not rows:
                                continue
                            running = inflight.get(chain)
                            if running is not None:
                                if not running.done():
                                    continue
                                inflight.pop(chain, None)
                                try:
                                    running.result()
                                except Exception:
                                    pass  # _run_chain_once 已经把错误写进 health
                            if tick_started < next_due.get(chain, 0.0):
                                continue
                            inflight[chain] = executors[chain].submit(self._run_chain_once, chain, rows)
                            interval = max(0.0, float(self.chain_interval_seconds.get(chain) or 0.0))
                            next_due[chain] = tick_started + interval if interval > 0 else 0.0
                except Exception:
                    pass
                self.stop_event.wait(self.poll_interval_seconds)
        finally:
            for executor in executors.values():
                executor.shutdown(wait=False)

    def _thread_session(self) -> requests.Session:
        """每条链跑在自己的线程里；共享 Session 的 cookie/headers 是可变状态。"""
        session = self.session
        base = getattr(self, "_base_session", session)
        if session is not base:
            return session  # 被外部/测试替换过，原样尊重
        if getattr(self, "_session_injected", False) or not isinstance(session, requests.Session):
            return session
        local = getattr(self._session_local, "session", None)
        if local is None:
            local = requests.Session()
            local.trust_env = session.trust_env
            local.headers.update(session.headers)
            self._session_local.session = local
        return local

    # 「缩写合约」补名字的节流：稳定态每 2 分钟最多 12 个铸币，事件全部一起改。
    SYMBOL_BACKFILL_INTERVAL = 120.0
    SYMBOL_BACKFILL_BATCH = 12
    # 查不到的铸币压 6 小时再试：老 pump 盘连 Jupiter 都没收录，一遍遍重试只白花配额
    SYMBOL_BACKFILL_MISS_TTL = 6 * 3600.0

    def backfill_truncated_symbols(self, limit: int | None = None) -> int:
        """把 symbol 还是「缩写合约」的事件回头补成真名字（只处理 Solana）。

        用户 2026-11-21 截图：「KOL zrool 买入 J1D7e7…eWfv · $85」—— 落库那一刻这个
        新盘既没有 DexScreener 交易对、DAS 也查不到资产，只能写铸币缩写。等几十秒
        Jupiter 索引就有了，所以这里定期回头补（`events` 上同一铸币全部一起改）。

        **为什么要多取 4 倍候选**：库里躺着几百个老 pump 盘的缩写（DexScreener 没
        交易对、Jupiter 也没收录），它们永远补不上。若按 ``LIMIT batch`` 取最新 12 个，
        一旦这 12 个全是「查不到」，这一轮就白跑、下一轮又是同样的 12 个 —— 真正能补
        的铸币永远轮不到。所以候选取 4 倍，查不到的记进 :attr:`_symbol_backfill_miss`
        压 6 小时，跳过它们继续往下走。
        """
        now = time.time()
        if now - float(getattr(self, "_symbol_backfill_at", 0.0) or 0.0) < self.SYMBOL_BACKFILL_INTERVAL:
            return 0
        self._symbol_backfill_at = now
        batch = max(1, int(limit or self.SYMBOL_BACKFILL_BATCH))
        misses = getattr(self, "_symbol_backfill_miss", None)
        if not isinstance(misses, dict):
            misses = self._symbol_backfill_miss = {}
        if len(misses) > 800:
            cut = now - self.SYMBOL_BACKFILL_MISS_TTL
            for mint, ts in list(misses.items()):
                if float(ts or 0) < cut:
                    misses.pop(mint, None)
        try:
            rows = self.store.truncated_symbol_rows(batch * 4, chain="solana")
        except Exception:
            return 0
        fixed = 0
        attempts = 0
        for row in rows:
            if attempts >= batch:
                break
            mint = str(row.get("token_address") or "")
            if not mint:
                continue
            hit_at = misses.get(mint)
            if hit_at and now - float(hit_at) < self.SYMBOL_BACKFILL_MISS_TTL:
                continue
            attempts += 1
            meta = self.solana_token_metadata(mint)
            symbol = str(meta.get("symbol") or "").strip()
            if not symbol or symbol.upper() == "UNKNOWN" or "…" in symbol:
                misses[mint] = now
                continue
            misses.pop(mint, None)
            fixed += self.store.rename_token_symbol(mint, symbol)
        if fixed:
            print(f"[smart-money] 缩写合约补名字：改了 {fixed} 条事件（查了 {attempts} 个铸币）", flush=True)
        return fixed

    def _run_chain_once(self, chain: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """同一条链永远只有一个线程在跑；已经在跑就返回 None 让调用方跳过。"""
        guard = getattr(self, "_chain_guard", None)
        running = getattr(self, "_chain_running", None)
        if guard is None or running is None:  # 兼容老测试直接构造的实例
            return self._poll_chain(chain, rows)
        with guard:
            if chain in running:
                return None
            running.add(chain)
        try:
            return self._poll_chain(chain, rows)
        finally:
            with guard:
                running.discard(chain)

    def _poll_chain(self, chain: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            if chain == "solana":
                events = self._poll_solana(rows)
                # 顺手把「缩写合约」补成真名字（节流 5 分钟，见 backfill_truncated_symbols）
                try:
                    self.backfill_truncated_symbols()
                except Exception:
                    pass
                return events
            return self._poll_evm(chain, rows)
        except Exception as exc:
            self.store.update_health(chain, "error", str(exc)[:300], self.store.cursor(
                f"solana:{rows[0]['address']}:signature" if chain == "solana" else f"evm:{chain}:block"
            ))
            return []

    def rpc_urls(self, chain: str) -> list[str]:
        meta = CHAIN_META[chain]
        configured = [value.strip() for value in str(os.getenv(meta["rpcEnv"], "") or "").split(",") if value.strip()]
        return list(dict.fromkeys([*configured, *meta["rpcs"]]))

    def rpc(self, chain: str, method: str, params: list[Any]) -> Any:
        errors: list[str] = []
        for url in self.rpc_urls(chain):
            try:
                response = self._thread_session().post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    timeout=(4, 12),
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                return payload.get("result")
            except Exception as exc:
                errors.append(f"{url}: {str(exc)[:120]}")
        raise RuntimeError("；".join(errors) or f"{chain} RPC 不可用")

    def _token_price_and_symbol(self, chain: str, token: str) -> tuple[float, str]:
        """DexScreener 单币行情：返回 ``(priceUsd, symbol)``，取不到就是 ``(0.0, "")``。

        **符号是白捡的**：``/tokens/v1/<dex>/<address>`` 的每一行都自带
        ``baseToken.symbol``（实测 ``AFSASwmc…PEAR`` → ``{"baseToken": {"symbol":
        "PEAR"}, "priceUsd": "0.000009189"}``），以前这里只把 ``priceUsd`` 抠出来、
        把符号丢了，于是 Solana 侧永远拿不到名字（见 :meth:`solana_token_metadata`
        的 2026-11-12 注释）。一次请求同时喂两个消费方，不多花一份配额。

        任何一步失败都返回 ``(0.0, "")`` —— 调用方把它当「没价 / 没名」，
        绝不编一个出来。

        **要重试**（2026-11-12）：本机走 Clash 代理，去 dexscreener 时不时来一发
        ``SSL: UNEXPECTED_EOF_WHILE_READING``（实测两次里就有一次）。早先这里一次不成就
        放弃，于是同一分钟内价格能取到、名字却是 UNKNOWN —— 卡片上就那一行是空的，
        用户一眼就看见。重试一次把这类抖动吸收掉，代价只在失败路径上。
        """
        for attempt in range(2):
            try:
                response = self._thread_session().get(
                    f"https://api.dexscreener.com/tokens/v1/{CHAIN_META[chain]['dex']}/{token}",
                    headers={"Accept": "application/json"}, timeout=(4, 8),
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list):
                    return 0.0, ""
                best = max((row for row in rows if isinstance(row, dict)), key=lambda row: float((row.get("liquidity") or {}).get("usd") or 0), default={})
                price = float(best.get("priceUsd") or 0)
                symbol = str((best.get("baseToken") or {}).get("symbol") or "").strip()
                return price, symbol
            except Exception:
                if attempt == 0:
                    time.sleep(0.6)
                    continue
                return 0.0, ""
        return 0.0, ""

    def _token_price(self, chain: str, token: str) -> float:
        return self._token_price_and_symbol(chain, token)[0]

    @staticmethod
    def _decode_abi_string(value: Any) -> str:
        raw = str(value or "").removeprefix("0x")
        try:
            data = bytes.fromhex(raw)
        except ValueError:
            return ""
        if len(data) >= 64:
            offset = int.from_bytes(data[:32], "big")
            if offset + 32 <= len(data):
                length = int.from_bytes(data[offset:offset + 32], "big")
                return data[offset + 32:offset + 32 + length].decode("utf-8", errors="ignore").strip("\x00")
        return data[:32].decode("utf-8", errors="ignore").strip("\x00")

    def onchain_quote_price(self, chain: str, token: str) -> float:
        """用 Uniswap V3 池子的 slot0 反推「报价代币」的美元价。

        只在 DexScreener 查不到价时兜底（:data:`ONCHAIN_QUOTE_POOLS`）。池子一侧
        必须是 1 美元的稳定币，否则算出来的不是美元价。任何一步失败都返回 0.0 ——
        调用方把它当「没价」，绝不写一个编出来的金额。
        """
        spec = ONCHAIN_QUOTE_POOLS.get((normalize_chain(chain), str(token or "").lower()))
        if not spec:
            return 0.0
        try:
            slot0 = self.rpc(chain, "eth_call", [{"to": spec["pool"], "data": "0x3850c7bd"}, "latest"])
            sqrt_price_x96 = _hex_int(str(slot0 or "")[:66])
            if sqrt_price_x96 <= 0:
                return 0.0
            # sqrtPriceX96 = sqrt(token1_raw / token0_raw) * 2^96
            ratio = (sqrt_price_x96 / float(2**96)) ** 2
            # 换成人类单位：1 个 quote（token0）能换多少个本币（token1）
            quote_to_token = ratio * 10 ** (int(spec["quoteDecimals"]) - int(spec["tokenDecimals"]))
            if quote_to_token <= 0:
                return 0.0
            if int(spec["tokenIndex"]) == 1:
                return float(spec["quotePriceUsd"]) / quote_to_token
            return float(spec["quotePriceUsd"]) * quote_to_token
        except Exception:
            return 0.0

    def evm_token_metadata(self, chain: str, token: str) -> dict[str, Any]:
        key = (chain, token.lower())
        cached = self._metadata_cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return dict(cached[1])
        symbol = ""
        decimals = 18
        try:
            decimals = _hex_int(self.rpc(chain, "eth_call", [{"to": token, "data": "0x313ce567"}, "latest"]))
            symbol = self._decode_abi_string(self.rpc(chain, "eth_call", [{"to": token, "data": "0x95d89b41"}, "latest"]))
        except Exception:
            pass
        price = self._token_price(chain, token)
        if price <= 0:
            price = self.onchain_quote_price(chain, token)
        result = {
            "symbol": symbol[:40] or "UNKNOWN", "decimals": decimals if 0 <= decimals <= 36 else 18,
            "priceUsd": price, "stable": symbol.upper() in STABLE_SYMBOLS and (price == 0 or 0.8 <= price <= 1.2),
        }
        self._metadata_cache[key] = (time.time(), result)
        return dict(result)

    def solana_token_metadata(self, mint: str) -> dict[str, Any]:
        """Solana 铸币的 ``{symbol, priceUsd, stable}``。

        **曾经的 bug（2026-11-12 修）**：这里只查 :data:`SOLANA_STABLE_MINTS`，
        查不到就一律返回 ``"UNKNOWN"`` —— 根本没有取名字的那一步，所以链上扫描
        （``classification="solana-balance-delta"``）落库的 2732 条事件符号**全是**
        字面量 ``UNKNOWN``，卡片标题就成了「KOL cowboy 买入 UNKNOWN · $807」。

        现在的取名顺序（前一步拿不到才走下一步，绝不编造）：
        1. 稳定币表命中（USDC/USDT/SOL 这些固定铸币）；
        2. DexScreener 的 ``baseToken.symbol`` —— 和取价同一次请求，白捡；
        3. Solana RPC ``getAsset``（Metaplex 标准，覆盖 pump.fun 等新发币）。

        三步全空才回落 ``UNKNOWN``；那种情况多半是刚发出来还没有任何索引的盘，
        几分钟后下一轮自然能补上。注意缓存 1 小时：**取到 UNKNOWN 时不留缓存**，
        否则一次抽风会让这个币一小时都没名字。
        """
        key = ("solana", mint)
        cached = self._metadata_cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return dict(cached[1])
        price, symbol = self._token_price_and_symbol("solana", mint)
        symbol = symbol or SOLANA_STABLE_MINTS.get(mint, "")
        name = ""
        if not symbol:
            # DexScreener 对新盘常常还是一条 pair 都没有（`{"pairs": null}`），
            # 这时换 Jupiter 的免费索引拿名字 —— 实测新发的 pump 盘几秒后就有。
            symbol, name = self._jupiter_token(mint)
        if not symbol:
            symbol = self._solana_onchain_symbol(mint)
        result = {
            "symbol": symbol[:40] or "UNKNOWN", "name": name[:60], "priceUsd": price,
            "stable": mint in SOLANA_STABLE_MINTS,
        }
        if symbol:
            self._metadata_cache[key] = (time.time(), result)
        return dict(result)

    def _jupiter_token(self, mint: str) -> tuple[str, str]:
        """Jupiter lite API（免费、免 key）里查 ``(symbol, name)``，取不到返回 ``("", "")``。

        ``/tokens/v2/search?query=<mint>`` 是按关键词搜的，会顺带返回别的币，
        所以必须校验 ``id`` 与铸币完全相同才算数。超时给得很紧（3/6 秒）：
        这是弹窗链路，宁可没名字也不能把提醒卡住。
        """
        try:
            response = self._thread_session().get(
                "https://lite-api.jup.ag/tokens/v2/search",
                params={"query": mint}, timeout=(3, 6),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            return "", ""
        if not isinstance(data, list):
            return "", ""
        for row in data:
            if isinstance(row, dict) and str(row.get("id") or "") == mint:
                return (str(row.get("symbol") or "").strip()[:40],
                        str(row.get("name") or "").strip()[:60])
        return "", ""

    def _solana_onchain_symbol(self, mint: str) -> str:
        """走 Solana RPC ``getAsset`` 读 Metaplex 元数据里的符号，失败返回 ``""``。

        ``getAsset`` 是 DAS 标准接口，公共端点（含 Helius 免费档、Ankr）都支持。
        返回结构形如 ``{"result": {"content": {"metadata": {"symbol": "XYZ"}}}}``，
        但也有端点把它塞在 ``content.metadata.name`` 里，所以下面按三层兜。
        """
        try:
            # rpc() 已经把 JSON-RPC 的 result 解出来了，直接就是资产对象
            payload = self.rpc("solana", "getAsset", [{"id": mint}])
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        content = payload.get("content") or {}
        metadata = (content.get("metadata") or {}) if isinstance(content, dict) else {}
        for candidate in (
            metadata.get("symbol"),
            (payload.get("token_info") or {}).get("symbol"),
            (payload.get("mpl_core_asset") or {}).get("symbol"),
        ):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    def native_price_usd(self, chain: str) -> float:
        symbol = CHAIN_META[chain]["native"]
        cached = self._native_price_cache.get(symbol)
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        try:
            response = self._thread_session().get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": f"{symbol}USDT"}, timeout=(4, 8),
            )
            response.raise_for_status()
            price = float(response.json().get("price") or 0)
        except Exception:
            price = 0.0
        self._native_price_cache[symbol] = (time.time(), price)
        return price

    def _poll_evm(self, chain: str, wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        latest = _hex_int(self.rpc(chain, "eth_blockNumber", []))
        key = f"evm:{chain}:block"
        previous = _hex_int(self.store.cursor(key))
        if not previous:
            self.store.set_cursor(key, latest)
            self.store.update_health(chain, "baseline", "已建立最新区块基线，不追溯旧交易", str(latest))
            return []
        if latest <= previous:
            self.store.update_health(chain, "ok", "已同步到最新区块", str(latest))
            return []
        watched = {row["address"]: row for row in wallets}
        events: list[dict[str, Any]] = []
        max_blocks = evm_max_blocks_per_poll(chain)
        start = max(previous + 1, latest - max_blocks + 1)
        skipped = max(0, start - previous - 1)
        candidates: dict[str, set[str]] = {}
        candidate_transactions: dict[str, dict[str, Any]] = {}
        recipient_topics = ["0x" + wallet.removeprefix("0x").rjust(64, "0") for wallet in watched]
        # Public BSC RPCs refuse address-less ``eth_getLogs`` (verified 2026-10-10:
        # publicnode → -32701 "Please specify an address in your request", bnbchain
        # dataseeds → -32005 "limit exceeded"), and this filter cannot carry an
        # ``address`` because the bought token is unknown before the query.  So BSC
        # always takes the raw-block path; ``evm_fallback_blocks`` sizes its window.
        log_error: Exception | None = (
            RuntimeError("BSC 公共日志接口受限") if chain == "bsc" else None
        )
        if log_error is None:
            try:
                logs = self.rpc(chain, "eth_getLogs", [{
                    "fromBlock": hex(start),
                    "toBlock": hex(latest),
                    "topics": [TRANSFER_TOPIC, None, recipient_topics],
                }]) or []
                for log in logs:
                    tx_hash = str(log.get("transactionHash") or "")
                    topics = log.get("topics") or []
                    recipient = "0x" + str(topics[2])[-40:].lower() if len(topics) > 2 else ""
                    if tx_hash and recipient in watched:
                        candidates.setdefault(tx_hash, set()).add(recipient)
                    elif tx_hash and len(watched) == 1:
                        candidates.setdefault(tx_hash, set()).add(next(iter(watched)))
            except Exception as exc:
                log_error = exc

        if log_error is not None:
            fallback_max = evm_fallback_blocks(chain)
            fallback_start = max(previous + 1, latest - fallback_max + 1)
            skipped = max(skipped, max(0, fallback_start - previous - 1))
            block_numbers = list(range(fallback_start, latest + 1))
            blocks: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=min(4, len(block_numbers))) as executor:
                futures = {
                    executor.submit(self.rpc, chain, "eth_getBlockByNumber", [hex(block_number), True]): block_number
                    for block_number in block_numbers
                }
                for future in as_completed(futures):
                    blocks[futures[future]] = future.result() or {}
            for block_number in block_numbers:
                block = blocks.get(block_number) or {}
                for transaction in block.get("transactions") or []:
                    sender = str(transaction.get("from") or "").lower()
                    tx_hash = str(transaction.get("hash") or "")
                    if sender in watched and tx_hash:
                        candidates.setdefault(tx_hash, set()).add(sender)
                        candidate_transactions[tx_hash] = transaction
        for tx_hash, candidate_wallets in candidates.items():
            transaction = candidate_transactions.get(tx_hash) or self.rpc(chain, "eth_getTransactionByHash", [tx_hash]) or {}
            sender = str(transaction.get("from") or "").lower()
            if sender not in candidate_wallets:
                continue
            receipt = self.rpc(chain, "eth_getTransactionReceipt", [tx_hash]) or {}
            event = classify_evm_buy(
                chain, sender, transaction, receipt,
                lambda token: self.evm_token_metadata(chain, token),
                native_price_usd=self.native_price_usd(chain),
                threshold_usd=float(watched[sender].get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
                # 名单内的地址允许「用非稳定报价代币付款」的兜底，名单外行为不变。
                allow_quote_payment=sender in QUOTE_TOKEN_PAYMENT_WALLETS,
            )
            if event:
                events.append(self.record_buy(event)["event"])
        self.store.set_cursor(key, latest)
        message = "只读新区块监控正常"
        if log_error is not None:
            message = "快速日志接口受限，已自动切换新区块扫描"
        if skipped:
            scanned = min(max_blocks, latest - start + 1) if log_error is None else min(
                evm_fallback_blocks(chain), latest - previous
            )
            message = f"{message}；已扫描最近 {scanned} 个区块，跳过 {skipped} 个旧区块"
        self.store.update_health(chain, "ok", message, str(latest))
        return events

    def _solana_signature_batch(self, wallet: dict[str, Any]) -> dict[str, Any]:
        """只做「取这个钱包的最新一段签名」，不落库、不判单。

        这一步是 Solana 一轮里的主要开销：20+ 个钱包各一次
        ``getSignaturesForAddress``，其中被机器人灌满的还要再放大到 1000 条再来一次。
        纯读、每个钱包各自一条游标，彼此没有依赖，所以可以并行（见 ``_poll_solana``）。
        """
        address = wallet["address"]
        key = f"solana:{address}:signature"
        previous = self.store.cursor(key)
        params: dict[str, Any] = {"limit": SOLANA_SIGNATURE_LIMIT_DEFAULT, "commitment": "confirmed"}
        if previous:
            params["until"] = previous
        signatures = self.rpc("solana", "getSignaturesForAddress", [address, params]) or []
        limit_used = SOLANA_SIGNATURE_LIMIT_DEFAULT
        # Hitting the cap means the window was truncated — on a wallet that
        # bots spam with failing transactions, that silently drops real buys.
        # Retry once with the ceiling so the window still reaches back to the
        # previous cursor.
        if previous and len(signatures) >= SOLANA_SIGNATURE_LIMIT_DEFAULT:
            widened = self.rpc("solana", "getSignaturesForAddress",
                               [address, {**params, "limit": SOLANA_SIGNATURE_LIMIT_CEILING}]) or []
            if len(widened) > len(signatures):
                signatures = widened
                limit_used = SOLANA_SIGNATURE_LIMIT_CEILING
        return {"wallet": wallet, "key": key, "previous": previous,
                "signatures": signatures, "limitUsed": limit_used}

    def _poll_solana(self, wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        failed_total = 0
        backlog_total = 0
        truncated_total = 0
        batches: list[dict[str, Any]] = []
        lookup_errors: list[str] = []

        def guarded(wallet: dict[str, Any]) -> dict[str, Any]:
            # 单个钱包被 RPC 限流（429）不该把整条链打空：它自己的游标不动，
            # 下一轮重试就是了。只有**所有**钱包都失败时才算这条链出错。
            try:
                return self._solana_signature_batch(wallet)
            except Exception as exc:
                return {"wallet": wallet, "error": exc}

        if len(wallets) > 1 and SOLANA_SIGNATURE_LOOKUP_WORKERS > 1:
            # ``map`` 保持钱包顺序，等价于原来的串行顺序。
            with ThreadPoolExecutor(
                max_workers=min(SOLANA_SIGNATURE_LOOKUP_WORKERS, len(wallets)),
                thread_name_prefix="solana-signatures",
            ) as pool:
                batches = list(pool.map(guarded, wallets))
        else:
            batches = [guarded(wallet) for wallet in wallets]
        if batches and all(batch.get("error") is not None for batch in batches):
            raise batches[0]["error"]
        for batch in batches:
            if batch.get("error") is not None:
                lookup_errors.append(str(batch["error"])[:160])
                continue
            wallet = batch["wallet"]
            address = wallet["address"]
            key = batch["key"]
            previous = batch["previous"]
            signatures = batch["signatures"]
            limit_used = batch["limitUsed"]
            if not previous:
                if signatures:
                    self.store.set_cursor(key, signatures[0].get("signature") or "")
                continue
            failed = sum(1 for row in signatures if row.get("err") is not None)
            failed_total += failed
            # Still at the cap even after widening = the flood outran the window
            # and the hole between the old cursor and the oldest row we can see is
            # unrecoverable.  Crawling it oldest-first would replay stale trades
            # while fresh buys go unnoticed for hours, so walk newest-first and
            # jump the cursor forward, reporting the loss instead of hiding it.
            truncated = len(signatures) >= limit_used
            if truncated:
                truncated_total += 1
            ordered = list(signatures) if truncated else list(reversed(signatures))
            # Oldest-first leaves a clean checkpoint: the cursor advances to the
            # newest signature we actually looked at and never jumps past work we
            # skipped.  Failed transactions cost no RPC call (they can never be a
            # buy) but the cursor may still move past them, which is what lets a
            # spam flood drain quickly.
            cursor = str(signatures[0].get("signature") or previous) if truncated else previous
            watched_total = 0
            backlog = 0
            for row in ordered:
                signature = str(row.get("signature") or "")
                if not signature:
                    continue
                if row.get("err") is not None:
                    if not truncated:
                        cursor = signature
                    continue
                if watched_total >= SOLANA_TX_BUDGET_PER_ROUND:
                    backlog += 1
                    continue
                payload = self.rpc("solana", "getTransaction", [signature, {
                    "encoding": "jsonParsed", "commitment": "confirmed",
                    # v1 covers v0 as well; asking for 0 makes every address-lookup-table
                    # transaction fail with -32015 and silently vanish from monitoring.
                    "maxSupportedTransactionVersion": 1,
                }]) or {}
                watched_total += 1
                if not truncated:
                    cursor = signature
                payload["signature"] = signature
                event = classify_solana_buy(
                    address, payload, self.solana_token_metadata,
                    native_price_usd=self.native_price_usd("solana"),
                    threshold_usd=float(wallet.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
                )
                if event:
                    events.append(self.record_buy(event)["event"])
            if cursor and cursor != previous:
                self.store.set_cursor(key, cursor)
            if backlog:
                backlog_total += backlog
        notes = []
        if lookup_errors:
            notes.append(f"{len(lookup_errors)} 个钱包本轮取签名失败（限流/超时），下轮重试")
        if failed_total:
            notes.append(f"跳过 {failed_total} 笔失败交易")
        if backlog_total:
            notes.append(f"还有 {backlog_total} 笔待回追")
        if truncated_total:
            notes.append(f"{truncated_total} 个钱包的签名窗口被灌满，已取最新一段")
        self.store.update_health(
            "solana", "ok",
            "只读签名监控正常" + ("（" + "，".join(notes) + "）" if notes else ""),
            str(len(wallets)),
        )
        return events

    def poll_once(self) -> list[dict[str, Any]]:
        """把所有链各跑一轮（并行）。后台调度走 ``_worker`` 的每链独立槽位，
        这个入口留给「立即刷新」和测试：它们要的是「一轮之内六条链都看过」。
        """
        if not self._intake_allowed():
            return []
        if not self.poll_lock.acquire(blocking=False):
            return []
        try:
            wallets = self.store.list_wallets(enabled_only=True)
            by_chain = {chain: [row for row in wallets if row["chain"] == chain] for chain in SUPPORTED_CHAINS}
            jobs = {chain: rows for chain, rows in by_chain.items() if rows}
            if not jobs:
                return []
            if len(jobs) == 1:
                chain, rows = next(iter(jobs.items()))
                return self._run_chain_once(chain, rows) or []
            events: list[dict[str, Any]] = []
            # 按 SUPPORTED_CHAINS 的顺序收集，保证和以前的输出顺序一致。
            with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="smart-money-round") as pool:
                futures = {
                    chain: pool.submit(self._run_chain_once, chain, rows)
                    for chain, rows in jobs.items()
                }
                for chain in SUPPORTED_CHAINS:
                    future = futures.get(chain)
                    if future is None:
                        continue
                    try:
                        events.extend(future.result() or [])
                    except Exception:
                        pass  # _poll_chain 已经把错误写进 health
            return events
        finally:
            self.poll_lock.release()

    def payload(self) -> dict[str, Any]:
        wallets = self.store.list_wallets()
        events = self.store.list_events()
        health = self.store.health()
        today = time.strftime("%Y-%m-%d", time.localtime())
        today_start = int(time.mktime(time.strptime(today, "%Y-%m-%d")) * 1000)
        return {
            "ok": True,
            "supportedChains": [
                {"id": chain, "label": CHAIN_META[chain]["label"], "vmType": CHAIN_META[chain]["vm"]}
                for chain in SUPPORTED_CHAINS
            ],
            "wallets": wallets,
            "events": events,
            "health": health,
            "summary": {
                "running": self.running,
                "wallets": len(wallets),
                "enabledWallets": sum(1 for row in wallets if row.get("enabled")),
                "events": len(events),
                "todayEligibleBuys": sum(
                    1 for row in events if row.get("popupEligible") and int(row.get("observedAt") or 0) >= today_start
                ),
                "alertThresholdUsd": DEFAULT_ALERT_THRESHOLD_USD,
                "readOnly": True,
            },
            "updatedAt": _now_ms(),
        }
