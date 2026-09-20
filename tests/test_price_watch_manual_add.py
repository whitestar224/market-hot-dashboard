import gc
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import server


class PriceWatchManualAddTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = Path(self.directory.name) / "auth.db"
        server.init_auth_db()
        self.now = int(time.time() * 1000)

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db
        gc.collect()
        self.directory.cleanup()

    def seed_niulai_rows(self):
        with closing(server.auth_db()) as conn, conn:
            conn.execute(
                """
                INSERT INTO price_watch_assets(
                    symbol, name, icon, pair_hint, onchain_chain,
                    onchain_chain_label, onchain_contract_address,
                    binance_wallet_hot_first_seen_at, binance_wallet_hot_last_seen_at,
                    status, created_at, updated_at
                ) VALUES(?, ?, '', ?, '56', 'BSC', ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    "牛来", "牛来", "BSC 0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
                    "0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
                    self.now, self.now, self.now, self.now,
                ),
            )
            conn.execute(
                """
                INSERT INTO price_watch_assets(
                    symbol, name, manual_pinned, status, created_at, updated_at
                ) VALUES('NIULAI', 'NIULAI', 1, 'pending', ?, ?)
                """,
                (self.now, self.now),
            )

    def test_transliteration_resolves_to_existing_contract_identity(self):
        self.seed_niulai_rows()
        discovery = [{"symbol": "牛来", "name": "牛来", "note": "niulaiusdt:weex"}]
        with patch.object(server, "price_watch_manual_discovery_rows", return_value=discovery):
            resolved = server.price_watch_manual_asset_resolution("niulai")

        self.assertEqual(resolved["requestedSymbol"], "NIULAI")
        self.assertEqual(resolved["symbol"], "牛来")
        self.assertEqual(resolved["name"], "牛来")
        self.assertEqual(
            resolved["identity"]["onchain_contract_address"],
            "0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
        )

    def test_add_merges_wrong_placeholder_and_returns_before_full_payload_scan(self):
        self.seed_niulai_rows()
        discovery = [{"symbol": "牛来", "name": "牛来", "note": "niulaiusdt:weex"}]
        market = {
            "source": "Binance", "sourceLabel": "Binance 合约",
            "provider": "Binance Futures", "pair": "牛来USDT",
            "listedAt": self.now - 86_400_000,
        }
        with (
            patch.object(server, "price_watch_manual_discovery_rows", return_value=discovery),
            patch.object(server, "price_watch_large_exchange_contract", return_value=market),
            patch.object(server, "price_watch_payload", side_effect=AssertionError("full payload must not block add")),
            patch.object(server.threading.Thread, "start", return_value=None),
        ):
            result = server.add_price_watch_symbol("niulai")

        self.assertTrue(result["ok"])
        self.assertEqual(result["addition"]["symbol"], "牛来")
        self.assertEqual(result["addition"]["name"], "牛来")
        self.assertEqual(result["addition"]["replacedSymbol"], "NIULAI")
        self.assertEqual(result["addition"]["marketProvider"], "Binance Futures")
        self.assertEqual(result["addition"]["marketPair"], "牛来USDT")
        with closing(server.auth_db()) as conn:
            canonical = conn.execute(
                """
                SELECT manual_pinned, new_contract_source, new_contract_pair,
                       onchain_contract_address
                FROM price_watch_assets WHERE symbol = '牛来'
                """
            ).fetchone()
            placeholder = conn.execute(
                "SELECT 1 FROM price_watch_assets WHERE symbol = 'NIULAI'"
            ).fetchone()
        self.assertEqual(canonical["manual_pinned"], 1)
        self.assertEqual(canonical["new_contract_source"], "Binance 合约")
        self.assertEqual(canonical["new_contract_pair"], "牛来USDT")
        self.assertTrue(canonical["onchain_contract_address"].startswith("0xbeea"))
        self.assertIsNone(placeholder)

    def test_binance_native_symbol_and_verified_contract_take_priority(self):
        self.assertEqual(server.binance_price_watch_pair("牛来"), "牛来USDT")
        row = {
            "symbol": "牛来",
            "onchain_chain": "56",
            "onchain_contract_address": "0xbeea1d618e533a387d941f58a7d4c9b7bd377777",
            "new_contract_source": "Binance 合约",
            "new_contract_pair": "牛来USDT",
        }
        self.assertEqual(server.price_watch_explicit_market_source(row), "binance")
        start = 1_800_000_000_000
        hourly = [(start + index * 3_600_000, 10.0, 9.5) for index in range(24)]
        onchain = (
            "链上多源 K线",
            lambda: (_ for _ in ()).throw(AssertionError("verified Binance must run first")),
            lambda: ([], "链上多源 K线"),
            lambda: ([], "链上多源 K线"),
        )
        with (
            patch.object(server, "price_watch_candles_from_binance", return_value=(hourly, "Binance Futures")) as binance,
            patch.object(server, "price_watch_daily_candles_from_binance", return_value=([], "Binance Futures")),
            patch.object(server, "price_watch_extended_snapshot_providers", return_value=[onchain]),
            patch.object(server, "PRICE_STRUCTURE_PROVIDER_PREFERENCE", {}),
        ):
            result = server.fetch_price_watch_snapshot(row)

        self.assertEqual(result["provider"], "Binance Futures")
        self.assertEqual(result["currentPrice"], 9.5)
        self.assertEqual(binance.call_count, 2)

    def test_newly_verified_exchange_listing_replaces_stale_onchain_source(self):
        start = 1_800_000_000_000
        hourly = [
            (start + index * 3_600_000, 0.04421, 0.04020, 0.04000, 0.03900, 100)
            for index in range(24)
        ]
        market = {
            "source": "Binance", "sourceLabel": "Binance 合约",
            "provider": "Binance Futures", "pair": "哈基米USDT",
            "listedAt": self.now - 86_400_000,
        }
        row = {
            "symbol": "哈基米",
            "onchain_chain": "56",
            "onchain_contract_address": "0x82ec31d69b3c289e541b50e30681fd1acad24444",
            "new_contract_source": "",
            "new_contract_pair": "",
        }
        onchain = (
            "链上多源 K线",
            lambda: (_ for _ in ()).throw(AssertionError("verified Binance must run first")),
            lambda: ([], "链上多源 K线"),
            lambda: ([], "链上多源 K线"),
        )
        setup = {
            "qualified": True, "priorHighConfirmed": True,
            "type": "structure-level", "referenceHigh": 0.04421,
        }
        with (
            patch.object(server, "price_watch_large_exchange_contract", return_value=market),
            patch.object(server, "price_watch_candles_from_binance", return_value=(hourly, "Binance Futures")) as binance,
            patch.object(server, "price_watch_daily_candles_from_binance", return_value=([], "Binance Futures")),
            patch.object(server, "price_watch_extended_snapshot_providers", return_value=[onchain]),
            patch.object(server, "price_watch_prior_high_setup", return_value=setup),
            patch.object(server, "PRICE_STRUCTURE_PROVIDER_PREFERENCE", {}),
        ):
            result = server.fetch_price_watch_snapshot(row)

        self.assertEqual(result["provider"], "Binance Futures")
        self.assertEqual(result["weekHigh"], 0.04421)
        self.assertEqual(result["resolvedMarket"]["pair"], "哈基米USDT")
        self.assertTrue(all(call.args[0] == "哈基米" for call in binance.call_args_list))

    def test_http_add_queue_acknowledges_before_background_write(self):
        captured = {}

        class DeferredThread:
            def __init__(self, *, target, **_kwargs):
                captured["target"] = target

            def start(self):
                captured["started"] = True

        completed = {
            "ok": True,
            "_skipIdentityEnrichment": True,
            "addition": {"symbol": "牛来", "name": "牛来", "status": "pending"},
        }
        with server.PRICE_WATCH_ADD_JOBS_LOCK:
            server.PRICE_WATCH_ADD_JOBS.clear()
        with (
            patch.object(server.threading, "Thread", DeferredThread),
            patch.object(server, "add_price_watch_symbol", return_value=completed),
        ):
            queued = server.queue_price_watch_symbol_add("niulai")
            self.assertTrue(captured.get("started"))
            self.assertEqual(queued["addition"]["status"], "adding")
            self.assertNotIn("item", queued["addition"])
            captured["target"]()
            status = server.price_watch_symbol_add_job(queued["addition"]["jobId"])

        self.assertEqual(status["job"]["status"], "complete")
        self.assertEqual(status["addition"]["symbol"], "牛来")
        self.assertNotIn("_skipIdentityEnrichment", completed)


class PriceWatchManualAddFrontendTests(unittest.TestCase):
    def test_add_button_has_independent_visible_loading_state(self):
        root = Path(server.ROOT)
        html = (root / "price-watch.html").read_text(encoding="utf-8")
        script = (root / "price-watch.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="watchAddButton"', html)
        self.assertIn('role="status" aria-live="polite"', html)
        self.assertIn("let adding = false", script)
        self.assertIn("if (!symbol || adding) return", script)
        self.assertIn('addButton.textContent = busy ? "正在添加…" : "添加监控"', script)
        self.assertIn("waitForAddJob", script)
        self.assertIn("/api/price-watch/add-status", script)
        self.assertIn("price-watch-add-spin", styles)


if __name__ == "__main__":
    unittest.main()
