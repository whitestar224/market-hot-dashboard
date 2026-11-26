"""Regression tests for the BlockBeats newsflash (Nuxt) scraper.

The legacy matcher anchored on the ``isSup`` field. The publisher only stamps
that flag on the newest ~10 cards, so every older card was silently dropped: a
flash published during a fetch outage rolled out of the flagged set and was
lost for good. These tests lock in the balanced-brace scan that recovers every
card regardless of which optional flags it carries.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


def _card(item_id: int, title: str, add_time: int, *, is_sup: bool = False, url: str = "") -> str:
    fields = [
        f"id:{item_id}",
        f"article_id:{item_id + 100}",
        "content_id:1",
        "type:1",
        "is_show_home:!0",
        "is_detective:!1",
        "is_top:!0",
        "is_original:1",
        "special_id:0",
        "topic_id:0",
        "ios:1",
        "is_first:1",
        "is_hot:!1",
        "is_premium:!1",
    ]
    if is_sup:
        fields.append("isSup:!0")
    fields.extend([
        f"add_time:{add_time}",
        'img_url:""',
        'c_img_url:""',
        f'url:"{url}"',
        'crypto_token:""',
        f'title:"{title}"',
        'lang:"cn"',
        "p_id:1",
        'abstract:""',
        'content:"<p>body</p>"',
    ])
    return "{" + ",".join(fields) + "}"


def _page(children: str) -> str:
    return (
        "window.__NUXT__=({layout:\"default\",data:[{days:["
        "{timeM:\"09\",timeD:30,timeY:2026,timeW:\"WED\",children:[" + children + "]}"
        "]}]})"
    )


class BlockbeatsFlashParseTests(unittest.TestCase):
    def setUp(self):
        # Two of the three cards deliberately lack the `isSup` flag, exactly like
        # the live payload beyond the newest ten entries. The middle card is also
        # duplicated, as the page repeats recent entries in a second list.
        self.html = _page(",".join([
            _card(219115, "最新一条带标记", 1790778182, is_sup=True, url="https://a"),
            _card(219113, "中间一条无标记", 1790777456, url="https://b"),
            _card(219113, "中间一条无标记", 1790777456, url="https://b"),
            _card(219069, "较早一条无标记", 1790760849, url="https://c"),
        ]))

    def _fetch(self):
        response = type("FakeResponse", (), {"text": self.html})()
        with patch.object(server, "http_get_race", return_value=response):
            return server.fetch_blockbeats_flash()["items"]

    def test_balanced_scan_recovers_cards_without_the_issup_flag(self):
        items = self._fetch()
        ids = {str(item["id"]) for item in items}
        # The duplicate collapses; both unflagged cards survive.
        self.assertEqual(ids, {"219115", "219113", "219069"})
        self.assertEqual(len(items), 3)

    def test_legacy_regex_would_have_dropped_unflagged_cards(self):
        matches = re.findall(
            r"\{id:(?:\d+|[A-Za-z_$][\w$]*),article_id:.*?isSup:[^}]+\}",
            self.html,
            flags=re.S,
        )
        # Documents the regression: only the single flagged card was seen.
        self.assertEqual(len(matches), 1)

    def test_items_are_sorted_newest_first_with_parsed_fields(self):
        items = self._fetch()
        self.assertEqual([item["add_time"] for item in items], [1790778182, 1790777456, 1790760849])
        self.assertEqual(items[0]["title"], "最新一条带标记")
        self.assertEqual(items[0]["url"], "https://a")
        self.assertEqual(items[2]["title"], "较早一条无标记")

    def test_balanced_scan_survives_braces_inside_strings(self):
        block_html = _page(_card(1, "标题里有 } 和 { 花括号", 1790778182))
        blocks = server.extract_nuxt_object_blocks(block_html)
        self.assertEqual(len(blocks), 1)
        field = server.js_object_field(blocks[0], "title", {})
        self.assertEqual(server.clean_feed_text(field), "标题里有 } 和 { 花括号")


class BlockbeatsRelayFallbackTests(unittest.TestCase):
    """直连被拒时经海外中转取同一页（2026-11-18）。

    现场：用户要求「不开 Clash、也不断开 Proton」。Proton 全隧道下
    `www.theblockbeats.info` 的 TCP 能连、TLS 握手被丢包，直连彻底不可用。
    中转（r.jina.ai）回的是服务端渲染后的整页 HTML，`window.__NUXT__` 原始脚本
    还在，所以解析器完全复用 —— 这两条测试钉住「路由切换」这一层。
    """

    def _html(self) -> str:
        return _page(",".join([
            _card(219115, "最新一条带标记", 1790778182, is_sup=True, url="https://a"),
            _card(219113, "中间一条无标记", 1790777456, url="https://b"),
        ]))

    def _run(self, direct_ok: bool):
        html = self._html()
        calls = []

        def fake_race(url, *, headers=None, timeout=10.0):
            calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
            if url == server.BLOCKBEATS_URL:
                if direct_ok:
                    return type("R", (), {"text": html, "raise_for_status": lambda self=None: None})()
                raise OSError("TLS handshake timed out")
            return type("R", (), {"text": html, "raise_for_status": lambda self=None: None})()

        with patch.object(server, "http_get_race", side_effect=fake_race), patch.object(
            server, "spawn_background_news_ingest"
        ), patch.object(server, "remember_blockbeats_last_good"):
            payload = server.fetch_blockbeats_flash()
        return payload, calls

    def test_direct_failure_falls_back_to_relay_and_keeps_the_parser(self):
        payload, calls = self._run(direct_ok=False)
        self.assertEqual(payload["fetchedVia"], "relay")
        self.assertEqual({str(item["id"]) for item in payload["items"]}, {"219115", "219113"})
        # 中转请求必须显式要 HTML，否则 Jina 回 markdown，解析器一条也解不出。
        self.assertEqual(calls[-1]["headers"].get("X-Return-Format"), "html")
        self.assertIn("r.jina.ai", calls[-1]["url"])

    def test_direct_success_never_touches_the_relay(self):
        payload, calls = self._run(direct_ok=True)
        self.assertEqual(payload["fetchedVia"], "direct")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], server.BLOCKBEATS_URL)

    def test_relay_url_builder_accepts_both_template_shapes(self):
        target = "https://www.theblockbeats.info/newsflash"
        self.assertEqual(
            server.blockbeats_relay_url("https://r.jina.ai/{url}", target),
            f"https://r.jina.ai/{target}",
        )
        self.assertEqual(
            server.blockbeats_relay_url("https://r.jina.ai/", target),
            f"https://r.jina.ai/{target}",
        )
        # 空模板 = 关闭中转（原样抛直连错误，不许拼出半个 URL）
        self.assertEqual(server.blockbeats_relay_url("  ", target), "")


if __name__ == "__main__":
    unittest.main()
