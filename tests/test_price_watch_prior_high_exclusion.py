import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PriceWatchPriorHighExclusionTests(unittest.TestCase):
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

    def insert_asset(self, symbol="TEST"):
        now_ms = int(time.time() * 1000)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, aicoin_first_seen_at, aicoin_last_seen_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, symbol, now_ms, now_ms, now_ms, now_ms),
            )
        return now_ms

    def test_exclusion_removes_only_prior_high_state_and_hides_aicoin_pool_item(self):
        now_ms = self.insert_asset()
        with server.auth_db() as conn:
            conn.execute(
                "INSERT INTO price_watch_alert_state VALUES (?, ?, 1, ?, 0, 2, ?)",
                ("TEST", 10.0, now_ms, now_ms),
            )
            conn.execute(
                """
                INSERT INTO price_watch_first_confirmations (
                    symbol, episode, confirmed_at, current_price, reference_high,
                    distance_pct, provider, setup_type
                ) VALUES (?, 1, ?, 9.8, 10, 2, 'test', 'retest')
                """,
                ("TEST", now_ms),
            )
            conn.execute(
                "INSERT INTO price_watch_oversold_alert_state VALUES (?, 1, 2, 1, 0, ?, 0, 1, ?)",
                ("TEST", now_ms, now_ms),
            )
            conn.execute(
                "INSERT INTO price_watch_fib_alert_state VALUES (?, '0.5', 1, 2, 1.5, 1.4, 1, 0, ?, 1, ?)",
                ("TEST", now_ms, now_ms),
            )

        payload = server.exclude_price_watch_prior_high("TEST")

        self.assertTrue(payload["ok"])
        item = next(item for item in payload["items"] if item["symbol"] == "TEST")
        self.assertFalse(item["priorHighEnabled"])
        self.assertEqual(payload["summary"]["priorHighTotal"], 0)
        with server.auth_db() as conn:
            asset = conn.execute(
                "SELECT prior_high_excluded_at, prior_high_absent_at FROM price_watch_assets WHERE symbol = 'TEST'"
            ).fetchone()
            prior_state = conn.execute(
                "SELECT 1 FROM price_watch_alert_state WHERE symbol = 'TEST'"
            ).fetchone()
            confirmation = conn.execute(
                "SELECT 1 FROM price_watch_first_confirmations WHERE symbol = 'TEST'"
            ).fetchone()
            oversold_state = conn.execute(
                "SELECT 1 FROM price_watch_oversold_alert_state WHERE symbol = 'TEST'"
            ).fetchone()
            fib_state = conn.execute(
                "SELECT 1 FROM price_watch_fib_alert_state WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertGreater(asset["prior_high_excluded_at"], 0)
        self.assertEqual(asset["prior_high_absent_at"], 0)
        self.assertIsNone(prior_state)
        self.assertIsNone(confirmation)
        self.assertIsNotNone(oversold_state)
        self.assertIsNotNone(fib_state)

    def test_excluded_symbol_stays_out_until_it_leaves_and_reenters_aicoin(self):
        self.insert_asset()
        server.exclude_price_watch_prior_high("TEST")
        test_row = {"symbol": "TEST", "name": "Test", "note": "crypto"}
        other_row = {"symbol": "OTHER", "name": "Other", "note": "crypto"}

        with patch.object(
            server,
            "price_watch_aicoin_source",
            return_value={"status": "ok", "rows": [test_row]},
        ):
            server.sync_price_watch_aicoin_candidates()
        with server.auth_db() as conn:
            current = conn.execute(
                "SELECT prior_high_excluded_at, prior_high_absent_at FROM price_watch_assets WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertGreater(current["prior_high_excluded_at"], 0)
        self.assertEqual(current["prior_high_absent_at"], 0)

        with patch.object(
            server,
            "price_watch_aicoin_source",
            return_value={"status": "ok", "rows": [other_row]},
        ):
            server.sync_price_watch_aicoin_candidates()
        with server.auth_db() as conn:
            absent = conn.execute(
                "SELECT prior_high_excluded_at, prior_high_absent_at FROM price_watch_assets WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertGreater(absent["prior_high_excluded_at"], 0)
        self.assertGreater(absent["prior_high_absent_at"], 0)

        with patch.object(
            server,
            "price_watch_aicoin_source",
            return_value={"status": "ok", "rows": [test_row]},
        ):
            server.sync_price_watch_aicoin_candidates()
        with server.auth_db() as conn:
            restored = conn.execute(
                "SELECT prior_high_excluded_at, prior_high_absent_at FROM price_watch_assets WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertEqual(dict(restored), {"prior_high_excluded_at": 0, "prior_high_absent_at": 0})

    def test_exclusion_suppresses_prior_high_but_keeps_oversold_evaluation(self):
        now_ms = self.insert_asset()
        server.exclude_price_watch_prior_high("TEST")
        result = {
            "symbol": "TEST",
            "currentPrice": 49,
            "weekHigh": 50,
            "distancePct": 2,
            "provider": "Test Futures",
            "status": "near",
            "setupType": "retest",
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
            "checkedAt": now_ms + 1,
            "error": "",
        }

        events = server.update_price_watch_snapshot(result)

        self.assertEqual([event["eventType"] for event in events], ["oversold_rebound"])


if __name__ == "__main__":
    unittest.main()
