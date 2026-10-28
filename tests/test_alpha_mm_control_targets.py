"""控盘名单的**只读复用**接口（alpha_mm_monitor.control_targets）。

价格异动板块要拿它判「爆红」：控盘 ≥90% + Alpha 合约 + 流通市值 ≤3000 万美元。
这里测的是**这一个出口**，不重测五条判据本身（那些在 test_alpha_mm_monitor.py）。

最容易犯的三个错，每个都有对应用例：
1. 把「市值拿不到」（0）当成「小市值」放行 → 假爆红；
2. 把墓碑（拉盘后暴跌 ≥60%）里的币又算回来；
3. 每条弹窗都去读一次 10KB JSON → 缓存失效或缓存永不失效。
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import alpha_mm_monitor as alpha_mm

BASE = {
    "name": "Doodles",
    "chain": "BSC",
    "contract": "0x722294f6c97102fb0ddb5b907c8d16bdeab3f6d9",
    "network": "bsc",
    "pool": "0xc1d32d62",
    "cacheKey": "bsc:0x722294f6c97102fb0ddb5b907c8d16bdeab3f6d9",
    "marketCap": 24_990_468.0,
    "controlPct": 97.36,
    "changePct": 110.25,
    "url": "https://www.binance.com/zh-CN/alpha/bsc/0x7222",
    "origin": "热门榜 / 涨幅榜",
    "drawdownPct": 23.14,
}


def entry(**overrides):
    row = dict(BASE)
    row["at"] = time.time() - 3600
    row.update(overrides)
    return row


class ControlTargetsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "alpha_mm.json")
        alpha_mm._CONTROL_TARGETS_CACHE.update(
            {"path": "", "mtime": -1.0, "at": 0.0, "value": {}}
        )

    def tearDown(self):
        self._tmp.cleanup()
        alpha_mm._CONTROL_TARGETS_CACHE.update(
            {"path": "", "mtime": -1.0, "at": 0.0, "value": {}}
        )
        os.environ.pop("XINGYUN_ALPHA_MM_MIN_CONTROL_PCT", None)

    def write(self, *, watch=None, blocked=None):
        Path(self.path).write_text(
            json.dumps({"watch": watch or {}, "blocked": blocked or {}}, ensure_ascii=False),
            encoding="utf-8",
        )

    def targets(self, **kwargs):
        kwargs.setdefault("refresh", True)
        return alpha_mm.control_targets(self.path, **kwargs)

    def test_a_qualified_token_is_listed_with_its_numbers(self):
        self.write(watch={"DOOD": entry()})
        row = self.targets()["DOOD"]
        self.assertAlmostEqual(row["controlPct"], 97.36)
        self.assertAlmostEqual(row["marketCap"], 24_990_468.0)
        self.assertEqual(row["chain"], "BSC")
        self.assertEqual(row["contract"], "0x722294f6c97102fb0ddb5b907c8d16bdeab3f6d9")
        self.assertTrue(row["alphaListed"], "入选即 Alpha 合约，这个身份要显式带出来")

    def test_symbols_are_normalised_the_same_way_everywhere(self):
        self.write(watch={" dood ": entry()})
        self.assertIn("DOOD", self.targets())

    def test_control_below_ninety_percent_is_out(self):
        self.write(watch={"ARIA": entry(controlPct=85.08)})
        self.assertEqual(self.targets(), {})

    def test_market_cap_above_thirty_million_is_out(self):
        self.write(watch={"ERA": entry(marketCap=33_400_000.0)})
        self.assertEqual(self.targets(), {})

    def test_a_missing_market_cap_is_not_read_as_small(self):
        """市值拿不到 = 未核验，**不是**小市值。这里放行就会造出假爆红。"""
        self.write(watch={"X": entry(marketCap=0)})
        self.assertEqual(self.targets(), {})

    def test_a_tombstoned_token_never_comes_back(self):
        """拉盘后暴跌 ≥60% 的币写进 blocked 就是永久排除，名单里不许再出现。"""
        self.write(watch={"PRL": entry()}, blocked={"PRL": {"reason": "回撤 97%"}})
        self.assertEqual(self.targets(), {})

    def test_a_stale_entry_is_ignored(self):
        """watch 14 天过期，超时的条目市值早变了。"""
        self.write(watch={"OLD": entry(at=time.time() - alpha_mm.MM_WATCH_SECONDS - 60)})
        self.assertEqual(self.targets(), {})

    def test_the_thresholds_follow_the_environment_override(self):
        os.environ["XINGYUN_ALPHA_MM_MIN_CONTROL_PCT"] = "80"
        self.write(watch={"ARIA": entry(controlPct=85.08)})
        self.assertIn("ARIA", self.targets())

    def test_a_missing_or_broken_state_file_is_an_empty_list(self):
        self.assertEqual(alpha_mm.control_targets(self.path), {})
        Path(self.path).write_text("{not json", encoding="utf-8")
        self.assertEqual(alpha_mm.control_targets(self.path, refresh=True), {})

    def test_the_result_is_cached_until_the_file_changes(self):
        """价格异动一页 120 行都会查它，每条都读一次文件太浪费。"""
        self.write(watch={"DOOD": entry()})
        first = self.targets()
        self.assertIn("DOOD", first)
        calls = {"n": 0}
        original = alpha_mm.load_state

        def counting(path=alpha_mm.STATE_PATH):
            calls["n"] += 1
            return original(path)

        alpha_mm.load_state = counting
        try:
            second = alpha_mm.control_targets(self.path)
        finally:
            alpha_mm.load_state = original
        self.assertIn("DOOD", second)
        self.assertEqual(calls["n"], 0, "文件没变却重新读了一遍")

    def test_touching_the_file_invalidates_the_cache(self):
        self.write(watch={"DOOD": entry()})
        self.assertIn("DOOD", self.targets())
        self.write(watch={})
        self.assertEqual(alpha_mm.control_targets(self.path), {})

    def test_the_caller_cannot_poison_the_cache(self):
        self.write(watch={"DOOD": entry()})
        first = self.targets()
        first["DOOD"]["controlPct"] = 1.0
        self.assertAlmostEqual(self.targets()["DOOD"]["controlPct"], 97.36)


if __name__ == "__main__":
    unittest.main()
