import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class MonitorAlertReplayTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = self.db_path
        server.init_auth_db()
        with server.PRICE_STRUCTURE_REPLAY_SUPPRESSED_LOCK:
            server.PRICE_STRUCTURE_REPLAY_SUPPRESSED_KEYS.clear()

    def tearDown(self):
        with server.PRICE_STRUCTURE_REPLAY_SUPPRESSED_LOCK:
            server.PRICE_STRUCTURE_REPLAY_SUPPRESSED_KEYS.clear()
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.db_path.unlink(missing_ok=True)

    def test_long_unobserved_gap_is_stale_but_missing_or_recent_baseline_is_not(self):
        now_ms = 2_000_000
        max_gap_ms = 10 * 60 * 1000

        self.assertFalse(server.monitor_alert_replay_gap_is_stale(0, now_ms, max_gap_ms=max_gap_ms))
        self.assertFalse(server.monitor_alert_replay_gap_is_stale(now_ms - max_gap_ms, now_ms, max_gap_ms=max_gap_ms))
        self.assertTrue(server.monitor_alert_replay_gap_is_stale(now_ms - max_gap_ms - 1, now_ms, max_gap_ms=max_gap_ms))

    def test_stale_structure_keys_stay_quiet_while_a_new_signal_still_alerts(self):
        now_ms = int(time.time() * 1000)
        old_signal = {
            "id": "old-signal",
            "interval": "1h",
            "label": "1小时",
            "decisionTime": now_ms,
            "barsAgo": 0,
        }
        old_pending = {
            "id": "old-pending",
            "interval": "1h",
            "decisionTime": now_ms,
        }
        item = {
            "symbol": "TEST",
            "checkedAt": now_ms,
            "signals": [old_signal],
            "alertHints": [],
            "frames": [{"key": "1h", "pending": old_pending}],
        }
        suppressed = server.suppress_price_structure_replay_alerts(item)

        self.assertIn(server.price_structure_replay_alert_key("signal", "TEST", old_signal), suppressed)
        self.assertIn(server.price_structure_replay_alert_key("prearm", "TEST", old_pending), suppressed)
        fallback = server.price_structure_replay_baseline_item(
            {**item, "checkedAt": now_ms - server.MONITOR_ALERT_REPLAY_MAX_GAP_MS - 1},
            {"symbol": "TEST", "checkedAt": now_ms, "frames": [], "signals": []},
        )
        self.assertEqual(fallback["frames"][0]["pending"]["id"], "old-pending")
        self.assertEqual(fallback["checkedAt"], now_ms)

        with patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(server, "price_structure_alert_interval_allowed", return_value=True), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ) as launch:
            self.assertEqual(server.launch_price_structure_strategy_alerts(item), 0)
            launch.assert_not_called()

            new_signal = {**old_signal, "id": "new-signal", "decisionTime": now_ms + 1}
            new_item = {**item, "checkedAt": now_ms + 1, "signals": [new_signal], "frames": []}
            self.assertEqual(server.launch_price_structure_strategy_alerts(new_item), 1)
            launch.assert_called_once()

    def test_stale_prior_high_scan_seeds_zone_silently_then_live_reentry_alerts(self):
        now_ms = int(time.time() * 1000)
        old_checked_at = now_ms - server.MONITOR_ALERT_REPLAY_MAX_GAP_MS - 1
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, aicoin_first_seen_at, aicoin_last_seen_at,
                    last_checked_at, status, created_at, updated_at
                ) VALUES ('TEST', 'TEST', ?, ?, ?, 'normal', ?, ?)
                """,
                (now_ms, now_ms, old_checked_at, now_ms, now_ms),
            )

        def snapshot(status, checked_at, distance):
            return {
                "symbol": "TEST",
                "currentPrice": 9.8,
                "weekHigh": 10.0,
                "distancePct": distance,
                "provider": "Test Futures",
                "status": status,
                "setupType": "retest",
                "structure": {},
                "oversoldStatus": "normal",
                "oversold": {"status": "normal", "candidate": False},
                "fibStatus": "normal",
                "fib": {"status": "normal", "candidate": False},
                "checkedAt": checked_at,
                "error": "",
            }

        self.assertEqual(server.update_price_watch_snapshot(snapshot("near", now_ms, 2.0)), [])
        with server.auth_db() as conn:
            seeded = conn.execute(
                "SELECT in_zone, last_alert_at FROM price_watch_alert_state WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertEqual(dict(seeded), {"in_zone": 1, "last_alert_at": 0})

        server.update_price_watch_snapshot(snapshot("normal", now_ms + 1, 8.0))
        events = server.update_price_watch_snapshot(snapshot("near", now_ms + 2, 2.0))
        self.assertEqual([event["eventType"] for event in events], ["prior_high"])


if __name__ == "__main__":
    unittest.main()
