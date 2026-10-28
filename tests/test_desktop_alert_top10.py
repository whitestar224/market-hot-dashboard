"""服务端：弹窗里的「链上 Top10 持仓率」（Arkham + GMGN 两个口径，2026-10-21）。

用户的三条要求都要钉死：

* 「如果超过了 70%，也在弹窗里提醒」→ 任一源 ≥70% 追加「筹码高度集中」，
  只有一边超线时点名是谁（否则旁边那个更低的数会让这句话看着自相矛盾）；
* 「并且把具体的 top10 持仓率显示出来」→ 数字恒显示，拿不到就写「未收录 / 暂不可用」，
  **绝不许写 0%**（那是「筹码分散」，跟「查不到」正好相反）；
* 「把两个版本的都做一个统计」→ Arkham 和 GMGN 两个数并排标源。

另外四件事容易在改动中悄悄丢掉，也一并钉住：

* 只有 AIcoin 热门榜 / 涨幅榜 / 价格异动这三类弹窗算 Top10，快讯和推文不算；
* 只动正文，**不动 queuePriority、不改皮肤**（跟 Alpha 皮肤同一纪律）；
* Alpha 那句「千万别碰，买现货」在正文拼接时不能被裁掉；
* 榜单行只有 symbol 时，反查要把 symbol + 弹窗价 +（池子里的）合约一起带下去。
"""

import unittest
from pathlib import Path
from unittest.mock import patch

import server


def normalized(**payload):
    return server.normalize_desktop_alert(payload)


class ScopeTest(unittest.TestCase):
    """哪条弹窗该算 Top10。"""

    def test_the_three_sources_named_by_the_user_are_in_scope(self):
        for key in (
            "rank-monitor:hot|aicoin:crypto:dood|1792080000",
            "rank-monitor:aicoin|hot:crypto:DNA|1792080000000",
            "gainers-leader:okx-dex-gainers|DOOD",
            "rank-monitor:gainers|okx-dex-gainers:DOOD|new",
            "price-move:DOOD:1792080000000",
        ):
            with self.subTest(key=key):
                self.assertTrue(server.top10_alert_in_scope({"key": key}))

    def test_stock_and_other_boards_stay_out(self):
        for key in (
            "rank-monitor:hot:futu-hk:HK:00992:new:10",
            "rank-monitor:turnover|binance:crypto:xrp|new",
        ):
            with self.subTest(key=key):
                self.assertFalse(server.top10_alert_in_scope({"key": key}))

    def test_news_and_tweets_never_get_a_concentration_line(self):
        for key in (
            "flash:1792080000:abc",
            "x-kol:1792080000:abc",
            "newsflash:1792080000",
            "price-watch:structure:DOOD:1792080000",
            "smart-money-buy:abc",
        ):
            with self.subTest(key=key):
                self.assertFalse(server.top10_alert_in_scope({"key": key}))

    def test_a_missing_key_is_out_of_scope(self):
        self.assertFalse(server.top10_alert_in_scope({}))


class AnnotationTest(unittest.TestCase):
    def entry(self, arkham=None, gmgn=None, pricing_id="doodles"):
        def source(pct):
            if pct is None:
                return {"status": "empty", "pct": None, "count": 0, "reason": "无数据"}
            return {"status": "ok", "pct": pct, "count": 10, "reason": ""}

        sources = {"arkham": source(arkham), "gmgn": source(gmgn)}
        statuses = [sources[key]["status"] for key in ("arkham", "gmgn")]
        return {
            "status": "ok" if "ok" in statuses else "empty",
            "pct": arkham,
            "count": 10,
            "sources": sources,
            "chain": "bsc",
            "contract": "0x" + "aa" * 20,
            "pricingId": pricing_id,
        }

    def run_annotation(self, payload, entry):
        # 顺序与 launch_desktop_alert 一致：先链标注（Alpha 皮肤在里面补风险提示），
        # 再挂 Top10 —— 这样才测得到「拼正文时不许裁掉 Alpha 那句」。
        item = server.annotate_desktop_alert_chain(normalized(**payload))
        with patch.object(server.TOP10_HOLDERS, "lookup", return_value=entry) as lookup:
            result = server.annotate_desktop_alert_top10(item)
        return result, lookup

    def test_both_sources_are_written_into_the_body(self):
        item, _lookup = self.run_annotation(
            {
                "key": "price-move:DOOD:1792080000000",
                "symbol": "DOOD",
                "title": "价格异动：DOOD 3 小时涨 28.0%",
                "body": "$0.0120（窗口最低）→ $0.0154 · 3 小时 +28.0% · 链上",
                "contractAddress": "0x" + "aa" * 20,
                "chain": "bsc",
            },
            self.entry(arkham=63.4, gmgn=58.1),
        )
        self.assertEqual(item["top10Status"], "ok")
        self.assertAlmostEqual(item["top10ArkhamPct"], 63.4)
        self.assertAlmostEqual(item["top10GmgnPct"], 58.1)
        self.assertIn("Arkham 63.4%", item["body"])
        self.assertIn("GMGN 58.1%", item["body"])
        self.assertFalse(item["top10High"])
        self.assertNotIn("筹码高度集中", item["body"])

    def test_only_the_available_source_shows_when_the_other_fails(self):
        item, _lookup = self.run_annotation(
            {
                "key": "rank-monitor:hot|aicoin:crypto:jct|1792080000000",
                "symbol": "JCT",
                "body": "排名 #3 · $0.0046",
                "contractAddress": "0x" + "bb" * 20,
                "chain": "bsc",
            },
            self.entry(arkham=None, gmgn=54.2),
        )
        self.assertIn("Top10 持仓 GMGN 54.2%", item["body"])
        self.assertNotIn("Arkham", item["body"])

    def test_seventy_percent_and_above_raises_the_risk_note(self):
        item, _lookup = self.run_annotation(
            {
                "key": "rank-monitor:hot|aicoin:crypto:jct|1792080000000",
                "symbol": "JCT",
                "title": "AIcoin热门榜新进：JCT",
                "body": "排名 #3 · $0.0123 · +12.1%",
                "contractAddress": "0x" + "bb" * 20,
                "chain": "bsc",
            },
            self.entry(arkham=82.6, gmgn=79.1),
        )
        self.assertTrue(item["top10High"])
        self.assertIn("Arkham 82.6%", item["body"])
        self.assertIn("筹码高度集中", item["body"])

    def test_only_one_source_above_the_line_names_it(self):
        item, _lookup = self.run_annotation(
            {
                "key": "price-move:JCT:1792080000000",
                "symbol": "JCT",
                "body": "x",
                "contractAddress": "0x" + "aa" * 20,
                "chain": "bsc",
            },
            self.entry(arkham=11.2, gmgn=78.4),
        )
        self.assertTrue(item["top10High"])
        self.assertIn("按 GMGN", item["body"])

    def test_the_popup_priority_is_never_touched(self):
        """筹码集中是「更该看」，不是「更该抢队列」—— 和 Alpha 皮肤同一纪律。"""
        item, _lookup = self.run_annotation(
            {
                "key": "price-move:DOOD:1792080000000",
                "symbol": "DOOD",
                "body": "x",
                "contractAddress": "0x" + "aa" * 20,
                "chain": "bsc",
                "queuePriority": 82,
                "alertTone": "normal",
            },
            self.entry(arkham=95.0, gmgn=96.0),
        )
        self.assertEqual(item["queuePriority"], 82)
        self.assertEqual(item["alertTone"], "normal")

    def test_a_failure_is_written_as_unavailable_never_as_zero(self):
        for status, word in (("error", "暂不可用"), ("empty", "未收录")):
            with self.subTest(status=status):
                item, _lookup = self.run_annotation(
                    {
                        "key": "price-move:DOOD:1792080000000",
                        "symbol": "DOOD",
                        "body": "x",
                        "contractAddress": "0x" + "aa" * 20,
                        "chain": "bsc",
                    },
                    {
                        "status": status,
                        "pct": None,
                        "count": 0,
                        "sources": {
                            "arkham": {"status": status, "pct": None, "count": 0},
                            "gmgn": {"status": status, "pct": None, "count": 0},
                        },
                        "pricingId": "",
                    },
                )
                self.assertIn(word, item["body"])
                self.assertNotIn("0.0%", item["body"])

    def test_an_unresolved_token_gets_no_line_at_all(self):
        item, _lookup = self.run_annotation(
            {"key": "price-move:NOPE:1792080000000", "symbol": "NOPE", "body": "x"},
            {
                "status": "unresolved", "pct": None, "count": 0,
                "sources": {
                    "arkham": {"status": "unresolved", "pct": None, "count": 0},
                    "gmgn": {"status": "unresolved", "pct": None, "count": 0},
                },
            },
        )
        self.assertEqual(item["body"], "x")
        self.assertEqual(item.get("top10Status", ""), "")

    def test_the_arkham_link_is_kept_when_the_pricing_id_is_known(self):
        item, _lookup = self.run_annotation(
            {
                "key": "rank-monitor:hot|aicoin:crypto:jct|1792080000000",
                "symbol": "JCT",
                "body": "x",
                "contractAddress": "0x" + "bb" * 20,
                "chain": "bsc",
            },
            self.entry(arkham=60.0, gmgn=55.0, pricing_id="janction"),
        )
        self.assertEqual(item["top10Url"], "https://arkm.com/explorer/token/janction")

    def test_the_symbol_and_price_reach_the_lookup(self):
        """榜单行只有 symbol，反查要靠 symbol + 弹窗价两样都传下去。"""
        item = normalized(
            **{
                "key": "rank-monitor:hot|aicoin:crypto:zec|1792080000000",
                "symbol": "ZEC",
                "title": "AIcoin热门榜新进：ZEC",
                "body": "排名 #1 · $1,282.54 · +11.88%",
                "priceValue": 1282.54,
            }
        )
        with patch.object(server.TOP10_HOLDERS, "lookup",
                          return_value=self.entry(60.0, 55.0)) as lookup:
            server.annotate_desktop_alert_top10(item)
        _args, kwargs = lookup.call_args
        self.assertEqual(kwargs["symbol"], "ZEC")
        self.assertAlmostEqual(kwargs["price"], 1282.54)

    def test_no_pool_lookup_is_done_on_the_alert_path(self):
        """弹窗路径不打库：榜单行只有 symbol 时靠 Arkham 主部署，不按监控池挑链。"""
        item = normalized(
            **{
                "key": "rank-monitor:hot|aicoin:crypto:jct|1792080000000",
                "symbol": "JCT",
                "title": "AIcoin热门榜新进：JCT",
                "body": "排名 #10 · $0.004628",
                "priceValue": 0.004628,
            }
        )
        with patch.object(server.TOP10_HOLDERS, "lookup", return_value=self.entry(60.0, 55.0)) as lookup:
            server.annotate_desktop_alert_top10(item)
        self.assertNotIn("prefer_contract", lookup.call_args.kwargs)

    def test_the_alpha_risk_note_survives_the_append(self):
        """正文拼接只能裁中间那截，Alpha 那句「买现货」必须留在最尾巴。"""
        body = "排名 #1 · $0.0123 · +12.1% · AIcoin热门榜 · 这是很长一段用来占位的中文正文内容"
        item, _lookup = self.run_annotation(
            {
                "key": "rank-monitor:hot|aicoin:crypto:jct|1792080000000",
                "symbol": "JCT",
                "body": body,
                "contractAddress": "0x" + "bb" * 20,
                "chain": "bsc",
                "alphaPhase": "Alpha · BSC",
            },
            self.entry(arkham=88.0, gmgn=85.0),
        )
        self.assertIn("Arkham 88.0%", item["body"])
        self.assertTrue(item["body"].endswith(server.ALPHA_NEW_RISK_NOTE))
        self.assertEqual(item["alertTone"], "red")


class BodyNoteTest(unittest.TestCase):
    def test_a_long_body_loses_its_own_tail_not_the_note(self):
        body = "甲" * 190
        result = server.append_alert_body_note(body, "Top10 持仓 Arkham 71.0%", limit=200)
        self.assertLessEqual(len(result), 200)
        self.assertIn("Top10 持仓 Arkham 71.0%", result)

    def test_the_alpha_note_stays_at_the_very_end(self):
        body = f"{'甲' * 150} / {server.ALPHA_NEW_RISK_NOTE}"
        result = server.append_alert_body_note(body, "Top10 持仓 Arkham 71.0%", limit=200)
        self.assertTrue(result.endswith(server.ALPHA_NEW_RISK_NOTE))
        self.assertIn("Top10 持仓 Arkham 71.0%", result)

    def test_an_empty_note_leaves_the_body_alone(self):
        self.assertEqual(server.append_alert_body_note("abc", ""), "abc")


class WiringTest(unittest.TestCase):
    """挂点存在性 —— 这一处被删掉，整条链路就静默失效了。"""

    def setUp(self):
        self.source = (
            Path(__file__).resolve().parent.parent / "server.py"
        ).read_text(encoding="utf-8")

    def test_launch_desktop_alert_annotates_top10(self):
        start = self.source.index("def launch_desktop_alert(")
        end = self.source.index("def send_smart_money_buy_desktop_alert(", start)
        self.assertIn("annotate_desktop_alert_top10(", self.source[start:end])

    def _launch_body(self):
        start = self.source.index("def launch_desktop_alert(")
        end = self.source.index("def send_smart_money_buy_desktop_alert(", start)
        return self.source[start:end]

    def test_the_lookup_runs_after_the_filters_and_the_dedupe(self):
        """被静默 / 被去重掉的弹窗不该先花几秒去查 Top10。"""
        body = self._launch_body()
        at = body.index("annotate_desktop_alert_top10(")
        self.assertLess(body.index("if desktop_alert_source_is_muted(normalized):"), at)
        self.assertLess(body.index("muted_board = price_watch_alert_board_muted(normalized)"), at)
        self.assertLess(body.index("if any(key in DESKTOP_ALERT_SEEN for key in keys):"), at)
        self.assertLess(at, body.index("priority = desktop_alert_queue_priority(normalized)"),
                        "查完要赶在入库 / 渲染之前写进正文")

    def test_the_lookup_never_holds_the_delivery_lock(self):
        """它要等 Arkham + GMGN 好几秒，绝不能带着弹窗锁去等。"""
        body = self._launch_body()
        line = next(
            row for row in body.splitlines() if "annotate_desktop_alert_top10(" in row
        )
        self.assertTrue(line.startswith("    "), line)
        self.assertFalse(line.startswith("        "), "缩进进去了，说明被写进了某个 with 块")

    def test_the_monitor_enables_it_at_startup(self):
        self.assertIn("start_top10_holders()", self.source)

    def test_the_gainers_leader_event_carries_the_onchain_identity(self):
        self.assertIn('"contractAddress": leader.get("contractAddress") or ""', self.source)
        self.assertIn('"priceValue": price_watch_numeric(leader.get("price"))', self.source)

    def test_the_price_move_event_carries_its_numeric_price(self):
        start = self.source.index("def send_price_move_desktop_alert(")
        end = self.source.index("def price_move_payload(", start)
        self.assertIn('"priceValue": price', self.source[start:end])


class NormalizeTest(unittest.TestCase):
    def test_the_numeric_price_survives_normalization(self):
        item = normalized(**{"key": "price-move:DOOD:1", "priceValue": 0.0154})
        self.assertAlmostEqual(item["priceValue"], 0.0154)

    def test_a_missing_price_is_zero_not_an_error(self):
        self.assertEqual(normalized(**{"key": "price-move:DOOD:1"})["priceValue"], 0)


if __name__ == "__main__":
    unittest.main()
