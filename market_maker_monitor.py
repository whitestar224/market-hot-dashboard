"""做市商监控：跟这 8 家做市商的链上地址，看它们**正在操作哪些标的**。

数据源只有一个：Blockscout v2 的 ``/api/v2/addresses/{addr}/token-transfers``。
它连美元单价都自带（``token.exchange_rate``），所以不需要再外挂 DexScreener，
也不吃 BscScan / Etherscan 那套 key 配额 —— 整个模块零密钥。

**地址从哪来**：一律是可审计的公开来源，按证据等级标注（``MM_EVIDENCE``）：

* ``tag``     —— 链上浏览器自己的公共标签（Blockscout / OLI），最硬
* ``related`` —— 链上浏览器的「关联地址」标签（``related_level_1``），推断而来
* ``report``  —— 第三方链上研报 / 媒体标注，次硬（会写明来源）
* ``manual``  —— 用户自己加的

**被排除的标签**（不是做市行为，收进来就是污染）：
``Wintermute Exploiter``（当年被盗的黑客地址）、``Wintermute Negotiator``、
``"Wintermute89" on Polymarket``、以及一堆名字里带厂家的**同名代币合约**
（``DWF Labs`` / ``B2C2`` / ``DRWHO`` / ``Drwn`` …）。判据是看标签 slug 与地址类型，
不看名字里有没有那家公司的字 —— 这是唯一不会误收的办法。

**两类弹窗**（用户拍板：新标的 + 大额调仓，其余只进列表）：

1. 新标的进场 —— 某家在它从没碰过的代币上第一次动钱
2. 大额调仓   —— 单笔折合美元 ≥ ``MM_LARGE_TRANSFER_USD``（默认 100 万）

**首次建基不弹窗**：新地址第一次拉到的 50 条是历史流水，只会写进库供列表展示，
带 ``baseline=1`` 标记，一律不弹。而且「新标的」还要等这家完成**首轮全量扫街**
（``sweep_complete``）才算数 —— 否则分几轮才扫完的地址会把几百年的老仓全报成新标的。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

import requests


# --------------------------------------------------------------------------- #
# 链
# --------------------------------------------------------------------------- #
#: 只保留**有可用 Blockscout** 的链。Base 的标签库几乎为空、BSC 根本没有公开实例
#: （与「Dev 发币」那边同一个限制），硬加进来只会得到一片空白。
CHAIN_META = {
    "ethereum": {
        "label": "Ethereum",
        "blockscout": "https://eth.blockscout.com",
        "explorerTx": "https://etherscan.io/tx/{}",
        "explorerToken": "https://etherscan.io/token/{}",
        "gmgn": "eth",
    },
    "robinhood": {
        "label": "Robinhood",
        "blockscout": "https://robinhoodchain.blockscout.com",
        "explorerTx": "https://robinhoodchain.blockscout.com/tx/{}",
        "explorerToken": "https://robinhoodchain.blockscout.com/token/{}",
        "gmgn": "robinhood",
    },
}
CHAINS = tuple(CHAIN_META)
CHAIN_ALIASES = {
    "ethereum": "ethereum", "eth": "ethereum", "以太坊": "ethereum", "1": "ethereum",
    "robinhood": "robinhood", "hood": "robinhood", "4663": "robinhood",
}

#: 展示用的链名与 GMGN 路径别名。**范围比 CHAINS 宽**：做市商控盘那一路的标的
#: 多半在 BSC / Base / Solana 上，这里只用于给它们拼链接与中文标签，不代表本模块会
#: 去那几条链上轮询做市商地址。
CHAIN_LABELS = {
    "ethereum": "Ethereum", "eth": "Ethereum", "1": "Ethereum",
    "bsc": "BSC", "bnb": "BSC", "56": "BSC",
    "base": "Base", "8453": "Base",
    "solana": "Solana", "sol": "Solana",
    "robinhood": "Robinhood", "4663": "Robinhood",
}
GMGN_SLUGS = {
    "ethereum": "eth", "eth": "eth",
    "bsc": "bsc", "bnb": "bsc",
    "base": "base",
    "solana": "sol", "sol": "sol",
    "robinhood": "robinhood",
}

EVIDENCE_LABELS = {
    "tag": "链上标签",
    "related": "关联标签",
    "report": "第三方研报",
    "manual": "手动添加",
}

# --------------------------------------------------------------------------- #
# 八家做市商（顺序照用户给的截图）
# --------------------------------------------------------------------------- #
MM_FIRMS: tuple[tuple[str, str, str, str], ...] = (
    (
        "wintermute", "Wintermute", "WM",
        "业内覆盖最广的顶级做市商之一，专注高频算法交易，为 250+ 种代币做市，"
        "是 Binance 超 50% 流动性的来源。在 OP、ARB 等主流 L2 代币中深度参与，"
        "被称为「四大天王参团率 100%」。",
    ),
    (
        "jump", "Jump Trading", "JUMP",
        "源自传统金融的全球顶级量化交易公司，成立于 1999 年，将高频交易和算法策略"
        "深度应用于加密市场，资金规模庞大。",
    ),
    (
        "gsr", "GSR Markets", "GSR",
        "老牌加密做市商，近年来积极向合规化转型，收购了 FINRA 经纪商并推出 ETF 相关"
        "产品，获得了渣打银行旗下 SC Ventures 的战略投资。",
    ),
    (
        "dwf", "DWF Labs", "DWF",
        "风格较为激进和活跃的做市商，常以「上新支持 + 流动性包」的形式深度参与项目，"
        "在透明度排名中位列第一梯队。",
    ),
    (
        "amber", "Amber Group", "AMBER",
        "总部位于亚洲的知名加密金融服务商，在做市、资管和借贷等领域均有布局，"
        "透明度表现也位居前列。",
    ),
    (
        "b2c2", "B2C2", "B2C2",
        "加密市场主要的场外交易（OTC）做市商之一，为机构客户提供大额交易的流动性，"
        "也是与渣打银行有深度合作的机构。",
    ),
    (
        "cumberland", "Cumberland (DRW)", "DRW",
        "隶属于老牌交易公司 DRW Holdings，是加密市场中重要的流动性提供商，"
        "资金规模位居前列。",
    ),
    (
        "flow", "Flow Traders", "FLOW",
        "欧洲知名的交易所交易产品（ETP）做市商，在加密 ETF 和传统金融产品与加密市场的"
        "连接中扮演重要角色。",
    ),
)
MM_FIRM_LABELS = {firm: label for firm, label, _short, _brief in MM_FIRMS}
MM_FIRM_SHORTS = {firm: short for firm, _label, short, _brief in MM_FIRMS}

#: ``(chain, firm, address, tag, evidence, sourceUrl)``。
#:
#: 2026-10-20 用 Blockscout 公共标签接口逐家核实（``/api/v2/search?q=<厂家名>``，
#: 只收 ``type == "metadata_tag"``，并把标签原文留在 ``tag`` 里以便随时复核）。
#: ``sourceUrl`` 是标签自带的 tooltip（多为公司官网），能一眼看出这个标签是谁标的。
MM_SEED_ADDRESSES: tuple[tuple[str, str, str, str, str, str], ...] = (
    # ---- Wintermute ----
    ("ethereum", "wintermute", "0xDBF5E9c5206d0dB70a90108bf936DA60221dC080", "Wintermute: 0xdbf...080", "tag", "https://wintermute.com/"),
    ("ethereum", "wintermute", "0x0000006daea1723962647b7e189d311d757Fb793", "Wintermute 1", "tag", ""),
    ("ethereum", "wintermute", "0x000002Cba8DfB0a86A47a415592835E17fac080a", "Wintermute 2", "tag", "https://www.wintermute.com/"),
    ("ethereum", "wintermute", "0x5cddEbd8a7b3BC60156550187BF5465b1c3290f5", "Wintermute 3", "tag", "https://wintermute.com/"),
    ("ethereum", "wintermute", "0xf8191D98ae98d2f7aBDFB63A9b0b812b93C873AA", "Wintermute 4", "tag", "https://www.wintermute.com/"),
    ("ethereum", "wintermute", "0xCe84449A8Ebec019AC110a4b6662E55d0fD9f228", "Wintermute 5", "tag", "https://www.wintermute.com/"),
    ("ethereum", "wintermute", "0xB6b7b5F4193D00945317e3E601891ed31eFd3843", "Wintermute: Deposit Address", "tag", ""),
    ("ethereum", "wintermute", "0x4f3a120E72C76c22ae802D129F599BFDbc31cb81", "Wintermute: Multisig", "tag", "https://www.wintermute.com/"),
    ("ethereum", "wintermute", "0x8e914fE8984e232Cb5eA448C8FD83A6F9a5c6C9F", "Wintermute trading:6c9f(related_level_1)", "related", ""),
    ("ethereum", "wintermute", "0x18c5fb0518C81510E80d4D350bc3a8778C548d6a", "Wintermute trading:8d6a(related_level_1)", "related", ""),
    ("ethereum", "wintermute", "0x0E5e696d19029CeB1e199fEAd470d71901De9407", "Wintermute trading:9407(related_level_1)", "related", ""),
    ("ethereum", "wintermute", "0x06920C9fC643De77B99cB7670A944AD31eaAA260", "Wintermute trading:a260(related_level_1)", "related", ""),
    ("ethereum", "wintermute", "0xb8a56eFaB668bF7B84752b9285940B78A58bA5C4", "Wintermute trading:a5c4(related_level_1)", "related", ""),
    ("ethereum", "wintermute", "0xa9dcd7A6E00CfF04A91b7509E3a2CdD144dDAD77", "Wintermute trading:ad77(related_level_1)", "related", ""),
    ("ethereum", "wintermute", "0xDbefB887662Ca19CD485381F3386fe5F8537B910", "Wintermute trading:b910(related_level_1)", "related", ""),
    # Robinhood 链上这家是同名合约（标签同时挂着 "MEV Bot"，流水是真实做市换手）
    ("robinhood", "wintermute", "0x51C72848c68a965f66FA7a88855F9f7784502a7F", "Wintermute 1", "tag", "https://www.wintermute.com/"),

    # ---- Jump Trading ----
    ("ethereum", "jump", "0x9507c04B10486547584C37bCBd931B2a4FeE9A41", "Jump Trading 2", "tag", "https://www.jumptrading.com/"),
    ("ethereum", "jump", "0xf584F8728B874a6a5c7A8d4d387C9aae9172D621", "Jump Trading", "tag", "https://www.jumptrading.com/"),
    ("ethereum", "jump", "0xF05e2A70346560d3228c7002194Bb7C5dC8Fe100", "Jump Trading: Binance Deposit", "tag", "https://www.jumptrading.com/"),
    ("ethereum", "jump", "0x2eFB50e952580f4ff32D8d2122853432bbF2E204", "Jump Trading: Robinhood Processing", "tag", ""),
    ("ethereum", "jump", "0x73BCEb1Cd57C711feaC4224D062b0F6ff338501e", "Jump trading:501e", "tag", ""),
    ("ethereum", "jump", "0x4f1a5a5d258522254933a6DA9648c57Fe230D17e", "Jump trading:d17e", "tag", ""),
    ("ethereum", "jump", "0x6140B40c6dde0381531e2F6dbC4A080E3b7f321f", "Jump Trading:321f(related_level_1)", "related", ""),
    ("ethereum", "jump", "0xcc261Ab4bE137EACf57C19ed97c186b4d88004Ca", "Jump trading:04ca(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x565Ee423315547F94c04352cB4AEc3aC6d38055A", "Jump trading:055a(related_level_1)", "related", ""),
    ("ethereum", "jump", "0xAa04F1162899554fB66d06d1d60C68d46dcE4D6A", "Jump trading:4d6a(related_level_1)", "related", ""),
    ("ethereum", "jump", "0xabB68108A5dB599Ba635c227e4792d53D7484e3E", "Jump trading:4e3e(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x62990647e11f3d71f04778e756c22Da94A315f35", "Jump trading:5f35(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x3da68B4C6b04be2b656fdE9dA1f2538DB05766b9", "Jump trading:66b9(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x1C696Fe18992AF4859dEd5a86DEE67972D6A6da0", "Jump trading:6da0(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x40B228C95Dd8563Be6e3dDe8f2C92CFD36Bd6e7d", "Jump trading:6e7d(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x0F72F828209baB0cBeCC5ec50A9C25C280627922", "Jump trading:7922(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x9ee3547bb73F46FcD5c730c8Da67533a5d1B938F", "Jump trading:938f(related_level_1)", "related", ""),
    ("ethereum", "jump", "0xBB8b82734448050bC47439b158Ab3B9FB6249bFC", "Jump trading:9bfc(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x4A2DA6811E11b1ce15C84e77EE36124Af020A0AA", "Jump trading:a0aa(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x8Bd5D1bB424A171050BFa7E10E88167e943dB2E7", "Jump trading:b2e7(related_level_1)", "related", ""),
    ("ethereum", "jump", "0xf323db7fC09Ad3bf4eb8F37292efABB76949d171", "Jump trading:d171(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x51b39Dc3288Ed1d776fF46b6a01ADe06fe50eAB5", "Jump trading:eab5(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x70BB5Ad3AAA4cEc6E14b458a4a5c4413Ec2FED4e", "Jump trading:ed4e(related_level_1)", "related", ""),
    ("ethereum", "jump", "0x6593Df99d6016a175E9c2c1a0259b09961d7fbCF", "Jump trading:fbcf(related_level_1)", "related", ""),
    ("robinhood", "jump", "0x9507c04B10486547584C37bCBd931B2a4FeE9A41", "Jump Trading 2", "tag", "https://www.jumptrading.com/"),

    # ---- GSR Markets（链上只有 1 个标签，已被 arbitragescanner 的链上报告交叉验证）----
    ("ethereum", "gsr", "0xD8D6fFE342210057BF4DCc31DA28D006f253cEF0", "GSR: 0xD8D...EF0", "tag", ""),

    # ---- DWF Labs ----
    ("ethereum", "dwf", "0xc0E59d7B743c2757C8b39B791B5EbaF2Bf6e725D", "DWF Labs", "tag", "https://www.dwf-labs.com/"),
    ("ethereum", "dwf", "0xD4B69e8D62C880E9DD55d419d5E07435C3538342", "DWF Labs: 0xD4B...342", "tag", ""),
    ("ethereum", "dwf", "0xddAcAd3B1edee8E2F5b2e84f658202534fcb0374", "DWF Labs: 0xddA...374", "tag", ""),
    ("ethereum", "dwf", "0x53c902A9EF069F3b85e5e71f918C4D582F3063Fa", "DWFLabs: 0x53c...3Fa", "tag", ""),
    ("ethereum", "dwf", "0x7DE1D1Dfd4631734571337414caa9902F78269e6", "DWFLabs", "tag", ""),

    # ---- Amber Group ----
    ("ethereum", "amber", "0xE11970f2F3dE9d637Fb786f2d869F8FeA44195AC", "Amber Group", "tag", "https://www.ambergroup.io/"),
    ("ethereum", "amber", "0xA3d64C1d38dc221A41FF8665Ac94d90A2aa8d121", "Amber Group", "tag", ""),
    ("ethereum", "amber", "0x5b990C664aE7E759763ACfEC76E11c289c53Be77", "Amber Group: 0x5b9...e77", "tag", ""),
    ("ethereum", "amber", "0x2081eA54366FFCb145Cc1feE30F36aA33A72E778", "Amber Group: WBTC Merchant Deposit Address", "tag", ""),
    ("ethereum", "amber", "0x585f4fBe2d2A889c286fa71FB81D01f30773F4B1", "Amber Group_0x585f", "tag", ""),
    ("ethereum", "amber", "0xa0f75491720835b36edC92D06DDc468D201e9b73", "Amber Group_0xa0f7", "tag", ""),
    ("ethereum", "amber", "0x05ae4Fb228AfAE3B70e688799eE069dd979c4913", "Amber Group_0x05ae", "tag", ""),
    ("ethereum", "amber", "0x142822D4C96AaC6D721C55B6Ef254F46EA5bd207", "Amber Group_0x1428", "tag", ""),
    ("ethereum", "amber", "0x20cdbED6747cE91C2CB3FDC62ead2Db45d302372", "Amber Group_0x20cd", "tag", ""),
    ("ethereum", "amber", "0x253622Ee9EebeE710987867a24b6AcEF7606b292", "Amber Group_0x2536", "tag", ""),
    ("ethereum", "amber", "0x2B5FFF897B27Fb48f8c3e1F5dD8Eeda092d5c640", "Amber Group_0x2b5f", "tag", ""),
    ("ethereum", "amber", "0x3E2401ffcF74070194889e1F16afE3E3Bc9F3e1e", "Amber Group_0x3e24", "tag", ""),
    ("ethereum", "amber", "0x457e814CD580D0fC5477264c152aEF55FF42f093", "Amber Group_0x457e", "tag", ""),
    ("ethereum", "amber", "0x5ee062af5eAEe40c764D870AD756e104148B6434", "Amber Group_0x5ee0", "tag", ""),
    ("ethereum", "amber", "0x60e821Fe0fD3b35BAa343d5cE4C996958a65AD4F", "Amber Group_0x60e8", "tag", ""),
    ("ethereum", "amber", "0x69cC4654C9dFcB6dE0aDd8eA06E2772eE00D5760", "Amber Group_0x69cc", "tag", ""),
    ("ethereum", "amber", "0x97d2Afc28b86a068310cF8D4Df2b2f2b8Baa53A8", "Amber Group_0x97d2", "tag", ""),
    ("ethereum", "amber", "0xA0d430B6886ba1483dB8E729764378Fd2BA78f8C", "Amber Group_0xa0d4", "tag", ""),
    ("ethereum", "amber", "0xB84374799991B884338AA1F01bedB05a396fA6f8", "Amber Group_0xb843", "tag", ""),
    ("ethereum", "amber", "0xd7Ab7EB91241AFaB67D506F3132ed964FF096216", "Amber Group_0xd7ab", "tag", ""),
    ("ethereum", "amber", "0xdEB0A73361C4Bb49aD740934334F4A1D506Fa745", "Amber Group_0xdeb0", "tag", ""),
    ("ethereum", "amber", "0xeA1228A4fC0d9ce0E6797DFce9131485ca350B45", "Amber Group_0xea12", "tag", ""),
    ("ethereum", "amber", "0xF33dAb89ac69430D2082806956D499A3691b331b", "Amber Group_0xf33d", "tag", ""),
    ("ethereum", "amber", "0xfCA1d423Ed8B543A2cd3e7B55F13E2335e39B182", "Amber Group_0xfca1", "tag", ""),
    ("ethereum", "amber", "0xfE736c30D2cb0c25a37bdE1D112CBd89eBbC404E", "Amber Group_0xfe73", "tag", ""),

    # ---- B2C2 ----
    ("ethereum", "b2c2", "0xC333E80eF2deC2805F239E3f1e810612D294F771", "B2C2 Group 1", "tag", "https://www.b2c2.com/"),

    # ---- Cumberland (DRW) ----
    ("ethereum", "cumberland", "0x87b49A99CbCe4A9030e67919B776AA97d538aDda", "Cumberland: 0x87b...Dda", "tag", ""),
    ("ethereum", "cumberland", "0xad6eaa735D9dF3D7696fd03984379dAE02eD8862", "Cumberland: 0xad6...862", "tag", ""),
    ("ethereum", "cumberland", "0x7Ce672223ca12c990f179034645bdf8348472442", "Robinhood Cumberland Deposit 1", "tag", ""),

    # ---- Flow Traders ----
    # 链上公共标签库里一个都没有（搜 "Flow Traders" 命中 0 条 metadata_tag），
    # 这条来自 arbitragescanner 的链上资金流报告，证据等级如实降一档。
    ("ethereum", "flow", "0xAeA6291CB48d9675B120F74E2F0b0De01dB47a00", "Flow Traders（研报标注）", "report", "https://arbitragescanner.io/blog/how-funds-and-market-makers-profited-from-the-market-downturn"),
)

DEFAULT_POLL_SECONDS = 300.0
#: 一轮最多轮询多少个地址。8 家一共 80+ 个地址，一次全扫会把公共实例惹毛；
#: 按 ``last_polled_at`` 升序轮转，几轮之内自然铺满全部地址。
DEFAULT_ADDRESSES_PER_CYCLE = 30
#: 只有新地址的第一次拉取才多翻一页，之后每轮只取最近 50 条。
FIRST_POLL_PAGES = 2
STEADY_POLL_PAGES = 1
#: 单笔折合多少美元算「大额调仓」。
MM_LARGE_TRANSFER_USD = 1_000_000.0
#: 三方共振（大额异动 × 涨幅榜 × AIcoin 热门榜）同一标的的静默窗口。
#: 共振是**持续状态**而不是一次性事件，6 小时内重复弹没有新信息（用户 2026-10-21 定）。
RESONANCE_COOLDOWN_SECONDS = 6 * 3600
#: 「正在操作」的观察窗（小时）：窗口内还有流水的标的才算在读。
ACTIVE_WINDOW_HOURS = 48
DEFAULT_RESULTS_LIMIT = 240
#: 页面快照的缓存寿命。轮询一条流水一次事务，GET 直接读库会被饿到十几秒，
#: 而做市商的仓位是分钟级变化 —— 30 秒的旧快照对使用者没有区别。
PAYLOAD_CACHE_SECONDS = 30.0
HTTP_TIMEOUT = (6, 25)
ALERTS_SETTING_KEY = "alertsEnabled"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
#: 这些代币是资金本身，不是「操作的标的」：它们只进流水，不进标的榜。
CASH_TOKENS = frozenset({
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG (Robinhood)
})

_SESSION = None
_SESSION_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def runtime_dir() -> Path:
    configured = str(os.getenv("XINGYUN_RUNTIME_DIR", "") or "").strip()
    root = Path(__file__).resolve().parent
    return Path(configured).expanduser().resolve() if configured else root / ".runtime-cache"


def default_db_path() -> Path:
    return runtime_dir() / "market_maker_monitor.sqlite"


def normalize_chain(value: Any) -> str:
    key = " ".join(str(value or "").split()).casefold().replace("_", " ").replace("-", " ")
    return CHAIN_ALIASES.get(key.strip(), "")


def chain_label(value: Any) -> str:
    """展示用链名。**优先认本模块轮询的两条链**，再回落到更宽的展示表——
    做市商控盘那一节的标的多半在 BSC / Base / Solana，也得有中文名。"""
    chain = normalize_chain(value)
    if chain:
        return str((CHAIN_META.get(chain) or {}).get("label") or "")
    return CHAIN_LABELS.get(str(value or "").strip().casefold(), "")


def chain_options() -> list[dict[str, str]]:
    return [
        {"id": chain, "label": CHAIN_META[chain]["label"], "available": True, "note": ""}
        for chain in CHAINS
    ]


def firm_label(firm: Any) -> str:
    return MM_FIRM_LABELS.get(str(firm or ""), str(firm or ""))


def clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def symbol_key(value: Any) -> str:
    """把 ``BTCUSDT`` / ``btc`` / ``BTC/USDT`` 压成同一个 ``BTC``。

    涨幅榜用的是交易所合约代码（带 USDT 后缀），AIcoin 热门榜用的是裸币名 ——
    不归一就永远交不上集。``USDC`` 这种本身就是币名的不能剥（长度必须大于后缀）。
    """
    text = "".join(ch for ch in str(value or "").upper() if ch.isdigit() or ch.isalpha())[:24]
    for suffix in ("USDT", "USDC", "PERP", "USD"):
        if len(text) > len(suffix) and text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def board_symbol_index(
    payload: Any,
    source_ids: Any,
    *,
    origin: str = "",
) -> dict[str, dict[str, Any]]:
    """榜单 payload → ``{symbol: {rank, changePct, sourceId, chain, contract}}``。

    同一只币在多个来源里出现时取**排名最靠前**的那条（不同交易所的榜单各自从 1 排），
    这样「涨幅榜第 3」才说得出口。``rank`` 优先用行里自带的，没有就按行序。
    """
    wanted = {str(item) for item in source_ids or ()}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list) or not wanted:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "")
        if source_id not in wanted:
            continue
        rows = source.get("rows") if isinstance(source.get("rows"), list) else []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            key = symbol_key(row.get("symbol") or row.get("asset") or row.get("name"))
            if not key:
                continue
            rank = int(to_float(row.get("rank"), index) or index)
            current = out.get(key)
            if current is not None and int(current["rank"]) <= rank:
                continue
            out[key] = {
                "rank": rank,
                "changePct": to_float(row.get("change")),
                "sourceId": source_id,
                "origin": origin,
                "chain": clean_text(row.get("chain") or row.get("chainLabel"), 24),
                "contract": clean_text(row.get("contractAddress"), 96),
            }
    return out


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def truthy(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if not text:
        return default
    return text not in {"0", "false", "no", "off"}


def is_evm_address(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 42 and text[:2].lower() == "0x" and all(
        ch in "0123456789abcdefABCDEF" for ch in text[2:]
    )


def money_text(value: Any) -> str:
    """美元速记：$1.20B / $8.50M / $120K。"""
    amount = to_float(value)
    if amount <= 0:
        return ""
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:,.0f}"


def amount_text(value: Any) -> str:
    """代币数量速记：1.20B / 8.50M / 12.3K / 0.0042。"""
    amount = abs(to_float(value))
    if amount <= 0:
        return "0"
    if amount >= 1_000_000_000:
        return f"{amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.2f}K"
    if amount >= 1:
        return f"{amount:,.2f}"
    return f"{amount:.6f}".rstrip("0").rstrip(".")


def explorer_tx_url(chain: str, tx_hash: Any) -> str:
    template = str((CHAIN_META.get(chain) or {}).get("explorerTx") or "")
    return template.format(tx_hash) if template and tx_hash else ""


def explorer_token_url(chain: str, token: Any) -> str:
    template = str((CHAIN_META.get(chain) or {}).get("explorerToken") or "")
    return template.format(token) if template and token else ""


def gmgn_slug(value: Any) -> str:
    """任意链代号 → GMGN 路径 slug。折算不出来返回空串（**绝不猜**）。

    GMGN 对任何路径都回 200 的 SPA 壳，slug 写错了页面照样「加载成功」但内容空白，
    所以这里宁可返回空串让上层留空，也不做模糊匹配。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    chain = normalize_chain(raw)
    key = (chain or raw).strip().casefold()
    return GMGN_SLUGS.get(key, "")


def gmgn_token_url(chain: str, token: Any) -> str:
    """GMGN 站内交易页（跟榜单页 / 热搜弹窗同口径）。"""
    slug = gmgn_slug(chain)
    if not slug or not token:
        return ""
    return f"https://gmgn.ai/{slug}/token/{token}"


def evidence_label(value: Any) -> str:
    return EVIDENCE_LABELS.get(str(value or ""), str(value or ""))


def _iso_to_ms(value: Any) -> int:
    """Blockscout 的时间戳是 ``2026-10-21T00:08:58.000000Z``。"""
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def price_text(value: Any) -> str:
    """单价速记：1200 → $1,200.00 / 0.42 → $0.42 / 0.00001234 → $0.00001234。"""
    amount = to_float(value)
    if amount <= 0:
        return ""
    if amount >= 1000:
        return f"${amount:,.2f}"
    if amount >= 1:
        return f"${amount:.4f}".rstrip("0").rstrip(".")
    return f"${amount:.8f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------- #
# 做市商控盘达标标的（用户 2026-10-21 追加）
#
# 五条判据全在 `alpha_mm_monitor.run_round()` 里：控盘 ≥90%（剔池/销毁/交易所托管）、
# 必须 Alpha 收录 + 有永续、流通市值 ≤3000 万美元、拉盘后暴跌（回撤 ≥60%）永久排除、
# 上涨结构回踩支撑给买点。候选源是**涨幅榜 + 热门榜 + Alpha 板块涨幅**，用户要求把
# 「这些条件满足的标的」也放进做市监控板块里展示。
#
# **这里绝不重写判定**：只读 `alpha_mm_monitor` 的状态文件。两边各判一次的话，口径
# 一改就会分叉——用户会看到「名册里有、但弹窗不报」或者反过来。唯一真源只能是
# `run_round()` 写进去的 `watch` / `seeds` / `buys` / `blocked`。
# --------------------------------------------------------------------------- #
CONTROL_TARGET_LIMIT = 60
CONTROL_EXCLUDED_LIMIT = 20


def alpha_mm_module():
    """惰性取判据模块（server 与测试拿到的都是同一个实例）。"""
    import alpha_mm_monitor

    return alpha_mm_monitor


def alpha_control_state() -> dict[str, Any]:
    """读控盘模块的状态。先按它自己写的位置读，读不到再按项目根目录兜一次。"""
    try:
        module = alpha_mm_module()
    except Exception:
        return {}
    candidates: list[str | None] = [None]
    relative = str(getattr(module, "STATE_PATH", "") or "")
    if relative and not os.path.isabs(relative):
        candidates.append(str((Path(__file__).resolve().parent / relative).resolve()))
    for path in candidates:
        try:
            state = module.load_state() if path is None else module.load_state(path)
        except Exception:
            continue
        if isinstance(state, dict) and state:
            return state
    return {}


def control_status(entry: dict[str, Any]) -> tuple[str, str]:
    """把「上涨结构 / 支撑距离 / 破位」翻成一句话状态。

    日线与支撑都没算出来的条目（旧版本写进去的 watch 记录、或这一轮日线抓取失败）
    一律标「待下轮复核」——不能显示成「结构走弱」，那是在替用户下一个没有依据的判断。
    """
    if truthy(entry.get("broken"), default=False):
        return "broken", "支撑破位"
    if to_float(entry.get("price")) <= 0 or to_float(entry.get("support")) <= 0:
        return "pending", "待下轮复核"
    if truthy(entry.get("inBuyZone"), default=False) and truthy(entry.get("trendUp"), default=False):
        return "buy", "回踩支撑到位"
    if truthy(entry.get("trendUp"), default=False):
        return "wait", "上涨结构·等回踩"
    return "weak", "结构走弱"


def control_targets(state: dict[str, Any] | None = None, *,
                    limit: int = CONTROL_TARGET_LIMIT) -> dict[str, Any]:
    """控盘达标标的 + 已排除名单 + 阈值，字段全部可直接渲染。"""
    state = alpha_control_state() if state is None else state
    watch = state.get("watch") if isinstance(state.get("watch"), dict) else {}
    seeds = state.get("seeds") if isinstance(state.get("seeds"), dict) else {}
    buys = state.get("buys") if isinstance(state.get("buys"), dict) else {}
    blocked = state.get("blocked") if isinstance(state.get("blocked"), dict) else {}

    rows: list[dict[str, Any]] = []
    for symbol, entry in watch.items():
        if not isinstance(entry, dict):
            continue
        chain = str(entry.get("chain") or "")
        contract = str(entry.get("contract") or "")
        price = to_float(entry.get("price"))
        support = to_float(entry.get("support"))
        distance = ((price - support) / support * 100.0) if (price > 0 and support > 0) else 0.0
        status, status_label = control_status(entry)
        seeded = seeds.get(symbol) if isinstance(seeds.get(symbol), dict) else {}
        bought = buys.get(symbol) if isinstance(buys.get(symbol), dict) else {}
        origins = [str(item) for item in (entry.get("origins") or []) if item]
        rows.append({
            "symbol": str(symbol),
            "name": clean_text(entry.get("name"), 60),
            "chain": chain,
            "chainLabel": chain_label(chain),
            "contract": contract,
            "network": str(entry.get("network") or ""),
            "controlPct": round(to_float(entry.get("controlPct")), 2),
            "controlText": f"{to_float(entry.get('controlPct'), 0.0):.1f}%",
            "marketCap": round(to_float(entry.get("marketCap")), 2),
            "marketCapText": money_text(entry.get("marketCap")),
            "drawdownPct": round(to_float(entry.get("drawdownPct")), 2),
            "drawdownText": f"回撤 {to_float(entry.get('drawdownPct'), 0.0):.0f}%",
            "changePct": round(to_float(entry.get("changePct")), 2),
            "changeText": f"{to_float(entry.get('changePct'), 0.0):+.1f}%",
            "price": price,
            "priceText": price_text(price),
            "support": support,
            "supportText": price_text(support),
            "supportLabel": str(entry.get("supportLabel") or ""),
            "distancePct": round(distance, 2),
            "inBuyZone": truthy(entry.get("inBuyZone"), default=False),
            "broken": truthy(entry.get("broken"), default=False),
            "trendUp": truthy(entry.get("trendUp"), default=False),
            "status": status,
            "statusLabel": status_label,
            "origin": " / ".join(origins) or str(entry.get("origin") or ""),
            "origins": origins,
            "board": str(entry.get("board") or ""),
            "url": str(entry.get("url") or ""),
            "gmgnUrl": gmgn_token_url(chain, contract),
            "explorerUrl": explorer_token_url(chain, contract),
            "watchedAt": int(to_float(entry.get("at")) * 1000),
            "seededAt": int(to_float(seeded.get("at")) * 1000),
            "seeded": bool(seeded),
            "boughtAt": int(to_float(bought.get("at")) * 1000),
        })

    tone_order = {"buy": 0, "wait": 1, "pending": 2, "weak": 3, "broken": 4}
    rows.sort(key=lambda r: (tone_order.get(r["status"], 9), -r["controlPct"], -r["watchedAt"]))
    rows = rows[:max(1, limit)]

    excluded = []
    for symbol, entry in blocked.items():
        if not isinstance(entry, dict):
            continue
        excluded.append({
            "symbol": str(symbol),
            "reason": clean_text(entry.get("reason"), 120) or "历史高点回撤过大",
            "drawdownPct": round(to_float(entry.get("drawdownPct")), 2),
            "at": int(to_float(entry.get("at")) * 1000),
        })
    excluded.sort(key=lambda r: -r["at"])

    def threshold(name: str, default: float) -> float:
        try:
            module = alpha_mm_module()
        except Exception:
            return default
        return to_float(getattr(module, name, default), default)

    return {
        "targets": rows,
        "excluded": excluded[:CONTROL_EXCLUDED_LIMIT],
        "summary": {
            "total": len(rows),
            "buyZone": sum(1 for r in rows if r["status"] == "buy"),
            "waiting": sum(1 for r in rows if r["status"] == "wait"),
            "pending": sum(1 for r in rows if r["status"] == "pending"),
            "weak": sum(1 for r in rows if r["status"] == "weak"),
            "broken": sum(1 for r in rows if r["status"] == "broken"),
            "excluded": len(excluded),
            "hotOnly": sum(1 for r in rows if any("热门" in o for o in r["origins"])),
            "thresholds": {
                "minControlPct": threshold("MM_MIN_CONTROL_PCT", 90.0),
                "maxMarketCapUsd": threshold("MM_MAX_MARKET_CAP_USD", 30_000_000.0),
                "maxDrawdownPct": threshold("MM_MAX_DRAWDOWN_PCT", 60.0),
                "supportZonePct": threshold("MM_SUPPORT_ZONE_PCT", 2.0),
                "supportBreakPct": threshold("MM_SUPPORT_BREAK_PCT", 3.0),
            },
        },
    }


# --------------------------------------------------------------------------- #
# 索引器
# --------------------------------------------------------------------------- #
def _session():
    """懒建 requests.Session —— import 本模块不该产生网络依赖。

    ``trust_env=True`` 是默认值且必须保留：外网抓取一律跟随系统代理。
    """
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = requests.Session()
        return _SESSION


def _blockscout_headers(base: str) -> dict[str, str]:
    """公共 Blockscout 实例前面挂着 Cloudflare。

    实测（2026-10-15）：不带浏览器 UA / ``Accept`` / ``Referer`` 一律 403；
    v1 的 ``/api?module=...`` 直接吃挑战页。只有 ``/api/v2/...`` 带齐头才稳定 200。
    """
    return {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
        "Referer": base.rstrip("/") + "/",
    }


class IndexerError(RuntimeError):
    """索引器不可用。调用方一律降级，绝不把「查不到」当成「没有动作」。"""


def address_token_transfers(
    chain: str, address: str, *, pages: int = 1
) -> list[dict[str, Any]]:
    """拉一个地址最近的 ERC-20 转账（进出都有）。

    返回**已归一化**的行：``occurredAt`` 毫秒、``amount`` 已按 decimals 折算、
    ``direction`` 是 ``in``/``out``、``counterparty`` 是对方地址。
    """
    base = str((CHAIN_META.get(chain) or {}).get("blockscout") or "")
    if not base:
        raise IndexerError(f"{chain} 没有可用的 Blockscout 实例")
    target = str(address or "").strip()
    if not is_evm_address(target):
        raise IndexerError(f"{target[:12]} 不是合法的 EVM 地址")
    rows: list[dict[str, Any]] = []
    params: dict[str, Any] = {"type": "ERC-20"}
    for page in range(max(1, min(4, int(pages)))):
        try:
            response = _session().get(
                f"{base}/api/v2/addresses/{target}/token-transfers",
                params=dict(params),
                headers=_blockscout_headers(base),
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise IndexerError(f"{chain} 拉取 {target[:10]}… 转账失败：{str(exc)[:140]}") from exc
        if not isinstance(payload, dict):
            raise IndexerError(f"{chain} 返回了非对象响应")
        for item in payload.get("items") or []:
            row = normalize_transfer(chain, target, item)
            if row:
                rows.append(row)
        next_page = payload.get("next_page_params")
        if page + 1 >= pages or not isinstance(next_page, dict) or not next_page:
            break
        params = {"type": "ERC-20", **next_page}
    return rows


def normalize_transfer(chain: str, address: str, item: Any) -> dict[str, Any] | None:
    """把一条 Blockscout 转账记录折算成库里的行；不可用的返回 ``None``。"""
    if not isinstance(item, dict):
        return None
    token = item.get("token") if isinstance(item.get("token"), dict) else {}
    if str(token.get("type") or "ERC-20").upper() != "ERC-20":
        return None
    if str(token.get("reputation") or "").casefold() in {"spam", "scam"}:
        return None
    holder = address.strip().lower()
    sender = str((item.get("from") or {}).get("hash") or "").strip().lower()
    receiver = str((item.get("to") or {}).get("hash") or "").strip().lower()
    if not sender or not receiver:
        return None
    if sender == receiver:
        # 自己转给自己（常见于包装/解包），净额为零，不算动作。
        return None
    if holder == receiver:
        direction, counterparty = "in", sender
    elif holder == sender:
        direction, counterparty = "out", receiver
    else:
        return None
    raw = item.get("total") if isinstance(item.get("total"), dict) else {}
    decimals = max(0, min(36, int(to_float(raw.get("decimals") or token.get("decimals"), 18))))
    try:
        amount = int(str(raw.get("value") or "0")) / (10 ** decimals)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return None
    tx_hash = str(item.get("transaction_hash") or "").strip().lower()
    if not tx_hash:
        return None
    price = to_float(token.get("exchange_rate"))
    token_address = str(token.get("address_hash") or "").strip().lower()
    if not token_address:
        return None
    return {
        "chain": chain,
        "tx_hash": tx_hash,
        "log_index": int(to_float(item.get("log_index"))),
        "address": holder,
        "token": token_address,
        "symbol": clean_text(token.get("symbol"), 40),
        "name": clean_text(token.get("name"), 80),
        "decimals": decimals,
        "amount": amount,
        "direction": direction,
        "counterparty": counterparty,
        "price_usd": price,
        "usd": amount * price if price > 0 else 0.0,
        "occurred_at": _iso_to_ms(item.get("timestamp")),
    }


# --------------------------------------------------------------------------- #
# 存储
# --------------------------------------------------------------------------- #
class MarketMakerStore:
    """做市商地址簿 + 流水 + 标的聚合。独立库，不与 auth_db / 其他子板块抢锁。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self._lock = threading.Lock()
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def _session(self):
        """短连接会话；退出即提交并**显式 close()**（WAL 下不关会攒 fd）。"""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._lock, self._session() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mm_addresses (
                    chain TEXT NOT NULL,
                    address TEXT NOT NULL,
                    firm TEXT NOT NULL,
                    tag TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT 'manual',
                    source_url TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_polled_at INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, address)
                );
                CREATE INDEX IF NOT EXISTS idx_mm_addr_firm ON mm_addresses (firm, chain);
                CREATE INDEX IF NOT EXISTS idx_mm_addr_poll ON mm_addresses (last_polled_at);
                CREATE TABLE IF NOT EXISTS mm_transfers (
                    chain TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL DEFAULT 0,
                    address TEXT NOT NULL,
                    firm TEXT NOT NULL,
                    token TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    decimals INTEGER NOT NULL DEFAULT 18,
                    amount REAL NOT NULL DEFAULT 0,
                    direction TEXT NOT NULL DEFAULT '',
                    counterparty TEXT NOT NULL DEFAULT '',
                    price_usd REAL NOT NULL DEFAULT 0,
                    usd REAL NOT NULL DEFAULT 0,
                    occurred_at INTEGER NOT NULL DEFAULT 0,
                    seen_at INTEGER NOT NULL DEFAULT 0,
                    baseline INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, tx_hash, log_index, address)
                );
                CREATE INDEX IF NOT EXISTS idx_mm_tx_seen ON mm_transfers (seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_mm_tx_token ON mm_transfers (chain, token);
                CREATE TABLE IF NOT EXISTS mm_positions (
                    firm TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    token TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    decimals INTEGER NOT NULL DEFAULT 18,
                    amount_in REAL NOT NULL DEFAULT 0,
                    amount_out REAL NOT NULL DEFAULT 0,
                    usd_in REAL NOT NULL DEFAULT 0,
                    usd_out REAL NOT NULL DEFAULT 0,
                    transfers INTEGER NOT NULL DEFAULT 0,
                    addresses INTEGER NOT NULL DEFAULT 0,
                    price_usd REAL NOT NULL DEFAULT 0,
                    first_seen_at INTEGER NOT NULL DEFAULT 0,
                    last_seen_at INTEGER NOT NULL DEFAULT 0,
                    announced_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (firm, chain, token)
                );
                CREATE INDEX IF NOT EXISTS idx_mm_pos_seen ON mm_positions (last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS mm_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                -- 三方共振的静默台账：按**标的**记账（不是按厂家），因为提示说的是
                -- 「这只币在做市商、涨幅榜、AIcoin 三边同时有动静」。
                CREATE TABLE IF NOT EXISTS mm_resonance (
                    chain TEXT NOT NULL,
                    token TEXT NOT NULL,
                    announced_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, token)
                );
            """)
            self._seed_addresses(conn)

    def _seed_addresses(self, conn: sqlite3.Connection) -> None:
        """把内置地址簿灌进库；已存在的行**只补 source_url**，绝不覆盖用户的开关。"""
        now = int(time.time() * 1000)
        for chain, firm, address, tag, evidence, source in MM_SEED_ADDRESSES:
            conn.execute(
                """
                INSERT INTO mm_addresses
                    (chain, address, firm, tag, evidence, source_url, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(chain, address) DO UPDATE SET
                    firm = excluded.firm,
                    tag = CASE WHEN mm_addresses.tag = '' THEN excluded.tag ELSE mm_addresses.tag END,
                    evidence = CASE WHEN mm_addresses.evidence = 'manual'
                                    THEN excluded.evidence ELSE mm_addresses.evidence END,
                    source_url = CASE WHEN mm_addresses.source_url = ''
                                      THEN excluded.source_url ELSE mm_addresses.source_url END
                """,
                (chain, address.lower(), firm, tag, evidence, source, now, now),
            )

    # -- 地址簿 ------------------------------------------------------------- #
    def addresses(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mm_addresses"
        if enabled_only:
            sql += " WHERE enabled = 1"
        with self._lock, self._session() as conn:
            rows = conn.execute(sql + " ORDER BY firm, chain, address").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            item["evidenceLabel"] = evidence_label(item["evidence"])
            out.append(item)
        return out

    def address(self, chain: Any, address: Any) -> dict[str, Any] | None:
        chain_key = normalize_chain(chain)
        key = str(address or "").strip().lower()
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT * FROM mm_addresses WHERE chain = ? AND address = ?",
                (chain_key, key),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["evidenceLabel"] = evidence_label(item["evidence"])
        return item

    def save_address(
        self,
        chain: Any,
        address: Any,
        firm: Any,
        *,
        tag: Any = "",
        evidence: Any = "manual",
        source_url: Any = "",
        enabled: Any = None,
    ) -> dict[str, Any]:
        chain_key = normalize_chain(chain)
        if not chain_key:
            raise ValueError("不支持的链")
        key = str(address or "").strip().lower()
        if not is_evm_address(key):
            raise ValueError("地址不是合法的 EVM 地址")
        firm_key = str(firm or "").strip()
        if firm_key not in MM_FIRM_LABELS:
            raise ValueError("不在做市商名册里")
        now = int(time.time() * 1000)
        with self._lock, self._session() as conn:
            conn.execute(
                """
                INSERT INTO mm_addresses
                    (chain, address, firm, tag, evidence, source_url, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, address) DO UPDATE SET
                    firm = excluded.firm,
                    tag = CASE WHEN excluded.tag = '' THEN mm_addresses.tag ELSE excluded.tag END,
                    evidence = excluded.evidence,
                    source_url = CASE WHEN excluded.source_url = '' THEN mm_addresses.source_url
                                      ELSE excluded.source_url END,
                    enabled = COALESCE(?, mm_addresses.enabled),
                    updated_at = excluded.updated_at
                """,
                (
                    chain_key, key, firm_key, clean_text(tag, 120),
                    clean_text(evidence, 20) or "manual", clean_text(source_url, 400),
                    1 if truthy(enabled, default=True) else 0, now, now,
                    None if enabled is None else (1 if truthy(enabled) else 0),
                ),
            )
        return self.address(chain_key, key) or {}

    def set_enabled(self, chain: Any, address: Any, enabled: Any) -> bool:
        chain_key = normalize_chain(chain)
        key = str(address or "").strip().lower()
        with self._lock, self._session() as conn:
            cursor = conn.execute(
                "UPDATE mm_addresses SET enabled = ?, updated_at = ? WHERE chain = ? AND address = ?",
                (1 if truthy(enabled) else 0, int(time.time() * 1000), chain_key, key),
            )
            return bool(cursor.rowcount)

    def remove_address(self, chain: Any, address: Any) -> bool:
        chain_key = normalize_chain(chain)
        key = str(address or "").strip().lower()
        with self._lock, self._session() as conn:
            cursor = conn.execute(
                "DELETE FROM mm_addresses WHERE chain = ? AND address = ?", (chain_key, key)
            )
            return bool(cursor.rowcount)

    def addresses_due(self, limit: int) -> list[dict[str, Any]]:
        """轮转：最久没被轮询的地址优先。"""
        with self._lock, self._session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mm_addresses WHERE enabled = 1
                ORDER BY last_polled_at ASC, firm ASC, address ASC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_polled(self, chain: str, address: str, *, error: str = "") -> None:
        now = int(time.time() * 1000)
        with self._lock, self._session() as conn:
            if error:
                # 索引器失败**不推进** last_polled_at：否则这条地址会被排到队尾，
                # 一次抖动就等于几十分钟没有覆盖。
                conn.execute(
                    "UPDATE mm_addresses SET last_error = ?, updated_at = ?"
                    " WHERE chain = ? AND address = ?",
                    (clean_text(error, 200), now, chain, address.lower()),
                )
            else:
                conn.execute(
                    "UPDATE mm_addresses SET last_polled_at = ?, last_error = '', updated_at = ?"
                    " WHERE chain = ? AND address = ?",
                    (now, now, chain, address.lower()),
                )

    def sweep_complete(self, firm: str, chain: str) -> bool:
        """这家在这条链上的地址是否**都至少被成功轮询过一次**。

        第一次扫街没走完就宣布「新标的」会把陈年老仓全报一遍，所以必须先跑满。
        """
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN last_polled_at > 0 THEN 1 ELSE 0 END) AS done"
                " FROM mm_addresses WHERE enabled = 1 AND firm = ? AND chain = ?",
                (firm, chain),
            ).fetchone()
        total = int(row["total"] or 0) if row else 0
        done = int(row["done"] or 0) if row else 0
        return total > 0 and done >= total

    # -- 设置 --------------------------------------------------------------- #
    def setting(self, key: str, default: Any = "") -> Any:
        with self._lock, self._session() as conn:
            row = conn.execute("SELECT value FROM mm_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> str:
        text = str(value)
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO mm_settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, text),
            )
        return text

    def alerts_enabled(self) -> bool:
        return truthy(self.setting(ALERTS_SETTING_KEY, "1"), default=True)

    def set_alerts_enabled(self, value: Any) -> bool:
        self.set_setting(ALERTS_SETTING_KEY, "1" if truthy(value) else "0")
        return self.alerts_enabled()

    # -- 流水 / 标的 -------------------------------------------------------- #
    def record_transfer(self, row: dict[str, Any], *, baseline: bool) -> dict[str, Any]:
        """写一条流水并增量更新标的聚合。返回 ``{inserted, newToken, position}``。"""
        now = int(time.time() * 1000)
        token = str(row["token"]).lower()
        firm = str(row["firm"])
        with self._lock, self._session() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO mm_transfers
                    (chain, tx_hash, log_index, address, firm, token, symbol, decimals, amount,
                     direction, counterparty, price_usd, usd, occurred_at, seen_at, baseline)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["chain"], row["tx_hash"], int(row["log_index"]), row["address"], firm,
                    token, row["symbol"], int(row["decimals"]), float(row["amount"]),
                    row["direction"], row["counterparty"], float(row["price_usd"]),
                    float(row["usd"]), int(row["occurred_at"]), now, 1 if baseline else 0,
                ),
            )
            if not cursor.rowcount:
                return {"inserted": False, "newToken": False, "position": {}}
            existing = conn.execute(
                "SELECT * FROM mm_positions WHERE firm = ? AND chain = ? AND token = ?",
                (firm, row["chain"], token),
            ).fetchone()
            new_token = existing is None
            amount = float(row["amount"])
            usd = float(row["usd"])
            incoming = amount if row["direction"] == "in" else 0.0
            outgoing = amount if row["direction"] == "out" else 0.0
            usd_in = usd if row["direction"] == "in" else 0.0
            usd_out = usd if row["direction"] == "out" else 0.0
            if new_token:
                conn.execute(
                    """
                    INSERT INTO mm_positions
                        (firm, chain, token, symbol, name, decimals, amount_in, amount_out,
                         usd_in, usd_out, transfers, addresses, price_usd, first_seen_at,
                         last_seen_at, announced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, 0)
                    """,
                    (
                        firm, row["chain"], token, row["symbol"], row.get("name") or "",
                        int(row["decimals"]), incoming, outgoing, usd_in, usd_out,
                        float(row["price_usd"]), int(row["occurred_at"]), int(row["occurred_at"]),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE mm_positions SET
                        amount_in = amount_in + ?,
                        amount_out = amount_out + ?,
                        usd_in = usd_in + ?,
                        usd_out = usd_out + ?,
                        transfers = transfers + 1,
                        price_usd = CASE WHEN ? > 0 THEN ? ELSE price_usd END,
                        symbol = CASE WHEN symbol = '' THEN ? ELSE symbol END,
                        last_seen_at = MAX(last_seen_at, ?)
                    WHERE firm = ? AND chain = ? AND token = ?
                    """,
                    (
                        incoming, outgoing, usd_in, usd_out,
                        float(row["price_usd"]), float(row["price_usd"]), row["symbol"],
                        int(row["occurred_at"]), firm, row["chain"], token,
                    ),
                )
            distinct = conn.execute(
                "SELECT COUNT(DISTINCT address) AS n FROM mm_transfers"
                " WHERE firm = ? AND chain = ? AND token = ?",
                (firm, row["chain"], token),
            ).fetchone()
            conn.execute(
                "UPDATE mm_positions SET addresses = ? WHERE firm = ? AND chain = ? AND token = ?",
                (int(distinct["n"] or 1), firm, row["chain"], token),
            )
            position = conn.execute(
                "SELECT * FROM mm_positions WHERE firm = ? AND chain = ? AND token = ?",
                (firm, row["chain"], token),
            ).fetchone()
        return {
            "inserted": True,
            "newToken": new_token,
            "position": dict(position) if position else {},
            "baseline": baseline,
        }

    def mark_announced(self, firm: str, chain: str, token: str) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "UPDATE mm_positions SET announced_at = ?"
                " WHERE firm = ? AND chain = ? AND token = ? AND announced_at = 0",
                (int(time.time() * 1000), firm, chain, token.lower()),
            )

    def resonance_announced_at(self, chain: str, token: str) -> int:
        """这只标的上次三方共振提示的时间（毫秒），0 = 从没提示过。"""
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT announced_at FROM mm_resonance WHERE chain = ? AND token = ?",
                (chain, str(token or "").lower()),
            ).fetchone()
        return int(row["announced_at"] or 0) if row else 0

    def mark_resonance_announced(self, chain: str, token: str) -> None:
        now = int(time.time() * 1000)
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO mm_resonance (chain, token, announced_at) VALUES (?, ?, ?)"
                " ON CONFLICT(chain, token) DO UPDATE SET announced_at = excluded.announced_at",
                (chain, str(token or "").lower(), now),
            )

    def positions(self, limit: int = DEFAULT_RESULTS_LIMIT) -> list[dict[str, Any]]:
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM mm_positions ORDER BY last_seen_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_transfers(self, limit: int = 60, *, large_only_usd: float = 0.0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mm_transfers"
        params: list[Any] = []
        if large_only_usd > 0:
            sql += " WHERE usd >= ?"
            params.append(float(large_only_usd))
        sql += " ORDER BY occurred_at DESC, seen_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock, self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._lock, self._session() as conn:
            def one(sql: str, *args: Any) -> int:
                row = conn.execute(sql, args).fetchone()
                return int((row[0] if row else 0) or 0)

            return {
                "addresses": one("SELECT COUNT(*) FROM mm_addresses"),
                "enabledAddresses": one("SELECT COUNT(*) FROM mm_addresses WHERE enabled = 1"),
                "firms": one("SELECT COUNT(DISTINCT firm) FROM mm_addresses"),
                "polled": one("SELECT COUNT(*) FROM mm_addresses WHERE last_polled_at > 0"),
                "transfers": one("SELECT COUNT(*) FROM mm_transfers"),
                "positions": one("SELECT COUNT(*) FROM mm_positions"),
                "cashTransfers": one(
                    "SELECT COUNT(*) FROM mm_transfers WHERE token IN ("
                    + ",".join("?" for _ in CASH_TOKENS) + ")",
                    *tuple(CASH_TOKENS),
                ),
            }


# --------------------------------------------------------------------------- #
# 巡航
# --------------------------------------------------------------------------- #
class MarketMakerMonitor:
    """按节奏轮询做市商地址的代币流水，聚合成「正在操作哪些标的」。"""

    def __init__(
        self,
        store: MarketMakerStore | None = None,
        *,
        interval: float | None = None,
        addresses_per_cycle: int | None = None,
        large_usd: float | None = None,
        alert_callback=None,
    ):
        self.store = store or MarketMakerStore()
        self.interval = float(
            interval if interval is not None
            else (os.getenv("MM_POLL_SECONDS") or DEFAULT_POLL_SECONDS)
        )
        self.addresses_per_cycle = int(
            addresses_per_cycle if addresses_per_cycle is not None
            else (os.getenv("MM_ADDRESSES_PER_CYCLE") or DEFAULT_ADDRESSES_PER_CYCLE)
        )
        self.large_usd = float(
            large_usd if large_usd is not None
            else (os.getenv("MM_LARGE_TRANSFER_USD") or MM_LARGE_TRANSFER_USD)
        )
        self.on_event = alert_callback
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # 一轮要打几十个外部请求，手动「立即刷新」撞上定时循环时后到的必须放弃。
        self._cycle_lock = threading.Lock()
        # 页面快照缓存：见 ``payload()`` —— 不让页面排在轮询的写事务后面。
        self._payload_lock = threading.Lock()
        self._payload_cache: dict[str, Any] | None = None
        self._payload_cache_at = 0.0
        self._payload_rebuilding = False
        self.last_cycle_at = 0.0
        self.last_error = ""
        # 「进场」弹窗的放行闸（用户 2026-10-21：只报与涨幅榜 / AIcoin 热门榜共振的标的）。
        # 由 server 注入：返回 (是否放行, 原因)。没注入 = 不设闸（老行为）。
        self.entry_qualifier = None
        self.entry_filtered = 0
        self.entry_filter_note = ""
        # 三方共振（大额异动 × 涨幅榜 × AIcoin）：由 server 注入，返回
        # ``{"ok": bool, "gainersRank": int, "gainersChangePct": float, "aicoinRank": int}``。
        # 没注入 / 数据不可用 = 一律走普通「大额调仓」，绝不假报共振。
        self.resonance_qualifier = None
        self.resonance_cooldown = float(RESONANCE_COOLDOWN_SECONDS)
        self.resonance_blocked = 0
        self.resonance_note = ""

    # -- 生命周期 ----------------------------------------------------------- #
    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="market-maker-monitor", daemon=True
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        self.stop()

    def set_alert_callback(self, callback) -> None:
        self.on_event = callback

    def set_entry_qualifier(self, qualifier) -> None:
        """「某家开始操作一个新标的」的放行闸。

        只在 ``new-token`` 这一类判定里用，**不碰大额调仓**。传 ``None`` 表示不设闸。
        """
        self.entry_qualifier = qualifier

    def set_resonance_qualifier(self, qualifier) -> None:
        """三方共振查询（用户 2026-10-21）：大额异动时问一下这只币此刻是不是
        同时挂在涨幅榜与 AIcoin 热门榜上。返回 ``{"ok": bool, ...明细}``。

        传 ``None`` 或不注入 = 永远走普通「大额调仓」，不会假报共振。
        """
        self.resonance_qualifier = qualifier

    def resonance_info(self, symbol: str) -> dict[str, Any]:
        """命中三方共振时返回明细，否则空 dict（**绝不因为查不到就编一个**）。"""
        if self.resonance_qualifier is None:
            return {}
        try:
            info = self.resonance_qualifier(symbol)
        except Exception as exc:
            self.resonance_note = f"共振查询出错：{type(exc).__name__}"
            return {}
        if not isinstance(info, dict) or not info.get("ok"):
            self.resonance_note = str((info or {}).get("note") or "")[:120]
            return {}
        return info

    def resonance_cooldown_ok(self, chain: str, token: str) -> bool:
        """同一标的 6 小时内只提示一次。共振是持续状态，反复弹没有新信息。"""
        last = self.store.resonance_announced_at(chain, token)
        if not last:
            return True
        if (time.time() * 1000 - last) < self.resonance_cooldown * 1000:
            self.resonance_blocked += 1
            return False
        return True

    def entry_allowed(self, symbol: str) -> bool:
        """进场弹窗放行与否。**不放行时绝不能写 ``announced_at``** ——

        先标记再判断的话，一只币今天不共振、明天共振了就永远不会再弹（墓碑效应）。
        """
        if self.entry_qualifier is None:
            return True
        try:
            allowed, reason = self.entry_qualifier(symbol)
        except Exception as exc:
            self.entry_filter_note = f"放行闸出错：{type(exc).__name__}"
            return False
        if allowed:
            return True
        self.entry_filtered += 1
        self.entry_filter_note = str(reason or "")
        return False

    def alerts_enabled(self) -> bool:
        return self.store.alerts_enabled()

    def set_alerts_enabled(self, value: Any) -> bool:
        return self.store.set_alerts_enabled(value)

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        # 启动先歇一拍：不和 server 起来时那一堆监控线程抢网络。
        self.stop_event.wait(35)
        while not self.stop_event.is_set():
            started = time.time()
            try:
                self.run_cycle()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            # 间隔按**起点**算，不是「跑完再干等一轮」：一轮 30 个地址要几分钟，
            # 跑完再 wait(interval) 会把实际节奏拖成 10 分钟以上（实测 14 分钟一轮），
            # 77 个地址要 40 分钟才轮完一圈。这里补足差额，节奏才等于 interval。
            remaining = self.interval - (time.time() - started)
            self.stop_event.wait(max(5.0, remaining))

    # -- 一轮 --------------------------------------------------------------- #
    def run_cycle(self) -> dict[str, int]:
        if not self._cycle_lock.acquire(blocking=False):
            return {"polled": 0, "transfers": 0, "busy": 1}
        polled = transfers = 0
        try:
            for row in self.store.addresses_due(self.addresses_per_cycle):
                if self.stop_event.is_set():
                    break
                try:
                    transfers += self.poll_address(row)
                    polled += 1
                except IndexerError as exc:
                    # 索引器失败只记错误，不推进游标 —— 下一轮还会从同一位置重试。
                    self.last_error = f"{row['chain']} {row['address'][:10]}… {exc}"[:200]
                    self.store.mark_polled(row["chain"], row["address"], error=str(exc))
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    self.store.mark_polled(row["chain"], row["address"], error=str(exc))
            self.last_cycle_at = time.time()
            # 一轮跑完顺手把页面快照热一遍：这时没有别的写者，读库是毫秒级，
            # 之后 30 秒内的每次页面轮询都是零等待。
            try:
                self.refresh_payload_cache()
            except Exception as exc:
                self.last_error = f"快照刷新失败：{type(exc).__name__}: {exc}"[:200]
            return {"polled": polled, "transfers": transfers}
        finally:
            self._cycle_lock.release()

    def poll_address(self, row: dict[str, Any]) -> int:
        """拉一个地址的转账并入库。返回本轮新写入的流水条数。"""
        chain = str(row["chain"])
        address = str(row["address"])
        firm = str(row["firm"])
        first_poll = not int(row.get("last_polled_at") or 0)
        pages = FIRST_POLL_PAGES if first_poll else STEADY_POLL_PAGES
        items = address_token_transfers(chain, address, pages=pages)
        # 「新标的」要等这家把这批地址**全扫过一遍**才算数，否则第一次拉到的
        # 陈年老仓会被当成今天刚开的新标的。
        announce_ok = self.store.sweep_complete(firm, chain)
        inserted = 0
        for item in items:
            payload = dict(item)
            payload["firm"] = firm
            payload["address"] = address.lower()
            result = self.store.record_transfer(payload, baseline=first_poll)
            if not result.get("inserted"):
                continue
            inserted += 1
            if first_poll:
                continue
            self._maybe_announce(payload, result, announce_ok=announce_ok)
        self.store.mark_polled(chain, address)
        return inserted

    def _maybe_announce(
        self, payload: dict[str, Any], result: dict[str, Any], *, announce_ok: bool
    ) -> None:
        """三类弹窗：新标的进场 / 三方共振 / 大额调仓。其余只进列表。

        「三方共振」不是第四类事件，而是**大额调仓的升级版**：同一笔流水如果同时满足
        「标的挂在涨幅榜」和「标的挂在 AIcoin 热门榜」，就发共振那一条，不再发普通大额，
        所以同一个动作永远不会出现两条弹窗（用户 2026-10-21 定）。
        """
        if self.on_event is None or not self.store.alerts_enabled():
            return
        firm = str(payload["firm"])
        chain = str(payload["chain"])
        token = str(payload["token"])
        symbol = str(payload.get("symbol") or "未知代币")
        position = result.get("position") or {}
        if result.get("newToken") and announce_ok and not int(position.get("announced_at") or 0):
            if not self.entry_allowed(symbol):
                return
            self.store.mark_announced(firm, chain, token)
            self._emit({
                "reason": "new-token",
                "firm": firm,
                "chain": chain,
                "token": token,
                "symbol": symbol,
                "direction": payload["direction"],
                "amount": payload["amount"],
                "usd": payload["usd"],
                "counterparty": payload["counterparty"],
                "txHash": payload["tx_hash"],
                "occurredAt": payload["occurred_at"],
            })
            return
        if float(payload.get("usd") or 0) >= self.large_usd:
            resonance = self.resonance_info(symbol)
            if resonance and self.resonance_cooldown_ok(chain, token):
                self.store.mark_resonance_announced(chain, token)
                self._emit({
                    "reason": "triple-resonance",
                    "firm": firm,
                    "chain": chain,
                    "token": token,
                    "symbol": symbol,
                    "direction": payload["direction"],
                    "amount": payload["amount"],
                    "usd": payload["usd"],
                    "counterparty": payload["counterparty"],
                    "txHash": payload["tx_hash"],
                    "occurredAt": payload["occurred_at"],
                    # 榜单明细原样带下去，文案由弹窗层拼（它手上才有格式化函数）。
                    "resonance": {
                        "gainersRank": int(to_float(resonance.get("gainersRank"))),
                        "gainersChangePct": to_float(resonance.get("gainersChangePct")),
                        "aicoinRank": int(to_float(resonance.get("aicoinRank"))),
                    },
                })
                return
            self._emit({
                "reason": "large-transfer",
                "firm": firm,
                "chain": chain,
                "token": token,
                "symbol": symbol,
                "direction": payload["direction"],
                "amount": payload["amount"],
                "usd": payload["usd"],
                "counterparty": payload["counterparty"],
                "txHash": payload["tx_hash"],
                "occurredAt": payload["occurred_at"],
            })

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.on_event(event)
        except Exception:
            # 弹窗通道异常绝不能影响扫描循环。
            pass

    def poll_address_now(self, chain: Any, address: Any) -> dict[str, Any]:
        """添加地址后立刻拉一次：用户马上就能看到这家在这个地址上动过什么。"""
        row = self.store.address(chain, address)
        if not row:
            return {"ok": False, "error": "该地址不在监控列表里"}
        if not row["enabled"]:
            return {"ok": False, "error": "该地址当前是暂停状态"}
        try:
            inserted = self.poll_address(row)
        except IndexerError as exc:
            return {"ok": False, "error": str(exc)[:200]}
        # 刚写完就得让下一次页面轮询看到（不然要等到下一轮 cycle 结束时才热）。
        try:
            self.refresh_payload_cache()
        except Exception as exc:
            self.last_error = f"快照刷新失败：{type(exc).__name__}: {exc}"[:200]
        return {"ok": True, "transfers": inserted}

    # -- 快照 --------------------------------------------------------------- #
    def status(self) -> dict[str, Any]:
        return {
            "running": self.running(),
            "intervalSeconds": self.interval,
            "addressesPerCycle": self.addresses_per_cycle,
            "largeTransferUsd": self.large_usd,
            "lastCycleAt": int(self.last_cycle_at * 1000) if self.last_cycle_at else 0,
            "lastError": self.last_error,
            "counts": self.store.counts(),
        }

    def payload(self, *, results_limit: int = DEFAULT_RESULTS_LIMIT, force_refresh: bool = False) -> dict[str, Any]:
        """监控页「做市商监控」子板块的完整快照。

        **页面这条路绝不排队等 store 锁**：轮询线程一条流水一次事务，而这个快照
        要连拿 5 次锁（地址簿 / 持仓 / 流水 ×2 / 计数），实测被饿到 2–47 秒 ——
        页面上就是一直停在「正在读取做市商监控状态」。所以：
        缓存热就立刻给；过期了也先给旧的 + 起后台重建；一次都没有时才同步建。
        """
        if force_refresh:
            return dict(self.refresh_payload_cache(results_limit=results_limit))
        now = time.time()
        with self._payload_lock:
            cached = self._payload_cache
            if cached is not None and now - self._payload_cache_at < PAYLOAD_CACHE_SECONDS:
                return dict(cached)
            if cached is not None:
                # 过期但能用：先给旧的，重建丢后台（同一时刻只允许一个重建线程）。
                if not self._payload_rebuilding:
                    self._payload_rebuilding = True
                    threading.Thread(
                        target=self._rebuild_payload_cache,
                        kwargs={"results_limit": results_limit},
                        name="market-maker-payload-rebuild",
                        daemon=True,
                    ).start()
                return dict(cached)
        return dict(self.refresh_payload_cache(results_limit=results_limit))

    def refresh_payload_cache(self, *, results_limit: int = DEFAULT_RESULTS_LIMIT) -> dict[str, Any]:
        """重建快照并写缓存。轮询线程每轮结束会调它，所以正常运行时缓存一直是热的。"""
        snapshot = self._build_payload(results_limit=results_limit)
        with self._payload_lock:
            self._payload_cache = snapshot
            self._payload_cache_at = time.time()
            self._payload_rebuilding = False
        return snapshot

    def _rebuild_payload_cache(self, *, results_limit: int = DEFAULT_RESULTS_LIMIT) -> None:
        try:
            self.refresh_payload_cache(results_limit=results_limit)
        except Exception as exc:  # 后台线程不能把异常抛给任何人
            with self._payload_lock:
                self._payload_rebuilding = False
            self.last_error = f"快照重建失败：{type(exc).__name__}: {exc}"[:200]

    def _build_payload(self, *, results_limit: int = DEFAULT_RESULTS_LIMIT) -> dict[str, Any]:
        """真正读库拼快照（慢，只在缓存冷了或 force 时走）。"""
        now_ms = int(time.time() * 1000)
        active_since = now_ms - int(ACTIVE_WINDOW_HOURS * 3600 * 1000)
        addresses = self.store.addresses()
        positions = self.store.positions(results_limit)
        transfers = self.store.recent_transfers(60)
        large = self.store.recent_transfers(40, large_only_usd=self.large_usd)
        counts = self.store.counts()

        position_rows = []
        for row in positions:
            token = str(row["token"])
            net = float(row["amount_in"] or 0) - float(row["amount_out"] or 0)
            net_usd = float(row["usd_in"] or 0) - float(row["usd_out"] or 0)
            is_cash = token.casefold() in CASH_TOKENS
            position_rows.append({
                "firm": row["firm"],
                "firmLabel": firm_label(row["firm"]),
                "chain": row["chain"],
                "chainLabel": chain_label(row["chain"]),
                "token": token,
                "symbol": row["symbol"] or "未知代币",
                "name": row["name"] or "",
                "amountIn": round(float(row["amount_in"] or 0), 8),
                "amountOut": round(float(row["amount_out"] or 0), 8),
                "amountInText": amount_text(row["amount_in"]),
                "amountOutText": amount_text(row["amount_out"]),
                "net": round(net, 8),
                "netText": ("+" if net >= 0 else "-") + amount_text(net),
                "usdIn": round(float(row["usd_in"] or 0), 2),
                "usdOut": round(float(row["usd_out"] or 0), 2),
                "usdInText": money_text(row["usd_in"]),
                "usdOutText": money_text(row["usd_out"]),
                "netUsd": round(net_usd, 2),
                "netUsdText": ("+" if net_usd >= 0 else "-") + (money_text(abs(net_usd)) or "$0"),
                "transfers": int(row["transfers"] or 0),
                "addresses": int(row["addresses"] or 0),
                "priceUsd": float(row["price_usd"] or 0),
                "firstSeenAt": int(row["first_seen_at"] or 0),
                "lastSeenAt": int(row["last_seen_at"] or 0),
                "active": int(row["last_seen_at"] or 0) >= active_since,
                "isCash": is_cash,
                "announced": bool(int(row["announced_at"] or 0)),
                "gmgnUrl": gmgn_token_url(row["chain"], token),
                "explorerUrl": explorer_token_url(row["chain"], token),
            })

        # 标的视角：同一个代币被几家的地址在动 —— 「共识标的」比单家仓位更有信息量。
        cross: dict[tuple[str, str], dict[str, Any]] = {}
        for row in position_rows:
            if row["isCash"] or not row["active"]:
                continue
            key = (row["chain"], row["token"])
            bucket = cross.get(key)
            if bucket is None:
                bucket = cross[key] = {
                    "chain": row["chain"],
                    "chainLabel": row["chainLabel"],
                    "token": row["token"],
                    "symbol": row["symbol"],
                    "firms": [],
                    "firmLabels": [],
                    "netUsd": 0.0,
                    "gestures": 0,
                    "lastSeenAt": 0,
                    "gmgnUrl": row["gmgnUrl"],
                    "explorerUrl": row["explorerUrl"],
                }
            bucket["firms"].append(row["firm"])
            bucket["firmLabels"].append(row["firmLabel"])
            bucket["netUsd"] += row["netUsd"]
            bucket["gestures"] += row["transfers"]
            bucket["lastSeenAt"] = max(bucket["lastSeenAt"], row["lastSeenAt"])
        cross_rows = sorted(cross.values(), key=lambda r: (-len(r["firms"]), -r["lastSeenAt"]))
        for item in cross_rows:
            item["netUsd"] = round(item["netUsd"], 2)
            item["netUsdText"] = ("+" if item["netUsd"] >= 0 else "-") + (
                money_text(abs(item["netUsd"])) or "$0"
            )
            item["firmCount"] = len(item["firms"])
            item["firmText"] = " / ".join(item["firmLabels"])

        transfer_rows = [
            {
                "firm": row["firm"],
                "firmLabel": firm_label(row["firm"]),
                "chain": row["chain"],
                "chainLabel": chain_label(row["chain"]),
                "token": row["token"],
                "symbol": row["symbol"] or "未知代币",
                "direction": row["direction"],
                "directionLabel": "收到" if row["direction"] == "in" else "转出",
                "amount": round(float(row["amount"] or 0), 8),
                "amountText": amount_text(row["amount"]),
                "usd": round(float(row["usd"] or 0), 2),
                "usdText": money_text(row["usd"]),
                "address": row["address"],
                "counterparty": row["counterparty"],
                "txHash": row["tx_hash"],
                "occurredAt": int(row["occurred_at"] or 0),
                "baseline": bool(int(row["baseline"] or 0)),
                "explorerUrl": explorer_tx_url(row["chain"], row["tx_hash"]),
                "gmgnUrl": gmgn_token_url(row["chain"], row["token"]),
            }
            for row in transfers
        ]
        large_rows = [row for row in transfer_rows if float(row["usd"] or 0) >= self.large_usd] or [
            {
                "firm": row["firm"],
                "firmLabel": firm_label(row["firm"]),
                "chain": row["chain"],
                "chainLabel": chain_label(row["chain"]),
                "token": row["token"],
                "symbol": row["symbol"] or "未知代币",
                "direction": row["direction"],
                "directionLabel": "收到" if row["direction"] == "in" else "转出",
                "amount": round(float(row["amount"] or 0), 8),
                "amountText": amount_text(row["amount"]),
                "usd": round(float(row["usd"] or 0), 2),
                "usdText": money_text(row["usd"]),
                "address": row["address"],
                "counterparty": row["counterparty"],
                "txHash": row["tx_hash"],
                "occurredAt": int(row["occurred_at"] or 0),
                "baseline": bool(int(row["baseline"] or 0)),
                "explorerUrl": explorer_tx_url(row["chain"], row["tx_hash"]),
                "gmgnUrl": gmgn_token_url(row["chain"], row["token"]),
            }
            for row in large
        ]

        firms = []
        for firm, label, short, brief in MM_FIRMS:
            owned = [row for row in addresses if row["firm"] == firm]
            active_positions = [
                row for row in position_rows
                if row["firm"] == firm and row["active"] and not row["isCash"]
            ]
            latest = max(
                [row["lastSeenAt"] for row in position_rows if row["firm"] == firm]
                + [int(row["occurredAt"]) for row in transfer_rows if row["firm"] == firm]
                + [0]
            )
            firms.append({
                "id": firm,
                "label": label,
                "short": short,
                "brief": brief,
                "addressCount": len(owned),
                "enabledCount": sum(1 for row in owned if row["enabled"]),
                "chains": sorted({row["chain"] for row in owned}),
                "chainLabels": sorted({chain_label(row["chain"]) for row in owned}),
                "activeTokens": len(active_positions),
                "tokens": int(sum(1 for row in position_rows if row["firm"] == firm)),
                "lastActivityAt": latest,
                "evidence": sorted({row["evidence"] for row in owned}),
                "evidenceLabels": sorted({row["evidenceLabel"] for row in owned}),
                "polled": sum(1 for row in owned if int(row["last_polled_at"] or 0) > 0),
            })

        last_cycle_at = int(self.last_cycle_at * 1000) if self.last_cycle_at else 0
        # 控盘达标标的走的是另一条数据源（榜单 + GoPlus 持仓），和做市商地址流水
        # 互不依赖：读不到状态就是空表，不影响流水展示。
        control = control_targets()
        return {
            "ok": True,
            "now": now_ms,
            "running": self.running(),
            "intervalSeconds": self.interval,
            "addressesPerCycle": self.addresses_per_cycle,
            "largeTransferUsd": self.large_usd,
            "activeWindowHours": ACTIVE_WINDOW_HOURS,
            "lastCycleAt": last_cycle_at,
            "lastError": self.last_error,
            "chains": chain_options(),
            "firms": firms,
            "addresses": addresses,
            "positions": position_rows,
            "crossToken": cross_rows[:40],
            "transfers": transfer_rows,
            "largeTransfers": large_rows,
            # 做市商控盘那一路（热门榜 / 涨幅榜里满足五条判据的标的）。
            "controlTargets": control["targets"],
            "controlExcluded": control["excluded"],
            "controlSummary": control["summary"],
            "summary": {
                "running": self.running(),
                "alertsEnabled": self.store.alerts_enabled(),
                "firms": len(MM_FIRMS),
                "addresses": int(counts.get("addresses") or 0),
                "enabledAddresses": int(counts.get("enabledAddresses") or 0),
                "polled": int(counts.get("polled") or 0),
                "transfers": int(counts.get("transfers") or 0),
                "cashTransfers": int(counts.get("cashTransfers") or 0),
                "positions": int(counts.get("positions") or 0),
                "activePositions": sum(1 for row in position_rows if row["active"]),
                "largeTransfers": len([row for row in transfer_rows if float(row["usd"] or 0) >= self.large_usd]),
                "crossTokens": len(cross_rows),
                "controlTargets": int(control["summary"]["total"]),
                "controlBuyZone": int(control["summary"]["buyZone"]),
                "controlExcluded": int(control["summary"]["excluded"]),
                "sinceHours": ACTIVE_WINDOW_HOURS,
                "lastCycleAt": last_cycle_at,
                "intervalSeconds": self.interval,
                "addressesPerCycle": self.addresses_per_cycle,
                "largeTransferUsd": self.large_usd,
                "lastError": self.last_error,
                # 进场闸的「已暂缓几次」由监控器自己数，server 拼 entryGate 时要取它。
                "entryGate": {
                    "filtered": int(self.entry_filtered),
                    "note": self.entry_filter_note,
                },
                # 三方共振：是否装了查询、静默窗口多长、被静默过几次。
                "resonance": {
                    "armed": self.resonance_qualifier is not None,
                    "cooldownSeconds": int(self.resonance_cooldown),
                    "blocked": int(self.resonance_blocked),
                    "note": self.resonance_note,
                },
            },
        }


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="做市商监控：查名册、地址、标的与流水")
    parser.add_argument("--payload", action="store_true", help="打印完整的子板块快照")
    parser.add_argument("--once", action="store_true", help="只跑一轮巡航")
    parser.add_argument("--firms", action="store_true", help="打印做市商名册与地址数")
    args = parser.parse_args()

    monitor = MarketMakerMonitor()
    if args.firms:
        rows = monitor.store.addresses()
        for firm, label, _short, _brief in MM_FIRMS:
            owned = [row for row in rows if row["firm"] == firm]
            chains = sorted({row["chain"] for row in owned})
            print(f"{label:<18} 地址 {len(owned):>3}  链 {','.join(chains) or '-'}")
        return 0
    if args.once:
        print(json.dumps(monitor.run_cycle(), ensure_ascii=False))
        print(f"lastError={monitor.last_error}")
        return 0
    payload = monitor.payload()
    print(json.dumps(payload, ensure_ascii=False, indent=1)[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
