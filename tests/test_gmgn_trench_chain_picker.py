"""GMGN 战壕新币榜的「链」下拉：选项推导 + 每条链的计数。

榜是一条扁平 tape（300 条重行），服务端**不按链复制 rows**——只预计算选项与
计数，客户端按 ``row.network`` 现筛。这里锁住那两块推导。
"""

import unittest
from pathlib import Path

import server


def board_row(chain, *, backfilled=False, is_current=False, key="network"):
    row = {
        "symbol": f"T{chain[:3].upper()}",
        "contractAddress": f"0x{chain}",
        "network": chain,
        "chain": chain,
        "poolCreatedAt": 1791548326000,
    }
    if key != "network":
        row.pop("network", None)
    if backfilled:
        row["backfilled"] = True
    if is_current:
        row["isCurrent"] = True
    return row


class GmgnTrenchChainTallyTests(unittest.TestCase):
    def test_counts_normalise_case_and_ignore_unusable_rows(self):
        tally = server.gmgn_trench_chain_tally([
            board_row("solana"),
            board_row("SOLANA"),
            board_row("bsc"),
            {"symbol": "NOCHAIN"},
            "not-a-dict",
            None,
        ])
        self.assertEqual(tally, {"solana": 2, "bsc": 1})

    def test_falls_back_to_the_chain_field(self):
        self.assertEqual(server.gmgn_trench_chain_tally([board_row("arc", key="chain")]), {"arc": 1})


class GmgnTrenchChainPickerTests(unittest.TestCase):
    def test_options_start_with_all_and_cover_exactly_the_boards_chains(self):
        options, _ = server.gmgn_trench_chain_picker([], [], 0)
        self.assertEqual(options[0], {"value": "all", "label": "综合"})
        values = [option["value"] for option in options]
        self.assertEqual(values, ["all", "solana", "bsc", "robinhood", "eth", "arc"])
        # 顺序必须稳定（按 GMGN_TRENCH_CHAIN_FILTERS 的声明序），否则下拉会随数据抖动。
        self.assertEqual(len(values), len(set(values)))
        # 每条链都要有标签，且用的是战壕榜那套短码（robinhood → HOOD，不是 RBN）。
        for option in options[1:]:
            self.assertTrue(option["label"], option)
            self.assertEqual(option["label"], server.GMGN_TRENCH_CHAIN_LABELS[option["value"]])
        self.assertEqual(next(o["label"] for o in options if o["value"] == "robinhood"), "HOOD")

    def test_base_is_dropped_because_the_board_never_ingests_it(self):
        options, counts = server.gmgn_trench_chain_picker([], [], 0)
        self.assertNotIn("base", [option["value"] for option in options])
        self.assertNotIn("base", counts)

    def test_all_mirrors_the_flat_board_totals(self):
        rows = [board_row("solana"), board_row("bsc"), board_row("arc", backfilled=True)]
        current = [board_row("solana", is_current=True)]
        _, counts = server.gmgn_trench_chain_picker(rows, current, 1)
        self.assertEqual(counts["all"], {"total": 3, "current": 1, "backfilled": 1})

    def test_every_chain_carries_its_own_total_current_and_backfill(self):
        rows = [
            board_row("solana"),
            board_row("solana", backfilled=True),
            board_row("robinhood", is_current=True),
        ]
        current = [board_row("robinhood", is_current=True)]
        _, counts = server.gmgn_trench_chain_picker(rows, current, 1)
        self.assertEqual(counts["solana"], {"total": 2, "current": 0, "backfilled": 1})
        self.assertEqual(counts["robinhood"], {"total": 1, "current": 1, "backfilled": 0})
        self.assertEqual(counts["bsc"], {"total": 0, "current": 0, "backfilled": 0})

    def test_backfill_count_is_sanitised(self):
        _, counts = server.gmgn_trench_chain_picker([], [], None)
        self.assertEqual(counts["all"]["backfilled"], 0)
        _, counts = server.gmgn_trench_chain_picker([], [], float("nan"))
        self.assertEqual(counts["all"]["backfilled"], 0)


class GmgnTrenchChainPickerWiringTests(unittest.TestCase):
    """榜刷新必须把选项与计数挂到 source 上，否则前端没有下拉可渲染。"""

    def test_the_refresh_attaches_the_picker_to_the_source(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn("chain_options, chain_counts = gmgn_trench_chain_picker(", source)
        self.assertIn('"chainOptions": chain_options,', source)
        self.assertIn('"chainCounts": chain_counts,', source)


if __name__ == "__main__":
    unittest.main()
