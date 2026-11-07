import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_money_monitor import (
    SOLANA_SIGNATURE_LIMIT_CEILING,
    SOLANA_SIGNATURE_LIMIT_DEFAULT,
    SmartMoneyMonitor,
    SmartMoneyStore,
    classify_evm_buy,
    classify_solana_buy,
    evm_fallback_blocks,
    evm_max_blocks_per_poll,
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
CRYPTO_CHARMING_WALLET = "0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9"
ROBINHOOD_PLAIN_WALLET = "0xe28601398a5448d1147e6e8b0e0c6d686f0d216d"
AFENG_WALLET = "0xbf004bff64725914ee36d03b87d6965b0ced4903"
COWBOY_WALLET = "0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407"
INQ_WALLET = EVM_WALLET
#: pons榜5（用户 2026-11-04 补正备注）：Robinhood 链上**用 DELTA 付款**的那位聪明钱。
PONS_WALLET = "0x7e3ba68c49561aae7c23c1d20fef0f1d7615a3ad"
PONS_DELTA = "0xe8ffd7e24187f72afb08d75b1bb13088a989a791"
PONS_DELTA_USDG_POOL = "0xfee4638485d736bf7bf7222d2dcb11a298d44d41"
#: 该池 slot0 的 sqrtPriceX96（2026-11-04 实读），换算出来 DELTA ≈ $0.00891。
PONS_SLOT0_SQRT_PRICE_X96 = "0x0000000000000000000000000000000000a19f1397f0ad450e32cf8b84ce6e85"
PONS_BUY_TOKEN = "0x72fffb21d64c1177e29e9e2de532efb038d8061b"  # FLYWHEEL
PONS_BUY_TOKEN_RAW = 1319628559603659063641233162
PONS_DELTA_SPENT_RAW = 300000 * 10**18
PONS_BUY_TX = "0x7599fd4b3340f9049541a4a3a48943b1d4a591cc5b4e68159374401864cd1337"
#: THEO（用户 2026-11-06 提供的 Solana 聪明钱，base58 地址，只接 solana 单链）。
THEO_WALLET = "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt"
#: Limfork（用户 2026-11-07 提供）：真实交易钱包，但长期被机器人灌失败交易。
LIMFORK_WALLET = "BQVz7fQ1WsQmSTMY3umdPEPPTm1sdcBcX9sP7o6kPRmB"
BONK_GUY_NO_THRESHOLD_WALLET = BONK_GUY_EVM_WALLET
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

    def test_robinhood_usdg_swap_is_classified_as_a_buy(self):
        """Robinhood Chain quotes in USDG.  If USDG is not recognised as a
        stablecoin the swap looks like 'two tokens in, nothing paid' and the buy
        is silently dropped."""
        usdg = "0x5fc5360d0400a0fd4f2b3f1a5b0b0f7b8a5c1e2d"

        def meta(address):
            if address == usdg:
                # No DEX price for a Robinhood-chain token -> price stays 0, so
                # the STABLE_SYMBOLS fallback is the only thing that can save it.
                return {"symbol": "USDG", "decimals": 18, "priceUsd": 0, "stable": False}
            return {"symbol": "AMC", "decimals": 18, "priceUsd": 0.0117, "stable": False}

        receipt = {
            "status": "0x1",
            "transactionHash": "0xhood",
            "blockNumber": "0x20",
            "logs": [
                transfer_log(usdg, CRYPTO_CHARMING_WALLET, EVM_OTHER, 320 * 10**18),
                transfer_log(TOKEN, EVM_OTHER, CRYPTO_CHARMING_WALLET, 27_000 * 10**18),
            ],
        }
        event = classify_evm_buy(
            "robinhood",
            CRYPTO_CHARMING_WALLET,
            {"hash": "0xhood", "from": CRYPTO_CHARMING_WALLET, "to": EVM_OTHER, "value": "0x0"},
            receipt,
            meta,
            native_price_usd=2500,
            threshold_usd=100,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["symbol"], "AMC")
        self.assertEqual(event["paymentAsset"], "USDG")
        self.assertAlmostEqual(event["paymentUsd"], 320)
        self.assertTrue(event["popupEligible"])

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


class SolanaSignatureWindowTests(unittest.TestCase):
    """被机器人灌垃圾交易的地址，签名窗口必须自动放大，否则真实买入被挤掉。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = SmartMoneyMonitor(Path(self.tmp.name) / "window.sqlite")

    def tearDown(self):
        self.monitor.close()
        self.tmp.cleanup()

    def _run(self, first_page_rows, second_page_rows=None):
        key = f"solana:{LIMFORK_WALLET}:signature"
        self.monitor.store.set_cursor(key, "cursor-1")
        asked = []

        def fake_rpc(chain, method, params):
            limit = params[1].get("limit")
            asked.append(limit)
            if limit == SOLANA_SIGNATURE_LIMIT_DEFAULT:
                return [{"signature": f"a{i}", "err": {"InstructionError": [0, 7]}}
                        for i in range(first_page_rows)]
            return [{"signature": f"b{i}", "err": {"InstructionError": [0, 7]}}
                    for i in range(second_page_rows or 0)]

        self.monitor.rpc = fake_rpc
        self.monitor._poll_solana([{"address": LIMFORK_WALLET, "chain": "solana",
                                    "alertThresholdUsd": 500.0}])
        return asked

    def test_window_widens_to_the_ceiling_when_the_first_page_fills_up(self):
        asked = self._run(SOLANA_SIGNATURE_LIMIT_DEFAULT, SOLANA_SIGNATURE_LIMIT_CEILING)
        self.assertEqual(asked, [SOLANA_SIGNATURE_LIMIT_DEFAULT, SOLANA_SIGNATURE_LIMIT_CEILING])

    def test_quiet_wallet_only_costs_one_call(self):
        asked = self._run(3)
        self.assertEqual(asked, [SOLANA_SIGNATURE_LIMIT_DEFAULT])

    def test_failed_transactions_never_become_events(self):
        key = f"solana:{LIMFORK_WALLET}:signature"
        self.monitor.store.set_cursor(key, "cursor-1")
        self.monitor.rpc = lambda chain, method, params: [
            {"signature": "noise1", "err": {"InstructionError": [0, 7]}},
            {"signature": "noise2", "err": {"InstructionError": [1, 13]}},
        ]
        self.assertEqual(self.monitor._poll_solana([{"address": LIMFORK_WALLET, "chain": "solana",
                                                     "alertThresholdUsd": 500.0}]), [])


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
            # cowboy 占三行（BSC/ETH/Base 各一），阿峰占一行（只 BSC），
            # 15 个「追踪钱包」各占三行（只记录不弹窗，见 test_kol_sync），
            # pons榜5 占一行（只 Robinhood），THEO 与 Limfork 各占一行（只 Solana）。
            self.assertEqual(len(rows), 86)
            self.assertEqual({row["chain"] for row in rows}, {"ethereum", "bsc", "base", "robinhood", "solana"})
            by_address = {}
            for row in rows:
                by_address.setdefault(row["address"], set()).add((row["nickname"], row["sourceName"]))
            self.assertEqual(by_address[EVM_WALLET], {("Inq", "Inq5️⃣连杆提供")})
            self.assertEqual(by_address[EVM_OTHER], {("身份待核验 · a0ed…ff85", "Inq5️⃣连杆提供")})
            self.assertEqual(by_address[CRYPTO_CHARMING_WALLET], {("CryptoCharming", "用户提供 · Robinhood 链")})
            self.assertEqual(
                by_address[ROBINHOOD_PLAIN_WALLET],
                {("NΞO", "用户提供 · Robinhood 链")},
            )
            self.assertEqual(by_address[BONK_GUY_EVM_WALLET], {("Bonk Guy (Unipcs)", "Bonk Guy 公开钱包")})
            self.assertEqual(by_address[BONK_GUY_SOL_WALLET], {("Bonk Guy (Unipcs)", "Bonk Guy 公开钱包")})
            self.assertEqual(by_address[THEO_WALLET], {("THEO", "用户提供 · Solana")})
            self.assertEqual(by_address[LIMFORK_WALLET], {("Limfork", "用户提供 · Solana")})
            # base58 地址在 EVM 上不存在：漏写 chains 会白铺四条 EVM 链。
            self.assertEqual([row["chain"] for row in rows if row["address"] == THEO_WALLET], ["solana"])
            self.assertEqual([row["chain"] for row in rows if row["address"] == LIMFORK_WALLET], ["solana"])
            self.assertEqual(by_address[PONS_WALLET], {("pons榜5", "用户提供 · Robinhood 链")})
            self.assertEqual(by_address[BONK_GUY_USELESS_WALLET], {("Bonk Guy (Unipcs)", "Bonk Guy 公开钱包")})
            self.assertEqual(sum(row["address"] == BONK_GUY_EVM_WALLET for row in rows), 4)
            first = rows[0]
            seeded.store.save_wallet(first["id"], enabled=False)
            self.assertEqual(seeded.seed_defaults(), 0)
            self.assertFalse(seeded.store.wallet(first["chain"], first["address"])["enabled"])
        finally:
            seeded.close()

    def test_crypto_charming_seed_is_monitored_on_robinhood(self):
        seeded = SmartMoneyMonitor(Path(self.tmp.name) / "charming.sqlite", seed_defaults=True)
        try:
            wallet = seeded.store.wallet("robinhood", CRYPTO_CHARMING_WALLET)
            self.assertTrue(wallet)
            self.assertEqual(wallet["nickname"], "CryptoCharming")
            self.assertEqual(wallet["sourceName"], "用户提供 · Robinhood 链")
            self.assertEqual(wallet["sourceKind"], "seed")
            self.assertTrue(wallet["enabled"])
            # 链上实测单笔成交在数十美元量级，默认 10000 阈值等于永不提醒。
            self.assertEqual(wallet["alertThresholdUsd"], 100)
            # An EVM address is keyed by the same private key on every EVM chain,
            # so the seed follows the existing four-EVM-chain convention.
            self.assertEqual(
                sorted(
                    row["chain"] for row in seeded.store.list_wallets()
                    if row["address"] == CRYPTO_CHARMING_WALLET
                ),
                ["base", "bsc", "ethereum", "robinhood"],
            )
        finally:
            seeded.close()

    def test_user_supplied_robinhood_wallet_seed_alerts_at_a_reachable_threshold(self):
        seeded = SmartMoneyMonitor(Path(self.tmp.name) / "rh-plain.sqlite", seed_defaults=True)
        try:
            wallet = seeded.store.wallet("robinhood", ROBINHOOD_PLAIN_WALLET)
            self.assertTrue(wallet)
            self.assertEqual(wallet["nickname"], "NΞO")
            self.assertEqual(wallet["sourceName"], "用户提供 · Robinhood 链")
            self.assertEqual(wallet["sourceKind"], "seed")
            self.assertTrue(wallet["enabled"])
            # 链上实测买入在 0.05~0.15 ETH（约 $120~$350），默认 10000 阈值等于永不提醒。
            self.assertEqual(wallet["alertThresholdUsd"], 100)
            self.assertEqual(
                sorted(
                    row["chain"] for row in seeded.store.list_wallets()
                    if row["address"] == ROBINHOOD_PLAIN_WALLET
                ),
                ["base", "bsc", "ethereum", "robinhood"],
            )
        finally:
            seeded.close()

    def test_afeng_seed_watches_bsc_only_at_the_agreed_threshold(self):
        """用户 2026-10-22 指定监控的 KOL「阿峰_Afeng」。

        拍板两点：**只盯 BSC**（他没要 ETH/Base 那两份）、单笔 **≥500U** 才弹
        （GMGN 统计单笔买入均值约 $344，沿用默认 10000U 等于永不提醒）。
        """
        seeded = SmartMoneyMonitor(Path(self.tmp.name) / "afeng.sqlite", seed_defaults=True)
        try:
            wallet = seeded.store.wallet("bsc", AFENG_WALLET)
            self.assertTrue(wallet)
            self.assertEqual(wallet["nickname"], "阿峰_Afeng")
            self.assertEqual(wallet["sourceName"], "用户提供 · GMGN KOL")
            self.assertEqual(wallet["sourceKind"], "seed")
            self.assertTrue(wallet["enabled"])
            self.assertEqual(wallet["alertThresholdUsd"], 500)
            self.assertEqual(
                [row["chain"] for row in seeded.store.list_wallets()
                 if row["address"] == AFENG_WALLET],
                ["bsc"],
            )
        finally:
            seeded.close()

    def test_cowboy_seed_watches_his_three_active_chains_at_500(self):
        """用户 2026-10-27 指定监控的 KOL「cowboy」。

        链上核验（2026-10-27）：BSC nonce 11,164 / 83.41 BNB、ETH nonce 298 /
        1.78 ETH、Base nonce 162 / 1.34 ETH，全是普通 EOA。监控面给这三条
        **他真实活跃的链**（不给 Robinhood）；提醒门槛沿用阿峰的拍板：单笔 ≥500U。
        """
        seeded = SmartMoneyMonitor(Path(self.tmp.name) / "cowboy.sqlite", seed_defaults=True)
        try:
            rows = [
                row for row in seeded.store.list_wallets()
                if row["address"] == COWBOY_WALLET
            ]
            self.assertEqual(
                sorted(row["chain"] for row in rows),
                ["base", "bsc", "ethereum"],
            )
            for row in rows:
                with self.subTest(chain=row["chain"]):
                    self.assertEqual(row["nickname"], "cowboy")
                    self.assertEqual(row["sourceName"], "用户提供 · KOL")
                    self.assertEqual(row["sourceKind"], "seed")
                    self.assertTrue(row["enabled"])
                    self.assertEqual(row["alertThresholdUsd"], 500)
        finally:
            seeded.close()

    def test_inq_seed_now_clears_a_hundred_dollars_on_all_four_chains(self):
        """用户 2026-10-28：「把这个地址的买入也监控上并弹窗提示」——Inq。

        这个地址其实早就在库里，但阈值一直是默认的 10000U。实盘事件表里 1202 笔
        真实买入，单笔 ≥10000U 的有 **0** 笔（中位 $46、最大 $581），所以从来没
        弹过一次窗。按实测规模定 100U（与 CryptoCharming / NΞO 同档）。
        """

        seeded = SmartMoneyMonitor(Path(self.tmp.name) / "inq.sqlite", seed_defaults=True)
        try:
            rows = [
                row for row in seeded.store.list_wallets()
                if row["address"] == INQ_WALLET
            ]
            self.assertEqual(
                sorted(row["chain"] for row in rows),
                ["base", "bsc", "ethereum", "robinhood"],
            )
            for row in rows:
                with self.subTest(chain=row["chain"]):
                    self.assertEqual(row["nickname"], "Inq")
                    self.assertTrue(row["enabled"])
                    self.assertEqual(row["alertThresholdUsd"], 100)
        finally:
            seeded.close()

    def test_seed_threshold_upgrade_only_touches_default_and_seed_rows(self):
        """改代码里的阈值必须能落到**已入库**的种子行，但不能压掉用户的选择。

        线上真实场景：Inq 的四行是 10000U 默认值（所以永远不弹）；如果只靠
        ``INSERT OR IGNORE``，改 seed 完全无效。这里同时钉住三条边界：
        默认值 → 升级、用户改过的值 → 不碰、没声明阈值的 seed → 保持默认。
        """

        database = Path(self.tmp.name) / "threshold-upgrade.sqlite"
        untouched = SmartMoneyStore(database)
        try:
            for chain in ("ethereum", "bsc", "base", "robinhood"):
                untouched.upsert_wallet(
                    chain, INQ_WALLET, nickname="Inq", source_name="Inq5️⃣连杆提供",
                    source_kind="seed",  # 不传阈值 = 默认 10000U，和线上一致
                )
            user_tuned = untouched.upsert_wallet(
                "robinhood", CRYPTO_CHARMING_WALLET, nickname="CryptoCharming",
                source_name="用户提供 · Robinhood 链", source_kind="seed",
            )
            untouched.save_wallet(user_tuned["id"], alert_threshold_usd=300.0)
            manual = untouched.upsert_wallet(
                "bsc", "0x3391a39a1b508e54a361924a26056c01c1c2c07d",
                nickname="手工加的", source_name="手动添加", source_kind="manual",
            )
        finally:
            untouched.close()

        upgraded = SmartMoneyMonitor(database, seed_defaults=True)
        try:
            for chain in ("ethereum", "bsc", "base", "robinhood"):
                with self.subTest(chain=chain):
                    self.assertEqual(
                        upgraded.store.wallet(chain, INQ_WALLET)["alertThresholdUsd"], 100
                    )
            # 用户手动调成 300 的不许被 seed 覆盖。
            self.assertEqual(
                upgraded.store.wallet("robinhood", CRYPTO_CHARMING_WALLET)["alertThresholdUsd"],
                300,
            )
            # 手工添加（source_kind != seed）的脏数据不在升级范围里。
            self.assertEqual(
                upgraded.store.wallet("bsc", "0x3391a39a1b508e54a361924a26056c01c1c2c07d")[
                    "alertThresholdUsd"
                ],
                10000,
            )
            # 没在种子里声明阈值的地址（Bonk Guy）保持默认，不自作主张。
            self.assertEqual(
                upgraded.store.wallet("robinhood", BONK_GUY_NO_THRESHOLD_WALLET)[
                    "alertThresholdUsd"
                ],
                10000,
            )
            # 幂等：第二次不会再改任何行（阈值已不是默认值）。
            self.assertEqual(upgraded.store.apply_seed_thresholds([
                {"address": INQ_WALLET, "alertThresholdUsd": 100.0},
            ]), 0)
            self.assertEqual(manual["alertThresholdUsd"], 10000)
        finally:
            upgraded.close()

    def test_placeholder_seed_label_is_claimed_once_the_user_names_the_wallet(self):
        """线上真实序列：先以占位名入库，用户后来说出真名 → 重启时自愈成真名。"""

        database = Path(self.tmp.name) / "placeholder-seeded.sqlite"
        first = SmartMoneyStore(database)
        try:
            for chain in ("ethereum", "bsc", "base", "robinhood"):
                first.upsert_wallet(
                    chain, ROBINHOOD_PLAIN_WALLET,
                    nickname="身份待核验 · e286…216d", source_name="用户提供 · Robinhood 链",
                    source_kind="seed", source_evidence="用户提供的 Robinhood 链地址",
                    alert_threshold_usd=100.0,
                )
        finally:
            first.close()

        renamed = SmartMoneyMonitor(database, seed_defaults=True)
        try:
            for chain in ("ethereum", "bsc", "base", "robinhood"):
                wallet = renamed.store.wallet(chain, ROBINHOOD_PLAIN_WALLET)
                self.assertEqual(wallet["nickname"], "NΞO")
                self.assertEqual(wallet["sourceName"], "用户提供 · Robinhood 链")
            # 认领之后不再重复命中（幂等）。
            self.assertEqual(renamed.seed_defaults(), 0)
        finally:
            renamed.close()

    def test_placeholder_seed_label_never_overwrites_a_panel_rename(self):
        """用户自己在面板改过名 → 占位标签对不上，自愈必须放手。"""

        database = Path(self.tmp.name) / "user-renamed.sqlite"
        first = SmartMoneyStore(database)
        try:
            row = first.upsert_wallet(
                "robinhood", ROBINHOOD_PLAIN_WALLET,
                nickname="身份待核验 · e286…216d", source_name="用户提供 · Robinhood 链",
                source_kind="seed", source_evidence="用户提供的 Robinhood 链地址",
                alert_threshold_usd=100.0,
            )
            first.save_wallet(row["id"], nickname="我自己起的名字")
        finally:
            first.close()

        reopened = SmartMoneyMonitor(database, seed_defaults=True)
        try:
            wallet = reopened.store.wallet("robinhood", ROBINHOOD_PLAIN_WALLET)
            self.assertEqual(wallet["nickname"], "我自己起的名字")
        finally:
            reopened.close()

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


class SmartMoneyPollWindowTests(unittest.TestCase):
    """Robinhood Chain mines ~10 blocks/second, so the generic 120-block window
    only covers ~12 seconds and drops most buys between polls.  The fast-chain
    window has to outlast a poll cycle while staying inside the RPC's 30000-block
    getLogs span limit."""

    def test_fast_chain_window_outlasts_a_poll_cycle(self):
        self.assertEqual(evm_max_blocks_per_poll("robinhood"), 3000)
        self.assertEqual(evm_max_blocks_per_poll("ethereum"), 120)
        self.assertEqual(evm_max_blocks_per_poll("bsc"), 120)
        self.assertEqual(evm_max_blocks_per_poll("base"), 120)

    def test_env_override_applies_to_every_chain(self):
        with patch.dict(os.environ, {"SMART_MONEY_EVM_MAX_BLOCKS_PER_POLL": "900"}):
            self.assertEqual(evm_max_blocks_per_poll("robinhood"), 900)
            self.assertEqual(evm_max_blocks_per_poll("bsc"), 900)

    def test_window_never_exceeds_the_rpc_span_limit(self):
        with patch.dict(os.environ, {"SMART_MONEY_EVM_MAX_BLOCKS_PER_POLL": "999999"}):
            self.assertEqual(evm_max_blocks_per_poll("robinhood"), 30000)

    def test_unusable_env_value_falls_back_to_the_per_chain_default(self):
        for value in ("abc", "0", "-5", ""):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"SMART_MONEY_EVM_MAX_BLOCKS_PER_POLL": value}):
                    self.assertEqual(evm_max_blocks_per_poll("robinhood"), 3000)

    def test_poll_scans_the_wide_window_when_blocks_were_missed(self):
        monitor = SmartMoneyMonitor(Path(tempfile.mkdtemp()) / "window.sqlite")
        try:
            monitor.add_wallet({
                "chain": "robinhood", "address": CRYPTO_CHARMING_WALLET, "nickname": "CryptoCharming",
            })
            monitor.store.set_cursor("evm:robinhood:block", "100")
            calls = []

            def fake_rpc(chain, method, params, *args, **kwargs):
                calls.append((method, params))
                if method == "eth_blockNumber":
                    return hex(20_100)  # 20,000 blocks behind the cursor
                if method == "eth_getLogs":
                    return []
                return {}

            wallets = [row for row in monitor.store.list_wallets(enabled_only=True) if row["chain"] == "robinhood"]
            with patch.object(monitor, "rpc", side_effect=fake_rpc):
                monitor._poll_evm("robinhood", wallets)

            windows = [params[0] for method, params in calls if method == "eth_getLogs"]
            self.assertEqual(len(windows), 1)
            span = int(windows[0]["toBlock"], 16) - int(windows[0]["fromBlock"], 16) + 1
            self.assertEqual(span, 3000)
            health = [row for row in monitor.store.health() if row["chain"] == "robinhood"]
            self.assertTrue(health)
        finally:
            monitor.close()


class SmartMoneyFallbackWindowTests(unittest.TestCase):
    """Public BSC RPCs refuse address-less eth_getLogs, so BSC always scans raw
    blocks.  BSC mines ~0.45 s/block, so the generic 24-block budget covers only
    ~11 seconds — less than a single poll cycle once the monitor has walked its
    other chains — and every block beyond it is dropped without warning."""

    def test_fast_block_chain_gets_a_wider_catch_up_budget(self):
        self.assertEqual(evm_fallback_blocks("bsc"), 240)
        self.assertEqual(evm_fallback_blocks("ethereum"), 24)
        self.assertEqual(evm_fallback_blocks("base"), 24)

    def test_env_override_applies_to_every_chain(self):
        with patch.dict(os.environ, {"SMART_MONEY_EVM_FALLBACK_BLOCKS": "200"}):
            self.assertEqual(evm_fallback_blocks("bsc"), 200)
            self.assertEqual(evm_fallback_blocks("ethereum"), 200)

    def test_unusable_env_value_falls_back_to_the_per_chain_default(self):
        for value in ("abc", "0", "-5", ""):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"SMART_MONEY_EVM_FALLBACK_BLOCKS": value}):
                    self.assertEqual(evm_fallback_blocks("bsc"), 240)

    def test_requested_budget_is_clamped_to_the_ceiling(self):
        with patch.dict(os.environ, {"SMART_MONEY_EVM_FALLBACK_BLOCKS": "999999"}):
            self.assertEqual(evm_fallback_blocks("bsc"), 600)

    def test_bsc_poll_scans_the_wide_catch_up_window(self):
        monitor = SmartMoneyMonitor(Path(tempfile.mkdtemp()) / "fallback.sqlite")
        try:
            monitor.add_wallet({
                "chain": "bsc", "address": CRYPTO_CHARMING_WALLET, "nickname": "CryptoCharming",
            })
            monitor.store.set_cursor("evm:bsc:block", "1000")
            scanned: list[int] = []

            def fake_rpc(chain, method, params, *args, **kwargs):
                if method == "eth_blockNumber":
                    return hex(1300)  # 300 blocks behind: beyond the old 24-block cap
                if method == "eth_getBlockByNumber":
                    scanned.append(int(params[0], 16))
                    return {"transactions": []}
                return {}

            wallets = [row for row in monitor.store.list_wallets(enabled_only=True) if row["chain"] == "bsc"]
            with patch.object(monitor, "rpc", side_effect=fake_rpc):
                monitor._poll_evm("bsc", wallets)

            # BSC never uses eth_getLogs, and the window reaches back 240 blocks
            # (≈108 s) so a ~80 s poll cycle no longer leaves a silent gap.
            self.assertEqual(len(scanned), 240)
            self.assertEqual(min(scanned), 1061)
            self.assertEqual(max(scanned), 1300)
            health = [row for row in monitor.store.health() if row["chain"] == "bsc"][0]
            self.assertIn("已扫描最近 240 个区块", health["message"])
        finally:
            monitor.close()


class SmartMoneyAlertLineTests(unittest.TestCase):
    """The popup card prints the line the event cleared, and that line is stored
    PER WALLET.  ``record_buy`` therefore has to stamp the threshold onto the
    persisted event, otherwise the card falls back to a meaningless 10,000U."""

    def test_recorded_buy_carries_the_wallets_popup_line(self):
        monitor = SmartMoneyMonitor(Path(tempfile.mkdtemp()) / "threshold.sqlite")
        try:
            monitor.add_wallet({
                "chain": "bsc", "address": CRYPTO_CHARMING_WALLET,
                "nickname": "CryptoCharming", "alertThresholdUsd": 100,
            })
            result = monitor.record_buy({
                "chain": "bsc", "walletAddress": CRYPTO_CHARMING_WALLET,
                "transactionHash": "0x" + "cd" * 32, "tokenAddress": TOKEN,
                "symbol": "MEME", "tokenAmount": 10.0, "paymentAsset": "USDT",
                "paymentAmount": 150.0, "paymentUsd": 150.0, "priceUsd": 15.0,
                "details": {"classification": "net-token-flow", "readOnly": True},
            })
            self.assertTrue(result["created"])
            event = result["event"]
            self.assertTrue(event["popupEligible"])
            self.assertEqual(event["details"]["alertThresholdUsd"], 100.0)
            # The classifier's own detail keys must survive the merge.
            self.assertEqual(event["details"]["classification"], "net-token-flow")
        finally:
            monitor.close()


# 真实主网成交（Robinhood Chain block 88,921,965）：用户新加的钱包
# 0xe2860139…0d216d 用 0.144117 ETH（原生）买入 12,056,495.70822647 MINNIE。
# 该 receipt 共 26 条日志，但只有下面这一条涉及被监控钱包，其余与钱包无关、
# 不参与 net 计算，所以这里只保留这一条真机日志即可如实复现。
ROBINHOOD_REAL_BUY_WALLET = ROBINHOOD_PLAIN_WALLET
ROBINHOOD_REAL_BUY_TOKEN = "0x76edd94f5eb7aeac551980a2134174d5ce0c1e18"
ROBINHOOD_REAL_BUY_POOL = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
ROBINHOOD_REAL_BUY_RAW_AMOUNT = 12056495708226470144892637
ROBINHOOD_REAL_BUY_TX_VALUE = 0x20001712DA4016C  # 0.1441167736845111 ETH
ROBINHOOD_REAL_BUY_ETH_USD = 2416.0  # 2026-10-15 币安 ETHUSDT 现价


class RobinhoodNativePaymentBuyTests(unittest.TestCase):
    """Robinhood 链用原生 ETH 付款的买入必须能被判定并越过阈值。

    该链的代币在 DexScreener 上没有报价（``_token_price`` 返回 0），所以金额
    只能靠 ``native_price_usd`` 换算 —— 这条用例同时守住「原生 ETH 支付腿
    依旧参与 payment_options」这个易碎点。
    """

    def test_real_native_eth_buy_clears_the_hundred_dollar_threshold(self):
        receipt = {
            "status": "0x1",
            "transactionHash": "0x75c58340b9327ca85e7647896bd7e5701c73b4dd6a4548ee16f295d4a1aac9e2",
            "blockNumber": "0x54ccf9d",
            "logs": [
                transfer_log(
                    ROBINHOOD_REAL_BUY_TOKEN,
                    ROBINHOOD_REAL_BUY_POOL,
                    ROBINHOOD_REAL_BUY_WALLET,
                    ROBINHOOD_REAL_BUY_RAW_AMOUNT,
                )
            ],
        }
        transaction = {
            "hash": receipt["transactionHash"],
            "from": ROBINHOOD_REAL_BUY_WALLET,
            "to": ROBINHOOD_REAL_BUY_POOL,
            "value": hex(ROBINHOOD_REAL_BUY_TX_VALUE),
        }

        event = classify_evm_buy(
            "robinhood", ROBINHOOD_REAL_BUY_WALLET, transaction, receipt,
            lambda token: {"symbol": "MINNIE", "decimals": 18, "priceUsd": 0.0},
            native_price_usd=ROBINHOOD_REAL_BUY_ETH_USD,
            threshold_usd=100.0,
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["symbol"], "MINNIE")
        self.assertAlmostEqual(event["tokenAmount"], 12_056_495.70822647, places=6)
        self.assertEqual(event["paymentAsset"], "ETH")
        self.assertAlmostEqual(event["paymentAmount"], 0.1441167736845111, places=12)
        self.assertAlmostEqual(
            event["paymentUsd"], 0.1441167736845111 * ROBINHOOD_REAL_BUY_ETH_USD, places=6
        )
        # $348 的真实成交必须过 100U 阈值；若原生 ETH 腿被丢掉就会退回 None。
        self.assertTrue(event["popupEligible"])

    def test_native_price_of_zero_never_invents_a_buy_signal(self):
        """取不到 ETH 价时必须安静地不产信号，而不是把 0 当成合格成交。"""

        receipt = {
            "status": "0x1",
            "transactionHash": "0xdead",
            "blockNumber": "0x54ccf9d",
            "logs": [
                transfer_log(
                    ROBINHOOD_REAL_BUY_TOKEN,
                    ROBINHOOD_REAL_BUY_POOL,
                    ROBINHOOD_REAL_BUY_WALLET,
                    ROBINHOOD_REAL_BUY_RAW_AMOUNT,
                )
            ],
        }
        transaction = {
            "hash": "0xdead",
            "from": ROBINHOOD_REAL_BUY_WALLET,
            "value": hex(ROBINHOOD_REAL_BUY_TX_VALUE),
        }

        self.assertIsNone(classify_evm_buy(
            "robinhood", ROBINHOOD_REAL_BUY_WALLET, transaction, receipt,
            lambda token: {"symbol": "MINNIE", "decimals": 18, "priceUsd": 0.0},
            native_price_usd=0.0,
            threshold_usd=100.0,
        ))


# Robinhood Chain 上的「用报价代币付款」买单（pons榜5，用户 2026-11-04）。
#
# 真实主网成交：block 90,954,692 / tx 0x7599fd4b…1337 —— 该地址用 300,000 DELTA
# 买入 1,319,628,559.603659 FLYWHEEL，整笔交易 value=0（不碰原生币），
# 付款方既不是稳定币也不是包装原生币，正是默认逻辑认不出来的那类。
PONS_RECEIPT = {
    "status": "0x1",
    "transactionHash": PONS_BUY_TX,
    "blockNumber": "0x56c1ef4",
    "logs": [
        transfer_log(PONS_DELTA, PONS_WALLET, PONS_DELTA_USDG_POOL, PONS_DELTA_SPENT_RAW),
        transfer_log(PONS_BUY_TOKEN, PONS_DELTA_USDG_POOL, PONS_WALLET, PONS_BUY_TOKEN_RAW),
    ],
}
PONS_TRANSACTION = {
    "hash": PONS_BUY_TX,
    "from": PONS_WALLET,
    "to": PONS_WALLET,          # 智能账户自己执行（0xe9ae5c53 批量兑换）
    "value": "0x0",
}


def pons_metadata(prices):
    def lookup(token):
        token = token.lower()
        if token == PONS_DELTA:
            return {"symbol": "DELTA", "decimals": 18, "priceUsd": prices.get("delta", 0.0)}
        if token == PONS_BUY_TOKEN:
            return {"symbol": "FLYWHEEL", "decimals": 18, "priceUsd": prices.get("target", 0.0)}
        return {"symbol": "UNKNOWN", "decimals": 18, "priceUsd": 0.0}
    return lookup


class QuoteTokenPaymentBuyTests(unittest.TestCase):
    """付款方是「非稳定报价代币」时的两级兜底。

    这几条守的是**边界**，不是实现细节：

    * 名单外的地址行为一个字都不能变（否则 Inq / 阿峰 那些换仓会被当成新买入，
      把用户已经调好的弹窗节奏搞乱）；
    * 名单内的地址要能把「花 300,000 DELTA」换算成美元并过阈值；
    * 两边都拿不到价时宁可返回 None，也不许编一个 0 美元的弹窗。
    """

    def test_unlisted_wallet_still_ignores_quote_token_spends(self):
        self.assertIsNone(classify_evm_buy(
            "robinhood", PONS_WALLET, PONS_TRANSACTION, PONS_RECEIPT,
            pons_metadata({"delta": 0.00891}), threshold_usd=1.0,
        ))

    def test_listed_wallet_prices_the_quote_token_spend(self):
        event = classify_evm_buy(
            "robinhood", PONS_WALLET, PONS_TRANSACTION, PONS_RECEIPT,
            pons_metadata({"delta": 0.00891}),
            threshold_usd=1000.0, allow_quote_payment=True,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["symbol"], "FLYWHEEL")
        self.assertEqual(event["paymentAsset"], "DELTA")
        self.assertAlmostEqual(event["paymentAmount"], 300000.0, places=6)
        self.assertAlmostEqual(event["paymentUsd"], 300000.0 * 0.00891, places=6)
        self.assertTrue(event["popupEligible"])
        self.assertEqual(event["details"]["paymentSource"], "quote-token")

    def test_below_threshold_is_recorded_but_not_popup_eligible(self):
        event = classify_evm_buy(
            "robinhood", PONS_WALLET, PONS_TRANSACTION, PONS_RECEIPT,
            pons_metadata({"delta": 0.00891}),
            threshold_usd=5000.0, allow_quote_payment=True,
        )
        self.assertIsNotNone(event)
        self.assertFalse(event["popupEligible"])

    def test_falls_back_to_target_value_when_quote_token_has_no_price(self):
        """DELTA 查不到价时，用「买到的代币市值」估这笔成交的名义规模。"""

        event = classify_evm_buy(
            "robinhood", PONS_WALLET, PONS_TRANSACTION, PONS_RECEIPT,
            pons_metadata({"target": 0.000002}),
            threshold_usd=1000.0, allow_quote_payment=True,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["details"]["paymentSource"], "target-value")
        self.assertAlmostEqual(event["paymentUsd"], 1319628559.603659 * 0.000002, places=2)

    def test_no_price_anywhere_never_invents_a_signal(self):
        self.assertIsNone(classify_evm_buy(
            "robinhood", PONS_WALLET, PONS_TRANSACTION, PONS_RECEIPT,
            pons_metadata({}), threshold_usd=1.0, allow_quote_payment=True,
        ))


class OnchainQuotePriceTests(unittest.TestCase):
    """DexScreener 不覆盖 Robinhood Chain，DELTA 的美元价只能从 USDG 池的 slot0 读。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = SmartMoneyMonitor(Path(self.tmp.name) / "quote.sqlite")

    def tearDown(self):
        self.monitor.close()
        self.tmp.cleanup()

    def test_slot0_of_the_usdg_pool_prices_delta(self):
        slot0 = PONS_SLOT0_SQRT_PRICE_X96 + "0" * 384  # 后面几列（tick/观测位）不用
        with patch.object(SmartMoneyMonitor, "rpc", return_value=slot0):
            self.assertAlmostEqual(
                self.monitor.onchain_quote_price("robinhood", PONS_DELTA), 0.00891, places=4,
            )

    def test_unknown_token_is_not_priced(self):
        with patch.object(SmartMoneyMonitor, "rpc", return_value="0x0" + "0" * 62):
            self.assertEqual(self.monitor.onchain_quote_price("robinhood", PONS_BUY_TOKEN), 0.0)

    def test_broken_pool_read_returns_zero_instead_of_raising(self):
        with patch.object(SmartMoneyMonitor, "rpc", side_effect=RuntimeError("rpc down")):
            self.assertEqual(self.monitor.onchain_quote_price("robinhood", PONS_DELTA), 0.0)


class PonsSeedRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = SmartMoneyMonitor(Path(self.tmp.name) / "pons.sqlite", seed_defaults=True)

    def tearDown(self):
        self.monitor.close()
        self.tmp.cleanup()

    def test_pons_wallet_is_seeded_on_robinhood_only(self):
        rows = [row for row in self.monitor.store.list_wallets() if row["address"] == PONS_WALLET]
        self.assertEqual([row["chain"] for row in rows], ["robinhood"])
        self.assertEqual(rows[0]["nickname"], "pons榜5")
        self.assertEqual(rows[0]["alertThresholdUsd"], 1000)
        self.assertTrue(rows[0]["enabled"])

    def test_pons_wallet_is_whitelisted_for_quote_token_payment(self):
        from smart_money_monitor import QUOTE_TOKEN_PAYMENT_WALLETS

        self.assertIn(PONS_WALLET, QUOTE_TOKEN_PAYMENT_WALLETS)


if __name__ == "__main__":
    unittest.main()