#!/usr/bin/env python3
"""GMGN 交易记录的「被夹」检测（三明治夹 / sandwich）。

在 DEX 上买小币时，攻击者可以在**同一个区块**里「先买 → 你买 → 再卖」：
他的买入把池价抬高，你按抬高的价格成交，他随后卖出把利润落袋。受害者多付的
那部分就是被夹损失。本模块把 GMGN 交易记录逐笔回链上取证，判定是否被夹。

三层漏斗（越往下越贵，只对上一层命中的样本做）：

  第 1 层（0 次 RPC）  纯 GMGN 数据：只保留 ``buy`` / ``sell`` 且金额达到阈值的成交。
  第 2 层（2 次 RPC）  区块内计数：拉该区块涉及该 token 的 ERC-20 Transfer，
                       若整块只有它自己这一笔，同块内不可能存在前后夹 → 直接判「正常」。
  第 3 层（重）        拉整块 + 受害者 receipt → 找池子 → 按池子净流排序列出同块所有
                       相关交易 → 判定 front/victim/back 结构。

结论三档：``normal``（正常）/ ``suspected``（疑似：只看到一侧，或两侧不同源）/
``confirmed``（确认：前后夹同源且筹码往返）。每笔 tx 的结论**永久缓存** —— 历史交易
不会变，重复取证只是白烧 RPC 配额。

为什么不直接信 GMGN：它的交易记录只有成交汇总（价、量、gas），没有区块内顺序。
有没有被夹是**区块内相对位置**决定的，只能在链上还原。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
#: 只做 EVM：Solana 判夹要逐 slot 拉区块再解析 AMM 指令，公共 RPC 限流明显。
EVM_CHAINS = ("bsc", "ethereum", "base", "robinhood")
#: GMGN CLI 的 --chain 取值（它用 eth 而不是 ethereum）。
GMGN_CHAIN_BY_CHAIN = {"bsc": "bsc", "ethereum": "eth", "base": "base", "robinhood": "robinhood"}

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
#: Uniswap/PancakeSwap V2 ``Swap(address,uint256,uint256,uint256,uint256,address)``
SWAP_V2_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
#: Uniswap/PancakeSwap V3 ``Swap(address,address,int256,int256,uint160,uint128,int24)``
SWAP_V3_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
SWAP_TOPICS = (SWAP_V2_TOPIC, SWAP_V3_TOPIC)

DEFAULT_POLL_SECONDS = 90.0
DEFAULT_MIN_TRADE_USD = 20.0
DEFAULT_ACTIVITY_LIMIT = 30
DEFAULT_MAX_CHECKS_PER_CYCLE = 6
DEFAULT_RESULTS_LIMIT = 60
#: 桌面弹窗总开关的设置键（关了只写列表，不弹窗）。
ALERTS_SETTING_KEY = "alertsEnabled"
GMGN_ACTIVITY_TIMEOUT = 45
RPC_TIMEOUT = (4, 15)
#: front/back 筹码往返容差：夹子必须把买的筹码卖回去，数量不会差太多。
ROUND_TRIP_TOLERANCE = 0.35
#: 单个区块最多处理多少个相关交易（防病态区块把一轮拖死）。
MAX_BLOCK_CANDIDATES = 40
#: 单笔最多取证几次。RPC 抖动值得重试，永久性 unknown（如交易还没上链）不该无限空转。
MAX_CHECK_ATTEMPTS = 3

#: 各链 RPC（与 smart_money_monitor.CHAIN_META 同源；这里只保留本模块需要的字段）。
CHAIN_META: dict[str, dict[str, Any]] = {
    "bsc": {
        "label": "BSC", "gmgn": "bsc", "native": "BNB",
        "explorer": "https://bscscan.com/tx/{}",
        "rpcEnv": "SMART_MONEY_BSC_RPC_URLS",
        "rpcs": (
            "https://bsc-rpc.publicnode.com",
            "https://bsc-dataseed.bnbchain.org",
            "https://bsc-dataseed-public.bnbchain.org",
        ),
    },
    "ethereum": {
        "label": "Ethereum", "gmgn": "eth", "native": "ETH",
        "explorer": "https://etherscan.io/tx/{}",
        "rpcEnv": "SMART_MONEY_ETHEREUM_RPC_URLS",
        "rpcs": (
            "https://eth.drpc.org", "https://rpc.flashbang.xyz",
            "https://ethereum-rpc.publicnode.com",
        ),
    },
    "base": {
        "label": "Base", "gmgn": "base", "native": "ETH",
        "explorer": "https://basescan.org/tx/{}",
        "rpcEnv": "SMART_MONEY_BASE_RPC_URLS",
        "rpcs": ("https://mainnet.base.org", "https://base-rpc.publicnode.com"),
    },
    "robinhood": {
        "label": "Robinhood", "gmgn": "robinhood", "native": "ETH",
        "explorer": "https://robinhoodchain.blockscout.com/tx/{}",
        "rpcEnv": "SMART_MONEY_ROBINHOOD_RPC_URLS",
        "rpcs": ("https://rpc.mainnet.chain.robinhood.com/",),
    },
}
CHAIN_ALIASES = {
    "bsc": "bsc", "bnb": "bsc", "binance": "bsc", "56": "bsc", "币安": "bsc",
    "eth": "ethereum", "ethereum": "ethereum", "1": "ethereum", "以太坊": "ethereum",
    "base": "base", "8453": "base",
    "robinhood": "robinhood", "hood": "robinhood", "4663": "robinhood",
}
EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

#: 需要 buy/sell 之外的活动类型一律不检测（approve / transfer / burn 没有夹的概念）。
TRADE_EVENT_TYPES = ("buy", "sell")

_GMGN_INDEX_JS = os.getenv(
    "GMGN_CLI_INDEX_JS",
    r"C:\Users\ZhuanZ1\AppData\Roaming\npm\node_modules\gmgn-cli\dist\index.js",
)


def normalize_chain(value: Any) -> str:
    """把任意链名收敛成 EVM_CHAINS 里的一个；不认识就返回空串。"""
    key = str(value or "").strip().lower()
    return CHAIN_ALIASES.get(key, key if key in EVM_CHAINS else "")


def chain_options() -> list[dict[str, str]]:
    """给前端钱包表单用的链清单（顺序 = EVM_CHAINS，前端不再自己维护一份）。"""
    return [
        {"id": chain, "label": CHAIN_META[chain]["label"], "native": CHAIN_META[chain]["native"]}
        for chain in EVM_CHAINS
    ]


def truthy(value: Any, *, default: bool = True) -> bool:
    """宽松真值：``"0"/"false"/"off"/"no"/"disabled"`` 为假，空值取 ``default``。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "off", "no", "disabled"}


def is_evm_address(value: Any) -> bool:
    return bool(EVM_ADDRESS_RE.match(str(value or "").strip()))


def runtime_dir() -> Path:
    """运行态目录，算法与 ``app.core.state.PERSIST_CACHE_DIR`` 保持一致。"""
    configured = str(os.getenv("XINGYUN_RUNTIME_DIR", "") or "").strip()
    root = Path(__file__).resolve().parent
    return Path(configured).expanduser().resolve() if configured else root / ".runtime-cache"


def default_db_path() -> Path:
    return runtime_dir() / "sandwich_detector.sqlite"


def explorer_url(chain: str, tx_hash: str) -> str:
    template = str((CHAIN_META.get(chain) or {}).get("explorer") or "")
    return template.format(tx_hash) if template and tx_hash else ""


def hex_to_int(value: Any) -> int:
    """RPC 标量（``"0x1a"`` / ``"26"``）转 int，坏值返回 0。"""
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(float(text))
    except (TypeError, ValueError):
        return 0


def hex_word(value: Any) -> int:
    """解析一段**裸十六进制**（EVENT data 切出来的 32 字节字），坏值返回 0。

    不能借道 ``hex_to_int``：那里没有 ``0x`` 前缀就按十进制读，而事件数据切片后
    是纯十六进制 —— ``"…03e8"`` 会被 ``float()`` 读成 ``3e8 = 300000000``（
    ``"…3039"`` 这类全是数字的还能读出个看似合理的错数，更难发现）。
    """
    text = str(value or "").strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if not text:
        return 0
    try:
        return int(text, 16)
    except ValueError:
        return 0


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # NaN 归零


# --------------------------------------------------------------------------- #
# GMGN 交易记录
# --------------------------------------------------------------------------- #
def _gmgn_node_args() -> list[str]:
    """直接跑 ``node index.js``，不经过 gmgn-cli.cmd。

    Windows 上 ``shell=True`` 调 .cmd 是超时陷阱：subprocess 的 timeout 只杀
    cmd.exe 包装进程，真正的 node 子进程还活着，communicate() 会一直等它的
    stdout 管道（该项目此前踩过，见 gmgn_local_trench_analyzer 的注释）。
    """
    node = shutil.which("node") or "node"
    return [node, _GMGN_INDEX_JS]


def fetch_wallet_activity(chain: str, wallet: str, *, limit: int = DEFAULT_ACTIVITY_LIMIT) -> list[dict[str, Any]]:
    """拉一个钱包在一条链上的最近成交。失败返回空列表（调用方照常往下走）。"""
    chain_key = normalize_chain(chain)
    if not chain_key or not is_evm_address(wallet):
        return []
    args = [
        *_gmgn_node_args(), "portfolio", "activity",
        "--chain", CHAIN_META[chain_key]["gmgn"],
        "--wallet", str(wallet).strip(),
        "--limit", str(max(1, min(int(limit), 100))),
        "--raw",
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=GMGN_ACTIVITY_TIMEOUT)
        if result.returncode != 0:
            return []
        payload = json.loads(result.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, ValueError):
        return []
    rows = payload.get("activities") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def normalize_activity(chain: str, wallet: str, row: dict[str, Any]) -> dict[str, Any]:
    """把一条 GMGN activity 收敛成本模块的行结构。

    实测（2026-10-13，BSC 真实返回）：GMGN 的数字**全是字符串**（``"487.60"``），
    字段缺失时是空串而不是 null —— 所以一律走 ``to_float``。

    ``price_usd`` 是**代币**的美元价，``quote_amount`` 是付出的报价币数量，两者一起
    可以反推出报价币的美元价（实测 BNB ≈ 671，量级对得上），后面对损失估值要用 ——
    所以这里必须把四个数都原样留下。``cost_usd`` 是 GMGN 自己算好的美元规模，比
    「代币数 × 单价」更可靠（个别行 ``price_usd`` 为空）—— 留在行里给 ``trade_usd``
    用，不入库。
    """
    token = row.get("token") if isinstance(row.get("token"), dict) else {}
    return {
        "chain": chain,
        "tx_hash": str(row.get("tx_hash") or "").strip().lower(),
        "wallet": str(row.get("wallet") or wallet or "").strip().lower(),
        "token": str(token.get("address") or "").strip().lower(),
        "symbol": str(token.get("symbol") or "").strip()[:40],
        "event_type": str(row.get("event_type") or "").strip().lower(),
        "token_amount": to_float(row.get("token_amount")),
        "quote_amount": to_float(row.get("quote_amount")),
        "price_usd": to_float(row.get("price_usd")),
        "cost_usd": to_float(
            row.get("cost_usd") or row.get("buy_cost_usd") or row.get("sell_cost_usd")
        ),
        "trade_at": int(to_float(row.get("timestamp"))),
        "gas_usd": to_float(row.get("gas_usd")),
        "priority_fee": to_float(row.get("priority_fee")),
        "tip_fee": to_float(row.get("tip_fee")),
        "launchpad": str(row.get("launchpad_platform") or row.get("launchpad") or "").strip()[:60],
    }


def trade_usd(activity: dict[str, Any]) -> float:
    """该笔成交的美元规模。GMGN 给了 ``cost_usd`` 就先用它，否则用 代币数 × 单价 补。

    优先 GMGN 那个数不是图省事：``price_usd`` 偶尔是空串，此时乘法得 0，这笔就会
    被金额门槛静默丢掉 —— 而它恰恰是真实的成交。
    """
    for key in ("cost_usd", "buy_cost_usd", "sell_cost_usd"):
        value = to_float(activity.get(key))
        if value > 0:
            return value
    return to_float(activity.get("token_amount")) * to_float(activity.get("price_usd"))


# --------------------------------------------------------------------------- #
# RPC
# --------------------------------------------------------------------------- #
_RPC_SESSION = None  # 懒建：让 import 本模块不产生网络依赖
_RPC_SESSION_LOCK = threading.Lock()


def _session():
    global _RPC_SESSION
    with _RPC_SESSION_LOCK:
        if _RPC_SESSION is None:
            import requests

            _RPC_SESSION = requests.Session()
        return _RPC_SESSION


def rpc_urls(chain: str) -> list[str]:
    meta = CHAIN_META[chain]
    configured = [v.strip() for v in str(os.getenv(meta["rpcEnv"], "") or "").split(",") if v.strip()]
    return list(dict.fromkeys([*configured, *meta["rpcs"]]))


def rpc(chain: str, method: str, params: list[Any]) -> Any:
    """JSON-RPC，多端点逐个降级。全部失败抛 RuntimeError。"""
    chain_key = normalize_chain(chain)
    if not chain_key:
        raise RuntimeError(f"不支持的链：{chain}")
    errors: list[str] = []
    session = _session()
    for url in rpc_urls(chain_key):
        try:
            response = session.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=RPC_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(str(payload["error"])[:160])
            return payload.get("result")
        except Exception as exc:
            errors.append(f"{url}: {str(exc)[:100]}")
    raise RuntimeError("；".join(errors) or f"{chain_key} RPC 不可用")


# --------------------------------------------------------------------------- #
# 纯解析（无网络，全部可单测）
# --------------------------------------------------------------------------- #
def transfer_rows(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 ERC-20 Transfer 日志拍平成 ``{from, to, value, txHash, txIndex, logIndex}``。

    只认标准 3-topic 的 Transfer（ERC-721 的 Transfer 有 4 个 topic，值语义不同，
    混进来会把数量算歪）。
    """
    rows: list[dict[str, Any]] = []
    for log in logs or []:
        if not isinstance(log, dict):
            continue
        topics = [str(t).lower() for t in (log.get("topics") or [])]
        if len(topics) != 3 or topics[0] != TRANSFER_TOPIC:
            continue
        rows.append({
            "from": "0x" + topics[1][-40:],
            "to": "0x" + topics[2][-40:],
            "value": hex_to_int(log.get("data")),
            "txHash": str(log.get("transactionHash") or "").lower(),
            "txIndex": hex_to_int(log.get("transactionIndex")),
            "logIndex": hex_to_int(log.get("logIndex")),
        })
    return rows


def pool_net_flows(transfers: list[dict[str, Any]], pool: str) -> dict[str, dict[str, Any]]:
    """按交易聚合「该代币相对池子的净流」。

    正数 = 净流入池子（这笔在**卖**该代币），负数 = 净流出池子（这笔在**买**）。
    这个符号约定避开了解析 token0/token1——只需要池子地址就能判方向。
    """
    pool = str(pool or "").lower()
    flows: dict[str, dict[str, Any]] = {}
    for row in transfers:
        tx = row.get("txHash") or ""
        if not tx:
            continue
        bucket = flows.setdefault(tx, {
            "txHash": tx,
            "txIndex": row.get("txIndex") or 0,
            "logIndex": row.get("logIndex") or 0,
            "in": 0,
            "out": 0,
        })
        bucket["txIndex"] = min(bucket["txIndex"], row.get("txIndex") or bucket["txIndex"])
        if row.get("to") == pool:
            bucket["in"] += row.get("value") or 0
        elif row.get("from") == pool:
            bucket["out"] += row.get("value") or 0
    for bucket in flows.values():
        bucket["net"] = bucket["in"] - bucket["out"]
        bucket["amount"] = max(bucket["in"], bucket["out"])
        if bucket["net"] > 0:
            bucket["direction"] = "sell"
        elif bucket["net"] < 0:
            bucket["direction"] = "buy"
        else:
            bucket["direction"] = ""
    return {tx: bucket for tx, bucket in flows.items() if bucket["direction"]}


def swap_pool_from_receipt(receipt: dict[str, Any]) -> str:
    """从交易回执里找出 DEX 池子地址（第一个标准 AMM Swap 事件的 emit 者）。"""
    for log in (receipt or {}).get("logs") or []:
        topics = [str(t).lower() for t in ((log or {}).get("topics") or [])]
        if topics and topics[0] in SWAP_TOPICS:
            return str((log or {}).get("address") or "").lower()
    return ""


def parse_v2_swap(log: dict[str, Any], token: str, pool_token0: str) -> dict[str, Any]:
    """解析 V2 ``Swap`` 事件，返回该笔在池子里的代币/报价币进出量。

    需要 pool_token0 才能把 amount0/amount1 对上代币（V2 的 Swap 事件本身不带
    代币顺序，只有两个 indexed 地址：sender / to）。
    """
    data = str((log or {}).get("data") or "")
    body = data[2:] if data.lower().startswith("0x") else data
    if len(body) < 256:            # 标准 V2 Swap 的 data 恒为四个 32 字节字
        return {}
    amount0_in, amount1_in, amount0_out, amount1_out = (
        hex_word(body[i * 64:(i + 1) * 64]) for i in range(4)
    )
    if str(token).lower() == str(pool_token0).lower():
        token_in, token_out = amount0_in, amount0_out
        quote_in, quote_out = amount1_in, amount1_out
    else:
        token_in, token_out = amount1_in, amount1_out
        quote_in, quote_out = amount0_in, amount0_out
    return {
        "tokenIn": token_in, "tokenOut": token_out,
        "quoteIn": quote_in, "quoteOut": quote_out,
    }


def swap_events(receipt: dict[str, Any], pool: str) -> list[dict[str, Any]]:
    """取出回执里属于 ``pool`` 的标准 AMM Swap 事件。"""
    pool = str(pool or "").lower()
    return [
        log for log in ((receipt or {}).get("logs") or [])
        if str((log or {}).get("address") or "").lower() == pool
        and str(((log or {}).get("topics") or [""])[0]).lower() in SWAP_TOPICS
    ]


def v2_price(swap: dict[str, Any], *, buy: bool) -> float:
    """V2 成交价（报价币 / 代币）。买入看 quoteIn/tokenOut，卖出看 quoteOut/tokenIn。"""
    if buy:
        token_out = to_float(swap.get("tokenOut"))
        quote_in = to_float(swap.get("quoteIn"))
        return quote_in / token_out if token_out > 0 and quote_in > 0 else 0.0
    token_in = to_float(swap.get("tokenIn"))
    quote_out = to_float(swap.get("quoteOut"))
    return quote_out / token_in if token_in > 0 and quote_out > 0 else 0.0


def classify_sandwich(
    entries: list[dict[str, Any]],
    victim_tx: str,
    *,
    tolerance: float = ROUND_TRIP_TOLERANCE,
) -> dict[str, Any]:
    """判定 victim 是否落在「同源前后夹」里。

    结构（受害者是买入时）：前面的攻击者也买（推高价格）、后面同一地址卖出（落袋）。
    受害者是卖出时方向对称：前面同向卖、后面反向买回。

    判定条件（三条同时成立才算 confirmed）：
      1. victim 前最近一笔**同向**交易存在（front），后最近一笔**反向**交易存在（back）；
      2. front 与 back 来自同一地址（``sender``）；
      3. back 的筹码量与 front 往返（差额在 ``tolerance`` 内）——夹子必须把买的卖回去。
    只有一侧、或两侧不同源，判 ``suspected``；两侧都找不到判 ``normal``。
    """
    victim_key = str(victim_tx or "").lower()
    ordered = sorted(
        [e for e in entries if e.get("direction")],
        key=lambda e: (e.get("txIndex") or 0, e.get("logIndex") or 0),
    )
    position = next((i for i, e in enumerate(ordered) if str(e.get("txHash") or "").lower() == victim_key), -1)
    if position < 0:
        return {"verdict": "unknown", "reason": "受害者交易不在本块相关列表里", "front": None, "back": None}
    victim = ordered[position]

    front = None
    for entry in reversed(ordered[:position]):
        if entry.get("direction") == victim.get("direction"):
            front = entry
            break
    back = None
    for entry in ordered[position + 1:]:
        if entry.get("direction") != victim.get("direction"):
            back = entry
            break

    if not front and not back:
        return {"verdict": "normal", "reason": "同块内没有前后方向的夹击交易", "front": None, "back": None}

    same_sender = bool(front and back) and str(front.get("sender") or "") == str(back.get("sender") or "")
    round_trip = False
    deviation = None
    if front and back and to_float(front.get("amount")) > 0:
        deviation = abs(to_float(back.get("amount")) - to_float(front.get("amount"))) / to_float(front.get("amount"))
        round_trip = deviation <= tolerance

    detail = {
        "front": front,
        "back": back,
        "attacker": str((front or {}).get("sender") or "") if same_sender else "",
        "sameSender": same_sender,
        "roundTrip": round_trip,
        "roundTripDeviation": deviation,
    }
    if front and back and same_sender and round_trip:
        detail.update({
            "verdict": "confirmed",
            "reason": "同块内同地址先顺向抬价、后反向落袋，筹码往返一致",
        })
    else:
        reasons = []
        if not front or not back:
            reasons.append("只看到一侧的夹击交易")
        if front and back and not same_sender:
            reasons.append("前后两笔不是同一地址")
        if front and back and same_sender and not round_trip:
            reasons.append("前后筹码量相差过大")
        detail.update({"verdict": "suspected", "reason": "；".join(reasons) or "结构不完整"})
    return detail


def quote_usd_price(activity: dict[str, Any]) -> float:
    """从一笔成交反推**报价币**的美元价（BNB/ETH/USDT…）。

    GMGN 给的是代币的美元价与报价币数量，两者相乘即这笔的美元金额，再除以报价币
    数量就是报价币本身的美元价。拿不到就返回 0（后续损失估算会走退化路线）。
    """
    quote_amount = to_float(activity.get("quote_amount"))
    token_amount = to_float(activity.get("token_amount"))
    price_usd = to_float(activity.get("price_usd"))
    if quote_amount <= 0 or token_amount <= 0 or price_usd <= 0:
        return 0.0
    return (token_amount * price_usd) / quote_amount


def estimate_loss(
    *,
    victim_activity: dict[str, Any],
    pre_price: float,
    victim_price: float,
) -> dict[str, Any]:
    """估算被夹损失。

    ``pre_price`` / ``victim_price`` 都以报价币计价（V2 swap 事件算出来的）。
    损失 = 受害者拿到的代币数量 × 单价差；再用报价币美元价折成美元。
    价格算不出来时返回 0，并标明原因 —— 不编数字。
    """
    token_amount = to_float(victim_activity.get("token_amount"))
    if token_amount <= 0 or pre_price <= 0 or victim_price <= 0 or victim_price <= pre_price:
        return {"lossUsd": 0.0, "impactPct": 0.0, "note": "价格数据不足，未能估算损失"}
    impact_pct = (victim_price - pre_price) / pre_price * 100.0
    extra_quote = token_amount * (victim_price - pre_price)
    quote_usd = quote_usd_price(victim_activity)
    return {
        "lossUsd": round(extra_quote * quote_usd, 4) if quote_usd > 0 else 0.0,
        "lossQuote": round(extra_quote, 10),
        "quoteUsd": round(quote_usd, 6),
        "impactPct": round(impact_pct, 3),
        "prePrice": pre_price,
        "victimPrice": victim_price,
        "note": "" if quote_usd > 0 else "缺少报价币美元价，只有百分比冲击",
    }


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
class SandwichStore:
    """钱包配置 + 成交 + 结论。独立库，不与 auth_db / 聪明钱库抢锁。"""

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
        """短连接会话：退出即提交并**显式 close()**。

        ``sqlite3.Connection`` 自己当上下文管理器只提交事务、**不关连接** —— 直接
        ``with self._connect() as conn`` 会把文件句柄一路漏下去：单测里临时目录删不掉
        （Windows ``WinError 32``），常驻进程里则是句柄池缓慢膨胀。
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._lock, self._session() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sandwich_wallets (
                    chain TEXT NOT NULL,
                    address TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, address)
                );
                CREATE TABLE IF NOT EXISTS sandwich_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandwich_activities (
                    chain TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    token TEXT NOT NULL DEFAULT '',
                    wallet TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    token_amount REAL NOT NULL DEFAULT 0,
                    quote_amount REAL NOT NULL DEFAULT 0,
                    price_usd REAL NOT NULL DEFAULT 0,
                    block_number INTEGER NOT NULL DEFAULT 0,
                    tx_index INTEGER NOT NULL DEFAULT 0,
                    trade_at INTEGER NOT NULL DEFAULT 0,
                    observed_at INTEGER NOT NULL DEFAULT 0,
                    gas_usd REAL NOT NULL DEFAULT 0,
                    priority_fee REAL NOT NULL DEFAULT 0,
                    tip_fee REAL NOT NULL DEFAULT 0,
                    launchpad TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    verdict TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    detected_at INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, tx_hash, event_type, token)
                );
                CREATE INDEX IF NOT EXISTS idx_sandwich_status ON sandwich_activities(status, trade_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sandwich_trade ON sandwich_activities(trade_at DESC);
            """)
            # 老库补列：``CREATE TABLE IF NOT EXISTS`` 不会给已存在的表加字段。
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sandwich_activities)").fetchall()}
            if "attempts" not in columns:
                conn.execute("ALTER TABLE sandwich_activities ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")

    # -- 钱包 --------------------------------------------------------------- #
    def wallets(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT chain, address, label, enabled FROM sandwich_wallets"
        if enabled_only:
            sql += " WHERE enabled = 1"
        with self._lock, self._session() as conn:
            rows = conn.execute(sql + " ORDER BY chain, address").fetchall()
        return [
            {"chain": row["chain"], "address": row["address"], "label": row["label"], "enabled": bool(row["enabled"])}
            for row in rows
        ]

    def save_wallet(self, chain: Any, address: Any, label: Any = None, enabled: Any = None) -> dict[str, Any]:
        """新增或更新一个监控钱包。

        ``label`` / ``enabled`` 传 ``None`` 表示**这一项不动** —— 前端「暂停/恢复」
        只发 enabled，不能顺手把用户写的备注抹掉（老实现用 upsert 无条件覆盖
        label，一次暂停就会清空备注）。
        """
        chain_key = normalize_chain(chain)
        address_key = str(address or "").strip().lower()
        if not chain_key:
            raise ValueError("链无效，仅支持 bsc / eth / base / robinhood")
        if not is_evm_address(address_key):
            raise ValueError("钱包地址格式无效")
        now = int(time.time() * 1000)
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT label, enabled FROM sandwich_wallets WHERE chain = ? AND address = ?",
                (chain_key, address_key),
            ).fetchone()
            if row:
                next_label = row["label"] if label is None else str(label or "")[:40]
                next_enabled = (
                    bool(row["enabled"]) if enabled is None
                    else truthy(enabled, default=True)
                )
                conn.execute(
                    "UPDATE sandwich_wallets SET label = ?, enabled = ?, updated_at = ? "
                    "WHERE chain = ? AND address = ?",
                    (next_label, 1 if next_enabled else 0, now, chain_key, address_key),
                )
            else:
                next_label = str(label or "")[:40]
                next_enabled = truthy(enabled, default=True)
                conn.execute(
                    """INSERT INTO sandwich_wallets (chain, address, label, enabled, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (chain_key, address_key, next_label, 1 if next_enabled else 0, now, now),
                )
        return {"chain": chain_key, "address": address_key, "label": next_label, "enabled": next_enabled}

    def remove_wallet(self, chain: Any, address: Any) -> bool:
        chain_key = normalize_chain(chain)
        with self._lock, self._session() as conn:
            cursor = conn.execute(
                "DELETE FROM sandwich_wallets WHERE chain = ? AND address = ?",
                (chain_key, str(address or "").strip().lower()),
            )
            return bool(cursor.rowcount)

    # -- 设置 --------------------------------------------------------------- #
    def setting(self, key: str, default: Any = "") -> Any:
        with self._lock, self._session() as conn:
            row = conn.execute("SELECT value FROM sandwich_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> str:
        text = str(value)
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO sandwich_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(key), text),
            )
        return text

    def min_trade_usd(self) -> float:
        """排队门槛（美元）：低于它的成交连取证队列都不进。

        未配置 → 用 ``DEFAULT_MIN_TRADE_USD``；配置了但**解析不出来** → 0（等于不设
        门槛）。刻意不让非法值回落到默认数：用户既然填了，就别拿一个他从没填过的
        数字替他做主。
        """
        raw = str(self.setting("minTradeUsd", "") or "").strip()
        if not raw:
            return DEFAULT_MIN_TRADE_USD
        return max(0.0, to_float(raw, 0.0))

    def alerts_enabled(self) -> bool:
        """桌面弹窗总开关。关闭只影响弹窗，列表照常记录。"""
        return truthy(self.setting(ALERTS_SETTING_KEY, "1"), default=True)

    def set_alerts_enabled(self, value: Any) -> bool:
        enabled = truthy(value, default=True)
        self.set_setting(ALERTS_SETTING_KEY, "1" if enabled else "0")
        return enabled

    # -- 成交 --------------------------------------------------------------- #
    def upsert_activity(self, row: dict[str, Any]) -> bool:
        """入库一条成交，返回「是不是新记录」。老记录不覆盖已有结论。"""
        with self._lock, self._session() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sandwich_activities WHERE chain = ? AND tx_hash = ? AND event_type = ? AND token = ?",
                (row["chain"], row["tx_hash"], row["event_type"], row["token"]),
            ).fetchone()
            if exists:
                # 只补市场字段（价格会随行情更新），结论与状态保持不动。
                conn.execute(
                    """UPDATE sandwich_activities SET price_usd = ?, token_amount = ?, quote_amount = ?
                       WHERE chain = ? AND tx_hash = ? AND event_type = ? AND token = ?""",
                    (row["price_usd"], row["token_amount"], row["quote_amount"],
                     row["chain"], row["tx_hash"], row["event_type"], row["token"]),
                )
                return False
            conn.execute(
                """INSERT INTO sandwich_activities
                   (chain, tx_hash, event_type, token, wallet, symbol, token_amount, quote_amount,
                    price_usd, trade_at, observed_at, gas_usd, priority_fee, tip_fee, launchpad, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (row["chain"], row["tx_hash"], row["event_type"], row["token"], row["wallet"],
                 row["symbol"], row["token_amount"], row["quote_amount"], row["price_usd"],
                 row["trade_at"], int(time.time()), row["gas_usd"], row["priority_fee"],
                 row["tip_fee"], row["launchpad"]),
            )
            return True

    def pending(self, limit: int = DEFAULT_MAX_CHECKS_PER_CYCLE) -> list[dict[str, Any]]:
        """待取证的成交。

        ``attempts`` 是**重试上限**的抓手：RPC 抽风导致的 unknown 值得再试几次，
        但「交易还没有区块高度」这种会一直 unknown —— 没有上限就是永不停止的
        空转（而且还会把同一条记录一遍遍顶到队列最前，把新交易饿死）。
        """
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM sandwich_activities WHERE status IN ('pending', 'retry') "
                "AND attempts < ? ORDER BY trade_at DESC LIMIT ?",
                (MAX_CHECK_ATTEMPTS, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def finish(self, item: dict[str, Any], *, status: str, verdict: str, detail: dict[str, Any]) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                """UPDATE sandwich_activities
                   SET status = ?, verdict = ?, detail = ?, detected_at = ?, attempts = attempts + 1,
                       block_number = COALESCE(?, block_number), tx_index = COALESCE(?, tx_index)
                   WHERE chain = ? AND tx_hash = ? AND event_type = ? AND token = ?""",
                (status, verdict, json.dumps(detail, ensure_ascii=False),
                 int(time.time() * 1000), detail.get("blockNumber"), detail.get("txIndex"),
                 item["chain"], item["tx_hash"], item["event_type"], item["token"]),
            )

    def results(self, limit: int = 60, *, only_flagged: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sandwich_activities WHERE status IN ('done', 'skipped')"
        if only_flagged:
            sql += " AND verdict IN ('confirmed', 'suspected')"
        sql += " ORDER BY trade_at DESC LIMIT ?"
        with self._lock, self._session() as conn:
            rows = conn.execute(sql, (max(1, int(limit)),)).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except ValueError:
                item["detail"] = {}
            item["explorerUrl"] = explorer_url(item["chain"], item["tx_hash"])
            # ``contractAddress`` 是 ``token`` 的别名，只为对齐监控页既有的
            # ``buyIdentity`` 富化（monitor_identity 认 contractAddress/tokenAddress
            # 而不认 token）—— 结果卡片才带得上「买入」按钮。
            item["contractAddress"] = item.get("token") or ""
            out.append(item)
        return out

    def counts(self) -> dict[str, int]:
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM sandwich_activities "
                "WHERE status IN ('done', 'skipped') GROUP BY verdict"
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM sandwich_activities WHERE status IN ('pending', 'retry')"
            ).fetchone()
        tally = {str(row["verdict"] or "unknown"): int(row["n"]) for row in rows}
        tally["pending"] = int(pending["n"]) if pending else 0
        return tally


# --------------------------------------------------------------------------- #
# 取证（Stage 2 / 3）
# --------------------------------------------------------------------------- #
def _block_transactions(chain: str, block_number: int) -> dict[str, dict[str, Any]]:
    """区块号 → {txHash: {from, txIndex}}。一次拿全，省掉逐笔 getTransaction。"""
    block = rpc(chain, "eth_getBlockByNumber", [hex(block_number), True]) or {}
    out: dict[str, dict[str, Any]] = {}
    for tx in block.get("transactions") or []:
        if not isinstance(tx, dict):
            continue
        tx_hash = str(tx.get("hash") or "").lower()
        if tx_hash:
            out[tx_hash] = {
                "from": str(tx.get("from") or "").lower(),
                "to": str(tx.get("to") or "").lower(),
                "txIndex": hex_to_int(tx.get("transactionIndex")),
            }
    return out


def _pool_token0(chain: str, pool: str) -> str:
    """池子的 token0（V2/V3 都有这个 view）。失败返回空串。"""
    try:
        result = rpc(chain, "eth_call", [{"to": pool, "data": "0x0dfe1681"}, "latest"])
    except RuntimeError:
        return ""
    word = str(result or "")
    return "0x" + word[-40:].lower() if len(word) >= 42 else ""


def check_activity(chain: str, activity: dict[str, Any]) -> dict[str, Any]:
    """对一个成交做完整取证，返回 verdict dict。

    任何一步失败都返回 ``unknown`` + 原因，**绝不猜测**：宁可让用户看到「无法取证」，
    也不能把没证据的交易标成「被夹」。
    """
    chain_key = normalize_chain(chain)
    tx_hash = str(activity.get("tx_hash") or "").lower()
    token = str(activity.get("token") or "").lower()
    if not chain_key or not tx_hash or not is_evm_address(token):
        return {"verdict": "unknown", "reason": "缺少链上定位所需字段（tx/合约地址）"}

    # ---- Stage 2：定位区块 + 数清同块该代币的参与度 ------------------------- #
    try:
        tx = rpc(chain_key, "eth_getTransactionByHash", [tx_hash]) or {}
        block_number = hex_to_int(tx.get("blockNumber"))
        tx_index = hex_to_int(tx.get("transactionIndex"))
    except RuntimeError as exc:
        return {"verdict": "unknown", "reason": f"取交易失败：{exc}"[:180]}
    if not block_number:
        return {"verdict": "unknown", "reason": "交易还没有区块高度（可能刚提交）"}

    try:
        transfers = transfer_rows(rpc(chain_key, "eth_getLogs", [{
            "fromBlock": hex(block_number), "toBlock": hex(block_number),
            "address": token, "topics": [TRANSFER_TOPIC],
        }]) or [])
    except RuntimeError as exc:
        return {"verdict": "unknown", "reason": f"区块日志不可用：{exc}"[:180]}

    involved = {row["txHash"] for row in transfers if row.get("txHash")}
    if len(involved) <= 1:
        return {
            "verdict": "normal",
            "reason": "同块内只有这一笔涉及该代币，不存在夹击空间",
            "blockNumber": block_number, "txIndex": tx_index,
            "stage": 2,
        }

    # ---- Stage 3：找池子 → 排顺序 → 判结构 -------------------------------- #
    try:
        receipt = rpc(chain_key, "eth_getTransactionReceipt", [tx_hash]) or {}
    except RuntimeError as exc:
        return {"verdict": "unknown", "reason": f"取回执失败：{exc}"[:180],
                "blockNumber": block_number, "txIndex": tx_index, "stage": 2}

    pool = swap_pool_from_receipt(receipt)
    if not pool:
        return {
            "verdict": "suspected",
            "reason": "该笔未经标准 AMM 池（找不到 Swap 事件），无法还原区块内价格顺序",
            "blockNumber": block_number, "txIndex": tx_index,
            "involved": len(involved), "stage": 2,
        }

    flows = pool_net_flows(transfers, pool)
    if tx_hash not in flows:
        return {"verdict": "unknown", "reason": "该代币日志里找不到这笔对池子的进出",
                "blockNumber": block_number, "txIndex": tx_index, "stage": 3}
    if len(flows) > MAX_BLOCK_CANDIDATES:
        flows = dict(sorted(flows.items(), key=lambda kv: abs(kv[1]["txIndex"] - tx_index))[:MAX_BLOCK_CANDIDATES])

    try:
        txs = _block_transactions(chain_key, block_number)
    except RuntimeError as exc:
        return {"verdict": "unknown", "reason": f"取整块失败：{exc}"[:180],
                "blockNumber": block_number, "txIndex": tx_index, "stage": 3}

    entries = []
    for tx_key, flow in flows.items():
        meta = txs.get(tx_key) or {}
        entries.append({
            "txHash": tx_key,
            "txIndex": meta.get("txIndex", flow.get("txIndex") or 0),
            "logIndex": flow.get("logIndex") or 0,
            "sender": meta.get("from") or "",
            "amount": flow.get("amount") or 0,
            "net": flow.get("net") or 0,
            "direction": flow.get("direction") or "",
        })
    verdict = classify_sandwich(entries, tx_hash)
    verdict.update({
        "blockNumber": block_number,
        "txIndex": tx_index,
        "pool": pool,
        "poolLabel": str(pool),
        "involved": len(involved),
        "stage": 3,
    })
    victim_entry = next((e for e in entries if e["txHash"] == tx_hash), {})
    verdict["victim"] = {
        "txHash": tx_hash, "sender": victim_entry.get("sender") or "",
        "amount": victim_entry.get("amount") or 0, "amountText": str(victim_entry.get("amount") or 0),
        "direction": victim_entry.get("direction") or "",
    }

    # 只有确认/疑似才值得再花 RPC 算价格；正常交易直接返回。
    if verdict.get("verdict") in {"confirmed", "suspected"}:
        verdict["price"] = _price_context(chain_key, token, pool, receipt, victim_entry, verdict, activity)
    return verdict


def _price_context(
    chain: str,
    token: str,
    pool: str,
    victim_receipt: dict[str, Any],
    victim_entry: dict[str, Any],
    verdict: dict[str, Any],
    activity: dict[str, Any],
) -> dict[str, Any]:
    """用 V2 Swap 事件还原「夹前价 → 受害者成交价」，估算损失。"""
    token0 = _pool_token0(chain, pool)
    if not token0:
        return {"available": False, "note": "池子 token0 读取失败"}
    buy = (victim_entry.get("direction") or activity.get("event_type")) == "buy"
    victim_swap = next((s for s in swap_events(victim_receipt, pool)
                        if str(((s.get("topics") or [""])[0])).lower() == SWAP_V2_TOPIC), None)
    if victim_swap is None:
        return {"available": False, "note": "受害者这笔不是 V2 池交易，无法按 V2 口径算价"}
    victim_price = v2_price(parse_v2_swap(victim_swap, token, token0), buy=buy)
    if victim_price <= 0:
        return {"available": False, "note": "受害者成交价解析为空"}

    front = verdict.get("front") or {}
    front_receipt = None
    if front.get("txHash"):
        try:
            front_receipt = rpc(chain, "eth_getTransactionReceipt", [front["txHash"]]) or {}
        except RuntimeError:
            front_receipt = None
    pre_price = 0.0
    if front_receipt:
        front_swap = next((s for s in swap_events(front_receipt, pool)
                           if str(((s.get("topics") or [""])[0])).lower() == SWAP_V2_TOPIC), None)
        if front_swap is not None:
            # 夹子的第一笔与受害者同向（买入受害者 → 夹子也是买入），故用同一 buy 口径。
            pre_price = v2_price(parse_v2_swap(front_swap, token, token0), buy=buy)
    if pre_price <= 0:
        return {"available": False, "victimPrice": victim_price,
                "note": "拿不到夹前价（缺少夹子那一笔的 V2 事件）"}
    loss = estimate_loss(victim_activity=activity, pre_price=pre_price, victim_price=victim_price)
    loss.update({"available": True, "poolToken0": token0, "method": "v2-swap"})
    return loss


# --------------------------------------------------------------------------- #
# 扫描器
# --------------------------------------------------------------------------- #
class SandwichDetector:
    """按固定节奏拉 GMGN 成交、跑三层漏斗，命中的回调给上层（弹窗）。"""

    def __init__(
        self,
        store: SandwichStore | None = None,
        *,
        interval: float | None = None,
        max_checks_per_cycle: int | None = None,
        on_confirmed=None,
    ):
        self.store = store or SandwichStore()
        self.interval = float(interval if interval is not None else
                              (os.getenv("SANDWICH_POLL_SECONDS") or DEFAULT_POLL_SECONDS))
        self.max_checks = int(max_checks_per_cycle or
                              (os.getenv("SANDWICH_MAX_CHECKS_PER_CYCLE") or DEFAULT_MAX_CHECKS_PER_CYCLE))
        self.on_confirmed = on_confirmed
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # 一轮 = 抓 GMGN + 若干次 RPC，可能跑很久。手动「立即检测」与定时循环
        # 撞上时必须让后到的那个直接放弃，否则同一条记录会被并发取证两次。
        self._cycle_lock = threading.Lock()
        self.last_cycle_at = 0.0
        self.last_error = ""

    # -- 生命周期 ----------------------------------------------------------- #
    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="sandwich-detector", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        """atexit 钩子用（与 ``SMART_MONEY_MONITOR.close`` 对齐）。"""
        self.stop()

    def set_alert_callback(self, callback) -> None:
        """由服务端注入弹窗通道；回调异常绝不允许影响检测循环。"""
        self.on_confirmed = callback

    def alerts_enabled(self) -> bool:
        return self.store.alerts_enabled()

    def set_alerts_enabled(self, value: Any) -> bool:
        return self.store.set_alerts_enabled(value)

    def _loop(self) -> None:
        # 启动先歇一拍：别和 server 起来时的一堆监控线程抢网络。
        self.stop_event.wait(20)
        while not self.stop_event.is_set():
            try:
                self.run_cycle()
            except Exception as exc:  # 任何异常都不该让线程死掉
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            self.stop_event.wait(self.interval)

    # -- 一轮 --------------------------------------------------------------- #
    def run_cycle(self) -> dict[str, int]:
        """抓取 + 取证各跑一轮。已在跑就立刻返回 ``busy``，不排队。"""
        if not self._cycle_lock.acquire(blocking=False):
            return {"ingested": 0, "checked": 0, "busy": True}
        try:
            ingested = self.scan_wallets()
            checked = self.process_pending()
            self.last_cycle_at = time.time()
            return {"ingested": ingested, "checked": checked}
        finally:
            self._cycle_lock.release()

    def scan_wallets(self) -> int:
        """把所有启用的钱包 × 链的最近成交灌进库，返回新增条数。"""
        min_usd = self.store.min_trade_usd()
        added = 0
        for wallet in self.store.wallets(enabled_only=True):
            rows = fetch_wallet_activity(wallet["chain"], wallet["address"])
            for raw in rows:
                row = normalize_activity(wallet["chain"], wallet["address"], raw)
                if row["event_type"] not in TRADE_EVENT_TYPES:
                    continue
                if not row["tx_hash"] or not is_evm_address(row["token"]):
                    continue
                # 第 1 层漏斗：金额太小的交易夹子无利可图，直接不排队。
                if trade_usd(row) < min_usd:
                    continue
                if self.store.upsert_activity(row):
                    added += 1
        return added

    def process_pending(self) -> int:
        """对排队中的成交做链上取证。"""
        checked = 0
        for item in self.store.pending(self.max_checks):
            if self.stop_event.is_set():
                break
            try:
                verdict = check_activity(item["chain"], item)
            except Exception as exc:
                verdict = {"verdict": "unknown", "reason": f"{type(exc).__name__}: {exc}"[:180]}
            if verdict.get("verdict") != "unknown":
                status = "done"
            else:
                # 用完重试次数就落到终态，否则它会永远排在队列里挡住别人。
                used = int(item.get("attempts") or 0) + 1
                status = "retry" if used < MAX_CHECK_ATTEMPTS else "skipped"
            self.store.finish(item, status=status, verdict=str(verdict.get("verdict") or "unknown"), detail=verdict)
            checked += 1
            if verdict.get("verdict") == "confirmed" and self.on_confirmed is not None:
                try:
                    self.on_confirmed({**item, "detail": verdict})
                except Exception:
                    pass
        return checked

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "intervalSeconds": self.interval,
            "lastCycleAt": int(self.last_cycle_at * 1000) if self.last_cycle_at else 0,
            "lastError": self.last_error,
            "counts": self.store.counts(),
            "wallets": self.store.wallets(),
            "minTradeUsd": self.store.min_trade_usd(),
        }

    def payload(self, *, results_limit: int = DEFAULT_RESULTS_LIMIT) -> dict[str, Any]:
        """监控页「被夹检测」子板块的完整快照。

        前端只渲染、不二次统计 —— 计数（含 ``pending``）一律以库里的最终状态为准，
        否则页面数字会和实际队列悄悄漂移。
        """
        now_ms = int(time.time() * 1000)
        counts = self.store.counts()
        wallets = self.store.wallets()
        last_cycle_at = int(self.last_cycle_at * 1000) if self.last_cycle_at else 0
        return {
            "ok": True,
            "running": bool(self._thread and self._thread.is_alive()),
            "now": now_ms,
            "intervalSeconds": self.interval,
            "lastCycleAt": last_cycle_at,
            "lastError": self.last_error,
            "counts": counts,
            "chains": chain_options(),
            "wallets": wallets,
            "results": self.store.results(results_limit),
            "summary": {
                "running": bool(self._thread and self._thread.is_alive()),
                "minTradeUsd": self.store.min_trade_usd(),
                "alertsEnabled": self.store.alerts_enabled(),
                "wallets": len(wallets),
                "enabledWallets": sum(1 for wallet in wallets if wallet["enabled"]),
                "pending": int(counts.get("pending") or 0),
                "confirmed": int(counts.get("confirmed") or 0),
                "suspected": int(counts.get("suspected") or 0),
                "normal": int(counts.get("normal") or 0),
                "unknown": int(counts.get("unknown") or 0),
                "lastCycleAt": last_cycle_at,
                "intervalSeconds": self.interval,
                "lastError": self.last_error,
            },
        }


def main() -> int:
    """排查用：``--status`` 看状态，``--once`` 跑一轮，``--check <chain> <tx>`` 单笔取证。"""
    import argparse

    parser = argparse.ArgumentParser(description="GMGN 交易被夹检测")
    parser.add_argument("--status", action="store_true", help="打印配置与统计")
    parser.add_argument("--once", action="store_true", help="跑一轮（抓取 + 取证）")
    parser.add_argument("--check", nargs=2, metavar=("CHAIN", "TX"), help="对单笔交易做取证")
    parser.add_argument("--token", default="", help="配合 --check 指定代币合约地址")
    args = parser.parse_args()

    detector = SandwichDetector()
    if args.status:
        print(json.dumps(detector.status(), ensure_ascii=False, indent=2))
        return 0
    if args.once:
        print(json.dumps(detector.run_cycle(), ensure_ascii=False))
        return 0
    if args.check:
        chain, tx = args.check
        activity = {"chain": chain, "tx_hash": tx, "token": args.token, "event_type": "buy"}
        print(json.dumps(check_activity(chain, activity), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
