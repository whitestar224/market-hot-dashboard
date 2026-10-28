"""``top10_holders`` 的单元测试。

两个口径（用户 2026-10-21）：
* **Arkham** —— 「arkham 里的是怎么算的就怎么来」，前十行直接相加、含交易所、
  不剔池子/销毁；
* **GMGN** —— ``gmgn-cli token holders`` 前十行 ``amount_percentage`` 相加。
  它按「这条链的供应」当分母，所以多链币会和 Arkham 差好几倍（JCT 11.2% vs 54.2%）。

这跟做市商控盘那套（剔池子/销毁/交易所）是两回事，别写混。

最重要的两条铁律：
* **抓取失败 ≠ 0%**（拿不到要显示「暂不可用」，不能读成「筹码分散」）；
* **没 enable 一次网络都不发**（跑单测不该打 Arkham，也不该起 node 进程）。
"""

import json
import threading
import time
import unittest
from unittest.mock import patch

import arkham_client as ARKHAM
import top10_holders as TOP10


def holder(chain, pct, address="0xabc", **extra):
    row = {"chain": chain, "pct": pct, "address": address, "usd": 0.0, "balance": 0.0}
    row.update(extra)
    return row


def no_gmgn(chain, contract):
    """默认的 GMGN 桩：不覆盖 / 没数据。真实实现要起 node 进程，单测不能碰它。"""
    return {"pct": None, "count": 0, "reason": "GMGN 不覆盖该链"}


def make_book(**kwargs):
    kwargs.setdefault("gmgn_fetcher", no_gmgn)
    return TOP10.Top10Holders(**kwargs)


class TopHoldersPctTest(unittest.TestCase):
    """前十行直接相加。"""

    def test_the_top_ten_are_summed_verbatim(self):
        rows = [holder("ethereum", pct) for pct in (30.0, 20.0, 10.0, 5.0, 4.0, 3.0, 2.0, 1.5, 1.0, 0.5)]
        result = TOP10.top_holders_pct(rows, chain="ethereum")
        self.assertEqual(result["count"], 10)
        self.assertAlmostEqual(result["pct"], 77.0, places=2)

    def test_exchange_hot_wallets_are_counted_not_excluded(self):
        """用户截图里第 1 名 Gate、第 2 名 Bithumb —— 交易所算进去，这是口径的一部分。"""
        rows = [
            holder("ethereum", 4.9, entityName="Gate", entityType="cex"),
            holder("ethereum", 3.11, entityName="Bithumb", entityType="cex"),
        ]
        self.assertAlmostEqual(TOP10.top_holders_pct(rows, chain="ethereum")["pct"], 8.0, places=2)

    def test_pools_and_burn_addresses_are_not_excluded(self):
        rows = [
            holder("bsc", 60.0, address="0x000000000000000000000000000000000000dead"),
            holder("bsc", 10.0, address="0x0000000000000000000000000000000000000000"),
        ]
        self.assertAlmostEqual(TOP10.top_holders_pct(rows, chain="bsc")["pct"], 70.0, places=2)

    def test_the_sum_is_capped_at_one_hundred(self):
        """上游偶尔按流通量算，DOOD 实测 107% —— 超过 100 只能写 100。"""
        rows = [holder("bsc", pct) for pct in (50.0, 40.0, 17.0)]
        self.assertEqual(TOP10.top_holders_pct(rows, chain="bsc")["pct"], 100.0)

    def test_only_the_queried_chain_is_summed(self):
        """USDT 的以太坊表和波场表混着加会把两条链的筹码拼成一个数。"""
        rows = [holder("ethereum", 40.0), holder("tron", 50.0)]
        self.assertAlmostEqual(TOP10.top_holders_pct(rows, chain="ethereum")["pct"], 40.0, places=2)
        # ArbM 的链名在 Arkham 里是 arbitrum_one，别因为写法不同就漏掉。
        rows = [holder("arbitrum_one", 12.0), holder("ethereum", 3.0)]
        self.assertAlmostEqual(
            TOP10.top_holders_pct(rows, chain="arbitrum")["pct"], 12.0, places=2
        )

    def test_rows_of_other_chains_are_dropped_when_the_query_chain_is_unknown(self):
        rows = [holder("ethereum", 40.0), holder("solana", 50.0)]
        self.assertIsNone(TOP10.top_holders_pct(rows, chain="bsc")["pct"])

    def test_fewer_than_ten_holders_reports_the_real_count(self):
        rows = [holder("bsc", 55.0), holder("bsc", 5.0)]
        result = TOP10.top_holders_pct(rows, chain="bsc")
        self.assertEqual(result["count"], 2)
        self.assertAlmostEqual(result["pct"], 60.0, places=2)

    def test_no_rows_is_not_zero_percent(self):
        """「查不到」和「筹码分散」是两个相反的结论，绝不能混。"""
        self.assertIsNone(TOP10.top_holders_pct([], chain="bsc")["pct"])
        self.assertIsNone(TOP10.top_holders_pct(None, chain="bsc")["pct"])

    def test_holders_without_any_percentage_are_ignored(self):
        rows = [holder("bsc", 0.0), holder("bsc", 0.0)]
        self.assertIsNone(TOP10.top_holders_pct(rows, chain="bsc")["pct"])


class SyntheticGuardTest(unittest.TestCase):
    """包装 / 锚定 / 代币化股票的「Top10」是桥和托管方，不是这个币自己的筹码。"""

    def test_pegged_and_bridged_and_wrapped_are_rejected(self):
        for pricing_id, name in (
            ("binance-peg-zcash-token", "Binance-Peg Zcash"),
            ("omnibridge-bridged-zcash-solana", "Zcash (OmniBridge)"),
            ("binance-peg-near-protocol", "Binance-Peg NEAR"),
            ("wrapped-ether", "Wrapped Ether"),
        ):
            with self.subTest(pricing_id=pricing_id):
                self.assertTrue(TOP10.looks_synthetic(pricing_id, name))

    def test_tokenized_stocks_and_etfs_are_rejected(self):
        for pricing_id, name in (
            ("sandisk-corporation-robinhood-tokenized-stock", "SanDisk"),
            ("direxion-daily-semiconductor-bull-3x-etf-dinari-tokenized-etf", "SOXL"),
            ("strategy-pp-fixed-xstock", "STRK"),
            ("sandisk-backpack-securities", "SanDisk"),
        ):
            with self.subTest(pricing_id=pricing_id):
                self.assertTrue(TOP10.looks_synthetic(pricing_id, name))

    def test_normal_tokens_survive(self):
        for pricing_id, name in (
            ("doodles", "Doodles"),
            ("janction", "Janction"),
            ("iostoken", "IOST"),
            ("bitway", "Bitway"),
            ("starknet", "Starknet"),
        ):
            with self.subTest(pricing_id=pricing_id):
                self.assertFalse(TOP10.looks_synthetic(pricing_id, name))


def search_row(symbol, pricing_id, name, price, deployments):
    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "identifier": {"pricingID": pricing_id},
        "deployments": [{"chain": chain, "address": address} for chain, address in deployments],
    }


class PickSearchHitTest(unittest.TestCase):
    """symbol → 链上合约的三道闸。"""

    def test_the_exact_symbol_wins_over_a_lookalike(self):
        rows = [
            search_row("ghost", "ghost-inu", "Ghost Inu", 0.02, [("ethereum", "0x" + "11" * 20)]),
            search_row("iost", "iostoken", "IOST", 0.004, [("bsc", "0x" + "22" * 20)]),
        ]
        hit = TOP10.pick_search_hit(rows, "IOST")
        self.assertEqual(hit["pricingId"], "iostoken")
        self.assertEqual(hit["chain"], "bsc")

    def test_a_bridged_variant_is_skipped_even_when_it_is_listed_first(self):
        rows = [
            search_row("zec", "omnibridge-bridged-zcash-solana", "Zcash (OmniBridge)", 1282.0,
                       [("solana", "A7bdiYdS5GjqGFxQ2RBvSQ4p6yZGfLk4uV8fWQdU9YQz")]),
            search_row("zec", "zcash", "Zcash", 1282.0, [("zcash", "t1ZcashTransparentAddress")]),
        ]
        hit = TOP10.pick_search_hit(rows, "ZEC", price=1282.54)
        self.assertEqual(hit["pricingId"], "zcash")
        self.assertEqual(hit["chain"], "zcash")

    def test_a_price_mismatch_rejects_an_unrelated_same_ticker_token(self):
        """AIcoin 上的 CL 是原油，Arkham 搜出来的第一个 cl 是 CoinLancer。"""
        rows = [search_row("cl", "coinlancer", "CoinLancer", 0.0009, [("ethereum", "0x" + "33" * 20)])]
        self.assertEqual(TOP10.pick_search_hit(rows, "CL", price=71.4), {})

    def test_a_close_price_keeps_the_token(self):
        rows = [search_row("dood", "doodles", "Doodles", 0.003466, [("bsc", "0x" + "44" * 20)])]
        self.assertTrue(TOP10.pick_search_hit(rows, "DOOD", price=0.00315))

    def test_the_first_supported_deployment_is_used(self):
        rows = [search_row("jct", "janction", "Janction", 0.01,
                           [("ethereum", "0x" + "55" * 20), ("bsc", "0x" + "66" * 20)])]
        self.assertEqual(TOP10.pick_search_hit(rows, "JCT")["chain"], "ethereum")

    def test_the_primary_deployment_wins_even_when_another_chain_is_bigger(self):
        """多链部署一律取 Arkham 排第一的那条 —— 用户点开 arkm.com 看到的就是它。

        试过按监控池里的合约挑，实测更差：池子里的 STRK 是 Solana 桥接版，
        按它挑会把 ethereum 的 69.2% 算成 0.2%。
        """
        rows = [
            search_row("strk", "starknet", "Starknet", 0.0559,
                       [("ethereum", "0x" + "55" * 20), ("solana", "HsRpHQn6VbyMs5b5j5SV6xQ2VvpvvCCzu19GjytVSCoz")]),
        ]
        hit = TOP10.pick_search_hit(rows, "STRK", price=0.0559)
        self.assertEqual(hit["chain"], "ethereum")

    def test_a_bridged_token_is_rejected_even_when_it_comes_first(self):
        """包装版本身不许算（那 90%+ 是桥，不是这个币的筹码）。"""
        bridged = "A7bdiYdS5GjqGFxQ2RBvSQ4p6yZGfLk4uV8fWQdU9YQz"
        rows = [
            search_row("zec", "omnibridge-bridged-zcash-solana", "OmniBridge Bridged Zcash", 1288.0,
                       [("solana", bridged)]),
            search_row("zec", "zcash", "Zcash", 1288.0, [("zcash", "")]),
        ]
        self.assertEqual(TOP10.pick_search_hit(rows, "ZEC"), {})

    def test_a_deployment_whose_address_shape_is_wrong_is_skipped(self):
        rows = [search_row("jct", "janction", "Janction", 0.01, [("bsc", "not-an-address")])]
        self.assertEqual(TOP10.pick_search_hit(rows, "JCT"), {})

    def test_an_empty_query_returns_nothing(self):
        self.assertEqual(TOP10.pick_search_hit([], ""), {})


def holders_payload(pcts, chain="bsc", pricing_id="doodles"):
    return {
        "identity": {"name": "Doodles", "symbol": "DOOD", "pricingId": pricing_id},
        "holders": [holder(chain, pct) for pct in pcts],
        "chains": [chain],
        "reason": "",
    }


class DisabledByDefaultTest(unittest.TestCase):
    """没 enable 就不许联网 —— 跑一遍单测不该打 Arkham，弹窗也不该被拖住。"""

    def test_lookup_is_off_until_enabled(self):
        calls = []
        book = make_book(fetcher=lambda *a, **k: calls.append(a) or holders_payload([50.0]))
        result = book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(result["status"], "off")
        self.assertEqual(calls, [])

    def test_resolve_is_off_until_enabled(self):
        calls = []
        book = make_book(searcher=lambda *a, **k: calls.append(a) or [])
        self.assertEqual(book.resolve("DOOD"), {})
        self.assertEqual(calls, [])


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.book = make_book(fetcher=self.fetcher, budget=5.0)
        self.book.enable(True)

    def fetcher(self, chain, contract):
        self.calls.append((chain, contract))
        return self.answer(chain, contract)

    def answer(self, chain, contract):
        return holders_payload([30.0, 20.0, 10.0, 5.0], chain=chain)

    def test_a_successful_lookup_returns_the_percentage(self):
        result = self.book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["pct"], 65.0, places=2)
        self.assertEqual(result["count"], 4)

    def test_the_second_lookup_comes_from_cache(self):
        self.book.lookup("bsc", "0x" + "aa" * 20)
        self.book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(len(self.calls), 1)

    def test_the_contract_case_does_not_matter(self):
        self.book.lookup("bsc", "0x" + "AB" * 20)
        self.book.lookup("bsc", "0x" + "ab" * 20)
        self.assertEqual(len(self.calls), 1)

    def test_a_fetch_failure_is_reported_as_error_not_zero(self):
        def boom(chain, contract):
            raise ARKHAM.ArkhamError("Arkham 限流或服务异常（HTTP 429）", status=429)

        book = make_book(fetcher=boom, budget=5.0)
        book.enable(True)
        result = book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["pct"])
        self.assertIn("429", result["sources"]["arkham"]["reason"])

    def test_an_empty_holder_list_is_reported_as_empty(self):
        book = make_book(fetcher=lambda chain, contract: {"holders": [], "reason": "Arkham 没有该标的的持币人数据"}, budget=5.0)
        book.enable(True)
        self.assertEqual(book.lookup("bsc", "0x" + "aa" * 20)["status"], "empty")

    def test_a_slow_fetch_returns_pending_instead_of_blocking_the_popup(self):
        release = threading.Event()

        def slow(chain, contract):
            release.wait(3.0)
            return holders_payload([10.0], chain=chain)

        book = make_book(fetcher=slow, budget=0.05)
        book.enable(True)
        started = time.time()
        result = book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(result["status"], "pending")
        self.assertLess(time.time() - started, 1.5)
        release.set()

    def test_a_missing_contract_falls_back_to_the_symbol_search(self):
        searched = []

        def searcher(query):
            searched.append(query)
            return [search_row("dood", "doodles", "Doodles", 0.003466, [("bsc", "0x" + "44" * 20)])]

        book = make_book(fetcher=self.fetcher, searcher=searcher, budget=5.0)
        book.enable(True)
        result = book.lookup("", "", symbol="DOOD", price=0.00315)
        self.assertEqual(searched, ["DOOD"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["chain"], "bsc")
        self.assertEqual(result["pricingId"], "doodles")

    def test_an_unknown_symbol_is_unresolved_not_zero(self):
        book = make_book(fetcher=self.fetcher, searcher=lambda query: [], budget=5.0)
        book.enable(True)
        result = book.lookup("", "", symbol="NOPE")
        self.assertEqual(result["status"], "unresolved")
        self.assertIsNone(result["pct"])

    def test_a_failed_resolve_is_negative_cached(self):
        searched = []
        book = make_book(
            fetcher=self.fetcher,
            searcher=lambda query: searched.append(query) or [],
            budget=5.0,
        )
        book.enable(True)
        book.lookup("", "", symbol="NOPE")
        book.lookup("", "", symbol="NOPE")
        self.assertEqual(len(searched), 1)


class DeploymentChoiceTest(unittest.TestCase):
    """同一个 symbol 在几条链都有合约时，算哪条 —— 按 Arkham 前十名的美元合计挑。

    实测三例（2026-10-21），桥接版 / 小头部署的占比会虚高，只有「钱」不会骗人：

    * ``DOOD`` —— BSC 393 万 / Solana 3323 万 → Solana，71.2% / 71.2% 两源互证；
    * ``STRK`` —— ETH 4.02 亿 / Solana 119 万 → ETH，GMGN 在桥接版给的 65.4% 是假强势；
    * ``JCT``  —— ETH 1.85 亿 / BSC 2613 万 → ETH，11.2% 是 BSC 那条小头。
    """

    # 合法的 Solana base58（假地址会被 address_matches_chain 拦掉，部署表就只剩一条）。
    SOL_MINT = "HsRpHQn6VbyMs5b5j5SV6xQ2VvpvvCCzu19GjytVSCoz"

    def make(self, deployments, arkham_by_chain, gmgn_pct=None, price=0.01):
        """``arkham_by_chain`` = {chain: (pct, usd)}；``gmgn_pct`` = {chain: pct}。"""
        self.arkham_calls = []
        self.gmgn_calls = []
        gmgn_pct = gmgn_pct or {}

        def searcher(query):
            return [search_row("jct", "janction", "Janction", price, deployments)]

        def arkham(chain, contract):
            self.arkham_calls.append(chain)
            pct, usd = arkham_by_chain.get(chain, (None, 0))
            if pct is None:
                return {"holders": [], "reason": "Arkham 没有该标的的持币人数据"}
            return {"holders": [holder(chain, pct, usd=usd)], "reason": ""}

        def gmgn(chain, contract):
            self.gmgn_calls.append(chain)
            pct = gmgn_pct.get(chain)
            if pct is None:
                return {"status": "empty", "pct": None, "count": 0, "reason": "GMGN 不覆盖该链"}
            return {"status": "ok", "pct": pct, "count": 10, "reason": ""}

        book = make_book(fetcher=arkham, searcher=searcher, gmgn_fetcher=gmgn, budget=5.0)
        book.enable(True)
        return book

    def test_the_chain_where_the_money_is_wins(self):
        """DOOD：主部署 BSC 只有 393 万，Solana 有 3323 万 —— 算 Solana。"""
        book = self.make(
            [("bsc", "0x" + "11" * 20), ("solana", self.SOL_MINT)],
            {"bsc": (8.4, 3_933_422), "solana": (71.3, 33_233_943)},
            {"solana": 71.2},
        )
        result = book.lookup("", "", symbol="JCT")
        self.assertEqual(result["chain"], "solana")
        self.assertEqual(result["sources"]["arkham"]["pct"], 71.3)
        self.assertEqual(result["sources"]["gmgn"]["pct"], 71.2)

    def test_a_bridged_deployment_with_a_fat_percentage_is_not_chosen(self):
        """STRK：桥接版占比 65.4% 很唬人，但只有 119 万美元，真身在 ETH（4.02 亿）。"""
        book = self.make(
            [("ethereum", "0x" + "11" * 20), ("solana", self.SOL_MINT)],
            {"ethereum": (69.2, 401_709_239), "solana": (0.2, 1_193_868)},
            {"solana": 65.4},
        )
        result = book.lookup("", "", symbol="JCT")
        self.assertEqual(result["chain"], "ethereum")
        self.assertEqual(result["sources"]["arkham"]["pct"], 69.2)
        self.assertEqual(result["sources"]["gmgn"]["status"], "empty")

    def test_gmgn_is_only_called_for_the_chosen_chain(self):
        """GMGN 只起一次 node：两个部署并行探会撞 429 RATE。"""
        book = self.make(
            [("bsc", "0x" + "11" * 20), ("solana", self.SOL_MINT)],
            {"bsc": (8.4, 3_933_422), "solana": (71.3, 33_233_943)},
            {"solana": 71.2, "bsc": 12.0},
        )
        book.lookup("", "", symbol="JCT")
        self.assertEqual(self.gmgn_calls, ["solana"])
        self.assertEqual(sorted(self.arkham_calls), ["bsc", "solana"])

    def test_the_primary_deployment_is_the_fallback_when_nothing_is_fetchable(self):
        book = self.make(
            [("ethereum", "0x" + "11" * 20), ("solana", self.SOL_MINT)],
            {},
        )
        result = book.lookup("", "", symbol="JCT")
        self.assertEqual(result["chain"], "ethereum")
        self.assertEqual(result["sources"]["arkham"]["status"], "empty")

    def test_without_usd_the_richest_percentage_breaks_the_tie(self):
        """上游偶尔不给 usd（全 0），这时退回按占比挑。"""
        book = self.make(
            [("bsc", "0x" + "11" * 20), ("solana", self.SOL_MINT)],
            {"bsc": (11.2, 0), "solana": (79.3, 0)},
        )
        self.assertEqual(book.lookup("", "", symbol="JCT")["chain"], "solana")

    def test_a_single_deployment_is_never_probed(self):
        book = self.make([("bsc", "0x" + "11" * 20)], {"bsc": (55.0, 1_000_000)})
        result = book.lookup("", "", symbol="JCT")
        self.assertEqual(result["chain"], "bsc")
        self.assertEqual(self.arkham_calls, ["bsc"])  # 只有正式取数那一次

    def test_an_alert_that_already_carries_a_contract_is_not_probed(self):
        book = self.make(
            [("bsc", "0x" + "11" * 20), ("ethereum", "0x" + "22" * 20)],
            {"bsc": (55.0, 9_000_000), "ethereum": (12.0, 1_000)},
        )
        result = book.lookup("ethereum", "0x" + "22" * 20)
        self.assertEqual(result["chain"], "ethereum")
        self.assertEqual(self.arkham_calls, ["ethereum"])


def entry(arkham=None, gmgn=None):
    """按新形状（``sources`` 两个源）拼一条记录。"""
    sources = {
        "arkham": arkham if arkham is not None else {"status": "empty", "pct": None, "count": 0},
        "gmgn": gmgn if gmgn is not None else {"status": "empty", "pct": None, "count": 0},
    }
    return {"status": "ok", "sources": sources, "pct": sources["arkham"].get("pct")}


def ok(pct, count=10):
    return {"status": "ok", "pct": pct, "count": count, "reason": ""}


class BodyTextTest(unittest.TestCase):
    def test_both_sources_are_shown_side_by_side_with_their_names(self):
        """用户 2026-10-21：「两个版本都做一个统计」—— 两个数并排、各标各的源。"""
        text = TOP10.body_text(entry(ok(63.4), ok(58.1)))
        self.assertEqual(text, "Top10 持仓 Arkham 63.4% · GMGN 58.1%")

    def test_a_single_available_source_is_still_labelled(self):
        text = TOP10.body_text(entry(ok(63.4), {"status": "error", "pct": None, "count": 0}))
        self.assertEqual(text, "Top10 持仓 Arkham 63.4%")
        text = TOP10.body_text(entry({"status": "empty", "pct": None, "count": 0}, ok(41.2)))
        self.assertEqual(text, "Top10 持仓 GMGN 41.2%")

    def test_both_sources_above_the_line_add_the_risk_note(self):
        text = TOP10.body_text(entry(ok(78.2), ok(81.0)))
        self.assertIn("Arkham 78.2%", text)
        self.assertIn("GMGN 81.0%", text)
        self.assertIn("筹码高度集中（≥70%）", text)

    def test_only_one_source_above_the_line_names_that_source(self):
        """JCT 实测 11.2% vs 54.2%：只有一边超线时必须点名，否则读起来自相矛盾。"""
        text = TOP10.body_text(entry(ok(11.2), ok(78.4)))
        self.assertIn("筹码高度集中", text)
        self.assertIn("按 GMGN", text)
        text = TOP10.body_text(entry(ok(74.1), ok(52.0)))
        self.assertIn("按 Arkham", text)

    def test_sixty_nine_point_nine_is_still_below_the_line(self):
        self.assertNotIn("筹码高度集中", TOP10.body_text(entry(ok(69.9), ok(69.9))))

    def test_a_failure_never_reads_as_zero_percent(self):
        for status in ("error", "empty"):
            with self.subTest(status=status):
                text = TOP10.body_text(entry({"status": status, "pct": None, "count": 0},
                                             {"status": status, "pct": None, "count": 0}))
                self.assertNotIn("0.0%", text)
                self.assertTrue(text)

    def test_pending_and_off_say_nothing(self):
        for status in ("pending", "off", "unresolved"):
            with self.subTest(status=status):
                self.assertEqual(
                    TOP10.body_text(entry({"status": status, "pct": None, "count": 0},
                                          {"status": status, "pct": None, "count": 0})),
                    "",
                )
        self.assertEqual(TOP10.body_text(None), "")


class GmgnTopHoldersTest(unittest.TestCase):
    """GMGN 口径：``amount_percentage`` 是 0~1 的小数，前十行相加。"""

    def run_gmgn(self, stdout="", returncode=0, stderr="", **kwargs):
        completed = type("Completed", (), {
            "returncode": returncode, "stdout": stdout, "stderr": stderr,
        })()
        with patch.object(TOP10.subprocess, "run", return_value=completed), \
                patch.object(TOP10.os.path, "exists", return_value=True):
            return TOP10.gmgn_top_holders(kwargs.pop("chain", "bsc"),
                                          kwargs.pop("contract", "0x" + "aa" * 20))

    def test_the_top_ten_amount_percentages_are_summed(self):
        rows = [{"amount_percentage": value} for value in (
            0.1886, 0.1147, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01,
        )]
        result = self.run_gmgn(json.dumps({"list": rows}))
        self.assertEqual(result["count"], 10)
        self.assertAlmostEqual(result["pct"], 74.3, places=1)

    def test_an_empty_list_is_not_zero_percent(self):
        result = self.run_gmgn(json.dumps({"list": []}))
        self.assertIsNone(result["pct"])
        self.assertTrue(result["reason"])

    def test_a_failing_cli_is_reported_as_reason_not_zero(self):
        result = self.run_gmgn("", returncode=1, stderr="401 unauthorized")
        self.assertIsNone(result["pct"])
        self.assertIn("401", result["reason"])

    def test_a_chain_gmgn_does_not_cover_is_skipped_without_spawning(self):
        with patch.object(TOP10.subprocess, "run") as run:
            result = TOP10.gmgn_top_holders("arbitrum", "0x" + "aa" * 20)
        run.assert_not_called()
        self.assertIsNone(result["pct"])
        self.assertIn("不覆盖", result["reason"])

    def test_the_chain_is_folded_into_a_gmgn_slug(self):
        rows = [{"amount_percentage": 0.5}]
        with patch.object(TOP10.subprocess, "run") as run, \
                patch.object(TOP10.os.path, "exists", return_value=True):
            run.return_value = type("Completed", (), {
                "returncode": 0, "stdout": json.dumps({"list": rows}), "stderr": "",
            })()
            TOP10.gmgn_top_holders("ethereum", "0x" + "aa" * 20)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--chain") + 1], "eth")


class CombinedLookupTest(unittest.TestCase):
    """两个源并行抓，各自落缓存；一个失败不影响另一个显示。"""

    def make(self, arkham=None, gmgn=None):
        calls = []

        def arkham_fetcher(chain, contract):
            calls.append("arkham")
            if isinstance(arkham, Exception):
                raise arkham
            return arkham if arkham is not None else {"holders": [], "reason": "无数据"}

        def gmgn_fetcher(chain, contract):
            calls.append("gmgn")
            if isinstance(gmgn, Exception):
                raise gmgn
            return gmgn if gmgn is not None else {"pct": None, "count": 0, "reason": "无数据"}

        book = TOP10.Top10Holders(
            fetcher=arkham_fetcher, gmgn_fetcher=gmgn_fetcher, budget=5.0
        )
        book.enable(True)
        return book, calls

    def test_both_sources_land_in_one_record(self):
        book, calls = self.make(holders_payload([30.0, 20.0]), {"pct": 58.1, "count": 9})
        result = book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(sorted(calls), ["arkham", "gmgn"])
        self.assertEqual(result["sources"]["arkham"]["pct"], 50.0)
        self.assertEqual(result["sources"]["gmgn"]["pct"], 58.1)
        self.assertIn("Arkham 50.0%", TOP10.body_text(result))
        self.assertIn("GMGN 58.1%", TOP10.body_text(result))

    def test_one_failing_source_does_not_hide_the_other(self):
        book, _calls = self.make(
            ARKHAM.ArkhamError("Arkham 限流或服务异常（HTTP 429）", status=429),
            {"pct": 71.4, "count": 10},
        )
        result = book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(result["sources"]["arkham"]["status"], "error")
        self.assertEqual(result["sources"]["gmgn"]["status"], "ok")
        self.assertEqual(
            TOP10.body_text(result), "Top10 持仓 GMGN 71.4%（BSC） · 筹码高度集中（≥70%）"
        )

    def test_a_rate_limited_gmgn_call_is_an_error_not_empty(self):
        """429 是「不知道」，不是「这币没人持有」—— 弹窗上必须写「暂不可用」。"""
        book, _calls = self.make(
            holders_payload([30.0, 20.0]),
            {"status": "error", "pct": None, "count": 0, "reason": "GMGN 退出码 1：HTTP 429 RATE"},
        )
        result = book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(result["sources"]["gmgn"]["status"], "error")
        self.assertEqual(TOP10.body_text(result), "Top10 持仓 Arkham 50.0%（BSC）")

    def test_both_failing_is_reported_once_as_unavailable(self):
        book, _calls = self.make(
            ARKHAM.ArkhamError("boom", status=500),
            {"pct": None, "count": 0, "reason": "GMGN 查询超时"},
        )
        result = book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(TOP10.body_text(result), "Top10 持仓 暂不可用")

    def test_the_second_lookup_hits_the_cache_for_both_sources(self):
        book, calls = self.make(holders_payload([30.0]), {"pct": 40.0, "count": 10})
        book.lookup("bsc", "0x" + "aa" * 20)
        book.lookup("bsc", "0x" + "aa" * 20)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
