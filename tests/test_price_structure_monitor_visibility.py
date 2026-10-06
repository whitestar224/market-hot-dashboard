"""A structure popup must never outlive the card it points at.

The structure scanner admits a wider roster than the price monitor page: live
AiCoin rows, Binance-Wallet 4h rows and personal-X contexts all enter the
structure pool, and the pool keeps a 30-day window.  The page adds two gates the
scanner never had -- the turnover/activity gate and a usable quote -- so a coin
could keep popping "首次结构观察" for hours after its card disappeared from the
monitor pool.  These tests pin the roster gate that ends that.
"""

import gc
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

import server


NOW_MS = 1_800_000_000_000


def structure_asset(symbol, **overrides):
    """A row that passes every price-monitor source window."""
    row = {
        "symbol": symbol,
        "name": symbol,
        "manual_pinned": 0,
        "aicoin_first_seen_at": NOW_MS,
        "aicoin_last_seen_at": NOW_MS,
        "status": "normal",
        "created_at": NOW_MS,
        "updated_at": NOW_MS,
    }
    row.update(overrides)
    return row


def observation_item(symbol, *, monitor_pool=None, **extra):
    item = {
        "symbol": symbol,
        "provider": "Binance Futures",
        "checkedAt": NOW_MS,
        "frames": [{
            "key": "15m",
            "label": "15分钟",
            "pattern": "盘整突破",
            "stage": "结构观察",
            "confidence": 82,
            "support": 0.02731,
            "resistance": 0.0283773,
        }],
    }
    if monitor_pool is not None:
        item["monitorPool"] = monitor_pool
    item.update(extra)
    return item


def prearm_item(symbol, *, monitor_pool=None):
    item = {
        "symbol": symbol,
        "provider": "Binance Futures",
        "checkedAt": int(time.time() * 1000),
        "frames": [{
            "key": "15m",
            "label": "15分钟",
            "pattern": "横盘起飞",
            "stage": "结构观察",
            "confidence": 99,
            "resistance": 1.0,
            "pending": {
                "id": f"{symbol}-prearm",
                "interval": "15m",
                "label": "15分钟",
                "pattern": "横盘起飞",
                "certainty": 99,
                "grade": "A+",
                "triggerPrice": 1.0,
                "decisionTime": int(time.time() * 1000),
            },
        }],
    }
    if monitor_pool is not None:
        item["monitorPool"] = monitor_pool
    return item


class PriceStructureMonitorVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        self.original_snapshot_path = server.PRICE_STRUCTURE_SNAPSHOT_PATH
        server.AUTH_DB_PATH = Path(self.temp_dir.name) / "auth.db"
        server.PRICE_STRUCTURE_SNAPSHOT_PATH = Path(self.temp_dir.name) / "structure.json"
        server.init_auth_db()
        self.runtime_patchers = [
            patch.object(
                server,
                "ALERT_DELIVERY_STORE",
                server.AlertDeliveryStore(Path(self.temp_dir.name) / "alerts.sqlite"),
            ),
            patch.object(server, "DESKTOP_ALERT_QUEUE", deque()),
            patch.object(server, "DESKTOP_ALERT_DELIVERIES", {}),
            patch.object(server, "PRICE_MONITOR_ACTIVITY_STATES", {}),
            patch.object(server, "PRICE_MONITOR_RETENTION_ARCHIVES", {}),
            patch.object(server, "NEW_COIN_LOW_ACTIVITY_CACHE", None),
            patch.object(server, "NEW_COIN_LOW_MONITOR_ACTIVE", False),
        ]
        for patcher in self.runtime_patchers:
            patcher.start()
        self.reset_visibility()

    def tearDown(self):
        for patcher in reversed(self.runtime_patchers):
            patcher.stop()
        self.reset_visibility()
        server.AUTH_DB_PATH = self.original_db_path
        server.PRICE_STRUCTURE_SNAPSHOT_PATH = self.original_snapshot_path
        gc.collect()
        self.temp_dir.cleanup()

    @staticmethod
    def reset_visibility():
        with server.PRICE_STRUCTURE_MONITOR_VISIBILITY_LOCK:
            server.PRICE_STRUCTURE_MONITOR_VISIBILITY_CACHE = None

    def insert_assets(self, *rows):
        columns = sorted({key for row in rows for key in row})
        placeholders = ", ".join("?" for _ in columns)
        with server.auth_db() as conn:
            for row in rows:
                conn.execute(
                    f"INSERT INTO price_watch_assets ({', '.join(columns)}) "
                    f"VALUES ({placeholders})",
                    tuple(row.get(column) for column in columns),
                )

    @staticmethod
    def activity_state(symbol_value, activity, *, inactive=()):
        symbol = server.clean_price_watch_symbol(symbol_value)
        if symbol in inactive:
            return {
                "active": False,
                "status": "inactive",
                "reason": "turnover-below-threshold",
                "turnover24hUsd": 1_000.0,
                "thresholdUsd": 10_000_000.0,
            }
        return {
            "active": True,
            "status": "active",
            "reason": "turnover-active",
            "turnover24hUsd": 50_000_000.0,
            "thresholdUsd": 10_000_000.0,
        }

    def assert_observation_alert(self, item, *, expected):
        with patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(
            server, "price_structure_alert_interval_allowed", return_value=True
        ), patch.object(
            server, "claim_price_structure_observation_alert", return_value=True
        ), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ) as launch:
            count = server.launch_price_structure_first_observation_alerts(item, None)
        self.assertEqual(count, expected)
        self.assertEqual(launch.call_count, expected)

    def test_symbol_dropped_by_the_activity_gate_stops_emitting_alerts(self):
        self.insert_assets(structure_asset("VISIBLE1"), structure_asset("GONE1"))
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(
                symbol, activity, inactive={"GONE1"}
            ),
        ), patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(
            server, "price_structure_alert_interval_allowed", return_value=True
        ), patch.object(
            server, "claim_price_structure_observation_alert", return_value=True
        ), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ) as launch:
            tracked, visible = server.price_structure_monitor_visibility()
            self.assertIn("GONE1", tracked)
            self.assertNotIn("GONE1", visible)
            self.assertIn("VISIBLE1", visible)
            gone = server.launch_price_structure_first_observation_alerts(
                observation_item("GONE1"), None
            )
            alive = server.launch_price_structure_first_observation_alerts(
                observation_item("VISIBLE1"), None
            )

        self.assertEqual(gone, 0)
        self.assertEqual(alive, 1)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.args[0]["excludeSymbol"], "VISIBLE1")

    def test_symbol_without_a_usable_quote_stops_emitting_alerts(self):
        self.insert_assets(
            structure_asset("VISIBLE2"),
            structure_asset("QUOTELESS2", status="unavailable"),
        )
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(symbol, activity),
        ):
            tracked, visible = server.price_structure_monitor_visibility()

        self.assertIn("QUOTELESS2", tracked)
        self.assertNotIn("QUOTELESS2", visible)
        self.assertIn("VISIBLE2", visible)

    def test_untracked_symbol_keeps_the_previous_behaviour(self):
        """A live discovery row has no card to lose, so it is never gated."""
        self.insert_assets(structure_asset("VISIBLE3"))
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(symbol, activity),
        ), patch.object(
            server, "price_structure_symbol_excluded", return_value=False
        ), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(
            server, "price_structure_alert_interval_allowed", return_value=True
        ), patch.object(
            server, "claim_price_structure_observation_alert", return_value=True
        ), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ):
            tracked, visible = server.price_structure_monitor_visibility()
            self.assertNotIn("LIVEROW3", tracked)
            count = server.launch_price_structure_first_observation_alerts(
                observation_item("LIVEROW3"), None
            )

        self.assertEqual(tracked, {"VISIBLE3"})
        self.assertEqual(visible, {"VISIBLE3"})
        self.assertEqual(count, 1)

    def test_new_coin_low_items_are_never_gated_on_the_price_monitor_roster(self):
        self.insert_assets(structure_asset("VISIBLE4"))
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(symbol, activity),
        ), patch.object(
            server, "price_structure_symbol_excluded", return_value=False
        ), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(
            server, "price_structure_alert_interval_allowed", return_value=True
        ), patch.object(
            server, "claim_price_structure_observation_alert", return_value=True
        ), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ):
            item = observation_item("NEWLOW4", monitor_pool="new-coin-low")
            count = server.launch_price_structure_first_observation_alerts(item, None)

        self.assertTrue(server.price_structure_item_in_visible_monitor(item))
        self.assertEqual(count, 1)

    def test_roster_is_computed_once_per_cache_window(self):
        self.insert_assets(structure_asset("VISIBLE5"))
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(symbol, activity),
        ), patch.object(
            server, "price_watch_active_rows", wraps=server.price_watch_active_rows
        ) as active_rows:
            server.price_structure_monitor_visibility()
            server.price_structure_monitor_visibility()
            server.price_structure_item_in_visible_monitor({"symbol": "VISIBLE5"})

        self.assertEqual(active_rows.call_count, 1)

    def test_unavailable_roster_never_mutes_the_pool(self):
        with patch.object(
            server, "price_watch_active_rows", side_effect=RuntimeError("db unavailable")
        ), patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(
            server, "price_structure_alert_interval_allowed", return_value=True
        ), patch.object(
            server, "claim_price_structure_observation_alert", return_value=True
        ), patch.object(
            server, "launch_desktop_alert", return_value={"queued": True}
        ):
            self.assertIsNone(server.price_structure_monitor_visibility())
            count = server.launch_price_structure_first_observation_alerts(
                observation_item("UNKNOWN6"), None
            )

        self.assertEqual(count, 1)

    def test_prearm_candidates_drop_symbols_that_left_the_pool(self):
        self.insert_assets(structure_asset("VISIBLE7"), structure_asset("GONE7"))
        payload = {
            "ok": True,
            "items": [
                prearm_item("VISIBLE7"),
                prearm_item("GONE7"),
                prearm_item("NEWLOW7", monitor_pool="new-coin-low"),
            ],
        }
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(
                symbol, activity, inactive={"GONE7"}
            ),
        ), patch.object(
            server, "price_structure_latest_snapshot_payload", return_value=payload
        ), patch.object(
            server, "price_structure_excluded_symbols", return_value=set()
        ), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(
            server, "price_structure_alert_interval_allowed", return_value=True
        ):
            candidates = server.price_structure_prearm_candidates(
                now_ms=int(time.time() * 1000)
            )

        self.assertEqual(
            sorted(candidate["symbol"] for candidate in candidates),
            ["NEWLOW7", "VISIBLE7"],
        )

    def test_prearm_watcher_stops_alerting_a_symbol_that_left_the_pool(self):
        self.insert_assets(structure_asset("VISIBLE8"), structure_asset("GONE8"))
        candidate = {
            "id": "GONE8-prearm",
            "symbol": "GONE8",
            "interval": "15m",
            "triggerPrice": 1.0,
            "certainty": 99,
            "grade": "A+",
        }
        quote = {"price": 0.99, "speedPctPerMinute": 0.2, "upRatio": 0.8}
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(
                symbol, activity, inactive={"GONE8"}
            ),
        ), patch.object(
            server, "price_structure_symbol_excluded", return_value=False
        ), patch.object(server, "launch_desktop_alert") as launch:
            result = server.launch_price_structure_prearm_alert(candidate, quote)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "symbol left the price monitor pool")
        launch.assert_not_called()

    def test_formal_buy_trigger_is_gated_on_the_visible_roster(self):
        self.insert_assets(structure_asset("VISIBLE9"), structure_asset("GONE9"))
        item = {
            "symbol": "GONE9",
            "provider": "Binance Futures",
            "checkedAt": NOW_MS,
            "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
            "signals": [{
                "id": "gone9-signal",
                "interval": "15m",
                "decisionTime": int(time.time() * 1000),
                "barsAgo": 0,
                "triggerPrice": 1.0,
                "pattern": "横盘起飞",
                "certainty": 99,
                "grade": "A+",
            }],
        }
        with patch.object(
            server,
            "price_monitor_market_activity_state",
            side_effect=lambda symbol, activity: self.activity_state(
                symbol, activity, inactive={"GONE9"}
            ),
        ), patch.object(
            server, "price_structure_symbol_excluded", return_value=False
        ), patch.object(
            server, "price_structure_broadcast_allowed", return_value=True
        ), patch.object(
            server, "price_structure_alert_interval_allowed", return_value=True
        ), patch.object(server, "launch_desktop_alert") as launch:
            count = server.launch_price_structure_strategy_alerts(item)

        self.assertEqual(count, 0)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
