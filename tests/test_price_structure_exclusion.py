import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PriceStructureExclusionTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = self.db_path
        server.init_auth_db()
        self.wallet_history_patcher = patch.object(server, "binance_wallet_4h_structure_rows", return_value=[])
        self.wallet_history_patcher.start()
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()

    def tearDown(self):
        self.wallet_history_patcher.stop()
        server.AUTH_DB_PATH = self.original_db_path
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()
        gc.collect()
        self.db_path.unlink(missing_ok=True)

    @staticmethod
    def source(*symbols):
        return {
            "status": "ok",
            "rows": [
                {"symbol": symbol, "name": symbol.title(), "note": "crypto"}
                for symbol in symbols
            ],
        }

    def test_structure_exclusion_is_independent_from_other_price_monitors(self):
        now_ms = int(time.time() * 1000)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned, aicoin_first_seen_at,
                    aicoin_last_seen_at, created_at, updated_at
                ) VALUES ('TEST', 'Test', 0, ?, ?, ?, ?)
                """,
                (now_ms, now_ms, now_ms, now_ms),
            )

        result = server.exclude_price_structure_symbol("TEST")

        self.assertTrue(result["ok"])
        self.assertEqual(result["restoreRule"], "aicoin_leave_then_reenter")
        with server.auth_db() as conn:
            exclusion = conn.execute(
                "SELECT excluded_at, absent_at FROM price_structure_exclusions WHERE symbol = 'TEST'"
            ).fetchone()
            asset = conn.execute(
                """
                SELECT prior_high_excluded_at, oversold_status, fib_status
                FROM price_watch_assets WHERE symbol = 'TEST'
                """
            ).fetchone()
        self.assertGreater(exclusion["excluded_at"], 0)
        self.assertEqual(exclusion["absent_at"], 0)
        self.assertEqual(asset["prior_high_excluded_at"], 0)
        self.assertEqual(asset["oversold_status"], "normal")
        self.assertEqual(asset["fib_status"], "normal")

    def test_symbol_stays_hidden_until_aicoin_leave_then_reentry(self):
        server.exclude_price_structure_symbol("TEST")
        common_patches = (
            patch.object(server, "strategy_active_adaptive_contexts", return_value=[]),
            patch.object(server, "price_watch_active_rows", return_value=[]),
        )
        with common_patches[0], common_patches[1], patch.object(
            server, "price_watch_aicoin_source", return_value=self.source("TEST")
        ):
            self.assertEqual(server.price_structure_watch_rows(), [])
        with server.auth_db() as conn:
            current = conn.execute(
                "SELECT absent_at FROM price_structure_exclusions WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertEqual(current["absent_at"], 0)

        with patch.object(server, "strategy_active_adaptive_contexts", return_value=[]), patch.object(
            server, "price_watch_active_rows", return_value=[]
        ), patch.object(server, "price_watch_aicoin_source", return_value=self.source("OTHER")):
            server.price_structure_watch_rows()
        with server.auth_db() as conn:
            absent = conn.execute(
                "SELECT absent_at FROM price_structure_exclusions WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertGreater(absent["absent_at"], 0)

        with patch.object(server, "strategy_active_adaptive_contexts", return_value=[]), patch.object(
            server, "price_watch_active_rows", return_value=[]
        ), patch.object(server, "price_watch_aicoin_source", return_value=self.source("TEST")):
            restored_rows = server.price_structure_watch_rows()
        self.assertEqual([row["symbol"] for row in restored_rows], ["TEST"])
        self.assertFalse(server.price_structure_symbol_excluded("TEST"))

    def test_excluded_symbol_cannot_emit_formal_or_prearm_alerts(self):
        server.exclude_price_structure_symbol("TEST")
        item = {
            "symbol": "TEST",
            "signals": [
                {
                    "id": "test-signal",
                    "interval": "15m",
                    "decisionTime": int(time.time() * 1000),
                    "pattern": "横盘起飞",
                    "certainty": 99,
                    "grade": "A+",
                    "price": 1.0,
                }
            ],
        }
        candidate = {
            "id": "test-prearm",
            "symbol": "TEST",
            "interval": "15m",
            "triggerPrice": 1.0,
            "certainty": 99,
            "grade": "A+",
        }

        with patch.object(server, "launch_desktop_alert") as launch:
            self.assertEqual(server.launch_price_structure_strategy_alerts(item), 0)
            prearm = server.launch_price_structure_prearm_alert(
                candidate,
                {"price": 0.99, "speedPctPerMinute": 0.2, "upRatio": 0.8},
            )

        self.assertTrue(prearm["skipped"])
        launch.assert_not_called()

    def test_structure_observation_cooldown_is_shared_by_all_intervals_and_persists(self):
        now_ms = 1_800_000_000_000
        self.assertTrue(server.claim_price_structure_observation_alert("TEST", now_ms=now_ms))
        self.assertFalse(server.claim_price_structure_observation_alert(
            "TEST",
            now_ms=now_ms + server.PRICE_STRUCTURE_OBSERVATION_COOLDOWN_MS - 1,
        ))
        self.assertTrue(server.claim_price_structure_observation_alert(
            "TEST",
            now_ms=now_ms + server.PRICE_STRUCTURE_OBSERVATION_COOLDOWN_MS,
        ))

        with server.auth_db() as conn:
            state = conn.execute(
                "SELECT last_alert_at FROM price_structure_observation_alert_state WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertEqual(
            state["last_alert_at"],
            now_ms + server.PRICE_STRUCTURE_OBSERVATION_COOLDOWN_MS,
        )


if __name__ == "__main__":
    unittest.main()
