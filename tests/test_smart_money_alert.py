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
    def setUp(self):
        # 同一钱包+同一代币有 5 分钟合并窗口，逐条用例必须从干净台账开始，
        # 否则第二个用例会被上一条的弹窗合并掉。
        server.reset_smart_money_buy_popup_cluster()
        # 机会关键词联动会去问 GIGA X 桥接；单测默认把它打成空结果，既不打网络，
        # 也不让弹窗凭空多出「机会关键词」文案 / 红色调，干扰既有断言。
        self._gigax_patch = patch.object(
            server, "fetch_gigax_exchange_ai_narrative", return_value={}
        )
        self._gigax_patch.start()
        self.addCleanup(self._gigax_patch.stop)

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
        # 叙事已改成 GIGA X 优先：先问 GIGA X（这里 stub 成空），没命中才落到币安。
        with patch.object(server, "fetch_gigax_exchange_ai_narrative", return_value={}), \
                patch.object(server, "fetch_binance_wallet_ai_narrative",
                             return_value={"binanceAiNarrative": "stub",
                                           "binanceAiNarrativeStatus": "GENERATED"}) as stub:
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

    def test_opportunity_keywords_tint_the_popup_red_and_note_the_hits(self):
        # KOL 买入的币叙事命中机会关键词 → 正文点名命中的词 + 整条报红。
        with patch.object(
            server, "fetch_gigax_exchange_ai_narrative",
            return_value={"exchangeAiNarrative": "这是首个上线的发射台代币，属于新机制。"},
        ):
            payload = self._payload()
        self.assertEqual(payload["alertTone"], "red")
        self.assertIn("机会关键词命中", payload["body"])
        self.assertIn("首个", payload["body"])
        self.assertIn("发射台", payload["body"])

    def test_opportunity_keywords_are_silent_when_narrative_has_none(self):
        # 叙事没命中机会词 → 不报红、正文不夹带提示。
        with patch.object(
            server, "fetch_gigax_exchange_ai_narrative",
            return_value={"exchangeAiNarrative": "普通社区代币，未获官方背书。"},
        ):
            payload = self._payload()
        self.assertNotIn("alertTone", payload)
        self.assertNotIn("机会关键词命中", payload["body"])

    def test_opportunity_keywords_are_silent_when_narrative_is_missing(self):
        # 桥接没返回叙事（no-narrative / 网络抖动）→ 静默，不报红也不阻塞弹窗。
        payload = self._payload()  # setUp 已把 GIGA X stub 成 {}
        self.assertNotIn("alertTone", payload)
        self.assertNotIn("机会关键词命中", payload["body"])


class SmartMoneyPopupTargetTests(unittest.TestCase):
    """弹窗「查看」要落在 GMGN 的对应 CA 交易页，而不是链上交易记录查询页。

    用户是在 GMGN 上下单的，跳去 etherscan / blockscout 等于断了他的下一步。
    链上浏览器只在 GMGN 链接拼不出来时兜底，保证按钮不会变成死链接。
    """

    def setUp(self):
        server.reset_smart_money_buy_popup_cluster()
        self._gigax_patch = patch.object(
            server, "fetch_gigax_exchange_ai_narrative", return_value={}
        )
        self._gigax_patch.start()
        self.addCleanup(self._gigax_patch.stop)

    def _payload(self, event: dict | None = None) -> dict:
        captured: dict = {}

        def fake_launch(payload, *args, **kwargs):
            captured.update(payload)
            return {"ok": True}

        with patch.object(server, "launch_desktop_alert", side_effect=fake_launch):
            server.send_smart_money_buy_desktop_alert(event or sample_event())
        return captured

    def test_the_view_button_opens_the_gmgn_trade_page(self):
        payload = self._payload()
        self.assertEqual(
            payload["url"],
            f"https://gmgn.ai/robinhood/token/{HOODERS}",
        )

    def test_an_evm_explorer_link_is_not_the_target(self):
        payload = self._payload()
        self.assertNotIn("blockscout.com", payload["url"])
        self.assertNotIn("/tx/", payload["url"])

    def test_internal_chain_codes_are_folded_to_the_gmgn_slug(self):
        # ethereum / solana 是项目内部代号，GMGN 上叫 eth / sol。
        self.assertEqual(
            self._payload({**sample_event(), "chain": "ethereum"})["url"],
            f"https://gmgn.ai/eth/token/{HOODERS}",
        )
        self.assertEqual(
            self._payload({**sample_event(), "chain": "bsc"})["url"],
            f"https://gmgn.ai/bsc/token/{HOODERS}",
        )

    def test_an_elysium_buy_links_to_the_gmgn_trade_page(self):
        """用户 2026-11-09：「点击查看要跳转到 GMGN 的交易页」「不要跳转到 dexscreener」。

        GMGN 首日就接了 Elysium（它自己的 /v1/token/info 返回
        ``link.gmgn = https://gmgn.ai/elysium/token/<CA>``），所以这条链和其它链
        走完全相同的路径 —— 曾经那段 dexscreener 特判必须不再出现。
        """
        payload = self._payload({**sample_event(), "chain": "elysium"})
        self.assertEqual(payload["url"], f"https://gmgn.ai/elysium/token/{HOODERS}")
        self.assertNotIn("dexscreener", payload["url"])
        self.assertNotIn("/tx/", payload["url"])

    def test_a_solana_buy_links_to_the_sol_token_page(self):
        solana_event = {
            **sample_event(),
            "chain": "solana",
            "tokenAddress": "2heJbC32Tpfcb3nbUb5ER61K11FGZVfVGtVnDm6LDogF",
        }
        self.assertEqual(
            self._payload(solana_event)["url"],
            "https://gmgn.ai/sol/token/2heJbC32Tpfcb3nbUb5ER61K11FGZVfVGtVnDm6LDogF",
        )

    def test_the_explorer_is_only_the_fallback(self):
        # 掐断 GMGN 链接来验证回落分支：这时才允许出现链上交易记录页，
        # 否则「查看」会变成一个点了没反应的死按钮。
        tx_hash = "0x" + "ab" * 32
        with patch.object(server, "gmgn_token_trade_url", return_value=""):
            payload = self._payload()
        self.assertEqual(
            payload["url"],
            f"https://robinhoodchain.blockscout.com/tx/{tx_hash}",
        )

    def test_an_unknown_chain_yields_no_button_at_all(self):
        # 既没有 GMGN slug、也没有区块浏览器的链：宁可不给按钮，也不给死链。
        payload = self._payload({**sample_event(), "chain": "unknown-chain"})
        self.assertEqual(payload["url"], "")

    def test_the_token_page_keeps_the_contract_the_popup_copied(self):
        # 「查看」按钮会先复制 CA 再开页面，两者必须是同一个地址。
        payload = self._payload()
        self.assertEqual(payload["contractAddress"], HOODERS)


class SmartMoneyPopupClusterTests(unittest.TestCase):
    """同一钱包在同一代币上连续买入只弹一条（用户 2026-11-09「重复弹了」）。

    实测场景：钱包 0x7b3d…8d61 在 20 秒内分两笔 tx 各买 50 HYPE/$3,740 的同一个
    代币，弹出两条一模一样的卡片。``event_key`` 就是 tx hash，两笔天然不同 key，
    所以必须在投递前按 (链, 钱包, 代币) 合并。
    """

    def setUp(self):
        server.reset_smart_money_buy_popup_cluster()

    def _dispatch(self, event: dict) -> tuple[dict, dict]:
        captured: dict = {}

        def fake_launch(payload, *args, **kwargs):
            captured.update(payload)
            return {"ok": True}

        with patch.object(server, "launch_desktop_alert", side_effect=fake_launch):
            result = server.send_smart_money_buy_desktop_alert(event)
        return captured, result

    def _split_order(self, index: int) -> dict:
        return {
            **sample_event(),
            "chain": "elysium",
            "transactionHash": "0x" + f"{index:02x}" * 32,
            "eventKey": f"elysium:0x{index:02x}:{HOODERS}",
            "observedAt": 1_760_000_000_000 + index * 20_000,
        }

    def test_a_split_order_pops_only_once(self):
        first, _ = self._dispatch(self._split_order(1))
        second, result = self._dispatch(self._split_order(2))
        self.assertIn("HOODERS", first["title"])
        self.assertEqual(second, {}, "第二笔拆单不该再弹一条")
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("category"), "smart-money-cluster")

    def test_a_different_token_from_the_same_wallet_still_pops(self):
        self._dispatch(self._split_order(1))
        other, _ = self._dispatch({
            **self._split_order(2),
            "tokenAddress": "0x" + "11" * 20,
            "symbol": "OTHER",
            "eventKey": "elysium:other",
        })
        self.assertIn("OTHER", other["title"])

    def test_each_chain_keeps_its_own_window(self):
        self._dispatch(self._split_order(1))
        other_chain, _ = self._dispatch({**self._split_order(2), "chain": "bsc"})
        self.assertIn("HOODERS", other_chain["title"])

    def test_the_next_popup_reports_what_was_merged(self):
        # 窗口过期后的第一条要把上一窗口被合并掉的笔数/金额写在正文里，
        # 否则用户永远不知道那 5 分钟里还有几笔。
        key = f"elysium:{KOL_WALLET.lower()}:{HOODERS.lower()}"
        server.SMART_MONEY_BUY_POPUP_LEDGER[key] = {"at": 0.0, "count": 2.0, "usd": 7_480.0}
        payload, _ = self._dispatch(self._split_order(3))
        self.assertIn("另 2 笔", payload["body"])
        self.assertIn("$7,480", payload["body"])

    def test_the_body_shortens_the_wallet_address(self):
        # 弹窗正文只有一行：42 字符的完整地址会被挤出可视区域，用户看到的
        # 就是「钱包」后面一片空白（2026-11-09 截图）。
        payload, _ = self._dispatch(sample_event())
        self.assertNotIn(KOL_WALLET, payload["body"])
        self.assertIn("0xcc29…f5a9", payload["body"])

    def test_a_wallet_without_a_nickname_keeps_a_readable_title(self):
        payload, _ = self._dispatch({**sample_event(), "walletNickname": ""})
        self.assertIn("KOL 0xcc29…f5a9", payload["title"])
        self.assertNotIn(KOL_WALLET, payload["title"])


if __name__ == "__main__":
    unittest.main()
