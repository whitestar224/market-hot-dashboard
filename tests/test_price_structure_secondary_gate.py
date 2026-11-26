"""结构监控的准入：只收「交易所的加密现货/合约」，链上标的和股票代币都不收。

用户 2026-11-26 两句话定的口径：
  * 「把链上标的的结构监控给停止掉」+「只监控二级合约的结构」；
  * 「还有就是股票代币也不做任何的结构监控」。

三条断言：
  1. 股票代币（交易所自己标了 TradFi）**一票否决**，哪怕二级名单是空的；
  2. 链上内盘（只被钱包 4H / GMGN 热搜 / 链上 CA 带进来的）不进池；
  3. 交易所清单抓不到（冷启动、上游全挂）时放行 —— 宁可照旧，也不能把整池关掉。
"""
import unittest
from unittest.mock import patch

import server


UNIVERSE = frozenset({"BTC", "ETH", "AAVE", "PEPE", "ZEC"})


class SecondaryGateTests(unittest.TestCase):
    def test_exchange_crypto_is_allowed(self):
        for symbol in ("BTC", "ETH", "BTCUSDT", "pepe", "1000PEPE"):
            with self.subTest(symbol=symbol):
                self.assertTrue(
                    server.price_structure_symbol_is_secondary(symbol, universe=UNIVERSE)
                )

    def test_onchain_only_ticker_is_refused(self):
        # 「全球第一吊」这种只有链上内盘的中文名、以及没上任何交易所的英文名。
        for symbol in ("全球第一吊", "SOMEDOGCOIN", "0x7A9B91B6545D1001CAEA6B11EE561556FC4C7777"):
            with self.subTest(symbol=symbol):
                self.assertFalse(
                    server.price_structure_symbol_is_secondary(symbol, universe=UNIVERSE)
                )

    def test_tradfi_is_refused_even_without_a_universe(self):
        # 用户：「股票代币也不做任何的结构监控」。股票代币的否决**不能**依赖二级名单
        # 抓没抓到 —— 冷启动时名单是空的。
        with patch.object(server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset({"NVDA", "TSLA"})):
            for symbol in ("NVDA", "TSLAUSDT"):
                with self.subTest(symbol=symbol):
                    self.assertFalse(
                        server.price_structure_symbol_is_secondary(symbol, universe=frozenset())
                    )
                    self.assertFalse(
                        server.price_structure_symbol_is_secondary(symbol, universe=UNIVERSE)
                    )

    def test_crypto_wins_when_the_ticker_is_not_tradfi(self):
        with patch.object(server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset({"NVDA"})):
            self.assertTrue(server.price_structure_symbol_is_secondary("BTC", universe=UNIVERSE))

    def test_empty_universe_falls_open(self):
        # 上游全挂 / 冷启动且磁盘没有上一版：不能把整池关掉。
        self.assertTrue(server.price_structure_symbol_is_secondary("WHATEVER", universe=frozenset()))

    def test_empty_symbol_is_refused(self):
        self.assertFalse(server.price_structure_symbol_is_secondary("", universe=UNIVERSE))
        self.assertFalse(server.price_structure_symbol_is_secondary(None, universe=UNIVERSE))


class PopupGateTests(unittest.TestCase):
    """弹窗这条路也必须过同一道门（页面不显示 ≠ 不弹）。"""

    def test_onchain_item_never_pops(self):
        item = {"symbol": "全球第一吊", "monitorPool": "structure"}
        with patch.object(server, "PRICE_STRUCTURE_SECONDARY_ONLY", True), patch.object(
            server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset()
        ), patch.object(
            server, "price_watch_secondary_market_symbols", lambda: UNIVERSE
        ):
            self.assertFalse(server.price_structure_item_in_visible_monitor(item))

    def test_tradfi_item_never_pops(self):
        item = {"symbol": "NVDA"}
        with patch.object(server, "PRICE_STRUCTURE_SECONDARY_ONLY", True), patch.object(
            server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset({"NVDA"})
        ), patch.object(
            server, "price_watch_secondary_market_symbols", lambda: UNIVERSE
        ):
            self.assertFalse(server.price_structure_item_in_visible_monitor(item))

    def test_secondary_item_keeps_the_previous_behaviour(self):
        item = {"symbol": "BTC"}
        with patch.object(server, "PRICE_STRUCTURE_SECONDARY_ONLY", True), patch.object(
            server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset()
        ), patch.object(
            server, "price_watch_secondary_market_symbols", lambda: UNIVERSE
        ):
            self.assertTrue(
                server.price_structure_item_in_visible_monitor(
                    item, visibility=(frozenset(), frozenset())
                )
            )

    def test_new_coin_low_keeps_its_own_surface(self):
        # 交易所新上市低位池有自己的页面，且新币刚上市时清单缓存可能还没刷到。
        item = {"symbol": "NEWLISTING", "monitorPool": "new-coin-low"}
        with patch.object(server, "PRICE_STRUCTURE_SECONDARY_ONLY", True), patch.object(
            server, "price_watch_secondary_market_symbols", lambda: UNIVERSE
        ):
            self.assertTrue(server.price_structure_item_in_visible_monitor(item))

    def test_switch_off_restores_the_old_pool(self):
        item = {"symbol": "全球第一吊"}
        with patch.object(server, "PRICE_STRUCTURE_SECONDARY_ONLY", False), patch.object(
            server, "price_watch_secondary_market_symbols", lambda: UNIVERSE
        ):
            self.assertTrue(
                server.price_structure_item_in_visible_monitor(
                    item, visibility=(frozenset(), frozenset())
                )
            )


class StockTokenTests(unittest.TestCase):
    """`new-coin-low` 榜上全是刚上市的新合约，只否决股票/指数代币，不套二级白名单。

    `fapi.binance.com` / `www.okx.com` 在本机长期超时，`tradfi_assets` 靠各 venue
    现场抓会剩空集 —— 2026-11-26 实测「新币低点」63 条里有 60 条是 BABA/ADBE/NFLX
    这类股票代币，全是这么漏进来的。所以这层用**全局 TradFi 名单**兜底。
    """

    def _is_stock(self, symbol, tradfi, crypto=UNIVERSE):
        with patch.object(server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset(tradfi)), patch.object(
            server, "price_watch_secondary_market_symbols", lambda: crypto
        ):
            return server.price_structure_symbol_is_stock_token(symbol)

    def test_stock_token_is_refused(self):
        for symbol in ("NVDA", "TSLAUSDT", "BABA", "US100"):
            with self.subTest(symbol=symbol):
                self.assertTrue(self._is_stock(symbol, {"NVDA", "TSLA", "BABA", "US100"}))

    def test_stock_token_is_refused_even_when_the_crypto_list_is_empty(self):
        # 冷启动 / 上游全挂时 `price_watch_secondary_market_symbols()` 是空集，
        # 这时候更不能把股票代币放进来。
        self.assertTrue(self._is_stock("NVDA", {"NVDA"}, crypto=frozenset()))

    def test_onchain_listing_is_not_mistaken_for_a_stock(self):
        # 链上内盘（中文名 / 没上任何交易所的币）不是股票 —— 这一层只管股票，
        # 链上的由 `price_structure_symbol_is_secondary()` 管，别在这里顺手误杀。
        for symbol in ("全球第一吊", "SOMEDOGCOIN"):
            with self.subTest(symbol=symbol):
                self.assertFalse(self._is_stock(symbol, {"NVDA", "BABA"}))

    def test_same_ticker_crypto_contract_wins(self):
        # CAT 既是 Gate 的 Caterpillar，也是币安的 Simons Cat —— 加密那份赢
        # （和 `new_coin_low_symbol_excluded_as_tradfi()` 的老口径一致）。
        self.assertFalse(self._is_stock("CAT", {"CAT"}, crypto=UNIVERSE | {"CAT"}))

    def test_new_coin_low_popup_refuses_stock_tokens(self):
        item = {"symbol": "BABA", "monitorPool": "new-coin-low"}
        with patch.object(server, "PRICE_STRUCTURE_SECONDARY_ONLY", True), patch.object(
            server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset({"BABA"})
        ), patch.object(server, "price_watch_secondary_market_symbols", lambda: UNIVERSE):
            self.assertFalse(server.price_structure_item_in_visible_monitor(item))

    def test_new_coin_low_popup_still_opens_for_a_fresh_crypto_listing(self):
        item = {"symbol": "BRANDNEW", "monitorPool": "new-coin-low"}
        with patch.object(server, "PRICE_STRUCTURE_SECONDARY_ONLY", True), patch.object(
            server, "PRICE_STRUCTURE_TRADFI_SYMBOLS", frozenset({"BABA"})
        ), patch.object(server, "price_watch_secondary_market_symbols", lambda: UNIVERSE):
            self.assertTrue(server.price_structure_item_in_visible_monitor(item))


if __name__ == "__main__":
    unittest.main()
