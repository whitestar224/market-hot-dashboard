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
        self.assertEqual(len(source["rows"]), 10)

    def test_parser_rejects_a_non_success_response(self):
        response = Mock(status_code=200, text="quota error")
        response.json.return_value = {"code": 1001, "message": "quota error", "data": []}

        with patch.object(gmgn_agentic._GMGN_HTTP_SESSION, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "quota error"):
                server.fetch_gmgn_hot_search()


if __name__ == "__main__":
    unittest.main()
