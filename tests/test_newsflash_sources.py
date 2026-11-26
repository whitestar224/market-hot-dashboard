import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# --- 业务代码在 src/ 下：把它加进 sys.path 才能 `from app.x import y` ---------
import sys as _sys
from pathlib import Path as _Path

_SRC_DIR = _Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SRC_DIR))
# ---------------------------------------------------------------------------
from app.sources.newsflash_sources import (
    _get_via_route,
    aggregate_newsflash,
    deduplicate_news_items,
    http_get_race,
    parse_feed_xml,
    parse_foresight_html,
    parse_foresight_tag_html,
    stories_match,
)


FORESIGHT_HTML = """
<div class="news-list">
  <div class="news-item">
    <div class="news-time"><span class="time-detail">2026.11.05</span></div>
    <a href="/news/detail/116956" class="news-card">
      <div class="news-content"><div class="content-main"><div class="content-text">
        <h3>Soneium 将发售 50 枚 NFT</h3>
        <p><p>Foresight News 消息，索尼旗下 Soneium 宣布发售。</p><p><br></p></p>
        <div class="news-meta"><span class="publish-time">2026.11.05 21:38</span></div>
      </div></div></div>
    </a>
  </div>
  <div class="news-item">
    <div class="news-time"><span class="time-detail">2026.11.04</span></div>
    <a href="/article/detail/101389" class="news-card">
      <div class="content-text">
        <h3>深度：Slop Cash 公售</h3>
        <p>Slop Cash 让开源项目为 AI 辅助完成的工作付费。</p>
      </div>
    </a>
  </div>
  <div class="news-item">
    <a href="/news/detail/116956" class="news-card">
      <div class="content-text"><h3>同 id 的重复条目</h3></div>
    </a>
  </div>
</div>
"""


FORESIGHT_TAG_HTML = """
<div class="search_result">
  <div class="item_news news_content">
    <div class="news_content_left">
      <a href="/news/detail/117029" target="_blank" class="news_body_title redcolor">币安 Alpha 推出 35 万美元感谢空投</a>
      <div class="news_body_content"><span style="font-family:'OPPO Sans'">Foresight News 消息，币安钱包发推表示，空投与交易竞赛活动将很快恢复。</span></div>
      <div class="foot_tag"><span class="foot_tag_title">快讯</span> <span class="foot_tag_time">2026-11-06 15:37</span></div>
    </div>
  </div>
  <div class="item_news news_content">
    <div class="news_content_left">
      <a href="/article/detail/101389" target="_blank" class="news_body_title">深度：怎么撸空投才不被反撸</a>
      <div class="news_body_content">空投赛道的三条铁律。</div>
      <div class="foot_tag"><span class="foot_tag_title">文章</span> <span class="foot_tag_time">2026-11-06 09:00</span></div>
    </div>
  </div>
</div>
"""


def _feed_payload(rows, has_more=True):
    """站内接口的返回形状：data 里 items + has_more（真实返回还可能整包 zlib+base64）。"""
    return {"code": 1, "data": {"items": rows, "has_more": has_more}}


def _news_row(news_id, title, published=1793950632):
    return {
        "source_type": "news",
        "source_id": news_id,
        "published_at": published,
        "news": {
            "id": news_id,
            "title": title,
            "brief": f"{title} 的摘要",
            "content": f"<p>{title} 的正文</p>",
            "published_at": published,
        },
    }


def _article_row(article_id):
    return {
        "source_type": "article",
        "source_id": article_id,
        "published_at": 1793900000,
        "article": {"id": article_id, "title": "一篇深度文章"},
    }


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

    with patch("app.sources.newsflash_sources.configured_feed_sources", return_value=feeds), patch(
        "app.sources.newsflash_sources._fetch_feed", side_effect=fake_fetch
    ):
        payload = aggregate_newsflash(base)

    assert len(payload["items"]) == 1
    assert payload["deduplicatedCount"] == 1
    assert any(source["id"] == "broken" and source["status"] == "error" for source in payload["sources"])








def test_http_get_race_returns_first_healthy_route_and_skips_dead_proxies():
    direct_response = MagicMock()
    direct_response.raise_for_status.return_value = None

    def fake_route(url, headers, timeout, proxy):
        if proxy:
            raise RuntimeError(f"proxy {proxy} is half-dead")
        return direct_response

    with patch("app.sources.newsflash_sources._get_via_route", side_effect=fake_route), patch(
        "app.sources.newsflash_sources._env_proxy_url", return_value="http://127.0.0.1:7890"
    ):
        assert http_get_race("https://example.com/feed", timeout=8) is direct_response


def test_http_get_race_uses_proxy_route_when_direct_fails():
    proxy_response = MagicMock()
    proxy_response.raise_for_status.return_value = None

    def fake_route(url, headers, timeout, proxy):
        if not proxy:
            raise RuntimeError("direct is blocked")
        return proxy_response

    with patch("app.sources.newsflash_sources._get_via_route", side_effect=fake_route), patch(
        "app.sources.newsflash_sources._env_proxy_url", return_value="http://127.0.0.1:7890"
    ):
        assert http_get_race("https://example.com/feed", timeout=8) is proxy_response


def test_http_get_race_raises_last_error_when_every_route_fails():
    def fake_route(url, headers, timeout, proxy):
        raise RuntimeError(f"route {proxy or 'direct'} failed")

    with patch("app.sources.newsflash_sources._get_via_route", side_effect=fake_route), patch(
        "app.sources.newsflash_sources._env_proxy_url", return_value=""
    ):
        try:
            http_get_race("https://example.com/feed", timeout=8)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError when all routes fail")


def test_direct_route_ignores_proxy_environment():
    captured = {}

    class FakeSession:
        trust_env = True

        def get(self, url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        def close(self):
            return None

    with patch("app.sources.newsflash_sources.requests.Session", return_value=FakeSession()):
        _get_via_route("https://example.com/feed", {"User-Agent": "t"}, 8, "")

    assert captured.get("timeout") == 8


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

    def test_race_returns_first_healthy_route(self):
        test_http_get_race_returns_first_healthy_route_and_skips_dead_proxies()

    def test_race_falls_back_to_proxy_route(self):
        test_http_get_race_uses_proxy_route_when_direct_fails()

    def test_race_raises_when_all_routes_fail(self):
        test_http_get_race_raises_last_error_when_every_route_fails()

    def test_direct_route_bypasses_env_proxy(self):
        test_direct_route_ignores_proxy_environment()

    def test_foresight_list_page_is_parsed_like_a_feed(self):
        """Foresight 没有 RSS，靠 SSR 的 div.news-item 解析（2026-11-07 接入）。"""
        source = {"id": "foresight", "name": "Foresight 空投线报", "label": "FN", "priority": 60}
        rows = parse_foresight_html(FORESIGHT_HTML, source)
        # 第三条与第一条同 id（/news/detail/116956），必须被丢掉
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["id"], "foresight:116956")
        self.assertEqual(first["title"], "Soneium 将发售 50 枚 NFT")
        # 正文里不能重复标题、不能带时间尾巴
        self.assertEqual(first["content"], "Foresight News 消息，索尼旗下 Soneium 宣布发售。")
        self.assertEqual(first["url"], "https://foresightnews.pro/news/detail/116956")
        self.assertEqual(first["sourceLabel"], "FN")
        self.assertEqual(
            first["add_time"],
            int(datetime(2026, 11, 5, 21, 38, tzinfo=timezone(timedelta(hours=8))).timestamp()),
        )
        # 深度文章（/article/detail）也收，只有日期时按当天中午排
        second = rows[1]
        self.assertEqual(second["url"], "https://foresightnews.pro/article/detail/101389")
        self.assertEqual(
            second["add_time"],
            int(datetime(2026, 11, 4, 12, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()),
        )

    def test_foresight_source_falls_back_to_html_when_api_dies(self):
        """kind=html-foresight：接口挂了要退回 SSR HTML，而且两条路都不能落到 requests（会被 567）。"""
        source = {"id": "foresight", "name": "Foresight 空投线报", "label": "FN",
                  "url": "https://foresightnews.pro/airdropnews", "priority": 60,
                  "kind": "html-foresight"}
        from app.sources.newsflash_sources import _fetch_feed

        with patch("app.sources.newsflash_sources.fetch_foresight_airdrop_news", side_effect=RuntimeError("api down")), \
                patch("app.sources.newsflash_sources.fetch_foresight_html", return_value=FORESIGHT_HTML) as html_fetch, \
                patch("app.sources.newsflash_sources.http_get_race", side_effect=AssertionError("不该走 RSS/requests 路径")):
            rows = _fetch_feed(source, {"User-Agent": "test"}, 8)
        self.assertEqual(len(rows), 2)
        html_fetch.assert_called_once()

    def test_foresight_airdrop_feed_comes_from_site_api(self):
        """接口能用时不准再抓 HTML：SSR 只有 20 条，接口翻页才能拿到全量。"""
        source = {"id": "foresight", "name": "Foresight 空投线报", "label": "FN",
                  "url": "https://foresightnews.pro/airdropnews", "priority": 60,
                  "kind": "html-foresight"}
        from app.sources.newsflash_sources import _fetch_feed

        with patch("app.sources.newsflash_sources.fetch_foresight_json",
                   return_value=_feed_payload([_news_row(116956, "Soneium 空投"), _article_row(1)],
                                              has_more=False)) as api, \
                patch("app.sources.newsflash_sources.fetch_foresight_html",
                      side_effect=AssertionError("接口正常时不该抓 HTML")):
            rows = _fetch_feed(source, {"User-Agent": "test"}, 8)
        self.assertEqual([row["id"] for row in rows], ["foresight:116956"])
        self.assertEqual(rows[0]["url"], "https://foresightnews.pro/news/detail/116956")
        self.assertIn("event_ids=235,53", api.call_args.args[0])

    def test_foresight_tag_feed_paginates_and_keeps_news_only(self):
        """标签页接口混排文章/快讯，只收快讯；has_more 为真才继续翻页。"""
        source = {"id": "foresight-tag-airdrop", "name": "Foresight 空投标签",
                  "label": "FN·标签", "url": "https://foresightnews.pro/tag?tagId=55",
                  "priority": 55, "kind": "html-foresight-tag"}
        from app.sources.newsflash_sources import _fetch_feed

        pages = [
            _feed_payload([_news_row(117029, "币安 Alpha 空投"), _article_row(9)], has_more=True),
            _feed_payload([_news_row(116950, "Bitget PoolX 空投"), _news_row(117029, "重复条目")],
                          has_more=False),
        ]
        with patch("app.sources.newsflash_sources.fetch_foresight_json", side_effect=pages) as api:
            rows = _fetch_feed(source, {"User-Agent": "test"}, 8)
        # 两页都翻了，文章被丢掉，跨页重复 id 去重
        self.assertEqual(api.call_count, 2)
        self.assertIn("tag_ids=55", api.call_args_list[0].args[0])
        self.assertIn("page=2", api.call_args_list[1].args[0])
        self.assertEqual([row["id"] for row in rows],
                         ["foresight-tag-airdrop:117029", "foresight-tag-airdrop:116950"])
        self.assertEqual(rows[0]["sourceLabel"], "FN·标签")

    def test_foresight_is_a_public_direct_source(self):
        from app.sources.newsflash_sources import DIRECT_SOURCE_FAMILIES, SOURCE_PRIORITY, configured_feed_sources, source_family

        ids = {str(row.get("id")) for row in configured_feed_sources()}
        self.assertIn("foresight", ids)
        self.assertIn("foresight-tag-airdrop", ids)
        self.assertEqual(source_family("Foresight News"), "foresight")
        self.assertEqual(source_family("foresight-tag-airdrop"), "foresight")
        self.assertIn("foresight", DIRECT_SOURCE_FAMILIES)
        self.assertEqual(SOURCE_PRIORITY["foresight"], 60)
        self.assertEqual(SOURCE_PRIORITY["foresight-tag-airdrop"], 55)

    def test_foresight_tag_page_uses_its_own_parser(self):
        """标签页结构（div.item_news）和空投线报页（div.news-item）完全不同。"""
        source = {"id": "foresight-tag-airdrop", "name": "Foresight 空投标签",
                  "label": "FN·标签", "priority": 55}
        rows = parse_foresight_tag_html(FORESIGHT_TAG_HTML, source)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["id"], "foresight-tag-airdrop:117029")
        self.assertEqual(first["title"], "币安 Alpha 推出 35 万美元感谢空投")
        self.assertIn("空投与交易竞赛活动", first["content"])
        self.assertEqual(first["url"], "https://foresightnews.pro/news/detail/117029")
        self.assertEqual(
            first["add_time"],
            int(datetime(2026, 11, 6, 15, 37, tzinfo=timezone(timedelta(hours=8))).timestamp()),
        )
        # 深度文章（/article/detail）同样收
        self.assertEqual(rows[1]["url"], "https://foresightnews.pro/article/detail/101389")
        # 两页解析器不能互相吃：空投线报的解析器在这个 HTML 上应该颗粒无收
        legacy = {"id": "foresight", "name": "Foresight 空投线报", "label": "FN", "priority": 60}
        self.assertEqual(parse_foresight_html(FORESIGHT_TAG_HTML, legacy), [])


