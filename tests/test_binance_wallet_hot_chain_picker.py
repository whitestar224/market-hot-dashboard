"""币安钱包热门榜的「链」下拉：行构造 + 按链取数 + chainBoards 装配。

与战壕榜不同，这里的链视图**不是把综合榜前十筛一遍**：上游 unified rank 接口的
``chainId`` 是服务端过滤，每条链返回自己独立的前 20 名，所以链板必须单独请求。
这里锁住那三层：共享的行构造器、单链请求、以及「一条链失败不能拖垮榜」。
"""

import unittest
from unittest import mock

import server


def token(symbol, chain_id="56", **overrides):
    raw = {
        "symbol": symbol,
        "name": f"{symbol} token",
        "chainId": chain_id,
        "contractAddress": f"0x{symbol.lower()}",
        "price": 1.23,
        "percentChange24h": 4.5,
        "volume24h": 1_000_000,
        "liquidity": 250_000,
        "marketCap": 9_000_000,
        "holders": 1200,
        "icon": "https://example.test/i.png",
    }
    raw.update(overrides)
    return raw


class BinanceWalletRowBuilderTests(unittest.TestCase):
    """综合榜与链板必须共用同一个行构造器，否则字段会漂移。"""

    def test_rows_carry_the_chain_label_from_the_shared_chain_meta(self):
        rows = server.binance_wallet_hot_rows_from_tokens([token("ABC", "56")], "24h")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["chain"], "56")
        self.assertEqual(row["chainLabel"], server.BINANCE_WALLET_CHAIN_META["56"][0])
        self.assertEqual(row["rank"], 1)
        self.assertIn("BSC", row["tags"])
        self.assertEqual(row["url"], server.binance_wallet_token_url("56", "0xabc"))

    def test_rank_is_positional_so_a_chain_board_starts_at_one(self):
        rows = server.binance_wallet_hot_rows_from_tokens(
            [token("A"), token("B"), token("C")], "24h"
        )
        self.assertEqual([row["rank"] for row in rows], [1, 2, 3])

    def test_the_board_is_capped_at_ten_rows(self):
        rows = server.binance_wallet_hot_rows_from_tokens(
            [token(f"T{i}") for i in range(25)], "24h"
        )
        self.assertEqual(len(rows), 10)

    def test_the_source_and_its_rows_agree_for_a_chain_payload(self):
        payload = {"data": {"tokens": [token("ONLY", "4663")]}}
        source = server.binance_wallet_hot_source_from_payload(payload, "4h")
        self.assertEqual([row["chainLabel"] for row in source["rows"]], ["Robinhood"])

    def test_malformed_tokens_are_tolerated(self):
        rows = server.binance_wallet_hot_rows_from_tokens(
            [None, "nope", {}, *[token(f"T{i}") for i in range(30)]], "24h"
        )
        self.assertEqual(len(rows), 10)


class BinanceWalletChainTableTests(unittest.TestCase):
    """BINANCE_WALLET_HOT_CHAINS 与 BINANCE_WALLET_CHAIN_META 是两张表，
    必须一致——行级标签取后者、下拉取前者，漂移了就会出现下拉写 Solana、
    行标签写 sol 这种错位。"""

    def test_every_picker_chain_matches_the_row_level_meta(self):
        for chain_id, code, label in server.BINANCE_WALLET_HOT_CHAINS:
            with self.subTest(chain=code):
                self.assertIn(chain_id, server.BINANCE_WALLET_CHAIN_META)
                meta_label, meta_code = server.BINANCE_WALLET_CHAIN_META[chain_id]
                self.assertEqual(label, meta_label)
                self.assertEqual(code, meta_code)

    def test_codes_are_unique_and_non_empty(self):
        codes = [code for _chain_id, code, _label in server.BINANCE_WALLET_HOT_CHAINS]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(codes))

    def test_every_chain_is_reachable_by_the_exchange_ai_chain_map(self):
        # 链视图的 ✦ 币安 AI 叙事 是客户端懒取的，接口侧靠这张映射表把
        # chainId 翻成币安链 id。缺一个，那条链的按钮就永远不出现。
        for chain_id, _code, label in server.BINANCE_WALLET_HOT_CHAINS:
            with self.subTest(chain=label):
                self.assertTrue(server.normalize_exchange_ai_binance_chain(chain_id), chain_id)


class BinanceWalletChainRequestTests(unittest.TestCase):
    def test_the_chain_id_is_only_sent_when_a_chain_was_asked_for(self):
        sent = []

        class FakeResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"success": True, "data": {"tokens": []}}

        def fake_post(url, json=None, headers=None, timeout=None):
            sent.append(dict(json or {}))
            return FakeResponse()

        with mock.patch.object(server.requests, "post", side_effect=fake_post):
            server.fetch_binance_wallet_rank_payload("24h")
            server.fetch_binance_wallet_rank_payload("24h", chain_id="56")

        self.assertNotIn("chainId", sent[0])
        self.assertEqual(sent[1]["chainId"], "56")
        # 周期在上游是整数枚举，不是字符串。
        self.assertEqual(sent[0]["period"], server.BINANCE_WALLET_HOT_PERIODS["24h"]["api"])

    def test_a_chain_payload_goes_through_the_shared_row_builder(self):
        calls = []

        def fake_payload(period, *, chain_id=None):
            calls.append((period, chain_id))
            return {"data": {"tokens": [token("CHAINED", chain_id)]}}

        with mock.patch.object(server, "fetch_binance_wallet_rank_payload", side_effect=fake_payload):
            rows = server.binance_wallet_chain_board_rows("24h", "8453")

        self.assertEqual(calls, [("24h", "8453")])
        self.assertEqual([row["chainLabel"] for row in rows], ["Base"])


class BinanceWalletChainBoardsTests(unittest.TestCase):
    @staticmethod
    def built_rows(chain_id):
        """``binance_wallet_chain_board_rows`` 返回的是**装配好的行**，不是原始 token。"""
        return server.binance_wallet_hot_rows_from_tokens(
            [token(f"T{chain_id}", chain_id)], "24h"
        )

    def _attach(self, board_rows, prior_boards=None):
        source = {"id": "binance-wallet-hot", "rows": [{"symbol": "GLOBAL"}]}
        with mock.patch.object(server, "binance_wallet_chain_board_rows", side_effect=board_rows):
            server.attach_binance_wallet_chain_boards(
                source, "24h", prior_boards=prior_boards
            )
        return source

    def test_options_start_with_all_and_follow_the_declared_order(self):
        source = self._attach(lambda period, chain_id: self.built_rows(chain_id))
        self.assertEqual(source["chainOptions"][0], {"value": "all", "label": "综合"})
        self.assertEqual(
            [option["value"] for option in source["chainOptions"]],
            ["all", *[code for _chain_id, code, _label in server.BINANCE_WALLET_HOT_CHAINS]],
        )

    def test_every_chain_gets_its_own_board_with_its_chain_id(self):
        source = self._attach(lambda period, chain_id: self.built_rows(chain_id))
        boards = {board["chain"]: board for board in source["chainBoards"]}
        self.assertEqual(len(boards), len(server.BINANCE_WALLET_HOT_CHAINS))
        for chain_id, code, label in server.BINANCE_WALLET_HOT_CHAINS:
            with self.subTest(chain=code):
                self.assertEqual(boards[code]["chainId"], chain_id)
                self.assertEqual(boards[code]["label"], label)
                self.assertEqual(boards[code]["status"], "ok")
                self.assertEqual(boards[code]["error"], "")
                self.assertEqual(boards[code]["rows"][0]["chain"], chain_id)
                self.assertEqual(boards[code]["rows"][0]["rank"], 1)

    def test_a_failing_chain_is_contained_and_marked_unavailable(self):
        def board_rows(period, chain_id):
            if chain_id == "56":
                raise RuntimeError("bsc 端点读超时")
            return self.built_rows(chain_id)

        source = self._attach(board_rows)
        boards = {board["chain"]: board for board in source["chainBoards"]}
        self.assertEqual(boards["bsc"]["status"], "unavailable")
        self.assertEqual(boards["bsc"]["rows"], [])
        self.assertIn("bsc", boards["bsc"]["error"])
        # 榜本身与其它链都不受影响。
        self.assertEqual(source["rows"], [{"symbol": "GLOBAL"}])
        self.assertEqual(boards["eth"]["status"], "ok")

    def test_a_failing_chain_falls_back_to_the_previous_board(self):
        def board_rows(period, chain_id):
            raise RuntimeError("上游抖动")

        stale = {"bsc": {"chain": "bsc", "rows": [{"symbol": "STALE"}]}}
        source = self._attach(board_rows, prior_boards=stale)
        boards = {board["chain"]: board for board in source["chainBoards"]}
        self.assertEqual(boards["bsc"]["status"], "stale")
        self.assertEqual(boards["bsc"]["rows"], [{"symbol": "STALE"}])
        self.assertEqual(boards["eth"]["rows"], [])

    def test_preexisting_chain_requests_are_reused_instead_of_reissued(self):
        """调用方先提交 future（好让 6 个请求并发），装配时不能重复发一遍。"""
        from concurrent import futures as futures_module

        calls = []

        def board_rows(period, chain_id):
            calls.append(chain_id)
            return [token("T", chain_id)]

        pool = server.BINANCE_WALLET_CHAIN_POOL
        # 自己起一批等价 future，再交给装配函数。
        with mock.patch.object(server, "binance_wallet_chain_board_rows", side_effect=board_rows):
            chain_futures = {
                pool.submit(board_rows, "24h", chain_id): (chain_id, code, label)
                for chain_id, code, label in server.BINANCE_WALLET_HOT_CHAINS
            }
            for future in futures_module.as_completed(chain_futures):
                future.result()
            sent_before = list(calls)
            source = {"rows": []}
            server.attach_binance_wallet_chain_boards(
                source, "24h", chain_futures=chain_futures
            )

        self.assertEqual(calls, sent_before)
        self.assertEqual(len(source["chainBoards"]), len(server.BINANCE_WALLET_HOT_CHAINS))
        self.assertTrue(all(board["status"] == "ok" for board in source["chainBoards"]))


class BinanceWalletChainWiringTests(unittest.TestCase):
    """源码级回归：两个容易在重构里被悄悄改坏的形状。"""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        cls.source = Path(server.__file__).read_text(encoding="utf-8")

    def test_the_global_request_overlaps_the_chain_requests(self):
        # 上游单次请求就是 10~20 秒量级；若先等综合榜再发链请求，默认视图会慢一倍。
        body = self.source[
            self.source.index("def fetch_binance_wallet_hot("):
            self.source.index("def binance_wallet_hot_source(")
        ]
        self.assertLess(
            body.index("BINANCE_WALLET_CHAIN_POOL.submit("),
            body.index("global_future.result()"),
            "链请求必须先提交，才能和综合榜并发跑",
        )

    def test_chain_boards_are_not_eagerly_narrative_enriched(self):
        # 五张板最多 50 行候选、每行一次上游调用 —— 实测多花约 40 秒。链视图的
        # ✦ 按钮改由客户端懒取（app.js::exchangeAiCandidate 已放行）。
        body = self.source[
            self.source.index("def attach_binance_wallet_chain_boards("):
            self.source.index("def fetch_binance_wallet_hot(")
        ]
        self.assertNotIn("enrich_binance_wallet_ai_narratives(", body)
        self.assertIn('source["chainBoards"] = boards', body)
        self.assertIn('{"value": "all", "label": "综合"}', body)

    def test_the_structure_pool_still_only_reads_the_global_board(self):
        # 链板是视图，不是第二个入池面：喂进去会把 4 小时池放大五倍。
        body = self.source[
            self.source.index("def fetch_binance_wallet_hot("):
            self.source.index("def binance_wallet_hot_source(")
        ]
        self.assertIn("record_binance_wallet_4h_structure_source(source)", body)
        self.assertNotIn("record_binance_wallet_4h_structure_source(board", body)


if __name__ == "__main__":
    unittest.main()
