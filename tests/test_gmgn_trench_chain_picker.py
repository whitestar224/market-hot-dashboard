"""GMGN 战壕新币榜的「链」下拉：选项推导 + 每条链的计数。

榜是一条扁平 tape（300 条重行），服务端**不按链复制 rows**——只预计算选项与
计数，客户端按 ``row.network`` 现筛。这里锁住那两块推导。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class TrenchOverlayChainPreferenceTests(unittest.TestCase):
    """榜单页写的「当前查看链」。

    桌面战壕悬浮窗是独立 pythonw 进程，读不到浏览器的 localStorage，所以两边唯一的
    联动点就是这份共享偏好：前端 POST、面板轮询 GET。这里的收口失败会让面板带着脏链
    去请求 /api/onchain-trenches，整条链一取不到数就变空面板。
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "trench_overlay_chain.json"
        for patcher in (
            patch.object(server, "TRENCH_OVERLAY_CHAIN_PREFERENCE_PATH", self.path),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(
            server.TRENCH_OVERLAY_CHAIN_PREFERENCE.update, {"loaded": False, "chain": "all"}
        )
        server.TRENCH_OVERLAY_CHAIN_PREFERENCE.update({"loaded": False, "chain": "all"})

    def test_unknown_values_fall_back_to_all(self):
        for bad in ("", None, "all", "ALL", "base", "sol", "../../etc/passwd", "a" * 40):
            with self.subTest(bad=bad):
                self.assertEqual(server.normalize_trench_overlay_chain(bad), "all")

    def test_monitored_chains_survive_normalisation(self):
        for chain in ("robinhood", "solana", "bsc", "eth", "arc"):
            with self.subTest(chain=chain):
                self.assertEqual(server.normalize_trench_overlay_chain(chain), chain)
                self.assertEqual(server.normalize_trench_overlay_chain(chain.upper()), chain)

    def test_defaults_to_all_before_anything_is_written(self):
        self.assertEqual(server.trench_overlay_chain_preference(), "all")

    def test_written_chain_is_read_back_and_persisted(self):
        self.assertEqual(server.set_trench_overlay_chain_preference("robinhood"), "robinhood")
        self.assertEqual(server.trench_overlay_chain_preference(), "robinhood")
        self.assertTrue(self.path.exists())

        # 模拟服务重启：内存态清空，磁盘上的选择必须还在，否则面板会悄悄跳回「综合」。
        server.TRENCH_OVERLAY_CHAIN_PREFERENCE.update({"loaded": False, "chain": "all"})
        self.assertEqual(server.trench_overlay_chain_preference(), "robinhood")

    def test_an_unusable_chain_resets_the_shared_preference(self):
        server.set_trench_overlay_chain_preference("robinhood")
        self.assertEqual(server.set_trench_overlay_chain_preference("base"), "all")
        self.assertEqual(server.trench_overlay_chain_preference(), "all")
        self.assertEqual(
            server.json.loads(self.path.read_text(encoding="utf-8"))["chain"], "all"
        )

    def test_a_corrupt_file_never_breaks_the_reader(self):
        self.path.write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(server.trench_overlay_chain_preference(), "all")

    def test_an_unwritable_path_only_loses_persistence(self):
        # 指向一个目录：落盘必然失败，但当前进程内的选择必须照样生效。
        with patch.object(server, "TRENCH_OVERLAY_CHAIN_PREFERENCE_PATH", Path(self.path.parent)):
            self.assertEqual(server.set_trench_overlay_chain_preference("robinhood"), "robinhood")
        self.assertEqual(server.trench_overlay_chain_preference(), "robinhood")


class TrenchOverlayChainEndpointWiringTests(unittest.TestCase):
    """读完一个写的端点必须都挂上，否则「榜单切链 → 面板跟随」整条链路断开。"""

    def test_get_and_post_routes_are_wired(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn('parsed.path == "/api/trench-overlay-preference"', source)
        self.assertIn('route == "/api/trench-overlay-preference"', source)
        self.assertIn("trench_overlay_chain_preference()", source)
        self.assertIn("set_trench_overlay_chain_preference(", source)


if __name__ == "__main__":
    unittest.main()
