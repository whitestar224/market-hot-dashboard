"""战壕悬浮窗（trench_overlay.py）的纯逻辑回归测试。

GUI 本身由 --preview 截图验收；这里覆盖能在无头环境下稳定断言的部分：
热键解析、显示宽度折行、中文闸门、卡片挑选与排序、拖动与位置记忆、以及数据源缓存行为。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import trench_overlay as overlay


def make_row(index, *, symbol=None, network="solana", market_cap=10_000, change=1.0):
    return {
        "network": network,
        "contractAddress": f"CA{index}",
        "symbol": symbol or f"T{index}",
        "name": f"Token {index}",
        "metrics": {"marketCapUsd": market_cap, "priceChangeH1": change},
    }


class InlineThread:
    """把后台刷新线程变成同步执行，让 feed 测试可确定复现。"""

    def __init__(self, target=None, name=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class HotkeyTests(unittest.TestCase):
    def test_parses_modifier_plus_key_case_insensitively(self):
        self.assertEqual(overlay.parse_hotkey("alt+q")[2], "Alt+Q")
        self.assertEqual(overlay.parse_hotkey("ALT + Q")[2], "Alt+Q")
        self.assertEqual(overlay.parse_hotkey("ctrl+shift+k")[2], "Ctrl+Shift+K")

    def test_rejects_configurations_that_would_misfire(self):
        for bad in ("q", "", "alt+zz", "meta+q", "alt+"):
            with self.subTest(bad=bad):
                with self.assertRaises(overlay.HotkeyError):
                    overlay.parse_hotkey(bad)

    def test_alt_q_maps_to_the_documented_virtual_keys(self):
        modifiers, main_vk, _ = overlay.parse_hotkey("alt+q")
        self.assertEqual(modifiers, ((0x12,),))
        self.assertEqual(main_vk, 0x51)


class FormattingTests(unittest.TestCase):
    def test_compact_usd_marks_unusable_values(self):
        self.assertEqual(overlay.compact_usd(0), "—")
        self.assertEqual(overlay.compact_usd(None), "—")
        self.assertEqual(overlay.compact_usd("abc"), "—")
        self.assertEqual(overlay.compact_usd(950), "$950")
        self.assertEqual(overlay.compact_usd(47_545.3), "$47.5K")
        self.assertEqual(overlay.compact_usd(3_100_000), "$3.1M")

    def test_percent_uses_the_domestic_up_is_red_convention(self):
        self.assertEqual(overlay.format_percent(12.4), ("+12.4%", "up"))
        self.assertEqual(overlay.format_percent(-4.8), ("-4.8%", "down"))
        self.assertEqual(overlay.format_percent(0), ("0.0%", ""))
        self.assertEqual(overlay.format_percent(None), ("", ""))

    def test_clean_narrative_keeps_chinese_and_drops_english(self):
        self.assertEqual(
            overlay.clean_narrative("ZERO is a privacy token on Robinhood Chain launched via Pons V2."),
            "",
        )
        kept = overlay.clean_narrative("BLUEPRINT 代币源于 blueprintools 的愿景，旨在革新创意表达。")
        self.assertTrue(kept.startswith("BLUEPRINT 代币源于"))

    def test_truncate_text_collapses_whitespace_and_adds_ellipsis(self):
        self.assertEqual(overlay.truncate_text("  a \n b  "), "a b")
        long_text = overlay.truncate_text("甲" * 300)
        self.assertTrue(long_text.endswith("…"))
        self.assertEqual(len(long_text), overlay.NARRATIVE_CHARS)


class WrapTests(unittest.TestCase):
    def test_wrap_fills_every_line_without_orphaning_a_short_latin_word(self):
        text = (
            "$RETAIL 是社区驱动型代币，灵感来自零售投资热潮与迷因文化的结合，"
            "通过社交媒体营销、TikTok 推广与社区投票迅速传播。"
        )
        lines = overlay.wrap_display_text(text).split("\n")
        self.assertGreater(len(lines), 1)
        # 首个英文词不能单独占一行：Tk 自带 wraplength 就会犯这个毛病。
        self.assertNotEqual(lines[0].strip(), "$RETAIL")
        for line in lines[:-1]:
            self.assertGreaterEqual(sum(overlay._char_cost(ch) for ch in line), 20)

    def test_wrap_caps_lines_and_marks_truncation(self):
        lines = overlay.wrap_display_text("甲" * 300, max_lines=3).split("\n")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("…"))

    def test_wrap_returns_empty_for_blank_input(self):
        self.assertEqual(overlay.wrap_display_text("   "), "")
        self.assertEqual(overlay.wrap_display_text(None), "")


class CardTests(unittest.TestCase):
    def test_row_key_pairs_network_with_contract(self):
        self.assertEqual(overlay.trench_row_key({"network": "solana", "contractAddress": "A"}), "solana:A")
        self.assertEqual(overlay.trench_row_key({"contract": "B"}), ":B")
        self.assertEqual(overlay.trench_row_key({"network": "solana"}), "")

    def test_cards_are_always_the_newest_rows_even_when_older_ones_have_narratives(self):
        rows = [make_row(i) for i in range(5)]
        narratives = {"solana:CA3": "第三条的中文叙事内容足够长可以显示。",
                      "solana:CA4": "第四条的中文叙事内容足够长可以显示。"}
        cards = overlay.build_cards(rows, narratives)
        # 叙事绝不能从更新的币手里抢走名额：第 ① 条必须就是榜单第 1 条。
        self.assertEqual([card["symbol"] for card in cards], ["T0", "T1", "T2"])
        self.assertEqual([card["rank"] for card in cards], ["①", "②", "③"])
        self.assertEqual([card["narrative"] for card in cards], ["", "", ""])

    def test_display_rows_dedupes_and_skips_contract_less_rows(self):
        rows = [
            make_row(0), make_row(0),
            {"network": "solana", "symbol": "NOCA", "metrics": {}},
            make_row(1), make_row(2),
        ]
        self.assertEqual([row["symbol"] for _, row in overlay.display_rows(rows)], ["T0", "T1", "T2"])
        self.assertEqual(len(overlay.display_rows(rows, limit=2)), 2)

    def test_cards_report_the_age_when_available(self):
        row = make_row(0)
        row["ageMinutes"] = 7
        self.assertEqual(overlay.build_cards([row], {})[0]["ageText"], "7分钟前")


class AgeTests(unittest.TestCase):
    def test_formats_common_ranges(self):
        self.assertEqual(overlay.format_age(0.4), "刚刚")
        self.assertEqual(overlay.format_age(3), "3分钟前")
        self.assertEqual(overlay.format_age(59.9), "59分钟前")
        self.assertEqual(overlay.format_age(90), "1小时前")
        self.assertEqual(overlay.format_age(60 * 30), "1天前")

    def test_returns_empty_for_unusable_values(self):
        for value in (None, "", "abc", -5):
            self.assertEqual(overlay.format_age(value), "", value)

    def test_cards_cap_at_three_and_dedupe_contracts(self):
        rows = [make_row(0), make_row(1), make_row(1), make_row(2), make_row(3)]
        cards = overlay.build_cards(rows, {})
        self.assertEqual(len(cards), overlay.MAX_CARDS)
        self.assertEqual([card["symbol"] for card in cards], ["T0", "T1", "T2"])

    def test_cards_expose_metrics_and_tone(self):
        rows = [make_row(0, market_cap=162_093, change=-4.8)]
        card = overlay.build_cards(rows, {})[0]
        self.assertEqual(card["marketCap"], "$162.1K")
        self.assertEqual(card["changeText"], "-4.8%")
        self.assertEqual(card["changeTone"], "down")
        self.assertEqual(card["narrative"], "")

    def test_rows_without_a_contract_are_skipped(self):
        rows = [{"network": "solana", "symbol": "NOCA", "metrics": {}}, make_row(1)]
        cards = overlay.build_cards(rows, {})
        self.assertEqual([card["symbol"] for card in cards], ["T1"])


class FetchTests(unittest.TestCase):
    def test_row_fetch_targets_the_trench_route_with_a_valid_page_size(self):
        captured = []

        def fake_read(request, timeout):
            captured.append(request)
            return {"items": []}

        with patch.object(overlay, "_read_json", fake_read):
            overlay.fetch_trench_rows(8765, page_size=4)

        url = captured[0].full_url
        self.assertIn("/api/onchain-trenches", url)
        self.assertIn("page=1", url)
        # 服务端 pageSize 下限是 12，小于它会被夹住。
        self.assertIn("pageSize=12", url)

    def test_narrative_fetch_sends_the_trenches_identity_and_reads_keys_back(self):
        captured = []

        def fake_read(request, timeout):
            captured.append(request)
            return {"items": [
                {"key": "solana:CA0", "exchangeAiNarrative": "第一条中文叙事内容足够长了。"},
                {"key": "solana:CA1", "exchangeAiNarrative": "ZERO is a privacy token on Robinhood Chain."},
                {"key": "solana:CA2"},
            ]}

        rows = [make_row(0), make_row(1), make_row(2)]
        with patch.object(overlay, "_read_json", fake_read):
            result = overlay.fetch_narratives(8765, rows)

        body = json.loads(captured[0].data.decode("utf-8"))
        self.assertEqual(body["items"][0]["sourceId"], "gmgn-trenches")
        self.assertEqual(body["items"][0]["key"], "solana:CA0")
        self.assertEqual(body["items"][0]["contractAddress"], "CA0")
        self.assertEqual(body["items"][0]["chain"], "solana")
        # 英文叙事被本地闸门挡掉，空值不产生条目。
        self.assertEqual(result, {"solana:CA0": "第一条中文叙事内容足够长了。"})

    def test_narrative_fetch_skips_rows_without_a_contract(self):
        captured = []

        def fake_read(request, timeout):
            captured.append(request)
            return {"items": []}

        with patch.object(overlay, "_read_json", fake_read):
            result = overlay.fetch_narratives(8765, [{"network": "solana", "symbol": "NOCA"}])

        self.assertEqual(result, {})
        self.assertEqual(captured, [])


class FeedTests(unittest.TestCase):
    def test_refresh_merges_narratives_into_cards(self):
        feed = overlay.TrenchFeed(8765)
        rows = [make_row(i) for i in range(3)]
        with patch.object(overlay, "fetch_trench_rows", return_value={"items": rows}), \
                patch.object(overlay, "fetch_narratives",
                             return_value={"solana:CA1": "第二条的中文叙事内容足够长了。"}), \
                patch.object(overlay.threading, "Thread", InlineThread):
            self.assertTrue(feed.refresh())

        cards, error, fetched_at = feed.snapshot()
        self.assertEqual(error, "")
        self.assertGreater(fetched_at, 0)
        self.assertEqual([card["symbol"] for card in cards], ["T0", "T1", "T2"])
        self.assertEqual(cards[1]["narrative"], "第二条的中文叙事内容足够长了。")
        self.assertTrue(feed.is_fresh())

    def test_refresh_reports_rate_limit_instead_of_faking_data(self):
        feed = overlay.TrenchFeed(8765)
        payload = {"items": [], "rateLimited": True, "retryAfterSeconds": 90,
                   "sourceStatus": {"solana/gmgn-trenches": "rate_limited"}}
        with patch.object(overlay, "fetch_trench_rows", return_value=payload), \
                patch.object(overlay.threading, "Thread", InlineThread):
            feed.refresh()

        cards, error, fetched_at = feed.snapshot()
        self.assertEqual(cards, [])
        self.assertIn("限流", error)
        self.assertEqual(fetched_at, 0)

    def test_refresh_reports_failed_chains_without_replacing_cached_cards(self):
        feed = overlay.TrenchFeed(8765)
        with patch.object(overlay, "fetch_trench_rows", return_value={"items": [make_row(0)]}), \
                patch.object(overlay, "fetch_narratives", return_value={}), \
                patch.object(overlay.threading, "Thread", InlineThread):
            feed.refresh()
        good_cards, _, good_at = feed.snapshot()
        self.assertEqual(len(good_cards), 1)

        failed = {"items": [], "sourceStatus": {"solana/gmgn-trenches": "error", "bsc/gmgn-trenches": "error"}}
        with patch.object(overlay, "fetch_trench_rows", return_value=failed), \
                patch.object(overlay.threading, "Thread", InlineThread):
            feed.refresh()

        cards, error, fetched_at = feed.snapshot()
        self.assertEqual(len(cards), 1)          # 旧卡片留着，不闪成空面板
        self.assertEqual(fetched_at, good_at)    # 时间戳不刷新
        self.assertIn("链异常", error)

    def test_refresh_survives_a_fetch_exception(self):
        feed = overlay.TrenchFeed(8765)
        with patch.object(overlay, "fetch_trench_rows", side_effect=RuntimeError("boom")), \
                patch.object(overlay.threading, "Thread", InlineThread):
            feed.refresh()
        cards, error, _ = feed.snapshot()
        self.assertEqual(cards, [])
        self.assertIn("boom", error)
        self.assertFalse(feed.refreshing())

    def test_narratives_are_reused_across_refreshes(self):
        feed = overlay.TrenchFeed(8765)
        rows = [make_row(0)]
        with patch.object(overlay, "fetch_trench_rows", return_value={"items": rows}), \
                patch.object(overlay, "fetch_narratives",
                             return_value={"solana:CA0": "第一条中文叙事内容足够长了。"}) as fetch_ai, \
                patch.object(overlay.threading, "Thread", InlineThread):
            feed.refresh()
            # 第二次服务端不再返回叙事，但进程内缓存应让它继续显示。
            fetch_ai.return_value = {}
            feed.refresh()

        cards, _, _ = feed.snapshot()
        self.assertEqual(cards[0]["narrative"], "第一条中文叙事内容足够长了。")

    def test_concurrent_refresh_is_collapsed(self):
        feed = overlay.TrenchFeed(8765)
        with patch.object(overlay.threading, "Thread", InlineThread):
            with patch.object(overlay, "fetch_trench_rows", return_value={"items": [make_row(0)]}), \
                    patch.object(overlay, "fetch_narratives", return_value={}):
                # Thread 被替换成同步执行后，第一次就会把 _refreshing 复位，
                # 所以这里直接验证 lock 保护下的状态机。
                self.assertTrue(feed.refresh())
                with feed._lock:
                    feed._refreshing = True
                self.assertFalse(feed.refresh())
                with feed._lock:
                    feed._refreshing = False


class SingleInstanceTests(unittest.TestCase):
    def test_non_windows_never_blocks_on_the_mutex(self):
        with patch.object(overlay.os, "name", "posix"):
            self.assertTrue(overlay.acquire_single_instance())

    def test_pid_file_round_trip(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "pid"
            with patch.object(overlay, "PID_FILE", target):
                overlay.write_pid_file()
                self.assertEqual(target.read_text(encoding="utf-8"), str(os.getpid()))
                overlay.clear_pid_file()
                self.assertFalse(target.exists())


class DiagnosticsTests(unittest.TestCase):
    """悬浮窗跑在 pythonw 下没有控制台：失败必须落盘，否则现象与「没启动」完全一样。"""

    def test_log_line_appends_every_entry_to_the_log_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "overlay.log"
            with patch.object(overlay, "LOG_FILE", target):
                overlay.log_line("第一条")
                overlay.log_line("第二条")
                text = target.read_text(encoding="utf-8")
        self.assertIn("第一条", text)
        self.assertIn("第二条", text)
        self.assertEqual(len(text.strip().splitlines()), 2)

    def test_log_line_never_raises_on_an_unwritable_path(self):
        from pathlib import Path
        with patch.object(overlay, "LOG_FILE", Path("Z:/definitely/missing/overlay.log")):
            overlay.log_line("写不进去也不能抛")

    def test_window_open_failure_is_logged_instead_of_swallowed(self):
        instance = overlay.TrenchOverlay(overlay.TrenchFeed(1), hotkey="alt+q")
        logged = []
        with patch.object(overlay, "log_line", side_effect=logged.append), \
                patch.object(overlay.TrenchOverlay, "show", side_effect=RuntimeError("boom")):
            instance._handle_hotkey(True)
        self.assertTrue(any("切换面板失败" in str(item) for item in logged), logged)
        # 失败后仍记作「已按下」，否则会每 40ms 重试一次刷屏。
        self.assertTrue(instance._held)

    def test_releasing_the_hotkey_clears_the_held_flag(self):
        instance = overlay.TrenchOverlay(overlay.TrenchFeed(1), hotkey="alt+q")
        instance._held = True
        instance._handle_hotkey(False)
        self.assertFalse(instance._held)

    def test_selfcheck_reports_a_bad_hotkey(self):
        import contextlib
        import io
        with patch.object(overlay, "_read_json", side_effect=OSError("no server")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = overlay.selfcheck(8765, "q")
        self.assertEqual(code, 1)
        self.assertIn("hotkey", out.getvalue())

    def test_selfcheck_passes_when_environment_and_port_are_healthy(self):
        import contextlib
        import io
        payload = {"items": [{"contractAddress": "CA1"}], "sourceStatus": {"solana/gmgn-trenches": "ok"}}
        with patch.object(overlay, "_read_json", return_value=payload), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = overlay.selfcheck(8765, "alt+q")
        self.assertEqual(code, 0)
        self.assertIn("可以启动", out.getvalue())

    def test_selfcheck_flags_an_unreachable_monitor_service(self):
        import contextlib
        import io
        with patch.object(overlay, "_read_json", side_effect=OSError("connection refused")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = overlay.selfcheck(8765, "alt+q")
        self.assertEqual(code, 1)
        self.assertIn("monitor api", out.getvalue())


    def test_log_tail_returns_only_the_last_lines(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "overlay.log"
            target.write_text("\n".join(f"line{i}" for i in range(30)), encoding="utf-8")
            with patch.object(overlay, "LOG_FILE", target):
                tail = overlay.read_log_tail(5)
        self.assertEqual(tail.splitlines(), ["line25", "line26", "line27", "line28", "line29"])

    def test_log_tail_is_empty_when_no_log_exists(self):
        from pathlib import Path
        with patch.object(overlay, "LOG_FILE", Path("Z:/definitely/missing/overlay.log")):
            self.assertEqual(overlay.read_log_tail(5), "")


class SpawnTests(unittest.TestCase):
    """悬浮窗必须由启动 server.py 的一方拉起，且失败只能回报、不能抛。"""

    def test_skips_on_non_windows(self):
        with patch.object(overlay.os, "name", "posix"):
            result = overlay.spawn_detached()
        self.assertFalse(result["started"])
        self.assertIn("Windows", result["reason"])

    def test_env_switch_recognises_falsy_spellings(self):
        for value in ("0", "false", "OFF", " no "):
            with patch.dict(overlay.os.environ, {"XYS_TRENCH_OVERLAY": value}):
                self.assertTrue(overlay.overlay_disabled(), value)

    def test_env_switch_leaves_truthy_values_enabled(self):
        for value in ("1", "true", "yes", ""):
            with patch.dict(overlay.os.environ, {"XYS_TRENCH_OVERLAY": value}):
                self.assertFalse(overlay.overlay_disabled(), value)

    def test_skips_when_disabled_by_env(self):
        with patch.dict(overlay.os.environ, {"XYS_TRENCH_OVERLAY": "0"}):
            result = overlay.spawn_detached()
        self.assertFalse(result["started"])
        self.assertIn("disabled", result["reason"])

    def test_reports_a_missing_script_instead_of_raising(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            result = overlay.spawn_detached(root=Path(tmp))
        self.assertFalse(result["started"])
        self.assertIn("script not found", result["reason"])

    def test_spawns_pythonw_detached_and_tags_the_source(self):
        import tempfile
        from pathlib import Path
        fake = MagicMock()
        fake.pid = 4242
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "trench_overlay.py").write_text("", encoding="utf-8")
            with patch.object(overlay.subprocess, "Popen", return_value=fake) as popen, \
                    patch.object(overlay, "log_line") as logged:
                result = overlay.spawn_detached(root=Path(tmp), source="server")
        self.assertTrue(result["started"])
        self.assertEqual(result["pid"], 4242)
        argv = popen.call_args.args[0]
        self.assertIn("pythonw", argv[0].lower())
        self.assertTrue(argv[1].endswith("trench_overlay.py"))
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["env"]["XYS_TRENCH_OVERLAY_SOURCE"], "server")
        self.assertTrue(kwargs["creationflags"] & 0x00000008, "需要 DETACHED_PROCESS，否则随 server.py 一起被回收")
        self.assertTrue(logged.called)

    def test_popen_failure_is_reported_not_raised(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "trench_overlay.py").write_text("", encoding="utf-8")
            with patch.object(overlay.subprocess, "Popen", side_effect=OSError("denied")), \
                    patch.object(overlay, "log_line"):
                result = overlay.spawn_detached(root=Path(tmp))
        self.assertFalse(result["started"])
        self.assertIn("OSError", result["reason"])


class ToggleTests(unittest.TestCase):
    """默认常显 + Alt+Q 切换：按一次隐藏，再按一次显示（不是按住显示）。"""

    def setUp(self):
        self.instance = overlay.TrenchOverlay(overlay.TrenchFeed(1), hotkey="alt+q")
        self.instance.root = MagicMock()
        self.instance._visible = True          # 默认常显
        # 真实的 show/hide 会碰 Tk 细节，这里只验证「可见性状态机」。
        for name, state in (("show", True), ("hide", False)):
            patcher = patch.object(
                overlay.TrenchOverlay, name,
                side_effect=lambda state=state: setattr(self.instance, "_visible", state),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_press_toggles_hide_then_show(self):
        with patch.object(overlay, "log_line"):
            self.instance._handle_hotkey(True)
            self.assertFalse(self.instance._visible, "第一次按应该隐藏")
            self.instance._handle_hotkey(False)     # 松开只复位，不产生动作
            self.assertFalse(self.instance._visible)
            self.instance._handle_hotkey(True)
            self.assertTrue(self.instance._visible, "再按一次应该显示")

    def test_holding_the_key_down_does_not_flicker(self):
        with patch.object(overlay, "log_line"):
            for _ in range(25):                     # 按住不放 = 连续 25 次 down
                self.instance._handle_hotkey(True)
        self.assertFalse(self.instance._visible, "按住不放只能切换一次")
        self.assertTrue(self.instance._held)

    def test_release_alone_never_shows_a_hidden_panel(self):
        self.instance._visible = False
        with patch.object(overlay, "log_line"):
            self.instance._handle_hotkey(False)
        self.assertFalse(self.instance._visible)

    def test_first_toggle_is_logged_only_once(self):
        logged = []
        with patch.object(overlay, "log_line", side_effect=logged.append):
            self.instance._handle_hotkey(True)
            self.instance._handle_hotkey(False)
            self.instance._handle_hotkey(True)
        toggles = [item for item in logged if "首次" in str(item)]
        self.assertEqual(len(toggles), 1, logged)

    def test_auto_refresh_only_runs_while_visible(self):
        with patch.object(overlay.TrenchFeed, "refresh") as refresh, \
                patch.object(overlay.TrenchFeed, "is_fresh", return_value=False):
            self.instance._visible = False
            self.instance._auto_refresh_if_stale()
            refresh.assert_not_called()
            self.instance._visible = True
            self.instance._auto_refresh_if_stale()
            refresh.assert_called_once()

    def test_auto_refresh_is_rate_limited(self):
        with patch.object(overlay.TrenchFeed, "refresh") as refresh, \
                patch.object(overlay.TrenchFeed, "is_fresh", return_value=False):
            self.instance._auto_refresh_if_stale()
            self.instance._auto_refresh_if_stale()
            self.instance._auto_refresh_if_stale()
        self.assertEqual(refresh.call_count, 1, "5 秒内只应检查/触发一次")


class PositionTests(unittest.TestCase):
    """位置记忆与越界保护：面板是置顶无边框窗，位置丢了就再也抓不回来。"""

    def _temp_position_file(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name) / "xys-trench-overlay.pos"

    def test_saved_position_round_trips(self):
        target = self._temp_position_file()
        with patch.object(overlay, "POSITION_FILE", target):
            self.assertIsNone(overlay.read_saved_position(), "没拖过时不该有位置")
            overlay.write_saved_position(321, 654)
            self.assertEqual(overlay.read_saved_position(), (321, 654))
            self.assertTrue(overlay.clear_saved_position())
            self.assertIsNone(overlay.read_saved_position())
            self.assertFalse(overlay.clear_saved_position(), "删过了再删应返回 False")

    def test_corrupt_position_file_is_treated_as_absent(self):
        target = self._temp_position_file()
        target.write_text("不是坐标", encoding="utf-8")
        with patch.object(overlay, "POSITION_FILE", target):
            self.assertIsNone(overlay.read_saved_position())

    def test_clamp_keeps_the_panel_inside_the_virtual_desktop(self):
        with patch.object(overlay, "virtual_screen", return_value=(0, 0, 1920, 1080)):
            self.assertEqual(overlay.clamp_position(-50, -20, 396, 400), (0, 0))
            self.assertEqual(overlay.clamp_position(5000, 5000, 396, 400), (1524, 680))
            self.assertEqual(overlay.clamp_position(300, 200, 396, 400), (300, 200))

    def test_clamp_spans_every_monitor_not_just_the_primary_one(self):
        # 副屏挂在主屏左边时虚拟桌面 left 是负数，面板应该能拖过去而不是被夹回 0。
        with patch.object(overlay, "virtual_screen", return_value=(-1920, 0, 1920, 1080)):
            self.assertEqual(overlay.clamp_position(-1800, 100, 396, 400), (-1800, 100))

    def test_clamp_is_a_no_op_without_a_screen_size(self):
        with patch.object(overlay, "virtual_screen", return_value=None):
            self.assertEqual(overlay.clamp_position(700, 800, 396, 400), (700, 800))

    def test_overlay_starts_from_the_remembered_position(self):
        with patch.object(overlay, "read_saved_position", return_value=(640, 360)):
            instance = overlay.TrenchOverlay(overlay.TrenchFeed(1), hotkey="alt+q")
        self.assertEqual(instance._position, (640, 360))


def make_overlay(*, click_through=False):
    """造一个「已显示、位置固定、窗口桩」的面板，供拖动类测试复用。"""
    instance = overlay.TrenchOverlay(
        overlay.TrenchFeed(1), hotkey="alt+q", click_through=click_through
    )
    instance.root = MagicMock()
    instance._visible = True                      # 默认常显
    instance._position = (100, 100)
    instance._panel_size = (overlay.PANEL_WIDTH, 400)
    return instance


class DragTests(unittest.TestCase):
    """**直接左键**拖动面板（不按 Alt）。

    拖动完全靠 ``_poll_pointer()`` 全局轮询：Tk 的 <B1-Motion> 在穿透模式下根本收不到
    事件，普通窗口模式也没必要再维护一套绑定。这里把光标/按键/屏幕全部虚拟化，
    不依赖真实桌面。
    """

    def setUp(self):
        self.instance = make_overlay()
        self.saved: list[tuple[int, int]] = []
        self._patch("clamp_position", side_effect=lambda x, y, width, height: (x, y))
        self._patch("write_saved_position", side_effect=lambda x, y: self.saved.append((x, y)))
        self._patch("log_line")
        self._patch("_auto_refresh_if_stale")
        # 默认状态：左键按住、光标落在面板内（150,120 -> 距面板左上角偏移 50,20）。
        self._patch("modifiers_down", return_value=True)
        self._patch("left_button_down", return_value=True)
        self._patch("cursor_position", return_value=(150, 120))

    def _patch(self, name, **kwargs):
        # 模块级函数（clamp_position / write_saved_position …）挂在 module 上；
        # 面板自己的方法（_auto_refresh_if_stale / _handle_hotkey …）挂在类上。
        target = overlay.TrenchOverlay if hasattr(overlay.TrenchOverlay, name) else overlay
        patcher = patch.object(target, name, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drag_to(self, point):
        """按下并拖过阈值：第一次轮询记起手点，第二次轮询真移动。"""
        self.instance._poll_pointer()
        with patch.object(overlay, "cursor_position", return_value=point):
            self.instance._poll_pointer()

    def test_left_drag_moves_the_panel_and_remembers_the_position(self):
        self.instance._poll_pointer()                      # 起手：只记下起手点
        self.assertFalse(self.instance._dragging, "刚按下还没移动，不该算拖动")
        with patch.object(overlay, "cursor_position", return_value=(260, 300)):
            self.instance._poll_pointer()                  # 拖到 260,300 -> 面板落在 210,280
        self.assertTrue(self.instance._dragging)
        self.instance.root.geometry.assert_called_with(f"{overlay.PANEL_WIDTH}x400+210+280")
        with patch.object(overlay, "left_button_down", return_value=False):
            self.instance._poll_pointer()                  # 松手
        self.assertFalse(self.instance._dragging)
        self.assertEqual(self.saved, [(210, 280)], "松手应把位置落盘")

    def test_plain_left_drag_never_asks_for_a_modifier(self):
        # 这条就是这次改动的目的：不按 Alt，直接左键就能拖。
        with patch.object(overlay, "modifiers_down", return_value=False):
            self._drag_to((260, 300))
        self.assertTrue(self.instance._dragging, "普通窗口模式不能要求修饰键")
        self.instance.root.geometry.assert_called_with(f"{overlay.PANEL_WIDTH}x400+210+280")

    def test_a_click_below_the_threshold_never_moves_the_panel(self):
        # 光电鼠标单击随手会抖 1~3px；没有阈值就会变成「每点一下面板挪一点」。
        self.instance._poll_pointer()                      # 记下起手点 150,120
        with patch.object(overlay, "cursor_position", return_value=(152, 121)):
            self.instance._poll_pointer()                  # 只抖了 2px
        self.assertFalse(self.instance._dragging)
        self.instance.root.geometry.assert_not_called()
        with patch.object(overlay, "left_button_down", return_value=False):
            self.instance._poll_pointer()
        self.assertEqual(self.saved, [], "点一下不该写位置")

    def test_the_threshold_boundary_still_starts_a_drag(self):
        self.instance._poll_pointer()
        with patch.object(overlay, "cursor_position", return_value=(154, 120)):
            self.instance._poll_pointer()                  # 正好 4px
        self.assertTrue(self.instance._dragging, "达到阈值就该开始拖")

    def test_drag_must_start_inside_the_panel(self):
        with patch.object(overlay, "cursor_position", return_value=(900, 900)):
            self.instance._poll_pointer()
        self.assertFalse(self.instance._dragging, "面板外起手不该进入拖动")
        self.assertIsNone(self.instance._press_origin)

    def test_a_press_starting_outside_the_panel_never_starts_a_drag(self):
        # 用户在浏览器里按下左键、按住拖过面板时，绝不能让面板跟着跑。
        with patch.object(overlay, "cursor_position", return_value=(900, 900)):
            self.instance._poll_pointer()                  # 按下点在面板外
        with patch.object(overlay, "cursor_position", return_value=(150, 120)):
            self.instance._poll_pointer()                  # 光标进来了，但按下不在这里
        self.assertIsNone(self.instance._press_origin)
        with patch.object(overlay, "cursor_position", return_value=(400, 400)):
            self.instance._poll_pointer()                  # 继续拖也不该动面板
        self.assertFalse(self.instance._dragging)
        self.instance.root.geometry.assert_not_called()

    def test_release_ends_the_drag_and_writes_the_position_once(self):
        self._drag_to((260, 300))                          # 真拖一把 -> 面板 210,280
        with patch.object(overlay, "left_button_down", return_value=False):
            self.instance._poll_pointer()
            self.instance._poll_pointer()
        self.assertEqual(self.saved, [(210, 280)], "松手只应落盘一次")
        self.assertIsNone(self.instance._press_origin, "松手要清掉起手点")

    def test_hidden_panel_always_ends_an_in_flight_drag(self):
        self.instance._dragging = True
        self.instance._press_origin = (150, 120)
        self.instance._press_seen = True
        self.instance._visible = False
        self.instance._poll_pointer()
        self.assertFalse(self.instance._dragging, "隐藏后必须收尾，否则轮询一直卡在 16ms")
        self.assertIsNone(self.instance._press_origin)

    def test_hotkey_is_ignored_while_dragging(self):
        self._drag_to((260, 300))
        with patch.object(overlay, "hotkey_is_down", return_value=True), \
                patch.object(overlay.TrenchOverlay, "_handle_hotkey") as handle:
            self.instance._poll_hotkey()
        handle.assert_not_called()

    def test_poll_interval_tightens_while_dragging(self):
        self._drag_to((260, 300))
        with patch.object(overlay, "hotkey_is_down", return_value=False):
            self.instance._poll_hotkey()
        self.instance.root.after.assert_called_with(
            overlay.DRAG_POLL_MS, self.instance._poll_hotkey
        )

    def test_poll_interval_stays_relaxed_when_not_dragging(self):
        with patch.object(overlay, "left_button_down", return_value=False), \
                patch.object(overlay, "hotkey_is_down", return_value=False):
            self.instance._poll_hotkey()
        self.instance.root.after.assert_called_with(
            self.instance._poll_interval_ms, self.instance._poll_hotkey
        )

    def test_place_panel_keeps_the_dragged_position(self):
        # 刷新会改高度（卡片数变了），但绝不能把拖动过的位置冲回左上角。
        self.instance._position = (300, 220)
        self.instance.root.winfo_reqheight.return_value = 420
        self.instance._place_panel()
        self.instance.root.geometry.assert_called_with(f"{overlay.PANEL_WIDTH}x420+300+220")

    def test_place_panel_falls_back_to_the_top_left_margin(self):
        self.instance._position = None
        self.instance.root.winfo_reqheight.return_value = 300
        with patch.object(overlay, "work_area", return_value=(0, 0, 1920, 1040)):
            self.instance._place_panel()
        self.instance.root.geometry.assert_called_with(f"{overlay.PANEL_WIDTH}x300+16+14")
        self.assertEqual(self.instance._position, (16, 14))

    def test_place_panel_never_shrinks_below_eighty_pixels(self):
        self.instance._position = None
        self.instance.root.winfo_reqheight.return_value = 10
        with patch.object(overlay, "work_area", return_value=(0, 0, 1920, 1040)):
            self.instance._place_panel()
        self.instance.root.geometry.assert_called_with(f"{overlay.PANEL_WIDTH}x80+16+14")

    def test_reset_position_flag_clears_the_memory_and_exits(self):
        with patch.object(overlay, "clear_saved_position", return_value=True) as clear, \
                patch.object(sys, "argv", ["trench_overlay.py", "--reset-position"]), \
                patch("builtins.print"):
            self.assertEqual(overlay.main(), 0)
        clear.assert_called_once()


class ClickThroughTests(unittest.TestCase):
    """点击穿透是 opt-in（默认关）。

    默认必须是普通窗口，左键才拖得动；穿透只给「把面板当纯装饰」的场景用，
    那时左键要照常穿给下面的窗口，拖动退回 Alt+左键。
    """

    def setUp(self):
        self.saved: list[tuple[int, int]] = []
        self._patch("clamp_position", side_effect=lambda x, y, width, height: (x, y))
        self._patch("write_saved_position", side_effect=lambda x, y: self.saved.append((x, y)))
        self._patch("log_line")
        self._patch("left_button_down", return_value=True)

    def _patch(self, name, **kwargs):
        target = overlay.TrenchOverlay if hasattr(overlay.TrenchOverlay, name) else overlay
        patcher = patch.object(target, name, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drag(self, instance, to):
        with patch.object(overlay, "cursor_position", return_value=(150, 120)):
            instance._poll_pointer()
        with patch.object(overlay, "cursor_position", return_value=to):
            instance._poll_pointer()

    def _window_style(self, instance) -> int:
        instance.root.winfo_id.return_value = 0x1234
        api = MagicMock()
        api.GetParent.return_value = 0                 # 没有父窗口 -> 直接作用于自身
        api.GetWindowLongW.return_value = 0
        with patch.object(overlay, "_user32", return_value=api):
            instance._apply_no_activate()
        return api.SetWindowLongW.call_args.args[2]

    def test_default_mode_keeps_the_window_mouse_reachable(self):
        # WS_EX_TRANSPARENT(0x20) 一旦加上，窗口就收不到鼠标消息，左键再也拖不动它。
        style = self._window_style(make_overlay())
        self.assertFalse(style & 0x20, "默认模式不能加 WS_EX_TRANSPARENT")
        self.assertTrue(style & 0x08000000, "置顶不抢焦点仍要保留 WS_EX_NOACTIVATE")

    def test_click_through_mode_adds_the_transparent_style(self):
        style = self._window_style(make_overlay(click_through=True))
        self.assertTrue(style & 0x20, "穿透模式才加 WS_EX_TRANSPARENT")

    def test_plain_left_press_never_moves_a_click_through_panel(self):
        instance = make_overlay(click_through=True)
        with patch.object(overlay, "modifiers_down", return_value=False):
            self._drag(instance, (260, 300))
        self.assertFalse(instance._dragging, "穿透模式下左键必须穿给下面的窗口")
        instance.root.geometry.assert_not_called()
        self.assertEqual(self.saved, [])

    def test_alt_left_press_moves_a_click_through_panel(self):
        instance = make_overlay(click_through=True)
        with patch.object(overlay, "modifiers_down", return_value=True):
            self._drag(instance, (260, 300))
        self.assertTrue(instance._dragging)
        instance.root.geometry.assert_called_with(f"{overlay.PANEL_WIDTH}x400+210+280")

    def test_footer_text_follows_the_mode(self):
        self.assertEqual(
            make_overlay()._footer_text(), "Alt+Q 显示 / 隐藏 · 左键直接拖动移动"
        )
        self.assertEqual(
            make_overlay(click_through=True)._footer_text(),
            "Alt+Q 显示 / 隐藏 · 按住 Alt 拖动移动",
        )

    def test_env_switch_recognises_truthy_spellings(self):
        for value in ("1", "true", "ON", " yes "):
            with self.subTest(value=value), \
                    patch.dict(overlay.os.environ, {"XYS_TRENCH_OVERLAY_CLICKTHROUGH": value}):
                self.assertTrue(overlay.click_through_requested())

    def test_env_switch_leaves_absent_or_falsy_values_disabled(self):
        for value in ("", "0", "false", "off", "no"):
            with self.subTest(value=value), \
                    patch.dict(overlay.os.environ, {"XYS_TRENCH_OVERLAY_CLICKTHROUGH": value}):
                self.assertFalse(overlay.click_through_requested())

    def test_selfcheck_reports_the_panel_mode(self):
        with patch.object(sys, "argv", ["trench_overlay.py"]), \
                patch.object(overlay, "log_line"), \
                patch("builtins.print") as printed, \
                patch.object(overlay, "cursor_position", return_value=(1, 1)), \
                patch.object(overlay, "virtual_screen", return_value=(0, 0, 1920, 1080)), \
                patch.object(overlay, "left_button_down", return_value=False), \
                patch.object(overlay, "_read_json", return_value={"items": [{}]}):
            overlay.selfcheck(8765, "alt+q", True)
            overlay.selfcheck(8765, "alt+q", False)
        text = " ".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("点击穿透", text)
        self.assertIn("普通窗口", text)



class ServerWiringTests(unittest.TestCase):
    """静态校验 server.py 的接线位置——起错地方会让 market-worker 子进程也弹窗。"""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.source = (Path(overlay.__file__).resolve().parent / "server.py").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_server_defines_and_calls_the_launcher(self):
        self.assertIn("def start_trench_overlay()", self.source)
        self.assertIn("start_trench_overlay()", self.source.replace("def start_trench_overlay()", "", 1))

    def test_launcher_only_runs_outside_worker_market_mode(self):
        worker_guard = self.source.index("if args.worker_market:")
        call = self.source.index("\n        start_trench_overlay()")
        self.assertGreater(
            call, worker_guard,
            "start_trench_overlay() 必须在 `if args.worker_market:` 之后，否则 worker 子进程也会弹窗",
        )

    def test_launcher_swallows_errors_so_startup_cannot_break(self):
        start = self.source.index("def start_trench_overlay()")
        body = self.source[start:start + 1200]
        self.assertIn("except Exception", body)
        self.assertIn("import trench_overlay", body)


if __name__ == "__main__":
    unittest.main()
