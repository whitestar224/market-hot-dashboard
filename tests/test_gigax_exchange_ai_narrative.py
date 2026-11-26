"""GIGA X 接管全系统叙事分析的契约测试。

之前每个叙事入口都直接打币安 AI（``/api/exchange-ai-narratives`` 里那条
``fetch_binance_wallet_ai_narrative``）。现在顺序统一成「GIGA X 优先、币安 / Bitget
兜底」，这组用例钉住三件事：

1. 有正文的 GIGA X 结果直接返回，**不再**多打一次币安；
2. GIGA X 说「没有」（no-narrative，新币还没生成）时才走兜底，不能让一次空结果
   把币安也一起跳过；
3. 链别名、base58 地址大小写这些老坑不能因为多了一层映射而复现。
"""
import os
import unittest
from unittest.mock import patch

import server

CHINESE_NARRATIVE = "这是一个新玩法的发射台代币，属于新机制下的首个平台币。"
ENGLISH_NARRATIVE = (
    "This token implements a brand new launchpad mechanism where holders earn "
    "revenue share from every bonding curve trade on the platform."
)
SOL_ADDRESS = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"


def gigax_hit(text: str = CHINESE_NARRATIVE, tags=None) -> dict:
    return {
        "exchangeAiNarrativeAvailable": True,
        "exchangeAiNarrative": text,
        "exchangeAiNarrativeStatus": "GENERATED",
        "exchangeAiNarrativeSource": "GIGA X",
        "exchangeAiNarrativeProvider": "gigax",
        **({"exchangeAiNarrativeTags": tags} if tags else {}),
    }


class GigaxChainMappingTests(unittest.TestCase):
    def test_chain_aliases_fold_to_the_slugs_the_bridge_accepts(self):
        cases = {
            "bsc": "bsc",
            "56": "bsc",
            "bnb": "bsc",
            "BSC": "bsc",
            "solana": "sol",
            "sol": "sol",
            "CT_501": "sol",
            "501": "sol",
            "1": "eth",
            "ethereum": "eth",
            "base": "base",
            "8453": "base",
            "robinhood-chain": "robinhood",
            "4663": "robinhood",
            "99901": "elysium",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(server.gigax_exchange_ai_chain(raw), expected)

    def test_unmapped_chain_is_skipped_instead_of_hammering_upstream(self):
        self.assertEqual(server.gigax_exchange_ai_chain("sui"), "")
        self.assertEqual(server.gigax_exchange_ai_chain(""), "")
        self.assertEqual(server.gigax_exchange_ai_chain(None), "")

    def test_address_key_folds_evm_but_keeps_base58_case(self):
        self.assertEqual(
            server.exchange_ai_address_key("bsc", "0xAbC7777"),
            ("bsc", "0xabc7777"),
        )
        # Solana 地址小写化就是另一个地址，绝不能在去重键里折叠。
        self.assertEqual(
            server.exchange_ai_address_key("sol", SOL_ADDRESS),
            ("sol", SOL_ADDRESS),
        )
        self.assertNotEqual(
            server.exchange_ai_address_key("sol", SOL_ADDRESS),
            server.exchange_ai_address_key("sol", SOL_ADDRESS.lower()),
        )


class GigaxFetchTests(unittest.TestCase):
    def test_bridge_gets_the_canonical_slug_and_the_verbatim_address(self):
        with patch.object(
            server.gigax_api,
            "fetch_narrative",
            return_value={"ok": False, "reason": "no-narrative"},
        ) as fetch:
            server.fetch_gigax_exchange_ai_narrative("solana", SOL_ADDRESS)
            # 认不出的链不该产生任何上游请求。
            server.fetch_gigax_exchange_ai_narrative("sui", SOL_ADDRESS)
            # 没合约也不该请求。
            server.fetch_gigax_exchange_ai_narrative("bsc", "")

        fetch.assert_called_once_with("sol", SOL_ADDRESS)

    def test_ok_result_shapes_into_exchange_ai_fields_with_tags(self):
        with patch.object(
            server.gigax_api,
            "fetch_narrative",
            return_value={
                "ok": True,
                "reason": None,
                "text": CHINESE_NARRATIVE,
                "categoryTags": ["发射台", "新机制", "发射台"],
                "status": "ready",
            },
        ):
            result = server.fetch_gigax_exchange_ai_narrative("bsc", "0xabc")

        self.assertEqual(result["exchangeAiNarrative"], CHINESE_NARRATIVE)
        self.assertEqual(result["exchangeAiNarrativeSource"], "GIGA X")
        self.assertEqual(result["exchangeAiNarrativeProvider"], "gigax")
        self.assertEqual(result["exchangeAiNarrativeStatus"], "GENERATED")
        self.assertTrue(result["exchangeAiNarrativeAvailable"])
        self.assertEqual(result["exchangeAiNarrativeTags"], ["发射台", "新机制"])

    def test_no_narrative_and_auth_states_are_not_treated_as_a_hit(self):
        for payload in (
            {"ok": False, "reason": "no-narrative", "status": "generating"},
            {"ok": False, "reason": "auth", "error": "token 无效"},
            {"ok": False, "reason": "error", "error": "network"},
            {},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(server.exchange_ai_result_from_gigax(payload), {})

    def test_english_gigax_text_never_reaches_a_tooltip(self):
        # 翻译不可用时宁可没有叙事，也不能把英文塞进中文弹窗。
        with patch.object(server, "libretranslate_translate", return_value=""):
            result = server.exchange_ai_result_from_gigax(
                {"ok": True, "text": ENGLISH_NARRATIVE}
            )
        self.assertEqual(result, {})


class GigaxPrecedenceTests(unittest.TestCase):
    def test_gigax_hit_returns_before_any_legacy_provider_runs(self):
        item = {
            "key": "gmgn:bsc:0xabc7777",
            "sourceId": "gmgn-trenches",
            "symbol": "FONE",
            "chain": "bsc",
            "contractAddress": "0xabc7777",
        }
        with patch.object(
            server, "fetch_gigax_exchange_ai_narrative", return_value=gigax_hit()
        ) as gigax, patch.object(
            server, "fetch_binance_wallet_ai_narrative"
        ) as binance:
            result = server.exchange_ai_narrative_for_item(item)

        gigax.assert_called_once_with("bsc", "0xabc7777")
        binance.assert_not_called()
        self.assertEqual(result["exchangeAiNarrativeSource"], "GIGA X")
        self.assertEqual(result["exchangeAiNarrativeProvider"], "gigax")
        self.assertEqual(result["attemptedProviders"], ["GIGA X"])

    def test_no_narrative_from_gigax_falls_through_to_binance(self):
        item = {
            "key": "ave:bsc:0xabc7777",
            "sourceId": "ave",
            "symbol": "FONE",
            "chain": "bsc",
            "contractAddress": "0xabc7777",
        }
        with patch.object(
            server, "fetch_gigax_exchange_ai_narrative", return_value={}
        ), patch.object(
            server,
            "fetch_binance_wallet_ai_narrative",
            return_value={
                "binanceAiNarrative": "币安 AI 返回的兜底叙事。",
                "binanceAiNarrativeStatus": "GENERATED",
                "binanceAiNarrativeSource": "Binance AI",
            },
        ) as binance:
            result = server.exchange_ai_narrative_for_item(item)

        binance.assert_called_once_with("56", "0xabc7777")
        self.assertEqual(result["exchangeAiNarrativeSource"], "Binance AI")
        self.assertEqual(result["attemptedProviders"], ["GIGA X", "Binance AI"])

    def test_legacy_fallback_can_be_switched_off_entirely(self):
        item = {
            "key": "ave:bsc:0xabc7777",
            "sourceId": "ave",
            "symbol": "FONE",
            "chain": "bsc",
            "contractAddress": "0xabc7777",
        }
        with patch.dict(os.environ, {"EXCHANGE_AI_NARRATIVE_LEGACY_FALLBACK": "0"}), \
                patch.object(server, "fetch_gigax_exchange_ai_narrative", return_value={}), \
                patch.object(server, "fetch_binance_wallet_ai_narrative") as binance:
            result = server.exchange_ai_narrative_for_item(item)

        binance.assert_not_called()
        self.assertEqual(result["exchangeAiNarrativeStatus"], "UNAVAILABLE")
        self.assertEqual(result["attemptedProviders"], ["GIGA X"])

    def test_ticker_only_rows_ask_gigax_with_the_resolved_contract(self):
        item = {"key": "okx:uni", "sourceId": "okx", "symbol": "UNI", "name": "Uniswap"}
        with patch.object(
            server,
            "fetch_bitget_official_coin_identity",
            return_value={
                "exchangeAiVerifiedContracts": [{
                    "chainId": "1",
                    "chain": "Ethereum",
                    "contractAddress": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
                }],
            },
        ), patch.object(
            server, "fetch_gigax_exchange_ai_narrative", return_value=gigax_hit()
        ) as gigax, patch.object(
            server, "fetch_binance_wallet_ai_narrative"
        ) as binance:
            result = server.exchange_ai_narrative_for_item(item)

        gigax.assert_called_once_with("1", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984")
        binance.assert_not_called()
        self.assertEqual(result["exchangeAiNarrativeSource"], "GIGA X")
        self.assertEqual(result["attemptedProviders"], ["OKX AI", "GIGA X"])

    def test_board_enrichment_mirrors_gigax_into_the_legacy_keys(self):
        source = {
            "rows": [{
                "symbol": "FONE",
                "chain": "bsc",
                "contractAddress": "0xabc7777",
                "binanceAiNarrativeAvailable": True,
            }]
        }
        with patch.object(
            server, "fetch_gigax_exchange_ai_narrative", return_value=gigax_hit()
        ):
            enriched = server.enrich_binance_wallet_ai_narratives(source)

        row = enriched["rows"][0]
        self.assertEqual(row["exchangeAiNarrative"], CHINESE_NARRATIVE)
        # 弹窗摘要与 rank monitor 仍读老字段，必须镜像过去，否则切到 GIGA X
        # 之后它们会凭空丢掉叙事。
        self.assertEqual(row["binanceAiNarrative"], CHINESE_NARRATIVE)
        self.assertEqual(row["binanceAiNarrativeSource"], "GIGA X")

    def test_board_enrichment_stays_silent_when_nothing_is_available(self):
        source = {
            "rows": [{
                "symbol": "FONE",
                "chain": "bsc",
                "contractAddress": "0xabc7777",
                "binanceAiNarrativeAvailable": True,
            }]
        }
        with patch.object(
            server, "fetch_gigax_exchange_ai_narrative", return_value={}
        ), patch.object(
            server, "fetch_binance_wallet_ai_narrative", return_value={}
        ):
            enriched = server.enrich_binance_wallet_ai_narratives(source)

        row = enriched["rows"][0]
        self.assertNotIn("exchangeAiNarrative", row)
        self.assertNotIn("binanceAiNarrative", row)


if __name__ == "__main__":
    unittest.main()
