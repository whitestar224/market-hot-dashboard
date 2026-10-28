"""AI 叙事浮层的定位：**先保证不压住卡片**（用户 2026-10-22 的截图）。

旧写法把浮层贴在 ✦ 按钮正下方——那个位置就在卡片里。卡片恒 374 宽、
浮层 340 宽，一出来正好把标题尾巴和正文尾巴盖住，看起来像「KOL NΞO 买入」
被截断了。新逻辑按 右侧 → 下方 → 上方 → 贴边兜底 的顺序挑位置。

这里只测纯函数 ``desktop_alert.narrative_tooltip_position``，不起 Tk 窗口：
卡片 / 浮层 / 屏幕都是像素三元组、二元组，直接喂假坐标。

真实场景参数：卡片 374×约 300，浮层 340×约 140。
"""
import unittest

import desktop_alert


class NarrativeTooltipPositionTests(unittest.TestCase):
    # 弹窗卡片常见形态：左上角 (1200, 300)，374 宽。
    CARD = (1200, 300, 374, 300)
    TIP = (340, 140)

    def test_right_side_when_it_fits(self):
        # 屏幕 1920 宽：卡片右缘 1574 + 8 间距 + 340 浮层 = 1922 > 1912 放不下。
        # 屏幕给宽点（2200），右侧就该放得下。
        x, y = desktop_alert.narrative_tooltip_position(
            card=self.CARD, tooltip=self.TIP, screen=(2200, 900))
        self.assertEqual(x, 1200 + 374 + 8)  # 紧贴卡片右侧
        self.assertEqual(y, 300)  # 顶对卡片顶

    def test_below_when_right_side_is_off_screen(self):
        # 1920 宽屏幕：右侧放不下（1922 > 1912），退到卡片下方。
        x, y = desktop_alert.narrative_tooltip_position(
            card=self.CARD, tooltip=self.TIP, screen=(1920, 900))
        self.assertEqual(x, 1200 + (374 - 340) // 2)  # 水平居中对卡片
        self.assertEqual(y, 300 + 300 + 8)  # 紧贴卡片底缘之下
        # 关键断言：浮层顶不早于卡片底 —— 不压正文。
        self.assertGreaterEqual(y, 300 + 300)

    def test_above_when_below_is_off_screen(self):
        # 卡片贴着屏幕底：下方放不下，退到上方。
        x, y = desktop_alert.narrative_tooltip_position(
            card=(1200, 750, 374, 130), tooltip=self.TIP, screen=(1920, 900))
        self.assertEqual(x, 1200 + (374 - 340) // 2)
        self.assertEqual(y, 750 - 140 - 8)  # 紧贴卡片顶缘之上
        # 关键断言：浮层底不晚于卡片顶。
        self.assertLessEqual(y + 140, 750)

    def test_clamped_fallback_when_nothing_fits(self):
        # 屏幕只有 400×500，比浮层还窄：哪都放不下，退回兜底并被夹在屏内。
        x, y = desktop_alert.narrative_tooltip_position(
            card=(10, 50, 374, 300), tooltip=self.TIP, screen=(400, 500))
        # x 被夹到允许的最右（clamp），y 用「下方」的值再夹。
        self.assertGreaterEqual(x, 8)
        self.assertGreaterEqual(y, 8)
        self.assertLessEqual(x, 400 - 340 - 8 if 400 - 340 - 8 >= 8 else x)
        self.assertLessEqual(y, max(8, 500 - 140 - 8))

    def test_negative_coordinates_are_clamped(self):
        # 卡片快贴屏幕左上：右侧放不下、上方是负数，别让浮层跑到屏幕外。
        x, y = desktop_alert.narrative_tooltip_position(
            card=(0, 60, 374, 300), tooltip=self.TIP, screen=(1000, 500))
        self.assertGreaterEqual(x, 8)
        self.assertGreaterEqual(y, 8)

    def test_gap_is_configurable(self):
        x, _ = desktop_alert.narrative_tooltip_position(
            card=self.CARD, tooltip=self.TIP, screen=(2200, 900), gap=20)
        self.assertEqual(x, 1200 + 374 + 20)


if __name__ == "__main__":
    unittest.main()
