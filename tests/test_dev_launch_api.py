"""Dev 发币的服务端接线：弹窗负载 + 路由 + 后台回扫线程是否真的挂上了。

弹窗那段从 server.py 里按 AST 抽出来跑（不 import 整个 server，避免拉起配置 /
数据库 / 运行时缓存），这样判据测的是**真实代码**而不是它的复印件 —— 与
test_sandwich_detector_api.py 同一套手法。

这里唯一的替身是网络：``token_market`` / ``peak_market_cap`` 会被换成固定值（它们
要打 DexScreener 与 GeckoTerminal）；级别、金额文案、GMGN 链接都用**真实现**，
因为「弹窗里写的级别对不对」正是这个功能最要紧的地方。
"""
import ast
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import dev_launch_monitor as dlm


ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT.joinpath("server.py").read_text(encoding="utf-8")
DEV = "0x" + "ab" * 20
TOKEN = "0x" + "d8d5c24e6d1493b522a12efd695cb344024bee1b"

#: server.py 有 5 万多行，``ast.parse`` 一次要好几秒 —— 必须缓存。
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
            if isinstance(node, ast.FunctionDef) and node.name == "send_dev_launch_desktop_alert"
        ]
        if not nodes:
            raise AssertionError("server.py 里找不到 send_dev_launch_desktop_alert")
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *nodes,
        ], type_ignores=[])
        _ALERT_SENDER_CODE = compile(ast.fix_missing_locations(module), "server.py", "exec")
    return _ALERT_SENDER_CODE


def load_alert_sender(monitor, launch, *, market=None, peak=None):
    """把 ``send_dev_launch_desktop_alert`` 抽出来，只替换掉打外网的两个函数。"""
    namespace = {
        "json": json,
        "time": time,
        "clean_feed_text": clean_feed_text,
        "safe_float": safe_float,
        "DEV_LAUNCH_MONITOR": monitor,
        # 级别 / 金额文案 / 链名 / GMGN 链接一律用真实现：弹窗里写错级别是致命的。
        "dev_launch_tier": dlm.tier_for_market_cap,
        "dev_launch_money_text": dlm.money_text,
        "dev_launch_chain_label": dlm.chain_label,
        "dev_launch_gmgn_url": dlm.gmgn_token_url,
        "dev_launch_token_market": market or (lambda chain, token: {
            "symbol": "QUIV", "name": "QUIVER", "priceUsd": 0.001,
            "marketCapUsd": 12_000_000, "source": "dexscreener",
        }),
        "dev_launch_peak_market_cap": peak or (lambda chain, token, mkt: {
            "peakUsd": 150_000_000, "source": "geckoterminal",
        }),
        "launch_desktop_alert": launch,
    }
    exec(alert_sender_code(), namespace)
    return namespace["send_dev_launch_desktop_alert"]


def launch_event(**overrides):
    event = {
        "chain": "robinhood",
        "dev_address": DEV,
        "token": TOKEN,
        "via": "factory",
        "launch_tx": "0x" + "5a" * 32,
        "launched_at": 1792081333000,
        "label": "NΞO",
    }
    event.update(overrides)
    return event


class DevLaunchAlertSenderTests(unittest.TestCase):
    def setUp(self):
        self.launch = Mock(return_value={"ok": True})
        self.monitor = Mock()
        self.monitor.alerts_enabled = Mock(return_value=True)
        self.monitor.store = Mock()
        self.monitor.store.wallet = Mock(return_value={"label": "NΞO"})
        self.monitor.store.update_token_market = Mock()

    def send(self, event):
        return load_alert_sender(self.monitor, self.launch)(event)

    def test_the_popup_is_self_describing(self):
        self.send(launch_event())
        self.assertEqual(self.launch.call_count, 1)
        payload = self.launch.call_args[0][0]
        self.assertEqual(payload["board"], "dev-launch")
        self.assertEqual(payload["sourceId"], "dev-launch")
        self.assertEqual(payload["sourceType"], "dev-launch")
        self.assertEqual(payload["kind"], "Dev 发币")
        self.assertEqual(payload["chain"], "robinhood")
        self.assertEqual(payload["contractAddress"], TOKEN)
        self.assertEqual(payload["priority"], "顶级 Dev")
        self.assertEqual(payload["queuePriority"], 150)
        # 同一个币重放不能变成第二个弹窗：key 由链 + 代币唯一决定。
        self.assertEqual(payload["key"], f"dev-launch:robinhood:{TOKEN}")

    def test_view_link_opens_the_gmgn_trading_page(self):
        # 口径与 GMGN 热搜榜弹窗一致：用户在 GMGN 下单，且 desktop_alert 用本机
        # Chrome 默认配置打开，天然带已登录会话。
        self.send(launch_event())
        payload = self.launch.call_args[0][0]
        self.assertEqual(payload["url"], f"https://gmgn.ai/robinhood/token/{TOKEN}")

    def test_headline_and_body_carry_the_numbers_that_matter(self):
        self.send(launch_event())
        payload = self.launch.call_args[0][0]
        self.assertIn("QUIV", payload["title"])
        self.assertIn("NΞO", payload["body"])
        self.assertIn("Robinhood", payload["body"])
        self.assertIn("发射台代发", payload["body"])
        self.assertIn("$150.00M", payload["body"])
        self.assertIn("该 Dev 级别：顶级", payload["body"])

    def test_direct_deploy_is_worded_differently_from_a_launchpad(self):
        self.send(launch_event(via="deploy"))
        self.assertIn("直接部署", self.launch.call_args[0][0]["body"])

    def test_the_level_comes_from_the_historical_peak_not_the_current_market_cap(self):
        # 当前市值只有 1200 万（精品档），但历史峰值 1.5 亿 → 必须是「顶级」。
        self.send(launch_event())
        payload = self.launch.call_args[0][0]
        self.assertIn("顶级", payload["body"])
        self.assertNotIn("精品", payload["body"])

    def test_the_peak_is_written_back_before_alerting(self):
        # 级别是从库里的峰值派生的：不先落库，页面刷新后级别又会掉回「普通 / 流水」。
        self.send(launch_event())
        self.assertEqual(self.monitor.store.update_token_market.call_count, 1)
        args = self.monitor.store.update_token_market.call_args[0]
        self.assertEqual(args[0], "robinhood")
        self.assertEqual(args[1], TOKEN)
        self.assertEqual(args[3]["peakUsd"], 150_000_000)
        self.assertEqual(args[3]["peakSource"], "geckoterminal")

    def test_market_outage_still_alerts(self):
        # 「谁发了币」这个事实不依赖行情：行情挂了也必须弹，只是没有市值那一行。
        sender = load_alert_sender(
            self.monitor, self.launch,
            market=lambda chain, token: {},
            peak=lambda chain, token, mkt: {"peakUsd": 0, "source": ""},
        )
        sender(launch_event())
        self.assertEqual(self.launch.call_count, 1)
        payload = self.launch.call_args[0][0]
        self.assertIn("普通 / 流水", payload["body"])
        self.assertNotIn("$", payload["title"])

    def test_the_panel_switch_silences_the_popup_at_the_source(self):
        self.monitor.alerts_enabled = Mock(return_value=False)
        result = self.send(launch_event())
        self.assertTrue(result.get("skipped"))
        self.launch.assert_not_called()

    def test_missing_chain_or_token_is_skipped(self):
        for overrides in ({"chain": ""}, {"token": ""}):
            with self.subTest(overrides=overrides):
                self.launch.reset_mock()
                result = self.send(launch_event(**overrides))
                self.assertTrue(result.get("skipped"))
                self.launch.assert_not_called()

    def test_an_unnamed_dev_falls_back_to_a_short_address(self):
        self.monitor.store.wallet = Mock(return_value={})
        self.send(launch_event(label=""))
        self.assertIn(f"{DEV[:8]}…{DEV[-4:]}", self.launch.call_args[0][0]["body"])


class EngineSnapshotTests(unittest.TestCase):
    """``payload()`` 是前端唯一的数据来源，字段必须齐全且口径一致。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = dlm.DevLaunchStore(Path(tmp.name) / "dev_launch.sqlite")
        self.monitor = dlm.DevLaunchMonitor(self.store, interval=120)

    def test_payload_shape_and_summary_agree_with_the_counts(self):
        self.store.save_wallet("bsc", DEV, "老王")
        self.store.save_wallet("robinhood", "0x" + "22" * 20, "小号", enabled=False)
        payload = self.monitor.payload()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["running"])
        self.assertEqual([chain["id"] for chain in payload["chains"]], list(dlm.EVM_CHAINS))
        self.assertEqual([tier["id"] for tier in payload["tiers"]],
                         ["legendary", "premium", "quality", "flow"])
        self.assertEqual(len(payload["wallets"]), 2)
        summary = payload["summary"]
        self.assertEqual(summary["wallets"], 2)
        self.assertEqual(summary["enabledWallets"], 1)
        self.assertTrue(summary["alertsEnabled"])
        # 每个 Dev 都带级别对象（前端渲染徽章直接读它）。
        for wallet in payload["wallets"]:
            self.assertIn("tier", wallet)
            self.assertIn("peakMarketCapText", wallet["tier"])

    def test_the_alert_switch_round_trips_through_the_store(self):
        self.assertTrue(self.monitor.alerts_enabled())
        self.assertFalse(self.monitor.set_alerts_enabled(False))
        self.assertFalse(self.monitor.alerts_enabled())
        self.assertTrue(self.monitor.set_alerts_enabled("1"))
        self.assertTrue(self.monitor.alerts_enabled())

    def test_a_second_cycle_does_not_overlap_the_first(self):
        self.assertTrue(self.monitor._cycle_lock.acquire(blocking=False))
        try:
            self.assertEqual(self.monitor.run_cycle(),
                             {"discovered": 0, "enriched": 0, "busy": 1})
        finally:
            self.monitor._cycle_lock.release()

    def test_pausing_a_dev_keeps_its_label(self):
        # 暂停时不能把备注一起覆盖掉（与聪明钱 / 被夹检测同一口径）。
        self.store.save_wallet("bsc", DEV, "老王")
        self.store.save_wallet("bsc", DEV, enabled=False)
        wallet = self.store.wallets()[0]
        self.assertFalse(wallet["enabled"])
        self.assertEqual(wallet["label"], "老王")

    def test_results_expose_both_the_gmgn_and_the_explorer_link(self):
        self.store.save_wallet("robinhood", DEV, "NΞO")
        self.store.upsert_token({
            "chain": "robinhood", "token": TOKEN, "dev_address": DEV,
            "symbol": "QUIV", "launch_tx": "0x" + "5a" * 32,
        })
        row = self.monitor.payload()["results"][0]
        self.assertEqual(row["url"], f"https://gmgn.ai/robinhood/token/{TOKEN}")
        self.assertEqual(row["explorerUrl"],
                         f"https://robinhoodchain.blockscout.com/token/{TOKEN}")
        self.assertEqual(row["tierLabel"], "普通 / 流水")
        self.assertEqual(row["devLabel"], "NΞO")


class ServerWiringTests(unittest.TestCase):
    """一读一写两个端点 + 扫描线程 + 弹窗回调，缺一个整条链路就是死的。"""

    def setUp(self):
        self.source = SERVER_SOURCE

    def test_routes_are_registered(self):
        self.assertIn('parsed.path == "/api/dev-launch"', self.source)
        self.assertIn('route == "/api/dev-launch" or route.endswith("/api/dev-launch")', self.source)
        self.assertIn("DEV_LAUNCH_MONITOR.payload()", self.source)
        self.assertIn("DEV_LAUNCH_MONITOR.run_cycle", self.source)

    def test_the_monitor_is_started_and_stopped_with_the_service(self):
        self.assertIn("DEV_LAUNCH_MONITOR.set_alert_callback(send_dev_launch_desktop_alert)", self.source)
        self.assertIn("DEV_LAUNCH_MONITOR.start()", self.source)
        self.assertIn("atexit.register(DEV_LAUNCH_MONITOR.close)", self.source)
        self.assertIn("DEV_LAUNCH_MONITOR.stop()", self.source)

    def test_the_scan_thread_only_runs_on_the_primary_monitor_port(self):
        # 多端口并存时只有主端口该扫链，否则同一个 Dev 会被扫两遍、弹两遍窗。
        self.assertIn("if args.port == primary_monitor_port:\n        DEV_LAUNCH_MONITOR.start()", self.source)

    def test_the_payload_is_identity_enriched_like_other_monitor_boards(self):
        self.assertIn('"/api/sandwich-detector", "/api/dev-launch"', self.source)

    def test_writing_actions_are_admin_only_and_same_origin(self):
        block = self.source.split('route == "/api/dev-launch"', 1)[1][:900]
        self.assertIn("self.request_is_local_or_admin()", block)
        self.assertIn("self.request_origin_allowed()", block)

    def test_the_backfill_runs_off_the_request_thread(self):
        # 加地址后要翻多页 + 逐笔查内部交易，几十秒起步；同步做会把 HTTP 请求拖超时。
        # 注意必须先切到 /api/dev-launch 那段再找 action == "add" —— server.py 里
        # 还有 price-watch 的同名分支，直接 split 会切到错的那个。
        dev_block = self.source.split('route == "/api/dev-launch"', 1)[1]
        add_block = dev_block.split('if action == "add":', 1)[1][:1200]
        self.assertIn("threading.Thread(", add_block)
        self.assertIn("DEV_LAUNCH_MONITOR.scan_wallet_now", add_block)


if __name__ == "__main__":
    unittest.main()
