"""AIcoin 热门榜「取不到时」的表现（2026-11-05）。

用户截图反馈：官方加密接口和桌面端调试口同时挂掉时，卡片把两条 requests 异常的
全文（URL / 端口 / WinError 编号）直接糊在中间 —— 既看不懂，也不知道该做什么。

这一组用例守三件事：

* 实时源断了要**回落展示上一次真实榜单**，但必须标成 stale 并写明「X 分钟前」，
  绝不允许把旧榜单伪装成实时（否则就是 2026 年那次 "Fix AICoin hot list refresh"
  要治的老毛病）；
* 空卡上只留一句人话 + 该干什么，完整异常进 ``detail``；
* 官方接口失败后要有冷却，别让每次刷新都白等 18 秒超时。
"""

import json
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import server


def source_payload(updated_at_ms=None, rows=None):
    rows = rows if rows is not None else [
        {"rank": index + 1, "symbol": symbol, "name": symbol, "rows": []}
        for index, symbol in enumerate(("SNDK", "ZEC", "SOXL", "XAU", "AKE"))
    ]
    payload = {
        "id": "aicoin",
        "title": "AIcoin 热门榜",
        "status": "ok",
        "subtitle": "AICoin encrypted getHotCoinHour, count=666, currency USD",
        "sourceName": "AICoin encrypted getHotCoinHour",
        "rows": rows,
    }
    if updated_at_ms is not None:
        payload["updatedAt"] = updated_at_ms
    return payload


class AicoinStaleFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "market-hot_aicoin.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_cache(self, payload):
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_expired_cache_is_still_offered_instead_of_a_dead_card(self):
        """缓存 3 小时前的也必须能回落 —— 以前「超过 30 分钟就不许回落」才导致空卡。"""

        three_hours_ago = int(time.time() * 1000) - 3 * 3600 * 1000
        self.write_cache(source_payload(three_hours_ago))
        self.assertTrue(server.source_fallback_allowed("aicoin", self.path))

    def test_fallback_is_marked_stale_with_its_age(self):
        two_hours_ago = int(time.time() * 1000) - 2 * 3600 * 1000
        self.write_cache(source_payload(two_hours_ago))

        fallback = server.cached_source_fallback("aicoin", self.path)

        self.assertEqual(fallback["status"], "stale")
        self.assertEqual(len(fallback["rows"]), 5)
        self.assertIn("小时前", fallback["subtitle"])
        self.assertIn("实时源不可用", fallback["subtitle"])
        # 时间戳保持原始抓取时间，别让「读缓存」把它刷新成现在。
        self.assertEqual(fallback["updatedAt"], two_hours_ago)
        self.assertGreaterEqual(fallback["staleMinutes"], 119)

    def test_minutes_old_cache_says_minutes(self):
        twelve_minutes_ago = int(time.time() * 1000) - 12 * 60 * 1000
        self.write_cache(source_payload(twelve_minutes_ago))

        fallback = server.cached_source_fallback("aicoin", self.path)

        self.assertIn("12 分钟前", fallback["subtitle"])

    def test_empty_cache_still_yields_nothing(self):
        """没有行就还是让它报错 —— 回落不是用来掩盖「一次都没成功过」的。"""

        self.write_cache(source_payload(int(time.time() * 1000), rows=[]))
        self.assertEqual(server.cached_source_fallback("aicoin", self.path), {})

    def test_other_sources_are_not_touched_by_the_stale_marking(self):
        self.write_cache({
            "id": "binance", "title": "币安热门", "status": "ok",
            "sourceName": "币安", "rows": [{"rank": 1, "symbol": "BTC"}],
            "updatedAt": int(time.time() * 1000) - 5 * 3600 * 1000,
        })

        fallback = server.cached_source_fallback("binance", self.path)

        self.assertNotEqual(fallback["status"], "stale")
        self.assertEqual(len(fallback["rows"]), 1)


class AicoinFailureHintTests(unittest.TestCase):
    """空卡上的一句话必须说清「哪条断了」，而不是甩 requests 的全文。"""

    def test_ssl_reset_reads_as_needs_a_proxy(self):
        hint = server.aicoin_rest_hint(
            OSError("SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))")
        )
        self.assertIn("连不上", hint)

    def test_timeout_reads_as_timeout(self):
        self.assertIn("超时", server.aicoin_rest_hint(TimeoutError("connection timed out")))

    def test_missing_local_state_is_named(self):
        hint = server.aicoin_rest_hint(RuntimeError("AICoin local authorized state was not found"))
        self.assertIn("授权态", hint)

    def test_client_refused_port_reads_as_debug_port_closed(self):
        hint = server.aicoin_client_hint(
            ConnectionError("[WinError 10061] 由于目标计算机积极拒绝，无法连接。")
        )
        self.assertIn("9222", hint)

    def test_proxy_swallowed_debug_port_is_called_out(self):
        """http_proxy 把 127.0.0.1 也代理走时 /json/list 回的是 HTML → JSON 解析失败。"""

        hint = server.aicoin_client_hint(json.JSONDecodeError("Expecting value", "<html>", 0))
        self.assertIn("http_proxy", hint)

    def test_card_message_stays_short_and_actionable(self):
        raw = (
            "SSLError(HTTPSConnectionPool(host='vip-pcapi.aicoin.com', port=443): "
            "Max retries exceeded with url: /api/upgrade/billboard/getHotCoinHour)"
        )
        message = server.aicoin_unavailable_message(
            server.aicoin_rest_hint(OSError(raw)),
            server.aicoin_client_hint(ConnectionError("[WinError 10061] refused")),
        )
        self.assertNotIn("HTTPSConnectionPool", message)
        self.assertLess(len(message), 200)
        self.assertIn("--remote-debugging-port=9222", message)


class AicoinUnavailableCardTests(unittest.TestCase):
    def test_unavailable_card_keeps_the_raw_reason_in_detail(self):
        card = server.aicoin_unavailable("官方接口连不上。", detail="rest: boom")
        self.assertEqual(card["status"], "unavailable")
        self.assertEqual(card["rows"], [])
        self.assertEqual(card["detail"], "rest: boom")
        self.assertIn("官方接口连不上", card["emptyMessage"])


class AicoinNeverLaunchesTheDesktopAppTests(unittest.TestCase):
    """用户 2026-11-05：不要在后台反复起 AiCoin 桌面端，要「无感」。

    老实现会在调试口没开时「强杀 AiCoin → 带 --remote-debugging-port 重开 → 等 18 秒」，
    现象是桌面上的 AiCoin 窗口隔一会儿自己弹出来。这一组守住「永不启动 GUI 进程」。
    """

    def test_ensure_never_spawns_a_process(self):
        calls = []

        def record(*args, **kwargs):
            calls.append(args)
            raise AssertionError("服务端不允许拉起 AiCoin 桌面端")

        with unittest.mock.patch.object(server.subprocess, "Popen", record), \
                unittest.mock.patch.object(server.subprocess, "run", record):
            server.ensure_aicoin_cdp_ready("127.0.0.1", "9222")

        self.assertEqual(calls, [])

    def test_card_message_does_not_ask_the_service_to_relaunch_the_client(self):
        message = server.aicoin_unavailable_message(
            "官方接口连不上（本机直连被重置，需要代理）",
            "AiCoin 客户端没开调试口（127.0.0.1:9222）",
        )
        self.assertIn("不会在后台启停客户端", message)
        self.assertNotIn("让服务自动", message)
        # 仍然告诉用户「想走客户端通道该怎么做」。
        self.assertIn("--remote-debugging-port=9222", message)


class AicoinDebugPortProbeCooldownTests(unittest.TestCase):
    """客户端没开时，别每次刷新都去敲 9222 白等 2~4 秒。"""

    def setUp(self):
        server.AICOIN_CDP_PROBE["until"] = 0.0

    def tearDown(self):
        server.AICOIN_CDP_PROBE["until"] = 0.0

    def test_probe_failure_is_remembered_for_a_minute(self):
        probes = []

        def boom(*args, **kwargs):
            probes.append(args)
            raise ConnectionError("[WinError 10061] 目标计算机积极拒绝")

        with unittest.mock.patch.object(server.requests, "get", boom):
            with self.assertRaises(Exception):
                server.aicoin_client_hot_payload()
            self.assertGreater(server.AICOIN_CDP_PROBE["until"], time.time())
            # 第二次不再真的去敲调试口。
            with self.assertRaises(RuntimeError):
                server.aicoin_client_hot_payload()

        self.assertEqual(len(probes), 1)

if __name__ == "__main__":
    unittest.main()
