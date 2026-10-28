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
        ),
        # 四条链都留着：Robinhood 是主战场（175/200 笔），但 ETH / Base 也真在买。
        # 用户在面板里随时可以改阈值（改过之后本 seed 不会再覆盖，见
        # SmartMoneyStore.apply_seed_thresholds）。
        "alertThresholdUsd": 100.0,
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
        # 链上实测该地址单笔成交在数十美元量级（持仓 800~2300U），沿用默认
        # 10000U 阈值等于永不提醒，故按实际规模下调；面板里仍可随时改。
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
            "GMGN 统计单笔买入均值约 $344，故阈值按用户拍板定 500U（只弹有分量的买单）。"
        ),
        # 用户 2026-10-22 拍板「只 BSC」：ETH/Base 上他也在动，但那两边的买单不提醒。
        # 注意 BSC 走的是「按区块扫 from」的兜底路径（公共日志接口拒绝无 address 查询），
        # 所以这里必须是一个真的会自己发交易的地址 —— EIP-7702 委托之后 from 仍是它本人。
        "chains": ("bsc",),
        "alertThresholdUsd": 500.0,
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
            "提醒门槛沿用阿峰的拍板：单笔 ≥500U 才弹。"
        ),
        # 用户没限定链，但核验显示他只在 BSC / ETH / Base 活动：明确给三条，
        # 别让默认四链（含 robinhood）白占扫描窗口。
        "chains": ("bsc", "ethereum", "base"),
        "alertThresholdUsd": 500.0,
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
    "0xa7d4ffc4eca3c71af150ce302560a9d04a1d2b9f",
    "0x3472ccf6209925204b9cf352171ee150904fda7e",
    "0x01db37579e55ce13f4504019025e36047bdad845",
    "0x7e8fb0392542812476d9f2d0d71c01d1fa0776c5",
    "0xeb89055e16ae1c1e42ad6770a7344ff5c7b4f31d",
    "0x877af245c61289b24f0c619a4346cdbc68f1aaac",
    "0x7a2363a401b2340c7941dd2eeff0196a5078d2e6",
    "0xfe277aa25a4f65a182c0ec135854794eb7131e79",
    "0xddac928a240bdace3994c2cc0783d4e29a002127",
    "0xc2c6acd377458010713e733e1b21dd6f670d091c",
    "0x2ce9d43d1cba6ae31d7f07bfe0098dfa2d833373",
    "0xa83b73f5644cde337b61da79589f10ea15548811",
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
KOL_SYNC_WINDOW_SECONDS = 24 * 3600
KOL_SYNC_REALERT_SECONDS = 12 * 3600
#: 「同车增加」的有效期：从三人齐那一刻算起，这个窗口内再有人上车就继续提醒。
#: 过了窗口就不再骚扰 —— 一个币的同车故事讲 24 小时够了。
KOL_SYNC_JOIN_WINDOW_SECONDS = 24 * 3600

#: 「同车增加」看的人 = 用户的 20 个追踪钱包（含核心三人）。
#: 名字对得上的写人名，其余用短地址 —— 用户没给昵称，编一个反而会认错人。
KOL_SYNC_TRACK_LABELS: dict[str, str] = {
    "0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9": "CryptoCharming",
    "0xbf004bff64725914ee36d03b87d6965b0ced4903": "阿峰_Afeng",
    "0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407": "cowboy",
    "0xe28601398a5448d1147e6e8b0e0c6d686f0d216d": "NΞO",
    "0x6078ee8a93697c6d67863fcbff77141d9ab358b2": "Inq",
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
    """「同车增加」的观察面：用户那 20 个追踪钱包。

    比核心三人宽 —— 核心三人负责「判定同车成立」，其余追 17 个负责「还有谁上车」。
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
SUPPORTED_CHAINS = (*EVM_CHAINS, "solana")

# Blocks scanned per poll, per chain.  Chains do not agree on block time:
# Robinhood Chain mines ~10 blocks/second, so the generic 120-block window only
# covers ~12 seconds — a fraction of one poll cycle — and the monitor silently
# drops most buys (health showed "已扫描最近 120 个区块，跳过 198 个旧区块").
# The Robinhood RPC stays cheap on a wide span as long as the ERC-20 transfer
# topic filter is present (30000 blocks ≈ 0.8s, and its per-query span limit is
# exactly 30000), so fast chains get a window that comfortably outlasts a cycle.
EVM_MAX_BLOCKS_PER_POLL_DEFAULT = 120
EVM_MAX_BLOCKS_PER_POLL_BY_CHAIN = {"robinhood": 3000}
EVM_MAX_BLOCKS_PER_POLL_CEILING = 30000

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
    "solana": {
        "label": "Solana", "vm": "solana", "native": "SOL", "dex": "solana",
        "explorer": "https://solscan.io/tx/{}",
        "rpcEnv": "SMART_MONEY_SOLANA_RPC_URLS",
        "rpcs": ("https://api.mainnet-beta.solana.com",),
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
WRAPPED_NATIVE_SYMBOLS = {"WETH", "WBNB", "WSOL"}
SOLANA_STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
}
EXCLUDED_OPERATION_WORDS = {"approve", "approval", "bridge", "liquidity", "add_liquidity", "remove_liquidity"}


def _now_ms() -> int:
    return int(time.time() * 1000)


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
    if normalized_chain in EVM_CHAINS:
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

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY observed_at DESC, id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [_row_dict(row) for row in rows]

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
    if not targets or not payment_options:
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
        "details": {"classification": "net-token-flow", "readOnly": True},
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
        symbol = str(token_meta.get("symbol") or SOLANA_STABLE_MINTS.get(mint) or "").upper()
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
    return {
        "chain": "solana", "walletAddress": wallet, "transactionHash": tx_hash,
        "tokenAddress": target_mint, "symbol": str(target_meta.get("symbol") or "UNKNOWN")[:40],
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
        self.poll_interval_seconds = max(3.0, float(
            poll_interval_seconds or os.getenv("SMART_MONEY_POLL_INTERVAL_SECONDS", "6") or 6
        ))
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.poll_lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.running = False
        self.alert_callback: Callable[[dict[str, Any]], Any] | None = None
        # 「KOL 共振」单独提醒的回调（用户 2026-10-27）。只在三个人**都**买了同一个
        # 代币时触发一次，见 :meth:`_maybe_emit_kol_sync`。
        self.sync_alert_callback: Callable[[dict[str, Any]], Any] | None = None
        self.kol_sync_wallets: dict[str, str] = active_kol_sync_wallets()
        # 「同车增加」看的全量追踪钱包（含核心三人）。测试里可以替换。
        self.kol_sync_track_wallets: dict[str, str] = active_kol_sync_track_wallets()
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

    def _maybe_emit_kol_sync(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """刚记录的一笔买入，能不能触发「同车」提醒。两种模式：

        * ``trio``：核心三人（CryptoCharming / 阿峰 / cowboy）凑齐 → 弹一次；
        * ``join``：同车已成立后，**别的追踪钱包**又上了同一辆车 → 每次新上车都提醒一次
          （用户 2026-10-27：「在这个基础上，如果在这个同车标的上还有其他的 KOL 再进来，
          继续做同车增加的提醒」）。已经在车上的人不重复播报，靠
          ``_sync_episodes[token_key]["announced"]`` 记账。

        判定全部基于**事件表里的真实买入记录**，不看谁弹过窗 —— 金额可能都没过自己的
        门槛，但「都上车了」这件事本身才是信号。
        """
        callback = self.sync_alert_callback
        core = self.kol_sync_wallets
        track = self.kol_sync_track_wallets or core
        if callback is None or not core:
            return None
        chain = normalize_chain(event.get("chain"))
        token = str(event.get("tokenAddress") or "").strip()
        if not chain or not token:
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
        episode = self._sync_episodes.get(token_key)
        base_context = {
            "chain": chain,
            "tokenAddress": token,
            "symbol": str(event.get("symbol") or "").strip(),
            "windowHours": window_ms // 3600000,
            "minBuyers": minimum,
            "observedAt": now_ms,
        }

        # ---- 1) 三人齐：判定「同车成立」，并把当前所有在车上的人记为已播报 ----
        if core_count >= minimum:
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
        normalized = {
            **event,
            "walletNickname": event.get("walletNickname") or wallet.get("nickname") or wallet.get("sourceName") or "聪明钱",
            "popupEligible": float(event.get("paymentUsd") or 0) >= threshold,
            # The popup card prints the line the event cleared.  The threshold is
            # PER WALLET, so the card cannot hardcode it — carry it on the event.
            "details": {**details, "alertThresholdUsd": threshold},
        }
        result = self.store.record_event(normalized)
        stored = result["event"]
        if result["created"] and stored.get("popupEligible") and self.alert_callback:
            self.alert_callback(stored)
            self.store.mark_alerted(stored["eventKey"])
            stored["alertedAt"] = _now_ms()
        # 「三个人同时上车」是另一件事：金额可能都没过门槛，但要单独提醒一次。
        if result["created"]:
            self._maybe_emit_kol_sync(stored)
        return {"created": result["created"], "event": stored}

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
        while not self.stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self.stop_event.wait(self.poll_interval_seconds)

    def rpc_urls(self, chain: str) -> list[str]:
        meta = CHAIN_META[chain]
        configured = [value.strip() for value in str(os.getenv(meta["rpcEnv"], "") or "").split(",") if value.strip()]
        return list(dict.fromkeys([*configured, *meta["rpcs"]]))

    def rpc(self, chain: str, method: str, params: list[Any]) -> Any:
        errors: list[str] = []
        for url in self.rpc_urls(chain):
            try:
                response = self.session.post(
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

    def _token_price(self, chain: str, token: str) -> float:
        try:
            response = self.session.get(
                f"https://api.dexscreener.com/tokens/v1/{CHAIN_META[chain]['dex']}/{token}",
                headers={"Accept": "application/json"}, timeout=(4, 8),
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                return 0.0
            best = max((row for row in rows if isinstance(row, dict)), key=lambda row: float((row.get("liquidity") or {}).get("usd") or 0), default={})
            return float(best.get("priceUsd") or 0)
        except Exception:
            return 0.0

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
        result = {
            "symbol": symbol[:40] or "UNKNOWN", "decimals": decimals if 0 <= decimals <= 36 else 18,
            "priceUsd": price, "stable": symbol.upper() in STABLE_SYMBOLS and (price == 0 or 0.8 <= price <= 1.2),
        }
        self._metadata_cache[key] = (time.time(), result)
        return dict(result)

    def solana_token_metadata(self, mint: str) -> dict[str, Any]:
        key = ("solana", mint)
        cached = self._metadata_cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return dict(cached[1])
        price = self._token_price("solana", mint)
        result = {
            "symbol": SOLANA_STABLE_MINTS.get(mint, "UNKNOWN"), "priceUsd": price,
            "stable": mint in SOLANA_STABLE_MINTS,
        }
        self._metadata_cache[key] = (time.time(), result)
        return dict(result)

    def native_price_usd(self, chain: str) -> float:
        symbol = CHAIN_META[chain]["native"]
        cached = self._native_price_cache.get(symbol)
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        try:
            response = self.session.get(
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

    def _poll_solana(self, wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for wallet in wallets:
            address = wallet["address"]
            key = f"solana:{address}:signature"
            previous = self.store.cursor(key)
            params: dict[str, Any] = {"limit": 20, "commitment": "confirmed"}
            if previous:
                params["until"] = previous
            signatures = self.rpc("solana", "getSignaturesForAddress", [address, params]) or []
            if not previous:
                if signatures:
                    self.store.set_cursor(key, signatures[0].get("signature") or "")
                continue
            for row in reversed(signatures):
                signature = str(row.get("signature") or "")
                if not signature or row.get("err") is not None:
                    continue
                payload = self.rpc("solana", "getTransaction", [signature, {
                    "encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0,
                }]) or {}
                payload["signature"] = signature
                event = classify_solana_buy(
                    address, payload, self.solana_token_metadata,
                    native_price_usd=self.native_price_usd("solana"),
                    threshold_usd=float(wallet.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
                )
                if event:
                    events.append(self.record_buy(event)["event"])
            if signatures:
                self.store.set_cursor(key, signatures[0].get("signature") or previous)
        self.store.update_health("solana", "ok", "只读签名监控正常", str(len(wallets)))
        return events

    def poll_once(self) -> list[dict[str, Any]]:
        if not self._intake_allowed():
            return []
        if not self.poll_lock.acquire(blocking=False):
            return []
        try:
            wallets = self.store.list_wallets(enabled_only=True)
            by_chain = {chain: [row for row in wallets if row["chain"] == chain] for chain in SUPPORTED_CHAINS}
            events: list[dict[str, Any]] = []
            for chain, rows in by_chain.items():
                if not rows:
                    continue
                try:
                    if chain == "solana":
                        events.extend(self._poll_solana(rows))
                    else:
                        events.extend(self._poll_evm(chain, rows))
                except Exception as exc:
                    self.store.update_health(chain, "error", str(exc)[:300], self.store.cursor(
                        f"solana:{rows[0]['address']}:signature" if chain == "solana" else f"evm:{chain}:block"
                    ))
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
