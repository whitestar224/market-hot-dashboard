"""被夹检测引擎（sandwich_detector.py）的纯逻辑回归测试。

链路本身依赖 GMGN CLI 与公链 RPC，都不可在单测里真跑；但「什么算被夹」这件事
完全落在纯函数里：Transfer 拍平、池子净流、V2 价格、front/victim/back 判定、
损失估算。这里把那些判据钉死——它们是整套检测的公信力所在。
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sandwich_detector as sd


TRANSFER = sd.TRANSFER_TOPIC
V2 = sd.SWAP_V2_TOPIC
POOL = "0x" + "aa" * 20
TOKEN = "0x" + "bb" * 20
QUOTE = "0x" + "cc" * 20


def addr(tail: str) -> str:
    return "0x" + tail.rjust(40, "0")


def transfer(to, value, *, tx="0x" + "1" * 64, tx_index=1, log_index=0, sender="0x" + "d" * 40):
    """一条标准 ERC-20 Transfer 日志（3 个 topic）。"""
    return {
        "address": TOKEN,
        "topics": [TRANSFER, "0x" + sender[-40:], "0x" + to[-40:]],
        "data": hex(value),
        "transactionHash": tx,
        "transactionIndex": hex(tx_index),
        "logIndex": hex(log_index),
    }


def word(value: int) -> str:
    return f"{value:064x}"


def v2_log(*, a0in, a1in, a0out, a1out, address=POOL):
    """标准 V2 Swap 日志：四段 uint256 的 data。"""
    return {
        "address": address,
        "topics": [V2, "0x" + "0" * 40, "0x" + "0" * 40],
        "data": "0x" + word(a0in) + word(a1in) + word(a0out) + word(a1out),
    }


def entry(tx_index, direction, *, sender="0x" + "9" * 40, amount=1000, tx=None, log_index=0):
    return {
        "txHash": tx or f"0x{tx_index:064x}",
        "txIndex": tx_index,
        "logIndex": log_index,
        "sender": sender,
        "amount": amount,
        "net": -amount if direction == "buy" else amount,
        "direction": direction,
    }


class ChainAndAddressTests(unittest.TestCase):
    def test_chain_aliases_collapse_to_the_four_evm_chains(self):
        self.assertEqual(sd.normalize_chain("BSC"), "bsc")
        self.assertEqual(sd.normalize_chain("eth"), "ethereum")
        self.assertEqual(sd.normalize_chain("Binance"), "bsc")
        self.assertEqual(sd.normalize_chain("robinhood"), "robinhood")

    def test_solana_and_nonsense_are_refused(self):
        for bad in ("sol", "solana", "", None, "avax"):
            with self.subTest(bad=bad):
                self.assertEqual(sd.normalize_chain(bad), "")

    def test_only_evm_addresses_pass(self):
        self.assertTrue(sd.is_evm_address("0x" + "aB" * 20))
        self.assertFalse(sd.is_evm_address("0x1234"))
        self.assertFalse(sd.is_evm_address("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"))
        self.assertFalse(sd.is_evm_address(""))


class TransferParsingTests(unittest.TestCase):
    def test_three_topic_transfers_are_flattened(self):
        other = addr("ee")
        rows = sd.transfer_rows([transfer(other, 1234)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["to"], other)
        self.assertEqual(rows[0]["value"], 1234)
        self.assertEqual(rows[0]["txHash"], "0x" + "1" * 64)

    def test_erc721_four_topic_transfers_are_ignored(self):
        # ERC-721 的 Transfer 有 4 个 topic，data 为空——混进来数量会算歪。
        log = transfer(addr("ee"), 0)
        log["topics"].append("0x" + "0" * 64)
        self.assertEqual(sd.transfer_rows([log]), [])

    def test_broken_rows_never_raise(self):
        self.assertEqual(sd.transfer_rows([None, {}, {"topics": []}, "nope"]), [])


class PoolFlowTests(unittest.TestCase):
    def test_flow_sign_says_who_is_buying(self):
        # 买入 = 代币**流出**池子；卖出 = 代币**流入**池子。
        buy = transfer(addr("ee"), 500, tx="0x" + "2" * 64)
        buy["topics"][1] = "0x" + POOL[-40:]
        sell = transfer(POOL, 700, tx="0x" + "3" * 64, tx_index=2)
        flows = sd.pool_net_flows(sd.transfer_rows([buy, sell]), POOL)
        self.assertEqual(flows["0x" + "2" * 64]["direction"], "buy")
        self.assertEqual(flows["0x" + "2" * 64]["net"], -500)
        self.assertEqual(flows["0x" + "3" * 64]["direction"], "sell")
        self.assertEqual(flows["0x" + "3" * 64]["net"], 700)

    def test_internal_transfers_inside_one_tx_are_netted(self):
        # 一笔交易里路由拆单：先卖 300 再买 100，净卖出 200。
        sell = transfer(POOL, 300, tx="0x" + "4" * 64, log_index=0)
        buy = transfer(addr("ee"), 100, tx="0x" + "4" * 64, log_index=1)
        buy["topics"][1] = "0x" + POOL[-40:]
        flows = sd.pool_net_flows(sd.transfer_rows([sell, buy]), POOL)
        self.assertEqual(flows["0x" + "4" * 64]["net"], 200)
        self.assertEqual(flows["0x" + "4" * 64]["direction"], "sell")

    def test_transactions_that_never_touch_the_pool_are_dropped(self):
        unrelated = transfer(addr("ee"), 500, tx="0x" + "5" * 64)
        self.assertEqual(sd.pool_net_flows(sd.transfer_rows([unrelated]), POOL), {})


class PoolDetectionTests(unittest.TestCase):
    def test_pool_comes_from_the_swap_event_emitter(self):
        receipt = {"logs": [
            {"address": TOKEN, "topics": [TRANSFER]},
            v2_log(a0in=0, a1in=0, a0out=0, a1out=0),
        ]}
        self.assertEqual(sd.swap_pool_from_receipt(receipt), POOL)

    def test_v3_swaps_also_mark_the_pool(self):
        receipt = {"logs": [{"address": POOL, "topics": [sd.SWAP_V3_TOPIC], "data": "0x"}]}
        self.assertEqual(sd.swap_pool_from_receipt(receipt), POOL)

    def test_no_amm_swap_means_no_pool(self):
        self.assertEqual(sd.swap_pool_from_receipt({"logs": [{"address": TOKEN, "topics": [TRANSFER]}]}), "")
        self.assertEqual(sd.swap_pool_from_receipt({}), "")


class HexWordTests(unittest.TestCase):
    """事件 data 切出来的字是**裸十六进制**，不能借道十进制解析。"""

    def test_bare_hex_never_passes_through_float(self):
        # word(1000) 的尾巴是 "3e8"：走十进制分支会被 float() 读成 3e8 = 3 亿。
        self.assertEqual(sd.hex_word("0" * 61 + "3e8"), 1000)
        self.assertEqual(sd.hex_word("0x" + "0" * 61 + "3e8"), 1000)
        # 全是数字的裸十六进制更阴 —— 会读出一个看似合理的错数。
        self.assertEqual(sd.hex_word("0" * 60 + "3039"), 12345)

    def test_garbage_is_zero(self):
        for bad in ("", None, "zz", "0x"):
            with self.subTest(bad=bad):
                self.assertEqual(sd.hex_word(bad), 0)


class V2PriceTests(unittest.TestCase):
    def test_buy_price_is_quote_paid_over_tokens_received(self):
        # 代币是 token0：买 1000 个代币付出 2 个报价币 → 0.002 quote/token
        log = v2_log(a0in=0, a1in=2, a0out=1000, a1out=0)
        swap = sd.parse_v2_swap(log, TOKEN, TOKEN)
        self.assertEqual(swap["tokenOut"], 1000)
        self.assertEqual(swap["quoteIn"], 2)
        self.assertAlmostEqual(sd.v2_price(swap, buy=True), 0.002)

    def test_token1_pools_swap_the_two_sides(self):
        log = v2_log(a0in=5, a1in=0, a0out=0, a1out=2000)
        swap = sd.parse_v2_swap(log, TOKEN, QUOTE)   # token 不是 token0
        self.assertEqual(swap["tokenOut"], 2000)
        self.assertEqual(swap["quoteIn"], 5)

    def test_sell_price_is_quote_received_over_tokens_paid(self):
        log = v2_log(a0in=1000, a1in=0, a0out=0, a1out=3)
        swap = sd.parse_v2_swap(log, TOKEN, TOKEN)
        self.assertAlmostEqual(sd.v2_price(swap, buy=False), 0.003)

    def test_unusable_sides_yield_no_price(self):
        log = v2_log(a0in=0, a1in=0, a0out=0, a1out=0)
        self.assertEqual(sd.v2_price(sd.parse_v2_swap(log, TOKEN, TOKEN), buy=True), 0.0)

    def test_truncated_payload_is_refused_outright(self):
        # 半个事件（或空 data）不能靠「缺的位当 0」蒙混出价格。
        self.assertEqual(sd.parse_v2_swap({"data": "0x" + "00" * 12}, TOKEN, TOKEN), {})
        self.assertEqual(sd.parse_v2_swap({}, TOKEN, TOKEN), {})
        self.assertEqual(sd.v2_price(sd.parse_v2_swap({}, TOKEN, TOKEN), buy=True), 0.0)


class SandwichVerdictTests(unittest.TestCase):
    def test_classic_sandwich_is_confirmed(self):
        attacker = "0x" + "7" * 40
        victim = entry(110, "buy", tx="0x" + "a" * 64)
        rows = [
            entry(109, "buy", sender=attacker, amount=1000, tx="0x" + "b" * 64),
            victim,
            entry(111, "sell", sender=attacker, amount=1000, tx="0x" + "c" * 64),
        ]
        result = sd.classify_sandwich(rows, victim["txHash"])
        self.assertEqual(result["verdict"], "confirmed")
        self.assertEqual(result["attacker"], attacker)
        self.assertTrue(result["roundTrip"])

    def test_different_attackers_are_only_suspected(self):
        rows = [
            entry(109, "buy", sender="0x" + "1" * 40, tx="0x" + "b" * 64),
            entry(110, "buy", tx="0x" + "a" * 64),
            entry(111, "sell", sender="0x" + "2" * 40, tx="0x" + "c" * 64),
        ]
        self.assertEqual(sd.classify_sandwich(rows, "0x" + "a" * 64)["verdict"], "suspected")

    def test_non_matching_round_trip_is_only_suspected(self):
        attacker = "0x" + "7" * 40
        rows = [
            entry(109, "buy", sender=attacker, amount=1000, tx="0x" + "b" * 64),
            entry(110, "buy", tx="0x" + "a" * 64),
            entry(111, "sell", sender=attacker, amount=100, tx="0x" + "c" * 64),
        ]
        result = sd.classify_sandwich(rows, "0x" + "a" * 64)
        self.assertEqual(result["verdict"], "suspected")
        self.assertFalse(result["roundTrip"])

    def test_a_lonely_trade_in_the_block_is_normal(self):
        rows = [entry(110, "buy", tx="0x" + "a" * 64)]
        self.assertEqual(sd.classify_sandwich(rows, "0x" + "a" * 64)["verdict"], "normal")

    def test_only_one_side_present_is_suspected_not_confirmed(self):
        rows = [
            entry(109, "buy", sender="0x" + "7" * 40, tx="0x" + "b" * 64),
            entry(110, "buy", tx="0x" + "a" * 64),
        ]
        self.assertEqual(sd.classify_sandwich(rows, "0x" + "a" * 64)["verdict"], "suspected")

    def test_a_sell_victim_gets_the_mirrored_reading(self):
        attacker = "0x" + "7" * 40
        rows = [
            entry(109, "sell", sender=attacker, amount=500, tx="0x" + "b" * 64),
            entry(110, "sell", tx="0x" + "a" * 64),
            entry(111, "buy", sender=attacker, amount=500, tx="0x" + "c" * 64),
        ]
        self.assertEqual(sd.classify_sandwich(rows, "0x" + "a" * 64)["verdict"], "confirmed")

    def test_unknown_when_the_victim_is_not_in_the_list(self):
        self.assertEqual(sd.classify_sandwich([entry(1, "buy")], "0x" + "f" * 64)["verdict"], "unknown")


class LossEstimationTests(unittest.TestCase):
    def activity(self, *, token_amount=1000.0, quote_amount=2.0, price_usd=0.002):
        return {"token_amount": token_amount, "quote_amount": quote_amount, "price_usd": price_usd}

    def test_quote_usd_price_is_back_solved_from_gmgn_fields(self):
        # 1000 个代币 × $0.002 = $2，付出 2 个报价币 → 报价币 $1/个。
        self.assertAlmostEqual(sd.quote_usd_price(self.activity()), 1.0)

    def test_quote_usd_price_needs_every_input(self):
        self.assertEqual(sd.quote_usd_price(self.activity(quote_amount=0)), 0.0)
        self.assertEqual(sd.quote_usd_price(self.activity(price_usd=0)), 0.0)

    def test_loss_grows_with_the_price_gap(self):
        loss = sd.estimate_loss(victim_activity=self.activity(), pre_price=0.002, victim_price=0.003)
        # 1000 个代币 × 0.001 报价币 = 1 报价币；报价币 $1 → $1
        self.assertAlmostEqual(loss["lossUsd"], 1.0)
        self.assertAlmostEqual(loss["impactPct"], 50.0)

    def test_a_cheaper_fill_is_not_a_loss(self):
        loss = sd.estimate_loss(victim_activity=self.activity(), pre_price=0.002, victim_price=0.001)
        self.assertEqual(loss["lossUsd"], 0.0)
        self.assertIn("未", loss["note"])

    def test_missing_price_data_reports_instead_of_guessing(self):
        loss = sd.estimate_loss(victim_activity=self.activity(), pre_price=0, victim_price=0.003)
        self.assertEqual(loss["lossUsd"], 0.0)
        self.assertTrue(loss["note"])

    def test_without_a_quote_price_only_the_percentage_survives(self):
        loss = sd.estimate_loss(
            victim_activity=self.activity(quote_amount=0), pre_price=0.002, victim_price=0.003,
        )
        self.assertEqual(loss["lossUsd"], 0.0)
        self.assertAlmostEqual(loss["impactPct"], 50.0)


class ActivityNormalisationTests(unittest.TestCase):
    def raw(self, **overrides):
        row = {
            "wallet": "0xAbC", "chain": "bsc", "tx_hash": "0xDEAD", "timestamp": 1791729022,
            "event_type": "buy", "token": {"address": "0xTok", "symbol": "PVP"},
            "token_amount": 2066438.94, "quote_amount": 0.09, "price_usd": 0.0000292,
            "gas_usd": 0.105, "priority_fee": 0, "tip_fee": 0, "launchpad_platform": "fourmeme",
        }
        row.update(overrides)
        return row

    def test_fields_are_lowered_and_typed(self):
        item = sd.normalize_activity("bsc", "0xAbC", self.raw())
        self.assertEqual(item["tx_hash"], "0xdead")
        self.assertEqual(item["token"], "0xtok")
        self.assertEqual(item["symbol"], "PVP")
        self.assertEqual(item["trade_at"], 1791729022)
        self.assertEqual(item["launchpad"], "fourmeme")

    def test_missing_nested_token_is_tolerated(self):
        item = sd.normalize_activity("bsc", "0xAbC", self.raw(token=None))
        self.assertEqual(item["token"], "")
        self.assertEqual(item["symbol"], "")

    def test_trade_usd_multiplies_amount_by_price(self):
        item = sd.normalize_activity("bsc", "0xAbC", self.raw())
        self.assertAlmostEqual(sd.trade_usd(item), 2066438.94 * 0.0000292)


class RealGmgnPayloadTests(unittest.TestCase):
    """真机抓下来的原始行（2026-10-13，BSC，钱包 0x8894…d4e3）。

    GMGN 的数字全是**字符串**，缺失字段是空串而不是 null —— 合成数据永远不会暴露
    这一点，所以这里直接钉住真实形状。
    """

    BUY_ROW = {
        "wallet": "0x8894e0a0c962cb723c1976a4421c95949be2d4e3", "chain": "bsc",
        "tx_hash": "0x504201b7ff6935cdae59e81373ff2a67a4cd748d13b858814193b9783738a3e3",
        "timestamp": 1791729022, "event_type": "buy",
        "token": {"address": "0x33e7345d6f152f816fc9dc31b1010d65c2be66af", "symbol": "PVP"},
        "token_amount": "2066438.943015504", "quote_amount": "0.08999999999999996",
        "price_usd": "0.00002921854342915498", "cost_usd": "60.378335999999976",
        "buy_cost_usd": "60.378335999999976", "gas_usd": "0.105",
        "priority_fee": "0", "tip_fee": "0", "launchpad_platform": "fourmeme",
    }
    BURN_ROW = {
        "wallet": "0x8894e0a0c962cb723c1976a4421c95949be2d4e3", "chain": "bsc",
        "tx_hash": "0x" + "e" * 64, "timestamp": 1791729022, "event_type": "burn",
        "token": {"address": "0x" + "b3" * 20, "symbol": "DEC"},
        "token_amount": "487.6054859570854", "quote_amount": "0",
        "price_usd": "", "cost_usd": "", "buy_cost_usd": None, "gas_usd": "0",
    }

    def test_a_real_buy_row_sizes_exactly_like_gmgn_says(self):
        item = sd.normalize_activity("bsc", self.BUY_ROW["wallet"], self.BUY_ROW)
        self.assertEqual(item["token"], "0x33e7345d6f152f816fc9dc31b1010d65c2be66af")
        self.assertEqual(item["event_type"], "buy")
        self.assertEqual(item["launchpad"], "fourmeme")
        self.assertEqual(item["trade_at"], 1791729022)
        # 60.378 = GMGN 自己的 cost_usd；乘法算出来也是同一个数（互为验证）。
        self.assertAlmostEqual(sd.trade_usd(item), 60.3783, places=3)
        self.assertAlmostEqual(sd.trade_usd(item), 2066438.943015504 * 0.00002921854342915498, places=3)

    def test_a_real_burn_row_is_not_a_trade(self):
        item = sd.normalize_activity("bsc", self.BURN_ROW["wallet"], self.BURN_ROW)
        self.assertEqual(item["event_type"], "burn")
        self.assertNotIn(item["event_type"], sd.TRADE_EVENT_TYPES)
        # 空串必须落成 0，不能变成 NaN 或抛异常。
        self.assertEqual(item["price_usd"], 0.0)
        self.assertEqual(item["cost_usd"], 0.0)

    def test_gmgn_cost_usd_wins_when_the_price_field_is_blank(self):
        # 只有乘法的话这笔会被金额门槛静默丢掉，而它是真实成交。
        row = {**self.BUY_ROW, "price_usd": "", "cost_usd": "250"}
        item = sd.normalize_activity("bsc", self.BUY_ROW["wallet"], row)
        self.assertAlmostEqual(sd.trade_usd(item), 250.0)


class RealMainnetSwapTests(unittest.TestCase):
    """一条真实抓下来的 PancakeSwap V2 WBNB/USDT Swap（BSC 主网，2026-10-13）。

    留这条日志是因为它是「方向对不对」的唯一硬证据：BSC 三个公共 RPC 端点交叉核对
    当时储备为 USDT 29,004,380 / WBNB 39,160 → BNB ≈ $740.6，而同一条日志是
    1.05753 USDT 换出 0.00142428 WBNB = $742.5 —— 只有把 token0 认成 USDT、
    把 amount1Out 认成 WBNB，这两个数才对得上。
    """

    POOL = "0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae"
    TOKEN0 = "0x55d398326f99059ff775485246999027b3197955"   # 池子的 token0 = USDT
    WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
    ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"   # PancakeSwap V2 Router
    LOG = {
        "address": POOL,
        "topics": [V2, "0x" + "0" * 24 + ROUTER[2:], "0x" + "0" * 24 + ROUTER[2:]],
        "data": "0x"
                + "0000000000000000000000000000000000000000000000000ead1da7040cb591"
                + "0" * 64
                + "0" * 64
                + "00000000000000000000000000000000000000000000000000050f603fdd5d38",
        "blockNumber": "0x7980a18", "transactionIndex": "0x46", "logIndex": "0x157",
    }

    def test_the_captured_swap_prices_bnb_like_the_pool_reserves_do(self):
        swap = sd.parse_v2_swap(self.LOG, self.WBNB, self.TOKEN0)
        self.assertEqual(swap["tokenOut"], 1424280946302264)         # 0.00142428 WBNB
        self.assertEqual(swap["quoteIn"], 1057534090666620305)       # 1.05753 USDT
        self.assertAlmostEqual(sd.v2_price(swap, buy=True), 742.5038, places=3)

    def test_reading_it_as_token0_flips_the_sides(self):
        # 同一笔日志换个 token 口径，代币侧就变成 USDT 净流入（在池子里是买 WBNB）。
        swap = sd.parse_v2_swap(self.LOG, self.TOKEN0, self.TOKEN0)
        self.assertEqual(swap["tokenOut"], 0)
        self.assertEqual(swap["tokenIn"], 1057534090666620305)

    def test_the_pool_is_found_from_a_receipt(self):
        self.assertEqual(sd.swap_pool_from_receipt({"logs": [self.LOG]}), self.POOL)

    def test_the_router_never_masquerades_as_the_pool(self):
        # 日志里两个 indexed topic 都是路由地址；池子只能取 emit 者，取错就全盘皆错。
        self.assertNotEqual(sd.swap_pool_from_receipt({"logs": [self.LOG]}), self.ROUTER)


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = sd.SandwichStore(Path(tmp.name) / "sandwich.sqlite")

    def activity_row(self, tx="0x" + "1" * 64, **overrides):
        row = {
            "chain": "bsc", "tx_hash": tx, "event_type": "buy", "token": TOKEN,
            "wallet": addr("11"), "symbol": "PVP", "token_amount": 1000.0,
            "quote_amount": 2.0, "price_usd": 0.002, "trade_at": 1791729022,
            "gas_usd": 0.1, "priority_fee": 0, "tip_fee": 0, "launchpad": "",
        }
        row.update(overrides)
        return row

    def test_wallet_round_trip_and_validation(self):
        self.store.save_wallet("bsc", "0x" + "AB" * 20, "主钱包")
        wallets = self.store.wallets()
        self.assertEqual(len(wallets), 1)
        self.assertEqual(wallets[0]["address"], "0x" + "ab" * 20)
        with self.assertRaises(ValueError):
            self.store.save_wallet("solana", "0x" + "ab" * 20)
        with self.assertRaises(ValueError):
            self.store.save_wallet("bsc", "not-an-address")
        self.assertTrue(self.store.remove_wallet("bsc", "0x" + "ab" * 20))
        self.assertEqual(self.store.wallets(), [])

    def test_duplicate_activities_are_ingested_once(self):
        self.assertTrue(self.store.upsert_activity(self.activity_row()))
        self.assertFalse(self.store.upsert_activity(self.activity_row()))
        # 重复入库只刷新市场字段，不会把结论冲掉。
        self.store.finish(self.store.pending(1)[0], status="done", verdict="normal", detail={"verdict": "normal"})
        self.store.upsert_activity(self.activity_row(price_usd=0.003))
        self.assertEqual(self.store.results(5)[0]["verdict"], "normal")

    def test_pending_queue_respects_the_retry_ceiling(self):
        self.store.upsert_activity(self.activity_row())
        item = self.store.pending(1)[0]
        for _ in range(sd.MAX_CHECK_ATTEMPTS):
            self.store.finish(item, status="retry", verdict="unknown", detail={"verdict": "unknown"})
        self.assertEqual(self.store.pending(1), [])

    def test_counts_and_results_split_by_verdict(self):
        for index, verdict in enumerate(("confirmed", "normal", "suspected")):
            tx = f"0x{index:064x}"
            self.store.upsert_activity(self.activity_row(tx=tx))
            item = next(row for row in self.store.pending(5) if row["tx_hash"] == tx)
            self.store.finish(item, status="done", verdict=verdict, detail={"verdict": verdict})
        counts = self.store.counts()
        self.assertEqual(counts["confirmed"], 1)
        self.assertEqual(counts["suspected"], 1)
        self.assertEqual(len(self.store.results(10, only_flagged=True)), 2)

    def test_results_carry_an_explorer_link(self):
        self.store.upsert_activity(self.activity_row())
        self.store.finish(self.store.pending(1)[0], status="done", verdict="normal", detail={"verdict": "normal"})
        row = self.store.results(1)[0]
        self.assertTrue(row["explorerUrl"].startswith("https://bscscan.com/tx/0x"))
        self.assertIsInstance(row["detail"], dict)

    def test_min_trade_usd_setting_is_readable_and_sanitised(self):
        self.assertEqual(self.store.min_trade_usd(), sd.DEFAULT_MIN_TRADE_USD)
        self.store.set_setting("minTradeUsd", "150")
        self.assertEqual(self.store.min_trade_usd(), 150.0)
        self.store.set_setting("minTradeUsd", "abc")
        self.assertEqual(self.store.min_trade_usd(), 0.0)


class CheckActivityTests(unittest.TestCase):
    """取证流程的编排：RPC 全被替身接管，只验判据与状态迁移。"""

    def activity(self):
        return {"chain": "bsc", "tx_hash": "0x" + "a" * 64, "token": TOKEN, "event_type": "buy",
                "token_amount": 1000.0, "quote_amount": 2.0, "price_usd": 0.002}

    def fake_rpc(self, mapping):
        def caller(chain, method, params):
            key = (method, json.dumps(params, sort_keys=True))
            if key in mapping:
                return mapping[key]
            raise RuntimeError(f"未预置的 RPC 调用：{method}")
        return caller

    def test_a_solo_trade_in_the_block_is_normal_without_pulling_the_block(self):
        calls = []

        def caller(chain, method, params):
            calls.append(method)
            if method == "eth_getTransactionByHash":
                return {"blockNumber": hex(100), "transactionIndex": hex(3)}
            if method == "eth_getLogs":
                return [transfer(addr("ee"), 1, tx="0x" + "a" * 64, tx_index=3)]
            raise RuntimeError("不该再调其它 RPC")

        with patch.object(sd, "rpc", caller):
            verdict = sd.check_activity("bsc", self.activity())
        self.assertEqual(verdict["verdict"], "normal")
        self.assertEqual(verdict["blockNumber"], 100)
        self.assertNotIn("eth_getBlockByNumber", calls)

    def test_a_non_amm_trade_is_flagged_as_undecidable(self):
        def caller(chain, method, params):
            if method == "eth_getTransactionByHash":
                return {"blockNumber": hex(100), "transactionIndex": hex(3)}
            if method == "eth_getLogs":
                return [
                    transfer(addr("ee"), 1, tx="0x" + "a" * 64, tx_index=3),
                    transfer(addr("ff"), 1, tx="0x" + "b" * 64, tx_index=4),
                ]
            if method == "eth_getTransactionReceipt":
                return {"logs": [{"address": TOKEN, "topics": [TRANSFER]}]}
            raise RuntimeError("不该再调其它 RPC")

        with patch.object(sd, "rpc", caller):
            verdict = sd.check_activity("bsc", self.activity())
        self.assertEqual(verdict["verdict"], "suspected")
        self.assertIn("标准 AMM", verdict["reason"])

    def test_missing_tx_hash_is_unknown_not_a_guess(self):
        verdict = sd.check_activity("bsc", {"chain": "bsc", "tx_hash": "", "token": TOKEN})
        self.assertEqual(verdict["verdict"], "unknown")

    def test_rpc_failure_degrades_to_unknown(self):
        with patch.object(sd, "rpc", side_effect=RuntimeError("rpc down")):
            verdict = sd.check_activity("bsc", self.activity())
        self.assertEqual(verdict["verdict"], "unknown")
        self.assertIn("取交易失败", verdict["reason"])

    def test_a_full_sandwich_is_confirmed_end_to_end(self):
        attacker = "0x" + "7" * 40
        victim_hash = "0x" + "a" * 64
        front_hash = "0x" + "b" * 64
        back_hash = "0x" + "c" * 64

        buy_logs = []
        for tx_hash, index, amount in ((front_hash, 9, 1000), (victim_hash, 10, 1000)):
            log = transfer(addr("ee"), amount, tx=tx_hash, tx_index=index)
            log["topics"][1] = "0x" + POOL[-40:]      # 代币从池子流出 → 买入
            buy_logs.append(log)
        sell_log = transfer(POOL, 1000, tx=back_hash, tx_index=11)

        def caller(chain, method, params):
            if method == "eth_getTransactionByHash":
                return {"blockNumber": hex(100), "transactionIndex": hex(10)}
            if method == "eth_getLogs":
                return [*buy_logs, sell_log]
            if method == "eth_getTransactionReceipt":
                if params[0] == victim_hash:
                    return {"logs": [v2_log(a0in=0, a1in=2, a0out=1000, a1out=0)]}
                if params[0] == front_hash:
                    return {"logs": [v2_log(a0in=0, a1in=1, a0out=1000, a1out=0)]}
                return {"logs": []}
            if method == "eth_getBlockByNumber":
                return {"transactions": [
                    {"hash": front_hash, "from": attacker, "transactionIndex": hex(9)},
                    {"hash": victim_hash, "from": addr("12"), "transactionIndex": hex(10)},
                    {"hash": back_hash, "from": attacker, "transactionIndex": hex(11)},
                ]}
            if method == "eth_call":
                return "0x" + "0" * 24 + TOKEN[2:]          # token0 == 代币
            raise RuntimeError(f"未预置的 RPC 调用：{method}")

        with patch.object(sd, "rpc", caller):
            verdict = sd.check_activity("bsc", self.activity())

        self.assertEqual(verdict["verdict"], "confirmed")
        self.assertEqual(verdict["attacker"], attacker)
        self.assertEqual(verdict["pool"], POOL)
        # 夹前价 0.001 → 受害者成交价 0.002：1000 个代币多付 1 个报价币，报价币 $1 → $1
        self.assertTrue(verdict["price"]["available"])
        self.assertAlmostEqual(verdict["price"]["lossUsd"], 1.0)
        self.assertAlmostEqual(verdict["price"]["impactPct"], 100.0)


class DetectorCycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = sd.SandwichStore(Path(tmp.name) / "sandwich.sqlite")
        self.detector = sd.SandwichDetector(self.store, max_checks_per_cycle=5)

    def raw_activity(self, tx="0x" + "1" * 64, usd=100.0):
        """一条 GMGN 原始成交：1000 个代币，成交额恰好 ``usd`` 美元。

        报价币按 $1 记（``quote_amount == usd``），于是 ``price_usd = usd / 1000``
        —— 三个字段自洽：``trade_usd`` 与 ``quote_usd_price`` 都能反推回 usd。
        """
        return {
            "wallet": "0x" + "11" * 20, "chain": "bsc", "tx_hash": tx, "timestamp": 1791729022,
            "event_type": "buy", "token": {"address": TOKEN, "symbol": "PVP"},
            "token_amount": 1000.0, "quote_amount": usd, "price_usd": usd / 1000.0,
        }

    def test_small_trades_never_enter_the_queue(self):
        self.store.save_wallet("bsc", "0x" + "11" * 20)
        self.store.set_setting("minTradeUsd", "50")
        with patch.object(sd, "fetch_wallet_activity", return_value=[self.raw_activity(usd=10.0)]):
            added = self.detector.scan_wallets()
        self.assertEqual(added, 0)
        self.assertEqual(self.store.pending(5), [])

    def test_only_trade_events_are_ingested(self):
        self.store.save_wallet("bsc", "0x" + "11" * 20)
        rows = [
            self.raw_activity(tx="0x" + "1" * 64),
            {**self.raw_activity(tx="0x" + "2" * 64), "event_type": "burn"},
        ]
        with patch.object(sd, "fetch_wallet_activity", return_value=rows):
            added = self.detector.scan_wallets()
        self.assertEqual(added, 1)

    def test_disabled_wallets_are_skipped(self):
        self.store.save_wallet("bsc", "0x" + "11" * 20, enabled=False)
        with patch.object(sd, "fetch_wallet_activity", return_value=[self.raw_activity()]) as fetch:
            self.assertEqual(self.detector.scan_wallets(), 0)
        fetch.assert_not_called()

    def test_confirmed_hits_are_pushed_to_the_callback_once(self):
        hits = []
        detector = sd.SandwichDetector(self.store, on_confirmed=hits.append)
        self.store.save_wallet("bsc", "0x" + "11" * 20, enabled=False)
        self.store.upsert_activity({
            "chain": "bsc", "tx_hash": "0x" + "3" * 64, "event_type": "buy", "token": TOKEN,
            "wallet": "0x" + "11" * 20, "symbol": "PVP", "token_amount": 1000.0,
            "quote_amount": 2.0, "price_usd": 0.002, "trade_at": 1791729022,
            "gas_usd": 0.1, "priority_fee": 0, "tip_fee": 0, "launchpad": "",
        })
        with patch.object(sd, "check_activity", return_value={"verdict": "confirmed", "lossUsd": 1.0}):
            self.assertEqual(detector.process_pending(), 1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["token"], TOKEN)
        # 已完成的不会再被取一次。
        with patch.object(sd, "check_activity", return_value={"verdict": "confirmed"}):
            self.assertEqual(detector.process_pending(), 0)

    def test_fetch_failure_leaves_the_queue_untouched(self):
        self.store.save_wallet("bsc", "0x" + "11" * 20)
        with patch.object(sd, "fetch_wallet_activity", return_value=[]):
            self.assertEqual(self.detector.scan_wallets(), 0)

    def test_status_reports_wallets_and_counts(self):
        self.store.save_wallet("bsc", "0x" + "11" * 20)
        status = self.detector.status()
        self.assertFalse(status["running"])
        self.assertEqual(len(status["wallets"]), 1)
        self.assertIn("pending", status["counts"])


class GmgnActivityCliTests(unittest.TestCase):
    def test_solana_and_bad_addresses_never_spawn_a_process(self):
        with patch.object(sd.subprocess, "run") as run:
            self.assertEqual(sd.fetch_wallet_activity("solana", "0x" + "11" * 20), [])
            self.assertEqual(sd.fetch_wallet_activity("bsc", "0x123"), [])
        run.assert_not_called()

    def test_the_cli_argument_vector_is_exact(self):
        captured = {}

        class Result:
            returncode = 0
            stdout = json.dumps({"activities": [{"tx_hash": "0x1"}]})

        def fake_run(args, **kwargs):
            captured["args"] = args
            return Result()

        with patch.object(sd.subprocess, "run", fake_run):
            rows = sd.fetch_wallet_activity("ethereum", "0x" + "AB" * 20, limit=7)

        self.assertEqual(rows, [{"tx_hash": "0x1"}])
        args = captured["args"]
        self.assertIn("portfolio", args)
        self.assertIn("activity", args)
        self.assertEqual(args[args.index("--chain") + 1], "eth")      # GMGN 用 eth 而不是 ethereum
        self.assertEqual(args[args.index("--wallet") + 1], "0x" + "AB" * 20)
        self.assertEqual(args[args.index("--limit") + 1], "7")
        self.assertIn("--raw", args)

    def test_nonzero_exit_and_broken_json_are_swallowed(self):
        class Fail:
            returncode = 1
            stdout = ""

        class Garbage:
            returncode = 0
            stdout = "not json"

        with patch.object(sd.subprocess, "run", lambda *a, **k: Fail()):
            self.assertEqual(sd.fetch_wallet_activity("bsc", "0x" + "11" * 20), [])
        with patch.object(sd.subprocess, "run", lambda *a, **k: Garbage()):
            self.assertEqual(sd.fetch_wallet_activity("bsc", "0x" + "11" * 20), [])


class SchemaUpgradeTests(unittest.TestCase):
    def test_an_old_database_gains_the_attempts_column(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "old.sqlite"
        conn = sqlite3.connect(str(path))
        conn.executescript("""
            CREATE TABLE sandwich_activities (
                chain TEXT NOT NULL, tx_hash TEXT NOT NULL, event_type TEXT NOT NULL,
                token TEXT NOT NULL DEFAULT '', wallet TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '', token_amount REAL NOT NULL DEFAULT 0,
                quote_amount REAL NOT NULL DEFAULT 0, price_usd REAL NOT NULL DEFAULT 0,
                block_number INTEGER NOT NULL DEFAULT 0, tx_index INTEGER NOT NULL DEFAULT 0,
                trade_at INTEGER NOT NULL DEFAULT 0, observed_at INTEGER NOT NULL DEFAULT 0,
                gas_usd REAL NOT NULL DEFAULT 0, priority_fee REAL NOT NULL DEFAULT 0,
                tip_fee REAL NOT NULL DEFAULT 0, launchpad TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending', verdict TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '', detected_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chain, tx_hash, event_type, token));
        """)
        conn.commit()
        conn.close()

        sd.SandwichStore(path)
        conn = sqlite3.connect(str(path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sandwich_activities)")}
        conn.close()
        self.assertIn("attempts", columns)


if __name__ == "__main__":
    unittest.main()
