import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server
import desktop_alert
from event_flow import EventFlowStore, article_alias


class EventFlowTests(unittest.TestCase):
    def test_live_stream_expires_without_a_new_revision_and_keeps_archive(self):
        data = {"opportunity": True, "active": True, "window": {"eligible": True, "expiresAt": self.now + 1000}}
        event_id = self.store.record("fresh", "news", data, "opportunity", self.now)
        self.store.record("raw", "popup", {"kind": "系统通知"}, "queued", self.now)
        first = self.store.read(live=True, now_ms=self.now)
        self.assertEqual(first["total"], 1)
        self.assertEqual(first["activeIds"], [event_id])
        expired = self.store.read(live=True, after=first["cursor"], now_ms=self.now + 1000)
        self.assertEqual(expired["items"], [])
        self.assertEqual(expired["activeIds"], [])
        self.assertEqual(expired["total"], 0)
        self.assertEqual(self.store.read()["total"], 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EventFlowStore(Path(self.temp.name) / "flow.sqlite")
        self.now = int(time.time() * 1000)

    def test_persistence_dedupe_and_changed_status_preserve_one_event(self):
        first = self.store.record("news:a", "news", {"title": "原始消息", "symbol": "ALPHA"}, "news", self.now)
        baseline = self.store.read()
        self.store.record("news:a", "news", {"title": "原始消息", "symbol": "ALPHA"}, "news", self.now + 1)
        self.assertEqual(self.store.read(after=baseline["cursor"])["items"], [])
        self.store.record("news:a", "news", {"title": "原始消息", "symbol": "ALPHA", "opportunity": True, "active": True}, "opportunity", self.now + 2)
        self.store.record("news:a", "popup", {"title": "机会标的：ALPHA"}, "dispatched", self.now + 3)
        result = EventFlowStore(self.store.path).read(after=baseline["cursor"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], first)
        self.assertEqual(len(result["items"][0]["history"]), 3)
        self.assertTrue(result["items"][0]["news"]["opportunity"])
        self.assertEqual(result["items"][0]["popup"]["stage"], "dispatched")

    def test_discord_monitor_history_is_purged_without_touching_other_events(self):
        self.store.record(
            "discord:message:3", "popup",
            {"kind": "DC机会", "url": "https://discord.com/channels/1/2/3"},
            "queued", self.now,
        )
        kept = self.store.record("price:kept", "signal", {"type": "breakout"}, "signal", self.now)

        removed = self.store.purge_discord_monitor_history()
        result = self.store.read()

        self.assertEqual(removed, 1)
        self.assertEqual([item["id"] for item in result["items"]], [kept])

    def test_exact_article_merges_popup_and_news_but_coin_symbols_do_not(self):
        alias = article_alias("https://x.com/project/status/123")
        self.store.record("popup:a", "popup", {"title": "消息"}, "queued", self.now, aliases=[alias])
        self.store.record("news:a", "news", {"title": "研判", "symbol": "ALPHA"}, "analysis", self.now, aliases=[alias])
        self.store.record("news:b", "news", {"title": "另一个事件", "symbol": "ALPHA"}, "analysis", self.now)
        self.assertEqual(self.store.read()["total"], 2)
        merged = next(row for row in self.store.read()["items"] if "popup:a" in row["identities"])
        self.assertIn("news:a", merged["identities"])
        self.assertEqual(len(merged["history"]), 2)
        self.assertEqual(article_alias("./price-watch.html?mode=news"), "")
        self.assertEqual(article_alias("https://dexscreener.com/bsc/0x123"), "")

    def test_later_authoritative_topic_consolidates_existing_records_and_retains_history(self):
        self.store.record("one", "popup", {"title": "甲"}, "queued", self.now, aliases=["article:one"])
        self.store.record("two", "news", {"title": "乙"}, "news", self.now, aliases=["article:two"])
        self.store.record("topic", "news", {"title": "同一事件研判"}, "analysis", self.now, aliases=["article:one", "article:two"])
        result = self.store.read()
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"][0]["history"]), 3)
        self.assertIn("two", result["items"][0]["identities"])

    def test_cursor_does_not_skip_backlog_or_replay_initial_history(self):
        for index in range(5):
            self.store.record(str(index), "news", {"title": str(index)}, "news", self.now + index)
        first = self.store.read(limit=2)
        self.assertEqual([row["news"]["title"] for row in first["items"]], ["4", "3"])
        history = self.store.read(before=tuple(first["nextBefore"]), limit=2)
        self.assertEqual([row["news"]["title"] for row in history["items"]], ["2", "1"])
        self.assertEqual(self.store.read(after=first["cursor"])["items"], [])
        for index in range(5, 9):
            self.store.record(str(index), "news", {"title": str(index)}, "news", self.now + index)
        delta = self.store.read(after=first["cursor"], limit=2)
        more = self.store.read(after=delta["cursor"], limit=2)
        self.assertEqual(len(delta["items"] + more["items"]), 4)
        self.assertTrue(delta["hasMore"])
        self.assertFalse(more["hasMore"])

    def test_archive_and_per_event_history_are_bounded(self):
        store = EventFlowStore(self.store.path, max_events=2, history_limit=2)
        for index in range(4):
            store.record(str(index), "news", {"title": str(index)}, "news", self.now + index)
        for index in range(4):
            store.record("3", "news", {"title": str(index)}, "analysis", self.now + index)
        result = store.read()
        self.assertEqual(result["total"], 2)
        self.assertTrue(all(len(item["history"]) <= 2 for item in result["items"]))

    def test_server_filter_searches_archive_and_delta_includes_withdrawn_opportunities(self):
        self.store.record("a", "news", {"opportunity": True, "active": True}, "opportunity", self.now)
        self.store.record("b", "popup", {"kind": "系统自优化"}, "queued", self.now)
        first = self.store.read(category="opportunity")
        self.assertEqual(first["total"], 1)
        self.store.record("a", "news", {"opportunity": False, "active": True}, "analysis", self.now + 1)
        delta = self.store.read(category="opportunity", after=first["cursor"])
        self.assertEqual(len(delta["items"]), 1)  # Client must remove this from the opportunity filter.
        self.assertEqual(delta["total"], 0)
        self.assertEqual(self.store.read(category="system")["total"], 1)
        self.assertEqual(self.store.read(category="' OR 1=1 --")["total"], 2)

    def test_pre_token_meme_is_live_but_remains_non_executable(self):
        data = {
            "opportunity": False,
            "active": True,
            "preTokenMeme": {
                "eligible": True,
                "expiresAt": self.now + 1000,
                "tokenStatusLabel": "未发币 / 尚无可交易标的",
            },
        }
        event_id = self.store.record("pre-token", "news", data, "meme-potential", self.now)
        result = self.store.read(live=True, category="opportunity", now_ms=self.now)
        self.assertEqual(result["activeIds"], [event_id])
        self.assertFalse(result["items"][0]["news"]["opportunity"])
        self.assertEqual(
            self.store.read(live=True, now_ms=self.now + 1000)["total"],
            0,
        )


class NewsTradeDeliveryTests(unittest.TestCase):
    def test_price_warning_and_breakout_enter_without_ai_or_popup_and_do_not_replay(self):
        for episode, price, crossed in ((1, 99, False), (2, 101, True)):
            event = {"eventType": "prior_high", "symbol": "ALPHA", "currentPrice": price,
                     "weekHigh": 100, "priorHighBreakout": crossed, "checkedAt": self.now,
                     "episode": episode}
            server.record_price_watch_flow_event(event)
            server.record_price_watch_flow_event(event)
        result = server.EVENT_FLOW_STORE.read(live=True, category="signal", now_ms=self.now)
        self.assertEqual(result["total"], 2)
        self.assertEqual({row["signal"]["type"] for row in result["items"]}, {"near", "breakout"})
        self.assertTrue(all(not row.get("news") and not row.get("popup") for row in result["items"]))
        server.record_event_flow_popup({"key": "price-watch:ALPHA:episode:2", "title": "ALPHA 已突破"}, "failed", "窗口调起失败")
        result = server.EVENT_FLOW_STORE.read(live=True, category="signal", now_ms=self.now)
        self.assertEqual(result["total"], 2)
        self.assertTrue(any(row.get("popup", {}).get("stage") == "failed" for row in result["items"]))
        self.assertEqual(server.EVENT_FLOW_STORE.read(live=True, now_ms=self.now + 30*60000)["total"], 0)
        self.assertEqual(server.EVENT_FLOW_STORE.read()["total"], 2)

    def test_superseded_prior_high_signal_is_withdrawn_from_live_flow(self):
        event = {
            "eventType": "prior_high",
            "symbol": "USELESS",
            "currentPrice": .319,
            "weekHigh": .31846,
            "priorHighBreakout": True,
            "checkedAt": self.now,
            "episode": 2,
        }
        server.record_price_watch_flow_event(event)
        self.assertEqual(
            server.reconcile_price_watch_flow_signals(
                now_ms=self.now + 1,
                rows=[{
                    "symbol": "USELESS",
                    "week_high": .3360267,
                    "last_checked_at": self.now + 1,
                }],
            ),
            1,
        )
        self.assertEqual(
            server.EVENT_FLOW_STORE.read(live=True, category="signal", now_ms=self.now + 1)["total"],
            0,
        )
        archived = server.EVENT_FLOW_STORE.read(category="signal")["items"][0]
        self.assertFalse(archived["signal"]["active"])
        self.assertEqual(archived["signal"]["stage"], "reference-superseded")

    def test_current_prior_high_signal_stays_live(self):
        event = {
            "eventType": "prior_high",
            "symbol": "USELESS",
            "currentPrice": .319,
            "weekHigh": .31846,
            "priorHighBreakout": True,
            "checkedAt": self.now,
            "episode": 2,
        }
        server.record_price_watch_flow_event(event)
        self.assertEqual(
            server.reconcile_price_watch_flow_signals(
                now_ms=self.now + 1,
                rows=[{
                    "symbol": "USELESS",
                    "week_high": .31846,
                    "last_checked_at": self.now + 1,
                }],
            ),
            0,
        )
        self.assertEqual(
            server.EVENT_FLOW_STORE.read(live=True, category="signal", now_ms=self.now + 1)["total"],
            1,
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = int(time.time() * 1000)
        for key, value in (
            ("NEWS_TRADE_ALERT_STATE_PATH", Path(self.temp.name) / "state.json"),
            ("EVENT_FLOW_STORE", EventFlowStore(Path(self.temp.name) / "flow.sqlite")),
            ("NEWS_TRADE_AI_INFLIGHT", set()),
            ("NEWS_TRADE_AI_RETRY_AFTER", {}),
        ):
            patcher = patch.object(server, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def topic(self, key="alpha", verdict="trade-candidate", updated_at=None):
        topic = {"id": key, "topicKey": key, "title": "项目催化", "sourceActive": True,
                "timestamp": self.now - 60000, "eventHeatScore": 85,
                "relatedNews": [{"title": "社区围绕新发行机制创作", "timestamp": self.now - 1000}],
                "aiAnalysisStatus": "ready", "aiAnalysisUpdatedAt": updated_at or self.now,
                "aiAnalysis": {"verdict": verdict, "confidence": 80, "narrativeStrength": 85,
                               "catalyst": "新发行机制上线", "symbols": ["ALPHA"], "thesis": "新事件有明确标的"}}
        topic["aiAnalysis"].update(attentionStage="rising", catalystEvidenceId=server.attention_evidence(topic)[-1]["id"])
        return topic

    def test_just_completed_first_opportunity_alerts_once_while_historical_baseline_is_quiet(self):
        topic = self.topic()
        self.assertEqual(len(server.parse_site_event_monitor_events({"updatedAt": self.now, "newsTrades": [topic]}, completed=True)), 1)
        self.assertEqual(server.parse_site_event_monitor_events({"updatedAt": self.now, "newsTrades": [topic]}, completed=True), [])
        self.assertEqual(server.news_trade_transition_alert_topics([self.topic("history")], now_ms=self.now,
                         state_path=Path(self.temp.name) / "historical.json"), [])

    def test_new_topic_after_baseline_is_not_silently_lost(self):
        server.news_trade_transition_alert_topics([self.topic("baseline", "watch")], now_ms=self.now)
        fresh = server.news_trade_transition_alert_topics([self.topic("new")], now_ms=self.now)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["id"], "new")

    def test_failed_admission_does_not_commit_news_trade_transition(self):
        topic = self.topic()
        payload = {"updatedAt": self.now, "newsTrades": [topic]}
        with patch.object(server, 'launch_desktop_alert', return_value={'ok': False}):
            with self.assertRaises(RuntimeError):
                server.parse_site_event_monitor_events(payload, completed=True, admit_alerts=True)
        self.assertFalse(server.NEWS_TRADE_ALERT_STATE_PATH.exists())
        with patch.object(server, 'launch_desktop_alert', return_value={'ok': True, 'queued': True}) as launch:
            self.assertEqual(len(server.parse_site_event_monitor_events(payload, completed=True, admit_alerts=True)), 1)
            server.parse_site_event_monitor_events(payload, completed=True, admit_alerts=True)
            self.assertEqual(launch.call_count, 1)

    def test_same_verdict_after_cache_refresh_is_not_reannounced(self):
        topic = self.topic()
        server.news_trade_transition_alert_topics([topic], now_ms=self.now, completed=True)
        server.news_trade_transition_alert_topics([{**topic, "aiAnalysisStatus": "pending", "aiAnalysis": None}], now_ms=self.now + 1000)
        repeated = server.news_trade_transition_alert_topics([self.topic(updated_at=self.now + 2000)], now_ms=self.now + 2000, completed=True)
        self.assertEqual(repeated, [])

    def test_watch_reject_stale_and_inactive_results_stay_silent(self):
        rows = [self.topic("watch", "watch"), self.topic("reject", "reject"),
                self.topic("old", updated_at=self.now - 660_000), {**self.topic("inactive"), "sourceActive": False}]
        self.assertEqual(server.news_trade_transition_alert_topics(rows, now_ms=self.now, completed=True), [])

    def test_ai_queue_advances_beyond_top_ten_without_increasing_inflight_budget(self):
        topics = [{"id": str(index), "topicKey": str(index), "sourceActive": True} for index in range(18)]
        cache = {str(index): {"updatedAt": self.now, "analysis": {"verdict": "watch", "thesis": "等待确认"}} for index in range(10)}
        with patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "read_json_cache", return_value={"items": cache}), \
             patch.object(server, "news_trade_ai_topic_signature", side_effect=lambda row, settings: row["id"]), \
             patch.object(server.NEWS_TRADE_AI_POOL, "submit") as submit:
            attached = server.news_trade_attach_ai(topics, {"apiKey": "test-placeholder", "provider": "deepseek"})
            self.assertEqual([row["id"] for row in submit.call_args.args[1]], [str(index) for index in range(10, 16)])
            self.assertEqual(len(server.NEWS_TRADE_AI_INFLIGHT), 6)
            self.assertEqual(attached[16]["aiAnalysisStatus"], "queued")
            server.news_trade_attach_ai(topics, {"apiKey": "test-placeholder"})
            self.assertEqual(submit.call_count, 1)

    def test_popup_and_analysis_are_linked_and_include_non_opportunity_reason(self):
        row = self.topic(verdict="watch")
        server.record_event_flow_news([row])
        event = {"key": "popup-alpha", "eventFlowKey": "news:" + server.news_trade_topic_identity(row), "title": "ALPHA", "source": "News Trade 监控"}
        server.record_event_flow_popup(event, "failed", "调起失败")
        result = server.EVENT_FLOW_STORE.read()
        self.assertEqual(result["total"], 1)
        self.assertIn("未达到", result["items"][0]["news"]["reason"])
        self.assertEqual(result["items"][0]["popup"]["stage"], "failed")

    def test_strong_news_without_a_token_enters_pre_token_meme_watch(self):
        row = self.topic(verdict="watch")
        row.update({
            "sourceType": "newsflash",
            "source": "聚合快讯",
            "template": "meme-catalyst",
            "eventHeatScore": 82,
            "memeCandidates": [],
            "memeOpportunity": None,
            "assets": [],
            "candidateTier": "event-observation",
        })
        row["aiAnalysis"].update({
            "primarySymbol": "",
            "symbols": [],
            "confidence": 82,
            "narrativeStrength": 86,
            "memePotential": 91,
            "thesis": "鲜明人物冲突形成可复用传播梗",
        })
        server.record_event_flow_news([row])
        result = server.EVENT_FLOW_STORE.read(live=True, now_ms=self.now)
        self.assertEqual(result["total"], 1)
        news = result["items"][0]["news"]
        self.assertTrue(news["preTokenMeme"]["eligible"])
        self.assertFalse(news["opportunity"])
        self.assertEqual(news["symbol"], "")
        self.assertEqual(news["targets"], [])
        self.assertIn("不因同名币抢跑", news["actionHint"])

        pending = {**row, "aiAnalysisStatus": "pending", "aiAnalysis": None}
        server.record_event_flow_news([pending])
        retained = server.EVENT_FLOW_STORE.read(live=True, now_ms=self.now)["items"][0]["news"]
        self.assertTrue(retained["preTokenMeme"]["eligible"])
        self.assertFalse(retained["opportunity"])

    def test_ordinary_kol_cannot_enter_pre_token_meme_watch(self):
        analysis = {
            "verdict": "watch",
            "confidence": 95,
            "narrativeStrength": 95,
            "memePotential": 95,
        }
        topic = {
            "sourceType": "x-kol",
            "xCategory": "kol",
            "sourceActive": True,
            "timestamp": self.now,
            "eventHeatScore": 95,
            "template": "meme-catalyst",
        }
        self.assertFalse(
            server.news_trade_pre_token_meme_potential(topic, analysis, now_ms=self.now)["eligible"]
        )

    def test_speech_uses_utf8_for_chinese_and_emoji_without_opening_a_window(self):
        with patch.object(desktop_alert.threading, "Thread", side_effect=lambda **kwargs: SimpleNamespace(start=kwargs["target"])), \
             patch.object(desktop_alert.time, "sleep"), patch.object(desktop_alert.subprocess, "run") as run:
            desktop_alert.speak_text("项目官方👨发布", True)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertIn("InputEncoding", run.call_args.args[0][-1])


if __name__ == "__main__":
    unittest.main()
