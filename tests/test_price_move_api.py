"""价格异动监控的服务端接线：弹窗负载 + 行情采集 + 路由 + 生命周期。

弹窗与采集两段按 AST 从 server.py 里抽出来真跑（不 import 整个 server，避免拉起
配置 / 数据库 / 运行时缓存）—— 与 test_market_maker_api.py / test_dev_launch_api.py
同一套手法。替换的只有外部副作用（`launch_desktop_alert` / `binance_futures_json` /
监控器开关），文案、金额格式、GMGN 链接全走真实现。
"""

import ast
import json
import time
import unittest
from pathlib import Path

import market_maker_monitor as mm
import price_move_monitor as pm

ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT.joinpath("server.py").read_text(encoding="utf-8")

TOKEN = "0x39dbed3a2bd333467115de4566f8d8b0e4a4f2f1"

_EXTRACTED = {}


def clean_feed_text(value, limit=200):
    return str(value or "").strip()[:limit]


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def function_code(*names):
    """把 server.py 里这几个顶层函数抽出来编译一次（结果缓存）。"""
    key = tuple(names)
    if key not in _EXTRACTED:
        tree = ast.parse(SERVER_SOURCE)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        found = {node.name for node in nodes}
        missing = set(names) - found
        if missing:
            raise AssertionError(f"server.py 里找不到：{sorted(missing)}")
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *nodes,
        ], type_ignores=[])
        _EXTRACTED[key] = compile(ast.fix_missing_locations(module), "server.py", "exec")
    return _EXTRACTED[key]


class StubMonitor:
    def __init__(self, alerts_enabled=True, payload=None):
        self._alerts = alerts_enabled
        self._payload = payload or {"ok": True, "moves": []}

    def alerts_enabled(self):
        return self._alerts

    def payload(self, *, force_refresh=False):
        self.last_force_refresh = force_refresh
        return {**self._payload, "moves": [dict(row) for row in self._payload.get("moves") or []]}


def load_alert_sender(*, alerts_enabled=True, control=None, control_raises=False):
    """``control`` 是「控盘名单」：``{SYMBOL: {...}}``（空/不带 = 不在名单）。"""
    sent = []

    def launch(payload):
        sent.append(payload)
        return {"ok": True}

    class StubAlphaMM:
        @staticmethod
        def control_targets():
            if control_raises:
                raise RuntimeError("alpha_mm state broken")
            return {str(key).upper(): dict(value) for key, value in (control or {}).items()}

    namespace = {
        "json": json,
        "time": time,
        "clean_feed_text": clean_feed_text,
        "safe_float": safe_float,
        "PRICE_MOVE_MONITOR": StubMonitor(alerts_enabled),
        "ALPHA_MM": StubAlphaMM,
        "mm_price_text": mm.price_text,
        "mm_money_text": mm.money_text,
        "mm_chain_label": mm.chain_label,
        "mm_gmgn_token_url": mm.gmgn_token_url,
        "mm_symbol_key": mm.symbol_key,
        # 链标签归一有独立测试，这里只保证「状态里的 BSC → 展示 BSC」这一段。
        "desktop_alert_chain_label": lambda value: {
            "bsc": "BSC", "eth": "ETH", "ethereum": "ETH",
            "solana": "Solana", "base": "Base",
        }.get(str(value or "").strip().casefold(), ""),
        "launch_desktop_alert": launch,
    }
    exec(
        function_code(
            "price_move_trade_url",
            "price_move_alpha_control",
            "price_move_control_label",
            "send_price_move_desktop_alert",
        ),
        namespace,
    )
    return namespace["send_price_move_desktop_alert"], sent


def load_payload_builder(moves, *, targets=None, alpha_phase=None, targets_raise=False):
    monitor = StubMonitor(payload={"ok": True, "summary": {"total": len(moves)}, "moves": moves})

    class StubAlphaMM:
        @staticmethod
        def control_targets():
            if targets_raise:
                raise RuntimeError("state broken")
            return dict(targets or {})

    namespace = {
        "PRICE_MOVE_MONITOR": monitor,
        "ALPHA_MM": StubAlphaMM,
        "safe_float": safe_float,
        "mm_chain_label": mm.chain_label,
        "mm_gmgn_token_url": mm.gmgn_token_url,
        "mm_money_text": mm.money_text,
        "mm_symbol_key": mm.symbol_key,
        "desktop_alert_chain_label": lambda value: {
            "bsc": "BSC", "eth": "ETH", "solana": "Solana", "base": "Base",
        }.get(str(value or "").strip().casefold(), ""),
        "alpha_phase_for_asset": lambda symbol: (alpha_phase or {}).get(str(symbol or "").upper(), ""),
    }
    exec(function_code("price_move_trade_url", "price_move_control_label", "price_move_payload"), namespace)
    return namespace["price_move_payload"], monitor


def load_market_prices(rows=None, raises=None):
    def fake_json(path, *args, **kwargs):
        if raises:
            raise raises
        return rows

    namespace = {
        "binance_futures_json": fake_json,
        "clean_feed_text": clean_feed_text,
        "safe_float": safe_float,
        "is_excluded_crypto_asset": lambda name: str(name).upper() in {"XYZ"},
        # 稳定币 / 杠杆代币在采集端剔掉，用真实现（误杀真币是致命 Bug）。
        "price_move_is_tradable": pm.is_tradable_symbol,
    }
    exec(function_code("price_move_market_prices"), namespace)
    return namespace["price_move_market_prices"]


def event(**overrides):
    row = {
        "reason": "price-move",
        "symbol": "PONS",
        "pct": 26.67,
        "price": 0.0152,
        "basePrice": 0.012,
        "baseTs": 1_792_500_000,
        "windowSeconds": 6 * 3600,
        "source": "币安合约",
        "chain": "",
        "contract": "",
        "at": 1_792_521_600_000,
        "thresholdPct": 20.0,
    }
    row.update(overrides)
    return row


class AlertSenderTest(unittest.TestCase):
    def test_a_surge_is_announced_with_both_prices_and_the_window(self):
        send, sent = load_alert_sender()
        self.assertTrue(send(event()).get("ok"))
        alert = sent[0]
        self.assertIn("PONS", alert["title"])
        self.assertIn("6 小时涨 26.7%", alert["title"])
        # 基准价是**窗口最低点**，文案要标出来（不是「6 小时前那一帧」）。
        self.assertIn("$0.012（窗口最低）→ $0.0152", alert["body"])
        self.assertIn("6 小时 +26.7%", alert["body"])
        self.assertIn("币安合约", alert["body"])

    def test_a_short_window_is_not_reported_as_six_hours(self):
        """窗口没走满时跨度是真实值，弹窗不许硬编 6 小时。"""
        send, sent = load_alert_sender()
        send(event(windowSeconds=900))
        self.assertIn("0.2 小时涨 26.7%", sent[0]["title"])

    def test_it_is_its_own_board_so_the_main_boards_stay_quiet(self):
        send, sent = load_alert_sender()
        send(event())
        alert = sent[0]
        self.assertEqual(alert["board"], "price-move")
        self.assertEqual(alert["sourceType"], "price-move")
        self.assertEqual(alert["kind"], "价格异动")
        self.assertEqual(alert["priority"], "价格异动")
        self.assertEqual(alert["queuePriority"], 82)
        # 排在「三方共振」(90) 之后、「做市商新标的」(78) 之前。
        self.assertLess(alert["queuePriority"], 90)
        self.assertGreater(alert["queuePriority"], 78)

    def test_an_onchain_surge_says_which_chain_and_links_to_gmgn(self):
        send, sent = load_alert_sender()
        send(event(chain="robinhood", contract=TOKEN, source="链上"))
        alert = sent[0]
        self.assertIn("链上", alert["body"])
        self.assertIn("Robinhood", alert["body"])
        self.assertEqual(alert["chain"], "robinhood")
        self.assertEqual(alert["contractAddress"], TOKEN)
        self.assertEqual(alert["url"], f"https://gmgn.ai/robinhood/token/{TOKEN}")

    def test_a_contract_only_surge_links_to_the_binance_futures_page(self):
        """没有 CA 的合约币去 GMGN 是死链，必须落到币安合约页。"""
        send, sent = load_alert_sender()
        send(event())
        self.assertEqual(sent[0]["url"], "https://www.binance.com/zh-CN/futures/PONSUSDT")

    def test_the_default_window_is_six_hours_even_if_the_event_omits_it(self):
        send, sent = load_alert_sender()
        send(event(windowSeconds=0))
        self.assertIn("6 小时涨", sent[0]["title"])

    def test_an_odd_frame_gap_is_reported_as_the_real_window(self):
        """帧是 5 分钟一拍，基准帧只有在 5.9 小时前时也要如实写 5.9。"""
        send, sent = load_alert_sender()
        send(event(windowSeconds=5.9 * 3600))
        self.assertIn("5.9 小时涨", sent[0]["title"])

    def test_below_the_threshold_is_never_announced(self):
        send, sent = load_alert_sender()
        result = send(event(pct=12.0))
        self.assertTrue(result.get("skipped"))
        self.assertEqual(sent, [])

    def test_the_switch_off_suppresses_everything(self):
        send, sent = load_alert_sender(alerts_enabled=False)
        self.assertTrue(send(event()).get("skipped"))
        self.assertEqual(sent, [])

    def test_an_event_without_a_symbol_is_dropped(self):
        send, sent = load_alert_sender()
        self.assertTrue(send(event(symbol="")).get("skipped"))
        self.assertEqual(sent, [])

    def test_the_speech_line_rounds_the_percentage(self):
        send, sent = load_alert_sender()
        send(event(pct=26.67))
        self.assertIn("27 个百分点", sent[0]["speech"])


class AlphaControlTest(unittest.TestCase):
    """控盘 ≥90% + Alpha 合约 + 流通市值 ≤3000 万 → 爆红并标 Alpha（用户 2026-10-21）。

    判据本身由 alpha_mm 的名单给（`control_targets`），这里只验**弹窗怎么表现**：
    ``alertTone="red"``、``alphaPhase`` 标成「Alpha · BSC」、正文把控盘与市值写出来。
    """

    CONTROL = {
        "symbol": "DOOD", "chain": "BSC", "controlPct": 97.36,
        "marketCap": 24_990_468.0, "alphaListed": True,
    }

    def send(self, entry=None, **kwargs):
        row = self.CONTROL if entry is None else entry
        targets = {"DOOD": row} if row else {}
        send, sent = load_alert_sender(control=targets, **kwargs)
        payload = event(symbol="DOOD", pct=41.5, price=0.0028815, basePrice=0.0020)
        send(payload)
        return sent[0] if sent else None

    def test_a_controlled_alpha_contract_goes_red(self):
        alert = self.send()
        self.assertEqual(alert["alertTone"], "red")
        self.assertEqual(alert["alphaPhase"], "Alpha · BSC")
        self.assertTrue(alert["alphaListed"])

    def test_the_popup_says_why_it_is_red(self):
        alert = self.send()
        self.assertIn("项目方控盘 97.4%", alert["body"])
        self.assertIn("$24.99M", alert["body"])
        self.assertEqual(alert["alphaPhase"], "Alpha · BSC")
        self.assertNotIn("Alpha · BSC", alert["body"], "正文里再写一遍就是重复")

    def test_the_priority_label_names_the_control(self):
        alert = self.send()
        self.assertEqual(alert["priority"], "价格异动 · 高度控盘")
        # 皮肤变了，排队优先级不动：红色是「更该看」，不是「更该抢队列」。
        self.assertEqual(alert["queuePriority"], 82)

    def test_the_speech_mentions_the_control_percentage(self):
        alert = self.send()
        self.assertIn("控盘 97 个百分点", alert["speech"])

    def test_a_non_bsc_alpha_contract_still_goes_red(self):
        """Alpha 报红那条只管 BSC；这三条判据对 Solana / ETH 的 Alpha 币同样成立。"""
        alert = self.send(entry={**self.CONTROL, "chain": "solana"})
        self.assertEqual(alert["alertTone"], "red")
        self.assertEqual(alert["alphaPhase"], "Alpha · Solana")

    def test_a_plain_surge_keeps_the_normal_skin(self):
        alert = self.send(entry={})
        self.assertNotIn("alertTone", alert)
        self.assertEqual(alert["priority"], "价格异动")
        self.assertNotIn("控盘", alert["body"])
        self.assertNotIn("alphaPhase", alert)

    def test_a_broken_state_file_never_fakes_a_red_popup(self):
        send, sent = load_alert_sender(control_raises=True)
        result = send(event(symbol="DOOD"))
        self.assertTrue(result.get("ok"), "控盘名单读不到不该让弹窗整条丢掉")
        self.assertEqual(len(sent), 1)
        self.assertNotIn("alertTone", sent[0])

    def test_a_control_entry_without_chain_is_still_labelled_alpha(self):
        alert = self.send(entry={**self.CONTROL, "chain": ""})
        self.assertEqual(alert["alphaPhase"], "Alpha 在列")
        self.assertEqual(alert["alertTone"], "red")

    def test_the_symbol_is_normalised_before_the_lookup(self):
        """``btc`` / ``BTCUSDT`` 都要查到 ``BTC`` 那条，否则一半的异动不会爆红。"""
        send, sent = load_alert_sender(control={"BTC": {**self.CONTROL, "chain": "BSC"}})
        send(event(symbol="btcusdt", pct=30.0))
        self.assertEqual(sent[0]["alertTone"], "red")


class PayloadAlphaTest(unittest.TestCase):
    """列表也要标：控盘达标的爆红，其余 Alpha 标的只给角标。"""

    def test_a_controlled_row_is_flagged_for_the_page(self):
        build, _ = load_payload_builder(
            [{"symbol": "DOOD", "chain": "bsc", "contract": TOKEN}],
            targets={"DOOD": {"controlPct": 97.36, "marketCap": 24_990_468.0, "chain": "BSC"}},
        )
        row = build()["moves"][0]
        self.assertTrue(row["alphaHot"])
        self.assertEqual(row["alphaPhase"], "Alpha · BSC")
        self.assertAlmostEqual(row["alphaControlPct"], 97.36)
        self.assertEqual(row["alphaMarketCapText"], "$24.99M")

    def test_a_plain_alpha_row_gets_the_chip_without_going_red(self):
        build, _ = load_payload_builder(
            [{"symbol": "JCT", "chain": "", "contract": ""}],
            alpha_phase={"JCT": "Alpha · BSC"},
        )
        row = build()["moves"][0]
        self.assertEqual(row["alphaPhase"], "Alpha · BSC")
        self.assertNotIn("alphaHot", row)
        self.assertNotIn("alphaControlPct", row)

    def test_an_ordinary_coin_gets_neither(self):
        build, _ = load_payload_builder([{"symbol": "BTC", "chain": "", "contract": ""}])
        row = build()["moves"][0]
        self.assertNotIn("alphaPhase", row)
        self.assertNotIn("alphaHot", row)

    def test_a_broken_state_file_does_not_break_the_page(self):
        build, _ = load_payload_builder(
            [{"symbol": "DOOD", "chain": "", "contract": ""}], targets_raise=True
        )
        row = build()["moves"][0]
        self.assertNotIn("alphaHot", row)
        self.assertIn("url", row)


class AlphaWiringTests(unittest.TestCase):
    def test_the_price_move_prefix_is_in_the_chain_scope_list(self):
        """不加前缀，合约币既没 chain 也没合约地址 → 被作用域闸挡在 Alpha 身份之外。"""
        self.assertIn('"price-move:",', SERVER_SOURCE)

    def test_the_alpha_phase_has_a_single_implementation(self):
        self.assertIn("def alpha_phase_for_asset(", SERVER_SOURCE)
        self.assertIn("return alpha_phase_for_asset(symbol)", SERVER_SOURCE)

    def test_the_control_lookup_reuses_the_alpha_mm_state(self):
        self.assertIn("return ALPHA_MM.control_targets().get(key) or {}", SERVER_SOURCE)
        self.assertIn("def control_targets(", ROOT.joinpath("alpha_mm_monitor.py").read_text(encoding="utf-8"))


class PayloadLinkTest(unittest.TestCase):
    """「查看」链接在后端拼：GMGN 的 slug 规则只有后端知道。"""

    def test_every_row_gets_a_url_and_a_chain_label(self):
        build, _ = load_payload_builder([
            {"symbol": "PONS", "chain": "robinhood", "contract": TOKEN},
            {"symbol": "BTC", "chain": "", "contract": ""},
            {"symbol": "ODD", "chain": "arc", "contract": "0x" + "cd" * 20},
        ])
        rows = build()["moves"]
        self.assertEqual(rows[0]["url"], f"https://gmgn.ai/robinhood/token/{TOKEN}")
        self.assertEqual(rows[0]["chainLabel"], "Robinhood")
        self.assertEqual(rows[1]["url"], "https://www.binance.com/zh-CN/futures/BTCUSDT")
        self.assertEqual(rows[1]["chainLabel"], "")
        # arc 没有 GMGN slug → 落回币安合约页，不能是空链接。
        self.assertEqual(rows[2]["url"], "https://www.binance.com/zh-CN/futures/ODDUSDT")

    def test_the_page_payload_does_not_mutate_the_cached_snapshot(self):
        build, monitor = load_payload_builder([{"symbol": "PONS", "chain": "", "contract": ""}])
        build()
        self.assertNotIn("url", monitor._payload["moves"][0])

    def test_force_refresh_is_passed_through(self):
        build, monitor = load_payload_builder([])
        build(force_refresh=True)
        self.assertTrue(monitor.last_force_refresh)


class MarketPriceFetchTest(unittest.TestCase):
    """一次 ticker 拉全市场；空结果必须报错，不能伪装成「没有异动」。"""

    def test_all_usdt_pairs_are_kept_with_the_suffix_stripped(self):
        fetch = load_market_prices([
            {"symbol": "BTCUSDT", "price": "70000"},
            {"symbol": "PONSUSDT", "price": "0.0152"},
        ])
        self.assertEqual(fetch(), {"BTC": 70_000.0, "PONS": 0.0152})

    def test_non_usdt_pairs_are_ignored(self):
        fetch = load_market_prices([
            {"symbol": "BTCUSDC", "price": "70000"},
            {"symbol": "ETHBTC", "price": "0.05"},
            {"symbol": "BTCUSDT", "price": "70000"},
        ])
        self.assertEqual(fetch(), {"BTC": 70_000.0})

    def test_stablecoins_and_zero_prices_are_dropped(self):
        fetch = load_market_prices([
            {"symbol": "USDCUSDT", "price": "1"},
            {"symbol": "BTCUSDT", "price": "0"},
            {"symbol": "ETHUSDT", "price": "3000"},
        ])
        self.assertEqual(fetch(), {"ETH": 3000.0})

    def test_an_empty_response_raises_instead_of_looking_like_a_calm_market(self):
        with self.assertRaises(RuntimeError):
            load_market_prices([])()

    def test_a_junk_row_is_skipped_not_fatal(self):
        fetch = load_market_prices([None, "oops", {"symbol": "BTCUSDT", "price": "70000"}])
        self.assertEqual(fetch(), {"BTC": 70_000.0})


class ServerWiringTests(unittest.TestCase):
    def setUp(self):
        self.source = SERVER_SOURCE

    def test_routes_are_registered(self):
        self.assertIn('parsed.path == "/api/price-move"', self.source)
        self.assertIn('route == "/api/price-move" or route.endswith("/api/price-move")', self.source)
        # GET 走快照缓存，POST 改完设置强制重建（否则「点了没反应」）。
        self.assertIn("self.send_json(price_move_payload())", self.source)
        self.assertIn("price_move_payload(force_refresh=True)", self.source)

    def test_manual_refresh_runs_in_the_background(self):
        """一次全市场 ticker 虽然轻，也不能让页面等它。"""
        self.assertIn('name="price-move-manual-cycle"', self.source)
        self.assertIn("target=PRICE_MOVE_MONITOR.run_cycle", self.source)

    def test_only_the_futures_source_is_injected(self):
        self.assertIn("PRICE_MOVE_MONITOR.set_market_provider(price_move_market_prices)", self.source)
        self.assertIn("PRICE_MOVE_MONITOR.set_alert_callback(send_price_move_desktop_alert)", self.source)
        # 用户 2026-10-21：链上标的不用做价格异动（命中的十有八九是 Solana/BSC 的 meme，
        # 20% 对它们太廉价）。别再把价格监控池那条源接回来。
        self.assertNotIn("set_chain_provider", self.source)
        self.assertNotIn("price_move_chain_prices", self.source)

    def test_the_monitor_is_started_and_stopped_with_the_service(self):
        self.assertIn("PRICE_MOVE_MONITOR.start()", self.source)
        self.assertIn("atexit.register(PRICE_MOVE_MONITOR.close)", self.source)
        self.assertIn("PRICE_MOVE_MONITOR.stop()", self.source)

    def test_the_post_route_is_local_or_admin_only(self):
        self.assertIn("仅限本机或管理员配置价格异动监控", self.source)

    def test_the_payload_endpoint_is_in_the_enrichment_whitelist(self):
        """不在白名单里，链接与标链那一段就不会跑（做市商那次踩过）。"""
        index = self.source.index("def send_json")
        window = self.source[index:index + 6000]
        self.assertIn('"/api/price-move"', window)

    def test_the_page_has_a_tab_for_it(self):
        html = ROOT.joinpath("price-watch.html").read_text(encoding="utf-8")
        self.assertIn('data-watch-mode="pricemove"', html)
        js = ROOT.joinpath("price-watch.js").read_text(encoding="utf-8")
        self.assertIn('"pricemove"', js)
        self.assertIn("/api/price-move", js)
        self.assertIn("renderPriceMove", js)

    def test_the_packager_ships_the_module(self):
        package = ROOT.joinpath("package.json").read_text(encoding="utf-8")
        self.assertIn('"price_move_monitor.py"', package)

    def test_the_singleton_lives_next_to_the_other_monitors(self):
        source = ROOT.joinpath("src/app/core/state.py").read_text(encoding="utf-8")
        self.assertIn("PRICE_MOVE_MONITOR = PriceMoveMonitor(", source)
        self.assertIn("PriceMoveStore(PERSIST_CACHE_DIR", source)
        # server 必须从 state 里取，不能自己再造一个（两份状态 = 两个采样线程）。
        self.assertIn("    PRICE_MOVE_MONITOR,", self.source)


if __name__ == "__main__":
    unittest.main()
