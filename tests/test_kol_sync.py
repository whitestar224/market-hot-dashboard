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
D = "0xdddddddddddddddddddddddddddddddddddddddd"
E = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
TOKEN = "0x7777777777777777777777777777777777777777"
OTHER_TOKEN = "0x8888888888888888888888888888888888888888"


class KolSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.monitor = SMM.SmartMoneyMonitor(Path(self.tmp.name) / "sync.sqlite")
        self.addCleanup(self.monitor.close)
        # 三个假核心 KOL（生产名单是 CryptoCharming / 阿峰 / cowboy）。
        self.monitor.kol_sync_wallets = {A: "甲", B: "乙", C: "丙"}
        # 「同车增加」的观察面：核心三人 + 两个外围追踪钱包。
        self.monitor.kol_sync_track_wallets = {A: "甲", B: "乙", C: "丙", D: "丁", E: "戊"}
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


class KolSyncJoinTests(unittest.TestCase):
    """同车成立之后，别的追踪钱包再上车 → 继续提醒（用户 2026-10-27 追加要求）。

    「在这个同车标的上如果还有其他的 KOL 再进来，继续做同车增加的提醒」。
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.monitor = SMM.SmartMoneyMonitor(Path(self.tmp.name) / "join.sqlite")
        self.addCleanup(self.monitor.close)
        self.monitor.kol_sync_wallets = {A: "甲", B: "乙", C: "丙"}
        self.monitor.kol_sync_track_wallets = {A: "甲", B: "乙", C: "丙", D: "丁", E: "戊"}
        self.contexts = []
        self.monitor.set_sync_alert_callback(self.contexts.append)
        self.t0 = int(time.time() * 1000)

    def buy(self, wallet, *, usd=100.0, at=None, token=TOKEN, chain="bsc", symbol="西瓜"):
        return self.monitor.record_buy({
            "chain": chain,
            "walletAddress": wallet,
            "transactionHash": f"0x{wallet[-6:]}{int(at or 0)}{usd:g}",
            "tokenAddress": token,
            "symbol": symbol,
            "paymentUsd": usd,
            "paymentAsset": "BNB",
            "observedAt": int(at if at is not None else self.t0),
        })

    def trio(self):
        for index, wallet in enumerate((A, B, C)):
            self.buy(wallet, usd=100 + index, at=self.t0 - (3 - index) * 1000)
        self.assertEqual(len(self.contexts), 1)
        self.assertEqual(self.contexts[0]["mode"], "trio")

    # ------------------------------------------------------------------
    def test_fourth_tracked_wallet_triggers_a_join_alert(self):
        self.trio()
        self.buy(D, usd=860, at=self.t0 + 1000)
        self.assertEqual(len(self.contexts), 2)
        join = self.contexts[1]
        self.assertEqual(join["mode"], "join")
        self.assertEqual([item["label"] for item in join["newcomers"]], ["丁"])
        self.assertEqual(join["onBoardCount"], 4)
        # 车上的老面孔还在 buyers 里，但 newcomers 只列新人。
        self.assertEqual(len(join["buyers"]), 4)

    def test_the_same_newcomer_is_not_announced_twice(self):
        self.trio()
        self.buy(D, usd=860, at=self.t0 + 1000)
        self.buy(D, usd=100, at=self.t0 + 2000)
        self.assertEqual([ctx["mode"] for ctx in self.contexts], ["trio", "join"])

    def test_each_additional_wallet_gets_its_own_alert(self):
        self.trio()
        self.buy(D, usd=860, at=self.t0 + 1000)
        self.buy(E, usd=1200, at=self.t0 + 2000)
        joins = [ctx for ctx in self.contexts if ctx["mode"] == "join"]
        self.assertEqual(len(joins), 2)
        self.assertEqual([item["label"] for item in joins[1]["newcomers"]], ["戊"])
        self.assertEqual(joins[1]["onBoardCount"], 5)

    def test_no_join_alert_before_the_trio_is_complete(self):
        # 只有甲、乙 + 外围的丁：没凑齐核心三人，就不该有「同车」这回事。
        self.buy(A, at=self.t0 - 3000)
        self.buy(B, at=self.t0 - 2000)
        self.buy(D, at=self.t0 - 1000)
        self.assertEqual(self.contexts, [])

    def test_untracked_wallet_does_not_count_as_a_joiner(self):
        self.trio()
        stranger = "0x9999999999999999999999999999999999999999"
        self.buy(stranger, usd=5000, at=self.t0 + 1000)
        self.assertEqual([ctx["mode"] for ctx in self.contexts], ["trio"])

    def test_join_window_expires(self):
        self.trio()
        # 把剧集的起点往前拨出窗口：再有人上车也不该再提醒。
        episode = self.monitor._sync_episodes[f"bsc:{TOKEN.lower()}"]
        episode["episodeStart"] = self.t0 - (SMM.KOL_SYNC_JOIN_WINDOW_SECONDS + 60) * 1000
        self.buy(D, usd=860, at=self.t0 + 1000)
        self.assertEqual([ctx["mode"] for ctx in self.contexts], ["trio"])

    def test_newcomer_on_a_different_token_does_not_leak(self):
        self.trio()
        self.buy(D, usd=860, at=self.t0 + 1000, token=OTHER_TOKEN)
        # 另一个币没有同车剧情，丁在那儿买入不构成「增加」。
        self.assertEqual([ctx["mode"] for ctx in self.contexts], ["trio"])


class KolSyncTrackedSeedTests(unittest.TestCase):
    """用户 2026-10-27 给的 20 个追踪钱包里，15 个是为此新接入的。"""

    def test_aux_tracked_wallets_are_seeded_record_only_on_three_chains(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = SMM.SmartMoneyMonitor(Path(tmp.name) / "seed.sqlite", seed_defaults=True)
        self.addCleanup(monitor.close)
        aux = set(SMM.TRACKED_KOL_AUX_WALLETS)
        self.assertEqual(len(aux), 15)
        rows = [row for row in monitor.store.list_wallets()
                if (row["address"] or "").lower() in aux]
        # 每条三链（bsc / ethereum / base）。
        self.assertEqual(len(rows), 45)
        for row in rows:
            with self.subTest(address=row["address"], chain=row["chain"]):
                self.assertTrue(row["enabled"])
                # 阈值 10 亿 U = 只记录、不对单笔弹窗；同车判定照样能用到这笔。
                self.assertGreaterEqual(row["alertThresholdUsd"], SMM.KOL_TRACK_RECORD_ONLY_THRESHOLD_USD)
                self.assertEqual(row["sourceName"], "用户追踪钱包清单")
        # 观察面一共 20 人（5 个老 KOL + 15 个新增）。
        self.assertEqual(len(SMM.KOL_SYNC_TRACK_LABELS), 20)
        for known in ("0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9",
                      "0xbf004bff64725914ee36d03b87d6965b0ced4903",
                      "0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407"):
            self.assertIn(known, SMM.KOL_SYNC_TRACK_LABELS)


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

    def test_join_popup_payload_only_lists_newcomers(self):
        context = self._context()
        context.update({
            "mode": "join",
            "onBoardCount": 4,
            "newcomers": [
                {"address": "0x7a2363a401b2340c7941dd2eeff0196a5078d2e6",
                 "label": "追踪 0x7a23…d2e6", "usd": 860, "firstAt": 4, "count": 1},
            ],
        })
        captured = {}
        with patch.object(server, "launch_desktop_alert", side_effect=lambda p: captured.update(p) or {"ok": True}):
            server.send_kol_sync_desktop_alert(context)
        self.assertIn("同车增加", captured["title"])
        self.assertIn("车上 4 人", captured["title"])
        self.assertIn("新上车", captured["body"])
        self.assertIn("0x7a23…d2e6", captured["body"])
        # 老面孔（CryptoCharming 等）不进正文，否则卡片放不下。
        self.assertNotIn("CryptoCharming", captured["body"])
        self.assertEqual(captured["queuePriority"], 130)
        self.assertTrue(captured["key"].startswith("kol-join:"))


if __name__ == "__main__":
    unittest.main()
