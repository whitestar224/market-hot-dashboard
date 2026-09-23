import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import service_guard as guard


class ServiceGuardTests(unittest.TestCase):
    def test_start_runs_supervisor_in_the_project_process(self):
        with patch.object(guard, "supervise", return_value=17) as supervise, patch.object(
            guard.sys, "argv", ["service_guard.py", "start", "--host", "127.0.0.1", "--port", "8765"]
        ):
            self.assertEqual(guard.main(), 17)
            supervise.assert_called_once_with("127.0.0.1", 8765)

    def test_locked_status_file_never_kills_supervision(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(guard,'STATE',Path(directory)/'status.json'), patch.object(Path,'replace',side_effect=PermissionError('sharing violation')) as replace, patch.object(guard.time,'sleep'):
            self.assertFalse(guard.save_state(status='running',pid=123))
            self.assertEqual(replace.call_count,3)

    def test_status_write_failure_does_not_kill_supervision(self):
        with patch.object(Path,'write_text',side_effect=OSError('disk busy')),patch.object(guard.time,'sleep'):
            self.assertFalse(guard.save_state(status='running'))

    def test_lock_rejects_second_supervisor_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            first = guard.acquire_lock(path)
            self.assertIsNotNone(first)
            self.assertIsNone(guard.acquire_lock(path))
            first.close()
            next_lock = guard.acquire_lock(path)
            self.assertIsNotNone(next_lock)
            next_lock.close()

    def test_restart_backoff_is_bounded(self):
        self.assertEqual([guard.retry_delay(n) for n in (1, 2, 5, 100)], [2, 4, 32, 60])

    def test_probe_rejects_other_services_and_network_failure(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok":true,"service":"other","pid":12}'
        with patch.object(guard, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertIsNone(guard.probe("127.0.0.1", 1))
            response.read.return_value = b'{"ok":true,"service":"market-hot-dashboard","pid":12}'
            self.assertEqual(guard.probe("127.0.0.1", 1)["pid"], 12)
            opener.return_value.open.side_effect = OSError("offline")
            self.assertIsNone(guard.probe("127.0.0.1", 1))

    def test_stop_waits_for_child_before_forcing_only_that_child(self):
        child = Mock()
        child.poll.return_value = None
        guard.stop_child(child)
        child.terminate.assert_not_called()
        child.wait.side_effect = [guard.subprocess.TimeoutExpired("test", 1), None]
        guard.stop_child(child)
        child.terminate.assert_called_once()

    def test_port_binding_precedes_background_and_database_startup(self):
        tree = ast.parse(Path("server.py").read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = {node.func.id: node.lineno for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertLess(calls["ThreadingHTTPServer"], calls["init_auth_db"])
        self.assertLess(calls["ThreadingHTTPServer"], calls["start_chain_ecosystem_monitor"])


if __name__ == "__main__":
    unittest.main()
