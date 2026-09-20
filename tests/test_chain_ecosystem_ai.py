import tempfile
import json
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
    def test_high_potential_block_alert_opens_research_page_not_exchange(self):
        row = {"network": "bsc", "contractAddress": "0x" + "a" * 40, "symbol": "DOG"}
        analysis = {
            "verdict": "strong", "confidence": 88, "evidenceStatus": "supported", "evidenceRefs": ["story"],
            "summary": "题材和买方加速度共振，但合约审计阻断执行", "risk": "权限风险未解除",
            "frameworkAssessment": {
                "version": server.FRAMEWORK_VERSION,
                "potentialTier": "golden-dog", "opportunityScore": 86, "leaderScore": 75,
                "attentionState": "A6", "attentionTransition": "A5→A6，买方承接",
                "transitionTrigger": "TR_BUYER_RESPONSE",
                "narrativeDiscovery": "题材来源和传播路径可核验",
                "mappingFit": "当前CA与事件存在来源支持的映射",
                "leaderElection": "当前候选在买方响应上领先",
                "nextTrigger": "等待权限风险解除",
                "invalidation": "确认恶意权限或无法交易",
                "executionScore": 32, "riskScore": 91, "executionPermission": "BLOCK",
                "primaryDriver": "独立买方和跨社区传播同步加速", "leaderReason": "同题材扩散领先",
                "nextTransition": "等待权限风险解除",
            },
        }
        with patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            result = server.send_fast_onchain_alert(row, analysis, {"key": "bsc:dog", "first_seen_at": 1000, "analyzed_at": 4000})

        self.assertTrue(result["ok"])
        payload = launch.call_args.args[0]
        self.assertIn("大金狗潜力", payload["title"])
        self.assertIn("仅研究，不可直接执行", payload["body"])
        self.assertIn("price-watch.html?mode=chains", payload["url"])
        self.assertNotIn("web3.binance.com", payload["url"])

    def test_live_trench_ai_subject_uses_gmgn_facts_without_claiming_social_verification(self):
        row = {
            "network": "solana",
            "contractAddress": "Token111111111111111111111111111111111111",
            "symbol": "LIVE",
            "name": "Live Token",
            "launchpad": "pump-fun",
            "metrics": {"marketCapUsd": 200000, "liquidityUsd": 45000, "volumeH1Usd": 24000},
            "launchFacts": {"holders": 850, "top10Percent": 32, "smartMoneyHolders": 4},
            "narrativeContext": {"description": "项目自填资料", "socials": ["https://x.com/live"]},
        }

        subjects = server.onchain_trench_ai_subjects([row], {"provider": "deepseek", "model": "test"})

        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0]["key"], f"trench:solana:{row['contractAddress']}")
        self.assertEqual(subjects[0]["facts"]["gmgnFacts"]["smartMoneyHolders"], 4)
        self.assertIn("未读取或核验社交帖子正文", subjects[0]["facts"]["evidenceScope"])

    def test_fast_narrative_preserves_specific_research_and_requires_real_source_refs(self):
        row = {"network": "robinhood", "contractAddress": "0x" + "a" * 40, "symbol": "EXAMPLE",
               "researchEvidence": {"source": "news", "content": "来源称该项目试图把商品交易带上链，产品交付待核验。"},
               "crossValidation": {"status": "multi-source-contract", "evidence": [{
                   "source": "研究群", "content": "群友明确提及同一CA，但产品仍待核验",
                   "matchType": "exact-contract", "repeatedCount": 1,
               }]}}
        item = {"key": server.onchain_candidate_key(row), "verdict": "strong", "confidence": 85,
                "summary": "商品交易上链题材有具体产品方向，但当前资料尚不能证明交易系统已交付。",
                "evidenceStatus": "partial", "evidenceRefs": ["story", "made-up"],
                "narrative": {"thesis": "项目声称服务商品交易，代币如何承接收入仍未提供机制说明。",
                              "attention": "该链近期商品交易叙事受到关注；独立用户需求仍需验证。",
                              "evidence": "只有输入新闻的项目声称；没有产品交付和真实用户数据。",
                              "invalidation": "若合约不是新闻对应标的，或无法验证商品交易功能，应撤回判断。"},
                "frameworkAssessment": {
                    "version": server.FRAMEWORK_VERSION,
                    "potentialTier": "leader", "candidatePath": "rwa-mechanism-leader", "currentStage": "S5",
                    "attentionState": "A5", "previousAttentionState": "A4",
                    "attentionTransition": "A4→A5，新闻事件开始映射到当前CA",
                    "transitionTrigger": "TR_PRIMARY_EVENT",
                    "narrativeDiscovery": "商品上链题材出现具体来源，但产品交付仍待核验",
                    "mappingFit": "新闻内容和当前CA存在明确映射，官方性仍需复核",
                    "leaderElection": "当前只确认一个候选，尚不能证明已形成共识龙头",
                    "candidateSet": ["robinhood:当前CA · leader-candidate"],
                    "currentLeader": "未形成", "leaderRelation": "当前为待验证候选",
                    "nextTrigger": "验证产品交付与独立用户",
                    "chainContext": "新链早期商品资产试验", "assetIdentity": "商品交易协议的合约代币",
                    "minAttentionUnit": "商品上链", "primaryDriver": "新闻催化和早期交易形成共振",
                    "leaderReason": "同类题材中来源和成交验证更完整", "nextTransition": "验证产品交付与独立用户",
                    "invalidation": "合约身份错误或产品无法验证", "firstness": "最早批次候选，官方性待核验",
                    "quoteMigration": "尚未形成报价迁移", "historicalAnalogues": ["早期RWA协议"],
                    "discoveryScore": 83, "stateTransitionBonus": 12, "metaScore": 76,
                    "opportunityScore": 84, "leaderScore": 81, "executionScore": 57, "riskScore": 61,
                    "executionPermission": "CAUTION", "auditStatus": "partial",
                }}
        def response(messages, settings):
            prompt = str(messages)
            self.assertIn("失效", prompt)
            self.assertIn("Firstness", prompt)
            self.assertIn("Quote Migration", prompt)
            self.assertIn("执行许可", prompt)
            self.assertIn("群友明确提及同一CA", prompt)
            self.assertIn("重复转发不算独立证据", prompt)
            self.assertNotIn("不超过16个", prompt)
            self.assertGreater(settings["maxTokens"], 1000)
            self.assertTrue(settings["_codexWebSearch"])
            self.assertEqual(settings["_codexModel"], "gpt-6-astra")
            self.assertEqual(settings["_codexReasoningEffort"], "high")
            return {"choices": [{"message": {"content": json.dumps({"items": [item]}, ensure_ascii=False)}}], "_provider": "test"}
        with patch.object(server, "system_llm_settings", return_value={}), patch.object(server, "deepseek_enabled", return_value=True), patch.object(server, "deepseek_chat", side_effect=response):
            result = server.analyze_fast_onchain_candidates([row])[item["key"]]
            self.assertEqual(result["evidenceRefs"], ["story"])
            self.assertEqual(result["verdict"], "watch")
            self.assertEqual(result["narrative"], item["narrative"])
            self.assertEqual(result["frameworkAssessment"]["potentialTier"], "leader")
            self.assertEqual(result["frameworkAssessment"]["executionPermission"], "CAUTION")
            row.pop("researchEvidence")
            item["evidenceStatus"] = "supported"
            result = server.analyze_fast_onchain_candidates([row])[item["key"]]
            self.assertEqual(result["evidenceStatus"], "insufficient")
            self.assertEqual(result["verdict"], "watch")

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
            "identitySummary": "这是 BNB Chain 上的社区 Meme，核心炒吉祥物叙事，不是功能型项目。",
            "summary": "主网生态扩张",
            "catalyst": "官方公布代币计划",
            "risk": "交易深度仍需验证",
            "nextFocus": "观察链上资金",
            "tags": ["主网", "主网", "DEX"],
            "frameworkAssessment": {"potentialTier": "golden-dog", "opportunityScore": 109,
                                    "leaderScore": 81, "executionPermission": "block"},
        })

        self.assertEqual(result["confidence"], 100)
        self.assertEqual(result["narrativeStrength"], 0)
        self.assertIn("不是功能型项目", result["identitySummary"])
        self.assertEqual(result["tags"], ["主网", "DEX"])
        self.assertEqual(result["frameworkAssessment"]["opportunityScore"], 100)
        self.assertEqual(result["frameworkAssessment"]["executionPermission"], "BLOCK")
        self.assertEqual(result["frameworkAssessment"]["version"], "")
        self.assertFalse(server.framework_assessment_complete(result))

    def test_daily_research_only_schedules_quantitative_shortlist_for_ai(self):
        payload = sample_payload()
        payload["dailyResearch"] = {
            "selected": [{
                "network": "solana",
                "contractAddress": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6QXEk9dWQFwe",
                "symbol": "BONK",
                "name": "Bonk",
                "candidateType": "meme",
                "decision": "shortlisted",
                "memeScore": 82,
                "projectScore": 54,
                "selectedScore": 82,
                "confidence": 86,
                "metrics": {"liquidityUsd": 120000, "volumeH1Usd": 210000},
                "reasons": ["买方扩散"],
                "risks": ["待核验持仓集中度"],
                "walletProfile": {"classification": "independent-validation", "independentHolders": 5},
                "crossValidation": {"status": "multi-source-contract", "independentSourceCount": 2},
                "sameSymbolRole": "leader-candidate",
                "sameSymbolLeaderReason": "同名竞争中综合领先",
            }],
            "watching": [{"network": "base", "contractAddress": "0x0000000000000000000000000000000000000001"}],
        }

        subjects = server.chain_ecosystem_ai_subjects(payload, {"provider": "deepseek", "model": "test"})

        keys = {row["key"] for row in subjects}
        self.assertIn("research:solana:DezXAZ8z7PnrnRJjz3wXBoRgixCa6QXEk9dWQFwe", keys)
        self.assertNotIn("research:base:0x0000000000000000000000000000000000000001", keys)
        research = next(row for row in subjects if row["key"].startswith("research:solana:"))
        self.assertEqual(research["facts"]["walletProfile"]["independentHolders"], 5)
        self.assertEqual(research["facts"]["crossValidation"]["independentSourceCount"], 2)
        self.assertEqual(research["facts"]["sameSymbolRole"], "leader-candidate")

    def test_daily_research_is_replaced_by_gmgn_trench_coins_for_the_requested_day(self):
        day = "2026-09-20"
        day_start = int(server.time.mktime(server.time.strptime(day, "%Y-%m-%d")) * 1000)
        fresh = {
            "network": "solana",
            "contractAddress": "FreshTrench111111111111111111111111111111111",
            "symbol": "FRESH",
            "name": "Fresh Trench",
            "poolCreatedAt": day_start + 60_000,
            "decision": "warming",
            "selectedScore": 78,
            "metrics": {"liquidityUsd": 35_000},
        }
        stale = {**fresh, "contractAddress": "OldResearch111111111111111111111111111111", "symbol": "OLD", "poolCreatedAt": day_start - 86_400_000}
        filtered = {**fresh, "contractAddress": "FilteredTrench111111111111111111111111111", "symbol": "FILTERED", "decision": "filtered", "poolCreatedAt": day_start + 120_000}
        base = {
            "day": day,
            "selected": [stale],
            "funnel": {"discovered": 99, "selected": 1},
            "reviewQueue": {"pending": 4},
        }
        with patch.object(server, "fetch_gmgn_trenches_hot_board", return_value={
            "rows": [fresh, stale, filtered],
            "updatedAt": day_start + 300_000,
            "sourceStatus": {"solana": "ok"},
        }), patch.object(server.ONCHAIN_FAST_RESEARCH, "ingest"):
            result = server.gmgn_trench_daily_research_payload(base, research_day=day, now_ms=day_start + 600_000)

        self.assertEqual([row["symbol"] for row in result["selected"]], ["FRESH"])
        self.assertTrue(result["gmgnTrenchOnly"])
        self.assertEqual(result["researchSourceLabel"], "GMGN 战壕今日新币 · V4.4精选")
        self.assertEqual(result["funnel"]["discovered"], 1)
        self.assertEqual(result["selectedTotal"], 1)

    def test_gmgn_trench_research_public_list_requires_v44_selection(self):
        day = "2026-09-20"
        day_start = int(server.time.mktime(server.time.strptime(day, "%Y-%m-%d")) * 1000)
        fresh = {
            "network": "solana",
            "contractAddress": "FreshTrench222222222222222222222222222222222",
            "symbol": "FRESH",
            "name": "Fresh Trench",
            "poolCreatedAt": day_start + 60_000,
            "decision": "shortlisted",
            "selectedScore": 88,
            "metrics": {"liquidityUsd": 35_000},
        }
        with patch.object(server, "fetch_gmgn_trenches_hot_board", return_value={
            "rows": [fresh],
            "updatedAt": day_start + 300_000,
            "sourceStatus": {"solana": "ok"},
        }), patch.object(server.ONCHAIN_FAST_RESEARCH, "ingest"), patch.object(
            server.ONCHAIN_FAST_RESEARCH,
            "attach",
            return_value={
                "selected": [{**fresh, "fastResearch": True, "researchTier": "ai-recommended"}],
                "provisional": [],
                "reviewQueue": {},
            },
        ):
            raw = server.gmgn_trench_daily_research_payload({}, research_day=day, now_ms=day_start + 600_000)
            result = server.attach_gmgn_trench_research(raw)

        self.assertEqual([row["symbol"] for row in result["selected"]], ["FRESH"])
        self.assertEqual(result["researchSystem"], "onchain-fast-v4.4")
        self.assertTrue(result["fastResearchManaged"])

    def test_chain_research_prompt_uses_evidence_chain_instead_of_price_chasing(self):
        prompt = server.chain_ecosystem_ai_prompt([{"index": 1, "key": "research:solana:abc", "type": "research_candidate", "facts": {}}])
        content = "\n".join(row["content"] for row in prompt)

        self.assertIn("CryptoD 式证据链", content)
        self.assertIn("来源记录→项目研究卡→钱包验证→结果记录", content)
        self.assertIn("传播出币圈", content)
        self.assertIn("注意力复燃型 Meme", content)
        self.assertIn("代币价值承接", content)
        self.assertIn("多个地址跟随同一来源不能算多个独立判断", content)
        self.assertIn("钻石手", content)
        self.assertIn("高频 PVP", content)
        self.assertIn("同名币", content)
        self.assertIn("持币地址增长是否减速", content)
        self.assertIn("相同文案的跨群转发只算一条", content)
        self.assertIn("普通群友或KOL喊单不能单独升级结论", content)

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
