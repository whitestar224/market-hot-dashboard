import unittest
from unittest.mock import patch

import server


class StrategyBoardIntegrationTests(unittest.TestCase):
    def dragon_item(self, *, breakout_low=90.0, pending=False):
        signal = {
            "id": "1h-1787455000000-breakout",
            "interval": "1h",
            "grade": "A+",
            "certainty": 96,
            "score": 94,
            "pattern": "盘整突破 + 拐点再启动",
            "price": 100.0,
            "triggerPrice": 99.5,
            "breakoutOpen": 98.5,
            "breakoutLow": breakout_low,
            "decisionTime": 1787455000000,
            "time": 1787455000000,
        }
        return {
            "symbol": "COAI",
            "name": "COAI",
            "icon": "",
            "provider": "Binance Futures",
            "currentPrice": 100.0,
            "monitorPool": "new-coin-low",
            "strategy": "dragon-wave-engine",
            "strategyVersion": "v73",
            "checkedAt": 1787455001000,
            "frames": [{
                "key": "1h",
                "label": "1小时",
                "pattern": signal["pattern"],
                "stage": "预备起爆" if pending else "买点触发",
                "confidence": 96,
                "signal": None if pending else signal,
                "pending": signal if pending else None,
            }],
            "signals": [] if pending else [signal],
        }

    def test_stop_uses_breakout_candle_low_when_distance_is_at_least_three_percent(self):
        plan = server.strategy_stop_loss_plan(100, 94)

        self.assertEqual(plan["stopLossPrice"], 94)
        self.assertEqual(plan["stopDistancePct"], 6)
        self.assertEqual(plan["stopLossSource"], "breakout-candle-low")

    def test_stop_widens_small_breakout_candle_noise_to_three_percent(self):
        plan = server.strategy_stop_loss_plan(100, 98.5)

        self.assertEqual(plan["stopLossPrice"], 97)
        self.assertEqual(plan["stopDistancePct"], 3)
        self.assertEqual(plan["stopLossSource"], "default-3pct")

    def test_shared_engine_signal_enters_strategy_board_as_executable(self):
        with patch.object(server, "price_watch_active_rows", return_value=[]), patch.object(
            server, "strategy_dragon_wave_items", return_value={"COAI": self.dragon_item()}
        ):
            rows = server.strategy_board_signals()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "COAI")
        self.assertTrue(rows[0]["strategyReady"])
        self.assertEqual(rows[0]["phase"], 4)
        self.assertEqual(rows[0]["strategyPattern"], "盘整突破 + 拐点再启动")
        self.assertEqual(rows[0]["strategyBreakoutLow"], 90)
        self.assertEqual(rows[0]["strategyStopLossPrice"], 90)
        self.assertEqual(rows[0]["strategyStopLossSourceLabel"], "突破K低点")

    def test_prediction_is_visible_but_cannot_be_submitted_as_confirmed_signal(self):
        item = self.dragon_item(breakout_low=98.5, pending=True)
        with patch.object(server, "strategy_asset_row", return_value={
            "symbol": "COAI",
            "name": "COAI",
            "current_price": 100.0,
            "week_high": 99.5,
            "strategy_pending": item["frames"][0]["pending"],
            "strategy_engine": "dragon-wave-engine",
            "strategy_version": "v73",
        }):
            preview = server.strategy_order_preview({"symbol": "COAI", "totalCapital": 10_000})

        self.assertFalse(preview["signalConfirmed"])
        self.assertTrue(preview["strategySignalPending"])
        self.assertEqual(preview["stopLossPrice"], 97)
        self.assertEqual(preview["stopLossSource"], "default-3pct")

    def test_board_downgrades_signal_after_current_price_breaks_its_candle_low(self):
        item = self.dragon_item(breakout_low=101)
        with patch.object(server, "price_watch_active_rows", return_value=[]), patch.object(
            server, "strategy_dragon_wave_items", return_value={"COAI": item}
        ):
            rows = server.strategy_board_signals()

        self.assertFalse(rows[0]["strategyReady"])
        self.assertIn("只保留复盘", rows[0]["strategySignalInvalidReason"])

    def test_triggered_signal_preview_uses_breakout_low_and_is_confirmed(self):
        signal = self.dragon_item(breakout_low=94)["signals"][0]
        with patch.object(server, "strategy_asset_row", return_value={
            "symbol": "COAI",
            "name": "COAI",
            "current_price": 100.0,
            "week_high": 99.5,
            "strategy_signal": signal,
            "strategy_engine": "dragon-wave-engine",
            "strategy_version": "v73",
        }):
            preview = server.strategy_order_preview({"symbol": "COAI", "totalCapital": 10_000})

        self.assertTrue(preview["signalConfirmed"])
        self.assertEqual(preview["signalEpisode"], signal["decisionTime"])
        self.assertEqual(preview["breakoutPrice"], 99.5)
        self.assertEqual(preview["stopLossPrice"], 94)
        self.assertEqual(preview["stopLossSourceLabel"], "突破K低点")

    def test_triggered_signal_is_blocked_after_price_falls_below_breakout_low(self):
        signal = self.dragon_item(breakout_low=101)["signals"][0]
        with patch.object(server, "strategy_asset_row", return_value={
            "symbol": "COAI",
            "name": "COAI",
            "current_price": 100.0,
            "week_high": 99.5,
            "strategy_signal": signal,
            "strategy_engine": "dragon-wave-engine",
        }):
            preview = server.strategy_order_preview({"symbol": "COAI", "totalCapital": 10_000})

        self.assertTrue(preview["blocked"])
        self.assertIn("跌破突破K低点", preview["blockedReason"])
        self.assertEqual(preview["notional"], 0)


if __name__ == "__main__":
    unittest.main()
