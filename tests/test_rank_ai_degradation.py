import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import requests


class RankAiDegradationTests(unittest.TestCase):
    def setUp(self):
        # Load only the functions under test: importing server has runtime side effects.
        tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"rank_ai_chat", "deepseek_rank_insights_payload"}]
        self.clock = Mock(return_value=100)
        self.chat = Mock()
        self.ns = dict(Any=object, threading=threading, requests=requests,
                       time=SimpleNamespace(monotonic=self.clock, time=lambda: 100, sleep=Mock()),
                       RANK_AI_FAILURE_LOCK=threading.Lock(), RANK_AI_RETRY_AFTER=0,
                       DEEPSEEK_INSIGHTS_LOCK=threading.Lock(), deepseek_chat=self.chat)
        exec(compile(ast.Module(body=functions, type_ignores=[]), "server.py", "exec"), self.ns)

    def test_http_errors_and_failed_cli_are_circuit_broken_then_recover(self):
        for status in (402, 500, 503):
            with self.subTest(status=status):
                self.ns["RANK_AI_RETRY_AFTER"] = 0
                self.clock.return_value = 100
                response = requests.Response()
                response.status_code = status
                self.chat.reset_mock()
                self.chat.side_effect = requests.HTTPError(response=response)
                call = self.ns["rank_ai_chat"]
                self.assertIsNone(call([], {}))
                self.assertIsNone(call([], {}))
                self.assertEqual(self.chat.call_count, 1)
                self.clock.return_value = 161
                self.chat.side_effect = None
                self.chat.return_value = {"choices": []}
                self.assertEqual(call([], {}), {"choices": []})
        self.ns["RANK_AI_RETRY_AFTER"] = 0
        self.chat.side_effect = RuntimeError("both providers unavailable")
        self.assertIsNone(self.ns["rank_ai_chat"]([], {}))

    def test_partial_cache_survives_and_rules_are_not_persisted_during_failure(self):
        cached = {"detail": "cached", "provider": "deepseek"}
        cache = {"cached": {"updatedAt": 100000, "insight": cached}}
        save = Mock()
        self.ns.update(
            clean_feed_text=lambda value, limit: value or "", clean_model_provider=lambda value: value,
            clean_model_name=lambda value: value, deepseek_enabled=lambda settings: True,
            deepseek_compact_rows=lambda payload, settings: [{"key": key} for key in ("cached", "new", "empty")],
            deepseek_cache_ttl_seconds=lambda: 60, deepseek_load_insight_cache=lambda: cache,
            deepseek_row_hash=lambda row, mode, settings: row["key"], safe_float=lambda value, default=0: float(value or default),
            normalize_deepseek_insight=lambda item: dict(item) if item.get("detail") else None,
            deepseek_batch_rows=lambda settings: 1, env_value=lambda name, default: default,
            deepseek_rank_prompt=lambda rows, mode: [], deepseek_save_insight_cache=save,
            deepseek_context_fallback=lambda row, mode: {"detail": "local", "provider": "rules"} if row["key"] == "new" else None)
        self.chat.side_effect = RuntimeError("both providers unavailable")
        result = self.ns["deepseek_rank_insights_payload"]({}, {"provider": "deepseek", "model": "test"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["reasonCode"], "ai_unavailable")
        self.assertEqual(result["insights"]["cached"]["detail"], "cached")
        self.assertEqual(result["insights"]["new"]["provider"], "rules")
        self.assertNotIn("empty", result["insights"])
        self.assertEqual(set(cache), {"cached"})
        self.chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
