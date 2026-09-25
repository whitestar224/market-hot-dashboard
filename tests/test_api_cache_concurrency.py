import threading
import time
import unittest
from unittest.mock import patch

import server


class ApiCacheConcurrencyTests(unittest.TestCase):
    def test_api_cache_writes_are_serialized(self):
        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def fake_write(_path, _payload):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1

        threads = []
        with patch.object(server, "write_json_cache", side_effect=fake_write):
            for index in range(6):
                thread = threading.Thread(
                    target=server.refresh_api_cache_now,
                    args=(f"test-{index}", lambda index=index: {"value": index}),
                )
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(max_active, 1)

    def test_cached_api_payload_refreshes_synchronously_when_beyond_max_age(self):
        # A very old cache must force a synchronous refresh even without an
        # explicit force_refresh, so a stuck background worker can never leave
        # the page served unboundedly-stale data.
        stale_payload = {
            "_cache": {"key": "stale-key", "updatedAt": int(time.time() * 1000) - 100_000},
            "sources": [{"id": "old", "rows": []}],
        }
        fresh_payload = {"sources": [{"id": "fresh", "rows": [{"symbol": "NEW"}]}]}

        def fake_fetcher():
            return fresh_payload

        with (
            patch.object(server, "read_json_cache", return_value=stale_payload),
            patch.object(server, "refresh_api_cache_now", side_effect=lambda key, fetcher: fetcher()),
        ):
            result = server.cached_api_payload(
                "stale-key", fake_fetcher, 60, max_age_seconds=30,
            )

        self.assertEqual(result["sources"][0]["id"], "fresh")

    def test_cached_api_payload_serves_recent_cache_without_forced_fetch(self):
        recent_payload = {
            "_cache": {"key": "fresh-key", "updatedAt": int(time.time() * 1000) - 1_000},
            "sources": [{"id": "recent", "rows": []}],
        }
        fetcher_called = []

        def fake_fetcher():
            fetcher_called.append(True)
            return {"sources": []}

        with (
            patch.object(server, "read_json_cache", return_value=recent_payload),
            patch.object(server, "trigger_api_refresh"),
        ):
            result = server.cached_api_payload("fresh-key", fake_fetcher, 60, max_age_seconds=600)

        self.assertEqual(result["sources"][0]["id"], "recent")
        self.assertEqual(fetcher_called, [])


if __name__ == "__main__":
    unittest.main()
