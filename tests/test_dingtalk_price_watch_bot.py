import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import dingtalk_price_watch_bot as bot


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, watch_payload, post_payload=None, post_status=200):
        self.watch_payload = watch_payload
        self.post_payload = post_payload or {"errcode": 0, "errmsg": "ok"}
        self.post_status = post_status
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse(self.watch_payload)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse(self.post_payload, self.post_status)


def watch_item(symbol="BTC", episode=1, status="near"):
    return {
        "symbol": symbol,
        "name": symbol,
        "status": status,
        "setupType": "retest",
        "currentPrice": 99123.45,
        "weekHigh": 100000,
        "distancePct": 0.87655,
        "provider": "Binance Futures",
        "latestAlertEpisode": episode,
        "latestAlertAt": 1786118400000,
        "isFirstCandidate": episode == 1,
    }


class SigningTests(unittest.TestCase):
    def test_signed_webhook_uses_dingtalk_hmac_format(self):
        timestamp = 1700000000123
        secret = "SEC-test-secret"
        url = bot.build_signed_webhook(
            "https://oapi.dingtalk.com/robot/send?access_token=token-value",
            secret,
            timestamp,
        )

        query = parse_qs(urlparse(url).query)
        expected = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                f"{timestamp}\n{secret}".encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        self.assertEqual(query["timestamp"], [str(timestamp)])
        self.assertEqual(query["sign"], [expected])
        self.assertEqual(query["access_token"], ["token-value"])

    def test_markdown_contains_monitoring_details(self):
        payload = bot.build_markdown_payload(
            watch_item(),
            dashboard_url="http://127.0.0.1:8765/price-watch.html",
        )

        self.assertEqual(payload["msgtype"], "markdown")
        self.assertIn("币种监控", payload["markdown"]["title"])
        text = payload["markdown"]["text"]
        self.assertIn("BTC", text)
        self.assertIn("0.88%", text)
        self.assertIn("99,123.45", text)
        self.assertIn("100,000.00", text)
        self.assertIn("回撤后再接近", text)
        self.assertIn("Binance Futures", text)


class DeliveryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.config = bot.Config(
            api_url="http://127.0.0.1:8765/api/price-watch",
            dashboard_url="http://127.0.0.1:8765/price-watch.html",
            webhook_url="https://oapi.dingtalk.com/robot/send?access_token=test-token",
            secret="SEC-test",
            poll_seconds=60,
            timeout_seconds=5,
            state_path=self.state_path,
            send_existing_on_first_run=False,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_first_run_baselines_existing_episodes_without_sending(self):
        session = FakeSession({"ok": True, "items": [watch_item(episode=3)]})

        result = bot.run_cycle(self.config, session=session)

        self.assertEqual(result["baselined"], 1)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(session.post_calls, [])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["episodes"]["BTC"], 3)

    def test_new_episode_sends_once_and_checkpoints(self):
        bot.save_state(self.state_path, {"episodes": {"BTC": 1}})
        session = FakeSession({"ok": True, "items": [watch_item(episode=2)]})

        first = bot.run_cycle(self.config, session=session)
        second = bot.run_cycle(self.config, session=session)

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(session.post_calls), 1)
        state = bot.load_state(self.state_path)
        self.assertEqual(state["episodes"]["BTC"], 2)

    def test_failed_delivery_is_retried_next_cycle(self):
        bot.save_state(self.state_path, {"episodes": {"BTC": 1}})
        failed = FakeSession(
            {"ok": True, "items": [watch_item(episode=2)]},
            post_payload={"errcode": 310000, "errmsg": "sign not match"},
        )

        result = bot.run_cycle(self.config, session=failed)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(bot.load_state(self.state_path)["episodes"]["BTC"], 1)

        succeeded = FakeSession({"ok": True, "items": [watch_item(episode=2)]})
        retry = bot.run_cycle(self.config, session=succeeded)
        self.assertEqual(retry["sent"], 1)
        self.assertEqual(len(succeeded.post_calls), 1)

    def test_send_existing_only_sends_current_near_signals(self):
        config = bot.Config(**{**self.config.__dict__, "send_existing_on_first_run": True})
        session = FakeSession(
            {
                "ok": True,
                "items": [
                    watch_item("BTC", episode=2, status="near"),
                    watch_item("ETH", episode=4, status="normal"),
                ],
            }
        )

        result = bot.run_cycle(config, session=session)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(session.post_calls), 1)
        state = bot.load_state(self.state_path)
        self.assertEqual(state["episodes"], {"BTC": 2, "ETH": 4})

    def test_corrupt_state_is_safely_rebaselined(self):
        self.state_path.write_text("{partial", encoding="utf-8")
        session = FakeSession({"ok": True, "items": [watch_item(episode=5)]})

        result = bot.run_cycle(self.config, session=session)

        self.assertEqual(result["baselined"], 1)
        self.assertEqual(session.post_calls, [])
        self.assertEqual(bot.load_state(self.state_path)["episodes"]["BTC"], 5)

    def test_newer_alert_time_handles_monitor_database_reset(self):
        bot.save_state(
            self.state_path,
            {"episodes": {"BTC": 5}, "alertTimes": {"BTC": 1786000000000}},
        )
        event = watch_item(episode=1)
        event["latestAlertAt"] = 1787000000000
        session = FakeSession({"ok": True, "items": [event]})

        result = bot.run_cycle(self.config, session=session)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(bot.load_state(self.state_path)["episodes"]["BTC"], 1)

    def test_error_text_redacts_webhook_token_and_secret(self):
        error = RuntimeError(f"failed {self.config.webhook_url} {self.config.secret}")

        cleaned = bot.safe_error_text(error, self.config)

        self.assertNotIn("test-token", cleaned)
        self.assertNotIn("SEC-test", cleaned)
        self.assertNotIn(self.config.webhook_url, cleaned)


if __name__ == "__main__":
    unittest.main()
