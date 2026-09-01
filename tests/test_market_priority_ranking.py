import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import server


class MarketPriorityRankingTests(unittest.TestCase):
    def source(self, source_id, rows, group="crypto"):
        return {
            "id": source_id,
            "title": f"{source_id} 热门榜",
            "sourceLabel": source_id[:2].upper(),
            "group": group,
            "status": "ok",
            "rows": rows,
        }

    def row(self, rank, symbol, heat, amount, tags=None):
        return {
            "rank": rank,
            "symbol": symbol,
            "name": symbol,
            "heat": heat,
            "amount": amount,
            "turnover": server.money_usd(amount),
            "change": "+5.00%",
            "tags": tags or [],
            "note": "",
        }

    def test_cross_board_narrative_asset_is_deduplicated_and_ranked_first(self):
        now_ms = 1_800_000_000_000
        sources = [
            self.source("binance", [
                self.row(4, "TRUMP", 72, 80_000_000, ["Meme", "文化事件"]),
                self.row(1, "RANDOM", 100, 120_000_000),
            ]),
            self.source("okx", [self.row(2, "TRUMP", 84, 90_000_000, ["Meme"])]),
        ]
        assets = server.market_priority_current_assets(sources)
        history = [{
            "observedAt": now_ms - 30 * 60_000,
            "rows": [
                {"assetKey": "CRYPTO:TRUMP", "sourceId": "binance", "rank": 9, "heat": 50},
                {"assetKey": "CRYPTO:TRUMP", "sourceId": "okx", "rank": 7, "heat": 55},
                {"assetKey": "CRYPTO:RANDOM", "sourceId": "binance", "rank": 1, "heat": 100},
            ],
        }]
        boosts = {
            "CRYPTO:TRUMP": {
                "personalX": 4,
                "newsTrade": 8,
                "reasons": ["个人 X 近期提及", "News Trade 主题映射"],
            }
        }

        payload = server.market_priority_window_rows(assets, history, "1h", now_ms, boosts)

        self.assertEqual(payload["rows"][0]["symbol"], "TRUMP")
        self.assertEqual(payload["rows"][0]["sourceCount"], 2)
        self.assertEqual(sum(1 for row in payload["rows"] if row["symbol"] == "TRUMP"), 1)
        self.assertGreater(payload["rows"][0]["narrativeScore"], payload["rows"][1]["narrativeScore"])
        self.assertIn("2 个榜单叙事共振", payload["rows"][0]["priorityReasons"])
        self.assertEqual(payload["rows"][0]["priorityScore"], payload["rows"][0]["narrativeScore"])

    def test_narrative_strength_beats_raw_heat_and_turnover(self):
        now_ms = 1_800_000_000_000
        sources = [self.source("wallet", [
            self.row(1, "NO_STORY", 100, 500_000_000),
            self.row(8, "CULTURE", 30, 2_000_000, ["Meme", "文化事件"]),
        ])]
        assets = server.market_priority_current_assets(sources)

        payload = server.market_priority_window_rows(assets, [], "24h", now_ms, {})

        self.assertEqual(payload["rows"][0]["symbol"], "CULTURE")
        self.assertGreater(payload["rows"][0]["narrativeScore"], payload["rows"][1]["narrativeScore"])
        self.assertGreater(payload["rows"][1]["recentHeatScore"], payload["rows"][0]["recentHeatScore"])

    def test_period_window_changes_rank_momentum_without_changing_current_rows(self):
        now_ms = 1_800_000_000_000
        sources = [self.source("aicoin", [self.row(2, "HYPE", 88, 40_000_000, ["链上交易平台"])], "aicoin")]
        assets = server.market_priority_current_assets(sources)
        history = [
            {
                "observedAt": now_ms - 5 * 60 * 60_000,
                "rows": [{"assetKey": "CRYPTO:HYPE", "sourceId": "aicoin", "rank": 10, "heat": 45}],
            },
            {
                "observedAt": now_ms - 30 * 60_000,
                "rows": [{"assetKey": "CRYPTO:HYPE", "sourceId": "aicoin", "rank": 5, "heat": 70}],
            },
        ]

        one_hour = server.market_priority_window_rows(assets, history, "1h", now_ms, {})["rows"][0]
        six_hour = server.market_priority_window_rows(assets, history, "6h", now_ms, {})["rows"][0]

        self.assertEqual(one_hour["rankChange"], 3)
        self.assertEqual(six_hour["rankChange"], 8)
        self.assertGreater(six_hour["recentHeatScore"], one_hour["recentHeatScore"])

    def test_successful_market_refresh_records_a_bounded_snapshot_immediately(self):
        sources = [self.source("binance", [self.row(1, "HYPE", 100, 50_000_000, ["链上交易平台"])])]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rank-monitor.json"
            with patch.object(server, "RANK_MONITOR_STATE_PATH", path):
                server.market_priority_record_snapshot(sources, now_ms=1_800_000_000_000)
                server.market_priority_record_snapshot(sources, now_ms=1_800_000_120_000)
                first_state = server.read_json_cache(path)
                server.market_priority_record_snapshot(sources, now_ms=1_800_000_500_000)
                second_state = server.read_json_cache(path)

        self.assertEqual(len(first_state["marketPriorityHistory"]), 1)
        self.assertEqual(len(second_state["marketPriorityHistory"]), 2)
        self.assertEqual(second_state["marketPriorityHistory"][-1]["rows"][0]["assetKey"], "CRYPTO:HYPE")


if __name__ == "__main__":
    unittest.main()
