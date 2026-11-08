"""RootData 项目热门榜 + 一级融资：解析 / 排序 / 过滤 / 行映射 / 未读账本。

取数走 ``cn.rootdata.com`` 的 SSR 表格（显式代理），这里只测纯逻辑，不碰网络。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import rootdata_projects as rp


PROJECTS = [
    {"name": "Elysium", "symbol": "", "tags": ["#DeFi", "#Layer2"], "ecosystem": "",
     "intro": "High-performance DeFi L2", "growth": 724, "popularity": 281,
     "icon": "https://cdn/ely.png", "url": "https://www.rootdata.com/Projects/detail/Elysium"},
    {"name": "Binance", "symbol": "BNB", "tags": ["#CeFi", "#CEX"], "ecosystem": "",
     "intro": "The world's largest digital asset exchange", "growth": 1666, "popularity": 135},
    {"name": "OKX", "symbol": "OKB", "tags": ["#CeFi", "#CEX"], "ecosystem": "",
     "intro": "Cryptocurrency exchange", "growth": 1400, "popularity": 133},
    {"name": "Pearl", "symbol": "PRL", "tags": ["#Infra", "#Layer1", "#AI"], "ecosystem": "",
     "intro": "A Layer-1 blockchain that mines", "growth": 1119, "popularity": 214},
    {"name": "Tether", "symbol": "USDT", "tags": ["#Stablecoin"], "ecosystem": "",
     "intro": "stable", "growth": 10, "popularity": 99},
    {"name": "Leap", "symbol": "", "tags": ["#Trading Terminal", "#DeFi", "#RWA"], "ecosystem": "Solana",
     "intro": "Solana tokenized assets trading app", "growth": 697, "popularity": 209},
]


class RootdataFilterTests(unittest.TestCase):
    def test_exchanges_are_dropped_by_tag(self):
        self.assertTrue(rp.rootdata_project_excluded(PROJECTS[1]))
        self.assertTrue(rp.rootdata_project_excluded(PROJECTS[2]))

    def test_exchanges_are_dropped_by_name_even_without_tags(self):
        for name, symbol in (("Binance", ""), ("OKX", ""), ("Kraken", "KRAKEN"), ("Tether", "")):
            with self.subTest(name=name):
                self.assertTrue(rp.rootdata_project_excluded({"name": name, "symbol": symbol, "tags": []}))

    def test_normal_projects_survive(self):
        for row in (PROJECTS[0], PROJECTS[3], PROJECTS[5]):
            with self.subTest(name=row["name"]):
                self.assertFalse(rp.rootdata_project_excluded(row))

    def test_ranking_is_popularity_desc_and_renumbered(self):
        kept = rp.ranked_projects([dict(row) for row in PROJECTS])
        self.assertEqual([row["name"] for row in kept], ["Elysium", "Pearl", "Leap"])
        self.assertEqual([row["rank"] for row in kept], [1, 2, 3])

    def test_rows_carry_the_fields_the_board_renders(self):
        rows = rp.project_rows(payload={"projects": [dict(row) for row in PROJECTS]})
        self.assertEqual(rows[0]["symbol"], "Elysium")
        self.assertEqual(rows[0]["turnover"], "热度 281")
        # 涨跌幅按百分比展示（用户 2026-11-07 要求「后面加个百分号」）
        self.assertEqual(rows[0]["change"], "+724%")
        self.assertEqual(rows[0]["heat"], 100)
        self.assertEqual(rows[0]["url"], "https://www.rootdata.com/Projects/detail/Elysium")
        self.assertIn("热度 214", [row["turnover"] for row in rows])
        # 老大哥一个都不许出现在行里
        self.assertFalse({"Binance", "OKX", "Tether"} & {row["symbol"] for row in rows})

    def test_board_shows_only_the_top_ten(self):
        projects = [{"name": f"P{index}", "popularity": 1000 - index} for index in range(30)]
        rows = rp.project_rows(payload={"projects": projects})
        self.assertEqual(len(rows), rp.PROJECT_DISPLAY_LIMIT)
        self.assertEqual([row["rank"] for row in rows], list(range(1, 11)))

    def test_heat_bar_is_normalised_against_the_watched_range(self):
        """只显示 10 行时，热度条仍按前 20 名里的最高热度归一，不会整体变形。"""
        projects = [{"name": f"P{index}", "popularity": 1000 - index} for index in range(30)]
        rows = rp.project_rows(payload={"projects": projects})
        self.assertEqual(rows[0]["heat"], 100)
        self.assertEqual(rows[9]["heat"], 99)

    def test_missing_snapshot_renders_no_rows(self):
        self.assertEqual(rp.project_rows(payload={}), [])

    def test_missing_popularity_does_not_divide_by_zero(self):
        rows = rp.project_rows(payload={"projects": [{"name": "X", "popularity": None}]})
        self.assertEqual(rows[0]["heat"], 0)
        self.assertEqual(rows[0]["turnover"], "")


class RootdataSnapshotTests(unittest.TestCase):
    def test_age_of_a_fresh_snapshot_is_small(self):
        import time
        payload = {"fetchedAtMs": int(time.time() * 1000)}
        self.assertLess(rp.snapshot_age_seconds(payload), 5)

    def test_missing_snapshot_is_infinitely_old(self):
        self.assertEqual(rp.snapshot_age_seconds({}), float("inf"))


PROJECT_ROW_HTML = """
<table><tbody>
<tr><th>项目</th><th>标签</th><th>生态</th><th>介绍</th><th>成长</th><th>热度</th></tr>
<tr>
  <td class="p-2">
    <div class="flex items-center gap-2 py-1 pr-2">
      <a class="shrink-0" href="/projects/detail/Elysium?k=1" target="_blank">
        <span data-slot="avatar">
          <span class="flex size-full items-center justify-center rounded-full bg-muted
                       text-sm text-muted-foreground" data-slot="avatar-fallback">E</span>
        </span>
      </a>
      <div class="flex items-center gap-1.5 overflow-hidden">
        <a class="animate-underline text-sm font-medium" href="/projects/detail/Elysium?k=1">Elysium</a>
      </div>
    </div>
  </td>
  <td><span class="tag">#DeFi</span><span class="tag">#Layer2</span></td>
  <td>Ethereum</td>
  <td>高性能 DeFi L2</td>
  <td>724</td>
  <td>297</td>
</tr>
</tbody></table>
"""

FUNDRAISING_ROW_HTML = """
<table><tbody>
<tr><th>项目</th><th>轮次</th><th>金额</th><th>估值</th><th>日期</th><th>来源</th><th>投资方</th></tr>
<tr>
  <td class="p-2">
    <div class="flex items-center gap-2 py-1">
      <a class="shrink-0" href="/projects/detail/Lombard?k=2" target="_blank">
        <span data-slot="avatar">
          <span class="flex size-full items-center justify-center rounded-full bg-muted
                       text-sm text-muted-foreground" data-slot="avatar-fallback">L</span>
        </span>
      </a>
      <div class="flex items-center gap-1.5 overflow-hidden">
        <a class="animate-underline truncate font-medium" href="/projects/detail/Lombard?k=2">Lombard</a>
        <span class="text-muted-foreground">BARD</span>
      </div>
    </div>
  </td>
  <td>种子轮</td>
  <td>400万美元</td>
  <td>4000万美元</td>
  <td>11-05</td>
  <td><a href="https://example.com/report">CoinDesk</a></td>
  <td>
    <a href="/investors/detail/Ethereal"><span>E</span> Ethereal Ventures <em>*</em></a>
    <a href="/investors/detail/Veris"><span>V</span> Veris Ventures</a>
  </td>
</tr>
</tbody></table>
"""


#: 项目详情页「详情」区块的真实形状（2026-11-08 从 Avon 详情页截取）：
#: h2 详情 + p.line-clamp-3 简介 + 三个「标签 / 生态(n) / 成立时间」行，徽章 data-slot="badge" 在 span 上。
DETAIL_HTML = """
<html><body><div>
  <h2 class="mb-4 text-base font-semibold">详情</h2>
  <div class="mb-4"><div class="relative">
    <p class="line-clamp-3 text-sm leading-6 whitespace-pre-line text-foreground">
      <button aria-label="本内容由 RD AI 生成" type="button"></button>
      Avon 是一个可组合的基于订单簿的借贷框架。
    </p>
  </div></div>
  <div class="flex flex-col gap-3">
    <div class="flex min-w-0 items-baseline gap-2">
      <span class="shrink-0 text-sm text-muted-foreground">标签 :</span>
      <div class="min-w-0 text-sm font-medium">
        <a class="inline-flex" href="/projects?sd=224"><span data-slot="badge">DeFi</span></a>
        <a class="inline-flex" href="/projects?sd=75"><span data-slot="badge">借贷</span></a>
      </div>
    </div>
    <div class="flex min-w-0 items-baseline gap-2">
      <span class="shrink-0 text-sm text-muted-foreground">生态(1) :</span>
      <div class="min-w-0 text-sm font-medium">
        <a class="inline-flex" href="/ecosystemmap/list/336?n=MegaETH"><span data-slot="badge">MegaETH</span></a>
      </div>
    </div>
    <div class="flex min-w-0 items-baseline gap-2">
      <span class="shrink-0 text-sm text-muted-foreground">成立时间 :</span>
      <div class="min-w-0 text-sm font-medium"><span>2024</span></div>
    </div>
  </div>
</div></body></html>
"""


class RootdataParseTests(unittest.TestCase):
    def test_avatar_initial_is_never_mistaken_for_a_ticker(self):
        """单元格最左边的圆形头像是 ``span.text-muted-foreground``，里面只有项目名首字母。"""
        rows = rp.parse_projects(PROJECT_ROW_HTML)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Elysium")
        self.assertEqual(rows[0]["symbol"], "")
        self.assertEqual(rows[0]["popularity"], 297)
        self.assertEqual(rows[0]["growth"], 724)

    def test_project_detail_extracts_intro_tags_ecosystem_and_founded(self):
        detail = rp.parse_project_detail(DETAIL_HTML)
        self.assertEqual(detail["intro"], "Avon 是一个可组合的基于订单簿的借贷框架。")
        self.assertEqual(detail["tags"], ["DeFi", "借贷"])
        self.assertEqual(detail["ecosystem"], ["MegaETH"])
        self.assertEqual(detail["founded"], "2024")

    def test_project_detail_without_blocks_returns_empty_fields(self):
        detail = rp.parse_project_detail("<html><body>nothing here</body></html>")
        self.assertEqual(detail["intro"], "")
        self.assertEqual(detail["tags"], [])
        self.assertEqual(detail["ecosystem"], [])
        self.assertEqual(detail["founded"], "")

    def test_fundraising_row_maps_every_column(self):
        events = rp.parse_fundraising(FUNDRAISING_ROW_HTML)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["name"], "Lombard")
        self.assertEqual(event["symbol"], "BARD")
        self.assertEqual(event["round"], "种子轮")
        self.assertEqual(event["amount"], "400万美元")
        self.assertEqual(event["valuation"], "4000万美元")
        self.assertEqual(event["investors"], ["Ethereal Ventures", "Veris Ventures"])
        self.assertGreater(event["date"], 0)
        self.assertEqual(event["url"], "https://cn.rootdata.com/projects/detail/Lombard?k=2")

    def test_fundraising_rows_speak_the_listing_section_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "fundraising.json"
            with mock.patch.object(rp, "FUNDRAISING_SNAPSHOT", snapshot):
                rp.write_fundraising_snapshot(rp.parse_fundraising(FUNDRAISING_ROW_HTML), [])
                rows = rp.fundraising_rows(limit=5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["group"], "ipo")
        # 对外叫「链上融资」，不再出现 RootData 字样（用户 2026-11-07 改名）；
        # 但内部标记 rootdata 必须保留，页签过滤与去重账本都靠它。
        self.assertEqual(row["source"], "链上融资")
        self.assertEqual(row["sourceLabel"], "融资")
        self.assertTrue(row["rootdata"])
        self.assertEqual(row["title"], "Lombard 种子轮 $BARD")
        self.assertEqual(row["metric"], "400万美元")
        self.assertIn("投资方 Ethereal Ventures", row["note"])

    def test_real_rsc_shape_logo_img_with_multilingual_name(self):
        """生产页面的真实形状：``logoImg`` + 多语言 ``name`` 对象（2026-11-07 实测）。"""
        html = (
            '<script>self.__next_f.push([1,"'
            '{\\"logoImg\\":\\"https://public.rootdata.com/images/b89/1790126681568.png\\",'
            '\\"name\\":{\\"tw_value\\":\\"Elysium\\",\\"en_value\\":\\"Elysium\\",\\"cn_value\\":\\"Elysium\\"}}'
            '"]);</script>'
        )
        self.assertEqual(
            rp.icons_from_payload(html, ["Elysium"]),
            {"elysium": "https://public.rootdata.com/images/b89/1790126681568.png"},
        )

    def test_the_name_after_a_logo_wins_over_the_one_before_it(self):
        """每条记录是 logoImg→name；不能把上一条记录的项目名配给这张图。"""
        html = (
            '{"logoImg":"https://public.rootdata.com/images/a/1.png","name":{"en_value":"Alpha"}}'
            + "x" * 40
            + '{"logoImg":"https://public.rootdata.com/images/a/2.png","name":{"en_value":"Beta"}}'
        )
        icons = rp.icons_from_payload(html, ["Alpha", "Beta"])
        self.assertEqual(icons["alpha"], "https://public.rootdata.com/images/a/1.png")
        self.assertEqual(icons["beta"], "https://public.rootdata.com/images/a/2.png")


class RootdataFundingLedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.snapshot = Path(self._tmp.name) / "fundraising.json"
        self.patcher = mock.patch.object(rp, "FUNDRAISING_SNAPSHOT", self.snapshot)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        rp.set_fundraising_alert_callback(None)
        self.addCleanup(rp.set_fundraising_alert_callback, None)

    def test_events_are_marked_read_when_somebody_listened(self):
        events = rp.parse_fundraising(FUNDRAISING_ROW_HTML)
        rp.write_fundraising_snapshot(events, [])
        seen = rp.read_fundraising_snapshot()["seenKeys"]
        self.assertEqual(len(seen), 1)

    def test_skipped_keys_stay_unread_for_the_next_round(self):
        """没回调（手动 --pull / worker 进程）时绝不能记账本，否则这批融资被静默吞掉。"""
        events = rp.parse_fundraising(FUNDRAISING_ROW_HTML)
        key = rp.fundraising_event_key(events[0])
        rp.write_fundraising_snapshot(events, [], skip_keys=[key])
        self.assertEqual(rp.read_fundraising_snapshot()["seenKeys"], [])

    def test_emit_reports_whether_anyone_received_it(self):
        event = rp.parse_fundraising(FUNDRAISING_ROW_HTML)[0]
        self.assertFalse(rp._emit_fundraising_alerts([event]))  # noqa: SLF001
        received = []
        rp.set_fundraising_alert_callback(received.append)
        self.assertTrue(rp._emit_fundraising_alerts([event]))  # noqa: SLF001
        self.assertEqual(len(received), 1)


class RootdataFundingDetailTests(unittest.TestCase):
    """融资事件补「详情/标签/生态/成立时间」：增量、串行、失败不拖垮刷新。"""

    def _event(self, name="Avon", round_name="并购", date=1793937600000):
        return {
            "name": name, "symbol": "", "round": round_name, "amount": "", "valuation": "",
            "date": date, "investors": ["Aave"], "investorExtra": 0,
            "url": f"https://cn.rootdata.com/projects/detail/{name}?k=1", "newsUrl": "",
        }

    def test_carries_over_details_already_fetched_last_round(self):
        """上一轮抓到的详情不能丢：按 key 并回本轮，别对同一项目反复抓详情页。"""
        previous = {"events": [{
            **self._event(), "intro": "借贷框架", "tags": ["DeFi"], "ecosystem": ["MegaETH"], "founded": "2024",
        }]}
        events = [self._event()]
        with mock.patch.object(rp, "FUNDRAISING_DETAIL_BATCH", 0):
            result = rp._enrich_fundraising_details(events, previous)  # noqa: SLF001
        self.assertEqual(result[0]["intro"], "借贷框架")
        self.assertEqual(result[0]["ecosystem"], ["MegaETH"])
        self.assertEqual(result[0]["founded"], "2024")

    def test_fetches_at_most_the_batch_size_per_round(self):
        """每轮最多补 batch 个，串行；抓到的详情写回事件。"""
        events = [self._event(name=f"P{i}") for i in range(5)]
        with mock.patch.object(rp, "FUNDRAISING_DETAIL_BATCH", 2), \
             mock.patch.object(rp, "_working_proxy", return_value="http://127.0.0.1:7890"), \
             mock.patch.object(rp, "_fetch_detail", return_value=DETAIL_HTML):
            result = rp._enrich_fundraising_details(events, {})  # noqa: SLF001
        enriched = [event for event in result if event.get("intro")]
        self.assertEqual(len(enriched), 2)  # 只补了 2 个
        self.assertEqual(enriched[0]["tags"], ["DeFi", "借贷"])

    def test_stops_on_waf_block(self):
        """被 WAF 拦住立即停手，别再敲下一个门。"""
        events = [self._event(name=f"P{i}") for i in range(3)]
        with mock.patch.object(rp, "FUNDRAISING_DETAIL_BATCH", 3), \
             mock.patch.object(rp, "_working_proxy", return_value="http://127.0.0.1:7890"), \
             mock.patch.object(rp, "_fetch_detail", side_effect=rp.RootdataBlocked("captcha")):
            result = rp._enrich_fundraising_details(events, {})  # noqa: SLF001
        self.assertTrue(all(not event.get("intro") for event in result))

    def test_rows_render_details_into_note_and_tags(self):
        """行映射：简介+成立时间进 note（不受前端 tags 上限影响），标签/生态进 tags。"""
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "fundraising.json"
            with mock.patch.object(rp, "FUNDRAISING_SNAPSHOT", snapshot):
                event = {
                    **self._event(),
                    "intro": "Avon 是一个可组合的基于订单簿的借贷框架。",
                    "tags": ["DeFi", "借贷"], "ecosystem": ["MegaETH"], "founded": "2024",
                }
                rp.write_fundraising_snapshot([event], [])
                rows = rp.fundraising_rows(limit=5)
        row = rows[0]
        self.assertIn("Avon 是一个可组合的基于订单簿的借贷框架", row["note"])
        self.assertIn("成立于 2024", row["note"])
        self.assertIn("DeFi", row["tags"])
        self.assertIn("生态 MegaETH", row["tags"])


class RootdataProjectWatchTests(unittest.TestCase):
    """「只展示前 10，但弹窗监控前 20，有新进就提醒」。"""

    @staticmethod
    def _ranked(count, start=0):
        rows = [{"name": f"P{index}", "popularity": 1000 - index, "tags": []}
                for index in range(start, start + count)]
        return rp.ranked_projects(rows)

    def test_first_run_only_builds_the_baseline(self):
        newcomers, first_baseline, watched = rp.detect_newcomers(self._ranked(30), {})
        self.assertTrue(first_baseline)
        self.assertEqual(newcomers, [])
        self.assertEqual(len(watched), rp.PROJECT_WATCH_LIMIT)

    def test_snapshot_without_a_watched_list_also_rebuilds_the_baseline(self):
        """老快照没有 watched 字段（升级后的第一轮）不能一次弹 20 条。"""
        newcomers, first_baseline, _ = rp.detect_newcomers(
            self._ranked(30), {"projects": [], "fetchedAtMs": 1}
        )
        self.assertTrue(first_baseline)
        self.assertEqual(newcomers, [])

    def test_project_entering_the_top_twenty_is_reported_even_below_the_display_cut(self):
        baseline = [f"p{index}" for index in range(20)]
        ranked = self._ranked(30, start=15)  # P15..P44，前 20 名里含 P16..P34
        newcomers, first_baseline, _ = rp.detect_newcomers(ranked, {"watched": baseline})
        self.assertFalse(first_baseline)
        names = {row["name"] for row in newcomers}
        self.assertEqual(names, {f"P{index}" for index in range(20, 35)})
        # 第 11~20 名也算「新进」——监控范围是 20，不是展示的 10
        self.assertTrue(any(row["rank"] > rp.PROJECT_DISPLAY_LIMIT for row in newcomers))

    def test_unchanged_top_twenty_reports_nothing(self):
        watched = [f"p{index}" for index in range(20)]
        newcomers, _, _ = rp.detect_newcomers(self._ranked(20), {"watched": watched})
        self.assertEqual(newcomers, [])

    def test_snapshot_keeps_the_baseline_for_the_next_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "projects.json"
            with mock.patch.object(rp, "PROJECTS_SNAPSHOT", snapshot):
                rp.write_snapshot(self._ranked(5), watched=["alpha", "beta"])
                self.assertEqual(rp.read_snapshot()["watched"], ["alpha", "beta"])


class RootdataIconTests(unittest.TestCase):
    """项目 logo：单元格里有图就用图，没有就去内嵌 JSON 里按名字配，配不上就明说没有。"""

    ROW_WITH_IMG = """
    <table><tbody>
    <tr><th>项目</th><th>标签</th><th>生态</th><th>介绍</th><th>成长</th><th>热度</th></tr>
    <tr>
      <td><div>
        <a href="/projects/detail/Elysium?k=1">
          <img src="https://cdn.rootdata.com/project/elysium.png" alt="Elysium">
        </a>
        <a class="animate-underline" href="/projects/detail/Elysium?k=1">Elysium</a>
      </div></td>
      <td><a>#DeFi</a></td><td>Ethereum</td><td>高性能 DeFi L2</td><td>724</td><td>297</td>
    </tr>
    </tbody></table>
    """

    def test_image_inside_the_cell_wins(self):
        rows = rp.parse_projects(self.ROW_WITH_IMG)
        self.assertEqual(rows[0]["icon"], "https://cdn.rootdata.com/project/elysium.png")

    def test_payload_logo_is_paired_with_the_closest_known_name(self):
        html = (
            '<script>self.__next_f.push([1,"'
            '{\\"id\\":26023,\\"name\\":\\"Elysium\\",\\"logo\\":\\"https://cdn.rootdata.com/i/26023.png\\"}'
            '"]);</script>'
            + PROJECT_ROW_HTML
        )
        icons = rp.icons_from_payload(html, ["Elysium", "Pearl"])
        self.assertEqual(icons, {"elysium": "https://cdn.rootdata.com/i/26023.png"})

    def test_unknown_names_are_never_paired(self):
        """配不上的宁可没有——给别人家安 logo 比没有 logo 更糟。"""
        html = '{"name":"Someone Else","logo":"https://cdn.rootdata.com/i/999.png"}'
        self.assertEqual(rp.icons_from_payload(html, ["Elysium"]), {})

    def test_rows_tell_the_frontend_when_there_is_no_icon(self):
        rows = rp.project_rows(payload={"projects": [{"name": "Elysium", "popularity": 297}]})
        self.assertEqual(rows[0]["icon"], "")
        self.assertEqual(rows[0]["iconFallback"], "none")

        with_icon = rp.project_rows(payload={"projects": [
            {"name": "Elysium", "popularity": 297, "icon": "https://cdn.rootdata.com/i/1.png"}
        ]})
        self.assertTrue(with_icon[0]["icon"].startswith("https://cdn.rootdata.com/i/1"))
        self.assertEqual(with_icon[0]["iconFallback"], "")

    def test_non_image_urls_are_rejected(self):
        self.assertEqual(rp._image_url("https://cdn.rootdata.com/project/1"), "")  # noqa: SLF001
        self.assertEqual(rp._image_url("/relative/path.png"), "")
        self.assertTrue(rp._image_url("//cdn.rootdata.com/a.PNG").startswith("https://"))  # noqa: SLF001

    def test_icon_is_served_as_a_128_thumbnail_with_the_original_as_backup(self):
        """原图 ~235KB，缩略图 ~4KB；CDN 不认后缀时前端用 iconCandidates 退回原图。"""
        original = "https://public.rootdata.com/images/b89/1790126681568.png"
        rows = rp.project_rows(payload={"projects": [
            {"name": "Elysium", "popularity": 297, "icon": original}
        ]})
        self.assertEqual(rows[0]["icon"], "https://public.rootdata.com/images/b89/1790126681568_128x128.webp")
        self.assertEqual(rows[0]["iconCandidates"], [original])
        self.assertEqual(rows[0]["iconFallback"], "")

    def test_non_cdn_urls_keep_the_original(self):
        rows = rp.project_rows(payload={"projects": [
            {"name": "X", "popularity": 1, "icon": "https://example.com/logo?size=2"}
        ]})
        self.assertEqual(rows[0]["icon"], "https://example.com/logo?size=2")
        self.assertEqual(rows[0]["iconCandidates"], [])


if __name__ == "__main__":
    unittest.main()
