import unittest
from unittest.mock import patch

import server


class PriceWatchTradeUrlTests(unittest.TestCase):
    def test_builds_binance_futures_url(self):
        self.assertEqual(
            server.price_watch_trade_url("WLD", "Binance Futures"),
            "https://www.binance.com/zh-CN/futures/WLDUSDT",
        )

    def test_builds_okx_swap_url_for_okx_provider_alias(self):
        self.assertEqual(
            server.price_watch_trade_url("HYPE", "OKX Futures"),
            "https://www.okx.com/zh-hans/trade-swap/hype-usdt-swap",
        )

    def test_builds_bitget_futures_url(self):
        self.assertEqual(
            server.price_watch_trade_url("RIVER", "Bitget Futures"),
            "https://www.bitget.com/zh-CN/futures/usdt/RIVERUSDT",
        )

    def test_unknown_provider_keeps_monitor_fallback(self):
        self.assertEqual(
            server.price_watch_trade_url("WLD", "Market data"),
            "./price-watch.html",
        )

    def test_onchain_contract_opens_matching_binance_wallet_token(self):
        self.assertEqual(
            server.price_watch_trade_url(
                "INDEX",
                "链上多源 K线",
                chain_id="4663",
                contract_address="0x56910d4409f3a0c78c64dd8d0545ff0705389870",
            ),
            "https://web3.binance.com/en/token/robinhood/0x56910d4409f3a0c78c64dd8d0545ff0705389870?ref=MQ6JD2X4",
        )

    def test_wallet_origin_overrides_secondary_contract_provider(self):
        self.assertEqual(
            server.price_watch_trade_url(
                "STONK",
                "Binance Futures",
                chain_id="CT_501",
                contract_address="6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
                prefer_wallet=True,
            ),
            "https://web3.binance.com/en/token/sol/6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx?ref=MQ6JD2X4",
        )

    def test_price_alert_view_uses_signal_provider_trade_page(self):
        event = {
            "symbol": "WLD",
            "distancePct": 2.97,
            "currentPrice": 0.3436,
            "weekHigh": 0.3541,
            "episode": 1,
            "provider": "Binance Futures",
        }
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertEqual(payload["url"], "https://www.binance.com/zh-CN/futures/WLDUSDT")

    def test_oversold_alert_view_uses_signal_provider_trade_page(self):
        event = {
            "eventType": "oversold_rebound",
            "symbol": "TEST",
            "distancePct": 1.2,
            "currentPrice": 0.49,
            "weekHigh": 1,
            "drawdownPct": 51,
            "rangeLow": 0.4,
            "rangeHigh": 0.5,
            "episode": 1,
            "provider": "OKX Swap",
        }
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertEqual(payload["url"], "https://www.okx.com/zh-hans/trade-swap/test-usdt-swap")

    def test_onchain_price_alert_view_prefers_binance_wallet(self):
        event = {
            "symbol": "INDEX",
            "distancePct": 1.2,
            "currentPrice": 0.044,
            "weekHigh": 0.045,
            "episode": 1,
            "provider": "链上多源 K线",
            "chain": "4663",
            "contractAddress": "0x56910d4409f3a0c78c64dd8d0545ff0705389870",
        }
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertIn("/token/robinhood/0x56910d4409f3a0c78c64dd8d0545ff0705389870", payload["url"])

    def test_wallet_origin_alert_ignores_secondary_provider_page(self):
        event = {
            "symbol": "STONK",
            "distancePct": 2.93,
            "currentPrice": 0.2819,
            "weekHigh": 0.29041137,
            "episode": 1,
            "provider": "Binance Futures",
            "chain": "CT_501",
            "contractAddress": "6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
            "binanceWalletHot": True,
        }
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertIn(
            "/token/sol/6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
            payload["url"],
        )
        self.assertNotIn("/futures/", payload["url"])


if __name__ == "__main__":
    unittest.main()
