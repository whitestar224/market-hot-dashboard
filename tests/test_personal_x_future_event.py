import unittest

import personal_x_future_event as fx


class FutureEventDetectionTests(unittest.TestCase):
    def test_explicit_absolute_date(self):
        result = fx.detect_future_event("PixVerse 9月28日上市，重点关注")
        self.assertIsNotNone(result)
        self.assertIn("9月28日", result["date_label"])
        self.assertGreater(result["due_at"], 0)

    def test_relative_tomorrow(self):
        result = fx.detect_future_event("明天减半")
        self.assertIsNotNone(result)
        self.assertEqual(result["date_label"], "明天")

    def test_next_week_hint_requires_date_or_keyword(self):
        result = fx.detect_future_event("下周空投快照")
        self.assertIsNotNone(result)
        self.assertEqual(result["date_label"], "下周")

    def test_weak_hint_without_date_is_ignored(self):
        # 仅有「下周」但没有任何未来事件关键词，不应判定
        result = fx.detect_future_event("下周出去吃饭")
        self.assertIsNone(result)

    def test_past_event_is_excluded(self):
        result = fx.detect_future_event("这个币已经上线了，回顾一下")
        self.assertIsNone(result)

    def test_non_event_text_is_ignored(self):
        result = fx.detect_future_event("intel 热度高，等大分歧做延续观察")
        self.assertIsNone(result)

    def test_keyword_without_date_is_ignored(self):
        # 只有未来关键词但没有时间信息 → 排除，避免误判概念讨论帖
        result = fx.detect_future_event("cerebras 五月上市，AI芯片股重点关注")
        self.assertIsNone(result)

    def test_dedupe_key_is_stable(self):
        key_a = fx.event_dedupe_key("T", "same text")
        key_b = fx.event_dedupe_key("T", "same text")
        key_c = fx.event_dedupe_key("T", "different text")
        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)


if __name__ == "__main__":
    unittest.main()
