"""GMGN 5m hot-search entries are a short probation, not 30-day membership.

A token that surfaces on the GMGN 5-minute hot-search board joins the monitor
pool immediately.  It only keeps that slot for
``GMGN_HOT_SEARCH_POOL_RETENTION_SECONDS`` measured from its *latest* GMGN
appearance, and graduates to the normal 30-day lifecycle only by showing up on a
stronger feed (the Binance Wallet 4h hot ranking).  These tests pin that gate,
plus the origin label, which used to fall through to "aicoin" once the old 6h
grace expired and made GMGN entries render as "AICoin 新进".
"""

import gc
import tempfile
import time
import unittest
from pathlib import Path

import server

DAY_MS = 24 * 60 * 60 * 1000


class GmgnHotSearchRetentionTests(unittest.TestCase):
    def setUp(self):
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.runtime_dir.name) / "auth.db"
        server.init_auth_db()

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.runtime_dir.cleanup()

    def insert_asset(
        self,
        symbol,
        *,
        gmgn_last_seen_at,
        wallet_last_seen_at=0,
        aicoin_last_seen_at=0,
        manual_pinned=0,
        dismissed_until=0,
    ):
        now_ms = int(time.time() * 1000)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned,
                    gmgn_hot_search_first_seen_at, gmgn_hot_search_last_seen_at,
                    gmgn_hot_search_rank, binance_wallet_hot_last_seen_at,
                    aicoin_last_seen_at, dismissed_until,
                    onchain_chain, onchain_contract_address, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 5, ?, ?, ?, 'sol', ?, ?, ?)
                """,
                (
                    symbol, symbol, manual_pinned,
                    gmgn_last_seen_at, gmgn_last_seen_at,
                    wallet_last_seen_at, aicoin_last_seen_at, dismissed_until,
                    f"CA{symbol}", now_ms, now_ms,
                ),
            )
        return now_ms

    def active_symbols(self):
        rows = server.price_watch_active_rows(
            bypass_process_lock=True, persist_retention_transitions=False
        )
        return {row["symbol"] for row in rows}

    def active_row(self, symbol):
        rows = server.price_watch_active_rows(
            bypass_process_lock=True, persist_retention_transitions=False
        )
        for row in rows:
            if row["symbol"] == symbol:
                return row
        self.fail(f"{symbol} is not in the monitor pool")

    def test_window_is_a_three_day_probation(self):
        self.assertEqual(server.GMGN_HOT_SEARCH_POOL_RETENTION_SECONDS, 3 * 24 * 60 * 60)

    def test_gmgn_only_row_leaves_the_pool_once_the_window_expires(self):
        now_ms = int(time.time() * 1000)
        self.insert_asset("GMGNONLY", gmgn_last_seen_at=now_ms - 4 * DAY_MS)
        self.assertNotIn("GMGNONLY", self.active_symbols())

    def test_window_boundary_is_enforced(self):
        now_ms = int(time.time() * 1000)
        self.insert_asset("INSIDE", gmgn_last_seen_at=now_ms - int(2.9 * DAY_MS))
        self.insert_asset("OUTSIDE", gmgn_last_seen_at=now_ms - int(3.4 * DAY_MS))
        active = self.active_symbols()
        self.assertIn("INSIDE", active)
        self.assertNotIn("OUTSIDE", active)

    def test_clock_follows_the_latest_board_appearance(self):
        """Re-entering the board refreshes the slot, so the row survives."""
        now_ms = int(time.time() * 1000)
        self.insert_asset("RECHARTED", gmgn_last_seen_at=now_ms - 5 * DAY_MS)
        self.assertNotIn("RECHARTED", self.active_symbols())

        # The token shows up on the 5-minute board again; ingest only moves the
        # last-seen stamp forward (see sync_price_watch_gmgn_hot_search_candidates).
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET gmgn_hot_search_last_seen_at = ? WHERE symbol = ?",
                (now_ms, "RECHARTED"),
            )
        self.assertIn("RECHARTED", self.active_symbols())

    def test_binance_wallet_graduation_outlives_the_gmgn_window(self):
        now_ms = int(time.time() * 1000)
        self.insert_asset(
            "GRADUATED",
            gmgn_last_seen_at=now_ms - 5 * DAY_MS,
            wallet_last_seen_at=now_ms - DAY_MS,
        )
        self.assertIn("GRADUATED", self.active_symbols())

    def test_other_feeds_and_manual_pins_are_untouched(self):
        now_ms = int(time.time() * 1000)
        self.insert_asset(
            "AICOINTOOKOVER",
            gmgn_last_seen_at=now_ms - 5 * DAY_MS,
            aicoin_last_seen_at=now_ms - 60 * 60 * 1000,
        )
        self.insert_asset("PINNED", gmgn_last_seen_at=now_ms - 10 * DAY_MS, manual_pinned=1)
        self.insert_asset(
            "DISMISSED",
            gmgn_last_seen_at=now_ms - DAY_MS,
            dismissed_until=now_ms + DAY_MS,
        )
        active = self.active_symbols()
        self.assertIn("AICOINTOOKOVER", active)
        self.assertIn("PINNED", active)
        self.assertNotIn("DISMISSED", active)

    def test_gmgn_entries_keep_their_own_origin_label(self):
        """A GMGN-only row used to fall through to the "aicoin" fallback label."""
        now_ms = int(time.time() * 1000)
        self.insert_asset("LABELLED", gmgn_last_seen_at=now_ms - 2 * DAY_MS)
        item = server.price_watch_public_item(self.active_row("LABELLED"))
        self.assertEqual(item["origin"], "gmgn-hot-search")
        self.assertTrue(item["gmgnHotSearch"])
        self.assertTrue(item["priorHighEnabled"])


if __name__ == "__main__":
    unittest.main()
