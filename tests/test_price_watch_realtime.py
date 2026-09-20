import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PriceWatchRealtimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.directory.name) / "auth.db"
        server.init_auth_db()
        server.PRICE_WATCH_PROCESS_BASELINED_SYMBOLS.add("USELESS")
        self.now = int(time.time() * 1000)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets(
                    symbol, name, manual_pinned, current_price, week_high,
                    distance_pct, status, aicoin_last_seen_at, provider,
                    setup_type, structure_json,
                    last_checked_at, created_at, updated_at
                ) VALUES('USELESS', 'USELESS', 1, 0.317, 0.31846, 0.458,
                         'near', ?, 'DEX Screener · solana', 'retest',
                         '{"priorHighConfirmed":true,"referenceHigh":0.31846}',
                         ?, ?, ?)
                """,
                (
                    self.now,
                    self.now - server.MONITOR_ALERT_REPLAY_MAX_GAP_MS - 1,
                    self.now,
                    self.now,
                ),
            )
            conn.execute(
                """
                INSERT INTO price_watch_alert_state(
                    symbol, reference_high, in_zone, last_alert_at,
                    left_zone_at, episode, updated_at
                ) VALUES('USELESS', 0.31846, 1, 0, 0, 0, ?)
                """,
                (self.now,),
            )

    def tearDown(self):
        server.PRICE_WATCH_PROCESS_BASELINED_SYMBOLS.discard("USELESS")
        server.AUTH_DB_PATH = self.original_db
        gc.collect()
        self.directory.cleanup()

    def test_realtime_quote_recovers_never_delivered_cross_after_observation_gap(self):
        result = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.319, "provider": "DEX Screener · solana"}},
            checked_at=self.now,
            persist_alerts=False,
        )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(result["alerts"]), 1)
        self.assertTrue(result["alerts"][0]["priorHighBreakout"])
        with server.auth_db() as conn:
            state = conn.execute(
                "SELECT in_breakout, last_alert_at FROM price_watch_breakout_state WHERE symbol='USELESS'"
            ).fetchone()
        self.assertEqual(dict(state), {"in_breakout": 1, "last_alert_at": self.now})

    def test_realtime_quote_alerts_on_fresh_cross_and_uses_observation_time(self):
        previous = self.now - 3_000
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET last_checked_at=?, current_price=0.317, status='near' WHERE symbol='USELESS'",
                (previous,),
            )
        result = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.319, "provider": "DEX Screener · solana"}},
            checked_at=self.now,
            persist_alerts=False,
        )

        self.assertEqual(len(result["alerts"]), 1)
        self.assertEqual(result["alerts"][0]["checkedAt"], self.now)
        self.assertTrue(result["alerts"][0]["priorHighBreakout"])

    def test_live_breakout_does_not_replace_the_historical_reference_high(self):
        previous = self.now - 3_000
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET last_checked_at=?, current_price=0.317, status='near' WHERE symbol='USELESS'",
                (previous,),
            )

        first = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.319, "provider": "DEX Screener · solana"}},
            checked_at=self.now,
            persist_alerts=False,
        )
        pullback = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.3189, "provider": "DEX Screener · solana"}},
            checked_at=self.now + server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS * 1000 + 1,
            persist_alerts=False,
        )
        marginal_high = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.3191, "provider": "DEX Screener · solana"}},
            checked_at=self.now + server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS * 1000 + 2,
            persist_alerts=False,
        )

        self.assertEqual(len(first["alerts"]), 1)
        self.assertEqual(pullback["alerts"], [])
        self.assertEqual(marginal_high["alerts"], [])
        with server.auth_db() as conn:
            asset = conn.execute(
                "SELECT week_high FROM price_watch_assets WHERE symbol='USELESS'"
            ).fetchone()
            alert_state = conn.execute(
                "SELECT reference_high, in_zone FROM price_watch_alert_state WHERE symbol='USELESS'"
            ).fetchone()
        self.assertAlmostEqual(asset["week_high"], 0.31846)
        self.assertAlmostEqual(alert_state["reference_high"], 0.31846)
        self.assertEqual(alert_state["in_zone"], 0)

    def test_exact_equality_is_not_a_near_or_breakout_alert(self):
        previous = self.now - 3_000
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET last_checked_at=?, current_price=0.317, status='normal' WHERE symbol='USELESS'",
                (previous,),
            )

        result = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.31846, "provider": "DEX Screener · solana"}},
            checked_at=self.now,
            persist_alerts=False,
        )

        self.assertEqual(result["alerts"], [])
        with server.auth_db() as conn:
            asset = conn.execute(
                "SELECT week_high, distance_pct, status FROM price_watch_assets WHERE symbol='USELESS'"
            ).fetchone()
        self.assertAlmostEqual(asset["week_high"], 0.31846)
        self.assertAlmostEqual(asset["distance_pct"], 0.0)
        self.assertEqual(asset["status"], "normal")

    def test_first_sample_after_quick_restart_recovers_never_delivered_cross_once(self):
        server.PRICE_WATCH_PROCESS_BASELINED_SYMBOLS.discard("USELESS")
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET last_checked_at=?, current_price=0.317, status='near' WHERE symbol='USELESS'",
                (self.now - 1_000,),
            )
        result = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.319, "provider": "DEX Screener · solana"}},
            checked_at=self.now,
            persist_alerts=False,
        )

        self.assertEqual(len(result["alerts"]), 1)
        repeated = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.319, "provider": "DEX Screener · solana"}},
            checked_at=self.now + 1_000,
            persist_alerts=False,
        )
        self.assertEqual(repeated["alerts"], [])

    def test_reference_high_refresh_does_not_create_a_batch_near_high_alert(self):
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET last_checked_at=?, current_price=0.317, status='near' WHERE symbol='USELESS'",
                (self.now - 3_000,),
            )
            conn.execute(
                "UPDATE price_watch_alert_state SET reference_high=0.30, in_zone=1 WHERE symbol='USELESS'"
            )

        result = server.apply_price_watch_realtime_quotes(
            [{"symbol": "USELESS"}],
            {"USELESS": {"price": 0.31846, "provider": "DEX Screener · solana"}},
            checked_at=self.now,
            persist_alerts=False,
        )

        self.assertEqual(result["alerts"], [])
        with server.auth_db() as conn:
            state = conn.execute(
                "SELECT reference_high, in_zone FROM price_watch_alert_state WHERE symbol='USELESS'"
            ).fetchone()
        self.assertAlmostEqual(state["reference_high"], 0.31846)
        self.assertEqual(state["in_zone"], 1)

    def test_exact_contract_quote_wins_over_same_ticker_exchange_quote(self):
        contract = "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk"
        rows = [{"symbol": "USELESS", "onchain_chain": "CT_501", "onchain_contract_address": contract}]

        def exchange_quotes(source, **_kwargs):
            return {"USELESS": {"price": 99.0, "provider": source}}

        def dex_quotes(chain, addresses, **_kwargs):
            self.assertEqual(chain, "solana")
            self.assertIn(contract, addresses)
            return {contract.casefold(): {"price": 0.319, "provider": "DEX Screener · solana"}}

        with patch.object(server, "price_watch_realtime_exchange_quotes", side_effect=exchange_quotes), patch.object(
            server, "price_watch_realtime_dex_quotes", side_effect=dex_quotes
        ):
            quotes = server.fetch_price_watch_realtime_quotes(rows)

        self.assertEqual(quotes["USELESS"]["price"], 0.319)
        self.assertEqual(quotes["USELESS"]["provider"], "DEX Screener · solana")

    def test_contract_without_exact_quote_does_not_fall_back_to_same_ticker(self):
        rows = [{
            "symbol": "SAME",
            "onchain_chain": "solana",
            "onchain_contract_address": "ExactContractAddress",
        }]
        with patch.object(
            server,
            "price_watch_realtime_exchange_quotes",
            return_value={"SAME": {"price": 100.0, "provider": "Binance Futures"}},
        ), patch.object(server, "price_watch_realtime_dex_quotes", return_value={}):
            quotes = server.fetch_price_watch_realtime_quotes(rows)

        self.assertNotIn("SAME", quotes)

    def test_binance_discovery_does_not_accept_same_ticker_hyperliquid_quote(self):
        rows = [{
            "symbol": "SCR",
            "pair_hint": "Binance 涨幅榜 SCRUSDT Scroll",
            "provider": "Binance Futures",
        }]

        def exchange_quotes(source, **_kwargs):
            if source == "hyperliquid":
                return {"SCR": {"price": 0.04737, "provider": "Hyperliquid"}}
            if source == "binance":
                return {"SCR": {"price": 0.02198, "provider": "Binance Futures"}}
            return {}

        with patch.object(
            server, "price_watch_realtime_exchange_quotes", side_effect=exchange_quotes
        ):
            quotes = server.fetch_price_watch_realtime_quotes(rows)

        self.assertEqual(quotes["SCR"]["price"], 0.02198)
        self.assertEqual(quotes["SCR"]["provider"], "Binance Futures")

    def test_provider_result_is_emitted_before_the_whole_batch_is_applied(self):
        rows = [{"symbol": "FAST", "provider": "Binance Futures"}]
        partials = []

        def exchange_quotes(source, **_kwargs):
            return (
                {"FAST": {"price": 1.25, "provider": "Binance Futures"}}
                if source == "binance" else {}
            )

        with patch.object(
            server, "price_watch_realtime_exchange_quotes", side_effect=exchange_quotes
        ):
            quotes = server.fetch_price_watch_realtime_quotes(
                rows,
                on_partial=lambda partial_rows, partial_quotes: partials.append(
                    (partial_rows, partial_quotes)
                ),
            )

        self.assertEqual(quotes["FAST"]["price"], 1.25)
        self.assertEqual(len(partials), 1)
        self.assertEqual(partials[0][1]["FAST"]["price"], 1.25)

    def test_one_hung_source_cannot_hold_the_next_realtime_cycle(self):
        rows = [{"symbol": "FAST", "provider": "Binance Futures"}]

        def exchange_quotes(source, **_kwargs):
            if source == "gate":
                time.sleep(0.25)
                return {}
            return (
                {"FAST": {"price": 1.25, "provider": "Binance Futures"}}
                if source == "binance" else {}
            )

        started = time.perf_counter()
        with patch.object(
            server, "PRICE_WATCH_REALTIME_CYCLE_DEADLINE_SECONDS", 0.05
        ), patch.object(
            server, "price_watch_realtime_exchange_quotes", side_effect=exchange_quotes
        ):
            quotes = server.fetch_price_watch_realtime_quotes(rows)

        self.assertLess(time.perf_counter() - started, 0.2)
        self.assertEqual(quotes["FAST"]["price"], 1.25)


if __name__ == "__main__":
    unittest.main()
