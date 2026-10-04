import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


SLEUTHY = "3XBKc5N6ecPdxSBxPRSoBzZ4wWiEqTvxVmJyQPentRD8"
PERCY = "2pA25N1kJdFMo5YApiykMsHwyDxRNbB62CTxsR1niZ5F"
STIMMY = "7CHR2LVw4kwbR7p1ZRkdDgfySjMWtqXzXUNmrQ8RPUMP"


def gmgn_source(rows):
    return {
        "id": "gmgn-hot-search",
        "title": "GMGN 热搜榜",
        "group": "crypto",
        "periodBoards": [
            {"chain": "all", "period": "5m", "status": "ok", "rows": rows},
        ],
    }


def board_row(symbol, contract, rank):
    return {
        "symbol": symbol,
        "name": symbol,
        "chain": "sol",
        "chainId": "CT_501",
        "chainLabel": "Solana",
        "contractAddress": contract,
        "rank": rank,
        "price": "0.00020679",
        "change": "+108.47%",
        "amount": "$160.34K",
    }


class GmgnHotSearchAlertWarmupTests(unittest.TestCase):
    """服务重启后不得补弹停机期间积压的「GMGN 5 分钟热搜榜新进」。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp.name) / "gmgn_hot_search_alert_state.json"
        self.events = []
        self.ingested = []

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, *, boot_id, membership=(), last_alerts=None, last_seen=None, updated_at=None):
        self.state_path.write_text(
            json.dumps({
                "version": server.GMGN_HOT_SEARCH_ALERT_STATE_VERSION,
                "ready": True,
                "period": server.GMGN_HOT_SEARCH_ALERT_PERIOD,
                "bootId": boot_id,
                "updatedAt": int(updated_at if updated_at is not None else time.time() * 1000),
                "membership": list(membership),
                "lastAlerts": dict(last_alerts or {}),
                "lastSeen": dict(last_seen or {}),
            }),
            encoding="utf-8",
        )

    def read_state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def sync(self, rows, boot_ms):
        with patch.object(server, "GMGN_HOT_SEARCH_ALERT_STATE_PATH", self.state_path), \
                patch.object(server, "SERVER_BOOT_AT_MS", boot_ms), \
                patch.object(server, "persist_alert_events", side_effect=self.events.extend), \
                patch.object(
                    server,
                    "sync_price_watch_gmgn_hot_search_candidates",
                    side_effect=lambda rows=None, **kwargs: self.ingested.append(list(rows or [])),
                ):
            return server.sync_gmgn_hot_search_alert_feed(gmgn_source(rows))

    def test_restart_first_poll_seeds_baseline_without_popups(self):
        # 上一次进程留下的状态：membership 为空（隔夜停机后榜单已全部换血），bootId 是旧进程的
        self.write_state(boot_id="1791000000000", updated_at=time.time() * 1000 - 10 * 3600 * 1000)
        rows = [
            board_row("SLEUTHY", SLEUTHY, 1),
            board_row("PERCY", PERCY, 2),
            board_row("STIMMY", STIMMY, 9),
        ]

        events = self.sync(rows, 1791081000000)

        self.assertEqual(events, [])
        state = self.read_state()
        self.assertEqual(state["bootId"], "1791081000000")
        self.assertEqual(len(state["membership"]), 3)
        # 基线行仍要进监控池（否则这些币就永远不进结构监控了）
        self.assertEqual(len(self.ingested[0]), 3)
        self.assertEqual({row["symbol"] for row in self.ingested[0]}, {"SLEUTHY", "PERCY", "STIMMY"})

    def test_first_ever_run_is_silent_and_feeds_the_pool(self):
        events = self.sync([board_row("PERCY", PERCY, 1)], 1791081000000)

        self.assertEqual(events, [])
        self.assertEqual(len(self.ingested[0]), 1)
        self.assertTrue(self.read_state()["ready"])

    def test_later_poll_in_same_run_alerts_only_genuinely_new_rows(self):
        rows = [board_row("SLEUTHY", SLEUTHY, 1), board_row("PERCY", PERCY, 2)]
        self.sync(rows, 1791081000000)
        self.events.clear()
        self.ingested.clear()

        events = self.sync(rows + [board_row("STIMMY", STIMMY, 9)], 1791081000000)

        self.assertEqual(len(events), 1)
        self.assertIn("STIMMY", events[0]["title"])
        self.assertEqual([row["symbol"] for row in self.ingested[0]], ["STIMMY"])

    def test_next_restart_does_not_replay_current_members(self):
        rows = [board_row("SLEUTHY", SLEUTHY, 1), board_row("PERCY", PERCY, 2)]
        self.sync(rows, 1791081000000)
        self.events.clear()

        # 新进程启动（bootId 变化），榜单与停机前一致：一条都不该补弹
        events = self.sync(rows, 1791089999999)

        self.assertEqual(events, [])
        self.assertEqual(self.read_state()["bootId"], "1791089999999")

    def test_rows_alerted_in_the_last_window_stay_silent_across_a_restart(self):
        key = server.rank_monitor_asset_key(gmgn_source([]), board_row("SLEUTHY", SLEUTHY, 1))
        self.write_state(
            boot_id="1791000000000",
            last_alerts={f"5m:new:{key}": time.time() - 600},
        )

        events = self.sync([board_row("SLEUTHY", SLEUTHY, 1)], 1791081000000)

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
