"""方程式新闻（BWE）快讯进入桌面弹窗流的回归。

桌面弹窗的快讯源原本只挂 BlockBeats 律动一条线（``fetch_blockbeats_flash``），
方程式新闻虽然进了 ``/api/newsflash`` 聚合页，却从未进入弹窗事件流。这里锁定：

* 新增 ``bwenews`` 弹窗源，共用 ``parse_site_newsflash_events`` 解析；
* 弹窗事件带正确的来源身份（sourceId / source / sourceLabel），不再顶着律动的名字；
* 实时流（官方 WebSocket）+ 公开 RSS 合并，同题只留一份；
* 聚合页把两家同题新闻算一条，所以弹窗侧也用 ``StoryIndex`` 对律动去重；
* 律动那一路的抓取行为不变（只多了一层同 tick 记忆，避免重复抓页面）。

实时流自身的解析 / 缓冲 / 重连见 ``tests/test_bwe_news_stream.py``。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from newsflash_sources import StoryIndex


def _bwe_item(item_id: str, title: str, content: str, *, url: str = "", add_time: int | None = None) -> dict:
    return {
        "id": item_id,
        "title": title,
        "content": content,
        "url": url,
        "image": "",
        "links": [url] if url else [],
        "add_time": add_time if add_time is not None else int(time.time()),
        "sourceId": "bwenews",
        "source": "方程式新闻",
        "sourceLabel": "BWE",
        "sourcePriority": 80,
    }


def _blockbeats_item(item_id: str, title: str, content: str, *, add_time: int | None = None) -> dict:
    return {
        "id": item_id,
        "title": title,
        "content": content,
        "links": [],
        "url": "",
        "image": "",
        "add_time": add_time if add_time is not None else int(time.time()),
    }


def patch_formula_sources(rss_items, stream_items=None):
    """同时屏蔽实时流与 RSS 两条来源，让用例只验证合并/去重与弹窗语义。"""
    return patch.multiple(
        server,
        bwe_news_ws_recent_items=Mock(return_value=list(stream_items or [])),
        fetch_formula_news_rss_items=Mock(return_value=list(rss_items)),
    )


class StoryIndexTests(unittest.TestCase):
    def test_same_event_from_two_publishers_matches(self):
        now = int(time.time())
        index = StoryIndex(window_seconds=3600)
        index.add(_blockbeats_item("bb-1", "币安将上线 ABC 永续合约", "币安将上线 ABC 永续合约", add_time=now))
        duplicate = _bwe_item(
            "bwe-9", "快讯：币安将上线 ABC 永续合约", "币安将上线 ABC 永续合约", add_time=now,
        )
        self.assertTrue(index.contains(duplicate))

    def test_unrelated_story_is_not_a_duplicate(self):
        now = int(time.time())
        index = StoryIndex(window_seconds=3600)
        index.add(_blockbeats_item("bb-1", "币安将上线 ABC 永续合约", "币安将上线 ABC 永续合约", add_time=now))
        other = _bwe_item("bwe-2", "美联储宣布降息 25 个基点", "联邦基金利率下调", add_time=now)
        self.assertFalse(index.contains(other))

    def test_index_is_bounded_and_clearable(self):
        now = int(time.time())
        index = StoryIndex(window_seconds=3600, limit=2)
        for offset in range(4):
            index.add(_blockbeats_item(f"bb-{offset}", f"消息 {offset}", f"内容 {offset}", add_time=now))
        self.assertEqual(len(index._rows), 2)
        index.clear()
        self.assertEqual(index._rows, [])


class FormulaNewsFetchTests(unittest.TestCase):
    def tearDown(self):
        with server.BLOCKBEATS_FLASH_MEMO_LOCK:
            server.BLOCKBEATS_FLASH_MEMO.update({"updatedAt": 0, "items": [], "fetchedAt": 0.0})
        with server.BWE_RSS_MEMO_LOCK:
            server.BWE_RSS_MEMO.update({"fetchedAt": 0.0, "items": []})

    def test_story_already_carried_by_blockbeats_is_dropped(self):
        now = int(time.time())
        duplicate = _bwe_item("bwe-dup", "快讯：币安将上线 ABC 永续合约", "币安将上线 ABC 永续合约", add_time=now)
        unique = _bwe_item("bwe-unique", "某公链完成主网升级", "本次升级缩短了出块时间", add_time=now)
        with patch_formula_sources([duplicate, unique]), patch.object(
            server,
            "fetch_blockbeats_flash_shared",
            return_value={"updatedAt": 0, "items": [
                _blockbeats_item("bb-1", "币安将上线 ABC 永续合约", "币安将上线 ABC 永续合约", add_time=now)
            ]},
        ):
            payload = server.fetch_formula_news_flash()

        self.assertEqual([row["id"] for row in payload["items"]], ["bwe-unique"])

    def test_all_items_pass_when_blockbeats_has_nothing(self):
        now = int(time.time())
        items = [
            _bwe_item("bwe-1", "币安将上线 ABC 永续合约", "币安将上线 ABC 永续合约", add_time=now),
            _bwe_item("bwe-2", "某公链完成主网升级", "本次升级缩短了出块时间", add_time=now),
        ]
        with patch_formula_sources(items), patch.object(
            server, "fetch_blockbeats_flash_shared", return_value={"updatedAt": 0, "items": []}
        ):
            payload = server.fetch_formula_news_flash()
        self.assertEqual([row["id"] for row in payload["items"]], ["bwe-1", "bwe-2"])

    def test_empty_sources_short_circuit_without_reading_blockbeats(self):
        with patch_formula_sources([]), patch.object(
            server, "fetch_blockbeats_flash_shared"
        ) as shared:
            payload = server.fetch_formula_news_flash()
        self.assertEqual(payload["items"], [])
        shared.assert_not_called()

    def test_blockbeats_shared_fetch_memoizes_one_tick(self):
        payload = {"updatedAt": 1, "items": [_blockbeats_item("bb-1", "标题", "正文")]}
        with patch.object(server, "fetch_blockbeats_flash", return_value=payload) as scraper:
            first = server.fetch_blockbeats_flash_shared()
            second = server.fetch_blockbeats_flash_shared()
        self.assertEqual(scraper.call_count, 1)
        self.assertEqual(first["items"], second["items"])

        with patch.object(server, "fetch_blockbeats_flash", return_value=payload) as scraper, patch.object(
            server, "BLOCKBEATS_FLASH_MEMO_SECONDS", 0.0
        ):
            server.fetch_blockbeats_flash_shared()
        self.assertEqual(scraper.call_count, 1)


class FormulaNewsEventIdentityTests(unittest.TestCase):
    def test_formula_item_keeps_its_own_source_identity(self):
        item = _bwe_item(
            "bwe-77",
            "社区热议 $PEPE 同名 Meme 币",
            "合约地址：0x" + "12" * 20,
            url="https://example.com/bwe/77",
        )
        event = server.parse_site_newsflash_events({"items": [item]})[0]
        self.assertEqual(event["sourceId"], "bwenews")
        self.assertEqual(event["source"], "方程式新闻")
        self.assertEqual(event["sourceLabel"], "BWE")
        self.assertEqual(event["kind"], "聚合快讯")
        self.assertEqual(event["sourceType"], "newsflash")
        self.assertEqual(event["url"], "https://example.com/bwe/77")
        # 解释入口按 kind/sourceType 判定，来源换了名字也必须继续可用。
        self.assertEqual(event["explanationContext"]["symbol"], "PEPE")

    def test_formula_item_without_url_falls_back_to_its_own_feed(self):
        item = _bwe_item("bwe-78", "某项目完成 A 轮融资", "融资规模 2000 万美元")
        event = server.parse_site_newsflash_events({"items": [item]})[0]
        self.assertEqual(event["url"], "https://rss-public.bwe-ws.com")

    def test_blockbeats_item_defaults_to_blockbeats_identity(self):
        item = _blockbeats_item("219115", "最新一条带标记", "正文")
        event = server.parse_site_newsflash_events({"items": [item]})[0]
        self.assertEqual(event["sourceId"], "blockbeats")
        self.assertEqual(event["source"], "BlockBeats 律动")
        self.assertEqual(event["sourceLabel"], "BB")
        self.assertEqual(event["url"], "https://www.theblockbeats.info/newsflash")

    def test_formula_popup_is_not_muted_at_delivery_boundary(self):
        item = _bwe_item("bwe-79", "某项目完成 A 轮融资", "融资规模 2000 万美元")
        event = server.parse_site_newsflash_events({"items": [item]})[0]
        normalized = server.normalize_desktop_alert(event)
        self.assertEqual(normalized["sourceId"], "bwenews")
        self.assertEqual(normalized["source"], "方程式新闻")
        self.assertFalse(server.desktop_alert_source_is_muted(normalized))


class SiteAlertFeedRegistrationTests(unittest.TestCase):
    def test_formula_feed_is_registered_on_the_popup_pipeline(self):
        feeds = {feed["name"]: feed for feed in server.site_alert_feeds()}
        self.assertIn("bwenews", feeds)
        formula = feeds["bwenews"]
        self.assertIs(formula["fetch"], server.fetch_formula_news_flash)
        self.assertIs(formula["parse"], server.parse_site_newsflash_events)
        # 公开 RSS 是低频镜像，窗口必须比律动宽，否则长期静默。
        self.assertEqual(formula["maxAgeMs"], server.NEWSFLASH_FORMULA_ALERT_MAX_AGE_MS)
        self.assertGreater(formula["maxAgeMs"], server.NEWSFLASH_ALERT_MAX_AGE_MS)

    def test_formula_feed_polls_faster_than_the_page_rss(self):
        feeds = {feed["name"]: feed for feed in server.site_alert_feeds()}
        # 实时流条目在内存里，轮询只是搬运；公开 RSS 另有记忆兜住，不会被高频打。
        self.assertLess(feeds["bwenews"]["interval"], feeds["newsflash"]["interval"])
        self.assertGreater(server.BWE_NEWS_WS_RSS_MEMO_SECONDS, feeds["bwenews"]["interval"] * 10)

    def test_blockbeats_feed_now_shares_the_one_tick_memo(self):
        feeds = {feed["name"]: feed for feed in server.site_alert_feeds()}
        self.assertIs(feeds["newsflash"]["fetch"], server.fetch_blockbeats_flash_shared)


class FormulaNewsPopupPipelineTests(unittest.TestCase):
    """走完整的 site-alert 流水线：来源 → 解析 → 投递判定 → 弹窗。"""

    def tearDown(self):
        with server.BLOCKBEATS_FLASH_MEMO_LOCK:
            server.BLOCKBEATS_FLASH_MEMO.update({"updatedAt": 0, "items": [], "fetchedAt": 0.0})

    def _run_feed(self, *, rss_items=(), stream_items=()):
        feed = next(row for row in server.site_alert_feeds() if row["name"] == "bwenews")
        with patch_formula_sources(rss_items, stream_items), patch.object(
            server, "fetch_blockbeats_flash_shared", return_value={"updatedAt": 0, "items": []}
        ), patch.object(
            server, "load_site_alert_state", return_value={"seen": {}, "ready": ["bwenews"]}
        ), patch.object(
            server, "save_site_alert_state"
        ), patch.object(
            server, "SITE_ALERT_STATE", {"seen": {}, "ready": ["bwenews"]}
        ), patch.object(
            server, "launch_desktop_alert", return_value={"ok": True}
        ) as launcher:
            server.sync_site_alert_feed(feed)
        return launcher

    def test_fresh_formula_flash_reaches_the_popup_launcher(self):
        item = _bwe_item("bwe-900", "某公链完成主网升级", "本次升级把出块时间缩短到 1 秒")
        launcher = self._run_feed(rss_items=[item])

        launcher.assert_called_once()
        event = launcher.call_args.args[0]
        self.assertEqual(event["sourceId"], "bwenews")
        self.assertEqual(event["source"], "方程式新闻")
        self.assertEqual(event["sourceLabel"], "BWE")
        self.assertEqual(event["kind"], "聚合快讯")
        self.assertEqual(event["sourceType"], "newsflash")
        # 普通快讯标签：不得擅自抬成报红或绕过静默。
        self.assertNotIn("alertTone", event)
        self.assertNotIn("queuePriority", event)

    def test_realtime_stream_item_also_reaches_the_popup_launcher(self):
        item = _bwe_item("bwenews-ws:abc", "实时流推送的一条 α", "相关代币：BTC / ETH")
        item["sourceChannel"] = "websocket"
        launcher = self._run_feed(stream_items=[item])
        launcher.assert_called_once()
        self.assertEqual(launcher.call_args.args[0]["sourceId"], "bwenews")

    def test_late_surfacing_formula_flash_still_pops(self):
        """公开 RSS 会晚十几小时才放出条目，宽窗口必须让这类消息仍然弹窗。"""
        late = int(time.time()) - 12 * 60 * 60
        item = _bwe_item("bwe-902", "十几小时前发布、刚进 RSS 的消息", "正文", add_time=late)
        launcher = self._run_feed(rss_items=[item])
        launcher.assert_called_once()

    def test_stale_formula_flash_does_not_pop(self):
        stale = int(time.time()) - 48 * 60 * 60
        item = _bwe_item("bwe-901", "两天前的旧消息", "正文", add_time=stale)
        launcher = self._run_feed(rss_items=[item])
        launcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
