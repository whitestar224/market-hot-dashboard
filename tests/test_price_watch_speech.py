import unittest
from unittest.mock import patch

import server


class PriceWatchSpeechTests(unittest.TestCase):
    def test_prior_high_alert_includes_spoken_summary(self):
        event = {
            "symbol": "HYPE",
            "distancePct": 2.46,
            "currentPrice": 42,
            "weekHigh": 43,
            "episode": 2,
            "checkedAt": 1_786_000_000_000,
            "provider": "Binance Futures",
        }

        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertEqual(payload["speech"], "前高预警，HYPE 已接近最近七日前高，距离 2.5%。")
        self.assertTrue(payload.get("sound", True))

    def test_oversold_alert_includes_spoken_summary(self):
        event = {
            "eventType": "oversold_rebound",
            "symbol": "TEST",
            "distancePct": 1.24,
            "currentPrice": 49,
            "weekHigh": 100,
            "drawdownPct": 51,
            "rangeLow": 40,
            "rangeHigh": 50,
            "episode": 1,
            "checkedAt": 1_786_000_000_000,
            "provider": "OKX Futures",
        }

        with patch.object(server, "launch_desktop_alert", side_effect=lambda payload: payload):
            payload = server.launch_price_watch_alert(event)

        self.assertEqual(payload["speech"], "超跌反弹预警，TEST 已接近低位阶段高点，距离 1.2%。")

    def test_normalization_preserves_speech(self):
        payload = server.normalize_desktop_alert({"title": "TEST", "speech": "前高预警"})
        self.assertEqual(payload["speech"], "前高预警")


if __name__ == "__main__":
    unittest.main()
