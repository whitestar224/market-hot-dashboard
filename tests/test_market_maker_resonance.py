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

import market_maker_monitor as mm

TOKEN = "0x39dbed3a2bd333467115de4566f8d8b0e4a4f2f1"
OTHER = "0x" + "7c" * 20


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

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
