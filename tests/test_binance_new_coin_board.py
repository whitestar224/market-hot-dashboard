"""Binance 新币榜（USDⓈ-M Futures）抓取的健壮性。

线上现象（2026-10-20）：卡片显示「已请求 Binance USDⓈ-M Futures exchangeInfo，但没有
返回可展示的新合约数据」。真相不是「币安没有新币」，而是 fapi 被限流时回了一个
**没有 symbols 的 JSON**（{"code":-1003,...}），旧代码不校验响应体，直接当成空榜，
于是把「上游抖了一下」渲染成「没有新合约」。

这里钉住四件事：正常出榜、限流不算空榜、失败退上一版、最近全是美股合约要说明白。
"""
import unittest
from unittest.mock import patch

import server


def contract(symbol, base, onboard, *, status="TRADING", quote="USDT", contract_type="PERPETUAL"):
    return {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": quote,
        "status": status,
        "contractType": contract_type,
        "onboardDate": onboard,
    }


def exchange(*symbols):
    return {"symbols": list(symbols)}


def tickers(*symbols):
    return [
        {"symbol": item["symbol"], "lastPrice": "1.23", "priceChangePercent": "4.5",
         "quoteVolume": "1000000"}
        for item in symbols
    ]


class BinanceNewCoinBoardTests(unittest.TestCase):
    def setUp(self):
        server.BINANCE_NEW_LAST_GOOD.clear()
        self.addCleanup(server.BINANCE_NEW_LAST_GOOD.clear)

    def fetch_with(self, exchange_payload, ticker_payload=None):
        def fake(path, **kwargs):
            if "exchangeInfo" in path:
                return exchange_payload
            return ticker_payload if ticker_payload is not None else []

        with patch.object(server, "binance_futures_json", side_effect=fake):
            return server.fetch_binance_new_coins()

    def test_the_board_lists_the_newest_crypto_perpetuals(self):
        payload = exchange(
            contract("OLDUSDT", "OLD", 1_700_000_000_000),
            contract("NEWUSDT", "NEW", 1_790_000_000_000),
            # 美股合约不进新币榜
            contract("AAPLUSDT", "AAPL", 1_800_000_000_000, contract_type="TRADIFI_PERPETUAL"),
            # 非 USDT 交易对 / 已下架也不进
            contract("BTCUSDC", "BTC", 1_800_000_000_000, quote="USDC"),
            contract("DEADUSDT", "DEAD", 1_800_000_000_000, status="SETTLING"),
        )
        section = self.fetch_with(payload, tickers(payload["symbols"][1]))
        self.assertEqual(section["status"], "ok")
        self.assertEqual([row["symbol"] for row in section["rows"]], ["NEWUSDT", "OLDUSDT"])
        self.assertEqual(section["rows"][0]["rank"], 1)

    def test_a_rate_limited_body_is_not_rendered_as_an_empty_board(self):
        """限流时 body 是 {"code":-1003}，绝不能变成「没有可展示的新合约数据」。"""
        def fake(path, **kwargs):
            raise RuntimeError("code=-1003 Too many requests")

        with patch.object(server, "binance_futures_json", side_effect=fake):
            section = server.fetch_binance_new_coins()

        self.assertEqual(section["status"], "unavailable")
        self.assertEqual(section["rows"], [])
        self.assertIn("请求失败", section["emptyTitle"])
        self.assertIn("-1003", section["emptyMessage"])
        self.assertNotIn("没有返回可展示的新合约数据", section["emptyMessage"])

    def test_a_failed_refresh_keeps_the_previous_rows(self):
        """上游抖一下不该把整张榜清空——保住上一次的 10 行。"""
        good = exchange(contract("NEWUSDT", "NEW", 1_790_000_000_000))
        self.fetch_with(good, tickers(*good["symbols"]))

        def fake(path, **kwargs):
            raise RuntimeError("HTTP 418 I'm a teapot")

        with patch.object(server, "binance_futures_json", side_effect=fake):
            section = server.fetch_binance_new_coins()

        self.assertEqual(section["status"], "ok")
        self.assertEqual([row["symbol"] for row in section["rows"]], ["NEWUSDT"])
        self.assertIn("上一版", section["sourceName"])

    def test_the_mirror_fallback_skips_a_dead_primary_host(self):
        calls = []

        class FakeResponse:
            def __init__(self, host):
                self.host = host

            def raise_for_status(self):
                return None

            def json(self):
                return exchange(contract("NEWUSDT", "NEW", 1_790_000_000_000))

        def fake_get(url, **kwargs):
            calls.append(url)
            if url.startswith(server.BINANCE_FUTURES_HOSTS[0]):
                raise RuntimeError("connection reset")
            return FakeResponse(url)

        with patch.object(server.requests, "get", side_effect=fake_get):
            data = server.binance_futures_json("/fapi/v1/exchangeInfo")

        self.assertEqual(len(calls), 2)
        self.assertIn("fapi1", calls[1])
        self.assertIn("symbols", data)

    def test_a_body_without_symbols_is_treated_as_a_failure(self):
        def fake(path, **kwargs):
            return {"code": -1003, "msg": "Too many requests"}

        with patch.object(server, "binance_futures_json", side_effect=fake):
            section = server.fetch_binance_new_coins()
        self.assertEqual(section["status"], "unavailable")
        self.assertIn("symbols", section["emptyMessage"])

    def test_an_all_tradfi_week_is_explained_instead_of_looking_broken(self):
        payload = exchange(
            contract("AAPLUSDT", "AAPL", 1_800_000_000_000, contract_type="TRADIFI_PERPETUAL"),
            contract("NVDAUSDT", "NVDA", 1_800_100_000_000, contract_type="TRADIFI_PERPETUAL"),
        )
        section = self.fetch_with(payload, tickers(*payload["symbols"]))
        self.assertEqual(section["rows"], [])
        self.assertIn("TradFi", section["emptyMessage"])

    def test_ticker_failure_does_not_empty_the_board(self):
        payload = exchange(contract("NEWUSDT", "NEW", 1_790_000_000_000))

        def fake(path, **kwargs):
            if "exchangeInfo" in path:
                return payload
            raise RuntimeError("ticker down")

        with patch.object(server, "binance_futures_json", side_effect=fake):
            section = server.fetch_binance_new_coins()
        self.assertEqual(section["status"], "ok")
        self.assertEqual(len(section["rows"]), 1)
        # 没行情时价格列留空，但榜照出。
        self.assertEqual(section["rows"][0]["turnover"], "--")


if __name__ == "__main__":
    unittest.main()
