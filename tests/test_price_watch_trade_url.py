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


if __name__ == "__main__":
    unittest.main()
