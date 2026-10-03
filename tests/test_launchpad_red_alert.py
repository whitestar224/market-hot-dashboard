"""Launch-platform detection — and the 2026-10-03 removal of the 「发射台」 red alert.

History: a coin that *graduated off* a launch platform used to be painted red
("发射台｜…").  It was retired because it carried no information — the GMGN trench
board ships a native ``launchpad`` value on 300/300 rows, so the rule flagged
*every* new coin, drowning out the actually rare signal.  Worse, the free-text
fallback that covered boards without the field fired on ordinary memecoins that
merely name-drop a platform ("PUMPFUN ARMY", "LIBRARY").

What remains:
  * ``launchpad_platform_for_row`` — structured-only detection, kept as plain
    data (e.g. ``price_watch_assets.launchpad_platform``).
  * The pad's OWN token ("台子币") is the only red launchpad-related alert; it is
    covered by ``test_platform_own_token_alert``.

These tests lock both halves: detection stays structured, and no board turns a
launchpad row red any more.
"""
import unittest
from unittest.mock import patch

import server


class LaunchpadDetectionTests(unittest.TestCase):
    def test_native_gmgn_launchpad_field_wins(self):
        self.assertEqual(
            server.launchpad_platform_for_row({"symbol": "SAPLING", "launchpad": "fourmeme"}),
            "four.meme",
        )
        self.assertEqual(
            server.launchpad_platform_for_row({"launchpad_platform": "pumpfun"}),
            "pump.fun",
        )

    def test_ordinary_token_is_not_a_launchpad(self):
        self.assertEqual(
            server.launchpad_platform_for_row({"symbol": "BTC", "name": "Bitcoin"}),
            "",
        )
        # A generic "launch"/"migrated" wording must never be a hit.
        self.assertEqual(
            server.launchpad_platform_for_row(
                {"symbol": "X", "summary": "Token launched and migrated to DEX"}
            ),
            "",
        )

    def test_brand_word_in_the_name_is_not_a_launchpad(self):
        # A memecoin that merely name-drops a platform is still a memecoin.
        # These two actually shipped as false-positive red alerts on the GMGN
        # 5-minute hot-search board before the free-text fallback was removed.
        self.assertEqual(
            server.launchpad_platform_for_row({"symbol": "ARMY", "name": "PUMPFUN ARMY"}),
            "",
        )
        self.assertEqual(
            server.launchpad_platform_for_row(
                {
                    "symbol": "LIBRARY",
                    "name": "Library",
                    "summary": "灵感来源于 PumpFun 平台历史大事件的 memecoin",
                }
            ),
            "",
        )

    def test_prose_may_not_claim_a_launchpad(self):
        # Free text is a narrative choice, never provenance.  Even an explicit
        # "launched on four.meme" blurb must not create a launch platform.
        self.assertEqual(
            server.launchpad_platform_for_row(
                {"symbol": "HOOKED", "name": "Hooked", "summary": "launched on four.meme"}
            ),
            "",
        )
        self.assertEqual(
            server.launchpad_platform_for_row({"symbol": "X", "narrativeLabels": ["pump.fun 生态"]}),
            "",
        )

    def test_the_free_text_marker_machinery_is_gone(self):
        # Guard against re-introducing the removed fallback.
        self.assertFalse(hasattr(server, "LAUNCHPAD_TEXT_MARKERS"))
        self.assertFalse(hasattr(server, "launchpad_platform_from_text"))
        self.assertFalse(hasattr(server, "apply_launchpad_alert_tone"))

    def test_unknown_platform_id_is_still_reported_verbatim(self):
        # Any non-empty native field describes where the coin launched, even for
        # platforms not in the display map.  (Informational only — it no longer
        # drives an alert.)
        self.assertEqual(server.launchpad_platform_for_row({"launchpad": "stonkfun"}), "stonkfun")

    def test_live_gmgn_platform_names_are_labelled(self):
        # Values observed on the live GMGN trenches board (2026-10-02).
        self.assertEqual(server.launchpad_platform_label("Pump.fun"), "Pump.fun")
        self.assertEqual(server.launchpad_platform_label("meteora_virtual_curve"), "Meteora 虚拟曲线")
        self.assertEqual(server.launchpad_platform_label("ray_launchpad"), "Raydium Launchpad")
        self.assertEqual(server.launchpad_platform_label("bankr"), "Bankr")


class RankBoardNoLongerRedsLaunchpadsTests(unittest.TestCase):
    """A launchpad value must not turn any rank/hot-search row red."""

    def _snapshot(self, **overrides):
        base = {
            "key": "gmgn-hot-search:HOOKED",
            "assetKey": "HOOKED",
            "sourceId": "gmgn-hot-search",
            "sourceTitle": "GMGN 热搜榜",
            "sourceLabel": "GMGN",
            "period": server.GMGN_HOT_SEARCH_ALERT_PERIOD,
            "periodLabel": "5 分钟",
            "group": "crypto",
            "symbol": "HOOKED",
            "name": "Hooked",
            "rank": 3,
            "price": "$0.001",
            "change": 42.0,
            "amount": 1_000_000,
            "heat": 88,
            "turnover": "$1.0M",
            "note": "GMGN 5m 热搜",
            "summary": "",
            "launchpad": "",
            "narrativeLabels": [],
            "chain": "sol",
            "contractAddress": "4JSmZJ1tG3vrnQKgUzH3eGVSG52UdFui8qPLbDaonFpb",
            "url": "https://gmgn.ai/sol/token/4JSmZJ1tG3vrnQKgUzH3eGVSG52UdFui8qPLbDaonFpb",
        }
        base.update(overrides)
        return base

    def test_hot_search_entry_with_a_launchpad_stays_normal(self):
        event = server.rank_monitor_event("hot", self._snapshot(launchpad="fourmeme"), "new")
        self.assertEqual(event.get("alertTone", "normal"), "normal")
        self.assertFalse(event["title"].startswith("发射台"))
        self.assertFalse(event.get("launchpadPlatform"))
        self.assertLess(event["queuePriority"], server.DESKTOP_ALERT_CRITICAL_PRIORITY)
        # It keeps its normal five-minute label.
        self.assertEqual(event["alertPeriod"], server.GMGN_HOT_SEARCH_ALERT_PERIOD)
        self.assertIn("GMGN 5 分钟热搜榜", event["title"])

    def test_launchpad_alone_no_longer_unmutes_a_board(self):
        # red + launchpadPlatform used to bypass board-level mutes.  It must not
        # any more — otherwise the retired alert could still force popups.
        normalized = server.normalize_desktop_alert(
            {
                "key": "newboard:gmgn-trenches:SAPLING",
                "kind": "新币上新",
                "source": "GMGN 战壕新币榜",
                "sourceLabel": "NEW",
                "title": "GMGN 战壕新币榜 SAPLING",
                "priority": "新币上新",
                "queuePriority": 930,
                "alertTone": "red",
                "launchpadPlatform": "Meteora 虚拟曲线",
            }
        )
        self.assertTrue(server.desktop_alert_source_is_muted(normalized))
        # The pad's own token still bypasses — that is a different, real signal.
        owned = server.normalize_desktop_alert({**normalized, "platformOwnToken": True})
        self.assertFalse(server.desktop_alert_source_is_muted(owned))

    def test_memecoin_named_after_a_platform_stays_normal(self):
        # Regression: "PUMPFUN ARMY" / "LIBRARY" shipped as red pump.fun alerts
        # purely because their *name* mentions PumpFun.
        snapshot = self._snapshot(
            symbol="ARMY", name="PUMPFUN ARMY", assetKey="ARMY", key="gmgn-hot-search:ARMY",
            summary="灵感来源于 PumpFun 平台历史大事件的 memecoin",
            contractAddress="29REVVLFoM2ERuSTt2p86zsn9q7CxTNuzHPw29dnpump",
        )
        event = server.rank_monitor_event("hot", snapshot, "new")
        self.assertEqual(event.get("alertTone", "normal"), "normal")
        self.assertFalse(event["title"].startswith("发射台"))
        self.assertFalse(event.get("platformOwnToken", False))


class TrenchScanNoLongerRedsLaunchpadsTests(unittest.TestCase):
    def _row(self, **overrides):
        base = {
            "symbol": "SAPLING",
            "name": "Sapling",
            "network": "bsc",
            # Live GMGN trench rows always carry this; it must not drive colour.
            "contractAddress": "CztXPpXSuMf38d17UPLWsvhTFX5Q18wyqbMBqs3nyrDR",
            "launchpad": "fourmeme",
        }
        base.update(overrides)
        return base

    def _decision(self, **overrides):
        base = {
            "popupEligible": True, "potentialTier": "high", "gradeLabel": "S级",
            "label": "重点研究", "speechEligible": True, "executionPermission": "REVIEW",
            "reason": "",
        }
        base.update(overrides)
        return base

    def test_trench_scan_launchpad_token_is_not_red(self):
        analysis = {"researchRoute": "", "summary": "发射台新币", "frameworkAssessment": {}}
        with patch.object(server, "golden_leader_alert_decision",
                          return_value=self._decision()), \
                patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            server.send_fast_onchain_alert(
                self._row(), analysis,
                {"key": "trench:SAPLING", "analyzed_at": 1_700_000_000_000, "first_seen_at": 1_699_999_000_000},
            )

        payload = launch.call_args.args[0]
        self.assertNotEqual(payload.get("alertTone"), "red")
        self.assertFalse(payload["title"].startswith("发射台"))
        self.assertLess(payload.get("queuePriority", 0), server.DESKTOP_ALERT_CRITICAL_PRIORITY)
        self.assertFalse(payload.get("launchpadPlatform"))

    def test_trench_scan_ordinary_token_is_unchanged(self):
        analysis = {"researchRoute": "", "summary": "普通链上新币", "frameworkAssessment": {}}
        with patch.object(server, "golden_leader_alert_decision",
                          return_value=self._decision(potentialTier="mid", gradeLabel="A级",
                                                      label="观察")), \
                patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            server.send_fast_onchain_alert(
                self._row(launchpad=""), analysis,
                {"key": "trench:PLAIN", "analyzed_at": 1_700_000_000_000, "first_seen_at": 1_699_999_000_000},
            )

        payload = launch.call_args.args[0]
        self.assertEqual(payload.get("alertTone", "normal"), "normal")
        self.assertNotIn("发射台", payload["title"])
        self.assertFalse(payload.get("platformOwnToken", False))


class NewboardNoLongerRedsLaunchpadsTests(unittest.TestCase):
    def test_newboard_launchpad_row_is_not_red(self):
        payload = {
            "updatedAt": 1_700_000_000_000,
            "sections": [
                {
                    "id": "gmgn-trenches",
                    "title": "GMGN 战壕新币榜",
                    "group": "crypto",
                    "rows": [
                        {
                            "rank": 1,
                            "symbol": "SAPLING",
                            "launchpad": "meteora_virtual_curve",
                            "date": 1_700_000_000_000,
                        }
                    ],
                }
            ],
        }
        with patch.object(server, "observe_listings", return_value=({}, {})):
            events = server.parse_site_newboard_events(payload)
        for event in events:
            self.assertNotEqual(event.get("alertTone"), "red")
            self.assertFalse(str(event.get("title") or "").startswith("发射台"))
            self.assertFalse(event.get("launchpadPlatform"))


class NewboardMuteTests(unittest.TestCase):
    def test_ordinary_newboard_event_stays_muted(self):
        normalized = server.normalize_desktop_alert(
            {
                "key": "newboard:gmgn-trenches:PLAIN",
                "kind": "新币上新",
                "source": "GMGN 战壕新币榜",
                "sourceLabel": "NEW",
                "title": "GMGN 战壕新币榜 PLAIN",
                "priority": "新币上新",
                "queuePriority": 80,
            }
        )
        self.assertTrue(server.desktop_alert_source_is_muted(normalized))


if __name__ == "__main__":
    unittest.main()
