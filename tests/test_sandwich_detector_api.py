"""被夹检测的服务端接线：弹窗负载 + 面向前端的快照 + 路由是否真的挂上了。

弹窗那段是从 server.py 里按 AST 抽出来跑的（不 import 整个 server，避免拉起
配置/数据库/运行时缓存），这样判据测的是**真实代码**而不是它的复印件。
"""
import ast
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import sandwich_detector as sd


ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT.joinpath("server.py").read_text(encoding="utf-8")
#: server.py 有 5 万多行，``ast.parse`` 一次要 ~9 秒 —— 必须缓存，否则每个用例
#: 都重解析一遍（6 个用例就把测试拖到一分钟）。
_ALERT_SENDER_CODE = None


def clean_feed_text(value, limit=200):
    return str(value or "").strip()[:limit]


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def alert_sender_code():
    global _ALERT_SENDER_CODE
    if _ALERT_SENDER_CODE is None:
        tree = ast.parse(SERVER_SOURCE)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "send_sandwich_detected_desktop_alert"
        ]
        if not nodes:
            raise AssertionError("server.py 里找不到 send_sandwich_detected_desktop_alert")
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *nodes,
        ], type_ignores=[])
        _ALERT_SENDER_CODE = compile(ast.fix_missing_locations(module), "server.py", "exec")
    return _ALERT_SENDER_CODE


def load_alert_sender(detector, launch):
    """把 ``send_sandwich_detected_desktop_alert`` 抽出来，注入替身依赖。"""
    namespace = {
        "json": json,
        "time": time,
        "clean_feed_text": clean_feed_text,
        "safe_float": safe_float,
        "SANDWICH_DETECTOR": detector,
        "SMART_MONEY_CHAIN_META": {"bsc": {"label": "BSC"}},
        "sandwich_explorer_url": lambda chain, tx: f"https://bscscan.com/tx/{tx}" if tx else "",
        "launch_desktop_alert": launch,
    }
    exec(alert_sender_code(), namespace)
    return namespace["send_sandwich_detected_desktop_alert"]


def confirmed_event(**overrides):
    event = {
        "chain": "bsc",
        "tx_hash": "0x" + "a" * 64,
        "event_type": "buy",
        "token": "0x" + "bb" * 20,
        "symbol": "PVP",
        "trade_at": 1791729022,
        "detail": {
            "verdict": "confirmed",
            "attacker": "0x" + "7" * 40,
            "pool": "0x" + "aa" * 20,
            "txIndex": 10,
            "price": {"lossUsd": 12.5, "impactPct": 42.0},
        },
    }
    event.update(overrides)
    return event


class SandwichAlertSenderTests(unittest.TestCase):
    def setUp(self):
        self.launch = Mock(return_value={"ok": True})
        self.detector = Mock()
        self.detector.alerts_enabled = Mock(return_value=True)

    def send(self, event):
        return load_alert_sender(self.detector, self.launch)(event)

    def test_the_popup_is_self_describing(self):
        self.send(confirmed_event())
        self.assertEqual(self.launch.call_count, 1)
        payload = self.launch.call_args[0][0]
        self.assertEqual(payload["board"], "sandwich-detector")
        self.assertEqual(payload["sourceId"], "sandwich-detector")
        self.assertEqual(payload["sourceType"], "sandwich-detector")
        self.assertEqual(payload["kind"], "被夹检测")
        self.assertEqual(payload["chain"], "bsc")
        self.assertEqual(payload["contractAddress"], "0x" + "bb" * 20)
        self.assertEqual(payload["url"], "https://bscscan.com/tx/0x" + "a" * 64)
        # 同一笔交易重放不能变成第二个弹窗：key 必须由链+tx+方向唯一决定。
        self.assertEqual(payload["key"], f"sandwich-detected:bsc:0x{'a' * 64}:buy")

    def test_the_headline_carries_the_money_and_the_rest_goes_to_the_body(self):
        self.send(confirmed_event())
        payload = self.launch.call_args[0][0]
        self.assertIn("PVP", payload["title"])
        self.assertIn("$12.50", payload["title"])
        self.assertIn("买入", payload["body"])
        self.assertIn("0x" + "7" * 40, payload["body"])
        self.assertIn("BSC", payload["body"])

    def test_percentage_only_when_the_loss_cannot_be_priced(self):
        # 算不出美元损失时绝不能写「多付 $0.00」——那是把估算当成事实。
        self.send(confirmed_event(detail={
            "verdict": "confirmed", "attacker": "", "pool": "", "txIndex": 3,
            "price": {"lossUsd": 0, "impactPct": 37.5},
        }))
        title = self.launch.call_args[0][0]["title"]
        self.assertIn("37.5%", title)
        self.assertNotIn("$0", title)

    def test_missing_price_block_still_alerts(self):
        self.send(confirmed_event(detail={"verdict": "confirmed", "txIndex": 1}))
        self.assertEqual(self.launch.call_count, 1)
        self.assertIn("同区块前后夹", self.launch.call_args[0][0]["title"])

    def test_the_panel_switch_silences_the_popup_at_the_source(self):
        self.detector.alerts_enabled = Mock(return_value=False)
        result = self.send(confirmed_event())
        self.assertTrue(result.get("skipped"))
        self.launch.assert_not_called()


class EngineSnapshotTests(unittest.TestCase):
    """``payload()`` 是前端唯一的数据来源，字段必须齐全且口径一致。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = sd.SandwichStore(Path(tmp.name) / "sandwich.sqlite")
        self.detector = sd.SandwichDetector(self.store)

    def test_payload_shape_and_summary_agree_with_the_counts(self):
        self.store.save_wallet("bsc", "0x" + "11" * 20, "主钱包")
        self.store.save_wallet("ethereum", "0x" + "22" * 20, "小号", enabled=False)
        payload = self.detector.payload()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["running"])
        self.assertEqual([chain["id"] for chain in payload["chains"]], ["bsc", "ethereum", "base", "robinhood"])
        self.assertEqual(len(payload["wallets"]), 2)
        summary = payload["summary"]
        self.assertEqual(summary["wallets"], 2)
        self.assertEqual(summary["enabledWallets"], 1)
        self.assertEqual(summary["pending"], payload["counts"].get("pending", 0))
        self.assertEqual(summary["minTradeUsd"], sd.DEFAULT_MIN_TRADE_USD)
        self.assertTrue(summary["alertsEnabled"])

    def test_results_carry_contract_address_and_explorer_url(self):
        self.store.upsert_activity({
            "chain": "bsc", "tx_hash": "0x" + "9" * 64, "event_type": "buy", "token": "0x" + "bb" * 20,
            "wallet": "0x" + "11" * 20, "symbol": "PVP", "token_amount": 1000.0, "quote_amount": 2.0,
            "price_usd": 0.002, "trade_at": 1791729022, "gas_usd": 0.1, "priority_fee": 0,
            "tip_fee": 0, "launchpad": "",
        })
        self.store.finish(self.store.pending(1)[0], status="done", verdict="confirmed",
                          detail={"verdict": "confirmed"})
        row = self.detector.payload()["results"][0]
        # contractAddress 是 token 的别名，缺了监控按钮就渲染不出来。
        self.assertEqual(row["contractAddress"], row["token"])
        self.assertTrue(row["explorerUrl"].startswith("https://bscscan.com/tx/0x"))
        self.assertEqual(row["verdict"], "confirmed")

    def test_the_alert_switch_round_trips_through_the_store(self):
        self.assertTrue(self.detector.alerts_enabled())
        self.assertFalse(self.detector.set_alerts_enabled(False))
        self.assertFalse(self.detector.alerts_enabled())
        self.assertTrue(self.detector.set_alerts_enabled("1"))
        self.assertTrue(self.detector.alerts_enabled())

    def test_pausing_a_wallet_keeps_its_label(self):
        # 曾经用 upsert 无脑覆盖 label：点一次「暂停」备注就没了。
        self.store.save_wallet("bsc", "0x" + "11" * 20, "主钱包")
        self.store.save_wallet("bsc", "0x" + "11" * 20, enabled=False)
        wallet = self.store.wallets()[0]
        self.assertFalse(wallet["enabled"])
        self.assertEqual(wallet["label"], "主钱包")

    def test_a_second_cycle_does_not_overlap_the_first(self):
        self.assertTrue(self.detector._cycle_lock.acquire(blocking=False))
        try:
            self.assertEqual(self.detector.run_cycle(), {"ingested": 0, "checked": 0, "busy": True})
        finally:
            self.detector._cycle_lock.release()


def payload_verdict(row):
    return row["verdict"]


class ServerWiringTests(unittest.TestCase):
    """读完一个写的端点 + 扫描线程 + 弹窗回调，缺一个整条链路就是死的。"""

    def setUp(self):
        self.source = SERVER_SOURCE

    def test_routes_are_registered(self):
        self.assertIn('parsed.path == "/api/sandwich-detector"', self.source)
        self.assertIn('route == "/api/sandwich-detector" or route.endswith("/api/sandwich-detector")', self.source)
        self.assertIn("SANDWICH_DETECTOR.payload()", self.source)
        self.assertIn("SANDWICH_DETECTOR.run_cycle", self.source)

    def test_the_detector_is_started_and_stopped_with_the_service(self):
        self.assertIn("SANDWICH_DETECTOR.set_alert_callback(send_sandwich_detected_desktop_alert)", self.source)
        self.assertIn("SANDWICH_DETECTOR.start()", self.source)
        self.assertIn("atexit.register(SANDWICH_DETECTOR.close)", self.source)
        self.assertIn("SANDWICH_DETECTOR.stop()", self.source)

    def test_the_payload_is_identity_enriched_like_other_monitor_boards(self):
        self.assertIn('"/api/smart-money-monitor", "/api/sandwich-detector"', self.source)


if __name__ == "__main__":
    unittest.main()
