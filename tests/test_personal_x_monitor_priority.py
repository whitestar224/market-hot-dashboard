import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class PersonalXMonitorPriorityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        self.original_context_path = server.STRATEGY_ADAPTIVE_CONTEXT_PATH
        server.AUTH_DB_PATH = Path(self.tempdir.name) / "auth.db"
        server.STRATEGY_ADAPTIVE_CONTEXT_PATH = Path(self.tempdir.name) / "adaptive.json"
        server.init_auth_db()
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()
        with server.PRICE_STRUCTURE_ONCHAIN_POOL_CACHE_LOCK:
            server.PRICE_STRUCTURE_ONCHAIN_POOL_CACHE.clear()

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        server.STRATEGY_ADAPTIVE_CONTEXT_PATH = self.original_context_path
        with server.PRICE_STRUCTURE_CACHE_LOCK:
            server.PRICE_STRUCTURE_CACHE.clear()
        with server.PRICE_STRUCTURE_ONCHAIN_POOL_CACHE_LOCK:
            server.PRICE_STRUCTURE_ONCHAIN_POOL_CACHE.clear()
        gc.collect()
        self.tempdir.cleanup()

    @staticmethod
    def payload(timestamp, text="$chip好像有新币止跌的势头"):
        return {
            "items": [{
                "handle": "whitestar224",
                "fullText": text,
                "publishedAt": timestamp,
            }]
        }

    def ingest(self, timestamp, text="$chip好像有新币止跌的势头"):
        with patch.object(server, "x_kol_priority_handles", return_value=("whitestar224",)), patch.object(
            server, "queue_personal_x_monitor_priority_refresh"
        ):
            return server.update_strategy_contexts_from_personal_x_payload(
                self.payload(timestamp, text),
                now_ms=timestamp,
            )

    def test_new_mention_is_first_in_prior_high_and_multi_timeframe_lists(self):
        now = 1_800_000_000_000
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets (
                    symbol, name, manual_pinned, aicoin_first_seen_at,
                    aicoin_last_seen_at, created_at, updated_at
                ) VALUES ('MANUAL', 'Manual', 1, 0, 0, ?, ?)
                """,
                (now - 1_000, now - 1_000),
            )
        self.ingest(now)

        prior_rows = server.price_watch_active_rows()
        self.assertEqual(prior_rows[0]["symbol"], "CHIP")
        prior_item = server.price_watch_public_item(prior_rows[0])
        self.assertTrue(prior_item["personalXPriority"])
        self.assertEqual(prior_item["origin"], "personal-x")
        self.assertEqual(prior_item["personalXSourceText"], "$chip好像有新币止跌的势头")

        with (
            patch.object(server, "price_watch_aicoin_source", return_value={"status": "ok", "rows": []}),
            patch.object(server, "binance_wallet_4h_structure_rows", return_value=[]),
            patch.object(server, "strategy_active_adaptive_contexts", return_value=[]),
            patch.object(server, "filter_price_monitor_rows_by_activity", side_effect=lambda rows: rows),
        ):
            structure_rows = server.price_structure_watch_rows()
        self.assertEqual(structure_rows[0]["symbol"], "CHIP")
        self.assertEqual(structure_rows[0]["adaptiveContext"]["label"], "个人 X 提及")

    def test_same_cached_post_cannot_undo_manual_exclusions_but_new_post_can(self):
        now = 1_800_000_000_000
        self.ingest(now)
        server.exclude_price_watch_prior_high("CHIP")
        server.exclude_price_structure_symbol("CHIP")

        self.ingest(now)
        with server.auth_db() as conn:
            same_asset = conn.execute(
                "SELECT prior_high_excluded_at FROM price_watch_assets WHERE symbol = 'CHIP'"
            ).fetchone()
        self.assertGreater(same_asset["prior_high_excluded_at"], 0)
        self.assertTrue(server.price_structure_symbol_excluded("CHIP"))

        self.ingest(now + 60_000, "$CHIP 继续观察止跌后的承接")
        with server.auth_db() as conn:
            new_asset = conn.execute(
                "SELECT prior_high_excluded_at, personal_x_mentioned_at FROM price_watch_assets WHERE symbol = 'CHIP'"
            ).fetchone()
        self.assertEqual(new_asset["prior_high_excluded_at"], 0)
        self.assertEqual(new_asset["personal_x_mentioned_at"], now + 60_000)
        self.assertFalse(server.price_structure_symbol_excluded("CHIP"))

    def test_recent_personal_x_asset_survives_aicoin_cleanup(self):
        now = 1_800_000_000_000
        self.ingest(now)
        with patch.object(server.time, "time", return_value=now / 1000), patch.object(
            server,
            "price_watch_aicoin_source",
            return_value={"status": "ok", "rows": [{"symbol": "OTHER", "name": "Other", "note": "crypto"}]},
        ):
            server.sync_price_watch_aicoin_candidates()
        with server.auth_db() as conn:
            chip = conn.execute(
                "SELECT personal_x_mentioned_at FROM price_watch_assets WHERE symbol = 'CHIP'"
            ).fetchone()
        self.assertIsNotNone(chip)
        self.assertEqual(chip["personal_x_mentioned_at"], now)

    def test_startup_backfill_recovers_recent_offline_mention_into_both_pools(self):
        now = 1_800_000_000_000
        posted_at = now - 2 * 24 * 60 * 60_000
        with (
            patch.object(server, "x_kol_priority_handles", return_value=("whitestar224",)),
            patch.object(server, "queue_personal_x_monitor_priority_refresh") as refresh,
            patch.dict(server.os.environ, {"PERSONAL_X_MONITOR_BACKFILL_DAYS": "3"}),
        ):
            server.update_strategy_contexts_from_personal_x_payload(
                self.payload(posted_at, "$LONGXIA 看看能不能在前高盘整一下然后拉出主升浪"),
                now_ms=now,
                monitor_max_age_seconds=server.personal_x_monitor_backfill_seconds(),
            )

        with server.auth_db() as conn:
            recovered = conn.execute(
                "SELECT personal_x_mentioned_at, prior_high_excluded_at FROM price_watch_assets WHERE symbol = 'LONGXIA'"
            ).fetchone()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["personal_x_mentioned_at"], posted_at)
        self.assertEqual(recovered["prior_high_excluded_at"], 0)
        refresh.assert_called_once_with(["LONGXIA"])

        prior_rows = server.price_watch_active_rows()
        self.assertEqual(prior_rows[0]["symbol"], "LONGXIA")
        with (
            patch.object(server, "price_watch_aicoin_source", return_value={"status": "ok", "rows": []}),
            patch.object(server, "binance_wallet_4h_structure_rows", return_value=[]),
            patch.object(server, "strategy_active_adaptive_contexts", return_value=[]),
            patch.object(server, "filter_price_monitor_rows_by_activity", side_effect=lambda rows: rows),
        ):
            structure_rows = server.price_structure_watch_rows()
        self.assertEqual(structure_rows[0]["symbol"], "LONGXIA")

    def test_startup_backfill_does_not_replay_posts_older_than_window(self):
        now = 1_800_000_000_000
        posted_at = now - 4 * 24 * 60 * 60_000
        with (
            patch.object(server, "x_kol_priority_handles", return_value=("whitestar224",)),
            patch.object(server, "queue_personal_x_monitor_priority_refresh"),
            patch.dict(server.os.environ, {"PERSONAL_X_MONITOR_BACKFILL_DAYS": "3"}),
        ):
            server.update_strategy_contexts_from_personal_x_payload(
                self.payload(posted_at, "$LONGXIA 旧动态"),
                now_ms=now,
                monitor_max_age_seconds=server.personal_x_monitor_backfill_seconds(),
            )

        with server.auth_db() as conn:
            recovered = conn.execute(
                "SELECT symbol FROM price_watch_assets WHERE symbol = 'LONGXIA'"
            ).fetchone()
        self.assertIsNone(recovered)

    def test_personal_x_onchain_asset_uses_aggregate_dex_turnover_before_activity_gate(self):
        now = 1_800_000_000_000
        self.ingest(now, "$PONS 重点关注链上结构")
        cex_activity = {
            "PONS": {
                "turnover24hUsd": 2_000_000,
                "source": "Aster Futures",
            }
        }
        onchain_activity = {
            "active": True,
            "status": "active",
            "reason": "onchain-turnover-active",
            "turnover24hUsd": 18_500_000,
            "source": "DexScreener 链上聚合",
            "thresholdUsd": server.NEW_COIN_LOW_MIN_TURNOVER_24H_USD,
            "network": "robinhood",
            "contractAddress": "0x9fe1a89c2b5a702dd2f5eb9f783a08e3d6cec737",
        }
        with (
            patch.object(server.time, "time", return_value=now / 1000),
            patch.object(server, "price_watch_aicoin_source", return_value={"status": "ok", "rows": []}),
            patch.object(server, "fetch_new_coin_low_market_activity", return_value=cex_activity),
            patch.object(server, "price_structure_onchain_activity_state", return_value=onchain_activity) as fallback,
        ):
            structure_rows = server.price_structure_watch_rows()

        self.assertEqual(structure_rows[0]["symbol"], "PONS")
        self.assertEqual(structure_rows[0]["marketActivity"]["source"], "DexScreener 链上聚合")
        self.assertEqual(structure_rows[0]["chain"], "robinhood")
        self.assertEqual(
            structure_rows[0]["contractAddress"],
            "0x9fe1a89c2b5a702dd2f5eb9f783a08e3d6cec737",
        )
        fallback.assert_called_once_with("PONS", contract_address=None, chain=None)

    def test_personal_x_explicit_contract_identity_is_persisted_for_structure_kline(self):
        now = 1_800_000_000_000
        contract = "0xeb9e768c42d6f5b08d34980c6f721494372a7777"
        self.ingest(now, f"$CAILI BSC 合约地址 {contract} 重点关注链上结构")

        with server.auth_db() as conn:
            asset = conn.execute(
                """
                SELECT onchain_chain, onchain_chain_label, onchain_contract_address
                FROM price_watch_assets WHERE symbol = 'CAILI'
                """
            ).fetchone()
        self.assertEqual(asset["onchain_chain"], "56")
        self.assertEqual(asset["onchain_chain_label"], "BNB Chain")
        self.assertEqual(asset["onchain_contract_address"], contract)

        with (
            patch.object(server, "price_watch_aicoin_source", return_value={"status": "ok", "rows": []}),
            patch.object(server, "binance_wallet_4h_structure_rows", return_value=[]),
            patch.object(server, "strategy_active_adaptive_contexts", return_value=[]),
            patch.object(server, "filter_price_monitor_rows_by_activity", side_effect=lambda rows: rows),
        ):
            structure_rows = server.price_structure_watch_rows()
        self.assertEqual(structure_rows[0]["contractAddress"], contract)
        self.assertEqual(structure_rows[0]["chain"], "56")

    def test_onchain_pool_aggregates_same_contract_volume_across_pools(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "pairs": [
                {
                    "chainId": "robinhood",
                    "pairAddress": "0xpool1",
                    "baseToken": {"symbol": "PONS", "address": "0xofficial"},
                    "liquidity": {"usd": 1_100_000},
                    "volume": {"h24": 6_500_000},
                },
                {
                    "chainId": "robinhood",
                    "pairAddress": "0xpool2",
                    "baseToken": {"symbol": "PONS", "address": "0xofficial"},
                    "liquidity": {"usd": 700_000},
                    "volume": {"h24": 5_500_000},
                },
                {
                    "chainId": "bsc",
                    "pairAddress": "0xcopy",
                    "baseToken": {"symbol": "PONS", "address": "0xcopytoken"},
                    "liquidity": {"usd": 8_000},
                    "volume": {"h24": 20_000_000},
                },
            ]
        }
        with (
            patch.object(server.CHAIN_ECOSYSTEM_MONITOR.store, "list_chains", return_value=[]),
            patch.object(server.requests, "get", return_value=response),
        ):
            pool = server.price_structure_onchain_pool("PONS")

        self.assertEqual(pool["contractAddress"], "0xofficial")
        self.assertEqual(pool["poolAddress"], "0xpool1")
        self.assertEqual(pool["poolCount"], 2)
        self.assertEqual(pool["volume24hUsd"], 12_000_000)
        self.assertEqual(pool["aggregateLiquidityUsd"], 1_800_000)

    def test_prior_high_reuses_preferred_structure_source_for_onchain_personal_x_asset(self):
        def candles(symbol, interval, *, limit=140, min_rows=30, timeout=8):
            self.assertEqual(symbol, "PONS")
            count = 169 if interval == "1h" else 96 if interval == "15m" else 30
            rows = [
                (
                    1_700_000_000_000 + index * 60_000,
                    10 + index * 0.01,
                    10.4 + index * 0.01,
                    9.8 + index * 0.01,
                    10.2 + index * 0.01,
                    1_000,
                )
                for index in range(count)
            ]
            return rows[-limit:], "KuCoin Spot"

        with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
            server.PRICE_STRUCTURE_PROVIDER_PREFERENCE["PONS"] = "KuCoin Spot"
        try:
            with (
                patch.object(server, "price_structure_candles_from_kucoin", side_effect=candles) as kucoin,
                patch.object(server, "price_watch_candles_from_binance", side_effect=AssertionError("Binance should not run")),
                patch.object(server, "price_watch_candles_from_okx", side_effect=AssertionError("OKX should not run")),
                patch.object(server, "price_watch_candles_from_bitget_futures", side_effect=AssertionError("Bitget should not run")),
                patch.object(server, "price_structure_candles_from_gate", side_effect=RuntimeError("no Gate")),
                patch.object(server, "price_structure_candles_from_htx", side_effect=RuntimeError("no HTX")),
                patch.object(server, "price_structure_candles_from_aster", side_effect=RuntimeError("no Aster")),
                patch.object(server, "price_structure_candles_from_hyperliquid", side_effect=RuntimeError("no Hyperliquid")),
                patch.object(server, "price_structure_candles_from_geckoterminal", side_effect=RuntimeError("no chain")),
                patch.object(server, "price_watch_structure", return_value={"qualified": False}),
                patch.object(server, "price_watch_fib_structure", return_value={"mainWaveQualified": False}),
                patch.object(server, "price_watch_oversold_structure", return_value={"candidate": False}),
            ):
                result = server.fetch_price_watch_snapshot({
                    "symbol": "PONS",
                    "personal_x_mentioned_at": 1_800_000_000_000,
                    "marketActivity": {
                        "source": "DexScreener 链上主池",
                        "reason": "onchain-turnover-active",
                    },
                })
        finally:
            with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
                server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.pop("PONS", None)

        self.assertEqual(result["symbol"], "PONS")
        self.assertEqual(
            result.get("provider"),
            "KuCoin Spot",
            f"KuCoin calls={kucoin.call_args_list}; result={result}",
        )
        self.assertGreater(result["currentPrice"], 0)
        self.assertGreater(result["weekHigh"], 0)
        self.assertNotEqual(result["status"], "unavailable")
        self.assertEqual(kucoin.call_count, 3)

    def test_short_daily_history_does_not_blank_prior_high_snapshot(self):
        def candles(symbol, interval, *, limit=140, min_rows=30, timeout=8):
            self.assertEqual(symbol, "LONGXIA")
            count = 48 if interval == "1h" else 32 if interval == "15m" else 2
            rows = [
                (
                    1_700_000_000_000 + index * 60_000,
                    0.04 + index * 0.0001,
                    0.041 + index * 0.0001,
                    0.039 + index * 0.0001,
                    0.0405 + index * 0.0001,
                    1_000,
                )
                for index in range(count)
            ]
            return rows[-limit:], "KuCoin Spot"

        with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
            server.PRICE_STRUCTURE_PROVIDER_PREFERENCE["LONGXIA"] = "KuCoin Spot"
        try:
            with (
                patch.object(server, "price_structure_candles_from_kucoin", side_effect=candles),
                patch.object(server, "price_watch_candles_from_binance", side_effect=AssertionError("Binance should not run")),
                patch.object(server, "price_watch_candles_from_okx", side_effect=AssertionError("OKX should not run")),
                patch.object(server, "price_watch_candles_from_bitget_futures", side_effect=AssertionError("Bitget should not run")),
                patch.object(server, "price_watch_structure", return_value={"qualified": False}),
                patch.object(server, "price_watch_oversold_structure", return_value={"candidate": False}),
            ):
                result = server.fetch_price_watch_snapshot({"symbol": "LONGXIA"})
        finally:
            with server.PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK:
                server.PRICE_STRUCTURE_PROVIDER_PREFERENCE.pop("LONGXIA", None)

        self.assertEqual(result["provider"], "KuCoin Spot")
        self.assertGreater(result["currentPrice"], 0)
        self.assertGreater(result["weekHigh"], 0)
        self.assertNotEqual(result["status"], "unavailable")
        self.assertEqual(result["fib"]["reason"], "insufficient-daily-history")


if __name__ == "__main__":
    unittest.main()
