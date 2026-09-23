import unittest

from jev_decision import _normalize
from onchain_research_framework import FRAMEWORK_VERSION
from rapid_decision_compare import RAPID_DECISION_VERSION, _merge
from rapid_decision_training import readiness_from_counts, target_from_analysis


def decision(priority, deep, watch, reject):
    return {
        "priority": priority,
        "confidence": max(deep, watch, reject),
        "probabilities": {"deep-research": deep, "watch": watch, "reject": reject},
        "goodCandidateProbability": deep + 0.35 * watch,
        "latencyMs": 100,
    }


class RapidDecisionCompareTests(unittest.TestCase):
    def test_jev_reject_is_the_final_decision(self):
        merged = _merge("bsc:0xabc", decision("reject", 0.04, 0.06, 0.90))
        self.assertEqual(merged["version"], RAPID_DECISION_VERSION)
        self.assertEqual(merged["priority"], "reject")
        self.assertEqual(merged["comparisonStatus"], "jev-only")
        self.assertEqual(merged["decisionSource"], "jev-primary")
        self.assertEqual(merged["primaryModel"], "jev")
        self.assertEqual(merged["modelCount"], 1)
        self.assertEqual(merged["jevWeight"], 1.0)
        self.assertEqual(merged["probabilities"]["reject"], 0.9)
        self.assertTrue(merged["agreement"])
        self.assertNotIn("secondaryModel", merged)

    def test_jev_deep_research_is_the_final_decision(self):
        merged = _merge("bsc:0xdef", decision("deep-research", 0.86, 0.09, 0.05))
        self.assertEqual(merged["priority"], "deep-research")
        self.assertEqual(merged["comparisonStatus"], "jev-only")
        self.assertEqual(merged["goodCandidateProbability"], 0.8915)

    def test_missing_jev_does_not_create_fallback_decision(self):
        self.assertIsNone(_merge("solana:mint", None))

    def test_jev_response_normalizes_without_exposing_credentials(self):
        result = _normalize(
            {"network": "bsc", "contractAddress": "0xabc"},
            {"model": "jev-1.13.0", "answers": {"research_priority": {
                "choice": "watch", "confidence": 0.8,
                "probabilities": {"deep-research": 0.03, "watch": 0.86, "reject": 0.11},
            }}},
            1800,
            "req-test",
        )
        self.assertEqual(result["priority"], "watch")
        self.assertEqual(result["confidence"], 0.86)
        self.assertEqual(result["requestId"], "req-test")
        self.assertNotIn("apiKey", result)

    def test_training_labels_require_current_v47_for_positive_examples(self):
        self.assertEqual(target_from_analysis({
            "narrativeVersion": 7, "verdict": "strong",
            "frameworkAssessment": {"version": FRAMEWORK_VERSION},
        }), "deep-research")
        self.assertEqual(target_from_analysis({"narrativeVersion": 7, "verdict": "watch"}), "watch")
        self.assertEqual(target_from_analysis({"narrativeVersion": 7, "verdict": "avoid"}), "reject")
        self.assertEqual(target_from_analysis({"narrativeVersion": 7, "verdict": "strong"}), "")

    def test_training_readiness_blocks_tiny_or_imbalanced_sets(self):
        early = readiness_from_counts({"deep-research": 0, "watch": 12, "reject": 5})
        self.assertFalse(early["shadowReady"])
        ready = readiness_from_counts({"deep-research": 300, "watch": 800, "reject": 400})
        self.assertTrue(ready["calibratorReady"])


if __name__ == "__main__":
    unittest.main()
