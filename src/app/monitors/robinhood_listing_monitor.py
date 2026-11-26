#!/usr/bin/env python3
"""Robinhood 上币早期信号监控（**只读**）。

目标：在 Robinhood Chain（chainId 4663）上线某个**非股票**代币之前，从公开痕迹里提前
发现它。只读：不签名、不授权、不交易、不连钱包、不发帖、不需要私钥、不需要 API key
（`GITHUB_TOKEN` 可选，只用来提高 `--backtest` 的速率上限）。

为什么这条路能提前
------------------
Robinhood Chain 的托管方是 BitGo、做市商是 Wintermute。一个币真正上线之前，通常按这个
顺序留下公开痕迹：

1. BitGo 仓库 `BitGo/BitGoJS` 的 PR 里新增 `hoodeth:<symbol>` 代币定义（最早）；
2. 合并进 master、`@bitgo/statics` 发版 / npm 发布；
3. Wintermute 的权限注册合约批准该币的交易池；
4. Wintermute 金库 `approveTokens` 把该币加进 `getAllApprovedTokens()`；
5. Robinhood 上线。

四条信号源（对应 `--no-github` / `--no-chain` / `--no-eth` 可以分别关掉）：

* **github** —— BitGo 仓库的 PR / master / statics tag / npm 包；
* **chain**  —— Robinhood Chain 上 Wintermute 的注册合约、金库、多签；
* **eth**    —— （低置信）以太坊上 Wintermute → Robinhood 地址的转账。

分级
----
* BitGo 单独出现 → `high` 但要人工确认：BitGo 加过 `stonkbroker` / `index` / `up` / `ram` /
  `tendies` / `goose` / `ai` / `pipedog` / `orbio` 9 个币，Robinhood 后来一个都没上。
* Wintermute 批准池子或金库加入 → `high`，很干净（从 0 区块重放，非股票代币只触发过
  VIRTUAL（已上线）、CASHCAT、PONS）。
* **同一个合约既出现在 BitGo、又出现在 Wintermute → `critical`**，这是最可信的组合。
* 名称里带 `• Robinhood` 的是 Robinhood 自己的股票代币 → `low`，而且**不参与 critical 升级**。

用法
----
    python robinhood_listing_monitor.py                  # 默认跑一遍，输出新信号的 JSON 数组
    python robinhood_listing_monitor.py --rebaseline     # 重建基线（不报警）
    python robinhood_listing_monitor.py --no-eth         # 关掉某个源
    python robinhood_listing_monitor.py --backtest       # 回放 cashcat / PONS 的历史

stdout **只输出 JSON 数组**（没有新信号就是 `[]`），日志一律走 stderr —— 方便直接喂给
jq / 告警管道。状态是 JSON 文件，默认 `.runtime-cache/robinhood_listing_state.json`。
（唯一例外：`--backtest` 是**报告模式**，它往 stdout 打一张人看的表。它是手动跑的
诊断命令，不参与管道，所以没有为了「格式纯洁」把表塞进 stderr 让它消失。）

失败语义（重要）
----------------
单个信号源失败只记 stderr，**不推进它的状态**，下次重试；其余源照常输出。绝不允许把
「抓取失败」写成「没有新信号」——这条和项目里「抓取失败不许伪装成空榜」是同一条铁律。

基线也是**按源分开**记的（`state["baseline"]`）：某个源第一轮挂了，只有它下次还要
重建基线，不会因为「另一个源成功了」就被标成「已经建过」——那样它会拿着空白的
`known_tokens` 把全部存量代币当新币报一遍（几百条误报，比漏报更难收拾）。

已知限制（务必先读再信）
------------------------
* **方法在贬值**：原作者说 PONS 之后这套路数已经被很多人学会，提前量在收窄。PONS 的
  「上线」更像 Robinhood 设的抓内鬼局 —— 它至今不在 Robinhood 支持列表里。所以链上信号
  未必等于「一定会开盘」，只代表 Wintermute 侧被批准了。
* **BitGo 单独出现噪音大**：stonkbroker / index / up / ram / tendies / goose / ai /
  pipedog / orbio 9 个币 BitGo 加过，Robinhood 一个都没上。这类只给 `high`，需人工确认。
* **GitHub 腿没有用 git 协议做部分克隆**：提示词建议 `--filter=blob:none` 克隆 + `merge-base`
  对比，本实现改成 `git ls-remote`（一次约 100KB 拿 master / PR head / statics tag）
  加 `raw.githubusercontent` 逐个取两个代币文件。功能等价、不需要 key、不写磁盘缓存，
  代价是「PR 只改了别的文件」时也要把文件拉下来看一眼（每个 PR 约 2 秒）。
  首轮要处理 40 个未处理过的 PR，之后稳态只剩真正的新 PR。
* **四个「疑似交易合约」没有进 getLogs 地址过滤**：真身若是高流量池子，一次查询能吐回
  几十万条日志；而当前没有任何话题用到它们。
* **信号源 4（底部卖压枯竭）没有实现**：它只有 PONS 一个成功案例（8/16、8/18、8/22 触发了
  3 次、此前 18 天 0 误报），买卖比 1.6 这个阈值很可能过拟合，cashcat 上从未达到。
  先在别的币上把误报率测出来再谈，不然它只会变成又一个噪音源。
* **待核实**：Wintermute 在 Arkham / Blockscout 上的直接标签；注册合约里 6 个未标注的允许
  转出地址（其中可能有 Robinhood 自己的充值地址）；Robinhood 在 Robinhood Chain 上的热钱包。
  Wintermute 换合约或多签时，靠 `wintermute_safe_new_target` / `wintermute_new_contract_subscribed`
  发现，人工确认后再把新地址加进常量。
* **`symbol()` / `name()` 是别人合约上的函数**：返回值可能是空、一串 `\\x00` 或控制字符。
  一律过 `_printable()` 清洗；读不到符号时展示短地址（`0x020bfc…18b4`），绝不把脏字符串
  塞进 token 字段。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
ROBINHOOD_CHAIN_ID = 4663
#: 官方 RPC。可用 `ROBINHOOD_RPC_URLS`（逗号分隔）覆盖，失败时按顺序轮换。
ROBINHOOD_RPCS = ("https://rpc.mainnet.chain.robinhood.com",)
EXPLORER = "https://robinhoodchain.blockscout.com"
GMGN_CHAIN_SLUG = "robinhood"

#: 单次 `eth_getLogs` 最多能扫多少块（实测 100000，再多直接 -32602）。
MAX_LOG_SPAN = 100_000

#: 以太坊侧（低置信源）：publicnode / drpc 回测时拒绝大范围 getLogs，只有它稳。
ETH_RPC = "https://rpc.mevblocker.io"

BITGO_REPO = "https://github.com/BitGo/BitGoJS"
BITGO_RAW = "https://raw.githubusercontent.com/BitGo/BitGoJS/{sha}/{path}"
BITGO_TOKEN_FILES = (
    "modules/statics/src/coins/hoodethTokens.ts",
    "modules/statics/src/coins/ofcHoodethTokens.ts",
)
NPM_LATEST = "https://registry.npmjs.org/@bitgo/statics/latest"
NPM_ALL = "https://registry.npmjs.org/@bitgo/statics"
JSDELIVR = "https://cdn.jsdelivr.net/npm/@bitgo/statics@{version}/dist/src/coins/{file}"
GITHUB_API_PULL = "https://api.github.com/repos/BitGo/BitGoJS/pulls/{number}"

#: Wintermute 在 Robinhood Chain 上的合约簇（全部小写）。
WM_SAFE = "0x7d330dd7af628c24e9fc75eb2986b130b2125ad0"          # Gnosis Safe 4/10 多签
WM_PROPOSER = "0x4d9aac01e5f19d5f73f7afeb125032249183e5c0"      # 提交多签交易的地址
WM_REGISTRY = "0x7ff4ed8ab94c849b812622eb462682208be06b93"      # 权限/白名单注册合约
WM_VAULT = "0x51c72848c68a965f66fa7a88855f9f7784502a7f"         # 金库（有 getAllApprovedTokens）
#: 疑似交易合约（提示词标注「待核实」）—— 只当**额外**观察面，命中不单独升级。
WM_SUSPECTS = (
    "0x520ed467860d840ec6ca20f1dacb9c0fa5168aa7",
    "0xc0b6f994c0f0462e2599afc8cd01dcae2625b0df",
    "0xd4f67704d5d230fab774b97ed4b8c947cf3eb947",
    "0x907d1d174569b11624bdcefd20dcef27600237f8",
)
#: 基础币：出现在池子里不报警。
BASE_TOKENS = {
    "0x0000000000000000000000000000000000000000",   # ETH
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",   # WETH
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",   # USDG
}

#: 以太坊侧地址（Etherscan 标签）。
ETH_WINTERMUTE = (
    "0x0000006daea1723962647b7e189d311d757fb793",
    "0xf8191d98ae98d2f7abdfb63a9b0b812b93c873aa",
    "0xdbf5e9c5206d0db70a90108bf936da60221dc080",
)
ETH_ROBINHOOD = (
    "0x40b38765696e3d5d8d9d834d8aad4bb6e418e489",
    "0x2efb50e952580f4ff32d8d2122853432bbf2e204",
    "0x6081258689a75d253d87ce902a8de3887239fe80",
    "0xa26e73c8e9507d50bf808b7a2ca9d5de4fcc4a04",
    "0x841ed663f2636863d40be4ee76243377dff13a34",
    "0x73af3bcf944a6559933396c1577b257e2054d935",
    "0x7c5830cb1a1efe898dcd5cf401165cb952508ccc",
    "0xe0f3e36dbdfa9696b7bcb0d3313522a0bb28d5f4",
    "0x1887fa9edadeab7562b01cc3f4fa246ace2c3cdd",
)

# 选择器
SEL_GET_ALL_APPROVED = "0x09b0d50a"
SEL_APPROVE_TOKENS = "0x3cbe46f8"
SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"
SEL_SYMBOL = "0x95d89b41"
SEL_NAME = "0x06fdde03"

# 事件 topic0
TOPIC_OPERATION_SCHEDULED = "0xbcce059b45002729bd4848bd5d07112ec98e02105cf4763f6c3eb1c59e1b69a6"
TOPIC_ROLE_GRANTED = "0x2f8788117e7eff1d82e926ec794901d17c78024a50270940304540a733656f0d"
TOPIC_ROLE_REVOKED = "0xf6391f5c32d9c69d2a47ea670b442974b53935d1edc7fd64eb21e047a839171b"
TOPIC_SUBSCRIBED = "0x2233e465b01e7944c5d81e387d67020ca9fde7c231920acca0b064c96c023a00"
TOPIC_SAFE_TX = "0x66753cd2356569ee081232e3be8909b950e0a76c1f8460c3a5e3c2be32b11bed"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

#: `OperationScheduled.kind` 的前 4 字节 → 语义。
KIND_V3_POOL = "d50e2949"
KIND_V4_POOL = "5263548a"
KIND_V2_PAIR = "f82ad2ad"
KIND_WITHDRAW_RECIPIENT = "0da11e33"

REMINDER = (
    "仅为早期信号，不是交易建议。任何操作前先做合约与 LP 检查：合约是否开源验证、"
    "能否增发/黑名单/改税/暂停，LP 是否锁定或可被撤走；未验证合约或流动性可撤的项目一律不碰。"
)

#: 信号类型 → 基础严重度。
SEVERITY_BY_TYPE = {
    "bitgo_pr_new_hoodeth_token": "high",
    "bitgo_master_new_hoodeth_token": "high",
    "bitgo_statics_release_includes_new_hoodeth_token": "high",
    "npm_statics_publish_new_hoodeth_token": "high",
    "wintermute_registry_pool_approval_new_token": "high",
    "wintermute_vault_token_approval_new_token": "high",
    "wintermute_getAllApprovedTokens_added": "high",
    "wintermute_new_withdraw_recipient": "medium",
    "wintermute_new_contract_subscribed": "medium",
    "wintermute_safe_new_target": "medium",
    "eth_wintermute_to_robinhood_new_token": "medium",
}
BITGO_TYPES = frozenset(t for t in SEVERITY_BY_TYPE if t.startswith(("bitgo_", "npm_")))
WINTERMUTE_TYPES = frozenset(t for t in SEVERITY_BY_TYPE if t.startswith("wintermute_"))

#: `hoodeth:<sym>` 后面紧跟 name / decimals / 合约地址。编译版和多行 TS 版都能命中：
#: ``erc20Token('uuid', 'hoodeth:cashcat', 'Cash Cat', 18, '0x02…')``
_BITGO_TOKEN_RE = re.compile(
    r"'((?:ofc)?hoodeth:[A-Za-z0-9_.\-]+)'\s*,\s*'([^']*)'\s*,\s*(\d+)\s*,\s*'(0x[0-9a-fA-F]{40})'"
)


def log(message: str) -> None:
    """日志一律走 stderr，stdout 留给 JSON。"""
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# 时间
# --------------------------------------------------------------------------- #
ET = ZoneInfo("America/New_York")


def ms_to_times(ms: int) -> tuple[str, str]:
    """毫秒时间戳 → ``(UTC 字符串, 美东字符串)``。"""
    if not ms or ms <= 0:
        return "", ""
    moment = datetime.fromtimestamp(ms / 1000, timezone.utc)
    local = moment.astimezone(ET)
    return moment.strftime("%Y-%m-%d %H:%M:%S"), local.strftime("%Y-%m-%d %H:%M:%S %Z")


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
# HTTP / RPC
# --------------------------------------------------------------------------- #
_RPC_URLS: list[str] = []
_RPC_CURSOR = 0
_RPC_LOCK = None


def rpc_urls() -> list[str]:
    """要用的 RPC 列表。

    ``ROBINHOOD_RPC_URLS``（逗号分隔）是**覆盖**而不是追加：设了它就只用它列出的端点。
    追加会让「指向自己节点的这次演练/切换」永远轮不到 —— 官方端点排在最前面，正常工作
    时根本走不到后面那几条，想验证「RPC 挂了会怎样」也验不出来。
    """
    global _RPC_URLS
    if not _RPC_URLS:
        extra = [u.strip() for u in os.getenv("ROBINHOOD_RPC_URLS", "").split(",") if u.strip()]
        _RPC_URLS = extra or list(ROBINHOOD_RPCS)
    return _RPC_URLS


class SourceError(RuntimeError):
    """某个信号源这一轮失败。**不推进它的状态**。"""


_TLS = threading.local()


def _thread_session() -> requests.Session:
    """每线程一个 Session（``trust_env=True`` 跟随系统代理，不要改成直连）。

    ``requests.Session`` 不是线程安全的，而 ``fetch_logs`` 是并发的 —— 必须每线程一个，
    否则并发下会互相踩连接池。
    """
    session = getattr(_TLS, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; rh-listing-monitor/1.0)"})
        _TLS.session = session
    return session


def http_get(url: str, *, timeout: float = 25.0, retries: int = 3, headers: dict | None = None) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            response = _thread_session().get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 - 重试后统一抛出
            last = exc
            if attempt + 1 < retries:
                time.sleep(0.6 * (attempt + 1))
    raise SourceError(f"GET {url} 失败：{type(last).__name__}: {str(last)[:160]}")


def rpc(method: str, params: list[Any], *, timeout: float = 40.0) -> Any:
    """调 Robinhood Chain RPC。失败换下一个端点，全失败才抛。"""
    urls = rpc_urls()
    last: Exception | None = None
    for offset in range(len(urls)):
        url = urls[offset % len(urls)]
        try:
            response = _thread_session().post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
        if isinstance(payload, dict) and payload.get("error"):
            raise SourceError(f"{method} 被拒绝：{str(payload['error'])[:180]}")
        return payload.get("result")
    raise SourceError(f"{method} 全部端点失败：{type(last).__name__}: {str(last)[:160]}")


def eth_rpc(method: str, params: list[Any], *, timeout: float = 40.0, retries: int = 2) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            response = _thread_session().post(
                ETH_RPC,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                raise SourceError(str(payload["error"])[:180])
            return payload.get("result")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.0)
    raise SourceError(f"eth {method} 失败：{type(last).__name__}: {str(last)[:160]}")


def fetch_logs(
    addresses: Iterable[str],
    *,
    from_block: int,
    to_block: int,
    topics: list[Any] | None = None,
    chunk: int = MAX_LOG_SPAN,
    workers: int = 6,
    chunk_min: int = 1_000,
) -> list[dict[str, Any]]:
    """分段 + 并发拉 ``eth_getLogs``；某段失败就把它劈半重试。

    节点的硬限制是「一次最多 100000 块」，公开端点在高峰期还会更紧，所以这里**不能
    只按固定步长切**，必须能对失败的那一段自适应缩小区间。
    """
    addresses = [a.lower() for a in addresses if a]
    if from_block > to_block or not addresses:
        return []
    spans = [(lo, min(lo + chunk - 1, to_block)) for lo in range(from_block, to_block + 1, chunk)]
    results: list[dict[str, Any]] = []

    def one(span: tuple[int, int]) -> list[dict[str, Any]]:
        lo, hi = span
        if hi - lo + 1 <= chunk_min:
            params = {
                "fromBlock": hex(lo), "toBlock": hex(hi), "address": addresses,
            }
            if topics:
                params["topics"] = topics
            return rpc("eth_getLogs", [params]) or []
        size = hi - lo + 1
        half = max(chunk_min, size // 2)
        try:
            params = {"fromBlock": hex(lo), "toBlock": hex(hi), "address": addresses}
            if topics:
                params["topics"] = topics
            return rpc("eth_getLogs", [params]) or []
        except SourceError as exc:
            if "spans" not in str(exc) and "limit" not in str(exc).lower():
                # 不是「区间太大」的错，往下劈也可能白劈；先劈一次再放弃。
                pass
            left = one((lo, min(lo + half - 1, hi)))
            right = one((min(lo + half, hi), hi))
            return left + right

    if workers <= 1 or len(spans) <= 1:
        for span in spans:
            results.extend(one(span))
        return results
    with ThreadPoolExecutor(max_workers=min(workers, len(spans))) as pool:
        futures = [pool.submit(one, span) for span in spans]
        for future in as_completed(futures):
            results.extend(future.result())
    return results


def block_at_time(target_s: int, *, head: int | None = None) -> int:
    """二分查到最接近 ``target_s`` 的区块号（Robinhood 出块约 9.8 块/秒）。"""
    top = head if head is not None else int(rpc("eth_blockNumber", []), 16)
    lo, hi = 0, top
    while lo < hi:
        mid = (lo + hi) // 2
        block = rpc("eth_getBlockByNumber", [hex(mid), False])
        if not block:
            hi = mid
            continue
        if int(block["timestamp"], 16) < target_s:
            lo = mid + 1
        else:
            hi = mid
    return lo


# --------------------------------------------------------------------------- #
# ABI 小工具（只做我们用得上的那几种，不引入 web3）
# --------------------------------------------------------------------------- #
def _words(hexdata: str) -> list[str]:
    body = hexdata[2:] if hexdata.startswith("0x") else hexdata
    return [body[i:i + 64] for i in range(0, len(body) - len(body) % 64, 64)]


def word_to_address(word: str) -> str:
    return "0x" + word[-40:].lower()


def word_to_int(word: str) -> int:
    return int(word, 16) if word else 0


def dynamic_bytes_at(hexdata: str, index: int) -> str:
    """取第 ``index`` 个字指向的动态 ``bytes``（返回不带 0x 的十六进制）。"""
    body = hexdata[2:] if hexdata.startswith("0x") else hexdata
    offset = int(body[index * 64:(index + 1) * 64] or "0", 16) * 2
    if offset <= 0 or offset + 64 > len(body):
        return ""
    length = int(body[offset:offset + 64], 16) * 2
    return body[offset + 64:offset + 64 + length]


def decode_address_array(result: str) -> list[str]:
    """解 `address[]` 的返回值（``offset`` → ``length`` → 元素）。"""
    words = _words(result or "0x")
    if len(words) < 2:
        return []
    count = word_to_int(words[1])
    return [word_to_address(w) for w in words[2:2 + count] if w]


def _printable(text: str, limit: int = 48) -> str:
    """把链上读回来的字符串洗成能进弹窗的样子。

    ``symbol()`` 是**别人合约上的函数**，返回值不受我们控制：可能整串 ``\\x00``
    （只返回了 bytes32 的补零）、可能带控制字符、可能几十个字符长。这些脏东西一旦
    进了信号，弹窗标题就是一串豆腐块，而且没法跟榜单/池子做符号匹配。
    """
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if 32 <= ord(ch) < 127 or ord(ch) > 160)
    return cleaned.strip()[:limit]


def short_address(address: str) -> str:
    """``0x020bfc65…1018b4`` —— 没有符号时用它当展示名（完整 42 位会挤爆卡片）。"""
    body = (address or "").strip()
    if len(body) <= 12:
        return body
    return f"{body[:8]}…{body[-4:]}"


def decode_string(result: str) -> str:
    """`symbol()` / `name()`：兼容标准动态 string 和 bytes32 两种历史写法。"""
    body = (result or "")[2:]
    if not body:
        return ""
    if len(body) == 64:
        raw = bytes.fromhex(body).rstrip(b"\x00")
        return _printable(raw.decode("utf-8", "ignore"))
    try:
        offset = int(body[0:64], 16) * 2
        length = int(body[offset:offset + 64], 16) * 2
        raw = bytes.fromhex(body[offset + 64:offset + 64 + length])
        return _printable(raw.decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001
        return ""


def decode_operation_scheduled(log_entry: dict[str, Any]) -> dict[str, Any] | None:
    """解注册合约的 ``OperationScheduled(bytes32 kind, address target, bytes data)``。

    三个参数都在 data 里（没有 indexed）：``kind`` 是第一个字，其前 4 字节是选择器；
    ``target`` 是第二个字；第三个字是动态 bytes 的偏移。
    """
    data = log_entry.get("data") or "0x"
    body = data[2:]
    if len(body) < 192:
        return None
    kind = body[:8].lower()
    target = word_to_address(body[64:128])
    payload = dynamic_bytes_at(data, 2)
    return {
        "kind": kind,
        "target": target,
        "payload": payload,
        "tx": (log_entry.get("transactionHash") or "").lower(),
        "block": int(log_entry.get("blockNumber") or "0x0", 16),
    }


def decode_safe_transaction(log_entry: dict[str, Any]) -> dict[str, Any] | None:
    """解 Safe 的 ``SafeMultiSigTransaction``，取出 ``to`` 和内层 calldata。

    参数顺序：``to, value, data, operation, ...`` —— 三个都在 data 里，所以
    **第 3 个字（下标 2）**才是动态 bytes 的偏移（第 2 个是 value，不要拿错）。
    """
    data = log_entry.get("data") or "0x"
    body = data[2:]
    if len(body) < 192:
        return None
    to = word_to_address(body[24:64])
    calldata = dynamic_bytes_at(data, 2)
    return {
        "to": to,
        "calldata": calldata,
        "tx": (log_entry.get("transactionHash") or "").lower(),
        "block": int(log_entry.get("blockNumber") or "0x0", 16),
    }


def decode_approve_tokens(calldata: str) -> list[tuple[str, str, int]]:
    """解 ``approveTokens((address spender, address token, uint256 amount)[])``。

    返回 ``[(spender, token, amount)]``，**原样保留 amount=0 的条目**（调用方按提示词
    的规则跳过 0：同一个 token 会先 approve 0 再 approve max，前者是重置）。
    """
    body = calldata[8:] if calldata.startswith(SEL_APPROVE_TOKENS[2:]) else calldata
    if not body:
        return []
    try:
        offset = int(body[0:64], 16) * 2
        count = int(body[offset:offset + 64], 16)
    except Exception:  # noqa: BLE001
        return []
    entries: list[tuple[str, str, int]] = []
    for index in range(max(0, min(count, 500))):
        base = offset + 64 + index * 192
        chunk = body[base:base + 192]
        if len(chunk) < 192:
            break
        entries.append((
            word_to_address(chunk[24:64]),
            word_to_address(chunk[64 + 24:128]),
            word_to_int(chunk[128:192]),
        ))
    return entries


def decode_v4_pool_tokens(payload: str) -> list[str]:
    """v4 的 PoolKey：payload 前两个字是 ``currency0`` / ``currency1``。"""
    if len(payload) < 128:
        return []
    return [word_to_address(payload[0:64]), word_to_address(payload[64:128])]


# --------------------------------------------------------------------------- #
# BitGo 代币文件解析
# --------------------------------------------------------------------------- #
def parse_bitgo_tokens(text: str) -> dict[str, dict[str, Any]]:
    """``hoodeth:<sym>`` 定义 → ``{symbol: {name, contract, decimals}}``。

    只认「符号 + 名称 + 精度 + 合约地址」四件套齐全的条目 —— 光有符号名的引用
    （``UnderlyingAsset['hoodeth:cashcat']``）不能算一次新增。
    """
    tokens: dict[str, dict[str, Any]] = {}
    for symbol, name, decimals, contract in _BITGO_TOKEN_RE.findall(text or ""):
        key = symbol.split(":", 1)[1].lower()
        tokens[key] = {
            "symbol": key,
            "asset": symbol,
            "name": name.strip(),
            "contract": contract.lower(),
            "decimals": int(decimals),
        }
    return tokens


def is_stock_token(name: str) -> bool:
    """名称里带 ``• Robinhood`` 的是 Robinhood 自己的股票代币 —— 不是我们要的。"""
    return "robinhood" in (name or "").casefold()


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "baseline_done": False,
        #: 每个源各自是否已经建过基线。**不能只留一个全局标记**：某个源第一轮失败时
        #: 它的 known_tokens 是空的，若被当成「已基线」，下一轮会把全部存量代币当新币报。
        "baseline": {},
        "created_at": now_ms(),
        "updated_at": now_ms(),
        "github": {
            "master_sha": "", "pr_heads": {}, "pr_max": 0,
            "statics_tags": {}, "release_tokens": [],
            "npm_latest": "", "npm_tokens": [],
            "known_tokens": {},
        },
        "chain": {
            "last_block": 0, "known_tokens": {}, "vault_tokens": [], "approved_tokens": [],
            "roles": {}, "subscribers": [], "safe_targets": [], "token_meta": {},
        },
        "eth": {"last_block": 0, "seen_tokens": {}},
        # 按合约记「这一类信号第一次出现的时间」，用来把另一类信号升级成 critical。
        "ledger": {},
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"状态文件损坏：{exc}") from exc
    if not isinstance(data, dict):
        raise SourceError("状态文件不是 JSON 对象")
    state = empty_state()
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key].update(value)
        else:
            state[key] = value
    # 老状态文件只有全局 `baseline_done`，没有按源的 `baseline` 分区。直接当「没建过」
    # 会让下一次运行把全部存量代币重报一遍，所以按「分区里有没有游标」反推。
    if not state.get("baseline"):
        inferred: dict[str, Any] = {}
        for name in ("github", "chain", "eth"):
            section = state.get(name) or {}
            seeded = bool(section.get("master_sha")) if name == "github" else bool(section.get("last_block"))
            if seeded:
                inferred[name] = True
        state["baseline"] = inferred
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_ms()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 信号
# --------------------------------------------------------------------------- #
def make_signal(
    *,
    type_: str,
    token: str,
    contract: str = "",
    token_name: str = "",
    source_url: str = "",
    occurred_ms: int = 0,
    extra: dict[str, Any] | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    time_utc, time_et = ms_to_times(occurred_ms)
    return {
        "type": type_,
        "severity": severity or SEVERITY_BY_TYPE.get(type_, "medium"),
        "chain": f"robinhood({ROBINHOOD_CHAIN_ID})",
        "token": token,
        "token_name": token_name,
        "contract": contract,
        "source_url": source_url,
        "time_utc": time_utc,
        "time_et": time_et,
        "extra": extra or {},
        "reminder": REMINDER,
    }


def memory_signal(
    *, contract: str, family: str, occurred_ms: int, source_url: str, type_: str, extra: dict | None = None
) -> None:
    """往 ledger 里记一笔（family = ``bitgo`` / ``wintermute``），用于 critical 判定。"""
    if not contract:
        return
    entry = _LEDGER.setdefault(contract.lower(), {})
    if family not in entry or (occurred_ms and occurred_ms < int(entry[family].get("occurredAt") or 1 << 62)):
        entry[family] = {
            "occurredAt": int(occurred_ms or 0),
            "type": type_,
            "url": source_url,
        }


_LEDGER: dict[str, dict[str, Any]] = {}


def apply_severity(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按「同类第一次出现」+「BitGo ∩ Wintermute」把严重度定下来。

    * 股票代币（名称带 ``• Robinhood``）恒为 ``low``，**不参与** critical 升级；
    * 同一个合约在 ledger 里同时有 BitGo 类和 Wintermute 类 → ``critical``，
      并把另一类的时间/链接塞进 ``extra.crossSignal``。
    """
    for signal in signals:
        contract = (signal.get("contract") or "").lower()
        if is_stock_token(signal.get("token_name") or ""):
            signal["severity"] = "low"
            continue
        entry = _LEDGER.get(contract) or {}
        families = [f for f in ("bitgo", "wintermute") if f in entry]
        if len(families) == 2:
            other = "wintermute" if signal["type"] in BITGO_TYPES else "bitgo"
            info = entry.get(other) or {}
            signal["severity"] = "critical"
            signal.setdefault("extra", {})["crossSignal"] = {
                "family": other,
                "type": info.get("type", ""),
                "time_utc": ms_to_times(int(info.get("occurredAt") or 0))[0],
                "source_url": info.get("url", ""),
            }
    return signals


# --------------------------------------------------------------------------- #
# 信号源 1：BitGo GitHub / npm
# --------------------------------------------------------------------------- #
def ls_remote(patterns: Iterable[str], *, timeout: float = 180.0) -> dict[str, str]:
    """``git ls-remote`` —— 走 git 协议，不消耗 GitHub REST 配额。"""
    command = ["git", "ls-remote", BITGO_REPO, *patterns]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"git ls-remote 失败：{exc}") from exc
    if completed.returncode != 0:
        raise SourceError(f"git ls-remote 退出码 {completed.returncode}：{completed.stderr[:200]}")
    refs: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            refs[parts[1].strip()] = parts[0].strip()
    return refs


def fetch_bitgo_tokens_at(sha: str, *, timeout: float = 40.0) -> dict[str, dict[str, Any]]:
    """取某个 commit 下的代币定义集合（两个文件合并）。"""
    tokens: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for path in BITGO_TOKEN_FILES:
        url = BITGO_RAW.format(sha=sha, path=path)
        try:
            response = http_get(url, timeout=timeout, retries=2)
        except SourceError as exc:
            errors.append(str(exc)[:120])
            continue
        tokens.update(parse_bitgo_tokens(response.text))
    if not tokens and errors:
        raise SourceError("；".join(errors)[:300])
    return tokens


def fetch_npm_tokens(version: str, *, timeout: float = 40.0) -> dict[str, dict[str, Any]]:
    """取 npm 某个版本的编译产物（cdn.jsdelivr.net），解析代币集合。"""
    tokens: dict[str, dict[str, Any]] = {}
    for name in ("hoodethTokens.js", "ofcHoodethTokens.js"):
        url = JSDELIVR.format(version=version, file=name)
        try:
            response = http_get(url, timeout=timeout, retries=2)
        except SourceError:
            continue
        tokens.update(parse_bitgo_tokens(response.text))
    return tokens


def scan_github(section: dict[str, Any], cfg: "Config", baseline: bool) -> tuple[list[dict], dict]:
    """BitGo PR / master / statics tag / npm 四条腿。任何一个环节失败都抛 SourceError。"""
    state = json.loads(json.dumps(section))  # 深拷贝：失败时绝不污染旧状态
    signals: list[dict[str, Any]] = []
    refs = ls_remote(["refs/heads/master", "refs/pull/*/head", "refs/tags/@bitgo/statics@*"])

    master_sha = refs.get("refs/heads/master", "")
    if not master_sha:
        raise SourceError("ls-refs 里没有 refs/heads/master")
    master_tokens = fetch_bitgo_tokens_at(master_sha)
    known: dict[str, Any] = state.get("known_tokens") or {}

    if not baseline and state.get("master_sha") and master_tokens:
        for symbol, meta in master_tokens.items():
            if symbol not in known:
                signals.append(make_signal(
                    type_="bitgo_master_new_hoodeth_token",
                    token=meta["symbol"].upper(),
                    token_name=meta["name"],
                    contract=meta["contract"],
                    source_url=f"https://github.com/BitGo/BitGoJS/commit/{master_sha}",
                    occurred_ms=now_ms(),
                    extra={"commit": master_sha, "symbol": meta["asset"]},
                ))
                memory_signal(
                    contract=meta["contract"], family="bitgo", occurred_ms=now_ms(),
                    source_url=f"https://github.com/BitGo/BitGoJS/commit/{master_sha}",
                    type_="bitgo_master_new_hoodeth_token",
                )
    for symbol, meta in master_tokens.items():
        known.setdefault(symbol, {**meta, "firstSeenSource": "master", "firstSeenAt": now_ms()})
    state["master_sha"] = master_sha
    state["known_tokens"] = known

    # -- PR ---------------------------------------------------------------- #
    pr_heads: dict[str, str] = {}
    for ref, sha in refs.items():
        if not ref.startswith("refs/pull/") or not ref.endswith("/head"):
            continue
        try:
            number = int(ref.split("/")[2])
        except (IndexError, ValueError):
            continue
        pr_heads[str(number)] = sha
    numbers = sorted(int(n) for n in pr_heads)
    pr_max = numbers[-1] if numbers else int(state.get("pr_max") or 0)
    floor = max(0, pr_max - int(cfg.pr_window))
    old_heads: dict[str, str] = state.get("pr_heads") or {}
    changed = [
        n for n in numbers
        if n >= floor and str(pr_heads[str(n)]) != old_heads.get(str(n))
    ]
    if changed:
        log(f"github: {len(changed)} 个 PR 的 head 变了（取最近 {cfg.pr_window} 个 PR，单次最多处理 {cfg.max_pr_changes} 个）")
    processed: list[int] = []
    batch = changed[: int(cfg.max_pr_changes)]
    # 并发抓：每个 PR 要读 2 个文件，串行 40 个就是 80 个请求 ≈ 80 秒。首轮要处理
    # 40 个存量 PR，之后稳态只剩新 PR。信号仍按 PR 号顺序生成（并发只负责下载）。
    fetched: dict[int, dict[str, Any]] = {}
    if batch:
        with ThreadPoolExecutor(max_workers=min(6, len(batch))) as pool:
            futures = {pool.submit(fetch_bitgo_tokens_at, pr_heads[str(n)]): n for n in batch}
            for future in as_completed(futures):
                number = futures[future]
                try:
                    fetched[number] = future.result()
                except Exception as exc:  # noqa: BLE001 - 单个 PR 失败不能拖垮整轮
                    log(f"github: PR #{number} 取文件失败，跳过（下次重试）：{exc}")
    for number in batch:
        if number not in fetched:
            continue
        pr_tokens = fetched[number]
        sha = pr_heads[str(number)]
        # 只有真读过并落库的 PR 才记 head。基线一轮只处理 40 个，剩下的留到后面几轮，
        # 否则「没看过」会被标成「看过了」，这些 PR 以后永远不会被检查。
        processed.append(number)
        if not pr_tokens:
            continue
        added = {s: m for s, m in pr_tokens.items() if s not in master_tokens}
        for symbol, meta in added.items():
            if baseline:
                known.setdefault(symbol, {**meta, "firstSeenSource": f"pr:{number}", "firstSeenAt": now_ms()})
                continue
            signals.append(make_signal(
                type_="bitgo_pr_new_hoodeth_token",
                token=meta["symbol"].upper(),
                token_name=meta["name"],
                contract=meta["contract"],
                source_url=f"https://github.com/BitGo/BitGoJS/pull/{number}",
                occurred_ms=now_ms(),
                extra={"pr": number, "commit": sha, "symbol": meta["asset"]},
            ))
            memory_signal(
                contract=meta["contract"], family="bitgo", occurred_ms=now_ms(),
                source_url=f"https://github.com/BitGo/BitGoJS/pull/{number}",
                type_="bitgo_pr_new_hoodeth_token",
            )
    for number in processed:
        old_heads[str(number)] = pr_heads[str(number)]
    # 老 PR 的 head 留着（用于下次对比），但只保留窗口内的，免得状态文件无限长。
    state["pr_heads"] = {k: v for k, v in old_heads.items() if int(k) >= floor}
    state["pr_max"] = pr_max

    # -- statics tag -------------------------------------------------------- #
    tags = {
        ref.rsplit("@", 1)[-1]: sha
        for ref, sha in refs.items()
        if ref.startswith("refs/tags/@bitgo/statics@") and not ref.endswith("^{}")
    }
    old_tags: dict[str, str] = state.get("statics_tags") or {}
    new_tags = [v for k, v in tags.items() if k not in old_tags]
    if new_tags and not baseline:
        newest = new_tags[-1]
        try:
            release = fetch_bitgo_tokens_at(newest)
        except SourceError as exc:
            log(f"github: 最新 statics tag 取文件失败，跳过：{exc}")
            release = {}
        seen_release = set(state.get("release_tokens") or [])
        for symbol, meta in release.items():
            if symbol in seen_release or symbol in known:
                continue
            signals.append(make_signal(
                type_="bitgo_statics_release_includes_new_hoodeth_token",
                token=meta["symbol"].upper(),
                token_name=meta["name"],
                contract=meta["contract"],
                source_url=f"https://github.com/BitGo/BitGoJS/tree/{newest}",
                occurred_ms=now_ms(),
                extra={"tag_commit": newest, "symbol": meta["asset"]},
            ))
            memory_signal(
                contract=meta["contract"], family="bitgo", occurred_ms=now_ms(),
                source_url=f"https://github.com/BitGo/BitGoJS/tree/{newest}",
                type_="bitgo_statics_release_includes_new_hoodeth_token",
            )
        state["release_tokens"] = sorted(seen_release | set(release))
    state["statics_tags"] = {**old_tags, **tags}

    # -- npm ---------------------------------------------------------------- #
    try:
        latest = str(http_get(NPM_LATEST, timeout=20).json().get("version") or "")
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"npm registry 读取失败：{exc}") from exc
    if latest and latest != (state.get("npm_latest") or ""):
        npm_tokens = fetch_npm_tokens(latest)
        seen_npm = set(state.get("npm_tokens") or [])
        if not baseline and state.get("npm_latest"):
            for symbol, meta in npm_tokens.items():
                if symbol in seen_npm:
                    continue
                if is_stock_token(meta["name"]):
                    continue
                signals.append(make_signal(
                    type_="npm_statics_publish_new_hoodeth_token",
                    token=meta["symbol"].upper(),
                    token_name=meta["name"],
                    contract=meta["contract"],
                    source_url=f"https://www.npmjs.com/package/@bitgo/statics/v/{latest}",
                    occurred_ms=now_ms(),
                    extra={"version": latest, "symbol": meta["asset"]},
                ))
                memory_signal(
                    contract=meta["contract"], family="bitgo", occurred_ms=now_ms(),
                    source_url=f"https://www.npmjs.com/package/@bitgo/statics/v/{latest}",
                    type_="npm_statics_publish_new_hoodeth_token",
                )
        state["npm_tokens"] = sorted(seen_npm | set(npm_tokens))
        state["npm_latest"] = latest
    return signals, state


# --------------------------------------------------------------------------- #
# 信号源 2：Robinhood Chain 上的 Wintermute
# --------------------------------------------------------------------------- #
def token_meta(section: dict[str, Any], contract: str) -> dict[str, Any]:
    """symbol()/name()，带缓存。取不到就返回空 meta（**不影响**判定）。"""
    cache: dict[str, Any] = section.setdefault("token_meta", {})
    key = contract.lower()
    if key in cache:
        return cache[key]
    meta = {"symbol": "", "name": ""}
    for field_name, selector in (("symbol", SEL_SYMBOL), ("name", SEL_NAME)):
        try:
            result = rpc("eth_call", [{"to": key, "data": selector}, "latest"])
        except SourceError:
            result = None
        if result:
            meta[field_name] = decode_string(result)
    cache[key] = meta
    return meta


def _pool_tokens(entry: dict[str, Any]) -> list[str]:
    """从一条 OperationScheduled 里取出「这个池子涉及的两个代币」（已去掉基础币）。"""
    kind = entry["kind"]
    tokens: list[str] = []
    if kind == KIND_V3_POOL or kind == KIND_V2_PAIR:
        for selector in (SEL_TOKEN0, SEL_TOKEN1):
            try:
                result = rpc("eth_call", [{"to": entry["target"], "data": selector}, "latest"])
            except SourceError:
                continue
            if result and len(result) >= 66:
                tokens.append(word_to_address(result[2:]))
    elif kind == KIND_V4_POOL:
        tokens = decode_v4_pool_tokens(entry["payload"])
    return [t for t in tokens if t and t not in BASE_TOKENS]


def scan_chain(section: dict[str, Any], cfg: "Config", baseline: bool) -> tuple[list[dict], dict]:
    """注册合约 / 金库 / 多签三处的事件 + `getAllApprovedTokens()` 快照。"""
    state = json.loads(json.dumps(section))
    signals: list[dict[str, Any]] = []
    head = int(rpc("eth_blockNumber", []), 16)
    known: dict[str, Any] = state.get("known_tokens") or {}
    vault_tokens = set(x.lower() for x in (state.get("vault_tokens") or []))
    approved = set(x.lower() for x in (state.get("approved_tokens") or []))

    def emit_token(
        *, contract: str, type_: str, tx: str, block: int, extra: dict | None = None
    ) -> None:
        if not contract or contract in BASE_TOKENS:
            return
        if contract in known:
            return
        meta = token_meta(state, contract)
        block_info = rpc("eth_getBlockByNumber", [hex(block), False]) or {}
        occurred = int(block_info.get("timestamp") or "0x0", 16) * 1000
        known[contract] = {
            "symbol": meta.get("symbol") or "",
            "name": meta.get("name") or "",
            "firstSeenAt": occurred or now_ms(),
            "source": type_,
        }
        if baseline:
            return
        if is_stock_token(meta.get("name") or ""):
            # 股票代币不报警（这里连 low 都不发，避免每天几十条）。
            return
        url = f"{EXPLORER}/tx/{tx}" if tx else f"{EXPLORER}/block/{block}"
        symbol = (meta.get("symbol") or "").strip()
        signals.append(make_signal(
            type_=type_,
            # symbol 读不到时退回短地址（`.upper()` 会把 0x 前缀变成 0X，很难看）
            token=symbol.upper() if symbol else short_address(contract),
            token_name=meta.get("name") or "",
            contract=contract,
            source_url=url,
            occurred_ms=occurred,
            extra={"tx": tx, "block": block, **(extra or {})},
        ))
        memory_signal(
            contract=contract, family="wintermute", occurred_ms=occurred,
            source_url=url, type_=type_,
        )

    # -- 增量扫事件 -------------------------------------------------------- #
    from_block = int(state.get("last_block") or 0) + 1
    if not state.get("last_block"):
        from_block = int(cfg.chain_from_block or 0)
        if from_block == 0:
            log(f"chain: 无基线，从创世块扫到 {head}（{head} 块，分段 {MAX_LOG_SPAN}，并发 {cfg.chain_workers}）")
        else:
            log(f"chain: 无基线，从配置的起始块 {from_block} 扫到 {head}")
    # **不要把 WM_SUSPECTS 放进 getLogs 的地址过滤**：那四个是「疑似交易合约」，
    # 真身若是高流量池子，一次查询就能吐回几十万条日志，而当前没有任何话题用到它们。
    logs = fetch_logs(
        [WM_REGISTRY, WM_SAFE, WM_VAULT],
        from_block=from_block,
        to_block=head,
        workers=int(cfg.chain_workers),
    )
    log(f"chain: 扫 {from_block}→{head} 拿到 {len(logs)} 条日志")

    roles: dict[str, Any] = state.get("roles") or {}
    subscribers = set(state.get("subscribers") or [])
    safe_targets = set(x.lower() for x in (state.get("safe_targets") or []))

    for entry in logs:
        topics = entry.get("topics") or []
        topic0 = (topics[0] if topics else "").lower()
        block = int(entry.get("blockNumber") or "0x0", 16)
        tx = (entry.get("transactionHash") or "").lower()
        if topic0 == TOPIC_OPERATION_SCHEDULED:
            decoded = decode_operation_scheduled(entry)
            if not decoded:
                continue
            kind = decoded["kind"]
            if kind in {KIND_V3_POOL, KIND_V4_POOL, KIND_V2_PAIR}:
                for token in _pool_tokens(decoded):
                    emit_token(
                        contract=token,
                        type_="wintermute_registry_pool_approval_new_token",
                        tx=decoded["tx"], block=block,
                        extra={"pool": decoded["target"], "kind": kind},
                    )
            elif kind == KIND_WITHDRAW_RECIPIENT:
                recipient = decoded["target"]
                if recipient in safe_targets or baseline:
                    safe_targets.add(recipient)
                    continue
                safe_targets.add(recipient)
                if not baseline:
                    block_info = rpc("eth_getBlockByNumber", [hex(block), False]) or {}
                    occurred = int(block_info.get("timestamp") or "0x0", 16) * 1000
                    signals.append(make_signal(
                        type_="wintermute_new_withdraw_recipient",
                        token=short_address(recipient),
                        contract="",
                        source_url=f"{EXPLORER}/tx/{tx}",
                        occurred_ms=occurred,
                        extra={"recipient": recipient, "block": block, "tx": tx},
                    ))
        elif topic0 == TOPIC_SAFE_TX:
            decoded = decode_safe_transaction(entry)
            if not decoded:
                continue
            safe_targets.add(decoded["to"])
            if decoded["to"] == WM_VAULT and decoded["calldata"].startswith(SEL_APPROVE_TOKENS[2:]):
                for _spender, token, amount in decode_approve_tokens(decoded["calldata"]):
                    if amount == 0:
                        continue  # 0 是「先重置」，不是真批准
                    emit_token(
                        contract=token,
                        type_="wintermute_vault_token_approval_new_token",
                        tx=decoded["tx"], block=block,
                        extra={"amount": str(amount)},
                    )
        elif topic0 == TOPIC_SUBSCRIBED:
            subscriber = "0x" + (topics[1][-40:] if len(topics) > 1 else "")
            if subscriber in ("0x", "0x" + "0" * 40):
                data = entry.get("data") or "0x"
                subscriber = word_to_address(data[2:66]) if len(data) >= 66 else ""
            if subscriber and subscriber not in subscribers:
                subscribers.add(subscriber)
                if not baseline:
                    block_info = rpc("eth_getBlockByNumber", [hex(block), False]) or {}
                    occurred = int(block_info.get("timestamp") or "0x0", 16) * 1000
                    signals.append(make_signal(
                        type_="wintermute_new_contract_subscribed",
                        token=short_address(subscriber),
                        contract="",
                        source_url=f"{EXPLORER}/tx/{tx}",
                        occurred_ms=occurred,
                        extra={"subscriber": subscriber, "block": block, "tx": tx},
                    ))
        elif topic0 in (TOPIC_ROLE_GRANTED, TOPIC_ROLE_REVOKED):
            if len(topics) >= 3:
                role = topics[1]
                account = word_to_address(topics[2][2:])
                roles[f"{role}:{account}"] = "granted" if topic0 == TOPIC_ROLE_GRANTED else "revoked"

    # -- getAllApprovedTokens 快照对比 ------------------------------------- #
    try:
        snapshot = decode_address_array(rpc("eth_call", [{"to": WM_VAULT, "data": SEL_GET_ALL_APPROVED}, "latest"]))
    except SourceError as exc:
        # 这一条腿失败只跳过它自己：事件扫描已经拿到的东西不能白扔，
        # `approved_tokens` 不推进就等于下次会重新比对。
        log(f"chain: getAllApprovedTokens 读失败，本轮跳过这条腿：{exc}")
        snapshot = []
    for token in snapshot:
        token = token.lower()
        if token in approved:
            continue
        approved.add(token)
        if baseline or token in BASE_TOKENS:
            continue
        emit_token(
            contract=token,
            type_="wintermute_getAllApprovedTokens_added",
            tx="", block=head, extra={"snapshot": True},
        )

    state["known_tokens"] = known
    state["vault_tokens"] = sorted(vault_tokens | set(snapshot))
    state["approved_tokens"] = sorted(approved)
    state["roles"] = roles
    state["subscribers"] = sorted(subscribers)
    state["safe_targets"] = sorted(safe_targets)
    state["last_block"] = head
    return signals, state


# --------------------------------------------------------------------------- #
# 信号源 3：以太坊 Wintermute → Robinhood（低置信）
# --------------------------------------------------------------------------- #
def _topic_for_address(address: str) -> str:
    return "0x" + "0" * 24 + address.lower()[2:]


def scan_eth(section: dict[str, Any], cfg: "Config", baseline: bool) -> tuple[list[dict], dict]:
    state = json.loads(json.dumps(section))
    signals: list[dict[str, Any]] = []
    head = int(eth_rpc("eth_blockNumber", []), 16)
    lookback = int(cfg.eth_lookback_blocks)
    from_block = max(0, head - lookback)
    seen: dict[str, Any] = state.get("seen_tokens") or {}
    found = 0
    for wintermute in ETH_WINTERMUTE:
        for robinhood in ETH_ROBINHOOD:
            try:
                logs = eth_rpc("eth_getLogs", [{
                    "fromBlock": hex(from_block), "toBlock": hex(head),
                    "topics": [TOPIC_TRANSFER, _topic_for_address(wintermute), _topic_for_address(robinhood)],
                }], timeout=60)
            except SourceError as exc:
                log(f"eth: {wintermute[:8]}→{robinhood[:8]} 查询失败，跳过：{exc}")
                continue
            idx = 0
            while idx < len(logs or []):
                entry = logs[idx]
                idx += 1
                token = (entry.get("address") or "").lower()
                if not token or token in seen:
                    continue
                block = int(entry.get("blockNumber") or "0x0", 16)
                found += 1
                seen[token] = {
                    "firstSeenAt": now_ms(),
                    "from": wintermute,
                    "to": robinhood,
                    "block": block,
                }
                if baseline:
                    continue
                signals.append(make_signal(
                    type_="eth_wintermute_to_robinhood_new_token",
                    token=token[:10],
                    contract=token,
                    source_url=f"https://etherscan.io/tx/{(entry.get('transactionHash') or '')}",
                    occurred_ms=now_ms(),
                    extra={
                        "tx": (entry.get("transactionHash") or "").lower(),
                        "block": block,
                        "from": wintermute,
                        "to": robinhood,
                        "note": "低置信：BILL 回测时充值都发生在公告之后",
                    },
                ))
    state["seen_tokens"] = seen
    state["last_block"] = head
    log(f"eth: 扫 {from_block}→{head} 新增 {found} 个代币")
    return signals, state


# --------------------------------------------------------------------------- #
# 配置与主流程
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    state_path: Path = Path(".runtime-cache/robinhood_listing_state.json")
    pr_window: int = 300
    max_pr_changes: int = 40
    chain_workers: int = 6
    chain_from_block: int = 0
    eth_lookback_blocks: int = 216_000
    enable_github: bool = True
    enable_chain: bool = True
    enable_eth: bool = True
    backtest: bool = False
    rebaseline: bool = False
    dry_run: bool = False
    quiet: bool = False


def run(cfg: Config) -> tuple[list[dict], dict[str, str]]:
    """跑一轮。返回 ``(信号, 每个源的结果)``；单源失败只影响它自己。"""
    global _LEDGER
    state = load_state(cfg.state_path)
    _LEDGER = dict(state.get("ledger") or {})
    baselines: dict[str, Any] = state.setdefault("baseline", {})
    if not isinstance(baselines, dict):
        baselines = {}
        state["baseline"] = baselines

    collected: list[dict[str, Any]] = []
    outcome: dict[str, str] = {}

    plan: list[tuple[str, Any]] = []
    if cfg.enable_github:
        plan.append(("github", scan_github))
    if cfg.enable_chain:
        plan.append(("chain", scan_chain))
    if cfg.enable_eth:
        plan.append(("eth", scan_eth))

    if cfg.rebaseline:
        log("本轮按**重建基线**处理：所有源只写状态，不输出信号")

    for name, function in plan:
        started = time.time()
        # 基线**按源**判定：只有这个源自己没建过（或显式 --rebaseline）才走基线模式。
        baseline = bool(cfg.rebaseline) or not baselines.get(name)
        if baseline:
            log(f"{name}: 还没有基线，本轮只写状态、不输出信号")
        try:
            if name == "github":
                signals, section = function(state["github"], cfg, baseline)
            elif name == "chain":
                signals, section = function(state["chain"], cfg, baseline)
            else:
                signals, section = function(state["eth"], cfg, baseline)
        except Exception as exc:  # noqa: BLE001 - 单源失败不能拖垮整轮
            outcome[name] = f"failed: {type(exc).__name__}: {str(exc)[:200]}"
            log(f"{name}: 失败，状态保持不动（基线也不标记）—— {type(exc).__name__}: {str(exc)[:200]}")
            continue
        state[name] = section
        baselines[name] = True
        collected.extend(signals)
        outcome[name] = f"ok ({len(signals)} 条信号, {time.time() - started:.1f}s)"
        log(f"{name}: 完成，{len(signals)} 条信号，{time.time() - started:.1f}s")

    collected = apply_severity(collected)
    state["ledger"] = _LEDGER
    # 全局标记只是给人看的汇总；判定基线一律看 `state["baseline"][源名]`。
    state["baseline_done"] = bool(plan) and all(baselines.get(name) for name, _ in plan)
    if not cfg.dry_run:
        save_state(cfg.state_path, state)
    return collected, outcome


# --------------------------------------------------------------------------- #
# 回测
# --------------------------------------------------------------------------- #
BACKTEST_TARGETS = {
    "CASHCAT": {
        "contract": "0x020bfc650a365f8bb26819deaabf3e21291018b4",
        "listing_utc": "2026-08-06 13:31:00",
        "pr": 9357,
        "commit": "d958209f6d2ded6024ba59cc8181103b4ff5d0df",
    },
    "PONS": {
        "contract": "0x39dbed3a2bd333467115de45665cc57f813c4571",
        "listing_utc": "2026-09-02 13:29:00",
        "pr": 9603,
        "commit": "8ff8cd925a8f8cd401c39c23cdeb2670d375e9f2",
    },
}


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def backtest(cfg: Config) -> int:
    """回放 cashcat / PONS：链上部分精确重算，GitHub 部分只做「合约确实在」的核对。"""
    head = int(rpc("eth_blockNumber", []), 16)
    rows: list[dict[str, Any]] = []
    for token, spec in BACKTEST_TARGETS.items():
        listing = _parse_utc(spec["listing_utc"])
        start = listing - timedelta(hours=96)
        lo = block_at_time(int(start.timestamp()), head=head)
        hi = block_at_time(int((listing + timedelta(hours=1)).timestamp()), head=head)
        log(f"backtest: {token} 回放 {lo}→{hi}（{(hi - lo)} 块）")
        try:
            logs = fetch_logs([WM_REGISTRY, WM_SAFE, WM_VAULT], from_block=lo, to_block=hi, workers=cfg.chain_workers)
        except SourceError as exc:
            log(f"backtest: {token} 拉日志失败：{exc}")
            continue
        first_registry: dict[str, Any] | None = None
        first_vault: dict[str, Any] | None = None
        for entry in logs:
            topics = entry.get("topics") or []
            topic0 = (topics[0] if topics else "").lower()
            block = int(entry.get("blockNumber") or "0x0", 16)
            tx = (entry.get("transactionHash") or "").lower()
            tokens: list[str] = []
            kind = ""
            if topic0 == TOPIC_OPERATION_SCHEDULED:
                decoded = decode_operation_scheduled(entry)
                if not decoded:
                    continue
                kind = decoded["kind"]
                if kind in {KIND_V3_POOL, KIND_V4_POOL, KIND_V2_PAIR}:
                    tokens = _pool_tokens(decoded)
            elif topic0 == TOPIC_SAFE_TX:
                decoded = decode_safe_transaction(entry)
                if not decoded or not decoded["calldata"].startswith(SEL_APPROVE_TOKENS[2:]):
                    continue
                tokens = [
                    t for _s, t, amount in decode_approve_tokens(decoded["calldata"])
                    if amount > 0
                ]
                kind = "vault"
            if spec["contract"] not in tokens:
                continue
            info = rpc("eth_getBlockByNumber", [hex(block), False]) or {}
            when = int(info.get("timestamp") or "0x0", 16) * 1000
            if topic0 == TOPIC_OPERATION_SCHEDULED and first_registry is None:
                first_registry = {"when": when, "tx": tx, "kind": kind, "block": block}
            if topic0 == TOPIC_SAFE_TX and first_vault is None:
                first_vault = {"when": when, "tx": tx, "kind": "vault", "block": block}
        rows.append({
            "token": token,
            "listing": listing,
            "registry": first_registry,
            "vault": first_vault,
        })

    print("")
    print("回测：链上信号的提前量（相对 Robinhood 上线时间）")
    print("-" * 78)
    print(f"{'代币':<10}{'信号':<14}{'时间 (UTC)':<22}{'提前':>10}  交易")
    for row in rows:
        for name, key in (("池子批准", "registry"), ("金库加入", "vault")):
            info = row.get(key)
            if not info:
                print(f"{row['token']:<10}{name:<14}{'未命中':<22}{'-':>10}")
                continue
            when = datetime.fromtimestamp(info["when"] / 1000, timezone.utc)
            lead = (row["listing"] - when).total_seconds() / 3600.0
            print(f"{row['token']:<10}{name:<14}{when.strftime('%Y-%m-%d %H:%M:%S'):<22}{lead:>9.1f}h  {info['tx'][:18]}…")
    print("-" * 78)

    # GitHub 腿：只核对「这个合约在 PR 提交里确实存在」，时间要靠 REST（可能被限流）。
    token_header = os.getenv("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token_header:
        headers["Authorization"] = f"Bearer {token_header}"
    print("")
    print("回测：BitGo 侧")
    print("-" * 78)
    for token, spec in BACKTEST_TARGETS.items():
        try:
            tokens = fetch_bitgo_tokens_at(spec["commit"])
        except SourceError as exc:
            print(f"{token:<10} PR 提交取文件失败：{exc}")
            continue
        hit = tokens.get(token.lower())
        line = f"{token:<10} 提交 {spec['commit'][:10]} 中出现：{'是' if hit else '否'}"
        try:
            response = http_get(GITHUB_API_PULL.format(number=spec["pr"]), timeout=20, retries=1, headers=headers)
            payload = response.json()
            created = payload.get("created_at")
            merged = payload.get("merged_at")
            listing = _parse_utc(spec["listing_utc"])
            if created:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                lead = (listing - created_dt).total_seconds() / 3600.0
                line += f" · PR #{spec['pr']} 创建 {created_dt:%Y-%m-%d %H:%M} UTC（提前 {lead:.1f}h）"
            if merged:
                merged_dt = datetime.fromisoformat(merged.replace("Z", "+00:00"))
                lead = (listing - merged_dt).total_seconds() / 3600.0
                line += f" · 合并 {merged_dt:%Y-%m-%d %H:%M} UTC（提前 {lead:.1f}h）"
        except Exception as exc:  # noqa: BLE001
            line += f" · PR 时间取不到（{type(exc).__name__}），可设 GITHUB_TOKEN 提高配额"
        print(line)
    print("-" * 78)
    print("注：npm 发版提前量未在回测里重算（需要逐版本下载产物比对），正常运行时按版本增量检测。")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Robinhood 上币早期信号监控（只读）。stdout 只输出新信号的 JSON 数组。",
    )
    parser.add_argument("--state", default=str(Config.state_path), help="状态文件路径")
    parser.add_argument("--rebaseline", action="store_true", help="重建基线（本轮不报警）")
    parser.add_argument("--no-github", action="store_true", help="关掉 BitGo GitHub / npm 源")
    parser.add_argument("--no-chain", action="store_true", help="关掉 Robinhood Chain 源")
    parser.add_argument("--no-eth", action="store_true", help="关掉以太坊低置信源")
    parser.add_argument("--backtest", action="store_true", help="回放 cashcat / PONS 的历史")
    parser.add_argument("--pr-window", type=int, default=300, help="只看最近多少个 PR 编号（默认 300）")
    parser.add_argument("--max-pr-changes", type=int, default=40, help="单轮最多处理多少个变化的 PR")
    parser.add_argument("--chain-workers", type=int, default=6, help="getLogs 并发数")
    parser.add_argument("--chain-from-block", type=int, default=0, help="无基线时的起始块（0=创世）")
    parser.add_argument("--eth-lookback", type=int, default=216_000, help="以太坊回看块数（约 30 天）")
    parser.add_argument("--dry-run", action="store_true", help="不写状态文件")
    parser.add_argument("--quiet", action="store_true", help="不往 stderr 打进度")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config(
        state_path=Path(args.state),
        pr_window=args.pr_window,
        max_pr_changes=args.max_pr_changes,
        chain_workers=max(1, args.chain_workers),
        chain_from_block=args.chain_from_block,
        eth_lookback_blocks=args.eth_lookback,
        enable_github=not args.no_github,
        enable_chain=not args.no_chain,
        enable_eth=not args.no_eth,
        backtest=args.backtest,
        rebaseline=args.rebaseline,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )
    if args.quiet:
        global log
        log = lambda message: None  # noqa: E731

    if cfg.backtest:
        return backtest(cfg)

    try:
        signals, outcome = run(cfg)
    except SourceError as exc:
        print(json.dumps([], ensure_ascii=False))
        log(f"致命错误：{exc}")
        return 2

    print(json.dumps(signals, ensure_ascii=False, indent=2))
    for name, status in outcome.items():
        log(f"汇总 {name}: {status}")
    if outcome and all(status.startswith("failed") for status in outcome.values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
