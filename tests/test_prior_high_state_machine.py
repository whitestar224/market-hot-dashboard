import unittest

from prior_high_monitor import (
    Candle,
    Level,
    PriorHighConfig,
    _select_stage_high_level,
    analyze_prior_high,
)


def candle(index, open_price, high, low, close):
    return {
        "time": (index + 1) * 60_000,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
    }


class PriorHighStateMachineTests(unittest.TestCase):
    def config(self):
        return PriorHighConfig(
            atr_period=3,
            pivot_left=2,
            pivot_right=2,
            min_prominence_atr=0.35,
            internal_prominence_atr=0.5,
            swing_prominence_atr=1,
            major_prominence_atr=2,
            temporal_reset_bars=3,
            temporal_below_ratio=0.66,
            spatial_reset_atr=0.8,
            spatial_reset_pct=0.01,
            trigger_buffer_atr=0,
            trigger_buffer_pct=0,
            alert_micro=True,
        )

    def test_separation_fresh_cross_and_rearm_match_go_strategy(self):
        rows = [
            candle(0, 95, 96, 94, 95), candle(1, 96, 98, 95, 97),
            candle(2, 98, 100, 97, 99), candle(3, 98, 99, 96, 97),
            candle(4, 96, 98, 94, 95), candle(5, 95, 97, 93, 94),
            candle(6, 94, 98, 93, 97), candle(7, 97, 101, 96, 100.5),
            candle(8, 100.5, 102, 100, 101), candle(9, 101, 102, 99.5, 100.5),
            candle(10, 100, 100.2, 98, 99), candle(11, 99, 99.5, 97.5, 98.5),
            candle(12, 98.5, 101.5, 98, 101),
        ]
        result = analyze_prior_high(rows, "5m", self.config())
        same_level = [event for event in result["breakouts"] if event["anchor"] == 100]
        self.assertEqual([event["barIndex"] for event in same_level], [7])
        self.assertEqual([event["breakoutCount"] for event in same_level], [1])

    def test_mother_range_hides_internal_crosses_until_outer_edge_breaks(self):
        rows = [
            candle(0, 100, 102, 99, 101), candle(1, 101, 105, 100, 104),
            candle(2, 104, 110, 103, 108), candle(3, 108, 106, 102, 104),
            candle(4, 104, 103, 100, 101), candle(5, 101, 102, 99, 100),
            candle(6, 100, 105, 99.5, 104), candle(7, 104, 103, 100, 101),
            candle(8, 101, 102, 99, 100), candle(9, 100, 106, 99.5, 105),
            candle(10, 105, 104, 101, 102), candle(11, 102, 103, 100, 101),
            candle(12, 101, 111, 100.5, 110.5),
        ]
        result = analyze_prior_high(rows, "15m", self.config())
        self.assertEqual([event["barIndex"] for event in result["breakouts"]], [12])
        self.assertEqual(result["breakouts"][0]["anchor"], 110)
        self.assertTrue(all(event["anchor"] == 110 for event in result["reminders"]))

    def test_current_actionable_level_exposes_reference_without_old_context_gates(self):
        rows = [
            candle(0, 95, 96, 94, 95), candle(1, 96, 98, 95, 97),
            candle(2, 98, 100, 97, 99), candle(3, 98, 99, 96, 97),
            candle(4, 96, 97, 93, 94), candle(5, 94, 96, 93, 95),
            candle(6, 95, 96.5, 94, 96), candle(7, 96, 97, 95, 96.5),
            candle(8, 96.5, 98.4, 96, 98),
        ]
        result = analyze_prior_high(rows, "1h", self.config())
        self.assertEqual(result["actionableLevel"]["anchor"], 100)
        self.assertTrue(result["actionableLevel"]["armed"])
        self.assertTrue(result["reminders"])

    def test_main_wave_stage_high_beats_nearer_internal_noise(self):
        candles = [
            Candle(index, 40, high, 35, close, 100)
            for index, (high, close) in enumerate([
                (41, 40), (44.88, 43), (42, 39), (40.63, 39.5),
                (44.21, 41), (41, 39), (40.5, 40.2),
            ])
        ]
        swing = Level(
            level_id=1, native_tf="1h", level_class="SWING",
            anchor=44.88, zone_low=44.21, zone_high=44.88,
            created_bar=1, confirmed_bar=3, created_time=1,
            confirmed_time=3, atr_at_confirm=2, prominence_atr=2.3,
            touch_count=2, spatial_separated=True,
            separation_complete=True, armed=True, source_pivots=[1, 4],
        )
        internal = Level(
            level_id=2, native_tf="1h", level_class="INTERNAL",
            anchor=40.63, zone_low=40.63, zone_high=40.63,
            created_bar=3, confirmed_bar=5, created_time=3,
            confirmed_time=5, atr_at_confirm=2, prominence_atr=1.2,
            spatial_separated=True, separation_complete=True,
            armed=True, source_pivots=[3],
        )
        later_lower_swing = Level(
            level_id=3, native_tf="1h", level_class="SWING",
            anchor=43.44, zone_low=43.44, zone_high=43.44,
            created_bar=5, confirmed_bar=6, created_time=5,
            confirmed_time=6, atr_at_confirm=2, prominence_atr=2.0,
            spatial_separated=True, separation_complete=True,
            armed=True, source_pivots=[5],
        )

        selected = _select_stage_high_level(
            [internal, later_lower_swing, swing], candles, self.config()
        )

        self.assertEqual(selected["levelClass"], "SWING")
        self.assertEqual(selected["referenceAnchor"], 44.21)
        self.assertEqual(selected["referenceBar"], 4)
        self.assertEqual(selected["referenceKind"], "STAGE_HIGH_CANDIDATE")


if __name__ == "__main__":
    unittest.main()
