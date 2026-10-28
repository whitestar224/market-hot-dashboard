"""地址集群（Arkham）的聚类与跨标的判定。

这个子板块最容易错的五件事，每条都有对应用例：

1. **交易所不是控盘方**：CEX / DEX / 托管 / 跨链桥持有代币是业务需要，算进控盘
   会天天误报。必须是 venue 类实体一律剔除，但做市商、基金、KOL 要留下。
2. **实体要合并**：同一实体的多个地址 / 多条链占比相加，否则「一个人拆 5 个钱包」
   就永远过不了 5% 门槛。
3. **跨标的按标的数、不按链数**：同一标的两条链各有一条持仓记录，那是**一个标的**。
4. **抓取失败不许清零**：某轮失败只能记错误，不能把该标的已有持仓删掉 ——
   删了就等于「这个人突然清仓了」。
5. **只数榜上的标的**：库里的持仓是历史累积的，标的掉榜之后行还在。拿掉榜的行
   凑「跨 2 标的」，等于把「已经没人看的币」当成新信号（用户 2026-10-22 就是被
   这条误导的：``SHIB 6.5%`` 是 6 小时前扫的，早就不在榜上）。
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

import arkham_cluster_monitor as acm

ROOT = Path(__file__).resolve().parents[1]


def holder(address, pct, *, chain="bsc", entity=None, etype="", name="", label="", usd=None):
    return {
        "chain": chain,
        "address": address,
        "entityId": (entity or {}).get("id", "") if isinstance(entity, dict) else (entity or ""),
        "entityName": (entity or {}).get("name", "") if isinstance(entity, dict) else (name or entity or ""),
        "entityType": (entity or {}).get("type", "") if isinstance(entity, dict) else etype,
        "label": label,
        "pct": pct,
        "usd": usd if usd is not None else pct * 1000,
        "balance": pct,
    }


class BuildClustersTest(unittest.TestCase):
    def test_venue_entities_are_dropped(self):
        rows = [
            holder("0xB1", 3.89, etype="cex", entity="bitget"),
            holder("0xB2", 2.00, etype="dex", entity="uniswap"),
            holder("0xB3", 1.50, etype="custodian", entity="cobo"),
            holder("0xB4", 1.20, etype="bridge", entity="arbitrum-bridge"),
            holder("0xB5", 0.90, etype="stablecoin", entity="tether"),
        ]
        self.assertEqual(acm.build_clusters(rows), [])

    def test_market_maker_and_fund_are_kept(self):
        rows = [
            holder("0xB1", 3.0, etype="fund", entity="cyber-fund"),
            holder("0xB2", 2.0, etype="market-maker", entity="wintermute"),
        ]
        ids = {item["clusterId"] for item in acm.build_clusters(rows)}
        self.assertIn("entity:cyber-fund", ids)
        self.assertIn("entity:wintermute", ids)

    def test_pool_entity_disguised_as_meme_is_dropped(self):
        """``pump.fun`` 的类型是 meme、label 是 PumpSwap Market —— 那是 AMM 池。

        只按类型剔 venue 会让它横跨 11 个标的、最高占 MUSK 99.8%，天天报「同一个
        实体在控十几个盘」。类型不可靠，名字必须是第二道闸。
        """
        rows = [
            holder("0xP1", 41.0, chain="solana", etype="meme", entity="pump-fun", label="PumpSwap Market"),
            holder("0xP2", 30.0, chain="solana", etype="misc", entity="binance-wallet", name="Binance Wallet"),
        ]
        self.assertEqual(acm.build_clusters(rows), [])

    def test_real_meme_team_and_market_maker_survive_the_name_gate(self):
        rows = [
            holder("0xT1", 71.0, chain="solana", etype="meme",
                   entity="official-trump-meme", name="Official Trump Meme", label="Team Allocation"),
            holder("0xW1", 9.0, etype="fund", entity="wintermute",
                   name="Wintermute", label="Market Maker"),
        ]
        ids = {item["clusterId"] for item in acm.build_clusters(rows)}
        self.assertEqual(ids, {"entity:official-trump-meme", "entity:wintermute"})

    def test_platform_account_named_pump_fun_is_dropped(self):
        """``pump.fun`` 这个**实体本身**是平台账本（真机上拿着 NIGEL 95%、PUMP 41%）。

        但 Arkham 把普通钱包标成 ``"昵称" on Pump.fun`` —— 那是真人地址，必须留下，
        所以这条模式是**整名锚定**的，不能搜子串。
        """
        rows = [
            holder("0xPF", 95.1, chain="solana", etype="meme", entity="pump-fun", name="pump.fun"),
            holder("0xW1", 9.0, chain="solana", etype="individual", entity="theveeman",
                   name="@theveeman", label='"radicalcrab5744" on Pump.fun'),
        ]
        ids = {item["clusterId"] for item in acm.build_clusters(rows)}
        self.assertEqual(ids, {"entity:theveeman"})

    def test_same_entity_addresses_are_merged(self):
        rows = [
            holder("0xB1", 2.5, entity="wintermute"),
            holder("0xB2", 1.5, entity="wintermute"),
            holder("0xB3", 1.0, chain="ethereum", entity="wintermute"),
        ]
        clusters = acm.build_clusters(rows)
        self.assertEqual(len(clusters), 1)
        self.assertAlmostEqual(clusters[0]["pct"], 5.0, places=6)
        self.assertEqual(clusters[0]["addressCount"], 3)
        self.assertEqual(clusters[0]["kind"], "entity")

    def test_entity_below_floor_is_dropped(self):
        clusters = acm.build_clusters([holder("0xB1", 0.2, entity="tiny-fund")])
        self.assertEqual(clusters, [])

    def test_unlabelled_addresses_become_their_own_cluster(self):
        clusters = acm.build_clusters([
            holder("0xAAA", 26.29, chain="ethereum", label="Gnosis Safe Proxy"),
            holder("0xBBB", 21.34, chain="ethereum", label="Gnosis Safe Proxy"),
        ])
        self.assertEqual(len(clusters), 2)
        self.assertTrue(all(item["kind"] == "address" for item in clusters))
        self.assertEqual({item["clusterId"] for item in clusters}, {
            "addr:ethereum:0xaaa", "addr:ethereum:0xbbb",
        })

    def test_small_unlabelled_address_is_dropped(self):
        clusters = acm.build_clusters([holder("0xCCC", 0.4)])
        self.assertEqual(clusters, [])

    def test_burn_addresses_are_never_holders(self):
        clusters = acm.build_clusters([
            holder("0x000000000000000000000000000000000000dead", 50.0),
            holder("0x0000000000000000000000000000000000000000", 30.0),
        ])
        self.assertEqual(clusters, [])

    def test_sorted_by_share_desc(self):
        clusters = acm.build_clusters([
            holder("0xA1", 6.0),
            holder("0xA2", 26.0),
            holder("0xA3", 12.0),
        ])
        self.assertEqual([round(item["pct"]) for item in clusters], [26, 12, 6])


class _Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.store = acm.ArkhamClusterStore(os.path.join(self._dir.name, "c.sqlite"))
        self.monitor = acm.ArkhamClusterMonitor(self.store, interval=600)
        self.events = []
        self.monitor.set_alert_callback(lambda event: self.events.append(event))

    def tearDown(self):
        self._dir.cleanup()

    def record(self, symbol, pct, *, chain="bsc", contract=None, cluster="addr:bsc:0xsame"):
        contract = contract or f"0x{symbol.lower()}"
        clusters = [{
            "clusterId": cluster, "kind": "address", "name": "Safe",
            "clusterType": "", "chain": chain, "address": "0xsame", "label": "Safe",
            "pct": pct, "usd": pct * 1000, "balance": 1.0,
            "addressCount": 1, "topAddress": "0xsame", "topPct": pct,
        }]
        self.store.record_token(
            token_key=acm.token_key_of(chain, contract),
            symbol=symbol, name=symbol, chain=chain, contract=contract,
            pricing_id=f"pid-{symbol.lower()}",
            url=f"https://arkm.com/explorer/token/pid-{symbol.lower()}",
            sources=["gainer"], clusters=clusters,
        )
        return acm.token_key_of(chain, contract)


class CrossTokenTest(_Base):
    def test_two_tokens_above_threshold_make_one_cluster(self):
        self.record("JCT", 26.0)
        self.record("CAKE", 7.0)
        clusters = self.store.cross_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["tokenCount"], 2)
        self.assertAlmostEqual(clusters[0]["maxPct"], 26.0, places=6)

    def test_second_token_below_threshold_does_not_count(self):
        self.record("JCT", 26.0)
        self.record("CAKE", 4.9)
        self.assertEqual(self.store.cross_clusters(), [])

    def test_threshold_is_read_from_env(self):
        os.environ["XINGYUN_ARKHAM_CLUSTER_SIGNIFICANT_PCT"] = "3"
        try:
            self.record("JCT", 6.0)
            self.record("CAKE", 4.0)
            self.assertEqual(len(self.store.cross_clusters()), 1)
        finally:
            os.environ.pop("XINGYUN_ARKHAM_CLUSTER_SIGNIFICANT_PCT", None)

    def test_same_symbol_on_two_chains_counts_once(self):
        """同一标的两条链登记 → 仍然只是一个标的，不能凑成「跨 2 标的」。"""
        self.record("JCT", 26.0, chain="bsc", contract="0xjct")
        self.record("JCT", 22.0, chain="ethereum", contract="0xjct")
        self.assertEqual(self.store.cross_clusters(), [])

    def test_failed_fetch_keeps_previous_holdings(self):
        self.record("JCT", 26.0)
        self.record("CAKE", 7.0)
        self.assertEqual(len(self.store.cross_clusters()), 1)
        # 同一标的这一轮抓取失败：只记错误，持仓不能被清掉。
        self.store.record_token(
            token_key=acm.token_key_of("bsc", "0xjct"),
            symbol="JCT", name="JCT", chain="bsc", contract="0xjct",
            pricing_id="", url="", sources=["move"], clusters=[],
            error="Arkham 限流或服务异常（HTTP 429）",
        )
        self.assertEqual(len(self.store.cross_clusters()), 1)
        tokens = {row["token_key"]: row for row in self.store.board()["tokens"]}
        self.assertIn("429", tokens[acm.token_key_of("bsc", "0xjct")]["last_error"])


class DispatchTest(_Base):
    def setUp(self):
        super().setUp()
        self.record("JCT", 26.0)
        self.record("CAKE", 7.0)

    def test_disabled_switch_sends_nothing(self):
        self.store.set_alerts_enabled(False)
        self.assertEqual(self.monitor.dispatch_alerts(), 0)
        self.assertEqual(self.events, [])

    def test_sends_once_then_respects_cooldown(self):
        self.assertEqual(self.monitor.dispatch_alerts(), 1)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["kind"], "arkham-cluster")
        self.assertEqual(self.events[0]["key"], "arkham-cluster:addr:bsc:0xsame")
        # 冷却期内再跑一轮不应该重复弹。
        self.assertEqual(self.monitor.dispatch_alerts(), 0)
        self.assertEqual(len(self.events), 1)

    def test_event_carries_both_tokens(self):
        self.monitor.dispatch_alerts()
        symbols = {row["symbol"] for row in self.events[0]["tokens"]}
        self.assertEqual(symbols, {"JCT", "CAKE"})

    def test_failed_emit_is_not_recorded_as_alerted(self):
        def boom(_event):
            raise RuntimeError("desktop alert failed")

        self.monitor.set_alert_callback(boom)
        self.assertEqual(self.monitor.dispatch_alerts(), 0)
        # 没弹出去就不该记账，否则这个集群会被静默 6 小时。
        self.assertEqual(self.store.alert_at("addr:bsc:0xsame"), 0)
        self.monitor.set_alert_callback(lambda event: self.events.append(event))
        self.assertEqual(self.monitor.dispatch_alerts(), 1)

    def test_alerts_are_capped_per_cycle(self):
        for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE"):
            self.record(symbol, 9.0)
        self.assertLessEqual(self.monitor.dispatch_alerts(), acm.MAX_ALERTS_PER_CYCLE)


class CycleTest(_Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.monitor.set_candidate_provider(lambda: [
            {"symbol": "JCT", "name": "Janction", "chain": "bsc", "contract": "0xjct", "sources": ["move"]},
            {"symbol": "NOCHAIN", "name": "", "chain": "", "contract": "", "sources": ["gainer"]},
            {"symbol": "OFFCHAIN", "name": "", "chain": "某条链", "contract": "0xzz", "sources": ["hot"]},
        ])

    def _patch_fetch(self, result):
        original = acm.ARKHAM.fetch_token_holders
        acm.ARKHAM.fetch_token_holders = lambda chain, contract, **kw: (self.calls.append((chain, contract)), result)[1]

        def restore():
            acm.ARKHAM.fetch_token_holders = original

        self.addCleanup(restore)

    def test_only_onchain_candidates_are_fetched(self):
        self._patch_fetch({
            "identity": {"pricingId": "jct", "symbol": "JCT", "name": "Janction"},
            "holders": [holder("0xA", 26.0, label="Safe")],
            "reason": "",
        })
        result = self.monitor.run_cycle()
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0], ("bsc", "0xjct"))

    def test_second_cycle_reuses_cache_within_ttl(self):
        self._patch_fetch({
            "identity": {"pricingId": "jct"},
            "holders": [holder("0xA", 26.0, label="Safe")],
            "reason": "",
        })
        self.monitor.run_cycle()
        second = self.monitor.run_cycle()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second["skipped"], 1)

    def test_auth_error_propagates_and_stops_the_cycle(self):
        original = acm.ARKHAM.fetch_token_holders

        def boom(chain, contract, **kw):
            raise acm.ARKHAM.ArkhamAuthError("Arkham 签名被拒（HTTP 401）", status=401)

        acm.ARKHAM.fetch_token_holders = boom
        self.addCleanup(lambda: setattr(acm.ARKHAM, "fetch_token_holders", original))
        with self.assertRaises(acm.ARKHAM.ArkhamAuthError):
            self.monitor.run_cycle()
        self.assertIn("签名被拒", self.monitor.last_error)

    def test_empty_holders_is_recorded_as_error_not_empty_cluster(self):
        self._patch_fetch({"identity": {}, "holders": [], "reason": "Arkham 没有该标的的持币人数据"})
        self.monitor.run_cycle()
        board = self.store.board()
        self.assertEqual(board["clusters"], [])
        self.assertIn("没有该标的", board["tokens"][0]["last_error"])


class PayloadTest(_Base):
    def test_clone_does_not_leak_writes_back_into_cache(self):
        self.record("JCT", 26.0)
        self.record("CAKE", 7.0)
        first = self.monitor.payload(force_refresh=True)
        first["clusters"][0]["tokens"][0]["url"] = "MUTATED"
        first["clusters"][0]["name"] = "MUTATED"
        second = self.monitor.payload()
        self.assertNotEqual(second["clusters"][0]["name"], "MUTATED")
        self.assertNotEqual(second["clusters"][0]["tokens"][0]["url"], "MUTATED")

    def test_summary_exposes_thresholds(self):
        summary = self.monitor.payload(force_refresh=True)["summary"]
        self.assertEqual(summary["significantPct"], acm.significant_pct())
        self.assertEqual(summary["crossMinTokens"], acm.cross_min_tokens())


class TokenKeyTest(_Base):
    """同一个币从不同来源进来，必须是**同一个键**。

    链值有三套写法：价格异动 ``solana`` / 币安钱包 ``CT_501`` / 交易所 ``56``。
    按原始文本拼键的话同一个币会记成两条，``tokenTotal`` 虚高、板块里出现两次。
    """

    def test_chain_code_and_label_make_the_same_key(self):
        keys = {acm.token_key_of(raw, "0xABC") for raw in ("solana", "sol", "Solana", "CT_501", "501")}
        self.assertEqual(keys, {"solana:0xabc"})

    def test_contract_case_is_folded(self):
        self.assertEqual(acm.token_key_of("bsc", "0xAbC"), acm.token_key_of("56", "0xabc"))

    def test_same_token_recorded_twice_stays_one_row(self):
        self.record("PUMP", 41.0, chain="CT_501", contract="pumpCmXq")
        self.record("PUMP", 41.0, chain="solana", contract="pumpCmXq")
        board = self.store.board()
        self.assertEqual(len(board["tokens"]), 1)
        self.assertEqual(board["summary"]["tokenTotal"], 1)


class TokenKeyMigrationTest(unittest.TestCase):
    """旧库里按裸链号写的行必须被清掉。

    它永远不会再被更新（新轮次写 ``solana:<ca>``），却会一直挂在榜上，
    和正确那行并排显示成两个标的。
    """

    def test_stale_keys_are_dropped_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.sqlite")
            store = acm.ArkhamClusterStore(path)
            store.record_token(
                token_key=acm.token_key_of("solana", "pumpCmXq"),
                symbol="PUMP", name="PUMP", chain="solana", contract="pumpCmXq",
                pricing_id="pid-pump", url="", sources=["hot"],
                clusters=[{"clusterId": "addr:solana:x", "kind": "address", "name": "x",
                           "clusterType": "", "chain": "solana", "address": "x", "label": "",
                           "pct": 9.0, "usd": 1.0, "balance": 1.0, "addressCount": 1,
                           "topAddress": "x", "topPct": 9.0}],
            )
            # 手工塞一条上一代口径写下的僵尸行。
            import sqlite3
            conn = sqlite3.connect(path)
            conn.execute(
                "INSERT INTO ac_tokens (token_key, symbol, chain, contract) VALUES (?,?,?,?)",
                ("ct_501:pumpcmxq", "PUMP", "CT_501", "pumpCmXq"),
            )
            conn.execute(
                "INSERT INTO ac_holdings (token_key, cluster_id, symbol, chain, contract)"
                " VALUES (?,?,?,?,?)",
                ("ct_501:pumpcmxq", "addr:solana:x", "PUMP", "CT_501", "pumpCmXq"),
            )
            conn.commit()
            conn.close()

            reopened = acm.ArkhamClusterStore(path)
            board = reopened.board()
            # board()["tokens"] 是原始库行（前端按 row.top_pct 这类蛇形名取值）。
            self.assertEqual([row["token_key"] for row in board["tokens"]], ["solana:pumpcmxq"])
            self.assertEqual(board["summary"]["tokenTotal"], 1)
            # 僵尸持仓也得跟着删，否则「已存持仓」数会对不上。
            self.assertEqual(board["summary"]["holdingTotal"], 1)


class BoardScopeTest(_Base):
    """跨标的判定只数**榜上**的标的（用户 2026-10-22）。

    现场记录：弹了一条「0x2207…51D8 在 2 个标的占流通 ≥5%：SHIB 6.5%，STA 6.1%」。
    SHIB 那行是 6 小时前扫的、早就掉出热门榜，却和一个在榜标的凑成了「跨 2 标的」。
    """

    def setUp(self):
        super().setUp()
        # 同一个匿名地址簇同时出现在三个标的里：SHIB 已经掉榜，另外两个在榜上。
        self.record("SHIB", 6.5, chain="bsc", contract="0xshib")
        self.record("STA", 6.1, chain="bsc", contract="0xsta")
        self.record("CAKE", 7.0, chain="bsc", contract="0xcake")
        self.shib = acm.token_key_of("bsc", "0xshib")
        self.sta = acm.token_key_of("bsc", "0xsta")
        self.cake = acm.token_key_of("bsc", "0xcake")

    def _feed(self, symbols):
        self.monitor.set_candidate_provider(lambda: [
            {"symbol": symbol, "chain": "bsc", "contract": f"0x{symbol.lower()}"}
            for symbol in symbols
        ])
        return self.monitor._candidates()  # noqa: SLF001 - 只算候选，不发请求

    def test_unknown_scope_does_not_suppress(self):
        """provider 还没跑过时不做收窄 —— 否则刚起来那几十秒该弹的也压掉了。"""
        self.assertIsNone(self.monitor.board_scope())
        self.assertEqual(self.monitor.dispatch_alerts(), 1)
        self.assertEqual(len(self.events[0]["tokens"]), 3)

    def test_a_token_that_left_the_board_is_not_counted(self):
        self._feed(["STA", "CAKE"])
        self.assertEqual(self.monitor.dispatch_alerts(), 1)
        symbols = {row["symbol"] for row in self.events[0]["tokens"]}
        self.assertEqual(symbols, {"STA", "CAKE"})
        self.assertNotIn("SHIB", symbols)

    def test_one_board_token_alone_is_not_a_cross(self):
        """只剩一个在榜标的时不该弹 —— 另一个是掉榜的 SHIB，不算「跨 2 标的」。"""
        self._feed(["STA"])
        self.assertEqual(self.monitor.dispatch_alerts(), 0)
        self.assertEqual(self.events, [])

    def test_nothing_on_the_board_means_no_alert_at_all(self):
        """榜源给了空列表就是「榜上没标的」，不是「放宽成全库」。"""
        self._feed([])
        self.assertEqual(self.monitor.board_scope(), set())
        self.assertEqual(self.monitor.dispatch_alerts(), 0)

    def test_board_scope_covers_tokens_beyond_the_fetch_quota(self):
        """本轮配额只够查一个，另一个仍然是榜上标的 —— 口径不能随配额抖动。"""
        self.monitor.max_candidates = 1
        picked = self._feed(["STA", "CAKE"])
        self.assertEqual([row["symbol"] for row in picked], ["STA"])
        self.assertEqual(self.monitor.board_scope(), {self.sta, self.cake})

    def test_the_fetch_quota_rotates_through_the_whole_board(self):
        """候选池比单轮配额大时必须换起点。

        2026-10-22 剔掉战壕新币流之后配额才真正紧张：行情榜总共约 80–100 个标的、
        单轮只查 40 个。固定从头取的话，后半截永远不会被采样 —— 板块里会长期躺着
        一堆「0 个集群」的行，看着像没人控盘。
        """
        self.monitor.max_candidates = 2
        first = [row["symbol"] for row in self._feed(["STA", "CAKE", "DINO"])]
        second = [row["symbol"] for row in self._feed(["STA", "CAKE", "DINO"])]
        self.assertEqual(first, ["STA", "CAKE"])
        self.assertEqual(second, ["CAKE", "DINO"])
        # 没被查的那几个仍然是「榜上」，跨标的判定照样认。
        self.assertEqual(
            self.monitor.board_scope(),
            {self.sta, self.cake, acm.token_key_of("bsc", "0xdino")},
        )

    def test_a_broken_provider_keeps_the_previous_scope(self):
        self._feed(["STA", "CAKE"])

        def boom():
            raise RuntimeError("榜单接口挂了")

        self.monitor.set_candidate_provider(boom)
        self.assertEqual(self.monitor._candidates(), [])  # noqa: SLF001
        self.assertEqual(self.monitor.board_scope(), {self.sta, self.cake})
        self.assertIn("候选标的获取失败", self.monitor.last_error)

    def test_the_panel_list_uses_the_same_scope(self):
        """板块上列出的「跨标的」必须是会弹的那几条，不能只是历史库里的一堆。"""
        self._feed(["STA", "CAKE"])
        payload = self.monitor.payload(force_refresh=True)
        symbols = {row["symbol"] for row in payload["clusters"][0]["tokens"]}
        self.assertEqual(symbols, {"STA", "CAKE"})
        self.assertEqual(payload["summary"]["boardTokenTotal"], 2)
        # 已查询标的列表是研究视图：掉榜的也留着，别跟着收窄。
        self.assertIn("SHIB", {row["symbol"] for row in payload["tokens"]})


class CycleScopeTest(_Base):
    """跑完一整轮（含抓取）之后口径也必须是榜上的。"""

    def setUp(self):
        super().setUp()
        self.record("SHIB", 6.5, chain="bsc", contract="0xshib")

    def test_a_cycle_only_fills_the_scope_from_the_boards(self):
        original = acm.ARKHAM.fetch_token_holders
        acm.ARKHAM.fetch_token_holders = lambda chain, contract, **kw: {
            "identity": {"pricingId": "sta"}, "holders": [holder("0xA", 9.0, label="Safe")],
            "reason": "",
        }
        self.addCleanup(lambda: setattr(acm.ARKHAM, "fetch_token_holders", original))
        self.monitor.set_candidate_provider(lambda: [
            {"symbol": "STA", "chain": "bsc", "contract": "0xsta"},
        ])
        self.monitor.run_cycle()
        self.assertEqual(self.monitor.board_scope(), {acm.token_key_of("bsc", "0xsta")})
        # SHIB 的持仓还在库里，但不在榜上 → 不构成跨标的。
        self.assertEqual(self.monitor.dispatch_alerts(), 0)
        self.assertEqual(self.events, [])


class CandidateSourceTest(unittest.TestCase):
    """候选只喂榜上的标的（用户 2026-10-22：「不是榜上的标的，不做这个地址集群提醒」）。

    价格异动自 2026-10-21 起只收币安 U 本位合约，本身没有链上合约可查；更糟的是
    它会把「不在任何榜上、只是刚涨了 20%」的币捞进来 —— 正是用户否掉的东西。
    """

    @classmethod
    def setUpClass(cls):
        cls.source = ROOT.joinpath("server.py").read_text(encoding="utf-8")

    def _body(self, name):
        start = self.source.index(f"def {name}(")
        end = self.source.index("\ndef ", start + 1)
        return self.source[start:end]

    def test_price_moves_are_not_candidates(self):
        body = self._body("arkham_cluster_candidates")
        # 文档字符串里解释「为什么剔掉价格异动」是可以的，但代码里不许再接这条腿。
        code = body.split('"""')[-1]
        self.assertNotIn("PRICE_MOVE_MONITOR", code)
        self.assertNotIn('"move"', code)

    def test_the_trench_new_coin_feed_is_not_a_board(self):
        """战壕新币榜是「新币流」不是「榜」（用户 2026-10-22 第二次截图）。

        现场：``DINO 6.0%，GUARDDOG 5.2%`` —— 两个都只来自 ``gmgn-trenches``
        （300 行、按开盘时间倒序、币的寿命十几分钟），用户回头看页面上根本没有它们。
        """
        match = re.search(
            r"ARKHAM_CLUSTER_BOARD_SKIP_SOURCES\s*=\s*frozenset\(\{([^}]*)\}\)",
            self.source,
        )
        self.assertIsNotNone(match)
        self.assertIn("gmgn-trenches", match.group(1))

    def test_hot_rows_are_taken_per_source_card(self):
        """必须**逐张卡**取行：先拍平成一个列表就分不清行来自哪张卡，剔不掉战壕那张。"""
        code = self._body("arkham_cluster_candidates").split('"""')[-1]
        self.assertIn('source.get("id")', code)
        self.assertIn("ARKHAM_CLUSTER_BOARD_SKIP_SOURCES", code)
        self.assertIn("ARKHAM_CLUSTER_BOARD_ROWS_PER_SOURCE", code)
        self.assertNotIn("rank_monitor_market_rows(", code)

    def test_the_two_boards_are_still_the_sources(self):
        body = self._body("arkham_cluster_candidates")
        self.assertIn('add(snapshot, "gainer")', body)
        self.assertIn('add(snapshot, "hot")', body)

    def test_the_popup_copy_says_board_only(self):
        body = self._body("send_arkham_cluster_desktop_alert")
        self.assertIn("榜上标的", body)


class PurgeVenueTest(_Base):
    """规则收紧后，库里按旧规则存下的池子集群必须被清掉。

    历史持仓行只有 ``cluster_id``，没有 name/label —— 判断依据是 ``ac_clusters``
    那一行。不清的话它会一直挂在榜上，直到那个标的碰巧被重新采到。
    """

    def _record_pair(self):
        clusters = [
            {"clusterId": "entity:pump-fun", "kind": "entity", "name": "pump.fun",
             "clusterType": "meme", "chain": "solana", "address": "",
             "label": "PumpSwap Market", "pct": 99.7, "usd": 1.0, "balance": 1.0,
             "addressCount": 1, "topAddress": "0xP", "topPct": 99.7},
            {"clusterId": "entity:official-trump-meme", "kind": "entity",
             "name": "Official Trump Meme", "clusterType": "meme", "chain": "solana",
             "address": "", "label": "Team Allocation", "pct": 71.8, "usd": 1.0,
             "balance": 1.0, "addressCount": 1, "topAddress": "0xT", "topPct": 71.8},
        ]
        self.store.record_token(
            token_key=acm.token_key_of("solana", "MUSK"), symbol="MUSK", name="MUSK",
            chain="solana", contract="MUSK", pricing_id="pid-musk", url="",
            sources=["hot"], clusters=clusters,
        )

    def test_pool_cluster_is_purged_and_real_team_survives(self):
        self._record_pair()
        self.assertEqual(self.store.purge_venue_clusters(), 1)
        remaining = {row["clusterId"] for row in self.store.board()["clusters"]} or set()
        self.assertNotIn("entity:pump-fun", remaining)
        with self.store._session() as conn:  # noqa: SLF001 - 故意查底层台账
            ids = {row["cluster_id"] for row in conn.execute("SELECT cluster_id FROM ac_clusters")}
            held = {row["cluster_id"] for row in conn.execute("SELECT cluster_id FROM ac_holdings")}
        self.assertEqual(ids, {"entity:official-trump-meme"})
        self.assertEqual(held, {"entity:official-trump-meme"})

    def test_purge_is_idempotent(self):
        self._record_pair()
        self.store.purge_venue_clusters()
        self.assertEqual(self.store.purge_venue_clusters(), 0)

    def test_token_row_is_recomputed_after_purge(self):
        """标的行上「N 个集群 / 最大 X%」是入库时算的，删了持仓必须重算。

        不重算的话池子被剔掉之后，那个币还挂着「最大 99.8%」—— 看着像仍在被控盘。
        """
        self._record_pair()
        row = next(item for item in self.store.board()["tokens"] if item["symbol"] == "MUSK")
        self.assertEqual(row["cluster_count"], 2)
        self.assertGreater(row["top_pct"], 99)
        self.store.purge_venue_clusters()
        row = next(item for item in self.store.board()["tokens"] if item["symbol"] == "MUSK")
        self.assertEqual(row["cluster_count"], 1)
        self.assertAlmostEqual(row["top_pct"], 71.8, places=1)


if __name__ == "__main__":
    unittest.main()
