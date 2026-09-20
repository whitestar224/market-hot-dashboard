"""Actual native popup widgets, withdrawn: no notifications, speech or external actions."""
import unittest
from unittest.mock import Mock, patch
import desktop_alert


class PopupExplanationGuiTests(unittest.TestCase):
    def test_hot_and_news_button_visible_and_opens_same_context(self):
        import tkinter as tk
        for kind in ("币安钱包4小时热门榜新进", "News Trade · 潜在机会"):
            root = tk.Tk()
            root.withdraw()
            spawned = Mock()
            def children(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from children(child)
            def exercise():
                # Map at zero opacity to perform real Windows layout. Scheduled
                # fade, receipt and timeout callbacks are mocked below.
                root.deiconify()
                root.update()
                buttons = [w for w in children(root) if isinstance(w, tk.Button) and w.cget("text") == "解释推文"]
                self.assertEqual(len(buttons), 1)
                button = buttons[0]
                self.assertGreater(button.winfo_height(), 18)
                self.assertGreater(button.winfo_width(), 45)
                top = root.winfo_children()[0].pack_slaves()[0]
                self.assertIs(button.master, top)
                view = next(w for w in children(top) if isinstance(w,tk.Button) and w.cget('text') == '查看')
                self.assertLessEqual(button.winfo_x()+button.winfo_width(),view.winfo_x())
                self.assertEqual(button.winfo_y(),view.winfo_y())
                brand = next(w for w in children(top) if isinstance(w,tk.Label) and '星云社快讯' in w.cget('text'))
                self.assertLessEqual(brand.winfo_x()+brand.winfo_width(),button.winfo_x())
                self.assertFalse(any(isinstance(w,tk.Button) for w in root.winfo_children()[0].pack_slaves()))
                self.assertLessEqual(button.winfo_y()+button.winfo_height(), root.winfo_height())
                footer = root.winfo_children()[0].pack_slaves()[-1]
                self.assertGreaterEqual(footer.winfo_height(), footer.winfo_reqheight())
                button.invoke()
            try:
                with patch("tkinter.Tk", return_value=root), patch.object(root, "mainloop", side_effect=exercise), \
                     patch.object(root, "destroy"), patch.object(root, "after"), patch.object(desktop_alert, "PopupReceipts"), \
                     patch.object(desktop_alert, "play_sound"), patch.object(desktop_alert, "speak_text"), \
                     patch.object(desktop_alert.subprocess, "Popen", spawned), patch('news_trade_reader.reader_log'):
                    desktop_alert.show_popup({"kind":kind,"title":"测试 · 不播报", "url":"https://example.test", "explanationKey":"a"*40,
                        "explanationPort":8765,"sound":False}, 0)
                args = spawned.call_args.args[0]
                self.assertEqual(args[-4:], ["--explanations", "a"*40, "--port", "8765"])
            finally:
                root.destroy()
