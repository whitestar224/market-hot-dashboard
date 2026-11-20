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

    def test_two_core_wallets_alone_are_not_a_sync(self):
        # 只有甲、乙两个人（都是核心）——凑不满 3 个人，就不该有「同车」这回事。
        self.buy(A, at=self.t0 - 3000)
        self.buy(B, at=self.t0 - 2000)
        self.assertEqual(self.contexts, [])

    def test_three_wallets_with_two_core_trigger_a_sync(self):
        """2026-11-12 新口径：任意 3 人 + 其中 ≥2 个核心就算同车。

        起因是「币安生活」：cowboy $471 + 阿峰 $404 + antiposition $512 在 50 秒内
        先后上车，合计 $2,922 却零弹窗 —— antiposition 在同车观察面但不在核心三人里，
        旧口径 core_count 永远卡在 2。这里用甲/乙(核心) + 丁(外围) 复现同一形态。
        """
        self.buy(A, at=self.t0 - 3000)
        self.buy(D, at=self.t0 - 2000)
        self.buy(B, at=self.t0 - 1000)
        self.assertEqual([ctx["mode"] for ctx in self.contexts], ["trio"])
        self.assertEqual(self.contexts[0]["coreCount"], 2)
        self.assertEqual(len(self.contexts[0]["buyers"]), 3)

    def test_one_core_plus_two_aux_wallets_stay_silent(self):
        # 核心 1 + 外围 2：这是辅助追踪钱包凑数，不是 KOL 共振。
        self.buy(A, at=self.t0 - 3000)
        self.buy(D, at=self.t0 - 2000)
        self.buy(E, at=self.t0 - 1000)
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
    """用户 2026-10-27 给的 20 个追踪钱包里，15 个是为此新接入的。

    2026-11-11 其中三个被用户点名升级成「要弹窗的聪明钱」
    （0xa83b…8811 = antiposition、0xa7d4…2b9f = 米神、0x2ce9…3373 = 枯坐P小将），
    2026-11-20 又有 0xc2c6…091c（区块日记）被点名，都已移出这份「只记录」清单 ——
    所以现在是 11 个 / 33 行，但同车观察面仍是 20 人（名字在 KOL_SYNC_TRACK_LABELS
    里单独登记）。
    """

    def test_aux_tracked_wallets_are_seeded_record_only_on_three_chains(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = SMM.SmartMoneyMonitor(Path(tmp.name) / "seed.sqlite", seed_defaults=True)
        self.addCleanup(monitor.close)
        aux = set(SMM.TRACKED_KOL_AUX_WALLETS)
        self.assertEqual(len(aux), 11)
        rows = [row for row in monitor.store.list_wallets()
                if (row["address"] or "").lower() in aux]
        # 每条三链（bsc / ethereum / base）。
        self.assertEqual(len(rows), 33)
        for row in rows:
            with self.subTest(address=row["address"], chain=row["chain"]):
                self.assertTrue(row["enabled"])
                # 阈值 10 亿 U = 只记录、不对单笔弹窗；同车判定照样能用到这笔。
                self.assertGreaterEqual(row["alertThresholdUsd"], SMM.KOL_TRACK_RECORD_ONLY_THRESHOLD_USD)
                self.assertEqual(row["sourceName"], "用户追踪钱包清单")
        # 观察面至少 20 人（5 个老 KOL + 15 个新增）；2026-11-12 加了 CryptoCharming
        # 的两个 Solana 钱包变 22，所以这里只断下限，不钉死具体数字。
        self.assertGreaterEqual(len(SMM.KOL_SYNC_TRACK_LABELS), 20)
        for known in ("0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9",
                      "0xbf004bff64725914ee36d03b87d6965b0ced4903",
                      "0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407"):
            self.assertIn(known, SMM.KOL_SYNC_TRACK_LABELS)

    def test_antiposition_is_promoted_to_a_bsc_only_popup_wallet(self):
        """用户 2026-11-11：「0xa83b…8811，antiposition，bsc链的聪明钱」。

        它以前在「只记录」清单里（阈值 10 亿 U、三条链），现在要真弹窗：
        昵称改成 antiposition、只挂 BSC。阈值演变：11-11 定 1000U（中位 $522，
        500U 会到每天 29 条）；11-12 用户要求与 cowboy / 阿峰 统一 300U
        （约 35 张/天，用户知情同意）。
        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = SMM.SmartMoneyMonitor(Path(tmp.name) / "seed.sqlite", seed_defaults=True)
        self.addCleanup(monitor.close)
        address = SMM.ANTIPOSITION_WALLET
        self.assertNotIn(address, SMM.TRACKED_KOL_AUX_WALLETS)
        # 同车观察面不能因为这次升级缩水。
        self.assertEqual(SMM.KOL_SYNC_TRACK_LABELS[address], "antiposition")
        rows = [row for row in monitor.store.list_wallets()
                if (row["address"] or "").lower() == address]
        self.assertEqual([row["chain"] for row in rows], ["bsc"])
        self.assertEqual(rows[0]["nickname"], "antiposition")
        self.assertEqual(rows[0]["alertThresholdUsd"], 300.0)
        self.assertTrue(rows[0]["enabled"])

    def test_mi_shen_is_promoted_to_a_bsc_only_popup_wallet(self):
        """用户 2026-11-11：「0xa7d4…2b9f，这个也接上，KOL米神的」。

        同样是「只记录」清单里的地址被点名升级。买单档位是 0.5 BNB（≈$340）试探、
        1 BNB（≈$680）建仓；2026-11-11 定 500U 卡在两者之间（≈5.3 条/天），
        2026-11-12 因 MIRO 那笔 $325.94 漏掉，用户要求降到 300U（实测 6.1 张/天）。
        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = SMM.SmartMoneyMonitor(Path(tmp.name) / "seed.sqlite", seed_defaults=True)
        self.addCleanup(monitor.close)
        address = SMM.MI_SHEN_WALLET
        self.assertNotIn(address, SMM.TRACKED_KOL_AUX_WALLETS)
        self.assertEqual(SMM.KOL_SYNC_TRACK_LABELS[address], "米神")
        rows = [row for row in monitor.store.list_wallets()
                if (row["address"] or "").lower() == address]
        self.assertEqual([row["chain"] for row in rows], ["bsc"])
        self.assertEqual(rows[0]["nickname"], "米神")
        self.assertEqual(rows[0]["alertThresholdUsd"], 300.0)
        self.assertTrue(rows[0]["enabled"])

    def test_qukuai_riji_is_promoted_from_record_only_to_popup(self):
        """用户 2026-11-20：「0xc2c6…091c，区块日记的买入记录怎么没有弹出来」。

        这个地址原本在 20 人「只记录、不弹窗」清单里（阈值 10 亿 U，历史 394 笔
        popup_eligible 全为 0）。近 24h 23 笔 BSC 买单：试探单 $57~200、
        标准档 0.5 BNB ≈ $286~291（9 笔）、加大仓 $400~1,163（5 笔）。
        阈值先定 300U，用户当天改口「改成 200」→ ≈13 条/天。
        三条链本来都在轮询，升级后保持不变。
        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = SMM.SmartMoneyMonitor(Path(tmp.name) / "seed.sqlite", seed_defaults=True)
        self.addCleanup(monitor.close)
        address = SMM.QUKUAI_RIJI_WALLET
        self.assertNotIn(address, SMM.TRACKED_KOL_AUX_WALLETS)
        self.assertEqual(SMM.KOL_SYNC_TRACK_LABELS[address], "区块日记")
        rows = [row for row in monitor.store.list_wallets()
                if (row["address"] or "").lower() == address]
        self.assertEqual(sorted(row["chain"] for row in rows), ["base", "bsc", "ethereum"])
        for row in rows:
            self.assertEqual(row["nickname"], "区块日记")
            self.assertEqual(row["alertThresholdUsd"], 200.0)
            self.assertTrue(row["enabled"])


class NamedKolSeedTests(unittest.TestCase):
    """用户点名的 KOL（不在那份「只记录」清单里，阈值按真实成交定）。"""

    def _wallet_rows(self, address: str) -> list[dict]:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = SMM.SmartMoneyMonitor(Path(tmp.name) / "seed.sqlite", seed_defaults=True)
        self.addCleanup(monitor.close)
        return [row for row in monitor.store.list_wallets()
                if (row["address"] or "").lower() == address.lower()]

    def test_leslie_is_monitored_on_three_chains_at_150u(self):
        """用户 2026-11-11：「0x7942…1f87，kol leslie的」。

        GMGN 实测：BSC 走小单高频（20.9 小时 117 笔，中位 $22，最大 $331，
        $84~179 是空档），ETH 才是真实体量（中位 $1,313，但 10-06 后没动）。
        阈值 150U 卡在 BSC 的双峰断点上；ETH/Base 只挂不常动、成本可以忽略。
        """
        rows = self._wallet_rows(SMM.LESLIE_WALLET)
        self.assertEqual(sorted(row["chain"] for row in rows),
                         ["base", "bsc", "ethereum"])
        for row in rows:
            with self.subTest(chain=row["chain"]):
                self.assertEqual(row["nickname"], "leslie")
                self.assertEqual(row["alertThresholdUsd"], 150.0)
                self.assertTrue(row["enabled"])
        # 同车提醒的观察面是「用户 10-27 给的 20 个追踪钱包」，leslie 不进去。
        self.assertNotIn(SMM.LESLIE_WALLET, SMM.KOL_SYNC_TRACK_LABELS)
        self.assertGreaterEqual(len(SMM.KOL_SYNC_TRACK_LABELS), 20)


    def test_kuzuo_is_promoted_on_three_chains_at_400u(self):
        """用户 2026-11-11：「0x2ce9…3373，KOL枯坐P小将」。

        库里 57 笔 BSC 买单：0.69 BNB（≈$470）是标准仓（40 笔），1.3/2 BNB 是加仓，
        0.15~0.5 BNB 是试水。阈值 400U 让标准仓全弹、试水单不弹（≈3.3 条/天）；
        定 500U 会把 40 笔标准仓全挡掉，只剩每天 0.6 条。
        ETH/Base 各有 5 笔真实买入，所以三条链都留。
        """
        rows = self._wallet_rows(SMM.KUZUO_WALLET)
        self.assertEqual(sorted(row["chain"] for row in rows),
                         ["base", "bsc", "ethereum"])
        for row in rows:
            with self.subTest(chain=row["chain"]):
                self.assertEqual(row["nickname"], "枯坐P小将")
                self.assertEqual(row["alertThresholdUsd"], 400.0)
                self.assertTrue(row["enabled"])
        self.assertNotIn(SMM.KUZUO_WALLET, SMM.TRACKED_KOL_AUX_WALLETS)
        self.assertEqual(SMM.KOL_SYNC_TRACK_LABELS[SMM.KUZUO_WALLET], "枯坐P小将")
        self.assertGreaterEqual(len(SMM.KOL_SYNC_TRACK_LABELS), 20)

    def test_jingou_is_monitored_on_solana_only_at_300u(self):
        """用户 2026-11-12：「KOL金狗挖掘机的SOL链地址」。

        第一条纯 Solana 的 KOL 点名钱包 —— chains 必须显式只写 solana，
        不写会按 EVM_CHAINS 铺 4 条，base58 地址在 EVM 上不存在、白烧扫描窗口。
        GMGN 实测 8.23 天 66 笔买：脏单 $8~9×22、试水 $79~99×5、
        标准仓 $244~343×35、重仓 $1,730/1,799×2；阈值 300U ≈ 2.3 条/天。
        """
        rows = self._wallet_rows(SMM.JINGOU_WALLET)
        self.assertEqual([row["chain"] for row in rows], ["solana"])
        row = rows[0]
        self.assertEqual(row["nickname"], "金狗挖掘机")
        self.assertEqual(row["alertThresholdUsd"], 300.0)
        self.assertTrue(row["enabled"])
        # 没被顺手加进「同车」集合，观察面仍是 20 人。
        self.assertNotIn(SMM.JINGOU_WALLET, SMM.KOL_SYNC_TRACK_LABELS)
        self.assertGreaterEqual(len(SMM.KOL_SYNC_TRACK_LABELS), 20)

    def test_k99_is_monitored_on_solana_only_at_50u(self):
        """用户 2026-11-12：「K99 | 1000X GEM，SOL链的地址」。

        撒网型：235 天 120 笔，长期单笔 $8~20；11-12 当天突然密集建仓
        （18 笔买 / 中位 $85 / p75 $95 / 最大 $170，基本 0.5 SOL 一档）。
        阈值 50U 卡在「试水 $8~20」与「0.5 SOL 真仓」之间。
        """
        rows = self._wallet_rows(SMM.K99_GEM_WALLET)
        self.assertEqual([row["chain"] for row in rows], ["solana"])
        row = rows[0]
        self.assertEqual(row["nickname"], "K99 | 1000X GEM")
        self.assertEqual(row["alertThresholdUsd"], 50.0)
        self.assertTrue(row["enabled"])

    def test_latuche_is_monitored_on_solana_only_at_500u(self):
        """用户 2026-11-12：「GJA1HEb…SU65，kol：Latuche，sol链的」。

        高频单币加仓型：120 笔只覆盖 3.35 小时（23 笔买/小时），3.35 小时
        砸 $23,856、75% 压在同一个币上。单笔只有 $273 与 $846 两档，
        阈值 500U 卡在 $275 → $846 的空档。
        """
        rows = self._wallet_rows(SMM.LATUCHE_WALLET)
        self.assertEqual([row["chain"] for row in rows], ["solana"])
        row = rows[0]
        self.assertEqual(row["nickname"], "Latuche")
        self.assertEqual(row["alertThresholdUsd"], 500.0)
        self.assertTrue(row["enabled"])

    def test_yukaz_is_monitored_on_bsc_only_at_500u(self):
        """用户 2026-11-12：「0xa05ec35f…742f，bsc链的，kol是yukaz」。

        机器化高频：100 笔 / 1.07 天（买 63，≈59 笔/天）。$7 扫链单 38 笔；
        0.5 BNB（$400~499）标准仓 22 笔却散在 22 个币上；$500 以上只有 3 笔。
        阈值 500U → 合并后 1.9 张/天；降到 400U 会变成 20.6 张/天。
        """
        rows = self._wallet_rows(SMM.YUKAZ_WALLET)
        self.assertEqual([row["chain"] for row in rows], ["bsc"])
        row = rows[0]
        self.assertEqual(row["nickname"], "yukaz")
        self.assertEqual(row["alertThresholdUsd"], 500.0)
        self.assertTrue(row["enabled"])

    def test_cowboy_solana_wallet_is_separate_from_his_evm_chains(self):
        """用户 2026-11-12：「cowboy的sol链买入也监控上」，阈值先 200U 后改 300U。

        同一个人两套链，阈值现在**故意不同**（2026-11-15 17:30 按各自实测重定）：
        Solana 侧 250U（200U 会把弹窗从 1 张/天推到 5 张/天，预算吃不消）；
        EVM 三条链 200U（他 BSC 的日常仓位就在 300~490U，降到 200U 只多 0.4 张/天）。
        「每日首单」上线后，弹窗数由标的数决定，所以 EVM 侧可以比 Solana 更敏感。
        """
        sol = self._wallet_rows(SMM.COWBOY_SOL_WALLET)
        self.assertEqual([row["chain"] for row in sol], ["solana"])
        self.assertEqual(sol[0]["nickname"], "cowboy")
        self.assertEqual(sol[0]["alertThresholdUsd"], 250.0)
        self.assertTrue(sol[0]["enabled"])

        evm = [row for row in self._wallet_rows("0xe1e3252b8b2f9bf2a8335389ba8ee62e6ea29407")
               if row["nickname"] == "cowboy"]
        self.assertEqual(sorted(row["chain"] for row in evm), ["base", "bsc", "ethereum"])
        for row in evm:
            with self.subTest(chain=row["chain"]):
                self.assertEqual(row["alertThresholdUsd"], 200.0)

    def test_cryptocharming_solana_wallets_are_solana_only_at_100u(self):
        """用户 2026-11-12 提供 GMGN profile 上的两个 Solana 公开钱包。

        大仓那支 25 天 47 条、买入中位 $1,973（0.2 张/天）；小仓那支 26 天 60 条、
        买入中位 $100（0.6 张/天）。阈值沿用他 EVM 那五条链的 100U，
        两支合计约 0.8 张/天。
        """
        for address in (
            "HyYvwH1bU8qb82wbhZvsgyVmjJroAv2W8qVyhccWj79P",
            "4X43DmYRtzLcyv2YRhC8vaTeqB5BTL6jX7LDtzfz47aC",
        ):
            with self.subTest(address=address[:10]):
                rows = self._wallet_rows(address)
                # 纯 Solana 钱包必须只挂 solana —— 缺省会铺四条 EVM 链，白烧扫描窗口。
                self.assertEqual([row["chain"] for row in rows], ["solana"])
                self.assertEqual(rows[0]["nickname"], "CryptoCharming")
                # 这两支是独立钱包（不同地址），阈值仍按各自实测：大仓那支 100U。
                self.assertEqual(rows[0]["alertThresholdUsd"], 100.0)
                self.assertTrue(rows[0]["enabled"])
                # 同车观察面要认得这两支，否则「三支钱包同时上车」数不出来。
                self.assertIn(address, SMM.KOL_SYNC_TRACK_LABELS)


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
