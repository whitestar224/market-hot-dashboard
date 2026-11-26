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

    def test_onchain_contract_opens_matching_gmgn_trade_page(self):
        """链上代币的「查看」落 GMGN 交易页（2026-11-07 用户要求，不再跳到币安）。"""
        self.assertEqual(
            server.price_watch_trade_url(
                "INDEX",
                "链上多源 K线",
                chain_id="4663",
                contract_address="0x56910d4409f3a0c78c64dd8d0545ff0705389870",
            ),
            "https://gmgn.ai/robinhood/token/0x56910d4409f3a0c78c64dd8d0545ff0705389870",
        )

    def test_binance_wallet_chain_ids_fold_into_gmgn_slugs(self):
        """price_watch 多数存的是币安钱包 chainId，必须折成 GMGN 的 slug 才能拼链接。"""
        cases = {
            "56": "bsc",
            "1": "eth",
            "8453": "base",
            "4663": "robinhood",
            "CT_501": "sol",
        }
        for chain_id, slug in cases.items():
            with self.subTest(chain_id=chain_id):
                url = server.onchain_token_view_url(chain_id, "0xabc7777")
                self.assertEqual(url, f"https://gmgn.ai/{slug}/token/0xabc7777")

    def test_elysium_folds_into_the_gmgn_slug(self):
        """Elysium（chainId 99901）在 GMGN 上就是 ``elysium``，别再拼死链。

        铁证：GMGN 自己的 /v1/token/info 返回
        ``link.gmgn = https://gmgn.ai/elysium/token/<CA>``。
        """
        contract = "0x32193afd14c53f12c45a4c04eca9f7ba98e3ec71"
        for chain_id in ("elysium", "99901"):
            with self.subTest(chain_id=chain_id):
                self.assertEqual(
                    server.onchain_token_view_url(chain_id, contract),
                    f"https://gmgn.ai/elysium/token/{contract}",
                )

    def test_unknown_chain_still_falls_back_to_binance_wallet(self):
        """GMGN 认不出的链不许吐死链，回落币安钱包页。"""
        self.assertEqual(
            server.onchain_token_view_url("", "0xabc7777"),
            "",
        )
        url = server.price_watch_trade_url(
            "SOMECOIN", "链上多源 K线", chain_id="", contract_address="0xabc7777"
        )
        self.assertEqual(url, "./price-watch.html")

    def test_wallet_origin_overrides_secondary_contract_provider(self):
        self.assertEqual(
            server.price_watch_trade_url(
                "STONK",
                "Binance Futures",
                chain_id="CT_501",
                contract_address="6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
                prefer_wallet=True,
            ),
            "https://gmgn.ai/sol/token/6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
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

    def test_onchain_price_alert_view_opens_gmgn_trade_page(self):
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

        self.assertEqual(
            payload["url"],
            "https://gmgn.ai/robinhood/token/0x56910d4409f3a0c78c64dd8d0545ff0705389870",
        )
        self.assertNotIn("web3.binance.com", payload["url"])

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

        self.assertEqual(
            payload["url"],
            "https://gmgn.ai/sol/token/6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
        )
        self.assertNotIn("/futures/", payload["url"])


    def test_onchain_asset_with_secondary_binance_provider_never_opens_futures_page(self):
        """链上代币即使被中心化行情源覆盖，也不许跳到币安二级合约页。"""
        url = server.price_watch_trade_url(
            "PUMP",
            "Binance Futures",
            chain_id="CT_501",
            contract_address="pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",
        )
        self.assertIn("gmgn.ai/sol/token/pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn", url)
        self.assertNotIn("/futures/", url)

    def test_onchain_asset_with_unknown_chain_falls_back_instead_of_futures(self):
        """链上入参即使用不出链上代币页（链未知/地址残缺），也不许退化到交易所合约页。"""
        for contract in ("0x56910d4409f3a0c78c64dd8d0545ff0705389870", "0xabc7777"):
            with self.subTest(contract=contract):
                url = server.price_watch_trade_url(
                    "SOMECOIN",
                    "Binance Futures",
                    chain_id="",
                    contract_address=contract,
                )
                self.assertEqual(url, "./price-watch.html")
                self.assertNotIn("/futures/", url)

    def test_onchain_price_alert_with_binance_provider_view_stays_onchain(self):
        event = {
            "symbol": "ZEC",
            "distancePct": 2.4,
            "currentPrice": 41.2,
            "weekHigh": 42.0,
            "episode": 1,
            "provider": "Binance Futures",
            "chain": "CT_501",
            "contractAddress": "A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS",
        }
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertEqual(
            payload["url"],
            "https://gmgn.ai/sol/token/A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS",
        )
        self.assertNotIn("/futures/", payload["url"])


if __name__ == "__main__":
    unittest.main()
