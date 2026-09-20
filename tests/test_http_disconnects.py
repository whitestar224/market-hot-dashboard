import ast
import gzip
import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler
from pathlib import Path
from unittest.mock import Mock, patch


# Load the actual handler without executing server startup/config/cache imports.
source = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
tree = ast.parse(source)
handler_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Handler")
module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), handler_node], type_ignores=[])
namespace = dict(SimpleHTTPRequestHandler=SimpleHTTPRequestHandler, threading=threading,
                 json=json, gzip=gzip)
exec(compile(ast.fix_missing_locations(module), "server.py", "exec"), namespace)
Handler = namespace["Handler"]


class DisconnectTests(unittest.TestCase):
    def setUp(self):
        Handler.disconnect_counts = {}
        Handler.disconnect_total = 0

    def handler(self, request=b"GET /api/check?token=hidden HTTP/1.1\r\nHost: localhost\r\n\r\n"):
        handler = object.__new__(Handler)
        handler.rfile = io.BytesIO(request)
        handler.wfile = io.BytesIO()
        handler.client_address = ("127.0.0.1", 53314)
        handler.close_connection = False
        handler.log_message = Mock()
        handler.end_headers = lambda: BaseHTTPRequestHandler.end_headers(handler)
        handler.do_GET = lambda: handler.send_json({"ok": True})
        return handler

    def test_success_preserves_json_response(self):
        handler = self.handler()
        handler.handle_one_request()
        response = handler.wfile.getvalue()
        self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertEqual(json.loads(response.split(b"\r\n\r\n", 1)[1]), {"ok": True})
        self.assertEqual(Handler.disconnect_total, 0)

    def test_monitor_response_attaches_preflight_but_never_changes_quote_payload(self):
        buy = Mock()
        buy.identities.enrich.side_effect = lambda payload: {**payload, "preflight": "attached"}
        with patch.dict(namespace, MONITOR_BUY=buy):
            for path in ("/api/event-monitor", "/api/price-watch?refresh=1", "/api/event-flow", "/api/chain-ecosystem"):
                handler = self.handler(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                handler.handle_one_request()
                body = json.loads(handler.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
                self.assertEqual(body["preflight"], "attached")
            buy.identities.enrich.reset_mock()
            handler = self.handler(b"GET /api/monitor-buy/quote HTTP/1.1\r\nHost: localhost\r\n\r\n")
            handler.handle_one_request()
            buy.identities.enrich.assert_not_called()

    def test_write_disconnects_are_counted_without_traceback(self):
        for error_type in (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            with self.subTest(error=error_type):
                handler = self.handler()
                handler.wfile = Mock()
                handler.wfile.write.side_effect = error_type(10054, "disconnected")
                handler.handle_one_request()
                self.assertTrue(handler.close_connection)
                self.assertEqual(Handler.disconnect_counts[("/api/check", "127.0.0.1", error_type.__name__)], 1)
                message = handler.log_message.call_args
                self.assertEqual(message.args[0].split()[0], "client_disconnect")
                self.assertNotIn("hidden", str(message))

    def test_response_body_and_flush_disconnects(self):
        for stage in ("body", "flush"):
            with self.subTest(stage=stage):
                self.setUp()
                handler = self.handler()
                handler.wfile = Mock()
                if stage == "body":
                    handler.wfile.write.side_effect = [None, ConnectionResetError()]
                else:
                    handler.wfile.flush.side_effect = ConnectionAbortedError()
                handler.handle_one_request()
                self.assertEqual(Handler.disconnect_total, 1)
                self.assertTrue(handler.close_connection)

    def test_stream_disconnect_uses_same_counter(self):
        handler = self.handler()
        handler.path = "/api/stream"
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = BrokenPipeError()
        with patch.dict(namespace, x_kol_realtime_key=lambda user: "key",
                        x_kol_realtime_snapshot=lambda user, wait_seconds: ({}, "sig")):
            handler.send_x_kol_stream(None)
        self.assertEqual(Handler.disconnect_counts[("/api/stream", "127.0.0.1", "BrokenPipeError")], 1)

    def test_read_disconnect_does_not_reuse_keepalive_route(self):
        handler = self.handler()
        handler.handle_one_request()
        handler.rfile = Mock()
        handler.rfile.readline.side_effect = ConnectionAbortedError(10053, "disconnected")
        handler.handle_one_request()
        self.assertIn(("<unparsed>", "127.0.0.1", "ConnectionAbortedError"), Handler.disconnect_counts)

    def test_finish_disconnect_is_counted_only_once_and_closes_streams(self):
        for recorded in (False, True):
            with self.subTest(recorded=recorded):
                self.setUp()
                handler = self.handler()
                handler.wfile = Mock(closed=False)
                handler.wfile.flush.side_effect = BrokenPipeError()
                handler.rfile = Mock()
                if recorded:
                    handler.record_client_disconnect(BrokenPipeError())
                handler.finish()
                self.assertEqual(Handler.disconnect_total, 1)
                handler.wfile.close.assert_called_once()
                handler.rfile.close.assert_called_once()

    def test_other_failures_propagate(self):
        for error in (ValueError("bug"), OSError("disk failure")):
            with self.subTest(error=error):
                handler = self.handler()
                handler.do_GET = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    handler.handle_one_request()
                self.assertEqual(Handler.disconnect_total, 0)

    def test_counts_use_peer_and_are_thread_safe(self):
        def disconnect(index):
            handler = self.handler()
            handler.path = "/api/check?token=hidden"
            handler.client_address = ("127.0.0.1", index)
            handler.record_client_disconnect(ConnectionResetError())
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(disconnect, range(80)))
        self.assertEqual(Handler.disconnect_total, 80)
        self.assertEqual(Handler.disconnect_counts, {("/api/check", "127.0.0.1", "ConnectionResetError"): 80})

    def test_cardinality_is_bounded_and_log_values_are_escaped(self):
        with patch.object(Handler, "disconnect_count_limit", 2):
            for index in range(10):
                handler = self.handler()
                handler.path = "/route" + str(index) + "\nforged?secret=hidden"
                handler.record_client_disconnect(BrokenPipeError())
                self.assertNotIn("\n", handler.log_message.call_args.args[1])
                self.assertNotIn("hidden", str(handler.log_message.call_args))
        self.assertEqual(len(Handler.disconnect_counts), 3)
        self.assertEqual(sum(Handler.disconnect_counts.values()), 10)


if __name__ == "__main__":
    unittest.main()
