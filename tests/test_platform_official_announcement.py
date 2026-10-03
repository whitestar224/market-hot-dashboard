"""A pad's *brand-new* token must be recognised the moment it appears.

The address allow-list can only name tokens that already exist, so it can never
answer "this pad just printed its first token".  The trust anchor that can is the
pad's own official X account: verified accounts publish their own mint
(``solana:BZFY…``), so we watch the account and auto-register what it announces.

Two rules keep this honest, and both are locked here:

* an account only earns a place in ``PLATFORM_OFFICIAL_X_ACCOUNTS`` after it has
  published the mint we independently verified — a guessed handle would promote
  an attacker's address to "official";
* an announced address is only the pad's own coin when the row's own symbol is
  that pad's brand ticker — verified accounts also tweet about partner and user
  tokens, and those must not become "台子币".

The knock-off label is deliberately silent: it never changes tone, priority or
mute policy.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server

SAPLING_CA = "BZFYNPeQAEW3HWQ4DNsTVahC1n4ZjTgn6jB2nnBbB96W"
HOOKED_CA = "C1mBfBoDkwWfd6uTFZp62ARHLjeVp3bDpCDMfMZtPngE"
HOOKR_CA = "0x18E674231A58c239Dc7DaeDcffE15Ec3A24cff5c"
BONK_CA = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# A pad token nobody has allow-listed yet -- the whole point of the feature.
BRAND_NEW_CA = "7MintNotInAnyAllowlistYet111111111111111111"

# Real copycats seen in the local dataset: right ticker, wrong address.
SAPLING_COPYCAT_CA = "CztXPpXSuMf38d17UPLWsvhTFX5Q18wyqbMBqs3nyrDR"
HOOKED_COPYCAT_CA = "u7XfTXk7zJs8i4L6zrL5w7bab2duSTjSaVdpBMxpump"


class _AnnouncementFixture(unittest.TestCase):
    """Isolated announced-token index + a temp cache path."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patchers = [
            patch.object(server, "api_cache_path", self._cache_path),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.addCleanup(self._stop_patchers)
        with server.PLATFORM_ANNOUNCED_TOKENS_LOCK:
            server.PLATFORM_ANNOUNCED_TOKENS.clear()
        server.PLATFORM_ANNOUNCED_TOKENS_LOADED = True
        server.PLATFORM_ANNOUNCEMENT_SYNC_LAST["at"] = 0.0

    def _cache_path(self, key):
        return Path(self._tmpdir.name) / f"api_{key}.json"

    def _stop_patchers(self):
        for patcher in self._patchers:
            patcher.stop()
        with server.PLATFORM_ANNOUNCED_TOKENS_LOCK:
            server.PLATFORM_ANNOUNCED_TOKENS.clear()
        server.PLATFORM_ANNOUNCED_TOKENS_LOADED = False
        self._tmpdir.cleanup()


class PlatformBrandTokenTests(unittest.TestCase):
    def test_brand_key_comes_from_the_verified_ticker(self):
        self.assertIn("sapling", server.platform_brand_tokens("sapling.cash"))
        self.assertIn("hooked", server.platform_brand_tokens("Hoookedpad"))
        self.assertIn("hookr", server.platform_brand_tokens("Hookr.fun"))

    def test_label_stem_drops_the_tld(self):
        self.assertIn("hookr", server.platform_brand_tokens("Hookr.fun"))
        self.assertNotIn("hookrfun", server.platform_brand_tokens("Hookr.fun"))

    def test_ticker_may_differ_from_the_brand(self):
        # Believe's own coin is LAUNCHCOIN, not BELIEVE.
        self.assertIn("launchcoin", server.platform_brand_tokens("Believe"))

    def test_claim_is_an_exact_match_only(self):
        self.assertEqual(server.platform_brand_claim_for_symbol("SAPLING"), "sapling.cash")
        self.assertEqual(server.platform_brand_claim_for_symbol("$sapling"), "sapling.cash")
        self.assertEqual(server.platform_brand_claim_for_symbol("SAPLINGINU"), "")
        self.assertEqual(server.platform_brand_claim_for_symbol(""), "")
        self.assertEqual(server.platform_brand_claim_for_symbol("RANDOMCOIN"), "")


class AnnouncedTokenIndexTests(_AnnouncementFixture):
    def test_record_is_idempotent_and_keeps_the_first_seen_time(self):
        self.assertTrue(server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash"))
        first = dict(server.PLATFORM_ANNOUNCED_TOKENS[BRAND_NEW_CA.casefold()])
        self.assertFalse(
            server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash", text="again")
        )
        self.assertEqual(
            server.PLATFORM_ANNOUNCED_TOKENS[BRAND_NEW_CA.casefold()]["firstSeenAt"],
            first["firstSeenAt"],
        )

    def test_first_publisher_wins_and_is_never_re_attributed(self):
        # Two verified accounts naming the same mint is pathological; keeping
        # the first keeps attribution stable instead of flapping between pads.
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        self.assertFalse(server.platform_announced_token_record(BRAND_NEW_CA, "Hoookedpad"))
        self.assertEqual(
            server.PLATFORM_ANNOUNCED_TOKENS[BRAND_NEW_CA.casefold()]["platform"], "sapling.cash"
        )

    def test_index_round_trips_through_disk(self):
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash", handle="saplingdotcash")
        stored = json.loads(self._cache_path("platform-announced-tokens").read_text("utf-8"))
        self.assertIn(BRAND_NEW_CA.casefold(), stored["tokens"])

    def test_match_requires_the_brand_symbol(self):
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        self.assertEqual(
            server.platform_announced_token_match(
                {"contractAddress": BRAND_NEW_CA, "symbol": "SAPLING"}
            ),
            ("sapling.cash", "SAPLING"),
        )

    def test_match_rejects_a_partner_token_the_same_account_tweeted(self):
        # Same verified account, same tweet stream, but the symbol is not the
        # pad's own ticker -- it must not become a "台子币".
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        self.assertEqual(
            server.platform_announced_token_match(
                {"contractAddress": BRAND_NEW_CA, "symbol": "FRIENDPROJECT"}
            ),
            ("", ""),
        )

    def test_silent_without_a_symbol(self):
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        self.assertEqual(
            server.platform_announced_token_match({"contractAddress": BRAND_NEW_CA}), ("", "")
        )

    def test_silent_for_an_unannounced_address(self):
        self.assertEqual(
            server.platform_announced_token_match(
                {"contractAddress": BRAND_NEW_CA, "symbol": "SAPLING"}
            ),
            ("", ""),
        )

    def test_address_case_does_not_matter(self):
        server.platform_announced_token_record(BRAND_NEW_CA.lower(), "sapling.cash")
        self.assertEqual(
            server.platform_announced_token_match(
                {"contractAddress": BRAND_NEW_CA.upper(), "symbol": "SAPLING"}
            ),
            ("sapling.cash", "SAPLING"),
        )


class PlatformOwnTokenDynamicHopTests(_AnnouncementFixture):
    def test_a_brand_new_announced_mint_is_a_platform_own_token(self):
        # Not in any allow-list -- only the pad's own announcement makes it real.
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        self.assertEqual(
            server.platform_own_token_for_row(
                {"contractAddress": BRAND_NEW_CA, "symbol": "SAPLING"}
            ),
            ("sapling.cash", "SAPLING"),
        )

    def test_announced_mint_without_the_brand_symbol_stays_silent(self):
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        self.assertEqual(
            server.platform_own_token_for_row(
                {"contractAddress": BRAND_NEW_CA, "symbol": "SOMETHINGELSE"}
            ),
            ("", ""),
        )

    def test_the_allowlist_still_wins(self):
        self.assertEqual(
            server.platform_own_token_for_row({"contractAddress": SAPLING_CA}), ("sapling.cash", "SAPLING")
        )

    def test_dynamic_hit_raises_the_red_popup(self):
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        row = {"contractAddress": BRAND_NEW_CA, "symbol": "SAPLING", "title": "x"}
        platform, ticker = server.platform_own_token_for_row(row)
        event = server.apply_platform_own_token_tone(dict(row), platform, ticker)
        self.assertTrue(event["platformOwnToken"])
        self.assertEqual(event["alertTone"], "red")
        self.assertTrue(event["title"].startswith("台子币｜"))
        self.assertGreaterEqual(
            event["queuePriority"], server.DESKTOP_ALERT_CRITICAL_PRIORITY + 40
        )


class CounterfeitLabelTests(_AnnouncementFixture):
    def test_allowlisted_mint_is_never_a_counterfeit(self):
        self.assertEqual(
            server.counterfeit_platform_claim_for_row({"contractAddress": SAPLING_CA, "symbol": "SAPLING"}),
            "",
        )

    def test_announced_mint_is_never_a_counterfeit(self):
        server.platform_announced_token_record(BRAND_NEW_CA, "sapling.cash")
        self.assertEqual(
            server.counterfeit_platform_claim_for_row(
                {"contractAddress": BRAND_NEW_CA, "symbol": "SAPLING"}
            ),
            "",
        )

    def test_same_ticker_wrong_address_is_a_counterfeit(self):
        self.assertEqual(
            server.counterfeit_platform_claim_for_row(
                {"contractAddress": SAPLING_COPYCAT_CA, "symbol": "SAPLING"}
            ),
            "sapling.cash",
        )
        self.assertEqual(
            server.counterfeit_platform_claim_for_row(
                {"contractAddress": HOOKED_COPYCAT_CA, "symbol": "HOOKED"}
            ),
            "Hoookedpad",
        )

    def test_ordinary_coin_is_not_labelled(self):
        self.assertEqual(
            server.counterfeit_platform_claim_for_row(
                {"contractAddress": "SomeOtherMint111111111111111111111111111111", "symbol": "PICKLE"}
            ),
            "",
        )

    def test_apply_sets_then_clears_the_label(self):
        row = {"contractAddress": SAPLING_COPYCAT_CA, "symbol": "SAPLING"}
        server.apply_counterfeit_platform_claim(row)
        self.assertEqual(row["counterfeitPlatformClaim"], "sapling.cash")
        self.assertEqual(row["counterfeitPlatformLabel"], "仿盘·冒充sapling.cash")
        real = {"contractAddress": SAPLING_CA, "symbol": "SAPLING"}
        server.apply_counterfeit_platform_claim(real)
        self.assertNotIn("counterfeitPlatformClaim", real)

    def test_label_is_carried_through_normalization(self):
        normalized = server.normalize_desktop_alert(
            {
                "key": "k",
                "title": "t",
                "counterfeitPlatformClaim": "sapling.cash",
                "counterfeitPlatformLabel": "仿盘·冒充sapling.cash",
            }
        )
        self.assertEqual(normalized["counterfeitPlatformClaim"], "sapling.cash")
        self.assertEqual(normalized["counterfeitPlatformLabel"], "仿盘·冒充sapling.cash")
        self.assertFalse(server.normalize_desktop_alert({"key": "k"})["counterfeitPlatformClaim"])

    def test_the_label_never_unmutes_an_alert(self):
        muted = {
            "key": "k",
            "sourceId": "ths-cn",
            "source": "同花顺",
            "counterfeitPlatformClaim": "sapling.cash",
            "counterfeitPlatformLabel": "仿盘·冒充sapling.cash",
        }
        self.assertTrue(server.desktop_alert_source_is_muted(muted))
        labelled = {**muted, "platformOwnToken": True}
        self.assertFalse(server.desktop_alert_source_is_muted(labelled))

    def test_overlay_annotates_items_rows_and_summary_rows(self):
        payload = {
            "items": [{"contractAddress": SAPLING_COPYCAT_CA, "symbol": "SAPLING"}],
            "rows": [{"contractAddress": SAPLING_CA, "symbol": "SAPLING"}],
            "summaryRows": [{"contractAddress": HOOKED_COPYCAT_CA, "symbol": "HOOKED"}],
        }
        out = server.attach_counterfeit_platform_labels(payload)
        self.assertEqual(out["items"][0]["counterfeitPlatformClaim"], "sapling.cash")
        self.assertNotIn("counterfeitPlatformClaim", out["rows"][0])
        self.assertEqual(out["summaryRows"][0]["counterfeitPlatformClaim"], "Hoookedpad")
        # The overlay must be a copy, never an in-place mutation of the cache.
        self.assertNotIn("counterfeitPlatformClaim", payload["items"][0])

    def test_overlay_never_marks_a_row_as_a_platform_own_token(self):
        # A list overlay must not be able to un-mute anything downstream.
        payload = {"items": [{"contractAddress": SAPLING_CA, "symbol": "SAPLING"}]}
        out = server.attach_counterfeit_platform_labels(payload)
        self.assertNotIn("platformOwnToken", out["items"][0])


class AnnouncementTextTests(unittest.TestCase):
    def test_solana_chain_prefix_is_read(self):
        text = f"on sapling the fee goes back into the system: solana:{SAPLING_CA} bought and burned"
        self.assertIn(SAPLING_CA, server.platform_announced_addresses_in_text(text))

    def test_robinhood_chain_prefix_is_read(self):
        text = f"Henlo @Hookrfun robinhood:{HOOKR_CA.lower()} ty for following"
        self.assertIn(HOOKR_CA.lower(), [a.lower() for a in server.platform_announced_addresses_in_text(text)])

    def test_uppercase_sol_prefix_is_read(self):
        text = f"SOL:{BONK_CA} is live"
        self.assertIn(BONK_CA, server.platform_announced_addresses_in_text(text))

    def test_transaction_hash_is_not_read_as_an_address(self):
        # 0x + 40 hex is a prefix of every 64-hex tx hash; the prefix must not
        # be mistaken for a contract address (this bit the explorer links).
        text = "tx hash: https://www.arcexplorer.org/tx/0x181cd84d918624221a2597442471e7ed4c6298c71d5ed3e7d85d14a89ac7e332"
        self.assertEqual(server.platform_announced_addresses_in_text(text), [])

    def test_a_plain_mention_is_not_an_address(self):
        self.assertEqual(
            server.platform_announced_addresses_in_text("we are building the first zcash launchpad"),
            [],
        )


class OfficialAccountIntegrityTests(unittest.TestCase):
    def test_every_account_maps_to_a_known_platform_label(self):
        known = {label.casefold() for _a, label, _t in server.PLATFORM_OWN_TOKEN_RAW}
        self.assertTrue(known)
        for platform, handle, _extra in server.PLATFORM_OFFICIAL_X_ACCOUNTS:
            self.assertIn(platform.casefold(), known, f"{platform} is not in PLATFORM_OWN_TOKEN_RAW")
            self.assertTrue(handle)
            self.assertNotIn(handle.startswith("@"), (True,), "handles are stored bare")

    def test_handles_are_unique(self):
        handles = [h.casefold() for _p, h, _e in server.PLATFORM_OFFICIAL_X_ACCOUNTS]
        self.assertEqual(len(handles), len(set(handles)))

    def test_every_verified_account_yields_a_brand_token(self):
        for platform, _handle, _extra in server.PLATFORM_OFFICIAL_X_ACCOUNTS:
            self.assertTrue(
                server.platform_brand_tokens(platform), f"{platform} has no brand token"
            )


class SyncTests(_AnnouncementFixture):
    def _timeline_for(self, by_handle):
        """Only the named accounts post; every other verified account is quiet."""

        def fake(source):
            handle = (source or {}).get("handle") or ""
            text = by_handle.get(handle)
            if not text:
                return {"items": []}
            return {
                "items": [
                    {"tweetId": "12345", "url": "https://x.com/status/12345", "text": text}
                ]
            }

        return fake

    def test_sync_records_a_new_announcement(self):
        with patch.object(
            server,
            "x_kol_fetch_fxtwitter_timeline",
            self._timeline_for({"saplingdotcash": f"our own coin: solana:{BRAND_NEW_CA}"}),
        ):
            result = server.sync_platform_official_announcements()
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["discovered"]), 1)
        self.assertEqual(result["discovered"][0]["platform"], "sapling.cash")
        self.assertIn(BRAND_NEW_CA.casefold(), server.PLATFORM_ANNOUNCED_TOKENS)

    def test_sync_reports_nothing_new_on_a_second_pass(self):
        fetcher = self._timeline_for({"saplingdotcash": f"our own coin: solana:{BRAND_NEW_CA}"})
        with patch.object(server, "x_kol_fetch_fxtwitter_timeline", fetcher):
            first = server.sync_platform_official_announcements()
            second = server.sync_platform_official_announcements()
        self.assertEqual(len(first["discovered"]), 1)
        self.assertEqual(second["discovered"], [])
        self.assertEqual(second["known"], 1)

    def test_sync_reads_each_verified_account_independently(self):
        with patch.object(
            server,
            "x_kol_fetch_fxtwitter_timeline",
            self._timeline_for(
                {
                    "saplingdotcash": f"solana:{SAPLING_CA} is live",
                    "HookrFun": f"$HOOKR CA: {HOOKR_CA}",
                }
            ),
        ):
            result = server.sync_platform_official_announcements()
        by_platform = {item["platform"] for item in result["discovered"]}
        self.assertEqual(by_platform, {"sapling.cash", "Hookr.fun"})

    def test_one_dead_account_does_not_stop_the_others(self):
        with patch.object(
            server, "x_kol_fetch_fxtwitter_timeline", side_effect=RuntimeError("mirror down")
        ):
            result = server.sync_platform_official_announcements()
        self.assertEqual(result["discovered"], [])
        self.assertEqual(len(result["errors"]), len(server.PLATFORM_OFFICIAL_X_ACCOUNTS))

    def test_maintenance_hook_swallows_a_failure(self):
        with patch.object(server, "sync_platform_official_announcements", side_effect=RuntimeError("boom")):
            result = server.ensure_platform_official_announcements(force=True)
        self.assertFalse(result["ok"])

    def test_maintenance_hook_is_throttled(self):
        with patch.object(
            server, "sync_platform_official_announcements", return_value={"ok": True, "known": 0}
        ) as mocked:
            server.ensure_platform_official_announcements(force=True)
            second = server.ensure_platform_official_announcements()
        self.assertTrue(second.get("skipped"))
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
