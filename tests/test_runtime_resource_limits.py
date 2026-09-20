import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class RuntimeResourceLimitTests(unittest.TestCase):
    def test_timestamped_cache_expires_and_caps_oldest_entries(self):
        now = 10_000.0
        cache = {
            "expired": (now - 61, {"value": 0}),
            "old": (now - 3, {"value": 1}),
            "middle": (now - 2, {"value": 2}),
            "new": (now - 1, {"value": 3}),
        }

        removed = server.prune_timestamped_cache(
            cache,
            ttl_seconds=60,
            max_entries=2,
            now=now,
        )

        self.assertEqual(removed, 2)
        self.assertEqual(set(cache), {"middle", "new"})

    def test_runtime_cleanup_keeps_state_and_only_latest_qr_images(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "xingyunshe_auth.db"
            state.write_bytes(b"state")
            for index in range(12):
                path = root / f"wechat-auth-qr-u{index}.png"
                path.write_bytes(b"qr")
                os.utime(path, (now - index, now - index))
            expired_current = root / "wechat-auth-qr-current.png"
            expired_current.write_bytes(b"current")
            os.utime(expired_current, (now - 7200, now - 7200))
            abandoned = root / "payload.json.tmp"
            abandoned.write_text("partial", encoding="utf-8")
            os.utime(abandoned, (now - 7200, now - 7200))

            removed = server.cleanup_runtime_ephemeral_files(
                root,
                now=now,
                keep_qr_uuid="current",
            )

            self.assertTrue(state.exists())
            self.assertTrue(expired_current.exists())
            self.assertLessEqual(len(list(root.glob("wechat-auth-qr-u*.png"))), 8)
            self.assertFalse(abandoned.exists())
            self.assertGreaterEqual(removed["qr"], 4)
            self.assertEqual(removed["temp"], 1)

    def test_event_monitor_core_reuses_persisted_snapshot(self):
        payload = {"ok": True, "updatedAt": 123, "events": [], "newsTrades": []}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server,
            "PERSIST_CACHE_DIR",
            Path(temp_dir),
        ), patch.object(
            server,
            "build_event_monitor_core_payload",
            return_value=payload,
        ) as build:
            first = server.cached_event_monitor_core_payload()
            second = server.cached_event_monitor_core_payload()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(build.call_count, 1)


if __name__ == "__main__":
    unittest.main()
