import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class GainersPriceWatchTests(unittest.TestCase):
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
    def source(source_id, *symbols):
        return {
            "id": source_id,
            "status": "ok",
            "rows": [
                {
                    "rank": index,
                    "symbol": symbol,
                    "name": f"{symbol}-USDT-SWAP" if source_id == "okx-gainers" else f"{symbol}USDT",
                    "icon": f"https://example.com/{symbol.lower()}.png",
                    "note": "24h gainers",
                }
                for index, symbol in enumerate(symbols, 1)
            ],
        }

    def test_binance_spot_futures_and_okx_leaders_enter_both_monitor_pools(self):
        now_ms = 1_800_000_000_000
        sources = [
            self.source("binance-gainers", "AAA", "SHARED"),
            self.source("binance-futures-gainers", "FUT", "AAA"),
            self.source("okx-gainers", "BBB", "SHARED"),
        ]
        with patch.object(server.time, "time", return_value=now_ms / 1000):
            synced = server.sync_price_watch_gainers_candidates(sources, now_ms=now_ms)
            active = server.price_watch_active_rows()
            public = {row["symbol"]: server.price_watch_public_item(row) for row in active}
            with (
                patch.object(server, "price_watch_aicoin_source", return_value={"status": "unavailable", "rows": []}),
                patch.object(server, "binance_wallet_4h_structure_rows", return_value=[]),
                patch.object(server, "fetch_new_coin_low_market_activity", return_value={}) as activity_fetch,
                patch.object(server, "strategy_active_adaptive_contexts", return_value=[]),
            ):
                structure = {row["symbol"]: row for row in server.price_structure_watch_rows()}

        self.assertEqual(synced, 3)
        self.assertEqual(set(public), {"AAA", "FUT", "BBB"})
        self.assertTrue(all(item["priorHighEnabled"] for item in public.values()))
        self.assertEqual(public["AAA"]["origin"], "binance-gainers")
        self.assertEqual(public["FUT"]["origin"], "binance-futures-gainers")
        self.assertEqual(public["BBB"]["origin"], "okx-gainers")
        self.assertEqual(structure["AAA"]["structureMembershipSources"], ["Binance涨幅榜"])
        self.assertEqual(structure["FUT"]["structureMembershipSources"], ["Binance合约涨幅榜"])
        self.assertEqual(structure["BBB"]["structureMembershipSources"], ["OKX涨幅榜"])
        self.assertTrue(all(item["monitorPoolEnteredAt"] == now_ms for item in structure.values()))
        self.assertTrue(all(item["structure1mEnabled"] for item in structure.values()))
        self.assertTrue(all(item["structure1mMode"] == "auto-pool-day" for item in structure.values()))
        activity_fetch.assert_not_called()

    def test_binance_futures_gainers_use_public_usdt_contract_tickers(self):
        payload = [
            {"symbol": "FUTUSDT", "lastPrice": "2.5", "priceChangePercent": "28.4", "quoteVolume": "9000000"},
            {"symbol": "LOWUSDT", "lastPrice": "1.2", "priceChangePercent": "12.1", "quoteVolume": "8000000"},
            {"symbol": "BTCUSDT", "lastPrice": "100000", "priceChangePercent": "40", "quoteVolume": "900000000"},
            {"symbol": "DOWNUSDT", "lastPrice": "1", "priceChangePercent": "-2", "quoteVolume": "100000"},
            {"symbol": "NOTUSD", "lastPrice": "1", "priceChangePercent": "99", "quoteVolume": "100000"},
        ]
        with patch.object(server.requests, "get", return_value=FakeResponse(payload)) as request:
            source = server.fetch_binance_futures_gainers()

        self.assertEqual(source["id"], "binance-futures-gainers")
        self.assertEqual(source["title"], "Binance 合约涨幅榜")
        self.assertEqual([row["symbol"] for row in source["rows"]], ["FUT", "LOW"])
        self.assertEqual(source["rows"][0]["change"], "+28.40%")
        self.assertIn("/futures/FUTUSDT", source["rows"][0]["url"])
        self.assertEqual(request.call_args.args[0], "https://fapi.binance.com/fapi/v1/ticker/24hr")

    def test_new_leaders_revoke_old_gainer_membership_but_keep_other_sources(self):
        now_ms = 1_800_000_000_000
        server.sync_price_watch_gainers_candidates([
            self.source("binance-gainers", "OLDB", "SECONDB"),
            self.source("okx-gainers", "OLDO", "SECONDO"),
        ], now_ms=now_ms)
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET aicoin_first_seen_at=?, aicoin_last_seen_at=? WHERE symbol='OLDB'",
                (now_ms, now_ms),
            )
            conn.execute(
                "INSERT INTO price_watch_alert_state(symbol, episode, last_alert_at, updated_at) VALUES('OLDO', 1, ?, ?)",
                (now_ms, now_ms),
            )

        later_ms = now_ms + 60_000
        server.sync_price_watch_gainers_candidates([
            self.source("binance-gainers", "NEWB", "OLDB"),
            self.source("okx-gainers", "NEWO", "OLDO"),
        ], now_ms=later_ms)

        with patch.object(server.time, "time", return_value=later_ms / 1000):
            active = {row["symbol"] for row in server.price_watch_active_rows()}
        with server.auth_db() as conn:
            rows = {
                row["symbol"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM price_watch_assets WHERE symbol IN ('OLDB','OLDO','NEWB','NEWO')"
                ).fetchall()
            }
            old_o_alert = conn.execute(
                "SELECT 1 FROM price_watch_alert_state WHERE symbol='OLDO'"
            ).fetchone()

        self.assertEqual(active, {"OLDB", "NEWB", "NEWO"})
        self.assertEqual(rows["OLDB"]["gainers_first_seen_at"], 0)
        self.assertEqual(rows["OLDB"]["binance_gainers_last_seen_at"], 0)
        self.assertGreater(rows["OLDB"]["aicoin_last_seen_at"], 0)
        self.assertEqual(rows["OLDO"]["gainers_first_seen_at"], 0)
        self.assertEqual(rows["OLDO"]["okx_gainers_last_seen_at"], 0)
        self.assertIsNone(old_o_alert)
        self.assertEqual(rows["NEWB"]["binance_gainers_rank"], 1)
        self.assertEqual(rows["NEWO"]["okx_gainers_rank"], 1)

    def test_unavailable_source_preserves_its_last_known_leader(self):
        now_ms = 1_800_000_000_000
        server.sync_price_watch_gainers_candidates([
            self.source("binance-gainers", "BINANCEOLD"),
            self.source("okx-gainers", "OKXOLD"),
        ], now_ms=now_ms)

        server.sync_price_watch_gainers_candidates([
            self.source("binance-gainers", "BINANCENEW"),
            {"id": "okx-gainers", "status": "unavailable", "rows": []},
        ], now_ms=now_ms + 60_000)

        with patch.object(server.time, "time", return_value=(now_ms + 60_000) / 1000):
            active = {row["symbol"] for row in server.price_watch_active_rows()}
        self.assertEqual(active, {"BINANCENEW", "OKXOLD"})

    def test_successful_empty_board_clears_that_exchange_leader(self):
        now_ms = 1_800_000_000_000
        server.sync_price_watch_gainers_candidates([
            self.source("binance-gainers", "BINANCEOLD"),
            self.source("okx-gainers", "OKXOLD"),
        ], now_ms=now_ms)

        server.sync_price_watch_gainers_candidates([
            {"id": "binance-gainers", "status": "ok", "rows": []},
            {"id": "okx-gainers", "status": "unavailable", "rows": []},
        ], now_ms=now_ms + 60_000)

        with patch.object(server.time, "time", return_value=(now_ms + 60_000) / 1000):
            active = {row["symbol"] for row in server.price_watch_active_rows()}
        self.assertEqual(active, {"OKXOLD"})

    def test_gainer_window_does_not_refresh_and_expires_after_three_days(self):
        now_ms = 1_800_000_000_000
        source = self.source("binance-gainers", "AAA")
        server.sync_price_watch_gainers_candidates([source], now_ms=now_ms)
        refreshed_ms = now_ms + 2 * 24 * 60 * 60 * 1000
        server.sync_price_watch_gainers_candidates([source], now_ms=refreshed_ms)

        expired_ms = now_ms + server.GAINERS_MONITOR_PROMOTION_SECONDS * 1000 + 1
        with (
            patch.object(server.time, "time", return_value=expired_ms / 1000),
            patch.object(server, "price_watch_aicoin_source", return_value={"status": "unavailable", "rows": []}),
        ):
            server.sync_price_watch_aicoin_candidates()
            self.assertEqual(server.price_watch_active_rows(), [])
            with server.auth_db() as conn:
                row = dict(conn.execute(
                    "SELECT * FROM price_watch_assets WHERE symbol = 'AAA'"
                ).fetchone())
            self.assertFalse(server.price_watch_prior_high_source_enabled(row, now_ms=expired_ms))
        self.assertEqual(row["gainers_first_seen_at"], now_ms)
        self.assertEqual(row["binance_gainers_last_seen_at"], refreshed_ms)

        # A service refresh must not reset the expired 72h clock while the coin
        # remains on the exchange leaderboard.
        server.sync_price_watch_gainers_candidates([source], now_ms=expired_ms)
        with patch.object(server.time, "time", return_value=expired_ms / 1000):
            self.assertEqual(server.price_watch_active_rows(), [])

    def test_aicoin_observation_graduates_gainer_to_normal_retention(self):
        now_ms = 1_800_000_000_000
        server.sync_price_watch_gainers_candidates(
            [self.source("okx-gainers", "AAA")],
            now_ms=now_ms,
        )
        aicoin_seen_ms = now_ms + 2 * 24 * 60 * 60 * 1000
        with (
            patch.object(server.time, "time", return_value=aicoin_seen_ms / 1000),
            patch.object(
                server,
                "price_watch_aicoin_source",
                return_value={
                    "status": "ok",
                    "rows": [{"symbol": "AAA", "name": "AAA", "note": "crypto"}],
                },
            ),
        ):
            server.sync_price_watch_aicoin_candidates()

        later_ms = now_ms + 10 * 24 * 60 * 60 * 1000
        with patch.object(server.time, "time", return_value=later_ms / 1000):
            active = server.price_watch_active_rows()
            item = server.price_watch_public_item(active[0])
        self.assertEqual([row["symbol"] for row in active], ["AAA"])
        self.assertEqual(item["origin"], "aicoin")
        self.assertTrue(item["priorHighEnabled"])

    def test_aicoin_native_chinese_contract_symbol_enters_monitor(self):
        now_ms = 1_800_000_000_000
        source = {
            "status": "ok",
            "rows": [{
                "rank": 2,
                "symbol": "龙虾",
                "name": "龙虾",
                "note": "longxiausdt:weex",
            }],
        }
        with (
            patch.object(server.time, "time", return_value=now_ms / 1000),
            patch.object(server, "price_watch_aicoin_source", return_value=source),
        ):
            synced = server.sync_price_watch_aicoin_candidates()
            active = server.price_watch_active_rows()

        self.assertEqual(synced, 1)
        self.assertEqual([row["symbol"] for row in active], ["龙虾"])
        self.assertEqual(active[0]["name"], "龙虾")
        self.assertEqual(active[0]["pair_hint"], "longxiausdt:weex")
        self.assertTrue(server.is_price_watch_auto_crypto_candidate("龙虾", "龙虾", "longxiausdt:weex"))

    def test_global_exclusion_still_blocks_gainer_refresh(self):
        now_ms = 1_800_000_000_000
        source = self.source("binance-gainers", "AAA")
        server.sync_price_watch_gainers_candidates([source], now_ms=now_ms)
        with patch.object(server.time, "time", return_value=now_ms / 1000):
            server.exclude_monitor_symbol_globally("AAA", source_pool="structure")
            server.sync_price_watch_gainers_candidates([source], now_ms=now_ms + 60_000)
            self.assertEqual(server.price_watch_active_rows(), [])
        self.assertTrue(server.price_structure_symbol_excluded("AAA"))

    def test_gainer_membership_is_not_removed_by_generic_turnover_gate(self):
        now_ms = 1_800_000_000_000
        row = {
            "symbol": "AAA",
            "gainers_first_seen_at": now_ms,
            "binance_gainers_last_seen_at": now_ms,
            "okx_gainers_last_seen_at": 0,
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
            patch.object(server, "price_monitor_market_activity_state", return_value=inactive),
        ):
            filtered = server.filter_price_monitor_rows_by_activity([row])

        self.assertEqual([item["symbol"] for item in filtered], ["AAA"])
        self.assertEqual(filtered[0]["marketActivity"]["reason"], "exchange-gainers-membership")

    def test_monitor_refresh_syncs_gainers_before_aicoin_cleanup(self):
        calls = []
        with (
            patch.object(server, "sync_price_watch_new_contract_candidates", side_effect=lambda: calls.append("new")),
            patch.object(server, "sync_price_watch_gainers_candidates", side_effect=lambda: calls.append("gainers")),
            patch.object(server, "sync_price_watch_aicoin_candidates", side_effect=lambda: calls.append("aicoin")),
            patch.object(server, "sync_price_watch_binance_wallet_candidates", side_effect=lambda: calls.append("wallet")),
            patch.object(server, "price_watch_active_rows", return_value=[]),
            patch.object(server, "filter_price_monitor_rows_by_activity", return_value=[]),
        ):
            server.sync_price_watch_monitor()
        self.assertEqual(calls, ["new", "gainers", "aicoin", "wallet"])


if __name__ == "__main__":
    unittest.main()
