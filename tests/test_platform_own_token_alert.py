"""A launchpad's OWN token ("台子币") must raise an immediate red alert.

This is a different question from ``test_launchpad_red_alert`` (which detects
coins that graduated *from* a launchpad).  Here we detect the platform's own
token — pump.fun -> PUMP, sapling.cash -> SAPLING, Hoookedpad -> HOOKED etc.

The hard part is copycats: every popular pad's ticker gets cloned by dozens of
fake mints.  The contract address is the only reliable tell, so the allow-list
wins, a self-declaring description is the fallback, and everything else stays
silent.
"""
import unittest
from unittest.mock import patch

import server

# The two tokens the user reported, verbatim from their message.
SAPLING_CA = "BZFYNPeQAEW3HWQ4DNsTVahC1n4ZjTgn6jB2nnBbB96W"
HOOKED_CA = "C1mBfBoDkwWfd6uTFZp62ARHLjeVp3bDpCDMfMZtPngE"

# Real copycats observed in the same local dataset: same ticker, wrong address.
HOOKED_COPYCAT_CA = "u7XfTXk7zJs8i4L6zrL5w7bab2duSTjSaVdpBMxpump"
SAPLING_COPYCAT_CA = "CztXPpXSuMf38d17UPLWsvhTFX5Q18wyqbMBqs3nyrDR"

# Pads surfaced by the 2026-10-02 audit of high-scoring filtered candidates.
# HOOKR is the important one: two same-name contracts sit in the same local
# dataset and only 0x18E67423... is the pad's real project token.
HOOKR_REAL_CA = "0x18E674231A58c239Dc7DaeDcffE15Ec3A24cff5c"
HOOKR_COPYCAT_CA = "0x74ccd8416cbc4e4a864176c2707bc1434819ef0f"
PONS_CA = "0x39dBED3a2bd333467115dE45665cC57F813C4571"
ARGUS_CA = "0xeCe5cA8bf9220718E5727754026757512212cb3c"
LIFT_FUN_CA = "0x44B453D355835Ce1269fc11D3FA4161c0DcC0087"


class PlatformOwnTokenDetectionTests(unittest.TestCase):
    def test_address_allowlist_wins_over_everything(self):
        self.assertEqual(
            server.platform_own_token_for_row({"contractAddress": SAPLING_CA}),
            ("sapling.cash", "SAPLING"),
        )
        self.assertEqual(
            server.platform_own_token_for_row({"contractAddress": HOOKED_CA}),
            ("Hoookedpad", "HOOKED"),
        )

    def test_well_known_pad_tokens_are_listed(self):
        cases = {
            "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn": "pump.fun",
            "3iQL8BFS2vE7mww4ehAqQHAsbmRNCrPxizWAT2Zfyr9y": "Virtuals",
            "0x0b3e328455c4059EEb9e3f84b5543F74E24e7E1b": "Virtuals",
            "boopkpWqe68MSxLqBGogs8ZbUDN4GXaLhFwNP7mpP1i": "boop.fun",
            "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "Jupiter",
            "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R": "Raydium",
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
        }
        for address, platform in cases.items():
            with self.subTest(address=address):
                self.assertEqual(server.platform_own_token_for_row({"contractAddress": address})[0], platform)

    def test_every_raw_address_round_trips(self):
        # Guards against hand-typed typos in the allow-list: each canonical
        # address must be found again when looked up verbatim.
        for address, platform, ticker in server.PLATFORM_OWN_TOKEN_RAW:
            with self.subTest(address=address):
                self.assertEqual(
                    server.platform_own_token_for_row({"contractAddress": address}),
                    (platform, ticker),
                )

    def test_audited_pads_are_recognised(self):
        # Every one of these was found in the candidates DB as a high-scoring
        # row that the pipeline had silently filtered.
        cases = {
            HOOKR_REAL_CA: ("Hookr.fun", "HOOKR"),
            PONS_CA: ("Pons", "PONS"),
            ARGUS_CA: ("Argus", "ARGUS"),
            LIFT_FUN_CA: ("lift.fun", "LIFT"),
        }
        for address, expected in cases.items():
            with self.subTest(address=address):
                self.assertEqual(server.platform_own_token_for_row({"contractAddress": address}), expected)

    def test_hookr_copycat_address_stays_silent(self):
        # Same symbol and name as the real pad token, different address.
        self.assertEqual(
            server.platform_own_token_for_row(
                {"symbol": "HOOKR", "name": "Hookr.fun", "contractAddress": HOOKR_COPYCAT_CA}
            ),
            ("", ""),
        )

    def test_copycat_same_ticker_wrong_address_stays_silent(self):
        # Same symbol, address not on the list, no self-declaration -> knock-off.
        self.assertEqual(
            server.platform_own_token_for_row(
                {"symbol": "HOOKED", "name": "Hooked", "contractAddress": HOOKED_COPYCAT_CA}
            ),
            ("", ""),
        )
        self.assertEqual(
            server.platform_own_token_for_row(
                {"symbol": "SAPLING", "name": "Sapling", "contractAddress": SAPLING_COPYCAT_CA}
            ),
            ("", ""),
        )

    def test_ordinary_token_is_not_a_platform_token(self):
        self.assertEqual(
            server.platform_own_token_for_row(
                {"symbol": "WIF", "name": "dogwifhat", "contractAddress": "abc123", "description": "best dog meme"}
            ),
            ("", ""),
        )

    def test_self_declaring_description_is_the_fallback(self):
        # A brand-new pad that is not in the allow-list yet, but the description
        # says outright that the coin IS the launchpad token.
        row = {
            "symbol": "NEWPAD",
            "name": "NewPad",
            "contractAddress": "SomeUnlistedNewPadAddress111111111111111",
            "description": "NewPad is a memecoin launchpad on Solana. The platform token powers buyback and burn.",
        }
        self.assertEqual(server.platform_own_token_for_row(row), ("自述平台币", "NEWPAD"))

    def test_self_declaration_chinese_variant(self):
        row = {
            "symbol": "新台子",
            "contractAddress": "SomeUnlistedNewPadAddress222222222222222",
            "description": "这是一个基于Solana的代币发射台，平台代币用于回购与销毁",
        }
        self.assertEqual(server.platform_own_token_for_row(row), ("自述平台币", "新台子"))

    def test_contract_address_is_read_from_aliases(self):
        for key in ("contractAddress", "contract", "address", "tokenAddress", "mint"):
            with self.subTest(key=key):
                self.assertEqual(
                    server.platform_own_token_for_row({key: SAPLING_CA})[0], "sapling.cash"
                )

    def test_address_lookup_is_case_insensitive(self):
        self.assertEqual(
            server.platform_own_token_for_row({"contractAddress": SAPLING_CA.lower()})[0],
            "sapling.cash",
        )
        self.assertEqual(
            server.platform_own_token_for_row({"contractAddress": SAPLING_CA.upper()})[0],
            "sapling.cash",
        )


class PlatformOwnTokenToneTests(unittest.TestCase):
    def test_tone_marks_red_critical_and_voice(self):
        event = server.apply_platform_own_token_tone(
            {"title": "链上投研 · V4.9潜力", "kind": "链上投研", "symbol": "SAPLING",
             "priority": "观察", "queuePriority": 10, "speech": ""},
            "sapling.cash",
            "SAPLING",
        )
        self.assertTrue(event["platformOwnToken"])
        self.assertEqual(event["alertTone"], "red")
        self.assertGreaterEqual(event["queuePriority"], server.DESKTOP_ALERT_CRITICAL_PRIORITY)
        self.assertTrue(event["title"].startswith("台子币｜"))
        self.assertIn("台子币", event["kind"])
        self.assertIn("sapling.cash", event["priority"])
        self.assertIn("台子币", event["speech"])
        self.assertTrue(event["sound"])

    def test_existing_higher_priority_is_preserved(self):
        event = server.apply_platform_own_token_tone(
            {"title": "X", "kind": "链上投研", "symbol": "HOOKED", "queuePriority": 9999},
            "Hoookedpad",
            "HOOKED",
        )
        # Existing higher priority is preserved, not lowered.
        self.assertEqual(event["queuePriority"], 9999)
        self.assertEqual(event["priority"], "台子币重点（Hoookedpad）")


class PlatformOwnTokenMuteBypassTests(unittest.TestCase):
    def test_platform_own_token_bypasses_delivery_mute(self):
        event = server.apply_platform_own_token_tone(
            {
                "key": "newboard:gmgn-trenches:HOOKED",
                "kind": "新币上新",
                "source": "GMGN 战壕新币榜",
                "sourceLabel": "NEW",
                "title": "GMGN 战壕新币榜 HOOKED",
                "priority": "新币上新",
                "queuePriority": 80,
            },
            "Hoookedpad",
            "HOOKED",
        )
        normalized = server.normalize_desktop_alert(event)
        # Both flags must survive normalization or the bypass silently dies.
        self.assertTrue(normalized["platformOwnToken"])
        self.assertEqual(normalized["launchpadPlatform"], "Hoookedpad")
        self.assertFalse(server.desktop_alert_source_is_muted(normalized))

    def test_flag_survives_normalize(self):
        normalized = server.normalize_desktop_alert(
            {"key": "k", "platformOwnToken": True, "alertTone": "red", "launchpadPlatform": "pump.fun"}
        )
        self.assertTrue(normalized["platformOwnToken"])


class TrenchScanPlatformOwnTokenTests(unittest.TestCase):
    """send_fast_onchain_alert: the pad's own token must pop even when filtered."""

    def _row(self, **overrides):
        base = {
            "symbol": "SAPLING",
            "name": "Sapling",
            "network": "solana",
            "contractAddress": SAPLING_CA,
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

    def test_platform_own_token_reports_red(self):
        analysis = {"researchRoute": "", "summary": "平台币", "frameworkAssessment": {}}
        with patch.object(server, "golden_leader_alert_decision",
                          return_value=self._decision()), \
                patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            server.send_fast_onchain_alert(
                self._row(), analysis,
                {"key": "trench:SAPLING", "analyzed_at": 1_700_000_000_000, "first_seen_at": 1_699_999_000_000},
            )
        payload = launch.call_args.args[0]
        self.assertEqual(payload["alertTone"], "red")
        self.assertTrue(payload["platformOwnToken"])
        self.assertTrue(payload["title"].startswith("台子币｜"))
        self.assertIn("台子币", payload["speech"])
        self.assertTrue(payload["sound"])

    def test_platform_own_token_not_suppressed_by_popup_ineligibility(self):
        # The crux of the bug: a high-value pad token used to be dropped here
        # because the research grade was not popup-eligible.
        analysis = {"researchRoute": "", "summary": "平台币", "frameworkAssessment": {}}
        with patch.object(server, "golden_leader_alert_decision",
                          return_value=self._decision(popupEligible=False, reason="综合质量未达到研究门槛")), \
                patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            result = server.send_fast_onchain_alert(
                self._row(), analysis,
                {"key": "trench:SAPLING", "analyzed_at": 1_700_000_000_000, "first_seen_at": 1_699_999_000_000},
            )
        self.assertTrue(launch.called, "platform-own token must not be suppressed")
        self.assertEqual(launch.call_args.args[0]["alertTone"], "red")
        self.assertNotEqual(result.get("suppressed"), True)

    def test_copycat_is_still_suppressed_normally(self):
        analysis = {"researchRoute": "", "summary": "仿盘", "frameworkAssessment": {}}
        with patch.object(server, "golden_leader_alert_decision",
                          return_value=self._decision(popupEligible=False, reason="综合质量未达到研究门槛")), \
                patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            result = server.send_fast_onchain_alert(
                self._row(contractAddress=HOOKED_COPYCAT_CA, symbol="HOOKED", name="Hooked"),
                analysis,
                {"key": "trench:HOOKEDCOPY", "analyzed_at": 1_700_000_000_000, "first_seen_at": 1_699_999_000_000},
            )
        self.assertFalse(launch.called)
        self.assertTrue(result.get("suppressed"))

    def test_ordinary_token_is_unchanged(self):
        analysis = {"researchRoute": "", "summary": "普通链上新币", "frameworkAssessment": {}}
        with patch.object(server, "golden_leader_alert_decision",
                          return_value=self._decision(potentialTier="mid", gradeLabel="A级",
                                                      label="观察")), \
                patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            server.send_fast_onchain_alert(
                self._row(contractAddress="SomeOrdinaryTokenAddress99999999", symbol="PLAIN", name="Plain"),
                analysis,
                {"key": "trench:PLAIN", "analyzed_at": 1_700_000_000_000, "first_seen_at": 1_699_999_000_000},
            )
        payload = launch.call_args.args[0]
        self.assertNotIn("台子币", payload.get("title", ""))
        self.assertFalse(payload.get("platformOwnToken", False))


class RankMonitorPlatformOwnTokenTests(unittest.TestCase):
    def _snapshot(self, **overrides):
        base = {
            "key": "binance-wallet-hot:HOOKED",
            "assetKey": "HOOKED",
            "sourceId": "binance-wallet-hot",
            "sourceTitle": "币安钱包热门榜",
            "sourceLabel": "币安",
            "period": "4h",
            "periodLabel": "4 小时",
            "group": "crypto",
            "symbol": "HOOKED",
            "name": "Hooked",
            "rank": 6,
            "price": "$0.001",
            "change": 12.0,
            "amount": 1_000_000,
            "heat": 70,
            "turnover": "$1.0M",
            "note": "",
            "summary": "",
            "narrativeLabels": [],
            "chain": "sol",
            "contractAddress": HOOKED_CA,
            "url": "",
        }
        base.update(overrides)
        return base

    def test_platform_own_token_rank_entry_is_red(self):
        event = server.rank_monitor_event("hot", self._snapshot(), "new")
        self.assertEqual(event["alertTone"], "red")
        self.assertTrue(event["platformOwnToken"])
        self.assertTrue(event["title"].startswith("台子币｜"))
        self.assertIn("台子币", event["speech"])

    def test_copycat_rank_entry_stays_normal(self):
        snapshot = self._snapshot(
            symbol="HOOKED", name="Hooked", assetKey="HOOKED",
            key="binance-wallet-hot:HOOKEDCOPY", contractAddress=HOOKED_COPYCAT_CA,
        )
        event = server.rank_monitor_event("hot", snapshot, "new")
        self.assertFalse(event.get("platformOwnToken", False))
        self.assertNotIn("台子币", event.get("title", ""))


class NewboardPlatformOwnTokenTests(unittest.TestCase):
    """parse_site_newboard_events must red-flag a pad's own token on the board."""

    def _events(self, symbol, contract):
        now = 1_700_000_000_000
        payload = {
            "updatedAt": now,
            "sections": [
                {
                    "id": "gmgn-trenches",
                    "title": "GMGN 战壕新币榜",
                    "group": "crypto",
                    "rows": [{"rank": 1, "symbol": symbol, "contractAddress": contract, "date": now}],
                }
            ],
        }
        state = {
            "proofs": {
                server.listing_asset_key(symbol): {
                    "key": f"newboard:gmgn-trenches:{symbol}",
                    "sourceId": "gmgn-trenches",
                    "expiresAt": now + 600_000,
                }
            }
        }
        with patch.object(server, "read_json_cache", return_value=state):
            return server.parse_site_newboard_events(payload)

    def test_platform_own_token_row_is_red(self):
        events = self._events("HOOKED", HOOKED_CA)
        red = [e for e in events if e.get("platformOwnToken")]
        self.assertTrue(red, "expected a platform-own token event")
        event = red[0]
        self.assertEqual(event["alertTone"], "red")
        self.assertTrue(event["title"].startswith("台子币｜"))
        self.assertGreaterEqual(event["queuePriority"], server.DESKTOP_ALERT_CRITICAL_PRIORITY)

    def test_copycat_row_is_not_red(self):
        events = self._events("HOOKED", HOOKED_COPYCAT_CA)
        for event in events:
            self.assertFalse(event.get("platformOwnToken", False))
            self.assertNotIn("台子币", event.get("title", ""))


class ProjectCandidateDetectionTests(unittest.TestCase):
    """A ``project`` candidate is tagged, never given a new trigger."""

    def test_project_type_is_detected(self):
        self.assertTrue(server.project_candidate_for_row({"candidateType": "project"}))
        self.assertTrue(server.project_candidate_for_row({"candidate_type": "project"}))
        self.assertTrue(server.project_candidate_for_row({"candidateType": "PROJECT"}))
        self.assertTrue(server.project_candidate_for_row({"candidateType": " Project "}))
        self.assertTrue(
            server.project_candidate_for_row({"launchFacts": {"candidateType": "project"}})
        )

    def test_meme_or_missing_type_is_not_a_project(self):
        for row in (
            {"candidateType": "meme"},
            {"symbol": "WIF"},
            {},
            {"launchFacts": {"candidateType": "meme"}},
            {"candidateType": ""},
        ):
            with self.subTest(row=row):
                self.assertFalse(server.project_candidate_for_row(row))

    def test_non_dict_is_safe(self):
        for value in (None, "project", 1, []):
            self.assertFalse(server.project_candidate_for_row(value))

    def test_tone_tags_without_changing_trigger(self):
        event = {
            "title": "币安钱包 4 小时热门榜新进：FOO",
            "priority": "钱包热榜新进",
            "alertTone": "normal",
            "queuePriority": 74,
            "speech": "币安钱包热门榜新进，FOO。",
        }
        out = server.apply_project_candidate_tone(dict(event))
        self.assertTrue(out["projectCandidate"])
        self.assertTrue(out["title"].startswith("项目｜"))
        self.assertIn("项目", out["priority"])
        # The trigger-critical fields must be untouched.
        self.assertEqual(out["alertTone"], "normal")
        self.assertEqual(out["queuePriority"], 74)
        self.assertEqual(out["speech"], event["speech"])

    def test_tone_is_idempotent(self):
        once = server.apply_project_candidate_tone({"title": "X", "priority": "新进"})
        twice = server.apply_project_candidate_tone(dict(once))
        self.assertEqual(once["title"], twice["title"])
        self.assertEqual(once["priority"], twice["priority"])

    def test_flag_survives_normalize(self):
        normalized = server.normalize_desktop_alert({"key": "k", "projectCandidate": True})
        self.assertTrue(normalized["projectCandidate"])
        self.assertFalse(server.normalize_desktop_alert({"key": "k"})["projectCandidate"])

    def test_project_flag_does_not_bypass_mute(self):
        # Unlike platformOwnToken, a project tag is a label only — it must NOT
        # punch through a board-level mute.
        normalized = server.normalize_desktop_alert(
            {"key": "k", "projectCandidate": True, "sourceId": "gmgn-hot-search",
             "alertPeriod": "5m", "kind": "榜单更新", "title": "GMGN 热搜榜更新：FOO"}
        )
        self.assertTrue(server.desktop_alert_source_is_muted(normalized))


class RankMonitorProjectCandidateTests(unittest.TestCase):
    def _snapshot(self, **overrides):
        base = {
            "key": "binance-wallet-hot:FOO",
            "assetKey": "FOO",
            "sourceId": "binance-wallet-hot",
            "sourceTitle": "币安钱包热门榜",
            "sourceLabel": "币安",
            "period": "4h",
            "periodLabel": "4 小时",
            "group": "crypto",
            "symbol": "FOO",
            "name": "Foo Protocol",
            "rank": 6,
            "price": "$1.0",
            "change": 12.0,
            "amount": 1_000_000,
            "heat": 70,
            "turnover": "$1.0M",
            "note": "",
            "summary": "",
            "narrativeLabels": [],
            "chain": "eth",
            "contractAddress": "0xdeadbeef",
            "candidateType": "project",
            "url": "",
        }
        base.update(overrides)
        return base

    def test_project_entry_is_tagged_but_not_red(self):
        event = server.rank_monitor_event("hot", self._snapshot(), "new")
        self.assertTrue(event.get("projectCandidate"))
        self.assertTrue(event["title"].startswith("项目｜"))
        self.assertIn("项目", event["priority"])
        self.assertNotEqual(event.get("alertTone"), "red")

    def test_meme_entry_is_not_tagged(self):
        event = server.rank_monitor_event("hot", self._snapshot(candidateType="meme"), "new")
        self.assertFalse(event.get("projectCandidate", False))
        self.assertNotIn("项目｜", event.get("title", ""))

    def test_platform_own_token_outranks_project_tag(self):
        # A pad's own token is stronger: it must stay 台子币 red, not become a
        # plain project label.
        snapshot = self._snapshot(
            symbol="SAPLING", assetKey="SAPLING", contractAddress=SAPLING_CA,
            candidateType="project",
        )
        event = server.rank_monitor_event("hot", snapshot, "new")
        self.assertTrue(event.get("platformOwnToken"))
        self.assertEqual(event["alertTone"], "red")
        self.assertNotIn("项目｜", event.get("title", ""))

    def test_snapshot_carries_candidate_type(self):
        source = {"id": "binance-wallet-hot", "title": "币安钱包热门榜", "period": "4h"}
        snapshot = server.rank_monitor_snapshot(source, {"symbol": "FOO", "candidateType": "project"}, 0, "hot")
        self.assertEqual(snapshot["candidateType"], "project")


if __name__ == "__main__":
    unittest.main()
