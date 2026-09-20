import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


NOW = 1_800_000_000_000


class GlobalHotspotMonitorTests(unittest.TestCase):
    def test_batch_claim_is_hourly_failure_counted_and_capped_at_24_per_day(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hotspots.json"
            claimed, state = server.global_hotspot_claim_batch(path=path, now_ms=NOW)
            self.assertTrue(claimed)
            self.assertEqual(state["attemptsToday"], 1)
            claimed, _ = server.global_hotspot_claim_batch(path=path, now_ms=NOW + 3_599_000)
            self.assertFalse(claimed)
            server.write_json_cache(path, {
                "version": 1,
                "day": server.global_hotspot_day_key(NOW),
                "attemptsToday": 24,
                "lastAttemptAt": NOW - 3_600_000,
            })
            claimed, state = server.global_hotspot_claim_batch(path=path, now_ms=NOW)
            self.assertFalse(claimed)
            self.assertEqual(state["attemptsToday"], 24)

    def test_normalizer_requires_public_evidence_and_keeps_stable_identity(self):
        raw = {
            "title": "Jacob Coxon 从 Anthropic 离职并质疑超级智能竞赛",
            "summary": "研究员公开离职声明形成高传播讨论。",
            "entityNames": ["Jacob Coxon", "Jacob"],
            "searchTerms": ["Jacob", "Jacob Coxon"],
            "memeThesis": "人物名和公开声明形成可识别叙事。",
            "hotnessScore": 88,
            "occurredAt": NOW - 600_000,
            "evidence": [{"url": "https://example.com/jacob", "title": "原始声明"}],
        }
        first = server.normalize_global_hotspot_event(raw, now_ms=NOW)
        second = server.normalize_global_hotspot_event({**raw, "hotnessScore": 91}, now_ms=NOW)
        self.assertIsNotNone(first)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["primaryUrl"], "https://example.com/jacob")
        self.assertIsNone(server.normalize_global_hotspot_event({**raw, "evidence": []}, now_ms=NOW))
        self.assertIsNone(server.normalize_global_hotspot_event(
            {**raw, "evidence": [{"url": "file:///secret", "title": "bad"}]}, now_ms=NOW
        ))

    def test_worker_uses_one_low_effort_codex_web_search_and_persists_verified_candidate(self):
        response = {
            "choices": [{"message": {"content": """{
              "events": [{
                "title": "Jacob Coxon 从 Anthropic 离职",
                "summary": "AI研究员公开离职并批评超级智能竞赛。",
                "entityNames": ["Jacob Coxon", "Jacob"],
                "searchTerms": ["Jacob"],
                "memeThesis": "人物事件可能形成同名 Meme 关注。",
                "hotnessScore": 90,
                "occurredAt": 1799999400000,
                "evidence": [{"url": "https://example.com/jacob", "title": "声明"}]
              }]
            }"""}}],
            "_provider": "codex-cli",
        }
        dex = [{
            "symbol": "JACOB", "name": "Jacob", "chain": "bsc",
            "contractAddress": "0x" + "a" * 40, "liquidityUsd": 50_000,
            "volume24hUsd": 200_000, "txns24h": 300, "url": "https://dex.example/jacob",
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hotspots.json"
            research = Mock()
            with patch.object(server, "GLOBAL_HOTSPOT_STATE_PATH", path), patch.object(
                server, "codex_cli_chat", return_value=response
            ) as chat, patch.object(server, "news_trade_dex_search_rows", return_value=dex), patch.object(
                server, "trigger_api_refresh"
            ), patch.object(server.ONCHAIN_FAST_RESEARCH, "ingest", research):
                payload = server.run_global_hotspot_batch(now_ms=NOW, existing_titles=["已有快讯"])

            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["sourceRows"]), 1)
            self.assertEqual(payload["sourceRows"][0]["sourceType"], "web-hotspot")
            self.assertEqual(payload["candidateRows"][0]["contractAddress"], "0x" + "a" * 40)
            self.assertTrue(chat.call_args.kwargs["web_search"])
            self.assertEqual(chat.call_args.kwargs["model_override"], "gpt-5.6-luna")
            self.assertEqual(chat.call_args.kwargs["reasoning_effort_override"], "minimal")
            self.assertEqual(chat.call_count, 1)
            research.assert_called_once()

    def test_hotspot_enricher_promotes_only_verified_liquid_active_exact_match(self):
        snapshot = {
            "events": [{
                "id": "hotspot:jacob", "title": "Jacob 离职事件", "summary": "公开声明",
                "entityNames": ["Jacob"], "searchTerms": ["Jacob"], "hotnessScore": 90,
                "occurredAt": NOW - 60_000, "primaryUrl": "https://example.com/jacob",
            }]
        }
        base = {
            "network": "bsc", "contractAddress": "0x" + "a" * 40,
            "symbol": "JACOB", "name": "Jacob", "decision": "watching",
            "metrics": {"liquidityUsd": 20_000, "volumeH1Usd": 10_000, "transactionsH1": 20},
            "reasons": [], "risks": [],
        }
        promoted = server.global_hotspot_enrich_candidate(base, snapshot=snapshot, now_ms=NOW)
        self.assertEqual(promoted["decision"], "shortlisted")
        self.assertEqual(promoted["researchEvidence"]["source"], "全网热点")
        self.assertEqual(promoted["newsSignal"]["tier"], "news-triggered")
        self.assertEqual(promoted["newsObservedAt"], NOW)
        self.assertEqual(server.global_hotspot_enrich_candidate(
            {**base, "metrics": {"liquidityUsd": 100, "volumeH1Usd": 10_000, "transactionsH1": 20}},
            snapshot=snapshot, now_ms=NOW,
        )["decision"], "watching")
        self.assertEqual(server.global_hotspot_enrich_candidate(
            {**base, "decision": "filtered"}, snapshot=snapshot, now_ms=NOW
        )["decision"], "filtered")
        self.assertEqual(server.global_hotspot_enrich_candidate(
            {**base, "contractAddress": ""}, snapshot=snapshot, now_ms=NOW
        )["decision"], "watching")

    def test_gmgn_narrative_fallback_uses_h24_only_when_h1_is_missing(self):
        snapshot = {
            "events": [{
                "id": "hotspot:gmgn-fallback", "title": "新 Meme 叙事形成热点", "summary": "公开事件快速传播",
                "entityNames": ["FALLBACK"], "searchTerms": ["FALLBACK"], "hotnessScore": 90,
                "occurredAt": NOW - 60_000, "primaryUrl": "https://example.com/fallback",
            }]
        }
        row = {
            "network": "solana", "contractAddress": "Fallback111111111111111111111111111111111",
            "symbol": "FALLBACK", "name": "Fallback", "decision": "warming",
            "narrativeFallbackEligible": True,
            "narrativeContext": {"socials": ["https://x.com/fallback"]},
            "metrics": {"liquidityUsd": 35_000, "volumeH1Usd": None, "transactionsH1": 0,
                        "volumeH24Usd": 80_000, "transactionsH24": 120},
            "reasons": [], "risks": [],
        }
        promoted = server.global_hotspot_enrich_candidate(
            row, snapshot=snapshot, now_ms=NOW, allow_narrative_fallback=True,
        )

        self.assertEqual(promoted["decision"], "shortlisted")
        self.assertTrue(promoted["narrativeFallbackApplied"])
        self.assertTrue(any("1小时成交数据尚未形成" in reason for reason in promoted["reasons"]))
        self.assertEqual(server.global_hotspot_enrich_candidate(
            row, snapshot=snapshot, now_ms=NOW,
        )["decision"], "warming")

    def test_web_hotspot_is_visible_in_news_trade_even_before_a_contract_is_found(self):
        event = server.normalize_global_hotspot_event({
            "title": "新人物事件形成全网热点",
            "summary": "一项公开声明在社交网络快速传播并引发大量讨论。",
            "entityNames": ["New Person"],
            "searchTerms": ["New Person"],
            "memeThesis": "人物名和公开事件具有可识别符号。",
            "hotnessScore": 82,
            "occurredAt": NOW - 60_000,
            "evidence": [{"url": "https://example.com/person", "title": "公开声明"}],
        }, now_ms=NOW)
        classified = server.classify_event_monitor_row(
            server.global_hotspot_source_row(event, []), NOW, candidate_source_rows=[]
        )

        self.assertIsNotNone(classified)
        self.assertTrue(classified["isNewsTrade"])
        self.assertEqual(classified["candidateTier"], "event-observation")
        self.assertEqual(classified["analysisIntakeReason"], "每小时全网热点发现")


if __name__ == "__main__":
    unittest.main()
