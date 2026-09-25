import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PersonalXFutureEventIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.tempdir.name) / "auth.db"
        server.init_auth_db()
        # 插入一个 admin 用户
        with server.AUTH_DB_LOCK, server.auth_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at, updated_at) "
                "VALUES ('admin', 'test', 'admin', ?, ?)",
                (1_800_000_000, 1_800_000_000),
            )
        with server.FUTURE_EVENT_LOCK:
            server.FUTURE_EVENT_SEEN.clear()

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        with server.FUTURE_EVENT_LOCK:
            server.FUTURE_EVENT_SEEN.clear()
        gc.collect()
        self.tempdir.cleanup()

    def test_future_event_writes_todo_and_fires_alert(self):
        with patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as alert:
            result = server.record_personal_x_future_event(
                "PixVerse 9月28日上市，重点关注",
                "whitestar224",
                1_800_000_000_000,
                now_ms=1_800_000_000_000,
            )
        self.assertIsNotNone(result)
        self.assertIn("9月28日", result["date_label"])
        # 验证弹窗触发
        self.assertTrue(alert.called)
        payload = alert.call_args[0][0]
        self.assertEqual(payload["kind"], "X未来事件")
        # 验证 todo 已写入 admin 用户
        user = server.admin_user()
        todo = server.load_user_payload(user, server.USER_SCOPE_TODO)
        project_names = [p["name"] for p in todo["projects"]]
        self.assertIn("X未来事件", project_names)
        tasks = [t for t in todo["tasks"] if "PixVerse" in t["title"]]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["priority"], "high")
        self.assertGreater(tasks[0]["dueAt"], 0)

    def test_non_future_event_is_noop(self):
        with patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as alert:
            result = server.record_personal_x_future_event(
                "intel 热度高，等大分歧做延续观察",
                "whitestar224",
                1_800_000_000_000,
                now_ms=1_800_000_000_000,
            )
        self.assertIsNone(result)
        self.assertFalse(alert.called)

    def test_duplicate_event_is_deduped(self):
        with patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as alert:
            first = server.record_personal_x_future_event(
                "下周空投快照", "whitestar224", 1_800_000_000_000, now_ms=1_800_000_000_000
            )
            second = server.record_personal_x_future_event(
                "下周空投快照", "whitestar224", 1_800_000_000_000, now_ms=1_800_000_000_000
            )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(alert.call_count, 1)


if __name__ == "__main__":
    unittest.main()
