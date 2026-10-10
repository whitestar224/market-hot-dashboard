"""聪明钱买入弹窗：KOL + 买入的币 + 币安 AI 叙事按钮。

弹窗上的 ✦ 币安 AI 叙事按钮由 ``contractAddress`` / ``chain`` / ``sourceId``
三者同时存在才渲染（desktop_alert.show_popup），而服务端
``exchange_ai_narrative_for_item`` 又以 ``sourceId in
EXCHANGE_AI_NARRATIVE_ALLOWED_SOURCES`` 为硬门槛，不在集合里直接返回
UNAVAILABLE。所以这三点必须一起被钉在测试里。
"""

import unittest
from unittest.mock import patch

import server

KOL_WALLET = "0xcc291dcd83bd9f2600cca4d65ae3b724eaf4f5a9"
HOODERS = "0x316fa3ab9a8fd8d7567a823dedecf28d9fee2894"
SMART_MONEY_SOURCE_ID = "smart-money-buy"


def sample_event() -> dict:
    return {
        "chain": "robinhood",
        "walletAddress": KOL_WALLET,
        "walletNickname": "CryptoCharming",
        "transactionHash": "0x" + "ab" * 32,
        "tokenAddress": HOODERS,
        "symbol": "HOODERS",
        "tokenAmount": 1234.5,
        "paymentAsset": "ETH",
        "paymentAmount": 3.2,
        "paymentUsd": 12_345.0,
        "priceUsd": 10.0,
        "eventKey": "robinhood:0xabc:0xdef",
        "observedAt": 1_760_000_000_000,
    }


class SmartMoneyAlertPayloadTests(unittest.TestCase):
    def _payload(self, event: dict | None = None) -> dict:
        captured: dict = {}

        def fake_launch(payload, *args, **kwargs):
            captured.update(payload)
            return {"ok": True}

        with patch.object(server, "launch_desktop_alert", side_effect=fake_launch):
            server.send_smart_money_buy_desktop_alert(event or sample_event())
        return captured

    def test_popup_shows_kol_name_and_bought_token(self):
        payload = self._payload()
        self.assertIn("CryptoCharming", payload["title"])
        self.assertIn("HOODERS", payload["title"])
        self.assertEqual(payload["kind"], "聪明钱买入")
        self.assertEqual(payload["sourceType"], SMART_MONEY_SOURCE_ID)

    def test_popup_carries_ai_narrative_identity_triple(self):
        payload = self._payload()
        # The three fields desktop_alert.show_popup requires before it renders
        # the ✦ Binance AI narrative button.
        self.assertEqual(payload["sourceId"], SMART_MONEY_SOURCE_ID)
        self.assertEqual(payload["chain"], "robinhood")
        self.assertEqual(payload["contractAddress"], HOODERS)

    def test_ai_narrative_source_is_allowed_server_side(self):
        self.assertIn(SMART_MONEY_SOURCE_ID, server.EXCHANGE_AI_NARRATIVE_ALLOWED_SOURCES)

    def test_robinhood_resolves_to_binance_chain_id(self):
        self.assertEqual(server.normalize_exchange_ai_binance_chain("robinhood"), "4663")

    def test_ai_narrative_lookup_is_not_short_circuited(self):
        # exchange_ai_narrative_for_item returns UNAVAILABLE before any provider
        # call when the source id is missing from the allow-list.  Reach the
        # provider layer with a stub so the test never hits the network.
        with patch.object(server, "fetch_binance_wallet_ai_narrative", return_value={"exchangeAiNarrative": "stub"}) as stub:
            server.exchange_ai_narrative_for_item({
                "key": "smart-money-buy:robinhood:0xabc:0xdef",
                "sourceId": SMART_MONEY_SOURCE_ID,
                "chain": "robinhood",
                "contractAddress": HOODERS,
            })
        self.assertTrue(stub.called, "source id was rejected before reaching the provider")

    def test_payload_survives_normalization(self):
        normalized = server.normalize_desktop_alert(self._payload())
        self.assertEqual(normalized["sourceId"], SMART_MONEY_SOURCE_ID)
        self.assertEqual(normalized["chain"], "robinhood")
        self.assertEqual(normalized["contractAddress"], HOODERS)
        self.assertEqual(normalized["sourceType"], SMART_MONEY_SOURCE_ID)

    def test_normalized_popup_maps_to_smartmoney_board(self):
        normalized = server.normalize_desktop_alert(self._payload())
        self.assertEqual(server.price_watch_alert_board(normalized), "smartmoney")

    def test_popup_is_not_suppressed_by_position_change_filter(self):
        normalized = server.normalize_desktop_alert(self._payload())
        self.assertEqual(server.desktop_alert_political_military_reason(normalized), "")

    def test_popup_prints_the_wallets_own_alert_line(self):
        # The popup line is PER WALLET (100U for a small KOL wallet), so the card
        # must not keep advertising a fixed 10,000U.  record_buy stamps the
        # threshold onto event["details"].
        payload = self._payload({**sample_event(), "details": {"alertThresholdUsd": 100.0}})
        self.assertEqual(payload["priority"], "单笔 ≥ 100U")

    def test_popup_falls_back_to_the_default_line_without_a_threshold(self):
        payload = self._payload()
        self.assertEqual(payload["priority"], "单笔 ≥ 10,000U")


if __name__ == "__main__":
    unittest.main()
