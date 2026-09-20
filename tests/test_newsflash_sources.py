import unittest
from unittest.mock import patch

from newsflash_sources import (
    aggregate_newsflash,
    deduplicate_news_items,
    parse_feed_xml,
    stories_match,
)


def item(source_id, source, title, *, content="", add_time=1_700_000_000, priority=50, url=""):
    return {
        "id": f"{source_id}:{title}",
        "sourceId": source_id,
        "source": source,
        "sourceLabel": source[:4],
        "sourcePriority": priority,
        "title": title,
        "content": content,
        "add_time": add_time,
        "url": url,
    }


def test_cross_source_duplicates_keep_blockbeats_and_merge_source_metadata():
    blockbeats = item("blockbeats", "BlockBeats 律动", "快讯：币安将上线 ABC 永续合约", priority=100)
    bwe = item("bwenews", "方程式新闻", "币安将上线 ABC 永续合约", priority=80, add_time=1_700_000_120)

    rows = deduplicate_news_items([bwe, blockbeats])

    assert len(rows) == 1
    assert rows[0]["sourceId"] == "blockbeats"
    assert rows[0]["duplicateCount"] == 1
    assert {source["name"] for source in rows[0]["sources"]} == {"BlockBeats 律动", "方程式新闻"}


def test_similar_headlines_with_different_numbers_are_not_merged():
    left = item("blockbeats", "BlockBeats 律动", "某机构增持 1200 枚 BTC")
    right = item("bwenews", "方程式新闻", "某机构增持 3200 枚 BTC")

    assert stories_match(left, right) is False
    assert len(deduplicate_news_items([left, right])) == 2


def test_same_source_item_survives_title_edit_as_one_core_event():
    first = item("blockbeats", "BlockBeats 律动", "美债收益率逼近高点，一句话或许能稳住债市")
    first["id"] = "215217"
    edited = item("blockbeats", "BlockBeats 律动", "美债收益率逼近高点，一句话或能稳住债市")
    edited["id"] = "215217"

    assert stories_match(first, edited) is True
    assert len(deduplicate_news_items([first, edited])) == 1


def test_rephrased_core_facts_are_merged_but_opposite_action_is_not():
    announced = item(
        "blockbeats",
        "BlockBeats 律动",
        "币安宣布将上线 ABC 永续合约",
        content="Binance 将于今日开放 ABCUSDT 交易",
    )
    rephrased = item(
        "bwenews",
        "方程式新闻",
        "ABC 合约今日登陆 Binance",
        content="币安新增 ABCUSDT 永续交易对",
        add_time=1_700_000_120,
    )
    opposite = item(
        "wublock",
        "吴说区块链",
        "币安将下线 ABC 永续合约",
        content="Binance 停止 ABCUSDT 交易",
        add_time=1_700_000_180,
    )

    assert stories_match(announced, rephrased) is True
    assert stories_match(announced, opposite) is False




def test_bwe_rss_prefers_the_chinese_headline():
    xml = """<?xml version="1.0"?><rss><channel><item>
      <title>Binance lists ABC&lt;br/&gt;币安将上线 ABC 永续合约&lt;br/&gt;2026-09-08 12:00:00</title>
      <link>https://t.me/BWEnews/1</link><pubDate>Tue, 08 Sep 2026 04:00:00 +0000</pubDate>
    </item></channel></rss>"""

    rows = parse_feed_xml(xml, {"id": "bwenews", "name": "方程式新闻", "label": "BWE", "priority": 80})

    assert rows[0]["title"] == "币安将上线 ABC 永续合约"
    assert rows[0]["source"] == "方程式新闻"
    assert rows[0]["add_time"] > 0


def test_atom_feed_is_normalized_to_newsflash_shape():
    xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>吴说测试快讯</title><link href="https://example.com/news/1"/>
      <updated>2026-09-08T16:06:05.000Z</updated><summary>测试摘要</summary>
    </entry></feed>"""

    rows = parse_feed_xml(xml, {"id": "wublock", "name": "吴说区块链", "label": "吴说", "priority": 70})

    assert rows[0]["url"] == "https://example.com/news/1"
    assert rows[0]["content"] == "测试摘要"


def test_aggregation_isolates_a_failed_source_and_reports_deduplication():
    base = {"items": [item("blockbeats", "BlockBeats 律动", "币安将上线 ABC 永续合约", priority=100)]}
    feeds = [
        {"id": "bwenews", "name": "方程式新闻", "label": "BWE", "url": "https://example.com/bwe", "priority": 80},
        {"id": "broken", "name": "故障源", "label": "故障", "url": "https://example.com/broken", "priority": 40},
    ]

    def fake_fetch(source, _headers, _timeout):
        if source["id"] == "broken":
            raise RuntimeError("offline")
        return [item("bwenews", "方程式新闻", "快讯：币安将上线 ABC 永续合约", priority=80)]

    with patch("newsflash_sources.configured_feed_sources", return_value=feeds), patch(
        "newsflash_sources._fetch_feed", side_effect=fake_fetch
    ):
        payload = aggregate_newsflash(base)

    assert len(payload["items"]) == 1
    assert payload["deduplicatedCount"] == 1
    assert any(source["id"] == "broken" and source["status"] == "error" for source in payload["sources"])








class NewsflashSourcesTests(unittest.TestCase):
    def test_cross_source_duplicates(self):
        test_cross_source_duplicates_keep_blockbeats_and_merge_source_metadata()

    def test_similar_headlines_with_different_numbers(self):
        test_similar_headlines_with_different_numbers_are_not_merged()

    def test_same_source_item_title_edit(self):
        test_same_source_item_survives_title_edit_as_one_core_event()

    def test_rephrased_core_facts_and_opposite_action(self):
        test_rephrased_core_facts_are_merged_but_opposite_action_is_not()


    def test_bwe_rss_title(self):
        test_bwe_rss_prefers_the_chinese_headline()

    def test_atom_feed_shape(self):
        test_atom_feed_is_normalized_to_newsflash_shape()

    def test_source_failure_isolation(self):
        test_aggregation_isolates_a_failed_source_and_reports_deduplication()


