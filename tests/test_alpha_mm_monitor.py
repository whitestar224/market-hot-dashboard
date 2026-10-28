"""做市商控盘提醒（alpha_mm_monitor）的判据回归。

用户 2026-10-20 拍板的五条：
1. 项目方控盘 ≥90%  → 前 10 持币**剔除 DEX 池 / 销毁 / 交易所热钱包**后合计
2. 必须是 alpha 合约 → Alpha 收录 **且** 有永续（合约涨幅榜命中）
3. 流通市值 ≤3000 万美元
4. 历史高点回撤 ≥60% → 永久排除（墓碑，后续不再处理）
5. 上涨结构里回踩支撑 → 再弹一次买点

这里的持仓/日线全部用真机抓下来的形状（BMT 原始 top10=90.42%、DOOD 合计 107%），
所以这些用例同时是「剔除名单对不对」的护栏。
"""

import json
import os
import tempfile
import unittest

import alpha_mm_monitor as mm

BINANCE8 = "0xf977814e90da44bfa03b6295a0616a897441acec"


def holders(*rows):
    return [{"address": address, "percent": percent, "tag": tag} for address, percent, tag in rows]


def candles_uptrend(count=60, start=1.0, step=0.017):
    """单调上涨的日线：`(ts, o, h, l, c)`，收盘价从 start 一路抬到 ≈ start+step*(count-1)。"""
    rows = []
    price = start
    for index in range(count):
        rows.append((1_790_000_000 + index * 86400, price, price * 1.02, price * 0.99, price))
        price += step
    return rows


def gainers(rows_by_source):
    sources = []
    for source_id, rows in rows_by_source.items():
        sources.append({"id": source_id, "title": source_id, "rows": rows})
    return {"updatedAt": 0, "sources": sources}


def gainer_row(symbol, change, *, market_type=None):
    row = {"symbol": symbol, "name": f"{symbol}USDT", "change": change, "price": "$0.01",
           "amount": 1_000_000, "tags": [], "url": ""}
    if market_type:
        row["marketType"] = market_type
    return row


ALPHA_INDEX = {
    "updatedAt": 0,
    "tokens": {
        "APR": {"symbol": "APR", "name": "aPriori", "chain": "BSC",
                "contract": "0x299ad4299da5b2b93fba4c96967b040c7f611099", "marketCap": 9_066_657.0},
        "BMT": {"symbol": "BMT", "name": "Bubblemaps", "chain": "BSC",
                "contract": "0x7d814b9ed370ec0a502edc3267393bf62d891b62", "marketCap": 15_226_238.0},
        "CTR": {"symbol": "CTR", "name": "Citrea", "chain": "Base",
                "contract": "0x11030f79109269d796fd0fb956d6244e502757f7", "marketCap": 53_317_520.0},
        "RBH": {"symbol": "RBH", "name": "Robinhood Coin", "chain": "Robinhood",
                "contract": "0x385f4f8ae4aaabb2ea0d4b0d8f8cd4bd4b1c1a11", "marketCap": 12_000_000.0},
    },
}

PAIRS = {
    "0x299ad4299da5b2b93fba4c96967b040c7f611099": [
        {"pairAddress": "0xPOOLAPR00000000000000000000000000000001", "marketCap": 9_100_000,
         "liquidity": {"usd": 400_000}},
    ],
    "0x7d814b9ed370ec0a502edc3267393bf62d891b62": [
        {"pairAddress": "0xPOOLBMT00000000000000000000000000000002", "marketCap": 15_300_000,
         "liquidity": {"usd": 500_000}},
    ],
    "0x11030f79109269d796fd0fb956d6244e502757f7": [
        {"pairAddress": "0xPOOLCTR00000000000000000000000000000003", "marketCap": 53_000_000,
         "liquidity": {"usd": 600_000}},
    ],
}

APR_HOLDERS = holders(
    ("0xcf65caea1111111111111111111111111111111111", 0.3662, ""),
    ("0xddebc3ba2222222222222222222222222222222222", 0.2845, ""),
    ("0x73d8bd543333333333333333333333333333333333", 0.1263, ""),
    ("0x32826ecd4444444444444444444444444444444444", 0.0562, ""),
    ("0xd9900319555555555555555555555555555555555", 0.0552, ""),
    ("0x4982085c6666666666666666666666666666666666", 0.0109, ""),
    ("0x238a3588777777777777777777777777777777777", 0.0105, ""),
    ("0x53f78a0788888888888888888888888888888888", 0.0103, ""),
    ("0xf8824d2c99999999999999999999999999999999", 0.0069, ""),
    ("0x537f572eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 0.0042, ""),
)


def security_apr(chain, contract):
    return {"holders": APR_HOLDERS, "holderCount": 15402, "creatorPercent": 0.0, "source": "GoPlus"}


class HoldersControlTest(unittest.TestCase):
    def test_bmt_real_case_drops_cex_and_pool(self):
        """BMT 真机 top10=90.42%，剔掉币安8(17.17%)+币安热钱包(1.66%)+PancakeV3池(2.51%) 后不到 90%。"""
        rows = holders(
            ("0xeaed594b00000000000000000000000000000001", 0.4124, ""),
            (BINANCE8, 0.1717, ""),
            ("0x5a52e96b00000000000000000000000000000002", 0.1695, ""),
            ("0x44c9abcf00000000000000000000000000000003", 0.0366, ""),
            ("0x09f9789c00000000000000000000000000000004", 0.0251, "PancakeV3"),
            ("0x70d9b50700000000000000000000000000000005", 0.0231, ""),
            ("0x0d07079600000000000000000000000000000006", 0.0219, ""),
            ("0x30de7cd300000000000000000000000000000007", 0.0170, ""),
            ("0x8894e0a0c962cb723c1976a4421c95949be2d4e3", 0.0166, ""),
            ("0x53f78a0700000000000000000000000000000008", 0.0102, ""),
        )
        result = mm.holders_control(rows)
        self.assertAlmostEqual(result["controlPct"], 69.07, places=2)
        self.assertEqual(result["kept"], 7)
        reasons = " ".join(item["reason"] for item in result["dropped"])
        self.assertIn("币安托管钱包", reasons)
        self.assertIn("池子", reasons)

    def test_pool_address_from_dexscreener_is_dropped(self):
        rows = holders(
            ("0xpool0000000000000000000000000000000001", 0.60, ""),
            ("0xteam0000000000000000000000000000000002", 0.35, ""),
        )
        # 不传池子地址：池子那 60% 会被算进控盘
        self.assertAlmostEqual(
            mm.holders_control(rows)["controlPct"], 95.0, places=2,
        )
        result = mm.holders_control(rows, pool_addresses=["0xPOOL0000000000000000000000000000000001"])
        self.assertAlmostEqual(result["controlPct"], 35.0, places=2)
        self.assertEqual(result["dropped"][0]["reason"], "DEX 池")

    def test_sum_over_100_is_clamped(self):
        """DOOD 真机合计 107%：上游按流通量算的，展示必须封顶 100。"""
        rows = holders(*[(f"0x{i + 1:040x}", percent, "")
                         for i, percent in enumerate(
                             [0.6136, 0.2455, 0.0613, 0.0508, 0.0502, 0.0294, 0.0089, 0.0063, 0.0030, 0.0029])])
        self.assertEqual(mm.holders_control(rows)["controlPct"], 100.0)

    def test_burn_and_zero_are_dropped(self):
        rows = holders(
            ("0x0000000000000000000000000000000000000000", 0.50, ""),
            ("0x000000000000000000000000000000000000dEaD", 0.40, ""),
            ("0xabc0000000000000000000000000000000000001", 0.30, ""),
        )
        result = mm.holders_control(rows)
        self.assertAlmostEqual(result["controlPct"], 30.0, places=2)
        self.assertEqual(len(result["dropped"]), 2)


class GainersCandidateTest(unittest.TestCase):
    def test_same_symbol_keeps_biggest_gain_and_perp_flag(self):
        payload = gainers({
            "binance-futures-gainers": [gainer_row("JCT", "+119.47%", market_type="futures")],
            "bitget-gainers": [gainer_row("JCT", "+111.17%")],
            "binance-gainers": [gainer_row("GNS", "+33.33%")],
            "futu-hk-gainers": [gainer_row("00700", "+9.99%")],
        })
        candidates = {item["symbol"]: item for item in mm.gainers_candidates(payload)}
        self.assertEqual(set(candidates), {"JCT", "GNS"})
        self.assertAlmostEqual(candidates["JCT"]["changePct"], 119.47, places=2)
        self.assertTrue(candidates["JCT"]["perp"])
        # 只有合约榜算「有永续」；Bitget 涨幅榜是现货榜，不提供合约证据
        self.assertEqual(set(candidates["JCT"]["perpSources"]), {"binance-futures-gainers"})
        self.assertFalse(candidates["GNS"]["perp"])

    def test_okx_swap_board_counts_as_perp(self):
        payload = gainers({"okx-gainers": [gainer_row("APR", "+28.59%")]})
        candidate = mm.gainers_candidates(payload)[0]
        self.assertTrue(candidate["perp"])


class HotBoardCandidateTest(unittest.TestCase):
    """用户 2026-10-21：热门榜里满足五条的标的也要进做市监控。

    热门榜只放宽「谁被看见」，后面每一道闸（Alpha ∩ 永续 / 市值 / 控盘 / 回撤）都还在，
    所以这里只需要证明「抽取器认得热门榜、并且把出处带下去了」。
    """

    def test_only_crypto_boards_are_read(self):
        """成交额榜里混着港股/美股/A 股：它们的 symbol 是 00700 / AAPL，绝不能当候选。"""
        payload = gainers({
            "binance": [gainer_row("APR", "+8.5%")],
            "okx-turnover": [gainer_row("BMT", "+2.1%")],
            "futu-hk-turnover": [gainer_row("00700", "+1.2%")],
            "futu-us-turnover": [gainer_row("AAPL", "+0.8%")],
            "ths-cn": [gainer_row("600519", "+3.3%")],
        })
        symbols = {item["symbol"] for item in mm.hot_candidates(payload)}
        self.assertEqual(symbols, {"APR", "BMT"})

    def test_the_hot_board_does_not_claim_a_perp(self):
        """成交额榜是现货榜：命中它**不能**当成「有永续」，否则条件 2 会被自己糊过去。"""
        payload = gainers({"okx-turnover": [gainer_row("APR", "+8.5%")]})
        candidate = mm.hot_candidates(payload)[0]
        self.assertFalse(candidate["perp"])
        self.assertEqual(candidate["perpSources"], [])
        self.assertEqual(candidate["origin"], "热门榜")

    def test_same_symbol_on_both_boards_unions_the_origins(self):
        """同一个币既在涨幅榜又在热门榜：涨幅取大的那条，出处留成「涨幅榜 / 热门榜」。"""
        gainers_payload = gainers({"binance-futures-gainers": [gainer_row("APR", "+28.59%", market_type="futures")]})
        hot_payload = gainers({"binance": [gainer_row("APR", "+27.00%")]})
        merged = mm.merge_candidates(
            mm.gainers_candidates(gainers_payload), mm.hot_candidates(hot_payload)
        )
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0]["changePct"], 28.59, places=2)
        self.assertTrue(merged[0]["perp"])
        self.assertEqual(merged[0]["origins"], ["涨幅榜", "热门榜"])
        self.assertEqual(merged[0]["origin"], "涨幅榜 / 热门榜")

    def test_a_hot_only_symbol_can_be_seeded_and_says_where_it_came_from(self):
        """只在热门榜出现、主站涨幅榜没收录的 Alpha 币，够了五条就要入选。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "alpha_mm_state.json")
            alerts = []
            stats = mm.run_round(
                gainers_payload=gainers({}),
                hot_payload=gainers({"binance": [gainer_row("APR", "+3.20%")]}),
                alpha_index=ALPHA_INDEX,
                alert=lambda p: (alerts.append(p) or {"ok": True}),
                state_path=state_path,
                now=1_800_000_000,
                # 热门榜是现货榜，靠 perp_symbols 补「有永续」这一条
                perp_symbols={"APR"},
                fetchers={
                    "security": security_apr,
                    "pairs": lambda network, contract: PAIRS.get(contract, []),
                    "candles": lambda network, pool: [
                        [int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                        for item in reversed(candles_uptrend())
                    ],
                },
            )
            self.assertEqual(stats["seeded"], 1, stats["skipped"])
            self.assertEqual(len(alerts), 1)
            self.assertIn("出自热门榜", alerts[0]["body"])
            with open(state_path, encoding="utf-8") as handle:
                watch = json.load(handle)["watch"]
            self.assertEqual(watch["APR"]["origins"], ["热门榜"])
            # 展示字段一并落库：做市监控板块靠这些渲染状态与支撑位
            self.assertTrue(watch["APR"]["trendUp"])
            self.assertGreater(watch["APR"]["price"], 0)
            self.assertGreater(watch["APR"]["support"], 0)


class CandleMetricsTest(unittest.TestCase):
    def test_drawdown_and_trend(self):
        candles = candles_uptrend()
        metrics = mm.candle_metrics(candles)
        self.assertTrue(metrics["trendUp"])
        self.assertLess(metrics["drawdownPct"], 5)
        self.assertGreater(metrics["ma20"], metrics["ma60"])

    def test_pumped_then_crashed_is_detected(self):
        """先拉到 3.0 再砸到 1.0：回撤 67%，必须被排除（阈值 60%）。"""
        rows = candles_uptrend(count=40, start=0.5, step=0.0625)[:39]
        rows.append((1_790_000_000 + 40 * 86400, 3.0, 3.0, 1.0, 1.0))
        metrics = mm.candle_metrics(rows)
        self.assertGreaterEqual(metrics["drawdownPct"], 60)
        self.assertFalse(metrics["trendUp"])

    def test_support_zone(self):
        candles = candles_uptrend()
        metrics = mm.candle_metrics(candles)
        ma20 = metrics["ma20"]
        # 收盘压回 MA20 上方 1%：回踩到位
        trimmed = candles[:-1] + [(candles[-1][0] + 86400, ma20, ma20 * 1.01, ma20 * 0.995, ma20 * 1.01)]
        near = mm.candle_metrics(trimmed)
        state = mm.support_state(near)
        self.assertTrue(state["inBuyZone"], state)
        self.assertLessEqual(state["distancePct"], mm.MM_SUPPORT_ZONE_PCT)

    def test_below_all_support_is_broken(self):
        candles = candles_uptrend()
        metrics = mm.candle_metrics(candles)
        ma20 = metrics["ma20"]
        crashed = candles[:-1] + [(candles[-1][0] + 86400, ma20 * 0.9, ma20 * 0.9, ma20 * 0.88, ma20 * 0.9)]
        state = mm.support_state(mm.candle_metrics(crashed))
        self.assertTrue(state["broken"])
        self.assertFalse(state["inBuyZone"])


def make_round(tmpdir, *, gainers_payload, security=None, pairs=None, candles=None):
    state_path = os.path.join(tmpdir, "alpha_mm_state.json")
    alerts = []

    def alert(payload):
        alerts.append(payload)
        return {"ok": True}

    def run(now):
        stats = mm.run_round(
            gainers_payload=gainers_payload,
            alpha_index=ALPHA_INDEX,
            alert=alert,
            state_path=state_path,
            now=now,
            fetchers={
                "security": security or security_apr,
                "pairs": pairs or (lambda network, contract: PAIRS.get(contract, [])),
                "candles": candles or (lambda network, pool: [
                    [int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                    for item in reversed(candles_uptrend())
                ]),
            },
        )
        return stats

    return run, alerts, state_path


class RunRoundTest(unittest.TestCase):
    def test_seed_then_support_buy_point(self):
        os.environ.pop("XINGYUN_ALPHA_MM_MIN_CONTROL_PCT", None)
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({
                "binance-futures-gainers": [gainer_row("APR", "+28.59%", market_type="futures")],
            })
            candles = candles_uptrend()
            candle_rows = [[int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                           for item in reversed(candles)]
            run, alerts, state_path = make_round(
                tmpdir, gainers_payload=payload,
                candles=lambda network, pool: candle_rows,
            )
            stats = run(now=1_800_000_000)
            self.assertEqual(stats["seeded"], 1)
            self.assertEqual(len(alerts), 1)
            seed = alerts[0]
            self.assertEqual(seed["key"], "alpha-mm-seed:APR")
            self.assertEqual(seed["kind"], "做市商控盘·入选")
            self.assertIn("控盘 93.1%", seed["body"])
            self.assertIn("流通市值 907 万美元", seed["body"])
            self.assertEqual(seed["chain"], "BSC")

            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            self.assertIn("APR", state["watch"])

            # 第二轮：价格回踩 MA20 上方 1% → 弹买点（metrics 缓存 30 分钟，故意跨过去）
            ma20 = mm.candle_metrics(candles)["ma20"]
            retest = candles[:-1] + [(candles[-1][0] + 86400, ma20, ma20 * 1.01, ma20 * 0.995, ma20 * 1.01)]
            rebound_rows = [[int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                            for item in reversed(retest)]
            run2, alerts2, _ = make_round(
                tmpdir, gainers_payload={"updatedAt": 0, "sources": []},
                candles=lambda network, pool: rebound_rows,
            )
            stats2 = run2(now=1_800_000_000 + 3600)
            self.assertEqual(stats2["buyPoints"], 1)
            self.assertEqual(alerts2[0]["key"].startswith("alpha-mm-buy:APR:"), True)
            self.assertEqual(alerts2[0]["kind"], "做市商控盘·支撑买点")

    def test_big_market_cap_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({"okx-gainers": [gainer_row("CTR", "+40%")]})
            run, alerts, _ = make_round(tmpdir, gainers_payload=payload)
            stats = run(now=1_800_000_000)
            self.assertEqual(alerts, [])
            self.assertEqual(stats["seeded"], 0)
            self.assertTrue(any("流通市值" in item for item in stats["skipped"]), stats["skipped"])

    def test_pumped_then_crashed_is_blocked_forever(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({
                "binance-futures-gainers": [gainer_row("APR", "+60%", market_type="futures")],
            })
            crashed = candles_uptrend(count=40) + [
                (1_790_000_000 + 40 * 86400, 3.0, 3.2, 1.0, 1.0),
            ]
            candle_rows = [[int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                           for item in reversed(crashed)]
            run, alerts, state_path = make_round(
                tmpdir, gainers_payload=payload, candles=lambda network, pool: candle_rows,
            )
            stats = run(now=1_800_000_000)
            self.assertEqual(alerts, [])
            self.assertEqual(stats["blocked"], 1)
            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            self.assertIn("APR", state["blocked"])

            # 墓碑生效：下一轮即使形态变好也不再处理
            run2, alerts2, _ = make_round(tmpdir, gainers_payload=payload)
            stats2 = run2(now=1_800_000_000 + 600)
            self.assertEqual(alerts2, [])
            self.assertEqual(stats2["blocked"], 1)

    def test_spot_only_symbol_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({"binance-gainers": [gainer_row("APR", "+20%")]})
            run, alerts, _ = make_round(tmpdir, gainers_payload=payload)
            stats = run(now=1_800_000_000)
            self.assertEqual(alerts, [])
            self.assertTrue(any("永续" in item for item in stats["skipped"]), stats["skipped"])

    def test_unsupported_chain_is_not_treated_as_zero_control(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({"okx-gainers": [gainer_row("RBH", "+50%")]})
            run, alerts, _ = make_round(
                tmpdir, gainers_payload=payload,
                security=lambda chain, contract: {"unsupported": True, "reason": "GoPlus 不支持 Robinhood"},
            )
            stats = run(now=1_800_000_000)
            self.assertEqual(alerts, [])
            self.assertTrue(any("未核验" in item for item in stats["skipped"]), stats["skipped"])

    def test_the_seed_popup_links_to_the_token_trade_page(self):
        """「查看」要直达这个币的 Alpha 交易页（用户 2026-10-21 给的 JCT 链接形态）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({
                "binance-futures-gainers": [gainer_row("APR", "+28.59%", market_type="futures")],
            })
            run, alerts, _ = make_round(tmpdir, gainers_payload=payload)
            run(now=1_800_000_000)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(
            alerts[0]["url"],
            "https://www.binance.com/zh-CN/alpha/bsc/0x299ad4299da5b2b93fba4c96967b040c7f611099",
        )

    def test_deferred_candidate_reports_one_reason(self):
        """配额用尽时同一币只能有一条原因（曾同时出现「配额留到下一轮」与「控盘度未核验」）。"""
        prev_budget = os.environ.get("XINGYUN_ALPHA_MM_CONTROL_PER_ROUND")
        prev_key = os.environ.pop("GOPLUS_API_KEY", None)
        os.environ["XINGYUN_ALPHA_MM_CONTROL_PER_ROUND"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                payload = gainers({
                    "binance-futures-gainers": [
                        gainer_row("APR", "+30%", market_type="futures"),
                        gainer_row("BMT", "+22%", market_type="futures"),
                    ],
                })
                run, alerts, _ = make_round(tmpdir, gainers_payload=payload)
                stats = run(now=1_800_000_000)
        finally:
            os.environ.pop("XINGYUN_ALPHA_MM_CONTROL_PER_ROUND", None)
            if prev_budget is not None:
                os.environ["XINGYUN_ALPHA_MM_CONTROL_PER_ROUND"] = prev_budget
            if prev_key is not None:
                os.environ["GOPLUS_API_KEY"] = prev_key
        bmt = [item for item in stats["skipped"] if item.startswith("BMT:")]
        self.assertEqual(len(bmt), 1, stats["skipped"])
        self.assertIn("配额留到下一轮", bmt[0])

    def test_non_alpha_symbol_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({
                "binance-futures-gainers": [gainer_row("ZZZ", "+99%", market_type="futures")],
            })
            run, alerts, _ = make_round(tmpdir, gainers_payload=payload)
            stats = run(now=1_800_000_000)
            self.assertEqual(alerts, [])
            self.assertEqual(stats["alpha"], 0)

    def test_low_control_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({
                "binance-futures-gainers": [gainer_row("APR", "+30%", market_type="futures")],
            })
            loose = [{"address": f"0x{i + 1:040x}", "percent": 0.03, "tag": ""} for i in range(10)]
            run, alerts, _ = make_round(
                tmpdir, gainers_payload=payload,
                security=lambda chain, contract: {"holders": loose, "holderCount": 5000},
            )
            stats = run(now=1_800_000_000)
            self.assertEqual(alerts, [])
            self.assertTrue(any("控盘 30.0%" in item for item in stats["skipped"]), stats["skipped"])


class AlphaBoardCandidateTest(unittest.TestCase):
    def test_alpha_own_gainers_become_candidates(self):
        """Alpha 板块自己的 24h 涨幅（≥20%）也算「涨幅榜」，主站榜没收录也不能漏。"""
        index = {"tokens": {
            "APR": {"symbol": "APR", "name": "aPriori", "chain": "BSC", "chainName": "BSC",
                    "contract": "0x299a", "marketCap": 9_000_000, "change24h": 26.85},
            "CTR": {"symbol": "CTR", "name": "Citrea", "chain": "Base",
                    "contract": "0x1103", "marketCap": 53_000_000, "change24h": 3.0},
        }}
        candidates = mm.alpha_gainers_candidates(index, min_change_pct=20)
        self.assertEqual([item["symbol"] for item in candidates], ["APR"])
        self.assertEqual(candidates[0]["sourceId"], "alpha-list")

    def test_merge_keeps_biggest_gain_and_perp_union(self):
        board = [{"symbol": "APR", "name": "APR", "changePct": 12.0, "chain": "BSC",
                  "contract": "", "sourceId": "binance-gainers", "board": "Binance 涨幅榜",
                  "perp": False, "perpSources": []}]
        alpha = [{"symbol": "APR", "name": "aPriori", "changePct": 26.85, "chain": "BSC",
                  "contract": "0x299a", "sourceId": "alpha-list", "board": "Binance Alpha 24h 涨幅",
                  "perp": False, "perpSources": []}]
        merged = mm.merge_candidates(board, alpha)
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0]["changePct"], 26.85, places=2)
        self.assertEqual(merged[0]["contract"], "0x299a")

    def test_perp_list_rescues_a_symbol_missing_from_the_futures_board(self):
        """涨幅榜只收 10 行：币不在合约榜里，但 exchangeInfo 证明它有永续 → 条件 2 成立。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({"alpha-list": []})  # 主站榜空
            run_alerts = []
            stats = mm.run_round(
                gainers_payload=payload,
                alpha_index={"tokens": {
                    "APR": {"symbol": "APR", "name": "aPriori", "chain": "BSC",
                            "contract": "0x299ad4299da5b2b93fba4c96967b040c7f611099",
                            "marketCap": 9_066_657.0, "change24h": 26.85},
                }},
                alert=lambda p: (run_alerts.append(p) or {"ok": True}),
                state_path=os.path.join(tmpdir, "s.json"),
                now=1_800_000_000,
                perp_symbols={"APR"},
                fetchers={
                    "security": security_apr,
                    "pairs": lambda network, contract: PAIRS.get(contract, []),
                    "candles": lambda network, pool: [
                        [int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                        for item in reversed(candles_uptrend())
                    ],
                },
            )
            self.assertEqual(stats["perp"], 1)
            self.assertEqual(stats["seeded"], 1)
            self.assertEqual(len(run_alerts), 1)

    def test_without_perp_list_the_same_symbol_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = mm.run_round(
                gainers_payload=gainers({"alpha-list": []}),
                alpha_index={"tokens": {
                    "APR": {"symbol": "APR", "name": "aPriori", "chain": "BSC",
                            "contract": "0x299ad4299da5b2b93fba4c96967b040c7f611099",
                            "marketCap": 9_066_657.0, "change24h": 26.85},
                }},
                alert=lambda p: {"ok": True},
                state_path=os.path.join(tmpdir, "s.json"),
                now=1_800_000_000,
                fetchers={"security": security_apr, "pairs": lambda n, c: [], "candles": lambda n, p: []},
            )
            self.assertEqual(stats["perp"], 0)
            self.assertTrue(any("永续" in item for item in stats["skipped"]), stats["skipped"])


class AlphaStatusTest(unittest.TestCase):
    """用户 2026-10-20：曾经上过 Alpha、后来转现货被踢出的（BMT 这种）也要纳入。"""

    def test_status_labels(self):
        self.assertEqual(mm.alpha_status_label({"offline": False}), "Alpha 在列")
        self.assertEqual(mm.alpha_status_label({"offline": True, "listingCex": True}),
                         "Alpha 已下架 · 已转币安现货")
        self.assertEqual(mm.alpha_status_label({"offline": True, "listingCex": False}), "Alpha 已下架")
        self.assertEqual(mm.alpha_status_label({}), "")

    def test_offline_token_is_still_a_candidate_from_the_gainers_board(self):
        index = {"tokens": {"BMT": {
            "symbol": "BMT", "name": "Bubblemaps", "chain": "BSC",
            "contract": "0x7d814b9ed370ec0a502edc3267393bf62d891b62",
            "marketCap": 16_165_330.0, "change24h": 22.76,
            "offline": True, "listingCex": True, "fullyDelisted": True,
        }}}
        with tempfile.TemporaryDirectory() as tmpdir:
            alerts = []
            stats = mm.run_round(
                gainers_payload=gainers({"binance-gainers": [gainer_row("BMT", "+22.76%")]}),
                alpha_index=index,
                alert=lambda p: (alerts.append(p) or {"ok": True}),
                state_path=os.path.join(tmpdir, "s.json"),
                now=1_800_000_000,
                perp_symbols={"BMT"},
                fetchers={
                    "security": security_apr,
                    "pairs": lambda network, contract: PAIRS.get(contract, []),
                    "candles": lambda network, pool: [
                        [int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                        for item in reversed(candles_uptrend())
                    ],
                },
            )
            self.assertEqual(stats["alpha"], 1)
            self.assertEqual(stats["alphaOffline"], 1)
            self.assertEqual(stats["seeded"], 1)
            self.assertIn("Alpha 已下架 · 已转币安现货", alerts[0]["body"])
            self.assertEqual(alerts[0]["alphaPhase"], "Alpha 已下架 · 已转币安现货")

    def test_offline_token_is_not_in_the_alpha_board_source(self):
        """已下架的不该出现在「Alpha 24h 涨幅」这一路（Alpha 板块里根本看不到它）。"""
        index = {"tokens": {
            "BMT": {"symbol": "BMT", "chain": "BSC", "offline": True, "change24h": 60.0},
            "APR": {"symbol": "APR", "chain": "BSC", "offline": False, "change24h": 26.0},
        }}
        candidates = mm.alpha_gainers_candidates(index, min_change_pct=20)
        self.assertEqual([item["symbol"] for item in candidates], ["APR"])


class BatchSecurityTest(unittest.TestCase):
    def test_one_request_per_chain_for_many_candidates(self):
        """同一条链上的多个候选必须合并成一次 GoPlus 请求（免费额度按 IP 计）。"""
        calls = []

        def batch(chain, contracts):
            calls.append((chain, list(contracts)))
            return {str(c).casefold(): {"holders": APR_HOLDERS, "holderCount": 15402}
                    for c in contracts}

        with tempfile.TemporaryDirectory() as tmpdir:
            payload = gainers({
                "binance-futures-gainers": [
                    gainer_row("APR", "+28%", market_type="futures"),
                    gainer_row("BMT", "+21%", market_type="futures"),
                ],
            })
            state_path = os.path.join(tmpdir, "state.json")
            alerts = []
            stats = mm.run_round(
                gainers_payload=payload,
                alpha_index=ALPHA_INDEX,
                alert=lambda p: (alerts.append(p) or {"ok": True}),
                state_path=state_path,
                now=1_800_000_000,
                fetchers={
                    "securityBatch": batch,
                    "pairs": lambda network, contract: PAIRS.get(contract, []),
                    "candles": lambda network, pool: [
                        [int(item[0] * 1000), item[1], item[2], item[3], item[4]]
                        for item in reversed(candles_uptrend())
                    ],
                },
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(sorted(calls[0][1]),
                             ["0x299ad4299da5b2b93fba4c96967b040c7f611099",
                              "0x7d814b9ed370ec0a502edc3267393bf62d891b62"])
            self.assertEqual(stats["seeded"], 2)

    def test_error_cache_short_circuits_next_round(self):
        self.assertTrue(mm.control_cache_settled({"error": "GoPlus 限流", "at": 100.0}, 100.0 + 60))
        self.assertFalse(mm.control_cache_settled({"error": "GoPlus 限流", "at": 100.0},
                                                 100.0 + mm.MM_HOLDER_ERROR_TTL_SECONDS + 1))
        self.assertTrue(mm.control_cache_settled({"controlPct": 93.0, "at": 100.0}, 100.0 + 600))


class ExcludedHolderTest(unittest.TestCase):
    def test_reasons(self):
        self.assertEqual(mm.excluded_holder(BINANCE8), "币安托管钱包")
        self.assertEqual(mm.excluded_holder("0x000000000000000000000000000000000000dEaD"), "销毁地址")
        self.assertEqual(mm.excluded_holder("0xpool", "PancakeV3"), "池子(PancakeV3)")
        self.assertEqual(mm.excluded_holder("0xTeam", "", ["0xpool"]), "")
        self.assertEqual(mm.excluded_holder("0xPOOL", "", ["0xpool"]), "DEX 池")

    def test_goplus_chain_mapping(self):
        self.assertEqual(mm.goplus_chain("BSC"), ("56", ""))
        self.assertEqual(mm.goplus_chain("Base"), ("8453", ""))
        self.assertEqual(mm.goplus_chain("Solana"), ("501", "solana"))
        self.assertEqual(mm.goplus_chain("Robinhood"), ("", ""))
        self.assertEqual(mm.network_name("BSC"), "bsc")
        self.assertEqual(mm.network_name("Robinhood"), "robinhood")

    def test_bare_chain_ids_are_accepted(self):
        """裸链号也要认：手工探针/上游事件经常只给 "56"、"CT_501"。

        曾经只认 ``bsc``/``bnb`` 短名，于是 ``fetch_token_security("56", …)``
        直接返回「GoPlus 不支持 56 的持仓核验」——控盘度被读成「不支持」，
        比读成 0% 好，但仍然是把能核验的币白扔了。
        """
        self.assertEqual(mm.goplus_chain("56"), ("56", ""))
        self.assertEqual(mm.goplus_chain("1"), ("1", ""))
        self.assertEqual(mm.goplus_chain("8453"), ("8453", ""))
        self.assertEqual(mm.goplus_chain("CT_501"), ("501", "solana"))
        self.assertEqual(mm.goplus_chain(56), ("56", ""))


class AlphaTokenUrlTest(unittest.TestCase):
    """「查看」按钮的落点（用户 2026-10-21 给的 JCT 链接）。

    ``https://www.binance.com/zh-CN/alpha/bsc/0xea37a8de…`` 是币安 Alpha 的代币交易页。
    slug 只收实测确认过的链：币安前端是 SPA，``bsc`` / ``56`` / ``bnb`` 三种 slug 抓下来
    都是同一个 202 空壳（字节数一模一样），状态码判不出对错——猜错就是打开空白页。
    """

    def test_bsc_contract_gets_the_token_trade_page(self):
        self.assertEqual(
            mm.binance_alpha_token_url("BSC", "0xea37a8de1de2d9d10772eeb569e28bfa5cb17707"),
            "https://www.binance.com/zh-CN/alpha/bsc/"
            "0xea37a8de1de2d9d10772eeb569e28bfa5cb17707",
        )
        # 裸链号与内部代号都要能归一
        self.assertEqual(
            mm.binance_alpha_token_url("56", "0xabc"),
            "https://www.binance.com/zh-CN/alpha/bsc/0xabc",
        )

    def test_an_unverified_chain_slug_falls_back_to_the_alpha_home(self):
        for chain in ("Base", "Solana", "ETH", "Robinhood", "", None):
            with self.subTest(chain=chain):
                self.assertEqual(
                    mm.binance_alpha_token_url(chain, "0xabc"), mm.MM_ALPHA_HOME_URL
                )
        self.assertEqual(mm.binance_alpha_token_url("BSC", ""), mm.MM_ALPHA_HOME_URL)


if __name__ == "__main__":
    unittest.main()
