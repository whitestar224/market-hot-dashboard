"""价格异动监控（6 小时内涨 ≥20%）的判定口径。

用户 2026-10-21 第二次拍板后，口径是**触线即报**，这个子板块最容易错的三件事：

1. **基准价 = 窗口内最低价**，不是「整 6 小时前那一帧」。拿固定锚点会把中途启动的
   行情算漏（币 3 小时前拉起来再横盘，和 6 小时前比只差 5%，可它确实在窗口内涨了 25%）。
2. **不等窗口走满**：只覆盖了 10 分钟也要照报 —— 涨够了就是涨够了，等 6 小时再结算
   等于把提醒推迟到行情结束之后。
3. **抓取失败 ≠ 空帧**：全挂时写空帧会把这一轮采样点抹掉，基准价就变成不可用 ——
   等于把一整段窗口弄瞎。
"""

import tempfile
import time
import unittest
from pathlib import Path

import market_maker_monitor as mm
import price_move_monitor as pm

HOUR = 3600
WINDOW = pm.PRICE_MOVE_WINDOW_SECONDS


class SymbolFilterTest(unittest.TestCase):
    """涨跌对稳定币 / 杠杆代币没有意义，进采样只会污染榜单。"""

    def test_stablecoins_are_not_targets(self):
        for symbol in ("USDT", "USDC", "FDUSD", "DAI", "USDTUSDT"):
            with self.subTest(symbol=symbol):
                self.assertFalse(pm.is_tradable_symbol(symbol))

    def test_leveraged_tokens_are_not_targets(self):
        for symbol in ("BTCUP", "BTCDOWN", "ETHBULL", "ETHBEAR", "BTCUPUSDT"):
            with self.subTest(symbol=symbol):
                self.assertFalse(pm.is_tradable_symbol(symbol))

    def test_real_coins_ending_in_up_are_not_mistaken_for_leverage(self):
        """JUP / SYRUP / PUMP 这类真币名字里也有 UP，绝不能一起误杀。"""
        for symbol in ("JUP", "SYRUP", "PUMP", "SUPER"):
            with self.subTest(symbol=symbol):
                self.assertTrue(pm.is_tradable_symbol(symbol))

    def test_a_stablecoin_suffix_is_not_stripped_into_a_fake_ticker(self):
        """FDUSD 会被 symbol_key 剥成 FD —— 那等于凭空造出一个叫 FD 的标的。"""
        self.assertFalse(pm.is_tradable_symbol("FDUSD"))
        self.assertFalse(pm.is_tradable_symbol("FDUSDUSDT"))

    def test_blank_and_suffixed_pairs_collapse_to_the_same_key(self):
        self.assertFalse(pm.is_tradable_symbol(""))
        self.assertFalse(pm.is_tradable_symbol(None))
        self.assertTrue(pm.is_tradable_symbol("BTCUSDT"))
        self.assertTrue(pm.is_tradable_symbol("btc/usdt"))


class PctChangeTest(unittest.TestCase):
    def test_a_plain_double_is_a_hundred_percent(self):
        self.assertAlmostEqual(pm.pct_change(200.0, 100.0), 100.0)
        self.assertAlmostEqual(pm.pct_change(120.0, 100.0), 20.0)

    def test_a_drop_is_negative(self):
        self.assertAlmostEqual(pm.pct_change(80.0, 100.0), -20.0)

    def test_a_missing_or_zero_base_never_divides_by_zero(self):
        self.assertEqual(pm.pct_change(100.0, 0.0), 0.0)
        self.assertEqual(pm.pct_change(100.0, -5.0), 0.0)
        self.assertEqual(pm.pct_change(0.0, 100.0), 0.0)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_frame_is_one_timestamp_shared_by_every_symbol(self):
        ts = 1_700_000_000
        self.assertEqual(self.store.record_frame(ts, {"AAA": 1.0, "BBB": 2.0}), 2)
        frame_ts, prices = self.store.frame_at_or_before(ts)
        self.assertEqual(frame_ts, ts)
        self.assertEqual(prices, {"AAA": 1.0, "BBB": 2.0})
        self.assertEqual(self.store.sample_stats()["frames"], 1)

    def test_the_base_frame_is_the_latest_one_not_after_the_deadline(self):
        """基准必须是「窗口起点**之前最近**的一帧」，晚一帧就少算几分钟的行情。"""
        self.store.record_frame(1000, {"AAA": 1.0})
        self.store.record_frame(2000, {"AAA": 2.0})
        self.store.record_frame(3000, {"AAA": 3.0})
        frame_ts, prices = self.store.frame_at_or_before(2500)
        self.assertEqual(frame_ts, 2000)
        self.assertEqual(prices["AAA"], 2.0)

    def test_no_frame_before_the_deadline_is_reported_as_zero(self):
        self.store.record_frame(3000, {"AAA": 1.0})
        frame_ts, prices = self.store.frame_at_or_before(1000)
        self.assertEqual((frame_ts, prices), (0, {}))

    def test_prices_that_are_not_positive_are_dropped(self):
        self.assertEqual(self.store.record_frame(1000, {"AAA": 0.0, "BBB": -1.0}), 0)

    def test_old_frames_are_pruned_but_the_window_survives(self):
        now = 1_700_000_000
        self.store.record_frame(now - WINDOW - 10_000, {"AAA": 1.0})
        self.store.record_frame(now - WINDOW, {"AAA": 2.0})
        removed = self.store.clear_old_samples(now - pm.SAMPLE_RETENTION_SECONDS)
        self.assertEqual(removed, 1)
        frame_ts, _ = self.store.frame_at_or_before(now - WINDOW)
        self.assertEqual(frame_ts, now - WINDOW, "窗口起点那一帧被当成过期删掉了")

    def test_the_move_log_keeps_only_the_latest_rows(self):
        for index in range(810):
            self.store.record_move({
                "symbol": "AAA", "pct": 25.0, "price": 1.0, "basePrice": 0.8,
                "baseTs": 1, "windowSeconds": WINDOW, "source": "币安合约",
                "chain": "", "contract": "", "at": 1_700_000_000_000 + index,
            })
        rows = self.store.recent_moves(limit=2000)
        self.assertEqual(len(rows), 800)
        self.assertNotIn(1_700_000_000_000, {row["at"] for row in rows})

    def test_the_alert_ledger_upserts_one_row_per_symbol(self):
        self.store.mark_alerted("AAA", 21.0)
        first = self.store.alert_state("AAA")
        self.store.mark_alerted("AAA", 33.0)
        second = self.store.alert_state("AAA")
        self.assertGreater(second["last_alert_at"], 0)
        self.assertEqual(second["last_pct"], 33.0)
        self.assertLessEqual(first["last_alert_at"], second["last_alert_at"])

    def test_popups_default_to_on_and_can_be_switched_off(self):
        self.assertTrue(self.store.alerts_enabled())
        self.assertFalse(self.store.set_alerts_enabled(False))
        self.assertFalse(self.store.alerts_enabled())
        self.assertTrue(self.store.set_alerts_enabled(True))


class RollingWindowTest(unittest.TestCase):
    """核心口径：拿**窗口内最低价**当基准，触线即报。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")
        self.sent = []
        self.monitor = pm.PriceMoveMonitor(
            self.store, interval=300, alert_callback=self.sent.append
        )
        self.now = 1_700_000_000

    def tearDown(self):
        self._tmp.cleanup()

    def _frame_six_hours_ago(self, prices):
        self.store.record_frame(self.now - WINDOW, prices)

    def test_only_the_symbols_over_the_threshold_are_recorded(self):
        self._frame_six_hours_ago({"AAA": 100.0, "BBB": 50.0, "CCC": 10.0})
        fired = self.monitor.evaluate(
            self.now, {"AAA": 125.0, "BBB": 55.0, "CCC": 25.0}
        )
        self.assertEqual(fired, 2)
        recorded = {row["symbol"]: row["pct"] for row in self.store.recent_moves()}
        self.assertEqual(recorded, {"AAA": 25.0, "CCC": 150.0})

    def test_a_drop_of_twenty_percent_is_not_a_price_surge(self):
        self._frame_six_hours_ago({"AAA": 100.0})
        self.assertEqual(self.monitor.evaluate(self.now, {"AAA": 80.0}), 0)
        self.assertEqual(self.store.recent_moves(), [])

    def test_exactly_twenty_percent_counts(self):
        self._frame_six_hours_ago({"AAA": 100.0})
        self.assertEqual(self.monitor.evaluate(self.now, {"AAA": 120.0}), 1)

    def test_a_symbol_without_a_base_price_is_skipped(self):
        """新上架的币窗口里一帧都没有 —— 不能拿它今天的开盘价凑数。"""
        self._frame_six_hours_ago({"AAA": 100.0})
        self.assertEqual(self.monitor.evaluate(self.now, {"AAA": 100.0, "NEW": 9.0}), 0)

    def test_the_base_is_the_window_low_not_the_frame_six_hours_ago(self):
        """币 3 小时前拉起来再横盘：跟 6 小时前比只差 5%，但窗口内实实在在涨了 25%。

        旧口径（固定 6 小时锚点）在这里什么都不报 —— 这正是用户要改掉的东西。
        """
        self.store.record_frame(self.now - WINDOW + 60, {"AAA": 100.0})   # 6 小时前：100
        self.store.record_frame(self.now - 3 * HOUR, {"AAA": 125.0})      # 3 小时前拉起
        self.store.record_frame(self.now - HOUR, {"AAA": 125.0})          # 之后横盘
        fired = self.monitor.evaluate(self.now, {"AAA": 125.0})
        self.assertEqual(fired, 1, "相对窗口低点涨了 25%，必须报")
        row = self.store.recent_moves()[0]
        self.assertEqual(row["base_price"], 100.0)
        self.assertEqual(row["base_ts"], self.now - WINDOW + 60)
        # 真实跨度写出来，不冒充 6 小时（这条是 6 小时差 1 分钟，仍然如实记）
        self.assertEqual(row["window_seconds"], WINDOW - 60)

    def test_the_recorded_window_is_the_real_span_not_a_round_six_hours(self):
        """基准是最低价那一刻，跨度就是「那一刻到现在」。"""
        self.store.record_frame(self.now - 3 * HOUR, {"AAA": 100.0})
        self.store.record_frame(self.now - 2 * HOUR, {"AAA": 104.0})
        self.monitor.evaluate(self.now, {"AAA": 130.0})
        row = self.store.recent_moves()[0]
        self.assertEqual(row["base_price"], 100.0)
        self.assertEqual(row["base_ts"], self.now - 3 * HOUR)
        self.assertEqual(row["window_seconds"], 3 * HOUR)

    def test_frames_provider_than_the_window_are_ignored(self):
        """窗口外的低价不算基准 —— 否则「一周前的低点」也能拿来算 6 小时涨幅。"""
        self.store.record_frame(self.now - WINDOW - 600, {"AAA": 10.0})   # 窗口外
        self.store.record_frame(self.now - 600, {"AAA": 100.0})           # 窗口内
        fired = self.monitor.evaluate(self.now, {"AAA": 115.0})
        self.assertEqual(fired, 0)
        self.assertEqual(self.monitor.evaluate(self.now, {"AAA": 130.0}), 1)

    def test_a_crash_then_a_twenty_percent_bounce_does_count(self):
        """暴跌后反弹 20% 也是「6 小时内涨 20%」—— 用户要的就是这个，不许当噪声滤掉。"""
        self.store.record_frame(self.now - 4 * HOUR, {"AAA": 100.0})
        self.store.record_frame(self.now - HOUR, {"AAA": 50.0})   # 腰斩
        self.assertEqual(self.monitor.evaluate(self.now, {"AAA": 60.0}), 1)
        self.assertAlmostEqual(self.store.recent_moves()[0]["pct"], 20.0, places=6)

    def test_a_short_history_fires_immediately_instead_of_waiting_six_hours(self):
        """服务刚起 10 分钟：只要这段时间涨够 20%，当场就报，不等窗口走满。"""
        self.store.record_frame(self.now - 600, {"AAA": 100.0})
        self.assertEqual(self.monitor.evaluate(self.now, {"AAA": 121.0}), 1)
        row = self.store.recent_moves()[0]
        self.assertEqual(row["base_price"], 100.0)
        self.assertEqual(row["window_seconds"], 600)

    def test_chain_only_symbols_never_enter_a_frame(self):
        """用户 2026-10-21：链上标的不用做价格异动。

        上线当天实测：命中的 10 条里 8 条是 Solana/BSC 的 meme（EARTH +205%、HOOT +37%、
        GTA6 +21%…），20% 这个阈值对它们毫无意义。这个板块只认币安 U 本位合约。
        """
        self.monitor.set_market_provider(lambda: {"ONBOARDUSDT": 1.0})
        prices, problems = self.monitor.collect_prices()
        self.assertEqual(prices, {"ONBOARD": 1.0})
        self.assertEqual(problems, [])
        # 采集层不许再有第二条注入口，否则链上标的会悄悄爬回来
        self.assertFalse(hasattr(self.monitor, "chain_provider"))
        self.assertFalse(hasattr(self.monitor, "set_chain_provider"))

    def test_a_contract_only_symbol_is_labelled_as_the_exchange(self):
        self._frame_six_hours_ago({"AAA": 100.0})
        self.monitor.evaluate(self.now, {"AAA": 130.0})
        self.assertEqual(self.store.recent_moves()[0]["source"], "币安合约")


class MoveLogThrottleTest(unittest.TestCase):
    """流水是**事件**，不是每帧一记 —— 否则一直在涨的币 6 小时能刷 72 条。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")
        self.monitor = pm.PriceMoveMonitor(self.store, interval=300)
        self.now = 1_700_000_000

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_same_level_does_not_log_twice_within_the_gap(self):
        at = 1_700_000_000_000
        self.assertTrue(self.monitor.should_record("AAA", 25.0, at))
        self.store.record_move({
            "symbol": "AAA", "pct": 25.0, "price": 1.25, "basePrice": 1.0,
            "baseTs": self.now - HOUR, "windowSeconds": HOUR, "source": "币安合约",
            "chain": "", "contract": "", "at": at,
        })
        self.assertFalse(self.monitor.should_record("AAA", 25.4, at + 60_000))
        # 隔了 30 分钟再记一条（行情还在，值得留个脚印）
        self.assertTrue(self.monitor.should_record("AAA", 25.4, at + 31 * 60_000))

    def test_a_new_high_is_logged_even_inside_the_gap(self):
        at = 1_700_000_000_000
        self.store.record_move({
            "symbol": "AAA", "pct": 21.0, "price": 1.21, "basePrice": 1.0,
            "baseTs": self.now - HOUR, "windowSeconds": HOUR, "source": "币安合约",
            "chain": "", "contract": "", "at": at,
        })
        self.assertFalse(self.monitor.should_record("AAA", 21.5, at + 60_000))
        self.assertTrue(self.monitor.should_record("AAA", 22.0, at + 60_000))

    def test_the_live_list_survives_while_the_log_is_throttled(self):
        """节流了流水，页面「当前 ≥20% 的标的」也不能掉下去。"""
        self.store.record_frame(int(time.time()) - HOUR, {"AAA": 100.0})
        self.monitor.evaluate(int(time.time()), {"AAA": 130.0})
        self.monitor.evaluate(int(time.time()) + 60, {"AAA": 130.4})
        self.monitor.last_cycle_at = time.time()
        payload = self.monitor.payload(force_refresh=True)
        self.assertEqual(payload["summary"]["total"], 1, "流水被节流成 1 条")
        self.assertEqual(payload["summary"]["current"], 1, "但当前榜还得有这只币")
        self.assertEqual([row["symbol"] for row in payload["moves"]], ["AAA"])


class CooldownTest(unittest.TestCase):
    """同一个币 6 小时只弹一次，之后回落再突破才算新的一波。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")
        self.sent = []
        self.monitor = pm.PriceMoveMonitor(
            self.store, interval=300, alert_callback=self.sent.append
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _row(self, symbol="AAA", pct=25.0):
        return {
            "symbol": symbol, "pct": pct, "price": 1.25, "basePrice": 1.0,
            "baseTs": 1_700_000_000, "windowSeconds": WINDOW,
            "source": "币安合约", "chain": "", "contract": "",
            "at": int(time.time() * 1000),
        }

    def test_the_same_symbol_only_pops_once(self):
        self.assertTrue(self.monitor.maybe_alert(self._row()))
        self.assertFalse(self.monitor.maybe_alert(self._row(pct=31.0)))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.monitor.blocked_count, 1)

    def test_another_symbol_is_not_affected_by_the_first_cooldown(self):
        self.assertTrue(self.monitor.maybe_alert(self._row("AAA")))
        self.assertTrue(self.monitor.maybe_alert(self._row("BBB")))
        self.assertEqual(len(self.sent), 2)

    def test_the_cooldown_expires(self):
        self.store.mark_alerted("AAA", 22.0)
        self.store._session  # 保持 store 可读
        self.monitor.cooldown_seconds = 0
        self.assertTrue(self.monitor.maybe_alert(self._row()))

    def test_the_ledger_is_written_only_after_a_successful_emit(self):
        """先判断再记账：emit 失败也不能把币锁死 6 小时。"""
        def boom(_event):
            raise RuntimeError("弹窗通道炸了")

        self.monitor.on_event = boom
        self.assertFalse(self.monitor.maybe_alert(self._row()))  # 异常被吞
        self.assertEqual(self.store.alert_state("AAA"), {})
        # 通道恢复后仍有机会弹
        self.sent.clear()
        self.monitor.on_event = self.sent.append
        self.assertTrue(self.monitor.maybe_alert(self._row()))

    def test_the_switch_off_means_no_popup_but_the_move_is_still_logged(self):
        self.monitor.set_alerts_enabled(False)
        self.assertFalse(self.monitor.maybe_alert(self._row()))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.alert_state("AAA"), {})

    def test_the_emitted_event_carries_the_threshold(self):
        self.monitor.maybe_alert(self._row())
        self.assertEqual(self.sent[0]["reason"], "price-move")
        self.assertEqual(self.sent[0]["thresholdPct"], 20.0)


class CollectPricesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")
        self.monitor = pm.PriceMoveMonitor(self.store, interval=300)

    def tearDown(self):
        self._tmp.cleanup()

    def test_suffixes_are_stripped_so_both_sides_meet_on_the_same_key(self):
        self.monitor.set_market_provider(lambda: {"ETHUSDT": 3_000.0, "sol/usdt": 150.0})
        prices, _ = self.monitor.collect_prices()
        self.assertEqual(prices, {"ETH": 3_000.0, "SOL": 150.0})

    def test_stablecoins_and_leveraged_tokens_never_enter_a_frame(self):
        self.monitor.set_market_provider(lambda: {
            "USDTUSDT": 1.0, "BTCUPUSDT": 3.0, "BTCUSDT": 70_000.0,
        })
        prices, _ = self.monitor.collect_prices()
        self.assertEqual(prices, {"BTC": 70_000.0})

    def test_a_broken_futures_source_is_reported_not_silently_empty(self):
        def boom():
            raise RuntimeError("币安挂了")

        self.monitor.set_market_provider(boom)
        prices, problems = self.monitor.collect_prices()
        self.assertEqual(prices, {})
        self.assertIn("合约行情：RuntimeError", problems)

    def test_an_unwired_source_is_reported_not_silently_empty(self):
        prices, problems = self.monitor.collect_prices()
        self.assertEqual(prices, {})
        self.assertIn("合约行情未接线", problems)


class CycleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")
        self.monitor = pm.PriceMoveMonitor(self.store, interval=300)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_total_outage_does_not_write_an_empty_frame(self):
        """写空帧 = 把这一轮采样点抹掉，6 小时后基准价就不可用了。"""
        self.store.record_frame(int(time.time()) - WINDOW, {"AAA": 100.0})
        before = self.store.sample_stats()["frames"]
        result = self.monitor.run_cycle()  # 两个 provider 都没接线
        self.assertEqual(result, {"inserted": 0, "moves": 0})
        self.assertEqual(self.store.sample_stats()["frames"], before)
        self.assertIn("未记录采样", self.monitor.last_note)
        self.assertTrue(self.monitor.last_error)

    def test_a_normal_cycle_records_a_frame_and_warms_the_page_snapshot(self):
        self.monitor.set_market_provider(lambda: {"AAAUSDT": 1.0, "BBBUSDT": 2.0})
        result = self.monitor.run_cycle()
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(self.store.sample_stats()["frames"], 1)
        self.assertIsNotNone(self.monitor._payload_cache)

    def test_a_cycle_finds_the_moves_once_the_window_is_full(self):
        now = int(time.time())
        self.store.record_frame(now - WINDOW, {"AAA": 100.0, "BBB": 100.0})
        self.monitor.set_market_provider(lambda: {"AAAUSDT": 130.0, "BBBUSDT": 101.0})
        self.monitor.set_alert_callback(lambda event: None)
        result = self.monitor.run_cycle()
        self.assertEqual(result["moves"], 1)
        symbols = [row["symbol"] for row in self.store.recent_moves()]
        self.assertEqual(symbols, ["AAA"])


class WarmUpNoteTest(unittest.TestCase):
    """窗口没满时说清楚**已经覆盖多长**，但绝不再说「不会提醒」。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")
        self.monitor = pm.PriceMoveMonitor(self.store, interval=300)
        self.now = 1_700_000_000

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_frame_at_all(self):
        self.assertEqual(self.monitor.build_note(self.now), "还没有采样帧")

    def test_a_single_frame_says_it_needs_one_more(self):
        self.store.record_frame(self.now - 300, {"AAA": 1.0})
        self.assertIn("只有 1 帧", self.monitor.build_note(self.now))

    def test_a_short_history_reports_the_real_coverage_and_still_alerts(self):
        for offset in range(0, 2 * HOUR, 300):
            self.store.record_frame(self.now - 2 * HOUR + offset, {"AAA": 1.0})
        note = self.monitor.build_note(self.now)
        self.assertIn("窗口未满", note)
        self.assertIn("120 分钟", note)
        self.assertIn("照样立刻提醒", note)

    def test_a_full_window_needs_no_note(self):
        self.store.record_frame(self.now - WINDOW - 60, {"AAA": 1.0})
        self.store.record_frame(self.now, {"AAA": 1.1})
        self.assertEqual(self.monitor.build_note(self.now), "")


class PayloadTest(unittest.TestCase):
    """页面每 30 秒拉一次，快照必须走缓存（采样线程一直在写库）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = pm.PriceMoveStore(Path(self._tmp.name) / "pm.sqlite")
        self.monitor = pm.PriceMoveMonitor(self.store, interval=300)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_move(self, symbol="AAA", pct=25.0):
        self.store.record_move({
            "symbol": symbol, "pct": pct, "price": 1.25, "basePrice": 1.0,
            "baseTs": int(time.time()) - WINDOW, "windowSeconds": WINDOW,
            "source": "币安合约", "chain": "", "contract": "",
            "at": int(time.time() * 1000),
        })

    def test_the_payload_carries_the_agreed_thresholds(self):
        payload = self.monitor.payload(force_refresh=True)
        self.assertEqual(payload["thresholdPct"], 20.0)
        self.assertEqual(payload["windowSeconds"], 6 * HOUR)
        self.assertEqual(payload["cooldownSeconds"], 6 * HOUR)
        self.assertEqual(payload["summary"]["thresholdPct"], 20.0)
        self.assertTrue(payload["ok"])

    def test_a_move_is_shaped_for_the_page(self):
        self._seed_move()
        payload = self.monitor.payload(force_refresh=True)
        row = payload["moves"][0]
        self.assertEqual(row["pctText"], "+25.0%")
        self.assertEqual(row["windowText"], "6.0 小时")
        self.assertEqual(row["basePrice"], 1.0)
        self.assertEqual(payload["summary"]["current"], 1)

    def test_a_chain_row_left_in_the_db_never_reaches_the_page(self):
        """上线首日库里留了 8 条链上命中（EARTH +205%…）；采集层已经不收，页面也不许显示。"""
        self._seed_move(symbol="AAA")
        self.store.record_move({
            "symbol": "EARTH", "pct": 205.9, "price": 2.0, "basePrice": 0.65,
            "baseTs": int(time.time()) - WINDOW, "windowSeconds": WINDOW,
            "source": "链上", "chain": "CT_501", "contract": "2qgnTgJbZU6Tu6",
            "at": int(time.time() * 1000),
        })
        payload = self.monitor.payload(force_refresh=True)
        self.assertEqual([row["symbol"] for row in payload["moves"]], ["AAA"])
        self.assertEqual(payload["summary"]["total"], 1)

    def test_an_old_move_is_no_longer_counted_as_current(self):
        """「当前异动」只认最近一帧左右的记录，老记录留作流水。"""
        self.store.record_frame(int(time.time()), {"AAA": 1.0})
        self.store.record_move({
            "symbol": "AAA", "pct": 40.0, "price": 1.4, "basePrice": 1.0,
            "baseTs": int(time.time()) - WINDOW, "windowSeconds": WINDOW,
            "source": "币安合约", "chain": "", "contract": "",
            "at": int(time.time() * 1000) - 3 * HOUR * 1000,
        })
        payload = self.monitor.payload(force_refresh=True)
        self.assertEqual(payload["summary"]["current"], 0)
        self.assertEqual(payload["summary"]["total"], 1)

    def test_the_second_call_does_not_touch_the_database(self):
        self.monitor.payload()
        for name in ("recent_moves", "sample_stats", "alerts_enabled"):
            def boom(*_args, **_kwargs):
                raise AssertionError(f"缓存命中时不应该再读库：{name}()")

            setattr(self.monitor.store, name, boom)
        self.assertTrue(self.monitor.payload()["ok"])

    def test_the_cache_returns_a_copy_so_callers_can_add_keys(self):
        first = self.monitor.payload()
        first["moves"].append({"symbol": "MUTATED"})
        second = self.monitor.payload()
        self.assertEqual(second["moves"], [])

    def test_force_refresh_bypasses_the_cache(self):
        self.monitor.payload()  # 先把缓存热起来
        calls = {"n": 0}
        original = self.monitor.store.sample_stats

        def counting():
            calls["n"] += 1
            return original()

        self.monitor.store.sample_stats = counting
        # 缓存内的普通调用一次库都不该碰。
        self.monitor.payload()
        self.assertEqual(calls["n"], 0, "缓存命中却还在读库")
        # 强制刷新必须真的重建（_build_payload 与 build_note 各读一次统计）。
        self.monitor.payload(force_refresh=True)
        self.assertGreaterEqual(calls["n"], 1, "强制刷新却没有重建快照")


class LoopCadenceTest(unittest.TestCase):
    def test_the_interval_is_measured_from_the_cycle_start(self):
        source = Path(pm.__file__).read_text(encoding="utf-8")
        loop = source[source.index("def _loop"):source.index("def collect_prices")]
        self.assertIn("started = time.time()", loop)
        self.assertIn("remaining = self.interval - (time.time() - started)", loop)
        self.assertNotIn("self.stop_event.wait(self.interval)", loop)

    def test_the_first_cycle_waits_a_beat(self):
        """和 server 起来时那一堆监控线程抢网络没有意义。"""
        source = Path(pm.__file__).read_text(encoding="utf-8")
        loop = source[source.index("def _loop"):source.index("def collect_prices")]
        self.assertIn("self.stop_event.wait(45)", loop)

    def test_start_is_idempotent_and_stop_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = pm.PriceMoveMonitor(
                pm.PriceMoveStore(Path(tmp) / "pm.sqlite"), interval=300
            )
            monitor.set_market_provider(lambda: {})
            try:
                self.assertTrue(monitor.start())
                self.assertFalse(monitor.start(), "重复 start 会起第二个采样线程")
                self.assertTrue(monitor.running())
            finally:
                monitor.stop()
                monitor.close()
                if monitor._thread:
                    monitor._thread.join(timeout=3)
            self.assertFalse(monitor.running())


class SymbolKeyReuseTest(unittest.TestCase):
    def test_the_suffix_stripping_comes_from_the_market_maker_module(self):
        """归一只有一份实现；这里再写一份，两边迟早分叉。"""
        self.assertIs(pm.symbol_key, mm.symbol_key)


if __name__ == "__main__":
    unittest.main()
