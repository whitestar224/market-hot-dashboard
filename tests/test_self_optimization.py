import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class SelfOptimizationTests(unittest.TestCase):
    def test_normalize_keeps_only_actionable_unique_suggestions(self):
        suggestions = server.normalize_self_optimization_suggestions([
            {
                "category": "可靠性",
                "title": "修复失效源退避",
                "problem": "同一失效源持续重试",
                "improvement": "增加分源指数退避",
                "benefit": "减少无效请求",
                "priority": "high",
                "scope": "small",
                "confidence": 91,
                "actionable": True,
                "evidence": ["source A status=failed"],
            },
            {
                "category": "可靠性",
                "title": "修复失效源退避",
                "problem": "同一失效源持续重试",
                "improvement": "增加分源指数退避",
                "benefit": "重复项",
                "priority": "high",
                "scope": "small",
                "confidence": 91,
                "actionable": True,
            },
            {
                "category": "缺失功能",
                "title": "证据不足",
                "problem": "只是猜测",
                "improvement": "随意添加功能",
                "confidence": 50,
                "actionable": False,
            },
        ])

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["title"], "修复失效源退避")
        self.assertEqual(len(suggestions[0]["id"]), 16)

    def test_unchanged_snapshot_does_not_consume_codex_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            server.write_json_cache(state_path, {
                "snapshotFingerprint": "same",
                "candidateQueue": [],
                "suggestions": [],
            })
            with (
                patch.object(server, "SELF_OPTIMIZATION_STATE_PATH", state_path),
                patch.object(server, "self_optimization_project_snapshot", return_value={"snapshotFingerprint": "same"}),
                patch.object(server, "codex_cli_chat") as codex_chat,
            ):
                result = server.run_self_optimization_check()

            self.assertTrue(result["ok"])
            self.assertFalse(result["changed"])
            codex_chat.assert_not_called()

    def test_new_snapshot_queues_one_confirmable_popup(self):
        raw = {
            "suggestions": [{
                "category": "去重",
                "title": "合并重复提醒",
                "problem": "相同事件存在两条提醒",
                "improvement": "增加稳定事件指纹",
                "benefit": "降低弹窗干扰",
                "priority": "high",
                "scope": "small",
                "confidence": 94,
                "actionable": True,
                "evidence": ["recent error signature"],
            }]
        }
        response = {"choices": [{"message": {"content": json.dumps(raw, ensure_ascii=False)}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            with (
                patch.object(server, "SELF_OPTIMIZATION_STATE_PATH", state_path),
                patch.object(server, "self_optimization_project_snapshot", return_value={
                    "snapshotFingerprint": "new",
                    "fileCount": 10,
                    "testFileCount": 2,
                    "recentErrorSignatures": ["one"],
                }),
                patch.object(server, "codex_cli_fallback_available", return_value=True),
                patch.object(server, "codex_cli_chat", return_value=response),
                patch.object(server, "self_optimization_alert", return_value={"ok": True}) as alert,
            ):
                result = server.run_self_optimization_check()

            self.assertTrue(result["ok"])
            self.assertEqual(result["suggestion"]["status"], "pending")
            alert.assert_called_once()
            stored = server.read_json_cache(state_path)
            self.assertEqual(stored["suggestions"][0]["title"], "合并重复提醒")

    def test_non_actionable_and_evidence_free_suggestions_are_rejected(self):
        suggestions = server.normalize_self_optimization_suggestions([
            {
                "category": "可靠性",
                "title": "已经存在的降级机制",
                "problem": "现有代码已处理",
                "improvement": "无需重复修改",
                "confidence": 98,
                "actionable": False,
                "evidence": ["检查代码后确认已实现"],
            },
            {
                "category": "可靠性",
                "title": "没有证据的建议",
                "problem": "可能存在问题",
                "improvement": "尝试优化",
                "confidence": 95,
                "actionable": True,
                "evidence": [],
            },
        ])

        self.assertEqual(suggestions, [])

    def test_semantic_duplicate_of_resolved_suggestion_is_not_realerted(self):
        completed = {
            "category": "可靠性",
            "title": "AI接口失败时降级返回可用结果",
            "problem": "AI额度异常会让接口返回502",
            "improvement": "增加缓存回退和短期熔断",
            "fingerprint": "old-fingerprint",
            "id": "old-id",
            "createdAt": 100,
            "status": "completed",
        }
        duplicate = {
            "category": "可靠性",
            "title": "AI接口失败时降级为可用状态",
            "problem": "DeepSeek 402会让AI分析接口整页失败",
            "improvement": "使用缓存结果并增加失败冷却",
            "fingerprint": "new-fingerprint",
            "id": "new-id",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            server.write_json_cache(state_path, {
                "snapshotFingerprint": "same",
                "candidateQueue": [duplicate],
                "suggestions": [completed],
            })
            with (
                patch.object(server, "SELF_OPTIMIZATION_STATE_PATH", state_path),
                patch.object(server, "self_optimization_project_snapshot", return_value={"snapshotFingerprint": "same"}),
                patch.object(server, "self_optimization_alert") as alert,
            ):
                result = server.run_self_optimization_check()

            self.assertTrue(result["ok"])
            self.assertIsNone(result["suggestion"])
            alert.assert_not_called()
            stored = server.read_json_cache(state_path)
            self.assertEqual(stored["candidateQueue"], [])

    def test_confirmation_is_idempotent(self):
        suggestion = {
            "id": "abc123",
            "createdAt": 123456,
            "status": "pending",
            "title": "一个改进",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            server.write_json_cache(state_path, {"suggestions": [suggestion]})
            with (
                patch.object(server, "SELF_OPTIMIZATION_STATE_PATH", state_path),
                patch.object(server.threading, "Thread") as thread,
            ):
                first = server.confirm_self_optimization({"symbol": "abc123", "episode": 123456})
                second = server.confirm_self_optimization({"symbol": "abc123", "episode": 123456})

            self.assertTrue(first["accepted"])
            self.assertFalse(second["accepted"])
            self.assertEqual(second["status"], "queued")
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()

    def test_protected_and_runtime_paths_are_never_allowed(self):
        self.assertTrue(server.self_optimization_relative_path_allowed("tests/test_feature.py"))
        self.assertFalse(server.self_optimization_relative_path_allowed(".env"))
        self.assertFalse(server.self_optimization_relative_path_allowed("../server.py"))
        self.assertFalse(server.self_optimization_relative_path_allowed(".runtime-cache/state.json"))
        self.assertFalse(server.self_optimization_relative_path_allowed("assets/logo.png"))

    def test_self_optimization_popup_never_enters_news_trade(self):
        self.assertFalse(server.desktop_alert_is_news_trade_intake({
            "kind": "系统自优化",
            "source": "系统自检",
            "title": "改进建议",
        }))

    def test_verified_staged_change_is_backed_up_then_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            workspace = Path(temp_dir) / "workspace"
            work_root = Path(temp_dir) / "runtime"
            root.mkdir()
            workspace.mkdir()
            (root / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
            (workspace / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")
            baseline = {
                "sample.py": {"hash": server.self_optimization_file_hash(root / "sample.py")}
            }
            with (
                patch.object(server, "ROOT", root),
                patch.object(server, "SELF_OPTIMIZATION_WORK_ROOT", work_root),
            ):
                backup = server.self_optimization_apply_changes(
                    workspace,
                    ["sample.py"],
                    baseline,
                    "job-one",
                )

            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertEqual((backup / "sample.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_concurrent_live_edit_blocks_staged_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            workspace = Path(temp_dir) / "workspace"
            root.mkdir()
            workspace.mkdir()
            live = root / "sample.py"
            live.write_text("VALUE = 1\n", encoding="utf-8")
            baseline = {"sample.py": {"hash": server.self_optimization_file_hash(live)}}
            live.write_text("VALUE = 'user edit'\n", encoding="utf-8")
            (workspace / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")
            with (
                patch.object(server, "ROOT", root),
                patch.object(server, "SELF_OPTIMIZATION_WORK_ROOT", Path(temp_dir) / "runtime"),
                self.assertRaises(RuntimeError),
            ):
                server.self_optimization_apply_changes(
                    workspace,
                    ["sample.py"],
                    baseline,
                    "job-conflict",
                )

            self.assertEqual(live.read_text(encoding="utf-8"), "VALUE = 'user edit'\n")

    def test_restart_marks_an_orphaned_job_retryable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            server.write_json_cache(state_path, {
                "suggestions": [{"id": "retry-me", "status": "running"}],
                "job": {"suggestionId": "retry-me", "status": "running"},
            })
            with patch.object(server, "SELF_OPTIMIZATION_STATE_PATH", state_path):
                server.self_optimization_recover_interrupted_job()

            state = server.read_json_cache(state_path)
            self.assertEqual(state["job"]["status"], "interrupted")
            self.assertEqual(state["suggestions"][0]["status"], "failed")

    def test_restart_prunes_resolved_semantic_duplicates(self):
        completed = {
            "id": "done",
            "title": "AI接口失败时降级返回可用结果",
            "problem": "AI额度异常导致502",
            "improvement": "缓存回退和失败冷却",
            "status": "completed",
        }
        duplicate = {
            "id": "duplicate",
            "title": "AI接口失败时降级为可用状态",
            "problem": "DeepSeek 402导致接口失败",
            "improvement": "增加缓存结果和熔断",
            "status": "pending",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            server.write_json_cache(state_path, {
                "suggestions": [completed, duplicate],
                "candidateQueue": [{**duplicate, "id": "queued"}],
            })
            with patch.object(server, "SELF_OPTIMIZATION_STATE_PATH", state_path):
                server.self_optimization_recover_interrupted_job()

            state = server.read_json_cache(state_path)
            self.assertEqual(state["suggestions"][1]["status"], "resolved")
            self.assertEqual(state["candidateQueue"], [])

    def test_codex_uses_compatible_reviewed_workspace_mode(self):
        captured = {}

        def fake_run(command, **_kwargs):
            captured["command"] = command
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(json.dumps({
                "status": "skipped",
                "summary": "无需改动",
                "changedFiles": [],
                "tests": [],
                "notes": [],
            }, ensure_ascii=False), encoding="utf-8")
            return server.subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp_dir:
            job_root = Path(temp_dir) / "job"
            workspace = job_root / "workspace"
            workspace.mkdir(parents=True)
            with (
                patch.object(server, "codex_cli_executable", return_value="codex"),
                patch.object(server.subprocess, "run", side_effect=fake_run),
            ):
                result = server.self_optimization_run_codex(
                    workspace,
                    job_root,
                    {"title": "兼容参数"},
                )

        self.assertEqual(result["status"], "skipped")
        self.assertIn("--approve-for-me", captured["command"])
        self.assertNotIn("--sandbox", captured["command"])

    def test_codex_noop_is_resolved_silently_instead_of_reported_as_failure(self):
        suggestion = {
            "id": "already-fixed",
            "createdAt": 123456,
            "status": "queued",
            "title": "已经实现的降级能力",
            "fingerprint": "already-fixed-fingerprint",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            work_root = Path(temp_dir) / "runtime"
            server.write_json_cache(state_path, {"suggestions": [suggestion]})
            with (
                patch.object(server, "SELF_OPTIMIZATION_STATE_PATH", state_path),
                patch.object(server, "SELF_OPTIMIZATION_WORK_ROOT", work_root),
                patch.object(server, "self_optimization_copy_source_tree", return_value={}),
                patch.object(server, "self_optimization_run_codex", return_value={
                    "status": "skipped",
                    "summary": "检查后确认现有代码已经实现，无需重复修改",
                    "changedFiles": [],
                    "tests": [],
                    "notes": [],
                }),
                patch.object(server, "self_optimization_validate_workspace", return_value=([], [])),
                patch.object(server, "self_optimization_result_alert") as alert,
            ):
                server.self_optimization_execute_job("already-fixed", 123456)

            stored = server.read_json_cache(state_path)
            self.assertEqual(stored["job"]["status"], "already-resolved")
            self.assertEqual(stored["suggestions"][0]["status"], "resolved")
            alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
