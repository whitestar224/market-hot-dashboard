"""地址集群监控：用 Arkham 的实体归因，盯「同一批人在多个标的里同时占大头」。

## 口径（用户 2026-10-21 拍板）

* **集群粒度** —— 「实体归因 + 大额散地址」：
  Arkham 认得出是谁的，多个地址合成一个**实体集群**（``entity:<id>``，
  比如某基金 / 某个 KOL / 某个 DAO）；认不出名字但单地址占流通够大的，
  按**地址集群**（``addr:<链>:<地址>``）单独存。
  这样 JCT 那种「前 5 个 Gnosis Safe 合计 79%、但都没名字」的匿名控盘不会漏掉。
* **提醒门槛** —— 同一个集群在 **≥2 个候选标的**里都占流通 **≥5%**。
  只要求「跨标的」会把大型基金 / 托管钱包的日常分散持仓全算进来，噪音太大；
  加上 5% 这一层才叫「真控得住」。
* **位置** —— 监控页独立子板块。

## 候选标的

**只做榜上的标的**：行情榜各卡里当前显示的行 + 涨幅榜（2026-10-22 起，价格异动
不再算候选；**GMGN 战壕新币榜那类「新币流」也不算榜** —— 见 server 的
``ARKHAM_CLUSTER_BOARD_SKIP_SOURCES``）。只取**有链上合约**的 —— 没有合约的交易所
标的没有持币人可查。由 server 通过
:meth:`ArkhamClusterMonitor.set_candidate_provider` 注入 —— 榜单结构只有
server 知道，这里不重复实现。

**榜外标的不提醒**（用户 2026-10-22：「不是榜上的标的，不做这个地址集群提醒」）：
库里的持仓是历史累积的，标的掉榜之后那些行会一直留着。上线首日就踩到 ——
``SHIB 6.5%``（6 小时前扫的，早就掉榜）配 ``STA 6.1%``（榜上）拼成一条
「跨 2 标的」提醒，看着像有人同时布局两个币，其实其中一个根本不在看板上。
所以跨标的判定只数**当前这一轮榜上的标的**（:meth:`board_scope`），
集合之外的持仓再大也不参与「跨 ≥N 个标的」的计数。

## 铁律

* **不重判别人已经判过的东西**：控盘/市值/Alpha 身份的唯一实现在
  ``alpha_mm_monitor``，这里只做「谁在持有、谁在多个标的里同时占大头」。
* **抓取失败 ≠ 没有集群**：``ArkhamAuthError``（网页口令被轮换）必须往上传，
  板块显示「接口不可用」；单个标的失败只记在该标的上，不许把它的旧持仓清零。
* **venue 不是控盘方**：交易所 / DEX / 托管 / 跨链桥 / 稳定币发行方持有代币是业务
  需要，算进「控盘」会天天误报，一律剔除（见 :data:`VENUE_ENTITY_TYPES`）。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import re
import sqlite3
import sys
import threading
import time
from typing import Any, Callable

import arkham_client as ARKHAM
from market_maker_monitor import symbol_key

#: 单个标的里，一个集群要占到流通多少才算「这一手有分量」。
CLUSTER_SIGNIFICANT_PCT = 5.0
#: 至少在几个候选标的都有分量，才算「跨标的」。
CLUSTER_CROSS_MIN_TOKENS = 2
#: 没有实体归因的散地址，单地址占比到这个数才值得入库（否则表会爆）。
ADDRESS_CLUSTER_MIN_PCT = 1.0
#: 有实体归因的集群，占比到这个数就入库（实体本来就少，门槛可以低）。
ENTITY_CLUSTER_MIN_PCT = 0.5
#: 同一个集群的弹窗静默窗口。
CLUSTER_ALERT_COOLDOWN_SECONDS = 6 * 3600
#: 一轮多久。持币人变化慢，10 分钟足够。
DEFAULT_CLUSTER_INTERVAL = 600.0
#: 同一个标的的持币人缓存多久（Arkham 那边单请求 ~2 秒，别每轮都重打）。
HOLDERS_TTL_SECONDS = 900.0
#: 一轮最多查几个标的 —— 按 0.3 秒/请求算，40 个约 80 秒，不压垮网络也不拖慢服务。
MAX_CANDIDATES = 40
#: 板块快照缓存（做市商那边直读库被写事务饿到 2–47 秒，这里照抄快照方案）。
PAYLOAD_CACHE_SECONDS = 30.0
#: 每轮最多弹几个跨标的提醒（防「一上线全库都是新集群」时刷屏）。
MAX_ALERTS_PER_CYCLE = 3
#: 榜单里保留多少条集群。
DEFAULT_CLUSTER_LIMIT = 200
#: 弹窗总开关的键名。
ALERTS_SETTING_KEY = "alertsEnabled"

#: 这些实体类型是**场所 / 基础设施**，不是「控盘的人」：
#: 交易所、DEX、聚合器、托管、跨链桥、混币、预言机、质押、借贷、稳定币发行方、
#: 钱包、OTC 台（替客户持仓）、MEV、launchpad、协议类……持有代币是业务需要。
VENUE_ENTITY_TYPES = frozenset({
    "cex", "dex", "dex-aggregator", "custodian", "otc", "wallet", "faucet",
    "bridge", "mixer", "privacy", "oracle", "staking", "liquid-staking",
    "lending-centralized", "lending-decentralized", "yield", "cdp", "mev",
    "stablecoin", "blockchain-infra", "blockchain-scaling",
    "crosschain-interoperability", "smart-contract-platform", "launchpad",
    "derivatives", "nft-marketplace", "nft", "gaming", "social",
    "prediction-market", "real-world-assets", "insurance", "charity",
    "miner-validator", "ai",
})

#: 销毁 / 黑洞地址，永远不是持有人。
BURN_ADDRESSES = frozenset({
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000042069420694206942069",
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
})

#: **按名字**剔除的场所。``VENUE_ENTITY_TYPES`` 只认 Arkham 的 *类型*，但池子经常
#: 被标成业务类型（``pump.fun`` 的类型是 ``meme``，label 却是 ``PumpSwap Market``
#: —— 那是 AMM 池地址，最高占 MUSK 99.8%），只靠类型会让「池子」冒充「控盘方」，
#: 天天报「同一个实体横跨 11 个标的」。类型不可靠时，名字是第二道闸。
VENUE_ENTITY_NAME_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # 平台本体。**必须整名锚定**：Arkham 把普通钱包标成 ``"昵称" on Pump.fun``，
        # 那是真人地址（很有价值的集群线索），只有名字**就是** ``pump.fun`` 的那个
        # 实体才是平台账本 —— 它拿着 NIGEL 95%、PUMP 41%，算进「跨标的控盘」等于
        # 天天报「pump.fun 在控十几个盘」（2026-10-22 真机上就在弹）。
        r"^pump\.fun$",
        # DEX / AMM / 做市池。
        r"\bpump[\s._-]?swap\b", r"\bpumpfun\s+market\b", r"\braydium\b", r"\borca\b",
        r"\bmeteora\b", r"\bjupiter\b", r"\buniswap\b", r"\bpancakeswap\b", r"\bbiswap\b",
        r"\baerodrome\b", r"\bvelodrome\b", r"\bsushiswap\b", r"\bcurve\b", r"\bbalancer\b",
        r"\b1inch\b", r"\bkyber\b", r"\bmaverick\b", r"\bramses\b", r"\bthena\b",
        r"\btrader\s+joe\b", r"\bthorswap\b", r"\bwoofi\b", r"\bdodo\b",
        # 池 / 流动性 / AMM 这类通用词。
        r"\b(?:liquidity\s+)?pool\b", r"\bamm\b", r"\bliquidity\b",
        # 钱包 / 托管方（Arkham 有时把类型写成 misc）。
        r"\bwallet\b",
    )
)


def runtime_dir() -> Path:
    return Path(os.getenv("XINGYUN_RUNTIME_DIR") or (Path(__file__).resolve().parent / ".runtime-cache"))


def default_db_path() -> Path:
    return runtime_dir() / "arkham_cluster_monitor.sqlite"


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def significant_pct() -> float:
    return _env_float("XINGYUN_ARKHAM_CLUSTER_SIGNIFICANT_PCT", CLUSTER_SIGNIFICANT_PCT)


def cross_min_tokens() -> int:
    return max(2, int(_env_float("XINGYUN_ARKHAM_CLUSTER_CROSS_TOKENS", CLUSTER_CROSS_MIN_TOKENS)))


def is_venue_entity(entity_type: Any) -> bool:
    return str(entity_type or "").strip().casefold() in VENUE_ENTITY_TYPES


def is_venue_name(*parts: Any) -> bool:
    """名字里带 DEX / 池 / 钱包 的实体也是场所（见 :data:`VENUE_ENTITY_NAME_PATTERNS`）。"""
    text = " ".join(str(part or "") for part in parts).strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in VENUE_ENTITY_NAME_PATTERNS)


def short_address(address: str, *, head: int = 6, tail: int = 4) -> str:
    text = str(address or "").strip()
    if len(text) <= head + tail + 2:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def token_key_of(chain: Any, contract: Any) -> str:
    """标的唯一键。**链必须先归一到 Arkham slug**。

    链值有三套写法：价格异动写 ``solana``、币安钱包写 ``CT_501``、交易所写 ``56``。
    只按原始文本拼键的话，同一个币从两个来源进来就是两条记录 —— ``tokenTotal`` 虚高、
    板块里同一个币出现两次、持仓基线还各算各的。归一之后全世界只有 ``solana:<ca>``。
    """
    slug = ARKHAM.arkham_chain_slug(chain) or str(chain or "").strip().casefold()
    return f"{slug}:{str(contract or '').strip().casefold()}"


def build_clusters(holders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把持币行聚成集群。

    * 有 ``entityId`` → 按实体合并（同一实体的多个地址 / 多条链占比相加）；
    * 没有 → 按单地址成簇。

    venue 类实体直接丢弃；占比低于门槛的丢弃（见模块常量）。
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in holders or []:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address") or "").strip()
        if not address or address.casefold() in BURN_ADDRESSES:
            continue
        pct = float(row.get("pct") or 0.0)
        if pct <= 0:
            continue
        chain = str(row.get("chain") or "").strip()
        entity_id = str(row.get("entityId") or "").strip()
        entity_type = str(row.get("entityType") or "").strip()
        if entity_id:
            if is_venue_entity(entity_type):
                continue
            # 类型不可靠：``pump.fun`` 的类型是 meme、label 是 PumpSwap Market（AMM 池）。
            # 名字命中场所模式的同样剔除，否则「同一个池子横跨 11 个标的」会天天报。
            if is_venue_name(row.get("entityName"), row.get("label")):
                continue
            if pct < ENTITY_CLUSTER_MIN_PCT and entity_id not in grouped:
                # 单地址不够门槛时先记着，等同一实体的其它地址把它累加过线。
                pass
            cluster_id = f"entity:{entity_id}"
        else:
            if pct < ADDRESS_CLUSTER_MIN_PCT:
                continue
            cluster_id = f"addr:{chain.casefold()}:{address.casefold()}"

        entry = grouped.get(cluster_id)
        if entry is None:
            entry = {
                "clusterId": cluster_id,
                "kind": "entity" if entity_id else "address",
                "name": (
                    str(row.get("entityName") or entity_id)
                    if entity_id
                    else (str(row.get("label") or "") or short_address(address))
                ),
                "clusterType": entity_type,
                "chain": chain,
                "address": "" if entity_id else address,
                "label": str(row.get("label") or ""),
                "pct": 0.0,
                "usd": 0.0,
                "balance": 0.0,
                "addressCount": 0,
                "topAddress": address,
                "topPct": 0.0,
            }
            grouped[cluster_id] = entry
        entry["pct"] += pct
        entry["usd"] += float(row.get("usd") or 0.0)
        entry["balance"] += float(row.get("balance") or 0.0)
        entry["addressCount"] += 1
        if pct > entry["topPct"]:
            entry["topPct"] = pct
            entry["topAddress"] = address

    result = []
    for entry in grouped.values():
        if entry["kind"] == "entity" and entry["pct"] < ENTITY_CLUSTER_MIN_PCT:
            continue
        result.append(entry)
    result.sort(key=lambda item: item["pct"], reverse=True)
    return result


class ArkhamClusterStore:
    """集群台账 + 持仓 + 弹窗台账。独立库，不和 auth_db 抢锁。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self._lock = threading.Lock()
        self.ensure_schema()
        self._normalize_token_keys()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        # WAL：监控线程一直写，页面一直读，非 WAL 会互相卡。
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ac_clusters (
                    cluster_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    cluster_type TEXT NOT NULL DEFAULT '',
                    chain TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    label TEXT NOT NULL DEFAULT '',
                    first_seen_at INTEGER NOT NULL DEFAULT 0,
                    last_seen_at INTEGER NOT NULL DEFAULT 0,
                    peak_pct REAL NOT NULL DEFAULT 0,
                    peak_symbol TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS ac_holdings (
                    token_key TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    chain TEXT NOT NULL DEFAULT '',
                    contract TEXT NOT NULL DEFAULT '',
                    pct REAL NOT NULL DEFAULT 0,
                    usd REAL NOT NULL DEFAULT 0,
                    balance REAL NOT NULL DEFAULT 0,
                    prev_pct REAL NOT NULL DEFAULT 0,
                    delta_pct REAL NOT NULL DEFAULT 0,
                    at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (token_key, cluster_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ac_holdings_cluster
                    ON ac_holdings (cluster_id, pct DESC);
                CREATE TABLE IF NOT EXISTS ac_tokens (
                    token_key TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    chain TEXT NOT NULL DEFAULT '',
                    contract TEXT NOT NULL DEFAULT '',
                    pricing_id TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    sources TEXT NOT NULL DEFAULT '',
                    last_seen_at INTEGER NOT NULL DEFAULT 0,
                    last_ok_at INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    cluster_count INTEGER NOT NULL DEFAULT 0,
                    top_pct REAL NOT NULL DEFAULT 0,
                    top_cluster TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS ac_alerts (
                    cluster_id TEXT PRIMARY KEY,
                    last_alert_at INTEGER NOT NULL DEFAULT 0,
                    last_tokens TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS ac_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def _normalize_token_keys(self) -> None:
        """把按原始链号写下的旧 ``token_key`` 清掉，让它们按归一键重写。

        ``token_key_of`` 改过一次口径（链要先过 Arkham slug）。旧库里因此会留下
        ``ct_501:<ca>`` 这种僵尸行：它永远不会再被更新（新轮次写的是 ``solana:<ca>``），
        却一直出现在榜上，和正确那行并排显示成两个标的。
        删掉是安全的 —— 下一轮 ``_ingest`` 会把持币人重新取一遍、重新建基线。
        """
        with self._lock, self._session() as conn:
            rows = conn.execute("SELECT token_key, chain, contract FROM ac_tokens").fetchall()
            stale = [row["token_key"] for row in rows
                     if token_key_of(row["chain"], row["contract"]) != row["token_key"]]
            for key in stale:
                conn.execute("DELETE FROM ac_tokens WHERE token_key = ?", (key,))
                conn.execute("DELETE FROM ac_holdings WHERE token_key = ?", (key,))
            # 顺带按持仓把每行的「N 个集群 / 最大 X%」重算一遍：老库里既可能有刚删掉的
            # 僵尸键，也可能有 venue 规则收紧后残留的「最大 99.8%」（看着像仍在被控盘）。
            for row in conn.execute("SELECT token_key FROM ac_tokens").fetchall():
                self._recompute_token_stats(conn, row["token_key"])
        if stale:
            print(f"arkham cluster: dropped {len(stale)} stale token keys", file=sys.stderr)

    @staticmethod
    def _recompute_token_stats(conn: sqlite3.Connection, token_key: str) -> None:
        """按 ``ac_holdings`` 重算某一行的集群数 / 最大占比 / 最大集群。"""
        conn.execute(
            "UPDATE ac_tokens SET"
            " cluster_count = (SELECT COUNT(*) FROM ac_holdings WHERE token_key = ?),"
            " top_pct = COALESCE((SELECT MAX(pct) FROM ac_holdings WHERE token_key = ?), 0),"
            " top_cluster = COALESCE((SELECT cluster_id FROM ac_holdings"
            "   WHERE token_key = ? ORDER BY pct DESC LIMIT 1), '')"
            " WHERE token_key = ?",
            (token_key, token_key, token_key, token_key),
        )

    def purge_venue_clusters(self) -> int:
        """清掉**已入库但按现行 venue 规则该剔**的集群。

        规则会收紧（`pump.fun` 就是后来才被认出是 PumpSwap 池的）。历史持仓行里没有
        ``entityName`` / ``label``，但它们对应的 ``ac_clusters`` 行有 —— 按那一行判，
        命中就整条删掉。否则旧数据会一直挂在榜上，直到那个标的碰巧被重新采到。
        """
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT cluster_id, name, cluster_type, label FROM ac_clusters"
            ).fetchall()
            doomed = [
                row["cluster_id"] for row in rows
                if is_venue_entity(row["cluster_type"]) or is_venue_name(row["name"], row["label"])
            ]
            touched: set[str] = set()
            for cluster_id in doomed:
                touched.update(
                    row["token_key"] for row in conn.execute(
                        "SELECT DISTINCT token_key FROM ac_holdings WHERE cluster_id = ?",
                        (cluster_id,),
                    )
                )
                conn.execute("DELETE FROM ac_holdings WHERE cluster_id = ?", (cluster_id,))
                conn.execute("DELETE FROM ac_clusters WHERE cluster_id = ?", (cluster_id,))
            # 标的行上的「N 个集群 / 最大 X%」是入库时算好的，删了持仓必须跟着重算，
            # 否则池子被剔掉之后，那个币还挂着「最大 99.8%」—— 看着像仍在被控盘。
            for token_key in sorted(touched):
                self._recompute_token_stats(conn, token_key)
        if doomed:
            print(f"arkham cluster: purged {len(doomed)} venue clusters", file=sys.stderr)
        return len(doomed)

    def prune_orphan_clusters(self) -> int:
        """删掉没有任何持仓的集群，返回删了几条。

        venue / 名字规则一收紧，原先入库的「池子实体」下一轮就不再被写入持仓，
        但 ``ac_clusters`` 那行还留着 —— 于是「已存集群」永远虚高，而且它永远
        不会再更新。删除是无损的：真有持仓的集群下一轮会被重新 upsert。
        """
        with self._lock, self._session() as conn:
            cur = conn.execute(
                "DELETE FROM ac_clusters WHERE cluster_id NOT IN"
                " (SELECT DISTINCT cluster_id FROM ac_holdings)"
            )
            removed = int(cur.rowcount or 0)
        if removed > 0:
            print(f"arkham cluster: pruned {removed} orphan clusters", file=sys.stderr)
        return removed

    # -- 设置 --------------------------------------------------------------- #
    def setting(self, key: str, default: str = "") -> str:
        with self._lock, self._session() as conn:
            row = conn.execute("SELECT value FROM ac_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO ac_settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def alerts_enabled(self) -> bool:
        return self.setting(ALERTS_SETTING_KEY, "1") != "0"

    def set_alerts_enabled(self, value: Any) -> bool:
        self.set_setting(ALERTS_SETTING_KEY, "1" if value else "0")
        return self.alerts_enabled()

    # -- 写入 --------------------------------------------------------------- #
    def record_token(
        self,
        *,
        token_key: str,
        symbol: str,
        name: str,
        chain: str,
        contract: str,
        pricing_id: str,
        url: str,
        sources: list[str],
        clusters: list[dict[str, Any]],
        error: str = "",
    ) -> None:
        now = int(time.time() * 1000)
        top = clusters[0] if clusters else {}
        with self._lock, self._session() as conn:
            conn.execute(
                """
                INSERT INTO ac_tokens (token_key, symbol, name, chain, contract, pricing_id, url,
                                       sources, last_seen_at, last_ok_at, last_error,
                                       cluster_count, top_pct, top_cluster)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_key) DO UPDATE SET
                    symbol = excluded.symbol,
                    name = CASE WHEN excluded.name != '' THEN excluded.name ELSE ac_tokens.name END,
                    chain = excluded.chain,
                    contract = excluded.contract,
                    pricing_id = CASE WHEN excluded.pricing_id != '' THEN excluded.pricing_id ELSE ac_tokens.pricing_id END,
                    url = CASE WHEN excluded.url != '' THEN excluded.url ELSE ac_tokens.url END,
                    sources = excluded.sources,
                    last_seen_at = excluded.last_seen_at,
                    last_ok_at = CASE WHEN excluded.last_error = '' THEN excluded.last_ok_at ELSE ac_tokens.last_ok_at END,
                    last_error = excluded.last_error,
                    cluster_count = CASE WHEN excluded.last_error = '' THEN excluded.cluster_count ELSE ac_tokens.cluster_count END,
                    top_pct = CASE WHEN excluded.last_error = '' THEN excluded.top_pct ELSE ac_tokens.top_pct END,
                    top_cluster = CASE WHEN excluded.last_error = '' THEN excluded.top_cluster ELSE ac_tokens.top_cluster END
                """,
                (
                    token_key, symbol, name, chain, contract, pricing_id, url,
                    ",".join(sources), now,
                    now if not error else 0,
                    error,
                    len(clusters), float(top.get("pct") or 0.0), str(top.get("clusterId") or ""),
                ),
            )
            if error:
                # 抓取失败：只更新错误信息，绝不清空已有持仓（那是「假装没数据」）。
                return
            conn.execute("DELETE FROM ac_holdings WHERE token_key = ?", (token_key,))
            for cluster in clusters:
                cluster_id = cluster["clusterId"]
                previous = conn.execute(
                    "SELECT pct FROM ac_holdings WHERE token_key = ? AND cluster_id = ?",
                    (token_key, cluster_id),
                ).fetchone()
                prev_pct = float(previous["pct"]) if previous else 0.0
                conn.execute(
                    "INSERT INTO ac_holdings (token_key, cluster_id, symbol, chain, contract,"
                    " pct, usd, balance, prev_pct, delta_pct, at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        token_key, cluster_id, symbol, chain, contract,
                        float(cluster["pct"]), float(cluster["usd"]), float(cluster["balance"]),
                        prev_pct, float(cluster["pct"]) - prev_pct, now,
                    ),
                )
                existing = conn.execute(
                    "SELECT cluster_id FROM ac_clusters WHERE cluster_id = ?", (cluster_id,)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE ac_clusters SET name = ?, cluster_type = ?, chain = ?, address = ?,"
                        " label = ?, last_seen_at = ?,"
                        " peak_pct = CASE WHEN ? > peak_pct THEN ? ELSE peak_pct END,"
                        " peak_symbol = CASE WHEN ? > peak_pct THEN ? ELSE peak_symbol END"
                        " WHERE cluster_id = ?",
                        (
                            cluster["name"], cluster["clusterType"], cluster["chain"],
                            cluster["address"], cluster["label"], now,
                            float(cluster["pct"]), float(cluster["pct"]),
                            float(cluster["pct"]), symbol,
                            cluster_id,
                        ),
                    )
                else:
                    conn.execute(
                        "INSERT INTO ac_clusters (cluster_id, kind, name, cluster_type, chain,"
                        " address, label, first_seen_at, last_seen_at, peak_pct, peak_symbol)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            cluster_id, cluster["kind"], cluster["name"], cluster["clusterType"],
                            cluster["chain"], cluster["address"], cluster["label"],
                            now, now, float(cluster["pct"]), symbol,
                        ),
                    )

    def mark_alert(self, cluster_id: str, tokens: list[str]) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO ac_alerts (cluster_id, last_alert_at, last_tokens) VALUES (?, ?, ?)"
                " ON CONFLICT(cluster_id) DO UPDATE SET"
                " last_alert_at = excluded.last_alert_at, last_tokens = excluded.last_tokens",
                (cluster_id, int(time.time() * 1000), ",".join(tokens)),
            )

    def alert_at(self, cluster_id: str) -> int:
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT last_alert_at FROM ac_alerts WHERE cluster_id = ?", (cluster_id,)
            ).fetchone()
        return int(row["last_alert_at"]) if row else 0

    # -- 读取 --------------------------------------------------------------- #
    def cross_clusters(
        self,
        *,
        min_pct: float | None = None,
        min_tokens: int | None = None,
        board_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """把「在 ≥N 个标的都占 ≥X%」的集群算出来（含它命中的那些标的）。

        ``board_keys`` 给定时**只数榜上的标的**：不在集合里的持仓行直接跳过，
        它们既不构成「跨标的」，也不出现在结果的 ``tokens`` 里（用户 2026-10-22：
        「不是榜上的标的，不做这个地址集群提醒」）。``None`` = 调用方不关心榜上
        与否（纯库视图）。
        """
        threshold = significant_pct() if min_pct is None else float(min_pct)
        needed = cross_min_tokens() if min_tokens is None else max(1, int(min_tokens))
        with self._lock, self._session() as conn:
            rows = conn.execute(
                """
                SELECT h.cluster_id, h.token_key, h.symbol, h.chain, h.contract,
                       h.pct, h.usd, h.delta_pct, h.at,
                       c.name, c.kind, c.cluster_type, c.address, c.label,
                       c.first_seen_at, c.last_seen_at, c.peak_pct, c.peak_symbol,
                       t.pricing_id, t.url, t.name AS token_name
                FROM ac_holdings h
                JOIN ac_clusters c ON c.cluster_id = h.cluster_id
                LEFT JOIN ac_tokens t ON t.token_key = h.token_key
                WHERE h.pct >= ?
                ORDER BY h.pct DESC
                """,
                (float(threshold),),
            ).fetchall()
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            if board_keys is not None and str(row["token_key"]) not in board_keys:
                continue
            cluster_id = str(row["cluster_id"])
            bucket = buckets.get(cluster_id)
            if bucket is None:
                bucket = {
                    "clusterId": cluster_id,
                    "name": row["name"] or cluster_id,
                    "kind": row["kind"] or "",
                    "clusterType": row["cluster_type"] or "",
                    "address": row["address"] or "",
                    "label": row["label"] or "",
                    "firstSeenAt": int(row["first_seen_at"] or 0),
                    "lastSeenAt": int(row["last_seen_at"] or 0),
                    "peakPct": float(row["peak_pct"] or 0.0),
                    "peakSymbol": row["peak_symbol"] or "",
                    "tokens": [],
                    "maxPct": 0.0,
                }
                buckets[cluster_id] = bucket
            bucket["tokens"].append({
                "tokenKey": str(row["token_key"]),
                "symbol": row["symbol"] or "",
                "name": row["token_name"] or "",
                "chain": row["chain"] or "",
                "contract": row["contract"] or "",
                "pct": float(row["pct"] or 0.0),
                "usd": float(row["usd"] or 0.0),
                "deltaPct": float(row["delta_pct"] or 0.0),
                "url": row["url"] or ARKHAM.arkham_token_url(row["pricing_id"]),
            })
            bucket["maxPct"] = max(bucket["maxPct"], float(row["pct"] or 0.0))
        result = []
        for item in buckets.values():
            # 同一标的可能登记了多条链的合约（bsc + ethereum 都有记录），
            # 那是**一个标的**，不是两个 —— 按 symbol 去重，留占比最高的那条。
            by_symbol: dict[str, dict[str, Any]] = {}
            for row in item["tokens"]:
                key = symbol_key(row.get("symbol")) or row.get("tokenKey") or ""
                kept = by_symbol.get(key)
                if kept is None or row["pct"] > kept["pct"]:
                    by_symbol[key] = row
            item["tokens"] = list(by_symbol.values())
            if len(item["tokens"]) < needed:
                continue
            result.append(item)
        for item in result:
            item["tokens"].sort(key=lambda row: row["pct"], reverse=True)
            item["tokenCount"] = len(item["tokens"])
            item["totalPct"] = sum(row["pct"] for row in item["tokens"])
        result.sort(key=lambda item: (item["tokenCount"], item["maxPct"]), reverse=True)
        return result

    def board(
        self,
        *,
        limit: int = DEFAULT_CLUSTER_LIMIT,
        board_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """板块数据。``board_keys`` 与弹窗口径一致 —— 板块上列出的「跨标的」
        必须就是会弹的那几条，否则用户会看到一条永远不会弹的记录。

        ``tokens`` 仍是全部扫过的标的（那是研究视图，掉榜的也在），不跟着收窄。
        """
        min_pct = significant_pct()
        clusters = self.cross_clusters(min_pct=min_pct, board_keys=board_keys)
        with self._lock, self._session() as conn:
            tokens = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM ac_tokens ORDER BY last_seen_at DESC LIMIT 200"
                ).fetchall()
            ]
            total_clusters = conn.execute("SELECT COUNT(*) AS n FROM ac_clusters").fetchone()["n"]
            holdings = conn.execute("SELECT COUNT(*) AS n FROM ac_holdings").fetchone()["n"]
            alerted = conn.execute("SELECT COUNT(*) AS n FROM ac_alerts").fetchone()["n"]
        ok_tokens = [row for row in tokens if not row.get("last_error")]
        return {
            "clusters": clusters[:limit],
            "tokens": tokens[:60],
            "summary": {
                "clusterTotal": int(total_clusters or 0),
                "holdingTotal": int(holdings or 0),
                "crossTotal": len(clusters),
                "alertedTotal": int(alerted or 0),
                "tokenTotal": len(tokens),
                "tokenOk": len(ok_tokens),
                "significantPct": min_pct,
                "crossMinTokens": cross_min_tokens(),
                # 榜上标的数：跨标的判定只数这些。0 就说明榜源这轮没数据，
                # 「跨标的」自然是空的 —— 别把它读成「没人控盘」。
                "boardTokenTotal": len(board_keys) if board_keys is not None else 0,
            },
        }


class ArkhamClusterMonitor:
    """每轮把候选标的的持币人拉下来聚成集群，找跨标的重复出现的集群并提醒。"""

    def __init__(
        self,
        store: ArkhamClusterStore | None = None,
        *,
        interval: float | None = None,
        cooldown_seconds: float = CLUSTER_ALERT_COOLDOWN_SECONDS,
        max_candidates: int | None = None,
        alert_callback: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.store = store or ArkhamClusterStore()
        self.interval = float(
            interval if interval is not None
            else _env_float("XINGYUN_ARKHAM_CLUSTER_INTERVAL", DEFAULT_CLUSTER_INTERVAL)
        )
        self.cooldown_seconds = float(cooldown_seconds)
        self.max_candidates = max(1, int(
            max_candidates if max_candidates is not None
            else _env_float("XINGYUN_ARKHAM_CLUSTER_MAX_CANDIDATES", MAX_CANDIDATES)
        ))
        self.on_event = alert_callback
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycle_lock = threading.Lock()
        self._lock = threading.Lock()
        # 候选标的由 server 注入：榜单结构只有 server 知道。
        self.candidate_provider: Callable[[], list[dict[str, Any]]] | None = None
        # 上一轮「榜上」的标的键集合（用户 2026-10-22：跨标的只数榜上标的）。
        # ``_board_known`` 为 False 表示 provider 还没成功跑过 —— 这时不做收窄，
        # 免得刚起来那几十秒把该弹的也一起压掉。
        self._board_keys: set[str] = set()
        self._board_known = False
        # 候选抽取的轮转计数（见 ``_candidates``）：让配额之外的那批标的也能轮到。
        self._cycle_index = 0
        self.last_cycle_at = 0.0
        self.last_error = ""
        self.last_note = ""
        self.fetched_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        # 页面快照缓存。
        self._payload_lock = threading.Lock()
        self._payload_cache: dict[str, Any] | None = None
        self._payload_cache_at = 0.0
        self._payload_rebuilding = False

    # -- 注入 --------------------------------------------------------------- #
    def set_candidate_provider(self, provider: Callable[[], list[dict[str, Any]]]) -> None:
        self.candidate_provider = provider

    def set_alert_callback(self, callback: Callable[[dict[str, Any]], Any]) -> None:
        self.on_event = callback

    def alerts_enabled(self) -> bool:
        return self.store.alerts_enabled()

    def set_alerts_enabled(self, value: Any) -> bool:
        return self.store.set_alerts_enabled(value)

    # -- 生命周期 ----------------------------------------------------------- #
    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="arkham-cluster-monitor", daemon=True
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        self.stop()

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        # 启动先歇一拍：不和 server 起来时那一堆监控线程抢网络。
        self.stop_event.wait(75)
        while not self.stop_event.is_set():
            started = time.time()
            try:
                self.run_cycle()
            except ARKHAM.ArkhamAuthError as exc:
                # 口令被轮换：系统性故障，记下来让板块明说，别伪装成没有集群。
                self.last_error = str(exc)[:200]
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            remaining = self.interval - (time.time() - started)
            self.stop_event.wait(max(5.0, remaining))

    # -- 一轮 --------------------------------------------------------------- #
    def board_scope(self) -> set[str] | None:
        """上一轮「榜上」的标的键集合；``None`` = 还不知道（provider 还没跑过）。

        跨标的判定与板块列表都吃这个集合（用户 2026-10-22：「不是榜上的标的，
        不做这个地址集群提醒」）。空集**不是** ``None`` —— 榜源真的没给标的时，
        就该一条都不提醒，而不是放宽成全库。
        """
        return self._board_keys if self._board_known else None

    def _candidates(self) -> list[dict[str, Any]]:
        if self.candidate_provider is None:
            return []
        try:
            rows = self.candidate_provider() or []
        except Exception as exc:
            # 榜源整条腿挂了：保留上一轮的榜上集合，别顺手把口径放宽成全库。
            self.last_error = f"候选标的获取失败：{type(exc).__name__}: {exc}"[:200]
            return []
        picked: list[dict[str, Any]] = []
        board_keys: set[str] = set()
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            chain = str(row.get("chain") or "").strip()
            contract = str(row.get("contract") or "").strip()
            if not chain or not contract:
                continue
            if not ARKHAM.arkham_chain_slug(chain):
                continue
            key = token_key_of(chain, contract)
            # 榜上成员照收，哪怕本轮抓取配额不够：它是**榜上的标的**，
            # 跨标的判定必须认得它（否则「榜上」会随配额抖动）。
            board_keys.add(key)
            if key in seen:
                continue
            seen.add(key)
            picked.append({
                "tokenKey": key,
                "symbol": symbol_key(row.get("symbol")) or str(row.get("symbol") or "").strip(),
                "name": str(row.get("name") or "").strip(),
                "chain": chain,
                "contract": contract,
                "sources": [str(s) for s in (row.get("sources") or []) if s],
                "price": float(row.get("price") or 0.0),
            })
        self._board_keys = board_keys
        self._board_known = True
        # 候选池（≈80–100 个行情榜标的）比单轮配额大：**每轮换起点**，否则固定从头取
        # 会让后面那几十个永远排不上，板块里长期躺着从没被采样过的行。
        # 2026-10-22 剔掉战壕新币流之后配额才真正紧张起来（原先那 300 行把配额吃满）。
        if len(picked) > self.max_candidates:
            offset = self._cycle_index % len(picked)
            picked = picked[offset:] + picked[:offset]
        self._cycle_index += 1
        return picked[:self.max_candidates]

    def run_cycle(self, *, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
        """跑一轮。``candidate`` 给定时只查这一个标的（手动刷新用）。"""
        with self._cycle_lock:
            candidates = [candidate] if candidate else self._candidates()
            fetched = failed = skipped = 0
            errors: list[str] = []
            for item in candidates:
                try:
                    if self._ingest(item):
                        fetched += 1
                    else:
                        skipped += 1
                except ARKHAM.ArkhamAuthError as exc:
                    # 鉴权类不吞：整轮中止，让上层把板块标成不可用。
                    self.last_error = str(exc)[:200]
                    self.failed_count = failed + 1
                    self.last_cycle_at = time.time()
                    raise
                except Exception as exc:
                    failed += 1
                    message = f"{type(exc).__name__}: {exc}"[:160]
                    errors.append(f"{item.get('symbol') or item['tokenKey']}: {message}")
                    self.store.record_token(
                        token_key=item["tokenKey"],
                        symbol=item["symbol"],
                        name=item["name"],
                        chain=item["chain"],
                        contract=item["contract"],
                        pricing_id="",
                        url="",
                        sources=item["sources"],
                        clusters=[],
                        error=message,
                    )
            self.fetched_count = fetched
            self.failed_count = failed
            self.skipped_count = skipped
            self.last_cycle_at = time.time()
            if fetched:
                self.last_error = ""
            self.last_note = (
                f"本轮 {len(candidates)} 个候选：成功 {fetched} / 复用缓存 {skipped} / 失败 {failed}"
            )
            if errors:
                self.last_note += "；" + "；".join(errors[:3])
            # 剔掉已经没有任何持仓的集群：venue 规则一收紧，老的池子集群就得从
            # 「已存集群」里消失，否则它会一直挂在那、永远不再更新。
            self.store.purge_venue_clusters()
            self.store.prune_orphan_clusters()
            delivered = self.dispatch_alerts()
            self.invalidate_payload()
            return {
                "candidates": len(candidates),
                "fetched": fetched,
                "skipped": skipped,
                "failed": failed,
                "alerts": delivered,
            }

    def _ingest(self, item: dict[str, Any]) -> bool:
        """查一个标的并落库。返回 False 表示本轮复用了已有快照（没发请求）。"""
        token_key = item["tokenKey"]
        with self.store._lock, self.store._session() as conn:
            row = conn.execute(
                "SELECT last_ok_at, top_cluster, top_pct FROM ac_tokens WHERE token_key = ?",
                (token_key,),
            ).fetchone()
        last_ok = int(row["last_ok_at"] or 0) if row else 0
        age = (time.time() * 1000 - last_ok) / 1000.0 if last_ok else HOLDERS_TTL_SECONDS + 1
        if last_ok and age < HOLDERS_TTL_SECONDS and not item.get("force"):
            return False

        result = ARKHAM.fetch_token_holders(item["chain"], item["contract"])
        identity = result.get("identity") or {}
        holders = result.get("holders") or []
        if not holders:
            reason = str(result.get("reason") or "Arkham 没有该标的的持币人数据")
            self.store.record_token(
                token_key=token_key,
                symbol=item["symbol"],
                name=item["name"] or identity.get("name", ""),
                chain=item["chain"],
                contract=item["contract"],
                pricing_id=str(identity.get("pricingId") or ""),
                url="",
                sources=item["sources"],
                clusters=[],
                error=reason,
            )
            return True

        clusters = build_clusters(holders)
        pricing_id = str(identity.get("pricingId") or "")
        self.store.record_token(
            token_key=token_key,
            symbol=item["symbol"] or str(identity.get("symbol") or ""),
            name=item["name"] or str(identity.get("name") or ""),
            chain=item["chain"],
            contract=item["contract"],
            pricing_id=pricing_id,
            url=ARKHAM.arkham_token_url(pricing_id),
            sources=item["sources"],
            clusters=clusters,
        )
        return True

    def dispatch_alerts(self) -> int:
        """把「跨 ≥N 个**榜上**标的都占 ≥X%」的集群挑出来发弹窗（每个集群有冷却）。

        榜外标的的持仓不参与计数 —— 它们掉榜之后仍留在库里，拿它们凑「跨标的」
        等于把「已经没人看的币」当成新信号（用户 2026-10-22，SHIB/STA 那次）。
        """
        if not self.alerts_enabled() or self.on_event is None:
            return 0
        now = time.time()
        delivered = 0
        scope = self.board_scope()
        for cluster in self.store.cross_clusters(board_keys=scope):
            if delivered >= MAX_ALERTS_PER_CYCLE:
                break
            last_ms = self.store.alert_at(cluster["clusterId"])
            if last_ms and (now - last_ms / 1000.0) < self.cooldown_seconds:
                continue
            event = dict(cluster)
            event["kind"] = "arkham-cluster"
            event["key"] = f"arkham-cluster:{cluster['clusterId']}"
            event["significantPct"] = significant_pct()
            try:
                self.on_event(event)
            except Exception:
                # 弹窗发不出去就别记账，否则这个集群会被静默 6 小时。
                continue
            self.store.mark_alert(
                cluster["clusterId"],
                [row["symbol"] for row in cluster["tokens"]],
            )
            delivered += 1
        return delivered

    # -- 页面 --------------------------------------------------------------- #
    def invalidate_payload(self) -> None:
        with self._payload_lock:
            self._payload_cache_at = 0.0

    def _build_payload(self) -> dict[str, Any]:
        board = self.store.board(board_keys=self.board_scope())
        board["state"] = {
            "running": self.running(),
            "interval": self.interval,
            "lastCycleAt": int(self.last_cycle_at * 1000) if self.last_cycle_at else 0,
            "lastError": self.last_error,
            "lastNote": self.last_note,
            "fetched": self.fetched_count,
            "failed": self.failed_count,
            "skipped": self.skipped_count,
            "alertsEnabled": self.store.alerts_enabled(),
            "maxCandidates": self.max_candidates,
            "holdersTtl": HOLDERS_TTL_SECONDS,
            "boardTokens": len(self._board_keys),
            "authBroken": bool(self.last_error and "签名被拒" in self.last_error),
        }
        return board

    def payload(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """板块快照。快照缓存 30 秒 —— 直读库会被监控线程的写事务饿到几十秒。"""
        now = time.time()
        with self._payload_lock:
            if (
                not force_refresh
                and self._payload_cache is not None
                and now - self._payload_cache_at < PAYLOAD_CACHE_SECONDS
            ):
                return self._clone(self._payload_cache)
        try:
            payload = self._build_payload()
        except Exception as exc:
            self.last_error = f"板块快照构建失败：{type(exc).__name__}: {exc}"[:200]
            with self._payload_lock:
                if self._payload_cache is not None:
                    return self._clone(self._payload_cache)
            raise
        with self._payload_lock:
            self._payload_cache = payload
            self._payload_cache_at = time.time()
        return self._clone(payload)

    @staticmethod
    def _clone(payload: dict[str, Any]) -> dict[str, Any]:
        """浅拷到「server 再往每行补 url 也不会写回缓存」的深度。"""
        clone = dict(payload)
        clone["clusters"] = [dict(item) for item in payload.get("clusters") or []]
        for item in clone["clusters"]:
            item["tokens"] = [dict(row) for row in item.get("tokens") or []]
        clone["tokens"] = [dict(item) for item in payload.get("tokens") or []]
        clone["summary"] = dict(payload.get("summary") or {})
        clone["state"] = dict(payload.get("state") or {})
        return clone
