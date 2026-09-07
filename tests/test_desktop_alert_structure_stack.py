import unittest
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
            server.desktop_alert_queue_worker()
            self.assertFalse(first_process.terminated)
            self.assertFalse(second_process.terminated)
            self.assertEqual([call.args[1] for call in spawn.call_args_list], [0, 1])
            self.assertEqual(spawn.call_args_list[0].args[0]["autoCloseMs"], 3 * 60 * 1000)

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
            server.desktop_alert_queue_worker()
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
            server.desktop_alert_queue_worker()

        self.assertFalse(first_process.terminated)
        self.assertFalse(second_process.terminated)
        self.assertEqual([call.args[1] for call in spawn.call_args_list], [0, 1])

    def test_popup_geometry_stacks_before_starting_a_new_column(self):
        bounds = (0, 0, 1920, 1080)
        positions = [desktop_alert.popup_position(bounds, 374, 212, slot) for slot in range(5)]
        self.assertEqual(len(set(positions)), 5)
        self.assertEqual([position[0] for position in positions[:4]], [18, 18, 18, 18])
        self.assertGreater(positions[4][0], positions[0][0])

    def test_requested_structure_lifetime_is_honored_and_bounded(self):
        self.assertEqual(desktop_alert.AUTO_CLOSE_MS, 3 * 60 * 1000)
        self.assertEqual(desktop_alert.popup_auto_close_ms({}), desktop_alert.AUTO_CLOSE_MS)
        self.assertEqual(desktop_alert.popup_auto_close_ms({"autoCloseMs": 30 * 60 * 1000}), 30 * 60 * 1000)
        self.assertEqual(desktop_alert.popup_auto_close_ms({"autoCloseMs": 1}), desktop_alert.MIN_AUTO_CLOSE_MS)
        self.assertEqual(
            desktop_alert.popup_auto_close_ms({"autoCloseMs": 99 * 60 * 60 * 1000}),
            desktop_alert.MAX_AUTO_CLOSE_MS,
        )


if __name__ == "__main__":
    unittest.main()
