"""弹窗 / 战壕悬浮窗里的 CoinMarketCap 基本面。

2026-10-24 首版只给弹窗加「流通市值」；2026-10-25 用户对着 CMC 代币页的截图要求
把基本面四项一起摆出来（市值 / 流通供应量 / 流通率 / 总供应量），于是
``_build_snapshot`` 多存 ``totalSupply / maxSupply / fullyDilluttedMarketCap``，
文案由 ``body_text``（弹窗整句）和 ``fundamentals_text``（悬浮窗短句）分别给出。

## 数据源：免 key 的 CMC 网页端 data-api

``https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing``（按市值排序的
整页榜单）—— 2026-10-24 实测：免 key、一次最多 5000 行、每行带
``symbol / slug / cmcRank / circulatingSupply / quotes[0].marketCap / isActive``。
其余路全堵死：``search`` 系端点 404、``detail?address=`` 报「Id or slug must be
provided」、``quotes/latest`` 404、``detail?slug=`` 能通但 slug 猜不中就是空壳。

所以策略是**整榜快照**：一次拉 5000 名（约 6 MB），内存里建 ``symbol → 最佳行``
的倒排，弹窗路径只查内存，零网络等待；后台线程按 TTL 刷新。快照落盘
``.runtime-cache/cmc_market_cap.json``，重启服务头几分钟也有数可用。

## symbol 撞车：选 cmcRank 最小的那一行

CMC 上大量山寨币共用同一个 ticker（实测 ``XRP``：真身 id 52 / rank 5 / 市值 810 亿，
山寨 id 27809 / rank 2770 / 市值 18 万）。规则：**流通市值 > 0 且 cmcRank 最小**的行
胜出——rank 5 的 XRP 永远压过 rank 2770 的假 XRP。``isActive=0`` 的行直接不参选。

## 失败纪律（和 Top10 同一条铁律）

拿不到就**沉默**：``lookup`` 返回非 ok 状态，调用方不加任何正文、绝不写
``$0``。宁可这条弹窗没有市值，也不给一个别的币的数字。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

import requests

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_LISTING_URL = (
    "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
    "?start=1&limit={limit}&sortBy=market_cap&sortType=desc&convert=USD"
)

#: 整榜刷新间隔。市值不是秒级指标，弹窗场景 4 小时足够。
REFRESH_TTL_SECONDS = 4 * 3600
#: 快照还是空的时候，弹窗路径**只异步 kick 后台刷新，绝不等待**——2026-10-24 实测
#: 一次整榜下载+解析要 ~54 秒，这种等待发生在弹窗投递路径上等于卡死第一条弹窗。
#: 冷启动覆盖靠两件事兜底：``start()`` 起线程时立刻 kick 一次 + 快照落盘（重启即有数）。
ASYNC_WARM_ONLY = True

SNAPSHOT_FILENAME = "cmc_market_cap.json"


def cn_usd_text(value: Any) -> str:
    """美元金额的中文读法：1.62e12 → ``$1.62万亿``、39758882 → ``$3976万``。"""
    number = float(value or 0)
    if number >= 1e12:
        return f"${number / 1e12:.2f}万亿"
    if number >= 1e8:
        return f"${number / 1e8:.2f}亿"
    if number >= 1e4:
        return f"${number / 1e4:,.0f}万"
    return f"${number:,.0f}"


def supply_text(value: Any) -> str:
    """代币数量的中文读法：10亿 → ``10亿``、256000000 → ``2.56亿``、14710000 → ``1471万``。

    量级差得太远：BTC 是 2010 万枚，SHIB 是 589 万亿枚。统一成「亿/万」两位小数，
    弹窗和悬浮窗那一行才放得下。
    """
    number = float(value or 0)
    if number <= 0:
        return ""
    if number >= 1e12:
        return f"{number / 1e12:.2f}万亿"
    if number >= 1e8:
        return f"{number / 1e8:.2f}亿"
    if number >= 1e4:
        return f"{number / 1e4:,.1f}万"
    return f"{number:,.0f}"



class CmcMarketCap:
    """整榜快照缓存 + 后台刷新。弹窗路径只碰 ``lookup``（纯内存读）。"""

    # 展示用的文本函数挂在类上，server.py 里 ``CMC_MARKET_CAP.supply_text(...)``
    # 才拿得到（模块级函数不会自动变成实例属性）。
    cn_usd_text = staticmethod(cn_usd_text)
    supply_text = staticmethod(supply_text)

    def __init__(
        self,
        *,
        max_rows: int | None = None,
        fetcher: Callable[[str], dict[str, Any]] | None = None,
        snapshot_path: str | None = None,
    ) -> None:
        default_rows = int(os.environ.get("XINGYUN_CMC_MAX_ROWS", "5000") or 5000)
        self.max_rows = max_rows if max_rows and max_rows > 0 else max(200, default_rows)
        self._fetcher = fetcher or self._http_fetch
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime-cache")
        self.snapshot_path = snapshot_path or os.path.join(base_dir, SNAPSHOT_FILENAME)
        self._enabled = False
        # ``{symbol.casefold(): entry}`` —— copy-on-write，读侧不拿锁。
        self._snapshot: dict[str, dict[str, Any]] = {}
        self._snapshot_loaded_from_disk = False
        self._refreshed_at = 0.0
        self._refresh_lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def enable(self, flag: bool = True) -> None:
        """``False`` = 只读缓存、一次都不联网（测试与离线脚本的纪律）。"""
        self._enabled = bool(flag)

    def start(self) -> None:
        """起后台刷新线程。已起过 / 未 enable 就什么都不做。"""
        if self._worker is not None or not self._enabled:
            return
        self._load_disk_snapshot()
        self._refresh_async()
        worker = threading.Thread(target=self._loop, name="cmc-market-cap", daemon=True)
        self._worker = worker
        worker.start()

    def _loop(self) -> None:
        while True:
            time.sleep(REFRESH_TTL_SECONDS)
            try:
                self.refresh()
            except Exception:
                # 后台线程永远不死；下一轮再试。
                pass

    def _refresh_async(self) -> None:
        def kick() -> None:
            try:
                self.refresh()
            except Exception:
                pass

        threading.Thread(target=kick, name="cmc-market-cap-warm", daemon=True).start()

    # ------------------------------------------------------------------
    # 抓取与快照构建
    # ------------------------------------------------------------------
    @staticmethod
    def _http_fetch(url: str) -> dict[str, Any]:
        # 跟随系统代理（requests 默认 trust_env=True，项目铁律：禁固化直连）。
        response = requests.get(
            url,
            headers={"User-Agent": _DEFAULT_UA, "Accept": "application/json"},
            timeout=(15, 60),
        )
        response.raise_for_status()
        return response.json()

    def refresh(self) -> int:
        """拉一版整榜快照；返回收录行数。失败抛异常（由调用方决定怎么兜）。"""
        with self._refresh_lock:
            payload = self._fetcher(_LISTING_URL.format(limit=self.max_rows))
            rows = (
                ((payload or {}).get("data") or {}).get("cryptoCurrencyList") or []
                if isinstance(payload, dict)
                else []
            )
            snapshot = self._build_snapshot(rows)
            if not snapshot:
                raise ValueError("cmc listing returned no usable rows")
            self._snapshot = snapshot
            self._refreshed_at = time.time()
            self._save_disk_snapshot()
            return len(snapshot)

    @staticmethod
    def _build_snapshot(rows: Any) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        if not isinstance(rows, list):
            return snapshot
        for row in rows:
            if not isinstance(row, dict):
                continue
            if int(row.get("isActive") or 0) <= 0:
                continue
            symbol = str(row.get("symbol") or "").strip().casefold()
            if not symbol:
                continue
            quotes = row.get("quotes") if isinstance(row.get("quotes"), list) else []
            quote = quotes[0] if quotes and isinstance(quotes[0], dict) else {}
            mcap = float(quote.get("marketCap") or 0)
            if mcap <= 0:
                continue
            candidate = {
                "symbol": str(row.get("symbol") or "").strip(),
                "name": str(row.get("name") or "")[:80],
                "slug": str(row.get("slug") or "")[:120],
                "rank": int(row.get("cmcRank") or 0),
                "mcapUsd": mcap,
                "circulatingSupply": float(row.get("circulatingSupply") or 0),
                "totalSupply": float(row.get("totalSupply") or 0),
                "maxSupply": float(row.get("maxSupply") or 0),
                # CMC 原文就是这个拼写（三个 t），别"顺手修正"成 fullyDiluted。
                "fdvUsd": float(quote.get("fullyDilluttedMarketCap") or 0),
                "priceUsd": float(quote.get("price") or 0),
            }
            current = snapshot.get(symbol)
            # 撞 ticker 时 rank 最小的赢（rank 0 视为最大，别让异常行抢真身）。
            if current is not None and current.get("rank", 10**9) <= (
                candidate["rank"] or 10**9
            ):
                continue
            snapshot[symbol] = candidate
        return snapshot

    # ------------------------------------------------------------------
    # 落盘 / 读盘（重启头几分钟也有数）
    # ------------------------------------------------------------------
    def _save_disk_snapshot(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.snapshot_path), exist_ok=True)
            tmp_path = f"{self.snapshot_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump({"savedAt": time.time(), "rows": self._snapshot}, handle)
            os.replace(tmp_path, self.snapshot_path)
        except Exception:
            pass  # 缓存文件写不了不影响主流程。

    def _load_disk_snapshot(self) -> bool:
        if self._snapshot_loaded_from_disk or self._snapshot:
            return bool(self._snapshot)
        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            snapshot = data.get("rows") if isinstance(data, dict) else None
            if isinstance(snapshot, dict) and snapshot:
                self._snapshot = snapshot
                self._refreshed_at = float(data.get("savedAt") or 0)
                self._snapshot_loaded_from_disk = True
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # 查询（弹窗路径；纯内存，唯一可能的等待是「快照还是空的」时同步拉一次）
    # ------------------------------------------------------------------
    def lookup(self, symbol: Any) -> dict[str, Any]:
        key = str(symbol or "").strip().casefold()
        if not key:
            return {"status": "no-symbol"}
        if not self._enabled:
            return {"status": "disabled"}
        if not self._snapshot and not self._load_disk_snapshot():
            # 冷启动：快照还没有 —— 只 kick 后台刷新（异步），弹窗立即拿空状态，
            # 绝不在投递路径上等那 ~54 秒的整榜下载。
            self._refresh_async()
            return {"status": "empty"}
        row = self._snapshot.get(key)
        if not row:
            return {"status": "unlisted"}
        return {"status": "ok", **row}

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    @staticmethod
    def fundamentals(entry: dict[str, Any]) -> dict[str, Any]:
        """把一条快照整理成「基本面」四项，缺哪项就留 0（调用方据此省略）。

        * ``circulating`` 流通供应量
        * ``total`` 总供应量（CMC 的 ``totalSupply``，拿不到退 ``maxSupply``）
        * ``floatRatio`` 流通率 = 流通量 ÷ 总量（百分数）—— 用户的 CMC 截图里
          这一项是「市值 ÷ FDV」，两种算法在总量=最大供应时相同；CMC 的 totalSupply
          已经扣过销毁，用它更贴近页面上的「流通率」。
        """
        circulating = float(entry.get("circulatingSupply") or 0)
        total = float(entry.get("totalSupply") or 0) or float(entry.get("maxSupply") or 0)
        ratio = (circulating / total * 100.0) if circulating > 0 and total > 0 else 0.0
        return {
            "circulating": circulating,
            "total": total,
            "max": float(entry.get("maxSupply") or 0),
            "floatRatio": round(ratio, 2) if 0 < ratio <= 100.5 else 0.0,
            "fdvUsd": float(entry.get("fdvUsd") or 0),
        }

    @classmethod
    def fundamentals_text(cls, entry: dict[str, Any]) -> str:
        """``流通 2.56亿 · 流通率 25.6% · 总量 10.00亿``；全缺就返回空串。"""
        if not isinstance(entry, dict) or entry.get("status") != "ok":
            return ""
        info = cls.fundamentals(entry)
        parts: list[str] = []
        circulating = supply_text(info["circulating"])
        if circulating:
            parts.append(f"流通 {circulating}")
        if info["floatRatio"]:
            parts.append(f"流通率 {info['floatRatio']:.1f}%")
        total = supply_text(info["total"])
        if total:
            parts.append(f"总量 {total}")
        return " · ".join(parts)

    @classmethod
    def body_text(cls, entry: dict[str, Any]) -> str:
        """弹窗正文里的那句话；``ok`` 以外的状态一律返回空串（沉默纪律）。

        2026-10-25 起带上基本面四项（用户对着 CMC 代币页的截图点的）：
        ``流通市值 $6070万（CMC #192） · 流通 2.56亿 · 流通率 25.6% · 总量 10.00亿``
        """
        if not isinstance(entry, dict) or entry.get("status") != "ok":
            return ""
        mcap = float(entry.get("mcapUsd") or 0)
        if mcap <= 0:
            return ""
        rank = int(entry.get("rank") or 0)
        tail = f"CMC #{rank}" if rank > 0 else "CMC"
        head = f"流通市值 {cn_usd_text(mcap)}（{tail}）"
        extra = cls.fundamentals_text(entry)
        return f"{head} · {extra}" if extra else head


_DEFAULT = CmcMarketCap()


def default_store() -> CmcMarketCap:
    return _DEFAULT
