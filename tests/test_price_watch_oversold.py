import gc
import tempfile
import time
import unittest
from pathlib import Path

import server


HOUR_MS = 60 * 60 * 1000


def candles(values):
    """Build (timestamp, high, close) candles from (high, close) pairs."""
    return [
        (1_780_000_000_000 + index * HOUR_MS, float(high), float(close))
        for index, (high, close) in enumerate(values)
    ]


class PriceWatchOversoldStructureTests(unittest.TestCase):
    def test_uses_only_price_action_after_the_post_peak_low(self):
        series = candles(
            [
                (100, 98),
                (96, 92),
                (89, 84),
                (75, 70),
                (61, 56),
                (52, 40),  # Lowest close after the seven-day peak.
                (44, 43),
                (48, 47),
                (50, 49),  # Low-range stage high.
                (48, 47),
                (46, 45),  # Real pullback after the stage high.
                (47, 46),
                (49.2, 49),  # Current, excluded from structural history.
            ]
        )

        result = server.price_watch_oversold_structure(series, 49)

        self.assertTrue(result["candidate"])
        self.assertTrue(result["qualified"])
        self.assertTrue(result["near"])
        self.assertEqual(result["status"], "near")
        self.assertEqual(result["rangeLow"], 40)
        self.assertEqual(result["rangeHigh"], 50)
        self.assertAlmostEqual(result["distancePct"], 2.0, places=4)

    def test_recent_low_and_vertical_rebound_are_not_a_range(self):
        series = candles(
            [
                (91, 90),
                (94, 93),
                (97, 96),
                (100, 98),
                (92, 88),
                (80, 75),
                (68, 62),
                (55, 49),
                (44, 40),
                (46, 45),
                (49.8, 49.5),
                (49.7, 49.2),  # Current: only a tiny red candle after the pump.
            ]
        )

        result = server.price_watch_oversold_structure(series, 49.2)

        self.assertTrue(result["candidate"])
        self.assertFalse(result["qualified"])
        self.assertFalse(result["near"])
        self.assertEqual(result["status"], "forming")

    def test_stage_high_requires_a_meaningful_retest(self):
        series = candles(
            [
                (100, 99),
                (90, 86),
                (78, 74),
                (64, 60),
                (48, 40),
                (42, 41),
                (45, 44),
                (48, 47),
                (50, 49.5),
                (49.5, 49),  # Pullback is only 2% from the stage high.
                (49.4, 49.2),
                (49.3, 49.1),
            ]
        )

        result = server.price_watch_oversold_structure(series, 49.1)

        self.assertTrue(result["candidate"])
        self.assertFalse(result["qualified"])
        self.assertFalse(result["near"])
        self.assertLess(result["retestDepthPct"], server.PRICE_WATCH_OVERSOLD_RETEST_DEPTH_PCT)

    def test_drawdown_must_exceed_fifty_percent(self):
        series = candles(
            [
                (100, 99),
                (92, 90),
                (85, 82),
                (73, 70),
                (65, 62),
                (58, 55),
                (60, 59),
                (64, 63),
                (66, 65),
                (63, 62),
                (64, 63),
                (62, 61),
            ]
        )

        result = server.price_watch_oversold_structure(series, 61)

        self.assertFalse(result["candidate"])
        self.assertFalse(result["qualified"])
        self.assertFalse(result["near"])
        self.assertEqual(result["status"], "normal")


class PriceWatchOversoldStateTests(unittest.TestCase):
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

    def test_near_stage_high_persists_and_emits_one_oversold_event(self):
        now_ms = int(time.time() * 1000)
        conn = server.auth_db()
        try:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?)
                """,
                ("TEST", "Test", now_ms, now_ms),
            )
            conn.commit()
        finally:
            conn.close()

        result = {
            "symbol": "TEST",
            "currentPrice": 49,
            "weekHigh": 100,
            "distancePct": 51,
            "provider": "Test Futures",
            "status": "normal",
            "setupType": "",
            "structure": {},
            "oversoldStatus": "near",
            "oversold": {
                "candidate": True,
                "qualified": True,
                "near": True,
                "status": "near",
                "drawdownPct": 51,
                "rangeLow": 40,
                "rangeHigh": 50,
                "distancePct": 2,
                "priorMainWaveQualified": True,
            },
            "fib": {"mainWaveQualified": True},
            "checkedAt": now_ms,
            "error": "",
        }

        events = server.update_price_watch_snapshot(result)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eventType"], "oversold_rebound")
        conn = server.auth_db()
        try:
            asset = conn.execute(
                """
                SELECT oversold_status, oversold_range_low, oversold_range_high
                FROM price_watch_assets WHERE symbol = 'TEST'
                """
            ).fetchone()
            state = conn.execute(
                """
                SELECT episode, armed, in_zone
                FROM price_watch_oversold_alert_state WHERE symbol = 'TEST'
                """
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(dict(asset), {
            "oversold_status": "near",
            "oversold_range_low": 40.0,
            "oversold_range_high": 50.0,
        })
        self.assertEqual(dict(state), {"episode": 1, "armed": 0, "in_zone": 1})

        repeated = server.update_price_watch_snapshot({**result, "checkedAt": now_ms + 60_000})
        self.assertEqual(repeated, [])

    def test_oversold_rebound_without_a_prior_main_wave_never_emits(self):
        now_ms = int(time.time() * 1000)
        conn = server.auth_db()
        try:
            conn.execute(
                "INSERT INTO price_watch_assets (symbol, name, manual_pinned, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
                ("BEAT", "BEAT", now_ms, now_ms),
            )
            conn.commit()
        finally:
            conn.close()
        result = {
            "symbol": "BEAT", "currentPrice": 0.417, "weekHigh": 3.979,
            "distancePct": 89.5, "provider": "Binance Futures", "status": "normal",
            "oversoldStatus": "near",
            "oversold": {
                "candidate": True, "qualified": True, "near": True, "status": "near",
                "drawdownPct": 89.5, "rangeLow": 0.342, "rangeHigh": 0.4232,
                "distancePct": 1.46, "priorMainWaveQualified": False,
            },
            "fib": {
                "mainWaveQualified": False, "impulseGainPct": 113.8152,
                "trendEfficiency": 0.1911, "positiveDayRatio": 0.75,
            },
            "checkedAt": now_ms, "error": "",
        }

        self.assertEqual(server.update_price_watch_snapshot(result), [])


if __name__ == "__main__":
    unittest.main()
