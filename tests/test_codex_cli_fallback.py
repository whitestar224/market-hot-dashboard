import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

import server


class CodexCliFallbackTests(unittest.TestCase):
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
        self.assertNotIn("DEEPSEEK_API_KEY", captured["kwargs"]["env"])
        self.assertNotIn("PRIVATE_SECRET", captured["kwargs"]["env"])

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
