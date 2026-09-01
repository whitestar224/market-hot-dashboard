import time
import unittest
from unittest.mock import Mock, patch

import server


def candles(closes, widths=None):
    rows = []
    for index, close in enumerate(closes):
        width = widths[index] if widths else max(0.2, close * 0.004)
        rows.append(
            (
                1_700_000_000_000 + index * 300_000,
                float(close),
                float(close + width),
                float(close - width),
                float(close),
                float(1_000 + index),
            )
        )
    return rows


def ohlcv_candles(closes, interval_ms, highs=None, lows=None, volumes=None):
    rows = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        high = highs[index] if highs else max(previous, close) * 1.01
        low = lows[index] if lows else min(previous, close) * 0.99
        volume = volumes[index] if volumes else 100.0
        rows.append(
            (
                1_730_000_000_000 + index * interval_ms,
                float(previous),
                float(high),
                float(low),
                float(close),
                float(volume),
            )
        )
    return rows


class PriceStructureRecognitionTests(unittest.TestCase):
    def test_cold_full_pool_payload_returns_placeholders_without_exchange_fanout(self):
        rows = [{"symbol": f"T{index}", "structure1mOverride": -1} for index in range(50)]
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()
        try:
            with patch.object(server, "price_structure_watch_rows", return_value=rows), patch.object(
                server, "read_json_cache", return_value={}
            ), patch.object(server, "write_json_cache"), patch.object(
                server, "fetch_price_structure_item"
            ) as fetch:
                payload = server.price_structure_payload()
            self.assertEqual(len(payload["items"]), 50)
            self.assertEqual(payload["summary"]["monitorMode"], "parallel-full-pool")
            fetch.assert_not_called()
        finally:
            with server.PRICE_STRUCTURE_CACHE_LOCK:
                server.PRICE_STRUCTURE_CACHE.clear()

    def test_recent_new_coin_low_position_does_not_require_heat_or_volume(self):
        now_ms = int(time.time() * 1000)
        daily = [
            (now_ms - (12 - index) * 86_400_000, 80 - index * 3, 100 - index * 4, 70 - index * 2.5, 75 - index * 2.5, 0)
            for index in range(12)
        ]
        recent = server.new_coin_low_position_context(
            {"1d": daily}, now_ms - 200 * 86_400_000, now_ms=now_ms
        )
        expired = server.new_coin_low_position_context(
            {"1d": daily}, now_ms - 366 * 86_400_000, now_ms=now_ms
        )

        self.assertTrue(recent["qualified"])
        self.assertGreaterEqual(recent["drawdownPct"], 30)
        self.assertFalse(expired["qualified"])

    def test_background_strategy_scan_is_seconds_not_three_minutes(self):
        self.assertLessEqual(server.PRICE_STRUCTURE_MONITOR_INTERVAL_SECONDS, 3)

    def test_fast_monitor_rotates_one_leader_and_preserves_the_other_cached_rows(self):
        rows = [
            {"symbol": "H", "name": "Humanity Protocol", "icon": ""},
            {"symbol": "CYS", "name": "Cysic", "icon": ""},
        ]
        cache_key = server.price_structure_cache_key(rows)
        existing_cys = {
            "symbol": "CYS", "name": "Cysic", "frames": [{"key": "1m"}],
            "signals": [], "signalCount": 0, "checkedAt": 1,
        }
        fresh_h = {
            "symbol": "H", "name": "Humanity Protocol", "frames": [{"key": "1m"}],
            "signals": [{"id": "h-live"}], "signalCount": 1, "checkedAt": 2,
        }
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            original_cache = dict(server.PRICE_STRUCTURE_CACHE)
            server.PRICE_STRUCTURE_CACHE.clear()
            server.PRICE_STRUCTURE_CACHE[cache_key] = (0, {"items": [existing_cys]})
        try:
            with (
                patch.object(server, "fetch_price_structure_item", return_value=fresh_h) as fetch,
                patch.object(server, "launch_price_structure_strategy_alerts", return_value=1),
                patch.object(server, "write_json_cache"),
            ):
                payload = server.refresh_price_structure_strategy_monitor_item(rows[0], rows)
        finally:
            with server.PRICE_STRUCTURE_CACHE_LOCK:
                server.PRICE_STRUCTURE_CACHE.clear()
                server.PRICE_STRUCTURE_CACHE.update(original_cache)

        fetch.assert_called_once_with(rows[0], fast_provider_probe=True)
        self.assertEqual([item["symbol"] for item in payload["items"]], ["H", "CYS"])
        self.assertEqual(payload["summary"]["alerts"], 1)
        self.assertEqual(payload["summary"]["monitorMode"], "parallel-priority")
        self.assertEqual(payload["summary"]["available"], 2)

    def test_hot_coin_monitor_uses_shared_dragon_wave_engine_for_all_six_frames(self):
        market_rows = candles([100 + index * 0.05 for index in range(120)])
        strategy_frames = [
            {
                "key": key,
                "label": label,
                "pattern": "无明确结构",
                "stage": "观察",
                "confidence": 0,
                "support": None,
                "resistance": None,
                "summary": "等待起爆",
                "signal": None,
                "pending": None,
            }
            for key, label, *_ in server.PRICE_STRUCTURE_TIMEFRAMES
        ]
        strategy_payload = {
            "ok": True,
            "strategy": "dragon-wave-engine",
            "strategyVersion": "shared-engine-live",
            "frames": strategy_frames,
            "signals": [],
        }
        row = {"symbol": "PI", "name": "Pi", "icon": ""}
        adaptive_context = {
            "symbol": "PI",
            "mode": "acceleration",
            "label": "主升加速段",
            "mainWaveStage": "active",
            "sourceKind": "personal-x",
        }
        with (
            patch.object(server, "price_structure_candles_from_binance", return_value=(market_rows, "Binance Futures")) as fetch,
            patch.object(server, "run_dragon_wave_monitor_strategy", return_value=strategy_payload) as strategy,
            patch.object(server, "strategy_adaptive_context_for_symbol", return_value=adaptive_context),
        ):
            result = server.fetch_price_structure_item(row)

        self.assertEqual(result["strategy"], "dragon-wave-engine")
        self.assertEqual([frame["key"] for frame in result["frames"]], ["1m", "5m", "15m", "1h", "4h", "1d"])
        self.assertEqual(fetch.call_count, 6)
        passed_timeframes = strategy.call_args.args[0]
        self.assertEqual(list(passed_timeframes), ["1m", "5m", "15m", "1h", "4h", "1d"])
        self.assertEqual(strategy.call_args.kwargs["adaptive_context"], adaptive_context)
        self.assertEqual(result["adaptiveContext"]["mode"], "acceleration")
        self.assertTrue(result["broadcastEligibility"]["eligible"])

    def test_deep_legacy_coin_small_low_level_bounce_is_display_only_even_when_hot(self):
        daily_closes = [7.0, 5.0, 3.0, 1.8, 1.0] + [0.55 - index * 0.002 for index in range(50)] + [0.44]
        daily_highs = [12.0] + [max(value * 1.04, value + 0.01) for value in daily_closes[1:]]
        daily_lows = [max(0.01, value * 0.96) for value in daily_closes]
        intraday_closes = [0.35] * 70 + [0.36, 0.37, 0.39, 0.40, 0.42, 0.44]
        eligibility = server.price_structure_broadcast_eligibility(
            {"symbol": "BEAT", "hotRank": 1, "heat": 100},
            {
                "1d": ohlcv_candles(daily_closes, 86_400_000, daily_highs, daily_lows),
                "15m": ohlcv_candles(intraday_closes, 900_000),
            },
        )

        self.assertTrue(eligibility["deepDrawdown"])
        self.assertFalse(eligibility["recentMainWave"])
        self.assertFalse(eligibility["marketRegimeReset"])
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["reason"], "deep-legacy-no-new-wave")

    def test_deep_legacy_coin_can_requalify_after_real_market_regime_reset(self):
        daily_closes = [1.4, 1.0, 0.7, 0.4, 0.2] + [0.04] * 70 + [0.14]
        daily_highs = [2.0] + [value * 1.03 for value in daily_closes[1:]]
        daily_lows = [value * 0.97 for value in daily_closes]
        intraday_closes = [0.04] * 80 + [0.05, 0.06, 0.08, 0.11, 0.14, 0.20, 0.35, 0.28, 0.24, 0.20, 0.18, 0.16, 0.15, 0.14]
        intraday_volumes = [100.0] * 80 + [150, 180, 250, 400, 700, 1_200, 5_000, 1_800, 1_200, 900, 700, 600, 500, 450]
        eligibility = server.price_structure_broadcast_eligibility(
            {"symbol": "TNSR", "hotRank": 1, "heat": 100},
            {
                "1d": ohlcv_candles(daily_closes, 86_400_000, daily_highs, daily_lows),
                "15m": ohlcv_candles(intraday_closes, 900_000, volumes=intraday_volumes),
            },
        )

        self.assertTrue(eligibility["deepDrawdown"])
        self.assertTrue(eligibility["repricing"]["capitalConfirmed"])
        self.assertGreaterEqual(eligibility["regimeResetScore"], server.PRICE_STRUCTURE_REGIME_RESET_MIN_SCORE)
        self.assertTrue(eligibility["marketRegimeReset"])
        self.assertTrue(eligibility["eligible"])
        self.assertEqual(eligibility["reason"], "deep-coin-market-regime-reset")
        self.assertEqual(eligibility["allowedIntervals"], [])

    def test_deep_coin_regime_reset_does_not_depend_on_a_specific_platform_shape(self):
        repricing = {
            "qualified": True,
            "resetScore": 80,
            "capitalConfirmed": True,
        }
        daily = ohlcv_candles(
            [1.0] + [0.1] * 30 + [0.25],
            86_400_000,
            highs=[1.2] + [0.11] * 30 + [0.26],
            lows=[0.9] + [0.09] * 30 + [0.20],
        )
        with patch.object(server, "price_structure_intraday_repricing", return_value=repricing), patch.object(
            server, "price_watch_fib_structure", return_value={"mainWaveQualified": False}
        ):
            eligibility = server.price_structure_broadcast_eligibility(
                {"symbol": "RESET", "hotRank": 8, "heat": 40},
                {"1d": daily, "15m": []},
            )

        self.assertTrue(eligibility["eligible"])
        self.assertTrue(eligibility["marketRegimeReset"])

    def test_recent_event_can_restore_a_deep_coin_when_attention_has_returned(self):
        daily = ohlcv_candles(
            [1.0] + [0.1] * 30 + [0.11],
            86_400_000,
            highs=[1.2] + [0.11] * 30 + [0.12],
            lows=[0.9] + [0.09] * 30 + [0.10],
        )
        event_context = {
            "eventId": "event-1",
            "title": "项目重大进展",
            "score": 88,
            "expiresAt": 1_900_000_000_000,
        }
        with patch.object(server, "price_structure_recent_event_context", return_value=event_context):
            eligibility = server.price_structure_broadcast_eligibility(
                {"symbol": "EVENT", "hotRank": 2, "heat": 90},
                {"1d": daily, "15m": ohlcv_candles([0.1] * 30 + [0.11], 900_000)},
            )

        self.assertTrue(eligibility["eventDrivenReset"])
        self.assertTrue(eligibility["eligible"])
        self.assertEqual(eligibility["reason"], "event-driven-market-revival")

    def test_event_without_attention_or_price_response_does_not_bypass_filter(self):
        daily = ohlcv_candles(
            [1.0] + [0.1] * 30 + [0.1],
            86_400_000,
            highs=[1.2] + [0.11] * 31,
            lows=[0.9] + [0.09] * 31,
        )
        with patch.object(
            server,
            "price_structure_recent_event_context",
            return_value={"eventId": "event-2", "score": 90, "expiresAt": 1_900_000_000_000},
        ):
            eligibility = server.price_structure_broadcast_eligibility(
                {"symbol": "NOFLOW", "hotRank": 9, "heat": 20},
                {"1d": daily, "15m": ohlcv_candles([0.1] * 40, 900_000)},
            )

        self.assertFalse(eligibility["eventDrivenReset"])
        self.assertFalse(eligibility["eligible"])

    def test_closed_cross_frame_signal_never_emits_popup_or_voice(self):
        child = {
            "id": "pi-1m-child",
            "interval": "1m",
            "label": "1分钟",
            "decisionTime": 1_740_226_620_000,
            "pattern": "横盘起飞 + 突破前高",
            "certainty": 92,
            "grade": "A+",
            "price": 1.003,
        }
        parent = {
            "id": "pi-5m-parent",
            "interval": "5m",
            "label": "5分钟",
            "decisionTime": 1_740_226_620_000,
            "pattern": "横盘起飞 + 突破前高",
            "certainty": 94,
            "grade": "A+",
            "price": 1.003,
            "multiTimeframeConfluence": True,
            "lowerTimeframeTriggerId": "pi-1m-child",
        }
        captured = []
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: captured.append(payload) or {"queued": True}):
            count = server.launch_price_structure_strategy_alerts({
                "symbol": "PI",
                "provider": "OKX Swap",
                "adaptiveContext": {"label": "主升加速段"},
                "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
                "signals": [child, parent],
            })

        self.assertEqual(count, 0)
        self.assertEqual(captured, [])

    def test_closed_secondary_breakout_hint_never_emits_popup_or_voice(self):
        hint = {
            "id": "pi-5m-secondary",
            "interval": "5m",
            "label": "5分钟",
            "decisionTime": 1_740_226_920_000,
            "pattern": "二次突破提示 · 盘整突破 + 前高突破",
            "certainty": 86,
            "grade": "A",
            "price": 1.012,
            "secondaryBreakoutHint": True,
            "alertOnly": True,
        }
        captured = []
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: captured.append(payload) or {"queued": True}):
            count = server.launch_price_structure_strategy_alerts({
                "symbol": "PI",
                "provider": "OKX Swap",
                "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
                "signals": [],
                "alertHints": [hint],
            })

        self.assertEqual(count, 0)
        self.assertEqual(captured, [])

    def test_prearm_candidates_only_keep_fresh_unconfirmed_a_grade_structures(self):
        now_ms = 1_800_000_000_000
        fresh_pending = {
            "key": "5m",
            "label": "5分钟",
            "signal": None,
            "pending": {
                "id": "hemi-pending",
                "interval": "5m",
                "pattern": "横盘起飞 + 拐点收复",
                "certainty": 94,
                "grade": "A+",
                "triggerPrice": 1.02,
            },
        }
        payload = {
            "items": [
                {"symbol": "HEMI", "provider": "Binance Futures", "checkedAt": now_ms - 1_000, "frames": [fresh_pending], "broadcastEligibility": {"eligible": True, "allowedIntervals": []}},
                {"symbol": "OLD", "provider": "OKX Swap", "checkedAt": now_ms - 999_000, "frames": [fresh_pending], "broadcastEligibility": {"eligible": True, "allowedIntervals": []}},
                {
                    "symbol": "DONE", "provider": "OKX Swap", "checkedAt": now_ms - 1_000,
                    "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
                    "frames": [{**fresh_pending, "signal": {"id": "formal"}}],
                },
                {
                    "symbol": "LOW", "provider": "Bitget Futures", "checkedAt": now_ms - 1_000,
                    "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
                    "frames": [{**fresh_pending, "pending": {**fresh_pending["pending"], "certainty": 89}}],
                },
            ]
        }

        with (
            patch.object(server, "price_structure_latest_snapshot_payload", return_value=payload),
            patch.object(server, "price_structure_excluded_symbols", return_value=set()),
        ):
            result = server.price_structure_prearm_candidates(now_ms=now_ms)

        self.assertEqual([candidate["symbol"] for candidate in result], ["HEMI"])
        self.assertEqual(result[0]["grade"], "A+")

    def test_prearm_alert_forecasts_a_structure_inside_ten_minute_window(self):
        candidate = {
            "id": "hemi-5m-pending",
            "symbol": "HEMI",
            "provider": "Binance Futures",
            "interval": "5m",
            "label": "5分钟",
            "pattern": "横盘起飞 + 拐点收复",
            "triggerPrice": 100,
            "certainty": 94,
            "grade": "A+",
            "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
        }
        quote = {
            "price": 98,
            "provider": "Binance Futures",
            "speedPctPerMinute": 0.25,
            "upRatio": 0.7,
        }
        captured = []
        with (
            patch.object(server, "price_structure_symbol_excluded", return_value=False),
            patch.object(server, "launch_desktop_alert", side_effect=lambda payload: captured.append(payload) or {"queued": True}),
        ):
            result = server.launch_price_structure_prearm_alert(candidate, quote)

        self.assertTrue(result["queued"])
        self.assertEqual(captured[0]["queuePriority"], server.DESKTOP_ALERT_TRADING_PREARM_PRIORITY)
        self.assertIn("5至10分钟提前预判", captured[0]["body"])
        self.assertIn("预计约 8 分钟触发", captured[0]["body"])

    def test_prearm_alert_keeps_near_trigger_fallback_without_momentum(self):
        candidate = {
            "id": "near-pending", "symbol": "H", "provider": "OKX Swap",
            "interval": "1m", "label": "1分钟", "pattern": "拐点收复",
            "triggerPrice": 100, "certainty": 92, "grade": "A",
            "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
        }
        with (
            patch.object(server, "price_structure_symbol_excluded", return_value=False),
            patch.object(server, "launch_desktop_alert", return_value={"queued": True}) as launch,
        ):
            result = server.launch_price_structure_prearm_alert(candidate, {"price": 99.5})

        self.assertTrue(result["queued"])
        self.assertIn("已进入临界触发区", launch.call_args.args[0]["body"])

    def test_prearm_alert_skips_far_or_slow_structure(self):
        candidate = {
            "id": "slow-pending", "symbol": "H", "provider": "OKX Swap",
            "interval": "5m", "label": "5分钟", "pattern": "拐点收复",
            "triggerPrice": 100, "certainty": 92, "grade": "A",
            "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
        }
        with (
            patch.object(server, "price_structure_symbol_excluded", return_value=False),
            patch.object(server, "launch_desktop_alert") as launch,
        ):
            result = server.launch_price_structure_prearm_alert(
                candidate,
                {"price": 97, "speedPctPerMinute": 0.1, "upRatio": 0.7},
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "outside forecast window")
        launch.assert_not_called()

    def test_prearm_monitor_updates_background_health_status_even_without_page(self):
        with patch.object(server, "price_structure_prearm_candidates", return_value=[]):
            result = server.price_structure_prearm_monitor_once()

        self.assertEqual(result["candidates"], 0)
        health = server.health_payload()["monitors"]
        self.assertGreater(health["structurePrearmStatus"]["lastRunAt"], int(time.time() * 1000) - 2_000)
        self.assertEqual(health["structurePrearmForecastMinutes"], 10)

    def test_structure_only_frames_never_emit_a_desktop_alert(self):
        item = {
            "symbol": "ETHFI",
            "provider": "Binance Futures",
            "frames": [{
                "key": "15m", "label": "15分钟", "pattern": "下降楔形突破",
                "stage": "结构观察", "confidence": 91,
            }],
            "signals": [],
        }
        with patch.object(server, "launch_desktop_alert") as launch:
            count = server.launch_price_structure_strategy_alerts(item)

        self.assertEqual(count, 0)
        launch.assert_not_called()

    def test_first_structure_observation_alerts_once_on_transition(self):
        previous = {
            "symbol": "CHIP", "checkedAt": 1_000,
            "frames": [{"key": "15m", "label": "15分钟", "pattern": "无明确结构"}],
        }
        current = {
            "symbol": "CHIP", "provider": "Binance Futures", "checkedAt": 2_000,
            "frames": [{
                "key": "15m", "label": "15分钟", "pattern": "盘整突破 + 下降楔形突破",
                "stage": "结构观察", "confidence": 82, "support": 0.02731, "resistance": 0.0283773,
            }],
        }
        with patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(server, "price_structure_alert_interval_allowed", return_value=True), patch.object(
            server, "claim_price_structure_observation_alert", side_effect=[True, False]
        ), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ) as launch:
            first = server.launch_price_structure_first_observation_alerts(current, previous)
            repeated = server.launch_price_structure_first_observation_alerts(current, current)

        self.assertEqual(first, 1)
        self.assertEqual(repeated, 0)
        self.assertEqual(launch.call_count, 1)
        alert = launch.call_args.args[0]
        self.assertIn("首次结构观察", alert["title"])
        self.assertNotIn("盘整突破", alert["body"])
        self.assertNotIn("下降楔形", alert["body"])
        self.assertNotIn("盘整突破", alert["speech"])
        self.assertNotIn("下降楔形", alert["speech"])
        self.assertIn("结构观察", alert["speech"])
        self.assertTrue(alert["excludeEndpoint"].endswith("/api/price-structures"))
        self.assertEqual(alert["excludeSymbol"], "CHIP")
        self.assertEqual(alert["excludeLabel"], "剔除结构")
        self.assertEqual(alert["excludeAction"], "exclude_structure")

    def test_closed_bar_signal_is_not_replayed_but_live_buy_trigger_is_alerted(self):
        base = {
            "symbol": "CHIP", "provider": "Binance Futures",
            "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
        }
        signal = {
            "id": "chip-1h", "interval": "1h", "label": "1小时", "pattern": "盘整突破",
            "decisionTime": int(time.time() * 1000), "certainty": 97, "grade": "A+", "price": 0.031,
        }
        with patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(server, "price_structure_alert_interval_allowed", return_value=True), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ) as launch:
            stale = server.launch_price_structure_strategy_alerts({**base, "signals": [{**signal, "barsAgo": 1}]})
            live = server.launch_price_structure_strategy_alerts({**base, "signals": [{**signal, "barsAgo": 0}]})

        self.assertEqual(stale, 0)
        self.assertEqual(live, 1)
        self.assertEqual(launch.call_count, 1)
        alert = launch.call_args.args[0]
        self.assertEqual(alert["title"], "CHIP 1小时 起爆买点")
        self.assertNotIn("盘整突破", alert["body"])
        self.assertNotIn("确定性", alert["body"])
        self.assertNotIn("信号K线", alert["body"])
        self.assertNotIn("$0.031", alert["body"])
        self.assertIn("K线：Binance Futures", alert["body"])
        self.assertEqual(alert["speech"], "龙头策略买点提醒，CHIP，1小时。")

    def test_last_closed_candle_is_not_mistaken_for_a_live_signal_after_a_long_scan(self):
        now_ms = int(time.time() * 1000)
        stale_hour = {
            "interval": "1h",
            "decisionTime": now_ms - 60 * 60 * 1000 - server.PRICE_STRUCTURE_SIGNAL_CLOSE_GRACE_MS - 1,
            "barsAgo": 0,
        }
        still_live_hour = {
            "interval": "1h",
            "decisionTime": now_ms - 35 * 60 * 1000,
            "barsAgo": 0,
        }

        self.assertFalse(server.price_structure_signal_actionable_now(stale_hour, {"checkedAt": now_ms}))
        self.assertTrue(server.price_structure_signal_actionable_now(still_live_hour, {"checkedAt": now_ms}))

    def test_parallel_scheduler_reserves_capacity_for_stalest_regular_symbols(self):
        now_ms = 1_800_000_000_000
        rows = [
            {"symbol": f"P{index}", "personalXPriority": True}
            for index in range(6)
        ] + [{"symbol": "OLDEST"}, {"symbol": "NEWER"}]
        snapshots = [
            {"symbol": row["symbol"], "checkedAt": now_ms - (index + 1) * 120_000, "frames": []}
            for index, row in enumerate(rows)
        ]

        selected = server.price_structure_monitor_next_rows(
            rows,
            snapshots,
            set(),
            4,
            now_ms=now_ms,
        )
        selected_symbols = [row["symbol"] for row in selected]

        self.assertEqual(len(selected_symbols), 4)
        self.assertEqual(sum(symbol.startswith("P") for symbol in selected_symbols), 2)
        self.assertIn("OLDEST", selected_symbols)
        self.assertIn("NEWER", selected_symbols)

    def test_fast_provider_fallback_reaches_gate_and_htx_before_giving_up(self):
        providers = [
            ("Binance Futures",), ("OKX Swap",), ("Bitget Futures",), ("KuCoin Spot",), ("Gate Futures",),
            ("HTX Futures",), ("Aster Futures",), ("Hyperliquid",), ("Trade.xyz",), ("链上 K线",),
        ]
        preferred = "OKX Swap"
        ordered = sorted(providers, key=lambda provider: 0 if provider[0] == preferred else 1)
        first_batch = server.price_structure_provider_probe_batch(ordered, 0)
        second_batch = server.price_structure_provider_probe_batch(providers, len(first_batch))
        first_names = [provider[0] for provider in first_batch]
        second_names = [provider[0] for provider in second_batch]

        self.assertEqual(first_names[0], preferred)
        self.assertIn("Gate Futures", first_names)
        self.assertIn("HTX Futures", first_names)
        self.assertIn("KuCoin Spot", first_names)
        self.assertIn("Hyperliquid", second_names)
        self.assertIn("Trade.xyz", second_names)
        self.assertIn("链上 K线", second_names)
        self.assertEqual(server.PRICE_STRUCTURE_FAST_PROVIDER_LIMIT, 6)

    def test_kucoin_spot_structure_candles_map_ohlcv_and_sort_oldest_first(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": "200000",
            "data": [
                ["1800000060", "1.10", "1.20", "1.30", "1.00", "120"],
                ["1800000000", "1.00", "1.10", "1.20", "0.90", "100"],
            ],
        }
        with patch.object(server.requests, "get", return_value=response) as get:
            candles, provider = server.price_structure_candles_from_kucoin(
                "LONGXIA", "1h", limit=2, min_rows=2
            )

        self.assertEqual(provider, "KuCoin Spot")
        self.assertEqual(candles[0], (1_800_000_000_000, 1.0, 1.2, 0.9, 1.1, 100.0))
        self.assertEqual(candles[1], (1_800_000_060_000, 1.1, 1.3, 1.0, 1.2, 120.0))
        self.assertEqual(get.call_args.kwargs["params"], {"symbol": "LONGXIA-USDT", "type": "1hour"})

    def test_recent_listing_uses_short_history_in_regular_structure_pool(self):
        observed_min_rows = []

        def fake_binance(_symbol, _interval, **kwargs):
            observed_min_rows.append(kwargs.get("min_rows"))
            return [
                (1_800_000_000_000, 1.0, 1.1, 0.9, 1.0, 100.0),
                (1_800_000_060_000, 1.0, 1.2, 0.95, 1.1, 120.0),
            ], "Binance Futures"

        with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
            original_preferences = dict(server.PRICE_STRUCTURE_PROVIDER_PREFERENCE)
            original_cursors = dict(server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR)
            server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.pop("DOS", None)
            server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR.pop("DOS", None)
        try:
            with (
                patch.object(server, "price_structure_recent_listing_at", return_value=1_799_000_000_000),
                patch.object(server, "price_structure_candles_from_binance", side_effect=fake_binance),
                patch.object(server, "run_dragon_wave_monitor_strategy", return_value={
                    "frames": [], "signals": [], "alertHints": [], "strategyVersion": "test",
                }),
            ):
                item = server.fetch_price_structure_item({
                    "symbol": "DOS",
                    "monitorPool": "aicoin-x",
                    "adaptiveContext": {"mode": "hot-leader"},
                }, fast_provider_probe=True)
        finally:
            with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
                server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.clear()
                server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.update(original_preferences)
                server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR.clear()
                server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR.update(original_cursors)

        self.assertEqual(item["provider"], "Binance Futures")
        self.assertEqual(len(observed_min_rows), len(server.PRICE_STRUCTURE_TIMEFRAMES))
        self.assertEqual(set(observed_min_rows), {2})

    def test_unavailable_structure_item_hides_raw_provider_errors_and_clears_stale_preference(self):
        row = {"symbol": "DOS", "adaptiveContext": {"mode": "hot-leader"}}
        with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
            original = dict(server.PRICE_STRUCTURE_PROVIDER_PREFERENCE)
            original_cursors = dict(server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR)
            server.PRICE_STRUCTURE_PROVIDER_PREFERENCE["DOS"] = "OKX Swap"
            server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR.pop("DOS", None)
        try:
            patches = [
                patch.object(server, name, side_effect=RuntimeError("provider timed out"))
                for name in (
                    "price_structure_candles_from_binance", "price_structure_candles_from_okx",
                    "price_structure_candles_from_bitget", "price_structure_candles_from_gate",
                    "price_structure_candles_from_kucoin", "price_structure_candles_from_htx", "price_structure_candles_from_aster",
                    "price_structure_candles_from_hyperliquid", "price_structure_candles_from_binance_wallet",
                    "price_structure_candles_from_geckoterminal",
                )
            ]
            for active_patch in patches:
                active_patch.start()
            try:
                item = server.fetch_price_structure_item(row, fast_provider_probe=True)
            finally:
                for active_patch in reversed(patches):
                    active_patch.stop()
            with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
                stale_preference_cleared = server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.get("DOS") != "OKX Swap"
                next_probe_cursor = server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR.get("DOS")
        finally:
            with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
                server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.clear()
                server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.update(original)
                server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR.clear()
                server.PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR.update(original_cursors)

        self.assertEqual(item["error"], "可用行情源本轮暂不可用，后台正在切换备用源重试")
        self.assertIsInstance(item["providerDiagnostics"], list)
        self.assertNotIn("HTTPSConnectionPool", item["error"])
        self.assertTrue(stale_preference_cleared)
        self.assertEqual(next_probe_cursor, server.PRICE_STRUCTURE_FAST_PROVIDER_LIMIT)

    def test_binance_wallet_contract_kline_parses_recent_solana_candles(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": "000000",
            "success": True,
            "data": {
                "klineInfos": [
                    [1_800_000_000_000, "0.10", "0.13", "0.09", "0.12", "120000", 1_800_003_599_999],
                    [1_800_003_600_000, "0.12", "0.15", "0.11", "0.14", "150000", 1_800_007_199_999],
                ]
            },
        }
        with patch.object(server.requests, "get", return_value=response) as request:
            rows, provider = server.price_structure_candles_from_binance_wallet(
                "AVBN6kXdaw27ySuvMevKYzNTL8d39b7sGQFDCmsvpump",
                "CT_501",
                "1h",
                limit=140,
            )

        self.assertEqual(provider, "Binance Wallet K线")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1][4], 0.14)
        self.assertEqual(request.call_args.kwargs["params"]["chainId"], "CT_501")
        self.assertEqual(request.call_args.kwargs["params"]["interval"], "1h")

    def test_contract_asset_prefers_binance_wallet_kline_before_exchange_symbol_probes(self):
        market_rows = candles([0.1 + index * 0.001 for index in range(80)])
        strategy_payload = {
            "ok": True,
            "strategyVersion": "shared-engine-live",
            "frames": [],
            "signals": [],
            "alertHints": [],
        }
        row = {
            "symbol": "PINK",
            "name": "PINK",
            "chain": "CT_501",
            "contractAddress": "AVBN6kXdaw27ySuvMevKYzNTL8d39b7sGQFDCmsvpump",
        }
        with patch.object(
            server,
            "price_structure_candles_from_binance_wallet",
            return_value=(market_rows, "Binance Wallet K线"),
        ) as wallet_kline, patch.object(
            server,
            "run_dragon_wave_monitor_strategy",
            return_value=strategy_payload,
        ), patch.object(server, "price_structure_candles_from_binance") as binance_futures:
            item = server.fetch_price_structure_item(row, fast_provider_probe=True)

        self.assertEqual(item["provider"], "Binance Wallet K线")
        self.assertEqual(wallet_kline.call_count, len(server.PRICE_STRUCTURE_TIMEFRAMES))
        binance_futures.assert_not_called()

    def test_structure_cache_identity_does_not_change_with_display_order(self):
        rows = [
            {"symbol": "CHIP", "structure1mOverride": 1, "structureIntervalOverrides": {}},
            {"symbol": "PRL", "structure1mOverride": -1, "structureIntervalOverrides": {"1h": 0}},
        ]

        self.assertEqual(
            server.price_structure_cache_key(rows),
            server.price_structure_cache_key(list(reversed(rows))),
        )

    def test_shared_turnover_gate_filters_price_and_structure_pools(self):
        rows = [{"symbol": "HIGH"}, {"symbol": "LOW"}, {"symbol": "UNKNOWN"}]
        activity = {
            "HIGH": {"turnover24hUsd": 20_000_000, "source": "Binance"},
            "LOW": {"turnover24hUsd": 9_999_999, "source": "Gate"},
        }
        with patch.object(server, "fetch_new_coin_low_market_activity", return_value=activity):
            filtered = server.filter_price_monitor_rows_by_activity(rows)

        self.assertEqual([row["symbol"] for row in filtered], ["HIGH", "UNKNOWN"])
        self.assertEqual(filtered[0]["marketActivity"]["status"], "active")
        self.assertEqual(filtered[1]["marketActivity"]["status"], "unavailable")
        self.assertEqual(server.PRICE_MONITOR_ACTIVITY_SUMMARY["excluded"], 1)
        self.assertEqual(server.PRICE_MONITOR_ACTIVITY_SUMMARY["thresholdUsd"], 10_000_000)

    def test_broadcast_qualification_rejects_formal_and_prearm_exits(self):
        rejected = {"eligible": False, "reason": "deep-legacy-no-new-wave", "allowedIntervals": []}
        item = {
            "symbol": "BEAT",
            "broadcastEligibility": rejected,
            "signals": [{
                "id": "beat-5m", "interval": "5m", "label": "5分钟",
                "decisionTime": 1_740_226_620_000, "pattern": "横盘起飞",
                "certainty": 99, "grade": "A+", "price": 0.44,
            }],
        }
        candidate = {
            "id": "beat-prearm", "symbol": "BEAT", "interval": "5m",
            "triggerPrice": 0.45, "certainty": 99, "grade": "A+",
            "broadcastEligibility": rejected,
        }
        with patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "launch_desktop_alert"
        ) as launch:
            formal_count = server.launch_price_structure_strategy_alerts(item)
            prearm = server.launch_price_structure_prearm_alert(
                candidate,
                {"price": 0.448, "speedPctPerMinute": 0.2, "upRatio": 0.8},
            )

        self.assertEqual(formal_count, 0)
        self.assertTrue(prearm["skipped"])
        self.assertEqual(prearm["reason"], "broadcast qualification rejected")
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
