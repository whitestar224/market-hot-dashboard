import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class EventMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now_ms = int(time.time() * 1000)

    def classify(self, **overrides):
        row = {
            "id": "event-1",
            "sourceType": "newsflash",
            "source": "BlockBeats",
            "sourceLabel": "BB",
            "title": "市场事件",
            "body": "",
            "url": "https://example.com/event-1",
            "timestamp": self.now_ms,
        }
        row.update(overrides)
        return server.classify_event_monitor_row(row, self.now_ms)

    def culture_fixture(self):
        path = Path(__file__).parent / "fixtures" / "news_trade_counter_consensus_culture.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def onchain_row(
        symbol="牛来",
        *,
        contract="0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
        heat=90,
        liquidity="120万",
        volume=8_000_000,
        transactions="8.2万",
        chain="bsc",
    ):
        return {
            "symbol": symbol,
            "name": f"{symbol} 热门池",
            "chain": chain,
            "chainLabel": chain.upper(),
            "heat": heat,
            "amount": volume,
            "note": f"市值 $2600万 · 流动性 ${liquidity} · 交易 {transactions} · 合约 铸币关闭 非貔貅盘",
            "url": f"https://web3.okx.com/zh-hans/token/{chain}/{contract}",
        }

    def test_hot_event_entities_include_quoted_cultural_topic(self):
        entities = server.event_monitor_hot_entities(
            "暑期档动画电影《牛来》因争议反向出圈",
            "BNB Chain 上出现多个同名 Meme。",
        )

        self.assertIn("牛来", entities)

    def test_hot_event_entities_extract_repeated_catchphrase_aliases(self):
        entities = server.event_monitor_hot_entities(
            "名人长文中的传播金句",
            "以后别叫我景甜了，叫我妈妈吧。",
        )

        self.assertIn("景甜", entities)
        self.assertIn("妈妈", entities)

    def test_news_keywords_extract_topic_and_propagation_terms(self):
        keywords = server.event_monitor_news_keywords(
            "反常电影《倒放》因离谱差评突然爆红并出现同名 Meme",
            "观众开始围观打卡和二创，影院随后加场。",
        )

        self.assertIn("倒放", keywords)
        self.assertIn("离谱", keywords)
        self.assertIn("二创", keywords)

    def test_accepted_popup_news_is_persisted_as_existing_pipeline_input(self):
        with tempfile.TemporaryDirectory() as tempdir, patch.object(
            server, "NEWS_TRADE_DESKTOP_INTAKE_PATH", Path(tempdir) / "desktop-intake.json"
        ):
            item = server.normalize_desktop_alert({
                "key": "flash:fixture-1",
                "kind": "律动快讯",
                "source": "BlockBeats",
                "sourceLabel": "BB",
                "title": "冷门作品因争议突然出圈",
                "body": "出现围观、二创和同名链上 Meme",
                "url": "https://example.com/fixture-1",
                "time": self.now_ms,
            })
            server.record_desktop_alert_news_trade_intake(item)
            payload = server.read_json_cache(server.NEWS_TRADE_DESKTOP_INTAKE_PATH)

        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["sourceType"], "newsflash")
        self.assertTrue(payload["rows"][0]["fromDesktopAlert"])

    def test_curiosity_profile_scores_absurd_controversial_discussion_and_legend(self):
        profile = server.event_monitor_curiosity_profile(
            "这个离谱反常事件引发争议并登上热搜，草根小人物一夜逆袭成为传奇。"
        )

        self.assertTrue(profile["qualified"])
        self.assertIn("新奇猎奇", profile["labels"])
        self.assertIn("争议性", profile["labels"])
        self.assertIn("高讨论度", profile["labels"])
        self.assertIn("传奇性", profile["labels"])
        self.assertGreater(profile["breakdown"]["legendary"], 0)

    def test_big_gossip_scores_public_figure_pair_as_its_own_event_dimension(self):
        profile = server.event_monitor_counter_consensus_profile(
            "孙宇晨和景甜成为全网讨论的大瓜，双方关系引发围观。"
        )
        heat = server.event_monitor_event_heat_score(
            profile,
            {"velocityScore": 20, "crossPlatformScore": 30, "discussionSignalCount": 2},
            news_count=1,
        )

        self.assertTrue(profile["qualified"])
        self.assertIn("大瓜", profile["labels"])
        self.assertGreaterEqual(profile["features"]["bigGossip"], 80)
        self.assertGreater(heat["raw"]["bigGossip"], 0)
        self.assertGreater(heat["points"]["bigGossip"], 0)
        self.assertEqual(sum(heat["weights"].values()), 100)

    def test_counter_consensus_culture_model_is_generic_not_a_named_whitelist(self):
        profile = server.event_monitor_counter_consensus_profile(
            "一部原本无人问津的冷门动画《倒放》因离谱差评、极端反差和吐槽突然爆红，"
            "观众开始围观打卡、制作表情包和二创，影院随后加场。"
        )

        self.assertTrue(profile["qualified"])
        self.assertIn("低基数", profile["labels"])
        self.assertIn("极端反差", profile["labels"])
        self.assertIn("参与式共识", profile["labels"])
        self.assertNotIn("牛来", json.dumps(profile, ensure_ascii=False))

    def test_counter_consensus_fixture_is_explicitly_mock_and_clusters_to_one_topic(self):
        fixture = self.culture_fixture()
        self.assertTrue(fixture["fixture"])
        self.assertTrue(fixture["mock"])
        market = fixture["onchainCandidates"]["highQuality"]
        events = []
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            for row in fixture["events"]:
                row = {
                    **row,
                    "timestamp": self.now_ms - int(row["minutesAgo"] * 60_000),
                    "capturedAt": self.now_ms,
                }
                event = server.classify_event_monitor_row(row, self.now_ms)
                if event and event["isNewsTrade"]:
                    events.append(event)

        topics = server.event_monitor_cluster_topics(events, now_ms=self.now_ms)

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["eventType"], "反常识文化事件 / 负面共识破圈")
        self.assertEqual(topics[0]["newsCount"], len(events))
        self.assertGreaterEqual(topics[0]["sourceCount"], 2)
        self.assertNotEqual(topics[0]["memeCandidates"][0]["associationStatus"], "official-confirmed")

    def test_high_event_heat_with_low_quality_chain_stays_event_observation(self):
        fixture = self.culture_fixture()
        market = fixture["onchainCandidates"]["lowQuality"]
        row = {
            **fixture["events"][0],
            "timestamp": self.now_ms - 30 * 60_000,
            "capturedAt": self.now_ms,
        }
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            event = server.classify_event_monitor_row(row, self.now_ms)
        topic = server.event_monitor_cluster_topics([event], now_ms=self.now_ms)[0]

        self.assertGreaterEqual(topic["eventHeatScore"], server.NEWS_TRADE_CONFIG["thresholds"]["eventObservation"])
        self.assertLess(topic["onchainTradeScore"], server.NEWS_TRADE_CONFIG["thresholds"]["onchainTrade"])
        self.assertEqual(topic["candidateTier"], "event-observation")
        self.assertFalse(topic["executionEligible"])

    def test_high_quality_chain_can_upgrade_culture_event_to_trade_candidate(self):
        fixture = self.culture_fixture()
        market = fixture["onchainCandidates"]["highQuality"]
        events = []
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            for row in fixture["events"]:
                event = server.classify_event_monitor_row(
                    {
                        **row,
                        "timestamp": self.now_ms - int(row["minutesAgo"] * 60_000),
                        "capturedAt": self.now_ms,
                    },
                    self.now_ms,
                )
                if event and event["isNewsTrade"]:
                    events.append(event)
        topic = server.event_monitor_cluster_topics(events, now_ms=self.now_ms)[0]

        self.assertGreaterEqual(topic["eventHeatScore"], server.NEWS_TRADE_CONFIG["thresholds"]["eventTrade"])
        self.assertGreaterEqual(topic["onchainTradeScore"], server.NEWS_TRADE_CONFIG["thresholds"]["onchainTrade"])
        self.assertEqual(topic["candidateTier"], "trade-candidate")
        self.assertTrue(topic["executionEligible"])
        self.assertIn(topic["memeCandidates"][0]["associationStatus"], {"highly-related-unconfirmed", "name-only"})

    def test_news_trade_phase_separates_fast_understanding_day_window_and_review(self):
        understanding = server.event_monitor_news_trade_phase(120, "离谱事件引发热议")
        pre_fermentation = server.event_monitor_news_trade_phase(12 * 60, "离谱事件持续讨论")
        expired = server.event_monitor_news_trade_phase(25 * 60, "离谱事件仍在讨论")
        fermented = server.event_monitor_news_trade_phase(30, "同名 Meme 24小时暴涨40倍")

        self.assertEqual(understanding["code"], "understanding")
        self.assertTrue(understanding["earlyEntryEligible"])
        self.assertEqual(pre_fermentation["code"], "pre-fermentation")
        self.assertTrue(pre_fermentation["earlyEntryEligible"])
        self.assertEqual(expired["code"], "expired")
        self.assertFalse(expired["earlyEntryEligible"])
        self.assertEqual(fermented["code"], "fermented")
        self.assertFalse(fermented["earlyEntryEligible"])

    def test_candidate_ranker_prefers_the_hotter_larger_same_name_pool(self):
        smaller = self.onchain_row(
            contract="0x650d427179522c383d45365b05c9879c7cb6ffff",
            heat=57,
            liquidity="9.94万",
            volume=1_855_800,
            transactions="1.33万",
        )
        larger = self.onchain_row(
            contract="0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
            heat=92,
            liquidity="93.51万",
            volume=48_287_800,
            transactions="20.73万",
        )

        ranked = server.event_monitor_rank_meme_candidates(
            {"sourceType": "newsflash", "url": "https://example.com/牛来"},
            "动画电影《牛来》因争议反向出圈",
            "BNB Chain 同名 Meme 热度快速上升",
            source_rows=[smaller, larger],
        )

        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["contractAddress"], "0xbeea1d618e533a387d941f58a7d4c9b7bd377777")
        self.assertGreater(ranked[0]["candidateScore"], ranked[1]["candidateScore"])
        self.assertGreater(ranked[0]["liquidityUsd"], ranked[1]["liquidityUsd"])

    def test_geckoterminal_trend_parser_preserves_contract_metrics_and_creation_time(self):
        payload = {
            "data": [{
                "id": "bsc_pool",
                "type": "pool",
                "attributes": {
                    "address": "0x9999999999999999999999999999999999999999",
                    "base_token_price_usd": "0.0045",
                    "pool_created_at": "2026-08-27T11:11:12Z",
                    "market_cap_usd": "4500000",
                    "price_change_percentage": {"h24": "4515.91"},
                    "transactions": {"h24": {"buys": 1200, "sells": 900}},
                    "volume_usd": {"h1": "900000", "h24": "11020000"},
                    "reserve_in_usd": "394710",
                },
                "relationships": {
                    "base_token": {"data": {"id": "bsc_token", "type": "token"}},
                },
            }],
            "included": [{
                "id": "bsc_token",
                "type": "token",
                "attributes": {
                    "address": "0xff673079235560E4De3Fe4554c9981D759aF7777",
                    "name": "关系金句事件样本",
                    "symbol": "金句",
                },
            }],
        }

        rows = server.news_trade_geckoterminal_rows_from_payload(payload, "bsc", now_ms=self.now_ms)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contractAddress"], "0xff673079235560E4De3Fe4554c9981D759aF7777")
        self.assertEqual(rows[0]["chain"], "bsc")
        self.assertEqual(rows[0]["liquidityUsd"], 394710)
        self.assertEqual(rows[0]["volume24hUsd"], 11020000)
        self.assertGreater(rows[0]["pairCreatedAt"], 0)

    def test_onchain_reverse_discovery_keeps_primary_and_low_liquidity_semantic_backup(self):
        primary_contract = "0xff673079235560e4de3fe4554c9981d759af7777"
        backup_contract = "0xd6997e37d545a58e8d7bdea3effd0b548d8f7777"
        trend = {
            "symbol": "金句",
            "name": "关系金句事件样本",
            "chain": "bsc",
            "chainLabel": "BNB Chain",
            "contractAddress": primary_contract,
            "heat": 96,
            "liquidityUsd": 394_710,
            "volume24hUsd": 11_020_000,
            "change24hPercent": 120,
            "pairCreatedAt": self.now_ms - 60 * 60_000,
            "url": "https://www.geckoterminal.com/bsc/pools/fixture",
            "source": "GeckoTerminal 链上趋势",
        }
        public_rows = [{
            "id": "public-fixture",
            "sourceType": "web-search",
            "source": "公开新闻",
            "title": "名人本人下场发布万字长文，恋情爆料与一句‘以后叫我妈妈’形成热搜词条",
            "body": "离谱反转引发争议、围观和二创。",
            "url": "https://example.com/public-fixture",
            "timestamp": self.now_ms,
        }]
        dex_rows = [
            {**trend, "venue": "DexScreener", "tradeUrl": "https://dexscreener.com/bsc/primary"},
            {
                **trend,
                "symbol": "妈妈",
                "name": "关系金句事件样本",
                "contractAddress": backup_contract,
                "liquidityUsd": 40_580,
                "volume24hUsd": 900_090,
                "heat": 68,
                "venue": "DexScreener",
                "tradeUrl": "https://dexscreener.com/bsc/backup",
            },
        ]
        with patch.object(server, "news_trade_cached_public_search_rows", return_value=public_rows), patch.object(
            server, "news_trade_cached_dex_search_rows", return_value=dex_rows
        ):
            discovery = server.news_trade_background_discovery([trend], now_ms=self.now_ms)

        self.assertEqual(len(discovery["sourceRows"]), 2)
        self.assertEqual({row["contractAddress"] for row in discovery["candidateRows"]}, {primary_contract, backup_contract})
        event = server.classify_event_monitor_row(
            discovery["sourceRows"][0],
            self.now_ms,
            candidate_source_rows=discovery["candidateRows"],
        )
        self.assertIsNotNone(event)
        self.assertTrue(event["isNewsTrade"])
        self.assertEqual(len(event["memeCandidates"]), 2)
        self.assertEqual(event["memeCandidates"][0]["contractAddress"], primary_contract)
        backup = next(row for row in event["memeCandidates"] if row["contractAddress"] == backup_contract)
        self.assertFalse(backup["tradeReady"])

    def test_bsc_candidate_exposes_wallet_and_native_dex_routes(self):
        candidate = server.event_monitor_onchain_candidate({
            "symbol": "金句",
            "name": "关系金句事件样本",
            "chain": "bsc",
            "contractAddress": "0xff673079235560e4de3fe4554c9981d759af7777",
            "liquidityUsd": 394_710,
            "volume24hUsd": 11_020_000,
            "venue": "DexScreener",
            "tradeUrl": "https://dexscreener.com/bsc/fixture",
        })

        self.assertIn("OKX DEX", candidate["tradeUrls"])
        self.assertIn("PancakeSwap", candidate["tradeUrls"])
        self.assertIn("Bitget Wallet", candidate["tradeUrls"])
        self.assertTrue(candidate["tradeReady"])

    def test_elon_doge_tweet_is_information_latency_candidate(self):
        event = self.classify(
            sourceType="x-kol",
            source="Elon Musk",
            sourceLabel="X",
            title="Elon Musk 正式发文提及 DOGE",
            body="DOGEUSDT 价格和成交量快速变化",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["template"], "kol-latency")
        self.assertIn("DOGE", event["assets"])
        self.assertFalse(event["isNewsTrade"])

    def test_usdc_depeg_is_anchor_policy_candidate(self):
        event = self.classify(
            title="SVB 风险导致 USDC 脱锚",
            body="官方确认储备处置方案，USDCUSD 价格出现折价",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["template"], "anchor-policy")
        self.assertIn("USDC", event["assets"])
        self.assertFalse(event["isNewsTrade"])

    def test_grayscale_lawsuit_win_is_instant_repricing_candidate(self):
        event = self.classify(
            title="Grayscale 在 ETF 诉讼中胜诉",
            body="法院正式裁决后 BTCUSDT 价格和成交量同步变化",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["template"], "instant-repricing")
        self.assertIn("BTC", event["assets"])
        self.assertFalse(event["isNewsTrade"])

    def test_binance_listing_is_listing_latency_candidate(self):
        event = self.classify(
            sourceType="listing",
            source="Binance",
            sourceLabel="BN",
            title="Binance 宣布上线 ACTUSDT 永续合约",
            body="ACTUSDT 新增交易安排",
            symbol="ACTUSDT",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["template"], "listing-latency")
        self.assertIn("ACT", event["assets"])
        self.assertFalse(event["isNewsTrade"])

    def test_only_a_hot_liquid_new_contract_enters_news_trade(self):
        market = self.onchain_row(symbol="ACT", heat=88, liquidity="180万", volume=12_000_000)
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            with patch.object(server, "event_monitor_dex_pair", return_value={}):
                event = self.classify(
                    sourceType="listing",
                    source="Binance",
                    sourceLabel="BN",
                    title="Binance 宣布上线 ACTUSDT 永续合约",
                    body="ACTUSDT 新增交易安排",
                    symbol="ACTUSDT",
                )

        self.assertTrue(event["isNewsTrade"])
        self.assertEqual(event["memeCandidates"][0]["symbol"], "ACT")
        self.assertGreaterEqual(event["memeCandidates"][0]["liquidityUsd"], 250_000)

    def test_tradfi_perpetual_announcement_never_enters_news_trade(self):
        market = self.onchain_row(symbol="ANET", heat=95, liquidity="500万", volume=30_000_000)
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            event = self.classify(
                sourceType="listing",
                source="Bitget",
                title="【重要】Bitget 关于上线 ANETUSDT 热门股票永续合约的公告",
                body="股票合约最高五倍杠杆",
                symbol="ANETUSDT",
            )

        self.assertFalse(event["isNewsTrade"])

    def test_news_trade_event_publishes_a_short_lived_structure_catalyst(self):
        market = self.onchain_row(symbol="EVENT", heat=92, liquidity="260万", volume=20_000_000)
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            event = self.classify(
                sourceType="listing",
                source="Binance",
                sourceLabel="BN",
                title="Binance 宣布上线 EVENTUSDT 永续合约",
                body="EVENTUSDT 新增交易安排",
                symbol="EVENTUSDT",
            )
        with server.PRICE_STRUCTURE_EVENT_CONTEXT_LOCK:
            original_contexts = dict(server.PRICE_STRUCTURE_EVENT_CONTEXTS)
        try:
            contexts = server.update_price_structure_event_contexts([event], now_ms=self.now_ms)
            context = server.price_structure_recent_event_context("EVENT", now_ms=self.now_ms)
        finally:
            with server.PRICE_STRUCTURE_EVENT_CONTEXT_LOCK:
                server.PRICE_STRUCTURE_EVENT_CONTEXTS.clear()
                server.PRICE_STRUCTURE_EVENT_CONTEXTS.update(original_contexts)

        self.assertIn("EVENT", contexts)
        self.assertIsNotNone(context)
        self.assertEqual(context["eventId"], event["id"])
        self.assertGreater(context["expiresAt"], self.now_ms)

    def test_flash_crash_is_market_dislocation_candidate(self):
        event = self.classify(
            title="BTC 出现闪崩与异常清算",
            body="BTCUSDT 价格短时错价，成交量和爆仓规模快速上升",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["template"], "market-dislocation")
        self.assertIn("BTC", event["assets"])
        self.assertFalse(event["isNewsTrade"])

    def test_orderbook_basis_anomaly_is_risk_candidate(self):
        event = self.classify(
            title="BROCCOLI714 盘口与基差异常",
            body="BROCCOLI714USDT 订单簿深度、资金费率和持仓量同时异动",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["template"], "orderbook-risk")
        self.assertIn("BROCCOLI714", event["assets"])
        self.assertFalse(event["isNewsTrade"])

    def test_hot_culture_meme_event_resolves_chain_contract_and_trade_route(self):
        with patch.object(server, "event_monitor_dex_pair", return_value={}), patch.object(
            server, "event_monitor_onchain_source_rows", return_value=[]
        ):
            event = self.classify(
                title="BSC上Meme币「牛来」市值短时突破2600万美元，24小时涨超40倍",
                body="暑期档动画电影《牛来》因制作争议反向出圈，同期交易量达2450万美元。",
                url="https://gmgn.ai/bsc/token/i_hot_0xBEEA1D618e533a387D941F58a7d4c9b7bD377777",
            )

        self.assertIsNotNone(event)
        self.assertEqual(event["template"], "meme-catalyst")
        self.assertIn("牛来", event["assets"])
        self.assertTrue(event["isNewsTrade"])
        opportunity = event["memeOpportunity"]
        self.assertEqual(opportunity["chain"], "bsc")
        self.assertEqual(opportunity["chainId"], "56")
        self.assertEqual(opportunity["contractAddress"], "0xbeea1d618e533a387d941f58a7d4c9b7bd377777")
        self.assertEqual(opportunity["marketCapUsd"], 26_000_000)
        self.assertIn("GMGN", opportunity["venues"])
        self.assertTrue(opportunity["tradeReady"])
        self.assertEqual(event["newsTradePhase"], "fermented")
        self.assertFalse(event["executionEligible"])

    def test_topic_pool_retains_bull_movie_after_source_cache_rotation(self):
        candidate = {
            "symbol": "牛来",
            "name": "牛来",
            "chain": "bsc",
            "chainId": "56",
            "chainLabel": "BNB Chain",
            "contractAddress": "0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
            "liquidityUsd": 1_200_000,
            "tradeReady": True,
            "candidateScore": 88,
            "narrativeRelevance": 98,
        }
        topic = {
            "id": "topic-bull-movie",
            "topicKey": "meme:bsc:0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
            "title": "动画电影《牛来》因离谱争议引发热议",
            "body": "同名 Meme 开始出现讨论度",
            "timestamp": self.now_ms,
            "firstSeenAt": self.now_ms,
            "isNewsTrade": True,
            "memeCandidates": [candidate],
            "memeOpportunity": candidate,
            "confirmation": ["新奇猎奇"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(server, "PERSIST_CACHE_DIR", Path(temp_dir)):
                first = server.news_trade_update_topic_pool([topic], now_ms=self.now_ms)
                rotated = server.news_trade_update_topic_pool([], now_ms=self.now_ms + 60_000)
                newer = server.news_trade_update_topic_pool([
                    {
                        **topic,
                        "id": "topic-newer",
                        "topicKey": "meme:bsc:0x1111111111111111111111111111111111111111",
                        "title": "动画电影《另一个梗》引发离谱争议讨论",
                        "timestamp": self.now_ms + 120_000,
                        "firstSeenAt": self.now_ms + 120_000,
                        "eventEntities": ["另一个梗"],
                        "assets": ["另一个梗"],
                        "memeCandidates": [{
                            **candidate,
                            "symbol": "另一个梗",
                            "name": "另一个梗",
                            "contractAddress": "0x1111111111111111111111111111111111111111",
                        }],
                    }
                ], now_ms=self.now_ms + 120_000)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(rotated), 1)
        self.assertEqual(rotated[0]["title"], topic["title"])
        self.assertFalse(rotated[0]["sourceActive"])
        self.assertEqual(rotated[0]["enteredAt"], first[0]["enteredAt"])
        self.assertIn("牛来", rotated[0]["newsKeywords"])
        self.assertEqual(len(newer), 2)
        self.assertIn("narrative:另一个梗", {row["topicKey"] for row in newer})
        self.assertEqual(newer[0]["topicKey"], "narrative:另一个梗")

    def test_legacy_contract_topics_collapse_but_listing_keeps_separate_identity(self):
        def candidate(contract):
            return {
                "symbol": "MarsCoin",
                "name": "MarsCoin",
                "chain": "bsc",
                "contractAddress": contract,
                "liquidityUsd": 1_000_000,
                "volume24hUsd": 20_000_000,
                "candidateScore": 90,
                "onchainTradeScore": 70,
                "tradeReady": True,
            }

        first_contract = candidate("0x1111111111111111111111111111111111111111")
        second_contract = candidate("0x2222222222222222222222222222222222222222")
        common = {
            "title": "MarsCoin 热点事件持续发酵",
            "body": "MarsCoin 同名链上资产出现多个候选",
            "timestamp": self.now_ms,
            "firstSeenAt": self.now_ms,
            "eventEntities": ["MarsCoin"],
            "assets": ["MarsCoin"],
            "isNewsTrade": True,
            "eventHeatScore": 80,
            "onchainTradeScore": 70,
            "topicScore": 74,
        }
        narrative_a = {
            **common,
            "id": "legacy-a",
            "topicKey": "meme:bsc:0x1111111111111111111111111111111111111111",
            "memeCandidates": [first_contract, second_contract],
            "memeOpportunity": first_contract,
        }
        narrative_b = {
            **common,
            "id": "legacy-b",
            "topicKey": "meme:bsc:0x2222222222222222222222222222222222222222",
            "memeCandidates": [second_contract, first_contract],
            "memeOpportunity": second_contract,
        }
        listing = {
            **common,
            "id": "aster-listing",
            "sourceType": "listing",
            "template": "listing-latency",
            "source": "Aster",
            "title": "Aster 上线 MarsCoin/USDT 永续合约",
            "memeCandidates": [second_contract],
            "memeOpportunity": second_contract,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(server, "PERSIST_CACHE_DIR", Path(temp_dir)):
                topics = server.news_trade_update_topic_pool(
                    [narrative_a, narrative_b, listing],
                    now_ms=self.now_ms,
                )

        self.assertEqual(len(topics), 2)
        self.assertEqual(sum(row["topicKey"] == "narrative:marscoin" for row in topics), 1)
        self.assertTrue(any(row["topicKey"].startswith("listing:aster:MARSCOIN:") for row in topics))
        narrative = next(row for row in topics if row["topicKey"] == "narrative:marscoin")
        self.assertEqual(len(narrative["memeCandidates"]), 2)

    def test_fermented_topic_cannot_prepare_a_buy(self):
        with patch.object(server, "event_monitor_dex_pair", return_value={}):
            event = self.classify(
                title="BSC上Meme币「牛来」24小时暴涨40倍",
                body="电影《牛来》因离谱争议引发全网热议。",
                url="https://gmgn.ai/bsc/token/i_hot_0xBEEA1D618e533a387D941F58a7d4c9b7bD377777",
            )

        with self.assertRaisesRegex(ValueError, "仅复盘"):
            server.prepare_news_trade_execution(
                {"eventId": event["id"], "amountUsdt": 100},
                events=[event],
            )

    def test_fermented_topic_remains_visible_for_review(self):
        with patch.object(server, "event_monitor_dex_pair", return_value={}):
            event = self.classify(
                title="BSC上Meme币「牛来」24小时暴涨40倍",
                body="电影《牛来》因离谱争议引发全网热议。",
                url="https://gmgn.ai/bsc/token/i_hot_0xBEEA1D618e533a387D941F58a7d4c9b7bD377777",
            )

        topics = server.event_monitor_cluster_topics([event], now_ms=self.now_ms)

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["newsTradePhase"], "fermented")
        self.assertFalse(topics[0]["executionEligible"])

    def test_strong_name_binding_can_admit_a_verified_topic(self):
        market = self.onchain_row(symbol="OPENAI", heat=86, liquidity="80万", volume=5_000_000)
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            event = self.classify(
                title="Tom Lee谈AI时代人的稀缺性",
                body="访谈多次讨论OpenAI与Sam Altman，并提及马斯克。",
            )

        self.assertTrue(event["nameBinding"]["qualified"])
        self.assertTrue(event["isNewsTrade"])

    def test_same_hot_topic_news_merges_into_one_ranked_topic(self):
        market = self.onchain_row(heat=94, liquidity="180万", volume=28_000_000)
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            first = self.classify(
                id="news-a",
                title="动画电影《牛来》因争议反向出圈",
                body="BNB Chain 同名 Meme 成交量快速放大",
                timestamp=self.now_ms - 60_000,
            )
            second = self.classify(
                id="news-b",
                source="重点 KOL",
                sourceType="x-kol",
                title="《牛来》登上热搜，链上同名 Meme 继续扩散",
                body="官方账号发文后，市场成交量与流动性同步上升",
                timestamp=self.now_ms,
            )

        topics = server.event_monitor_cluster_topics([first, second], now_ms=self.now_ms)

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["newsCount"], 2)
        self.assertEqual(topics[0]["sourceCount"], 2)
        self.assertEqual(len(topics[0]["relatedNews"]), 2)
        self.assertEqual(topics[0]["memeCandidates"][0]["symbol"], "牛来")
        self.assertEqual(topics[0]["id"], server.event_monitor_cluster_topics([second, first], now_ms=self.now_ms)[0]["id"])

    def test_active_news_search_finds_missed_local_topic(self):
        rows = [
            {
                "id": "unrelated",
                "sourceType": "newsflash",
                "title": "BTC 市场保持平稳",
                "body": "暂无新的热点",
                "timestamp": self.now_ms,
            },
            {
                "id": "bull-movie",
                "sourceType": "newsflash",
                "source": "BlockBeats",
                "title": "动画电影《牛来》因争议反向出圈",
                "body": "同名 Meme 在 BNB Chain 热度上升",
                "timestamp": self.now_ms,
            },
        ]

        results = server.news_trade_local_search_rows("牛来", source_rows=rows)

        self.assertEqual(results[0]["id"], "bull-movie")
        self.assertEqual(len(results), 1)

    def test_active_news_search_builds_confirmable_topic_preview(self):
        market = self.onchain_row(heat=96, liquidity="260万", volume=32_000_000)
        external = [
            {
                "id": "web-bull-movie",
                "sourceType": "web-search",
                "source": "公开新闻",
                "sourceLabel": "WEB",
                "title": "动画电影《牛来》登上热搜并引发讨论",
                "body": "现实热点快速扩散",
                "url": "https://example.com/bull-movie",
                "timestamp": self.now_ms,
            }
        ]
        with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
            preview = server.news_trade_search_preview(
                "牛来",
                local_rows=[],
                external_rows=external,
                now_ms=self.now_ms,
                existing_topics=[],
            )

        self.assertTrue(preview["ok"])
        self.assertEqual(len(preview["topics"]), 1)
        self.assertEqual(preview["topics"][0]["memeCandidates"][0]["symbol"], "牛来")
        self.assertFalse(preview["topics"][0]["duplicate"])
        self.assertTrue(preview["previewId"])

    def test_confirmed_search_topic_persists_one_deduplicated_source_row(self):
        market = self.onchain_row(heat=96, liquidity="260万", volume=32_000_000)
        external = [{
            "id": "web-bull-movie",
            "sourceType": "web-search",
            "source": "公开新闻",
            "title": "动画电影《牛来》登上热搜",
            "body": "热点扩散",
            "url": "https://example.com/bull-movie",
            "timestamp": self.now_ms,
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(server, "PERSIST_CACHE_DIR", Path(temp_dir)):
                with patch.object(server, "event_monitor_onchain_source_rows", return_value=[market]):
                    first = server.news_trade_search_preview(
                        "牛来", local_rows=[], external_rows=external, now_ms=self.now_ms, existing_topics=[]
                    )
                    saved = server.news_trade_confirm_preview(first["previewId"])
                    saved_again = server.news_trade_confirm_preview(first["previewId"])
                stored = server.read_json_cache(Path(temp_dir) / "news_trade_search_rows.json")

        self.assertTrue(saved["ok"])
        self.assertTrue(saved_again["alreadyAdded"])
        self.assertEqual(len(stored["rows"]), 1)

    def test_news_trade_prepare_never_executes_without_credentials_and_confirmation(self):
        with patch.object(server, "event_monitor_dex_pair", return_value={}):
            event = self.classify(
                title="BSC上Meme币「牛来」市值突破2600万美元，成交量放大",
                body="BNB Chain Meme热点快速传播。",
                url="https://gmgn.ai/bsc/token/i_hot_0xBEEA1D618e533a387D941F58a7d4c9b7bD377777",
            )
        safe_security = server.news_trade_finalize_security({
            "verified": True,
            "provider": "fixture",
            "checkedAt": self.now_ms,
            "isHoneypot": False,
            "canSell": True,
            "openSource": True,
            "buyTaxPct": 0,
            "sellTaxPct": 0,
        })
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(server, "news_trade_security_snapshot", return_value=safe_security):
                result = server.prepare_news_trade_execution(
                    {"eventId": event["id"], "amountUsdt": 100},
                    events=[event],
                )

        self.assertEqual(result["mode"], "preview")
        self.assertFalse(result["liveExecution"])
        self.assertTrue(result["requiresConfirmation"])
        self.assertGreaterEqual(len(result["missingConfiguration"]), 1)
        self.assertEqual(
            [step["id"] for step in result["steps"]],
            ["security", "cost", "fund", "bridge", "swap", "track"],
        )
        self.assertIn("securityCheck", result)
        self.assertIn("costEstimate", result)
        self.assertIn("recommendedSlippagePct", result["costEstimate"])
        self.assertIn("minimumReceivedUsdEquivalent", result["costEstimate"])

    def test_news_trade_security_hard_blocks_honeypot_and_extreme_tax(self):
        security = server.news_trade_finalize_security({
            "verified": True,
            "provider": "fixture",
            "checkedAt": self.now_ms,
            "isHoneypot": True,
            "canSell": False,
            "buyTaxPct": 25,
            "sellTaxPct": 30,
        })

        self.assertTrue(security["hardBlocked"])
        self.assertEqual(security["status"], "danger")
        self.assertEqual(security["label"], "禁止买入")
        self.assertTrue(any("蜜罐" in reason for reason in security["hardBlockReasons"]))
        self.assertTrue(any("卖出" in reason for reason in security["hardBlockReasons"]))

    def test_news_trade_cost_estimate_warns_and_blocks_excessive_impact(self):
        estimate = server.news_trade_execution_cost_estimate(
            5000,
            {"chainId": "56", "liquidityUsd": 20_000},
            {"buyTaxPct": 6, "poolFeePct": 0.3},
            {"chainMatches": False},
        )

        self.assertTrue(estimate["blocked"])
        self.assertGreater(estimate["priceImpactPct"], 3)
        self.assertGreater(estimate["recommendedSlippagePct"], 5)
        self.assertLess(estimate["minimumReceivedUsdEquivalent"], 5000)
        self.assertTrue(estimate["blockingReasons"])

    def test_okx_wallet_authorization_supplies_client_signer_and_target_chain(self):
        with patch.object(server, "event_monitor_dex_pair", return_value={}):
            event = self.classify(
                title="BSC上Meme币「牛来」市值突破2600万美元，成交量放大",
                body="BNB Chain Meme热点快速传播。",
                url="https://gmgn.ai/bsc/token/i_hot_0xBEEA1D618e533a387D941F58a7d4c9b7bD377777",
            )
        with patch.dict("os.environ", {}, clear=True):
            result = server.prepare_news_trade_execution(
                {
                    "eventId": event["id"],
                    "amountUsdt": 100,
                    "walletProvider": "okx",
                    "walletNamespace": "evm",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "walletChainId": "0x38",
                },
                events=[event],
            )

        self.assertTrue(result["walletAuthorization"]["authorized"])
        self.assertTrue(result["walletAuthorization"]["chainMatches"])
        self.assertNotIn("签名钱包", result["missingConfiguration"])
        self.assertEqual(next(step for step in result["steps"] if step["id"] == "swap")["status"], "ready")
        self.assertFalse(result["liveExecution"])

    def test_binance_wallet_authorization_uses_the_same_evm_execution_path(self):
        authorization = server.news_trade_wallet_authorization(
            {
                "walletProvider": "binance",
                "walletNamespace": "evm",
                "walletAddress": "0x2222222222222222222222222222222222222222",
                "walletChainId": "0x38",
            },
            {"chain": "bsc", "chainId": "56"},
        )

        self.assertEqual(authorization["provider"], "binance")
        self.assertTrue(authorization["authorized"])
        self.assertTrue(authorization["chainMatches"])

    def test_generic_market_commentary_is_ignored(self):
        event = self.classify(
            title="市场今日整体平稳",
            body="项目保持正常运营，暂无新的公告。",
        )

        self.assertIsNone(event)

    def test_model_article_with_uppercase_terms_is_ignored(self):
        event = self.classify(
            title="DeepSeek V4 Flash 通过模型评测",
            body="HARNESS 与 Claude Code Agent 的能力对比。",
        )

        self.assertIsNone(event)

    def test_generic_policy_rate_article_is_ignored(self):
        event = self.classify(
            title="美联储继续讨论政策利率",
            body="CPI 数据公布后，市场关注政策路径与 BTC 价格。",
        )

        self.assertIsNone(event)

    def test_news_article_mentioning_a_post_is_not_kol_latency(self):
        event = self.classify(
            title="分析师发文讨论 BTC 走势",
            body="文章回顾 BTCUSDT 成交量，没有新的官方确认。",
        )

        self.assertIsNone(event)

    def test_ordinary_x_commentary_is_not_event_driven(self):
        event = self.classify(
            sourceType="x-kol",
            source="Trader",
            sourceLabel="X",
            title="从 TUT 的走势来看，不要轻易做空妖币",
            body="$TUT 今天波动很大。",
        )

        self.assertIsNone(event)

    def test_normal_basis_commentary_is_ignored(self):
        event = self.classify(
            title="CryptoQuant 观察 BTC 基差",
            body="BTCUSDT 基差和资金费率保持正常区间。",
        )

        self.assertIsNone(event)

    def test_newsflash_cannot_impersonate_listing_source(self):
        event = self.classify(
            title="某基金上线新的研究报告",
            body="报告新增 HYPEUSDT 合约市场章节。",
        )

        self.assertIsNone(event)

    def test_parser_only_emits_early_news_trade_transition_with_priority_and_speech(self):
        topic = {
            "id": "topic-test",
            "title": "《TEST》热点进入早期传播",
            "topicKey": "narrative:test",
            "eventEntities": ["TEST"],
            "assets": ["TEST"],
            "templateName": "反常识文化事件 / 负面共识破圈",
            "newsTradePhase": "understanding",
            "eventStage": "accelerating",
            "sourceActive": True,
            "fullyFermented": False,
            "topicScore": 60,
            "onchainTradeScore": 40,
            "candidateTier": "event-observation",
            "topicMetrics": {"velocityScore": 30, "platformCount": 1},
            "latestCatalyst": {"storyBeat": "breakout", "timestamp": self.now_ms},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "news_trade_alert_state.json"
            with patch.object(server, "NEWS_TRADE_ALERT_STATE_PATH", state_path):
                baseline = server.parse_site_event_monitor_events({"updatedAt": self.now_ms, "newsTrades": [topic]})
                alerts = server.parse_site_event_monitor_events({
                    "updatedAt": self.now_ms + 60_000,
                    "newsTrades": [{
                        **topic,
                        "topicMetrics": {"velocityScore": 35, "platformCount": 2},
                        "updatedAt": self.now_ms + 60_000,
                    }],
                })

        self.assertEqual(baseline, [])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["queuePriority"], 70)
        self.assertIn("News Trade 早期提醒", alerts[0]["speech"])
        self.assertIn("多个平台", alerts[0]["body"])

    def test_ended_or_old_news_trade_confirmation_never_alerts(self):
        topic = {
            "id": "topic-ended",
            "title": "《结束事件》结果确认",
            "eventEntities": ["结束事件"],
            "newsTradePhase": "understanding",
            "eventStage": "accelerating",
            "sourceActive": True,
            "topicScore": 50,
            "topicMetrics": {"velocityScore": 20, "platformCount": 1},
            "latestCatalyst": {"storyBeat": "update", "timestamp": self.now_ms},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "alert-state.json"
            server.news_trade_transition_alert_topics([topic], now_ms=self.now_ms, state_path=state_path)
            alerts = server.news_trade_transition_alert_topics(
                [{
                    **topic,
                    "newsTradePhase": "expired",
                    "eventStage": "decline",
                    "topicMetrics": {"velocityScore": 90, "platformCount": 3},
                }],
                now_ms=self.now_ms + 60_000,
                state_path=state_path,
            )

        self.assertEqual(alerts, [])

    def test_military_topic_without_explicit_onchain_response_never_alerts(self):
        topic = {
            "id": "topic-military",
            "title": "军事冲突消息",
            "eventEntities": ["军事冲突"],
            "newsTradePhase": "understanding",
            "eventStage": "accelerating",
            "sourceActive": True,
            "politicalMilitary": True,
            "explicitCryptoMapping": False,
            "onchainTradeScore": 0,
            "topicMetrics": {"velocityScore": 20, "platformCount": 1},
            "latestCatalyst": {"storyBeat": "update", "timestamp": self.now_ms},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "alert-state.json"
            server.news_trade_transition_alert_topics([topic], now_ms=self.now_ms, state_path=state_path)
            alerts = server.news_trade_transition_alert_topics(
                [{**topic, "topicMetrics": {"velocityScore": 90, "platformCount": 3}}],
                now_ms=self.now_ms + 60_000,
                state_path=state_path,
            )

        self.assertEqual(alerts, [])

    def test_event_identity_does_not_change_when_timestamp_changes(self):
        first = self.classify(
            sourceType="listing",
            source="Binance",
            title="Binance 宣布上线 ACTUSDT 永续合约",
            symbol="ACTUSDT",
            timestamp=self.now_ms,
        )
        second = self.classify(
            sourceType="listing",
            source="Binance",
            title="Binance 宣布上线 ACTUSDT 永续合约",
            symbol="ACTUSDT",
            timestamp=self.now_ms + 30_000,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["id"], second["id"])

    def test_future_listing_uses_discovery_time_for_alerting(self):
        observed_at = self.now_ms
        scheduled_at = observed_at + 7 * 24 * 60 * 60 * 1000
        listing_payload = {
            "updatedAt": observed_at,
            "sections": [
                {
                    "id": "exchange-listings",
                    "sourceName": "Aster",
                    "rows": [
                        {
                            "id": "aster-test",
                            "source": "Aster",
                            "title": "Aster 宣布上线 TESTUSDT 永续合约",
                            "symbol": "TESTUSDT",
                            "date": scheduled_at,
                        }
                    ],
                }
            ],
        }

        def fake_cache(path):
            if path.name == "api_listing-events-v3.json":
                return listing_payload
            return {"items": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(server, "PERSIST_CACHE_DIR", Path(temp_dir)):
                with patch.object(server, "read_json_cache", side_effect=fake_cache):
                    rows = server.event_monitor_source_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], observed_at)
        self.assertEqual(rows[0]["scheduledAt"], scheduled_at)

    def test_stock_ipo_section_is_not_treated_as_crypto_news_trade(self):
        listing_payload = {
            "updatedAt": self.now_ms,
            "sections": [
                {
                    "id": "ipo-calendar",
                    "sourceName": "Nasdaq IPO calendar",
                    "rows": [
                        {
                            "id": "nasdaq-test",
                            "source": "Nasdaq",
                            "title": "LEADERS ADVANTAGE ACQUISITION CORP UNIT 1 CL A",
                            "symbol": "LEDRU",
                            "date": self.now_ms,
                        }
                    ],
                }
            ],
        }

        def fake_cache(path):
            if path.name == "api_listing-events-v3.json":
                return listing_payload
            return {"items": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(server, "PERSIST_CACHE_DIR", Path(temp_dir)):
                with patch.object(server, "read_json_cache", side_effect=fake_cache):
                    rows = server.event_monitor_source_rows()

        self.assertEqual(rows, [])

    def test_buy_preparation_uses_the_candidate_selected_from_the_topic_card(self):
        primary = {
            "symbol": "PRIMARY",
            "contractAddress": "0x1111111111111111111111111111111111111111",
            "chain": "bsc",
            "chainId": "56",
            "chainLabel": "BSC",
            "tradeReady": True,
            "tradeUrls": {"OKX DEX": "https://example.com/primary"},
        }
        selected = {
            "symbol": "SELECTED",
            "contractAddress": "0x2222222222222222222222222222222222222222",
            "chain": "bsc",
            "chainId": "56",
            "chainLabel": "BSC",
            "tradeReady": True,
            "tradeUrls": {"OKX DEX": "https://example.com/selected"},
        }
        event = {
            "id": "topic:selected-candidate",
            "executionEligible": True,
            "memeOpportunity": primary,
            "memeCandidates": [primary, selected],
        }
        payload = {
            "eventId": event["id"],
            "amountUsdt": 80,
            "candidateContract": selected["contractAddress"],
            "candidateChain": "bsc",
            "walletProvider": "okx",
            "walletNamespace": "evm",
            "walletAddress": "0x3333333333333333333333333333333333333333",
            "walletChainId": "0x38",
        }
        readiness = {
            "configured": True,
            "liveEnabled": False,
            "requiresConfirmation": True,
            "missingConfiguration": [],
            "maxOrderUsdt": 200,
        }

        with patch.object(server, "news_trade_execution_readiness", return_value=readiness):
            prepared = server.prepare_news_trade_execution(payload, events=[event])

        self.assertEqual(prepared["opportunity"]["symbol"], "SELECTED")
        self.assertEqual(prepared["opportunity"]["contractAddress"], selected["contractAddress"])
        self.assertTrue(prepared["walletAuthorization"]["chainMatches"])

    def test_manual_buy_intent_remains_available_after_system_recommendation_window(self):
        candidate = {
            "symbol": "REVIEW",
            "contractAddress": "0x4444444444444444444444444444444444444444",
            "chain": "bsc",
            "chainId": "56",
            "chainLabel": "BSC",
            "tradeReady": True,
            "tradeUrls": {"OKX DEX": "https://example.com/review"},
        }
        event = {
            "id": "topic:review",
            "executionEligible": False,
            "newsTradePhaseLabel": "已发酵 · 仅复盘",
            "memeOpportunity": candidate,
            "memeCandidates": [candidate],
        }
        payload = {
            "eventId": event["id"],
            "amountUsdt": 20,
            "manualIntent": True,
            "candidateContract": candidate["contractAddress"],
            "candidateChain": "bsc",
            "walletProvider": "okx",
            "walletNamespace": "evm",
            "walletAddress": "0x5555555555555555555555555555555555555555",
            "walletChainId": "0x38",
        }
        readiness = {
            "configured": True,
            "liveEnabled": False,
            "requiresConfirmation": True,
            "missingConfiguration": [],
            "maxOrderUsdt": 200,
        }

        with patch.object(server, "news_trade_execution_readiness", return_value=readiness):
            prepared = server.prepare_news_trade_execution(payload, events=[event])

        self.assertFalse(prepared["systemRecommended"])
        self.assertTrue(prepared["manualIntent"])
        self.assertEqual(prepared["opportunity"]["symbol"], "REVIEW")


if __name__ == "__main__":
    unittest.main()
