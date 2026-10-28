"""做市商监控的服务端接线：弹窗负载 + 路由 + 生命周期。

弹窗那段按 AST 从 server.py 里抽出来跑（不 import 整个 server，避免拉起配置 /
数据库 / 运行时缓存），判据测的是**真实代码**而不是复印件 —— 与
test_dev_launch_api.py / test_sandwich_detector_api.py 同一套手法。

这里唯一的替身是「监控器对象」：`MARKET_MAKER_MONITOR.alerts_enabled()` 用一个
可切换的桩；金额/链名/厂家名/GMGN 链接全部走真实现，因为弹窗里写错这几样是致命的。
"""

import ast
import json
import time
import unittest
from pathlib import Path

import market_maker_monitor as mm

ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT.joinpath("server.py").read_text(encoding="utf-8")

TOKEN = "0x39dbed3a2bd333467115de4566f8d8b0e4a4f2f1"
OTHER_TOKEN = "0x" + "5f" * 20

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
            if isinstance(node, ast.FunctionDef) and node.name == "send_market_maker_desktop_alert"
        ]
        if not nodes:
            raise AssertionError("server.py 里找不到 send_market_maker_desktop_alert")
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *nodes,
        ], type_ignores=[])
        _ALERT_SENDER_CODE = compile(ast.fix_missing_locations(module), "server.py", "exec")
    return _ALERT_SENDER_CODE


class StubMonitor:
    def __init__(self, alerts_enabled=True):
        self._alerts = alerts_enabled

    def alerts_enabled(self):
        return self._alerts


def load_alert_sender(*, alerts_enabled=True):
    sent = []

    def launch(payload):
        sent.append(payload)
        return {"ok": True}

    namespace = {
        "json": json,
        "time": time,
        "clean_feed_text": clean_feed_text,
        "safe_float": safe_float,
        "MARKET_MAKER_MONITOR": StubMonitor(alerts_enabled),
        # 文案与链接一律用真实现：这里写错，用户看到的就是错的。
        "mm_amount_text": mm.amount_text,
        "mm_money_text": mm.money_text,
        "mm_chain_label": mm.chain_label,
        "mm_firm_label": mm.firm_label,
        "mm_gmgn_token_url": mm.gmgn_token_url,
        "mm_explorer_tx_url": mm.explorer_tx_url,
        "launch_desktop_alert": launch,
    }
    exec(alert_sender_code(), namespace)
    return namespace["send_market_maker_desktop_alert"], sent


def event(**overrides):
    row = {
        "reason": "new-token",
        "firm": "wintermute",
        "chain": "robinhood",
        "token": TOKEN,
        "symbol": "PONS",
        "direction": "in",
        "amount": 60_170.0,
        "usd": 16_900.0,
        "counterparty": "0x" + "1a" * 20,
        "txHash": "0x" + "be" * 32,
        "occurredAt": 1_792_500_000_000,
    }
    row.update(overrides)
    return row


class AlertSenderTest(unittest.TestCase):
    def test_a_new_token_is_announced_as_that_firm_starting_to_trade_it(self):
        send, sent = load_alert_sender()
        result = send(event())
        self.assertTrue(result.get("ok"))
        self.assertEqual(len(sent), 1)
        alert = sent[0]
        self.assertEqual(alert["kind"], "做市商监控")
        self.assertEqual(alert["board"], "market-maker")
        self.assertIn("Wintermute", alert["title"])
        self.assertIn("PONS", alert["title"])
        self.assertEqual(alert["chain"], "robinhood")
        self.assertEqual(alert["contractAddress"], TOKEN)
        self.assertEqual(alert["queuePriority"], 78)
        self.assertIn("$16.9K", alert["body"])
        self.assertIn("Robinhood", alert["body"])

    def test_the_view_button_opens_the_gmgn_token_page(self):
        send, sent = load_alert_sender()
        send(event())
        self.assertEqual(sent[0]["url"], f"https://gmgn.ai/robinhood/token/{TOKEN}")

    def test_a_large_rebalance_ranks_above_a_new_token(self):
        send, sent = load_alert_sender()
        send(event(reason="large-transfer", usd=4_200_000.0, direction="out", amount=2_100.0))
        alert = sent[0]
        self.assertEqual(alert["queuePriority"], 84)
        self.assertIn("大额调仓", alert["title"])
        self.assertIn("转出", alert["title"])
        self.assertIn("$4.20M", alert["body"])

    def test_an_ethereum_token_links_to_the_eth_gmgn_page(self):
        send, sent = load_alert_sender()
        send(event(chain="ethereum", token=OTHER_TOKEN))
        self.assertEqual(sent[0]["url"], f"https://gmgn.ai/eth/token/{OTHER_TOKEN}")

    def test_the_explorer_is_only_the_fallback(self):
        """链折算不出 GMGN slug 时也要给出一个能点的地址，不能是死链。"""
        send, sent = load_alert_sender()
        send(event(chain="base", token=OTHER_TOKEN, txHash="0x" + "cd" * 32))
        # Base 的 slug 是 base，拼得出来 → 还是 GMGN
        self.assertEqual(sent[0]["url"], f"https://gmgn.ai/base/token/{OTHER_TOKEN}")
        send(event(chain="arc", token=OTHER_TOKEN))
        # arc 没有 slug：GMGN 为空、区块浏览器也没有对应模板 → 宁可为空
        self.assertEqual(sent[1]["url"], "")

    def test_popups_are_off_when_the_sub_board_switch_is_off(self):
        send, sent = load_alert_sender(alerts_enabled=False)
        result = send(event())
        self.assertTrue(result.get("skipped"))
        self.assertEqual(sent, [])

    def test_only_the_agreed_reasons_pop_up(self):
        """用户 2026-10-20 拍板：新标的 + 大额调仓；2026-10-21 追加三方共振。
        其余（普通流水 / 基线 / 拼错的原因）一律不弹。"""
        send, sent = load_alert_sender()
        for reason in ("transfer", "baseline", "", "large", "resonance"):
            with self.subTest(reason=reason):
                result = send(event(reason=reason))
                self.assertTrue(result.get("skipped"))
        self.assertEqual(sent, [])

    def test_an_event_missing_the_firm_or_token_is_dropped(self):
        send, sent = load_alert_sender()
        for broken in ({"firm": ""}, {"token": ""}, {"chain": ""}):
            with self.subTest(broken=broken):
                result = send(event(**broken))
                self.assertTrue(result.get("skipped"))
        self.assertEqual(sent, [])


class TripleResonanceAlertTest(unittest.TestCase):
    """三方共振（大额异动 × 涨幅榜 × AIcoin）：用户 2026-10-21 追加。

    它是「大额调仓」的升级版，不是第四类事件 —— 所以排队优先级必须高过普通大额，
    而且正文要把**榜单名次与涨幅**写出来，否则用户看不出这条凭什么更急。
    """

    def send(self, **overrides):
        send, sent = load_alert_sender()
        payload = event(
            reason="triple-resonance",
            usd=2_400_000.0,
            amount=900_000.0,
            resonance={"gainersRank": 3, "gainersChangePct": 18.24, "aicoinRank": 12},
        )
        payload.update(overrides)
        send(payload)
        return sent[0] if sent else None

    def test_the_alert_says_all_three_sides_out_loud(self):
        alert = self.send()
        self.assertIn("三方共振", alert["title"])
        self.assertIn("Wintermute", alert["title"])
        self.assertIn("PONS", alert["title"])
        self.assertIn("$2.40M", alert["body"])
        self.assertIn("涨幅榜第 3", alert["body"])
        self.assertIn("+18.2%", alert["body"])
        self.assertIn("AIcoin 热门第 12", alert["body"])
        self.assertIn("Robinhood", alert["body"])

    def test_it_outranks_a_plain_large_rebalance_and_a_new_token(self):
        alert = self.send()
        self.assertEqual(alert["queuePriority"], 90)
        self.assertGreater(alert["queuePriority"], 84)
        self.assertEqual(alert["priority"], "做市商三方共振")

    def test_the_view_button_still_opens_gmgn(self):
        alert = self.send()
        self.assertEqual(alert["url"], f"https://gmgn.ai/robinhood/token/{TOKEN}")

    def test_missing_rank_detail_degrades_to_a_plain_sentence(self):
        alert = self.send(resonance={})
        self.assertIn("三方共振", alert["title"])
        self.assertIn("涨幅榜 · AIcoin 热门", alert["body"])
        self.assertNotIn("None", alert["body"])
        self.assertNotIn("第 0", alert["body"])

    def test_a_symbol_that_is_only_on_the_gainers_board_is_not_claimed_as_aicoin(self):
        alert = self.send(resonance={"gainersRank": 3, "gainersChangePct": 0, "aicoinRank": 0})
        self.assertIn("涨幅榜第 3", alert["body"])
        self.assertNotIn("AIcoin 热门第", alert["body"])


class PayloadSnapshotTests(unittest.TestCase):
    """页面快照必须走缓存，**不能排在轮询的写事务后面**。

    真机上 GET /api/market-maker 实测 2–47 秒（页面上一直「正在读取」）：轮询线程
    一条流水一次事务，而这个快照要连拿 5 次 store 锁。这里用「库一旦被读就报错」
    来证明第二次调用确实没碰库。
    """

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        store = mm.MarketMakerStore(Path(self._tmp.name) / "mm.sqlite")
        self.monitor = mm.MarketMakerMonitor(store, interval=300, addresses_per_cycle=30)

    def tearDown(self):
        self._tmp.cleanup()

    def _poison_store(self, name):
        def boom(*_args, **_kwargs):
            raise AssertionError(f"缓存命中时不应该再读库：{name}()")

        setattr(self.monitor.store, name, boom)

    def test_second_call_is_served_from_cache(self):
        first = self.monitor.payload()
        self.assertTrue(first["ok"])
        # 缓存热了之后，任何一次读库都会炸 —— 再取一次仍要成功。
        for name in ("addresses", "positions", "recent_transfers", "counts"):
            self._poison_store(name)
        second = self.monitor.payload()
        self.assertTrue(second["ok"])
        self.assertEqual(first["summary"]["addresses"], second["summary"]["addresses"])

    def test_cache_returns_a_copy_so_callers_can_add_keys(self):
        first = self.monitor.payload()
        first["entryGate"] = {"mode": "resonance"}
        second = self.monitor.payload()
        self.assertNotIn("entryGate", second, "缓存对象被调用方改脏了")

    def test_force_refresh_bypasses_the_cache(self):
        self.monitor.refresh_payload_cache()
        self._poison_store("counts")
        with self.assertRaises(AssertionError):
            self.monitor.payload(force_refresh=True)

    def test_expired_cache_still_answers_and_schedules_a_rebuild(self):
        self.monitor.refresh_payload_cache()
        # 把时间戳推回过期之前：读库会炸，说明这一次给的是旧快照而不是重建。
        self.monitor._payload_cache_at = time.time() - mm.PAYLOAD_CACHE_SECONDS - 1
        for name in ("addresses", "positions", "recent_transfers", "counts"):
            self._poison_store(name)
        stale = self.monitor.payload()
        self.assertTrue(stale["ok"])
        # 重建线程拿同样的毒化 store 会失败，但不能把异常抛给页面。
        for _ in range(50):
            if not self.monitor._payload_rebuilding:
                break
            time.sleep(0.02)

    def test_run_cycle_warms_the_cache(self):
        # 没有地址可轮询时 run_cycle 也要把快照热起来，页面才不会每 30 秒重建一次。
        # 真的去打 Blockscout 会让这条单测跑上百秒，所以把「一个地址」这一步桩掉。
        self.monitor.poll_address = lambda row: 0
        self.monitor.run_cycle()
        self.assertIsNotNone(self.monitor._payload_cache)


class ServerWiringTests(unittest.TestCase):
    """读完端点 + 轮询线程 + 弹窗回调，缺一个整条链路就是死的。"""

    def setUp(self):
        self.source = SERVER_SOURCE

    def test_routes_are_registered(self):
        self.assertIn('parsed.path == "/api/market-maker"', self.source)
        self.assertIn('route == "/api/market-maker" or route.endswith("/api/market-maker")', self.source)
        # GET 走快照缓存，POST 改完设置必须强制重建（否则「点了没反应」）。
        self.assertIn("MARKET_MAKER_MONITOR.payload(force_refresh=force_refresh)", self.source)
        self.assertIn("market_maker_payload_with_gate(force_refresh=True)", self.source)
        self.assertIn("MARKET_MAKER_MONITOR.run_cycle", self.source)

    def test_the_monitor_is_started_and_stopped_with_the_service(self):
        self.assertIn(
            "MARKET_MAKER_MONITOR.set_alert_callback(send_market_maker_desktop_alert)", self.source
        )
        self.assertIn("MARKET_MAKER_MONITOR.start()", self.source)
        self.assertIn("atexit.register(MARKET_MAKER_MONITOR.close)", self.source)
        self.assertIn("MARKET_MAKER_MONITOR.stop()", self.source)

    def test_the_resonance_gate_is_wired_into_the_monitor(self):
        """三方共振必须**注入**进去，否则 monitor 永远走普通大额分支（静默失效）。"""
        self.assertIn(
            "MARKET_MAKER_MONITOR.set_resonance_qualifier(market_maker_resonance_info)", self.source
        )
        self.assertIn("def market_maker_resonance_info(", self.source)
        # 明细里要有榜单名次，弹窗才写得出「涨幅榜第 3」。
        self.assertIn('"gainersRank": int(gainers_index[key]["rank"])', self.source)
        self.assertIn('"aicoinRank": int(hot_index[key]["rank"])', self.source)
        # 榜单归一只能有一份实现（server 转调 monitor），两份迟早分叉。
        self.assertIn("return mm_symbol_key(value)", self.source)
        self.assertIn("return set(mm_board_symbol_index(payload, source_ids))", self.source)

    def test_the_post_route_guards_local_and_admin_only(self):
        self.assertIn("仅限本机或管理员配置做市商监控", self.source)

    def test_the_page_has_a_tab_for_it(self):
        html = ROOT.joinpath("price-watch.html").read_text(encoding="utf-8")
        self.assertIn('data-watch-mode="marketmaker"', html)
        js = ROOT.joinpath("price-watch.js").read_text(encoding="utf-8")
        self.assertIn('"marketmaker"', js)
        self.assertIn("/api/market-maker", js)
        self.assertIn("renderMarketMaker", js)

    def test_the_packager_ships_the_module(self):
        """desktop 打包是白名单制：漏一个文件，装了桌面版就整块消失。"""
        package = ROOT.joinpath("package.json").read_text(encoding="utf-8")
        self.assertIn('"market_maker_monitor.py"', package)
        self.assertIn('"alpha_mm_monitor.py"', package)


class CadenceTest(unittest.TestCase):
    """一轮 30 个地址要几分钟：间隔必须按「起点」算，否则节奏会被拖成两倍。"""

    def test_the_interval_is_measured_from_the_cycle_start(self):
        source = ROOT.joinpath("market_maker_monitor.py").read_text(encoding="utf-8")
        loop = source[source.index("def _loop"):source.index("def run_cycle")]
        self.assertIn("started = time.time()", loop)
        self.assertIn("remaining = self.interval - (time.time() - started)", loop)
        # 跑完直接 wait(interval) 是旧的（错的）写法：实测 5 分钟间隔变成 14 分钟。
        self.assertNotIn("self.stop_event.wait(self.interval)", loop)


if __name__ == "__main__":
    unittest.main()
