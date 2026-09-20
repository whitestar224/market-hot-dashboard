import unittest
import json
from collections import deque
from unittest.mock import patch

import desktop_alert
import server


class DesktopAlertPriorityTests(unittest.TestCase):
    def setUp(self):
        journal = patch.object(server, "record_event_flow_popup")
        journal.start()
        self.addCleanup(journal.stop)
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

    def test_onchain_research_alert_sits_between_price_signal_and_news(self):
        news = server.normalize_desktop_alert({"key": "news:1", "kind": "律动快讯"})
        chain = server.normalize_desktop_alert(
            {"key": "chain-ecosystem:1", "kind": "链上投研", "queuePriority": 60}
        )
        price = server.normalize_desktop_alert({"key": "price-watch:HYPE:episode:2", "kind": "价格监控"})

        server.enqueue_desktop_alert(news)
        server.enqueue_desktop_alert(chain)
        server.enqueue_desktop_alert(price)

        self.assertEqual([item["key"] for item in server.DESKTOP_ALERT_QUEUE], [price["key"], chain["key"], news["key"]])

    def test_x_status_id_dedupes_changed_retweet_formats(self):
        first = server.normalize_desktop_alert({
            "key": "x-kol:first-format",
            "kind": "X KOL动态",
            "sourceType": "x-kol",
            "sourceId": "crypto-koryo",
            "source": "CryptoKoryo",
            "title": "CryptoKoryo：转推了 _dexuai 的动态",
            "url": "https://x.com/_dexuai/status/2096555530281959754",
        })
        second = server.normalize_desktop_alert({
            "key": "x-kol:expanded-format",
            "kind": "X KOL动态",
            "sourceType": "x-kol",
            "sourceId": "crypto-koryo",
            "source": "CryptoKoryo",
            "title": "CryptoKoryo：完整转推原文",
            "url": "https://x.com/_dexuai/status/2096555530281959754?ref=feed",
        })

        shared = set(server.alert_dedupe_keys(first)) & set(server.alert_dedupe_keys(second))

        self.assertIn("alert-x-status:crypto-koryo|2096555530281959754", shared)

    def test_newsflash_source_id_dedupes_minor_title_edits(self):
        first = server.normalize_desktop_alert({
            "key": "flash:215217|美债收益率逼近高点，沃什面临考验：一句话或许能稳住债市|1788928663",
            "kind": "聚合快讯",
            "source": "BlockBeats 律动",
        })
        edited = server.normalize_desktop_alert({
            "key": "flash:215217|美债收益率逼近高点，沃什面临考验：一句话或能稳住债市|1788928663",
            "kind": "聚合快讯",
            "source": "BlockBeats 律动",
        })

        shared = set(server.alert_dedupe_keys(first)) & set(server.alert_dedupe_keys(edited))

        self.assertIn("alert-newsflash-id:215217|1788928663", shared)

    def test_legacy_seen_newsflash_keys_gain_stable_identity_on_load(self):
        old_key = "flash:215217|旧标题|1788928663"

        expanded = server.expand_legacy_desktop_alert_seen_keys({old_key: 123.0})

        self.assertEqual(expanded["alert-newsflash-id:215217|1788928663"], 123.0)

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

    def test_personal_profit_scorecards_never_create_popups_or_speech(self):
        examples = (
            {
                "title": "淡出交易近一年的地址异常回归，提前交易VVV拉升",
                "body": (
                    "据 TradingBeats 监测地址，0xc1e7 昨晚入金并重仓 VVV。"
                    "VVV 随后大幅上涨，该地址一度浮盈约91.4万美元，"
                    "截至发稿当前浮盈59.92万美元。"
                ),
            },
            {
                "title": "数据：FOMO平台上95%交易者亏损或盈利不足100美元，"
                         "仅0.06%用户盈利超1万美元",
            },
            {
                "title": "加密KOL Unipcs晒战绩：近30日在Robinhood链上浮盈300万美元",
            },
            {
                "title": "ZCAT百倍赢家看涨Meme币AGI，腰斩处买入6笔浮盈升至71.5%",
            },
            {
                "title": "jew.sol持有USELESS超9个月，由浮亏500万美元转为浮盈500万美元",
            },
        )

        for index, example in enumerate(examples):
            with self.subTest(title=example["title"]):
                result = server.launch_desktop_alert({
                    "key": f"flash:personal-pnl-{index}",
                    "kind": "聚合快讯",
                    "source": "BlockBeats 律动",
                    "speech": example["title"],
                    **example,
                })
                self.assertTrue(result["skipped"])
                self.assertEqual(result["reason"], "personal profit/loss update filtered")
                self.assertEqual(result["category"], "position-change")
        self.assertEqual(len(server.DESKTOP_ALERT_QUEUE), 0)

    def test_market_profitability_analysis_is_not_mistaken_for_a_personal_scorecard(self):
        examples = (
            "分析：比特币链上盈利结构接近牛市早期，但仍存下行风险",
            "Tether季度净利润增长，储备资产保持充足",
            "协议升级后为用户提供新的质押收益方案",
            "德国计划调整加密资产收益税规则",
        )

        for title in examples:
            with self.subTest(title=title):
                item = server.normalize_desktop_alert({
                    "kind": "聚合快讯",
                    "source": "BlockBeats 律动",
                    "title": title,
                })
                self.assertEqual(server.desktop_alert_political_military_reason(item), "")

    def test_whale_and_corporate_holding_changes_are_filtered_globally(self):
        examples = (
            {
                "title": "CleanSpark减持228枚比特币，总持仓降至1,703枚",
                "body": "据BitcoinTreasuries数据更新。",
            },
            {
                "title": "某巨鲸增持2,000枚ETH",
                "body": "该地址持仓量升至12,000枚ETH。",
            },
            {
                "title": "Large holder update",
                "body": "A whale reduced its BTC holdings after selling 228 BTC.",
            },
        )

        for example in examples:
            with self.subTest(title=example["title"]):
                item = server.normalize_desktop_alert({
                    "kind": "聚合快讯",
                    "source": "BlockBeats 律动",
                    **example,
                })
                self.assertEqual(
                    server.desktop_alert_political_military_reason(item),
                    "position/holding change filtered",
                )

    def test_holding_change_filter_does_not_hide_price_signals_or_real_project_catalysts(self):
        price_signal = server.normalize_desktop_alert({
            "key": "price-watch:BTC:episode:1",
            "kind": "价格监控",
            "source": "币种价格监控",
            "title": "BTC 买点",
            "body": "巨鲸增持后价格结构突破。",
        })
        project_catalyst = server.normalize_desktop_alert({
            "kind": "项目公告",
            "source": "Project ABC",
            "title": "项目方宣布回购并销毁代币",
        })

        self.assertEqual(server.desktop_alert_political_military_reason(price_signal), "")
        self.assertEqual(server.desktop_alert_political_military_reason(project_catalyst), "")

    def test_whale_holding_change_is_rejected_before_windows_queue(self):
        result = server.launch_desktop_alert({
            "key": "flash:cleanspark-holdings",
            "kind": "聚合快讯",
            "source": "BlockBeats 律动",
            "title": "CleanSpark减持228枚比特币，总持仓降至1,703枚",
        })

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "position/holding change filtered")
        self.assertEqual(result["category"], "position-change")
        self.assertEqual(len(server.DESKTOP_ALERT_QUEUE), 0)

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
        self.assertTrue(oversold["excludeEndpoint"].endswith("/api/price-watch"))
        self.assertEqual(oversold["excludeSymbol"], "TEST")
        self.assertEqual(oversold["excludeLabel"], "剔除监控")
        self.assertEqual(oversold["excludeAction"], "exclude_prior_high")

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

    def test_desktop_exclusion_posts_temporary_mode_without_downgrading_it(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"ok": true, "mode": "temporary"}'

        with patch.object(desktop_alert.urllib.request, "urlopen", return_value=Response()) as urlopen:
            result = desktop_alert.post_price_watch_exclusion(
                "http://127.0.0.1:8765/api/price-watch",
                "CHIP",
                "temporary_exclude",
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(result["mode"], "temporary")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"action": "temporary_exclude", "symbol": "CHIP"},
        )


if __name__ == "__main__":
    unittest.main()
