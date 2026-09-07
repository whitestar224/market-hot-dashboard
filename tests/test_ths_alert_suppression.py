import unittest
from collections import deque
from unittest.mock import patch

import server


class TonghuashunAlertSuppressionTests(unittest.TestCase):
    def test_ths_is_rejected_before_queue_dedupe_intake_or_speech(self):
        for source_fields in (
            {"sourceId": "ths-cn"},
            {"sourceLabel": "THS"},
            {"source": "A股同花顺24h热门榜"},
            {"source": "THS"},
        ):
            with (
                self.subTest(source_fields=source_fields),
                patch.object(server, "env_flag", return_value=False),
                patch.object(server, "DESKTOP_ALERT_QUEUE", deque()) as queue,
                patch.object(server, "claim_desktop_alert_marker") as marker,
                patch.object(server, "record_desktop_alert_news_trade_intake") as intake,
                patch.object(server, "spawn_desktop_alert_process") as spawn,
            ):
                result = server.launch_desktop_alert({
                    "key": "market-leader:ths-cn:601086:1",
                    "kind": "榜首换手",
                    "title": "THS 榜首变为 国芳集团",
                    "speech": "同花顺榜首换手",
                    **source_fields,
                })
                self.assertTrue(result["ok"])
                self.assertTrue(result["skipped"])
                self.assertEqual(result["category"], "muted-source")
                self.assertEqual(len(queue), 0)
                marker.assert_not_called()
                intake.assert_not_called()
                spawn.assert_not_called()

    def test_other_sources_and_news_merely_mentioning_ths_are_not_muted(self):
        for label in ("BN", "AI", "BW", "OK", "BB"):
            with self.subTest(label=label):
                item = server.normalize_desktop_alert({
                    "sourceLabel": label,
                    "source": "其他市场来源",
                    "title": "同花顺发布新项目",
                    "body": "同花顺 THS 相关市场信息",
                })
                self.assertFalse(server.desktop_alert_source_is_muted(item))

    def market_payload(self):
        return {"sources": [
            {
                "id": "ths-cn", "sourceLabel": "THS", "title": "A股同花顺24h热门榜", "group": "cn",
                "rows": [{"rank": 1, "symbol": "601086", "name": "国芳集团", "change": "+3.60%"}],
            },
            {
                "id": "binance", "sourceLabel": "BN", "title": "Binance 热门币种", "group": "crypto",
                "rows": [{"rank": 1, "symbol": "BTC", "name": "Bitcoin", "change": "+2.00%"}],
            },
        ]}

    def test_leader_alerts_skip_ths_without_removing_ranking_data(self):
        payload = self.market_payload()
        events = server.parse_site_market_events(payload)
        self.assertEqual([item["sourceLabel"] for item in events], ["BN"])
        self.assertEqual(len(payload["sources"]), 2)
        self.assertEqual(payload["sources"][0]["rows"][0]["symbol"], "601086")

    def test_rank_watch_skips_ths_but_keeps_its_data(self):
        with patch.object(server, "is_excluded_crypto_asset", return_value=False):
            rows = server.rank_monitor_market_rows(self.market_payload())
        self.assertEqual(len(rows), 2)
        for board in ("hot", "turnover"):
            with self.subTest(board=board):
                watched = server.rank_monitor_watch_rows(board, rows)
                self.assertEqual([item["sourceId"] for item in watched], ["binance"])

    def test_worker_discards_queued_ths_but_delivers_other_sources(self):
        muted = server.normalize_desktop_alert({"sourceLabel": "THS", "title": "榜首换手"})
        allowed = server.normalize_desktop_alert({"sourceLabel": "BN", "title": "榜首换手"})
        with (
            patch.object(server, "DESKTOP_ALERT_QUEUE", deque([muted, allowed])) as queue,
            patch.object(server, "DESKTOP_ALERT_QUEUE_ACTIVE", True),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_AT", 0),
            patch.object(server, "DESKTOP_ALERT_LAST_LAUNCHED_PRIORITY", 0),
            patch.object(server, "DESKTOP_ALERT_ACTIVE_PROCESS", None),
            patch.object(server, "desktop_alert_interval_seconds", return_value=0),
            patch.object(server, "spawn_desktop_alert_process") as spawn,
        ):
            server.desktop_alert_queue_worker()
            self.assertEqual(len(queue), 0)
            self.assertFalse(server.DESKTOP_ALERT_QUEUE_ACTIVE)
            spawn.assert_called_once_with(allowed, 0)


if __name__ == "__main__":
    unittest.main()
