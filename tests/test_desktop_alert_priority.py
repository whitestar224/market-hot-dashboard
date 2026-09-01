import unittest
import json
from collections import deque
from unittest.mock import patch

import desktop_alert
import server


class DesktopAlertPriorityTests(unittest.TestCase):
    def setUp(self):
        self.original_queue = server.DESKTOP_ALERT_QUEUE
        server.DESKTOP_ALERT_QUEUE = deque()

    def tearDown(self):
        server.DESKTOP_ALERT_QUEUE = self.original_queue

    def test_price_watch_signal_moves_ahead_of_normal_alerts(self):
        normal = server.normalize_desktop_alert({"key": "news:1", "kind": "律动快讯"})
        signal = server.normalize_desktop_alert({"key": "price-watch:HYPE:episode:2", "kind": "价格监控"})

        server.enqueue_desktop_alert(normal)
        server.enqueue_desktop_alert(signal)

        self.assertEqual(server.DESKTOP_ALERT_QUEUE[0]["key"], signal["key"])
        self.assertEqual(server.desktop_alert_queue_priority(signal), 100)

    def test_equal_priority_signals_keep_arrival_order(self):
        first = server.normalize_desktop_alert({"key": "price-watch:HYPE:episode:2", "kind": "价格监控"})
        second = server.normalize_desktop_alert({"key": "price-watch:US:episode:3", "kind": "价格监控"})

        server.enqueue_desktop_alert(first)
        server.enqueue_desktop_alert(second)

        self.assertEqual([item["key"] for item in server.DESKTOP_ALERT_QUEUE], [first["key"], second["key"]])

    def test_chain_ecosystem_alert_sits_between_price_signal_and_news(self):
        news = server.normalize_desktop_alert({"key": "news:1", "kind": "律动快讯"})
        chain = server.normalize_desktop_alert(
            {"key": "chain-ecosystem:1", "kind": "公链生态监控", "queuePriority": 60}
        )
        price = server.normalize_desktop_alert({"key": "price-watch:HYPE:episode:2", "kind": "价格监控"})

        server.enqueue_desktop_alert(news)
        server.enqueue_desktop_alert(chain)
        server.enqueue_desktop_alert(price)

        self.assertEqual([item["key"] for item in server.DESKTOP_ALERT_QUEUE], [price["key"], chain["key"], news["key"]])

    def test_dragon_wave_signals_are_critical_but_serialized_for_popup_and_tts(self):
        signal = server.normalize_desktop_alert(
            {
                "key": "price-watch:dragon-wave:H:1m:1740226620000",
                "kind": "价格监控",
                "queuePriority": server.DESKTOP_ALERT_TRADING_SIGNAL_PRIORITY,
            }
        )

        self.assertGreaterEqual(
            server.desktop_alert_queue_priority(signal),
            server.DESKTOP_ALERT_CRITICAL_PRIORITY,
        )
        self.assertGreaterEqual(server.desktop_alert_interval_seconds(signal), 4)
        self.assertLessEqual(server.desktop_alert_interval_seconds(signal), 10)

    def test_whale_profit_loss_update_is_filtered_globally(self):
        item = server.normalize_desktop_alert({
            "kind": "律动快讯",
            "source": "BlockBeats",
            "title": "巨鲸第四次止损，累计亏损99万美元",
            "body": "某地址的比特币空头仓位已经平仓。",
        })

        self.assertEqual(
            server.desktop_alert_political_military_reason(item),
            "whale profit/loss update filtered",
        )

    def test_only_news_like_popups_enter_news_trade_intake(self):
        self.assertTrue(server.desktop_alert_is_news_trade_intake(server.normalize_desktop_alert({
            "key": "flash:1", "kind": "律动快讯", "source": "BlockBeats", "title": "新事件出现",
        })))
        self.assertTrue(server.desktop_alert_is_news_trade_intake(server.normalize_desktop_alert({
            "key": "listing:1", "kind": "交易所上新", "source": "Gate", "title": "上线 NEWUSDT",
        })))
        self.assertFalse(server.desktop_alert_is_news_trade_intake(server.normalize_desktop_alert({
            "key": "price-watch:NEW:1", "kind": "价格监控", "source": "币种价格监控", "title": "NEW 买点",
        })))
        self.assertFalse(server.desktop_alert_is_news_trade_intake(server.normalize_desktop_alert({
            "key": "news-trade:1", "kind": "News Trade · 事件驱动", "source": "News Trade 监控", "title": "主题升级",
        })))

    def test_token_trading_transition_never_reaches_desktop_or_speech(self):
        with patch.object(server, "launch_desktop_alert") as launch:
            result = server.send_chain_ecosystem_desktop_alert(
                {"id": 9, "chainId": 1, "eventType": "token_trading", "title": "已形成有效交易"}
            )

        self.assertTrue(result["skipped"])
        launch.assert_not_called()

    def test_military_situation_is_rejected_before_it_enters_windows_queue(self):
        result = server.launch_desktop_alert(
            {
                "key": "news:military-1",
                "kind": "律动快讯",
                "title": "以色列军方发动空袭，地区冲突升级",
                "body": "多枚导弹落入相关区域",
                "speech": "军事局势更新",
            }
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["category"], "political-military")
        self.assertEqual(len(server.DESKTOP_ALERT_QUEUE), 0)

    def test_military_actor_headline_from_newsflash_is_filtered(self):
        result = server.launch_desktop_alert(
            {
                "key": "news:military-us-forces-strait",
                "kind": "律动快讯",
                "source": "BlockBeats",
                "title": "伊朗称美军已被驱逐不得进入海峡",
                "body": "伊朗陆军司令表示，美军已被驱逐，不再获准进入该海峡。",
            }
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "military situation filtered")
        self.assertEqual(result["category"], "political-military")
        self.assertEqual(len(server.DESKTOP_ALERT_QUEUE), 0)

    def test_political_election_and_english_geopolitics_are_filtered(self):
        election = server.normalize_desktop_alert(
            {"title": "美国大选进入最终计票阶段", "kind": "市场快讯"}
        )
        english = server.normalize_desktop_alert(
            {
                "title": "Regional update",
                "translationText": "Russia and Ukraine discuss a possible ceasefire after missile attacks.",
                "kind": "X KOL动态",
            }
        )
        political_figure = server.normalize_desktop_alert(
            {"title": "特朗普与普京将举行会谈", "kind": "律动快讯"}
        )

        self.assertTrue(server.desktop_alert_political_military_reason(election))
        self.assertTrue(server.desktop_alert_political_military_reason(english))
        self.assertTrue(server.desktop_alert_political_military_reason(political_figure))

    def test_financial_policy_listing_and_price_signals_are_not_misclassified(self):
        financial_policy = server.normalize_desktop_alert(
            {"title": "美联储宣布降息 25 个基点", "kind": "律动快讯"}
        )
        crypto_regulation = server.normalize_desktop_alert(
            {"title": "SEC 批准现货 ETF 上市交易", "kind": "上新事件"}
        )
        price_signal = server.normalize_desktop_alert(
            {
                "key": "price-watch:TRUMP:5m:1",
                "kind": "价格监控",
                "title": "TRUMP 5分钟 起爆预判",
                "body": "多周期结构确认",
            }
        )

        self.assertEqual(server.desktop_alert_political_military_reason(financial_policy), "")
        self.assertEqual(server.desktop_alert_political_military_reason(crypto_regulation), "")
        self.assertEqual(server.desktop_alert_political_military_reason(price_signal), "")

    def test_prior_high_alert_carries_the_narrow_exclusion_action(self):
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            alert = server.launch_price_watch_alert(
                {
                    "eventType": "prior_high",
                    "symbol": "TEST",
                    "currentPrice": 9.8,
                    "weekHigh": 10,
                    "distancePct": 2,
                    "provider": "Test Futures",
                    "episode": 1,
                    "isFirstCandidate": True,
                }
            )
            oversold = server.launch_price_watch_alert(
                {
                    "eventType": "oversold_rebound",
                    "symbol": "TEST",
                    "currentPrice": 4.9,
                    "distancePct": 2,
                    "drawdownPct": 51,
                    "rangeLow": 4,
                    "rangeHigh": 5,
                    "episode": 1,
                }
            )

        normalized = server.normalize_desktop_alert(alert)
        self.assertEqual(normalized["excludeSymbol"], "TEST")
        self.assertEqual(normalized["excludeLabel"], "剔除前高")
        self.assertTrue(normalized["excludeEndpoint"].endswith("/api/price-watch"))
        self.assertEqual(normalized["excludeAction"], "exclude_prior_high")
        self.assertNotIn("excludeEndpoint", oversold)

    def test_desktop_exclusion_posts_the_requested_structure_action(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"ok": true}'

        with patch.object(desktop_alert.urllib.request, "urlopen", return_value=Response()) as urlopen:
            result = desktop_alert.post_price_watch_exclusion(
                "http://127.0.0.1:8765/api/price-structures",
                "CHIP",
                "exclude_structure",
            )

        request = urlopen.call_args.args[0]
        self.assertTrue(result["ok"])
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"action": "exclude_structure", "symbol": "CHIP"},
        )


if __name__ == "__main__":
    unittest.main()
