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


def gmgn_source(rows, chain=server.GMGN_HOT_SEARCH_ALERT_CHAIN):
    """Build a hot-search source with ONE 5m board (Robinhood by default).

    The alert feed reads the Robinhood 5-minute board only, so the aggregate
    ``all`` board the real payload also carries must be irrelevant here.
    """
    return {
        "id": "gmgn-hot-search",
        "title": "GMGN 热搜榜",
        "group": "crypto",
        "periodBoards": [
            {"chain": chain, "label": chain, "period": "5m", "status": "ok", "rows": rows},
        ],
    }


def board_row(symbol, contract, rank):
    return {
        "symbol": symbol,
        "name": symbol,
        "chain": "robinhood",
        "chainId": "4663",
        "chainLabel": "Robinhood Chain",
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


class GmgnHotSearchAlertBoardTests(unittest.TestCase):
    """弹窗只观测 Robinhood 单链 5m 板，不观测「综合」板（2026-10-07 用户拍板）。"""

    def alert_rows(self, source):
        return server.gmgn_hot_search_5m_alert_rows(source)

    def test_robinhood_5m_board_is_the_observed_board(self):
        rows = [board_row("VAUL", "0xabc", 1)]
        board = gmgn_source(rows)
        snaps = self.alert_rows(board)
        self.assertEqual([row["symbol"] for row in snaps], ["VAUL"])
        self.assertEqual(snaps[0]["period"], server.GMGN_HOT_SEARCH_ALERT_PERIOD)
        self.assertEqual(snaps[0]["chain"], "robinhood")
        self.assertEqual(snaps[0]["chainLabel"], "Robinhood Chain")

    def test_aggregate_board_is_ignored(self):
        # 综合板被 sol/bsc 主导，robinhood 几乎挤不进前 10 —— 它的新进不该弹。
        snaps = self.alert_rows(gmgn_source([board_row("SOLONLY", "0xsol", 1)], chain="all"))
        self.assertEqual(snaps, [])

    def test_other_chains_and_periods_are_ignored(self):
        source = {
            "id": "gmgn-hot-search",
            "title": "GMGN 热搜榜",
            "group": "crypto",
            "periodBoards": [
                {"chain": "sol", "period": "5m", "status": "ok", "rows": [board_row("S", "0xs", 1)]},
                {"chain": "robinhood", "period": "1h", "status": "ok", "rows": [board_row("H", "0xh", 1)]},
            ],
        }
        self.assertEqual(self.alert_rows(source), [])

    def test_a_different_source_id_is_ignored(self):
        source = dict(gmgn_source([board_row("VAUL", "0xabc", 1)]), id="ave")
        self.assertEqual(self.alert_rows(source), [])

    def test_chain_label_falls_back_to_robinhood_when_the_row_omits_it(self):
        rows = [dict(board_row("VAUL", "0xabc", 1), chainLabel="")]
        snaps = self.alert_rows(gmgn_source(rows))
        self.assertEqual(snaps[0]["chainLabel"], server.GMGN_HOT_SEARCH_ALERT_CHAIN)

    def test_event_title_names_the_chain(self):
        snap = self.alert_rows(gmgn_source([board_row("VAUL", "0xabc", 1)]))[0]
        event = server.rank_monitor_event("hot", snap, "new")
        self.assertEqual(event["alertPeriod"], server.GMGN_HOT_SEARCH_ALERT_PERIOD)
        self.assertIn("Robinhood", event["title"])
        self.assertTrue(event["kind"].endswith("新进"))
        # 这条事件必须能穿过 GMGN 热搜榜的静音闸门，否则弹不出来。
        self.assertFalse(server.desktop_alert_source_is_muted(event))


if __name__ == "__main__":
    unittest.main()
