"""大幅异动监控：**3 日跌超 50%** / **2 日涨超 100%**（用户 2026-11-01 提）。

这两条规则讲的是两件完全不同的事，所以文案必须分开：

* **跌（3 日 −50%）** —— 三个自然日内从区间最高价跌掉一半，绝大多数是**流动性真空**：
  深度被打穿、做市撤退、连锁强平。这种跌法之后经常跟着**剧烈反弹**（V 型回抽
  20%~40% 很常见），所以提醒的重点是「别在这里追空 / 准备接反抽」，不是「继续看跌」。
* **涨（2 日 +100%）** —— 两天翻倍属于短线透支，重点变成**注意反弹见顶**：
  冲高回落、放量滞涨通常是这一波反弹的尾声。

**范围（用户 2026-11-01 追加拍板）**：只监控**上过 AICOIN 热门榜的币**
（``price_watch_assets.aicoin_first_seen_at > 0``）。理由很实在 —— 全市场扫一天能报出
几十条没人看的死币，而「上过热榜」意味着它至少进过散户的视野，异动才有交易价值。

**数据口径**：日线（最后一根是**当天还没走完的日线**，收盘价就是现价）。

* 跌幅 = 现价 ÷ 最近 N 根日线的**最高价** − 1
* 涨幅 = 现价 ÷ 最近 N 根日线的**最低价** − 1
* 同一标的同一规则 **12 小时**只弹一次：暴跌之后横着不动不该每 15 分钟响一次。

行情由 server 注入（``set_candle_provider``）—— 币安合约日线 → 币安现货日线 →
OKX 永续日线 → 链上 K 线，这套取数逻辑跟 ``price_structure`` 那一套复用，不在这里重写。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sqlite3
import threading
import time
from typing import Any, Callable


#: 规则 id。弹窗 key、库里主键、页面开关都用它。
RULE_DROP = "drop"
RULE_PUMP = "pump"

#: 暴跌规则：3 个自然日内，现价相对区间最高价跌 ≥50%。
BIG_MOVE_DROP_DAYS = 3
BIG_MOVE_DROP_PCT = -50.0
#: 暴涨规则：2 个自然日内，现价相对区间最低价涨 ≥100%。
BIG_MOVE_PUMP_DAYS = 2
BIG_MOVE_PUMP_PCT = 100.0

#: 同一标的同一规则的弹窗静默窗口。
BIG_MOVE_ALERT_COOLDOWN_SECONDS = 12 * 3600
#: 采样节奏：15 分钟。日线口径用不着更密，一轮 200 来个请求也够轻。
BIG_MOVE_CYCLE_SECONDS = 900.0
#: 一次取多少根日线。窗口最长 3 天，多取几根便于将来加「N 日」规则与人工核对。
BIG_MOVE_CANDLE_LIMIT = 8
#: 少于这个数就没法算窗口（只有 1 根日线时最高/最低价都等于自己）。
BIG_MOVE_MIN_CANDLES = 2
#: 一轮里同时抓几条（上游都有速率上限，别贪）。
BIG_MOVE_CONCURRENCY = 6
#: 快照缓存秒数（GET 不排在一轮扫描后面）。
BIG_MOVE_PAYLOAD_CACHE_SECONDS = 20.0
#: 命中流水保留多少条给页面/接口看。
BIG_MOVE_HIT_LIMIT = 200

ALERTS_SETTING_KEY = "alertsEnabled"

#: 给调用方的短名字：server 那边写 ``BIG_MOVE.CANDLE_LIMIT`` 比
#: ``BIG_MOVE.BIG_MOVE_CANDLE_LIMIT`` 清爽，也不会把「模块前缀 + 常量前缀」叠两层。
CANDLE_LIMIT = BIG_MOVE_CANDLE_LIMIT
MIN_CANDLES_FOR_WINDOW = BIG_MOVE_MIN_CANDLES

#: 阈值比较的容差。``100/50 - 1`` 在浮点里是 ``0.9999999999999998``，
#: 恰好翻倍的标的会被「≥100%」判漏，边界必须算命中。
EPSILON = 1e-9


def runtime_dir() -> Path:
    return Path(os.getenv("XINGYUN_RUNTIME_DIR") or (Path(__file__).resolve().parent / ".runtime-cache"))


def default_db_path() -> Path:
    return runtime_dir() / "big_move_monitor.sqlite"


def pct_change(current: Any, base: Any) -> float:
    """``(现价 / 基准 − 1) * 100``；基准无效时返回 0.0（调用方自己判空）。"""
    try:
        base_value = float(base)
        current_value = float(current)
    except (TypeError, ValueError):
        return 0.0
    if base_value <= 0 or current_value <= 0:
        return 0.0
    return (current_value / base_value - 1.0) * 100.0


def rule_specs() -> tuple[dict[str, Any], ...]:
    """两条规则的完整定义（文案也在这里，别在两处各写一遍）。"""
    return (
        {
            "id": RULE_DROP,
            "days": BIG_MOVE_DROP_DAYS,
            "thresholdPct": BIG_MOVE_DROP_PCT,
            "direction": "down",
            "label": f"{BIG_MOVE_DROP_DAYS} 日跌超 {abs(BIG_MOVE_DROP_PCT):g}%",
            "headline": "疑似流动性真空",
            # 正文里这句要**自己说清楚是什么事**：光写「暴跌」用户不知道该干什么。
            "advice": "疑似流动性真空，警惕剧烈反弹：这种跌法之后常出现 V 型回抽，别在真空里追空",
        },
        {
            "id": RULE_PUMP,
            "days": BIG_MOVE_PUMP_DAYS,
            "thresholdPct": BIG_MOVE_PUMP_PCT,
            "direction": "up",
            "label": f"{BIG_MOVE_PUMP_DAYS} 日涨超 {BIG_MOVE_PUMP_PCT:g}%",
            "headline": "短线已经透支",
            "advice": "注意反弹见顶：两日翻倍多是这波反弹的尾声，冲高回落该减仓",
        },
    )


def rule_spec(rule_id: str) -> dict[str, Any]:
    for spec in rule_specs():
        if spec["id"] == rule_id:
            return spec
    raise ValueError(f"未知的大幅异动规则：{rule_id}")


def asset_key(asset: dict[str, Any]) -> str:
    """标的唯一键：符号 + 链 + 合约（同一符号在多条链上是不同的币）。"""
    symbol = str(asset.get("symbol") or "").strip().upper()
    chain = str(asset.get("chain") or "").strip().lower()
    contract = str(asset.get("contract") or "").strip().lower()
    return f"{symbol}|{chain}|{contract}"


def _normalize_candles(candles: Any) -> list[tuple[int, float, float, float]]:
    """把 K 线统一成 ``(时间, 最高, 收盘, 最低)``，坏行直接丢掉。

    项目里所有 K 线源的约定都是 ``(t, high, close, open, low, volume)``，
    这里只取用得上的四项；空值 / 0 值行当坏行（0 的最高价会把跌幅算成无穷大）。
    """
    rows: list[tuple[int, float, float, float]] = []
    for candle in candles or []:
        try:
            if isinstance(candle, dict):
                high = float(candle.get("high") or 0)
                close = float(candle.get("close") or 0)
                low = float(candle.get("low") or 0)
                at = int(candle.get("time") or candle.get("timestamp") or 0)
            else:
                high = float(candle[1])
                close = float(candle[2])
                low = float(candle[4])
                at = int(candle[0])
        except (IndexError, TypeError, ValueError):
            continue
        if close <= 0 or high <= 0 or low <= 0:
            continue
        rows.append((at, high, close, low))
    rows.sort(key=lambda row: row[0])
    return rows


def analyze_candles(
    asset: dict[str, Any],
    candles: Any,
    *,
    specs: tuple[dict[str, Any], ...] | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    """一日线序列 → 命中的规则列表（可能同时命中两条，虽然极少见）。

    **只用日线**：``rows[-1]`` 是今天那根没走完的日线，它的收盘价就是现价。
    窗口 = ``rows[-days:]``；不足两根日线时不做判断 —— 一根日线里最高价、
    最低价、收盘价是同一段时间的三个点，拿它算「3 日跌幅」纯属自欺。
    """
    rows = _normalize_candles(candles)
    if len(rows) < BIG_MOVE_MIN_CANDLES:
        return []
    _, _, price, _ = rows[-1]
    if price <= 0:
        return []
    at_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    hits: list[dict[str, Any]] = []
    for spec in specs or rule_specs():
        window = rows[-max(2, int(spec["days"])):]
        if len(window) < 2:
            continue
        if spec["direction"] == "down":
            base = max(row[1] for row in window)
            pct = pct_change(price, base)
            # 跌幅是负数：跌得不够（比如 −12%）就跳过。容差让「恰好 −50%」算命中。
            if pct > float(spec["thresholdPct"]) + EPSILON:
                continue
            base_label = "最高"
        else:
            base = min(row[3] for row in window)
            pct = pct_change(price, base)
            if pct < float(spec["thresholdPct"]) - EPSILON:
                continue
            base_label = "最低"
        if base <= 0:
            continue
        hits.append({
            "rule": spec["id"],
            "symbol": str(asset.get("symbol") or "").strip().upper(),
            "name": str(asset.get("name") or ""),
            "chain": str(asset.get("chain") or ""),
            "contract": str(asset.get("contract") or ""),
            "source": str(asset.get("source") or ""),
            "pct": round(pct, 2),
            "price": price,
            "basePrice": base,
            "baseLabel": base_label,
            "days": int(spec["days"]),
            "thresholdPct": float(spec["thresholdPct"]),
            "windowStart": int(window[0][0]),
            "candles": len(window),
            "at": at_ms,
            "assetKey": asset_key(asset),
        })
    return hits


class BigMoveStore:
    """命中的流水 + 冷却记账。独立库：一轮扫 200 个标的会写不少行，别去挤 auth_db。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or default_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bm_hits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_key TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    chain TEXT NOT NULL DEFAULT '',
                    contract TEXT NOT NULL DEFAULT '',
                    pct REAL NOT NULL DEFAULT 0,
                    price REAL NOT NULL DEFAULT 0,
                    base_price REAL NOT NULL DEFAULT 0,
                    days INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    at INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_bm_hits_at ON bm_hits(at DESC);
                CREATE TABLE IF NOT EXISTS bm_state (
                    asset_key TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    chain TEXT NOT NULL DEFAULT '',
                    contract TEXT NOT NULL DEFAULT '',
                    last_alert_at INTEGER NOT NULL DEFAULT 0,
                    last_pct REAL NOT NULL DEFAULT 0,
                    last_price REAL NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (asset_key, rule)
                );
                CREATE TABLE IF NOT EXISTS bm_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self.conn.commit()

    # -- 设置 --------------------------------------------------------------- #
    def setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM bm_settings WHERE key = ?", (str(key),)
            ).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO bm_settings(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(key), str(value)),
            )
            self.conn.commit()

    def alerts_enabled(self) -> bool:
        return self.setting(ALERTS_SETTING_KEY, "1") != "0"

    def set_alerts_enabled(self, value: Any) -> bool:
        self.set_setting(ALERTS_SETTING_KEY, "1" if value else "0")
        return self.alerts_enabled()

    # -- 冷却 --------------------------------------------------------------- #
    def alert_state(self, key: str, rule: str) -> dict[str, Any]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM bm_state WHERE asset_key = ? AND rule = ?", (str(key), str(rule))
            ).fetchone()
        return dict(row) if row else {}

    def last_alert_at(self, key: str, rule: str) -> int:
        return int(self.alert_state(key, rule).get("last_alert_at") or 0)

    def mark_alerted(self, hit: dict[str, Any]) -> None:
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO bm_state (
                    asset_key, rule, symbol, chain, contract,
                    last_alert_at, last_pct, last_price, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_key, rule) DO UPDATE SET
                    last_alert_at = excluded.last_alert_at,
                    last_pct = excluded.last_pct,
                    last_price = excluded.last_price,
                    symbol = excluded.symbol,
                    updated_at = excluded.updated_at
                """,
                (
                    str(hit.get("assetKey") or ""),
                    str(hit.get("rule") or ""),
                    str(hit.get("symbol") or ""),
                    str(hit.get("chain") or ""),
                    str(hit.get("contract") or ""),
                    int(hit.get("at") or now),
                    float(hit.get("pct") or 0),
                    float(hit.get("price") or 0),
                    now,
                ),
            )
            self.conn.commit()

    # -- 流水 --------------------------------------------------------------- #
    def record_hit(self, hit: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO bm_hits (
                    asset_key, rule, symbol, name, chain, contract,
                    pct, price, base_price, days, source, at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(hit.get("assetKey") or ""),
                    str(hit.get("rule") or ""),
                    str(hit.get("symbol") or ""),
                    str(hit.get("name") or ""),
                    str(hit.get("chain") or ""),
                    str(hit.get("contract") or ""),
                    float(hit.get("pct") or 0),
                    float(hit.get("price") or 0),
                    float(hit.get("basePrice") or 0),
                    int(hit.get("days") or 0),
                    str(hit.get("source") or ""),
                    int(hit.get("at") or 0),
                ),
            )
            self.conn.commit()

    def recent_hits(self, limit: int = BIG_MOVE_HIT_LIMIT) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM bm_hits ORDER BY at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append({
                "assetKey": row["asset_key"],
                "rule": row["rule"],
                "symbol": row["symbol"],
                "name": row["name"],
                "chain": row["chain"],
                "contract": row["contract"],
                "pct": round(float(row["pct"] or 0), 2),
                "price": float(row["price"] or 0),
                "basePrice": float(row["base_price"] or 0),
                "days": int(row["days"] or 0),
                "source": row["source"],
                "at": int(row["at"] or 0),
            })
        return out

    def hit_count(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) FROM bm_hits").fetchone()
        return int(row[0] or 0) if row else 0

    def close(self) -> None:
        with self._lock:
            self.conn.close()


class BigMoveMonitor:
    """每 15 分钟扫一遍「上过 AICOIN 热榜的币」，日线算两条规则的涨跌幅。"""

    def __init__(
        self,
        store: BigMoveStore | None = None,
        *,
        interval: float | None = None,
        specs: tuple[dict[str, Any], ...] | None = None,
        cooldown_seconds: float = BIG_MOVE_ALERT_COOLDOWN_SECONDS,
        concurrency: int = BIG_MOVE_CONCURRENCY,
        alert_callback: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.store = store or BigMoveStore()
        self.interval = float(
            interval if interval is not None
            else (os.getenv("BIG_MOVE_SECONDS") or BIG_MOVE_CYCLE_SECONDS)
        )
        self.specs = specs or rule_specs()
        self.cooldown_seconds = float(cooldown_seconds)
        self.concurrency = max(1, int(concurrency))
        self.on_event = alert_callback
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycle_lock = threading.Lock()
        self._lock = threading.Lock()
        # 数据由 server 注入：标的清单（上过 AICOIN 热榜）+ 单个标的的日线。
        self.universe_provider: Callable[[], list[dict[str, Any]]] | None = None
        self.candle_provider: Callable[[dict[str, Any]], Any] | None = None
        self.last_cycle_at = 0.0
        self.last_error = ""
        self.last_note = ""
        self.blocked_count = 0
        self._scan_stats: dict[str, Any] = {"scanned": 0, "covered": 0, "missing": [], "errors": 0}
        self._payload_lock = threading.Lock()
        self._payload_cache: dict[str, Any] | None = None
        self._payload_cache_at = 0.0
        self._payload_rebuilding = False

    # -- 注入 --------------------------------------------------------------- #
    def set_universe_provider(self, provider: Callable[[], list[dict[str, Any]]]) -> None:
        self.universe_provider = provider

    def set_candle_provider(self, provider: Callable[[dict[str, Any]], Any]) -> None:
        self.candle_provider = provider

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
                target=self._loop, name="big-move-monitor", daemon=True
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
        self.stop_event.wait(90)
        while not self.stop_event.is_set():
            started = time.time()
            try:
                self.run_cycle()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            remaining = self.interval - (time.time() - started)
            self.stop_event.wait(max(10.0, remaining))

    # -- 一轮 --------------------------------------------------------------- #
    def run_cycle(self) -> dict[str, Any]:
        if not self._cycle_lock.acquire(blocking=False):
            return {"scanned": 0, "hits": 0, "fired": 0, "busy": 1}
        try:
            assets = self.collect_universe()
            if not assets:
                # 拿不到标的清单时**不许当「今天没有异动」**：那会把一次失败
                # 伪装成一份干净的榜（项目里这条纪律踩过好几次）。
                self.last_error = "没有拿到标的清单（上过 AICOIN 热榜的币）"
                self.last_note = "本轮跳过，未做任何判断"
                return {"scanned": 0, "hits": 0, "fired": 0}
            hits, stats = self.scan_assets(assets)
            fired = 0
            for hit in hits:
                if self.maybe_alert(hit):
                    fired += 1
            self._scan_stats = stats
            self.last_cycle_at = time.time()
            self.last_error = ""
            self.last_note = (
                f"扫描 {stats['scanned']} 个标的，取到日线 {stats['covered']} 个，"
                f"命中 {len(hits)} 条"
            )
            try:
                self.refresh_payload_cache()
            except Exception:
                pass
            return {"scanned": stats["scanned"], "hits": len(hits), "fired": fired}
        finally:
            self._cycle_lock.release()

    def collect_universe(self) -> list[dict[str, Any]]:
        if self.universe_provider is None:
            return []
        try:
            rows = self.universe_provider() or []
        except Exception as exc:
            self.last_error = f"标的清单：{type(exc).__name__}: {exc}"[:200]
            return []
        assets: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not str(row.get("symbol") or "").strip():
                continue
            assets.append(row)
        return assets

    def scan_assets(self, assets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """并发取日线、逐条判断。返回 ``(命中列表, 统计)``。"""
        hits: list[dict[str, Any]] = []
        if self.candle_provider is None:
            return hits, {"scanned": len(assets), "covered": 0, "missing": [], "errors": len(assets)}
        now_ms = int(time.time() * 1000)
        covered = 0
        errors = 0
        missing: list[str] = []
        by_rule: dict[str, int] = {spec["id"]: 0 for spec in self.specs}

        def work(asset: dict[str, Any]) -> tuple[dict[str, Any], Any, str]:
            try:
                return asset, self.candle_provider(asset), ""
            except Exception as exc:
                return asset, None, f"{type(exc).__name__}: {exc}"[:120]

        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="big-move-scan") as pool:
            results = list(pool.map(work, assets))
        for asset, candles, error in results:
            if error or not candles:
                errors += 1
                symbol = str(asset.get("symbol") or "")
                if symbol and len(missing) < 40:
                    missing.append(symbol)
                continue
            covered += 1
            for hit in analyze_candles(asset, candles, specs=self.specs, now_ms=now_ms):
                by_rule[hit["rule"]] = by_rule.get(hit["rule"], 0) + 1
                hits.append(hit)
        # 跌得最狠的排前面；同幅度按规则顺序。
        hits.sort(key=lambda row: (row["rule"] != RULE_DROP, -abs(float(row["pct"]))))
        return hits, {
            "scanned": len(assets),
            "covered": covered,
            "missing": missing,
            "errors": errors,
            "byRule": by_rule,
        }

    def maybe_alert(self, hit: dict[str, Any]) -> bool:
        """同一标的同一规则 12 小时只弹一次。**先判断再记账**，mark 放最后。"""
        key = str(hit.get("assetKey") or "")
        rule = str(hit.get("rule") or "")
        last = self.store.last_alert_at(key, rule)
        now_ms = int(hit.get("at") or time.time() * 1000)
        if last and (now_ms - last) < self.cooldown_seconds * 1000:
            self.blocked_count += 1
            return False
        self.store.record_hit(hit)
        # 关掉弹窗时**流水照落**（用户要看历史），只是不弹。开关判断放在监控层，
        # 免得不接 server 的调用方（离线脚本）绕过开关。
        if self.on_event is None or not self.alerts_enabled():
            # 只记录不弹（离线脚本 / 关闭弹窗时）。流水照落，方便回看。
            self.store.mark_alerted(hit)
            return False
        try:
            self.on_event(dict(hit))
        except Exception:
            # 弹窗通道异常绝不能影响扫描；**也不能记账** ——
            # 没弹出去就锁 12 小时，用户就永远看不到这一波了。
            return False
        self.store.mark_alerted(hit)
        return True

    # -- 快照 --------------------------------------------------------------- #
    def refresh_payload_cache(self) -> dict[str, Any]:
        snapshot = self._build_payload()
        with self._payload_lock:
            self._payload_cache = snapshot
            self._payload_cache_at = time.time()
            self._payload_rebuilding = False
        return snapshot

    def _rebuild_payload_cache(self) -> None:
        try:
            self.refresh_payload_cache()
        except Exception as exc:
            with self._payload_lock:
                self._payload_rebuilding = False
            self.last_error = f"快照重建失败：{type(exc).__name__}: {exc}"[:200]

    @staticmethod
    def _clone(snapshot: dict[str, Any]) -> dict[str, Any]:
        clone = dict(snapshot)
        clone["hits"] = [dict(row) for row in snapshot.get("hits") or []]
        clone["rules"] = [dict(spec) for spec in snapshot.get("rules") or []]
        clone["status"] = dict(snapshot.get("status") or {})
        return clone

    def payload(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """接口快照。扫描线程一直在写库，这里绝不排队等写事务。"""
        if force_refresh:
            return self._clone(self.refresh_payload_cache())
        now = time.time()
        with self._payload_lock:
            cached = self._payload_cache
            if cached is not None and now - self._payload_cache_at < BIG_MOVE_PAYLOAD_CACHE_SECONDS:
                return self._clone(cached)
            if cached is not None:
                if not self._payload_rebuilding:
                    self._payload_rebuilding = True
                    threading.Thread(
                        target=self._rebuild_payload_cache,
                        name="big-move-payload-rebuild",
                        daemon=True,
                    ).start()
                return self._clone(cached)
        return self._clone(self.refresh_payload_cache())

    def _build_payload(self) -> dict[str, Any]:
        stats = dict(self._scan_stats or {})
        return {
            "ok": True,
            "enabled": self.alerts_enabled(),
            "cooldownSeconds": int(self.cooldown_seconds),
            "rules": [
                {
                    "id": spec["id"],
                    "days": spec["days"],
                    "thresholdPct": spec["thresholdPct"],
                    "direction": spec["direction"],
                    "label": spec["label"],
                    "headline": spec["headline"],
                    "advice": spec["advice"],
                }
                for spec in self.specs
            ],
            "hits": self.store.recent_hits(BIG_MOVE_HIT_LIMIT),
            "status": {
                **stats,
                "running": self.running(),
                "lastCycleAt": int(self.last_cycle_at * 1000) if self.last_cycle_at else 0,
                "lastError": self.last_error,
                "lastNote": self.last_note,
                "blocked": self.blocked_count,
                "totalHits": self.store.hit_count(),
            },
            "updatedAt": int(time.time() * 1000),
        }
