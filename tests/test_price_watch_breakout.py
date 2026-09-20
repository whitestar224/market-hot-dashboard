import gc
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PriceOnlyBreakoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.directory.name) / "auth.db"
        server.init_auth_db()
        server.PRICE_WATCH_PROCESS_BASELINED_SYMBOLS.add("SOPH")
        self.now = int(time.time() * 1000)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets(
                    symbol, name, manual_pinned, aicoin_last_seen_at,
                    current_price, week_high, status, provider, setup_type,
                    structure_json, created_at, updated_at
                ) VALUES(
                    'SOPH', 'Sophon', 1, ?, 96, 100, 'normal', 'test',
                    'retest', '{"priorHighConfirmed":true,"referenceHigh":100}', ?, ?
                )
                """,
                (self.now, self.now, self.now),
            )

    def tearDown(self):
        server.PRICE_WATCH_PROCESS_BASELINED_SYMBOLS.discard("SOPH")
        server.AUTH_DB_PATH = self.original
        gc.collect()
        self.directory.cleanup()

    def snapshot(self, price, delta=0, *, week_high=100, observed_high=None):
        return {"symbol": "SOPH", "currentPrice": price, "weekHigh": week_high,
                "observedHigh": observed_high if observed_high is not None else max(week_high, price),
                "distancePct": max(0, week_high-price), "status": "breakout" if price>week_high else "near" if price>=week_high*0.97 else "normal",
                "setupType": "retest",
                "priorHighConfirmed": True,
                "structure": {"qualified": True, "priorHighConfirmed": True, "referenceHigh": week_high, "type": "retest"},
                "provider": "test", "checkedAt": self.now + delta}

    def test_main_wave_stage_requires_an_independent_ignition_quality_advance(self):
        hour = 3_600_000
        baseline = [
            (self.now + index * hour, 0.0304, 0.0301, 0.0300, 0.0298, 100)
            for index in range(40)
        ]
        advance = []
        for offset in range(25):
            ratio = offset / 24
            close = 0.0289 + (0.0420 - 0.0289) * ratio
            advance.append((
                self.now + (40 + offset) * hour,
                0.04421 if offset == 24 else close * 1.012,
                close,
                close * 0.995,
                0.0287 if offset == 0 else close * 0.985,
                320,
            ))
        pullback = [
            (self.now + (65 + index) * hour, 0.041, 0.0394, 0.0397, 0.0388, 120)
            for index in range(8)
        ]
        candles = baseline + advance + pullback
        structure = {
            "referenceHigh": 0.04421,
            "referenceHighAt": advance[-1][0],
        }
        with patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None), \
             patch.object(server, "price_structure_broadcast_eligibility", return_value={
                 "reason": "active-hot-coin", "marketRegimeReset": False,
                 "eventDrivenReset": False,
             }):
            result = server.price_watch_main_wave_stage_context(
                {"symbol": "哈基米"}, candles, [], [], structure, {}, 0.0394,
                current_at=pullback[-1][0],
            )

        self.assertTrue(result["qualified"])
        self.assertEqual(result["reason"], "ignition-quality-recent-independent-advance")
        self.assertGreaterEqual(result["advanceBars"], 24)
        self.assertGreater(result["priorAdvancePct"], 50)

    def test_short_rebound_inside_a_long_decline_is_not_a_main_wave(self):
        hour = 3_600_000
        baseline = [
            (self.now + index * hour, 0.00305, 0.0030, 0.00302, 0.00298, 100)
            for index in range(40)
        ]
        rebound = [
            (self.now + (40 + index) * hour, high, close, close * 0.99, low, 250)
            for index, (high, close, low) in enumerate([
                (0.00299, 0.00296, 0.002922),
                (0.00310, 0.00307, 0.00295),
                (0.00322, 0.00318, 0.00302),
                (0.00332, 0.00327, 0.00312),
                (0.00341, 0.00325, 0.00320),
            ])
        ]
        candles = baseline + rebound
        structure = {
            "referenceHigh": 0.00341,
            "referenceHighAt": rebound[-1][0],
        }
        with patch.object(server, "strategy_adaptive_context_for_symbol", return_value=None), \
             patch.object(server, "price_structure_broadcast_eligibility", return_value={
                 "reason": "active-hot-coin", "marketRegimeReset": False,
                 "eventDrivenReset": False,
             }):
            result = server.price_watch_main_wave_stage_context(
                {"symbol": "REZ"}, candles, [], [], structure, {}, 0.00325,
                current_at=rebound[-1][0],
            )

        self.assertFalse(result["qualified"])
        self.assertEqual(result["stage"], "neutral")
        self.assertEqual(result["reason"], "no-independent-main-wave-advance")

    def test_realtime_quote_cannot_alert_an_unqualified_stage_high(self):
        structure = {
            "qualified": False,
            "priorHighConfirmed": False,
            "referenceHigh": 100,
            "referenceKind": "STAGE_HIGH_CANDIDATE",
            "mainWaveQualified": False,
            "mainWaveReason": "no-independent-main-wave-advance",
        }
        with server.auth_db() as conn:
            conn.execute(
                """
                UPDATE price_watch_assets
                SET current_price=96, week_high=100, status='normal', provider='test',
                    structure_json=?, last_checked_at=?
                WHERE symbol='SOPH'
                """,
                (json.dumps(structure), self.now),
            )
            row = dict(conn.execute(
                "SELECT * FROM price_watch_assets WHERE symbol='SOPH'"
            ).fetchone())

        result = server.apply_price_watch_realtime_quotes(
            [row],
            {"SOPH": {"price": 99, "provider": "test"}},
            checked_at=self.now + 1000,
            persist_alerts=False,
        )

        self.assertEqual(result["alerts"], [])
        with server.auth_db() as conn:
            stored = dict(conn.execute(
                "SELECT status FROM price_watch_assets WHERE symbol='SOPH'"
            ).fetchone())
            public_item = server.price_watch_public_item(dict(conn.execute(
                "SELECT * FROM price_watch_assets WHERE symbol='SOPH'"
            ).fetchone()))
        self.assertEqual(stored["status"], "forming")
        self.assertFalse(public_item["mainWaveQualified"])
        self.assertEqual(public_item["stageHighLabel"], "阶段高点（未通过主升浪）")

    def test_near_then_breakout_alert_separately_and_survive_restart(self):
        with patch.object(server, "price_watch_live_transition_is_current", return_value=True):
            near = server.update_price_watch_snapshot(self.snapshot(99))
            crossed = server.update_price_watch_snapshot(self.snapshot(101, 1000, observed_high=101))
            self.assertEqual(len(near), 1)
            self.assertFalse(near[0]["priorHighBreakout"])
            self.assertEqual(len(crossed), 1)
            self.assertTrue(crossed[0]["priorHighBreakout"])
            server.init_auth_db()
            self.assertEqual(server.update_price_watch_snapshot(self.snapshot(102, 2000, observed_high=102)), [])
            self.assertEqual(server.update_price_watch_snapshot(self.snapshot(99, 3000, week_high=102)), [])
            self.assertEqual(server.update_price_watch_snapshot(
                self.snapshot(101, server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS*1000+2000, week_high=102)
            ), [])
            server.update_price_watch_snapshot(self.snapshot(96, server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS*1000+3000, week_high=102))
            later = server.update_price_watch_snapshot(
                self.snapshot(101, server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS*1000+4000, week_high=102)
            )
            self.assertEqual(len(later), 1)
            self.assertFalse(later[0]["priorHighBreakout"])
            self.assertEqual(later[0]["weekHigh"], 102)

    def test_crossed_reference_does_not_rearm_without_detector_confirmation(self):
        with patch.object(server, "price_watch_live_transition_is_current", return_value=True):
            self.assertEqual(len(server.update_price_watch_snapshot(self.snapshot(99))), 1)
            self.assertEqual(len(server.update_price_watch_snapshot(self.snapshot(101, 1000))), 1)
            cooldown = server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS * 1000
            self.assertEqual(server.update_price_watch_snapshot(self.snapshot(94, cooldown + 2000)), [])
            self.assertEqual(server.update_price_watch_snapshot(self.snapshot(99, cooldown + 3000)), [])

        with server.auth_db() as conn:
            asset = conn.execute(
                "SELECT status, distance_pct FROM price_watch_assets WHERE symbol='SOPH'"
            ).fetchone()
            breakout = conn.execute(
                "SELECT in_breakout FROM price_watch_breakout_state WHERE symbol='SOPH'"
            ).fetchone()
        self.assertEqual(asset["status"], "redefining")
        self.assertIsNone(asset["distance_pct"])
        self.assertEqual(breakout["in_breakout"], 1)

    def test_live_crossing_revalidates_kline_before_using_a_stale_high(self):
        row = {"symbol": "SOPH", "week_high": 100, "prior_high_consumed": 0}
        quote = {"SOPH": {"price": 101, "provider": "test"}}
        newer_reference = self.snapshot(104, week_high=105, observed_high=105)
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_alert_state(
                    symbol, reference_high, in_zone, last_alert_at, left_zone_at, episode, updated_at
                ) VALUES('SOPH', 100, 0, 0, 0, 0, ?)
                """,
                (self.now,),
            )
        with patch.object(server, "fetch_price_watch_snapshot", return_value=newer_reference), \
             patch.object(server, "price_watch_live_transition_is_current", return_value=True):
            checked = server.price_watch_revalidate_realtime_breakouts(
                [row], quote, persist_alerts=False
            )
            events = server.apply_price_watch_realtime_quotes(
                [row], checked["quotes"], checked_at=self.now + 1000, persist_alerts=False
            )["alerts"]

        self.assertEqual(checked["refreshed"], 1)
        self.assertEqual(len(checked["alerts"]), 1)
        self.assertFalse(checked["alerts"][0]["priorHighBreakout"])
        self.assertEqual(events, [])
        with server.auth_db() as conn:
            asset = conn.execute(
                "SELECT week_high, status FROM price_watch_assets WHERE symbol='SOPH'"
            ).fetchone()
        self.assertEqual(asset["week_high"], 105)
        self.assertNotEqual(asset["status"], "breakout")

    def test_forming_candle_high_is_saved_as_the_new_week_high(self):
        hourly = [(self.now - (24-i)*3_600_000, 100, 99, 95) for i in range(23)]
        hourly.append((self.now // 3_600_000 * 3_600_000, 105, 101, 98))

        def candles(*args, **kwargs):
            if kwargs.get("interval") == "15m":
                return hourly, "Binance Futures"
            return hourly, "Binance Futures"

        with patch.object(server, "price_watch_candles_from_binance", side_effect=candles), \
             patch.object(server, "price_watch_daily_candles_from_binance", return_value=([], "test")), \
             patch.object(server, "price_watch_extended_snapshot_providers", return_value=[]), \
             patch.object(server, "price_watch_main_wave_stage_context", return_value={
                 "qualified": True, "stage": "active", "reason": "test-main-wave",
             }), \
             patch.object(server, "PRICE_STRUCTURE_PROVIDER_PREFERENCE", {}):
            result = server.fetch_price_watch_snapshot({"symbol": "SOPH"})

        self.assertEqual(result["weekHigh"], 100)
        self.assertEqual(result["observedHigh"], 105)

    def test_prior_high_setup_requires_a_causal_pivot_and_real_separation(self):
        hour = 3_600_000
        vertical = [
            (self.now - (13 - index) * hour, 88 + index, 87 + index, 86 + index, 85 + index, 100)
            for index in range(13)
        ]
        unconfirmed = server.price_watch_prior_high_setup(
            vertical,
            [],
            current_at=self.now,
        )

        highs = [92, 94, 96, 98, 100, 99, 97, 96, 95, 94, 95, 96, 97, 98]
        separated = [
            (self.now - (len(highs) - index) * hour, high, high - 0.5, high - 1, high - 2, 100)
            for index, high in enumerate(highs)
        ]
        confirmed = server.price_watch_prior_high_setup(
            separated,
            [],
            current_at=self.now,
        )

        self.assertFalse(unconfirmed["qualified"])
        self.assertEqual(unconfirmed["reason"], "waiting-structure-level")
        self.assertTrue(confirmed["qualified"])
        self.assertEqual(confirmed["type"], "structure-level")
        self.assertEqual(confirmed["referenceHigh"], 100)
        self.assertIn(confirmed["separationType"], {"SPATIAL", "SPATIAL+TEMPORAL"})

    def test_prior_high_setup_uses_latest_main_wave_stage_pivot(self):
        hour = 3_600_000
        historical = [
            (self.now - (24 - index) * hour, 0.045, 0.040, 0.040, 0.039, 100)
            for index in range(24)
        ]
        analysis = {
            "version": "test-stage-high",
            "reason": "structure-level-ready",
            "actionableLevel": {
                "levelId": 5,
                "levelClass": "SWING",
                "anchor": 0.04488,
                "referenceAnchor": 0.04421,
                "createdBar": 4,
                "createdTime": historical[4][0],
                "referenceBar": 17,
                "referenceTime": historical[17][0],
                "referenceKind": "MAIN_WAVE_STAGE_HIGH",
                "separationType": "SPATIAL+TEMPORAL",
                "touchCount": 4,
                "breakoutCount": 0,
                "armed": True,
            },
        }

        with patch.object(server, "analyze_prior_high", return_value=analysis):
            result = server.price_watch_prior_high_setup(
                historical, [], current_at=self.now
            )

        self.assertEqual(result["referenceHigh"], 0.04421)
        self.assertEqual(result["referenceHighAt"], historical[17][0])
        self.assertEqual(result["barsSinceHigh"], 6)
        self.assertEqual(result["referenceKind"], "MAIN_WAVE_STAGE_HIGH")

    def test_detector_rearm_allows_a_fresh_cross_of_the_same_level(self):
        with patch.object(server, "price_watch_live_transition_is_current", return_value=True):
            first = server.update_price_watch_snapshot(self.snapshot(101, observed_high=101))
            self.assertEqual(len(first), 1)

            reset = self.snapshot(
                96,
                server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS * 1000 + 1000,
                observed_high=101,
            )
            reset["structure"].update({"armed": True, "rearmed": True, "breakoutCount": 1})
            self.assertEqual(server.update_price_watch_snapshot(reset), [])
            with server.auth_db() as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT in_breakout FROM price_watch_breakout_state WHERE symbol='SOPH'"
                    ).fetchone()[0],
                    0,
                )

            second = self.snapshot(
                101,
                server.PRICE_WATCH_REENTRY_COOLDOWN_SECONDS * 1000 + 2000,
                observed_high=101,
            )
            second["structure"].update({"armed": False, "rearmed": False, "breakoutCount": 2})
            repeated = server.update_price_watch_snapshot(second)
            self.assertEqual(len(repeated), 1)
            self.assertTrue(repeated[0]["priorHighBreakout"])

    def test_binance_gainer_snapshot_cannot_switch_to_hyperliquid_same_ticker(self):
        hourly = [
            (self.now - (24 - index) * 3_600_000, 0.025, 0.022)
            for index in range(24)
        ]
        hyperliquid = (
            "Hyperliquid",
            lambda: ([(row[0], 0.06, 0.04737) for row in hourly], "Hyperliquid"),
            lambda: ([], "Hyperliquid"),
            lambda: ([], "Hyperliquid"),
        )
        with patch.object(
            server, "price_watch_candles_from_binance", return_value=(hourly, "Binance Futures")
        ), patch.object(
            server, "price_watch_daily_candles_from_binance", return_value=([], "Binance Futures")
        ), patch.object(
            server, "price_watch_extended_snapshot_providers", return_value=[hyperliquid]
        ), patch.object(server, "PRICE_STRUCTURE_PROVIDER_PREFERENCE", {}):
            result = server.fetch_price_watch_snapshot({
                "symbol": "SCR",
                "pair_hint": "Binance 涨幅榜 SCRUSDT Scroll",
                "binance_gainers_last_seen_at": self.now,
            })

        self.assertEqual(result["provider"], "Binance Futures")
        self.assertEqual(result["currentPrice"], 0.022)
        self.assertNotEqual(result["currentPrice"], 0.04737)

    def test_late_higher_wick_refresh_seeds_new_near_zone_without_rebound(self):
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO price_watch_alert_state(
                    symbol, reference_high, in_zone, last_alert_at, left_zone_at, episode, updated_at
                ) VALUES('SOPH', 100, 0, 0, 0, 2, ?)
                """,
                (self.now,),
            )

        with patch.object(server, "price_watch_live_transition_is_current", return_value=True):
            events = server.update_price_watch_snapshot(
                self.snapshot(104, week_high=105, observed_high=105)
            )

        self.assertEqual(events, [])
        with server.auth_db() as conn:
            state = conn.execute(
                "SELECT reference_high, in_zone, episode FROM price_watch_alert_state WHERE symbol='SOPH'"
            ).fetchone()
        self.assertEqual(dict(state), {"reference_high": 105.0, "in_zone": 1, "episode": 2})

    def test_production_price_episode_rolls_back_when_outbox_cannot_commit(self):
        store=server.AlertDeliveryStore(Path(self.directory.name)/'deliveries.sqlite')
        with patch.object(server,'ALERT_DELIVERY_STORE',store),patch.object(server,'record_price_watch_flow_event'),patch.object(server,'env_flag',return_value=False),patch.object(server,'price_watch_live_transition_is_current',return_value=True):
            with patch.object(store,'admit',side_effect=OSError('injected full disk')):
                with self.assertRaises(OSError):
                    server.update_price_watch_snapshot(self.snapshot(101),persist_alerts=True)
            events=server.update_price_watch_snapshot(self.snapshot(101),persist_alerts=True)
            self.assertEqual(len(events),1)
            self.assertEqual(store.inbox()['pending'],1)
            self.assertEqual(server.update_price_watch_snapshot(self.snapshot(102,1000),persist_alerts=True),[])

    def test_old_gap_above_high_alerts_once_and_exclusion_clears_state(self):
        with patch.object(server, "price_watch_live_transition_is_current", return_value=False):
            events = server.update_price_watch_snapshot(self.snapshot(102))
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["priorHighBreakout"])
        with server.auth_db() as conn:
            self.assertEqual(conn.execute("SELECT in_breakout FROM price_watch_breakout_state").fetchone()[0], 1)
            conn.execute("DELETE FROM price_watch_alert_state WHERE symbol='SOPH'")
            self.assertIsNone(conn.execute("SELECT * FROM price_watch_breakout_state").fetchone())

    def test_stale_gap_recovers_a_cross_that_was_never_delivered(self):
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET current_price=99, week_high=100, status='near' WHERE symbol='SOPH'"
            )
            conn.execute(
                """
                INSERT INTO price_watch_alert_state(symbol, reference_high, in_zone, last_alert_at, episode, updated_at)
                VALUES('SOPH', 100, 1, 0, 0, ?)
                """,
                (self.now,),
            )
        with patch.object(server, "price_watch_live_transition_is_current", return_value=False):
            events = server.update_price_watch_snapshot(self.snapshot(101))

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["priorHighBreakout"])
        with server.auth_db() as conn:
            state = conn.execute(
                "SELECT in_breakout, last_alert_at FROM price_watch_breakout_state WHERE symbol='SOPH'"
            ).fetchone()
        self.assertEqual(dict(state), {"in_breakout": 1, "last_alert_at": self.now})

    def test_missing_intraday_structure_uses_hourly_confirmation_for_price_near_high(self):
        highs = [92, 94, 96, 98, 100, 99, 97, 96, 95, 94, 94.2, 94.5,
                 94.8, 95.2, 95.6, 96, 96.4, 96.8, 97.2, 97.6, 98, 98.4,
                 98.8, 99.5]
        hourly = [
            (
                self.now - (24 - index) * 3_600_000,
                high,
                99 if index == len(highs) - 1 else high - 0.5,
                high - 1,
                high - 2,
                100,
            )
            for index, high in enumerate(highs)
        ]
        def candles(*args, **kwargs):
            if kwargs.get("interval") == "15m":
                raise TimeoutError("structure unavailable")
            return hourly, "Binance Futures"
        with patch.object(server, "price_watch_candles_from_binance", side_effect=candles), \
             patch.object(server, "price_watch_daily_candles_from_binance", return_value=([], "test")), \
             patch.object(server, "price_watch_extended_snapshot_providers", return_value=[]), \
             patch.object(server, "price_watch_main_wave_stage_context", return_value={
                 "qualified": True, "stage": "active", "reason": "test-main-wave",
             }), \
             patch.object(server, "PRICE_STRUCTURE_PROVIDER_PREFERENCE", {}):
            result = server.fetch_price_watch_snapshot({"symbol": "SOPH"})
        self.assertEqual(result["status"], "near")
        self.assertTrue(result["structure"]["qualified"])
        self.assertTrue(result["structure"]["priorHighConfirmed"])
        self.assertEqual(result["structure"]["type"], "structure-level")

    def test_verified_market_resolution_is_persisted_with_stage_high(self):
        snapshot = self.snapshot(0.03851, week_high=0.04421, observed_high=0.04421)
        snapshot.update({
            "provider": "Binance Futures",
            "resolvedMarket": {
                "sourceLabel": "Binance 合约",
                "pair": "哈基米USDT",
                "listedAt": self.now,
            },
        })
        snapshot["structure"].update({
            "referenceHigh": 0.04421,
            "referenceKind": "MAIN_WAVE_STAGE_HIGH",
            "levelClass": "SWING",
        })

        server.update_price_watch_snapshot(snapshot, persist_alerts=False)

        with server.auth_db() as conn:
            asset = conn.execute(
                """
                SELECT provider, week_high, new_contract_source, new_contract_pair
                FROM price_watch_assets WHERE symbol='SOPH'
                """
            ).fetchone()
        self.assertEqual(asset["provider"], "Binance Futures")
        self.assertEqual(asset["week_high"], 0.04421)
        self.assertEqual(asset["new_contract_source"], "Binance 合约")
        self.assertEqual(asset["new_contract_pair"], "哈基米USDT")

    def test_popup_says_breakout_not_consolidation(self):
        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            result = server.launch_price_watch_alert({**self.snapshot(102), "priorHighBreakout": True})
        self.assertIn("已突破", result["title"])
        self.assertNotIn("盘整", result["body"])


if __name__ == "__main__":
    unittest.main()
