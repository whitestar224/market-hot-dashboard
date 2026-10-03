"""Per-venue new-listing alerts (用户指定：币安 + OKX 按所独立提醒).

Regression guard for the measured bug where the global "first listing" policy could
never fire for a main venue: five of the six cross-venue inventories carry no date,
so ``min(owner_entries)`` collapsed to 0 and every Binance/OKX new contract was also
silently written into the permanent ``seen`` set.  Measured history before the fix:
19 ``新币上新`` popups in 25 days — Gate 11, Hyperliquid 5, Aster 2, OKX 1, Binance 0.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from listing_alerts import (REQUIRED_SOURCES, VENUE_LISTING_KEY_PREFIX, attach_inventory,
                            observe_listings, observe_venue_listings)

MINUTE = 60_000
HOUR = 60 * MINUTE


class VenueListingPolicyTests(unittest.TestCase):
    now = 1788860000000

    def test_fresh_contract_alerts_even_when_the_asset_already_trades_elsewhere(self):
        sources = [
            attach_inventory({"id": "binance-new", "status": "ok"},
                             [("CT", self.now - MINUTE), ("OLDCOIN", self.now - 90 * 24 * HOUR)]),
            # Same assets exist on venues whose inventories carry no date at all.
            attach_inventory({"id": "binance-spot-inventory", "status": "ok"}, [("CT", 0)]),
            attach_inventory({"id": "gate-spot-inventory", "status": "ok"}, [("CT", 0)]),
        ]
        _, proofs = observe_venue_listings(
            {}, sources, venue_ids=server.VENUE_LISTING_SOURCES, now_ms=self.now)
        self.assertEqual(set(proofs), {"binance-new:CT"})
        self.assertEqual(proofs["binance-new:CT"]["sourceId"], "binance-new")
        self.assertTrue(proofs["binance-new:CT"]["key"].startswith(VENUE_LISTING_KEY_PREFIX + ":"))

    def test_global_policy_alone_would_still_stay_silent_here(self):
        """The whole point: the same payload yields no global proof."""
        sources = [attach_inventory({"id": source, "status": "ok", "rows": []}, [("EXISTING", self.now - HOUR)])
                   for source in REQUIRED_SOURCES]
        row = next(r for r in sources if r["id"] == "binance-new")
        row["listingInventory"].append({"asset": "CT", "listedAt": self.now - MINUTE})
        row["rows"].append({"asset": "CT", "symbol": "CTUSDT", "date": self.now - MINUTE})
        _, global_proofs = observe_listings({}, sources, now_ms=self.now)
        self.assertEqual(global_proofs, {})
        _, venue_proofs = observe_venue_listings(
            {}, sources, venue_ids=server.VENUE_LISTING_SOURCES, now_ms=self.now)
        self.assertEqual(set(venue_proofs), {"binance-new:CT"})

    def test_stale_contracts_and_unknown_venues_never_alert(self):
        sources = [
            attach_inventory({"id": "binance-new", "status": "ok"}, [("OLD", self.now - 16 * MINUTE)]),
            attach_inventory({"id": "okx-new", "status": "ok"}, [("NODATE", 0)]),
            attach_inventory({"id": "gate-new", "status": "ok"}, [("GATEFRESH", self.now - MINUTE)]),
        ]
        _, proofs = observe_venue_listings(
            {}, sources, venue_ids=server.VENUE_LISTING_SOURCES, now_ms=self.now)
        self.assertEqual(proofs, {})

    def test_unavailable_venue_source_is_skipped(self):
        sources = [attach_inventory({"id": "binance-new", "status": "unavailable"},
                                    [("CT", self.now - MINUTE)])]
        _, proofs = observe_venue_listings(
            {}, sources, venue_ids=server.VENUE_LISTING_SOURCES, now_ms=self.now)
        self.assertEqual(proofs, {})

    def test_observe_listings_preserves_venue_proofs_across_the_round_trip(self):
        carried = {"binance-new:CT": {"expiresAt": self.now + HOUR, "key": "venue-listing:x"}}
        state, _ = observe_listings({"venueProofs": carried}, [], now_ms=self.now)
        self.assertEqual(state["venueProofs"], carried)


class VenueListingParserTests(unittest.TestCase):
    # Real clock: desktop_alert_source_is_muted compares the proof expiry against
    # time.time(), so a synthetic past "now" would make every proof look expired.
    now = int(time.time() * 1000)

    def sections(self, binance_rows=None, okx_rows=None):
        return [
            {"id": "binance-new", "title": "Binance 新币榜", "group": "new-coin", "status": "ok",
             "listingInventoryComplete": True,
             "listingInventory": binance_rows or [],
             "rows": binance_rows or []},
            {"id": "okx-new", "title": "OKX 新币榜", "group": "new-coin", "status": "ok",
             "listingInventoryComplete": True,
             "listingInventory": okx_rows or [],
             "rows": okx_rows or []},
        ]

    def parse(self, sections):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, "FIRST_LISTING_ALERT_STATE_PATH", Path(directory) / "first.json"), \
             patch.object(server, "NEW_COIN_LOW_LISTING_HISTORY_PATH", Path(directory) / "history.json"), \
             patch.object(server, "first_listing_additional_inventories", return_value=[]):
            events = server.parse_site_newboard_events(
                {"updatedAt": self.now, "sections": sections}, track_listings=True)
            state = json.loads((Path(directory) / "first.json").read_text(encoding="utf-8"))
            return events, state

    def row(self, asset, stamp, rank=1):
        return {"rank": rank, "group": "new-coin", "asset": asset, "symbol": asset + "USDT",
                "date": stamp, "assetType": "crypto"}

    def test_fresh_binance_contract_produces_a_venue_labelled_popup(self):
        events, state = self.parse(self.sections(binance_rows=[self.row("CT", self.now - MINUTE)]))
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertTrue(event["key"].startswith("venue-listing:binance-new:CT:"))
        self.assertEqual(event["title"], "币安新合约｜CT")
        self.assertEqual(event["venueListing"], "币安")
        self.assertEqual(event["kind"], "新币上新")
        self.assertEqual(event["listingPolicyVersion"], 1)
        self.assertFalse(server.desktop_alert_source_is_muted(event))
        self.assertIn("binance-new:CT", state["venueProofs"])

    def test_okx_uses_its_own_label_and_stale_rows_stay_silent(self):
        events, _ = self.parse(self.sections(
            binance_rows=[self.row("OLDCOIN", self.now - 12 * HOUR)],
            okx_rows=[self.row("H100", self.now - MINUTE)],
        ))
        self.assertEqual([e["title"] for e in events], ["OKX 新合约｜H100"])

    def test_event_key_is_stable_across_cycles_so_delivery_dedupes(self):
        sections = self.sections(binance_rows=[self.row("CT", self.now - MINUTE)])
        first, _ = self.parse(sections)
        second, _ = self.parse(sections)
        self.assertEqual(first[0]["key"], second[0]["key"])

    def test_global_proof_owned_by_another_venue_cannot_suppress_the_venue_popup(self):
        """Regression: a global proof held by e.g. Gate must not re-mute Binance."""
        sections = self.sections(binance_rows=[self.row("CT", self.now - MINUTE)])
        global_proofs = {"CT": {"sourceId": "gate-new", "listedAt": self.now - MINUTE,
                                "key": "first-listing:CT:1", "expiresAt": self.now + MINUTE}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, "FIRST_LISTING_ALERT_STATE_PATH", Path(directory) / "first.json"), \
             patch.object(server, "NEW_COIN_LOW_LISTING_HISTORY_PATH", Path(directory) / "history.json"), \
             patch.object(server, "first_listing_additional_inventories", return_value=[]), \
             patch.object(server, "observe_listings", return_value=({}, global_proofs)):
            events = server.parse_site_newboard_events(
                {"updatedAt": self.now, "sections": sections}, track_listings=True)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["key"].startswith("venue-listing:binance-new:CT:"))
        self.assertEqual(events[0]["title"], "币安新合约｜CT")

    def test_expired_venue_proof_stops_emitting(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, "FIRST_LISTING_ALERT_STATE_PATH", Path(directory) / "first.json"), \
             patch.object(server, "NEW_COIN_LOW_LISTING_HISTORY_PATH", Path(directory) / "history.json"), \
             patch.object(server, "first_listing_additional_inventories", return_value=[]):
            sections = self.sections(binance_rows=[self.row("CT", self.now - MINUTE)])
            server.parse_site_newboard_events({"updatedAt": self.now, "sections": sections}, track_listings=True)
            later = server.parse_site_newboard_events(
                {"updatedAt": self.now + 30 * MINUTE, "sections": sections}, track_listings=True)
        self.assertEqual(later, [])


class VenueListingMuteTests(unittest.TestCase):
    def test_venue_key_prefix_bypasses_the_listing_policy_mute(self):
        event = {"key": "venue-listing:binance-new:CT:1", "kind": "新币上新",
                 "listingPolicyVersion": 1, "expiresAt": int(time.time() * 1000) + MINUTE}
        self.assertFalse(server.desktop_alert_source_is_muted(event))
        self.assertTrue(server.desktop_alert_source_is_muted({**event, "expiresAt": 1}))

    def test_legacy_newboard_key_is_unaffected(self):
        self.assertTrue(server.desktop_alert_source_is_muted(
            {"key": "newboard:hyperliquid-new:OLD", "kind": "新币上新"}))


if __name__ == "__main__":
    unittest.main()
