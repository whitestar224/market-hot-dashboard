"""Exercise the actual Tk reader while withdrawn; no visible alerts or network."""
import json
import time
import unittest
from unittest.mock import Mock, patch

from news_trade_reader import show_reader


class ReaderGuiTests(unittest.TestCase):
    def setUp(self):
        patcher=patch('news_trade_reader.reader_log')
        patcher.start();self.addCleanup(patcher.stop)

    def test_retry_error_preserves_original_symbol_and_actionable_reason(self):
        import io
        import urllib.error
        import tkinter as tk
        root=tk.Tk();root.withdraw()
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'title':'AKE','status':'unavailable','message':'搜索暂不可用'}).encode()
        opener=Mock();opener.open.side_effect=[response,urllib.error.HTTPError('http://127.0.0.1',429,'rate limited',{},io.BytesIO(b'{}'))]
        def descendants(widget):
            for w in widget.winfo_children():
                yield w;yield from descendants(w)
        def retry_buttons():
            return [w for w in descendants(root) if isinstance(w,tk.Button) and w.cget('text')=='重新搜索 / 复核']
        def exercise():
            for _ in range(60):
                root.update()
                if retry_buttons():break
                time.sleep(.02)
            self.assertIn('AKE',root.title());retry_buttons()[0].invoke()
            for _ in range(60):
                root.update()
                text=' '.join(str(w.cget('text')) for w in descendants(root) if isinstance(w,tk.Label))
                if '一分钟后' in text:break
                time.sleep(.02)
            self.assertIn('AKE',root.title());self.assertIn('一分钟后',text)
            self.assertNotIn('重试暂未启动',text);self.assertEqual(retry_buttons()[0].cget('state'),'normal')
        try:
            with patch('tkinter.Tk',return_value=root),patch.object(root,'mainloop',side_effect=exercise),patch('urllib.request.build_opener',return_value=opener):
                show_reader('a'*40,8765,'AKE')
        finally:root.destroy()

    def test_transient_service_restart_recovers_without_manual_retry(self):
        import tkinter as tk
        root=tk.Tk();root.withdraw()
        payload={
            'title':'BNBGUY',
            'status':'analysis',
            'message':'已生成 AI 中文解读',
            'posts':[],
            'aiExplanation':{'summary':'重连后成功显示结果'},
        }
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(payload).encode()
        opener=Mock();opener.open.side_effect=[TimeoutError(),response]

        def descendants(widget):
            for child in widget.winfo_children():
                yield child;yield from descendants(child)

        def exercise():
            for _ in range(80):
                root.update()
                text=' '.join(str(w.cget('text')) for w in descendants(root) if isinstance(w,tk.Label))
                if '重连后成功显示结果' in text:break
                time.sleep(.01)
            self.assertIn('重连后成功显示结果',text)
            self.assertIn('BNBGUY',root.title())

        try:
            with patch('tkinter.Tk',return_value=root), \
                 patch.object(root,'mainloop',side_effect=exercise), \
                 patch('urllib.request.build_opener',return_value=opener), \
                 patch('news_trade_reader.threading.Event.wait',return_value=False):
                self.assertEqual(show_reader('a'*40,8765,'BNBGUY'),0)
            self.assertEqual(opener.open.call_count,2)
        finally:root.destroy()

    def test_default_three_expand_five_and_original_links(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as exc:
            self.skipTest(str(exc))
        root.withdraw()
        payload = {"title": "测试标的", "status": "ready", "message": "测试数据，不发送播报", "posts": [
            {"id": str(123456+i), "handle": f"writer{i}", "author": f"作者{i}", "focus": "事件来龙去脉",
             "reason": "测试阅读重点", "text": "测试原文内容。" * 25,
             "url": f"https://x.com/writer{i}/status/{123456+i}", "publishedAt": 1788864000000}
            for i in range(5)]}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(payload).encode()
        opener = Mock()
        opener.open.return_value = response
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def buttons(text):
            return [w for w in descendants(root) if isinstance(w, tk.Button) and w.cget("text") == text]
        def exercise():
            for _ in range(30):
                root.update()
                if buttons("再看 2 篇"):
                    break
                time.sleep(.02)
            self.assertEqual(len(buttons("在 X 查看原推文 ↗")), 3)
            buttons("再看 2 篇")[0].invoke()
            root.update()
            self.assertEqual(len(buttons("在 X 查看原推文 ↗")), 5)
            self.assertIn("测试标的", root.title())
        try:
            with patch("tkinter.Tk", return_value=root), patch.object(root, "mainloop", side_effect=exercise), \
                 patch("urllib.request.build_opener", return_value=opener):
                self.assertEqual(show_reader("a"*40, 8765), 0)
            self.assertTrue(opener.open.call_args.args[0].startswith("http://127.0.0.1:8765/api/news-trade/explanations?id="))
        finally:
            root.destroy()

    def test_unreadable_post_link_and_manual_search_are_visible_and_safe(self):
        try:
            import tkinter as tk
            root=tk.Tk()
        except Exception as exc:
            self.skipTest(str(exc))
        root.withdraw()
        post_url='https://x.com/writer/status/1234567890123456789'
        search_url='https://x.com/search?q=%22TEST%22&f=live'
        payload={'title':'TEST','status':'links','message':'正文暂不可读','posts':[],
                 'links':[{'url':post_url,'title':'原推文'}],
                 'searchLinks':[{'url':search_url,'label':'在 X 搜索标的'}]}
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(payload).encode();opener=Mock();opener.open.return_value=response
        def descendants(widget):
            for child in widget.winfo_children():
                yield child;yield from descendants(child)
        def exercise():
            for _ in range(40):
                root.update();buttons=[w for w in descendants(root) if isinstance(w,tk.Button)]
                if any('原推文' in str(w.cget('text')) for w in buttons):break
                time.sleep(.02)
            labels=' '.join(str(w.cget('text')) for w in descendants(root) if isinstance(w,tk.Label))
            self.assertIn('相关推文链接 · 正文未核验',labels)
            self.assertTrue(any('原推文' in str(w.cget('text')) for w in buttons))
            self.assertTrue(any('在 X 搜索标的' in str(w.cget('text')) for w in buttons))
        try:
            with patch('tkinter.Tk',return_value=root),patch.object(root,'mainloop',side_effect=exercise),\
                 patch('urllib.request.build_opener',return_value=opener):
                show_reader('a'*40,8765,'TEST')
        finally:root.destroy()

    def test_ai_fallback_is_clearly_labeled_and_shows_event_explanation(self):
        try:
            import tkinter as tk
            root=tk.Tk()
        except Exception as exc:
            self.skipTest(str(exc))
        root.withdraw()
        payload={'title':'TEST','status':'ready','message':'已生成 AI 中文解读','posts':[{ 
                     'url':'https://x.com/researcher/status/12345','author':'研究员','handle':'researcher',
                     'text':'这是此前保存的相关原帖','sourceType':'previously-saved-post'}],
                 'aiExplanation':{'sourceType':'local-codex-analysis','summary':'事件概要',
                                   'confusionPoint':'最容易误解的是把凭证币当成治理币',
                                   'asset':'这是用户当前看到的质押凭证代币',
                                   'plainMechanism':'质押原生币 → 协议返回凭证 → 赎回时换回底层资产',
                                   'conceptDistinctions':'底层资产不是质押凭证；质押凭证也不是治理币',
                                   'underlyingEvent':'产品发布触发传播',
                                   'whyHot':'成交和讨论同步升温','narrative':'资产映射叙事',
                                   'opportunity':'关注采用扩张','validation':'观察后续数据'}}
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(payload).encode();opener=Mock();opener.open.return_value=response
        def descendants(widget):
            for child in widget.winfo_children():
                yield child;yield from descendants(child)
        def exercise():
            for _ in range(40):
                root.update()
                labels=' '.join(str(w.cget('text')) for w in descendants(root) if isinstance(w,tk.Label))
                if 'AI 事件、叙事与机会解读' in labels:break
                time.sleep(.02)
            self.assertIn('AI 事件、叙事与机会解读 · 非 X 推文',labels)
            self.assertIn('事件概要',labels);self.assertIn('用户当前看到的质押凭证代币',labels)
            self.assertIn('最容易搞混的地方',labels);self.assertIn('把凭证币当成治理币',labels)
            self.assertIn('具体机制怎么运转（大白话）',labels);self.assertIn('容易混淆的概念',labels)
            self.assertIn('底层资产不是质押凭证',labels)
            self.assertIn('背后事件起因',labels);self.assertIn('核心叙事',labels)
            self.assertIn('潜在机会',labels);self.assertIn('此前按同一链与 CA 保存',labels)
        try:
            with patch('tkinter.Tk',return_value=root),patch.object(root,'mainloop',side_effect=exercise),\
                 patch('urllib.request.build_opener',return_value=opener):
                show_reader('a'*40,8765,'TEST')
        finally:root.destroy()

    def test_timed_out_search_still_shows_monitor_context_and_retry(self):
        try:
            import tkinter as tk
            root=tk.Tk()
        except Exception as exc:
            self.skipTest(str(exc))
        root.withdraw()
        payload={'title':'BNBGUY','status':'unavailable',
                 'message':'本地 Codex 本次搜索超时；已保留即时的监控数据说明',
                 'posts':[],'retryable':True,
                 'aiExplanation':{'sourceType':'local-monitor-context',
                     'summary':'BNBGUY 已触发监控。','asset':'BNBGUY；CA：0x1234',
                     'underlyingEvent':'真正事件起因仍在核对。'}}
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(payload).encode();opener=Mock();opener.open.return_value=response
        def descendants(widget):
            for child in widget.winfo_children():
                yield child;yield from descendants(child)
        def exercise():
            for _ in range(40):
                root.update()
                labels=' '.join(str(w.cget('text')) for w in descendants(root) if isinstance(w,tk.Label))
                buttons=[str(w.cget('text')) for w in descendants(root) if isinstance(w,tk.Button)]
                if '监控数据快速说明' in labels:break
                time.sleep(.02)
            self.assertIn('监控数据快速说明 · 本地 Codex 正在补充',labels)
            self.assertIn('BNBGUY 已触发监控',labels)
            self.assertTrue(any('重新搜索 / 补充' in text for text in buttons))
        try:
            with patch('tkinter.Tk',return_value=root),patch.object(root,'mainloop',side_effect=exercise),\
                 patch('urllib.request.build_opener',return_value=opener):
                show_reader('a'*40,8765,'BNBGUY')
        finally:root.destroy()


if __name__ == "__main__":
    unittest.main()
