"""繁体推文自动转简体：入站一趟、全链路一致。

港台 KOL（如 CryptoCharming）的推文是繁体。转换放在 X 快照的**入站唯一漏斗**
``publish_x_kol_realtime_payload``，所以弹窗、X 追踪页面、AI 分析提示词、聪明钱
文本提取拿到的是同一份简体文本；``send_x_kol_direct_alert`` 再兜一次，防止事件来自
转换上线之前写下的磁盘历史。

两条硬约束（都曾在项目里踩过）：
  * **必须缓存**：X 实时 worker 每 3 秒把同一份 240 条快照重新 publish 一次，
    实测全量转换一轮 ~170ms；只有 lru_cache 才能把稳态压到查表级，否则热路径
    吃 GIL 会饿死页面。
  * **opencc 缺失必须降级**：拿不到词表就原样返回，绝不能因此不弹窗。
"""

import os
import unittest
from unittest.mock import patch

import server

TRADITIONAL = "行情被我前幾天一講還真就冷炸了，這個車頭真的猛"
SIMPLIFIED = "行情被我前几天一讲还真就冷炸了，这个车头真的猛"


class SimplifiedTextTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("XINGYUN_X_KOL_SIMPLIFIED", None)
        server._simplified_chinese_text_cached.cache_clear()

    def test_traditional_chinese_is_folded(self):
        self.assertEqual(server.simplified_chinese_text(TRADITIONAL), SIMPLIFIED)

    def test_conversion_is_idempotent(self):
        # 入站转换会被多条路径重复经过，重复必须无害。
        once = server.simplified_chinese_text(TRADITIONAL)
        self.assertEqual(server.simplified_chinese_text(once), once)

    def test_tickers_handles_and_urls_survive(self):
        text = "上車 $MarsCoin @wangchaionbnb 0x316fa3ab9a8fd8d7567a823dedecf28d9fee2894 https://x.com/a/status/1"
        folded = server.simplified_chinese_text(text)
        for token in (
            "$MarsCoin",
            "@wangchaionbnb",
            "0x316fa3ab9a8fd8d7567a823dedecf28d9fee2894",
            "https://x.com/a/status/1",
        ):
            self.assertIn(token, folded)

    def test_english_and_empty_pass_through(self):
        for value in ("", None, "gm everyone $BTC to the moon", 12345):
            self.assertEqual(
                server.simplified_chinese_text(value),
                "" if value is None or value == "" else str(value),
            )

    def test_enable_switch_turns_conversion_off(self):
        os.environ["XINGYUN_X_KOL_SIMPLIFIED"] = "0"
        self.assertFalse(server.traditional_to_simplified_enabled())
        self.assertEqual(server.simplified_chinese_text(TRADITIONAL), TRADITIONAL)

    def test_missing_opencc_degrades_instead_of_raising(self):
        with patch.object(server, "opencc_t2s_converter", return_value=None):
            server._simplified_chinese_text_cached.cache_clear()
            self.assertEqual(server.simplified_chinese_text(TRADITIONAL), TRADITIONAL)

    def test_steady_state_is_served_from_cache(self):
        # 这一条是性能护栏：worker 每 3 秒重发同一份快照，重复文本必须命中缓存。
        server._simplified_chinese_text_cached.cache_clear()
        server.simplified_chinese_text(TRADITIONAL)
        before = server._simplified_chinese_text_cached.cache_info().hits
        for _ in range(5):
            server.simplified_chinese_text(TRADITIONAL)
        after = server._simplified_chinese_text_cached.cache_info().hits
        self.assertGreaterEqual(after - before, 5)


class SimplifiedPayloadTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("XINGYUN_X_KOL_SIMPLIFIED", None)
        server._simplified_chinese_text_cached.cache_clear()

    @staticmethod
    def payload() -> dict:
        return {
            "items": [
                {
                    "id": "1",
                    "sourceId": "x:cryptocharming",
                    "handle": "CryptoCharming",
                    "sourceName": "中國奧茲",
                    "text": TRADITIONAL,
                    "fullText": TRADITIONAL,
                    "title": TRADITIONAL,
                    "quote": {"text": "上車了兄弟", "url": "https://x.com/a/status/2"},
                    "metrics": {"like": 12},
                },
                {
                    "id": "2",
                    "sourceId": "x:ikunxvwfz",
                    "handle": "ikunxvwfz",
                    "text": "already simplified 中文",
                    "metrics": {"like": 1},
                },
            ],
            "sources": [
                {"id": "x:cryptocharming", "handle": "CryptoCharming", "displayName": "中國奧茲"},
                {"id": "x:ikunxvwfz", "handle": "ikunxvwfz", "displayName": "中国奥兹"},
            ],
        }

    def test_text_fields_are_folded(self):
        folded = server.simplified_payload_text(self.payload())
        item = folded["items"][0]
        self.assertEqual(item["text"], SIMPLIFIED)
        self.assertEqual(item["fullText"], SIMPLIFIED)
        self.assertEqual(item["title"], SIMPLIFIED)
        self.assertEqual(item["sourceName"], "中国奥兹")
        self.assertEqual(item["quote"]["text"], "上车了兄弟")

    def test_merge_keys_and_untouched_rows_are_preserved(self):
        payload = self.payload()
        folded = server.simplified_payload_text(payload)
        self.assertEqual(folded["items"][0]["id"], "1")
        self.assertEqual(folded["items"][0]["handle"], "CryptoCharming")
        self.assertEqual(folded["items"][0]["sourceId"], "x:cryptocharming")
        self.assertEqual(folded["items"][0]["metrics"], {"like": 12})
        # 本来就没有繁体的行必须是同一个对象，避免无意义的重建。
        self.assertIs(folded["items"][1], payload["items"][1])

    def test_source_display_names_are_folded(self):
        folded = server.simplified_payload_text(self.payload())
        self.assertEqual(folded["sources"][0]["displayName"], "中国奥兹")
        self.assertEqual(folded["sources"][0]["handle"], "CryptoCharming")

    def test_input_payload_is_not_mutated_in_place(self):
        original = self.payload()
        server.simplified_payload_text(original)
        self.assertEqual(original["items"][0]["text"], TRADITIONAL)
        self.assertEqual(original["items"][0]["quote"]["text"], "上車了兄弟")

    def test_unchanged_payload_returns_the_same_object(self):
        # 返回同一对象 → 上游 payload 签名不变 → 页面不会被无谓地刷新。
        payload = {"items": [{"id": "1", "text": "已经很简单"}]}
        self.assertIs(server.simplified_payload_text(payload), payload)

    def test_malformed_shapes_are_tolerated(self):
        for payload in (
            {"items": None},
            {"items": []},
            {"items": "nope"},
            {"items": [None, 7, "x"]},
            {},
        ):
            self.assertIs(server.simplified_payload_text(payload), payload)

    def test_entry_point_is_a_noop_when_disabled(self):
        os.environ["XINGYUN_X_KOL_SIMPLIFIED"] = "0"
        payload = self.payload()
        self.assertIs(server.x_kol_simplified_payload(payload), payload)


class PublishFunnelTests(unittest.TestCase):
    """繁体转换必须挂在 X 快照的唯一入站漏斗上，页面/弹窗/AI 才会一致。"""

    def _publish(self, payload: dict) -> dict:
        with patch.object(server, "hydrate_x_kol_realtime_history", return_value=None), \
             patch.object(server, "update_strategy_contexts_from_personal_x_payload", return_value=[]), \
             patch.object(server, "ingest_smart_money_text", return_value=None), \
             patch.object(server, "ingest_trench_person_payload", return_value=None), \
             patch.dict(server.X_KOL_REALTIME_SNAPSHOTS, {}, clear=True):
            return server.publish_x_kol_realtime_payload(
                None,
                {
                    "ok": True,
                    "items": [{
                        "id": "t1",
                        "handle": "CryptoCharming",
                        "text": TRADITIONAL,
                        "publishedAt": 1_791_545_000_000,
                    }],
                    "sources": [{"id": "x:cryptocharming", "handle": "CryptoCharming", "displayName": "中國奧茲"}],
                },
            )

    def test_published_snapshot_carries_simplified_text(self):
        published = self._publish({})
        item = published["items"][0]
        self.assertEqual(item["text"], SIMPLIFIED)
        self.assertEqual(item["sourceName"] if "sourceName" in item else item["handle"], "CryptoCharming")

    def test_published_source_name_is_simplified(self):
        published = self._publish({})
        self.assertEqual(published["sources"][0]["displayName"], "中国奥兹")


if __name__ == "__main__":
    unittest.main()
