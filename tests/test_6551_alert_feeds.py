"""Tests for the 6551/OpenNews + OpenTwitter + Pump Claim alert feeds."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opennews_client
import opentwitter_client
import pump_claim_monitor


class OpenNewsEventTests(unittest.TestCase):
    def test_listing_alerts_regardless_of_score_with_stable_keys(self):
        payload = {
            "fetchedAt": 1789000000000,
            "listing": [
                {"id": "L1", "text": "Binance Will List FOO", "newsType": "Binance",
                 "engineType": "listing", "link": "https://x.com/1",
                 "coins": [{"symbol": "foo", "market_type": "cex"}], "ts": "2026-09-26T08:00:00Z",
                 "aiRating": {"score": 40, "signal": "neutral"}},
            ],
            "onchain": [], "market": [], "news": [],
        }
        events = opennews_client.parse_opennews_events(payload)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "海外所上新")
        self.assertEqual(event["sourceLabel"], "BN")
        self.assertIn("FOO", event["title"])
        self.assertEqual(event["time"], 1790409600000)  # 2026-09-26T08:00:00Z
        again = opennews_client.parse_opennews_events(payload)
        self.assertEqual(again[0]["key"], event["key"])
        self.assertTrue(event["key"].startswith("opennews:l1"))

    def test_onchain_and_market_respect_score_thresholds_and_news_passes(self):
        payload = {
            "fetchedAt": 1789000000000,
            "listing": [],
            "onchain": [
                {"id": "W1", "text": "whale opens 50M BTC long", "newsType": "Hyperliquid",
                 "link": "", "ts": "2026-09-26T08:00:00Z",
                 "coins": [{"symbol": "BTC"}], "aiRating": {"score": 72, "signal": "long"}},
                {"id": "W2", "text": "small whale", "newsType": "Hyperliquid",
                 "link": "", "ts": "2026-09-26T08:00:00Z",
                 "coins": [{"symbol": "BTC"}], "aiRating": {"score": 40, "signal": "neutral"}},
            ],
            "market": [
                {"id": "M1", "text": "large liquidation cascade", "newsType": "large_liquidation",
                 "link": "", "ts": "2026-09-26T08:00:00Z",
                 "coins": [{"symbol": "SOL"}], "aiRating": {"score": 88, "signal": "short"}},
                {"id": "M2", "text": "minor funding wiggle", "newsType": "funding_rate",
                 "link": "", "ts": "2026-09-26T08:00:00Z",
                 "coins": [{"symbol": "SOL"}], "aiRating": {"score": 55, "signal": "neutral"}},
            ],
            "news": [
                {"id": "N1", "text": "SEC approves generic listing standards", "newsType": "Reuters",
                 "link": "https://reuters.example/1", "ts": "2026-09-26T08:00:00Z",
                 "coins": [], "aiRating": {"score": 91, "signal": "long", "summary": "监管利好"}},
            ],
        }
        events = opennews_client.parse_opennews_events(payload)
        kinds = [event["kind"] for event in events]
        self.assertEqual(kinds.count("HL 巨鲸异动"), 1)   # W2 filtered by score
        self.assertEqual(kinds.count("衍生品异动"), 1)     # M2 filtered by score
        self.assertEqual(kinds.count("海外快讯"), 1)
        news = next(event for event in events if event["kind"] == "海外快讯")
        self.assertEqual(news["sourceLabel"], "RTR")
        self.assertIn("看多", news["body"])

    def test_disabled_payload_yields_no_events(self):
        self.assertEqual(opennews_client.parse_opennews_events({"disabled": True}), [])
        self.assertEqual(opennews_client.parse_opennews_events("junk"), [])


class OpenTwitterEventTests(unittest.TestCase):
    def test_deleted_tweets_become_risk_events(self):
        payload = {
            "fetchedAt": 1789000000000,
            "accounts": [
                {"username": "@rugproject",
                 "deleted": [
                     {"id": "t2", "text": "we are totally not rugging"},
                     {"id": "t1", "text": "buy the top"},
                 ]},
                {"username": "@quiet", "deleted": []},
            ],
        }
        events = opentwitter_client.parse_twitter_watch_events(payload)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "X 删推预警")
        self.assertIn("rugproject", event["title"])
        self.assertEqual(event["priority"], "风控预警")
        self.assertIn("t2", event["key"])  # sorted ids in the dedupe key
        self.assertIn("t1", event["key"])

    def test_watch_accounts_parses_env_list(self):
        with patch.dict("os.environ", {"OPENTWITTER_WATCH_ACCOUNTS": " @A , @B;x  @a "}):
            handles = opentwitter_client.watch_accounts()
        lowered = [handle.lower() for handle in handles]
        self.assertEqual(lowered, ["a", "b", "x"])

    def test_disabled_or_empty_watch_stays_silent(self):
        self.assertEqual(opentwitter_client.parse_twitter_watch_events({"disabled": True}), [])


class PumpClaimEventTests(unittest.TestCase):
    def row(self, **overrides):
        base = {
            "token_address": "7GUnr7krtQhJwd6ASY2VUprd9t4c64zcgCsjdmZepump",
            "signal_type": 18,
            "trigger_at": 1788999600,
            "trigger_mc": 250_000,
            "signal_times": 1,
            "signal_times_by_type": {"18": 1},
            "data": {"symbol": "NPC", "name": "NPCs", "launchpad": "Pump.fun",
                     "creator_token_status": "creator_close",
                     "address": "7GUnr7krtQhJwd6ASY2VUprd9t4c64zcgCsjdmZepump"},
        }
        base.update(overrides)
        return base

    def test_claim_of_tracked_symbol_alerts_once(self):
        payload = {"fetchedAt": 1789000000000, "rows": [self.row()]}
        with patch.object(pump_claim_monitor, "tracked_symbol_keys", return_value={"NPC"}):
            events = pump_claim_monitor.parse_pump_claim_events(payload)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "Pump 认领预警")
        self.assertIn("NPC", event["title"])
        self.assertIn("250K", event["body"])
        self.assertEqual(event["queuePriority"], 95)

    def test_untracked_symbol_and_repeat_claims_stay_silent(self):
        payload = {"fetchedAt": 1789000000000, "rows": [
            self.row(signal_times=3, signal_times_by_type={"18": 3}),  # repeat claim
            self.row(token_address="OtherAddr11111111111111111111111111111111pump",
                     signal_times=3, signal_times_by_type={"18": 3},
                     data={"symbol": "STRANGER", "launchpad": "Pump.fun"}),
        ]}
        with patch.object(pump_claim_monitor, "tracked_symbol_keys", return_value={"NPC"}):
            events = pump_claim_monitor.parse_pump_claim_events(payload)
        self.assertEqual(events, [])

    def test_disabled_payload_stays_silent(self):
        self.assertEqual(pump_claim_monitor.parse_pump_claim_events({"disabled": True}), [])


if __name__ == "__main__":
    unittest.main()
