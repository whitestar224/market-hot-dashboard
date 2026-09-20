import json
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

    def test_persisted_history_hydrates_before_live_source_sync(self):
        observed_at = 1_800_000_000_000
        saved_row = {
            **self.wallet_row("FABLE", "0xfable", 6),
            "firstSeenAt": observed_at,
            "lastSeenAt": observed_at,
            "expiresAt": observed_at + 30 * 24 * 60 * 60 * 1000,
            "walletHotRank": 6,
            "walletHeat": 50,
            "wallet4hVolumeUsd": 12_000_000,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wallet-4h.json"
            path.write_text(
                json.dumps({"updatedAt": observed_at, "items": [saved_row]}),
                encoding="utf-8",
            )
            with patch.object(server, "BINANCE_WALLET_4H_STRUCTURE_PATH", path), patch.object(
                server, "BINANCE_WALLET_4H_STRUCTURE_ACTIVE", False
            ):
                rows = server.binance_wallet_4h_structure_rows(now_ms=observed_at + 60_000)

        self.assertEqual([row["symbol"] for row in rows], ["FABLE"])

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
            patch.object(server, "filter_price_monitor_rows_by_activity", side_effect=lambda rows: rows),
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

    def test_same_symbol_uses_latest_ranked_wallet_contract_instead_of_list_order(self):
        rows = [
            {
                **self.wallet_row("SAME", "0xnew", 2),
                "lastSeenAt": 1_800_000_200_000,
                "walletHotRank": 2,
                "walletHeat": 92,
            },
            {
                **self.wallet_row("SAME", "0xold", 1),
                "lastSeenAt": 1_800_000_100_000,
                "walletHotRank": 1,
                "walletHeat": 100,
            },
        ]

        selected = server.price_structure_wallet_rows_by_symbol(rows)

        self.assertEqual(selected["SAME"]["contractAddress"], "0xnew")

    def test_wallet_contract_has_priority_over_a_conflicting_personal_x_contract(self):
        wallet = {
            **self.wallet_row("SAME", "0xwallet", 1),
            "lastSeenAt": 1_800_000_200_000,
            "walletHotRank": 1,
        }
        stored = {
            "symbol": "SAME",
            "personal_x_mentioned_at": 1_800_000_190_000,
            "personal_x_source_text": "$SAME BSC 合约地址 0x1111111111111111111111111111111111111111",
            "onchain_chain": "56",
            "onchain_contract_address": "0x1111111111111111111111111111111111111111",
        }

        identity = server.price_structure_resolved_onchain_identity(
            stored,
            wallet_row=wallet,
            market_activity={},
        )

        self.assertEqual(identity["contractAddress"], "0xwallet")
        self.assertEqual(identity["source"], "binance-wallet-hot")


if __name__ == "__main__":
    unittest.main()
