import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

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
        # main() binds the port through BoundedThreadingHTTPServer (a subclass of
        # ThreadingHTTPServer), so assert on the name it actually calls.
        tree = ast.parse(Path("server.py").read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = {node.func.id: node.lineno for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertLess(calls["BoundedThreadingHTTPServer"], calls["init_auth_db"])
        self.assertLess(calls["BoundedThreadingHTTPServer"], calls["start_chain_ecosystem_monitor"])


class ServiceStatusReportTests(unittest.TestCase):
    """status 是用户唯一的状态窗口，说人话比说 JSON 重要。"""

    def test_flags_a_bare_server_that_the_supervisor_does_not_own(self):
        # 手动裸跑的 python server.py：端口有响应，但不是守护的 child。
        with patch.object(guard, "read_state", return_value={"status": "stopped"}), patch.object(
            guard, "probe", return_value={"ok": True, "service": "market-hot-dashboard", "pid": 4321}
        ):
            text = guard.describe_service()
        self.assertIn("4321", text)
        self.assertIn("不是守护托管的进程", text)
        self.assertIn("启动后台服务.cmd", text)

    def test_reports_running_when_the_live_pid_is_the_supervised_child(self):
        with patch.object(guard, "read_state", return_value={"status": "running", "pid": 99, "restarts": 2}), patch.object(
            guard, "probe", return_value={"ok": True, "service": "market-hot-dashboard", "pid": 99}
        ):
            text = guard.describe_service()
        self.assertIn("运行中", text)
        self.assertIn("pid=99", text)
        self.assertIn("已重启 2 次", text)

    def test_stopped_state_points_at_the_autostart_and_manual_scripts(self):
        with patch.object(guard, "read_state", return_value={"status": "stopped", "updatedAt": 0}), patch.object(
            guard, "probe", return_value=None
        ):
            text = guard.describe_service()
        self.assertIn("已停止", text)
        self.assertIn("启动后台服务.cmd", text)

    def test_never_started_state_explains_the_first_step(self):
        with patch.object(guard, "read_state", return_value={}), patch.object(guard, "probe", return_value=None):
            text = guard.describe_service()
        self.assertIn("尚未启动", text)
        self.assertIn("开机自启-安装.cmd", text)

    def test_status_is_read_only_and_survives_a_missing_state_file(self):
        with patch.object(guard, "read_state", return_value={}), patch.object(guard, "probe", return_value=None) as probe:
            guard.describe_service()
        probe.assert_called_once()


class ServiceRestartTests(unittest.TestCase):
    """restart 必须先让旧守护退场，否则会撞上端口占用而白跑一趟。"""

    def test_restart_asks_the_old_supervisor_to_leave_then_takes_over(self):
        stop = MagicMock()
        with patch.object(guard, "STOP", stop), patch.object(guard, "wait_for_supervisor_exit", return_value=True) as wait, patch.object(
            guard, "supervise", return_value=0
        ) as supervise, patch.object(guard.sys, "argv", ["service_guard.py", "restart"]):
            self.assertEqual(guard.main(), 0)
        stop.touch.assert_called_once()
        wait.assert_called_once()
        supervise.assert_called_once_with("127.0.0.1", 8765)

    def test_restart_refuses_to_start_when_the_old_supervisor_will_not_leave(self):
        with patch.object(guard, "STOP", MagicMock()), patch.object(guard, "wait_for_supervisor_exit", return_value=False), patch.object(
            guard, "supervise"
        ) as supervise, patch.object(guard.sys, "argv", ["service_guard.py", "restart"]):
            self.assertEqual(guard.main(), 3)
        supervise.assert_not_called()

    def test_status_json_flag_keeps_the_raw_payload_for_scripts(self):
        state = Path(tempfile.gettempdir()) / "guard-status-test.json"
        state.write_text('{"status":"running"}', encoding="utf-8")
        try:
            with patch.object(guard, "STATE", state), patch.object(guard.sys, "argv", ["service_guard.py", "status", "--json"]):
                self.assertEqual(guard.main(), 0)
        finally:
            state.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
