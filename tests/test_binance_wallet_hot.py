import unittest
from unittest.mock import Mock, patch

import server


class BinanceWalletHotTests(unittest.TestCase):
    @staticmethod
    def payload():
        return {
            "code": "000000",
            "success": True,
            "data": {
                "tokens": [
                    {
                        "chainId": "56",
                        "contractAddress": "0xff673079235560e4de3fe4554c9981d759af7777",
                        "symbol": "我的女友景甜",
                        "price": "0.0045",
                        "percentChange1h": "88",
                        "volume1h": "11000000",
                    },
                    {
                        "chainId": "56",
                        "contractAddress": "0xabc7777",
                        "symbol": "FONE",
                        "name": "fone",
                        "icon": "/images/fone.png",
                        "price": "0.025",
                        "marketCap": "8500000",
                        "liquidity": "394710",
                        "holders": "1234",
                        "percentChange5m": "1.25",
                        "percentChange1h": "18.16",
                        "percentChange4h": "25.5",
                        "percentChange24h": "55.47",
                        "volume5m": "120000",
                        "volume1h": "11020000",
                        "volume4h": "22000000",
                        "volume24h": "480630000",
                    },
                    {
                        "chainId": "CT_501",
                        "contractAddress": "So11111111111111111111111111111111111111112",
                        "symbol": "SOLMEME",
                        "price": "1.5",
                        "percentChange1h": "-3.5",
                        "volume1h": "500000",
                    },
                    {
                        "chainId": "56",
                        "contractAddress": "0xstable",
                        "symbol": "USDT",
                        "percentChange1h": "0",
                    },
                ]
            },
        }

    def test_parser_uses_selected_period_and_preserves_chain_contract_data(self):
        source = server.binance_wallet_hot_source_from_payload(self.payload(), "1h")

        self.assertEqual(source["id"], "binance-wallet-hot")
        self.assertEqual(source["period"], "1h")
        self.assertEqual(source["periodLabel"], "1 小时")
        self.assertEqual([row["symbol"] for row in source["rows"]], ["我的女友景甜", "FONE", "SOLMEME"])
        first = next(row for row in source["rows"] if row["symbol"] == "FONE")
        self.assertEqual(first["change"], "+18.16%")
        self.assertEqual(first["amount"], 11020000.0)
        self.assertEqual(first["chainLabel"], "BSC")
        self.assertEqual(first["contractAddress"], "0xabc7777")
        self.assertEqual(first["icon"], "https://bin.bnbstatic.com/images/fone.png")
        self.assertEqual(first["url"], "https://web3.binance.com/en/token/bsc/0xabc7777")
        self.assertEqual(source["rows"][0]["name"], "我的女友景甜")
        self.assertEqual(source["rows"][2]["url"], "https://web3.binance.com/en/token/sol/So11111111111111111111111111111111111111112")

    def test_periods_map_to_binance_wallet_rank_fields(self):
        expectations = {
            "5m": ("+1.25%", 120000.0),
            "1h": ("+18.16%", 11020000.0),
            "4h": ("+25.50%", 22000000.0),
            "24h": ("+55.47%", 480630000.0),
        }
        for period, expected in expectations.items():
            with self.subTest(period=period):
                rows = server.binance_wallet_hot_source_from_payload(self.payload(), period)["rows"]
                row = next(item for item in rows if item["symbol"] == "FONE")
                self.assertEqual((row["change"], row["amount"]), expected)

    def test_parser_displays_at_most_ten_valid_assets(self):
        payload = self.payload()
        template = payload["data"]["tokens"][0]
        payload["data"]["tokens"] = [
            {**template, "symbol": f"TOKEN{index}", "contractAddress": f"0x{index:040x}"}
            for index in range(14)
        ]

        source = server.binance_wallet_hot_source_from_payload(payload, "1h")

        self.assertEqual(len(source["rows"]), 10)
        self.assertEqual(source["rows"][-1]["rank"], 10)

    def test_fetch_uses_unified_trending_rank_with_selected_window(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = self.payload()
        with patch.object(server.requests, "post", return_value=response) as post:
            source = server.fetch_binance_wallet_hot("4h")

        self.assertEqual(source["period"], "4h")
        request_body = post.call_args.kwargs["json"]
        self.assertEqual(request_body["rankType"], 10)
        self.assertEqual(request_body["period"], 40)
        self.assertEqual(request_body["sortBy"], 1)
        self.assertEqual(request_body["size"], 20)


if __name__ == "__main__":
    unittest.main()
