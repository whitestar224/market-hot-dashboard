"""价格异动监控：**6 小时内涨 ≥20% 就立刻提醒**（用户 2026-10-21 提，独立子板块）。

口径（用户 2026-10-21 第二次拍板 —— **触线即报**）：
* 范围 —— **只有币安 U 本位合约**。不看美股 / A 股；**链上标的（DEX 池里的币）也不进**——
  用户 2026-10-21 上线当天实测：命中的 10 条里 8 条是 Solana/BSC 的 meme（6 小时涨
  20%~205%），20% 这个阈值对它们毫无意义，而链上币的热度已经有 GMGN / AIcoin / 钱包热榜在管。
* 算法 —— **窗口内最低价**当基准：当前价 ÷ 最近 6 小时的最低价 − 1 ≥ 20% 就**当场**弹窗。
  不是「等 6 小时走满再结算」，也不是「拿整 6 小时前那一帧比」：
  - 拿整 6 小时前那一帧比，会把**中途启动**的行情算漏（币在 3 小时前拉起来、之后横盘，
    和 6 小时前比只差 5%，但它是实打实在 6 小时内涨了 25%）；
  - 等窗口走满再结算，等于把提醒推迟最多 6 小时 —— 那时候行情早结束了。
  取最低价的代价是会包含「暴跌后反弹 20%」，但那**确实**是 6 小时内的 20% 涨幅，该报。
* 频率 —— 同一个币 **6 小时**只弹一次；之后只有回落再突破才算新的一波。

**为什么自己采样而不是查 K 线**：500 多个合约逐个拉 1h K 线是一轮 500 个请求，
而 ``/fapi/v1/ticker/price`` 不带 symbol 时**一次返回全部合约的当前价**。所以这里做成
「每轮记一帧价格」，涨跌幅完全靠自己的帧序列算 —— 每轮 1 个请求，还能精确控制窗口。

**代价**：服务刚启动时窗口里只有几帧，基准价就是这几帧的最低点 —— 只覆盖了这么长，
所以「涨幅」是**这段真实跨度**的涨幅，记录里写真实秒数、不冒充 6 小时。
帧落独立 sqlite，重启后还能接着用。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import threading
import time
from typing import Any, Callable

from market_maker_monitor import symbol_key

#: 滚动窗口：6 小时。基准价取**这段窗口里的最低价**。
PRICE_MOVE_WINDOW_SECONDS = 6 * 3600
#: 触发阈值（百分比）。
PRICE_MOVE_THRESHOLD_PCT = 20.0
#: 同一个币的弹窗静默窗口。
PRICE_MOVE_ALERT_COOLDOWN_SECONDS = 6 * 3600
#: 命中流水的写入节流：同一只币 30 分钟内不重复记一条，除非涨幅又抬高了 1 个百分点。
#: 不节流的话「一直在涨」的币每 5 分钟记一条，5 分钟 × 72 帧 = 一个币刷 72 条流水。
MOVE_RECORD_MIN_GAP_SECONDS = 1800
MOVE_RECORD_PCT_STEP = 1.0
#: 至少要两帧才能「比」。只有一帧时最低价就是现价，涨幅恒为 0。
MIN_SCAN_FRAMES = 2
#: 采样节奏。5 分钟一帧 → 6 小时 72 帧，够算基准价又不至于压垮库。
DEFAULT_PRICE_MOVE_INTERVAL = 300.0
#: 帧保留时长：窗口 + 半小时余量（清理用，别把刚过期的基准帧删早了）。
SAMPLE_RETENTION_SECONDS = PRICE_MOVE_WINDOW_SECONDS + 1800
#: 列表里保留多少条命中记录。
DEFAULT_MOVE_LIMIT = 120
#: 弹窗总开关的键名。
ALERTS_SETTING_KEY = "alertsEnabled"
#: 页面快照缓存：采样线程每轮写上百行，GET 不该排在写事务后面（做市商那边踩过）。
PAYLOAD_CACHE_SECONDS = 20.0

#: 这些不是「标的」，涨跌没有意义，进采样只会污染榜单。
CASH_SYMBOLS = frozenset({
    "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "PYUSD",
    "USDE", "USDS", "USD1", "EUR", "EURI", "AEUR", "BKRW", "IDRT",
    "BIDR", "BVND", "VAI", "UST", "USTC",
})
#: 杠杆代币（BTCUPUSDT / ETHDOWNUSDT …）是衍生品，不是现货标的。
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
#: 名字恰好也以 UP / DOWN 结尾的**真币**。SYRUP / PUMP 都被后缀规则误杀过，
#: 而杠杆代币只存在于币安现货，这条 feed 读的是 U 本位合约 —— 宁可漏一个杠杆币，
#: 也不能把一个真标的藏起来。
LEVERAGED_LOOKALIKES = frozenset({"SYRUP", "JUP", "PUMP", "SUPER", "SUP", "BACKUP"})


def runtime_dir() -> Path:
    return Path(os.getenv("XINGYUN_RUNTIME_DIR") or (Path(__file__).resolve().parent / ".runtime-cache"))


def default_db_path() -> Path:
    return runtime_dir() / "price_move_monitor.sqlite"


def is_tradable_symbol(symbol: str) -> bool:
    """稳定币 / 杠杆代币 / 空值一律不当标的。"""
    text = "".join(ch for ch in str(symbol or "").upper() if ch.isdigit() or ch.isalpha())
    if not text:
        return False
    # 先按**原样**判一次：FDUSD 会被 symbol_key 剥成 FD（后缀规则），
    # 剥完就不在稳定币表里了 —— 于是「FDUSD/USDT」被记成一个叫 FD 的币在涨跌。
    if text in CASH_SYMBOLS:
        return False
    key = symbol_key(text)
    if not key or key in CASH_SYMBOLS:
        return False
    if key in LEVERAGED_LOOKALIKES:
        return True
    for suffix in LEVERAGED_SUFFIXES:
        # 只认「像 BTCUP 这种整段后缀」，避免把 JUP / SYRUP 也误杀。
        if len(key) > len(suffix) + 1 and key.endswith(suffix):
            return False
    return True


def pct_change(current: float, base: float) -> float:
    if base <= 0 or current <= 0:
        return 0.0
    return (current / base - 1.0) * 100.0


class PriceMoveStore:
    """帧序列 + 命中记录 + 弹窗台账。独立库，不和 auth_db 抢锁。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self._lock = threading.Lock()
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        # WAL：采样线程一直写，页面一直读，非 WAL 会互相卡（做市商那边踩过 47 秒）。
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
        with self._lock, self._session() as conn:
            conn.executescript(
                """
                -- 一帧 = 某一时刻全市场的价格快照。ts 在所有行里相同，查基准价只用取一帧。
                CREATE TABLE IF NOT EXISTS pm_samples (
                    ts INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    PRIMARY KEY (ts, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_pm_samples_ts ON pm_samples (ts);

                -- 命中记录：列表展示用（谁说涨了 20%、什么时候、从多少涨到多少）。
                CREATE TABLE IF NOT EXISTS pm_moves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    pct REAL NOT NULL,
                    price REAL NOT NULL,
                    base_price REAL NOT NULL,
                    base_ts INTEGER NOT NULL,
                    window_seconds INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    chain TEXT NOT NULL DEFAULT '',
                    contract TEXT NOT NULL DEFAULT '',
                    at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pm_moves_at ON pm_moves (at);

                -- 弹窗台账：同一个币 6 小时内只弹一次。
                CREATE TABLE IF NOT EXISTS pm_alerts (
                    symbol TEXT PRIMARY KEY,
                    last_alert_at INTEGER NOT NULL DEFAULT 0,
                    last_pct REAL NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS pm_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    # -- 采样 --------------------------------------------------------------- #
    def record_frame(self, ts: int, prices: dict[str, float]) -> int:
        rows = [(ts, symbol, float(price)) for symbol, price in prices.items() if price > 0]
        if not rows:
            return 0
        with self._lock, self._session() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO pm_samples (ts, symbol, price) VALUES (?, ?, ?)", rows
            )
        return len(rows)

    def clear_old_samples(self, before_ts: int) -> int:
        with self._lock, self._session() as conn:
            cursor = conn.execute("DELETE FROM pm_samples WHERE ts < ?", (before_ts,))
        return int(cursor.rowcount or 0)

    def frame_at_or_before(self, ts: int) -> tuple[int, dict[str, float]]:
        """取 ``ts`` 之前最近的一帧（整天写的那一帧，不是一个 symbol 一条）。"""
        with self._lock, self._session() as conn:
            row = conn.execute("SELECT MAX(ts) AS ts FROM pm_samples WHERE ts <= ?", (ts,)).fetchone()
            frame_ts = int(row["ts"] or 0) if row else 0
            if not frame_ts:
                return 0, {}
            rows = conn.execute(
                "SELECT symbol, price FROM pm_samples WHERE ts = ?", (frame_ts,)
            ).fetchall()
        return frame_ts, {row["symbol"]: float(row["price"]) for row in rows}

    def window_lows(self, now: int, window_seconds: int) -> dict[str, dict[str, float]]:
        """窗口内每个标的的**最低价**和它出现的时间 —— 「6 小时内涨 20%」的基准。

        取最低价而不是「整 6 小时前那一帧」：币在 3 小时前拉起来再横盘，跟 6 小时前比
        只差 5%，可它实打实在窗口内涨了 25%。用最低价，涨幅一到位就成立。
        """
        since = int(now) - max(0, int(window_seconds))
        with self._lock, self._session() as conn:
            rows = conn.execute(
                # 先按 symbol 取最低价，再回连出**这个最低价最早出现的那一帧**
                "SELECT m.symbol AS symbol, m.low AS low, MIN(s.ts) AS low_ts"
                " FROM (SELECT symbol AS symbol, MIN(price) AS low FROM pm_samples"
                "        WHERE ts >= ? GROUP BY symbol) m"
                " JOIN pm_samples s"
                "   ON s.symbol = m.symbol AND s.ts >= ?"
                "      AND s.price <= m.low * 1.000000001"
                " GROUP BY m.symbol",
                (since, since),
            ).fetchall()
        return {
            str(row["symbol"]): {"price": float(row["low"]), "ts": int(row["low_ts"] or 0)}
            for row in rows
        }

    def last_move(self, symbol: str) -> tuple[int, float]:
        """这只币上一次记流水的 ``(时间毫秒, 涨幅)``；没记过就是 ``(0, 0.0)``。"""
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT at, pct FROM pm_moves WHERE symbol = ? ORDER BY at DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        return (int(row["at"] or 0), float(row["pct"] or 0)) if row else (0, 0.0)

    def sample_stats(self) -> dict[str, Any]:
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ts) AS frames, COUNT(*) AS rows,"
                " MIN(ts) AS oldest, MAX(ts) AS newest FROM pm_samples"
            ).fetchone()
            symbols = conn.execute("SELECT COUNT(DISTINCT symbol) AS n FROM pm_samples").fetchone()
        return {
            "frames": int(row["frames"] or 0),
            "rows": int(row["rows"] or 0),
            "symbols": int(symbols["n"] or 0),
            "oldest": int(row["oldest"] or 0),
            "newest": int(row["newest"] or 0),
        }

    # -- 命中 / 台账 -------------------------------------------------------- #
    def record_move(self, row: dict[str, Any]) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO pm_moves (symbol, pct, price, base_price, base_ts, window_seconds,"
                " source, chain, contract, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["symbol"], float(row["pct"]), float(row["price"]),
                    float(row["basePrice"]), int(row["baseTs"]), int(row["windowSeconds"]),
                    str(row.get("source") or ""), str(row.get("chain") or ""),
                    str(row.get("contract") or ""), int(row["at"]),
                ),
            )
            # 只留最近 800 条：这是个「流水」，不是账本。
            conn.execute(
                "DELETE FROM pm_moves WHERE id NOT IN"
                " (SELECT id FROM pm_moves ORDER BY id DESC LIMIT 800)"
            )

    def recent_moves(self, limit: int = DEFAULT_MOVE_LIMIT) -> list[dict[str, Any]]:
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_moves ORDER BY at DESC, pct DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def alert_state(self, symbol: str) -> dict[str, Any]:
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT * FROM pm_alerts WHERE symbol = ?", (symbol,)
            ).fetchone()
        return dict(row) if row else {}

    def mark_alerted(self, symbol: str, pct: float) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO pm_alerts (symbol, last_alert_at, last_pct) VALUES (?, ?, ?)"
                " ON CONFLICT(symbol) DO UPDATE SET"
                " last_alert_at = excluded.last_alert_at, last_pct = excluded.last_pct",
                (symbol, int(time.time() * 1000), float(pct)),
            )

    # -- 设置 --------------------------------------------------------------- #
    def setting(self, key: str, default: str = "") -> str:
        with self._lock, self._session() as conn:
            row = conn.execute("SELECT value FROM pm_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._session() as conn:
            conn.execute(
                "INSERT INTO pm_settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def alerts_enabled(self) -> bool:
        return self.setting(ALERTS_SETTING_KEY, "1") != "0"

    def set_alerts_enabled(self, value: Any) -> bool:
        self.set_setting(ALERTS_SETTING_KEY, "1" if value else "0")
        return self.alerts_enabled()


class PriceMoveMonitor:
    """每轮记一帧价格，用 6 小时前的帧算涨幅，超阈值的进列表并按需弹窗。"""

    def __init__(
        self,
        store: PriceMoveStore | None = None,
        *,
        interval: float | None = None,
        window_seconds: int = PRICE_MOVE_WINDOW_SECONDS,
        threshold_pct: float = PRICE_MOVE_THRESHOLD_PCT,
        cooldown_seconds: float = PRICE_MOVE_ALERT_COOLDOWN_SECONDS,
        alert_callback: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.store = store or PriceMoveStore()
        self.interval = float(
            interval if interval is not None
            else (os.getenv("PRICE_MOVE_SECONDS") or DEFAULT_PRICE_MOVE_INTERVAL)
        )
        self.window_seconds = int(window_seconds)
        self.threshold_pct = float(threshold_pct)
        self.cooldown_seconds = float(cooldown_seconds)
        self.on_event = alert_callback
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycle_lock = threading.Lock()
        self._lock = threading.Lock()
        # 数据由 server 注入：币安 U 本位全市场价。链上标的 2026-10-21 起不进这个板块。
        self.market_provider: Callable[[], dict[str, float]] | None = None
        self.last_cycle_at = 0.0
        self.last_error = ""
        self.last_note = ""
        self.blocked_count = 0
        # 最近一次 scan 的结果：页面列表直接读它，不再每次重建都去扫 6 小时窗口。
        self._live: list[dict[str, Any]] = []
        self._live_at = 0.0
        # 页面快照缓存（见 payload()）。
        self._payload_lock = threading.Lock()
        self._payload_cache: dict[str, Any] | None = None
        self._payload_cache_at = 0.0
        self._payload_rebuilding = False

    # -- 注入 --------------------------------------------------------------- #
    def set_market_provider(self, provider: Callable[[], dict[str, float]]) -> None:
        self.market_provider = provider

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
                target=self._loop, name="price-move-monitor", daemon=True
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
        self.stop_event.wait(45)
        while not self.stop_event.is_set():
            started = time.time()
            try:
                self.run_cycle()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            remaining = self.interval - (time.time() - started)
            self.stop_event.wait(max(5.0, remaining))

    # -- 一轮 --------------------------------------------------------------- #
    def collect_prices(self) -> tuple[dict[str, float], list[str]]:
        """取这一帧的全市场价格。返回 ``(价格, 失败原因)``。"""
        prices: dict[str, float] = {}
        problems: list[str] = []
        if self.market_provider is not None:
            try:
                for symbol, price in (self.market_provider() or {}).items():
                    key = symbol_key(symbol)
                    if is_tradable_symbol(key) and float(price) > 0:
                        prices[key] = float(price)
            except Exception as exc:
                problems.append(f"合约行情：{type(exc).__name__}")
        else:
            problems.append("合约行情未接线")
        return prices, problems

    def run_cycle(self) -> dict[str, int]:
        if not self._cycle_lock.acquire(blocking=False):
            return {"inserted": 0, "moves": 0, "busy": 1}
        try:
            now = int(time.time())
            prices, problems = self.collect_prices()
            if not prices:
                # 抓取全挂时**不写空帧**：写空帧会把这一轮的采样点抹掉，
                # 6 小时后基准价就变成「不可用」，等于把一整段窗口弄瞎。
                self.last_error = "、".join(problems) or "没有拿到任何价格"
                self.last_note = "本轮没有可用行情，未记录采样"
                return {"inserted": 0, "moves": 0}
            inserted = self.store.record_frame(now, prices)
            self.store.clear_old_samples(now - SAMPLE_RETENTION_SECONDS)
            self.last_error = "、".join(problems)
            self.last_cycle_at = time.time()
            moves = self.evaluate(now, prices)
            self.last_note = self.build_note(now)
            try:
                self.refresh_payload_cache()
            except Exception as exc:
                self.last_error = f"快照刷新失败：{type(exc).__name__}: {exc}"[:200]
            return {"inserted": inserted, "moves": moves}
        finally:
            self._cycle_lock.release()

    def scan(self, now: int, prices: dict[str, float]) -> list[dict[str, Any]]:
        """这一帧里，相对**窗口最低价**涨够阈值的标的。当场成立，不等窗口走满。"""
        if not prices:
            return []
        lows = self.store.window_lows(now, self.window_seconds)
        if not lows:
            return []
        at_ms = int(time.time() * 1000)
        moves: list[dict[str, Any]] = []
        for symbol, price in prices.items():
            low = lows.get(symbol)
            base = float((low or {}).get("price") or 0)
            if base <= 0:
                continue
            pct = pct_change(float(price), base)
            # 容差 1e-9：120/100 在浮点里是 0.19999999999999996，直接比 20 会漏掉
            # 恰好涨 20% 的标的（阈值本身就是「20%」，边界必须算命中）。
            if pct < self.threshold_pct - 1e-9:
                continue
            base_ts = int((low or {}).get("ts") or 0)
            # 真实跨度：最低价那一刻到现在。窗口没走满时它就是实际覆盖长度，不冒充 6 小时。
            actual_window = max(1, now - base_ts) if base_ts else self.window_seconds
            moves.append({
                "symbol": symbol,
                "pct": round(pct, 2),
                "price": float(price),
                "basePrice": base,
                "baseTs": base_ts,
                "windowSeconds": actual_window,
                "source": "币安合约",
                # 链上标的不进这个板块，但行结构保持不变：下游（链接、弹窗标链）
                # 一律按「有链就用、没有就退回中心化」处理。
                "chain": "",
                "contract": "",
                "at": at_ms,
            })
        moves.sort(key=lambda row: -row["pct"])
        return moves

    def evaluate(self, now: int, prices: dict[str, float]) -> int:
        """算出这一帧的异动、记流水、该弹的当场弹掉。返回弹了几条。"""
        moves = self.scan(now, prices)
        # 页面列表直接读这份（live），不再靠流水表反推「谁还在涨」——
        # 流水是**事件**（什么时候第一次触线、什么时候创新高），节流后不保证每帧都有。
        with self._lock:
            self._live = moves
            self._live_at = time.time()
        fired = 0
        for row in moves:
            if self.should_record(row["symbol"], row["pct"], row["at"]):
                self.store.record_move(row)
            if self.maybe_alert(row):
                fired += 1
        return fired

    def should_record(self, symbol: str, pct: float, at_ms: int) -> bool:
        """流水要不要落一条：没记过 / 隔了 30 分钟 / 涨幅又抬高了 1 个点。"""
        last_at, last_pct = self.store.last_move(symbol)
        if not last_at:
            return True
        if at_ms - last_at >= MOVE_RECORD_MIN_GAP_SECONDS * 1000:
            return True
        return pct >= last_pct + MOVE_RECORD_PCT_STEP

    def maybe_alert(self, row: dict[str, Any]) -> bool:
        """同一个币 6 小时只弹一次。**先判断再记账**，mark 放最后。"""
        if not self.alerts_enabled() or self.on_event is None:
            return False
        state = self.store.alert_state(row["symbol"])
        last = int(state.get("last_alert_at") or 0)
        if last and (time.time() * 1000 - last) < self.cooldown_seconds * 1000:
            self.blocked_count += 1
            return False
        try:
            self.on_event(dict(row, reason="price-move", thresholdPct=self.threshold_pct))
        except Exception:
            # 弹窗通道异常绝不能影响采样循环；但**也不能记账** ——
            # 没弹出去就锁 6 小时，用户就永远看不到这一波了。
            return False
        self.store.mark_alerted(row["symbol"], float(row["pct"]))
        return True

    def build_note(self, now: int) -> str:
        """页面顶部说明。**不再是「没凑满 6 小时就不报」**——那正是要改掉的行为。"""
        stats = self.store.sample_stats()
        if not stats["frames"]:
            return "还没有采样帧"
        if stats["frames"] < MIN_SCAN_FRAMES:
            return "只有 1 帧，下一帧记下来就能开始比"
        span = max(0, now - (stats["oldest"] or now)) if stats["oldest"] else 0
        if span < self.window_seconds:
            minutes = max(1, int(span // 60))
            return (
                f"窗口未满：已覆盖 {minutes} 分钟，基准价取这段时间的最低价"
                f"（涨够 {self.threshold_pct:g}% 照样立刻提醒）"
            )
        return ""

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
        """浅拷贝到一个页面调用方可以随便改的副本。

        ``dict(cached)`` 不够：``moves`` 里的 dict 还是缓存里那几个，server 给每行
        补 ``url`` 时会**直接改到缓存对象**（做市商那边踩过：缓存被调用方改脏）。
        """
        clone = dict(snapshot)
        clone["moves"] = [dict(row) for row in snapshot.get("moves") or []]
        clone["summary"] = dict(snapshot.get("summary") or {})
        return clone

    def payload(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """页面快照。采样线程一直写库，这里**绝不排队等写事务**（做市商那边踩过 47 秒）。"""
        if force_refresh:
            return self._clone(self.refresh_payload_cache())
        now = time.time()
        with self._payload_lock:
            cached = self._payload_cache
            if cached is not None and now - self._payload_cache_at < PAYLOAD_CACHE_SECONDS:
                return self._clone(cached)
            if cached is not None:
                if not self._payload_rebuilding:
                    self._payload_rebuilding = True
                    threading.Thread(
                        target=self._rebuild_payload_cache,
                        name="price-move-payload-rebuild",
                        daemon=True,
                    ).start()
                return self._clone(cached)
        return self._clone(self.refresh_payload_cache())

    def _build_payload(self) -> dict[str, Any]:
        now = int(time.time())
        stats = self.store.sample_stats()
        rows = self.store.recent_moves(DEFAULT_MOVE_LIMIT)
        # 双保险：采集层已经不再收链上标的，但库里还留着上线首日的 8 条链上命中
        # （EARTH +205%、HOOT +37%…）。过滤放在服务端，前端不该看到它们。
        rows = [row for row in rows if (row["source"] or "币安合约") == "币安合约"]
        total_count = len(rows)
        with self._lock:
            live = [dict(row) for row in self._live]
            live_at = self._live_at
        if live:
            # **当前异动榜 = 这一帧实算出来的**（相对窗口最低价还涨着 ≥20% 的）。
            # 不从流水表反推：流水是事件日志，节流后不是每帧都有一条。
            logged: dict[str, dict[str, Any]] = {}
            for row in rows:
                logged.setdefault(row["symbol"], row)
            moves = []
            for item in live:
                hit = logged.get(item["symbol"]) or {}
                detected = int(hit.get("at") or 0)
                base_ms = int(item.get("baseTs") or 0) * 1000
                moves.append({
                    "symbol": item["symbol"],
                    "pct": round(float(item["pct"]), 2),
                    "pctText": f"{float(item['pct']):+.1f}%",
                    "price": float(item["price"]),
                    "basePrice": float(item["basePrice"]),
                    "baseTs": int(item.get("baseTs") or 0),
                    "windowSeconds": int(item.get("windowSeconds") or 0),
                    "windowText": f"{round(int(item.get('windowSeconds') or 0) / 3600, 1)} 小时",
                    "source": item.get("source") or "币安合约",
                    "chain": item.get("chain") or "",
                    "contract": item.get("contract") or "",
                    # 「记录于」= 第一次触线的时刻；没记过流水就是这个理由当前这一帧。
                    "at": detected if detected >= base_ms else int(item["at"]),
                    "detectedAt": detected,
                })
            current = len(live)
        else:
            # 还没采过帧（服务刚起 / 采样线程停了）：退回流水表，页面不至于空白。
            # 一只币只留最近一条 —— 流水里同一只币可能有好几条（创新高会再记一条）。
            latest_by_symbol: dict[str, dict[str, Any]] = {}
            for row in rows:
                latest_by_symbol.setdefault(row["symbol"], row)
            rows = list(latest_by_symbol.values())
            latest_ts = stats["newest"] or 0
            fresh_cutoff = (latest_ts - 900) if latest_ts else 0
            current = sum(1 for row in rows if int(row["at"] or 0) >= fresh_cutoff * 1000)
            moves = []
            for row in rows:
                moves.append({
                    "symbol": row["symbol"],
                    "pct": round(float(row["pct"]), 2),
                    "pctText": f"{float(row['pct']):+.1f}%",
                    "price": float(row["price"]),
                    "basePrice": float(row["base_price"]),
                    "baseTs": int(row["base_ts"] or 0),
                    "windowSeconds": int(row["window_seconds"] or 0),
                    "windowText": f"{round(int(row['window_seconds'] or 0) / 3600, 1)} 小时",
                    "source": row["source"],
                    "chain": row["chain"],
                    "contract": row["contract"],
                    "at": int(row["at"] or 0),
                    "detectedAt": int(row["at"] or 0),
                })
        covered = stats["symbols"]
        coverage = max(0, (stats["newest"] or now) - stats["oldest"]) if stats["oldest"] else 0
        return {
            "ok": True,
            "now": now * 1000,
            "running": self.running(),
            "intervalSeconds": self.interval,
            "windowSeconds": self.window_seconds,
            "thresholdPct": self.threshold_pct,
            "cooldownSeconds": self.cooldown_seconds,
            "lastCycleAt": int(self.last_cycle_at * 1000) if self.last_cycle_at else 0,
            "lastError": self.last_error,
            "note": self.build_note(now),
            "moves": moves,
            "summary": {
                "running": self.running(),
                "alertsEnabled": self.alerts_enabled(),
                "symbols": covered,
                "frames": stats["frames"],
                "oldestFrameAt": stats["oldest"] * 1000,
                "newestFrameAt": stats["newest"] * 1000,
                # 已覆盖多长的窗口：页面用它说清「还没满 6 小时，但已经在比了」。
                "coverageSeconds": coverage,
                "liveAt": int(live_at * 1000) if live_at else 0,
                "current": current,
                "total": total_count,
                "blocked": self.blocked_count,
                "intervalSeconds": self.interval,
                "windowSeconds": self.window_seconds,
                "thresholdPct": self.threshold_pct,
                "cooldownSeconds": self.cooldown_seconds,
                "lastCycleAt": int(self.last_cycle_at * 1000) if self.last_cycle_at else 0,
                "lastError": self.last_error,
            },
        }


def main(argv: list[str] | None = None) -> int:
    """命令行自查：``python price_move_monitor.py --once`` 记一帧并打印结果。"""
    import argparse

    parser = argparse.ArgumentParser(description="价格异动监控（6 小时涨 20%）")
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    parser.add_argument("--stats", action="store_true", help="只打印采样统计")
    args = parser.parse_args(argv)
    store = PriceMoveStore()
    if args.stats:
        print(json.dumps(store.sample_stats(), ensure_ascii=False, indent=2))
        return 0
    monitor = PriceMoveMonitor(store)
    if args.once:
        print(json.dumps(monitor.run_cycle(), ensure_ascii=False))
        return 0
    monitor.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
