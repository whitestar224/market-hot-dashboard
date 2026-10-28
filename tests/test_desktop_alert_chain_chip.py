"""渲染层：弹窗卡片上那个「类型 · 链」标签（用户 2026-10-17）。

``chain_chip_label`` 之所以是纯函数，是因为弹窗卡片只有 tkinter 一条路——把它埋进
``show_popup`` 里就没法测了。这里钉死三种情形：有链就显示、正文已写链就不重复、
没链就不占位。
"""
import unittest

import desktop_alert


class ChainChipLabelTests(unittest.TestCase):
    def test_the_chain_label_is_shown_next_to_the_kind(self):
        self.assertEqual(desktop_alert.chain_chip_label({"chainLabel": "BSC"}), "BSC")

    def test_every_chain_label_shape_passes_through(self):
        for label in ("Solana", "ETH", "Base", "Robinhood", "中心化"):
            with self.subTest(label=label):
                self.assertEqual(desktop_alert.chain_chip_label({"chainLabel": label}), label)

    def test_it_is_not_repeated_when_the_body_already_names_the_chain(self):
        # Alpha 那几句正文自带「BSC 链」，再挂一个「· BSC」就重复了。
        self.assertEqual(
            desktop_alert.chain_chip_label({"chainLabel": "BSC"}, body_text="DNA，BSC 链，$0.27"),
            "",
        )

    def test_it_is_not_repeated_when_the_title_already_names_the_chain(self):
        self.assertEqual(
            desktop_alert.chain_chip_label({"chainLabel": "Solana"}, title_text="Solana 生态异动"),
            "",
        )

    def test_a_popup_without_a_chain_gets_no_chip(self):
        for payload in ({}, {"chainLabel": ""}, {"chainLabel": "   "}, {"chain": "56"}):
            with self.subTest(payload=payload):
                self.assertEqual(desktop_alert.chain_chip_label(payload), "")

    def test_the_raw_chain_id_is_never_shown_as_the_chip(self):
        # chain="56" 是给 AI 按钮判定用的原始值，展示一律走 chainLabel。
        self.assertEqual(desktop_alert.chain_chip_label({"chain": "56"}), "")

    def test_whitespace_is_collapsed_and_long_labels_are_clamped(self):
        self.assertEqual(desktop_alert.chain_chip_label({"chainLabel": "  Solana  "}), "Solana")
        self.assertLessEqual(len(desktop_alert.chain_chip_label({"chainLabel": "X" * 60})), 20)

    def test_alpha_phase_wins_over_the_centralized_label(self):
        """「这币在 Alpha」比「中心化」有用，chip 优先显示它（用户 2026-10-21）。"""
        payload = {"chainLabel": "中心化", "alphaPhase": "Alpha · BSC"}
        self.assertEqual(desktop_alert.chain_chip_label(payload), "Alpha · BSC")

    def test_alpha_phase_is_skipped_when_the_body_already_says_it(self):
        # alpha-mm 那张卡的正文里已经有「Alpha 在列」和「BSC 链」，chip 不再重复。
        payload = {"chainLabel": "BSC", "alphaPhase": "Alpha 在列"}
        self.assertEqual(
            desktop_alert.chain_chip_label(payload, body_text="DOOD，BSC 链，Alpha 在列，控盘 96.8%"),
            "",
        )

    def test_the_chain_still_shows_when_only_the_alpha_part_is_redundant(self):
        payload = {"chainLabel": "BSC", "alphaPhase": "Alpha 在列"}
        self.assertEqual(
            desktop_alert.chain_chip_label(payload, body_text="说明里提到了 Alpha 在列"),
            "BSC",
        )


if __name__ == "__main__":
    unittest.main()
