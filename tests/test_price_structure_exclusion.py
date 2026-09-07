import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PriceStructureExclusionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "auth.db"
        self.original_db_path = server.AUTH_DB_PATH
        self.original_structure_snapshot_path = server.PRICE_STRUCTURE_SNAPSHOT_PATH
        self.original_new_low_snapshot_path = server.NEW_COIN_LOW_SNAPSHOT_PATH
        server.AUTH_DB_PATH = self.db_path
        server.PRICE_STRUCTURE_SNAPSHOT_PATH = Path(self.temp_dir.name) / "price-structure.json"
        server.NEW_COIN_LOW_SNAPSHOT_PATH = Path(self.temp_dir.name) / "new-low-structure.json"
        server.init_auth_db()
        self.wallet_history_patcher = patch.object(server, "binance_wallet_4h_structure_rows", return_value=[])
        self.wallet_history_patcher.start()
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()
        with server.NEW_COIN_LOW_LOCK:
            server.NEW_COIN_LOW_ITEMS.clear()

    def tearDown(self):
        self.wallet_history_patcher.stop()
        server.AUTH_DB_PATH = self.original_db_path
        server.PRICE_STRUCTURE_SNAPSHOT_PATH = self.original_structure_snapshot_path
        server.NEW_COIN_LOW_SNAPSHOT_PATH = self.original_new_low_snapshot_path
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()
        with server.NEW_COIN_LOW_LOCK:
            server.NEW_COIN_LOW_ITEMS.clear()
        gc.collect()
        self.temp_dir.cleanup()

    @staticmethod
    def source(*symbols):
        return {
            "status": "ok",
            "rows": [
                {"symbol": symbol, "name": symbol.title(), "note": "crypto"}
                for symbol in symbols
            ],
        }

    def test_structure_exclusion_removes_symbol_from_every_monitor_pool(self):
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
        self.assertEqual(result["restoreRule"], "leave_then_reenter_or_manual_readd")
        with server.auth_db() as conn:
            exclusion = conn.execute(
                "SELECT excluded_at, absent_at FROM price_structure_exclusions WHERE symbol = 'TEST'"
            ).fetchone()
            asset = conn.execute(
                """
                SELECT prior_high_excluded_at, opportunity_active,
                       opportunity_manual_removed_at, oversold_status, fib_status
                FROM price_watch_assets WHERE symbol = 'TEST'
                """
            ).fetchone()
        self.assertGreater(exclusion["excluded_at"], 0)
        self.assertEqual(exclusion["absent_at"], 0)
        self.assertGreater(asset["prior_high_excluded_at"], 0)
        self.assertEqual(asset["opportunity_active"], 0)
        self.assertGreater(asset["opportunity_manual_removed_at"], 0)
        self.assertEqual(asset["oversold_status"], "normal")
        self.assertEqual(asset["fib_status"], "normal")
        self.assertEqual(server.price_watch_active_rows(), [])

    def test_single_aicoin_omission_does_not_restore_excluded_symbol(self):
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
                """
                SELECT absent_at, absent_confirmations
                FROM price_structure_exclusions WHERE symbol = 'TEST'
                """
            ).fetchone()
        self.assertGreater(absent["absent_at"], 0)
        self.assertEqual(absent["absent_confirmations"], 1)

        with patch.object(server, "strategy_active_adaptive_contexts", return_value=[]), patch.object(
            server, "price_watch_active_rows", return_value=[]
        ), patch.object(server, "price_watch_aicoin_source", return_value=self.source("TEST")):
            restored_rows = server.price_structure_watch_rows()
        self.assertEqual(restored_rows, [])
        self.assertTrue(server.price_structure_symbol_excluded("TEST"))
        with server.auth_db() as conn:
            reset = conn.execute(
                """
                SELECT absent_at, absent_confirmations, last_absent_at
                FROM price_structure_exclusions WHERE symbol = 'TEST'
                """
            ).fetchone()
        self.assertEqual(reset["absent_at"], 0)
        self.assertEqual(reset["absent_confirmations"], 0)
        self.assertEqual(reset["last_absent_at"], 0)

    def test_confirmed_aicoin_leave_then_reentry_restores_symbol(self):
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, opportunity_active, opportunity_first_seen_at,
                    opportunity_manual_removed_at, created_at, updated_at
                ) VALUES ('TEST', 'Test', 1, 1, 0, 1, 1)
                """
            )
        server.exclude_price_structure_symbol("TEST")
        base_ms = 1_800_000_000_000
        sample_ms = server.PRICE_STRUCTURE_REENTRY_CONFIRM_INTERVAL_SECONDS * 1000
        minimum_ms = server.PRICE_STRUCTURE_REENTRY_ABSENT_MIN_SECONDS * 1000

        for observed_at in (base_ms, base_ms + sample_ms, base_ms + minimum_ms):
            excluded = server.reconcile_price_structure_exclusions(
                {"OTHER"},
                source_is_current=True,
                now_ms=observed_at,
            )
            self.assertIn("TEST", excluded)

        restored = server.reconcile_price_structure_exclusions(
            {"TEST"},
            source_is_current=True,
            now_ms=base_ms + minimum_ms + 1_000,
        )
        self.assertNotIn("TEST", restored)
        self.assertFalse(server.price_structure_symbol_excluded("TEST"))
        with server.auth_db() as conn:
            asset = conn.execute(
                """
                SELECT prior_high_excluded_at, opportunity_active,
                       opportunity_manual_removed_at, dismissed_until
                FROM price_watch_assets WHERE symbol = 'TEST'
                """
            ).fetchone()
        self.assertEqual(asset["prior_high_excluded_at"], 0)
        self.assertEqual(asset["opportunity_active"], 1)
        self.assertEqual(asset["opportunity_manual_removed_at"], 0)
        self.assertEqual(asset["dismissed_until"], 0)

    def test_stale_pre_restart_absence_does_not_restore_symbol(self):
        server.exclude_price_structure_symbol("TEST")
        base_ms = 1_800_000_000_000
        sample_ms = server.PRICE_STRUCTURE_REENTRY_CONFIRM_INTERVAL_SECONDS * 1000
        minimum_ms = server.PRICE_STRUCTURE_REENTRY_ABSENT_MIN_SECONDS * 1000
        stale_gap_ms = server.PRICE_STRUCTURE_REENTRY_RECENCY_SECONDS * 1000 + 1

        for observed_at in (base_ms, base_ms + sample_ms, base_ms + sample_ms * 2):
            server.reconcile_price_structure_exclusions(
                {"OTHER"},
                source_is_current=True,
                now_ms=observed_at,
            )

        still_excluded = server.reconcile_price_structure_exclusions(
            {"TEST"},
            source_is_current=True,
            now_ms=base_ms + minimum_ms + stale_gap_ms,
        )
        self.assertIn("TEST", still_excluded)
        self.assertTrue(server.price_structure_symbol_excluded("TEST"))

    def test_exclusion_purges_memory_and_disk_snapshots_immediately(self):
        item = {
            "symbol": "TEST",
            "frames": [{"key": "1h"}],
            "signalCount": 2,
            "checkedAt": int(time.time() * 1000),
        }
        payload = {
            "ok": True,
            "items": [item, {"symbol": "KEEP", "frames": [], "signalCount": 0}],
            "summary": {"total": 2, "available": 1, "signals": 2},
        }
        server.write_json_cache(server.PRICE_STRUCTURE_SNAPSHOT_PATH, {
            "cacheKey": "old",
            "savedAt": int(time.time() * 1000),
            "payload": payload,
        })
        server.write_json_cache(server.NEW_COIN_LOW_SNAPSHOT_PATH, {
            "savedAt": int(time.time() * 1000),
            "items": [item],
        })
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE["old"] = (time.time(), payload)
        with server.NEW_COIN_LOW_LOCK:
            server.NEW_COIN_LOW_ITEMS["TEST"] = dict(item)

        server.exclude_price_structure_symbol("TEST")

        latest = server.price_structure_latest_snapshot_payload()
        disk_payload = server.read_json_cache(server.PRICE_STRUCTURE_SNAPSHOT_PATH)["payload"]
        new_low = server.read_json_cache(server.NEW_COIN_LOW_SNAPSHOT_PATH)
        self.assertEqual([row["symbol"] for row in latest["items"]], ["KEEP"])
        self.assertEqual([row["symbol"] for row in disk_payload["items"]], ["KEEP"])
        self.assertEqual(disk_payload["summary"]["total"], 1)
        self.assertEqual(new_low["items"], [])
        with server.NEW_COIN_LOW_LOCK:
            self.assertNotIn("TEST", server.NEW_COIN_LOW_ITEMS)

    def test_scan_started_before_exclusion_cannot_reinsert_symbol(self):
        row = {"symbol": "TEST", "name": "Test"}
        fresh = {
            **row,
            "frames": [],
            "signals": [],
            "signalCount": 0,
            "checkedAt": int(time.time() * 1000),
        }

        def finish_after_exclusion(*_args, **_kwargs):
            server.exclude_price_structure_symbol("TEST")
            return fresh

        with patch.object(server, "fetch_price_structure_item", side_effect=finish_after_exclusion):
            result = server.refresh_price_structure_strategy_monitor_item(row, [row])

        self.assertTrue(result["skipped"])
        self.assertEqual(result["skipReason"], "已从结构监控池剔除")
        self.assertTrue(server.price_structure_symbol_excluded("TEST"))
        self.assertNotIn(
            "TEST",
            [item.get("symbol") for item in server.price_structure_latest_snapshot_payload().get("items", [])],
        )

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
