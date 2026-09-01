import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import qq_onebot_bridge
import wechat_group_monitor

from wechat_group_monitor import (
    _chat_window_signature_matches,
    candidate_rule_score,
    extract_candidate_symbols,
    message_fingerprint,
    messages_from_ocr_rows,
    normalize_chat_platform,
    normalize_group_name,
    normalize_ocr_rows,
    sender_filter_matches,
)


class WechatGroupMonitorParsingTests(unittest.TestCase):
    def test_exact_wechat_chat_requires_one_unique_result_and_confirms_open_title(self):
        current = {"name": None}
        item = SimpleNamespace(
            Name="文件传输助手",
            AutomationId="search_item_function_文件传输助手",
        )
        item.Click = lambda: current.update(name="文件传输助手")
        result_list = SimpleNamespace(
            Exists=lambda *_args: True,
            GetChildren=lambda: [item],
        )
        window = SimpleNamespace(ListControl=lambda **_kwargs: result_list)
        search = object()
        driver = SimpleNamespace(
            _win=window,
            current_chat=lambda: current["name"],
            _search_box=lambda _window: search,
            _paste_into=MagicMock(),
        )

        with patch.object(wechat_group_monitor.time, "sleep", return_value=None):
            opened = wechat_group_monitor._open_exact_wechat_chat(driver, "文件传输助手")

        self.assertTrue(opened)
        driver._paste_into.assert_called_once_with(search, "文件传输助手", clear=True)

    def test_wechat_delivery_readback_requires_exact_text_bubble(self):
        exact = "【历史消息转发测试】 原文：测试"
        message_list = SimpleNamespace(
            GetChildren=lambda: [
                SimpleNamespace(ClassName="mmui::ChatTextItemView", Name="别的消息"),
                SimpleNamespace(ClassName="mmui::ChatTextItemView", Name=exact),
            ]
        )
        driver = SimpleNamespace(_message_list=lambda: message_list)

        self.assertTrue(wechat_group_monitor._wechat_message_visible(driver, exact, timeout=0.5))
        self.assertFalse(wechat_group_monitor._wechat_message_visible(driver, "不存在", timeout=0.5))

    def test_wechat_delivery_accepts_only_a_nearly_complete_truncated_uia_prefix(self):
        exact = "【历史消息转发测试】 " + ("一段足够长且具有唯一性的消息内容" * 5)
        truncated = wechat_group_monitor._stable_text(exact)[:-2]
        too_short = wechat_group_monitor._stable_text(exact)[:50]
        message_list = SimpleNamespace(
            GetChildren=lambda: [SimpleNamespace(ClassName="mmui::ChatTextItemView", Name=truncated)]
        )
        driver = SimpleNamespace(_message_list=lambda: message_list)

        self.assertTrue(wechat_group_monitor._wechat_message_visible(driver, exact, timeout=0.5))
        message_list.GetChildren = lambda: [
            SimpleNamespace(ClassName="mmui::ChatTextItemView", Name=too_short)
        ]
        self.assertFalse(wechat_group_monitor._wechat_message_visible(driver, exact, timeout=0.5))

    def test_qq_collection_prefers_background_onebot_without_window_access(self):
        expected = {
            "ok": True,
            "status": "connected",
            "messages": [{"sender": "鲸鱼🐳PP", "content": "$PONS"}],
            "collectorMode": "onebot",
            "platform": "qq",
        }
        with patch.object(qq_onebot_bridge, "onebot_enabled", return_value=True), \
                patch.object(qq_onebot_bridge, "collect_qq_onebot_messages", return_value=expected), \
                patch.object(wechat_group_monitor, "_collect_with_ui_automation") as ui_collector, \
                patch.dict(os.environ, {"QQ_UI_FALLBACK_ENABLED": "0"}):
            actual = wechat_group_monitor.collect_visible_group_messages(
                "地表最强bsc eth", platform="qq", sender_filter="鲸鱼🐳PP",
            )

        self.assertEqual(actual, expected)
        ui_collector.assert_not_called()

    def test_group_member_count_is_removed_from_name(self):
        self.assertEqual(normalize_group_name("梦之队🌙 (12)"), "梦之队🌙")
        self.assertEqual(normalize_group_name("梦之队🌙（88）"), "梦之队🌙")
        self.assertEqual(normalize_group_name("地表最强bsc eth (337)"), "地表最强bsc eth")

    def test_qq_platform_and_sender_id_normalization(self):
        self.assertEqual(normalize_chat_platform("Q群"), "qq")
        self.assertTrue(sender_filter_matches("鲸鱼🐳PP", "鲸鱼PP"))
        self.assertTrue(_chat_window_signature_matches(r"QQ Chrome_WidgetWin_1 C:\Tencent\QQ.exe", "qq"))
        self.assertFalse(_chat_window_signature_matches(r"微信 WeChat.exe", "qq"))

    def test_file_helper_can_use_exact_search_when_wechat_exposes_no_chat_controls(self):
        controls = [SimpleNamespace(Name="WxTrayIconMessageWindow", ControlTypeName="WindowControl")]

        self.assertTrue(
            wechat_group_monitor._allow_builtin_file_helper_without_title("文件传输助手", controls)
        )
        self.assertFalse(
            wechat_group_monitor._allow_builtin_file_helper_without_title("其他联系人", controls)
        )

    def test_file_helper_still_requires_title_check_when_chat_controls_are_accessible(self):
        controls = [
            SimpleNamespace(Name="WxTrayIconMessageWindow", ControlTypeName="WindowControl"),
            SimpleNamespace(Name="聊天正文", ControlTypeName="TextControl"),
        ]

        self.assertFalse(
            wechat_group_monitor._allow_builtin_file_helper_without_title("文件传输助手", controls)
        )

    def test_message_fingerprint_is_stable_but_content_sensitive(self):
        first = message_fingerprint("梦之队🌙", "白星", "TUT 可能有机会")
        repeated = message_fingerprint("梦之队🌙", "白星", "TUT 可能有机会")
        changed = message_fingerprint("梦之队🌙", "白星", "BTW 可能有机会")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed)

    def test_message_fingerprint_ignores_ocr_whitespace_variation(self):
        first = message_fingerprint("梦之队🌙", "群成员", "Binance 将上线 TUT")
        repeated = message_fingerprint("梦之队🌙", "群成员", "  Binance   将上线 TUT  ")

        self.assertEqual(first, repeated)

    def test_symbol_extraction_keeps_candidates_and_drops_noise(self):
        symbols = extract_candidate_symbols(
            "Binance 将上线 $TUT 和 BTWUSDT，BTC、ETH 与 USDT 不作为机会"
        )

        self.assertIn("TUT", symbols)
        self.assertIn("BTWUSDT", symbols)
        self.assertNotIn("BTC", symbols)
        self.assertNotIn("ETH", symbols)
        self.assertNotIn("USDT", symbols)

    def test_rule_score_prefers_market_opportunity_messages(self):
        opportunity = candidate_rule_score(
            "Binance 将上线 TUTUSDT 永续合约，24h 成交量 2.3 亿，关注突破机会"
        )
        greeting = candidate_rule_score("大家早上好")

        self.assertGreaterEqual(opportunity, 50)
        self.assertEqual(greeting, 0)

    def test_ocr_rows_require_group_title_and_ignore_sidebar(self):
        rows = [
            {"text": "梦之队", "score": 0.96, "left": 520, "top": 24, "right": 610, "bottom": 48},
            {"text": "另一个群的侧边栏摘要", "score": 0.95, "left": 60, "top": 170, "right": 260, "bottom": 192},
            {"text": "Binance 将上线 TUTUSDT 永续合约", "score": 0.94, "left": 390, "top": 180, "right": 760, "bottom": 206},
        ]

        parsed = messages_from_ocr_rows("梦之队🌙", rows, 1200, 800, captured_at=123)

        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["collectorMode"], "window_ocr")
        self.assertEqual(len(parsed["messages"]), 1)
        self.assertIn("TUTUSDT", parsed["messages"][0]["content"])
        self.assertEqual(parsed["messages"][0]["capturedAt"], 123)

    def test_ocr_rows_reject_a_different_open_group(self):
        rows = [
            {"text": "另一个群", "score": 0.98, "left": 520, "top": 24, "right": 610, "bottom": 48},
        ]

        parsed = messages_from_ocr_rows("梦之队🌙", rows, 1200, 800)

        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["status"], "group_not_open")

    def test_qq_ocr_only_keeps_the_configured_sender_message(self):
        rows = [
            {"text": "地表最强bsc eth (337)", "score": 0.98, "left": 470, "top": 22, "right": 720, "bottom": 48},
            {"text": "鲸鱼PP", "score": 0.97, "left": 390, "top": 150, "right": 480, "bottom": 174},
            {"text": "$PONS", "score": 0.96, "left": 400, "top": 180, "right": 495, "bottom": 205},
            {"text": "其他成员", "score": 0.97, "left": 390, "top": 242, "right": 490, "bottom": 266},
            {"text": "$NOISE", "score": 0.96, "left": 400, "top": 272, "right": 510, "bottom": 296},
        ]

        parsed = messages_from_ocr_rows(
            "地表最强bsc eth",
            rows,
            1200,
            800,
            captured_at=456,
            platform="qq",
            sender_filter="鲸鱼🐳PP",
        )

        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["platform"], "qq")
        self.assertEqual(parsed["senderFilter"], "鲸鱼🐳PP")
        self.assertEqual([item["content"] for item in parsed["messages"]], ["$PONS"])
        self.assertEqual(parsed["messages"][0]["sender"], "鲸鱼🐳PP")

    def test_rapidocr_v1_rows_are_normalized(self):
        raw = ([[[1, 2], [31, 2], [31, 12], [1, 12]], "测试文字", 0.91],)

        rows = normalize_ocr_rows((raw, {"elapsed": 0.1}))

        self.assertEqual(rows[0]["text"], "测试文字")
        self.assertEqual(rows[0]["left"], 1)
        self.assertEqual(rows[0]["bottom"], 12)


if __name__ == "__main__":
    unittest.main()
