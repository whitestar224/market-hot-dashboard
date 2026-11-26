"""三方共振（做市商大额异动 × 涨幅榜 × AIcoin 热门榜）。

用户 2026-10-21 的口径：
1. 「异动」只认**大额**（单笔 ≥100 万美元），小单不参与；
2. 同一标的 **6 小时**只提示一次（共振是持续状态，反复弹没有新信息）；
3. 与现有弹窗**合并成一条**：命中共振就发「三方共振」，不再发普通「大额调仓」，
   同一个动作永远只出现一条弹窗。

这里测两件事：
* `board_symbol_index()` —— 榜单 payload → 「第几名、涨多少」，同币多来源取最优；
* `MarketMakerMonitor._maybe_announce()` —— 三分支与静默窗口。

弹窗文案本身在 `test_market_maker_api.TripleResonanceAlertTest`。
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import market_maker_monitor as mm

TOKEN = "0x39dbed3a2bd333467115de4566f8d8b0e4a4f2f1"
OTHER = "0x" + "7c" * 20


def seed_baseline(store, firm="wintermute", chain="bsc", days_ago=10):
    """给这家在这条链上造一条「基线」流水（``baseline=1``）。

    「新标的」的判据是**以基线为准**（用户 2026-11-16）：标的的最早池子必须建在这条
    基线之后才算新盘。``seen_at`` 得直接写 SQL —— `record_transfer` 固定用 now，
    而测试需要一个「过去某个时刻」的基线。
    """
    seen = int(time.time() * 1000) - int(days_ago * 86_400_000)
    stamp = f"{int(days_ago * 1000):08x}"[:8]
    with store._session() as conn:
        conn.execute(
            "INSERT INTO mm_transfers (chain, tx_hash, log_index, address, firm, token,"
            " symbol, decimals, amount, direction, counterparty, price_usd, usd,"
            " occurred_at, seen_at, baseline)"
            " VALUES (?, ?, 0, ?, ?, ?, 'BASE', 18, 1.0, 'in', '', 1.0, 1.0, ?, ?, 1)",
            (
                chain, "0x" + stamp * 8, "0x" + stamp * 5,
                firm, "0x" + "a1" * 20, seen, seen,
            ),
        )


def board(rows, source_id="binance-gainers", change_key="change"):
    return {
        "sources": [
            {
                "id": source_id,
                "rows": [
                    {"symbol": symbol, change_key: change, "price": 1.23, "chain": "bsc"}
                    for symbol, change in rows
                ],
            }
        ]
    }


class BoardSymbolIndexTest(unittest.TestCase):
    def test_ranks_are_one_based_row_positions(self):
        index = mm.board_symbol_index(
            board([("PONSUSDT", 42.5), ("DOODUSDT", 31.0)]), {"binance-gainers"}
        )
        self.assertEqual(index["PONS"]["rank"], 1)
        self.assertEqual(index["DOOD"]["rank"], 2)
        self.assertAlmostEqual(index["PONS"]["changePct"], 42.5)

    def test_the_suffix_is_stripped_so_boards_can_intersect(self):
        index = mm.board_symbol_index(board([("BTCUSDT", 3.0)]), {"binance-gainers"})
        self.assertIn("BTC", index)
        self.assertNotIn("BTCUSDT", index)

    def test_a_symbol_on_several_boards_keeps_the_best_rank(self):
        payload = {
            "sources": [
                {"id": "okx-gainers", "rows": [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "PONS"}]},
                {"id": "binance-gainers", "rows": [{"symbol": "PONS"}]},
            ]
        }
        index = mm.board_symbol_index(payload, {"okx-gainers", "binance-gainers"})
        self.assertEqual(index["PONS"]["rank"], 1, "应该保留排名最靠前的那条")

    def test_sources_outside_the_whitelist_are_ignored(self):
        payload = {
            "sources": [
                {"id": "futu-hk-gainers", "rows": [{"symbol": "TENCENT"}]},
                {"id": "binance-gainers", "rows": [{"symbol": "PONS"}]},
            ]
        }
        index = mm.board_symbol_index(payload, {"binance-gainers"})
        self.assertEqual(list(index), ["PONS"])

    def test_a_broken_payload_yields_nothing_instead_of_raising(self):
        for payload in (None, {}, {"sources": None}, {"sources": [None, {}, {"id": "binance-gainers"}]}):
            self.assertEqual(mm.board_symbol_index(payload, {"binance-gainers"}), {})

    def test_an_empty_whitelist_matches_nothing(self):
        self.assertEqual(mm.board_symbol_index(board([("PONS", 1.0)]), set()), {})


class TripleResonanceGateTest(unittest.TestCase):
    """大额调仓的三条路：命中共振 / 命中但被静默 / 没命中。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = mm.MarketMakerStore(Path(self._tmp.name) / "mm.sqlite")
        self.monitor = mm.MarketMakerMonitor(
            self.store, interval=300, addresses_per_cycle=30, large_usd=1_000_000.0
        )
        self.sent = []
        self.monitor.set_alert_callback(self.sent.append)
        # 「新标的」要先过标的成熟度闸，那条路会问 DexScreener —— 单测里打桩成新盘。
        self._fresh = mock.patch.object(
            mm, "resolve_token_listed_at",
            return_value=(int(time.time() * 1000) - 2 * 86_400_000, "dexscreener"),
        )
        self._fresh.start()
        seed_baseline(self.store, chain="robinhood")

    def tearDown(self):
        self._fresh.stop()
        self._tmp.cleanup()

    def transfer(self, **overrides):
        row = {
            "firm": "wintermute",
            "chain": "robinhood",
            "token": TOKEN,
            "symbol": "PONS",
            "direction": "in",
            "amount": 900_000.0,
            "usd": 2_400_000.0,
            "counterparty": "0x" + "1a" * 20,
            "tx_hash": "0x" + "be" * 32,
            "occurred_at": 1_792_500_000_000,
        }
        row.update(overrides)
        return row

    def arm(self, allowed=True, **detail):
        self.monitor.set_resonance_qualifier(
            lambda symbol: {"ok": allowed, **detail} if allowed else {"ok": False, "note": "未共振"}
        )

    def announce(self, **overrides):
        self.monitor._maybe_announce(self.transfer(**overrides), {"newToken": False}, announce_ok=False)

    def test_a_large_transfer_on_a_resonating_symbol_is_upgraded(self):
        self.arm(gainersRank=3, gainersChangePct=18.2, aicoinRank=12)
        self.announce()
        self.assertEqual(len(self.sent), 1)
        event = self.sent[0]
        self.assertEqual(event["reason"], "triple-resonance")
        self.assertEqual(event["resonance"], {
            "gainersRank": 3, "gainersChangePct": 18.2, "aicoinRank": 12,
        })

    def test_the_same_symbol_is_silent_for_six_hours(self):
        self.arm(gainersRank=3, aicoinRank=12)
        self.announce()
        self.announce()  # 同一只币的下一笔大额
        self.assertEqual([e["reason"] for e in self.sent], ["triple-resonance", "large-transfer"])
        self.assertEqual(self.monitor.resonance_blocked, 1)

    def test_another_symbol_in_the_same_window_still_pops(self):
        self.arm(gainersRank=3, aicoinRank=12)
        self.announce()
        self.announce(token=OTHER, symbol="DOOD")
        self.assertEqual([e["reason"] for e in self.sent], ["triple-resonance", "triple-resonance"])

    def test_the_cooldown_expires(self):
        self.arm(gainersRank=3, aicoinRank=12)
        self.announce()
        # 把台账推回 6 小时零 1 秒之前：下一次应该重新算共振。
        with self.store._lock, self.store._session() as conn:
            conn.execute(
                "UPDATE mm_resonance SET announced_at = ?",
                (int((time.time() - mm.RESONANCE_COOLDOWN_SECONDS - 1) * 1000),),
            )
        self.announce()
        self.assertEqual([e["reason"] for e in self.sent], ["triple-resonance", "triple-resonance"])

    def test_without_resonance_it_stays_a_plain_large_transfer(self):
        self.arm(allowed=False)
        self.announce()
        self.assertEqual(self.sent[0]["reason"], "large-transfer")

    def test_without_a_qualifier_it_never_claims_resonance(self):
        self.announce()
        self.assertEqual(self.sent[0]["reason"], "large-transfer")

    def test_a_broken_qualifier_degrades_to_the_plain_alert(self):
        def boom(_symbol):
            raise RuntimeError("榜单读不到")

        self.monitor.set_resonance_qualifier(boom)
        self.announce()
        self.assertEqual(self.sent[0]["reason"], "large-transfer")
        self.assertIn("共振查询出错", self.monitor.resonance_note)

    def test_small_transfers_never_consult_the_gate(self):
        calls = []
        self.monitor.set_resonance_qualifier(lambda symbol: calls.append(symbol) or {"ok": True})
        self.announce(usd=120_000.0)
        self.assertEqual(self.sent, [])
        self.assertEqual(calls, [], "没到大额门槛就不该去查榜单")

    def test_a_new_token_is_never_downgraded_by_the_resonance_gate(self):
        self.arm(gainersRank=1, aicoinRank=2)
        self.monitor.set_entry_qualifier(lambda symbol: (True, ""))
        self.monitor._maybe_announce(
            self.transfer(), {"newToken": True, "position": {}}, announce_ok=True
        )
        self.assertEqual(self.sent[0]["reason"], "new-token")


class NewTokenAlertGateTest(unittest.TestCase):
    """「新标的」的两个护栏（用户 2026-11-15）：

    * **不再要求与涨幅榜 / AIcoin 共振** —— 做市商接新币通常在它上榜之前，
      那道闸专门漏掉最早的信号；
    * **改成金额闸** ``MM_NEW_TOKEN_MIN_USD``（$100）—— 近 7 天 247 个新仓位里
      240 个是 ``usd = 0`` 的空投 / 同形字骗局币，不设闸一天三十几条红色噪音。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = mm.MarketMakerStore(Path(self._tmp.name) / "mm.sqlite")
        self.monitor = mm.MarketMakerMonitor(self.store, large_usd=1_000_000.0)
        self.sent = []
        self.monitor.set_alert_callback(self.sent.append)
        # 标的成熟度会去问 DexScreener / RPC —— 单测一律打桩成「3 天前建池」，
        # 否则这一组「金额闸」的用例会变成联网测试。
        self._fresh = mock.patch.object(
            mm, "resolve_token_listed_at",
            return_value=(int(time.time() * 1000) - 3 * 86_400_000, "dexscreener"),
        )
        self._fresh.start()
        seed_baseline(self.store)  # 基线在 10 天前，3 天前上线的币算新盘

    def tearDown(self):
        self._fresh.stop()
        self._tmp.cleanup()

    def transfer(self, **overrides):
        row = {
            "firm": "wintermute", "chain": "bsc", "token": TOKEN, "symbol": "PONS",
            "direction": "in", "amount": 1_000.0, "usd": 250_000.0,
            "counterparty": "", "tx_hash": "0x" + "be" * 32, "occurred_at": 1_792_500_000_000,
        }
        row.update(overrides)
        return row

    def new_token(self, **overrides):
        self.monitor._maybe_announce(
            self.transfer(**overrides), {"newToken": True, "position": {}}, announce_ok=True
        )

    def test_the_entry_gate_no_longer_blocks_a_new_token(self):
        self.monitor.set_entry_qualifier(lambda symbol: (False, "未与涨幅榜 / AIcoin 热门榜共振"))
        self.new_token()
        self.assertEqual([e["reason"] for e in self.sent], ["new-token"])
        self.assertEqual(self.monitor.entry_filtered, 0, "这条路已经不走进场闸")

    def test_a_dust_amount_is_not_worth_a_red_alert(self):
        self.new_token(usd=0.0)
        self.assertEqual(self.sent, [])

    def test_the_gate_boundary_is_hundred_dollars(self):
        self.new_token(usd=99.0)
        self.assertEqual(self.sent, [])
        self.new_token(usd=100.0)
        self.assertEqual([e["reason"] for e in self.sent], ["new-token"])

    def test_it_still_waits_for_the_whole_firm_to_be_swept(self):
        self.monitor._maybe_announce(
            self.transfer(), {"newToken": True, "position": {}}, announce_ok=False
        )
        self.assertEqual(self.sent, [], "首轮扫街没扫完就报，等于把陈年老仓全报一遍")

    def test_board_ranks_ride_along_but_are_optional(self):
        self.monitor.set_resonance_qualifier(
            lambda symbol: {"ok": True, "gainersRank": 3, "aicoinRank": 12}
        )
        self.new_token()
        self.assertEqual(self.sent[0]["resonance"]["gainersRank"], 3)
        self.sent.clear()
        self.monitor.set_resonance_qualifier(lambda symbol: {"ok": False, "note": "未共振"})
        self.new_token(tx_hash="0x" + "cf" * 32)
        self.assertEqual(self.sent[0]["resonance"], {}, "没上榜也要弹，只是不带榜单信息")


class TokenMaturityGateTest(unittest.TestCase):
    """「新标的」的第三个护栏：**标的必须是基线之后才上线的**（用户 2026-11-16）。

    起因：2026-11-15 22:18 ~ 11-16 08:13 十个小时连弹 8 条红色的「做市商新标的」，
    全是老币 —— ``WIF``（2023 年上线）、``USD1``（稳定币）、``METAX`` / ``EWYx`` /
    ``SNDKx`` / ``INTCx`` / ``SKHYx``（xStocks 代币化股票）。根因是「新标的」= 「我们
    库里第一次见到这个 (厂家, 币) 组合」，而基线只覆盖每个地址最近几十条签名。

    用户的口径（原话）：「老币不用设置时间限制，**以基线为准**，之前上线的都算老币，
    只监控基线后出现的新币并且第一次出现在钱包活动里的」。所以这里**不用滚动窗口**，
    只比「标的最早池子的创建时间」和「这家在这条链上的基线时刻」。

    四种否决 + 一个放行，逐条钉住。
    """

    #: 基线定在 10 天前：3 天前上线的币算新盘，1091 天前的老币算老币。
    BASELINE_DAYS = 10

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = mm.MarketMakerStore(Path(self._tmp.name) / "mm.sqlite")
        self.monitor = mm.MarketMakerMonitor(self.store, large_usd=1_000_000.0)
        self.sent = []
        self.monitor.set_alert_callback(self.sent.append)
        self.calls = 0
        seed_baseline(self.store, days_ago=self.BASELINE_DAYS)

    def tearDown(self):
        self._tmp.cleanup()

    def _resolve(self, days_ago=None, source="dexscreener"):
        """桩：返回「N 天前建池」；``days_ago=None`` 表示查不到。"""
        self.calls += 1
        if days_ago is None:
            return 0, "unknown"
        return int(time.time() * 1000) - int(days_ago * 86_400_000), source

    def announce(self, *, token=TOKEN, symbol="PONS", chain="bsc", usd=250_000.0, digest="be"):
        row = {
            "firm": "wintermute", "chain": chain, "token": token, "symbol": symbol,
            "direction": "in", "amount": 1_000.0, "usd": usd,
            "counterparty": "", "tx_hash": "0x" + digest * 32, "occurred_at": 1_792_500_000_000,
        }
        self.monitor._maybe_announce(row, {"newToken": True, "position": {}}, announce_ok=True)

    def test_a_token_listed_after_the_baseline_pops(self):
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(3)):
            self.announce()
        self.assertEqual([e["reason"] for e in self.sent], ["new-token"])
        self.assertEqual(self.sent[0]["maturity"]["reason"], "fresh")
        self.assertGreater(self.sent[0]["maturity"]["listedAt"], self.sent[0]["maturity"]["baselineAt"])

    def test_a_token_that_existed_before_the_baseline_is_silent(self):
        """WIF 是 2023 年的币：金额再大也不能报「新标的」。"""
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(1091)):
            self.announce()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.monitor.new_token_filtered, 1)

    def test_the_boundary_is_the_baseline_itself(self):
        """判据是**严格大于**基线：恰好等于基线那一刻上线的，也算老币。"""
        baseline = self.store.baseline_at("wintermute", "bsc")
        exact = self.monitor._maturity_from_listed(baseline, "dexscreener", baseline)
        self.assertFalse(exact["ok"], "listed_at == baseline 不该算新盘")
        one_ms_later = self.monitor._maturity_from_listed(baseline + 1, "dexscreener", baseline)
        self.assertTrue(one_ms_later["ok"], "比基线晚 1 毫秒就算新盘")
        # 走完整链路再验一次：10.5 天前建池（早于 10 天前的基线）→ 不弹。
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(self.BASELINE_DAYS + 0.5)):
            self.announce(token=OTHER, digest="ce")
        self.assertEqual(self.sent, [], "比基线早，算老币")
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(self.BASELINE_DAYS - 1)):
            self.announce(token="0x" + "9d" * 20, digest="cf")
        self.assertEqual([e["reason"] for e in self.sent], ["new-token"], "比基线晚一天，算新盘")

    def test_a_firm_without_a_baseline_is_silent(self):
        """这家在这条链上还没建基线 → 没有参照系，一律不判「新」。"""
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(1)):
            self.announce(chain="base")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.monitor.new_token_filtered, 1)
        self.assertIn("no-baseline", self.monitor.new_token_filter_note)

    def test_a_stablecoin_is_never_a_new_token(self):
        """USD1 是稳定币：不查任何外部接口，直接否决。"""
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(1)):
            self.announce(symbol="USD1")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.calls, 0, "结构性否决不该产生任何网络调用")

    def test_tokenized_equities_are_never_new_tokens(self):
        """METAX / EWYx / SNDKx / INTCx / SKHYx —— 就算池子是昨天建的也不算新盘。"""
        for index, symbol in enumerate(("METAX", "EWYx", "SNDKx", "INTCx", "SKHYx")):
            with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(0.5)):
                self.announce(symbol=symbol, digest=f"{index:02d}")
        self.assertEqual(self.sent, [])

    def test_an_unknown_listing_date_stays_silent(self):
        """查不到上市时间 → 不弹（宁可漏，不可吵）。"""
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(None)):
            self.announce()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.monitor.new_token_filtered, 1)

    def test_a_mint_with_a_thousand_signatures_counts_as_old(self):
        baseline = self.store.baseline_at("wintermute", "solana") or int(time.time() * 1000)
        verdict = self.monitor._maturity_from_listed(0, "sigs-full", baseline)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], "too-old")

    def test_the_verdict_is_cached_so_the_second_transfer_costs_nothing(self):
        with mock.patch.object(mm, "resolve_token_listed_at", side_effect=lambda *a: self._resolve(400)):
            self.announce(digest="dd")
            self.announce(digest="de")
        self.assertEqual(self.calls, 1, "同一只老币只该问一次外部接口")

    def test_the_baseline_is_the_earliest_scan_not_the_latest(self):
        """后来新加的地址也会写 baseline=1，但不能把基线往后推。"""
        seed_baseline(self.store, days_ago=2)  # 模拟「昨天又加了一个地址」
        baseline = self.store.baseline_at("wintermute", "bsc")
        expected = int(time.time() * 1000) - self.BASELINE_DAYS * 86_400_000
        self.assertLess(abs(baseline - expected), 60_000, "基线必须仍是最早那次扫街")

    def test_no_baseline_yields_zero(self):
        self.assertEqual(self.store.baseline_at("wintermute", "elysium"), 0)

    def test_xstocks_mints_are_recognised_by_prefix(self):
        # AAPLx 的真实 mint：xStocks 的铸造地址一律 ``Xs`` 开头。
        self.assertTrue(
            mm.looks_like_tokenized_equity("solana", "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp", "AAPLx")
        )
        # 普通 memecoin 不命中。
        self.assertFalse(
            mm.looks_like_tokenized_equity("solana", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "WIF")
        )


if __name__ == "__main__":
    unittest.main()
