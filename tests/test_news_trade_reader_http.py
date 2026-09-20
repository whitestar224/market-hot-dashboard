import io
import inspect
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch
from news_trade_reader import reader_payload, show_reader


class ReaderHttpTests(unittest.TestCase):
    def test_open_reader_keeps_reconnecting_until_closed_or_completed(self):
        source=inspect.getsource(show_reader)
        self.assertIn('while not stopped.is_set()',source)
        self.assertNotIn('for _ in range(90)',source)

    def test_retry_fixed_loopback_json_and_actionable_http_failures(self):
        for code,expected in [(400,'原事件'),(403,'访问被拒绝'),(429,'一分钟'),(503,'繁忙')]:
            opener=Mock();opener.open.side_effect=urllib.error.HTTPError('http://127.0.0.1',code,'ignored',{},io.BytesIO(b'{"error":"SECRET_IGNORE"}'))
            with patch('news_trade_reader.reader_log'):
                result=reader_payload(opener,'a'*40,8765,True)
            self.assertIn(expected,result['message']);self.assertNotIn('SECRET',result['message'])
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'http://127.0.0.1:8765/api/news-trade/explanations/retry')
            self.assertEqual(json.loads(request.data),{'id':'a'*40})
            self.assertEqual(request.method,'POST')

    def test_transport_and_malformed_response_are_distinguished(self):
        opener=Mock();opener.open.side_effect=TimeoutError()
        with patch('news_trade_reader.reader_log'):
            reconnecting=reader_payload(opener,'a'*40,8765)
        self.assertTrue(reconnecting['connectionFailed'])
        self.assertEqual(reconnecting['status'],'pending')
        self.assertIn('自动重连',reconnecting['message'])
        opener.open.side_effect=None
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"ok":false,"error":"SECRET_IGNORE"}'
        opener.open.return_value=response
        with patch('news_trade_reader.reader_log'):
            result=reader_payload(opener,'a'*40,8765)
        self.assertIn('格式异常',result['message']);self.assertNotIn('SECRET',result['message'])
