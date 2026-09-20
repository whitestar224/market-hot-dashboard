import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_money_monitor import (
    SmartMoneyMonitor,
    SmartMoneyStore,
    classify_evm_buy,
    classify_solana_buy,
    extract_smart_money_mentions,
    normalize_chain,
    normalize_wallet_address,
)


EVM_WALLET = "0x6078ee8a93697c6d67863fcbff77141d9ab358b2"
EVM_OTHER = "0xa0ed47a5dc1017db87d3807ecd95750b3ea0ff85"
TOKEN = "0x1111111111111111111111111111111111111111"
USDT = "0x2222222222222222222222222222222222222222"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SOL_WALLET = "So11111111111111111111111111111111111111112"
BONK_GUY_EVM_WALLET = "0x0a6ebed0155edb4b21d92ad02897a626cd90119e"
BONK_GUY_SOL_WALLET = "2heJbC32Tpfcb3nbUb5ER61K11FGZVfVGtVnDm6LDogF"
BONK_GUY_USELESS_WALLET = "5M8ACGKEXG1ojKDTMH3sMqhTihTgHYMSsZc6W8i7QW3Y"
SOL_TOKEN = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6JgB263QP6Bpump"
SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def topic_address(value):
    return "0x" + "0" * 24 + value[2:].lower()


def transfer_log(token, sender, recipient, amount):
    return {
        "address": token,
        "topics": [TRANSFER_TOPIC, topic_address(sender), topic_address(recipient)],
        "data": hex(amount),
    }


class SmartMoneyRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SmartMoneyStore(Path(self.tmp.name) / "smart-money.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_normalizes_supported_chain_aliases(self):
        self.assertEqual(normalize_chain("ETH"), "ethereum")
        self.assertEqual(normalize_chain("BNB Smart Chain"), "bsc")
        self.assertEqual(normalize_chain("SOL"), "solana")
        self.assertEqual(normalize_chain("Robinhood Chain"), "robinhood")
        self.assertEqual(normalize_chain("unknown"), "")

    def test_validates_evm_and_solana_addresses(self):
        self.assertEqual(normalize_wallet_address("bsc", EVM_WALLET.upper()), EVM_WALLET)
        self.assertEqual(normalize_wallet_address("solana", SOL_WALLET), SOL_WALLET)
        with self.assertRaisesRegex(ValueError, "钱包地址"):
            normalize_wallet_address("ethereum", "0x123")
        with self.assertRaisesRegex(ValueError, "Solana"):
            normalize_wallet_address("solana", "not-a-solana-wallet")

    def test_upsert_is_unique_per_chain_and_address(self):
        first = self.store.upsert_wallet("bsc", EVM_WALLET, nickname="Alpha", source_name="群聊")
        second = self.store.upsert_wallet("BSC", EVM_WALLET.upper(), nickname="Beta", source_name="个人 X")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["nickname"], "Beta")
        self.assertEqual(second["sourceName"], "个人 X")
        self.assertEqual(len(self.store.list_wallets()), 1)

    def test_extracts_only_explicit_smart_money_wallet_mentions(self):
        text = f"BSC 聪明钱地址：{EVM_WALLET}，后续重点观察"
        rows = extract_smart_money_mentions(text, source_name="Inq5️⃣连杆")
        self.assertEqual([(row["chain"], row["address"]) for row in rows], [("bsc", EVM_WALLET)])
        self.assertEqual(rows[0]["sourceName"], "Inq5️⃣连杆")

        self.assertEqual(extract_smart_money_mentions(f"地址 {EVM_WALLET}"), [])
        self.assertEqual(extract_smart_money_mentions(f"聪明钱买入，CA：{TOKEN}"), [])
        self.assertEqual(extract_smart_money_mentions("某巨鲸增持 100 万美元，但未提供钱包"), [])

    def test_ambiguous_evm_smart_money_address_expands_to_four_evm_chains(self):
        rows = extract_smart_money_mentions(f"Smart Money wallet {EVM_WALLET}", source_name="News Trade")
        self.assertEqual(
            {row["chain"] for row in rows},
            {"ethereum", "bsc", "base", "robinhood"},
        )


class SmartMoneyClassificationTests(unittest.TestCase):
    def token_meta(self, address):
        if address.lower() == USDT:
            return {"symbol": "USDT", "decimals": 6, "priceUsd": 1, "stable": True}
        return {"symbol": "MOON", "decimals": 18, "priceUsd": 0.02, "stable": False}

    def test_evm_swap_is_classified_from_net_token_flows(self):
        receipt = {
            "status": "0x1",
            "transactionHash": "0xabc",
            "blockNumber": "0x10",
            "logs": [
                transfer_log(USDT, EVM_WALLET, EVM_OTHER, 10_500 * 10**6),
                transfer_log(TOKEN, EVM_OTHER, EVM_WALLET, 600_000 * 10**18),
            ],
        }
        event = classify_evm_buy(
            "bsc",
            EVM_WALLET,
            {"hash": "0xabc", "from": EVM_WALLET, "to": EVM_OTHER, "value": "0x0"},
            receipt,
            self.token_meta,
            native_price_usd=600,
            threshold_usd=10_000,
        )
        self.assertEqual(event["tokenAddress"], TOKEN)
        self.assertEqual(event["symbol"], "MOON")
        self.assertAlmostEqual(event["paymentUsd"], 10_500)
        self.assertTrue(event["popupEligible"])

    def test_evm_non_buys_are_rejected(self):
        failed = {"status": "0x0", "logs": []}
        self.assertIsNone(classify_evm_buy("bsc", EVM_WALLET, {"value": "0x0"}, failed, self.token_meta))

        airdrop = {
            "status": "0x1", "logs": [transfer_log(TOKEN, EVM_OTHER, EVM_WALLET, 10**18)]
        }
        self.assertIsNone(classify_evm_buy("bsc", EVM_WALLET, {"value": "0x0"}, airdrop, self.token_meta))

        transfer = {
            "status": "0x1", "logs": [transfer_log(USDT, EVM_WALLET, EVM_OTHER, 20_000 * 10**6)]
        }
        self.assertIsNone(classify_evm_buy("bsc", EVM_WALLET, {"value": "0x0"}, transfer, self.token_meta))

    def test_threshold_is_inclusive_and_small_buy_is_record_only(self):
        def classify(amount):
            receipt = {
                "status": "0x1",
                "transactionHash": f"0x{amount}",
                "logs": [
                    transfer_log(USDT, EVM_WALLET, EVM_OTHER, amount * 10**6),
                    transfer_log(TOKEN, EVM_OTHER, EVM_WALLET, 10**18),
                ],
            }
            return classify_evm_buy(
                "ethereum", EVM_WALLET, {"value": "0x0"}, receipt,
                self.token_meta, threshold_usd=10_000,
            )

        self.assertFalse(classify(9_999)["popupEligible"])
        self.assertTrue(classify(10_000)["popupEligible"])

    def test_solana_swap_uses_pre_and_post_wallet_balances(self):
        payload = {
            "slot": 101,
            "transaction": {"signatures": ["sig-1"]},
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": [2_000_000_000],
                "postBalances": [1_999_995_000],
                "preTokenBalances": [
                    {"accountIndex": 2, "owner": SOL_WALLET, "mint": SOL_USDC,
                     "uiTokenAmount": {"uiAmountString": "20000", "decimals": 6}},
                    {"accountIndex": 3, "owner": SOL_WALLET, "mint": SOL_TOKEN,
                     "uiTokenAmount": {"uiAmountString": "0", "decimals": 5}},
                ],
                "postTokenBalances": [
                    {"accountIndex": 2, "owner": SOL_WALLET, "mint": SOL_USDC,
                     "uiTokenAmount": {"uiAmountString": "9000", "decimals": 6}},
                    {"accountIndex": 3, "owner": SOL_WALLET, "mint": SOL_TOKEN,
                     "uiTokenAmount": {"uiAmountString": "500000", "decimals": 5}},
                ],
            },
        }
        event = classify_solana_buy(
            SOL_WALLET,
            payload,
            token_metadata=lambda mint: {"symbol": "USDC" if mint == SOL_USDC else "BONK"},
            threshold_usd=10_000,
        )
        self.assertEqual(event["tokenAddress"], SOL_TOKEN)
        self.assertAlmostEqual(event["paymentUsd"], 11_000)
        self.assertTrue(event["popupEligible"])

    def test_solana_native_spend_only_uses_the_watched_wallet_account(self):
        payload = {
            "slot": 102,
            "transaction": {
                "signatures": ["sig-native"],
                "message": {"accountKeys": [EVM_OTHER, {"pubkey": SOL_WALLET}]},
            },
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": [1_000_000_000_000, 10_000_000_000],
                "postBalances": [0, 9_000_000_000],
                "preTokenBalances": [
                    {"owner": SOL_WALLET, "mint": SOL_TOKEN, "uiTokenAmount": {"uiAmountString": "0"}},
                ],
                "postTokenBalances": [
                    {"owner": SOL_WALLET, "mint": SOL_TOKEN, "uiTokenAmount": {"uiAmountString": "500"}},
                ],
            },
        }
        event = classify_solana_buy(
            SOL_WALLET,
            payload,
            token_metadata=lambda mint: {"symbol": "BONK", "priceUsd": 0.3},
            native_price_usd=150,
        )
        self.assertAlmostEqual(event["paymentAmount"], 1)
        self.assertAlmostEqual(event["paymentUsd"], 150)
        self.assertFalse(event["popupEligible"])


class SmartMoneyMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = SmartMoneyMonitor(Path(self.tmp.name) / "smart-money.sqlite")

    def tearDown(self):
        self.monitor.close()
        self.tmp.cleanup()

    def test_mentions_are_registered_and_duplicate_events_alert_once(self):
        added = self.monitor.ingest_text(
            f"BSC smart money wallet {EVM_WALLET}",
            source_name="DC 群聊",
        )
        self.assertEqual(len(added), 1)
        event = {
            "chain": "bsc", "walletAddress": EVM_WALLET, "transactionHash": "0xabc",
            "tokenAddress": TOKEN, "symbol": "MOON", "tokenAmount": 1,
            "paymentAsset": "USDT", "paymentAmount": 10_000, "paymentUsd": 10_000,
            "popupEligible": True, "observedAt": 1234,
        }
        alerts = []
        self.monitor.set_alert_callback(alerts.append)
        first = self.monitor.record_buy(event)
        second = self.monitor.record_buy(event)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(self.monitor.payload()["summary"]["wallets"], 1)

    def test_repeated_text_intake_does_not_resume_a_paused_wallet(self):
        text = f"BSC smart money wallet {EVM_WALLET}"
        added = self.monitor.ingest_text(text, source_name="DC 群聊")
        self.monitor.store.save_wallet(added[0]["id"], enabled=False, alert_threshold_usd=22_000)
        self.monitor.ingest_text(text, source_name="个人 X")
        stored = self.monitor.store.wallet("bsc", EVM_WALLET)
        self.assertFalse(stored["enabled"])
        self.assertEqual(stored["alertThresholdUsd"], 22_000)
        self.assertEqual(stored["sourceName"], "个人 X")

    def test_start_is_idempotent(self):
        self.monitor.start(start_worker=False)
        self.monitor.start(start_worker=False)
        self.assertTrue(self.monitor.payload()["summary"]["running"])

    def test_seed_addresses_cover_four_evm_chains_without_overwriting_pause(self):
        seeded = SmartMoneyMonitor(Path(self.tmp.name) / "seeded.sqlite", seed_defaults=True)
        try:
            rows = seeded.store.list_wallets()
            self.assertEqual(len(rows), 26)
            self.assertEqual({row["chain"] for row in rows}, {"ethereum", "bsc", "base", "robinhood", "solana"})
            by_address = {}
            for row in rows:
                by_address.setdefault(row["address"], set()).add((row["nickname"], row["sourceName"]))
            self.assertEqual(by_address[EVM_WALLET], {("Inq", "Inq5️⃣连杆提供")})
            self.assertEqual(by_address[EVM_OTHER], {("身份待核验 · a0ed…ff85", "Inq5️⃣连杆提供")})
            self.assertEqual(by_address[BONK_GUY_EVM_WALLET], {("Bonk Guy (Unipcs)", "Bonk Guy 公开钱包")})
            self.assertEqual(by_address[BONK_GUY_SOL_WALLET], {("Bonk Guy (Unipcs)", "Bonk Guy 公开钱包")})
            self.assertEqual(by_address[BONK_GUY_USELESS_WALLET], {("Bonk Guy (Unipcs)", "Bonk Guy 公开钱包")})
            self.assertEqual(sum(row["address"] == BONK_GUY_EVM_WALLET for row in rows), 4)
            first = rows[0]
            seeded.store.save_wallet(first["id"], enabled=False)
            self.assertEqual(seeded.seed_defaults(), 0)
            self.assertFalse(seeded.store.wallet(first["chain"], first["address"])["enabled"])
        finally:
            seeded.close()

    def test_seed_upgrade_repairs_only_untouched_legacy_inq_labels(self):
        database = Path(self.tmp.name) / "legacy-seeded.sqlite"
        legacy = SmartMoneyStore(database)
        try:
            wrong = legacy.upsert_wallet(
                "bsc", EVM_WALLET, nickname="Inq5️⃣连杆", source_name="Inq5️⃣连杆",
                source_kind="seed", source_evidence="用户确认加入聪明钱买入监控",
            )
            custom = legacy.upsert_wallet(
                "ethereum", EVM_WALLET, nickname="Inq5️⃣连杆", source_name="Inq5️⃣连杆",
                source_kind="seed", source_evidence="用户确认加入聪明钱买入监控",
            )
            legacy.save_wallet(custom["id"], nickname="已人工确认的钱包")
        finally:
            legacy.close()

        upgraded = SmartMoneyMonitor(database, seed_defaults=True)
        try:
            repaired = upgraded.store.wallet("bsc", EVM_WALLET)
            preserved = upgraded.store.wallet("ethereum", EVM_WALLET)
            self.assertEqual(repaired["nickname"], "Inq")
            self.assertEqual(repaired["sourceName"], "Inq5️⃣连杆提供")
            self.assertEqual(preserved["nickname"], "已人工确认的钱包")
            self.assertEqual(preserved["sourceName"], "Inq5️⃣连杆")
        finally:
            upgraded.close()

    def test_seed_upgrade_replaces_previous_generic_repair_with_per_wallet_identity(self):
        database = Path(self.tmp.name) / "generic-seeded.sqlite"
        previous = SmartMoneyStore(database)
        try:
            previous.upsert_wallet(
                "robinhood", EVM_OTHER, nickname="其他聪明钱", source_name="用户预置",
                source_kind="seed", source_evidence="用户确认加入聪明钱买入监控；具体归属待单独备注",
            )
        finally:
            previous.close()

        upgraded = SmartMoneyMonitor(database, seed_defaults=True)
        try:
            wallet = upgraded.store.wallet("robinhood", EVM_OTHER)
            self.assertEqual(wallet["nickname"], "身份待核验 · a0ed…ff85")
            self.assertEqual(wallet["sourceName"], "Inq5️⃣连杆提供")
        finally:
            upgraded.close()

    def test_evm_poll_establishes_baseline_then_reads_only_new_matching_transaction(self):
        self.monitor.add_wallet({"chain": "ethereum", "address": EVM_WALLET, "nickname": "Alpha"})
        with patch.object(self.monitor, "rpc", return_value="0x64") as rpc:
            self.assertEqual(self.monitor.poll_once(), [])
        self.assertEqual(self.monitor.store.cursor("evm:ethereum:block"), "100")
        rpc.assert_called_once_with("ethereum", "eth_blockNumber", [])

        tx = {"hash": "0xnew", "from": EVM_WALLET, "to": EVM_OTHER, "value": "0x0"}
        receipt = {
            "status": "0x1", "transactionHash": "0xnew", "blockNumber": "0x65",
            "logs": [
                transfer_log(USDT, EVM_WALLET, EVM_OTHER, 12_000 * 10**6),
                transfer_log(TOKEN, EVM_OTHER, EVM_WALLET, 2 * 10**18),
            ],
        }

        def rpc_result(chain, method, params):
            return {
                "eth_blockNumber": "0x65",
                "eth_getLogs": [{"transactionHash": "0xnew"}],
                "eth_getTransactionByHash": tx,
                "eth_getTransactionReceipt": receipt,
            }[method]

        def metadata(address):
            return (
                {"symbol": "USDT", "decimals": 6, "priceUsd": 1, "stable": True}
                if address.lower() == USDT else
                {"symbol": "MOON", "decimals": 18, "priceUsd": 6000, "stable": False}
            )

        with (
            patch.object(self.monitor, "rpc", side_effect=rpc_result),
            patch.object(self.monitor, "evm_token_metadata", side_effect=lambda chain, token: metadata(token)),
            patch.object(self.monitor, "native_price_usd", return_value=600),
        ):
            events = self.monitor.poll_once()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["transactionHash"], "0xnew")
        self.assertEqual(self.monitor.store.cursor("evm:ethereum:block"), "101")

    def test_evm_poll_falls_back_to_recent_blocks_when_public_log_api_is_restricted(self):
        self.monitor.add_wallet({"chain": "bsc", "address": EVM_WALLET, "nickname": "Alpha"})
        self.monitor.store.set_cursor("evm:bsc:block", 100)
        tx = {"hash": "0xfallback", "from": EVM_WALLET, "to": EVM_OTHER, "value": "0x0"}
        receipt = {
            "status": "0x1", "transactionHash": "0xfallback", "blockNumber": "0x65",
            "logs": [
                transfer_log(USDT, EVM_WALLET, EVM_OTHER, 12_000 * 10**6),
                transfer_log(TOKEN, EVM_OTHER, EVM_WALLET, 2 * 10**18),
            ],
        }

        def rpc_result(chain, method, params):
            if method == "eth_getLogs":
                raise RuntimeError("public log query restricted")
            return {
                "eth_blockNumber": "0x65",
                "eth_getBlockByNumber": {"transactions": [tx]},
                "eth_getTransactionReceipt": receipt,
            }[method]

        def metadata(address):
            if address.lower() == USDT:
                return {"symbol": "USDT", "decimals": 6, "priceUsd": 1, "stable": True}
            return {"symbol": "MOON", "decimals": 18, "priceUsd": 6000, "stable": False}

        with (
            patch.object(self.monitor, "rpc", side_effect=rpc_result),
            patch.object(self.monitor, "evm_token_metadata", side_effect=lambda chain, token: metadata(token)),
            patch.object(self.monitor, "native_price_usd", return_value=600),
        ):
            events = self.monitor.poll_once()
        self.assertEqual([event["transactionHash"] for event in events], ["0xfallback"])
        health = {row["chain"]: row for row in self.monitor.store.health()}
        self.assertIn("自动切换新区块扫描", health["bsc"]["message"])

    def test_evm_poll_does_not_query_an_empty_block_range(self):
        self.monitor.add_wallet({"chain": "ethereum", "address": EVM_WALLET})
        self.monitor.store.set_cursor("evm:ethereum:block", 100)
        with patch.object(self.monitor, "rpc", return_value="0x64") as rpc:
            self.assertEqual(self.monitor.poll_once(), [])
        rpc.assert_called_once_with("ethereum", "eth_blockNumber", [])
        health = {row["chain"]: row for row in self.monitor.store.health()}
        self.assertEqual(health["ethereum"]["status"], "ok")

    def test_solana_poll_first_run_only_saves_latest_signature(self):
        self.monitor.add_wallet({"chain": "solana", "address": SOL_WALLET})
        with patch.object(
            self.monitor,
            "rpc",
            return_value=[{"signature": "latest-signature", "err": None}],
        ) as rpc:
            self.assertEqual(self.monitor.poll_once(), [])
        self.assertEqual(self.monitor.store.cursor(f"solana:{SOL_WALLET}:signature"), "latest-signature")
        rpc.assert_called_once()


class SmartMoneyServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server

    def test_verified_buy_source_bypasses_muted_position_change_filter(self):
        item = {
            "key": "smart-money-buy:event-1",
            "kind": "聪明钱买入",
            "sourceType": "smart-money-buy",
            "title": "聪明钱买入 MOON · $12,000",
        }
        self.assertEqual(self.server.desktop_alert_political_military_reason(item), "")

    def test_alert_payload_uses_dedicated_source_and_contract(self):
        event = {
            "eventKey": "event-1", "chain": "bsc", "walletAddress": EVM_WALLET,
            "walletNickname": "Inq5️⃣连杆", "transactionHash": "0xabc", "tokenAddress": TOKEN,
            "symbol": "MOON", "paymentAsset": "USDT", "paymentAmount": 12_000,
            "paymentUsd": 12_000, "observedAt": 1234,
        }
        with patch.object(self.server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            self.server.send_smart_money_buy_desktop_alert(event)
        payload = launch.call_args.args[0]
        self.assertEqual(payload["sourceType"], "smart-money-buy")
        self.assertEqual(payload["contractAddress"], TOKEN)
        self.assertEqual(payload["queuePriority"], 120)

    def test_ingest_wrapper_never_breaks_source_feed(self):
        with patch.object(self.server.SMART_MONEY_MONITOR, "ingest_text", side_effect=RuntimeError("down")):
            self.assertEqual(self.server.ingest_smart_money_text("smart money wallet " + EVM_WALLET), [])

    def test_http_and_source_hooks_are_present(self):
        source = Path(self.server.__file__).read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count('parsed.path == "/api/smart-money-monitor"'), 1)
        self.assertGreaterEqual(source.count('route == "/api/smart-money-monitor"'), 1)
        self.assertIn('source_kind="personal-x"', source)
        self.assertIn('source_kind="qq" if platform == "qq" else "wechat"', source)


if __name__ == "__main__":
    unittest.main()
