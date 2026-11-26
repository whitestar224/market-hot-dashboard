"""战壕「新创建」的关键词弹窗（用户 2026-11-25）。

两条线各自验：

* 判定：命中什么、什么样的不算（太老 / 同名已报 / 超量 / 关了开关）；
* 排版落在 ``server.send_new_token_keyword_desktop_alert``，断言标题正文能拼出来。

数据源用假的 fetcher 注入，不联网。
"""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import new_token_alert_monitor as NTM
import server

# 用「现在」当基准：``NewTokenAlertStore.record()`` 记账用的是真实时钟，测试里若拿一个
# 硬编码的时间点，两边的差会超过一小时，同名去重/每小时限额的窗口就全错位了。
NOW = int(time.time())
MINT = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
TOKEN = "0x421a8fccae920f73784ffffe22e315b9622d7777"


def row(symbol="MUSKMAN", name="Musk Man", *, address=TOKEN, age=30,
        zh="", symbol_zh="", market_cap=3000.0, platform="fourmeme"):
    return {
        "symbol": symbol,
        "name": name,
        "address": address,
        "created_timestamp": NOW - age,
        "market_cap": market_cap,
        "liquidity": 5000.0,
        "volume_24h": 120.0,
        "swaps_24h": 8,
        "holder_count": 12,
        "launchpad_platform": platform,
        "twitter": "",
        "trans_name_zhcn": zh,
        "trans_symbol_zhcn": symbol_zh,
    }


class MatchTests(unittest.TestCase):
    def test_ascii_words_need_word_boundaries_only_when_short(self):
        compiled = NTM.compile_keywords(["ai", "musk", "cz"])
        self.assertEqual(NTM.match_row({"symbol": "SAID"}, compiled), [])
        self.assertEqual(NTM.match_row({"symbol": "AI"}, compiled), ["ai"])
        # 长词走子串：MUSKMAN 也该算命中。
        self.assertEqual(NTM.match_row({"symbol": "MUSKMAN"}, compiled), ["musk"])
        self.assertEqual(NTM.match_row({"symbol": "CZX"}, compiled), [])

    def test_chinese_words_match_a_chinese_symbol(self):
        compiled = NTM.compile_keywords(["马斯克", "特朗普"])
        hits = NTM.match_row({"symbol": "马斯克狗", "name": ""}, compiled)
        self.assertEqual(hits, ["马斯克"])

    def test_machine_translation_never_counts_as_a_hit(self):
        # 用户 2026-11-26：「战壕里都没有出现这个币，怎么又弹窗」。
        # `NBIANCE` / `BIANCEBIANCE` 原文里没有「币安」，是 GMGN 的机器翻译字段
        # 把它们译成「币安」—— 翻译是模型臆造出来的内容，绝不能当命中依据。
        # 词表里中文词都配了英文形式（binance/币安），原文匹配足够。
        compiled = NTM.compile_keywords(["币安", "binance"])
        for symbol in ("NBIANCE", "BIANCEBIANCE"):
            with self.subTest(symbol=symbol):
                row = {
                    "symbol": symbol,
                    "name": symbol,
                    "trans_symbol_zhcn": "币安",
                    "trans_name_zhcn": "币安",
                }
                self.assertEqual(NTM.match_row(row, compiled), [])

    def test_a_real_binance_imitator_still_fires_on_the_original_name(self):
        # 真叫 BINANCE 的土狗（原文命中）照旧要弹 —— 收紧的是翻译，不是词表。
        compiled = NTM.compile_keywords(["币安", "binance"])
        self.assertEqual(NTM.match_row({"symbol": "BINANCE"}, compiled), ["binance"])

    def test_only_name_fields_are_searched_not_links(self):
        # twitter / website 不参与：一个普通网址里的 binance 不该把提醒变成噪音。
        compiled = NTM.compile_keywords(["binance"])
        self.assertEqual(NTM.match_row({"symbol": "X", "twitter": "x.com/binance"}, compiled), [])

    def test_duplicate_words_are_compiled_once(self):
        self.assertEqual(len(NTM.compile_keywords(["Musk", "musk ", "MUSK"])), 1)


class MonitorTests(unittest.TestCase):
    def make_monitor(self, rows_by_chain):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = NTM.NewTokenAlertMonitor(
            Path(tmp.name) / "alerts.sqlite",
            keywords=["musk", "马斯克"],
            fetcher=lambda chain, limit: list(rows_by_chain.get(chain, [])),
        )
        self.addCleanup(monitor.store.close)
        self.contexts = []
        monitor.set_alert_callback(self.contexts.append)
        return monitor

    def test_a_fresh_hit_produces_one_popup(self):
        monitor = self.make_monitor({"bsc": [row()]})
        out = monitor.poll_once(now=NOW)
        self.assertEqual(len(out), 1)
        context = out[0]
        self.assertEqual(context["symbol"], "MUSKMAN")
        self.assertEqual(context["keywords"], ["musk"])
        self.assertEqual(context["ageSeconds"], 30)
        self.assertEqual(context["platform"], "fourmeme")
        self.assertEqual(len(self.contexts), 1)
        # 第二轮同一行不该再弹。
        self.assertEqual(monitor.poll_once(now=NOW + 20), [])

    def test_old_rows_are_ignored(self):
        monitor = self.make_monitor({"bsc": [row(age=NTM.NEW_TOKEN_MAX_AGE_SECONDS + 60)]})
        self.assertEqual(monitor.poll_once(now=NOW), [])
        self.assertEqual(monitor.store.recent(), [])

    def test_same_symbol_is_reported_once_per_hour(self):
        """实测同名会反复发币（FBTC 在 90 秒里发了 5 次），按合约去重挡不住。"""
        monitor = self.make_monitor({"bsc": [
            row(address=TOKEN),
            row(address="0x79736f3e08c7eea331878f69e474e1d4723dffff"),
        ]})
        out = monitor.poll_once(now=NOW)
        self.assertEqual([ctx["tokenAddress"] for ctx in out], [TOKEN])
        # 第二个同名币被入库（防以后再判），但没弹。
        self.assertEqual(len(monitor.store.recent()), 2)

    def test_hourly_cap_suppresses_the_rest(self):
        monitor = self.make_monitor({})
        monitor.store.set_setting("x", "y")
        rows = [
            row(symbol=f"MUSK{index}", address=f"0x{index:040x}")
            for index in range(5)
        ]
        monitor.fetcher = lambda chain, limit: rows if chain == "bsc" else []
        with patch.object(NTM, "NEW_TOKEN_MAX_PER_HOUR", 2):
            out = monitor.poll_once(now=NOW)
        self.assertEqual(len(out), 2)
        self.assertEqual(len(monitor.store.recent()), 5)

    def test_switching_alerts_off_keeps_records(self):
        monitor = self.make_monitor({"bsc": [row()]})
        monitor.set_alerts_enabled(False)
        self.assertEqual(monitor.poll_once(now=NOW), [])
        self.assertEqual(len(monitor.store.recent()), 1)
        self.assertFalse(monitor.store.recent()[0]["popped"])

    def test_one_broken_chain_does_not_block_the_others(self):
        def fetcher(chain, limit):
            if chain == "bsc":
                raise RuntimeError("gmgn 429")
            return [row(symbol="MUSKMAN", address=MINT)]

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = NTM.NewTokenAlertMonitor(
            Path(tmp.name) / "alerts.sqlite", keywords=["musk"], fetcher=fetcher
        )
        self.addCleanup(monitor.store.close)
        out = monitor.poll_once(now=NOW)
        self.assertEqual([ctx["chain"] for ctx in out], ["solana"])
        self.assertIn("429", monitor.last_error)

    def test_evm_address_is_lowercased_and_base58_is_not(self):
        self.assertEqual(NTM.row_identity("bsc", {"address": TOKEN.upper().replace("0X", "0x")}),
                         ("bsc", TOKEN.lower()))
        self.assertEqual(NTM.row_identity("solana", {"address": MINT}), ("solana", MINT))

    def test_keyword_crud(self):
        monitor = self.make_monitor({})
        monitor.store.add_keyword("Trump")
        self.assertIn("trump", monitor.store.enabled_keywords())
        monitor.store.set_keyword_enabled("trump", False)
        self.assertNotIn("trump", monitor.store.enabled_keywords())
        self.assertTrue(monitor.store.remove_keyword("trump"))
        self.assertNotIn("trump", monitor.store.enabled_keywords())
        # 删掉的默认词不会被下一次 seed 加回来。
        monitor.store.seed_keywords(["musk"])
        self.assertNotIn("trump", monitor.store.enabled_keywords())


class FreshnessTests(unittest.TestCase):
    """新鲜度看「创建」和「打满/开盘」两个时钟（用户 2026-11-25 的 `grok`）。

    用户截图里那只 `grok / Grok Coin`（Solana，Pooled 28.84 SOL，age 1m）在战壕
    「已开盘」列。这种币的 `created_timestamp` 往往是几小时前（内盘磨了很久才打满），
    只盯创建时间会被判成「太老」而静默 —— 这就是他说的「没有第一时间」。
    """

    def make_monitor(self, rows, *, keywords=("grok", "格罗克")):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = NTM.NewTokenAlertMonitor(
            Path(tmp.name) / "alerts.sqlite",
            keywords=list(keywords),
            fetcher=lambda chain, limit: list(rows) if chain == "bsc" else [],
        )
        self.addCleanup(monitor.store.close)
        return monitor

    def test_age_text_is_parsed(self):
        self.assertEqual(NTM.age_seconds_from_text("1m"), 60)
        self.assertEqual(NTM.age_seconds_from_text("42s"), 42)
        self.assertEqual(NTM.age_seconds_from_text("2h"), 7200)
        self.assertEqual(NTM.age_seconds_from_text("3d"), 259200)
        self.assertEqual(NTM.age_seconds_from_text("1 分钟"), 60)
        self.assertEqual(NTM.age_seconds_from_text("刚刚"), 0)
        # 卡片正文里的 `28.84 SOL` 不能被读成 84 秒（单位后面还得是结尾）。
        self.assertIsNone(NTM.age_seconds_from_text("28.84 SOL"))
        self.assertIsNone(NTM.age_seconds_from_text("$8.2K"))

    def test_just_graduated_token_is_fresh_even_if_created_hours_ago(self):
        rows = [{
            "address": TOKEN, "symbol": "GROK", "name": "Grok Coin",
            "created_timestamp": NOW - 3 * 3600,   # 三小时前创建
            "open_timestamp": NOW - 60,            # 一分钟前才打满
        }]
        out = self.make_monitor(rows).poll_once(now=NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["freshKind"], "opened")
        self.assertEqual(out[0]["ageSeconds"], 60)

    def test_old_token_without_a_graduation_clock_is_still_ignored(self):
        rows = [{"address": TOKEN, "symbol": "GROK", "created_timestamp": NOW - 3 * 3600}]
        self.assertEqual(self.make_monitor(rows).poll_once(now=NOW), [])

    def test_dom_row_only_needs_the_age_text(self):
        """DOM 兜底拿不到任何时间戳，只有卡片上的「1m」。"""
        rows = [{"address": TOKEN, "symbol": "格罗克", "age_text": "1m", "segment": "dom"}]
        out = self.make_monitor(rows).poll_once(now=NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "格罗克")
        self.assertEqual(out[0]["ageSeconds"], 60)
        self.assertEqual(out[0]["segment"], "dom")

    def test_raw_text_only_helps_when_no_name_was_read(self):
        compiled = NTM.compile_keywords(["grok"])
        # 有 symbol 时绝不看正文：卡片正文里的侧栏字样会把整列变成命中。
        self.assertEqual(
            NTM.match_row({"symbol": "DOGE", "raw_text": "chatgpt grok 1m"}, compiled), []
        )
        # 一个名字都没取到时，正文是最后一点线索。
        self.assertEqual(
            NTM.match_row({"raw_text": "GROK 1m · $8.2K"}, compiled), ["grok"]
        )


class CardTextTests(unittest.TestCase):
    """战壕卡片的正文解析（字符串是从真页面 probe 下来的）。

    DOM 兜底拿不到 `symbol`：实测 `img[alt]` 全是 `logo`、`a[href*="/token/"]` 是空串，
    币名只在正文里，紧跟缩写地址。
    """

    SAMPLES = {
        # 新创建列（BSC）
        "\ue06e0xd4...7777 皮皮皮皮船长\ue032\ue058安全未知Tax ?%BNB53s\ue0ab\ue0bc\ue04b@tudouxyz"
        "\ue0ae3.6K\ue06d2.6K000/21941/0%1$19.8K$1.1K": "皮皮皮皮船长",
        "\ue06e0x9e...ffff Giko Neko爆火IP\ue032\ue0b5Tax 11%BNBGiko Neko热门IP1m \ue0ab@JanailseIvo": (
            "Giko Neko爆火IP"
        ),
        "D0xde...7670 DemoStaking Pool (USD)\ue032\ue058安全未知Tax ?%USDT演示质押池 (USD)1m": (
            "DemoStaking Pool (USD)"
        ),
        "\ue06e4\ue0310x42...7777 希望HOPE\ue032\ue0b5Tax 11%BNB希望希望1m \ue0ab@ZzzzzSol": "希望HOPE",
    }

    def test_symbol_comes_from_the_card_text(self):
        for text, expected in self.SAMPLES.items():
            with self.subTest(text=text[:24]):
                self.assertEqual(NTM.card_text_name(text), expected)

    def test_handles_and_addresses_are_stripped_before_matching(self):
        # 实测卡片上就有 `@ancobinance` —— 那里的 binance 不该算命中。
        text = "\ue06e0xf3...7777 猫猫\ue032安全未知Tax 5%BNB55d\ue04b@ancobinance"
        compiled = NTM.compile_keywords(["binance"])
        self.assertEqual(NTM.match_row({"raw_text": text}, compiled), [])
        self.assertNotIn("@", NTM.clean_card_text(text))

    def test_repeated_name_on_the_card_is_collapsed(self):
        # 卡片把 symbol 和 name 都画一遍，textContent 拼出来就是两遍。
        for raw, expected in {
            "ELONCOINELONCOIN": "ELONCOIN",
            "猫猫币猫猫币": "猫猫币",
            "BinBin": "Bin",
            "babySpaceXbabySpaceX": "babySpaceX",
            "CiCiCiCi": "CiCi",          # 周期取最长的那个，不是 `Ci`
            "皮皮皮皮船长": "皮皮皮皮船长",   # 不是整串重复，别乱切
        }.items():
            with self.subTest(raw=raw):
                self.assertEqual(NTM.display_name_from_card_text(raw), expected)

    def test_name_text_from_the_page_wins_over_the_concatenated_tail(self):
        # 用户 2026-11-26：「弹窗里的币名，要用前面这个，而不是全部那一长串，比如 CICI」。
        # 卡片正文是 `CiCi` + 标签 `Binance AI Act` 拼起来的，字符串层面切不干净
        # （同一套规则会把 `希望HOPE` 切成 `希望`）。所以由脚本按字号取 `name_text`。
        row = {
            "address": "0xc66082579b131839488149d45129da7336837777",
            "created_timestamp": NOW - 60,
            "symbol": "",
            "name": "",
            "name_text": "CiCi",
            "raw_text": "0xc6...7777 CiCiBinance AI ActTax 2%BNBBNBCiCi币安AI行动8m",
        }
        context = NTM.new_token_context("bsc", row, ["binance"], now_seconds=NOW)
        self.assertEqual(context["symbol"], "CiCi")

    def test_dom_row_gets_a_usable_title(self):
        rows = [{
            "address": TOKEN, "symbol": "", "name": "",
            "raw_text": "\ue06e0x7a...7777 格罗克\ue032\ue058安全未知Tax ?%BNB40s",
            "age_text": "40s", "segment": "dom",
        }]
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        monitor = NTM.NewTokenAlertMonitor(
            Path(tmp.name) / "alerts.sqlite",
            keywords=["格罗克"],
            fetcher=lambda chain, limit: list(rows) if chain == "bsc" else [],
        )
        self.addCleanup(monitor.store.close)
        out = monitor.poll_once(now=NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "格罗克")


class FeedTests(unittest.TestCase):
    """页面回传的那条路（用户 2026-11-25：「用我页面的数据」「不然币太多了」）。"""

    def test_ingest_then_read(self):
        feed = NTM.NewTokenFeed(ttl_seconds=180)
        self.assertEqual(feed.ingest("bsc", [row()]), 1)
        rows = feed.rows("bsc")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chain"], "bsc")   # chain 由 feed 补上
        self.assertEqual(feed.rows("solana"), [])
        self.assertTrue(feed.status()["active"])

    def test_stale_feed_stops_feeding_judgement(self):
        feed = NTM.NewTokenFeed(ttl_seconds=10, active_seconds=5)
        feed.ingest("bsc", [row()], received_at=time.time() - 60)
        self.assertEqual(feed.rows("bsc"), [])
        self.assertTrue(feed.status()["chains"]["bsc"]["stale"])
        # 诊断接口要能看得到实际那一批（「这条为什么没弹」）。
        self.assertEqual(len(feed.rows("bsc", include_stale=True)), 1)

    def test_quiet_market_does_not_blind_the_monitor(self):
        """行情安静三分钟没有新币是常态 —— 默认 TTL 必须撑得住，别把窗口里的币丢掉。"""
        self.assertGreaterEqual(NTM.NEW_TOKEN_FEED_TTL_SECONDS, NTM.NEW_TOKEN_MAX_AGE_SECONDS)
        feed = NTM.NewTokenFeed()
        feed.ingest("bsc", [row()], received_at=time.time() - 300)
        self.assertEqual(len(feed.rows("bsc")), 1)
        self.assertTrue(feed.status()["chains"]["bsc"]["stale"])   # 但 active 已经算关页了

    def test_batches_merge_instead_of_replacing(self):
        """DOM 扫描只报当前这一屏；整批替换会让滚出视口的币凭空消失（=「没弹」）。"""
        feed = NTM.NewTokenFeed()
        first = row(address=TOKEN)
        second = row(symbol="OTHER", address="0x79736f3e08c7eea331878f69e474e1d4723dffff")
        feed.ingest("bsc", [first])
        feed.ingest("bsc", [second])
        addresses = [item["address"] for item in feed.rows("bsc")]
        self.assertIn(TOKEN, addresses)
        self.assertEqual(len(addresses), 2)
        # 同一枚币的两份数据要合并，别让后到的 DOM 行冲掉时间戳。
        feed.ingest("bsc", [{"address": TOKEN, "raw_text": "x", "age_text": "1m", "segment": "dom"}])
        merged = {item["address"]: item for item in feed.rows("bsc")}[TOKEN]
        self.assertEqual(merged["created_timestamp"], first["created_timestamp"])
        self.assertEqual(merged["raw_text"], "x")

    def test_ingest_rejects_rows_without_chain(self):
        feed = NTM.NewTokenFeed()
        self.assertEqual(feed.ingest("", [row()]), 0)
        self.assertEqual(feed.ingest("bsc", []), 0)

    def test_monitor_defaults_to_the_feed_not_the_api(self):
        """默认数据源必须是 feed —— 后端自己抓全量就是「币太多」的来源。"""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        feed = NTM.NewTokenFeed()
        monitor = NTM.NewTokenAlertMonitor(
            Path(tmp.name) / "alerts.sqlite", keywords=["musk"], feed=feed
        )
        self.addCleanup(monitor.store.close)
        self.assertEqual(monitor.source, "feed")
        self.assertEqual(monitor.poll_once(now=NOW), [])   # feed 空 → 不弹也不报错
        feed.ingest("bsc", [row()])
        out = monitor.poll_once(now=NOW)
        self.assertEqual([ctx["symbol"] for ctx in out], ["MUSKMAN"])
        self.assertEqual(monitor.last_source, "feed")


class PopupTests(unittest.TestCase):
    def _context(self):
        return {
            "chain": "bsc",
            "tokenAddress": TOKEN,
            "symbol": "MUSKMAN",
            "name": "Musk Man",
            "keywords": ["musk"],
            "ageSeconds": 42,
            "marketCap": 3200.0,
            "platform": "fourmeme",
            "createdAt": NOW * 1000,
        }

    def test_popup_payload(self):
        captured = {}
        with patch.object(server, "launch_desktop_alert",
                          side_effect=lambda p: captured.update(p) or {"ok": True}):
            server.send_new_token_keyword_desktop_alert(self._context())
        self.assertIn("新币命中", captured["title"])
        self.assertIn("MUSKMAN", captured["title"])
        self.assertIn("musk", captured["title"])
        self.assertIn("42 秒", captured["body"])
        self.assertIn("BSC", captured["body"])
        self.assertIn("fourmeme", captured["body"])
        self.assertEqual(captured["queuePriority"], 140)
        self.assertEqual(captured["chain"], "bsc")
        self.assertIn("/bsc/token/", captured["url"])
        self.assertEqual(captured["sourceType"], "new-token-keyword")

    def test_solana_popup_keeps_mint_case(self):
        context = self._context()
        context.update({"chain": "solana", "tokenAddress": MINT, "ageSeconds": 8})
        captured = {}
        with patch.object(server, "launch_desktop_alert",
                          side_effect=lambda p: captured.update(p) or {"ok": True}):
            server.send_new_token_keyword_desktop_alert(context)
        self.assertIn(f"/sol/token/{MINT}", captured["url"])
        self.assertEqual(captured["contractAddress"], MINT)

    def test_missing_token_is_skipped(self):
        context = self._context()
        context["tokenAddress"] = ""
        result = server.send_new_token_keyword_desktop_alert(context)
        self.assertTrue(result.get("skipped"))


if __name__ == "__main__":
    unittest.main()
