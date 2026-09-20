import unittest
from unittest.mock import Mock, patch

import server


class BinanceWalletHotTests(unittest.TestCase):
    def setUp(self):
        with server.BINANCE_WALLET_AI_NARRATIVE_CACHE_LOCK:
            server.BINANCE_WALLET_AI_NARRATIVE_CACHE.clear()
        with server.BITGET_EXCHANGE_AI_CACHE_LOCK:
            server.BITGET_EXCHANGE_AI_CACHE.clear()
        with server.BITGET_COIN_IDENTITY_CACHE_LOCK:
            server.BITGET_COIN_IDENTITY_CACHE.clear()

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
        self.assertEqual(first["url"], "https://web3.binance.com/en/token/bsc/0xabc7777?ref=MQ6JD2X4")
        self.assertEqual(source["rows"][0]["name"], "我的女友景甜")
        self.assertEqual(source["rows"][2]["url"], "https://web3.binance.com/en/token/sol/So11111111111111111111111111111111111111112?ref=MQ6JD2X4")

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

    def test_parser_preserves_binance_ai_narrative_flag(self):
        payload = self.payload()
        payload["data"]["tokens"][1]["metaInfo"] = {"aiNarrativeFlag": 1}

        source = server.binance_wallet_hot_source_from_payload(payload, "4h")

        fone = next(row for row in source["rows"] if row["symbol"] == "FONE")
        sol = next(row for row in source["rows"] if row["symbol"] == "SOLMEME")
        self.assertTrue(fone["binanceAiNarrativeAvailable"])
        self.assertFalse(sol["binanceAiNarrativeAvailable"])

    def test_official_narrative_parser_validates_identity_and_generated_status(self):
        payload = {
            "success": True,
            "data": {
                "chainId": "56",
                "contractAddress": "0xabc7777",
                "status": "GENERATED",
                "narrative": "FONE 是币安 AI 返回的链上叙事分析。",
            },
        }

        result = server.binance_wallet_ai_narrative_from_payload(
            payload,
            chain_id="56",
            contract_address="0xAbC7777",
        )
        mismatch = server.binance_wallet_ai_narrative_from_payload(
            payload,
            chain_id="CT_501",
            contract_address="0xabc7777",
        )

        self.assertEqual(result["binanceAiNarrative"], "FONE 是币安 AI 返回的链上叙事分析。")
        self.assertEqual(result["binanceAiNarrativeSource"], "Binance AI")
        self.assertEqual(mismatch, {})

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

    def test_fetch_enriches_flagged_rows_from_binance_official_ai_endpoint(self):
        rank_payload = self.payload()
        rank_payload["data"]["tokens"][1]["metaInfo"] = {"aiNarrativeFlag": 1}
        rank_response = Mock()
        rank_response.raise_for_status.return_value = None
        rank_response.json.return_value = rank_payload
        narrative_response = Mock()
        narrative_response.raise_for_status.return_value = None
        narrative_response.json.return_value = {
            "success": True,
            "data": {
                "chainId": "56",
                "contractAddress": "0xabc7777",
                "status": "GENERATED",
                "narrative": "FONE 聚焦社区驱动的链上叙事。",
            },
        }

        def post(url, **kwargs):
            if url.endswith(server.BINANCE_WALLET_AI_NARRATIVE_PATH):
                return narrative_response
            return rank_response

        with patch.object(server.requests, "post", side_effect=post) as request:
            source = server.fetch_binance_wallet_hot("4h")

        fone = next(row for row in source["rows"] if row["symbol"] == "FONE")
        self.assertEqual(fone["binanceAiNarrative"], "FONE 聚焦社区驱动的链上叙事。")
        self.assertEqual(fone["binanceAiNarrativeSource"], "Binance AI")
        narrative_call = next(
            call for call in request.call_args_list
            if call.args[0].endswith(server.BINANCE_WALLET_AI_NARRATIVE_PATH)
        )
        self.assertEqual(
            narrative_call.kwargs["json"],
            {"chainId": "56", "contractAddress": "0xabc7777"},
        )
        self.assertEqual(narrative_call.kwargs["headers"]["Lang"], "zh-CN")

    def test_exchange_ai_normalizes_chain_and_reuses_binance_for_other_onchain_boards(self):
        payload = {
            "items": [
                {
                    "key": "ave:bsc:0xabc7777",
                    "sourceId": "ave",
                    "symbol": "FONE",
                    "name": "fone",
                    "chain": "bsc",
                    "contractAddress": "0xabc7777",
                },
                {
                    "key": "gmgn:robinhood:0xdef7777",
                    "sourceId": "gmgn-hot-search",
                    "symbol": "SI",
                    "name": "Superior Inu",
                    "chain": "robinhood-chain",
                    "contractAddress": "0xdef7777",
                },
            ]
        }
        with patch.object(
            server,
            "fetch_binance_wallet_ai_narrative",
            side_effect=[
                {
                    "binanceAiNarrative": "FONE 是 Binance AI 返回的链上叙事。",
                    "binanceAiNarrativeStatus": "GENERATED",
                    "binanceAiNarrativeSource": "Binance AI",
                },
                {
                    "binanceAiNarrative": "SI 是 Binance AI 返回的链上叙事。",
                    "binanceAiNarrativeStatus": "GENERATED",
                    "binanceAiNarrativeSource": "Binance AI",
                },
            ],
        ) as fetch:
            result = server.exchange_ai_narratives_payload(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(fetch.call_args_list[0].args, ("56", "0xabc7777"))
        self.assertEqual(fetch.call_args_list[1].args, ("4663", "0xdef7777"))
        self.assertEqual(result["items"][0]["exchangeAiNarrativeSource"], "Binance AI")
        self.assertEqual(result["items"][0]["exchangeAiNarrativeProvider"], "binance")
        self.assertEqual(result["items"][1]["exchangeAiNarrativeStatus"], "GENERATED")

    def test_exchange_ai_does_not_guess_when_official_symbol_metadata_is_unavailable(self):
        with patch.object(
            server,
            "fetch_bitget_official_coin_identity",
            return_value={},
        ) as identity, patch.object(server, "fetch_binance_wallet_ai_narrative") as fetch:
            result = server.exchange_ai_narratives_payload({
                "items": [{
                    "key": "okx:zec",
                    "sourceId": "okx",
                    "symbol": "ZEC",
                    "name": "ZEC-USDT-SWAP",
                }]
            })

        identity.assert_called_once_with("ZEC")
        fetch.assert_not_called()
        self.assertEqual(result["items"][0]["exchangeAiNarrativeStatus"], "UNAVAILABLE")
        self.assertNotIn("exchangeAiNarrative", result["items"][0])

    def test_bitget_native_ai_summary_has_priority_over_binance_fallback(self):
        with patch.object(
            server,
            "fetch_bitget_exchange_ai_narrative",
            return_value={
                "exchangeAiNarrative": "Bitget 官方 AI 对 SUI 的今日叙事摘要。",
                "exchangeAiNarrativeStatus": "GENERATED",
                "exchangeAiNarrativeSource": "Bitget AI",
                "exchangeAiNarrativeProvider": "bitget",
            },
        ) as bitget, patch.object(server, "fetch_binance_wallet_ai_narrative") as binance:
            result = server.exchange_ai_narratives_payload({
                "items": [{
                    "key": "bitget:sui",
                    "sourceId": "bitget",
                    "symbol": "SUI",
                    "name": "SUIUSDT",
                    "chain": "sui",
                    "contractAddress": "0xnot-used",
                }]
            })

        bitget.assert_called_once_with("SUI")
        binance.assert_not_called()
        self.assertEqual(result["items"][0]["exchangeAiNarrativeSource"], "Bitget AI")

    def test_bitget_public_coin_config_is_the_only_symbol_to_contract_resolver(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": "00000",
            "data": [{
                "coinId": "76",
                "coin": "UNI",
                "chains": [{
                    "chain": "ERC20",
                    "contractAddress": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
                }],
            }],
        }
        with patch.object(server.requests, "get", return_value=response) as request:
            result = server.fetch_bitget_official_coin_identity("UNI")

        request.assert_called_once()
        self.assertEqual(
            request.call_args.kwargs["params"],
            {"coin": "UNI"},
        )
        self.assertEqual(
            result["exchangeAiVerifiedContracts"],
            [{
                "chainId": "1",
                "chain": "ERC20",
                "contractAddress": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            }],
        )

    def test_bitget_verified_contract_metadata_enables_binance_ai_fallback(self):
        with patch.object(
            server,
            "fetch_bitget_exchange_ai_narrative",
            return_value={
                "exchangeAiVerifiedContracts": [{
                    "chainId": "CT_501",
                    "chain": "Solana",
                    "contractAddress": "So11111111111111111111111111111111111111112",
                }],
            },
        ), patch.object(
            server,
            "fetch_binance_wallet_ai_narrative",
            return_value={
                "binanceAiNarrative": "币安 AI 根据 Bitget 已核验合约返回的 SUI 叙事。",
                "binanceAiNarrativeStatus": "GENERATED",
            },
        ) as binance:
            result = server.exchange_ai_narratives_payload({
                "items": [{
                    "key": "bitget:sui",
                    "sourceId": "bitget",
                    "symbol": "SUI",
                    "name": "SUIUSDT",
                }]
            })

        binance.assert_called_once_with(
            "CT_501",
            "So11111111111111111111111111111111111111112",
        )
        item = result["items"][0]
        self.assertEqual(item["exchangeAiNarrativeSource"], "Binance AI")
        self.assertEqual(item["attemptedProviders"], ["Bitget AI", "Binance AI"])

    def test_native_exchange_and_aicoin_rows_use_only_verified_contract_metadata(self):
        for source_id in ("binance", "okx", "aicoin"):
            with self.subTest(source_id=source_id), patch.object(
                server,
                "fetch_bitget_official_coin_identity",
                return_value={
                    "exchangeAiVerifiedContracts": [{
                        "chainId": "1",
                        "chain": "Ethereum",
                        "contractAddress": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
                    }],
                },
            ) as identity, patch.object(
                server,
                "fetch_binance_wallet_ai_narrative",
                return_value={
                    "binanceAiNarrative": "UNI 是以太坊上的去中心化交易协议代币。",
                    "binanceAiNarrativeStatus": "GENERATED",
                },
            ) as binance:
                result = server.exchange_ai_narratives_payload({
                    "items": [{
                        "key": f"{source_id}:uni",
                        "sourceId": source_id,
                        "symbol": "UNI",
                        "name": "Uniswap",
                    }]
                })

            identity.assert_called_once_with("UNI")
            binance.assert_called_once_with(
                "1",
                "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            )
            item = result["items"][0]
            self.assertEqual(item["exchangeAiNarrativeSource"], "Binance AI")
            self.assertEqual(
                item["attemptedProviders"],
                (["OKX AI", "Binance AI"] if source_id == "okx" else ["Binance AI"]),
            )

    def test_bitget_native_html_parser_validates_symbol_before_accepting_summary(self):
        state = {
            "queries": [{
                "state": {
                    "data": {
                        "detailsDataNew": {
                            "detailResult": {
                                "symbol": "SUI",
                                "contractAddress": [{
                                    "chain": "Solana",
                                    "url": "So11111111111111111111111111111111111111112",
                                }],
                            },
                            "priceAnalysisReport": {
                                "coinPriceSummary": "<b>SUI</b> 今日叙事由 Bitget 官方 AI 汇总。"
                            },
                        }
                    }
                }
            }]
        }
        page = (
            "<html><body><script>window.__ZEUS_REACT_QUERY_STATE__ = "
            + __import__("json").dumps(state, ensure_ascii=False)
            + ";</script></body></html>"
        )

        official_identity = {
            "exchangeAiOfficialContracts": [
                "So11111111111111111111111111111111111111112",
            ],
            "exchangeAiVerifiedContracts": [{
                "chainId": "CT_501",
                "chain": "Solana",
                "contractAddress": "So11111111111111111111111111111111111111112",
            }],
        }
        result = server.bitget_exchange_ai_from_html(page, "SUI", official_identity)
        mismatch = server.bitget_exchange_ai_from_html(page, "SUI2", official_identity)

        self.assertEqual(result["exchangeAiNarrative"], "SUI 今日叙事由 Bitget 官方 AI 汇总。")
        self.assertEqual(result["exchangeAiNarrativeProvider"], "bitget")
        self.assertEqual(
            result["exchangeAiVerifiedContracts"][0]["contractAddress"],
            "So11111111111111111111111111111111111111112",
        )
        self.assertEqual(mismatch, {})

    def test_bitget_seo_same_symbol_page_cannot_override_official_coin_contract(self):
        state = {
            "queries": [{
                "state": {
                    "data": {
                        "detailsDataNew": {
                            "detailResult": {
                                "symbol": "UNI",
                                "contractAddress": [{
                                    "chain": "sol",
                                    "url": "wpWXn1HXUNzZgzJKqB5R67TTJCGFvaTyDkHmtJobonk",
                                }],
                            },
                            "priceAnalysisReport": {
                                "coinPriceSummary": "这是同名 Solana Meme 的叙事。"
                            },
                        }
                    }
                }
            }]
        }
        page = (
            "<script>window.__ZEUS_REACT_QUERY_STATE__ = "
            + __import__("json").dumps(state, ensure_ascii=False)
            + ";</script>"
        )
        official = {
            "exchangeAiOfficialContracts": [
                "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            ],
            "exchangeAiVerifiedContracts": [{
                "chainId": "1",
                "chain": "ERC20",
                "contractAddress": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            }],
        }

        result = server.bitget_exchange_ai_from_html(page, "UNI", official)

        self.assertNotIn("exchangeAiNarrative", result)
        self.assertEqual(
            result["exchangeAiVerifiedContracts"][0]["contractAddress"],
            "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        )

    def test_okx_token_href_preserves_contract_for_safe_fallback(self):
        href = "https://web3.okx.com/zh-hans/token/robinhood-chain/0xef82ddc566653699b89c3afe123559a75aaa2976"

        self.assertEqual(server.okx_dex_chain_from_href(href), "robinhood-chain")
        self.assertEqual(
            server.okx_dex_contract_from_href(href),
            "0xef82ddc566653699b89c3afe123559a75aaa2976",
        )

    def test_four_hour_wallet_top_ten_enters_ca_news_research_pool(self):
        source = server.binance_wallet_hot_source_from_payload(self.payload(), "4h")

        rows = server.binance_wallet_hot_research_rows(source, observed_at=1_700_000_000_000)

        fone = next(row for row in rows if row["symbol"] == "FONE")
        sol = next(row for row in rows if row["symbol"] == "SOLMEME")
        self.assertEqual(fone["network"], "bsc")
        self.assertEqual(fone["contractAddress"], "0xabc7777")
        self.assertEqual(fone["metrics"]["liquidityUsd"], 394710.0)
        self.assertEqual(fone["metrics"]["volumeH1Usd"], 22000000.0)
        self.assertEqual(sol["network"], "solana")

    def test_gmgn_trench_narrative_reuses_binance_ai_endpoint(self):
        expected = {
            "binanceAiNarrative": "这是币安 AI 返回的战壕叙事。",
            "binanceAiNarrativeStatus": "GENERATED",
            "binanceAiNarrativeSource": "Binance AI",
        }
        with patch.object(server, "fetch_binance_wallet_ai_narrative", return_value=expected) as fetch:
            result = server.exchange_ai_narrative_for_item({
                "key": "gmgn-trenches:solana:test",
                "sourceId": "gmgn-trenches",
                "symbol": "TEST",
                "chain": "solana",
                "contractAddress": "So11111111111111111111111111111111111111112",
            })

        fetch.assert_called_once_with("CT_501", "So11111111111111111111111111111111111111112")
        self.assertEqual(result["exchangeAiNarrative"], expected["binanceAiNarrative"])
        self.assertEqual(result["exchangeAiNarrativeProvider"], "binance")
        self.assertEqual(result["attemptedProviders"], ["Binance AI"])


if __name__ == "__main__":
    unittest.main()
