import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class BinanceWalletPriorHighTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = self.db_path
        server.init_auth_db()

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.db_path.unlink(missing_ok=True)

    @staticmethod
    def wallet_row(symbol, contract, now_ms, rank=1):
        return {
            "symbol": symbol,
            "name": symbol,
            "icon": "https://example.com/token.png",
            "chain": "56",
            "chainLabel": "BNB Chain",
            "contractAddress": contract,
            "walletHotRank": rank,
            "firstSeenAt": now_ms - 60_000,
            "lastSeenAt": now_ms,
        }

    def test_wallet_history_enters_prior_high_with_contract_identity(self):
        now_ms = 1_800_000_000_000
        rows = [
            self.wallet_row("PONS", "0x1234", now_ms, 2),
            self.wallet_row("我的女友景甜", "0x5678", now_ms, 3),
        ]

        with patch.object(server.time, "time", return_value=now_ms / 1000):
            synced = server.sync_price_watch_binance_wallet_candidates(rows, now_ms=now_ms)
            active = server.price_watch_active_rows()
            public = {row["symbol"]: server.price_watch_public_item(row) for row in active}

        self.assertEqual(synced, 2)
        self.assertEqual(set(public), {"PONS", "我的女友景甜"})
        self.assertTrue(public["PONS"]["priorHighEnabled"])
        self.assertEqual(public["PONS"]["origin"], "binance-wallet")
        self.assertEqual(public["PONS"]["binanceWalletHotRank"], 2)
        self.assertEqual(public["PONS"]["chain"], "56")
        self.assertEqual(public["PONS"]["contractAddress"], "0x1234")
        self.assertEqual(
            public["PONS"]["tradeUrl"],
            "https://web3.binance.com/en/token/bsc/0x1234?ref=MQ6JD2X4",
        )
        self.assertTrue(public["我的女友景甜"]["priorHighEnabled"])
        with server.auth_db() as conn:
            chinese = conn.execute(
                """
                SELECT onchain_chain, onchain_chain_label, onchain_contract_address
                FROM price_watch_assets WHERE symbol = ?
                """,
                ("我的女友景甜",),
            ).fetchone()
        self.assertEqual(chinese["onchain_chain"], "56")
        self.assertEqual(chinese["onchain_chain_label"], "BNB Chain")
        self.assertEqual(chinese["onchain_contract_address"], "0x5678")

    def test_wallet_refresh_does_not_revive_globally_excluded_unicode_symbol(self):
        now_ms = 1_800_000_000_000
        row = self.wallet_row("我的女友景甜", "0x5678", now_ms)
        server.sync_price_watch_binance_wallet_candidates([row], now_ms=now_ms)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_structure_exclusions (
                    symbol, excluded_at, absent_at, absent_confirmations,
                    last_absent_at, updated_at
                ) VALUES (?, ?, 0, 0, 0, ?)
                """,
                ("我的女友景甜", now_ms, now_ms),
            )

        server.sync_price_watch_binance_wallet_candidates(
            [{**row, "lastSeenAt": now_ms + 60_000}],
            now_ms=now_ms + 60_000,
        )

        with patch.object(server.time, "time", return_value=(now_ms + 60_000) / 1000):
            active_symbols = {item["symbol"] for item in server.price_watch_active_rows()}
        self.assertNotIn("我的女友景甜", active_symbols)
        self.assertTrue(server.price_structure_symbol_excluded("我的女友景甜"))

    def test_wallet_hot_membership_is_not_removed_by_generic_turnover_gate(self):
        now_ms = 1_800_000_000_000
        row = {
            **self.wallet_row("BEN", "0x9999", now_ms),
            "binance_wallet_hot_last_seen_at": now_ms,
            "onchain_contract_address": "0x9999",
            "onchain_chain": "56",
        }
        inactive = {
            "active": False,
            "status": "inactive",
            "reason": "turnover-below-threshold",
            "turnover24hUsd": 100,
            "source": "test",
            "thresholdUsd": 10_000_000,
        }

        with (
            patch.object(server.time, "time", return_value=now_ms / 1000),
            patch.object(server, "fetch_new_coin_low_market_activity", return_value={}),
            patch.object(server, "price_structure_onchain_activity_state", return_value=inactive),
        ):
            filtered = server.filter_price_monitor_rows_by_activity([row])

        self.assertEqual([item["symbol"] for item in filtered], ["BEN"])
        self.assertEqual(
            filtered[0]["marketActivity"]["reason"],
            "binance-wallet-4h-hot-membership",
        )


if __name__ == "__main__":
    unittest.main()
