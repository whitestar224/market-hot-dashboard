"""「链上入池 · 按链开关」——关掉某条链，纯链上标的就不再入池/进结构扫描。

用户 2026-11-07 原话：「链上标的里，Solana 链的会加入结构监控里，Solana 链的标的
我不熟悉，可不可以做个开关，不对 Solana 的链上标的做入池……或者为每条链做一个开关」。

三条不变量：

* 没有存储行 = 该链开着（未动过开关的安装行为与上线前一致）。
* 读失败 fail-open：读不到开关绝不能把整池清空。
* **二级标的不受影响**：有交易所上新记录（``new_contract_listed_at`` > 0）的行，
  即使链被关掉也照旧留在池里 —— 用户关的是「不熟悉的链上噪声」，不是他在交易所盯的币。
"""

import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PriceWatchChainToggleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.temp_dir.name) / "auth.db"
        server.init_auth_db()
        self.reset_cache()

    def tearDown(self):
        self.reset_cache()
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.temp_dir.cleanup()

    @staticmethod
    def reset_cache():
        with server.PRICE_WATCH_CHAIN_TOGGLE_LOCK:
            server.PRICE_WATCH_CHAIN_TOGGLE_CACHE = None

    # -- roster -----------------------------------------------------------

    def test_roster_matches_the_registered_chain_ids(self):
        payload = server.price_watch_chain_payload()
        self.assertEqual([row["id"] for row in payload], list(server.PRICE_WATCH_INTAKE_CHAIN_IDS))
        self.assertIn("Solana", [row["id"] for row in payload])
        for row in payload:
            self.assertTrue(row["short"], row["id"])
            self.assertTrue(row["note"], row["id"])

    def test_every_chain_ships_enabled(self):
        for row in server.price_watch_chain_payload():
            self.assertTrue(row["intake"], row["id"])

    # -- 读写 -------------------------------------------------------------

    def test_toggling_solana_off_persists_and_only_touches_solana(self):
        result = server.set_price_watch_chain_toggle("Solana", False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])
        state = {row["id"]: row["intake"] for row in result["chains"]}
        self.assertFalse(state["Solana"])
        self.assertTrue(state["BSC"])
        self.assertTrue(state["ETH"])
        # 重新读一次（走 DB，不走缓存）也要是关的
        self.reset_cache()
        self.assertFalse(server.price_watch_chain_intake_enabled("Solana"))

    def test_short_name_and_raw_chain_code_are_accepted(self):
        server.set_price_watch_chain_toggle("SOL", False)
        self.assertFalse(server.price_watch_chain_intake_enabled("CT_501"))
        server.set_price_watch_chain_toggle("solana", True)
        self.assertTrue(server.price_watch_chain_intake_enabled("CT_501"))

    def test_unknown_chain_is_rejected(self):
        with self.assertRaises(ValueError):
            server.set_price_watch_chain_toggle("EthereumPoW", False)

    def test_a_broken_read_falls_back_to_enabled(self):
        with patch.object(server, "auth_db", side_effect=RuntimeError("db gone")):
            self.reset_cache()
            self.assertTrue(server.price_watch_chain_intake_enabled("Solana"))
            self.assertEqual(
                {row["id"]: row["intake"] for row in server.price_watch_chain_payload()},
                {chain: True for chain in server.PRICE_WATCH_INTAKE_CHAIN_IDS},
            )

    # -- 谓词 -------------------------------------------------------------

    def test_chain_codes_are_normalised(self):
        """CT_501 / 501 / sol / solana 都是同一条链，别漏掉任何一种写法。"""
        server.set_price_watch_chain_toggle("Solana", False)
        for raw in ("CT_501", "501", "sol", "solana", "Solana"):
            self.assertFalse(server.price_watch_chain_intake_enabled(raw), raw)

    def test_empty_chain_is_always_allowed(self):
        """没有链 = 交易所标的（二级），永远放行。"""
        server.set_price_watch_chain_toggle("Solana", False)
        self.assertTrue(server.price_watch_chain_intake_enabled(""))
        self.assertTrue(server.price_watch_chain_intake_enabled(None))

    def test_other_chains_keep_flowing(self):
        server.set_price_watch_chain_toggle("Solana", False)
        for raw in ("56", "bsc", "eth", "8453", "base", "4663", "robinhood"):
            self.assertTrue(server.price_watch_chain_intake_enabled(raw), raw)

    # -- 池行 -------------------------------------------------------------

    def test_onchain_row_on_a_closed_chain_is_blocked(self):
        server.set_price_watch_chain_toggle("Solana", False)
        row = {"symbol": "BONK", "onchain_chain": "CT_501", "onchain_contract_address": "abc"}
        self.assertTrue(server.price_watch_row_chain_blocked(row))

    def test_secondary_market_row_survives_a_closed_chain(self):
        """交易所有上新记录 = 二级标的，关掉链也不许把它从池里拿掉。"""
        server.set_price_watch_chain_toggle("Solana", False)
        row = {
            "symbol": "SOMESOL",
            "onchain_chain": "CT_501",
            "onchain_contract_address": "abc",
            "new_contract_listed_at": 1_790_000_000_000,
        }
        self.assertFalse(server.price_watch_row_chain_blocked(row))

    def test_exchange_only_row_is_never_blocked(self):
        server.set_price_watch_chain_toggle("Solana", False)
        self.assertFalse(server.price_watch_row_chain_blocked({"symbol": "BTCUSDT"}))
        self.assertFalse(server.price_watch_row_chain_blocked(None))

    # -- 入池闸门 ---------------------------------------------------------

    def test_gmgn_hot_search_stops_admitting_closed_chains(self):
        row = {"symbol": "BONK", "chain": "sol", "contractAddress": "0xbonk", "name": "Bonk"}
        server.set_price_watch_chain_toggle("Solana", False)
        self.assertEqual(server.sync_price_watch_gmgn_hot_search_candidates([row]), 0)
        server.set_price_watch_chain_toggle("Solana", True)
        self.assertEqual(server.sync_price_watch_gmgn_hot_search_candidates([row]), 1)

    def test_binance_wallet_stops_admitting_closed_chains(self):
        now_ms = int(__import__("time").time() * 1000)
        row = {
            "symbol": "WIF",
            "name": "dogwifhat",
            "chain": "CT_501",
            "chainLabel": "Solana",
            "contractAddress": "abc",
            "firstSeenAt": now_ms - 60_000,
            "lastSeenAt": now_ms,
        }
        server.set_price_watch_chain_toggle("Solana", False)
        self.assertEqual(server.sync_price_watch_binance_wallet_candidates([row], now_ms=now_ms), 0)
        server.set_price_watch_chain_toggle("Solana", True)
        self.assertEqual(server.sync_price_watch_binance_wallet_candidates([row], now_ms=now_ms), 1)


if __name__ == "__main__":
    unittest.main()
