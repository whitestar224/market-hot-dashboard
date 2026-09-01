import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class StrategyAdaptiveContextTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.tempdir.name) / "auth.db"
        server.init_auth_db()
        self.path_patch = patch.object(
            server,
            "STRATEGY_ADAPTIVE_CONTEXT_PATH",
            Path(self.tempdir.name) / "adaptive-context.json",
        )
        self.path_patch.start()
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()

    def tearDown(self):
        self.path_patch.stop()
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.tempdir.cleanup()

    def test_explicit_live_phrases_map_to_distinct_adaptive_stages(self):
        self.assertEqual(server.strategy_adaptive_stage_from_text("重点看 ETHFI"), "watch")
        self.assertEqual(server.strategy_adaptive_stage_from_text("PI 已经进入主升浪了"), "active")
        self.assertEqual(server.strategy_adaptive_stage_from_text("PI 有主升浪预期"), "expected")
        self.assertEqual(server.strategy_adaptive_stage_from_text("PI 正在进入加速段"), "acceleration")
        self.assertEqual(server.strategy_adaptive_stage_from_text("PI 现在走势拉得很急"), "rapid")
        self.assertEqual(server.strategy_adaptive_stage_from_text("PI 不是主升浪"), "cancel")

    def test_only_two_explicit_tactical_signal_types_are_exposed(self):
        self.assertEqual(server.strategy_tactical_signal_type_from_text("重点关注 $ETHFI"), "watch")
        self.assertEqual(server.strategy_tactical_signal_type_from_text("$PI 有主升浪预期"), "main-wave-expected")
        self.assertEqual(server.strategy_tactical_signal_type_from_text("$PI 已进入主升浪"), "")

    def test_hypothetical_language_never_opens_an_adaptive_permission(self):
        self.assertEqual(server.strategy_adaptive_stage_from_text("如果 PI 进入主升浪，再适当放宽"), "")
        self.assertEqual(server.strategy_adaptive_stage_from_text("等待 PI 进入主升浪"), "")

    def test_context_is_symbol_scoped_persistent_and_time_limited(self):
        now = 1_800_000_000_000
        changed = server.update_strategy_adaptive_context_from_text(
            "$pi 已进入主升浪，现在按主升节奏理解",
            source_kind="wechat",
            source_name="临盘群",
            observed_at=now,
            now_ms=now,
        )
        self.assertEqual([row["symbol"] for row in changed], ["PI"])
        context = server.strategy_adaptive_context_for_symbol("PIUSDT", now_ms=now + 60_000)
        self.assertEqual(context["mode"], "active")
        self.assertEqual(context["mainWaveStage"], "active")
        self.assertEqual(context["sourceKind"], "wechat")
        self.assertIsNone(server.strategy_adaptive_context_for_symbol("TUT", now_ms=now + 60_000))
        self.assertIsNone(server.strategy_adaptive_context_for_symbol("PI", now_ms=now + 9 * 60 * 60_000))

    def test_acceleration_falls_back_to_active_main_wave_after_short_mode_expires(self):
        now = 1_800_000_000_000
        server.update_strategy_adaptive_context_from_text(
            "PI 进入主升加速段",
            source_kind="personal-x",
            source_name="@whitestar224",
            observed_at=now,
            now_ms=now,
        )
        early = server.strategy_adaptive_context_for_symbol("PI", now_ms=now + 30 * 60_000)
        later = server.strategy_adaptive_context_for_symbol("PI", now_ms=now + 3 * 60 * 60_000)
        self.assertEqual(early["mode"], "acceleration")
        self.assertEqual(later["mode"], "active")
        self.assertEqual(later["mainWaveStage"], "active")

    def test_only_the_configured_personal_x_handle_can_change_strategy_context(self):
        now = 1_800_000_000_000
        payload = {
            "items": [
                {"handle": "other_kol", "fullText": "$PI 已进入主升浪", "publishedAt": now},
                {"handle": "whitestar224", "fullText": "$TUT 已进入主升浪", "publishedAt": now},
            ]
        }
        with patch.object(server, "x_kol_priority_handles", return_value=("whitestar224",)), patch.object(
            server, "queue_personal_x_monitor_priority_refresh"
        ):
            changed = server.update_strategy_contexts_from_personal_x_payload(payload, now_ms=now)
        self.assertEqual([row["symbol"] for row in changed], ["TUT"])
        self.assertIsNone(server.strategy_adaptive_context_for_symbol("PI", now_ms=now))
        self.assertEqual(server.strategy_adaptive_context_for_symbol("TUT", now_ms=now)["sourceKind"], "personal-x")

    def test_generic_personal_x_mention_prioritizes_without_unlocking_one_minute_alerts(self):
        now = 1_800_000_000_000
        payload = {
            "items": [{
                "handle": "whitestar224",
                "fullText": "$chip好像有新币止跌的势头",
                "publishedAt": now,
            }]
        }
        with patch.object(server, "x_kol_priority_handles", return_value=("whitestar224",)), patch.object(
            server, "queue_personal_x_monitor_priority_refresh"
        ):
            changed = server.update_strategy_contexts_from_personal_x_payload(payload, now_ms=now)

        self.assertEqual([row["symbol"] for row in changed], ["CHIP"])
        context = server.strategy_adaptive_context_for_symbol("CHIP", now_ms=now)
        self.assertEqual(context["mode"], "mentioned")
        self.assertEqual(context["sourceKind"], "personal-x")
        self.assertNotIn("signalType", context)
        self.assertFalse(server.price_structure_alert_interval_allowed({"adaptiveContext": context}, "1m"))

    def test_explicit_cancellation_removes_the_live_permission(self):
        now = 1_800_000_000_000
        server.update_strategy_adaptive_context_from_text(
            "PI 已进入主升浪",
            source_kind="wechat",
            source_name="临盘群",
            observed_at=now,
            now_ms=now,
        )
        server.update_strategy_adaptive_context_from_text(
            "PI 主升浪结束，取消主升预期",
            source_kind="wechat",
            source_name="临盘群",
            observed_at=now + 60_000,
            now_ms=now + 60_000,
        )
        self.assertIsNone(server.strategy_adaptive_context_for_symbol("PI", now_ms=now + 60_000))

    def test_one_minute_alerts_require_explicit_personal_x_focus(self):
        future = int(time.time() * 1000) + 60_000
        personal_watch = {
            "adaptiveContext": {
                "sourceKind": "personal-x", "signalType": "watch", "signalExpiresAt": future,
            }
        }
        wechat_expected = {
            "adaptiveContext": {
                "sourceKind": "wechat", "signalType": "main-wave-expected", "signalExpiresAt": future,
            }
        }
        other_kol = {
            "adaptiveContext": {
                "sourceKind": "kol-x", "signalType": "watch", "signalExpiresAt": future,
            }
        }
        personal_active_only = {
            "adaptiveContext": {
                "sourceKind": "personal-x", "mode": "active", "expiresAt": future,
            }
        }
        self.assertTrue(server.price_structure_alert_interval_allowed(personal_watch, "1m"))
        self.assertFalse(server.price_structure_alert_interval_allowed(wechat_expected, "1m"))
        self.assertFalse(server.price_structure_alert_interval_allowed(other_kol, "1m"))
        self.assertFalse(server.price_structure_alert_interval_allowed(personal_active_only, "1m"))
        self.assertTrue(server.price_structure_alert_interval_allowed({}, "5m"))


if __name__ == "__main__":
    unittest.main()
