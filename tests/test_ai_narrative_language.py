"""AI 叙事语言闸门：弹窗/浮窗里的 AI 叙事只能是中文。

Binance 的 `ai-widget/analysis-narrative` 只按请求头 ``Lang`` 决定语言：
``zh-CN`` 返回中文，缺失或其它值返回英文。所以除了请求头本身，取数、缓存、
下发和弹窗渲染每一层都要挡住英文，否则英文会以"接口用错"的形态出现在浮窗里。
"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server

ROOT = Path(__file__).resolve().parents[1]

ENGLISH_NARRATIVE = (
    "ZERO (zerotrace) is a privacy token on Robinhood Chain launched October 2, "
    "2026 via Pons V2. It enables shielding ERC-20 holdings into encrypted "
    "receipts for private transfers, and holders earn a share of the trading fees."
)
CHINESE_NARRATIVE = (
    "ZERO（zerotrace）是 Robinhood 链上的隐私代币，余额可通过零知识证明屏蔽，"
    "交易费用的 0.7% 分配给私密票据持有者。"
)


class NarrativeLanguageGateTests(unittest.TestCase):
    def setUp(self):
        with server.BINANCE_WALLET_AI_NARRATIVE_CACHE_LOCK:
            server.BINANCE_WALLET_AI_NARRATIVE_CACHE.clear()

    # --- 语言判定 -------------------------------------------------------
    def test_english_narrative_is_detected(self):
        self.assertTrue(server.narrative_text_looks_english(ENGLISH_NARRATIVE))
        self.assertFalse(server.narrative_text_looks_english(CHINESE_NARRATIVE))

    def test_short_chinese_summary_with_tickers_survives(self):
        for text in (
            "GMGN 原生 AI 叙事样例。",
            "Pump.fun 是领先的 Solana 表情包币启动平台。",
            "BINF 是一个基于 BSC 的 AI 推理平台，费用通过 PancakeSwap 路由。",
        ):
            with self.subTest(text=text):
                self.assertFalse(server.narrative_text_looks_english(text))
                self.assertTrue(server.narrative_text_is_chinese(text))

    def test_localized_payload_prefers_chinese_member(self):
        self.assertEqual(
            server.localized_narrative_text({"en": ENGLISH_NARRATIVE, "cn": CHINESE_NARRATIVE}),
            CHINESE_NARRATIVE,
        )

    # --- 币安解析层 -----------------------------------------------------
    def test_english_payload_never_becomes_a_narrative(self):
        payload = {
            "success": True,
            "data": {
                "chainId": "4663",
                "contractAddress": "0x316fa3ab9a8fd8d7567a823dedecf28d9fee2894",
                "status": "GENERATED",
                "narrative": ENGLISH_NARRATIVE,
            },
        }
        result = server.binance_wallet_ai_narrative_from_payload(
            payload,
            chain_id="4663",
            contract_address="0x316fa3ab9a8fd8d7567a823dedecf28d9fee2894",
        )
        self.assertNotIn("binanceAiNarrative", result)
        self.assertEqual(result["binanceAiNarrativeStatus"], "LANGUAGE_MISMATCH")
        self.assertEqual(result["binanceAiNarrativeLanguage"], "en")

    def test_chinese_payload_is_tagged_as_chinese(self):
        payload = {
            "success": True,
            "data": {
                "chainId": "4663",
                "contractAddress": "0xabc",
                "status": "GENERATED",
                "narrative": CHINESE_NARRATIVE,
            },
        }
        result = server.binance_wallet_ai_narrative_from_payload(
            payload, chain_id="4663", contract_address="0xabc"
        )
        self.assertEqual(result["binanceAiNarrative"], CHINESE_NARRATIVE)
        self.assertEqual(result["binanceAiNarrativeLanguage"], "zh-CN")

    # --- 翻译兜底 -------------------------------------------------------
    def test_english_only_result_is_translated_when_possible(self):
        with patch.object(server, "translate_narrative_to_chinese", return_value=CHINESE_NARRATIVE) as translate:
            result = server.binance_ai_narrative_chinese_result(
                {"binanceAiNarrativeEnglishOnly": ENGLISH_NARRATIVE, "binanceAiNarrativeStatus": "LANGUAGE_MISMATCH"}
            )
        translate.assert_called_once()
        self.assertEqual(result["binanceAiNarrative"], CHINESE_NARRATIVE)
        self.assertTrue(result["binanceAiNarrativeTranslated"])

    def test_english_only_result_is_withheld_without_translation(self):
        with patch.object(server, "translate_narrative_to_chinese", return_value=""):
            result = server.binance_ai_narrative_chinese_result(
                {"binanceAiNarrativeEnglishOnly": ENGLISH_NARRATIVE, "binanceAiNarrativeStatus": "LANGUAGE_MISMATCH"}
            )
        self.assertNotIn("binanceAiNarrative", result)
        self.assertEqual(result["binanceAiNarrativeStatus"], "UNAVAILABLE")

    # --- HTTP 层：请求头与缓存 ------------------------------------------
    def test_request_always_asks_for_the_chinese_region(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "success": True,
            "data": {
                "chainId": "4663",
                "contractAddress": "0xabc",
                "status": "GENERATED",
                "narrative": CHINESE_NARRATIVE,
            },
        }
        with patch.object(server.requests, "post", return_value=response) as post:
            result = server.fetch_binance_wallet_ai_narrative("4663", "0xabc")

        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Lang"], "zh-CN")
        self.assertIn("zh-CN", headers["Accept-Language"])
        self.assertEqual(result["binanceAiNarrative"], CHINESE_NARRATIVE)

    def test_english_response_is_not_cached_as_a_narrative(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "success": True,
            "data": {
                "chainId": "4663",
                "contractAddress": "0xabc",
                "status": "GENERATED",
                "narrative": ENGLISH_NARRATIVE,
            },
        }
        with patch.object(server.requests, "post", return_value=response), patch.object(
            server, "translate_narrative_to_chinese", return_value=""
        ):
            result = server.fetch_binance_wallet_ai_narrative("4663", "0xabc")

        self.assertNotIn("binanceAiNarrative", result)
        with server.BINANCE_WALLET_AI_NARRATIVE_CACHE_LOCK:
            cached = dict(server.BINANCE_WALLET_AI_NARRATIVE_CACHE)
        self.assertFalse(
            any(entry.get("binanceAiNarrative") for _, entry in cached.values()),
            "英文叙事不允许进入缓存",
        )

    def test_cached_english_narrative_is_dropped_and_refetched(self):
        with server.BINANCE_WALLET_AI_NARRATIVE_CACHE_LOCK:
            server.BINANCE_WALLET_AI_NARRATIVE_CACHE["4663:0xabc"] = (
                server.time.time(),
                {"binanceAiNarrative": ENGLISH_NARRATIVE, "binanceAiNarrativeStatus": "GENERATED"},
            )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "success": True,
            "data": {
                "chainId": "4663",
                "contractAddress": "0xabc",
                "status": "GENERATED",
                "narrative": CHINESE_NARRATIVE,
            },
        }
        with patch.object(server.requests, "post", return_value=response) as post:
            result = server.fetch_binance_wallet_ai_narrative("4663", "0xabc")

        post.assert_called()
        self.assertEqual(result["binanceAiNarrative"], CHINESE_NARRATIVE)

    # --- 下发层（唯一出口） ---------------------------------------------
    def test_payload_layer_withholds_english_items(self):
        with patch.object(server, "translate_narrative_to_chinese", return_value=""):
            item = server.exchange_ai_narrative_sanitize_item({
                "key": "gmgn-trenches:robinhood:0xabc",
                "exchangeAiNarrative": ENGLISH_NARRATIVE,
                "exchangeAiNarrativeStatus": "GENERATED",
                "exchangeAiNarrativeAvailable": True,
            })
        self.assertNotIn("exchangeAiNarrative", item)
        self.assertNotIn("exchangeAiNarrativeAvailable", item)
        self.assertEqual(item["exchangeAiNarrativeStatus"], "UNAVAILABLE")

    def test_payload_layer_keeps_chinese_items_untouched(self):
        item = {
            "key": "binance:56:0xabc",
            "exchangeAiNarrative": CHINESE_NARRATIVE,
            "exchangeAiNarrativeStatus": "GENERATED",
        }
        self.assertEqual(server.exchange_ai_narrative_sanitize_item(item), item)

    def test_payload_layer_translates_instead_of_dropping(self):
        with patch.object(server, "translate_narrative_to_chinese", return_value=CHINESE_NARRATIVE):
            item = server.exchange_ai_narrative_sanitize_item({
                "key": "binance:56:0xabc",
                "exchangeAiNarrative": ENGLISH_NARRATIVE,
                "exchangeAiNarrativeStatus": "GENERATED",
            })
        self.assertEqual(item["exchangeAiNarrative"], CHINESE_NARRATIVE)
        self.assertTrue(item["exchangeAiNarrativeTranslated"])

    def test_binance_provider_result_is_gated_too(self):
        with patch.object(server, "translate_narrative_to_chinese", return_value=""):
            result = server.exchange_ai_result_from_binance({"binanceAiNarrative": ENGLISH_NARRATIVE})
        self.assertNotIn("exchangeAiNarrative", result)

    # --- GMGN 原生叙事与桌面弹窗 ----------------------------------------
    def test_gmgn_narrative_requires_chinese(self):
        import gmgn_agentic

        self.assertEqual(
            gmgn_agentic._gmgn_native_narrative({"ai_narrative": {"cn": "GMGN 原生 AI 叙事样例。"}}),
            "GMGN 原生 AI 叙事样例。",
        )
        self.assertEqual(
            gmgn_agentic._gmgn_native_narrative({"ai_narrative": ENGLISH_NARRATIVE}),
            "",
        )

    def test_desktop_popup_tooltip_rejects_english(self):
        import desktop_alert

        self.assertEqual(desktop_alert.chinese_ai_narrative(ENGLISH_NARRATIVE), "")
        self.assertEqual(desktop_alert.chinese_ai_narrative(CHINESE_NARRATIVE), CHINESE_NARRATIVE)


class FrontendNarrativeGuardTests(unittest.TestCase):
    def test_app_tooltip_filters_non_chinese_narratives(self):
        js = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("function exchangeAiChineseNarrative(", js)
        self.assertIn('narrative: exchangeAiChineseNarrative(row?.exchangeAiNarrative', js)

    def test_gmgn_tooltip_filters_non_chinese_narratives(self):
        js = (ROOT / "price-watch.js").read_text(encoding="utf-8")
        self.assertIn("function gmgnChineseNarrativeText(", js)
        # The GMGN trenches response carries no narrative field of its own, so
        # the board now draws its narrative from Binance AI and the language
        # gate is applied to *that* value before it reaches the tooltip.
        self.assertIn("gmgnChineseNarrativeText(entry?.exchangeAiNarrative)", js)
        self.assertIn("gmgnChineseNarrativeText(trenchAiNarrativeFor(row)?.exchangeAiNarrative)", js)
        self.assertNotIn("gmgnChineseNarrativeText(row?.gmgnNarrative)", js)

    def test_trench_board_requests_binance_ai_narrative_for_the_visible_page(self):
        js = (ROOT / "price-watch.js").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/exchange-ai-narratives"', js)
        self.assertIn('sourceId: "gmgn-trenches"', js)
        # Cached per contract so paging, re-rendering and hover stay local.
        self.assertIn("const trenchAiNarratives = new Map();", js)
        self.assertIn("TRENCH_AI_ITEMS_MAX", js)
        # Driven from the load path so a page change re-asks for its own rows.
        self.assertIn("void requestOnchainTrenchAi({ force: refresh });", js)
        self.assertIn("applyOnchainTrenchAiNarratives();", js)


if __name__ == "__main__":
    unittest.main()
