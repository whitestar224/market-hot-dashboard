"""弹窗标链：把「这个币在哪条链」写到每一张含币的弹窗上（用户 2026-10-17 要求）。

难点不在文案，在于 ``chain`` 在各类事件里有四种互不兼容的形态：

  * 数字 chainId / 币安钱包链号 —— ``"1"`` / ``"56"`` / ``"8453"`` / ``"4663"`` / ``"CT_501"``
  * GMGN slug —— ``"sol"`` / ``"bsc"`` / ``"eth"`` / ``"robinhood"``
  * 项目内部代号 —— ``"ethereum"`` / ``"solana"`` / ``"base"``
  * 展示标签 —— ``"BSC"`` / ``"Solana"`` / ``"Robinhood"``

不归一就会出现同一个 Solana 在三个弹窗里分别写成 SOL / CT_501 / solana。反之也不许
瞎猜：拿不到证据的**留空**（X 推文、快讯那种跨界事件），只在交易所行情里出现的写
「中心化」——那是「没有链上合约」，不是「不知道哪条链」。
"""
import unittest
from unittest.mock import patch

import server


class ChainLabelNormalizationTests(unittest.TestCase):
    def test_numeric_chain_ids_become_display_labels(self):
        cases = {
            "1": "ETH",
            "56": "BSC",
            "8453": "Base",
            "4663": "Robinhood",
            "42161": "Arbitrum",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(server.desktop_alert_chain_label(raw), expected)

    def test_coinbase_wallet_style_chain_id_is_solana(self):
        # 币安钱包 / GMGN 的链号前缀形态，42 个弹窗里混着这种值。
        self.assertEqual(server.desktop_alert_chain_label("CT_501"), "Solana")

    def test_gmgn_slugs_and_internal_codes_land_on_the_same_label(self):
        for raw in ("sol", "solana", "SOL"):
            with self.subTest(raw=raw):
                self.assertEqual(server.desktop_alert_chain_label(raw), "Solana")
        for raw in ("eth", "ethereum", "mainnet"):
            with self.subTest(raw=raw):
                self.assertEqual(server.desktop_alert_chain_label(raw), "ETH")
        for raw in ("bsc", "bnb", "BNB Chain", "binance-smart-chain"):
            with self.subTest(raw=raw):
                self.assertEqual(server.desktop_alert_chain_label(raw), "BSC")

    def test_display_labels_pass_through_unchanged(self):
        self.assertEqual(server.desktop_alert_chain_label("BSC"), "BSC")
        self.assertEqual(server.desktop_alert_chain_label("Robinhood"), "Robinhood")

    def test_placeholders_are_not_chains(self):
        for raw in ("", "  ", "链上", "chain", "unknown", "多链", None):
            with self.subTest(raw=raw):
                self.assertEqual(server.desktop_alert_chain_label(raw), "")

    def test_an_unknown_bare_chain_id_is_not_echoed_to_the_popup(self):
        # 监控池里真出现过 onchain_chain="5042"；把裸数字印在弹窗上等于没标。
        self.assertEqual(server.desktop_alert_chain_label("5042"), "")


class AlertSymbolExtractionTests(unittest.TestCase):
    """标链的前提是先知道「这条弹窗说的是哪个币」，币名散落在 6 种 key 写法里。"""

    def symbol(self, key, **fields):
        return server.desktop_alert_asset_symbol({"key": key, **fields})

    def test_symbols_are_read_from_every_key_shape(self):
        cases = {
            "price-watch:structure-first:EPIC:5m:c4ef4ade": "EPIC",
            "price-watch:dragon-wave-prearm:BSP:1m-1791436020000": "BSP",
            "gainers-leader:binance-gainers|epic": "EPIC",
            "first-listing:BATON:1791805437000": "BATON",
            "venue-listing:okx-new:DNA:1792152000000": "DNA",
            "listing:futu-us-cty|富途|city therapeutics|cty|1792080000000": "CTY",
            "rank-monitor:aicoin|hot:crypto:DNA|1792080000000": "DNA",
            "market-leader:binance|PEPE|1792080000000": "PEPE",
        }
        for key, expected in cases.items():
            with self.subTest(key=key):
                self.assertEqual(self.symbol(key), expected)

    def test_an_explicit_symbol_field_wins(self):
        self.assertEqual(self.symbol("flash:1", symbol="PEPE"), "PEPE")

    def test_news_without_a_coin_has_no_symbol(self):
        self.assertEqual(self.symbol("flash:1792152000:blockbeats", title="某项目完成融资"), "")

    def test_a_binance_wallet_hot_key_does_not_become_a_fake_symbol(self):
        """币安钱包热榜的 key 是 ``…:wallet:56:0xea37…``——冒号段是链号 + 合约。

        曾经被硬当币名，抽出 ``560XEA37A8DE1DE2D9D1`` 这种怪物，于是 Alpha 身份、
        监控池、链兜底全线查不到（2026-10-21 实测）。这类 key 本身不含币名。
        """
        key = (
            "rank-monitor:hot|binance-wallet-hot:wallet:56:"
            "0xea37a8de1de2d9d10772eeb569e28bfa5cb17707|4h|new|10|5|56.17|10"
        )
        self.assertEqual(self.symbol(key, title="币安钱包4小时热门榜新进：JCT"), "JCT")
        # 没有标题可兜底时宁可空着，也不给一个假 symbol
        self.assertEqual(self.symbol(key), "")


class ChainAnnotationTests(unittest.TestCase):
    def setUp(self):
        # 每条用例都断言"兜底查了没、查到什么"，禁止真的去读监控池。
        patcher = patch.object(server, "price_watch_chain_map", return_value={})
        self.chain_map = patcher.start()
        self.addCleanup(patcher.stop)
        alpha_patcher = patch.object(server, "ALPHA_LISTED_CHAINS", {})
        alpha_patcher.start()
        self.addCleanup(alpha_patcher.stop)

    def annotate(self, item, **kwargs):
        return server.desktop_alert_chain_annotation(item, **kwargs)

    def test_event_chain_wins_over_every_fallback(self):
        item = {"chain": "56", "contractAddress": "0x" + "a" * 40, "symbol": "AAA"}
        self.assertEqual(self.annotate(item), "BSC")

    def test_chain_label_is_preferred_and_normalized(self):
        item = {"chainLabel": "ct_501", "symbol": "AAA"}
        self.assertEqual(self.annotate(item), "Solana")

    def test_a_base58_contract_without_0x_means_solana(self):
        # 无链、无池记录的链上行，只能靠地址形态判：0x 开头是 EVM，base58 是 Solana。
        item = {"contractAddress": "3iQL8BFS2vE7mww4ehAqQHAsbmRNCrPxizWAT2Zfyr9y"}
        self.assertEqual(self.annotate(item), "Solana")

    def test_alpha_inventory_supplies_the_chain_when_the_popup_has_a_contract(self):
        # 币安钱包/链上榜单的行带合约，只是没带链：这时用 Alpha 清单的 asset→链。
        with patch.object(server, "ALPHA_LISTED_CHAINS", {"DNA": "Base"}):
            item = {
                "key": "rank-monitor:hot|binance-wallet-hot:wallet:1:0xabc|4h|new",
                "symbol": "DNA",
                "contractAddress": "0x" + "b" * 40,
            }
            self.assertEqual(self.annotate(item), "Base")

    def test_a_cex_rank_entry_never_borrows_a_chain_from_the_alpha_inventory(self):
        """成交额榜/热门榜的标的是交易所标的，没有合约，不可以拿「同名币」的链顶上。"""
        with patch.object(server, "ALPHA_LISTED_CHAINS", {"XRP": "BSC"}):
            item = {
                "key": "rank-monitor:turnover|binance:crypto:xrp|new",
                "sourceId": "binance",
                "title": "成交额榜新进：XRP",
            }
            self.assertEqual(self.annotate(item), "中心化")

    def test_monitor_pool_supplies_the_chain_for_watchlist_popups(self):
        self.chain_map.return_value = {"PEPE": "ETH"}
        item = {"key": "price-watch:structure-first:PEPE:1h:x", "symbol": "pepe"}
        self.assertEqual(self.annotate(item), "ETH")

    def test_pool_lookup_can_be_switched_off_for_the_persist_only_path(self):
        self.chain_map.return_value = {"PEPE": "ETH"}
        item = {"key": "price-watch:structure-first:PEPE:1h:x", "symbol": "PEPE"}
        self.assertEqual(self.annotate(item), "ETH")
        self.chain_map.reset_mock()
        # persist_only 分支不许做额外 I/O，所以宁可标不出来也不查池。
        self.assertEqual(self.annotate(item, allow_pool_lookup=False), "")
        self.chain_map.assert_not_called()

    def test_a_cex_only_coin_is_marked_centralized_not_left_blank(self):
        item = {
            "key": "rank-monitor:hot|aicoin:crypto:sndk|new",
            "sourceId": "aicoin",
            "symbol": "SNDK",
            "title": "热门榜新进：SNDK",
        }
        self.assertEqual(self.annotate(item), "中心化")

    def test_a_news_flash_never_gets_a_chain_even_if_the_title_mentions_a_ticker(self):
        """快讯只是「提到」币，不是「讲」这个币：标题尾部抽出来的 AI / 7 全是噪音。"""
        self.chain_map.return_value = {"AI": "Robinhood", "7": "Solana"}
        for item in (
            {"key": "flash:1792152000:bk", "title": "马斯克：美债危机解决的唯一办法是AI和机器人"},
            {"key": "flash:1792152001:bk", "title": "谷歌Gemini 4正式开放：看段视频就能教机器人投沙包，连中7次"},
            {"key": "x-kol:1792152002", "title": "黄仁勋：AI将带来变革"},
        ):
            with self.subTest(item=item):
                self.assertEqual(self.annotate(item), "")

    def test_a_cex_rank_entry_does_not_borrow_the_pool_chain_of_a_same_ticker_token(self):
        """线上真实翻车：成交额榜新进 XRP 被标成 BSC。

        池里那条 XRP 是 BSC 上的桥接代币（0x1d2f0da1…），和币安现货 XRP 是两回事。
        交易所榜单里的标的没有合约，只能说「中心化」。
        """
        self.chain_map.return_value = {"XRP": "BSC"}
        item = {
            "key": "rank-monitor:turnover|binance:crypto:xrp|new",
            "sourceId": "binance",
            "kind": "成交额榜新进",
            "title": "成交额榜新进：XRP",
        }
        self.assertEqual(self.annotate(item), "中心化")

    def test_a_rank_entry_that_carries_a_contract_still_uses_the_pool_chain(self):
        self.chain_map.return_value = {"POP": "BSC"}
        item = {
            "key": "rank-monitor:hot|binance-wallet-hot:wallet:56:0xa3cf|4h|new",
            "sourceId": "binance-wallet-hot",
            "contractAddress": "0xa3cfb853339b77f385b994799b015cb04b208fe6",
            "symbol": "POP",
            "title": "币安钱包4小时热门榜新进：POP",
        }
        self.assertEqual(self.annotate(item), "BSC")

    def test_stock_and_tradfi_popups_never_get_a_chain(self):
        for item in (
            {"symbol": "NVDA", "sourceId": "futu", "title": "美股异动：NVDA"},
            {"symbol": "XAU", "source": "东方财富", "title": "黄金走高"},
        ):
            with self.subTest(item=item):
                self.assertEqual(self.annotate(item), "")

    def test_a_pool_member_without_a_chain_record_is_centralized(self):
        # 池里的空链记录 = 从交易所合约上新进来的币，没有链上合约。
        self.chain_map.return_value = {"EPIC": ""}
        self.assertEqual(self.annotate({"key": "price-watch:fib:EPIC:0.618", "title": "EPIC"}), "中心化")

    def test_a_coin_outside_the_pool_is_left_blank(self):
        # 池子里没有 = 查不到任何证据，宁可空着。
        self.chain_map.return_value = {"EPIC": "ETH"}
        self.assertEqual(self.annotate({"key": "price-watch:fib:ZZZ:0.618", "title": "ZZZ"}), "")

    def test_an_alpha_listed_coin_beats_the_pool_record(self):
        self.chain_map.return_value = {"DNA": "中心化"}
        with patch.object(server, "ALPHA_LISTED_CHAINS", {"DNA": "Base"}):
            self.assertEqual(self.annotate({"key": "price-watch:fib:DNA:0.618"}), "Base")

    def test_nothing_known_leaves_it_blank_instead_of_guessing(self):
        # 快讯 / 推文类跨界事件：不硬标一条链。
        item = {"title": "某项目宣布完成融资", "sourceId": "news"}
        self.assertEqual(self.annotate(item), "")

    def test_annotate_is_idempotent_and_keeps_an_existing_label(self):
        item = server.annotate_desktop_alert_chain({"chainLabel": "sol", "symbol": "AAA"})
        self.assertEqual(item["chainLabel"], "Solana")
        again = server.annotate_desktop_alert_chain(item)
        self.assertEqual(again, item)

    def test_annotate_leaves_the_payload_untouched_when_it_cannot_tell(self):
        item = {"title": "某项目宣布完成融资", "sourceId": "news"}
        server.annotate_desktop_alert_chain(item)
        self.assertNotIn("chainLabel", item)


class PopupPayloadCarriesTheChainTests(unittest.TestCase):
    def test_normalize_keeps_the_chain_and_the_symbol(self):
        # normalize 是白名单式返回：没显式加进去的字段会被丢掉，渲染层就无从显示。
        normalized = server.normalize_desktop_alert({
            "title": "Binance Alpha 新币新进：DNA",
            "chain": "56",
            "chainLabel": "BSC",
            "symbol": "DNA",
        })
        self.assertEqual(normalized["chain"], "56")
        self.assertEqual(normalized["chainLabel"], "BSC")
        self.assertEqual(normalized["symbol"], "DNA")

    def test_normalize_accepts_network_as_a_chain_label(self):
        normalized = server.normalize_desktop_alert({"title": "t", "network": "sol"})
        self.assertEqual(normalized["chainLabel"], "sol")


class AlphaPhaseAnnotationTests(unittest.TestCase):
    """「这币在 Alpha 清单里」要能进弹窗。

    用户 2026-10-21 原话：「可是 JCT 是 alpha 里的啊」。交易所榜单的标的不带合约，
    ``chainLabel`` 只能保守地写「中心化」——但 Alpha 清单同样来自币安官方，查它不算
    「拿同名币的链顶包」，所以这层事实走独立的 ``alphaPhase`` 字段。
    """

    def setUp(self):
        patcher = patch.object(server, "price_watch_chain_map", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def annotate(self, item, chains):
        with patch.object(server, "ALPHA_LISTED_CHAINS", chains):
            return server.annotate_desktop_alert_chain(dict(item))

    def test_a_cex_rank_coin_listed_on_alpha_is_marked(self):
        out = self.annotate(
            {
                "key": "gainers-leader:binance-futures-gainers|jct",
                "source": "Binance 合约涨幅榜",
                "kind": "涨幅榜异动",
                "title": "BN 涨幅榜榜首变为 JCT",
            },
            {"JCT": "BSC"},
        )
        # 链标注仍然保守：没有合约的交易所标的只能写「中心化」
        self.assertEqual(out["chainLabel"], "中心化")
        # 但「在 Alpha、清单上标 BSC」这层事实必须看得见
        self.assertEqual(out["alphaPhase"], "Alpha · BSC")

    def test_a_coin_outside_the_alpha_inventory_gets_no_phase(self):
        out = self.annotate(
            {
                "key": "gainers-leader:binance-futures-gainers|zzz",
                "title": "BN 涨幅榜榜首变为 ZZZ",
            },
            {"JCT": "BSC"},
        )
        self.assertNotIn("alphaPhase", out)
        self.assertEqual(out["chainLabel"], "中心化")

    def test_alpha_mm_payloads_keep_their_own_phase_wording(self):
        """alpha-mm-* 自己写的「已下架 · 已转币安现货」不许被「Alpha · BSC」覆盖。"""
        out = self.annotate(
            {
                "key": "alpha-mm-seed:BMT",
                "symbol": "BMT",
                "chain": "BSC",
                "alphaPhase": "Alpha 已下架 · 已转币安现货",
            },
            {"BMT": "BSC"},
        )
        self.assertEqual(out["alphaPhase"], "Alpha 已下架 · 已转币安现货")

    def test_news_flashes_never_get_an_alpha_phase(self):
        """快讯只是「提到」币，不在标链作用域里，更不该挂 Alpha 身份。"""
        out = self.annotate(
            {"key": "flash:1792152000:bk", "title": "JCT 上线某交易所"}, {"JCT": "BSC"}
        )
        self.assertNotIn("alphaPhase", out)

    def test_an_alpha_entry_without_a_chain_still_gets_a_label(self):
        # 清单里有它、但上游那条没给链（ALPHA_LISTED_CHAINS 只收有链的条目）：
        # 仍然要告诉用户「这币在 Alpha」。
        with patch.object(server, "ALPHA_LISTED_CHAINS", {}), patch.object(
            server, "ALPHA_LISTED_ASSETS", frozenset({"EPIC"})
        ):
            out = server.annotate_desktop_alert_chain(
                {"key": "gainers-leader:binance-gainers|epic", "title": "BN 涨幅榜榜首变为 EPIC"}
            )
        self.assertEqual(out["alphaPhase"], "Alpha 在列")


class AlphaRedSkinTests(unittest.TestCase):
    """BSC 链的 Alpha 币要染红 + 正文补那句风险提示（用户 2026-10-21 放开范围）。

    2026-10-17 曾收紧到「只属于 Alpha 新币榜自己」（币安钱包热榜的 POP 被套上那句话
    那次），现在按用户要求放开：只看「在 Alpha 清单里 + 清单标的是 BSC」，不看来源。
    纪律不变——**只换皮肤**，不动 queuePriority、不绕静默。
    """

    def setUp(self):
        patcher = patch.object(server, "price_watch_chain_map", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def annotate(self, item, chains):
        with patch.object(server, "ALPHA_LISTED_CHAINS", chains), patch.object(
            server, "ALPHA_LISTED_ASSETS", frozenset(chains)
        ):
            return server.annotate_desktop_alert_chain(
                server.normalize_desktop_alert(dict(item))
            )

    def bsc_item(self, **overrides):
        item = {
            "key": "gainers-leader:binance-futures-gainers|jct",
            "kind": "涨幅榜异动",
            "title": "BN 涨幅榜榜首变为 JCT",
            "body": "JCTUSDT / $0.002621 / +37.73% / $22.25M",
        }
        item.update(overrides)
        return item

    def test_a_bsc_alpha_coin_gets_the_red_skin_and_the_note(self):
        out = self.annotate(self.bsc_item(), {"JCT": "BSC"})
        self.assertEqual(out["alertTone"], "red")
        self.assertIn(server.ALPHA_NEW_RISK_NOTE, out["body"])
        self.assertTrue(out["body"].startswith("JCTUSDT"))
        self.assertEqual(out["alphaPhase"], "Alpha · BSC")

    def test_a_non_bsc_alpha_coin_stays_normal(self):
        out = self.annotate(self.bsc_item(**{
            "title": "BN 涨幅榜榜首变为 CTR",
            "body": "CTRUSDT / $0.05 / +3.2% / $1.1M",
        }), {"CTR": "Base"})
        self.assertEqual(out["alertTone"], "normal")
        self.assertNotIn(server.ALPHA_NEW_RISK_NOTE, out["body"])

    def test_a_coin_outside_the_inventory_stays_normal(self):
        out = self.annotate(self.bsc_item(), {"OTHER": "BSC"})
        self.assertEqual(out["alertTone"], "normal")
        self.assertNotIn(server.ALPHA_NEW_RISK_NOTE, out["body"])

    def test_a_long_body_still_keeps_the_whole_note(self):
        # 正文在 normalize 里就被截到 180 字，直接往后接会砍掉半句提示。
        out = self.annotate(self.bsc_item(body="甲" * 175), {"JCT": "BSC"})
        self.assertLessEqual(len(out["body"]), 180)
        self.assertTrue(out["body"].endswith(server.ALPHA_NEW_RISK_NOTE))

    def test_the_note_is_not_added_twice(self):
        body = f"JCTUSDT / +37% / {server.ALPHA_NEW_RISK_NOTE}"
        out = self.annotate(self.bsc_item(body=body), {"JCT": "BSC"})
        self.assertEqual(out["body"].count(server.ALPHA_NEW_RISK_NOTE), 1)

    def test_the_skin_never_touches_queue_priority(self):
        out = self.annotate(self.bsc_item(queuePriority=60), {"JCT": "BSC"})
        self.assertEqual(out["queuePriority"], 60)
        self.assertEqual(out["alertTone"], "red")


if __name__ == "__main__":
    unittest.main()
