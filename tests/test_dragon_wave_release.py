import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dragon_wave_release as release
import quiet_http_server as server


class StrategyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dragon-release-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.args = ("PIUSDT", "2025-02-21", "2025-02-27", "futures", "active")
        self.make_case("v90", engine="old-engine")

    def make_case(self, version, state="complete", engine=release.ENGINE_SHA256, partial=False):
        folder = self.root / version
        folder.mkdir(exist_ok=True)
        pair, start, end, market, stage = self.args
        records = {}
        for interval in release.INTERVALS:
            key = "|".join((version, pair, start, end, interval, market, stage))
            filename = hashlib.sha256(key.encode()).hexdigest() + ".json.gz"
            (folder / filename).write_bytes(b"test-fixture")
            records[key] = {"pair": pair, "start": start, "end": end, "interval": interval,
                "market": market, "mainWaveStage": stage, "file": filename, "bytes": 12,
                "contextComplete": not partial, "contextIntervals": list(release.INTERVALS),
                "engineSha256": engine}
        value = {"version": version, "records": records, "cases": {
            "|".join((version, *self.args)): {"state": state, "engineSha256": engine}}}
        (folder / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def test_missing_new_release_keeps_complete_old_case(self):
        result = release.resolve_case(self.root, *self.args)
        self.assertEqual(result["selectedVersion"], "v90")
        self.assertTrue(result["pending"])
        self.assertIsNone(result["engineSha256"])

    def test_incomplete_case_never_publishes_new_intervals(self):
        for state, partial in (("running", False), ("partial", False), ("complete", True)):
            with self.subTest(state=state, partial=partial):
                self.make_case("v91", state=state, partial=partial)
                self.assertEqual(release.resolve_case(self.root, *self.args)["selectedVersion"], "v90")

    def test_wrong_engine_cannot_masquerade_as_new_release(self):
        self.make_case("v91", engine="old-engine")
        self.assertEqual(release.resolve_case(self.root, *self.args)["selectedVersion"], "v90")

    def test_all_five_complete_and_matching_engine_publish_atomically(self):
        self.make_case("v91")
        result = release.resolve_case(self.root, *self.args)
        self.assertEqual(result["selectedVersion"], "v91")
        self.assertFalse(result["pending"])
        self.assertEqual(result["engineSha256"], release.ENGINE_SHA256)

    def test_missing_or_truncated_file_retains_old_case(self):
        manifest = self.make_case("v91")
        record = next(iter(manifest["records"].values()))
        (self.root / "v91" / record["file"]).write_bytes(b"short")
        self.assertEqual(release.resolve_case(self.root, *self.args)["selectedVersion"], "v90")
        (self.root / "v91" / record["file"]).unlink()
        self.assertEqual(release.resolve_case(self.root, *self.args)["selectedVersion"], "v90")

    def test_invalid_old_case_is_not_a_false_ready_fallback(self):
        self.make_case("v90", partial=True)
        self.assertIsNone(release.resolve_case(self.root, *self.args)["selectedVersion"])

    def test_finished_incomplete_case_is_reported_for_attention_not_forever_building(self):
        value = self.make_case("v91", state="partial", partial=True)
        self.assertFalse(release.resolve_case(self.root, *self.args)["needsAttention"])
        value["status"] = {"state": "partial", "finishedAt": 1, "processedCases": 85, "totalCases": 85}
        (self.root / "v91/manifest.json").write_text(json.dumps(value), encoding="utf-8")
        result = release.resolve_case(self.root, *self.args)
        self.assertTrue(result["needsAttention"])
        self.assertEqual(result["selectedVersion"], "v90")

    def test_direct_new_cache_lookup_also_rejects_partial_case(self):
        self.make_case("v91", state="running")
        pair, start, end, market, stage = self.args
        with patch.object(server, "PRECOMPUTED_ROOT", self.root):
            self.assertIsNone(server.precomputed_lookup({"version": ["v91"], "pair": [pair],
                "start": [start], "end": [end], "interval": ["1h"], "market": [market], "stage": [stage]}))

    def test_process_liveness_probe_is_read_only(self):
        self.assertTrue(server.precompute_process_alive(os.getpid()))
        self.assertFalse(server.precompute_process_alive(-1))

    def test_external_publisher_is_not_preempted_or_duplicated(self):
        (self.root / "v91.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        with patch.object(server, "PRECOMPUTED_ROOT", self.root), \
                patch.object(server, "precompute_autostart_enabled", return_value=True), \
                patch.object(server.subprocess, "Popen") as spawn:
            self.assertTrue(server.ensure_precompute_running("v91", force=True, preempt=True))
            spawn.assert_not_called()

    def test_finished_offline_partial_catalog_does_not_restart_on_page_miss(self):
        manifest = self.make_case("v91", partial=True)
        manifest["status"] = {"state": "partial", "localOnly": True, "finishedAt": 1,
            "processedCases": 85, "totalCases": 85}
        (self.root / "v91/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(server, "PRECOMPUTED_ROOT", self.root), \
                patch.object(server, "historical_precompute_cases", return_value=[{}] * 85), \
                patch.object(server, "precompute_autostart_enabled", return_value=True), \
                patch.object(server.subprocess, "Popen") as spawn:
            self.assertTrue(server.precompute_manifest_complete("v91"))
            self.assertFalse(server.ensure_precompute_running("v91", force=True, preempt=True))
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
