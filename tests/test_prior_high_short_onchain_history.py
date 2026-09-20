import unittest
from contextlib import ExitStack
from unittest.mock import patch

import server


class ShortOnchainPriorHighTests(unittest.TestCase):
    def snapshot(self, hourly_count=4, duplicate=False, fail=False, last_hour_high=10, newer_structure=False):
        start = 1_800_000_000_000
        calls = []

        def onchain(symbol, contract, chain, interval, *, limit, min_rows, timeout):
            calls.append((interval, min_rows))
            if fail:
                raise RuntimeError("contract-bound history unavailable")
            count = hourly_count if interval == "1h" else 2 if interval == "15m" else 0
            rows = [
                (start + (0 if duplicate else i) * 3_600_000, 9, 10, 8, 9.9, 100)
                for i in range(count)
            ]
            if interval == "1h" and rows:
                rows[-1] = (*rows[-1][:2], last_hour_high, *rows[-1][3:])
            if interval == "15m" and newer_structure:
                rows = [(row[0] + hourly_count * 3_600_000, *row[1:]) for row in rows]
            if len(rows) < min_rows:
                raise RuntimeError("insufficient history")
            return rows, "Binance Wallet K线"

        with ExitStack() as stack:
            stack.enter_context(patch.object(server, "price_structure_candles_from_onchain_fallbacks", side_effect=onchain))
            for name in (
                "price_watch_candles_from_binance", "price_watch_candles_from_okx",
                "price_watch_candles_from_bitget_futures", "price_structure_candles_from_kucoin",
                "price_structure_candles_from_gate", "price_structure_candles_from_htx",
                "price_structure_candles_from_aster", "price_structure_candles_from_hyperliquid",
            ):
                stack.enter_context(patch.object(server, name, side_effect=RuntimeError("unrelated exchange unavailable")))
            result = server.fetch_price_watch_snapshot({
                "symbol": "4STOCK", "onchain_chain": "bsc",
                "onchain_contract_address": "0xd270d4e1ec6e6e0d28c0ecb8be966ec75997ffff",
            })
        return result, calls

    def test_four_hour_old_coin_can_monitor_prior_high_without_daily_structure(self):
        result, calls = self.snapshot()
        self.assertEqual(result["status"], "forming", result)
        self.assertEqual(result["weekHigh"], 10)
        self.assertEqual(result["currentPrice"], 9.9)
        self.assertIn(("1h", 2), calls)
        self.assertIn(("15m", 2), calls)
        self.assertEqual(result["fib"]["reason"], "insufficient-daily-history")
        self.assertFalse(result["structure"]["qualified"])

    def test_contract_bound_asset_never_falls_back_to_same_ticker_cex_candles(self):
        exchange_rows = [
            (1_800_000_000_000 + index * 3_600_000, 20, 19)
            for index in range(24)
        ]
        onchain_provider = (
            "链上多源 K线",
            lambda: (_ for _ in ()).throw(RuntimeError("exact contract unavailable")),
            lambda: ([], "链上多源 K线"),
            lambda: ([], "链上多源 K线"),
        )
        with patch.object(
            server, "price_watch_candles_from_binance", return_value=(exchange_rows, "Binance Futures")
        ) as binance, patch.object(
            server, "price_watch_extended_snapshot_providers", return_value=[onchain_provider]
        ), patch.object(
            server, "price_watch_large_exchange_contract", return_value={}
        ), patch.object(server, "PRICE_STRUCTURE_PROVIDER_PREFERENCE", {}):
            result = server.fetch_price_watch_snapshot({
                "symbol": "GME",
                "onchain_chain": "4663",
                "onchain_contract_address": "0x1234567890abcdef1234567890abcdef12345678",
            })

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("exact contract unavailable", result["error"])
        binance.assert_not_called()

    def test_single_candle_cannot_be_its_own_prior_high(self):
        result, _ = self.snapshot(hourly_count=1)
        self.assertEqual(result["status"], "unavailable")

    def test_duplicate_candles_cannot_satisfy_minimum_history(self):
        result, _ = self.snapshot(duplicate=True)
        self.assertEqual(result["status"], "unavailable")

    def test_failure_keeps_relevant_onchain_error(self):
        result, _ = self.snapshot(fail=True)
        self.assertIn("contract-bound history unavailable", result["error"])

    def test_closed_last_hour_is_not_dropped_when_intraday_feed_is_newer(self):
        result, _ = self.snapshot(last_hour_high=20, newer_structure=True)
        self.assertEqual(result["weekHigh"], 20)
        self.assertEqual(result["status"], "normal")

    def test_forming_hour_high_is_not_its_own_breakout_reference(self):
        result, _ = self.snapshot(last_hour_high=20)
        self.assertEqual(result["weekHigh"], 10)


if __name__ == "__main__":
    unittest.main()
