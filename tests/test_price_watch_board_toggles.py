"""Per-board switches behind the 榜单页 (index.html) leaderboard cards.

Every market source a user sees as a card on the 榜单页 owns two switches:

* ``intake`` — keep taking NEW items from this board into the monitor pool
  (existing pool members are never touched).
* ``alert``  — keep popping desktop alerts for this board.

A board with no stored DB row keeps its declared default, so an untouched
install behaves exactly as before — except the GMGN 5-minute hot-search board,
whose popup switch ships OFF (2026-10-06 用户拍板: its 「新进」 popup is noise
until explicitly enabled).

There is exactly ONE suppression choke point per direction:

* alerts  — every producer funnels through ``persist_alert_events`` ->
  ``launch_desktop_alert`` -> ``price_watch_alert_board_muted``.
* intake  — inside each source's own ``sync_price_watch_*_candidates`` function,
  so no caller can bypass it.
"""

import gc
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


# Mirrors market_payload()'s fetcher list — the cards rendered on the 榜单页.
ALL_BOARDS = (
    "binance-wallet-hot", "gmgn-trenches", "aicoin", "binance", "gmgn-hot-search",
    "bitget", "futu-hk", "ave", "okx", "okx-dex", "ths-cn", "futu-us",
)
# Only these three really admit into the price monitor pool.
INTAKE_BOARDS = {"binance-wallet-hot", "aicoin", "gmgn-hot-search"}


class PriceWatchBoardToggleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.temp_dir.name) / "auth.db"
        server.init_auth_db()
        self.reset_cache()

    def tearDown(self):
        self.reset_cache()
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.temp_dir.cleanup()

    @staticmethod
    def reset_cache():
        with server.PRICE_WATCH_BOARD_TOGGLE_LOCK:
            server.PRICE_WATCH_BOARD_TOGGLE_CACHE = None

    # -- roster -----------------------------------------------------------

    def test_roster_matches_the_leaderboard_cards(self):
        boards = server.price_watch_board_payload()
        self.assertEqual([board["id"] for board in boards], list(ALL_BOARDS))
        self.assertEqual(list(server.PRICE_WATCH_BOARD_IDS), list(ALL_BOARDS))
        for board in boards:
            self.assertTrue(board["label"], board["id"])
            self.assertTrue(board["short"], board["id"])
            defaults = server.PRICE_WATCH_BOARD_DEFAULTS[board["id"]]
            self.assertEqual(board["intake"], defaults["intake"], board["id"])
            self.assertEqual(board["alert"], defaults["alert"], board["id"])
            self.assertEqual(board["intakeApplies"], board["id"] in INTAKE_BOARDS, board["id"])

    def test_roster_stays_glued_to_the_market_payload_fetchers(self):
        """The roster is keyed by market source id — the two must never drift.

        A card that renders without a roster entry silently loses its switches,
        and a roster entry with no card becomes unreachable, so the card list is
        parsed from the real ``market_payload()`` fetchers block instead of
        being trusted to a hardcoded copy.
        """
        source = Path(server.__file__).read_text(encoding="utf-8")
        block = source[source.index("def market_payload("):]
        block = block[block.index("fetchers = ["):]
        block = block[: block.index("\n    ]")]
        self.assertEqual(re.findall(r'\(\s*"([a-z0-9-]+)"\s*,', block), list(ALL_BOARDS))

    def test_only_the_gmgn_hot_search_popup_ships_off(self):
        self.assertFalse(server.price_watch_board_alert_enabled("gmgn-hot-search"))
        self.assertTrue(server.price_watch_board_intake_enabled("gmgn-hot-search"))
        for board_id in ALL_BOARDS:
            if board_id == "gmgn-hot-search":
                continue
            self.assertTrue(server.price_watch_board_alert_enabled(board_id), board_id)
            self.assertTrue(server.price_watch_board_intake_enabled(board_id), board_id)

    def test_unknown_board_and_kind_are_rejected(self):
        # "prior" was a board before the switches moved onto the 榜单页 cards.
        with self.assertRaises(ValueError):
            server.set_price_watch_board_toggle("prior", "alert", False)
        with self.assertRaises(ValueError):
            server.set_price_watch_board_toggle("aicoin", "sideways", False)

    # -- persistence ------------------------------------------------------

    def test_toggle_persists_and_only_touches_its_own_kind(self):
        result = server.set_price_watch_board_toggle("aicoin", "alert", False)
        self.assertTrue(result["ok"])
        self.assertFalse(server.price_watch_board_alert_enabled("aicoin"))
        self.assertTrue(server.price_watch_board_intake_enabled("aicoin"))
        # Another board is untouched.
        self.assertTrue(server.price_watch_board_alert_enabled("ave"))
        # A cold read from disk agrees with the live state.
        self.reset_cache()
        self.assertFalse(server.price_watch_board_alert_enabled("aicoin"))
        self.assertTrue(server.price_watch_board_intake_enabled("aicoin"))

    def test_board_payload_returned_by_the_setter_reflects_the_change(self):
        result = server.set_price_watch_board_toggle("okx-dex", "intake", False)
        entry = next(board for board in result["boards"] if board["id"] == "okx-dex")
        self.assertFalse(entry["intake"])
        self.assertTrue(entry["alert"])

    def test_a_broken_read_falls_back_to_the_declared_defaults(self):
        self.reset_cache()

        def boom(*_args, **_kwargs):
            raise RuntimeError("database unavailable")

        with patch.object(server, "auth_db", boom):
            toggles = server.price_watch_board_toggles()
        # A read failure must never mute the whole box: every default-on board
        # stays on.  The board that opted out by design keeps its declared
        # default rather than flipping to "on".
        for board_id, defaults in server.PRICE_WATCH_BOARD_DEFAULTS.items():
            self.assertEqual(toggles[board_id], defaults, board_id)
        self.assertTrue(server.price_watch_board_alert_enabled("aicoin"))
        self.assertFalse(server.price_watch_board_alert_enabled("gmgn-hot-search"))

    # -- alert routing ----------------------------------------------------

    def test_alert_payload_maps_back_to_its_board(self):
        for board_id in ALL_BOARDS:
            self.assertEqual(
                server.price_watch_alert_board({"key": "rank-monitor:hot:x:new", "sourceId": board_id}),
                board_id,
                board_id,
            )
        # An explicit board field wins over the source id.
        self.assertEqual(
            server.price_watch_alert_board({"key": "k", "sourceId": "ave", "board": "okx"}),
            "okx",
        )
        # Listing alerts carry venue ids (binance-new / okx-new); they must NOT be
        # mistaken for the 币安 / OKX 热门榜 cards.
        self.assertEqual(
            server.price_watch_alert_board({"key": "first-listing:x", "sourceId": "binance-new"}),
            "",
        )
        self.assertEqual(server.price_watch_alert_board({"key": "something-else"}), "")
        self.assertEqual(server.price_watch_alert_board(None), "")

    def test_gmgn_hot_search_popup_is_muted_by_default(self):
        item = {
            "key": "rank-monitor:hot:GMGN:solana:abc:new:1",
            "sourceId": "gmgn-hot-search",
            "alertPeriod": "5m",
            "kind": "GMGN 5 分钟热搜榜新进",
            "title": "GMGN 5 分钟热搜榜新进：ABC",
        }
        self.assertEqual(server.price_watch_alert_board_muted(item), "gmgn-hot-search")
        server.set_price_watch_board_toggle("gmgn-hot-search", "alert", True)
        self.assertEqual(server.price_watch_alert_board_muted(item), "")

    def test_desktop_alert_is_dropped_when_the_board_alert_switch_is_off(self):
        # Binance Wallet 4h 新进 is the one hot-board popup the delivery policy
        # deliberately lets through, so the board switch is what stops it.
        payload = {
            "key": "rank-monitor:hot:WALLET:solana:abc:new",
            "sourceId": "binance-wallet-hot",
            "alertPeriod": "4h",
            "kind": "币安钱包热门榜新进",
            "title": "币安钱包热门榜新进：ABC",
            "body": "进入币安钱包 4 小时热门榜",
        }
        self.assertFalse(server.desktop_alert_source_is_muted(payload))
        server.set_price_watch_board_toggle("binance-wallet-hot", "alert", False)
        with patch.object(server, "ingest_smart_money_text", lambda *args, **kwargs: None):
            result = server.launch_desktop_alert(payload)
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("category"), "muted-board")
        self.assertEqual(result.get("board"), "binance-wallet-hot")

    def test_desktop_alert_for_an_enabled_board_is_not_muted(self):
        self.assertEqual(
            server.price_watch_alert_board_muted({
                "key": "rank-monitor:hot:CRYPTO:BTC:new",
                "sourceId": "binance-wallet-hot",
            }),
            "",
        )

    # -- intake wiring ----------------------------------------------------

    def test_aicoin_intake_switch_gates_its_pool_discovery(self):
        source_mock = Mock(return_value={"rows": [], "status": "ok"})
        with patch.object(server, "price_watch_aicoin_source", source_mock):
            server.set_price_watch_board_toggle("aicoin", "intake", False)
            self.assertEqual(server.sync_price_watch_aicoin_candidates(), 0)
            source_mock.assert_not_called()
            server.set_price_watch_board_toggle("aicoin", "intake", True)
            server.sync_price_watch_aicoin_candidates()
            source_mock.assert_called_once()

    def test_binance_wallet_intake_switch_gates_its_pool_discovery(self):
        rows_mock = Mock(return_value=[])
        with patch.object(server, "binance_wallet_4h_structure_rows", rows_mock):
            server.set_price_watch_board_toggle("binance-wallet-hot", "intake", False)
            self.assertEqual(server.sync_price_watch_binance_wallet_candidates(), 0)
            rows_mock.assert_not_called()
            server.set_price_watch_board_toggle("binance-wallet-hot", "intake", True)
            server.sync_price_watch_binance_wallet_candidates()
            rows_mock.assert_called_once()

    def test_gmgn_hot_search_intake_switch_gates_its_pool_discovery(self):
        row = {"symbol": "ABC", "chain": "solana", "contractAddress": "abc"}
        symbol_mock = Mock(return_value="ABC")
        with patch.object(server, "price_structure_monitor_symbol", symbol_mock):
            server.set_price_watch_board_toggle("gmgn-hot-search", "intake", False)
            self.assertEqual(server.sync_price_watch_gmgn_hot_search_candidates([row]), 0)
            symbol_mock.assert_not_called()
            server.set_price_watch_board_toggle("gmgn-hot-search", "intake", True)
            server.sync_price_watch_gmgn_hot_search_candidates([row])
            symbol_mock.assert_called_once()

    def test_alert_switch_alone_does_not_stop_intake(self):
        server.set_price_watch_board_toggle("aicoin", "alert", False)
        self.assertTrue(server.price_watch_board_intake_enabled("aicoin"))

    def test_intake_switch_alone_does_not_stop_alerts(self):
        server.set_price_watch_board_toggle("aicoin", "intake", False)
        self.assertTrue(server.price_watch_board_alert_enabled("aicoin"))


if __name__ == "__main__":
    unittest.main()
