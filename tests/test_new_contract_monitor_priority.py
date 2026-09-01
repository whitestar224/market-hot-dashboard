import gc
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import server


class NewContractMonitorPriorityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        self.original_context_path = server.STRATEGY_ADAPTIVE_CONTEXT_PATH
        server.AUTH_DB_PATH = Path(self.tempdir.name) / "auth.db"
        server.STRATEGY_ADAPTIVE_CONTEXT_PATH = Path(self.tempdir.name) / "adaptive.json"
        server.init_auth_db()

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        server.STRATEGY_ADAPTIVE_CONTEXT_PATH = self.original_context_path
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()
        gc.collect()
        self.tempdir.cleanup()

    @staticmethod
    def china_ms(year, month, day, hour=0, minute=0):
        china = timezone(timedelta(hours=8))
        return int(datetime(year, month, day, hour, minute, tzinfo=china).timestamp() * 1000)

    @staticmethod
    def source(source_id, source_label, symbol, listed_at):
        return {
            "id": source_id,
            "status": "ok",
            "monitorSourceLabel": source_label,
            "rows": [{
                "asset": symbol,
                "symbol": f"{symbol}USDT",
                "name": f"{symbol}USDT",
                "date": listed_at,
            }],
        }

    def test_listing_window_is_metadata_only_and_does_not_enable_structure(self):
        listed_at = self.china_ms(2026, 8, 16, 15, 30)
        expected = self.china_ms(2026, 8, 18, 0, 0)
        self.assertEqual(server.new_contract_1m_expires_at(listed_at), expected)

        row = {
            "symbol": "NEW",
            "new_contract_listed_at": listed_at,
            "new_contract_source": "Binance 新合约",
        }
        before = server.price_structure_priority_context(
            row,
            now_ms=self.china_ms(2026, 8, 17, 23, 59),
        )
        after = server.price_structure_priority_context(row, now_ms=expected)
        self.assertIsNone(before)
        self.assertIsNone(after)
        self.assertFalse(server.price_structure_1m_state(
            {"adaptiveContext": before, "structure1mOverride": -1},
            now_ms=self.china_ms(2026, 8, 17, 23, 59),
        )["enabled"])
        self.assertFalse(server.price_structure_1m_state(
            {"adaptiveContext": after, "structure1mOverride": -1},
            now_ms=expected,
        )["enabled"])

    def test_exchange_contracts_enter_neither_structure_nor_prior_high_by_themselves(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        binance_at = self.china_ms(2026, 8, 16, 10, 0)
        okx_at = self.china_ms(2026, 8, 16, 18, 0)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned, created_at, updated_at
                ) VALUES ('MANUAL', 'Manual', 1, ?, ?)
                """,
                (now - 1_000, now - 1_000),
            )

        changed = server.sync_price_watch_new_contract_candidates(
            now_ms=now,
            sources=[
                self.source("binance-new", "Binance 新合约", "BNEW", binance_at),
                self.source("okx-new", "OKX 新合约", "ONEW", okx_at),
            ],
        )
        self.assertEqual({row["symbol"] for row in changed}, {"BNEW", "ONEW"})

        with patch.object(server.time, "time", return_value=now / 1000):
            prior_rows = server.price_watch_active_rows()
            self.assertEqual([row["symbol"] for row in prior_rows[:3]], ["ONEW", "BNEW", "MANUAL"])
            public = {row["symbol"]: server.price_watch_public_item(row) for row in prior_rows}
            self.assertFalse(public["ONEW"]["priorHighEnabled"])
            self.assertFalse(public["BNEW"]["priorHighEnabled"])
            self.assertFalse(public["MANUAL"]["priorHighEnabled"])
            with patch.object(
                server, "price_watch_aicoin_source", return_value={"status": "ok", "rows": []}
            ), patch.object(server, "binance_wallet_4h_structure_rows", return_value=[]):
                structure_rows = server.price_structure_watch_rows()
        self.assertEqual(structure_rows, [])

    def test_tradfi_contract_rows_never_enter_coin_monitors(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        source = self.source("binance-new", "Binance 新合约", "NAVER", now - 60_000)
        source["rows"][0].update({"assetType": "tradfi", "tags": ["永续", "TradFi"]})
        changed = server.sync_price_watch_new_contract_candidates(now_ms=now, sources=[source])
        self.assertEqual(changed, [])
        with server.auth_db() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM price_watch_assets WHERE symbol = 'NAVER'"
            ).fetchone())

    def test_manual_switch_is_the_only_way_a_listing_can_enable_one_minute(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        context = server.price_structure_priority_context({
            "symbol": "NEW",
            "new_contract_listed_at": self.china_ms(2026, 8, 16, 10, 0),
            "new_contract_source": "OKX 新合约",
        }, now_ms=now)
        self.assertIsNone(context)
        self.assertFalse(server.price_structure_1m_state(
            {"adaptiveContext": context, "structure1mOverride": -1}, now_ms=now
        )["enabled"])
        self.assertFalse(server.price_structure_1m_state(
            {"adaptiveContext": context, "structure1mOverride": 0}, now_ms=now
        )["enabled"])
        self.assertTrue(server.price_structure_1m_state(
            {"adaptiveContext": {}, "structure1mOverride": 1}, now_ms=now
        )["enabled"])

        closed = server.set_price_structure_1m_override("NEW", False)
        self.assertFalse(closed["structure1mEnabled"])
        self.assertEqual(server.price_structure_1m_override_for_symbol("NEW"), 0)
        opened = server.set_price_structure_1m_override("NEW", True)
        self.assertTrue(opened["structure1mEnabled"])
        self.assertEqual(server.price_structure_1m_override_for_symbol("NEW"), 1)

    def test_each_structure_interval_switch_is_independent_and_persistent(self):
        five_closed = server.set_price_structure_interval_override("NEW", "5m", False)
        self.assertFalse(five_closed["structureIntervalStates"]["5m"]["enabled"])
        self.assertTrue(five_closed["structureIntervalStates"]["15m"]["enabled"])
        self.assertFalse(server.price_structure_alert_interval_allowed({"symbol": "NEW"}, "5m"))
        self.assertTrue(server.price_structure_alert_interval_allowed({"symbol": "NEW"}, "15m"))

        daily_closed = server.set_price_structure_interval_override("NEW", "1d", False)
        self.assertFalse(daily_closed["structureIntervalStates"]["1d"]["enabled"])
        self.assertFalse(daily_closed["structureIntervalStates"]["5m"]["enabled"])
        self.assertTrue(daily_closed["structureIntervalStates"]["4h"]["enabled"])

        five_opened = server.set_price_structure_interval_override("NEW", "5m", True)
        persisted = server.price_structure_interval_overrides_for_symbol("NEW")
        self.assertTrue(five_opened["structureIntervalStates"]["5m"]["enabled"])
        self.assertEqual(persisted["5m"], 1)
        self.assertEqual(persisted["1d"], 0)

    def test_generic_interval_switch_leaves_listing_one_minute_auto_off(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        context = server.price_structure_priority_context({
            "symbol": "NEW",
            "new_contract_listed_at": self.china_ms(2026, 8, 16, 10, 0),
            "new_contract_source": "Binance 新合约",
        }, now_ms=now)
        with patch.object(server.time, "time", return_value=now / 1000):
            server.set_price_structure_interval_override("NEW", "15m", False)
            states = server.price_structure_interval_states({
                "adaptiveContext": context,
                "structure1mOverride": -1,
                "structureIntervalOverrides": {"15m": 0},
            }, now_ms=now)

        self.assertFalse(states["1m"]["enabled"])
        self.assertEqual(states["1m"]["mode"], "auto-off")
        self.assertFalse(states["15m"]["enabled"])
        self.assertTrue(states["1h"]["enabled"])

    def test_invalid_structure_interval_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "周期无效"):
            server.set_price_structure_interval_override("NEW", "2h", False)

    def test_new_coin_low_pool_honors_shared_interval_switches_and_exclusion(self):
        row = {"symbol": "LOW", "newCoinListedAt": self.china_ms(2026, 8, 1)}
        server.set_price_structure_interval_override("LOW", "15m", False)
        enriched = server.new_coin_low_apply_monitor_preferences([row])
        self.assertEqual(enriched[0]["structureIntervalOverrides"]["15m"], 0)

        server.exclude_price_structure_symbol("LOW")
        self.assertEqual(server.new_coin_low_apply_monitor_preferences([row]), [])

    def test_aster_is_candle_fallback_only_and_cannot_admit_new_coin_structure(self):
        aster_only = {
            "symbol": "KOMA",
            "monitorPool": "new-coin-low",
            "newCoinSource": "Aster",
            "newCoinSources": ["Aster"],
            "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
        }
        shared = {
            **aster_only,
            "symbol": "SHARED",
            "newCoinSource": "Aster",
            "newCoinSources": ["Aster", "OKX"],
        }

        self.assertFalse(server.new_coin_low_row_admitted(aster_only))
        self.assertFalse(server.price_structure_broadcast_allowed(aster_only, "4h"))
        self.assertTrue(server.new_coin_low_row_admitted(shared))
        self.assertTrue(server.price_structure_broadcast_allowed(shared, "4h"))
        with patch.object(server, "fetch_price_structure_item") as fetch:
            skipped = server.refresh_new_coin_low_structure_item(aster_only)
        self.assertTrue(skipped["skipped"])
        self.assertEqual(skipped["alerts"], 0)
        fetch.assert_not_called()

        original_inventory_cache = server.NEW_COIN_LOW_INVENTORY_CACHE
        try:
            server.NEW_COIN_LOW_INVENTORY_CACHE = (server.time.time(), [aster_only, shared])
            cached_rows = server.new_coin_low_inventory_rows()
            self.assertEqual([row["symbol"] for row in cached_rows], ["SHARED"])
        finally:
            server.NEW_COIN_LOW_INVENTORY_CACHE = original_inventory_cache

        snapshot_path = Path(self.tempdir.name) / "new-coin-low.json"
        original_items = dict(server.NEW_COIN_LOW_ITEMS)
        try:
            server.NEW_COIN_LOW_ITEMS.clear()
            server.write_json_cache(snapshot_path, {"items": [aster_only, shared]})
            with patch.object(server, "NEW_COIN_LOW_SNAPSHOT_PATH", snapshot_path):
                server.hydrate_new_coin_low_snapshot()
            self.assertNotIn("KOMA", server.NEW_COIN_LOW_ITEMS)
            self.assertIn("SHARED", server.NEW_COIN_LOW_ITEMS)
        finally:
            server.NEW_COIN_LOW_ITEMS.clear()
            server.NEW_COIN_LOW_ITEMS.update(original_items)

    def test_main_structure_broadcast_requires_aicoin_or_personal_x_membership(self):
        unauthorized = {
            "symbol": "ASTERONLY",
            "monitorPool": "aicoin-x",
            "structureMembershipSources": [],
            "provider": "Aster Futures",
            "broadcastEligibility": {"eligible": True, "allowedIntervals": []},
        }
        authorized = {
            **unauthorized,
            "symbol": "ACE",
            "structureMembershipSources": ["AiCoin"],
        }

        self.assertFalse(server.price_structure_broadcast_allowed(unauthorized, "4h"))
        self.assertTrue(server.price_structure_broadcast_allowed(authorized, "4h"))
        membership, provider = server.price_structure_alert_source_labels(authorized)
        self.assertEqual(membership, "入池：AiCoin")
        self.assertEqual(provider, "K线：Aster备用")

    def test_new_coin_low_age_uses_first_cross_exchange_listing_not_latest_listing(self):
        now = self.china_ms(2026, 8, 23, 12, 0)
        candidates = {}
        old_binance_listing = self.china_ms(2025, 7, 1, 12, 0)
        recent_gate_listing = self.china_ms(2026, 8, 22, 12, 0)

        server.merge_new_coin_low_listing_candidate(
            candidates, "OLD", recent_gate_listing, "Gate", "OLD_USDT", now_ms=now
        )
        server.merge_new_coin_low_listing_candidate(
            candidates, "OLD", old_binance_listing, "Binance", "OLDUSDT", now_ms=now
        )

        row = candidates["OLD"]
        self.assertEqual(row["newCoinFirstListedAt"], old_binance_listing)
        self.assertEqual(row["newCoinListedAt"], old_binance_listing)
        self.assertEqual(row["newCoinFirstSource"], "Binance")
        self.assertEqual(row["newCoinSources"], ["Binance", "Gate"])
        self.assertTrue(server.new_coin_low_row_admitted(row))
        self.assertFalse(server.new_coin_low_row_within_age(row, now_ms=now))

    def test_new_coin_low_activity_filter_requires_ten_million_and_is_reversible(self):
        now = self.china_ms(2026, 8, 24, 8, 0)
        old_row = {
            "symbol": "QUIET",
            "newCoinFirstListedAt": now - 30 * 24 * 60 * 60 * 1000,
        }
        recent_row = {
            "symbol": "FRESH",
            "newCoinFirstListedAt": now - 3 * 24 * 60 * 60 * 1000,
        }

        inactive = server.new_coin_low_activity_state(
            old_row,
            {"QUIET": {"turnover24hUsd": server.NEW_COIN_LOW_MIN_TURNOVER_24H_USD - 1, "source": "Gate"}},
            now_ms=now,
        )
        recovered = server.new_coin_low_activity_state(
            old_row,
            {"QUIET": {"turnover24hUsd": server.NEW_COIN_LOW_MIN_TURNOVER_24H_USD, "source": "Gate"}},
            now_ms=now,
        )
        unavailable = server.new_coin_low_activity_state(old_row, {}, now_ms=now)
        recent_but_inactive = server.new_coin_low_activity_state(
            recent_row,
            {"FRESH": {"turnover24hUsd": 1, "source": "HTX"}},
            now_ms=now,
        )

        self.assertFalse(inactive["active"])
        self.assertEqual(inactive["status"], "inactive")
        self.assertTrue(recovered["active"])
        self.assertTrue(unavailable["active"])
        self.assertEqual(unavailable["reason"], "activity-data-unavailable-keep")
        self.assertFalse(recent_but_inactive["active"])
        self.assertEqual(recent_but_inactive["status"], "inactive")
        self.assertEqual(server.NEW_COIN_LOW_MIN_TURNOVER_24H_USD, 10_000_000)
        self.assertEqual(server.NEW_COIN_LOW_ACTIVITY_GRACE_DAYS, 0)

    def test_history_only_exchange_can_disqualify_later_structure_admission(self):
        now = self.china_ms(2026, 8, 23, 12, 0)
        candidates = {}
        old_aster_listing = self.china_ms(2025, 6, 1, 12, 0)
        recent_okx_listing = self.china_ms(2026, 8, 23, 9, 0)

        server.merge_new_coin_low_listing_candidate(
            candidates, "RELIST", recent_okx_listing, "OKX", "RELIST-USDT-SWAP", now_ms=now
        )
        server.merge_new_coin_low_listing_candidate(
            candidates, "RELIST", old_aster_listing, "Aster", "RELISTUSDT", now_ms=now
        )

        row = candidates["RELIST"]
        self.assertEqual(row["newCoinFirstSource"], "Aster")
        self.assertEqual(row["newCoinSources"], ["Aster", "OKX"])
        self.assertTrue(server.new_coin_low_row_admitted(row))
        self.assertFalse(server.new_coin_low_row_within_age(row, now_ms=now))

        aster_only = {}
        server.merge_new_coin_low_listing_candidate(
            aster_only, "ASTERONLY", recent_okx_listing, "Aster", "ASTERONLYUSDT", now_ms=now
        )
        self.assertTrue(server.new_coin_low_row_within_age(aster_only["ASTERONLY"], now_ms=now))
        self.assertFalse(server.new_coin_low_row_admitted(aster_only["ASTERONLY"]))

    def test_new_coin_low_structure_only_exposes_one_hour_and_four_hour(self):
        now = self.china_ms(2026, 8, 23, 12, 0)
        candles = [
            (now - index * 60_000, 1.0, 1.1, 0.9, 1.0, 100.0)
            for index in range(40, 0, -1)
        ]
        strategy_result = {
            "frames": [
                {"key": "1m", "pattern": "ignored"},
                {"key": "1h", "pattern": "one-hour"},
                {"key": "4h", "pattern": "four-hour"},
                {"key": "1d", "pattern": "ignored"},
            ],
            "signals": [
                {"interval": "1m", "kind": "ignored"},
                {"interval": "1h", "kind": "keep"},
                {"interval": "4h", "kind": "keep"},
            ],
            "alertHints": [
                {"interval": "5m", "kind": "ignored"},
                {"interval": "4h", "kind": "keep"},
            ],
            "strategyVersion": "test",
        }
        row = {
            "symbol": "FRESH",
            "monitorPool": "new-coin-low",
            "newCoinListedAt": now - 30 * 24 * 60 * 60 * 1000,
            "newCoinSource": "OKX",
            "newCoinSources": ["OKX"],
            "adaptiveContext": {"mode": "new-coin-low"},
        }

        with patch.object(
            server, "price_structure_candles_from_binance", return_value=(candles, "Binance Futures")
        ) as fetch, patch.object(
            server, "run_dragon_wave_monitor_strategy", return_value=strategy_result
        ) as strategy, patch.object(
            server, "price_structure_broadcast_eligibility", return_value={"eligible": False, "allowedIntervals": []}
        ), patch.object(
            server, "new_coin_low_position_context", return_value={"qualified": True}
        ), patch.object(server.time, "time", return_value=now / 1000):
            result = server.fetch_price_structure_item(row)

        self.assertEqual(fetch.call_count, 3)
        strategy_timeframes = strategy.call_args.args[0]
        self.assertEqual(set(strategy_timeframes), {"1h", "4h"})
        self.assertEqual([frame["key"] for frame in result["frames"]], ["1h", "4h"])
        self.assertEqual([signal["interval"] for signal in result["signals"]], ["1h", "4h"])
        self.assertEqual([hint["interval"] for hint in result["alertHints"]], ["4h"])
        self.assertEqual(result["broadcastEligibility"]["allowedIntervals"], ["1h", "4h"])
        self.assertFalse(result["structure1mEnabled"])
        self.assertEqual(set(result["structureIntervalStates"]), {"1h", "4h"})
        self.assertFalse(server.price_structure_broadcast_allowed(result, "15m"))
        self.assertTrue(server.price_structure_broadcast_allowed(result, "1h"))

        legacy = server.normalize_new_coin_low_structure_item({
            **result,
            "frames": [{"key": "1m"}, {"key": "1h"}, {"key": "4h"}, {"key": "1d"}],
            "signals": [{"interval": "5m"}, {"interval": "4h"}],
            "alertHints": [{"interval": "15m"}, {"interval": "1h"}],
            "structure1mEnabled": True,
        })
        self.assertEqual([frame["key"] for frame in legacy["frames"]], ["1h", "4h"])
        self.assertEqual([signal["interval"] for signal in legacy["signals"]], ["4h"])
        self.assertEqual([hint["interval"] for hint in legacy["alertHints"]], ["1h"])
        self.assertFalse(legacy["structure1mEnabled"])

    def test_new_listing_undoes_neither_prior_high_nor_structure_exclusion(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        first_at = self.china_ms(2026, 8, 16, 10, 0)
        source = self.source("binance-new", "Binance 新合约", "NEW", first_at)
        server.sync_price_watch_new_contract_candidates(now_ms=now, sources=[source])
        server.exclude_price_watch_prior_high("NEW")
        server.exclude_price_structure_symbol("NEW")

        server.sync_price_watch_new_contract_candidates(now_ms=now, sources=[source])
        with server.auth_db() as conn:
            unchanged = conn.execute(
                "SELECT prior_high_excluded_at FROM price_watch_assets WHERE symbol = 'NEW'"
            ).fetchone()
        self.assertGreater(unchanged["prior_high_excluded_at"], 0)
        self.assertTrue(server.price_structure_symbol_excluded("NEW"))

        later_at = self.china_ms(2026, 8, 16, 18, 0)
        server.sync_price_watch_new_contract_candidates(
            now_ms=now,
            sources=[self.source("okx-new", "OKX 新合约", "NEW", later_at)],
        )
        with server.auth_db() as conn:
            restored = conn.execute(
                "SELECT prior_high_excluded_at, new_contract_source FROM price_watch_assets WHERE symbol = 'NEW'"
            ).fetchone()
        self.assertGreater(restored["prior_high_excluded_at"], 0)
        self.assertEqual(restored["new_contract_source"], "OKX 新合约")
        self.assertTrue(server.price_structure_symbol_excluded("NEW"))

    def test_aicoin_or_personal_x_are_the_only_prior_high_sources(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        base = {
            "symbol": "ONLY",
            "prior_high_excluded_at": 0,
            "dismissed_until": 0,
            "new_contract_listed_at": now - 60_000,
            "new_contract_source": "Gate 新合约",
        }
        with patch.object(server.time, "time", return_value=now / 1000):
            self.assertFalse(server.price_watch_prior_high_source_enabled(base, now_ms=now))
            self.assertTrue(server.price_watch_prior_high_source_enabled(
                {**base, "aicoin_last_seen_at": now - 29 * 24 * 60 * 60 * 1000}, now_ms=now
            ))
            self.assertTrue(server.price_watch_prior_high_source_enabled(
                {**base, "personal_x_mentioned_at": now - 29 * 24 * 60 * 60 * 1000}, now_ms=now
            ))
            self.assertFalse(server.price_watch_prior_high_source_enabled(
                {**base, "aicoin_last_seen_at": now - 31 * 24 * 60 * 60 * 1000}, now_ms=now
            ))

    def test_official_new_contract_window_does_not_qualify_structure_broadcast(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        context = server.price_structure_priority_context({
            "symbol": "NEW",
            "new_contract_listed_at": self.china_ms(2026, 8, 16, 10, 0),
            "new_contract_source": "Binance 新合约",
        }, now_ms=now)
        with patch.object(server.time, "time", return_value=now / 1000):
            result = server.price_structure_broadcast_eligibility(
                {"symbol": "NEW"},
                {"1m": [], "15m": [], "1d": []},
                context,
            )
        self.assertFalse(result["eligible"])
        self.assertFalse(result.get("officialNewContractWindow", False))

    def test_new_contract_fetch_uses_normal_history_requirements(self):
        now = self.china_ms(2026, 8, 16, 19, 0)
        context = server.price_structure_priority_context({
            "symbol": "NEW",
            "new_contract_listed_at": self.china_ms(2026, 8, 16, 10, 0),
            "new_contract_source": "Binance 新合约",
        }, now_ms=now)
        candles = [
            (now - 120_000, 1.0, 1.1, 0.9, 1.0, 100.0),
            (now - 60_000, 1.0, 1.2, 0.95, 1.1, 120.0),
        ]

        with patch.object(
            server,
            "price_structure_candles_from_binance",
            return_value=(candles, "Binance Futures"),
        ) as fetch, patch.object(
            server,
            "run_dragon_wave_monitor_strategy",
            return_value={"frames": [], "signals": [], "strategyVersion": "test"},
        ), patch.object(server.time, "time", return_value=now / 1000):
            result = server.fetch_price_structure_item({
                "symbol": "NEW",
                "adaptiveContext": context,
                "structure1mOverride": -1,
            })

        self.assertEqual(result["provider"], "Binance Futures")
        self.assertFalse(result["structure1mEnabled"])
        self.assertEqual(fetch.call_count, len(server.PRICE_STRUCTURE_TIMEFRAMES))
        self.assertTrue(all(call.kwargs["min_rows"] > 2 for call in fetch.call_args_list))


if __name__ == "__main__":
    unittest.main()
