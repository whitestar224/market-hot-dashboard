import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


DAY_MS = 24 * 60 * 60 * 1000


def daily_candles(values):
    """Build (timestamp, high, low, close) daily candles."""
    return [
        (1_780_000_000_000 + index * DAY_MS, float(high), float(low), float(close))
        for index, (high, low, close) in enumerate(values)
    ]


def daily_candles_ohlc(values):
    """Build (timestamp, high, low, close, open) daily candles."""
    return [
        (
            1_780_000_000_000 + index * DAY_MS,
            float(high),
            float(low),
            float(close),
            float(open_price),
        )
        for index, (open_price, high, low, close) in enumerate(values)
    ]


def recent_main_impulse(retrace_low=65, current=68):
    values = [
        (18, 12, 15),
        (13, 8, 10),
        (10, 5, 8),  # Much older monthly low; it must not be the Fib anchor.
        (13, 8, 11),
        (16, 10, 14),
        (19, 13, 17),
        (23, 16, 21),
        (27, 20, 25),
        (31, 24, 29),
        (35, 28, 33),
        (38, 31, 36),
        (40, 33, 38),
        (39, 32, 36),
        (37, 30, 34),
        (35, 28, 32),
        (33, 26, 30),
        (31, 24, 28),
        (29, 22, 26),
        (27, 20, 24),  # Recent daily launch pivot.
        (33, 23, 31),
        (42, 30, 40),
        (51, 39, 49),
        (61, 48, 59),
        (72, 58, 70),
        (82, 68, 80),
        (91, 78, 89),
        (101, 88, 99),
        (109, 97, 107),
        (116, 105, 114),
        (120, 112, 118),  # Latest completed swing high: +500% from launch.
        (112, retrace_low, 76),
        (78, min(retrace_low + 1, current), current),  # Current open daily bar.
    ]
    return daily_candles(values)


class PriceWatchFibonacciStructureTests(unittest.TestCase):
    def test_rejects_beat_like_choppy_double_as_a_main_wave(self):
        self.assertFalse(server.price_watch_main_wave_quality(113.8152, 0.1911, 0.75, "bullish-ratio-acceleration"))
        self.assertTrue(server.price_watch_main_wave_quality(416.59, 0.4326, 0.6154, "bullish-ratio-acceleration"))

    def test_anchors_to_recent_daily_main_impulse_not_old_monthly_low(self):
        result = server.price_watch_fib_structure(recent_main_impulse(), 68)

        self.assertTrue(result["candidate"])
        self.assertEqual(result["reason"], "recent-main-impulse-fib")
        self.assertEqual(result["launchLow"], 20)
        self.assertEqual(result["swingHigh"], 120)
        self.assertEqual(result["level05"], 70)
        self.assertEqual(result["level0618"], 58.2)
        self.assertLess(result["level0618"], result["level05"])
        self.assertEqual(result["status"], "near-0.5")
        self.assertGreater(result["impulseGainPct"], 100)

    def test_enables_first_fib_zone_before_retracing_below_half(self):
        result = server.price_watch_fib_structure(
            recent_main_impulse(retrace_low=65, current=65),
            65,
        )

        self.assertTrue(result["candidate"])
        self.assertEqual(result["status"], "between-levels")
        self.assertEqual(result["launchLow"], 20)
        self.assertEqual(result["level05"], 70)
        self.assertEqual(result["level0618"], 58.2)

    def test_bmt_like_pullback_is_near_fib_0618_without_reaching_half(self):
        result = server.price_watch_fib_structure(
            recent_main_impulse(retrace_low=57.8, current=58.5),
            58.5,
        )

        self.assertTrue(result["candidate"])
        self.assertEqual(result["status"], "near-0.618")
        self.assertLess(result["distance0618Pct"], 3)
        self.assertGreater(result["distance05Pct"], 3)

    def test_rejects_a_daily_wave_below_one_hundred_percent(self):
        values = [
            (22, 20, 21),
            (24, 21, 23),
            (27, 23, 26),
            (30, 25, 29),
            (33, 28, 32),
            (36, 31, 35),
            (39, 34, 38),
            (39.8, 36, 39),
            (35, 25, 30),
            (31, 29, 30),
        ]

        result = server.price_watch_fib_structure(daily_candles(values), 30)

        self.assertFalse(result["candidate"])
        self.assertEqual(result["reason"], "impulse-below-main-wave-threshold")
        self.assertEqual(result["launchLow"], 20)
        self.assertEqual(result["impulseGainPct"], 99)

    def test_keeps_the_full_multimonth_base_for_a_continuous_main_wave(self):
        values = []
        for index in range(18):
            close = 0.0200 - index * 0.0008
            values.append((close * 1.08, close * 0.92, close))
        values.append((0.0038, 0.0034, 0.00355))
        for index in range(42):
            close = 0.0037 + index * 0.00128
            values.append((close * 1.05, close * 0.95, close))
        values.extend(
            [
                (0.062, 0.052, 0.058),
                (0.055, 0.026, 0.031),
                (0.034, 0.029, 0.032),
            ]
        )

        result = server.price_watch_fib_structure(daily_candles(values), 0.032)

        self.assertTrue(result["candidate"])
        self.assertEqual(result["launchLow"], 0.0034)
        self.assertEqual(result["swingHigh"], 0.062)
        self.assertGreater(result["impulseGainPct"], 100)

    def test_uses_strong_departure_low_for_btw_current_main_leg(self):
        values = [
            (0.20510, 0.07589, 0.13325),
            (0.15600, 0.07100, 0.08052),
            (0.12474, 0.07990, 0.09419),
            (0.10685, 0.08090, 0.09999),
            (0.11973, 0.07565, 0.10126),
            (0.11555, 0.08500, 0.09680),
            (0.10085, 0.08156, 0.09632),
            (0.09821, 0.06283, 0.06448),
            (0.07596, 0.05610, 0.05709),
            (0.05972, 0.04773, 0.05024),  # Old cycle low, not this leg's launch.
            (0.07293, 0.04982, 0.05268),
            (0.07037, 0.05222, 0.06717),
            (0.07133, 0.05730, 0.06236),
            (0.06653, 0.05662, 0.06222),
            (0.06749, 0.05757, 0.06459),
            (0.08587, 0.06125, 0.07584),
            (0.07887, 0.05918, 0.06034),
            (0.06694, 0.06010, 0.06217),
            (0.06912, 0.05752, 0.06620),
            (0.06816, 0.06151, 0.06494),
            (0.06717, 0.06112, 0.06427),
            (0.06613, 0.05875, 0.05932),
            (0.06337, 0.05763, 0.06013),
            (0.06251, 0.05770, 0.06073),
            (0.06169, 0.05805, 0.05840),
            (0.06450, 0.05382, 0.06021),
            (0.06135, 0.05829, 0.05996),
            (0.06180, 0.05650, 0.06020),
            (0.06863, 0.05958, 0.06325),
            (0.06949, 0.06170, 0.06869),
            (0.07300, 0.06437, 0.06778),
            (0.07852, 0.06505, 0.07102),
            (0.07198, 0.06266, 0.06671),
            (0.06829, 0.06549, 0.06637),
            (0.07519, 0.06537, 0.06705),
            (0.07799, 0.06575, 0.07768),
            (0.08224, 0.06811, 0.07163),
            (0.11842, 0.07140, 0.07423),
            (0.09876, 0.06101, 0.09015),
            (0.10950, 0.07250, 0.08299),
            (0.08850, 0.07481, 0.08485),
            (0.10200, 0.07943, 0.10171),
            (0.12881, 0.08819, 0.09113),
            (0.09600, 0.07820, 0.08044),
            (0.11234, 0.07854, 0.10626),  # Strong departure candle.
            (0.13898, 0.10380, 0.13495),
            (0.17115, 0.12277, 0.16263),
            (0.19665, 0.14326, 0.19359),
            (0.22873, 0.16691, 0.22400),
            (0.24084, 0.12758, 0.20437),
            (0.22345, 0.14732, 0.21484),
            (0.22000, 0.16700, 0.17961),
        ]

        result = server.price_watch_fib_structure(daily_candles(values), 0.17961)

        self.assertTrue(result["candidate"])
        self.assertAlmostEqual(result["launchLow"], 0.07854)
        self.assertEqual(result["launchAt"], daily_candles(values)[44][0])

    def test_uses_tut_bullish_acceleration_window_low(self):
        values = [
            (0.01140, 0.01082, 0.01090),
            (0.01099, 0.00977, 0.01014),
            (0.01066, 0.00984, 0.01000),
            (0.01007, 0.00943, 0.00965),
            (0.01118, 0.00963, 0.01062),
            (0.01183, 0.01052, 0.01162),
            (0.01343, 0.01147, 0.01276),
            (0.01311, 0.01165, 0.01224),
            (0.01274, 0.01169, 0.01252),
            (0.01306, 0.01227, 0.01253),
            (0.01339, 0.01232, 0.01293),
            (0.01335, 0.01235, 0.01300),
            (0.01331, 0.01240, 0.01329),
            (0.01337, 0.01265, 0.01336),
            (0.01343, 0.01283, 0.01302),
            (0.01465, 0.01282, 0.01424),
            (0.01576, 0.01423, 0.01508),
            (0.01538, 0.01358, 0.01373),
            (0.01464, 0.01332, 0.01404),
            (0.01480, 0.01374, 0.01479),
            (0.01598, 0.01465, 0.01533),
            (0.01625, 0.01500, 0.01622),
            (0.01631, 0.01478, 0.01489),
            (0.01500, 0.01309, 0.01448),
            (0.01540, 0.01418, 0.01439),
            (0.01518, 0.01392, 0.01506),
            (0.01673, 0.01461, 0.01610),
            (0.01863, 0.01563, 0.01752),
            (0.01805, 0.01682, 0.01757),
            (0.02240, 0.01704, 0.02038),
            (0.02442, 0.02033, 0.02157),
            (0.02973, 0.02114, 0.02865),
            (0.03133, 0.02427, 0.02450),
            (0.04047, 0.02407, 0.03895),
            (0.11734, 0.03553, 0.10907),
            (0.33733, 0.09270, 0.20192),
            (0.25000, 0.11239, 0.11900),
        ]

        result = server.price_watch_fib_structure(daily_candles(values), 0.119)

        self.assertTrue(result["candidate"])
        self.assertAlmostEqual(result["launchLow"], 0.00963)
        self.assertEqual(result["launchAt"], daily_candles(values)[4][0])
        self.assertEqual(result["launchMethod"], "bullish-ratio-acceleration")

    def test_uses_low_of_real_bullish_ratio_acceleration_window(self):
        values = [
            (10.0, 10.2, 9.7, 9.8),
            (9.8, 10.0, 9.4, 9.5),
            (9.5, 9.7, 9.1, 9.2),
            (9.2, 9.4, 8.8, 8.9),
            (8.9, 9.1, 8.5, 8.7),
            (8.7, 8.9, 8.3, 8.5),
            (8.5, 8.7, 8.1, 8.3),
            (8.3, 8.5, 8.0, 8.1),
            (8.1, 8.3, 7.9, 8.0),
            (8.0, 8.2, 7.8, 7.9),
            (7.9, 8.7, 7.75, 8.5),
            (8.4, 10.1, 8.3, 9.9),
            (9.8, 12.4, 9.7, 12.0),
            (11.9, 15.4, 11.8, 15.0),
            (14.9, 18.5, 14.7, 18.1),
            (18.0, 22.0, 17.8, 21.4),
            (21.3, 26.0, 21.0, 25.2),
            (25.1, 30.0, 24.8, 29.2),
            (29.0, 31.0, 27.0, 28.0),
            (28.0, 29.0, 20.0, 22.0),
            (22.0, 23.0, 19.0, 21.0),
        ]

        result = server.price_watch_fib_structure(daily_candles_ohlc(values), 21.0)

        self.assertTrue(result["candidate"])
        self.assertAlmostEqual(result["launchLow"], 7.75)
        self.assertEqual(result["launchAt"], daily_candles_ohlc(values)[10][0])
        self.assertEqual(result["launchMethod"], "bullish-ratio-acceleration")
        self.assertGreaterEqual(result["acceleration"]["bullRatio"], 0.75)
        self.assertGreaterEqual(result["acceleration"]["ratioJump"], 0.35)

    def test_uses_first_obvious_breakout_candle_low_for_bmt(self):
        values = [
            (0.01223, 0.01124, 0.01189),
            (0.01214, 0.01183, 0.01196),
            (0.01288, 0.01188, 0.01206),
            (0.01376, 0.01204, 0.01258),
            (0.01280, 0.01170, 0.01187),
            (0.01218, 0.01186, 0.01199),
            (0.01220, 0.01174, 0.01195),
            (0.01204, 0.01121, 0.01132),
            (0.01173, 0.01110, 0.01147),
            (0.01151, 0.01085, 0.01102),
            (0.01162, 0.01093, 0.01116),
            (0.01166, 0.01116, 0.01151),
            (0.01233, 0.01151, 0.01181),
            (0.01185, 0.01161, 0.01167),
            (0.01206, 0.01153, 0.01183),
            (0.01266, 0.01172, 0.01224),
            (0.01255, 0.01192, 0.01223),
            (0.01299, 0.01217, 0.01265),
            (0.01299, 0.01247, 0.01256),
            (0.01338, 0.01253, 0.01312),
            (0.04124, 0.01306, 0.03302),
            (0.04359, 0.02551, 0.02657),
        ]

        result = server.price_watch_fib_structure(daily_candles(values), 0.02657)

        self.assertTrue(result["candidate"])
        self.assertAlmostEqual(result["launchLow"], 0.01306)
        self.assertEqual(result["launchAt"], daily_candles(values)[20][0])

    def test_rejects_a_small_rebound_after_an_old_spike_cycle(self):
        values = [(0.20, 0.14, 0.17)] * 10
        values.append((1.20, 0.18, 0.92))
        for index in range(20):
            close = 0.82 - index * 0.035
            values.append((close * 1.05, max(0.12, close * 0.9), max(0.13, close)))
        values.extend([(0.14, 0.116, 0.12)] * 12)
        values.extend(
            [
                (0.145, 0.12, 0.135),
                (0.16, 0.13, 0.15),
                (0.188, 0.15, 0.166),
                (0.18, 0.145, 0.16),
            ]
        )

        result = server.price_watch_fib_structure(daily_candles(values), 0.16)

        self.assertFalse(result["candidate"])
        self.assertEqual(result["reason"], "impulse-below-main-wave-threshold")
        self.assertAlmostEqual(result["launchLow"], 0.116)
        self.assertLess(result["impulseGainPct"], 100)


class PriceWatchFibonacciStateTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = self.db_path
        server.init_auth_db()

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.db_path.unlink(missing_ok=True)

    def test_near_half_level_emits_once_and_persists_wave_anchor(self):
        now_ms = int(time.time() * 1000)
        conn = server.auth_db()
        try:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?)
                """,
                ("FIB", "Fib Test", now_ms, now_ms),
            )
            conn.commit()
        finally:
            conn.close()

        result = {
            "symbol": "FIB",
            "currentPrice": 68,
            "weekHigh": 120,
            "distancePct": 43.3333,
            "provider": "Binance Futures",
            "status": "normal",
            "setupType": "",
            "structure": {},
            "oversoldStatus": "normal",
            "oversold": {"candidate": False, "qualified": False, "status": "normal"},
            "fibStatus": "near-0.5",
            "fib": {
                "candidate": True,
                "qualified": True,
                "status": "near-0.5",
                "launchLow": 20,
                "swingHigh": 120,
                "level05": 70,
                "level0618": 58.2,
                "distance05Pct": 2.8571,
                "distance0618Pct": 16.8704,
                "impulseGainPct": 500,
            },
            "checkedAt": now_ms,
            "error": "",
        }

        events = server.update_price_watch_snapshot(result)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eventType"], "oversold_fib_rebound")
        self.assertEqual(events[0]["fibLevel"], "0.5")
        conn = server.auth_db()
        try:
            asset = conn.execute(
                "SELECT fib_status, fib_json FROM price_watch_assets WHERE symbol = 'FIB'"
            ).fetchone()
            state = conn.execute(
                """
                SELECT reference_low, reference_high, target_price, episode, armed, in_zone
                FROM price_watch_fib_alert_state WHERE symbol = 'FIB' AND level = '0.5'
                """
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(asset["fib_status"], "near-0.5")
        self.assertIn('"launchLow":20', asset["fib_json"])
        self.assertEqual(
            dict(state),
            {
                "reference_low": 20.0,
                "reference_high": 120.0,
                "target_price": 70.0,
                "episode": 1,
                "armed": 0,
                "in_zone": 1,
            },
        )

        repeated = server.update_price_watch_snapshot({**result, "checkedAt": now_ms + 60_000})
        self.assertEqual(repeated, [])

    def test_tut_like_daily_candle_emits_both_levels_in_separate_rounds(self):
        now_ms = int(time.time() * 1000)
        conn = server.auth_db()
        try:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?)
                """,
                ("TUT", "TUT", now_ms, now_ms),
            )
            conn.commit()
        finally:
            conn.close()

        result = {
            "symbol": "TUT",
            "currentPrice": 57,
            "weekHigh": 120,
            "distancePct": 50,
            "provider": "Binance Futures",
            "status": "normal",
            "setupType": "",
            "structure": {},
            "oversoldStatus": "normal",
            "oversold": {"candidate": False, "qualified": False, "status": "normal"},
            "fibStatus": "near-0.618",
            "fib": {
                "candidate": True,
                "qualified": True,
                "status": "near-0.618",
                "launchLow": 20,
                "swingHigh": 120,
                "level05": 70,
                "level0618": 58.2,
                "distance05Pct": 18.5714,
                "distance0618Pct": 2.0619,
                "currentBarHigh": 90,
                "currentBarLow": 55,
                "impulseGainPct": 500,
            },
            "checkedAt": now_ms,
            "error": "",
        }

        first = server.update_price_watch_snapshot(result)
        second = server.update_price_watch_snapshot({**result, "checkedAt": now_ms + 60_000})
        third = server.update_price_watch_snapshot({**result, "checkedAt": now_ms + 120_000})

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["fibLevel"], "0.5")
        self.assertEqual(first[0]["direction"], "pullback")
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["fibLevel"], "0.618")
        self.assertEqual(second[0]["direction"], "pullback")
        self.assertEqual(third, [])

    def test_corrected_wave_anchor_is_not_blocked_by_old_cooldown(self):
        now_ms = int(time.time() * 1000)
        conn = server.auth_db()
        try:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?)
                """,
                ("BTW", "BTW", now_ms, now_ms),
            )
            conn.commit()
        finally:
            conn.close()

        base_result = {
            "symbol": "BTW",
            "currentPrice": 68,
            "weekHigh": 120,
            "distancePct": 43.3333,
            "provider": "Binance Futures",
            "status": "normal",
            "setupType": "",
            "structure": {},
            "oversoldStatus": "normal",
            "oversold": {"candidate": False, "qualified": False, "status": "normal"},
            "fibStatus": "near-0.5",
            "fib": {
                "candidate": True,
                "qualified": True,
                "status": "near-0.5",
                "launchLow": 20,
                "swingHigh": 120,
                "level05": 70,
                "level0618": 58.2,
                "distance05Pct": 2.8571,
                "distance0618Pct": 16.8704,
                "impulseGainPct": 500,
            },
            "checkedAt": now_ms,
            "error": "",
        }
        self.assertEqual(len(server.update_price_watch_snapshot(base_result)), 1)

        corrected_result = {
            **base_result,
            "currentPrice": 88,
            "fib": {
                **base_result["fib"],
                "launchLow": 30,
                "swingHigh": 150,
                "level05": 90,
                "level0618": 75.84,
                "distance05Pct": 2.2222,
                "distance0618Pct": 15.5144,
            },
            "checkedAt": now_ms + 60_000,
        }
        corrected = server.update_price_watch_snapshot(corrected_result)

        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0]["fibLevel"], "0.5")
        self.assertEqual(corrected[0]["launchLow"], 30)

    def test_pullback_alert_has_directional_chinese_speech(self):
        event = {
            "eventType": "oversold_fib_rebound",
            "symbol": "BMT",
            "currentPrice": 80,
            "fibLevel": "0.618",
            "fibTarget": 81.8,
            "launchLow": 20,
            "swingHigh": 120,
            "distancePct": 2.2,
            "direction": "pullback",
            "episode": 1,
            "checkedAt": 1_786_000_000_000,
            "provider": "Binance Futures",
        }

        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertEqual(
            payload["speech"],
            "超跌反弹预警，BMT 正在回撤至主升浪斐波那契 0.618 位置。",
        )

    def test_fib_alert_has_chinese_speech(self):
        event = {
            "eventType": "oversold_fib_rebound",
            "symbol": "FIB",
            "currentPrice": 68,
            "fibLevel": "0.5",
            "fibTarget": 70,
            "launchLow": 20,
            "swingHigh": 120,
            "distancePct": 2.86,
            "episode": 1,
            "checkedAt": 1_786_000_000_000,
            "provider": "Binance Futures",
        }

        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertEqual(
            payload["speech"],
            "超跌反弹预警，FIB 正在接近主升浪斐波那契 0.5 位置。",
        )


if __name__ == "__main__":
    unittest.main()
