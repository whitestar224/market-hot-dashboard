"""方程式新闻（BWE）官方 WebSocket 实时流的回归。

协议（https://telegra.ph/BWEnews-API-documentation-06-19）：
* 端点 ``wss://bwenews-api.bwe-ws.com/ws``
* 帧 ``{"source_name":"BWENEWS","news_title":"...","coins_included":["BTC"],
        "url":"https://...","timestamp":1745770800}``
* 心跳是**应用层纯文本**：发 ``ping`` 收 ``pong``（不是协议级 ping 帧）
* 交易所公告不在 WS 里，只在 RSS 里 → 两条通道互补

这里锁定解析、缓冲、去重、路线选择、心跳与自愈重连，以及"缺依赖就优雅降级"。
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


OFFICIAL_EXAMPLE = {
    "source_name": "BWENEWS",
    "news_title": "This is a test message news",
    "coins_included": ["BTC", "ETH", "SOL"],
    "url": "https://bwenews123.com/asdads",
    "timestamp": 1745770800,
}


class FrameParsingTests(unittest.TestCase):
    def test_official_example_frame_is_normalised(self):
        item = server.bwe_news_ws_item(OFFICIAL_EXAMPLE)
        self.assertIsNotNone(item)
        self.assertEqual(item["title"], "This is a test message news")
        self.assertEqual(item["url"], "https://bwenews123.com/asdads")
        self.assertEqual(item["add_time"], 1745770800)
        self.assertEqual(item["sourceId"], "bwenews")
        self.assertEqual(item["source"], "方程式新闻")
        self.assertEqual(item["sourceLabel"], "BWE")
        self.assertEqual(item["sourcePriority"], 80)
        self.assertEqual(item["sourceChannel"], "websocket")
        self.assertIn("BTC", item["content"])
        self.assertTrue(item["id"].startswith("bwenews-ws:"))

    def test_same_story_yields_the_same_identity(self):
        first = server.bwe_news_ws_item(OFFICIAL_EXAMPLE)
        second = server.bwe_news_ws_item(dict(OFFICIAL_EXAMPLE))
        self.assertEqual(first["id"], second["id"])

    def test_millisecond_timestamp_is_normalised_to_seconds(self):
        item = server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "timestamp": 1745770800_000})
        self.assertEqual(item["add_time"], 1745770800)

    def test_foreign_source_name_is_rejected(self):
        # 官方只转发自家报道；别的 source_name 不能冒充方程式新闻。
        self.assertIsNone(server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "source_name": "BINANCE"}))

    def test_missing_title_and_non_dict_are_rejected(self):
        self.assertIsNone(server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "news_title": "   "}))
        self.assertIsNone(server.bwe_news_ws_item(["not", "a", "dict"]))
        self.assertIsNone(server.bwe_news_ws_item(None))

    def test_frame_without_coins_has_empty_body(self):
        item = server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "coins_included": []})
        self.assertEqual(item["content"], "")


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(server, "BWE_NEWS_WS_LOADED", True),
            patch.object(server, "BWE_NEWS_WS_ITEMS", []),
            patch.object(server, "write_json_cache"),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def test_ingest_json_frame_stores_item(self):
        # 用当前时间戳，才能穿过 recent_items 的 24h 新鲜度窗口。
        frame = {**OFFICIAL_EXAMPLE, "timestamp": int(time.time())}
        added = server.bwe_news_ws_ingest_text(json.dumps(frame, ensure_ascii=False))
        self.assertEqual(added, 1)
        rows = server.bwe_news_ws_recent_items(server.NEWSFLASH_FORMULA_ALERT_MAX_AGE_MS)
        self.assertEqual([row["title"] for row in rows], ["This is a test message news"])

    def test_duplicate_frame_is_stored_once(self):
        server.bwe_news_ws_ingest_text(json.dumps(OFFICIAL_EXAMPLE))
        self.assertEqual(server.bwe_news_ws_ingest_text(json.dumps(OFFICIAL_EXAMPLE)), 0)
        self.assertEqual(len(server.BWE_NEWS_WS_ITEMS), 1)

    def test_batch_frame_is_accepted(self):
        added = server.bwe_news_ws_ingest_text(json.dumps([
            OFFICIAL_EXAMPLE,
            {**OFFICIAL_EXAMPLE, "news_title": "第二条实时 α", "url": "https://example.com/2"},
        ], ensure_ascii=False))
        self.assertEqual(added, 2)

    def test_pong_and_garbage_are_not_items(self):
        self.assertEqual(server.bwe_news_ws_ingest_text("pong"), 0)
        self.assertEqual(server.bwe_news_ws_ingest_text("not-json"), 0)
        self.assertEqual(server.bwe_news_ws_ingest_text(""), 0)
        self.assertEqual(server.BWE_NEWS_WS_ITEMS, [])

    def test_pong_still_marks_the_connection_as_alive(self):
        with patch.object(server, "BWE_NEWS_WS_STATE", dict(server.BWE_NEWS_WS_STATE)):
            before = int(server.BWE_NEWS_WS_STATE.get("lastFrameAt") or 0)
            server.bwe_news_ws_ingest_text("pong")
            self.assertGreaterEqual(int(server.BWE_NEWS_WS_STATE["lastFrameAt"]), before)

    def test_recent_items_respects_the_freshness_window(self):
        now = int(time.time())
        server.bwe_news_ws_store([
            server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "timestamp": now}),
            server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "news_title": "两天前", "url": "https://x/old",
                                     "timestamp": now - 48 * 3600}),
        ])
        rows = server.bwe_news_ws_recent_items(24 * 3600 * 1000)
        self.assertEqual([row["title"] for row in rows], ["This is a test message news"])

    def test_buffer_is_bounded(self):
        with patch.object(server, "BWE_NEWS_WS_BUFFER_LIMIT", 2):
            for index in range(4):
                server.bwe_news_ws_store([
                    server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "news_title": f"消息 {index}",
                                             "url": f"https://example.com/{index}"})
                ])
        self.assertEqual(len(server.BWE_NEWS_WS_ITEMS), 2)

    def test_buffer_survives_a_restart(self):
        server.bwe_news_ws_ingest_text(json.dumps(OFFICIAL_EXAMPLE))
        stored = list(server.BWE_NEWS_WS_ITEMS)
        with patch.object(server, "read_json_cache", return_value={"version": 1, "items": stored}), patch.object(
            server, "BWE_NEWS_WS_LOADED", False
        ), patch.object(server, "BWE_NEWS_WS_ITEMS", []):
            server.bwe_news_ws_load()
            self.assertEqual(len(server.BWE_NEWS_WS_ITEMS), 1)
            self.assertEqual(server.BWE_NEWS_WS_ITEMS[0]["title"], "This is a test message news")


class MergeTests(unittest.TestCase):
    def test_stream_copy_wins_over_the_rss_copy(self):
        now = int(time.time())
        stream = server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "timestamp": now})
        rss = {
            "id": "bwenews:fromrss",
            "title": OFFICIAL_EXAMPLE["news_title"],
            "content": OFFICIAL_EXAMPLE["news_title"],
            "url": OFFICIAL_EXAMPLE["url"],
            "add_time": now,
            "sourceId": "bwenews",
            "source": "方程式新闻",
            "sourceLabel": "BWE",
        }
        merged = server.merge_formula_news_items([stream], [rss])
        self.assertEqual([row["id"] for row in merged], [stream["id"]])
        # 稳定：RSS 版重复出现也不会换一份 key。
        again = server.merge_formula_news_items([stream], [rss])
        self.assertEqual([row["id"] for row in again], [stream["id"]])

    def test_rss_only_story_is_kept(self):
        now = int(time.time())
        rss = {
            "id": "bwenews:upbit", "title": "Upbit 上新: 关于上线 CASHCAT 的公告",
            "content": "Upbit 新交易对", "url": "https://upbit.com/x", "add_time": now,
            "sourceId": "bwenews", "source": "方程式新闻", "sourceLabel": "BWE",
        }
        merged = server.merge_formula_news_items([], [rss])
        self.assertEqual([row["id"] for row in merged], ["bwenews:upbit"])

    def test_distinct_stream_items_are_all_kept(self):
        now = int(time.time())
        first = server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "timestamp": now})
        second = server.bwe_news_ws_item({**OFFICIAL_EXAMPLE, "news_title": "美联储宣布降息 25 个基点",
                                          "url": "https://example.com/fed", "timestamp": now})
        merged = server.merge_formula_news_items([first, second], [])
        self.assertEqual(len(merged), 2)


class RouteSelectionTests(unittest.TestCase):
    def test_routes_put_the_adapter_choice_first_and_direct_last(self):
        with patch.object(server, "network_proxy_url", return_value="http://127.0.0.1:62459"), patch.object(
            server, "collect_proxy_candidates", return_value=["http://127.0.0.1:7890", "http://127.0.0.1:62459"]
        ):
            routes = server.bwe_news_ws_routes()
        self.assertEqual(routes[0], "http://127.0.0.1:62459")
        self.assertEqual(routes[-1], "")
        self.assertEqual(len(routes), len(set(routes)))

    def test_routes_still_offer_direct_when_no_proxy_is_selected(self):
        with patch.object(server, "network_proxy_url", return_value=""), patch.object(
            server, "collect_proxy_candidates", return_value=[]
        ):
            self.assertEqual(server.bwe_news_ws_routes(), [""])

    def test_connect_maps_proxy_url_to_library_arguments(self):
        module = Mock()
        server.bwe_news_ws_connect(module, "http://127.0.0.1:62459")
        kwargs = module.create_connection.call_args.kwargs
        self.assertEqual(kwargs["http_proxy_host"], "127.0.0.1")
        self.assertEqual(kwargs["http_proxy_port"], 62459)
        self.assertEqual(kwargs["proxy_type"], "http")

        server.bwe_news_ws_connect(module, "")
        self.assertNotIn("http_proxy_host", module.create_connection.call_args.kwargs)


class PumpTests(unittest.TestCase):
    class _Timeout(Exception):
        pass

    def _fake_module(self):
        module = Mock()
        module.WebSocketTimeoutException = self._Timeout
        return module

    def setUp(self):
        self.patches = [
            patch.object(server, "BWE_NEWS_WS_LOADED", True),
            patch.object(server, "BWE_NEWS_WS_ITEMS", []),
            patch.object(server, "write_json_cache"),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def test_pump_ingests_frames_and_sends_application_level_ping(self):
        connection = Mock()
        connection.recv.side_effect = [
            json.dumps(OFFICIAL_EXAMPLE, ensure_ascii=False),
            "pong",
            self._Timeout(),
            ConnectionResetError("closed"),
        ]
        module = self._fake_module()
        with patch.object(server, "BWE_NEWS_WS_PING_SECONDS", 0.0):
            with self.assertRaises(ConnectionResetError):
                server.bwe_news_ws_pump(module, connection)
        self.assertEqual([row["title"] for row in server.BWE_NEWS_WS_ITEMS],
                         ["This is a test message news"])
        connection.send.assert_called_with("ping")

    def test_pump_returns_when_the_network_route_changed(self):
        connection = Mock()
        connection.recv.side_effect = self._Timeout()
        module = self._fake_module()
        with patch.object(server, "network_proxy_generation", side_effect=[7, 8]):
            with self.assertRaises(ConnectionAbortedError):
                server.bwe_news_ws_pump(module, connection)

    def test_pump_returns_when_the_stream_goes_silent(self):
        connection = Mock()
        connection.recv.side_effect = self._Timeout()
        module = self._fake_module()
        with patch.object(server, "BWE_NEWS_WS_IDLE_TIMEOUT_SECONDS", -1.0):
            with self.assertRaises(TimeoutError):
                server.bwe_news_ws_pump(module, connection)


class LifecycleTests(unittest.TestCase):
    def test_stream_is_disabled_by_env_flag(self):
        with patch.object(server, "bwe_news_ws_enabled", return_value=False):
            self.assertFalse(server.start_bwe_news_stream())
            server.ensure_bwe_news_stream()  # 不得抛错

    def test_missing_websocket_client_degrades_gracefully(self):
        with patch.object(server, "_websocket_module", return_value=None), patch.object(
            server, "BWE_NEWS_WS_STATE", dict(server.BWE_NEWS_WS_STATE)
        ):
            server.bwe_news_ws_client_loop()  # 必须立即返回，不能自旋
            self.assertFalse(server.BWE_NEWS_WS_STATE["enabled"])
            self.assertEqual(server.BWE_NEWS_WS_STATE["lastError"], "websocket-client missing")

    def test_status_reports_stream_health(self):
        with patch.object(server, "BWE_NEWS_WS_LOADED", True), patch.object(
            server, "BWE_NEWS_WS_ITEMS", []
        ):
            status = server.bwe_news_ws_status()
        for key in ("enabled", "connected", "route", "received", "buffered", "url", "websocketAvailable"):
            self.assertIn(key, status)
        self.assertEqual(status["url"], server.BWE_NEWS_WS_URL)
        self.assertTrue(status["websocketAvailable"])


if __name__ == "__main__":
    unittest.main()
