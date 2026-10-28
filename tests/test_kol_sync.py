"""「三个人同时上车」的单独提醒（用户 2026-10-27）。

原话：「@cryptocharming @feibo03 @afeng 这三个人如果同时上车了一个币的话，记得单独提醒」。

判定落在 ``smart_money_monitor._maybe_emit_kol_sync``：**同链同合约**、窗口期内、
名单里的人都买过 → 回调一次；排版落在 ``server.send_kol_sync_desktop_alert``。
这里用假的名单和假时钟，不碰真网/真库。

几条必须守住的边界：
  * 金额门槛不参与判定（三人都买小单也是「同时上车」）；
  * 同名不同合约、跨链同名币都不算；
  * 一个币 12 小时内只提醒一次（否则第三人买完会连炸）；
  * 窗口外的旧买入不参与凑数。
"""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server
import smart_money_monitor as SMM

A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
C = "0xcccccccccccccccccccccccccccccccccccccccc"
TOKEN = "0x7777777777777777777777777777777777777777"
OTHER_TOKEN = "0x8888888888888888888888888888888888888888"


class KolSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.monitor = SMM.SmartMoneyMonitor(Path(self.tmp.name) / "sync.sqlite")
        self.addCleanup(self.monitor.close)
        # 三个假 KOL（生产名单里目前只有 CryptoCharming / 阿峰，第三个待用户给地址）。
        self.monitor.kol_sync_wallets = {A: "甲", B: "乙", C: "丙"}
        self.contexts = []
        self.monitor.set_sync_alert_callback(self.contexts.append)

    # -- helpers -------------------------------------------------------
    def buy(self, wallet, *, token=TOKEN, usd=100.0, at=None, chain="bsc", symbol="TEST"):
        return self.monitor.record_buy({
            "chain": chain,
            "walletAddress": wallet,
            "transactionHash": f"0x{wallet[-6:]}{int(at or 0)}{usd:g}",
            "tokenAddress": token,
            "symbol": symbol,
            "paymentUsd": usd,
            "paymentAsset": "BNB",
            "paymentAmount": 1.0,
            "observedAt": int(at if at is not None else time.time() * 1000),
        })

    # -- 判定 ----------------------------------------------------------
    def test_three_buyers_trigger_exactly_one_context(self):
        now = int(time.time() * 1000)
        self.buy(A, usd=100, at=now - 3_000)
        self.buy(B, usd=250, at=now - 2_000)
        self.assertEqual(self.contexts, [])  # 两个人还不够
        self.buy(C, usd=500, at=now - 1_000)
        self.assertEqual(len(self.contexts), 1)
        context = self.contexts[0]
        self.assertEqual(context["tokenAddress"], TOKEN)
        self.assertEqual([item["label"] for item in context["buyers"]], ["甲", "乙", "丙"])
        self.assertAlmostEqual(context["totalUsd"], 850.0)

    def test_small_buys_still_count(self):
        # 金额门槛（500U / 10000U）在这里不参与判定：都买 $10 也算同时上车。
        now = int(time.time() * 1000)
        for index, wallet in enumerate((A, B, C)):
            self.buy(wallet, usd=10, at=now - index * 1000)
        self.assertEqual(len(self.contexts), 1)
        self.assertAlmostEqual(self.contexts[0]["totalUsd"], 30.0)

    def test_repeat_buys_by_the_same_buyer_do_not_fake_a_third(self):
        now = int(time.time() * 1000)
        self.buy(A, usd=100, at=now - 5_000)
        self.buy(B, usd=100, at=now - 4_000)
        self.buy(A, usd=100, at=now - 3_000)
        self.assertEqual(self.contexts, [])

    def test_different_token_or_chain_does_not_combine(self):
        now = int(time.time() * 1000)
        self.buy(A, usd=100, at=now - 3_000, token=OTHER_TOKEN)
        self.buy(B, usd=100, at=now - 2_000, chain="ethereum")
        self.buy(C, usd=100, at=now - 1_000, token=OTHER_TOKEN)
        self.assertEqual(self.contexts, [])

    def test_old_buys_outside_the_window_do_not_count(self):
        now = int(time.time() * 1000)
        old = now - (SMM.KOL_SYNC_WINDOW_SECONDS + 3600) * 1000
        self.buy(A, usd=100, at=old)
        self.buy(B, usd=100, at=now - 2_000)
        self.buy(C, usd=100, at=now - 1_000)
        self.assertEqual(self.contexts, [])

    def test_realert_is_suppressed_within_the_cooldown(self):
        now = int(time.time() * 1000)
        for index, wallet in enumerate((A, B, C)):
            self.buy(wallet, usd=100, at=now - (3 - index) * 1000)
        self.assertEqual(len(self.contexts), 1)
        # 第四笔（任一人补仓）不该再弹一条。
        self.buy(A, usd=999, at=now + 1_000)
        self.assertEqual(len(self.contexts), 1)

    def test_store_query_is_chain_and_contract_scoped(self):
        now = int(time.time() * 1000)
        self.buy(A, usd=100, at=now, token=TOKEN.upper().replace("0X", "0x"))
        rows = self.monitor.store.buys_for_token("bsc", TOKEN, since_ms=now - 60_000)
        self.assertEqual(len(rows), 1)
        # 换链、换合约都查不到。
        self.assertEqual(self.monitor.store.buys_for_token("ethereum", TOKEN, since_ms=now - 60_000), [])
        self.assertEqual(
            self.monitor.store.buys_for_token("bsc", OTHER_TOKEN, since_ms=now - 60_000), []
        )


class KolSyncPopupTests(unittest.TestCase):
    """排版：标题/正文/优先级；以及「凑不齐人数就别弹」。"""

    def _context(self):
        return {
            "chain": "bsc",
            "tokenAddress": TOKEN,
            "symbol": "西瓜",
            "buyers": [
                {"address": A, "label": "CryptoCharming", "usd": 1200, "firstAt": 1, "count": 1},
                {"address": B, "label": "阿峰_Afeng", "usd": 900, "firstAt": 2, "count": 2},
                {"address": C, "label": "feibo03", "usd": 500, "firstAt": 3, "count": 1},
            ],
            "totalUsd": 2600,
            "windowHours": 24,
        }

    def test_popup_payload(self):
        captured = {}
        with patch.object(server, "launch_desktop_alert", side_effect=lambda p: captured.update(p) or {"ok": True}):
            server.send_kol_sync_desktop_alert(self._context())
        self.assertEqual(captured["key"], f"kol-sync:bsc:{TOKEN.lower()}")
        self.assertIn("三人同时上车", captured["title"])
        self.assertIn("2,600", captured["title"])
        self.assertIn("CryptoCharming", captured["body"])
        self.assertIn("feibo03", captured["body"])
        self.assertEqual(captured["queuePriority"], 130)
        self.assertEqual(captured["chain"], "bsc")
        self.assertEqual(captured["contractAddress"], TOKEN)

    def test_incomplete_context_is_skipped(self):
        context = self._context()
        context["buyers"] = context["buyers"][:2]
        captured = {}
        with patch.object(server, "launch_desktop_alert", side_effect=lambda p: captured.update(p) or {"ok": True}):
            result = server.send_kol_sync_desktop_alert(context)
        self.assertTrue(result.get("skipped"))
        self.assertEqual(captured, {})


if __name__ == "__main__":
    unittest.main()
