import copy
import unittest

from event_flow_window import attention_evidence, attention_window, HOUR


class AttentionWindowTests(unittest.TestCase):
    now = 1788860000000

    def sample(self):
        topic = {"title": "平台推出新的发行机制", "timestamp": self.now - 15 * 60000,
                 "aiAnalysisStatus": "ready", "sourceActive": True, "eventHeatScore": 80,
                 "relatedNews": [{"title": "社区开始围绕新机制创作", "timestamp": self.now - 5 * 60000}]}
        analysis = {"verdict": "trade-candidate", "confidence": 85, "narrativeStrength": 80,
                    "primarySymbol": "EXAMPLE", "thesis": "新发行方式引起关注", "catalyst": "新机制上线"}
        analysis.update(attentionStage="rising", catalystEvidenceId=attention_evidence(topic)[-1]["id"])
        return topic, analysis

    def check(self, topic, analysis, now=None, opportunity=True):
        return attention_window(topic, analysis, now_ms=self.now if now is None else now, opportunity=opportunity)

    def test_fresh_growing_opportunity_not_a_ticker_allowlist(self):
        topic, analysis = self.sample()
        for symbol in ("EXAMPLE", "NEVER_SEEN", "新标的"):
            self.assertTrue(self.check(topic, {**analysis, "primarySymbol": symbol})["eligible"])

    def test_price_gain_does_not_mean_the_emotion_window_has_passed(self):
        topic, analysis = self.sample()
        topic.update(eventStage="peak", newsTradePhase="fermented", fullyFermented=True, impliedGainPct=70000)
        self.assertTrue(self.check(topic, analysis)["eligible"])

    def test_explicit_ai_cooling_unknown_or_missing_opportunity_are_not_admitted(self):
        topic, analysis = self.sample()
        for stage in ("peak", "cooling", "expired", "unknown"):
            self.assertFalse(self.check(topic, {**analysis, "attentionStage": stage})["eligible"])
        missing_stage = {key: value for key, value in analysis.items() if key != "attentionStage"}
        self.assertFalse(self.check(topic, missing_stage)["eligible"])
        self.assertFalse(self.check(topic, analysis, opportunity=False)["eligible"])
        self.assertFalse(self.check({**topic, "sourceActive": False}, analysis)["eligible"])
        self.assertFalse(self.check({**topic, "aiAnalysisStatus": "pending"}, analysis)["eligible"])

    def test_polling_ai_refresh_and_reposts_cannot_rejuvenate_old_event(self):
        topic, analysis = self.sample()
        topic.update(timestamp=self.now - 25 * HOUR, aiAnalysisUpdatedAt=self.now)
        topic["relatedNews"].append({"title": topic["title"], "timestamp": self.now})
        self.assertFalse(self.check(topic, analysis)["eligible"])
        self.assertEqual(min(row["timestamp"] for row in attention_evidence(topic)), self.now - 25 * HOUR)

    def test_old_token_can_reenter_only_on_an_identified_new_catalyst(self):
        topic, analysis = self.sample()
        topic["timestamp"] = self.now - 20 * 24 * HOUR
        topic["relatedNews"].append({"title": "创始人刚确认全新的合作细节", "timestamp": self.now - 60000})
        key = next(row["id"] for row in attention_evidence(topic) if "全新" in row["title"])
        analysis.update(attentionStage="ignition", catalystEvidenceId=key)
        self.assertTrue(self.check(topic, analysis)["eligible"])
        self.assertFalse(self.check(topic, {**analysis, "catalystEvidenceId": "invented"})["eligible"])

    def test_single_fresh_catalyst_needs_explicit_high_quality_ignition_not_one_count_inflated_velocity(self):
        topic, analysis = self.sample()
        topic["relatedNews"] = []
        topic["topicMetrics"] = {"velocityScore": 100}
        self.assertFalse(self.check(topic, analysis)["eligible"])
        analysis.update(attentionStage="ignition", catalystEvidenceId=attention_evidence(topic)[0]["id"])
        self.assertTrue(self.check(topic, analysis)["eligible"])
        self.assertFalse(self.check(topic, {**analysis, "narrativeStrength": 20})["eligible"])

    def test_clock_expiry_and_missing_times_fail_closed_without_new_ai(self):
        topic, analysis = self.sample()
        window = self.check(topic, analysis)
        self.assertFalse(self.check(topic, analysis, now=window["expiresAt"])["eligible"])
        self.assertFalse(self.check({**topic, "timestamp": 0, "relatedNews": [], "firstSeenAt": self.now}, analysis)["eligible"])

    def test_existing_hard_risk_block_and_explicit_no_chase_stay_out(self):
        topic, analysis = self.sample()
        topic["memeCandidates"] = [{"symbol": "EXAMPLE", "security": {"hardBlocked": True}}]
        self.assertFalse(self.check(topic, analysis)["eligible"])
        topic["memeCandidates"] = []
        self.assertFalse(self.check(topic, {**analysis, "actionHint": "已错过，等待回落"})["eligible"])

    def test_no_input_mutation_or_sliding_expiry(self):
        topic, analysis = self.sample()
        before = copy.deepcopy(topic)
        first = self.check(topic, analysis)
        second = self.check(topic, analysis, now=self.now + 10000)
        self.assertEqual(first, second)
        self.assertEqual(topic, before)


if __name__ == "__main__":
    unittest.main()
