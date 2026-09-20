import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class AveHotMonitorTests(unittest.TestCase):
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
    def source(*symbols):
        return {
            "id": "ave",
            "title": "Ave.ai 热搜榜",
            "sourceLabel": "AVE",
            "group": "crypto",
            "status": "ok",
            "rows": [
                {
                    "rank": index,
                    "symbol": symbol,
                    "name": symbol,
                    "chain": "bsc",
                    "chainLabel": "BNB Chain",
                    "contractAddress": f"0x{index:040x}",
                    "url": f"https://ave.ai/token/{symbol.lower()}-bsc",
                    "amount": 100_000 * index,
                    "change": "+12%",
                }
                for index, symbol in enumerate(symbols, start=1)
            ],
        }

    def test_ave_hot_member_stays_on_board_and_does_not_enter_any_monitor_pool(self):
        now_ms = 1_800_000_000_000
        source = self.source("BREW")

        with patch.object(server.time, "time", return_value=now_ms / 1000):
            synced = server.sync_price_watch_ave_candidates(source, now_ms=now_ms)
            rows = server.price_watch_active_rows()

        self.assertEqual(synced, 0)
        self.assertEqual(rows, [])

    def test_ave_membership_does_not_override_shared_monitor_activity_gate(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "BREW",
            "ave_hot_last_seen_at": now_ms,
        }
        inactive = {
            "BREW": {
                "active": False,
                "status": "inactive",
                "reason": "below-turnover-threshold",
            }
        }

        with patch.object(server.time, "time", return_value=now_ms / 1000), \
             patch.object(server, "fetch_new_coin_low_market_activity", return_value=inactive), \
             patch.object(server, "PRICE_MONITOR_ACTIVITY_STATES", {}):
            filtered = server.filter_price_monitor_rows_by_activity([row])

        self.assertEqual(filtered, [])

    def test_existing_ave_only_rows_are_removed_but_other_memberships_are_kept(self):
        now_ms = 1_800_000_000_000
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets(
                    symbol, name, ave_hot_first_seen_at, ave_hot_last_seen_at,
                    ave_hot_rank, created_at, updated_at
                ) VALUES('AVEONLY', 'Ave only', ?, ?, 1, ?, ?)
                """,
                (now_ms, now_ms, now_ms, now_ms),
            )
            conn.execute(
                """
                INSERT INTO price_watch_assets(
                    symbol, name, manual_pinned, ave_hot_first_seen_at,
                    ave_hot_last_seen_at, ave_hot_rank, created_at, updated_at
                ) VALUES('KEPT', 'Kept', 1, ?, ?, 2, ?, ?)
                """,
                (now_ms, now_ms, now_ms, now_ms),
            )

        removed = server.sync_price_watch_ave_candidates(now_ms=now_ms)

        with server.auth_db() as conn:
            ave_only = conn.execute(
                "SELECT 1 FROM price_watch_assets WHERE symbol='AVEONLY'"
            ).fetchone()
            kept = conn.execute(
                """
                SELECT manual_pinned, ave_hot_first_seen_at,
                       ave_hot_last_seen_at, ave_hot_rank
                FROM price_watch_assets WHERE symbol='KEPT'
                """
            ).fetchone()
        self.assertEqual(removed, 1)
        self.assertIsNone(ave_only)
        self.assertEqual(dict(kept), {
            "manual_pinned": 1,
            "ave_hot_first_seen_at": 0,
            "ave_hot_last_seen_at": 0,
            "ave_hot_rank": 0,
        })

    def test_ave_hot_new_entries_update_membership_without_popup_or_speech(self):
        now_ms = 1_800_000_000_000
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server, "AVE_HOT_ALERT_STATE_PATH", Path(temp_dir) / "ave-hot.json"
        ) as state_path, patch.object(server, "launch_desktop_alert") as launch, patch.object(
            server.time, "time", return_value=now_ms / 1000
        ):
            baseline = server.sync_ave_hot_alert_feed(self.source("PONS"))
            new_events = server.sync_ave_hot_alert_feed(self.source("PONS", "BREW"))
            repeated = server.sync_ave_hot_alert_feed(self.source("PONS", "BREW"))
            state = server.read_json_cache(state_path)

        self.assertEqual(baseline, [])
        self.assertEqual(new_events, [])
        self.assertEqual(repeated, [])
        self.assertTrue(state["ready"])
        self.assertEqual(len(state["membership"]), 2)
        launch.assert_not_called()

    def test_ave_parser_preserves_case_sensitive_solana_mint(self):
        mint = "7YttLkHDoYzVnqjdJxnXGbUk8S7eRCJkFfPPnYkZpump"
        parsed = server.ave_asset_fields({
            "target_token": mint,
            "chain": "solana",
            "token0_address": mint,
            "token0_symbol": "PUP",
            "token0_name": "Pup",
        })

        self.assertEqual(parsed["target"], mint)
        self.assertEqual(parsed["symbol"], "PUP")

    def test_ave_rows_expose_1h_4h_24h_metrics_and_default_to_4h(self):
        rows = server.ave_hot_rows_from_items([{
            "target_token": "0x" + "a" * 40,
            "chain": "bsc",
            "token0_address": "0x" + "a" * 40,
            "token0_symbol": "FOUR",
            "token0_name": "Four",
            "current_price_usd": 0.42,
            "market_cap": 2_000_000,
            "tvl": 80_000,
            "price_change_1h": 1.5,
            "price_change_4h": 6.25,
            "price_change_24h": -3.0,
            "volume_u_1h": 10_000,
            "volume_u_4h": 40_000,
            "volume_u_24h": 90_000,
            "tx_1h_count": 20,
            "tx_4h_count": 80,
            "tx_24h_count": 150,
        }], max_rows=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["change"], "+6.25%")
        self.assertEqual(rows[0]["period"], "4h")
        self.assertEqual(set(rows[0]["periodMetrics"]), {"1h", "4h", "24h"})
        self.assertEqual(rows[0]["periodMetrics"]["1h"]["transactions"], 20)
        self.assertEqual(rows[0]["periodMetrics"]["24h"]["amount"], 90_000)

    def test_ave_fetches_total_and_five_requested_chain_boards_with_ten_rows_each(self):
        requested = []

        def fake_get(_url, *, params, headers, timeout):
            chain = params["chain"]
            requested.append(chain)
            prefix = chain or "all"
            items = []
            for index in range(12):
                contract = f"0x{index + 1:040x}"
                items.append({
                    "target_token": contract,
                    "chain": chain or "bsc",
                    "token0_address": contract,
                    "token0_symbol": f"{prefix[:3].upper()}{index}",
                    "token0_name": f"{prefix} {index}",
                    "current_price_usd": 1,
                    "price_change_4h": index,
                    "volume_u_4h": 1000 + index,
                    "tx_4h_count": 20 + index,
                })
            response = Mock(status_code=200)
            response.json.return_value = {"data": items}
            response.text = ""
            return response

        with patch.object(server, "ave_auth_values", return_value=("x" * 80, "device")), patch.object(
            server.requests, "get", side_effect=fake_get
        ):
            source = server.fetch_ave_hot()

        self.assertEqual(set(requested), {"", "robinhood", "solana", "eth", "bsc", "base"})
        self.assertEqual(len(source["rows"]), 10)
        self.assertEqual([board["chain"] for board in source["chainBoards"]], [
            "robinhood", "solana", "eth", "bsc", "base",
        ])
        self.assertTrue(all(len(board["rows"]) == 10 for board in source["chainBoards"]))
        self.assertEqual(source["period"], "4h")
        self.assertEqual([item["value"] for item in source["periodOptions"]], ["1h", "4h", "24h"])

    def test_ave_chain_boards_feed_research_without_duplicate_contracts(self):
        source = self.source("TOTAL")
        duplicate = dict(source["rows"][0])
        source["chainBoards"] = [
            {"chain": "bsc", "rows": [duplicate]},
            {"chain": "base", "rows": [{
                **duplicate,
                "symbol": "BASEHOT",
                "name": "Base Hot",
                "chain": "base",
                "chainLabel": "BASE",
                "contractAddress": "0x" + "b" * 40,
                "periodMetrics": {"4h": {"amount": 50_000, "transactions": 30, "changeValue": 9}},
            }]},
        ]

        flattened = server.ave_hot_all_rows(source)
        research = server.ave_hot_research_rows(source, observed_at=1_800_000_000_000)

        self.assertEqual(len(flattened), 2)
        self.assertEqual({row["network"] for row in research}, {"bsc", "base"})
        base = next(row for row in research if row["network"] == "base")
        self.assertEqual(base["metrics"]["volumeH1Usd"], 50_000)
        self.assertEqual(base["metrics"]["transactionsH1"], 30)


if __name__ == "__main__":
    unittest.main()
