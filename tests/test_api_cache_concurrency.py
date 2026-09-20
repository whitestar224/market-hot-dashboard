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


if __name__ == "__main__":
    unittest.main()
