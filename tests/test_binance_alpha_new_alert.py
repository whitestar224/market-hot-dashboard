"""Binance Alpha「新币新进」弹窗（用户 2026-10-16 要求）。

Alpha 是币安钱包生态的早鸟收录区，按设计**不参与**「全球首发」判定
（listing_alerts.NON_OWNER_SOURCES）——它的收录时间常常早于主站上新，若参与会把
币安/OKX 的正式上新弹窗永久压掉。但用户要求 Alpha 榜自己的新进也要弹，于是给它
一条独立的按榜「新进」策略：

  * 事件源 = Alpha 榜行自身的出现，key = ``alpha-new:<资产>``（幂等，一币一次）；
  * 服务启动首轮只建基线（sync_site_alert_feed 的 is_ready 门禁），不补弹停机积压；
  * 弹窗文案统一带风险提示「合约慎做，现货为主」。

同时回归保护两点：Alpha 的 proof 语义必须与 first/venue listing 隔离；该 feed 不能
挂到监控看板开关上（否则 board 关掉会连弹窗一起短路）。
"""
import time
import unittest
from unittest.mock import patch

import server

MINUTE = 60_000


def alpha_section(rows):
    return {
        "id": "binance-alpha-new",
        "title": "Binance Alpha 新币榜",
        "group": "new-coin",
        "status": "ok",
        "rows": rows,
    }


def alpha_row(symbol, *, stamp=0, name="", price="$0.27", change="+12.5%",
              turnover="$38.75M", chain="BSC", asset_type="crypto", chain_field=True):
    row = {
        "rank": 1,
        "group": "new-coin",
        "asset": symbol,
        "symbol": symbol,
        "name": name or symbol,
        "price": price,
        "change": change,
        "amount": 38_750_000,
        "turnover": turnover,
        "date": stamp,
        "assetType": asset_type,
        "tags": ["Binance Alpha", chain],
        "url": "https://www.binance.com/zh-CN/alpha",
    }
    if chain_field:
        row["chain"] = chain
    return row


class BinanceAlphaParserTests(unittest.TestCase):
    def parse(self, sections):
        return server.parse_binance_alpha_new_events({"updatedAt": int(time.time() * 1000), "sections": sections})

    def test_alpha_row_becomes_a_new_entry_popup_with_the_risk_note(self):
        events = self.parse([alpha_section([alpha_row("DNA")])])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["key"], "alpha-new:DNA")
        self.assertEqual(event["kind"], "Binance Alpha 新币新进")
        self.assertEqual(event["title"], "Binance Alpha 新币新进：DNA")
        self.assertIn(server.ALPHA_NEW_RISK_NOTE, event["body"])
        self.assertIn(server.ALPHA_NEW_RISK_NOTE, event["speech"])
        self.assertEqual(event["listingPolicyVersion"], 1)
        self.assertGreater(event["expiresAt"], int(time.time() * 1000))
        self.assertIn("新币", event["kind"])

    def test_only_the_alpha_section_is_consumed(self):
        events = self.parse([
            alpha_section([alpha_row("DNA")]),
            {"id": "binance-new", "title": "Binance 新币榜", "status": "ok",
             "rows": [alpha_row("CT"), alpha_row("AAA")]},
        ])
        self.assertEqual([event["key"] for event in events], ["alpha-new:DNA"])

    def test_key_is_stable_across_cycles_so_delivery_dedupes(self):
        sections = [alpha_section([alpha_row("DNA")])]
        self.assertEqual(self.parse(sections)[0]["key"], self.parse(sections)[0]["key"])

    def test_missing_listing_timestamp_still_produces_a_stable_key(self):
        # Alpha's listing stamp is date-grained and may be absent; the key must not
        # then depend on the poll clock, or every 60s cycle would pop again.
        events = self.parse([alpha_section([alpha_row("DNA", stamp=0)])])
        self.assertEqual(events[0]["key"], "alpha-new:DNA")

    def test_alpha_popup_carries_the_chain_reminder(self):
        # 用户 2026-10-17：Alpha 是多链收录（不是只有 BSC），弹窗要报出这个币对应的链。
        event = self.parse([alpha_section([alpha_row("DRV", chain="Base")])])[0]
        self.assertIn("Base 链", event["body"])
        self.assertIn("Base 链", event["speech"])
        self.assertEqual(event["chain"], "Base")

    def test_chain_alias_and_missing_chain_are_handled(self):
        self.assertEqual(server.alpha_chain_label("BNB Chain"), "BSC")
        self.assertEqual(server.alpha_chain_label("ethereum"), "ETH")
        self.assertEqual(server.alpha_chain_label("sol"), "Solana")
        # tags 里的占位符「链上」不是链名，不能拼出「链上 链」。
        self.assertEqual(server.alpha_chain_label("链上"), "")
        quiet = self.parse([alpha_section([alpha_row("DNA", chain="")])])[0]
        self.assertEqual(quiet["chain"], "")
        self.assertNotIn("链", quiet["body"])
        self.assertNotIn("链", quiet["speech"])

    def test_bsc_alpha_entry_is_red_while_other_chains_stay_normal(self):
        # 用户 2026-10-17：Alpha 里 BSC 链那一档要报红。渲染层只认 alertTone=="red"。
        bsc = self.parse([alpha_section([alpha_row("DNA", chain="BSC")])])[0]
        self.assertEqual(bsc["alertTone"], "red")
        for chain in ("Base", "Solana", "Robinhood", "ETH"):
            other = self.parse([alpha_section([alpha_row("X", chain=chain)])])[0]
            # 只有 BSC 会写这个字段；其余留空，由 normalize 落回默认的 "normal"。
            self.assertIsNone(other.get("alertTone"), chain)
            self.assertEqual(server.normalize_desktop_alert(other)["alertTone"], "normal", chain)

    def test_bsc_alias_also_turns_red(self):
        # 上游有时写 "BNB Chain"；归一化之后必须照样认成 BSC。
        event = self.parse([alpha_section([alpha_row("DNA", chain="BNB Chain")])])[0]
        self.assertEqual(event["chain"], "BSC")
        self.assertEqual(event["alertTone"], "red")

    def test_red_tone_does_not_skip_the_mute_gate(self):
        # 红色只是皮肤：它不许变成绕过静默的通行证（发射台档的教训）。
        event = self.parse([alpha_section([alpha_row("DNA", chain="BSC")])])[0]
        self.assertFalse(server.desktop_alert_source_is_muted(event))

    def test_chain_falls_back_to_tags_for_older_cached_rows(self):
        row = alpha_row("XDP", chain="Base")
        row.pop("chain")  # 老缓存的行没有独立 chain 字段
        event = self.parse([alpha_section([row])])[0]
        self.assertEqual(event["chain"], "Base")
        self.assertIn("Base 链", event["body"])

    def test_tradfi_rows_are_never_alerted(self):
        self.assertEqual(self.parse([alpha_section([alpha_row("AAPL", asset_type="tradfi")])]), [])

    def test_blank_or_unknown_sections_are_silent(self):
        self.assertEqual(self.parse([]), [])
        self.assertEqual(self.parse([{"id": "aster-new", "status": "ok", "rows": []}]), [])


class BinanceAlphaMuteTests(unittest.TestCase):
    now_ms = int(time.time() * 1000)

    def event(self, **overrides):
        base = {
            "key": "alpha-new:DNA",
            "kind": "Binance Alpha 新币新进",
            "title": "Binance Alpha 新币新进：DNA",
            "source": "Binance Alpha",
            "sourceLabel": "BA",
            "sourceId": "binance-alpha-new",
            "listingPolicyVersion": 1,
            "expiresAt": self.now_ms + 60 * MINUTE,
        }
        base.update(overrides)
        return base

    def test_alpha_new_entry_is_delivered(self):
        self.assertFalse(server.desktop_alert_source_is_muted(self.event()))

    def test_expired_or_wrong_policy_event_is_muted(self):
        self.assertTrue(server.desktop_alert_source_is_muted(self.event(expiresAt=1)))
        self.assertTrue(server.desktop_alert_source_is_muted(self.event(listingPolicyVersion=0)))

    def test_alpha_event_is_not_treated_as_a_wallet_hot_board_row(self):
        # 「币安钱包」字样会让 4h 之外的钱包热门榜事件被静默；Alpha 事件不是钱包热榜行。
        self.assertFalse(server.desktop_alert_source_is_muted(self.event(
            title="Binance Alpha 新币新进：DNA · 币安钱包生态早鸟收录")))
        self.assertEqual(server.price_watch_alert_board(self.event()), "")


class BinanceAlphaFeedTests(unittest.TestCase):
    def test_feed_is_registered_off_the_monitor_board_switches(self):
        feeds = {feed["name"]: feed for feed in server.site_alert_feeds()}
        self.assertIn("binance-alpha-new", feeds)
        # 挂在 PRICE_WATCH_SITE_FEED_BOARDS 上会被看板开关短路整轮轮询，弹窗随之消失。
        self.assertNotIn("binance-alpha-new", server.PRICE_WATCH_SITE_FEED_BOARDS)
        self.assertIs(feeds["binance-alpha-new"]["parse"], server.parse_binance_alpha_new_events)

    def test_first_cycle_only_builds_the_baseline_then_a_new_entry_pops(self):
        state = {"seen": {}, "ready": [], "newboardReadyScopes": [], "xTweetAnalysisWatermarks": {}}
        payload = {"updatedAt": int(time.time() * 1000),
                   "sections": [alpha_section([alpha_row("DNA"), alpha_row("DRV")])]}
        feed = {"name": "binance-alpha-new", "interval": 60, "maxAgeMs": server.ALPHA_NEW_ALERT_TTL_MS,
                "fetch": lambda: payload, "parse": server.parse_binance_alpha_new_events}
        popped = []
        with patch.object(server, "SITE_ALERT_STATE", state), \
             patch.object(server, "SITE_ALERT_STATE_LOADED", True), \
             patch.object(server, "save_site_alert_state", lambda: None), \
             patch.object(server, "launch_desktop_alert",
                          side_effect=lambda event: popped.append(event["key"]) or {"ok": True}):
            server.sync_site_alert_feed(feed)
            # 首轮：整榜都是「基线」，不许弹（否则重启一次就冲屏）。
            self.assertEqual(popped, [])
            # 同一榜再跑：没有新进，依旧不弹。
            server.sync_site_alert_feed(feed)
            self.assertEqual(popped, [])
            # 出现新收录的代币：只有它弹，且只弹一次。
            payload["sections"] = [alpha_section([alpha_row("NEWT"), alpha_row("DNA"), alpha_row("DRV")])]
            server.sync_site_alert_feed(feed)
            self.assertEqual(popped, ["alpha-new:NEWT"])
            server.sync_site_alert_feed(feed)
            self.assertEqual(popped, ["alpha-new:NEWT"])


class BinanceAlphaListedAssetTests(unittest.TestCase):
    """Alpha 清单要能被其它榜单新进弹窗复用（AIcoin 热榜新进 + Alpha 币 → 带提示与链）。"""

    def setUp(self):
        self._original = server.ALPHA_LISTED_ASSETS
        self._original_chains = server.ALPHA_LISTED_CHAINS

    def tearDown(self):
        server.ALPHA_LISTED_ASSETS = self._original
        server.ALPHA_LISTED_CHAINS = self._original_chains

    def test_parse_records_the_full_inventory_not_just_the_top_ten(self):
        server.parse_binance_alpha_new_events({"sections": [{
            "id": "binance-alpha-new", "status": "ok", "rows": [alpha_row("DNA")],
            "listingInventory": [{"asset": "DNA", "listedAt": 1, "chain": "BSC"},
                                 {"asset": "OLDCOIN", "listedAt": 2, "chain": "Solana"}],
        }]})
        self.assertIn("DNA", server.ALPHA_LISTED_ASSETS)
        self.assertIn("OLDCOIN", server.ALPHA_LISTED_ASSETS)
        # 链也要跟着清单一起记下来，否则「某榜新进 + Alpha 币」的弹窗说不出是哪条链。
        self.assertEqual(server.ALPHA_LISTED_CHAINS["DNA"], "BSC")
        self.assertEqual(server.binance_alpha_chain_for_asset({"symbol": "OLDCOINUSDT"}), "Solana")

    def test_an_empty_inventory_keeps_the_previous_set(self):
        server.ALPHA_LISTED_ASSETS = frozenset({"DNA"})
        server.ALPHA_LISTED_CHAINS = {"DNA": "BSC"}
        server.parse_binance_alpha_new_events({"sections": [
            {"id": "binance-alpha-new", "status": "unavailable", "rows": [], "listingInventory": []},
        ]})
        self.assertEqual(server.ALPHA_LISTED_ASSETS, frozenset({"DNA"}))
        self.assertEqual(server.ALPHA_LISTED_CHAINS, {"DNA": "BSC"})

    def test_a_relisted_asset_keeps_the_chain_it_already_had(self):
        server.ALPHA_LISTED_CHAINS = {"DNA": "BSC"}
        server.parse_binance_alpha_new_events({"sections": [{
            "id": "binance-alpha-new", "status": "ok", "rows": [],
            "listingInventory": [{"asset": "DNA", "listedAt": 3}],  # 这一轮上游没给链
        }]})
        self.assertEqual(server.binance_alpha_chain_for_asset({"symbol": "DNA"}), "BSC")

    def test_unknown_set_is_never_treated_as_listed(self):
        server.ALPHA_LISTED_ASSETS = frozenset()
        server.ALPHA_LISTED_CHAINS = {}
        self.assertFalse(server.binance_alpha_listed_asset({"symbol": "DNA"}))
        self.assertEqual(server.binance_alpha_chain_for_asset({"symbol": "DNA"}), "")

    def test_matches_quoted_symbols_like_dnausdt(self):
        server.ALPHA_LISTED_ASSETS = frozenset({"DNA"})
        self.assertTrue(server.binance_alpha_listed_asset({"assetKey": "DNA", "symbol": "DNAUSDT"}))


class BinanceAlphaRankMonitorTests(unittest.TestCase):
    """AIcoin 热门榜新进默认静默，但新进的是 Alpha 币时要弹，并带链提醒与重语气结论。"""

    def setUp(self):
        self._original = server.ALPHA_LISTED_ASSETS
        self._original_chains = server.ALPHA_LISTED_CHAINS
        server.ALPHA_LISTED_ASSETS = frozenset({"DNA"})
        server.ALPHA_LISTED_CHAINS = {"DNA": "BSC"}

    def tearDown(self):
        server.ALPHA_LISTED_ASSETS = self._original
        server.ALPHA_LISTED_CHAINS = self._original_chains

    def snapshot(self, symbol="DNA", source_id="aicoin"):
        return {
            "key": f"{source_id}:{symbol}", "assetKey": symbol, "sourceId": source_id,
            "sourceTitle": "AIcoin 热门榜", "sourceLabel": "AC", "symbol": symbol,
            "rank": 3, "price": "$0.27", "change": 12.5, "amount": 1_000_000.0,
            "turnover": "$1.00M", "board": "", "summary": "",
        }

    def test_a_rank_entry_of_an_alpha_asset_is_not_announced_with_alpha_words(self):
        """别的榜命中 Alpha 币，不许借用 Alpha 新币那句推销式文案（用户 2026-10-17）。"""
        event = server.rank_monitor_event("hot", self.snapshot(), "new")
        self.assertNotIn(server.ALPHA_NEW_RISK_NOTE, event["body"])
        self.assertNotIn(server.ALPHA_NEW_RISK_NOTE, event["speech"])
        self.assertNotIn("BSC 链", event["body"])
        self.assertIsNone(event.get("alphaListed"))
        # AIcoin 整榜放行（2026-10-16），与是不是 Alpha 币无关。
        self.assertFalse(server.desktop_alert_source_is_muted(event))

    def test_the_chain_is_still_annotated_for_a_rank_entry(self):
        """文案不外溢，但「这个币在哪条链」照标（用户 2026-10-17 全弹窗标链）。"""
        snapshot = {**self.snapshot(), "chain": "56"}
        event = server.rank_monitor_event("hot", snapshot, "new")
        self.assertEqual(server.desktop_alert_chain_annotation(event), "BSC")

    def test_a_rank_entry_of_an_alpha_asset_is_not_painted_red(self):
        event = server.rank_monitor_event("hot", self.snapshot(), "new")
        self.assertIsNone(event.get("alertTone"))
        self.assertEqual(server.normalize_desktop_alert(event)["alertTone"], "normal")
        server.ALPHA_LISTED_CHAINS = {"DNA": "Base"}
        other = server.rank_monitor_event("hot", self.snapshot(), "new")
        self.assertIsNone(other.get("alertTone"))

    def test_the_binance_wallet_hot_popup_of_an_alpha_coin_stays_plain(self):
        """线上真实案例：币安钱包热门榜新进 POP（Zypher，Alpha 收录 BSC 币）。

        这条弹窗是「热门榜新进」，不是 Alpha 新币公告，用户看到那句「合约极其恶心…」
        直接问「这个好像不是 alpha 的」。所以：不贴文案、不染色，只标链。
        """
        snapshot = {
            **self.snapshot("POP", source_id="binance-wallet-hot"),
            "chain": "56",
            "contractAddress": "0xa3cfb853339b77f385b994799b015cb04b208fe6",
            "sourceTitle": "币安钱包 4 小时热门榜",
            "sourceLabel": "BW",
        }
        event = server.rank_monitor_event("hot", snapshot, "new")
        self.assertNotIn(server.ALPHA_NEW_RISK_NOTE, event.get("body") or "")
        self.assertIsNone(event.get("alertTone"))
        self.assertIsNone(event.get("alphaListed"))
        self.assertEqual(server.desktop_alert_chain_annotation(event), "BSC")

    def test_aicoin_entry_of_an_ordinary_asset_is_delivered_without_the_alpha_note(self):
        # 用户 2026-10-16：AIcoin 热门榜新进整体要弹窗；只有 Alpha 币才额外带那句重提示。
        event = server.rank_monitor_event("hot", self.snapshot("SOMECOIN"), "new")
        self.assertFalse(event.get("alphaListed"))
        self.assertNotIn(server.ALPHA_NEW_RISK_NOTE, event["body"])
        self.assertFalse(server.desktop_alert_source_is_muted(event))

    def test_only_new_entries_get_the_note(self):
        event = server.rank_monitor_event("hot", self.snapshot(), "rank")
        self.assertFalse(event.get("alphaListed"))

    def test_the_hot_new_gate_exempts_aicoin_but_keeps_other_exchanges_silent(self):
        self.assertFalse(server.rank_monitor_hot_new_is_muted(self.snapshot()))
        self.assertFalse(server.rank_monitor_hot_new_is_muted(self.snapshot("SOMECOIN")))
        # 别家交易所照旧静默，别把 firehose 一起放回来。
        self.assertTrue(server.rank_monitor_hot_new_is_muted(self.snapshot("DNA", source_id="okx")))
        self.assertTrue(server.rank_monitor_hot_new_is_muted(self.snapshot("BTC", source_id="binance")))

    def test_normalize_keeps_the_marker_alive(self):
        normalized = server.normalize_desktop_alert(
            {"key": "rank-monitor:hot|aicoin:DNA|new", "kind": "AIcoin热门榜新进",
             "title": "AIcoin热门榜新进：DNA", "sourceId": "aicoin", "alphaListed": True})
        self.assertTrue(normalized["alphaListed"])
        self.assertFalse(server.desktop_alert_source_is_muted(normalized))
        # AIcoin 现在整榜放行（用户 2026-10-16）；是否额外带提示只取决于它是不是 Alpha 币。
        plain = server.normalize_desktop_alert(
            {"key": "rank-monitor:hot|aicoin:SOMECOIN|new", "kind": "AIcoin热门榜新进",
             "title": "AIcoin热门榜新进：SOMECOIN", "sourceId": "aicoin"})
        self.assertFalse(plain["alphaListed"])
        self.assertFalse(server.desktop_alert_source_is_muted(plain))
        # 别家交易所的同款新进仍然静默。
        other = server.normalize_desktop_alert(
            {"key": "rank-monitor:hot|okx:BTC|new", "kind": "OKX热门榜新进",
             "title": "OKX热门榜新进：BTC", "sourceId": "okx"})
        self.assertTrue(server.desktop_alert_source_is_muted(other))


if __name__ == "__main__":
    unittest.main()
