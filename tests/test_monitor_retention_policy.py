import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class MonitorRetentionPolicyTests(unittest.TestCase):
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

    def insert_asset(self, symbol, contract, now_ms, *, source_at=None, chain="56"):
        observed_at = int(source_at if source_at is not None else now_ms)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, icon, pair_hint,
                    aicoin_first_seen_at, aicoin_last_seen_at,
                    onchain_chain, onchain_contract_address,
                    created_at, updated_at
                ) VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, symbol, observed_at, observed_at,
                    chain, contract, now_ms, now_ms,
                ),
            )

    def test_contract_identity_does_not_archive_same_ticker_other_contract(self):
        now_ms = 1_800_000_000_000
        self.insert_asset("SAME", "0xABC", now_ms)
        result = server.cold_archive_price_monitor_symbols(["SAME"], now_ms=now_ms)

        rows = [
            {"symbol": "SAME", "chain": "bsc", "contractAddress": "0xabc"},
            {"symbol": "SAME", "chain": "bsc", "contractAddress": "0xdef"},
        ]
        with patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None):
            filtered = server.filter_price_monitor_cold_archives(rows, now_ms=now_ms + 1)

        self.assertEqual(result["archived"][0]["identityKey"], "contract:bsc:0xabc")
        self.assertEqual([row["contractAddress"] for row in filtered], ["0xdef"])

    def test_solana_contract_identity_preserves_case(self):
        upper = server.price_monitor_retention_identity({
            "symbol": "SOLX", "chain": "solana", "contractAddress": "AbCd",
        })
        lower = server.price_monitor_retention_identity({
            "symbol": "SOLX", "chain": "solana", "contractAddress": "abcd",
        })
        self.assertNotEqual(upper["key"], lower["key"])

    def test_new_source_after_archive_automatically_reenters(self):
        archived_at = 1_800_000_000_000
        self.insert_asset("RETURN", "0x123", archived_at, source_at=archived_at - 1)
        server.cold_archive_price_monitor_symbols(["RETURN"], now_ms=archived_at)
        row = {
            "symbol": "RETURN",
            "chain": "56",
            "contractAddress": "0x123",
            "personalXMentionedAt": archived_at + 1,
        }

        with patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None):
            filtered = server.filter_price_monitor_cold_archives([row], now_ms=archived_at + 2)

        self.assertEqual(filtered, [row])
        with server.auth_db() as conn:
            state = conn.execute(
                "SELECT archived_at, failure_count FROM price_monitor_retention_state"
            ).fetchone()
        self.assertEqual(state["archived_at"], 0)
        self.assertEqual(state["failure_count"], 0)

    def test_ordinary_retirement_needs_two_failures_a_day_apart(self):
        now_ms = 1_800_000_000_000
        source_at = now_ms - 4 * 24 * 60 * 60 * 1000
        row = {
            "symbol": "QUIET",
            "aicoin_last_seen_at": source_at,
            "provider": "Binance Futures",
            "current_price": 1,
            "marketActivity": {
                "active": False,
                "status": "inactive",
                "reason": "turnover-below-threshold",
                "turnover24hUsd": 100,
            },
        }
        day_ms = server.PRICE_MONITOR_RETENTION_CONFIRM_INTERVAL_SECONDS * 1000
        with (
            patch.object(server, "price_monitor_retention_snapshot_index", return_value={}),
            patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None),
        ):
            server.record_price_monitor_retention_failures([row], now_ms=now_ms)
            server.record_price_monitor_retention_failures([row], now_ms=now_ms + 60 * 60 * 1000)
            with server.auth_db() as conn:
                interim = conn.execute(
                    "SELECT failure_count, archived_at FROM price_monitor_retention_state"
                ).fetchone()
            server.record_price_monitor_retention_failures([row], now_ms=now_ms + day_ms + 1)

        self.assertEqual(interim["failure_count"], 1)
        self.assertEqual(interim["archived_at"], 0)
        with server.auth_db() as conn:
            final = conn.execute(
                "SELECT failure_reason, failure_count, archived_at FROM price_monitor_retention_state"
            ).fetchone()
        self.assertEqual(final["failure_reason"], "turnover-below-threshold")
        self.assertEqual(final["failure_count"], 2)
        self.assertEqual(final["archived_at"], now_ms + day_ms + 1)

    def test_low_turnover_onchain_asset_is_not_rejected_by_the_cex_gate(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "THIN",
            "chain": "56",
            "contractAddress": "0x4567",
            "aicoin_last_seen_at": now_ms - 2 * 24 * 60 * 60 * 1000,
            "provider": "DexScreener",
            "current_price": 1,
            "marketActivity": {
                "active": False,
                "status": "inactive",
                "turnover24hUsd": 100,
            },
        }

        with patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None):
            failure = server.price_monitor_retention_failure(
                row, {"signalCount": 1}, now_ms=now_ms
            )

        self.assertNotEqual(failure["reason"], "turnover-below-threshold")

    def test_low_turnover_secondary_contract_still_uses_the_hard_gate(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "THINPERP",
            "aicoin_last_seen_at": now_ms - 2 * 24 * 60 * 60 * 1000,
            "provider": "Binance Futures",
            "current_price": 1,
            "marketActivity": {
                "active": False,
                "status": "inactive",
                "turnover24hUsd": 9_999_999,
            },
        }

        with patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None):
            failure = server.price_monitor_retention_failure(row, now_ms=now_ms)

        self.assertEqual(failure["reason"], "turnover-below-threshold")

    def test_technical_edge_only_extends_an_expired_fast_source_for_one_day(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "EDGE",
            "chain": "56",
            "contractAddress": "0x4568",
            "ave_hot_last_seen_at": now_ms - 3 * 60 * 60 * 1000,
            "provider": "DexScreener",
            "current_price": 1,
            "marketActivity": {"active": True, "status": "unavailable"},
        }

        with patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None):
            protected = server.price_monitor_retention_failure(
                row, {"signalCount": 1}, now_ms=now_ms
            )
            expired = server.price_monitor_retention_failure(
                row,
                {"signalCount": 1},
                now_ms=now_ms + server.PRICE_MONITOR_TECHNICAL_EDGE_GRACE_SECONDS * 1000,
            )

        self.assertEqual(protected["reason"], "")
        self.assertEqual(expired["reason"], "ave-expired-no-edge")

    def test_daily_ordinary_retirement_is_capped_at_fifteen_percent(self):
        now_ms = 1_800_000_000_000
        rows = [
            {
                "symbol": f"LOW{index}",
                "aicoin_last_seen_at": now_ms - 4 * 24 * 60 * 60 * 1000,
                "provider": "Binance Futures",
                "current_price": 1,
                "marketActivity": {
                    "active": False,
                    "status": "inactive",
                    "turnover24hUsd": index + 1,
                },
            }
            for index in range(10)
        ]
        day_ms = server.PRICE_MONITOR_RETENTION_CONFIRM_INTERVAL_SECONDS * 1000
        with (
            patch.object(server, "price_monitor_retention_snapshot_index", return_value={}),
            patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None),
        ):
            server.record_price_monitor_retention_failures(rows, now_ms=now_ms)
            result = server.record_price_monitor_retention_failures(
                rows, now_ms=now_ms + day_ms + 1
            )

        self.assertEqual(len(result["archived"]), 2)
        with server.auth_db() as conn:
            states = conn.execute(
                "SELECT failure_count, archived_at FROM price_monitor_retention_state"
            ).fetchall()
        self.assertTrue(all(int(state["failure_count"]) >= 2 for state in states))
        self.assertEqual(sum(bool(state["archived_at"]) for state in states), 2)

    def test_ave_board_churn_uses_short_grace_and_leaves_realtime_quotes(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "CHURN",
            "chain": "56",
            "contractAddress": "0xcafe",
            "ave_hot_last_seen_at": now_ms - 3 * 60 * 60 * 1000,
            "provider": "DexScreener",
            "current_price": 1,
            "marketActivity": {
                "active": True,
                "status": "active",
                "turnover24hUsd": 20_000_000,
            },
        }
        current = {
            **row,
            "ave_hot_last_seen_at": now_ms - 60 * 60 * 1000,
        }

        with patch.object(server, "price_monitor_retention_snapshot_index", return_value={}), \
             patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None):
            failure = server.price_monitor_retention_failure(row, now_ms=now_ms)
            filtered = server.filter_price_monitor_high_frequency_rows([row, current], now_ms=now_ms)

        self.assertEqual(failure["reason"], "ave-expired-no-edge")
        self.assertEqual([item["ave_hot_last_seen_at"] for item in filtered], [current["ave_hot_last_seen_at"]])

    def test_expired_ranking_source_confirms_twice_six_hours_apart(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "FAST",
            "chain": "56",
            "contractAddress": "0xbeef",
            "ave_hot_last_seen_at": now_ms - 3 * 60 * 60 * 1000,
            "provider": "DexScreener",
            "current_price": 1,
            "marketActivity": {
                "active": True,
                "status": "active",
                "turnover24hUsd": 20_000_000,
            },
        }
        interval_ms = server.PRICE_MONITOR_FAST_RETENTION_CONFIRM_INTERVAL_SECONDS * 1000
        with patch.object(server, "price_monitor_retention_snapshot_index", return_value={}), \
             patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None):
            server.record_price_monitor_retention_failures([row], now_ms=now_ms)
            server.record_price_monitor_retention_failures([row], now_ms=now_ms + interval_ms - 1)
            with server.auth_db() as conn:
                interim = conn.execute(
                    "SELECT failure_count, archived_at FROM price_monitor_retention_state"
                ).fetchone()
            server.record_price_monitor_retention_failures([row], now_ms=now_ms + interval_ms + 1)

        self.assertEqual(dict(interim), {"failure_count": 1, "archived_at": 0})
        with server.auth_db() as conn:
            final = conn.execute(
                "SELECT failure_count, archived_at FROM price_monitor_retention_state"
            ).fetchone()
        self.assertEqual(final["failure_count"], 2)
        self.assertEqual(final["archived_at"], now_ms + interval_ms + 1)

    def test_unresolvable_identity_and_market_archives_immediately(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "BROKEN",
            "aicoin_last_seen_at": now_ms - 4 * 24 * 60 * 60 * 1000,
            "provider": "",
            "current_price": 0,
            "marketActivity": {"active": True, "status": "unavailable"},
        }
        with (
            patch.object(server, "price_monitor_retention_snapshot_index", return_value={}),
            patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None),
        ):
            result = server.record_price_monitor_retention_failures([row], now_ms=now_ms)

        self.assertEqual(result["archived"], ["BROKEN"])
        with server.auth_db() as conn:
            state = conn.execute(
                "SELECT failure_reason, failure_count, archived_at FROM price_monitor_retention_state"
            ).fetchone()
        self.assertEqual(state["failure_reason"], "identity-or-market-unavailable")
        self.assertEqual(state["failure_count"], 1)
        self.assertEqual(state["archived_at"], now_ms)

    def test_fresh_wallet_keeps_provenance_while_stale_ca_is_not_turnover_filtered(self):
        now_ms = 1_800_000_000_000
        inactive = {
            "active": False,
            "status": "inactive",
            "reason": "turnover-below-threshold",
            "turnover24hUsd": 100,
            "source": "test",
            "thresholdUsd": server.NEW_COIN_LOW_MIN_TURNOVER_24H_USD,
        }
        base = {
            "symbol": "WALLET",
            "chain": "56",
            "contractAddress": "0x789",
            "onchain_chain": "56",
            "onchain_contract_address": "0x789",
        }
        fresh = {
            **base,
            "binance_wallet_hot_last_seen_at": now_ms,
            "walletHotRank": 1,
        }
        stale = {
            **base,
            "binance_wallet_hot_last_seen_at": now_ms - 4 * 24 * 60 * 60 * 1000,
            "walletHotRank": 1,
        }
        with (
            patch.object(server.time, "time", return_value=now_ms / 1000),
            patch.object(server, "fetch_new_coin_low_market_activity", return_value={}),
            patch.object(server, "price_structure_onchain_activity_state", return_value=inactive),
            patch.object(server, "PRICE_MONITOR_ACTIVITY_STATES", {}),
        ):
            fresh_result = server.filter_price_monitor_rows_by_activity([fresh])
            stale_result = server.filter_price_monitor_rows_by_activity([stale])

        self.assertEqual([row["symbol"] for row in fresh_result], ["WALLET"])
        self.assertEqual(fresh_result[0]["marketActivity"]["reason"], "binance-wallet-4h-hot-membership")
        self.assertEqual([row["symbol"] for row in stale_result], ["WALLET"])
        self.assertEqual(
            stale_result[0]["marketActivity"]["reason"],
            "onchain-ca-resolved-no-hard-turnover-gate",
        )

    def test_recent_last_known_activity_is_used_when_sources_are_unavailable(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "FALLBACK",
            "chain": "56",
            "contractAddress": "0xf00",
            "onchain_chain": "56",
            "onchain_contract_address": "0xf00",
        }
        key = ("FALLBACK", "56", "0xf00")
        previous = {
            key: {
                "active": True,
                "status": "active",
                "reason": "turnover-qualified",
                "turnover24hUsd": 20_000_000,
                "checkedAt": now_ms - 60 * 60 * 1000,
            }
        }
        unavailable = {
            "active": True,
            "status": "unavailable",
            "reason": "activity-unavailable",
            "turnover24hUsd": None,
        }
        with (
            patch.object(server.time, "time", return_value=now_ms / 1000),
            patch.object(server, "fetch_new_coin_low_market_activity", return_value={}),
            patch.object(server, "price_structure_onchain_activity_state", return_value=unavailable),
            patch.object(server, "price_monitor_retention_snapshot_index", return_value={}),
            patch.object(server, "PRICE_MONITOR_ACTIVITY_STATES", previous),
        ):
            filtered = server.filter_price_monitor_rows_by_activity([row])

        self.assertEqual([item["symbol"] for item in filtered], ["FALLBACK"])
        self.assertTrue(filtered[0]["marketActivity"]["fallback"])
        self.assertEqual(filtered[0]["marketActivity"]["status"], "active")

    def test_new_coin_low_inventory_respects_shared_contract_archive(self):
        now_ms = 1_800_000_000_000
        self.insert_asset("LOW", "0xaaa", now_ms)
        server.cold_archive_price_monitor_symbols(["LOW"], now_ms=now_ms)
        rows = [{"symbol": "LOW", "chain": "bsc", "contractAddress": "0xaaa"}]
        with (
            patch.object(server.time, "time", return_value=(now_ms + 1) / 1000),
            patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None),
        ):
            filtered = server.new_coin_low_apply_monitor_preferences(rows)
        self.assertEqual(filtered, [])

    def test_cached_structure_payload_hides_cold_archive_immediately(self):
        now_ms = 1_800_000_000_000
        self.insert_asset("CACHED", "0xbbb", now_ms, source_at=now_ms - 1)
        server.cold_archive_price_monitor_symbols(["CACHED"], now_ms=now_ms)
        payload = {
            "ok": True,
            "items": [
                {
                    "symbol": "CACHED",
                    "chain": "bsc",
                    "contractAddress": "0xbbb",
                    "frames": [{"key": "15m"}],
                    "signalCount": 1,
                },
                {"symbol": "KEEP", "frames": [], "signalCount": 0},
            ],
            "summary": {"total": 2, "available": 1, "signals": 1},
        }
        with (
            patch.object(server.time, "time", return_value=(now_ms + 1) / 1000),
            patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None),
        ):
            filtered = server.price_structure_payload_without_symbols(payload, set())

        self.assertEqual([item["symbol"] for item in filtered["items"]], ["KEEP"])
        self.assertEqual(filtered["summary"]["total"], 1)
        self.assertEqual(filtered["summary"]["available"], 0)
        self.assertEqual(filtered["summary"]["signals"], 0)

    def test_aicoin_history_row_cannot_bypass_contract_archive(self):
        now_ms = 1_800_000_000_000
        old_source = now_ms - 4 * 24 * 60 * 60 * 1000
        self.insert_asset("HISTORY", "0xccc", now_ms, source_at=old_source)
        server.cold_archive_price_monitor_symbols(["HISTORY"], now_ms=now_ms)
        source = {
            "status": "ok",
            "rows": [{"symbol": "HISTORY", "name": "History", "note": "crypto"}],
        }
        with (
            patch.object(server.time, "time", return_value=(now_ms + 1) / 1000),
            patch.object(server, "price_watch_aicoin_source", return_value=source),
            patch.object(server, "binance_wallet_4h_structure_rows", return_value=[]),
            patch.object(server, "price_watch_active_rows", return_value=[]),
            patch.object(server, "strategy_active_adaptive_contexts", return_value=[]),
            patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None),
        ):
            rows = server.price_structure_watch_rows()

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
