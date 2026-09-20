import unittest
import tempfile
import time
import sqlite3
import gc
from pathlib import Path
from collections import deque
from unittest.mock import patch

import desktop_alert
import server


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


def structure_alert(symbol: str):
    return server.normalize_desktop_alert({
        "key": f"price-watch:structure-first:{symbol}:1h:identity",
        "kind": "价格监控",
        "source": "币种价格监控",
        "sourceLabel": "S",
        "title": f"{symbol} 1小时 首次结构观察",
        "priority": "一级·首次结构",
        "excludeAction": "exclude_structure",
        "queuePriority": server.DESKTOP_ALERT_TRADING_PREARM_PRIORITY - 1,
    })


class DesktopAlertStructureStackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.auth_db_path = Path(self.temp.name) / "auth.sqlite"
        for name, value in [('ALERT_DELIVERY_STORE', server.AlertDeliveryStore(Path(self.temp.name)/'alerts.sqlite')), ('DESKTOP_ALERT_DELIVERIES', {}), ('AUTH_DB_PATH', self.auth_db_path)]:
            p=patch.object(server,name,value);p.start();self.addCleanup(p.stop)
        journal = patch.object(server, "record_event_flow_popup")
        journal.start()
        self.addCleanup(journal.stop)

    def dispatch(self):
        now=time.time()
        for item in server.DESKTOP_ALERT_QUEUE:
            server.ALERT_DELIVERY_STORE.admit(item,server.alert_dedupe_keys(item),server.desktop_alert_queue_priority(item),now=now)
        for step in range(len(server.DESKTOP_ALERT_QUEUE)):
            with patch.object(server.time,'time',return_value=now+step*11):
                server.desktop_alert_delivery_tick()
                for identity, active in server.DESKTOP_ALERT_DELIVERIES.items():
                    server.ALERT_DELIVERY_STORE.receipt(identity,active['token'],'visible',4000)

    def test_structure_popup_is_classified_narrowly(self):
        self.assertTrue(server.desktop_alert_is_structure_observation(structure_alert("MON")))
        self.assertFalse(server.desktop_alert_is_structure_observation(server.normalize_desktop_alert({
            "key": "price-watch:dragon-wave:MON:1h:123",
            "kind": "价格监控",
            "priority": "一级·正式买点",
        })))

    def test_structure_popups_remain_alive_and_use_separate_slots(self):
        first_process = FakeProcess()
        second_process = FakeProcess()
        with (
            patch.object(server, "DESKTOP_ALERT_QUEUE", deque([structure_alert("MON"), structure_alert("PONS")])),
            patch.object(server, "DESKTOP_ALERT_QUEUE_ACTIVE", True),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_AT", 0),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_PRIORITY", 0),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS", None),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS_SLOT", 0),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
            patch.object(server, "desktop_alert_interval_seconds", return_value=0),
            patch.object(server, "price_structure_symbol_excluded", return_value=False),
            patch.object(server, "spawn_desktop_alert_process", side_effect=[first_process, second_process]) as spawn,
        ):
            self.dispatch()
            self.assertFalse(first_process.terminated)
            self.assertFalse(second_process.terminated)
            self.assertEqual([call.args[1] for call in spawn.call_args_list], [0, 1])
            self.assertEqual(spawn.call_args_list[0].args[0]["autoCloseMs"], 2 * 60 * 1000)

    def test_normal_popup_avoids_a_live_structure_slot(self):
        structure_process = FakeProcess()
        normal_process = FakeProcess()
        normal = server.normalize_desktop_alert({"key": "news:1", "kind": "律动快讯"})
        with (
            patch.object(server, "DESKTOP_ALERT_QUEUE", deque([normal])),
            patch.object(server, "DESKTOP_ALERT_QUEUE_ACTIVE", True),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_AT", 0),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_PRIORITY", 0),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS", None),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS_SLOT", 0),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {0: (structure_process, 1.0)}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
            patch.object(server, "desktop_alert_interval_seconds", return_value=0),
            patch.object(server, "spawn_desktop_alert_process", return_value=normal_process) as spawn,
        ):
            self.dispatch()
            self.assertFalse(structure_process.terminated)
            self.assertEqual(spawn.call_args.args[1], 1)

    def test_normal_popups_remain_alive_and_stack_instead_of_replacing(self):
        first_process = FakeProcess()
        second_process = FakeProcess()
        alerts = deque([
            server.normalize_desktop_alert({"key": "news:1", "kind": "律动快讯"}),
            server.normalize_desktop_alert({"key": "news:2", "kind": "律动快讯"}),
        ])
        with (
            patch.object(server, "DESKTOP_ALERT_QUEUE", alerts),
            patch.object(server, "DESKTOP_ALERT_QUEUE_ACTIVE", True),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_AT", 0),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_PRIORITY", 0),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS", None),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS_SLOT", 0),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
            patch.object(server, "desktop_alert_interval_seconds", return_value=0),
            patch.object(server, "spawn_desktop_alert_process", side_effect=[first_process, second_process]) as spawn,
        ):
            self.dispatch()

        self.assertFalse(first_process.terminated)
        self.assertFalse(second_process.terminated)
        self.assertEqual([call.args[1] for call in spawn.call_args_list], [0, 1])

    def test_popup_geometry_tiles_concurrent_windows_without_covering_each_other(self):
        bounds = (0, 0, 1920, 1080)
        positions = [desktop_alert.popup_position(bounds, 374, 212, slot) for slot in range(5)]
        self.assertEqual(positions, [(18, 850), (18, 628), (18, 406), (18, 184), (402, 850)])

    def test_one_delivery_tick_launches_all_fresh_windows_concurrently(self):
        first_process = FakeProcess()
        second_process = FakeProcess()
        now = time.time()
        alerts = [
            server.normalize_desktop_alert({"key": "news:instant:1", "kind": "律动快讯"}),
            server.normalize_desktop_alert({"key": "news:instant:2", "kind": "律动快讯"}),
        ]
        for alert in alerts:
            server.ALERT_DELIVERY_STORE.admit(
                alert, server.alert_dedupe_keys(alert), server.desktop_alert_queue_priority(alert), now=now
            )
        with (
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS", None),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS_SLOT", 0),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
            patch.object(server, "price_structure_symbol_excluded", return_value=False),
            patch.object(
                server, "spawn_desktop_alert_process", side_effect=[first_process, second_process]
            ) as spawn,
        ):
            server.desktop_alert_delivery_tick()

        self.assertEqual(spawn.call_count, 2)
        self.assertEqual([call.args[1] for call in spawn.call_args_list], [0, 1])

    def test_new_alert_attempts_window_delivery_in_producer_turn(self):
        process = FakeProcess()
        with (
            patch.object(server, "DESKTOP_ALERT_SEEN", {}),
            patch.object(server, "DESKTOP_ALERT_SEEN_LOADED", True),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS", None),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS_SLOT", 0),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
            patch.object(server, "DESKTOP_ALERT_QUEUE_ACTIVE", True),
            patch.object(server, "prefetch_popup_explanations"),
            patch.object(server, "record_desktop_alert_news_trade_intake"),
            patch.object(server, "price_structure_symbol_excluded", return_value=False),
            patch.object(server, "spawn_desktop_alert_process", return_value=process) as spawn,
        ):
            result = server.launch_desktop_alert({
                "key": "news:direct-delivery",
                "kind": "律动快讯",
                "source": "BlockBeats",
                "title": "新信息即时弹出",
            })

        self.assertFalse(result.get("deduped", False))
        spawn.assert_called_once()

    def test_stale_breakout_is_suppressed_before_a_window_opens(self):
        now = time.time()
        alert = server.normalize_desktop_alert({
            "key": "price-watch:STALE:episode:1",
            "kind": "价格监控",
            "priority": "突破前高",
            "confirmSymbol": "STALE",
            "time": int((now - 30) * 1000),
        })
        self.assertIn(
            "超过实时展示窗口",
            server.desktop_alert_price_watch_suppression_reason(alert, pending=True, now=now),
        )

    def test_delayed_structure_observation_is_not_filtered_as_a_stale_prior_high(self):
        now = time.time()
        alert = structure_alert("STRUCTURE")
        alert["time"] = int((now - 60) * 1000)

        self.assertEqual(
            server.desktop_alert_price_watch_suppression_reason(alert, pending=True, now=now),
            "",
        )

    def test_delayed_near_high_is_delivered_while_the_condition_still_holds(self):
        now = time.time()
        event_ms = int((now - 60) * 1000)
        with sqlite3.connect(self.auth_db_path) as db:
            db.execute("""
                CREATE TABLE price_watch_assets (
                    symbol TEXT PRIMARY KEY, current_price REAL, week_high REAL,
                    status TEXT, last_checked_at INTEGER
                )
            """)
            db.execute(
                "INSERT INTO price_watch_assets VALUES (?, ?, ?, ?, ?)",
                ("NEAR", 9.9, 10.0, "near", int(now * 1000)),
            )
        alert = server.normalize_desktop_alert({
            "key": "price-watch:NEAR:episode:1",
            "kind": "价格监控",
            "priority": "接近前高",
            "confirmSymbol": "NEAR",
            "time": event_ms,
        })
        with patch.object(server, "price_watch_prior_high_source_enabled", return_value=True):
            reason = server.desktop_alert_price_watch_suppression_reason(
                alert, pending=True, now=now
            )

        self.assertEqual(reason, "")

    def test_visible_breakout_closes_after_price_falls_back_below_high(self):
        now = time.time()
        event_ms = int((now - 2) * 1000)
        with sqlite3.connect(self.auth_db_path) as db:
            db.execute("""
                CREATE TABLE price_watch_assets (
                    symbol TEXT PRIMARY KEY, current_price REAL, week_high REAL,
                    status TEXT, last_checked_at INTEGER
                )
            """)
            db.execute(
                "INSERT INTO price_watch_assets VALUES (?, ?, ?, ?, ?)",
                ("DROP", 9.9, 10.0, "normal", int(now * 1000)),
            )
        alert = server.normalize_desktop_alert({
            "key": "price-watch:DROP:episode:1",
            "kind": "价格监控",
            "priority": "突破前高",
            "confirmSymbol": "DROP",
            "time": event_ms,
        })
        self.assertIn(
            "已失效",
            server.desktop_alert_price_watch_suppression_reason(alert, pending=False, now=now),
        )

    def test_visible_price_popup_is_not_killed_by_later_runtime_suppression(self):
        now = time.time()
        alert = server.normalize_desktop_alert({
            "key": "price-watch:KEEP:episode:1",
            "kind": "价格监控",
            "priority": "接近前高",
            "confirmSymbol": "KEEP",
            "time": int(now * 1000),
        })
        admitted = server.ALERT_DELIVERY_STORE.admit(
            alert, server.alert_dedupe_keys(alert), 1000, now=now
        )
        lease = server.ALERT_DELIVERY_STORE.lease(admitted["deliveryId"], now=now)
        process = FakeProcess()
        deliveries = {
            admitted["deliveryId"]: {"process": process, "slot": 0, "token": lease["token"]}
        }
        with (
            patch.object(server, "DESKTOP_ALERT_DELIVERIES", deliveries),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {0: (process, now)}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
            patch.object(server, "desktop_alert_runtime_suppression_reason", return_value="价格已回落"),
            patch.object(server, "price_structure_symbol_excluded", return_value=True),
            patch.object(server, "desktop_alert_source_is_muted", return_value=True),
        ):
            server.desktop_alert_delivery_tick()

        self.assertFalse(process.terminated)

    def test_mapped_hovered_popup_is_not_retried_after_startup_lease(self):
        now = time.time()
        alert = server.normalize_desktop_alert({
            "key": "price-watch:HOVER:structure-first:1h",
            "kind": "价格监控",
            "priority": "一级·首次结构",
        })
        admitted = server.ALERT_DELIVERY_STORE.admit(
            alert, server.alert_dedupe_keys(alert), 1000, now=now
        )
        lease = server.ALERT_DELIVERY_STORE.lease(admitted["deliveryId"], now=now)
        heartbeat_at = now + server.ALERT_DELIVERY_STORE.DISPLAY_LEASE_SECONDS
        server.ALERT_DELIVERY_STORE.receipt(
            admitted["deliveryId"], lease["token"], "visible", 0, now=heartbeat_at
        )
        process = FakeProcess()
        deliveries = {
            admitted["deliveryId"]: {"process": process, "slot": 0, "token": lease["token"]}
        }
        with (
            patch.object(server.time, "time", return_value=heartbeat_at + 1),
            patch.object(server, "DESKTOP_ALERT_DELIVERIES", deliveries),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {0: (process, now)}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
        ):
            server.desktop_alert_delivery_tick()

        self.assertFalse(process.terminated)
        row = server.ALERT_DELIVERY_STORE.get(admitted["deliveryId"])
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["state"], "displayed")
        self.assertEqual(row["visible_ms"], 0)

    def test_price_popup_is_never_reclaimed_for_a_new_slot(self):
        now = time.time()
        alert = server.normalize_desktop_alert({
            "key": "price-watch:KEEP:episode:2",
            "kind": "价格监控",
            "priority": "接近前高",
        })
        admitted = server.ALERT_DELIVERY_STORE.admit(
            alert, server.alert_dedupe_keys(alert), 1000, now=now
        )
        lease = server.ALERT_DELIVERY_STORE.lease(admitted["deliveryId"], now=now)
        server.ALERT_DELIVERY_STORE.receipt(
            admitted["deliveryId"], lease["token"], "visible", 120_000, now=now
        )
        process = FakeProcess()
        deliveries = {
            admitted["deliveryId"]: {"process": process, "slot": 0, "token": lease["token"]}
        }
        with (
            patch.object(server, "DESKTOP_ALERT_MAX_CONCURRENT_SLOTS", 1),
            patch.object(server, "DESKTOP_ALERT_DELIVERIES", deliveries),
            patch.object(server, "DESKTOP_ALERT_STRUCTURE_PROCESSES", {0: (process, now)}),
            patch.object(server, "DESKTOP_ALERT_GENERAL_PROCESSES", {}),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS", None),
        ):
            slot = server.next_desktop_alert_slot(reserve_normal=False)

        self.assertIsNone(slot)
        self.assertFalse(process.terminated)

    def test_requested_structure_lifetime_is_honored_and_bounded(self):
        self.assertEqual(desktop_alert.AUTO_CLOSE_MS, 2 * 60 * 1000)
        self.assertEqual(desktop_alert.popup_auto_close_ms({}), desktop_alert.AUTO_CLOSE_MS)
        self.assertEqual(desktop_alert.popup_auto_close_ms({"autoCloseMs": 30 * 60 * 1000}), 30 * 60 * 1000)
        self.assertEqual(desktop_alert.popup_auto_close_ms({"autoCloseMs": 1}), desktop_alert.MIN_AUTO_CLOSE_MS)
        self.assertEqual(
            desktop_alert.popup_auto_close_ms({"autoCloseMs": 99 * 60 * 60 * 1000}),
            desktop_alert.MAX_AUTO_CLOSE_MS,
        )


if __name__ == "__main__":
    unittest.main()
