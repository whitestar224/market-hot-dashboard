from contextlib import closing
import copy
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock

from monitor_identity import MonitorIdentityRegistry, target_identity
from monitor_buy import MonitorBuyService, SOLANA

CA = "0x" + "ab" * 20
ROW = {"symbol": "TEST", "chain": "bsc", "contractAddress": CA}
TARGET = {"symbol": "TEST", "chainId": 56, "address": CA, "decimals": 18, "chainLabel": "BNB", "vmType": "evm"}


class MonitorIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.resolver = Mock(return_value=copy.deepcopy(TARGET))
        self.registry = MonitorIdentityRegistry(Path(self.temp.name) / "identities.sqlite", self.resolver)
        self.registry.start()

    def tearDown(self):
        self.registry.stop()
        self.temp.cleanup()

    def ready(self, row=None):
        self.registry.snapshot(row or ROW)
        self.registry.jobs.join()
        return self.registry.snapshot(row or ROW, enqueue=False)

    def test_intake_verifies_in_background_and_persists_across_restart(self):
        result = self.ready()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["target"]["identityKey"], "56:" + CA)
        self.resolver.assert_called_once_with({"chainId": 56, "address": CA, "kind": "token"})
        self.registry.stop()
        self.registry = MonitorIdentityRegistry(self.registry.path, self.resolver)
        self.registry.start()
        self.assertEqual(self.registry.require(result["target"]), result["target"])
        self.resolver.assert_called_once()

    def test_only_explicit_exact_chain_and_ca_are_used(self):
        self.assertEqual(target_identity({"tradeUrl": f"https://web3.binance.com/en/token/bsc/{CA}"}),
                         {"chainId": 56, "address": CA, "kind": "token"})
        for row in [{"symbol": "TEST"}, {**ROW, "chainId": 1}, {**ROW, "tokenAddress": "0x" + "cd" * 20},
                    {**ROW, "tradeUrl": f"https://web3.binance.com/en/token/ethereum/{CA}"}, {**ROW, "kind": "nft"},
                    {**ROW, "contractAddress": "not-a-ca"}]:
            self.assertEqual(self.registry.snapshot(row)["status"], "unresolved")
        self.resolver.assert_not_called()

    def test_same_name_other_chain_cannot_reuse_identity_and_solana_preserves_case(self):
        self.ready()
        self.assertEqual(self.registry.snapshot({**ROW, "chain": "ethereum"}, enqueue=False)["status"], "pending")
        ca = "5ExRQUbJiZysXWG7KapGwsQmjYvx4hCeBgwAGvht5qab"
        self.assertEqual(target_identity({"chain": "solana", "contractAddress": ca})["address"], ca)
        self.assertEqual(target_identity({"chain": "solana", "chainId": 501, "contractAddress": ca})["chainId"], SOLANA)

    def test_symbol_mismatch_and_expiry_block_without_remote_lookup(self):
        result = self.ready()
        self.assertEqual(self.registry.snapshot({**ROW, "symbol": "OTHER"})["status"], "conflict")
        self.assertEqual(self.registry.snapshot({**ROW, "symbol": "TESTUSDT"})["status"], "verified")
        self.assertEqual(self.registry.snapshot({**ROW, "symbol": "USDT"})["status"], "conflict")
        for payload in [{**result["target"], "identityKey": "forged"}, {**result["target"], "decimals": 6},
                        {**result["target"], "chainId": 1}, {**result["target"], "symbol": "OTHER"}]:
            with self.assertRaises(ValueError): self.registry.require(payload)
        self.registry.records[result["key"]]["expiresAt"] = time.time() - 1
        with self.assertRaises(ValueError): self.registry.require(result["target"])
        self.resolver.assert_called_once()

    def test_network_failure_is_fail_closed_retryable_and_never_kills_workers(self):
        self.resolver.side_effect = TimeoutError("private endpoint must not leak")
        result = self.ready()
        self.assertEqual(result["status"], "retrying")
        self.assertNotIn("private", result["reason"])
        self.ready()
        self.resolver.assert_called_once()
        self.registry.records[result["key"]]["retryAt"] = 0
        self.resolver.side_effect = None
        self.assertEqual(self.ready()["status"], "verified")

    def test_resolver_cannot_change_chain_ca_or_precision(self):
        for changed in ({"chainId": 1}, {"address": "0x" + "ef" * 20}, {"decimals": 99}):
            self.registry.records.clear()
            self.resolver.return_value = {**TARGET, **changed}
            self.assertNotEqual(self.ready()["status"], "verified")

    def test_nested_monitor_targets_are_enriched_without_mutating_or_trusting_supplied_proofs(self):
        self.ready()
        payload = {"news": {"targets": [ROW]}, "items": [{"symbol": "UNKNOWN", "buyIdentity": {"status": "verified"}}]}
        original = copy.deepcopy(payload)
        enriched = self.registry.enrich(payload)
        self.assertEqual(enriched["news"]["targets"][0]["buyIdentity"]["status"], "verified")
        self.assertEqual(enriched["items"][0]["buyIdentity"]["status"], "unresolved")
        self.assertEqual(payload, original)

    def test_repeated_intake_is_deduplicated_and_no_wallet_methods_are_used(self):
        for _ in range(50): self.registry.observe({"items": [ROW, ROW]})
        self.registry.jobs.join()
        self.resolver.assert_called_once()
        with closing(sqlite3.connect(self.registry.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0], 1)

    def test_full_queue_does_not_block_intake_or_mark_overflow_as_scheduled(self):
        registry = MonitorIdentityRegistry(self.registry.path, self.resolver, capacity=1)
        registry.running = True  # Deliberately do not consume this isolated queue.
        registry.snapshot(ROW)
        other = {**ROW, "contractAddress": "0x" + "cd" * 20}
        self.assertEqual(registry.snapshot(other)["status"], "pending")
        self.assertEqual(registry.jobs.qsize(), 1)
        self.assertEqual(len(registry.pending), 1)
        self.resolver.assert_not_called()

    def test_visible_identity_has_reserved_capacity_when_background_queue_is_full(self):
        registry = MonitorIdentityRegistry(self.registry.path, self.resolver, capacity=1)
        registry.running = True  # Isolated queue admission test; no consumers.
        registry.snapshot(ROW)
        visible = {**ROW, "contractAddress": "0x" + "cd" * 20, "researchTier": "news-triggered"}
        enriched = registry.enrich({"dailyResearch": {"selected": [visible]}})
        self.assertEqual(registry.jobs.qsize(), 1)
        self.assertEqual(registry.urgent_jobs.qsize(), 1)
        self.assertEqual(enriched["dailyResearch"]["selected"][0]["buyIdentity"]["status"], "pending")

    def test_background_sweep_does_not_requeue_old_failures_but_visible_row_can_retry(self):
        registry = MonitorIdentityRegistry(self.registry.path, self.resolver, capacity=2)
        registry.running = True
        key = registry.key(target_identity(ROW))
        registry.records[key] = {"status": "retrying", "reason": "稍后重试", "retryAt": 0}
        registry.observe({"items": [ROW]})
        self.assertEqual(registry.jobs.qsize(), 0)
        registry.enrich({"items": [ROW]})
        self.assertEqual(registry.urgent_jobs.qsize(), 1)
        self.assertEqual(registry.pending[key], 0)

    def test_background_refresh_failure_preserves_unexpired_identity_but_metadata_change_blocks(self):
        result = self.ready()
        self.registry.records[result["key"]]["verifiedAt"] -= self.registry.REFRESH + 1
        self.resolver.side_effect = TimeoutError()
        self.assertEqual(self.ready()["status"], "verified")
        self.registry.records[result["key"]]["retryAt"] = 0
        self.resolver.side_effect = None
        self.resolver.return_value = {**TARGET, "decimals": 6}
        self.assertEqual(self.ready()["status"], "conflict")

    def test_corrupt_saved_record_does_not_break_other_monitors(self):
        self.registry.stop()
        with closing(sqlite3.connect(self.registry.path)) as conn:
            conn.execute("INSERT INTO identities VALUES (?, ?)", ("bad", "not json"))
            conn.commit()
        self.registry = MonitorIdentityRegistry(self.registry.path, self.resolver)
        self.registry.start()
        self.assertEqual(self.ready()["status"], "verified")

    def test_quote_requires_persisted_identity_before_any_balance_or_price_request(self):
        service = MonitorBuyService(Path(self.temp.name) / "buy.sqlite")
        service.http = Mock(side_effect=AssertionError("no network during identity check"))
        service.resolve = Mock(side_effect=AssertionError("no buy-time CA lookup"))
        try:
            with self.assertRaises(ValueError): service.quote({"target": TARGET, "amountUsdt": "10"})
            service.http.assert_not_called()
            service.resolve.assert_not_called()
            self.assertFalse(service.path.exists())
        finally:
            service.pool.shutdown(wait=True)

    def test_new_scan_enrolls_identity_without_opening_monitor_or_buy_page(self):
        from chain_ecosystem_monitor import ChainEcosystemStore
        store = ChainEcosystemStore(Path(self.temp.name) / "chain.sqlite")
        store.initialize()
        store.identity_observer = self.registry.observe
        candidate = {"network": "bsc", "contractAddress": CA, "symbol": "TEST", "decision": "watch"}
        store.save_onchain_research_scan([candidate])
        self.registry.jobs.join()
        self.assertEqual(self.registry.snapshot(candidate, enqueue=False)["status"], "verified")
        self.assertEqual(store.onchain_identity_rows()[0]["contractAddress"], CA)


if __name__ == "__main__":
    unittest.main()
