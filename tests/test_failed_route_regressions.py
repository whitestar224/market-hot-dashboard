import ast
import gzip
import io
import json
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse


# Follow test_http_disconnects: load real HTTP code without server startup,
# configuration, database, or runtime-cache imports.
source = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
tree = ast.parse(source)
names = {"Handler", "safe_error_text", "normalize_binance_wallet_hot_period",
         "BINANCE_WALLET_HOT_PERIODS"}
nodes = [
    node for node in tree.body
    if (isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names)
    or (isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in names for target in node.targets))
]
module = ast.Module(body=[
    ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
    *nodes,
], type_ignores=[])
namespace = dict(SimpleHTTPRequestHandler=SimpleHTTPRequestHandler, threading=threading,
                 json=json, gzip=gzip, re=re, urlparse=urlparse, parse_qs=parse_qs,
                 MONITOR_BUY=SimpleNamespace(
                     identities=SimpleNamespace(enrich=lambda payload: payload),
                 ))
exec(compile(ast.fix_missing_locations(module), "server.py", "exec"), namespace)
Handler = namespace["Handler"]


class FailedRouteRegressionTests(unittest.TestCase):
    def request_raw(self, path):
        handler = object.__new__(Handler)
        handler.rfile = io.BytesIO(
            f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii")
        )
        handler.wfile = io.BytesIO()
        handler.client_address = ("127.0.0.1", 53314)
        handler.close_connection = False
        handler.log_message = Mock()
        handler.current_user = Mock(return_value=None)
        handler.end_headers = lambda: BaseHTTPRequestHandler.end_headers(handler)
        handler.handle_one_request()
        headers, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        lines = headers.decode("ascii").split("\r\n")
        fields = dict(line.split(": ", 1) for line in lines[1:])
        self.assertEqual(int(fields["Content-Length"]), len(body))
        return int(lines[0].split()[1]), fields, body

    def request(self, path):
        status, fields, body = self.request_raw(path)
        self.assertEqual(fields["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(fields["Cache-Control"], "no-store")
        return status, json.loads(body)

    def check_route(self, path, provider_name, empty_payload, error_payload, args=(), kwargs=None):
        kwargs = kwargs or {}
        for outcome in ("upstream-error", "empty-result"):
            with self.subTest(outcome=outcome):
                provider = Mock()
                if outcome == "upstream-error":
                    provider.side_effect = TimeoutError("upstream timeout; token=fixture-only")
                else:
                    provider.return_value = empty_payload
                refresh = Mock(side_effect=AssertionError("unexpected monitor refresh"))
                with patch.dict(namespace, {provider_name: provider,
                                            "sync_price_watch_monitor": refresh}):
                    status, payload = self.request(path)
                provider.assert_called_once_with(*args, **kwargs)
                refresh.assert_not_called()
                if outcome == "upstream-error":
                    self.assertEqual(status, 502)
                    self.assertEqual(payload, {
                        **error_payload, "error": "upstream timeout; token=***",
                    })
                else:
                    self.assertEqual(status, 200)
                    self.assertEqual(payload, empty_payload)

    def test_price_watch_failure_and_empty_result(self):
        self.check_route(
            "/api/price-watch", "price_watch_payload",
            {"ok": True, "items": []},
            {"ok": False, "items": []},
            kwargs={"sync_candidates": False},
        )

    def test_strategy_board_failure_and_empty_result(self):
        self.check_route(
            "/api/strategy-board", "strategy_orders_payload",
            {"ok": True, "authenticated": False, "signals": [], "positions": []},
            {"ok": False, "signals": [], "positions": []},
            args=(None,),
        )

    def test_binance_wallet_hot_refresh_failure_and_empty_result(self):
        self.check_route(
            "/api/binance-wallet-hot?period=4h&refresh=1", "binance_wallet_hot_source",
            {"id": "binance-wallet-hot", "period": "4h", "rows": []},
            {"ok": False, "id": "binance-wallet-hot", "period": "4h", "rows": []},
            args=("4h",), kwargs={"force_refresh": True},
        )

    def test_main_service_exposes_the_dragon_wave_release_endpoint(self):
        payload = {"requestedVersion": "v91", "selectedVersion": "v91", "pending": False}
        release = SimpleNamespace(
            CURRENT_VERSION="v91",
            resolve_case=Mock(return_value=payload),
        )
        local = SimpleNamespace(
            strategy_release=release,
            PRECOMPUTED_ROOT=Path("precomputed"),
            precomputed_lookup=Mock(return_value=None),
        )
        path = "/api/dragon-wave-release?pair=TUTUSDT&start=2026-07-09&end=2026-08-10&market=futures&stage=active"
        with patch.dict(namespace, {"dragon_wave_local": local}):
            status, response = self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(response, payload)
        release.resolve_case.assert_called_once_with(
            Path("precomputed"), "TUTUSDT", "2026-07-09", "2026-08-10", "futures", "active"
        )

    def test_main_service_streams_the_existing_precomputed_snapshot(self):
        payload = {"version": "v91", "result": {"candles": [{"time": 1}]}}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json.gz"
            snapshot.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
            local = SimpleNamespace(precomputed_lookup=Mock(return_value=(snapshot, {"candleCount": 1})))
            path = "/api/dragon-wave-precomputed?version=v91&pair=TUTUSDT&start=2026-07-09&end=2026-08-10&interval=15m&market=futures&stage=active"
            with patch.dict(namespace, {"dragon_wave_local": local}):
                status, fields, body = self.request_raw(path)
        self.assertEqual(status, 200)
        self.assertEqual(fields["Content-Encoding"], "gzip")
        self.assertEqual(fields["X-Dragon-Wave-Precomputed"], "1")
        self.assertEqual(json.loads(gzip.decompress(body)), payload)


if __name__ == "__main__":
    unittest.main()

