import tempfile
import unittest
from pathlib import Path

import quiet_http_server as feedback_server


class DragonWaveFeedbackStorageTests(unittest.TestCase):
    def setUp(self):
        self.original_db = feedback_server.FEEDBACK_DB
        self.tempdir = tempfile.TemporaryDirectory()
        feedback_server.FEEDBACK_DB = Path(self.tempdir.name) / "feedback.db"
        feedback_server.init_feedback_db()

    def tearDown(self):
        feedback_server.FEEDBACK_DB = self.original_db
        self.tempdir.cleanup()

    @staticmethod
    def document(decision="confirmed", updated_at=10):
        return {
            "version": 1,
            "updatedAt": updated_at,
            "records": {
                "TUTUSDT|15m|1000": {
                    "key": "TUTUSDT|15m|1000",
                    "decision": decision,
                    "createdAt": 1,
                    "updatedAt": updated_at,
                    "pair": "TUTUSDT",
                    "interval": "15m",
                    "venue": "Binance",
                    "signal": {"time": 1000, "patternKey": "base"},
                }
            },
        }

    def test_local_sqlite_survives_a_fresh_read(self):
        device_id = "dragon-device-123456"
        feedback_server.save_local_feedback(device_id, self.document())
        loaded = feedback_server.load_local_feedback(device_id)
        self.assertEqual(loaded["records"]["TUTUSDT|15m|1000"]["decision"], "confirmed")

    def test_binance_candle_proxy_accepts_native_chinese_contract(self):
        query = {
            "market": ["futures"],
            "pair": ["币安人生USDT"],
            "interval": ["15m"],
            "start": ["1774828800000"],
            "end": ["1774832400000"],
            "limit": ["5"],
        }
        url = feedback_server.binance_candle_request_url(query)
        self.assertIn("fapi.binance.com/fapi/v1/klines", url)
        self.assertIn("symbol=%E5%B8%81%E5%AE%89%E4%BA%BA%E7%94%9FUSDT", url)
        self.assertIn("interval=15m", url)

    def test_binance_candle_proxy_rejects_unknown_market(self):
        with self.assertRaisesRegex(ValueError, "invalid Binance market"):
            feedback_server.binance_candle_request_url({
                "market": ["other"], "pair": ["币安人生USDT"], "interval": ["15m"],
                "start": ["1"], "end": ["2"],
            })

    def test_latest_decision_wins_during_merge(self):
        merged = feedback_server.merge_feedback_documents(
            self.document("confirmed", 10),
            self.document("denied", 20),
        )
        self.assertEqual(merged["records"]["TUTUSDT|15m|1000"]["decision"], "denied")

    def test_invalid_decisions_are_discarded(self):
        normalized = feedback_server.normalize_feedback_document(self.document("maybe", 10))
        self.assertEqual(normalized["records"], {})

    def test_three_state_feedback_builds_causal_optimization_rows(self):
        confirmed = self.document("confirmed", 10)
        confirmed["records"]["TUTUSDT|15m|1000"]["signal"].update({
            "foundationTypes": ["base"],
            "auxiliaryTypes": ["previousHigh"],
            "confluence": ["base", "previousHigh"],
            "rhythmScore": 92,
            "futureReturn": 88,
        })
        pending = self.document("pending", 20)
        pending["records"]["TUTUSDT|15m|1000"]["key"] = "TUTUSDT|15m|2000"
        pending["records"]["TUTUSDT|15m|1000"]["signal"]["time"] = 2000
        pending["records"] = {"TUTUSDT|15m|2000": pending["records"].pop("TUTUSDT|15m|1000")}
        denied = self.document("denied", 30)
        denied["records"]["TUTUSDT|15m|1000"]["key"] = "TUTUSDT|15m|3000"
        denied["records"]["TUTUSDT|15m|1000"]["signal"]["time"] = 3000
        denied["records"] = {"TUTUSDT|15m|3000": denied["records"].pop("TUTUSDT|15m|1000")}
        merged = feedback_server.merge_feedback_documents(confirmed, pending, denied)
        dataset = feedback_server.feedback_optimization_dataset(merged)
        self.assertEqual([row["label"] for row in dataset["rows"]], [1, 0, -1])
        self.assertEqual(dataset["summary"]["labeledCount"], 2)
        self.assertEqual(dataset["summary"]["pendingCount"], 1)
        self.assertEqual(dataset["summary"]["deniedPrototypeCount"], 1)
        self.assertEqual(dataset["summary"]["deniedPrototypeSampleCount"], 1)
        self.assertIn("foundation:base", dataset["rows"][0]["featureTokens"])
        self.assertNotIn("futureReturn", dataset["rows"][0]["metrics"])

    def test_cleared_feedback_is_kept_as_tombstone_but_removed_from_optimization(self):
        confirmed = self.document("confirmed", 10)
        confirmed["records"]["TUTUSDT|15m|1000"]["certaintyGrade"] = "A+"
        confirmed["records"]["TUTUSDT|15m|1000"]["structureTags"] = ["ema90Pullback"]
        cleared = self.document("cleared", 20)
        cleared["records"]["TUTUSDT|15m|1000"]["signal"]["manualCertaintyGrade"] = "A+"
        cleared["records"]["TUTUSDT|15m|1000"]["signal"]["manualStructureTags"] = ["ema90Pullback"]
        merged = feedback_server.merge_feedback_documents(confirmed, cleared)
        record = merged["records"]["TUTUSDT|15m|1000"]
        self.assertEqual(record["decision"], "cleared")
        self.assertEqual(record["optimizationRole"], "deleted")
        self.assertEqual(record["certaintyGrade"], "")
        self.assertEqual(record["structureTags"], [])
        self.assertNotIn("manualCertaintyGrade", record["signal"])
        self.assertNotIn("manualStructureTags", record["signal"])
        dataset = feedback_server.feedback_optimization_dataset(merged)
        self.assertEqual(dataset["datasetVersion"], 12)
        self.assertEqual(dataset["summary"]["total"], 0)

    def test_manual_certainty_grade_is_normalized_and_exported(self):
        confirmed = self.document("confirmed", 10)
        row = confirmed["records"]["TUTUSDT|15m|1000"]
        row["certaintyGrade"] = "a+"
        normalized = feedback_server.normalize_feedback_document(confirmed)
        saved = normalized["records"]["TUTUSDT|15m|1000"]
        self.assertEqual(saved["certaintyGrade"], "A+")
        self.assertEqual(saved["signal"]["manualCertaintyGrade"], "A+")
        dataset = feedback_server.feedback_optimization_dataset(normalized)
        self.assertEqual(dataset["datasetVersion"], 12)
        self.assertEqual(dataset["rows"][0]["certaintyGrade"], "A+")
        self.assertEqual(dataset["rows"][0]["metrics"]["manualCertaintyLevel"], 3)

    def test_causal_visual_signature_is_persisted_and_exported_for_model_training(self):
        confirmed = self.document("confirmed", 10)
        row = confirmed["records"]["TUTUSDT|15m|1000"]
        row["certaintyGrade"] = "A+"
        row["signal"]["visualSignature"] = {
            "version": 1,
            "model": "causal-kline-raster-v1",
            "featureCutoffTime": 999,
            "selectedCandleTime": 1000,
            "causality": "completed-candles-before-selected-index-only",
            "windows": [{
                "span": 40,
                "wick": "0101",
                "body": "0011",
                "ema": "6789",
                "volume": "4567",
                "closePath": [100, 200, 300],
                "rangePath": [300, 200, 100],
                "stats": {"drift": 100, "rangeCompression": 500},
            }],
        }
        normalized = feedback_server.normalize_feedback_document(confirmed)
        signature = normalized["records"]["TUTUSDT|15m|1000"]["signal"]["visualSignature"]
        self.assertEqual(signature["featureCutoffTime"], 999)
        self.assertEqual(signature["windows"][0]["stats"]["rangeCompression"], 500)
        dataset = feedback_server.feedback_optimization_dataset(normalized)
        self.assertEqual(dataset["rows"][0]["visualSignature"], signature)
        self.assertEqual(dataset["summary"]["visualLabeledCount"], 1)
        self.assertEqual(dataset["summary"]["visualAPlusCount"], 1)

    def test_manual_structure_tags_include_ema90_pullback_and_reject_unknown_values(self):
        confirmed = self.document("confirmed", 10)
        row = confirmed["records"]["TUTUSDT|15m|1000"]
        row["structureTags"] = ["horizontalLaunch", "ema90Pullback", "volumeBreakout", "nearPreviousHighConsolidation", "newCoinNotFalling", "mainWaveActive", "mainWaveExpected", "invalidTag"]
        normalized = feedback_server.normalize_feedback_document(confirmed)
        saved = normalized["records"]["TUTUSDT|15m|1000"]
        self.assertEqual(saved["structureTags"], ["horizontalLaunch", "ema90Pullback", "volumeBreakout", "nearPreviousHighConsolidation", "newCoinNotFalling", "mainWaveActive", "mainWaveExpected"])
        self.assertEqual(saved["signal"]["manualStructureTags"], saved["structureTags"])
        dataset = feedback_server.feedback_optimization_dataset(normalized)
        self.assertEqual(dataset["rows"][0]["structureTags"], saved["structureTags"])
        self.assertIn("manual-structure:ema90Pullback", dataset["rows"][0]["featureTokens"])
        self.assertIn("manual-structure:volumeBreakout", dataset["rows"][0]["featureTokens"])
        self.assertIn("manual-structure:nearPreviousHighConsolidation", dataset["rows"][0]["featureTokens"])
        self.assertIn("manual-structure:newCoinNotFalling", dataset["rows"][0]["featureTokens"])
        self.assertIn("manual-structure:mainWaveActive", dataset["rows"][0]["featureTokens"])
        self.assertIn("manual-structure:mainWaveExpected", dataset["rows"][0]["featureTokens"])

    def test_dataset_exports_interval_setup_tokens_for_supervised_calibration(self):
        confirmed = self.document("confirmed", 10)
        signal = confirmed["records"]["TUTUSDT|15m|1000"]["signal"]
        signal.update({
            "foundationTypes": ["base", "relaunch"],
            "auxiliaryTypes": ["previousHigh"],
            "structureShape": "converging-triangle",
        })
        dataset = feedback_server.feedback_optimization_dataset(confirmed)
        self.assertIn(
            "interval-setup:15m|base+relaunch>previousHigh|converging-triangle",
            dataset["rows"][0]["featureTokens"],
        )

    def test_dataset_builds_separate_positive_and_negative_causal_prototypes(self):
        positive = self.document("confirmed", 10)
        positive_row = positive["records"]["TUTUSDT|15m|1000"]
        positive_row["certaintyGrade"] = "A+"
        positive_row["structureTags"] = ["horizontalLaunch", "box"]
        positive_row["signal"].update({
            "consolidationBars": 52,
            "outerEdgeScore": 88,
            "ceilingTouches": 3,
            "launchDistancePercent": 1.4,
        })
        negative = self.document("denied", 20)
        negative_row = negative["records"]["TUTUSDT|15m|1000"]
        negative_row["key"] = "TUTUSDT|15m|2000"
        negative_row["structureTags"] = ["box", "previousHighBreakout"]
        negative_row["signal"].update({
            "time": 2000,
            "outerEdgeScore": 48,
            "ceilingTouches": 1,
            "launchDistancePercent": 8.6,
        })
        negative["records"] = {"TUTUSDT|15m|2000": negative["records"].pop("TUTUSDT|15m|1000")}
        dataset = feedback_server.feedback_optimization_dataset(
            feedback_server.merge_feedback_documents(positive, negative)
        )
        profile = dataset["supervisedPrototypeProfile"]
        self.assertEqual(profile["positiveAPlus"]["totalAPlusSamples"], 1)
        self.assertEqual(profile["negativeDenied"]["totalDeniedSamples"], 1)
        self.assertIn("quality:outer-edge:strong", profile["positiveAPlus"]["prototypes"][0]["sharedReasons"])
        self.assertIn("quality:launch-distance:far", profile["negativeDenied"]["prototypes"][0]["sharedReasons"])

    def test_strategy_prediction_and_manual_structure_review_are_persisted(self):
        confirmed = self.document("confirmed", 10)
        row = confirmed["records"]["TUTUSDT|15m|1000"]
        row["structureTags"] = ["horizontalLaunch", "fallingWedge"]
        row["predictedStructureTags"] = ["horizontalLaunch", "box"]
        normalized = feedback_server.normalize_feedback_document(confirmed)
        saved = normalized["records"]["TUTUSDT|15m|1000"]
        self.assertEqual(saved["predictedStructureTags"], ["horizontalLaunch", "box"])
        self.assertEqual(saved["structureReview"]["matched"], ["horizontalLaunch"])
        self.assertEqual(saved["structureReview"]["addedByUser"], ["fallingWedge"])
        self.assertEqual(saved["structureReview"]["removedByUser"], ["box"])
        dataset = feedback_server.feedback_optimization_dataset(normalized)
        self.assertIn("strategy-structure:box", dataset["rows"][0]["featureTokens"])
        self.assertIn("review-added:fallingWedge", dataset["rows"][0]["featureTokens"])


if __name__ == "__main__":
    unittest.main()
