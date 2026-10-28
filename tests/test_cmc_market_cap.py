"""弹窗里的 CoinMarketCap 流通市值（用户 2026-10-24）。

数据源实测（2026-10-24）：CMC 网页端 data-api 的 ``listing`` 端点免 key、一次
5000 行带 ``marketCap``；``search`` 系全 404、按 address 查被拒 —— 所以模块是
「整榜快照 + 内存倒排 + 后台刷新」。这里用注入的假 fetcher，不碰真网。

两条铁律和 Top10 同源：

* **symbol 撞车选 cmcRank 最小**（真 XRP rank 5 市值 810 亿，压过 rank 2770
  市值 18 万的山寨 XRP）；
* **拿不到就沉默** —— 绝不把「查不到」写成 $0。
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import cmc_market_cap
import server


def _listing_row(*, symbol: str, name: str, rank: int, mcap: float,
                 slug: str | None = None, active: int = 1, price: float = 1.0,
                 circ: float = 1000.0, total: float = 0.0, max_supply: float = 0.0,
                 fdv: float = 0.0) -> dict:
    return {
        "id": rank * 7 + 1,
        "name": name,
        "symbol": symbol,
        "slug": slug or name.lower().replace(" ", "-"),
        "cmcRank": rank,
        "isActive": active,
        "circulatingSupply": circ,
        "totalSupply": total,
        "maxSupply": max_supply,
        # CMC 原文就是三 t 的 fullyDilluttedMarketCap。
        "quotes": [{"marketCap": mcap, "price": price, "fullyDilluttedMarketCap": fdv}],
    }


def _payload(rows: list[dict]) -> dict:
    return {"data": {"cryptoCurrencyList": rows}}


class SnapshotBuildTests(unittest.TestCase):
    def test_duplicate_symbols_pick_the_lowest_ranked_row(self):
        rows = [
            _listing_row(symbol="XRP", name="Fake XRP", rank=2770, mcap=183_819.97),
            _listing_row(symbol="XRP", name="XRP", rank=5, mcap=81_080_711_424.45),
            _listing_row(symbol="XRP", name="Another Fake", rank=1200, mcap=5_000.0),
        ]
        snapshot = cmc_market_cap.CmcMarketCap._build_snapshot(rows)
        entry = snapshot.get("xrp")
        self.assertEqual(entry["rank"], 5)
        self.assertEqual(entry["mcapUsd"], 81_080_711_424.45)

    def test_zero_mcap_and_inactive_rows_are_dropped(self):
        rows = [
            _listing_row(symbol="DEAD", name="Dead", rank=10, mcap=0.0),
            _listing_row(symbol="GHOST", name="Ghost", rank=11, mcap=5.0, active=0),
        ]
        snapshot = cmc_market_cap.CmcMarketCap._build_snapshot(rows)
        self.assertEqual(snapshot, {})


class LookupTests(unittest.TestCase):
    def _store(self, rows, **kwargs) -> cmc_market_cap.CmcMarketCap:
        store = cmc_market_cap.CmcMarketCap(
            fetcher=lambda url: _payload(rows),
            snapshot_path=os.path.join(tempfile.mkdtemp(), "cmc_test.json"),
            **kwargs,
        )
        store.enable(True)
        # 新语义：弹窗路径绝不同步拉整榜，测试里手动刷一次当「后台线程已完成」。
        store.refresh()
        return store

    def test_ok_lookup_returns_mcap_and_rank(self):
        store = self._store([_listing_row(symbol="JCT", name="JCT", rank=437, mcap=39_758_882.7)])
        entry = store.lookup("JCT")
        self.assertEqual(entry["status"], "ok")
        self.assertAlmostEqual(entry["mcapUsd"], 39_758_882.7)
        self.assertEqual(entry["rank"], 437)

    def test_unlisted_and_empty_symbol(self):
        store = self._store([_listing_row(symbol="XRP", name="XRP", rank=5, mcap=1.0)])
        self.assertEqual(store.lookup("NOPE")["status"], "unlisted")
        self.assertEqual(store.lookup("")["status"], "no-symbol")

    def test_disabled_store_never_hits_the_network(self):
        store = cmc_market_cap.CmcMarketCap(
            fetcher=lambda url: (_ for _ in ()).throw(AssertionError("network")),
            snapshot_path=os.path.join(tempfile.mkdtemp(), "cmc_test.json"),
        )
        self.assertEqual(store.lookup("XRP")["status"], "disabled")

    def test_empty_snapshot_returns_fast_and_never_blocks_the_alert_path(self):
        # 2026-10-24 实测整榜下载+解析 ~54 秒 —— 弹窗路径绝不允许等它。
        # 快照还是空时 lookup 必须立即返回 empty（异步 kick 由 start()/lookup 触发）。
        def slow_fetcher(url: str) -> dict:
            raise RuntimeError("boom")

        store = cmc_market_cap.CmcMarketCap(
            fetcher=slow_fetcher,
            snapshot_path=os.path.join(tempfile.mkdtemp(), "cmc_test.json"),
        )
        store.enable(True)
        self.assertEqual(store.lookup("XRP")["status"], "empty")
        # 刷出快照后立刻可用。
        store._fetcher = lambda url: _payload([_listing_row(symbol="XRP", name="XRP", rank=5, mcap=2.0)])
        store.refresh()
        self.assertEqual(store.lookup("XRP")["status"], "ok")

    def test_snapshot_survives_restart_via_disk(self):
        rows = [_listing_row(symbol="PEPE", name="Pepe", rank=44, mcap=1_825_046_267.7)]
        path = os.path.join(tempfile.mkdtemp(), "cmc_test.json")
        warm = cmc_market_cap.CmcMarketCap(fetcher=lambda url: _payload(rows), snapshot_path=path)
        warm.enable(True)
        warm.refresh()
        self.assertEqual(warm.lookup("pepe")["status"], "ok")
        # 新实例（模拟重启）：不再联网，从盘上读。
        cold = cmc_market_cap.CmcMarketCap(
            fetcher=lambda url: (_ for _ in ()).throw(AssertionError("network")),
            snapshot_path=path,
        )
        cold.enable(True)
        self.assertEqual(cold.lookup("pepe")["status"], "ok")
        self.assertAlmostEqual(cold.lookup("pepe")["mcapUsd"], 1_825_046_267.7)


class BodyTextTests(unittest.TestCase):
    def test_chinese_units(self):
        cases = {
            1.62e12: "$1.62万亿",
            81_080_711_424.45: "$810.81亿",
            39_758_882.7: "$3,976万",
            5_321.0: "$5,321",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                text = cmc_market_cap.cn_usd_text(value)
                self.assertTrue(text.startswith(expected.split("$")[0] + "$"), text)
                self.assertIn(expected.split("$")[1], text)

    def test_body_text_silent_unless_ok(self):
        ok = cmc_market_cap.CmcMarketCap.body_text({
            "status": "ok", "mcapUsd": 39_758_882.7, "rank": 437,
        })
        self.assertEqual(ok, "流通市值 $3,976万（CMC #437）")
        self.assertEqual(cmc_market_cap.CmcMarketCap.body_text({"status": "unlisted"}), "")
        self.assertEqual(cmc_market_cap.CmcMarketCap.body_text({"status": "ok", "mcapUsd": 0}), "")


class FundamentalsTests(unittest.TestCase):
    """基本面四项（用户 2026-10-25 对着 CMC 代币页截图点的）：流通量 / 流通率 / 总量。"""

    def test_all_four_figures_are_written_to_the_body(self):
        """IRYS 那种形态：市值 607 万、流通 2.56 亿、总量 10 亿、流通率 25.6%。"""
        entry = {
            "status": "ok", "mcapUsd": 6_070_500.0, "rank": 192,
            "circulatingSupply": 256_000_000.0, "totalSupply": 1_000_000_000.0,
            "fdvUsd": 23_600_000.0,
        }
        self.assertEqual(
            cmc_market_cap.CmcMarketCap.fundamentals_text(entry),
            "流通 2.56亿 · 流通率 25.6% · 总量 10.00亿",
        )
        self.assertEqual(
            cmc_market_cap.CmcMarketCap.body_text(entry),
            "流通市值 $607万（CMC #192） · 流通 2.56亿 · 流通率 25.6% · 总量 10.00亿",
        )

    def test_max_supply_stands_in_when_total_supply_is_missing(self):
        entry = {"status": "ok", "mcapUsd": 1.0, "circulatingSupply": 2_100_000.0,
                 "totalSupply": 0, "maxSupply": 21_000_000.0}
        self.assertAlmostEqual(cmc_market_cap.CmcMarketCap.fundamentals(entry)["floatRatio"], 10.0)
        self.assertIn("总量 2,100.0万", cmc_market_cap.CmcMarketCap.fundamentals_text(entry))

    def test_missing_supply_degrades_to_market_cap_only(self):
        entry = {"status": "ok", "mcapUsd": 39_758_882.7, "rank": 437}
        self.assertEqual(cmc_market_cap.CmcMarketCap.fundamentals_text(entry), "")
        self.assertEqual(
            cmc_market_cap.CmcMarketCap.body_text(entry), "流通市值 $3,976万（CMC #437）"
        )

    def test_a_supply_larger_than_total_never_renders_an_absurd_ratio(self):
        """上游偶尔出现流通量 > 总量（自报口径），这时不写流通率，别显示 137%。"""
        entry = {"status": "ok", "mcapUsd": 1.0, "circulatingSupply": 137.0, "totalSupply": 100.0}
        self.assertEqual(cmc_market_cap.CmcMarketCap.fundamentals(entry)["floatRatio"], 0.0)
        self.assertNotIn("流通率", cmc_market_cap.CmcMarketCap.fundamentals_text(entry))

    def test_supply_text_reads_in_chinese_units(self):
        cases = {21_000_000: "2,100.0万", 1_000_000_000: "10.00亿",
                 589_000_000_000_000: "589.00万亿", 9_500: "9,500"}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(cmc_market_cap.supply_text(value), expected)
        self.assertEqual(cmc_market_cap.supply_text(0), "")


class TrenchAttachTests(unittest.TestCase):
    """战壕榜接口给每行挂 cmc（悬浮窗用）；查不到的行一个字段都不加。"""

    def _store(self, rows):
        fake = cmc_market_cap.CmcMarketCap(
            fetcher=lambda url: _payload(rows),
            snapshot_path=os.path.join(tempfile.mkdtemp(), "cmc_test.json"),
        )
        fake.enable(True)
        fake.refresh()
        return fake

    def test_rows_get_the_fundamentals_text(self):
        fake = self._store([_listing_row(
            symbol="IRYS", name="Irys", rank=192, mcap=6_070_500.0,
            circ=256_000_000.0, total=1_000_000_000.0,
        )])
        payload = {"items": [{"symbol": "IRYS", "name": "Irys"}, {"symbol": "NOSUCH"}]}
        with patch.object(server, "CMC_MARKET_CAP", fake):
            out = server.attach_trench_cmc_fundamentals(payload)
        self.assertEqual(out["items"][0]["cmc"]["text"], "流通 2.56亿 · 流通率 25.6% · 总量 10.00亿")
        self.assertEqual(out["items"][0]["cmc"]["rank"], 192)
        self.assertNotIn("cmc", out["items"][1])
        # 原 payload 不被改（缓存行不能被打脏）
        self.assertNotIn("cmc", payload["items"][0])


class AnnotateTests(unittest.TestCase):
    """服务端挂点：出闸处、作用域闸、股票排除、失败沉默。"""

    def _item(self, **overrides) -> dict:
        item = {
            "key": "rank-monitor:hot|aicoin:rank:XRP",
            "title": "XRP 突破：XRP",
            "body": "AIcoin 热门榜新进",
            "sourceId": "aicoin",
        }
        item.update(overrides)
        return item

    def test_ok_entry_appends_body_note(self):
        fake = cmc_market_cap.CmcMarketCap(
            fetcher=lambda url: _payload([_listing_row(symbol="XRP", name="XRP", rank=5, mcap=81_080_711_424.45)]),
            snapshot_path=os.path.join(tempfile.mkdtemp(), "cmc_test.json"),
        )
        fake.enable(True)
        fake.refresh()
        with patch.object(server, "CMC_MARKET_CAP", fake):
            out = server.annotate_desktop_alert_mcap(self._item())
        self.assertEqual(out["cmcMcapStatus"], "ok")
        self.assertEqual(out["cmcRank"], 5)
        self.assertIn("流通市值", out["body"])
        self.assertIn("CMC #5", out["body"])

    def test_unlisted_is_silent_and_never_writes_zero(self):
        # 快照里有别的币但没有 XRP：refresh 不把它当「上游假空」（空快照会抛错），
        # 所以这里必须给出非空快照，才能走到 lookup 的 unlisted 分支。
        fake = cmc_market_cap.CmcMarketCap(
            fetcher=lambda url: _payload([_listing_row(symbol="DOGE", name="Dogecoin", rank=9, mcap=2.0e10)]),
            snapshot_path=os.path.join(tempfile.mkdtemp(), "cmc_test.json"),
        )
        fake.enable(True)
        fake.refresh()
        with patch.object(server, "CMC_MARKET_CAP", fake):
            out = server.annotate_desktop_alert_mcap(self._item())
        self.assertEqual(out["cmcMcapStatus"], "unlisted")
        self.assertNotIn("cmcMcapUsd", out)
        self.assertNotIn("流通市值", out["body"])

    def test_stock_alerts_are_skipped(self):
        called = []

        class Boom(cmc_market_cap.CmcMarketCap):
            def lookup(self, symbol):
                called.append(symbol)
                return {"status": "ok", "mcapUsd": 1.0, "rank": 1}

        with patch.object(server, "CMC_MARKET_CAP", Boom()):
            out = server.annotate_desktop_alert_mcap(
                self._item(key="rank-monitor:usstocks|futu:SOXL", sourceId="futu-usstocks",
                           title="美股异动：SOXL")
            )
        self.assertEqual(called, [])
        self.assertNotIn("cmcMcapStatus", out)

    def test_out_of_scope_news_flash_is_skipped(self):
        called = []

        class Boom(cmc_market_cap.CmcMarketCap):
            def lookup(self, symbol):
                called.append(symbol)
                return {"status": "ok", "mcapUsd": 1.0, "rank": 1}

        with patch.object(server, "CMC_MARKET_CAP", Boom()):
            out = server.annotate_desktop_alert_mcap(
                self._item(key="flash:12345", title="马斯克：美债危机", sourceId="news-flash")
            )
        self.assertEqual(called, [])
        self.assertNotIn("cmcMcapStatus", out)

    def test_smart_money_buy_with_symbol_is_in_scope(self):
        fake = cmc_market_cap.CmcMarketCap(
            fetcher=lambda url: _payload([_listing_row(symbol="XRP", name="XRP", rank=5, mcap=8.1e10)]),
            snapshot_path=os.path.join(tempfile.mkdtemp(), "cmc_test.json"),
        )
        fake.enable(True)
        fake.refresh()
        with patch.object(server, "CMC_MARKET_CAP", fake):
            out = server.annotate_desktop_alert_mcap(self._item(
                key="smart-money-buy:bsc:0xabcd:123",
                title="KOL NΞO 买入 XRP · $122",
                symbol="XRP",
            ))
        self.assertEqual(out["cmcMcapStatus"], "ok")


if __name__ == "__main__":
    unittest.main()
