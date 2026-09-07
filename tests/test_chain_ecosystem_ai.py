import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


def sample_payload():
    project = {
        "id": 7,
        "name": "Sample DEX",
        "description": "A native exchange",
        "tokenStage": "potential",
        "potentialScore": {"score": 68, "confidence": 75},
        "markets": [{"marketKey": "dex", "name": "DEX", "confidence": 90}],
        "assets": [{"symbol": "SDEX", "contractAddress": "0x1234"}],
        "evidence": [{"source": "official", "title": "Token plan", "evidenceType": "token_announcement"}],
    }
    return {
        "ok": True,
        "selectedChain": {
            "id": 1,
            "slug": "sample-chain",
            "name": "Sample Chain",
            "stage": "mainnet_focus",
            "chainType": "Ethereum L2",
            "chainId": "4663",
            "gasSymbol": "ETH",
            "evidence": [{"source": "official", "title": "Mainnet is live", "evidenceType": "mainnet"}],
        },
        "chains": [],
        "markets": [{
            "key": "dex",
            "name": "DEX",
            "level": "L1",
            "description": "Decentralized exchanges",
            "top": [{
                "projectId": 7,
                "name": "Sample DEX",
                "symbol": "SDEX",
                "score": 72,
                "confidence": 90,
                "marketMetrics": {"liquidityUsd": 1_200_000, "volume24hUsd": 800_000},
            }],
            "candidates": [],
        }],
        "projects": [project],
        "potentialProjects": [dict(project)],
        "alerts": [{
            "id": 9,
            "dedupeKey": "sample-alert",
            "eventType": "stage_upgrade",
            "title": "Sample DEX stage upgraded",
            "confidence": 88,
            "details": {"from": "potential", "to": "announced"},
        }],
        "sourceHealth": [],
        "warnings": [],
        "updatedAt": 1000,
        "stale": False,
    }


class ChainEcosystemAiTests(unittest.TestCase):
    def test_subjects_cover_every_visible_analysis_scope(self):
        subjects = server.chain_ecosystem_ai_subjects(sample_payload(), {"provider": "deepseek", "model": "test"})

        self.assertEqual(
            {row["key"] for row in subjects},
            {"chain:1", "market:dex", "project:7", "alert:9"},
        )
        project = next(row for row in subjects if row["key"] == "project:7")
        self.assertIn("Token plan", str(project["facts"]))
        market = next(row for row in subjects if row["key"] == "market:dex")
        self.assertIn("volume24hUsd", str(market["facts"]))

    def test_normalizer_rejects_empty_and_bounds_scores(self):
        self.assertIsNone(server.normalize_chain_ecosystem_ai_analysis({}))
        result = server.normalize_chain_ecosystem_ai_analysis({
            "verdict": "strong",
            "confidence": 120,
            "narrativeStrength": -2,
            "importance": 88,
            "summary": "主网生态扩张",
            "catalyst": "官方公布代币计划",
            "risk": "交易深度仍需验证",
            "nextFocus": "观察链上资金",
            "tags": ["主网", "主网", "DEX"],
        })

        self.assertEqual(result["confidence"], 100)
        self.assertEqual(result["narrativeStrength"], 0)
        self.assertEqual(result["tags"], ["主网", "DEX"])

    def test_worker_persists_ai_results_and_marks_provider(self):
        settings = {"provider": "deepseek", "model": "test-model", "maxTokens": 4000}
        subjects = server.chain_ecosystem_ai_subjects(sample_payload(), settings)
        response_items = [
            {
                "key": row["key"],
                "verdict": "watch",
                "confidence": 80,
                "narrativeStrength": 65,
                "importance": 60,
                "summary": f"AI {row['type']} conclusion",
                "catalyst": "official progress",
                "risk": "evidence pending",
                "nextFocus": "track onchain data",
                "tags": ["AI"],
            }
            for row in subjects
        ]
        response = {
            "choices": [{"message": {"content": server.json.dumps({"items": response_items})}}],
            "_provider": "codex-cli",
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server, "CHAIN_ECOSYSTEM_AI_CACHE_PATH", Path(temp_dir) / "chain-ai.json"
        ), patch.object(server, "deepseek_chat", return_value=response):
            with server.CHAIN_ECOSYSTEM_AI_LOCK:
                server.CHAIN_ECOSYSTEM_AI_INFLIGHT.update(row["signature"] for row in subjects)
            server.chain_ecosystem_ai_worker(subjects, settings)
            cached = server.read_json_cache(server.CHAIN_ECOSYSTEM_AI_CACHE_PATH)

        self.assertEqual(len(cached["items"]), 4)
        self.assertTrue(all(item["provider"] == "codex-cli" for item in cached["items"].values()))
        self.assertEqual(server.CHAIN_ECOSYSTEM_AI_INFLIGHT, set())

    def test_attach_reuses_cached_ai_for_all_nested_rows(self):
        payload = sample_payload()
        settings = {"provider": "deepseek", "model": "test-model", "apiKey": "secret"}
        subjects = server.chain_ecosystem_ai_subjects(payload, settings)
        cached_items = {
            row["signature"]: {
                "updatedAt": server.time.time() * 1000,
                "provider": "deepseek",
                "analysis": {
                    "verdict": "watch",
                    "confidence": 81,
                    "narrativeStrength": 67,
                    "importance": 55,
                    "summary": row["key"],
                    "catalyst": "official evidence",
                    "risk": "market risk",
                    "nextFocus": "next evidence",
                    "tags": ["AI"],
                },
            }
            for row in subjects
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server, "CHAIN_ECOSYSTEM_AI_CACHE_PATH", Path(temp_dir) / "chain-ai.json"
        ), patch.object(server.CHAIN_ECOSYSTEM_AI_POOL, "submit") as submit:
            server.write_json_cache(server.CHAIN_ECOSYSTEM_AI_CACHE_PATH, {"items": cached_items})
            result = server.chain_ecosystem_attach_ai(payload, settings)

        self.assertEqual(result["aiAnalysisStatus"], "ready")
        self.assertEqual(result["selectedChain"]["aiAnalysis"]["summary"], "chain:1")
        self.assertEqual(result["markets"][0]["aiAnalysis"]["summary"], "market:dex")
        self.assertEqual(result["projects"][0]["aiAnalysis"]["summary"], "project:7")
        self.assertEqual(result["potentialProjects"][0]["aiAnalysis"]["summary"], "project:7")
        self.assertEqual(result["markets"][0]["top"][0]["aiAnalysis"]["summary"], "project:7")
        self.assertEqual(result["alerts"][0]["aiAnalysis"]["summary"], "alert:9")
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
