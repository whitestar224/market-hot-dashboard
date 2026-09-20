import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from monitor_balances import BalanceReadCache
from monitor_buy import MonitorBuyService, ZERO

OWNER = "0x" + "12" * 20
TOKEN = "0x" + "34" * 20


class BalanceCacheTests(unittest.TestCase):
    def test_ttl_copies_zero_and_isolation(self):
        cache = BalanceReadCache(capacity=2)
        loader = Mock(return_value={"balance": 0})
        with patch("monitor_balances.time.monotonic", return_value=100):
            cached = cache.get(("binance", OWNER, 56, ZERO), loader)
            cached["balance"] = 9
            self.assertEqual(cache.get(("binance", OWNER, 56, ZERO), loader)["balance"], 0)
        loader.assert_called_once()
        with patch("monitor_balances.time.monotonic", return_value=121):
            cache.get(("binance", OWNER, 56, ZERO), loader)
        self.assertEqual(loader.call_count, 2)
        cache.get(("okx", OWNER, 56, ZERO), loader)
        cache.get(("binance", TOKEN, 56, ZERO), loader)
        cache.get(("binance", OWNER, 8453, ZERO), loader)
        cache.get(("binance", OWNER, 56, TOKEN), loader)
        self.assertEqual(loader.call_count, 6)
        self.assertEqual(len(cache.values), 2)

    def test_failed_reads_are_not_cached_as_zero(self):
        cache = BalanceReadCache()
        loader = Mock(side_effect=[TimeoutError("RPC"), 12])
        with self.assertRaises(TimeoutError): cache.get("key", loader)
        self.assertEqual(cache.get("key", loader), 12)
        self.assertEqual(loader.call_count, 2)

    def test_concurrent_reads_share_one_request(self):
        cache = BalanceReadCache()
        entered, release = threading.Event(), threading.Event()
        def read():
            entered.set()
            if not release.wait(5): raise TimeoutError()
            return 42
        loader = Mock(side_effect=read)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(cache.get, "key", loader)
            self.assertTrue(entered.wait(2))
            second = pool.submit(cache.get, "key", loader)
            release.set()
            self.assertEqual(first.result(3), second.result(3))
        loader.assert_called_once()


class BalanceWarmupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = MonitorBuyService(Path(self.temp.name) / "orders.sqlite")
        self.native = {"chainId": 56, "address": ZERO, "decimals": 18, "symbol": "BNB"}
        self.stable = {"chainId": 56, "address": TOKEN, "decimals": 6, "symbol": "USDC"}
        self.service.networks = Mock(return_value={56: {"id": 56, "name": "bnb"}})
        self.service.tokens = Mock(return_value=[self.native, self.stable])
        self.service.http = Mock(return_value={"price": "600"})
        self.payload = {"wallets": {"evm": OWNER}, "walletProvider": "binance", "sourceChains": [56]}

    def tearDown(self):
        self.service.warm_pool.shutdown(wait=True, cancel_futures=True)
        self.service.pool.shutdown(wait=True, cancel_futures=True)
        self.service.read_pool.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    def test_background_warm_returns_without_waiting_or_using_quote_workers(self):
        entered, release = threading.Event(), threading.Event()
        def read(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {"items": [], "errors": []}
        self.service.balances = Mock(side_effect=read)
        self.service.quote = Mock(side_effect=AssertionError("must not quote or sign"))
        try:
            result = self.service.warm_balances({**self.payload, "preferredChain": 56})
            self.assertTrue(entered.wait(2))
            self.assertGreater(result["queued"], 0)
            self.assertEqual(self.service.warm_balances({**self.payload, "preferredChain": 56})["queued"], 0)
            self.assertLessEqual(len(self.service.warm_pending), 32)
            self.assertEqual(self.service.pool.submit(lambda: 42).result(1), 42)
            self.assertFalse(release.is_set())
        finally: release.set()
        self.service.warm_pool.shutdown(wait=True)
        self.service.quote.assert_not_called()
        for call in self.service.balances.call_args_list:
            self.assertFalse(call.kwargs["parallel"])
            self.assertEqual(call.args[0]["wallets"], {"evm": OWNER})

    def test_warmed_reads_reused_but_account_and_provider_remain_isolated(self):
        self.service.token_balance = Mock(side_effect=lambda network, token, owner: 10**18 if token["address"] == ZERO else 0)
        self.service.balances(self.payload, token_kind="all", parallel=False)
        self.assertEqual(self.service.token_balance.call_count, 2)
        result = self.service.balances(self.payload, token_kind="all")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(self.service.token_balance.call_count, 2)
        self.service.balances({**self.payload, "wallets": {"evm": TOKEN}}, token_kind="native")
        self.service.balances({**self.payload, "walletProvider": "okx"}, token_kind="native")
        self.assertEqual(self.service.token_balance.call_count, 4)

    def test_unreadable_balance_reports_error_and_recovers_on_next_read(self):
        self.service.token_balance = Mock(side_effect=[TimeoutError(), 10**18])
        first = self.service.balances(self.payload, token_kind="native")
        self.assertTrue(first["errors"])
        self.assertEqual(first["items"], [])
        second = self.service.balances(self.payload, token_kind="native")
        self.assertFalse(second["errors"])
        self.assertEqual(len(second["items"]), 1)

    def test_empty_or_invalid_wallet_does_not_start_background_work(self):
        self.assertEqual(self.service.warm_balances({})["queued"], 0)
        with self.assertRaises(ValueError): self.service.warm_balances({"wallets": {"evm": "invalid"}})
        self.assertEqual(len(self.service.warm_pending), 0)

    def test_native_balance_and_price_are_parallel_leaf_reads(self):
        barrier = threading.Barrier(2)
        def read(value):
            barrier.wait(timeout=3)
            return value
        self.service.http = Mock(side_effect=lambda *a, **k: read({'price': '600'}))
        self.service.token_balance = Mock(side_effect=lambda *a: read(10**18))
        result = self.service.balances(self.payload, token_kind='native')
        self.assertFalse(result['errors'])
        self.assertEqual(result['items'][0]['priceUsd'], '600')


if __name__ == "__main__":
    unittest.main()
