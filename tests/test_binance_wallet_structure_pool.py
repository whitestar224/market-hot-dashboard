import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class BinanceWalletStructurePoolTests(unittest.TestCase):
    def wallet_source(self, rows):
        return {
            "id": "binance-wallet-hot",
            "period": "4h",
            "status": "ok",
            "rows": rows,
        }

    def wallet_row(self, symbol="PONS", contract="0x1234", rank=1):
        return {
            "rank": rank,
            "symbol": symbol,
            "name": symbol,
            "icon": "https://example.com/icon.png",
            "chain": "56",
            "chainLabel": "BSC",
            "contractAddress": contract,
            "amount": 12_500_000,
            "heat": 100,
            "liquidity": 800_000,
            "url": "https://web3.binance.com/en/markets",
        }

    def test_live_4h_appearances_are_persisted_for_thirty_days(self):
        first_at = 1_800_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wallet-4h.json"
            with patch.object(server, "BINANCE_WALLET_4H_STRUCTURE_PATH", path), patch.object(
                server, "BINANCE_WALLET_4H_STRUCTURE_ACTIVE", True
            ):
                server.record_binance_wallet_4h_structure_source(
                    self.wallet_source([self.wallet_row("PONS", "0x1234", 3)]),
                    now_ms=first_at,
                )
                server.record_binance_wallet_4h_structure_source(
                    self.wallet_source([self.wallet_row("PONS", "0x1234", 1)]),
                    now_ms=first_at + 6 * 60 * 60 * 1000,
                )
                rows = server.binance_wallet_4h_structure_rows(now_ms=first_at + 7 * 60 * 60 * 1000)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "PONS")
        self.assertEqual(rows[0]["walletHotRank"], 1)
        self.assertEqual(rows[0]["firstSeenAt"], first_at)
        self.assertEqual(rows[0]["lastSeenAt"], first_at + 6 * 60 * 60 * 1000)
        self.assertEqual(rows[0]["expiresAt"] - rows[0]["lastSeenAt"], 30 * 24 * 60 * 60 * 1000)

    def test_wallet_history_joins_existing_structure_pool_with_contract_identity(self):
        wallet_row = {
            **self.wallet_row("我的女友景甜", "0xff7777", 2),
            "firstSeenAt": 1_800_000_000_000,
            "lastSeenAt": 1_800_000_100_000,
            "walletHotRank": 2,
            "walletHeat": 90,
            "wallet4hVolumeUsd": 20_000_000,
        }
        now_ms = 1_800_000_200_000
        with (
            patch.object(server.time, "time", return_value=now_ms / 1000),
            patch.object(server, "price_watch_aicoin_source", return_value={"status": "ok", "rows": []}),
            patch.object(server, "price_watch_active_rows", return_value=[]),
            patch.object(server, "filter_price_monitor_rows_by_activity", return_value=[]),
            patch.object(server, "reconcile_price_structure_exclusions", return_value=set()),
            patch.object(server, "strategy_active_adaptive_contexts", return_value=[]),
            patch.object(server, "price_structure_priority_context", return_value={}),
            patch.object(server, "binance_wallet_4h_structure_rows", return_value=[wallet_row]),
        ):
            rows = server.price_structure_watch_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "我的女友景甜")
        self.assertEqual(rows[0]["contractAddress"], "0xff7777")
        self.assertEqual(rows[0]["chain"], "56")
        self.assertIn("币安钱包4H", rows[0]["structureMembershipSources"])
        self.assertEqual(rows[0]["monitorPool"], "aicoin-x-wallet")


if __name__ == "__main__":
    unittest.main()
