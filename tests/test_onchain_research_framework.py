import unittest

from onchain_research_framework import (
    FRAMEWORK_VERSION,
    build_candidate_framework_snapshot,
    framework_assessment_complete,
    golden_leader_alert_decision,
    normalize_framework_assessment,
    promote_framework_candidate,
)


def v48_fields():
    return {
        "metaFamily": "源事件 → 当前CA → 同题材候选",
        "metaRole": "First Real Leader",
        "metaExpansion": "0-1h 建档，1-6h 买方与分发扩散",
        "survivalLabel": "strong-hold",
        "survivalAssessment": "1h 成交、新买家和流动性共同保持",
        "mechanismStrength": 62,
        "carrierStrength": 81,
        "longTermStage": "LT1",
        "thesisMemory": {
            "coreThesis": "可传播符号与真实买方开始共振",
            "terminalVision": "形成跨社区文化资产",
            "milestones": ["持币广度增长", "跨社区扩散"],
            "tokenValueCapture": "当前CA承载该符号的交易与社区共识",
            "invalidation": "买方、叙事与流动性同步衰减",
            "reactivationConditions": ["新事实与资金同时转强"],
        },
        "lifelines": {
            "project": "来源主张持续可验证",
            "narrative": "跨渠道注意力扩散",
            "token": "当前CA买方承接",
            "liquidity": "退出深度可用",
        },
        "reactivationEvidence": [],
        "personCatalyst": {"status": "none"},
        "marketMainline": {
            "status": "active", "asOf": 1_800_000_000_000,
            "primaryThemes": ["链上文化资产"], "phase": "accelerating",
            "leaders": ["LOBSTER"],
            "capitalAttention": "同类资产成交、买方和跨社区注意力同步增加",
            "evidence": ["同题材多个资产成交扩张", "独立新闻与社区注意力共振"],
            "candidateRelation": "core-leader",
            "relationReason": "当前候选在买方、流动性和传播上领先同题材资产",
            "nextTrigger": "跨链注意力继续扩散", "invalidation": "板块成交和买方同步退潮",
        },
        "hotspotOpportunity": {
            "eventId": "hot-lobster", "eventName": "龙虾热点",
            "relation": "independent-hotspot", "priority": "P1", "override": True,
            "officialClaimStatus": "非官方",
            "mappingFit": {"name": 90, "visual": 80, "semantic": 88, "time": 85, "culture": 82},
            "identityConclusion": "非官方社区部署，未冒充官方",
            "opportunityConclusion": "热点、映射和真实买盘同步承接，值得进入龙头竞争",
            "executionConclusion": "执行许可独立审计，不用热点覆盖风险",
            "nextTrigger": "跨平台扩散继续加速", "invalidation": "热点衰减且买方退出",
        },
    }


class OnchainResearchFrameworkTests(unittest.TestCase):
    def candidate(self):
        return {
            "network": "bsc",
            "contractAddress": "0x" + "a" * 40,
            "symbol": "LOBSTER",
            "name": "Lobster",
            "decision": "watch",
            "selectedScore": 64,
            "confidence": 88,
            "providers": ["dexscreener", "gmgn-trenches"],
            "dexId": "pancakeswap",
            "tradeUrl": "https://dexscreener.com/bsc/example",
            "quoteAsset": {"symbol": "WBNB", "name": "Wrapped BNB", "address": "0x" + "b" * 40},
            "narrativeContext": {
                "description": "社区围绕龙虾吉祥物传播，项目方资料仍需核验。",
                "websites": ["https://example.com"],
                "socials": ["https://x.com/example"],
            },
            "researchEvidence": {
                "identityStatus": "news-contract-explicit",
                "source": "news",
                "content": "新闻原文给出同一 CA，并提到社区活动。",
            },
            "walletProfile": {
                "coverage": True,
                "classification": "independent-validation",
                "independentHolders": 5,
                "score": 76,
                "top10Percent": 28,
            },
            "metrics": {
                "liquidityUsd": 45_000,
                "volumeH1Usd": 120_000,
                "volumeH6Usd": 180_000,
                "transactionsH1": 180,
                "buysH1": 126,
                "sellsH1": 54,
                "buyersM5": 35,
                "priceChangeM5": 18,
                "priceChangeH1": 65,
            },
            "observedAt": 1_800_000_000_000,
        }

    def test_snapshot_covers_identity_state_quote_buyers_attention_and_audit(self):
        row = self.candidate()
        snapshot = build_candidate_framework_snapshot(
            row,
            previous={"currentStage": "S4", "observedAt": row["observedAt"] - 60_000},
            observed_at=row["observedAt"],
        )

        self.assertEqual(snapshot["version"], FRAMEWORK_VERSION)
        self.assertEqual(snapshot["assetIdentity"]["assetClass"], "contract-token")
        self.assertEqual(snapshot["quoteAsset"]["symbol"], "WBNB")
        self.assertEqual(snapshot["currentStage"], "S6")
        self.assertEqual(snapshot["previousStage"], "S4")
        self.assertTrue(snapshot["stateChanged"])
        self.assertEqual(snapshot["paidBuyers"]["buysH1"], 126)
        self.assertIn("龙虾吉祥物", snapshot["attention"]["sourceClaim"])
        self.assertIn(snapshot["audit"]["executionPermission"], {"ALLOW", "CAUTION", "UNKNOWN"})
        self.assertEqual(snapshot["attentionState"], "A7")
        self.assertEqual(snapshot["attentionTransition"]["trigger"], "TR_LEADER_CONCENTRATION")
        self.assertEqual(snapshot["researchRetention"]["policy"], "KEEP")
        self.assertIn("narrativeDiscovery", snapshot["ledgers"])
        self.assertIn("leaderElection", snapshot["ledgers"])
        self.assertEqual(snapshot["longTermStage"], "LT0")
        self.assertIn(snapshot["survival"]["label"], {"strong-hold", "divergence", "decay", "dormant", "reactivating", "unknown"})
        self.assertIn("token", snapshot["lifelines"])
        self.assertIn("familyRole", snapshot["metaFamily"])
        self.assertEqual(snapshot["personCatalyst"]["status"], "none")
        self.assertEqual(snapshot["marketMainline"]["status"], "uncertain")
        self.assertEqual(snapshot["marketMainline"]["candidateRelation"], "uncertain")

    def test_confirmed_person_signal_is_a_separate_catalyst_ledger(self):
        row = self.candidate()
        row["personSignal"] = {
            "personName": "Market Mover", "personHandle": "marketmover", "personRole": "高影响人物",
            "sourceCategory": "notable", "sourceTier": "primary", "actionType": "quote",
            "identityStatus": "person-x-official-handle", "semanticDecisionVersion": "jev-semantic-v1",
            "semanticRelated": True, "semanticConfidence": 0.94,
            "semanticReason": "作者明确讨论该币官方账号",
            "postUrl": "https://x.com/marketmover/status/1", "publishedAt": row["observedAt"] - 30_000,
            "postText": "This token is interesting", "quoteText": "Official token launch",
        }

        catalyst = build_candidate_framework_snapshot(row)["personCatalyst"]

        self.assertEqual(catalyst["status"], "confirmed")
        self.assertEqual(catalyst["sourceTier"], "primary")
        self.assertEqual(catalyst["action"], "quote")
        self.assertEqual(catalyst["semanticConfidence"], 0.94)
        self.assertEqual(catalyst["ageMinutes"], 0.5)

    def test_non_official_hotspot_is_kept_as_high_priority_opportunity_not_identity_failure(self):
        row = self.candidate()
        row["researchEvidence"]["identityStatus"] = "community-ca-not-official"
        row["newsSignal"] = {"eventId": "hot-niulai", "eventName": "牛来公共热点"}
        row["hotspotRelation"] = "independent_hotspot"
        row["hotspotOverride"] = True
        row["hotspotPriority"] = "P0"

        hotspot = build_candidate_framework_snapshot(row)["hotspotOpportunity"]

        self.assertEqual(hotspot["relation"], "independent-hotspot")
        self.assertEqual(hotspot["priority"], "P0")
        self.assertTrue(hotspot["override"])
        self.assertIn("身份标签", hotspot["identityConclusion"])
        self.assertIn("独立审计", hotspot["executionConclusion"])

    def test_source_backed_candidate_can_enter_full_framework_review_without_price_score_rescue(self):
        row = self.candidate()
        row.update({"decision": "watch", "selectedScore": 41})
        row["metrics"].update({"liquidityUsd": 8_000, "volumeH1Usd": 7_500, "transactionsH1": 12})

        promoted = promote_framework_candidate(row)

        self.assertEqual(promoted["decision"], "shortlisted")
        self.assertGreaterEqual(promoted["selectedScore"], 60)
        self.assertTrue(any("V4.9" in reason for reason in promoted["reasons"]))

    def test_soft_risk_tags_do_not_delete_research_or_become_a_hard_block(self):
        row = self.candidate()
        row["metrics"]["liquidityUsd"] = 500
        row["walletProfile"]["top10Percent"] = 97
        snapshot = build_candidate_framework_snapshot(row)

        self.assertEqual(snapshot["researchRetention"]["policy"], "KEEP")
        self.assertEqual(snapshot["audit"]["executionPermission"], "CAUTION")
        self.assertEqual(snapshot["audit"]["hardBlockReason"], "")

        row["fatalExecutionRisk"] = "已确认无法卖出"
        blocked = build_candidate_framework_snapshot(row)
        self.assertEqual(blocked["researchRetention"]["policy"], "KEEP")
        self.assertEqual(blocked["audit"]["executionPermission"], "BLOCK")
        self.assertIn("无法卖出", blocked["audit"]["hardBlockReason"])

    def test_normalizer_keeps_three_ledgers_and_bounds_scores(self):
        result = normalize_framework_assessment({
            **v48_fields(),
            "potentialTier": "leader",
            "candidatePath": "meme-cultural-leader",
            "currentStage": "S6",
            "attentionState": "A6",
            "previousAttentionState": "A5",
            "attentionTransition": "链上买方开始承接链外注意力",
            "transitionTrigger": "TR_BUYER_RESPONSE",
            "narrativeDiscovery": "龙虾符号形成跨渠道传播",
            "mappingFit": "事件和当前CA存在来源支持的映射",
            "leaderElection": "当前合约在独立买方和成交扩散上领先",
            "candidateSet": ["bsc:0xaaa · 当前候选", "bsc:0xbbb · 挑战者"],
            "currentLeader": "bsc:0xaaa",
            "leaderRelation": "暂时领先，仍允许挑战者接管",
            "nextTrigger": "买方响应继续扩大",
            "riskTags": ["duplicate-image", "流动性待复核"],
            "hardBlockReason": "",
            "minAttentionUnit": "龙虾吉祥物",
            "primaryDriver": "社区符号与买方加速度共振",
            "leaderReason": "同题材独立买方和成交扩散领先",
            "nextTransition": "跨社区传播并维持净流入",
            "invalidation": "买家增速和流动性同时衰减",
            "discoveryScore": 108,
            "stateTransitionBonus": 25,
            "metaScore": 82,
            "opportunityScore": 86,
            "leaderScore": 84,
            "executionScore": 63,
            "riskScore": 47,
            "executionPermission": "caution",
            "auditStatus": "partial",
            "novelty": {"asset": 78, "protocol": 12, "mechanism": 8, "gameplay": 66},
            "historicalAnalogues": ["BONK", "WIF", "BONK"],
            "p0OfficialAsset": "不是官方首个资产，仍待核验",
            "actionWindow": "S6加速观察窗口",
        })

        self.assertEqual(result["version"], FRAMEWORK_VERSION)
        self.assertEqual(result["discoveryScore"], 100)
        self.assertEqual(result["stateTransitionBonus"], 20)
        self.assertEqual(result["executionPermission"], "CAUTION")
        self.assertEqual(result["historicalAnalogues"], ["BONK", "WIF"])
        self.assertEqual(result["novelty"]["asset"], 78)
        self.assertIn("官方首个", result["p0OfficialAsset"])
        self.assertIn("S6", result["actionWindow"])
        self.assertEqual(result["attentionState"], "A6")
        self.assertEqual(result["transitionTrigger"], "TR_BUYER_RESPONSE")
        self.assertEqual(result["candidateSet"], ["bsc:0xaaa · 当前候选", "bsc:0xbbb · 挑战者"])
        self.assertEqual(result["riskTags"], ["duplicate-image", "流动性待复核"])
        self.assertEqual(result["marketMainline"]["phase"], "accelerating")
        self.assertEqual(result["marketMainline"]["candidateRelation"], "core-leader")
        self.assertTrue(framework_assessment_complete({"frameworkAssessment": result}))

    def test_formal_recommendation_requires_market_mainline_or_explicit_uncertainty(self):
        fields = v48_fields()
        fields.pop("marketMainline")
        result = normalize_framework_assessment({
            **fields,
            "potentialTier": "leader", "currentStage": "S6",
            "attentionState": "A6", "attentionTransition": "A5→A6",
            "transitionTrigger": "TR_BUYER_RESPONSE",
            "narrativeDiscovery": "叙事可核验", "mappingFit": "映射可核验",
            "leaderElection": "买方领先", "nextTrigger": "继续扩散", "invalidation": "买方衰减",
            "executionPermission": "CAUTION",
        })

        self.assertFalse(framework_assessment_complete({"frameworkAssessment": result}))

        result["marketMainline"] = {
            "status": "uncertain", "asOf": 1_800_000_000_000, "primaryThemes": [],
            "phase": "unclear", "leaders": [], "capitalAttention": "", "evidence": [],
            "candidateRelation": "independent-catalyst",
            "relationReason": "批次证据不足，但当前候选存在独立官方事件催化",
            "nextTrigger": "出现跨资产资金共振", "invalidation": "独立催化无法核验",
        }
        self.assertTrue(framework_assessment_complete({"frameworkAssessment": result}))

    def test_v43_or_incomplete_assessment_is_not_a_v44_formal_recommendation(self):
        old = normalize_framework_assessment({
            "potentialTier": "leader", "currentStage": "S6",
            "primaryDriver": "买方扩散", "leaderReason": "暂时领先",
            "nextTransition": "继续扩散", "invalidation": "买方衰减",
            "executionPermission": "CAUTION",
        })
        old["version"] = "xmind-v4.3-full-1"
        self.assertFalse(framework_assessment_complete({"frameworkAssessment": old}))

        incomplete = {**old, "version": FRAMEWORK_VERSION, "attentionState": "A6"}
        self.assertFalse(framework_assessment_complete({"frameworkAssessment": incomplete}))

    def test_popup_requires_explicit_golden_or_leader_potential_but_keeps_risk_separate(self):
        analysis = {
            "verdict": "strong",
            "confidence": 86,
            "evidenceStatus": "supported",
            "evidenceRefs": ["story"],
            "frameworkAssessment": normalize_framework_assessment({
                **v48_fields(),
                "potentialTier": "leader",
                "candidatePath": "meme-cultural-leader",
                "currentStage": "S6",
                "attentionState": "A6",
                "attentionTransition": "A5→A6，买方开始承接",
                "transitionTrigger": "TR_BUYER_RESPONSE",
                "narrativeDiscovery": "龙虾符号已形成可核验传播",
                "mappingFit": "当前CA与事件存在来源支持的映射",
                "leaderElection": "当前候选在独立买方和成交扩散上领先",
                "nextTrigger": "持币广度继续增加",
                "minAttentionUnit": "龙虾吉祥物",
                "primaryDriver": "注意力与独立买方同步扩散",
                "leaderReason": "同题材综合领先",
                "nextTransition": "持币广度继续增加",
                "invalidation": "买方和流动性同步衰退",
                "discoveryScore": 81,
                "opportunityScore": 85,
                "leaderScore": 83,
                "executionScore": 42,
                "riskScore": 88,
                "executionPermission": "BLOCK",
            }),
        }

        decision = golden_leader_alert_decision(self.candidate(), analysis)

        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["label"], "龙头潜力")
        self.assertEqual(decision["executionPermission"], "BLOCK")
        self.assertFalse(decision["actionable"])

        analysis["frameworkAssessment"] = {
            **analysis["frameworkAssessment"],
            "potentialTier": "watch",
        }
        self.assertFalse(golden_leader_alert_decision(self.candidate(), analysis)["eligible"])


if __name__ == "__main__":
    unittest.main()
