"""大幅异动监控（3 日跌 50% / 2 日涨 100%）的规则、冷却与弹窗文案。

弹窗那段按 AST 从 server.py 里抽出来真跑（不 import 整个 server），与
``test_price_move_api.py`` / ``test_market_maker_api.py`` 同一套手法：
替换的只有外部副作用（``launch_desktop_alert``），文案全走真实现。
"""

import ast
import json
import tempfile
import time
import unittest
from pathlib import Path

import big_move_monitor as bm

ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT.joinpath("server.py").read_text(encoding="utf-8")
_EXTRACTED = {}


def clean_feed_text(value, limit=200):
    return str(value or "").strip()[:limit]


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def function_code(*names):
    key = tuple(names)
    if key not in _EXTRACTED:
        tree = ast.parse(SERVER_SOURCE)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        found = {node.name for node in nodes}
        missing = set(names) - found
        if missing:
            raise AssertionError(f"server.py 里找不到：{sorted(missing)}")
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *nodes,
        ], type_ignores=[])
        _EXTRACTED[key] = compile(ast.fix_missing_locations(module), "server.py", "exec")
    return _EXTRACTED[key]


def candle(day, *, high, close, low, open_=None, volume=1000.0):
    """项目统一的 K 线形状：``(时间, 最高, 收盘, 开盘, 最低, 量)``。"""
    return (
        day * 86_400_000,
        float(high),
        float(close),
        float(close if open_ is None else open_),
        float(low),
        float(volume),
    )


class BigMoveRuleTests(unittest.TestCase):
    """两条规则的边界。跌 50% / 涨 100% 都是**含边界**命中。"""

    def test_three_day_drop_from_window_high(self):
        candles = [
            candle(0, high=1.00, close=0.98, low=0.95),
            candle(1, high=1.10, close=1.05, low=1.00),   # 窗口最高 1.10
            candle(2, high=0.90, close=0.70, low=0.65),
            candle(3, high=0.60, close=0.50, low=0.48),   # 现价 0.50 → 相对 1.10 是 −54.5%
        ]
        hits = bm.analyze_candles({"symbol": "AAA"}, candles, now_ms=1)
        self.assertEqual([hit["rule"] for hit in hits], [bm.RULE_DROP])
        hit = hits[0]
        self.assertAlmostEqual(hit["pct"], -54.55, places=2)
        self.assertEqual(hit["basePrice"], 1.10)
        self.assertEqual(hit["price"], 0.50)
        self.assertEqual(hit["days"], 3)
        # 窗口只有最近三根日线：最高价取自第 2 根，不是第 1 根更早的价。
        self.assertEqual(hit["candles"], 3)

    def test_drop_that_is_not_deep_enough_is_ignored(self):
        candles = [
            candle(0, high=1.00, close=1.00, low=0.99),
            candle(1, high=1.10, close=1.05, low=1.00),
            candle(2, high=0.95, close=0.70, low=0.68),   # 现价 0.70 → −36.4%
        ]
        self.assertEqual(bm.analyze_candles({"symbol": "AAA"}, candles, now_ms=1), [])

    def test_exactly_fifty_percent_drop_counts(self):
        """边界必须算命中：``0.5/1.0-1`` 已经贴着浮点误差在跑。"""
        candles = [
            candle(0, high=1.00, close=1.00, low=0.99),
            candle(1, high=0.80, close=0.60, low=0.55),
            candle(2, high=0.52, close=0.50, low=0.49),
        ]
        hits = bm.analyze_candles({"symbol": "AAA"}, candles, now_ms=1)
        self.assertEqual([hit["rule"] for hit in hits], [bm.RULE_DROP])
        self.assertAlmostEqual(hits[0]["pct"], -50.0, places=6)

    def test_two_day_double_counts_at_the_boundary(self):
        """涨 100% 与跌 50% 是同一件事的两个方向，边界也得算命中。"""
        candles = [
            candle(0, high=0.42, close=0.35, low=0.33),
            candle(1, high=0.45, close=0.40, low=0.30),   # 最近 2 日的最低价 0.30
            candle(2, high=0.62, close=0.60, low=0.58),   # 现价 0.60 → +100%（浮点上差一点点）
        ]
        hits = bm.analyze_candles({"symbol": "BBB"}, candles, now_ms=1)
        self.assertEqual([hit["rule"] for hit in hits], [bm.RULE_PUMP])
        self.assertAlmostEqual(hits[0]["pct"], 100.0, places=6)
        self.assertEqual(hits[0]["basePrice"], 0.30)
        self.assertEqual(hits[0]["days"], 2)

    def test_pump_below_threshold_is_ignored(self):
        candles = [
            candle(0, high=1.10, close=1.00, low=1.00),
            candle(1, high=1.90, close=1.70, low=1.50),   # 现价 1.70 → 相对最低 1.00 是 +70%
        ]
        self.assertEqual(bm.analyze_candles({"symbol": "BBB"}, candles, now_ms=1), [])

    def test_window_uses_only_the_last_n_days(self):
        """老早以前的高点不该把今天的跌幅算成 50%：窗口是「最近 3 根日线」。"""
        candles = [
            candle(0, high=10.0, close=10.0, low=9.9),    # 10 天前的天价，已滚出窗口
            candle(1, high=1.00, close=1.00, low=0.98),
            candle(2, high=1.02, close=1.00, low=0.97),
            candle(3, high=1.01, close=0.99, low=0.96),
        ]
        self.assertEqual(bm.analyze_candles({"symbol": "CCC"}, candles, now_ms=1), [])

    def test_single_candle_never_fires(self):
        """一根日线里最高/最低/收盘是同一段时间的三个点，拿它算「跌 50%」是自欺。"""
        candles = [candle(0, high=10.0, close=0.1, low=0.05)]
        self.assertEqual(bm.analyze_candles({"symbol": "DDD"}, candles, now_ms=1), [])

    def test_broken_rows_are_dropped_not_trusted(self):
        """0 价行会让「跌 100%」这种假信号满天飞，必须在归一化时剔掉。"""
        candles = [
            candle(0, high=1.00, close=1.00, low=0.99),
            (1, 0.0, 0.0, 0.0, 0.0, 0.0),                 # 坏行
            {"time": 2, "high": 0.9, "close": 0.4, "low": 0.35},
            ("x", "y"),
        ]
        hits = bm.analyze_candles({"symbol": "EEE"}, candles, now_ms=1)
        # 只剩 2 根有效日线：最高 1.00 → 0.40 = −60%。
        self.assertEqual([hit["rule"] for hit in hits], [bm.RULE_DROP])
        self.assertAlmostEqual(hits[0]["pct"], -60.0, places=2)

    def test_both_rules_can_fire_on_the_same_asset(self):
        """先砸 −60% 再翻倍：两条规则各自成立，不该只报一条。"""
        candles = [
            candle(0, high=3.00, close=1.00, low=0.95),   # 3 日窗口里的最高价（之后腰斩）
            candle(1, high=1.10, close=1.05, low=0.40),   # 2 日窗口里的最低价
            candle(2, high=1.20, close=1.15, low=1.00),
        ]
        hits = bm.analyze_candles({"symbol": "FFF"}, candles, now_ms=1)
        self.assertEqual({hit["rule"] for hit in hits}, {bm.RULE_DROP, bm.RULE_PUMP})

    def test_asset_identity_is_carried_onto_the_hit(self):
        candles = [
            candle(0, high=1.00, close=1.00, low=0.99),
            candle(1, high=0.40, close=0.30, low=0.28),
        ]
        hits = bm.analyze_candles(
            {"symbol": "GGG", "name": "Gee", "chain": "56", "contract": "0xABC", "source": "币安合约日线"},
            candles,
            now_ms=1,
        )
        hit = hits[0]
        self.assertEqual(hit["assetKey"], "GGG|56|0xabc")
        self.assertEqual(hit["source"], "币安合约日线")
        self.assertEqual(hit["baseLabel"], "最高")


class BigMoveMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = bm.BigMoveStore(Path(self.tmp.name) / "big_move.sqlite")
        self.sent = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def build(self, *, assets=None, candles=None, cooldown=bm.BIG_MOVE_ALERT_COOLDOWN_SECONDS):
        monitor = bm.BigMoveMonitor(
            self.store, cooldown_seconds=cooldown, concurrency=2,
            alert_callback=lambda hit: self.sent.append(dict(hit)),
        )
        monitor.set_universe_provider(lambda: list(assets or []))
        monitor.set_candle_provider(
            candles if callable(candles) else (lambda asset: (candles or {}).get(asset["symbol"], []))
        )
        return monitor

    def crash_candles(self):
        return [
            candle(0, high=1.00, close=1.00, low=0.99),
            candle(1, high=1.10, close=1.00, low=0.95),
            candle(2, high=0.60, close=0.45, low=0.40),
        ]

    def test_hit_is_fired_once_then_cooled_down(self):
        monitor = self.build(
            assets=[{"symbol": "AAA", "chain": "", "contract": ""}],
            candles=lambda asset: self.crash_candles(),
        )
        result = monitor.run_cycle()
        self.assertEqual(result["hits"], 1)
        self.assertEqual(result["fired"], 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["rule"], bm.RULE_DROP)
        # 第二轮机一样：流水不再新增，弹窗被冷却挡住。
        second = monitor.run_cycle()
        self.assertEqual(second["fired"], 0)
        self.assertEqual(len(self.sent), 1)
        self.assertGreaterEqual(monitor.blocked_count, 1)
        self.assertEqual(self.store.hit_count(), 1)

    def test_cooldown_expiry_allows_a_second_alert(self):
        monitor = self.build(
            assets=[{"symbol": "AAA", "chain": "", "contract": ""}],
            candles=lambda asset: self.crash_candles(),
            cooldown=0.0,
        )
        monitor.run_cycle()
        monitor.run_cycle()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.store.hit_count(), 2)

    def test_failed_alert_callback_is_not_recorded_as_sent(self):
        """弹窗通道炸了**不许记账** —— 否则这只币被锁 12 小时，这一波就永远看不到。"""
        def boom(hit):
            raise RuntimeError("desktop alert bridge down")

        monitor = bm.BigMoveMonitor(self.store, cooldown_seconds=3600, alert_callback=boom)
        monitor.set_universe_provider(lambda: [{"symbol": "AAA"}])
        monitor.set_candle_provider(lambda asset: self.crash_candles())
        self.assertEqual(monitor.run_cycle()["fired"], 0)
        # 没记账 → 通道恢复后还能再弹。
        monitor.set_alert_callback(lambda hit: self.sent.append(hit))
        self.assertEqual(monitor.run_cycle()["fired"], 1)

    def test_empty_universe_is_reported_not_treated_as_a_clean_scan(self):
        """清单读不到时不许伪装成「今天没有异动」（项目里踩过好几次的坑）。"""
        monitor = bm.BigMoveMonitor(self.store, alert_callback=lambda hit: self.sent.append(hit))
        monitor.set_universe_provider(lambda: [])
        monitor.set_candle_provider(lambda asset: self.crash_candles())
        result = monitor.run_cycle()
        self.assertEqual(result["scanned"], 0)
        self.assertIn("标的清单", monitor.last_error)
        self.assertIn("跳过", monitor.last_note)

    def test_one_broken_provider_does_not_kill_the_rest(self):
        def provider(asset):
            if asset["symbol"] == "BAD":
                raise RuntimeError("no candles")
            return self.crash_candles()

        monitor = self.build(
            assets=[{"symbol": "BAD"}, {"symbol": "AAA"}],
            candles=provider,
        )
        result = monitor.run_cycle()
        self.assertEqual(result["hits"], 1)
        self.assertEqual(len(self.sent), 1)
        status = monitor.payload(force_refresh=True)["status"]
        self.assertEqual(status["scanned"], 2)
        self.assertEqual(status["covered"], 1)
        self.assertEqual(status["errors"], 1)
        self.assertEqual(status["missing"], ["BAD"])

    def test_disabled_alerts_still_record_the_hit(self):
        monitor = self.build(
            assets=[{"symbol": "AAA"}],
            candles=lambda asset: self.crash_candles(),
        )
        monitor.set_alerts_enabled(False)
        monitor.run_cycle()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.hit_count(), 1)
        self.assertFalse(monitor.payload(force_refresh=True)["enabled"])

    def test_recent_hits_are_newest_first(self):
        self.store.record_hit({"assetKey": "A||", "rule": bm.RULE_DROP, "symbol": "A",
                               "pct": -60, "price": 1, "basePrice": 2.5, "days": 3, "at": 1000})
        self.store.record_hit({"assetKey": "B||", "rule": bm.RULE_PUMP, "symbol": "B",
                               "pct": 130, "price": 3, "basePrice": 1.3, "days": 2, "at": 2000})
        rows = self.store.recent_hits()
        self.assertEqual([row["symbol"] for row in rows], ["B", "A"])
        # 接口快照与流水同序（前端直接渲染，别在两处各排一次）。
        monitor = bm.BigMoveMonitor(self.store)
        payload = monitor.payload(force_refresh=True)
        self.assertEqual([row["rule"] for row in payload["hits"]], [bm.RULE_PUMP, bm.RULE_DROP])


class BigMovePopupTests(unittest.TestCase):
    """弹窗文案：两条规则讲的是相反的动作，标题和正文都不能写反。"""

    def load(self, *, alerts_enabled=True):
        sent = []

        def launch(payload):
            sent.append(payload)
            return {"ok": True}

        class StubMonitor:
            def alerts_enabled(self_inner):
                return alerts_enabled

        namespace = {
            "json": json,
            "time": time,
            "clean_feed_text": clean_feed_text,
            "safe_float": safe_float,
            "BIG_MOVE": bm,
            "BIG_MOVE_MONITOR": StubMonitor(),
            "BIG_MOVE_ALERT_PRIORITY": 86,
            "mm_price_text": lambda value: f"${float(value):g}",
            "mm_chain_label": lambda value: {"56": "BSC"}.get(str(value or ""), ""),
            "price_move_trade_url": lambda symbol, chain, contract: f"https://example.test/{symbol}",
            "launch_desktop_alert": launch,
        }
        exec(function_code("send_big_move_desktop_alert"), namespace)
        return namespace["send_big_move_desktop_alert"], sent

    def test_drop_popup_talks_about_liquidity_vacuum_and_rebound(self):
        send, sent = self.load()
        send({
            "rule": bm.RULE_DROP, "symbol": "AAA", "pct": -62.4, "price": 0.38,
            "basePrice": 1.01, "days": 3, "thresholdPct": -50.0, "at": 1,
            "chain": "", "contract": "", "source": "币安合约日线",
        })
        payload = sent[0]
        self.assertIn("3 日跌 62.4%", payload["title"])
        self.assertIn("流动性真空", payload["body"])
        self.assertIn("剧烈反弹", payload["body"])
        self.assertEqual(payload["key"].split(":")[:3], ["big-move", bm.RULE_DROP, "AAA"])
        self.assertEqual(payload["queuePriority"], 86)
        self.assertEqual(payload["board"], "big-move")
        self.assertIn("警惕剧烈反弹", payload["speech"])

    def test_pump_popup_talks_about_a_top(self):
        send, sent = self.load()
        send({
            "rule": bm.RULE_PUMP, "symbol": "BBB", "pct": 128.5, "price": 2.3,
            "basePrice": 1.0, "days": 2, "thresholdPct": 100.0, "at": 2,
            "chain": "56", "contract": "0xABC", "source": "OKX 永续日线",
        })
        payload = sent[0]
        self.assertIn("2 日涨 128.5%", payload["title"])
        self.assertIn("反弹见顶", payload["body"])
        self.assertIn("低", payload["body"])          # 基准价写「区间最低」
        self.assertEqual(payload["chain"], "56")
        self.assertEqual(payload["contractAddress"], "0xABC")

    def test_below_threshold_never_reaches_the_popup(self):
        send, sent = self.load()
        for hit in (
            {"rule": bm.RULE_DROP, "symbol": "AAA", "pct": -12.0, "thresholdPct": -50.0},
            {"rule": bm.RULE_PUMP, "symbol": "BBB", "pct": 40.0, "thresholdPct": 100.0},
        ):
            send(hit)
        self.assertEqual(sent, [])

    def test_unknown_rule_is_skipped_instead_of_guessed(self):
        send, sent = self.load()
        send({"rule": "crash", "symbol": "AAA", "pct": -80})
        self.assertEqual(sent, [])

    def test_disabled_monitor_never_sends(self):
        send, sent = self.load(alerts_enabled=False)
        send({"rule": bm.RULE_DROP, "symbol": "AAA", "pct": -80, "thresholdPct": -50.0})
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
