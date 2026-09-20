"""Native popup CA copy controls, withdrawn so tests never show notifications."""
import unittest
from unittest.mock import patch

import desktop_alert
import server


class PopupContractCopyTests(unittest.TestCase):
    def test_normalization_keeps_explicit_contract_for_native_popup(self):
        contract = "0x1234567890AbCdEf1234567890aBcDeF12345678"

        normalized = server.normalize_desktop_alert({
            "kind": "DC机会",
            "title": "新 CA：TEST",
            "contractAddress": contract,
            "chain": "56",
            "alertTone": "red",
        })

        self.assertEqual(normalized["contractAddress"], contract)
        self.assertEqual(normalized["chain"], "56")
        self.assertEqual(normalized["alertTone"], "red")

    def test_news_ca_resonance_is_red_critical_and_keeps_contract(self):
        contract = "0x1234567890abcdef1234567890abcdef12345678"
        row = {"network": "bsc", "contractAddress": contract, "symbol": "4STOCK"}
        resonance = {
            "source": "BlockBeats 律动", "title": "4STOCK 热点事件发酵",
            "url": "https://example.test/4stock", "confidence": 91,
            "matchType": "exact-symbol", "observedAt": 1_700_000_000_000,
        }
        news = {"newsKey": "news-4stock", "content": "4STOCK 相关快讯"}

        with patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch:
            server.send_fast_onchain_news_resonance_alert(row, resonance, news)

        payload = launch.call_args.args[0]
        self.assertEqual(payload["alertTone"], "red")
        self.assertGreaterEqual(payload["queuePriority"], server.DESKTOP_ALERT_CRITICAL_PRIORITY)
        self.assertEqual(payload["contractAddress"], contract)
        self.assertIn("4STOCK", payload["title"])
        self.assertIn("BlockBeats", payload["body"])

    def test_red_tone_changes_native_popup_header_and_border(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()

        def exercise():
            root.deiconify()
            root.update()
            outer = root.winfo_children()[0]
            top = outer.pack_slaves()[0]
            self.assertEqual(root.cget("bg"), "#df3f35")
            self.assertEqual(outer.cget("bg"), "#df3f35")
            self.assertEqual(top.cget("bg"), "#ffd9d5")

        try:
            with (
                patch("tkinter.Tk", return_value=root),
                patch.object(root, "mainloop", side_effect=exercise),
                patch.object(root, "destroy"),
                patch.object(root, "after"),
                patch.object(desktop_alert, "PopupReceipts"),
                patch.object(desktop_alert, "speak_text"),
            ):
                desktop_alert.show_popup({
                    "kind": "News Trade · CA红色共振",
                    "title": "红色共振：4STOCK × 最新快讯",
                    "alertTone": "red",
                    "sound": False,
                }, 0)
        finally:
            root.destroy()

    def test_copy_button_and_view_both_copy_exact_ca_for_supported_popups(self):
        import tkinter as tk

        contract = "So11111111111111111111111111111111111111112"
        scenarios = (
            ("DC机会", True, "new-ca"),
            ("币安钱包4小时热门榜新进", True, "wallet-hot"),
            ("币安钱包4小时热门榜新进", False, "clipboard-busy"),
        )
        for kind, clipboard_available, scenario in scenarios:
            with self.subTest(kind=kind, scenario=scenario):
                root = tk.Tk()
                root.withdraw()

                def children(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from children(child)

                controls = {}

                def exercise():
                    root.deiconify()
                    root.update()
                    buttons = [widget for widget in children(root) if isinstance(widget, tk.Button)]
                    controls["copy"] = next(widget for widget in buttons if widget.cget("text") == "复制CA")
                    controls["view"] = next(widget for widget in buttons if widget.cget("text") == "查看")
                    self.assertIs(controls["copy"].master, controls["view"].master)
                    self.assertLessEqual(
                        controls["copy"].winfo_x() + controls["copy"].winfo_width(),
                        controls["view"].winfo_x(),
                    )
                    controls["copy"].invoke()
                    controls["view"].invoke()

                try:
                    with (
                        patch("tkinter.Tk", return_value=root),
                        patch.object(root, "mainloop", side_effect=exercise),
                        patch.object(root, "destroy"),
                        patch.object(root, "after"),
                        patch.object(desktop_alert, "PopupReceipts"),
                        patch.object(desktop_alert, "speak_text"),
                        patch.object(
                            desktop_alert,
                            "copy_text_to_clipboard",
                            return_value=clipboard_available,
                        ) as copy,
                        patch.object(desktop_alert.webbrowser, "open") as open_url,
                    ):
                        desktop_alert.show_popup({
                            "kind": kind,
                            "title": "新 CA：TEST",
                            "url": "https://example.test/token",
                            "contractAddress": contract,
                            "sound": False,
                        }, 0)

                    self.assertEqual([call.args[1] for call in copy.call_args_list], [contract, contract])
                    open_url.assert_called_once_with("https://example.test/token")
                finally:
                    root.destroy()

    def test_view_still_opens_when_clipboard_is_busy(self):
        class Root:
            def clipboard_clear(self):
                raise RuntimeError("clipboard busy")

        self.assertFalse(desktop_alert.copy_text_to_clipboard(Root(), "0xabc"))


if __name__ == "__main__":
    unittest.main()
