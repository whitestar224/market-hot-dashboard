import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
import tests.test_event_flow as fixtures
from alert_delivery import AlertDeliveryStore
from event_flow_window import attention_evidence, attention_window, HOUR
from listing_alerts import REQUIRED_SOURCES, attach_inventory, observe_listings


class SemanticOpportunityTests(fixtures.NewsTradeDeliveryTests):
    def emit(self, topic, now=None):
        return server.parse_site_event_monitor_events({"updatedAt": now or self.now, "newsTrades": [topic]}, completed=True)

    def test_rephrasing_confidence_tags_topic_id_and_refresh_are_not_new_opportunities(self):
        topic = self.topic()
        first = self.emit(topic)
        self.assertEqual(len(first), 1)
        for index in range(1, 5):
            changed = copy.deepcopy(topic)
            changed.update(id=f"changed-{index}", aiAnalysisUpdatedAt=self.now + index * 1000)
            changed["aiAnalysis"].update(confidence=80 + index, thesis=f"另一种叙述 {index}", tags=[str(index)])
            self.assertEqual(self.emit(changed, self.now + index * 1000), [])
        self.assertEqual(server.read_json_cache(server.NEWS_TRADE_ALERT_STATE_PATH)["version"], 2)

    def test_chain_aliases_and_contract_case_do_not_split_the_same_asset(self):
        topic = self.topic()
        topic["memeCandidates"] = [{"symbol": "ALPHA", "chain": "BSC", "contractAddress": "0xABCDEF"}]
        self.assertEqual(len(self.emit(topic)), 1)
        topic["memeCandidates"][0].update(chain="56", contractAddress="0xabcdef")
        topic["aiAnalysis"].update(thesis="新的说法")
        self.assertEqual(self.emit(topic), [])

    def test_new_external_catalyst_after_alert_can_notify_again_not_a_permanent_symbol_mute(self):
        topic = self.topic()
        first = self.emit(topic)
        later = self.now + 60_000
        new = copy.deepcopy(topic)
        new.update(aiAnalysisUpdatedAt=later)
        new["relatedNews"].append({"title": "创始人刚公布全新的参与规则", "timestamp": later - 1000})
        evidence = attention_evidence(new)[0]
        new["aiAnalysis"].update(attentionStage="ignition", catalystEvidenceId=evidence["id"])
        second = self.emit(new, later)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]["key"], second[0]["key"])
        self.assertEqual(self.emit(new, later + 1000), [])

    def test_legacy_migration_does_not_replay_an_already_announced_asset(self):
        topic = self.topic()
        identity = server.news_trade_topic_identity(topic)
        server.write_json_cache(server.NEWS_TRADE_ALERT_STATE_PATH, {"topics": {identity: {
            **server.news_trade_alert_snapshot(topic), "lastAiAlertAt": self.now - 1000, "lastSeenAt": self.now}}})
        topic["aiAnalysis"]["thesis"] = "分数和措辞都更新了"
        self.assertEqual(self.emit(topic), [])

    def test_brew_like_old_catalyst_cooling_and_dump_are_not_popup_opportunities(self):
        for mode in ("old", "cooling", "selloff"):
            topic = self.topic(key=mode)
            if mode == "old":
                topic.update(timestamp=self.now - 30 * HOUR, relatedNews=[])
            elif mode == "cooling":
                topic["aiAnalysis"].update(attentionStage="cooling")
            else:
                topic["memeCandidates"] = [{"symbol": "ALPHA", "change24hPercent": -65.66}]
            self.assertEqual(self.emit(topic), [], mode)

    def test_dump_can_reignite_only_with_new_evidence_and_both_short_windows_strengthening(self):
        topic = self.topic()
        topic["memeCandidates"] = [{"symbol": "ALPHA", "change24hPercent": -65,
                                    "priceChange": {"h1": 12, "m5": 3}}]
        analysis = server.normalize_news_trade_ai_analysis(topic["aiAnalysis"])
        self.assertTrue(attention_window(topic, analysis, now_ms=self.now, opportunity=True)["eligible"])
        self.assertEqual(len(self.emit(topic)), 1)

    def test_listing_announcements_do_not_bypass_the_first_listing_owner_via_ai(self):
        topic = self.topic()
        topic["template"] = "listing-latency"
        self.assertEqual(self.emit(topic), [])

    def test_window_withdrawn_while_queued_is_checked_again_at_delivery(self):
        topic = self.topic()
        server.record_event_flow_news([topic])
        popup = self.emit(topic)[0]
        self.assertFalse(server.desktop_alert_source_is_muted(popup))
        topic["aiAnalysis"].update(attentionStage="cooling")
        server.record_event_flow_news([topic])
        self.assertTrue(server.desktop_alert_source_is_muted(popup))


class DeliveryPreferenceTests(unittest.TestCase):
    def test_onchain_leaders_are_muted_and_only_binance_okx_secondary_leaders_remain(self):
        onchain_leaders = [
            {
                "sourceId": "okx-dex-gainers", "source": "OKX DEX 24h 涨幅榜", "sourceLabel": "DEX",
                "kind": "涨幅榜异动", "title": "DEX 涨幅榜榜首变为 LONGCAT",
            },
            {
                "sourceId": "binance-wallet-hot", "source": "币安钱包热门榜", "sourceLabel": "BW",
                "kind": "热门榜新进", "title": "热门榜新进：MOON", "body": "排名 #1",
            },
            {
                "sourceId": "ave", "source": "AVE.ai 热门榜", "sourceLabel": "AVE",
                "kind": "榜首换手", "title": "榜首变为 TEST",
            },
            {
                "sourceId": "gmgn-hot-search", "source": "GMGN 链上热搜榜", "sourceLabel": "GMGN",
                "kind": "榜首换手", "title": "榜首变为 CAT",
            },
        ]
        for item in onchain_leaders:
            with self.subTest(source=item["sourceId"]):
                self.assertTrue(server.desktop_alert_source_is_muted(item))

        self.assertFalse(server.desktop_alert_source_is_muted({
            "sourceId": "binance-gainers", "source": "Binance 涨幅榜", "sourceLabel": "BN",
            "kind": "涨幅榜异动", "title": "BN 涨幅榜榜首变为 TEST",
        }))
        self.assertFalse(server.desktop_alert_source_is_muted({
            "sourceId": "okx-gainers", "source": "OKX 涨幅榜", "sourceLabel": "OK",
            "kind": "涨幅榜异动", "title": "OK 涨幅榜榜首变为 TEST",
        }))
        for source_id, source_label, source in (
            ("bitget-gainers", "BG", "Bitget 涨幅榜"),
            ("aicoin", "AI", "AICoin 热门榜"),
        ):
            with self.subTest(source=source_id):
                self.assertTrue(server.desktop_alert_source_is_muted({
                    "sourceId": source_id, "source": source, "sourceLabel": source_label,
                    "kind": "榜首换手", "title": f"{source_label} 榜首变为 TEST",
                }))
        self.assertFalse(server.desktop_alert_source_is_muted({
            "sourceId": "binance-wallet-hot", "source": "币安钱包热门榜", "sourceLabel": "BW",
            "alertPeriod": "4h", "kind": "热门榜新进", "title": "热门榜新进：MOON", "body": "排名 #2",
        }))

    def test_onchain_leaders_are_not_generated_but_rank_payloads_stay_intact(self):
        market_payload = {"sources": [
            {
                "id": "okx-dex", "group": "crypto", "title": "OKX DEX 24小时热门榜", "sourceLabel": "DEX",
                "rows": [{"rank": 1, "symbol": "LONGCAT", "change": "+19074.90%"}],
            },
            {
                "id": "binance", "group": "crypto", "title": "Binance 热门榜", "sourceLabel": "BN",
                "rows": [{"rank": 1, "symbol": "BTC", "change": "+2.00%"}],
            },
            {
                "id": "okx", "group": "crypto", "title": "OKX 合约热门榜", "sourceLabel": "OK",
                "rows": [{"rank": 1, "symbol": "ETH", "change": "+3.00%"}],
            },
            {
                "id": "bitget", "group": "crypto", "title": "Bitget 热门榜", "sourceLabel": "BG",
                "rows": [{"rank": 1, "symbol": "SOL", "change": "+4.00%"}],
            },
        ]}
        gainer_payload = {"sources": [
            {
                "id": "okx-dex-gainers", "group": "crypto", "title": "OKX DEX 24h 涨幅榜", "sourceLabel": "DEX",
                "rows": [{"rank": 1, "symbol": "LONGCAT", "change": "+19074.90%"}],
            },
            {
                "id": "binance-gainers", "group": "crypto", "title": "Binance 涨幅榜", "sourceLabel": "BN",
                "rows": [{"rank": 1, "symbol": "TEST", "change": "+20.00%"}],
            },
            {
                "id": "okx-gainers", "group": "crypto", "title": "OKX 涨幅榜", "sourceLabel": "OK",
                "rows": [{"rank": 1, "symbol": "MOON", "change": "+18.00%"}],
            },
            {
                "id": "bitget-gainers", "group": "crypto", "title": "Bitget 涨幅榜", "sourceLabel": "BG",
                "rows": [{"rank": 1, "symbol": "CAT", "change": "+16.00%"}],
            },
        ]}

        self.assertEqual([event["sourceLabel"] for event in server.parse_site_market_events(market_payload)], ["BN", "OK"])
        self.assertEqual([event["sourceLabel"] for event in server.parse_site_gainers_events(gainer_payload)], ["BN", "OK"])
        self.assertEqual(market_payload["sources"][0]["rows"][0]["symbol"], "LONGCAT")
        self.assertEqual(gainer_payload["sources"][0]["rows"][0]["symbol"], "LONGCAT")

    def test_muted_leader_is_rejected_before_desktop_delivery(self):
        with (
            patch.object(server, "env_flag", return_value=False),
            patch.object(server, "ingest_smart_money_text"),
            patch.object(server.ALERT_DELIVERY_STORE, "admit") as admit,
        ):
            result = server.launch_desktop_alert({
                "key": "gainers-leader:bitget-gainers:CAT",
                "sourceId": "bitget-gainers", "source": "Bitget 涨幅榜", "sourceLabel": "BG",
                "kind": "涨幅榜异动", "title": "BG 涨幅榜榜首变为 CAT",
            })
        self.assertTrue(result["skipped"])
        self.assertEqual(result["category"], "muted-source")
        admit.assert_not_called()

    def test_stock_rank_and_company_popups_are_muted_without_muting_crypto_information(self):
        row = {
            "key": "futu-hk:HK:00992",
            "assetKey": "HK:00992",
            "sourceId": "futu-hk",
            "sourceTitle": "富途港股热门榜",
            "sourceLabel": "HK",
            "group": "hk",
            "symbol": "联想集团",
            "name": "联想集团",
            "rank": 10,
            "price": "32.460",
            "change": 6.92,
            "note": "交易热度 18",
        }
        event = server.rank_monitor_event("hot", row, "new")

        self.assertEqual(event["title"], "港股热门榜新进：联想集团")
        self.assertTrue(server.desktop_alert_source_is_muted(event))
        self.assertTrue(server.desktop_alert_source_is_muted({"kind": "港股热门榜排名更新"}))
        self.assertTrue(server.desktop_alert_source_is_muted({"title": "港股热门榜新进：测试"}))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "futu-us",
            "source": "富途美股热门榜",
            "kind": "美股热门榜新进",
            "title": "美股热门榜新进：英伟达",
        }))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "ths-cn",
            "source": "A股同花顺24h热门榜",
            "kind": "A股热门榜新进",
            "title": "A股热门榜新进：测试公司",
        }))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "futu-hk-gainers",
            "source": "富途港股涨幅榜",
            "sourceLabel": "HK",
            "kind": "涨幅异动",
            "title": "联想集团涨幅扩大",
        }))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "hkex",
            "source": "港交所",
            "sourceLabel": "HK",
            "kind": "公司公告",
            "title": "联想集团发布业绩公告",
        }))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "futu-us-turnover",
            "source": "富途美股成交额榜",
            "sourceLabel": "US",
            "kind": "榜首换手",
            "title": "US 成交额榜榜首变为英伟达",
        }))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "cn-stock-gainers",
            "source": "A股涨幅榜",
            "sourceLabel": "CN",
            "kind": "涨幅榜异动",
            "title": "CN 涨幅榜榜首变为测试公司",
        }))
        self.assertFalse(server.desktop_alert_source_is_muted({
            "sourceId": "binance-gainers",
            "source": "Binance 涨幅榜",
            "sourceLabel": "BN",
            "kind": "涨幅榜异动",
            "title": "BN 涨幅榜榜首变为 TEST",
        }))
        self.assertFalse(server.desktop_alert_source_is_muted({
            "sourceId": "blockbeats",
            "source": "BlockBeats 律动",
            "sourceLabel": "BB",
            "kind": "项目公告",
            "title": "某加密项目发布协议升级公告",
        }))

    def test_all_stock_ranks_are_not_watched_but_crypto_ranks_remain(self):
        hk_hot = {"sourceId": "futu-hk", "group": "hk", "rank": 1}
        us_hot = {"sourceId": "futu-us", "group": "us", "rank": 1}
        cn_hot = {"sourceId": "ths-cn", "group": "cn", "rank": 1}
        hk_turnover = {"sourceId": "futu-hk-turnover", "group": "hk", "rank": 1}
        us_turnover = {"sourceId": "futu-us-turnover", "group": "us", "rank": 1}
        crypto = {"sourceId": "binance", "group": "crypto", "rank": 1}

        self.assertEqual(server.rank_monitor_watch_rows("hot", [hk_hot, us_hot, cn_hot, crypto]), [crypto])
        self.assertEqual(
            server.rank_monitor_watch_rows("turnover", [hk_turnover, us_turnover, crypto]),
            [crypto],
        )

    def test_stock_gainer_events_are_not_generated_but_page_payload_is_unchanged(self):
        stock = {
            "id": "futu-hk-gainers",
            "group": "hk",
            "title": "富途港股涨幅榜",
            "sourceLabel": "HK",
            "rows": [{"rank": 1, "symbol": "00992", "name": "联想集团", "change": "+6.92%"}],
        }
        crypto = {
            "id": "binance-gainers",
            "group": "crypto",
            "title": "Binance 涨幅榜",
            "sourceLabel": "BN",
            "rows": [{"rank": 1, "symbol": "TEST", "name": "TESTUSDT", "change": "+20.00%"}],
        }
        payload = {"sources": [stock, crypto]}

        events = server.parse_site_gainers_events(payload)

        self.assertEqual([event["sourceLabel"] for event in events], ["BN"])
        self.assertEqual([source["id"] for source in payload["sources"]], ["futu-hk-gainers", "binance-gainers"])

    def test_24h_muted_4h_kept_and_unrelated_signals_unchanged(self):
        for period, expected in (("24h", True), ("4h", False)):
            raw = {"key": "rank-monitor:wallet", "sourceId": "binance-wallet-hot",
                   "kind": "币安钱包热门榜新进", "alertPeriod": period, "title": "测试"}
            self.assertEqual(server.desktop_alert_source_is_muted(server.normalize_desktop_alert(raw)), expected)
        self.assertTrue(server.desktop_alert_source_is_muted({"kind": "币安钱包24小时热门榜新进"}))
        self.assertFalse(server.desktop_alert_source_is_muted({"kind": "币安钱包4小时热门榜新进"}))
        self.assertFalse(server.desktop_alert_source_is_muted({"key": "price-watch:ALPHA:1", "kind": "价格监控"}))
        self.assertTrue(server.desktop_alert_source_is_muted({"sourceId": "ave", "kind": "AVE热门榜新进"}))

    def test_large_exchange_hot_new_entries_are_silent_but_other_alerts_remain(self):
        for source_id in ("binance", "okx", "bitget", "aicoin"):
            row = {"sourceId": source_id, "kind": "热门榜新进", "title": "热门榜新进：TEST"}
            self.assertTrue(server.rank_monitor_hot_new_is_silent(row))
            self.assertTrue(server.desktop_alert_source_is_muted(row))
            self.assertFalse(server.desktop_alert_source_is_muted({
                "sourceId": source_id,
                "kind": "成交额榜新进",
                "title": "成交额榜新进：TEST",
            }))
        self.assertTrue(server.rank_monitor_hot_new_is_silent({
            "sourceId": "gate-hot",
            "sourceTitle": "Gate 热门榜",
        }))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "gate-hot",
            "source": "Gate 热门榜",
            "kind": "热门榜新进",
            "title": "热门榜新进：TEST",
        }))
        for source_id, source in (
            ("binance-wallet-hot", "币安钱包热门榜"),
            ("okx-dex", "OKX DEX 24小时热门榜"),
        ):
            row = {"sourceId": source_id, "source": source, "kind": "热门榜新进"}
            self.assertFalse(server.rank_monitor_hot_new_is_silent(row))
        gmgn = {"sourceId": "gmgn-hot-search", "source": "GMGN 热搜榜", "kind": "热门榜新进"}
        self.assertTrue(server.rank_monitor_hot_new_is_silent(gmgn))
        self.assertTrue(server.desktop_alert_source_is_muted(gmgn))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceId": "gmgn-hot-search",
            "kind": "GMGN 数据更新",
            "title": "这条已排队消息也不应播报",
            "speech": "这条语音不应播放",
        }))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "sourceLabel": "GMGN",
            "source": "GMGN 战壕新币榜",
            "kind": "热门榜新进",
            "title": "热门榜新进：GLDX",
        }))
        gmgn_alias = {
            "sourceId": "GMGN-HOT",
            "source": "GMGN 热门榜",
            "kind": "热门榜新进",
            "title": "热门榜新进：ALIAS",
        }
        self.assertTrue(server.gmgn_hot_rank_source(gmgn_alias))
        self.assertTrue(server.rank_monitor_hot_new_is_silent(gmgn_alias))
        self.assertTrue(server.desktop_alert_source_is_muted(gmgn_alias))
        self.assertEqual(
            server.rank_monitor_watch_rows(
                "hot",
                [{"sourceId": "GMGN-HOT", "assetKey": "CRYPTO:ALIAS", "rank": 1}],
            ),
            [],
        )
        ave = {"sourceId": "ave", "source": "AVE.ai 热搜榜", "kind": "热门榜新进"}
        self.assertTrue(server.rank_monitor_hot_new_is_silent(ave))
        self.assertTrue(server.desktop_alert_source_is_muted(ave))
        self.assertTrue(server.desktop_alert_source_is_muted({
            "source": "AVE.ai 热门榜",
            "kind": "热门榜新进",
            "title": "AVE.ai热门榜新进：TEST",
        }))

    def test_queued_ave_hot_entry_cannot_launch_popup_or_speech(self):
        event = {
            "key": "rank-monitor:hot|ave:test|new",
            "sourceId": "ave",
            "source": "AVE.ai 热搜榜",
            "kind": "AVE.ai热门榜新进",
            "title": "AVE.ai热门榜新进：TEST",
            "speech": "这条语音不应播放",
        }
        with patch.object(server, "env_flag", return_value=False), patch.object(
            server, "ingest_smart_money_text"
        ), patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "ensure_desktop_alert_worker"
        ) as worker:
            result = server.launch_desktop_alert(event)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["category"], "muted-source")
        worker.assert_not_called()

    def test_queued_gmgn_hot_search_event_cannot_launch_popup_or_speech(self):
        event = {
            "key": "rank-monitor:hot|gmgn-hot-search:test|new",
            "sourceId": "gmgn-hot-search",
            "source": "GMGN 热搜榜",
            "kind": "GMGN 热搜榜新进",
            "title": "GMGN 热搜榜新进：TEST",
            "speech": "这条语音不应播放",
        }
        with patch.object(server, "env_flag", return_value=False), patch.object(
            server, "ingest_smart_money_text"
        ), patch.object(server, "price_structure_symbol_excluded", return_value=False), patch.object(
            server, "ensure_desktop_alert_worker"
        ) as worker:
            result = server.launch_desktop_alert(event)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["category"], "muted-source")
        worker.assert_not_called()

    def test_mapping_updates_and_new_leaders_are_muted_at_admission_and_delivery(self):
        self.assertTrue(server.desktop_alert_source_is_muted({"key": "rotation-map-change:AAA"}))
        self.assertTrue(server.desktop_alert_source_is_muted({"key": "rotation-new-leader:AAA"}))
        self.assertTrue(server.desktop_alert_source_is_muted({"kind": "补涨映射 · 新龙头"}))
        self.assertTrue(server.desktop_alert_source_is_muted({"sourceId":"rotation-map", "title":"新龙头入池：AAA"}))
        self.assertFalse(server.desktop_alert_source_is_muted({"key":"price-watch:AAA", "kind":"价格监控"}))
        with patch.object(server,'env_flag',return_value=False), \
             patch.object(server,'price_structure_symbol_excluded',return_value=False), \
             patch.object(server,'ensure_desktop_alert_worker') as worker:
            result=server.launch_desktop_alert({'key':'rotation-new-leader:AAA', 'kind':'补涨映射 · 新龙头',
                                              'speech':'不应播出的内容','title':'AAA'})
        self.assertTrue(result['skipped'])
        worker.assert_not_called()

    def test_old_ai_and_unverified_listing_queue_messages_cannot_bypass_new_policy(self):
        self.assertTrue(server.desktop_alert_source_is_muted({"key": "news-trade-ai:old"}))
        self.assertTrue(server.desktop_alert_source_is_muted({"key": "newboard:hyperliquid-new:OLD", "kind": "新币上新"}))
        event = {"key": "news-trade-ai:new", "opportunityPolicyVersion": 2, "expiresAt": int(time.time()*1000) + 60000}
        self.assertFalse(server.desktop_alert_source_is_muted(server.normalize_desktop_alert({**event, "title": "机会"})))
        self.assertTrue(server.desktop_alert_source_is_muted({**event, "expiresAt": 1}))
        self.assertTrue(server.desktop_alert_source_is_muted({"key": "newsflash:abc", "title": "Binance 将上线 OLD 永续合约"}))
        self.assertFalse(server.desktop_alert_source_is_muted({"key": "newsflash:def", "title": "Binance 钱包用户增长"}))

    def test_excluded_cp_active_window_closes_without_marking_user_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AlertDeliveryStore(Path(directory) / "delivery.sqlite")
            now = time.time()
            payload = {"key": "price-watch:structure-first:CP:1h:case", "excludeSymbol": "CP"}
            identity = store.admit(payload, [payload["key"]], 100, now=now)["deliveryId"]
            lease = store.lease(identity, now=now)
            store.receipt(identity, lease["token"], "visible", visible_ms=5000, now=now)
            process = SimpleNamespace(poll=lambda: None, terminate=Mock())
            with patch.object(server, "ALERT_DELIVERY_STORE", store), \
                 patch.dict(server.DESKTOP_ALERT_DELIVERIES, {identity: {"process": process, "slot": 0, "token": lease["token"]}}, clear=True), \
                 patch.object(server, "price_structure_symbol_excluded", return_value=True), \
                 patch.object(server, "record_event_flow_popup"):
                server.desktop_alert_delivery_tick()
            self.assertEqual(store.get(identity)["state"], "suppressed")
            self.assertEqual(store.get(identity)["read_at"], 0)
            process.terminate.assert_called_once()

    def test_muted_gmgn_popup_is_closed_if_it_was_already_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AlertDeliveryStore(Path(directory) / "delivery.sqlite")
            now = time.time()
            payload = {
                "key": "rank-monitor:hot|gmgn:test|new",
                "sourceLabel": "GMGN",
                "source": "GMGN 战壕新币榜",
                "kind": "热门榜新进",
                "title": "热门榜新进：GLDX",
            }
            identity = store.admit(payload, [payload["key"]], 100, now=now)["deliveryId"]
            lease = store.lease(identity, now=now)
            store.receipt(identity, lease["token"], "visible", visible_ms=5000, now=now)
            process = SimpleNamespace(poll=lambda: None, terminate=Mock())
            with patch.object(server, "ALERT_DELIVERY_STORE", store), \
                 patch.dict(server.DESKTOP_ALERT_DELIVERIES, {identity: {"process": process, "slot": 0, "token": lease["token"]}}, clear=True), \
                 patch.object(server, "record_event_flow_popup"):
                server.desktop_alert_delivery_tick()
            self.assertEqual(store.get(identity)["state"], "suppressed")
            process.terminate.assert_called_once()

    def test_wallet_period_switch_silently_baselines_before_first_4h_entry(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, "BINANCE_WALLET_HOT_ALERT_STATE_PATH", Path(directory) / "wallet.json"), \
             patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            source = {"id": "binance-wallet-hot", "period": "4h", "periodLabel": "4 小时", "status": "ok",
                      "rows": [{"symbol": "OLD", "rank": 1}]}
            server.write_json_cache(server.BINANCE_WALLET_HOT_ALERT_STATE_PATH,
                                    {"version": 1, "ready": True, "period": "24h", "membership": []})
            self.assertEqual(server.sync_binance_wallet_hot_alert_feed(source), [])
            self.assertEqual(server.sync_binance_wallet_hot_alert_feed({**source, "period": "24h"}), [])
            source["rows"].append({"symbol": "FRESH", "rank": 2})
            events = server.sync_binance_wallet_hot_alert_feed(source)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["alertPeriod"], "4h")
            launch.assert_called_once()


class FirstListingTests(unittest.TestCase):
    now = 1788860000000

    def sources(self):
        return [attach_inventory({"id": source, "status": "ok", "rows": []}, [("EXISTING", self.now - HOUR)])
                for source in sorted(REQUIRED_SOURCES)]

    def add(self, sources, source, symbol="FRESH", stamp=None):
        row = next(row for row in sources if row["id"] == source)
        stamp = self.now + 1000 if stamp is None else stamp
        row["listingInventory"].append({"asset": symbol, "listedAt": stamp})
        row["rows"].append({"asset": symbol, "symbol": symbol+"USDT", "date": stamp})

    def test_baseline_silent_then_first_cross_venue_listing_has_stable_retry_proof(self):
        sources = self.sources()
        state, proofs = observe_listings({}, sources, now_ms=self.now)
        self.assertEqual(proofs, {})
        self.add(sources, "binance-new")
        state, proofs = observe_listings(state, sources, now_ms=self.now + 2000)
        self.assertEqual(set(proofs), {"FRESH"})
        key = proofs["FRESH"]["key"]
        _, retry = observe_listings(state, sources, now_ms=self.now + 3000)
        self.assertEqual(retry["FRESH"]["key"], key)

    def test_existing_on_spot_or_history_or_delisted_stays_silent_on_later_venue(self):
        for mode in ("spot", "history", "delisted"):
            sources = self.sources()
            self.add(sources, "okx-spot-inventory", "OLD", self.now - HOUR)
            state, _ = observe_listings({}, sources, now_ms=self.now)
            sources = self.sources()
            self.add(sources, "hyperliquid-new", "OLD")
            historical = [{"symbol": "OLD", "newCoinFirstListedAt": self.now - HOUR}] if mode == "history" else []
            if mode == "history":
                state["seen"].pop("OLD")
            _, proofs = observe_listings(state, sources, now_ms=self.now + 2000, historical=historical)
            self.assertEqual(proofs, {}, mode)

    def test_incomplete_inventory_or_unknown_dates_never_prove_first_listing(self):
        for mode in ("missing", "slice", "unknown", "earlier"):
            sources = self.sources()
            state, _ = observe_listings({}, sources, now_ms=self.now)
            self.add(sources, "binance-new")
            if mode == "missing":
                sources[0]["status"] = "unavailable"
            elif mode == "slice":
                sources[0]["listingInventoryComplete"] = False
            else:
                self.add(sources, "kucoin-spot-inventory", stamp=0 if mode == "unknown" else self.now - HOUR)
            _, proofs = observe_listings(state, sources, now_ms=self.now + 2000)
            self.assertEqual(proofs, {}, mode)

    def test_same_coin_concurrent_venues_has_one_owner_and_late_older_evidence_revokes_proof(self):
        sources = self.sources()
        state, _ = observe_listings({}, sources, now_ms=self.now)
        self.add(sources, "binance-new")
        self.add(sources, "okx-new")
        state, proofs = observe_listings(state, sources, now_ms=self.now + 2000)
        self.assertEqual(len(proofs), 1)
        self.add(sources, "kucoin-spot-inventory", stamp=0)
        _, proofs = observe_listings(state, sources, now_ms=self.now + 3000)
        self.assertEqual(proofs, {})

    def test_first_listing_owner_integrates_with_parser_without_touching_real_state(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, "FIRST_LISTING_ALERT_STATE_PATH", Path(directory) / "first.json"), \
             patch.object(server, "NEW_COIN_LOW_LISTING_HISTORY_PATH", Path(directory) / "history.json"), \
             patch.object(server, "first_listing_additional_inventories", return_value=[]):
            sources = self.sources()
            self.assertEqual(server.parse_site_newboard_events({"updatedAt": self.now, "sections": sources}, track_listings=True), [])
            self.add(sources, "binance-new")
            events = server.parse_site_newboard_events({"updatedAt": self.now + 2000, "sections": sources}, track_listings=True)
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0]["key"].startswith("first-listing:FRESH:"))
            self.assertEqual(events[0]["listingPolicyVersion"], 1)


if __name__ == "__main__":
    unittest.main()
