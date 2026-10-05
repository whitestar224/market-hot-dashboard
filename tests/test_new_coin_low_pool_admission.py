import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server

DAY_MS = 24 * 60 * 60 * 1000


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class NewCoinLowPoolAdmissionTests(unittest.TestCase):
    """Binance / OKX new listings always join the pool; TradFi contracts never do."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original = {
            "AUTH_DB_PATH": server.AUTH_DB_PATH,
            "PRICE_STRUCTURE_SNAPSHOT_PATH": server.PRICE_STRUCTURE_SNAPSHOT_PATH,
            "NEW_COIN_LOW_SNAPSHOT_PATH": server.NEW_COIN_LOW_SNAPSHOT_PATH,
            "NEW_COIN_LOW_LISTING_HISTORY_PATH": server.NEW_COIN_LOW_LISTING_HISTORY_PATH,
        }
        server.AUTH_DB_PATH = self.root / "auth.db"
        server.PRICE_STRUCTURE_SNAPSHOT_PATH = self.root / "structure.json"
        server.NEW_COIN_LOW_SNAPSHOT_PATH = self.root / "new-low.json"
        server.NEW_COIN_LOW_LISTING_HISTORY_PATH = self.root / "listing-history.json"
        server.init_auth_db()
        server.NEW_COIN_LOW_INVENTORY_CACHE = None
        server.NEW_COIN_LOW_ACTIVITY_CACHE = None
        server.NEW_COIN_LOW_ACTIVITY_SUMMARY = {}

    def tearDown(self):
        for name, value in self.original.items():
            setattr(server, name, value)
        server.NEW_COIN_LOW_INVENTORY_CACHE = None
        server.NEW_COIN_LOW_ACTIVITY_CACHE = None
        server.NEW_COIN_LOW_ACTIVITY_SUMMARY = {}
        gc.collect()
        self.tempdir.cleanup()

    @staticmethod
    def listed_days_ago(days):
        return int(time.time() * 1000) - int(days * DAY_MS)

    # --- venue contract classification -------------------------------------

    def test_binance_tradfi_marker_is_the_contract_type(self):
        self.assertTrue(server.binance_contract_is_tradfi(
            {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"}
        ))
        self.assertTrue(server.binance_contract_is_tradfi(
            {"contractType": "PERPETUAL", "underlyingType": "COIN", "underlyingSubType": ["TradFi"]}
        ))
        # A crypto index product also reports underlyingType == "INDEX", so the
        # underlying type alone must never be treated as a TradFi signal.
        self.assertFalse(server.binance_contract_is_tradfi(
            {"contractType": "PERPETUAL", "underlyingType": "INDEX", "underlyingSubType": ["crypto", "index"]}
        ))
        self.assertFalse(server.binance_contract_is_tradfi(
            {"contractType": "PERPETUAL", "underlyingType": "COIN", "underlyingSubType": ["Meme", "Crypto"]}
        ))

    def test_okx_tradfi_marker_covers_equities_and_commodities(self):
        self.assertTrue(server.okx_contract_is_tradfi({"instCategory": "3"}))
        self.assertTrue(server.okx_contract_is_tradfi({"instCategory": "4"}))
        self.assertFalse(server.okx_contract_is_tradfi({"instCategory": "1"}))

    def test_gate_and_htx_use_contract_type_and_labels(self):
        self.assertTrue(server.gate_contract_is_tradfi({"contract_type": "stocks"}))
        self.assertFalse(server.gate_contract_is_tradfi({"contract_type": ""}))
        self.assertTrue(server.htx_contract_is_tradfi({"tradfi_labels": ["Stocks"]}))
        # HTX leaves tradfi_labels empty for newer stock listings.
        self.assertTrue(server.htx_contract_is_tradfi({"labels": ["stock"], "tradfi_labels": []}))
        self.assertFalse(server.htx_contract_is_tradfi({"labels": ["common"], "tradfi_labels": []}))
        self.assertFalse(server.htx_contract_is_tradfi({"labels": ["hot", "common"], "tradfi_labels": []}))

    def test_tradfi_symbol_only_excluded_when_no_admission_venue_lists_it_as_crypto(self):
        self.assertTrue(server.new_coin_low_symbol_excluded_as_tradfi(
            "AAPL", {"AAPL"}, set()
        ))
        # OKX lists BB as a tokenised equity while Binance lists the same ticker
        # as a crypto perpetual: the crypto listing wins.
        self.assertFalse(server.new_coin_low_symbol_excluded_as_tradfi(
            "BB", {"BB"}, {"BB"}
        ))
        self.assertFalse(server.new_coin_low_symbol_excluded_as_tradfi("CT", set(), set()))

    # --- turnover gate exemption -------------------------------------------

    def test_binance_and_okx_rows_bypass_the_turnover_gate(self):
        row = {"symbol": "ZZCOIN", "newCoinSources": ["Binance", "Gate"], "newCoinListedAt": self.listed_days_ago(5)}
        self.assertTrue(server.new_coin_low_admission_venue_priority(row))
        state = server.new_coin_low_activity_state(row, {"ZZCOIN": {"turnover24hUsd": 1000.0, "source": "Gate"}})
        self.assertTrue(state["active"])
        self.assertEqual(state["reason"], "admission-venue-priority")

        okx_row = {"symbol": "ZZOKX", "newCoinSource": "OKX", "newCoinListedAt": self.listed_days_ago(5)}
        self.assertTrue(server.new_coin_low_admission_venue_priority(okx_row))
        self.assertTrue(server.new_coin_low_activity_state(okx_row, {})["active"])

    def test_gate_only_rows_still_respect_the_turnover_gate(self):
        row = {"symbol": "ZZGATE", "newCoinSources": ["Gate"], "newCoinListedAt": self.listed_days_ago(5)}
        self.assertFalse(server.new_coin_low_admission_venue_priority(row))
        thin = server.new_coin_low_activity_state(row, {"ZZGATE": {"turnover24hUsd": 1000.0, "source": "Gate"}})
        self.assertFalse(thin["active"])
        self.assertEqual(thin["reason"], "turnover-below-threshold")
        fat = server.new_coin_low_activity_state(
            row, {"ZZGATE": {"turnover24hUsd": server.NEW_COIN_LOW_MIN_TURNOVER_24H_USD + 1, "source": "Gate"}}
        )
        self.assertTrue(fat["active"])

    # --- inventory end to end ---------------------------------------------

    def venue_payloads(self):
        crypto_at = self.listed_days_ago(5)
        return {
            "binance": {"symbols": [
                # crypto perpetual -> must join the pool even with no turnover
                {"symbol": "ZZCOINUSDT", "baseAsset": "ZZCOIN", "quoteAsset": "USDT",
                 "onboardDate": crypto_at, "contractType": "PERPETUAL",
                 "underlyingType": "COIN", "underlyingSubType": ["Meme", "Crypto"]},
                # stock perpetual -> TradFi, must never join
                {"symbol": "ZZEQTYUSDT", "baseAsset": "ZZEQTY", "quoteAsset": "USDT",
                 "onboardDate": crypto_at, "contractType": "TRADIFI_PERPETUAL",
                 "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]},
                # dual-classified: TradFi on OKX, crypto here -> crypto wins
                {"symbol": "ZZDUALUSDT", "baseAsset": "ZZDUAL", "quoteAsset": "USDT",
                 "onboardDate": crypto_at, "contractType": "PERPETUAL",
                 "underlyingType": "COIN", "underlyingSubType": ["DeFi", "Crypto"]},
                # forex pair, also marked TradFi on Binance
                {"symbol": "EURUSDUSDT", "baseAsset": "EURUSD", "quoteAsset": "USDT",
                 "onboardDate": crypto_at, "contractType": "TRADIFI_PERPETUAL",
                 "underlyingType": "FX", "underlyingSubType": ["TradFi"]},
            ]},
            "okx": {"data": [
                {"instId": "ZZOKX-USDT-SWAP", "instCategory": "1", "listTime": self.listed_days_ago(4)},
                {"instId": "ZZOIL-USDT-SWAP", "instCategory": "4", "listTime": self.listed_days_ago(4)},
                {"instId": "ZZDUAL-USDT-SWAP", "instCategory": "3", "listTime": self.listed_days_ago(4)},
            ]},
            "gate": [
                {"name": "ZZGATE_USDT", "contract_type": "", "launch_time": self.listed_days_ago(6) // 1000},
                {"name": "ZZBIG_USDT", "contract_type": "", "launch_time": self.listed_days_ago(6) // 1000},
                {"name": "ZZHXEQ_USDT", "contract_type": "stocks", "launch_time": self.listed_days_ago(6) // 1000},
            ],
            "htx": {"data": [
                {"symbol": "EURUSD", "contract_code": "EURUSD-USDT", "create_date": "20260909",
                 "labels": ["common"], "tradfi_labels": []},
                {"symbol": "ZZHXEQ", "contract_code": "ZZHXEQ-USDT", "create_date": "20260909",
                 "labels": ["stock"], "tradfi_labels": []},
            ]},
        }

    def fake_get(self, url, **_kwargs):
        payloads = self.venue_payloads()
        if "fapi.binance.com/fapi/v1/exchangeInfo" in url:
            return FakeResponse(payloads["binance"])
        if "okx.com/api/v5/public/instruments" in url:
            return FakeResponse(payloads["okx"])
        if "api.gateio.ws/api/v4/futures/usdt/contracts" in url:
            return FakeResponse(payloads["gate"])
        if "api.hbdm.com/linear-swap-api/v1/swap_contract_info" in url:
            return FakeResponse(payloads["htx"])
        if "api.bitget.com/api/v2/spot/public/symbols" in url:
            return FakeResponse({"data": []})
        raise AssertionError(f"unexpected url: {url}")

    def build_inventory(self, activity):
        with patch.object(server.requests, "get", side_effect=self.fake_get), \
                patch.object(server, "aster_contract_rows", return_value=[]), \
                patch.object(server, "fetch_new_coin_low_market_activity", return_value=activity), \
                patch.object(server, "price_structure_known_asset_metadata", return_value={}):
            return server.new_coin_low_inventory_rows(force_refresh=True)

    def test_pool_admits_binance_and_okx_listings_and_rejects_tradfi(self):
        rows = self.build_inventory({
            "ZZGATE": {"turnover24hUsd": 1000.0, "source": "Gate"},
            "ZZBIG": {"turnover24hUsd": 50_000_000.0, "source": "Gate"},
        })
        symbols = {row["symbol"] for row in rows}
        # Binance / OKX listings are monitored without clearing the turnover gate.
        self.assertIn("ZZCOIN", symbols)
        self.assertIn("ZZOKX", symbols)
        # A crypto listing wins over another venue's TradFi listing of the same ticker.
        self.assertIn("ZZDUAL", symbols)
        # Gate's own listing still has to clear the gate, and does when it does.
        self.assertNotIn("ZZGATE", symbols)
        self.assertIn("ZZBIG", symbols)
        # TradFi never joins: stock / commodity / FX contracts.
        for tradi in ("ZZEQTY", "ZZOIL", "ZZHXEQ", "EURUSD"):
            self.assertNotIn(tradi, symbols)
        # ZZDUAL is TradFi on OKX but a crypto perpetual on Binance, so it is
        # rescued and must not be reported as an exclusion.
        self.assertEqual(server.NEW_COIN_LOW_ACTIVITY_SUMMARY["tradfiExcluded"], 4)

    def test_manual_exclusion_keeps_a_live_contract_out_of_the_pool(self):
        rows = self.build_inventory({})
        self.assertIn("ZZCOIN", {row["symbol"] for row in rows})
        with server.auth_db() as conn:
            conn.execute(
                "INSERT INTO price_structure_exclusions (symbol, excluded_at, absent_at, updated_at) "
                "VALUES ('ZZCOIN', ?, 0, ?)",
                (int(time.time() * 1000), int(time.time() * 1000)),
            )
        filtered = server.new_coin_low_apply_monitor_preferences([dict(row) for row in rows])
        self.assertNotIn("ZZCOIN", {row["symbol"] for row in filtered})

    # --- new-contract monitor pool ----------------------------------------

    def test_gate_and_htx_tradfi_listings_never_enter_the_new_contract_pool(self):
        listed_at = self.listed_days_ago(1)
        now_ms = int(time.time() * 1000)
        sources = [
            {
                "id": "gate-new",
                "status": "ok",
                "monitorSourceLabel": "Gate 新合约",
                "rows": [
                    {"asset": "ZZGATE", "symbol": "ZZGATE_USDT", "date": listed_at,
                     "assetType": "crypto", "tags": ["Gate 新合约", "永续"]},
                    {"asset": "ZZGATEEQ", "symbol": "ZZGATEEQ_USDT", "date": listed_at,
                     "assetType": "tradfi", "tags": ["Gate 新合约", "TradFi"]},
                ],
            },
            {
                "id": "htx-new",
                "status": "ok",
                "monitorSourceLabel": "HTX 新合约",
                "rows": [
                    {"asset": "ZZHX", "symbol": "ZZHX-USDT", "date": listed_at,
                     "assetType": "crypto", "tags": ["HTX 新合约"]},
                    {"asset": "ZZHXEQ", "symbol": "ZZHXEQ-USDT", "date": listed_at,
                     "assetType": "tradfi", "tags": ["TradFi"]},
                ],
            },
        ]
        server.sync_price_watch_new_contract_candidates(now_ms=now_ms, sources=sources)
        with server.auth_db() as conn:
            symbols = {
                row["symbol"]
                for row in conn.execute(
                    "SELECT symbol FROM price_watch_assets WHERE new_contract_listed_at > 0"
                )
            }
        self.assertIn("ZZGATE", symbols)
        self.assertIn("ZZHX", symbols)
        # TradFi members of the same board must stay out of the coin monitor pool.
        self.assertNotIn("ZZGATEEQ", symbols)
        self.assertNotIn("ZZHXEQ", symbols)


if __name__ == "__main__":
    unittest.main()
