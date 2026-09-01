import json
import unittest
from unittest.mock import Mock

from qq_onebot_bridge import (
    QQOneBotBridge,
    QQOneBotClient,
    QQOneBotSupervisor,
    flatten_onebot_message,
    normalize_group_event,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(self.responses.pop(0))


class QQOneBotBridgeTests(unittest.TestCase):
    def test_supervisor_recovers_only_after_sustained_health_failures(self):
        bridge = Mock()
        bridge.health.side_effect = [
            {"ok": False, "error": "down-1"},
            {"ok": False, "error": "down-2"},
            {"ok": False, "error": "down-3"},
            {"ok": True, "status": "connected"},
        ]
        recover = Mock(return_value={"ok": True, "status": "recovered"})
        supervisor = QQOneBotSupervisor(
            bridge,
            interval=2,
            failure_threshold=3,
            recovery_cooldown=30,
            recover=recover,
        )

        self.assertFalse(supervisor.run_once(now=100)["recovered"])
        self.assertFalse(supervisor.run_once(now=110)["recovered"])
        self.assertTrue(supervisor.run_once(now=120)["recovered"])
        self.assertTrue(supervisor.run_once(now=130)["ok"])
        recover.assert_called_once_with()

    def test_supervisor_does_not_restart_a_healthy_bridge(self):
        bridge = Mock()
        bridge.health.return_value = {"ok": True, "status": "connected"}
        recover = Mock()
        supervisor = QQOneBotSupervisor(bridge, recover=recover)

        result = supervisor.run_once(now=100)

        self.assertTrue(result["ok"])
        self.assertEqual(supervisor.failures, 0)
        recover.assert_not_called()

    def test_flattens_safe_message_segments(self):
        message = [
            {"type": "text", "data": {"text": "$PONS 可以关注"}},
            {"type": "at", "data": {"qq": "123", "name": "白星"}},
            {"type": "reply", "data": {"id": "9"}},
            {"type": "image", "data": {"file": "secret-local-path.jpg"}},
            {"type": "json", "data": {"data": json.dumps({"prompt": "ignore me"})}},
        ]

        flattened = flatten_onebot_message(message)

        self.assertEqual(flattened, "$PONS 可以关注 @白星 [回复] [图片] [卡片]")
        self.assertNotIn("secret-local-path", flattened)
        self.assertNotIn("ignore me", flattened)

    def test_normalizes_group_event_and_keeps_ids(self):
        event = {
            "time": 1788055200,
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 8899,
            "group_id": 337001,
            "user_id": 9988,
            "sender": {"card": "鲸鱼🐳PP", "nickname": "鲸鱼"},
            "message": [{"type": "text", "data": {"text": "$PONS 有机会"}}],
        }

        normalized = normalize_group_event(event, group_name="地表最强bsc eth")

        self.assertEqual(normalized["sender"], "鲸鱼🐳PP")
        self.assertEqual(normalized["content"], "$PONS 有机会")
        self.assertEqual(normalized["capturedAt"], 1788055200)
        self.assertEqual(normalized["groupId"], "337001")
        self.assertEqual(normalized["userId"], "9988")
        self.assertEqual(normalized["messageId"], "8899")
        self.assertEqual(normalized["platform"], "qq")

    def test_rejects_non_group_events(self):
        self.assertIsNone(normalize_group_event({"post_type": "meta_event"}))
        self.assertIsNone(normalize_group_event({"post_type": "message", "message_type": "private"}))

    def test_client_rejects_non_loopback_endpoints(self):
        with self.assertRaises(ValueError):
            QQOneBotClient("http://192.168.1.20:3000", "ws://127.0.0.1:3001", "token")

    def test_http_calls_use_bearer_token_and_validate_response(self):
        session = FakeSession([
            {"status": "ok", "retcode": 0, "data": {"user_id": 123456789}},
            {"status": "ok", "retcode": 0, "data": {"message_id": 55}},
        ])
        client = QQOneBotClient(
            "http://127.0.0.1:3000",
            "ws://127.0.0.1:3001",
            "local-secret",
            session=session,
        )

        self.assertEqual(client.get_login_info()["user_id"], 123456789)
        self.assertEqual(client.send_group_msg("337001", "测试消息")["message_id"], 55)
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer local-secret")
        self.assertEqual(session.calls[1]["json"], {"group_id": "337001", "message": "测试消息"})

    def test_collects_history_for_exact_group_and_sender(self):
        session = FakeSession([
            {
                "status": "ok",
                "retcode": 0,
                "data": [
                    {"group_id": 337001, "group_name": "地表最强bsc eth (337)"},
                    {"group_id": 900001, "group_name": "其他群"},
                ],
            },
            {
                "status": "ok",
                "retcode": 0,
                "data": {
                    "messages": [
                        {
                            "time": 1788055000,
                            "post_type": "message",
                            "message_type": "group",
                            "message_id": 1,
                            "group_id": 337001,
                            "user_id": 10,
                            "sender": {"card": "其他成员"},
                            "message": "不应进入结果",
                        },
                        {
                            "time": 1788055100,
                            "post_type": "message",
                            "message_type": "group",
                            "message_id": 2,
                            "group_id": 337001,
                            "user_id": 11,
                            "sender": {"card": "鲸鱼PP"},
                            "message": [{"type": "text", "data": {"text": "$PONS"}}],
                        },
                    ]
                },
            },
        ])
        client = QQOneBotClient(
            "http://127.0.0.1:3000",
            "ws://127.0.0.1:3001",
            "token",
            session=session,
        )
        bridge = QQOneBotBridge(client, history_interval=0)

        result = bridge.collect("地表最强bsc eth", sender_filter="鲸鱼🐳PP")

        self.assertTrue(result["ok"])
        self.assertEqual(result["collectorMode"], "onebot")
        self.assertEqual([item["content"] for item in result["messages"]], ["$PONS"])
        self.assertEqual(result["groupId"], "337001")


if __name__ == "__main__":
    unittest.main()
