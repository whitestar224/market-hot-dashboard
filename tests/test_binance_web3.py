import base64
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock
from requests import Timeout

from binance_web3 import BinanceWeb3Client, protect, save_credentials, signed_request


class BinanceWeb3Tests(unittest.TestCase):
    def test_parameter_errors_are_not_misreported_as_unsupported_token(self):
        response = Mock(status_code=200)
        response.json.return_value = {'success':False,'code':40001,
            'msg':'autoSlippage and slippagePercent are mutually exclusive, pass only one'}
        client = BinanceWeb3Client(lambda: ('fake-key','fake-secret'), Mock(return_value=response))
        with self.assertRaisesRegex(ValueError, '自动滑点与固定滑点不能同时传入'):
            client.check_connection()
        response.json.return_value['msg'] = 'fake-secret arbitrary upstream text'
        with self.assertRaises(ValueError) as failure: client.check_connection()
        self.assertNotIn('fake-secret', str(failure.exception))
        self.assertIn('不代表该代币不受支持', str(failure.exception))

    def test_signature_binds_build_prefix_raw_query_and_timestamp(self):
        timestamp = "2026-09-08T14:00:00.000Z"
        path = "/api/v1/dex/aggregator/quote"
        url, headers, body = signed_request("fake-key", "fake-secret", "GET", path, {"symbol": "ETH USDT"}, timestamp=timestamp)
        self.assertEqual(url, "https://web3.binance.com/build"+path+"?symbol=ETH%20USDT")
        message = timestamp + "GET/build" + path + "?symbol=ETH%20USDT"
        self.assertEqual(headers["X-OC-SIGN"], base64.b64encode(hmac.new(b"fake-secret", message.encode(), hashlib.sha256).digest()).decode())
        self.assertEqual(body, "")

    def test_post_body_and_no_broadcast(self):
        _, _, body = signed_request("fake-key", "fake-secret", "POST", "/api/v1/dex/balance/token-balances-by-address", body={"a": "中"})
        self.assertEqual(body, '{"a":"中"}')
        with self.assertRaises(ValueError):
            signed_request("fake-key", "fake-secret", "POST", "/api/v1/dex/broadcast")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_dpapi_storage_contains_no_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"private"
            save_credentials("FAKE-API-KEY", "FAKE-SECRET-TEST-ONLY", path)
            self.assertNotIn(b"FAKE", path.read_bytes())
            self.assertIn(b"FAKE-SECRET-TEST-ONLY", protect(path.read_bytes(), decrypt=True))

    def test_client_fixed_host_no_redirect_no_secret_echo(self):
        http = Mock(return_value=Mock(status_code=401, text="FAKE-SECRET-TEST-ONLY"))
        client = BinanceWeb3Client(lambda: ("fake-key", "fake-secret"), http)
        with self.assertRaisesRegex(ValueError, "凭证或签名"):
            client.check_connection()
        self.assertFalse(http.call_args.kwargs["allow_redirects"])
        self.assertTrue(http.call_args.args[1].startswith("https://web3.binance.com/build/"))

    def test_clock_failure_retries_one_read_with_corrected_timestamp_and_new_nonce(self):
        first = Mock(status_code=401, headers={"Date": format_datetime(datetime.now(timezone.utc)+timedelta(seconds=10))})
        first.json.return_value = {"code": 40103}
        second = Mock(status_code=200)
        second.json.return_value = {"code": 0, "success": True, "data": [{"binanceChainId": "56"}]}
        http = Mock(side_effect=[first, second])
        client = BinanceWeb3Client(lambda: ("fake-key", "fake-secret"), http)
        self.assertTrue(client.check_connection()["ok"])
        a, b = [call.kwargs["headers"] for call in http.call_args_list]
        self.assertNotEqual(a["X-OC-NONCE"], b["X-OC-NONCE"])
        self.assertNotEqual(a["X-OC-TIMESTAMP"], b["X-OC-TIMESTAMP"])
        self.assertNotIn("X-OC-RECV-WINDOW", b)

    def test_read_only_network_failure_retries_once_but_never_broadcasts(self):
        success = Mock(status_code=200)
        success.json.return_value = {"code": 0, "success": True, "data": [{"binanceChainId": "56"}]}
        http = Mock(side_effect=[Timeout(), success])
        client = BinanceWeb3Client(lambda: ("fake-key", "fake-secret"), http)
        self.assertTrue(client.check_connection()["ok"])
        self.assertEqual(http.call_count, 2)
        first, second = [call.kwargs["headers"] for call in http.call_args_list]
        self.assertNotEqual(first["X-OC-NONCE"], second["X-OC-NONCE"])


if __name__ == "__main__":
    unittest.main()
