"""做市商监控板块里的「控盘达标标的」（用户 2026-10-21 追加）。

口径：热门榜 / 涨幅榜里满足这五条的标的也要进做市监控。
1. 项目方控盘 ≥90%  2. 必须 Alpha 合约  3. 流通市值 ≤3000 万美元
4. 拉盘后暴跌过的排除  5. 上涨过程中回踩支撑买入

**判定只有一处实现**（`alpha_mm_monitor.run_round()`），做市商监控这边只读它的状态文件
再排版。所以这里的用例全是「排版对不对」，尤其是**不能自己再判一次**——两边各判一次
早晚会分叉，用户就会看到「名册里有、弹窗不报」。

另一半是链接：控盘标的大半在 BSC / Base / Solana，而做市商地址只在 Ethereum /
Robinhood 上轮询。GMGN 链接必须按标的自己的链拼，不能被轮询范围卡住（曾经只有
CHAIN_META 的两条链能拼出 URL，BSC 的币点了是死链）。
"""

import threading
import unittest

import market_maker_monitor as mm


def watch_entry(**overrides):
    entry = {
        "at": 1_790_000_000,
        "name": "aPriori",
        "chain": "bsc",
        "contract": "0x299ad4299da5b2b93fba4c96967b040c7f611099",
        "network": "bsc",
        "pool": "0xPOOL",
        "cacheKey": "bsc:0x299a",
        "marketCap": 9_066_657.0,
        "controlPct": 93.1,
        "changePct": 28.59,
        "url": "https://www.binance.com/zh-CN/alpha/bsc/0x299a",
        "origin": "涨幅榜",
        "origins": ["涨幅榜"],
        "board": "Binance 合约涨幅榜",
        "drawdownPct": 18.0,
        "trendUp": True,
        "support": 0.0112,
        "supportLabel": "MA20",
        "inBuyZone": False,
        "broken": False,
        "ma20": 0.0112,
        "ma60": 0.0098,
        "price": 0.0125,
        "peak": 0.0152,
    }
    entry.update(overrides)
    return entry


class ControlTargetsTest(unittest.TestCase):
    def test_a_passing_symbol_keeps_every_field_the_card_needs(self):
        result = mm.control_targets({"watch": {"APR": watch_entry()}})
        self.assertEqual(result["summary"]["total"], 1)
        row = result["targets"][0]
        self.assertEqual(row["symbol"], "APR")
        self.assertEqual(row["chainLabel"], "BSC")
        self.assertEqual(row["controlText"], "93.1%")
        self.assertEqual(row["marketCapText"], "$9.07M")
        self.assertEqual(row["drawdownText"], "回撤 18%")
        self.assertEqual(row["status"], "wait")
        self.assertEqual(row["statusLabel"], "上涨结构·等回踩")
        # 现价 0.0125 / 支撑 0.0112 → 高 11.6%
        self.assertAlmostEqual(row["distancePct"], 11.61, places=2)

    def test_the_buy_zone_comes_first(self):
        """回踩到位 > 等回踩 > 结构走弱 > 破位；同一档里控盘高的在前。"""
        state = {
            "watch": {
                "WEAK": watch_entry(chain="base", trendUp=False, controlPct=99.0),
                "BUY": watch_entry(chain="solana", inBuyZone=True, controlPct=91.0),
                "WAIT": watch_entry(controlPct=95.0),
                "BROKEN": watch_entry(chain="ethereum", trendUp=False, broken=True, controlPct=98.0),
            }
        }
        rows = mm.control_targets(state)["targets"]
        self.assertEqual([row["symbol"] for row in rows], ["BUY", "WAIT", "WEAK", "BROKEN"])
        self.assertEqual(
            [row["statusLabel"] for row in rows],
            ["回踩支撑到位", "上涨结构·等回踩", "结构走弱", "支撑破位"],
        )

    def test_the_gmgn_link_follows_the_token_chain_not_the_polling_scope(self):
        """做市商地址只巡 Ethereum / Robinhood，但控盘标的可以在 BSC / Base / Solana。"""
        state = {
            "watch": {
                "APR": watch_entry(chain="bsc"),
                "CTR": watch_entry(chain="Base", contract="0x1103"),
                "DOOD": watch_entry(chain="solana", contract="DoODs1"),
                "ETH": watch_entry(chain="ethereum", contract="0xdead"),
            }
        }
        rows = {row["symbol"]: row for row in mm.control_targets(state)["targets"]}
        self.assertEqual(rows["APR"]["gmgnUrl"], "https://gmgn.ai/bsc/token/0x299ad4299da5b2b93fba4c96967b040c7f611099")
        self.assertEqual(rows["CTR"]["gmgnUrl"], "https://gmgn.ai/base/token/0x1103")
        self.assertEqual(rows["DOOD"]["gmgnUrl"], "https://gmgn.ai/sol/token/DoODs1")
        self.assertEqual(rows["ETH"]["gmgnUrl"], "https://gmgn.ai/eth/token/0xdead")

    def test_an_unknown_chain_gets_no_gmgn_link_at_all(self):
        """宁可留空也不猜 slug：GMGN 对任何路径都回 200，猜错就是打开空白页。"""
        state = {"watch": {"ARC": watch_entry(chain="arc", contract="0xabc")}}
        row = mm.control_targets(state)["targets"][0]
        self.assertEqual(row["gmgnUrl"], "")
        self.assertEqual(row["chainLabel"], "")

    def test_pumped_then_crashed_symbols_are_listed_as_excluded(self):
        """被回撤闸拦下的写墓碑；名册里不该出现，但要能看到「为什么排除」。"""
        state = {
            "watch": {"APR": watch_entry()},
            "blocked": {"JCT": {"at": 1_790_000_500, "reason": "历史高点回撤 78%（≥60%）", "drawdownPct": 78.0}},
        }
        result = mm.control_targets(state)
        self.assertEqual([row["symbol"] for row in result["targets"]], ["APR"])
        self.assertEqual(result["summary"]["excluded"], 1)
        excluded = result["excluded"][0]
        self.assertEqual(excluded["symbol"], "JCT")
        self.assertIn("回撤 78%", excluded["reason"])

    def test_the_thresholds_are_read_from_the_single_source(self):
        """阈值只认 `alpha_mm_monitor` 的常量，别在这里写死第二份。"""
        thresholds = mm.control_targets({})["summary"]["thresholds"]
        self.assertAlmostEqual(thresholds["minControlPct"], 90.0, places=2)
        self.assertAlmostEqual(thresholds["maxMarketCapUsd"], 30_000_000.0, places=2)
        self.assertAlmostEqual(thresholds["maxDrawdownPct"], 60.0, places=2)

    def test_the_hot_board_origin_is_counted(self):
        state = {"watch": {
            "APR": watch_entry(origins=["热门榜"], origin="热门榜"),
            "BMT": watch_entry(origins=["涨幅榜", "热门榜"], origin="涨幅榜 / 热门榜"),
            "CTR": watch_entry(origins=["涨幅榜"], origin="涨幅榜"),
        }}
        summary = mm.control_targets(state)["summary"]
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["hotOnly"], 2)

    def test_an_empty_state_is_an_empty_section_not_an_error(self):
        for state in ({}, {"watch": None, "blocked": "broken"}, {"watch": {"APR": "oops"}}):
            with self.subTest(state=state):
                result = mm.control_targets(state)
                self.assertEqual(result["targets"], [])
                self.assertEqual(result["excluded"], [])
                self.assertEqual(result["summary"]["total"], 0)

    def test_a_stale_entry_is_not_labelled_as_a_broken_structure(self):
        """旧版本写进去的 watch 记录没有日线字段：只能说「待下轮复核」，
        不能替用户判定「结构走弱」——那是一个没有数据支撑的结论。"""
        row = mm.control_targets({"watch": {"DOOD": watch_entry(
            price=0.0, support=0.0, supportLabel="", drawdownPct=0.0, trendUp=False,
        )}})["targets"][0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["statusLabel"], "待下轮复核")
        self.assertEqual(row["priceText"], "")
        self.assertEqual(row["supportText"], "")

    def test_the_payload_exposes_the_section(self):
        """`/api/market-maker` 的 payload 必须带上这一节，前端才有东西渲染。"""
        monitor = mm.MarketMakerMonitor.__new__(mm.MarketMakerMonitor)
        monitor.store = _StubStore()
        monitor.interval = 300.0
        monitor.addresses_per_cycle = 30
        monitor.large_usd = 1_000_000.0
        monitor.last_cycle_at = 0.0
        monitor.last_error = ""
        monitor._thread = None
        monitor._running = False
        # payload() 的 30 秒快照缓存（见 test_market_maker_api.PayloadSnapshotTests）：
        # 这里绕过 __init__，得把缓存字段补齐，否则取锁就炸。
        monitor._payload_lock = threading.Lock()
        monitor._payload_cache = None
        monitor._payload_cache_at = 0.0
        monitor._payload_rebuilding = False
        monitor.entry_filtered = 0
        monitor.entry_filter_note = ""
        # 三方共振那一节的字段（同样绕过 __init__，得手动补齐）。
        monitor.resonance_qualifier = None
        monitor.resonance_cooldown = float(mm.RESONANCE_COOLDOWN_SECONDS)
        monitor.resonance_blocked = 0
        monitor.resonance_note = ""
        original = mm.control_targets
        mm.control_targets = lambda *a, **k: original({"watch": {"APR": watch_entry()}})
        try:
            payload = monitor.payload()
        finally:
            mm.control_targets = original
        self.assertEqual([row["symbol"] for row in payload["controlTargets"]], ["APR"])
        self.assertEqual(payload["controlSummary"]["total"], 1)
        self.assertEqual(payload["summary"]["controlTargets"], 1)


class _StubStore:
    """payload() 要读库，这里只喂空表 —— 本节用例关心的是控盘那一节。"""

    alerts_enabled = staticmethod(lambda: True)

    def addresses(self):
        return []

    def positions(self, limit=0):
        return []

    def recent_transfers(self, limit=0, large_only_usd=0.0):
        return []

    def counts(self):
        return {}


if __name__ == "__main__":
    unittest.main()
