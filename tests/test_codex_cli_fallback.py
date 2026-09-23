import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

import server


class CodexCliFallbackTests(unittest.TestCase):
    def test_startup_ai_reconnect_clears_old_cooldowns_and_requeues_failed_research(self):
        response = {
            "choices": [{"message": {"content": '{"connected":true}'}}],
            "_provider": "codex-cli",
        }

        def connected(messages, settings):
            self.assertEqual(server.CODEX_CLI_UNAVAILABLE_UNTIL, 0.0)
            self.assertEqual(server.LLM_API_UNAVAILABLE_UNTIL, 0.0)
            self.assertEqual(server.RANK_AI_RETRY_AFTER, 0.0)
            self.assertEqual(server.NEWS_TRADE_AI_RETRY_AFTER, {})
            self.assertEqual(server.CHAIN_ECOSYSTEM_AI_RETRY_AFTER, {})
            self.assertEqual(settings["_analysisLane"], "startup")
            return response

        def requeue_after_ready():
            self.assertEqual(server.ai_startup_reconnect_snapshot()["status"], "ready")
            return 4

        with (
            patch.object(server, "CODEX_CLI_UNAVAILABLE_UNTIL", 999999.0),
            patch.object(server, "LLM_API_UNAVAILABLE_UNTIL", 999999.0),
            patch.object(server, "RANK_AI_RETRY_AFTER", 999999.0),
            patch.object(server, "NEWSFLASH_SEMANTIC_AI_RETRY_AFTER", 999999.0),
            patch.object(server, "ROTATION_AI_RETRY_AFTER", 999999.0),
            patch.object(server, "NEWS_TRADE_AI_RETRY_AFTER", {"news": 999999.0}),
            patch.object(server, "CHAIN_ECOSYSTEM_AI_RETRY_AFTER", {"chain": 999999.0}),
            patch.object(server, "deepseek_chat", side_effect=connected),
            patch.object(server.ONCHAIN_FAST_RESEARCH, "retry_unavailable_after_ai_reconnect", side_effect=requeue_after_ready) as retry,
        ):
            result = server.ai_startup_reconnect_once()

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["provider"], "codex-cli")
        self.assertEqual(result["requeued"], 4)
        retry.assert_called_once_with()

    def test_startup_ai_reconnect_failure_keeps_service_available_and_enters_cooldown(self):
        with (
            patch.object(server, "CODEX_CLI_UNAVAILABLE_UNTIL", 0.0),
            patch.object(server, "LLM_API_UNAVAILABLE_UNTIL", 0.0),
            patch.object(server, "deepseek_chat", side_effect=RuntimeError("quota unavailable")),
            patch.object(server.ONCHAIN_FAST_RESEARCH, "retry_unavailable_after_ai_reconnect") as retry,
        ):
            result = server.ai_startup_reconnect_once()
            self.assertGreater(server.CODEX_CLI_UNAVAILABLE_UNTIL, 0.0)
            self.assertGreater(server.LLM_API_UNAVAILABLE_UNTIL, 0.0)

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("quota unavailable", result["error"])
        retry.assert_not_called()

    def test_two_live_slots_are_independent_of_history_wallet_and_tweet_analysis(self):
        import time
        with server.CODEX_CLI_FAST_ANALYSIS_LOCK, server.CODEX_CLI_FALLBACK_LOCK, server.CODEX_CLI_WALLET_ANALYSIS_LOCK, server.CODEX_CLI_ONCHAIN_HISTORY_LOCK:
            with server.codex_cli_analysis_slot(time.monotonic()+2,lane='onchain-live'):
                with server.codex_cli_analysis_slot(time.monotonic()+2,lane='onchain-live'):
                    with self.assertRaises(server.ResearchCapacityBusy):
                        with server.codex_cli_analysis_slot(time.monotonic(),lane='onchain-live'): pass

    def test_model_capacity_failure_stays_typed_and_routes_to_the_live_lane(self):
        with patch.object(server, 'codex_cli_chat', side_effect=server.ResearchCapacityBusy('occupied')) as cli:
            with self.assertRaises(server.ResearchCapacityBusy):
                server.deepseek_chat([], {'apiKey':'','_analysisLane':'onchain-live','_analysisDeadline':1})
        self.assertEqual(cli.call_args.kwargs['lane'],'onchain-live')

    def test_preferred_codex_research_bypasses_plain_api_and_enables_web(self):
        expected = {
            "choices": [{"message": {"content": '{"answer":"researched"}'}}],
            "_provider": "codex-cli",
        }
        settings = {
            "apiKey": "configured-but-not-used",
            "_analysisLane": "chat-ca",
            "_analysisNoTimeout": True,
            "_preferCodexCli": True,
            "_codexWebSearch": True,
            "_codexAllowDuringCooldown": True,
            "_codexModel": "gpt-6-astra",
            "_codexReasoningEffort": "high",
        }
        with patch.object(server, "codex_cli_chat", return_value=expected) as cli, \
             patch.object(server.requests, "post") as api_post:
            result = server.deepseek_chat([{"role": "user", "content": "research"}], settings)

        self.assertEqual(result, expected)
        api_post.assert_not_called()
        self.assertEqual(cli.call_args.kwargs["lane"], "chat-ca")
        self.assertTrue(cli.call_args.kwargs["web_search"])
        self.assertEqual(cli.call_args.kwargs["model_override"], "gpt-6-astra")
        self.assertEqual(cli.call_args.kwargs["reasoning_effort_override"], "high")
        self.assertEqual(cli.call_args.kwargs["timeout_seconds"], 0)
        self.assertTrue(cli.call_args.kwargs["allow_during_cooldown"])

    def test_onchain_prompt_keeps_news_text_and_uses_the_live_lane(self):
        from tests.test_onchain_fast_research import candidate
        row=candidate();row['researchEvidence']={'text':'explanatory original source','identityStatus':'same-chain-symbol-unverified'}
        with patch.object(server,'system_llm_settings',return_value={}), patch.object(server,'deepseek_enabled',return_value=True), \
             patch.object(server,'deepseek_chat',return_value={'choices':[{'message':{'content':'{"items":[]}'}}]}) as chat:
            server.analyze_fast_onchain_candidates([row],lane='onchain-live')
        prompt=json.loads(chat.call_args.args[0][-1]['content'])
        self.assertEqual(prompt['rows'][0]['sources'][0]['content'],'explanatory original source')
        self.assertEqual(chat.call_args.args[1]['_analysisLane'],'onchain-live')
        self.assertTrue(chat.call_args.args[1]['_analysisNoTimeout'])
        self.assertNotIn('_analysisDeadline', chat.call_args.args[1])

    def test_wallet_review_does_not_wait_behind_scanning_analysis(self):
        import time
        with server.CODEX_CLI_FAST_ANALYSIS_LOCK, server.CODEX_CLI_FALLBACK_LOCK:
            with server.codex_cli_analysis_slot(time.monotonic() + 2, lane="wallet"):
                self.assertFalse(server.CODEX_CLI_WALLET_ANALYSIS_LOCK.acquire(blocking=False))

    def test_wallet_execution_has_no_model_callback(self):
        self.assertFalse(hasattr(server, "monitor_buy_ai_review"))
        self.assertIsNone(getattr(server.MONITOR_BUY, "ai_review", None))

    def test_fast_analysis_does_not_wait_behind_regular_cli_and_timeout_is_local(self):
        import subprocess
        import time
        deadline = time.monotonic() + 10
        with (
            patch.object(server, "codex_cli_executable", return_value="codex.exe"),
            patch.object(server, "codex_cli_fallback_available", return_value=True),
            patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 10)) as run,
            patch.object(server, "CODEX_CLI_UNAVAILABLE_UNTIL", 0.0),
            server.CODEX_CLI_FALLBACK_LOCK,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                server.codex_cli_chat([{"role": "user", "content": "Return JSON"}], deadline=deadline)
            self.assertLessEqual(run.call_args.kwargs["timeout"], 10)
            self.assertEqual(server.CODEX_CLI_UNAVAILABLE_UNTIL, 0.0)

    def fallback_response(self):
        return {
            "choices": [{"message": {"role": "assistant", "content": '{"answer":"ok"}'}}],
            "_provider": "codex-cli",
            "_fallback": True,
        }

    def test_cli_runs_ephemerally_in_an_isolated_read_only_directory(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(
                json.dumps({"content": json.dumps({"answer": "ok"})}),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(server, "codex_cli_executable", return_value="codex.exe"),
            patch.object(server, "codex_cli_fallback_available", return_value=True),
            patch.object(server.subprocess, "run", side_effect=fake_run),
            patch.object(server, "CODEX_CLI_UNAVAILABLE_UNTIL", 0.0),
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "must-not-leak", "PRIVATE_SECRET": "hidden"}),
        ):
            response = server.codex_cli_chat([
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": "Analyze this item."},
            ])

        self.assertEqual(response["_provider"], "codex-cli")
        self.assertEqual(json.loads(response["choices"][0]["message"]["content"]), {"answer": "ok"})
        command = captured["command"]
        if not server.env_value('CODEX_CLI_MODEL',''):
            self.assertEqual(command[command.index('--model')+1],'gpt-5.6-luna')
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--ignore-user-config", command)
        disabled_features = {
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--disable"
        }
        self.assertTrue({"shell_tool", "unified_exec", "computer_use", "browser_use"}.issubset(disabled_features))
        self.assertNotEqual(Path(captured["kwargs"]["cwd"]).resolve(), server.ROOT)
        self.assertNotEqual(
            Path(captured["kwargs"]["env"]["USERPROFILE"]).resolve(),
            Path.home().resolve(),
        )
        self.assertEqual(
            Path(captured["kwargs"]["env"]["CODEX_HOME"]).parent.resolve(),
            Path(captured["kwargs"]["env"]["USERPROFILE"]).resolve(),
        )
        self.assertNotIn("DEEPSEEK_API_KEY", captured["kwargs"]["env"])
        self.assertNotIn("PRIVATE_SECRET", captured["kwargs"]["env"])

    def test_legacy_minimal_effort_is_normalized_to_supported_low(self):
        captured = {}
        def fake_run(command, **_kwargs):
            captured['command'] = command
            output_path = Path(command[command.index('--output-last-message') + 1])
            output_path.write_text(json.dumps({'content':'{"answer":"ok"}'}), encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with (
            patch.object(server, 'codex_cli_executable', return_value='codex.exe'),
            patch.object(server, 'codex_cli_fallback_available', return_value=True),
            patch.object(server.subprocess, 'run', side_effect=fake_run),
        ):
            server.codex_cli_chat([{'role':'user','content':'Return JSON'}],
                                  reasoning_effort_override='minimal')
        command = captured['command']
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertNotIn('model_reasoning_effort="minimal"', command)

    def test_user_explanation_failure_does_not_disable_other_codex_tasks(self):
        failed = SimpleNamespace(returncode=1, stdout='', stderr='invalid request')
        with (
            patch.object(server, 'codex_cli_executable', return_value='codex.exe'),
            patch.object(server, 'codex_cli_fallback_available', return_value=True),
            patch.object(server.subprocess, 'run', return_value=failed),
            patch.object(server, 'CODEX_CLI_UNAVAILABLE_UNTIL', 0.0),
        ):
            with self.assertRaises(RuntimeError):
                server.codex_cli_chat([{'role':'user','content':'Return JSON'}],
                    allow_during_cooldown=True, reasoning_effort_override='low')
            self.assertEqual(server.CODEX_CLI_UNAVAILABLE_UNTIL, 0.0)

    def test_cli_copies_only_local_auth_into_the_temporary_codex_home(self):
        captured = {}

        def fake_run(command, **kwargs):
            isolated_home = Path(kwargs["env"]["CODEX_HOME"])
            captured["auth"] = (isolated_home / "auth.json").read_text(encoding="utf-8")
            captured["entries"] = sorted(path.name for path in isolated_home.iterdir())
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(
                json.dumps({"content": json.dumps({"answer": "ok"})}),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as auth_dir:
            auth_root = Path(auth_dir)
            (auth_root / "auth.json").write_text('{"token":"local-test-only"}', encoding="utf-8")
            (auth_root / "skills").mkdir()
            with (
                patch.object(server, "CODEX_HOME", auth_root),
                patch.object(server, "codex_cli_executable", return_value="codex.exe"),
                patch.object(server, "codex_cli_fallback_available", return_value=True),
                patch.object(server.subprocess, "run", side_effect=fake_run),
                patch.object(server, "CODEX_CLI_UNAVAILABLE_UNTIL", 0.0),
            ):
                server.codex_cli_chat([{"role": "user", "content": "Return JSON"}])

        self.assertEqual(captured["auth"], '{"token":"local-test-only"}')
        self.assertEqual(captured["entries"], ["auth.json"])

    def test_cli_keeps_proxy_routing_but_not_application_secrets(self):
        with patch.dict(
            os.environ,
            {
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "HTTP_PROXY": "http://127.0.0.1:7890",
                "DEEPSEEK_API_KEY": "must-not-leak",
            },
            clear=False,
        ):
            environment = server.codex_cli_sanitized_environment()

        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(environment["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertNotIn("DEEPSEEK_API_KEY", environment)

    def test_missing_api_key_goes_directly_to_cli(self):
        with (
            patch.object(server.requests, "post") as post,
            patch.object(server, "codex_cli_chat", return_value=self.fallback_response()) as cli,
            patch.object(server, "LLM_API_UNAVAILABLE_UNTIL", 0.0),
        ):
            response = server.deepseek_chat(
                [{"role": "user", "content": "Return JSON"}],
                {"apiKey": "", "baseUrl": "https://api.invalid", "model": "test"},
            )

        post.assert_not_called()
        cli.assert_called_once()
        self.assertTrue(response["_fallback"])

    def test_api_failure_uses_cli_and_temporarily_skips_repeated_api_calls(self):
        settings = {"apiKey": "configured", "baseUrl": "https://api.invalid", "model": "test"}
        with (
            patch.object(server.requests, "post", side_effect=requests.Timeout("offline")) as post,
            patch.object(server, "codex_cli_chat", return_value=self.fallback_response()) as cli,
            patch.object(server, "LLM_API_UNAVAILABLE_UNTIL", 0.0),
        ):
            first = server.deepseek_chat([{"role": "user", "content": "one"}], settings)
            second = server.deepseek_chat([{"role": "user", "content": "two"}], settings)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(cli.call_count, 2)
        self.assertEqual(first["_provider"], "codex-cli")
        self.assertEqual(second["_provider"], "codex-cli")

    def test_billing_failure_uses_long_cooldown_before_retrying_api(self):
        response = requests.Response()
        response.status_code = 402
        response.url = "https://api.example/chat/completions"
        settings = {"apiKey": "configured", "baseUrl": "https://api.example", "model": "test"}
        with (
            patch.object(server.requests, "post", return_value=response),
            patch.object(server, "codex_cli_chat", return_value=self.fallback_response()),
            patch.object(server, "LLM_API_UNAVAILABLE_UNTIL", 0.0),
            patch.object(server.time, "monotonic", return_value=100.0),
            patch.dict(os.environ, {"LLM_API_BILLING_COOLDOWN": "1800"}),
        ):
            result = server.deepseek_chat([{"role": "user", "content": "one"}], settings)
            cooldown_until = server.LLM_API_UNAVAILABLE_UNTIL

        self.assertEqual(result["_provider"], "codex-cli")
        self.assertEqual(cooldown_until, 1900.0)

    def test_successful_api_response_does_not_start_cli(self):
        response = SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": '{"answer":"api"}'}}]},
        )
        with (
            patch.object(server.requests, "post", return_value=response) as post,
            patch.object(server, "codex_cli_chat") as cli,
            patch.object(server, "LLM_API_UNAVAILABLE_UNTIL", 0.0),
        ):
            result = server.deepseek_chat(
                [{"role": "user", "content": "Return JSON"}],
                {"apiKey": "configured", "baseUrl": "https://api.example", "model": "test"},
            )

        post.assert_called_once()
        cli.assert_not_called()
        self.assertNotIn("_fallback", result)

    def test_ai_is_enabled_when_cli_is_the_only_available_provider(self):
        with patch.object(server, "codex_cli_fallback_available", return_value=True):
            self.assertTrue(server.deepseek_enabled({"apiKey": ""}))
            public = server.public_llm_settings({"provider": "deepseek", "apiKey": ""})
        self.assertFalse(public["hasApiKey"])
        self.assertTrue(public["codexCliFallback"])
        self.assertTrue(public["effectiveAvailable"])


if __name__ == "__main__":
    unittest.main()
