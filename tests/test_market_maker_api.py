"""做市商监控的服务端接线：弹窗负载 + 路由 + 生命周期。

弹窗那段按 AST 从 server.py 里抽出来跑（不 import 整个 server，避免拉起配置 /
数据库 / 运行时缓存），判据测的是**真实代码**而不是复印件 —— 与
test_dev_launch_api.py / test_sandwich_detector_api.py 同一套手法。

这里唯一的替身是「监控器对象」：`MARKET_MAKER_MONITOR.alerts_enabled()` 用一个
可切换的桩；金额/链名/厂家名/GMGN 链接全部走真实现，因为弹窗里写错这几样是致命的。
"""

import ast
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

# --- 业务代码在 src/ 下：把它加进 sys.path 才能 `from app.x import y` ---------
import sys as _sys
from pathlib import Path as _Path

_SRC_DIR = _Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SRC_DIR))
# ---------------------------------------------------------------------------
from app.monitors import market_maker_monitor as mm
ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT.joinpath("server.py").read_text(encoding="utf-8")
# server.py 里 `from app.core.state import (...)`，所以 `src` 必须在 sys.path 上。
for _entry in (str(ROOT), str(ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

# 报红档位的唯一真源（server.py 也是从这里取）。
from src.app.core.state import DESKTOP_ALERT_CRITICAL_PRIORITY

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
        "DESKTOP_ALERT_CRITICAL_PRIORITY": DESKTOP_ALERT_CRITICAL_PRIORITY,
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
        self.assertIn("$16.9K", alert["body"])
        self.assertIn("Robinhood", alert["body"])

    def test_a_new_token_pops_red_at_critical_priority(self):
        """用户 2026-11-15：新标的要「报红」。"""
        send, sent = load_alert_sender()
        send(event())
        alert = sent[0]
        self.assertEqual(alert["alertTone"], "red")
        self.assertEqual(alert["queuePriority"], DESKTOP_ALERT_CRITICAL_PRIORITY + 40)
        self.assertEqual(alert["priority"], "做市商新标的")
        self.assertIn("首次出现", alert["body"])

    def test_the_alert_says_how_new_the_token_is(self):
        """2026-11-16：正文要带「上线 N 天」—— 既证明它过了成熟度闸，也让人一眼看出盘多新。"""
        send, sent = load_alert_sender()
        send(event(maturity={"ok": True, "reason": "fresh", "ageDays": 3.4,
                             "listedAt": 1_792_000_000_000, "source": "dexscreener"}))
        body = sent[0]["body"]
        self.assertIn("上线 3 天", body)
        self.assertIn("首次出现", body)
        # 刚建池的写「今天刚建池」，不写「上线 0 天」。
        sent.clear()
        send(event(maturity={"ok": True, "reason": "fresh", "ageDays": 0.2,
                             "listedAt": 1_792_000_000_000, "source": "dexscreener"}))
        self.assertIn("今天刚建池", sent[0]["body"])

    def test_only_the_new_token_alert_is_red(self):
        send, sent = load_alert_sender()
        send(event(reason="large-transfer", usd=4_200_000.0))
        self.assertNotIn("alertTone", sent[0], "大额调仓不该跟着报红")

    def test_a_new_token_carries_board_ranks_when_it_is_also_on_a_board(self):
        send, sent = load_alert_sender()
        send(event(resonance={"ok": True, "gainersRank": 3, "gainersChangePct": 18.2, "aicoinRank": 12}))
        body = sent[0]["body"]
        self.assertIn("涨幅榜第 3", body)
        self.assertIn("AIcoin 热门第 12", body)

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

    def test_it_outranks_a_plain_large_rebalance(self):
        # 注意：**不再**比「新标的」高 —— 2026-11-15 起新标的报红，档位是 940，
        # 因为做市商接新币通常发生在它上榜之前，那才是最早的信号。
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
        html = ROOT.joinpath("web/price-watch.html").read_text(encoding="utf-8")
        self.assertIn('data-watch-mode="marketmaker"', html)
        js = ROOT.joinpath("web/price-watch.js").read_text(encoding="utf-8")
        self.assertIn('"marketmaker"', js)
        self.assertIn("/api/market-maker", js)
        self.assertIn("renderMarketMaker", js)

    def test_the_packager_ships_the_module(self):
        """desktop 打包是白名单制：漏一个文件，装了桌面版就整块消失。"""
        package = ROOT.joinpath("package.json").read_text(encoding="utf-8")
        # 业务模块搬进 src/app 之后按 src/** 整体打包。
        self.assertIn('"src/**"', package)


class CadenceTest(unittest.TestCase):
    """一轮 30 个地址要几分钟：间隔必须按「起点」算，否则节奏会被拖成两倍。"""

    def test_the_interval_is_measured_from_the_cycle_start(self):
        source = ROOT.joinpath("src/app/monitors/market_maker_monitor.py").read_text(encoding="utf-8")
        loop = source[source.index("def _loop"):source.index("def run_cycle")]
        self.assertIn("started = time.time()", loop)
        self.assertIn("remaining = self.interval - (time.time() - started)", loop)
        # 跑完直接 wait(interval) 是旧的（错的）写法：实测 5 分钟间隔变成 14 分钟。
        self.assertNotIn("self.stop_event.wait(self.interval)", loop)


class SolanaAddressBookTest(unittest.TestCase):
    """base58 区分大小写：地址簿不能无条件 ``.lower()``（2026-11-15 实际踩过）。

    现象：5 个 Solana 钱包入库时被降成小写，其中 3 个直接变成非法 pubkey，
    ``getSignaturesForAddress`` 回 ``-32602 Invalid param``，另外 2 个凑巧还能解码，
    但查到的是一个**不存在的地址** → 永远 0 笔流水，看着像「这钱包没动静」。
    """

    SOL_HOT = "44P5Ct5JkPz76Rs2K6juC65zXMpFRDrHatxcASJ4Dyra"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = mm.MarketMakerStore(Path(self._tmp.name) / "mm.sqlite")

    def tearDown(self):
        self._tmp.cleanup()

    def test_evm_keys_are_case_insensitive_but_solana_ones_are_not(self):
        self.assertEqual(mm.wallet_key("bsc", "0xF8191D98ae98d2f7aBDFB63A9b0b812b93C873AA"),
                         "0xf8191d98ae98d2f7abdfb63a9b0b812b93c873aa")
        self.assertEqual(mm.wallet_key("solana", self.SOL_HOT), self.SOL_HOT)

    def test_the_lowercased_hot_wallet_is_not_the_same_address(self):
        self.assertTrue(mm.is_solana_address(self.SOL_HOT))
        # 这个恰恰说明「语法合法 ≠ 同一个地址」：`44p5ct5j…` 也能解码成 32 字节，
        # 所以 RPC 不报错、安安静静返回 0 笔流水 —— 看着像「这钱包没动静」，
        # 实际盯的是一个不存在的地址。真正判「是不是同一个」只能靠原样保留大小写。
        self.assertNotEqual(mm.wallet_key("solana", self.SOL_HOT.lower()), self.SOL_HOT)
        # 而有的地址小写之后连解码都不是 32 字节，直接 -32602（MfDuWeq… 就是这样）。
        amm = "MfDuWeqSHEqTFVYZ7LoexgAK9dxk7cy4DFJWjWMGVWa"
        self.assertTrue(mm.is_solana_address(amm))
        self.assertFalse(mm.is_solana_address(amm.lower()))
        self.assertFalse(mm.is_solana_address("0x" + "ab" * 20))

    def test_seeding_keeps_the_solana_case(self):
        rows = {row["address"] for row in self.store.addresses() if row["chain"] == "solana"}
        self.assertIn(self.SOL_HOT, rows)
        self.assertNotIn(self.SOL_HOT.lower(), rows)

    def test_a_solana_wallet_can_be_added_from_the_page(self):
        other = "HwDkuDCUipJHHKodBBCjffFvrjhmd4iVVh7fq25fShvt"
        saved = self.store.save_address("solana", other, "dwf", tag="手加")
        self.assertEqual(saved["address"], other)
        self.assertIsNotNone(self.store.address("solana", other))
        with self.assertRaises(ValueError):
            self.store.save_address("solana", "not-a-wallet", "dwf")

    def test_only_the_polled_chains_are_ever_handed_out(self):
        for row in self.store.addresses_due(500):
            self.assertIn(row["chain"], mm.POLL_CHAINS)
        # Ethereum 的 168 个老地址仍然在册，只是永远不会被排队。
        self.assertTrue(any(row["chain"] == "ethereum" for row in self.store.addresses()))
        self.assertNotIn("ethereum", mm.POLL_CHAINS)


if __name__ == "__main__":
    unittest.main()
