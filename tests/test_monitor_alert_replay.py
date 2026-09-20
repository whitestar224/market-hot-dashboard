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

    def test_stale_prior_high_scan_alerts_once_when_the_detected_zone_was_never_delivered(self):
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
                "currentPrice": 10.0 * (1 - distance / 100),
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

        events = server.update_price_watch_snapshot(snapshot("near", now_ms, 2.0))
        self.assertEqual([event["eventType"] for event in events], ["prior_high"])
        with server.auth_db() as conn:
            seeded = conn.execute(
                "SELECT in_zone, last_alert_at FROM price_watch_alert_state WHERE symbol = 'TEST'"
            ).fetchone()
        self.assertEqual(dict(seeded), {"in_zone": 1, "last_alert_at": now_ms})

        self.assertEqual(server.update_price_watch_snapshot(snapshot("near", now_ms + 1, 2.0)), [])

    def test_pending_structure_alerts_once_even_when_the_first_snapshot_was_already_structured(self):
        now_ms = int(time.time() * 1000)
        pending = {
            "id": "zhongji-5m-pending",
            "interval": "5m",
            "pattern": "盘整突破 + 横盘起飞",
            "certainty": 99,
            "grade": "A+",
            "triggerPrice": 151.5,
        }
        current = {
            "symbol": "ZHONGJI",
            "monitorPool": "aicoin-x-wallet",
            "structureMembershipSources": [],
            "provider": "Binance Futures",
            "checkedAt": now_ms,
            "broadcastEligibility": {
                "eligible": False,
                "reason": "deep-legacy-no-new-wave",
                "allowedIntervals": [],
            },
            "frames": [{
                "key": "5m",
                "label": "5分钟",
                "pattern": "盘整突破 + 横盘起飞",
                "stage": "预备起爆",
                "confidence": 99,
                "pending": pending,
                "signal": None,
            }],
        }
        previous = {**current, "checkedAt": now_ms - server.MONITOR_ALERT_REPLAY_MAX_GAP_MS - 1}

        with patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ) as launch:
            first = server.launch_price_structure_first_observation_alerts(current, previous)
            repeated = server.launch_price_structure_first_observation_alerts(current, current)

        self.assertEqual(first, 1)
        self.assertEqual(repeated, 0)
        self.assertEqual(launch.call_count, 1)
        self.assertIn("首次结构观察", launch.call_args.args[0]["title"])

    def test_failed_popup_queue_does_not_consume_the_structure_observation(self):
        now_ms = int(time.time() * 1000)
        item = {
            "symbol": "RETRY",
            "monitorPool": "aicoin-x-wallet",
            "provider": "Binance Futures",
            "checkedAt": now_ms,
            "frames": [{
                "key": "15m",
                "label": "15分钟",
                "pattern": "盘整突破",
                "stage": "预备起爆",
                "confidence": 95,
                "pending": {"id": "retry-pending"},
                "signal": None,
            }],
        }
        with patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "launch_desktop_alert", side_effect=[{"queued": False}, {"queued": True}]
        ) as launch:
            rejected = server.launch_price_structure_first_observation_alerts(item, None)
            retried = server.launch_price_structure_first_observation_alerts(item, item)

        self.assertEqual(rejected, 0)
        self.assertEqual(retried, 1)
        self.assertEqual(launch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
