"""链上 Top10 持仓集中度 —— 同时给 **Arkham** 和 **GMGN** 两个口径。

用户 2026-10-21 两条要求：

  「把 AIcoin 热门榜的标的，还有异动提醒，涨幅榜提醒里的标的，再做一个链上 top10
    持仓计算，如果超过了 70%，也在弹窗里提醒」
  「并且把具体的 top10 持仓率显示出来」
  「统计 top10，把两个版本的都做一个统计，一个是 arkham 的数据，一个是 gmgn 的数据」

## 两个口径（用户拍板：「arkham 里的是怎么算的就怎么来」，「两个版本都做」）

**Arkham**：``/token/holders`` 里该链前 10 行的 ``pctOfCap`` 直接相加 —— 也就是
Arkham 网页 Explorer → Token → 「主要持有者」那张表的前十行。

* **交易所热钱包算进去**（用户截图里第 1 名 Gate 4.9%、第 2 名 Bithumb 3.11% 都在表里）；
* **池子 / 销毁地址不剔除**；
* 上游偶尔按流通量算，合计会超过 100%（DOOD 实测 107%）→ **封顶 100**。

**GMGN**：``gmgn-cli token holders`` 的前 10 行 ``amount_percentage`` 直接相加
（0~1 的小数，乘 100）。同样不剔除池子 / 交易所。

### 两个数为什么不一样（实测）

* 单链币两边几乎重合：DOOD 在 Solana 上 Arkham 71.5% / GMGN 71.31%；
* 多链币能差到 5 倍：JCT 在 **BSC** 上 Arkham 11.2% / GMGN 54.2% —— 因为 **分母不同**：
  Arkham 的 ``pctOfCap`` 按**跨链合计的流通供应**算（JCT 全球约 500 亿枚），
  GMGN 的 ``amount_percentage`` 按**这条链上的供应**算（BSC 上 103 亿枚）。
  1,945,200,457 ÷ 10,314,789,721 = 18.86%（GMGN 的 top1），
  1,945,200,457 ÷ 约 500 亿 = 3.89%（Arkham 的 top1）—— 两边各自都对，只是分母不是一回事。

所以弹窗里**两个数并排标源显示**，别把其中一个当成「另一个算错了」。

## 标的解析（为什么不能只拿 symbol 去查）

榜单（AIcoin 热门榜 / 交易所涨幅榜）的行是**交易所交易对**（``doodswapusdt:binance``
``soxlumcblusdt:bitget``），压根没有链上合约，所以必须有一步「symbol → 链上合约」。
两条路：

1. 弹窗自带 ``contractAddress`` + ``chain`` —— 直接用，最可信。
2. 没有合约 → Arkham ``/token/search?query=<symbol>``，再按三道闸筛：
   * **symbol 必须精确相等** —— Arkham 会顺手返回一堆相似币（查 IOST 会带回 GHOST）；
   * **不许是包装 / 锚定 / 代币化股票** —— ``binance-peg-zcash-token``、
     ``omnibridge-bridged-zcash-solana``、``…-tokenized-etf`` 这些的「Top10」是托管方
     和桥，不是「这个币自己的筹码」，算出来必然是 90%+ 的假警报；
   * **价格必须对得上**（弹窗价 ÷ Arkham 价 ∈ [0.5, 2]）—— 拦 symbol 撞车：AIcoin 上的
     ``CL`` 是原油，Arkham 搜出来的第一个 ``cl`` 是 CoinLancer。

三道闸全过才算「就是它」；任一不过就**宁可不显示**，绝不给一个别的币的数字。

挑中哪条**链的部署**（多链币才有这一步）：

1. 弹窗**自带合约** → 就用它（价格异动 / OKX DEX 榜都是这条路，最可信）；
2. 只有 symbol → 对 Arkham 给的每个部署各取一次持币表，**谁的前十名美元合计最大
   就算谁**；全都取不到才退回第一条（Arkham 的主部署）。

判据为什么是「钱」而不是「谁有 GMGN 数据」：桥接版 / 小头部署的供应极小，占比会
虚高得离谱，GMGN 恰好只索引这些地方。实测三例（2026-10-21）：

========  ============================  ================================
币        各部署 top10 美元             结论
========  ============================  ================================
DOOD      BSC 393 万 / Solana 3323 万   Solana，71.2% / 71.2%，两源互证
STRK      ETH 4.02 亿 / Solana 119 万    ETH，69.2%（GMGN 在 Solana 桥接
                                        版给的 65.4% 是假强势）
JCT       ETH 1.85 亿 / BSC 2613 万      ETH，79.3%（11.2% 是 BSC 那条小头）
========  ============================  ================================

（还试过「按监控池里记的合约挑」：池子里的 STRK 是 Solana 桥接版，按它挑会把
69.2% 算成 0.2%，同样被否掉。）

## 铁律

* **抓取失败 ≠ 0%**：拿不到就是 ``status="error"``，弹窗写「暂不可用」，
  绝不把失败读成「筹码分散」（这个项目在交易所限流上踩过一模一样的坑）。
* **不让弹窗等太久**：总预算 ``FETCH_BUDGET_SECONDS``，两个源**并行**抓，
  超时就当「还在查」。Arkham 单请求 1.6–2.4 秒，GMGN 要多起一个 node 进程。
* **只有显式 enable 过才联网**：测试和离线脚本不该因为调了一次弹窗就去打外网。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Iterable

import arkham_client as ARKHAM

#: 「Top10」的 10。
TOP_N = 10
#: 超过这个数就在弹窗里加风险提示（用户 2026-10-21 拍板）。
RISK_THRESHOLD_PCT = 70.0
#: 同一个标的的持币人缓存多久。Arkham 那边单请求 ~2 秒，筹码结构变化也慢。
HOLDERS_TTL_SECONDS = 1800.0
#: symbol → 链上合约的解析结果缓存多久。代币的部署关系几乎不变，缓存久一点。
RESOLVE_TTL_SECONDS = 6 * 3600.0
#: 解析失败 / 查不到时，隔多久再试。别让一个查不到的币每分钟重打一次。
MISS_TTL_SECONDS = 300.0
#: 弹窗路径上最多等多久拿结果（两个源并行，共用一个预算）。
FETCH_BUDGET_SECONDS = 8.0
#: 只有 symbol 时，探「哪条链的部署 GMGN 有持币人数据」最多等多久。
#: 几个部署是**并行**探的，所以这是一整轮的上限，不是每个部署各等一遍。
DEPLOY_PROBE_BUDGET_SECONDS = 6.0
#: 数据源（顺序即弹窗里的展示顺序）。
PROVIDERS: tuple[str, ...] = ("arkham", "gmgn")
SOURCE_LABELS = {"arkham": "Arkham", "gmgn": "GMGN"}

#: gmgn-cli 的入口。直接跑 node 而不是 .cmd 包装（Windows 上 shell 超时杀不掉孙进程）。
GMGN_CLI_INDEX = os.getenv("XINGYUN_GMGN_CLI_INDEX") or (
    r"C:\Users\ZhuanZ1\AppData\Roaming\npm\node_modules\gmgn-cli\dist\index.js"
)
#: Arkham slug → gmgn-cli ``--chain`` 认的别名。认不出就说明 GMGN 不覆盖这条链。
GMGN_CHAIN_SLUGS = {
    "ethereum": "eth", "solana": "sol", "bsc": "bsc",
    "base": "base", "robinhood": "robinhood",
}
GMGN_TIMEOUT_SECONDS = 18.0

#: 包装 / 锚定 / 代币化资产：它们的「Top10」是桥和托管方，不是这个币自己的筹码。
SYNTHETIC_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bwrapped\b", r"\bbridged\b", r"\bomnibridge\b", r"\bwormhole\b",
        r"\bbinance\s*peg\b", r"\bpegged\b", r"\btokenized\b", r"\bdinari\b",
        r"\bxstock\b", r"\bbackpack securities\b",
    )
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def looks_synthetic(pricing_id: Any, name: Any = "") -> bool:
    """这个代币是不是包装 / 锚定 / 代币化股票（这类资产回答不了「筹码集中」）。"""
    hay = re.sub(r"[^a-z0-9]+", " ", f"{pricing_id or ''} {name or ''}".casefold()).strip()
    if not hay:
        return False
    return any(pattern.search(hay) for pattern in SYNTHETIC_PATTERNS)


def top_holders_pct(
    holders: Iterable[dict[str, Any]] | None,
    *,
    chain: Any = "",
    top_n: int = TOP_N,
) -> dict[str, Any]:
    """该链前 ``top_n`` 个持有人的占比之和（百分数）。

    返回 ``{"pct": float | None, "count": int}``。``pct`` 为 ``None`` 表示
    **没有可用数据**（不是 0）—— 调用方必须把这两种情况分开。

    * ``pct`` 上游已经是百分数（``normalize_holders()`` 乘过 100 了）；
    * 只取**被查询那条链**的行：同一个 pricingID 在 Arkham 里可能挂着多条链的持币表
      （USDT 的以太坊表和波场表），混着加会把两条链的筹码拼成一个数；
    * 合计封顶 100：上游偶尔按流通量而非总量算，DOOD 实测 107%。
    """
    rows = [row for row in (holders or []) if isinstance(row, dict)]
    if not rows:
        return {"pct": None, "count": 0, "usd": 0.0}
    slug = ARKHAM.arkham_chain_slug(chain) if chain else ""
    if slug:
        same = [row for row in rows if ARKHAM.arkham_chain_slug(row.get("chain")) == slug]
        if same:
            rows = same
        elif len({ARKHAM.arkham_chain_slug(row.get("chain")) for row in rows}) > 1:
            # 一条链都对不上、而响应里混着好几条链 —— 拼起来必然是错的，宁可不算。
            return {"pct": None, "count": 0, "usd": 0.0}
    rows.sort(key=lambda row: _num(row.get("pct")), reverse=True)
    picked = [row for row in rows[: max(1, int(top_n))] if _num(row.get("pct")) > 0]
    if not picked:
        return {"pct": None, "count": 0, "usd": 0.0}
    total = sum(_num(row.get("pct")) for row in picked)
    # ``usd`` 是前十名的美元合计，只用来判「哪条链才是这个币的真身」
    # （见 ``_choose_deployment``），弹窗不显示它。
    usd = sum(max(0.0, _num(row.get("usd"))) for row in picked)
    return {"pct": round(min(100.0, total), 1), "count": len(picked), "usd": round(usd, 0)}


def gmgn_top_holders(chain: Any, contract: Any) -> dict[str, Any]:
    """GMGN 口径：``gmgn-cli token holders`` 前 10 行 ``amount_percentage`` 之和。

    返回 ``{"pct", "count", "reason", "status"}``；``pct=None`` + ``reason`` 表示
    拿不到（**不是 0%**）。``status`` 区分两件事，弹窗文案靠它：

    * ``error`` —— 抓失败了（退出码非 0 / 超时 / 429 限流 / 不是 JSON）；
    * ``empty`` —— 抓到了，但这只币没有持币人数据。

    GMGN 的 ``amount_percentage`` 是 **0~1 的小数**（0.1886 = 18.86%），分母是
    **这条链上的代币供应** —— 这也是它和 Arkham 在跨链币上差好几倍的原因（见模块
    docstring）。排序由 gmgn-cli 的 ``--order-by amount_percentage`` 保证，这里再排
    一次只是为了不依赖上游行为。
    """
    slug = GMGN_CHAIN_SLUGS.get(ARKHAM.arkham_chain_slug(chain), "")
    address = str(contract or "").strip()
    if not slug:
        return {"status": "empty", "pct": None, "count": 0, "reason": f"GMGN 不覆盖该链：{chain}"}
    if not address:
        return {"status": "empty", "pct": None, "count": 0, "reason": "缺少合约地址"}
    if not os.path.exists(GMGN_CLI_INDEX):
        return {"status": "error", "pct": None, "count": 0, "reason": "本机没有安装 gmgn-cli"}
    node = shutil.which("node") or "node"
    command = [
        node, GMGN_CLI_INDEX, "token", "holders",
        "--chain", slug, "--address", address,
        "--limit", str(max(TOP_N, 20)),
        "--order-by", "amount_percentage", "--direction", "desc", "--raw",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=GMGN_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "pct": None, "count": 0, "reason": "GMGN 查询超时"}
    except (OSError, ValueError) as exc:
        return {"status": "error", "pct": None, "count": 0,
                "reason": f"GMGN 调用失败：{type(exc).__name__}"}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return {"status": "error", "pct": None, "count": 0,
                "reason": f"GMGN 退出码 {completed.returncode}：{(detail[0] if detail else '')[:80]}"}
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        return {"status": "error", "pct": None, "count": 0, "reason": "GMGN 返回的不是 JSON"}
    rows = payload.get("list") if isinstance(payload, dict) else payload
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    if not rows:
        return {"status": "empty", "pct": None, "count": 0, "reason": "GMGN 没有该标的的持币人数据"}
    pcts = sorted(
        (_num(row.get("amount_percentage")) * 100.0 for row in rows), reverse=True
    )
    picked = [pct for pct in pcts[:TOP_N] if pct > 0]
    if not picked:
        return {"status": "empty", "pct": None, "count": 0, "reason": "GMGN 没有可用的持仓占比"}
    return {"status": "ok", "pct": round(min(100.0, sum(picked)), 1), "count": len(picked), "reason": ""}


def pick_search_hit(
    rows: Iterable[dict[str, Any]] | None,
    symbol: Any,
    *,
    price: float = 0.0,
) -> dict[str, Any]:
    """从 Arkham ``/token/search`` 的结果里挑出「就是这一个币」的那条。

    三道闸见模块 docstring。返回里同时带上这只币的**全部部署**，具体算哪条链由
    :meth:`Top10Holders._choose_deployment` 决定。

    返回 ``{"chain","contract","pricingId","symbol","name"}``，挑不出返回 ``{}``。
    """
    want = str(symbol or "").strip().casefold()
    if not want:
        return {}
    candidates: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").strip().casefold() != want:
            continue
        ident = row.get("identifier") if isinstance(row.get("identifier"), dict) else {}
        pricing_id = str(ident.get("pricingID") or "").strip()
        name = str(row.get("name") or "").strip()
        if looks_synthetic(pricing_id, name):
            continue
        if price > 0:
            upstream = _num(row.get("price"))
            # 价格对不上 2 倍以上，基本就是同名不同币（AIcoin 的 CL 是原油）。
            if upstream <= 0 or not (0.5 <= price / upstream <= 2.0):
                continue
        deployments: list[dict[str, str]] = []
        for deployment in row.get("deployments") or []:
            if not isinstance(deployment, dict):
                continue
            slug = ARKHAM.arkham_chain_slug(deployment.get("chain"))
            contract = str(deployment.get("address") or "").strip()
            if not slug or not contract:
                continue
            if not ARKHAM.address_matches_chain(slug, contract):
                continue
            deployments.append({"chain": slug, "contract": contract})
        if not deployments:
            continue
        candidates.append({
            "pricingId": pricing_id,
            "symbol": str(row.get("symbol") or "").strip(),
            "name": name,
            "deployments": deployments,
        })
    if not candidates:
        return {}
    first = candidates[0]
    # 把整张部署表带出去：同一个币在几条链都有合约时，「算哪条链」要看下面
    # ``_choose_deployment``（GMGN 有数据的那个部署才算数）。
    return {**first, **first["deployments"][0]}


def source_text(
    provider: str,
    entry: dict[str, Any] | None,
    *,
    threshold: float = RISK_THRESHOLD_PCT,
) -> str:
    """单个口径的展示片段：``Arkham 63.4%`` / ``GMGN 78.2%``；没数返回空串。"""
    if not isinstance(entry, dict) or str(entry.get("status") or "") != "ok":
        return ""
    pct = _num(entry.get("pct"), -1.0)
    if pct < 0:
        return ""
    return f"{SOURCE_LABELS.get(provider, provider)} {pct:.1f}%"


#: 弹窗里标链用的短名。``arkham_client.chain_label`` 给的是全称（Ethereum），
#: 挤在「类型 · 链」那一行太长，这里单独收一份短的。
SHORT_CHAIN_LABELS = {
    "ethereum": "ETH", "solana": "Solana", "bsc": "BSC", "base": "Base",
    "robinhood": "Robinhood", "arbitrum": "Arbitrum", "polygon": "Polygon",
    "tron": "Tron",
}


def chain_suffix(entry: dict[str, Any] | None) -> str:
    """数字后面那个「（ETH）」—— 同名的另一个部署可能差出好几倍，标上链才读得懂。"""
    if not isinstance(entry, dict):
        return ""
    slug = ARKHAM.arkham_chain_slug(entry.get("chain"))
    if not slug:
        return ""
    label = SHORT_CHAIN_LABELS.get(slug) or ARKHAM.chain_label(slug) or slug.upper()
    return f"（{label}）"


def body_text(entry: dict[str, Any] | None, *, threshold: float = RISK_THRESHOLD_PCT) -> str:
    """弹窗正文里那句「Top10 持仓 …」；没什么可说的时候返回空串。

    两个口径**并排标源**显示（用户 2026-10-21：「两个版本都做一个统计」）。
    **失败绝不写成 0%** —— 「查不到」和「筹码分散」是两个相反的结论。
    """
    if not isinstance(entry, dict):
        return ""
    sources = entry.get("sources") if isinstance(entry.get("sources"), dict) else {}
    if not sources:
        return ""
    parts: list[str] = []
    crossed: list[str] = []
    available = 0
    for provider in PROVIDERS:
        source = sources.get(provider) or {}
        text = source_text(provider, source, threshold=threshold)
        if text:
            parts.append(text)
            available += 1
            if _num(source.get("pct")) >= threshold - 1e-9:
                crossed.append(provider)
    if parts:
        head = "Top10 持仓 " + " · ".join(parts) + chain_suffix(entry)
        if not crossed:
            return head
        if len(crossed) >= available:
            return head + f" · 筹码高度集中（≥{threshold:g}%）"
        # 只有一个源超线时必须点名，否则旁边那个更低的数会让这句话看着像自相矛盾。
        return head + (
            f" · 筹码高度集中（≥{threshold:g}%，按 {SOURCE_LABELS.get(crossed[0], crossed[0])}）"
        )
    statuses = {str((sources.get(p) or {}).get("status") or "") for p in PROVIDERS}
    if "error" in statuses:
        return "Top10 持仓 暂不可用"
    if "empty" in statuses:
        return "Top10 持仓 未收录"
    return ""


def combine(
    arkham: dict[str, Any] | None,
    gmgn: dict[str, Any] | None,
    *,
    chain: str = "",
    contract: str = "",
    pricing_id: str = "",
) -> dict[str, Any]:
    """把两个源的结果合成一条记录（``sources`` 里各留一份原始结论）。"""
    sources = {
        "arkham": arkham or {"status": "pending", "pct": None, "count": 0},
        "gmgn": gmgn or {"status": "pending", "pct": None, "count": 0},
    }
    statuses = [str(sources[p].get("status") or "") for p in PROVIDERS]
    if "ok" in statuses:
        status = "ok"
    elif "pending" in statuses:
        status = "pending"
    elif "error" in statuses:
        status = "error"
    elif "empty" in statuses:
        status = "empty"
    elif "off" in statuses:
        status = "off"
    else:
        status = "unresolved"
    arkham_pct = _num(sources["arkham"].get("pct"), -1.0)
    return {
        "status": status,
        # 主口径沿用 Arkham（页面/旧字段都按它读），两个源的真值在 sources 里。
        "pct": arkham_pct if arkham_pct >= 0 else None,
        "count": int(_num(sources["arkham"].get("count"), 0)),
        "sources": sources,
        "chain": chain,
        "contract": contract,
        "pricingId": pricing_id,
        "at": int(time.time() * 1000),
    }


class Top10Holders:
    """带 TTL 缓存 + 单飞 + 预算等待的双口径 Top10 查询器。

    ``enable()`` 之前**一次网络请求都不会发**：测试和离线脚本会调弹窗，
    不该因此去打 Arkham / 起 node 进程。服务在启动时统一 enable
    （见 ``start_top10_holders``）。
    """

    def __init__(
        self,
        *,
        fetcher: Callable[..., dict[str, Any]] | None = None,
        searcher: Callable[..., list[dict[str, Any]]] | None = None,
        gmgn_fetcher: Callable[..., dict[str, Any]] | None = None,
        budget: float = FETCH_BUDGET_SECONDS,
    ) -> None:
        self._fetchers: dict[str, Callable[..., dict[str, Any]]] = {
            "arkham": fetcher or self._fetch_arkham,
            "gmgn": gmgn_fetcher or gmgn_top_holders,
        }
        self._search = searcher or ARKHAM.search_tokens
        self.budget = max(0.0, float(budget))
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._resolved: dict[str, tuple[float, dict[str, Any]]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._enabled = False

    # ---- 开关 ---------------------------------------------------------
    def enable(self, value: bool = True) -> None:
        with self._lock:
            self._enabled = value

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    # ---- 缓存 ---------------------------------------------------------
    def _read(self, table: dict[str, tuple[float, dict[str, Any]]], key: str) -> dict[str, Any] | None:
        with self._lock:
            hit = table.get(key)
        if not hit:
            return None
        expires_at, entry = hit
        if expires_at and expires_at <= time.time():
            return None
        return dict(entry)

    def _write(
        self,
        table: dict[str, tuple[float, dict[str, Any]]],
        key: str,
        entry: dict[str, Any],
        ttl: float,
    ) -> None:
        with self._lock:
            table[key] = (time.time() + max(1.0, float(ttl)), dict(entry))

    def cached(self, chain: Any, contract: Any) -> dict[str, Any] | None:
        """只读缓存（不联网、不等待），给页面 / 其它板块复用。"""
        key = self._token_key(chain, contract)
        if not key:
            return None
        return combine(
            self._read(self._entries, self._cache_key("arkham", key)),
            self._read(self._entries, self._cache_key("gmgn", key)),
            chain=ARKHAM.arkham_chain_slug(chain),
            contract=str(contract or "").strip(),
        )

    @staticmethod
    def _token_key(chain: Any, contract: Any) -> str:
        slug = ARKHAM.arkham_chain_slug(chain)
        address = str(contract or "").strip()
        if not slug or not address:
            return ""
        return f"{slug}:{address.casefold()}"

    @staticmethod
    def _cache_key(provider: str, token_key: str) -> str:
        return f"{provider}|{token_key}"

    # ---- symbol → 链上合约 --------------------------------------------
    def resolve(
        self,
        symbol: Any,
        *,
        price: float = 0.0,
        budget: float | None = None,
    ) -> dict[str, Any]:
        """按 symbol 反查链上合约（带缓存 + 单飞）。查不到返回 ``{}``。"""
        text = str(symbol or "").strip()
        if not text:
            return {}
        key = f"search:{text.casefold()}"
        hit = self._read(self._resolved, key)
        if hit is not None:
            return hit.get("identity") or {}
        if not self.enabled:
            return {}
        event, owner = self._claim(key)
        if owner:
            threading.Thread(
                target=self._run_resolve,
                args=(key, text, price, event),
                name=f"top10-resolve-{text[:12]}",
                daemon=True,
            ).start()
        event.wait(self.budget if budget is None else max(0.0, budget))
        hit = self._read(self._resolved, key)
        return (hit or {}).get("identity") or {}

    def _run_resolve(
        self,
        key: str,
        symbol: str,
        price: float,
        event: threading.Event,
    ) -> None:
        identity: dict[str, Any] = {}
        ttl = MISS_TTL_SECONDS
        try:
            rows = self._search(symbol)
            identity = pick_search_hit(rows, symbol, price=price)
            if identity:
                ttl = RESOLVE_TTL_SECONDS
        except Exception:
            # 口令被轮换 / 网络异常都只算「这一轮查不到」，别把结论缓半天。
            ttl = MISS_TTL_SECONDS
        finally:
            self._write(self._resolved, key, {"identity": identity, "at": int(time.time() * 1000)}, ttl)
            self._release(key, event)

    # ---- 单飞 ---------------------------------------------------------
    def _claim(self, key: str) -> tuple[threading.Event, bool]:
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                return existing, False
            event = threading.Event()
            self._inflight[key] = event
            return event, True

    def _release(self, key: str, event: threading.Event) -> None:
        with self._lock:
            if self._inflight.get(key) is event:
                self._inflight.pop(key, None)
        event.set()

    # ---- 挑哪条链的部署 ------------------------------------------------
    def _choose_deployment(
        self,
        resolved: dict[str, Any],
        *,
        budget: float | None = None,
    ) -> dict[str, Any]:
        """同一个 symbol 在几条链都有合约时，挑一条真要算的。

        判据是 **Arkham 前十名的美元合计，谁大算谁** —— 钱在哪条链，那条就是对
        这个币有意义的那条（2026-10-21 实测三例，见模块 docstring）：

        ========  ==========================  ==========================
        币        各部署 top10 美元            选中的
        ========  ==========================  ==========================
        DOOD      BSC 393 万 / Solana 3323 万  Solana（71.2%）
        STRK      ETH 4.02 亿 / Solana 119 万   ETH（69.2%）
        JCT       ETH 1.85 亿 / BSC 2613 万     ETH（79.3%）
        ========  ==========================  ==========================

        也试过「谁有 GMGN 数据就选谁」，被 STRK 否掉：GMGN 只在 Solana 桥接版
        有数据（65.4%），而 Arkham 那边只有 0.2% —— 桥接版的供应是小头，两个
        源反而互相打脸。按金额选则三条链一次全对。

        次要好处：GMGN 只为**选中的那一条**起一次 node。之前两个部署并行探
        GMGN，实测必有一路吃 ``429 RATE``，撞了就只能退回主部署。

        ``deployments`` 是 ``pick_search_hit`` 带出来的整张表；只有一条时**不
        探**，省掉一次 Arkham 请求。全都拿不到数就退回 ``deployments[0]``。
        """
        deployments = [
            {"chain": str(row.get("chain") or ""), "contract": str(row.get("contract") or "")}
            for row in (resolved.get("deployments") or [])
            if isinstance(row, dict) and row.get("chain") and row.get("contract")
        ]
        if not deployments:
            return {
                "chain": str(resolved.get("chain") or ""),
                "contract": str(resolved.get("contract") or ""),
            }
        if len(deployments) == 1:
            return deployments[0]
        wait = DEPLOY_PROBE_BUDGET_SECONDS if budget is None else max(0.0, float(budget))
        deadline = time.monotonic() + min(wait, self.budget)
        # 各部署**并行**探：Arkham 是签名直读、并发不吃限流（GMGN 才是按 key 限的），
        # 串行会让弹窗多等一整个往返。两条都回来再比大小，顺序不影响结论。
        probes: list[tuple[dict[str, str], str]] = []
        for deployment in deployments:
            key = self._cache_key("arkham", self._token_key(deployment["chain"], deployment["contract"]))
            if not key:
                continue
            probes.append((deployment, key))
            if self._read(self._entries, key) is None:
                self._kick("arkham", key, deployment["chain"], deployment["contract"], "")
        for _deployment, key in probes:
            if self._read(self._entries, key) is not None:
                continue
            with self._lock:
                event = self._inflight.get(key)
            if event is not None:
                event.wait(max(0.0, deadline - time.monotonic()))
        best: dict[str, Any] = {}
        best_key: tuple[float, float] = (-1.0, -1.0)
        for deployment, key in probes:
            entry = self._read(self._entries, key) or {}
            if str(entry.get("status") or "") != "ok":
                continue
            # 美元是主判据；上游偶尔不给 usd（返回全 0）时退回按占比挑。
            score = (_num(entry.get("usd")), _num(entry.get("pct")))
            if score > best_key:
                best_key = score
                best = deployment
        return best or deployments[0]

    # ---- 查询 ---------------------------------------------------------
    def lookup(
        self,
        chain: Any = "",
        contract: Any = "",
        *,
        symbol: Any = "",
        price: float = 0.0,
        budget: float | None = None,
    ) -> dict[str, Any]:
        """返回 ``{"status","pct","count","sources","chain","contract","pricingId"}``。

        ``sources[provider]`` 里每个源各一份 ``{"status","pct","count","reason"}``：

        * ``ok``      —— 有数（``pct`` 是百分数）
        * ``empty``   —— 收录了这只币但没有持币人数据
        * ``error``   —— 抓取失败（**不是 0%**）
        * ``pending`` —— 还在查，本次没等到
        """
        ident_chain = ARKHAM.arkham_chain_slug(chain)
        ident_contract = str(contract or "").strip()
        pricing_id = ""
        if not ident_chain or not ident_contract:
            # 榜单行往往只有 symbol（AIcoin 热门榜、涨幅榜），先反查链上合约。
            resolved = self.resolve(symbol, price=price, budget=budget)
            if not resolved:
                return combine(
                    {"status": "unresolved", "pct": None, "count": 0, "reason": "算不出链上合约"},
                    {"status": "unresolved", "pct": None, "count": 0, "reason": "算不出链上合约"},
                )
            # 同名币可能躺在几条链上，算哪条由 GMGN 有没有数据决定。
            deployment = self._choose_deployment(resolved, budget=budget)
            ident_chain = deployment["chain"]
            ident_contract = deployment["contract"]
            pricing_id = str(resolved.get("pricingId") or "")
        token_key = self._token_key(ident_chain, ident_contract)
        if not token_key:
            return combine(
                {"status": "unresolved", "pct": None, "count": 0, "reason": "链或合约不可用"},
                {"status": "unresolved", "pct": None, "count": 0, "reason": "链或合约不可用"},
            )
        if not self.enabled:
            return combine(
                {"status": "off", "pct": None, "count": 0, "reason": "Top10 查询未启用"},
                {"status": "off", "pct": None, "count": 0, "reason": "Top10 查询未启用"},
                chain=ident_chain, contract=ident_contract, pricing_id=pricing_id,
            )
        deadline = time.monotonic() + (self.budget if budget is None else max(0.0, budget))
        keys = {provider: self._cache_key(provider, token_key) for provider in PROVIDERS}
        # 两个源并行抓：单飞键按源分开，谁先回来谁先落缓存。
        for provider in PROVIDERS:
            if self._read(self._entries, keys[provider]) is None:
                self._kick(provider, keys[provider], ident_chain, ident_contract, pricing_id)
        results: dict[str, dict[str, Any]] = {}
        for provider in PROVIDERS:
            entry = self._read(self._entries, keys[provider])
            if entry is None:
                with self._lock:
                    event = self._inflight.get(keys[provider])
                if event is not None:
                    event.wait(max(0.0, deadline - time.monotonic()))
                entry = self._read(self._entries, keys[provider])
            results[provider] = entry or {
                "status": "pending", "pct": None, "count": 0,
                "reason": "仍在查询", "chain": ident_chain, "contract": ident_contract,
            }
        return combine(
            results["arkham"], results["gmgn"],
            chain=ident_chain, contract=ident_contract, pricing_id=pricing_id,
        )

    def _kick(
        self, provider: str, key: str, chain: str, contract: str, pricing_id: str
    ) -> None:
        event, owner = self._claim(key)
        if not owner:
            return
        threading.Thread(
            target=self._run_source,
            args=(provider, key, chain, contract, pricing_id, event),
            name=f"top10-{provider}-{chain}",
            daemon=True,
        ).start()

    def _run_source(
        self,
        provider: str,
        key: str,
        chain: str,
        contract: str,
        pricing_id: str,
        event: threading.Event,
    ) -> None:
        entry: dict[str, Any]
        ttl = HOLDERS_TTL_SECONDS
        try:
            result = self._fetchers[provider](chain, contract)
        except ARKHAM.ArkhamAuthError as exc:
            entry = {"status": "error", "pct": None, "count": 0,
                     "reason": f"Arkham 网页端口令可能已轮换：{exc}"[:160]}
            ttl = MISS_TTL_SECONDS
        except Exception as exc:  # 网络 / 解析异常一律当失败，不当作 0
            entry = {"status": "error", "pct": None, "count": 0,
                     "reason": f"{type(exc).__name__}: {exc}"[:160]}
            ttl = MISS_TTL_SECONDS
        else:
            payload = result if isinstance(result, dict) else {}
            pct = payload.get("pct")
            usd = 0.0
            if pct is None and "holders" in payload:
                calc = top_holders_pct(payload.get("holders"), chain=chain)
                pct = calc["pct"]
                count = calc["count"]
                usd = _num(calc.get("usd"))
            else:
                count = int(_num(payload.get("count"), 0))
                usd = _num(payload.get("usd"))
            if provider == "arkham" and isinstance(payload.get("identity"), dict):
                pricing_id = pricing_id or str(payload["identity"].get("pricingId") or "")
            if pct is None:
                # 源自己说了「失败」就别写成「未收录」：429 限流和「这币没人持有」
                # 是两个相反的结论，弹窗上一个写「暂不可用」、一个写「未收录」。
                hinted = str(payload.get("status") or "").strip()
                entry = {
                    "status": "error" if hinted == "error" else "empty",
                    "pct": None, "count": 0,
                    "reason": str(payload.get("reason") or "没有该标的的持币人数据")[:160],
                }
                ttl = MISS_TTL_SECONDS
            else:
                entry = {"status": "ok", "pct": _num(pct), "count": count,
                         "usd": usd, "reason": ""}
        entry.update({
            "chain": chain,
            "contract": contract,
            "pricingId": pricing_id,
            "at": int(time.time() * 1000),
        })
        try:
            self._write(self._entries, key, entry, ttl)
        finally:
            self._release(key, event)

    @staticmethod
    def _fetch_arkham(chain: str, contract: str) -> dict[str, Any]:
        return ARKHAM.fetch_token_holders(chain, contract)
