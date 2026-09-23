import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from trench_person_signals import important_person_sources, match_person_post


NOW = 1_800_000_000_000


def trench_row(symbol="AGRIPPA", name="Agrippa Pepe", contract="AgrippaContract111"):
    return {
        "network": "solana",
        "contractAddress": contract,
        "symbol": symbol,
        "name": name,
        "poolCreatedAt": NOW - 60_000,
        "xOriginal": {
            "url": "https://x.com/AgriPepe/status/123",
            "handle": "AgriPepe",
            "statusId": "123",
        },
        "metrics": {"liquidityUsd": 25_000, "volumeH24Usd": 40_000},
        "reasons": [],
    }


def person_source():
    return {
        "id": "trench-person:finkd",
        "handle": "finkd",
        "displayName": "Mark Zuckerberg",
        "personRole": "Meta 创始人",
        "category": "notable",
    }


def confirmed_semantics(pairs):
    return {
        signal["key"]: {
            "version": server.JEV_SEMANTIC_VERSION,
            "status": "confirmed",
            "related": True,
            "choice": "related",
            "confidence": 0.94,
            "provider": "JEV 快速语义模型",
            "model": "jev-test",
        }
        for _row, signal in pairs
    }


class TrenchPersonSignalTests(unittest.TestCase):
    def test_default_roster_keeps_only_market_moving_people_plus_official_accounts(self):
        rows = important_person_sources()
        by_handle = {row["handle"].casefold(): row for row in rows}

        self.assertEqual(len(rows), 129)
        for handle in (
            "vitalikbuterin", "realdonaldtrump", "jtlonsdale", "novogratz",
            "sunyuchentron", "binance", "solana", "layerzero_core",
        ):
            self.assertIn(handle, by_handle)
        for handle in ("brian_armstrong", "satyanadella", "karpathy", "secgov"):
            self.assertNotIn(handle, by_handle)
        self.assertEqual(by_handle["binance"]["category"], "project_official")
        self.assertEqual(by_handle["elonmusk"]["watchTier"], "primary")
        self.assertEqual(sum(row["category"] == "project_official" for row in rows), 80)
        self.assertEqual(sum(row["category"] != "project_official" for row in rows), 49)
        self.assertEqual(len(rows), len(by_handle))

    def test_quote_of_token_official_x_is_strong_identity(self):
        signal = match_person_post(
            trench_row(),
            {
                "tweetId": "9001",
                "text": "Interesting launch",
                "quote": {"handle": "AgriPepe", "text": "Agrippa is live"},
                "publishedAt": NOW,
                "url": "https://x.com/finkd/status/9001",
            },
            person_source(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["matchType"], "official-x")
        self.assertEqual(signal["identityStatus"], "person-x-official-handle")
        self.assertEqual(signal["actionType"], "quote")
        self.assertEqual(signal["sourceCategory"], "notable")
        self.assertEqual(signal["sourceTier"], "secondary")
        self.assertGreaterEqual(signal["confidence"], 95)

    def test_distinctive_name_segment_catches_event_before_news(self):
        signal = match_person_post(
            trench_row(),
            {
                "tweetId": "9002",
                "text": "Agrippa is a fascinating idea.",
                "publishedAt": NOW,
                "url": "https://x.com/finkd/status/9002",
            },
            person_source(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["matchType"], "exact-name")
        self.assertEqual(signal["identityStatus"], "person-x-name-unverified")

    def test_bare_generic_meme_word_is_not_a_person_signal(self):
        signal = match_person_post(
            trench_row(symbol="MEME", name="Meme Token"),
            {
                "tweetId": "9003",
                "text": "Memes are fun.",
                "publishedAt": NOW,
            },
            person_source(),
        )

        self.assertIsNone(signal)

    def test_bare_official_handle_word_does_not_fake_an_identity_match(self):
        row = trench_row(symbol="BNB", name="Binance Meme")
        row["xOriginal"] = {"url": "https://x.com/binance", "handle": "binance", "statusId": ""}
        signal = match_person_post(
            row,
            {
                "tweetId": "9003b",
                "text": "The Binance ecosystem is growing.",
                "publishedAt": NOW,
            },
            person_source(),
        )

        self.assertIsNone(signal)

    def test_quoted_generic_word_does_not_become_a_token_mention(self):
        signal = match_person_post(
            trench_row(symbol="OPEN", name="importance of staying open"),
            {
                "tweetId": "quote-open",
                "text": "Mimo-V2.6 is on B.AI",
                "quote": {"handle": "BAI_AGI", "text": "New open-weights multimodal models are live."},
                "publishedAt": NOW,
            },
            {**person_source(), "handle": "justinsuntron", "displayName": "Justin Sun"},
        )

        self.assertIsNone(signal)

    def test_rss_expansion_generic_agent_word_does_not_match_cra(self):
        signal = match_person_post(
            trench_row(symbol="CRA", name="Cra Agent", contract="0xCra"),
            {
                "tweetId": "rss-cra",
                "text": "小米 MiMo-V2.6 系列模型已上线 B.AI，面向大规模 Agent 调用。",
                "entryType": "rss",
                "publishedAt": NOW,
            },
            {**person_source(), "handle": "sunyuchentron", "displayName": "孙宇晨"},
        )

        self.assertIsNone(signal)

    def test_replies_and_comments_never_create_person_signals(self):
        signal = match_person_post(
            trench_row(),
            {
                "tweetId": "reply-1",
                "text": "Agrippa is interesting",
                "entryType": "replied_to",
                "publishedAt": NOW,
            },
            person_source(),
        )

        self.assertIsNone(signal)

    def test_source_provided_follow_record_requires_official_target(self):
        signal = match_person_post(
            trench_row(),
            {
                "id": "follow-1",
                "actionType": "follow",
                "targetHandle": "AgriPepe",
                "text": "follow relationship record",
                "publishedAt": NOW,
            },
            person_source(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["matchType"], "verified-follow")
        self.assertEqual(signal["actionLabel"], "新关注")
        self.assertEqual(signal["confidence"], 99)

    def test_hot_source_cannot_starve_an_unvisited_core_source(self):
        sources = [
            {
                "id": "trench-person:hot",
                "handle": "hot",
                "displayName": "Hot",
                "personRole": "重要人物",
                "category": "notable",
                "watchTier": "primary",
                "aliases": ["agrippa"],
            },
            {
                "id": "trench-person:fresh",
                "handle": "fresh",
                "displayName": "Fresh",
                "personRole": "重要人物",
                "category": "notable",
                "watchTier": "primary",
                "aliases": ["unrelated"],
            },
        ]
        with (
            patch.dict(server.TRENCH_PERSON_WATCH_NEXT_DUE, {"trench-person:hot": 1_000.0}, clear=True),
            patch.dict(server.TRENCH_PERSON_WATCH_FAILURES, {}, clear=True),
            patch.object(server.time, "monotonic", return_value=1_000.0),
            patch.object(server, "trench_person_recent_rows", return_value=[trench_row()]),
            patch.object(server, "trench_person_watch_sources", return_value=sources),
            patch.object(server, "trench_person_fetch_public_source", return_value={
                "source": {"status": "ok", "lastCheckAt": NOW},
                "items": [],
            }),
            patch.object(server, "trench_person_record_source_health"),
            patch.object(server, "ingest_trench_person_payload", return_value=[]),
        ):
            result = server.trench_person_poll_once()

        self.assertEqual(result["source"], "fresh")

    def test_free_providers_alternate_without_double_requesting(self):
        source = {"id": "trench-person:test", "handle": "test"}
        with (
            patch.dict(server.TRENCH_PERSON_WATCH_FAILURES, {}, clear=True),
            patch.object(server, "x_kol_fetch_fxtwitter_timeline", return_value={"source": {"status": "error"}}) as fx,
            patch.object(server, "x_kol_fetch_public_timeline", return_value={"source": {"status": "ok"}}) as public,
        ):
            server.trench_person_fetch_public_source(source)
        fx.assert_called_once_with(source)
        public.assert_not_called()

        with (
            patch.dict(server.TRENCH_PERSON_WATCH_FAILURES, {source["id"]: 1}, clear=True),
            patch.object(server, "x_kol_fetch_fxtwitter_timeline") as fx,
            patch.object(server, "x_kol_fetch_public_timeline", return_value={"source": {"status": "ok"}}) as public,
        ):
            server.trench_person_fetch_public_source(source)
        fx.assert_not_called()
        public.assert_called_once_with(source)

    def test_new_signal_is_persisted_handed_to_research_and_alerted_once(self):
        post = {
            "tweetId": "9004",
            "text": "Agrippa is a fascinating idea.",
            "publishedAt": NOW,
            "url": "https://x.com/finkd/status/9004",
            "_personSource": person_source(),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "signals.json"
            with (
                patch.object(server, "TRENCH_PERSON_SIGNAL_PATH", path),
                patch.object(server.time, "time", return_value=NOW / 1000),
                patch.object(server, "TRENCH_PERSON_ALERT_NOT_BEFORE_MS", NOW - 60_000),
                patch.dict(server.TRENCH_PERSON_LAST_ALERT_AT, {}, clear=True),
                patch.object(server, "trench_person_ai_semantic_assessments", side_effect=confirmed_semantics),
                patch.object(server, "apply_trench_person_signal_to_research") as research,
                patch.object(server, "send_trench_person_signal_alert") as alert,
            ):
                first = server.process_trench_person_activity([post], [trench_row()])
                second = server.process_trench_person_activity([post], [trench_row()])
                annotated = server.attach_trench_person_signals({"items": [trench_row()]})

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        research.assert_called_once()
        alert.assert_called_once()
        self.assertEqual(annotated["items"][0]["personSignal"]["personName"], "Mark Zuckerberg")

    def test_history_is_researched_but_never_replayed_as_a_popup(self):
        post = {
            "tweetId": "history-1",
            "text": "Agrippa is a fascinating idea.",
            "publishedAt": NOW - 60_000,
            "url": "https://x.com/finkd/status/history-1",
            "_personSource": person_source(),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "TRENCH_PERSON_SIGNAL_PATH", Path(temp_dir) / "signals.json"),
                patch.object(server.time, "time", return_value=NOW / 1000),
                patch.object(server, "TRENCH_PERSON_ALERT_NOT_BEFORE_MS", NOW),
                patch.dict(server.TRENCH_PERSON_LAST_ALERT_AT, {}, clear=True),
                patch.object(server, "trench_person_ai_semantic_assessments", side_effect=confirmed_semantics),
                patch.object(server, "apply_trench_person_signal_to_research") as research,
                patch.object(server, "send_trench_person_signal_alert") as alert,
            ):
                signals = server.process_trench_person_activity([post], [trench_row()])

        self.assertEqual(len(signals), 1)
        research.assert_called_once()
        alert.assert_not_called()

    def test_same_person_batch_emits_at_most_one_live_popup(self):
        posts = [
            {
                "tweetId": f"live-{index}",
                "text": "Agrippa is a fascinating idea.",
                "publishedAt": NOW - index * 1_000,
                "url": f"https://x.com/finkd/status/live-{index}",
                "_personSource": person_source(),
            }
            for index in range(2)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "TRENCH_PERSON_SIGNAL_PATH", Path(temp_dir) / "signals.json"),
                patch.object(server.time, "time", return_value=NOW / 1000),
                patch.object(server, "TRENCH_PERSON_ALERT_NOT_BEFORE_MS", NOW - 60_000),
                patch.dict(server.TRENCH_PERSON_LAST_ALERT_AT, {}, clear=True),
                patch.object(server, "trench_person_ai_semantic_assessments", side_effect=confirmed_semantics),
                patch.object(server, "apply_trench_person_signal_to_research"),
                patch.object(server, "send_trench_person_signal_alert") as alert,
            ):
                signals = server.process_trench_person_activity(posts, [trench_row()])

        self.assertEqual(len(signals), 2)
        alert.assert_called_once()

    def test_ambiguous_same_symbol_contracts_do_not_raise_immediate_popup(self):
        post = {
            "tweetId": "9005",
            "text": "$AGRIPPA",
            "publishedAt": NOW,
            "url": "https://x.com/finkd/status/9005",
            "_personSource": person_source(),
        }
        rows = [
            trench_row(contract="AgrippaContractA"),
            trench_row(contract="AgrippaContractB"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "TRENCH_PERSON_SIGNAL_PATH", Path(temp_dir) / "signals.json"),
                patch.object(server.time, "time", return_value=NOW / 1000),
                patch.object(server, "trench_person_ai_semantic_assessments", side_effect=confirmed_semantics),
                patch.object(server, "apply_trench_person_signal_to_research") as research,
                patch.object(server, "send_trench_person_signal_alert") as alert,
            ):
                signals = server.process_trench_person_activity([post], rows)

        self.assertEqual(len(signals), 1)
        self.assertTrue(signals[0]["identityAmbiguous"])
        self.assertLess(signals[0]["confidence"], 90)
        research.assert_called_once()
        alert.assert_not_called()

    def test_monitor_as_an_ordinary_tool_reaches_ai_and_is_rejected(self):
        post = {
            "tweetId": "monitor-tool",
            "text": "SCOUT is our City Hall monitor for public meetings.",
            "publishedAt": NOW,
            "url": "https://x.com/garryslist/status/monitor-tool",
            "_personSource": {**person_source(), "handle": "garryslist", "displayName": "Garry's List"},
        }
        row = trench_row(symbol="MONITOR", name="Monitor", contract="MonitorContract111")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "TRENCH_PERSON_SIGNAL_PATH", Path(temp_dir) / "signals.json"),
                patch.object(server.time, "time", return_value=NOW / 1000),
                patch.object(server, "trench_person_ai_semantic_assessments", return_value={}) as semantic,
                patch.object(server, "apply_trench_person_signal_to_research") as research,
                patch.object(server, "send_trench_person_signal_alert") as alert,
            ):
                signals = server.process_trench_person_activity([post], [row])

        self.assertEqual(signals, [])
        semantic.assert_called_once()
        self.assertEqual(semantic.call_args.args[0][0][1]["matchType"], "exact-name")
        research.assert_not_called()
        alert.assert_not_called()

    def test_jev_semantic_result_is_cached_by_post_and_contract(self):
        row = trench_row()
        signal = match_person_post(
            row,
            {
                "tweetId": "semantic-cache",
                "text": "Agrippa is a fascinating idea.",
                "publishedAt": NOW,
            },
            person_source(),
            semantic_prefilter=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "TRENCH_PERSON_SEMANTIC_CACHE_PATH", Path(temp_dir) / "semantic.json"),
                patch.object(server.time, "time", return_value=NOW / 1000),
                patch.object(server, "analyze_jev_person_semantics", return_value={
                    server.trench_person_semantic_cache_key(signal): {
                        "choice": "related",
                        "confidence": 0.93,
                        "model": "jev-test",
                        "analyzedAt": NOW,
                    },
                }) as analyzer,
            ):
                first = server.trench_person_ai_semantic_assessments([(row, signal)])
                second = server.trench_person_ai_semantic_assessments([(row, signal)])

        self.assertTrue(first[signal["key"]]["related"])
        self.assertEqual(first, second)
        analyzer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
