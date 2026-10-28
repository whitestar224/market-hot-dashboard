import unittest
from unittest.mock import Mock, patch

import gmgn_agentic
import server


class GmgnHotSearchTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {
            "GMGN_READONLY_PERSIST_CACHE": "0",
            "GMGN_PERSIST_RATE_STATE": "0",
            "GMGN_READONLY_KEY_MODE": "public",
            "GMGN_PROXY_URL": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        gmgn_agentic.reset_gmgn_runtime_state()

    @staticmethod
    def token(chain: str, period: str, index: int) -> dict:
        return {
            "chain": chain,
            "address": f"{chain}-{period}-{index}",
            "name": f"{chain} {period} token {index}",
            "symbol": f"{chain[:3].upper()}{index}",
            "logo": f"https://img.example/{chain}-{period}-{index}.png",
            "price": 0.001 * index,
            "price_change_percent": index,
            "volume": 1000 * index,
            "liquidity": 500 * index,
            "market_cap": 10_000 * index,
            "swaps": 20 * index,
            "buys": 12 * index,
            "sells": 8 * index,
            "holder_count": 50 * index,
            "visiting_count": 1000 - index,
            "rank": index,
        }

    def test_fetch_uses_official_hot_search_periods_and_chain_boards(self):
        captured = {}

        def fake_post(url, *, params, headers, json, timeout):
            captured.update({
                "url": url,
                "params": params,
                "headers": headers,
                "body": json,
                "timeout": timeout,
            })
            blocks = []
            for request in json["params"]:
                chain = request["chain"]
                period = request["interval"]
                blocks.append({
                    "chain": chain,
                    "interval": period,
                    "tokens": [self.token(chain, period, index) for index in range(1, 13)],
                })
            response = Mock(status_code=200, text="")
            response.json.return_value = {"code": 0, "message": "success", "data": blocks}
            return response

        with patch.object(gmgn_agentic._GMGN_HTTP_SESSION, "post", side_effect=fake_post):
            source = server.fetch_gmgn_hot_search(max_rows=10)

        requested = captured["body"]["params"]
        self.assertEqual(captured["url"], "https://openapi.gmgn.ai/v1/market/hot_searches")
        self.assertTrue(captured["headers"]["X-APIKEY"])
        self.assertTrue(captured["params"]["timestamp"])
        self.assertTrue(captured["params"]["client_id"])
        self.assertEqual({item["label"] for item in requested}, {"hot-search"})
        self.assertEqual({item["interval"] for item in requested}, {"1m", "5m", "1h", "6h", "24h"})
        self.assertEqual({item["chain"] for item in requested}, {
            "sol", "bsc", "base", "eth", "robinhood", "arc", "stable",
        })
        self.assertTrue(all(item["limit"] == 10 for item in requested))

        self.assertEqual(source["id"], "gmgn-hot-search")
        self.assertEqual(source["period"], "1h")
        self.assertEqual([item["value"] for item in source["periodOptions"]], ["1m", "5m", "1h", "6h", "24h"])
        self.assertEqual([item["value"] for item in source["chainOptions"]], [
            "all", "sol", "bsc", "base", "eth", "robinhood", "arc", "stable",
        ])
        self.assertEqual(len(source["periodBoards"]), 40)
        sol_1m = next(board for board in source["periodBoards"] if board["chain"] == "sol" and board["period"] == "1m")
        self.assertEqual(len(sol_1m["rows"]), 10)
        self.assertEqual(sol_1m["rows"][0]["searchHeat"], 999)
        self.assertEqual(sol_1m["rows"][0]["contractAddress"], "sol-1m-1")
        self.assertEqual(sol_1m["rows"][0]["note"], "GMGN 1m 热搜 · 999 次访问")
        # 「查看」跳 GMGN 站内交易页（不是币安钱包）。
        self.assertEqual(sol_1m["rows"][0]["url"], "https://gmgn.ai/sol/token/sol-1m-1")
        self.assertEqual(len(source["rows"]), 10)

    def test_parser_rejects_a_non_success_response(self):
        response = Mock(status_code=200, text="quota error")
        response.json.return_value = {"code": 1001, "message": "quota error", "data": []}

        with patch.object(gmgn_agentic._GMGN_HTTP_SESSION, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "quota error"):
                server.fetch_gmgn_hot_search()


class GmgnTokenTradeUrlTests(unittest.TestCase):
    """GMGN 交易页链接的唯一构造点（榜单行 + 弹窗「查看」共用）。"""

    def test_robinhood_token_matches_the_user_provided_sample(self):
        self.assertEqual(
            server.gmgn_token_trade_url("robinhood", "0x500e89cec6a0dfbd5ddc28b773d189ab3fc40cbb"),
            "https://gmgn.ai/robinhood/token/0x500e89cec6a0dfbd5ddc28b773d189ab3fc40cbb",
        )

    def test_chain_is_lowercased_and_the_contract_is_path_encoded(self):
        self.assertEqual(
            server.gmgn_token_trade_url("Robinhood", "0xAbC/../x"),
            "https://gmgn.ai/robinhood/token/0xAbC%2F..%2Fx",
        )

    def test_missing_or_numeric_chain_yields_no_link(self):
        self.assertEqual(server.gmgn_token_trade_url("", "0xabc"), "")
        self.assertEqual(server.gmgn_token_trade_url("robinhood", ""), "")
        # 4663 是链 ID，不是 GMGN 的路径别名：宁可不给链接，也不给一条 404 的链接。
        self.assertEqual(server.gmgn_token_trade_url("4663", "0xabc"), "")


class GmgnChainSlugTests(unittest.TestCase):
    """子系统链代号 → GMGN 路径别名。

    热搜榜那条路拿到的是 GMGN 自己吐出来的 slug（sol / eth / arc …），原样拼就行；
    但「聪明钱买入」弹窗拿到的是项目内部代号（ethereum / solana），直接拼进链接会
    落到 404 页面 —— 同一批链在 GMGN 上叫 eth / sol。
    """

    def test_internal_codes_fold_to_the_gmgn_slug(self):
        self.assertEqual(server.gmgn_chain_slug("ethereum"), "eth")
        self.assertEqual(server.gmgn_chain_slug("solana"), "sol")

    def test_chains_that_already_match_pass_through(self):
        # 热搜榜那条路不能被我改坏：它传进来的就是最终 slug。
        for slug in ("sol", "eth", "bsc", "base", "robinhood"):
            self.assertEqual(server.gmgn_chain_slug(slug), slug)

    def test_case_and_padding_do_not_matter(self):
        self.assertEqual(server.gmgn_chain_slug("  Robinhood "), "robinhood")
        self.assertEqual(server.gmgn_chain_slug("Solana"), "sol")

    def test_an_unknown_chain_never_becomes_a_dead_link(self):
        # 链 ID 与不明代号都折算不出来，调用方会回落到区块浏览器。
        self.assertEqual(server.gmgn_chain_slug("4663"), "")
        self.assertEqual(server.gmgn_chain_slug("arc"), "")
        self.assertEqual(server.gmgn_chain_slug(""), "")
        self.assertEqual(server.gmgn_chain_slug(None), "")

    def test_the_slug_is_what_the_url_builder_consumes(self):
        self.assertEqual(
            server.gmgn_token_trade_url(server.gmgn_chain_slug("ethereum"), "0xabc"),
            "https://gmgn.ai/eth/token/0xabc",
        )


if __name__ == "__main__":
    unittest.main()
