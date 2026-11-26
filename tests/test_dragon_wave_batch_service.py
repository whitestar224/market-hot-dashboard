import unittest
from unittest.mock import patch

# --- 业务代码在 src/ 下：把它加进 sys.path 才能 `from app.x import y` ---------
import sys as _sys
from pathlib import Path as _Path

_SRC_DIR = _Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SRC_DIR))
# ---------------------------------------------------------------------------
from app.core import quiet_http_server as server
class FullBatchServiceTests(unittest.TestCase):
    def test_autostart_default_is_unchanged(self):
        with patch.dict(server.os.environ, {}, clear=True):
            self.assertTrue(server.precompute_autostart_enabled())

    def test_batch_service_does_not_spawn_or_preempt_workers(self):
        for value in ("0", "false", "off"):
            with self.subTest(value=value), patch.dict(server.os.environ, {
                "DRAGON_WAVE_PRECOMPUTE_AUTOSTART": value
            }), patch.object(server.subprocess, "Popen") as spawn:
                self.assertFalse(server.ensure_precompute_running("v90", force=True, preempt=True))
                spawn.assert_not_called()

    def test_supervisor_does_not_seed_or_wait_in_batch_service_mode(self):
        with patch.dict(server.os.environ, {"DRAGON_WAVE_PRECOMPUTE_AUTOSTART": "0"}), \
                patch.object(server, "seed_confirmed_precompute_requests") as seed, \
                patch.object(server.time, "sleep") as sleep:
            server.supervise_precompute()
            seed.assert_not_called()
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
