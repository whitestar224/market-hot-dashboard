"""icodrops_source：解析 ICODrops ACTIVE 表格 + 空投/销售归类 + 失败降级。

铁律：抓取失败绝不能伪装成「0 条空投」——必须带 stale/error，让前端说明原因。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import icodrops_source as ic


def _row(name, ticker, round_text, raised="", preval="", date="", href="/demo/", img=None):
    """按上游真实 class 结构造一行（.Tbl-Row 里嵌套投资方 <li>，故意加一条验证不被截断）。

    ``img`` 传 <img> 的原始属性串，用来覆盖懒加载图标的几种形态；默认给一张正常图。
    """
    ticker_html = f'<p class="Cll-Project__ticker">{ticker}</p>' if ticker else ""
    if img is None:
        img = 'data-src="https://icodrops.com/media/cache/aa/bb/demo.webp"'
    return f"""
    <li class="Tbl-Row Tbl-Row--usual" tabindex="0">
      <div class="Tbl-Row__item Tbl-Row__item--avt">
        <a class="Cll-Avt" href="{href}" tabindex="-1"><img class="Avt Avt--28 Cll-Avt__img" {img} alt="{name}" /></a>
      </div>
      <div class="Tbl-Row__item Tbl-Row__item--project">
        <div class="Cll-Project">
          <a class="Cll-Project__link" href="{href}" tabindex="-1">
            <p class="Cll-Project__name ">{name}</p>
            {ticker_html}
          </a>
        </div>
      </div>
      <div class="Tbl-Row__item Tbl-Row__item--round">
        <p class="Cll-Value Cll-Value--ellipsis">{round_text}</p>
      </div>
      <div class="Tbl-Row__item Tbl-Row__item--raised">
        <p class="Cll-Value Cll-Value--fw-500">{raised}</p>
      </div>
      <div class="Tbl-Row__item Tbl-Row__item--pre-valuation">
        <p class="Cll-Value Cll-Value--fw-500">{preval}</p>
      </div>
      <div class="Tbl-Row__item Tbl-Row__item--investors">
        <ul class="Stacked-Images">
          <li class="Stacked-Images__item"><img alt="inv1" /></li>
          <li class="Stacked-Images__item"><img alt="inv2" /></li>
        </ul>
      </div>
      <div class="Tbl-Row__item Tbl-Row__item--date">
        <time class="Cll-Value">{date}</time>
      </div>
    </li>
    """


ACTIVE_HTML = f"""<html><body>
<table>
{_row("Kalshi", "", "Possible Retrodrop", "$4.02 B", "$40 B", "from Q1, 2024", "/kalshi/")}
{_row("Variational", "VAR", "Genesis Airdrop", "$61.8 M", "—", "Active", "/variational/")}
{_row("Solflare", "", "Incentivized Activities", "—", "—", "Active", "/solflare/")}
{_row("Tempo", "", "Testnet", "$500 M", "$5 B", "Active", "/tempo/")}
{_row("Recall", "", "Point System Activity", "—", "—", "Active", "/recall/")}
{_row("EarnBIT", "EBT", "IEO", "—", "$10 M", "23d left", "/earnbit/")}
{_row("CLIX", "CLIX", "IDO on KingdomStarter", "—", "$10 M", "from Jul 24, 2026", "/clix/")}
{_row("Slop Cash", "SLOP", "ICM on Umia", "—", "—", "38h left", "/slop-cash/")}
</table>
</body></html>
"""


class ClassifyKindTests(unittest.TestCase):
    def test_airdrop_family(self):
        for text in (
            "Airdrop",
            "Genesis Airdrop",
            "Possible Retrodrop",
            "Incentivized Activities",
            "Early Incentives",
            "Testnet",
            "Point System Activity",
            "Points Farming",
        ):
            with self.subTest(text=text):
                self.assertEqual(ic.classify_kind(text), "airdrop")

    def test_sale_family(self):
        for text in (
            "IEO",
            "IDO on Kommunitas",
            "IKO on Kommunitas",
            "ICM on Umia",
            "Public Sale",
            "Presale",
            "Pre-Sale Phase 3",
        ):
            with self.subTest(text=text):
                self.assertEqual(ic.classify_kind(text), "sale")

    def test_unknown(self):
        self.assertEqual(ic.classify_kind(""), "unknown")
        self.assertEqual(ic.classify_kind("Something Else"), "unknown")


class ParseActiveHtmlTests(unittest.TestCase):
    def test_parses_all_fields(self):
        items = ic.parse_active_html(ACTIVE_HTML)
        self.assertEqual(len(items), 8)
        first = items[0]
        self.assertEqual(first["name"], "Kalshi")
        self.assertEqual(first["round"], "Possible Retrodrop")
        self.assertEqual(first["kind"], "airdrop")
        self.assertEqual(first["raised"], "$4.02 B")
        self.assertEqual(first["preval"], "$40 B")
        self.assertEqual(first["date"], "from Q1, 2024")
        self.assertEqual(first["url"], "https://icodrops.com/kalshi/")

    def test_row_with_nested_investor_li_is_not_truncated(self):
        """投资方 logo 是嵌套 <li>，正则切分会把活动类型切丢——必须完整保留。"""
        variants = [x for x in ic.parse_active_html(ACTIVE_HTML) if x["name"] == "Variational"][0]
        self.assertEqual(variants["round"], "Genesis Airdrop")
        self.assertEqual(variants["kind"], "airdrop")
        self.assertEqual(variants["ticker"], "VAR")

    def test_dash_placeholder_normalized_to_empty(self):
        """上游用「—」表示无数据，要归一成空串，前端才不会显示一个假数值。"""
        solflare = [x for x in ic.parse_active_html(ACTIVE_HTML) if x["name"] == "Solflare"][0]
        self.assertEqual(solflare["raised"], "")
        self.assertEqual(solflare["preval"], "")

    def test_empty_html(self):
        self.assertEqual(ic.parse_active_html(""), [])


class IconTests(unittest.TestCase):
    """图标：上游懒加载，真实地址在 data-src，src 常是 data: 占位符。"""

    def _one(self, img_attrs):
        html = f'<ul>{_row("Demo", "", "Airdrop", img=img_attrs)}</ul>'
        return ic.parse_active_html(html)[0]

    def test_prefers_data_src(self):
        item = self._one(
            'src="data:image/gif;base64,R0lGOD" data-src="https://icodrops.com/media/cache/1/2/x.webp"'
        )
        self.assertEqual(item["icon"], "https://icodrops.com/media/cache/1/2/x.webp")

    def test_skips_data_placeholder_and_falls_back_to_srcset(self):
        item = self._one(
            'src="data:image/gif;base64,R0lGOD" data-srcset="https://icodrops.com/media/cache/3/4/y@2x.webp 2x"'
        )
        self.assertEqual(item["icon"], "https://icodrops.com/media/cache/3/4/y@2x.webp")

    def test_relative_path_made_absolute(self):
        item = self._one('data-src="/media/cache/5/6/z.webp"')
        self.assertEqual(item["icon"], "https://icodrops.com/media/cache/5/6/z.webp")

    def test_missing_img_yields_empty_string(self):
        html = """
        <ul><li class="Tbl-Row"><div class="Tbl-Row__item--project">
          <a class="Cll-Project__link" href="/x/"><p class="Cll-Project__name">NoLogo</p></a>
        </div></li></ul>
        """
        items = ic.parse_active_html(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["icon"], "")

    def test_rows_without_any_icon_attribute(self):
        """上游某些行只给 src（没有 data-src），也要能拿到图。"""
        item = self._one('src="https://icodrops.com/media/cache/7/8/w.webp"')
        self.assertEqual(item["icon"], "https://icodrops.com/media/cache/7/8/w.webp")


class PayloadTests(unittest.TestCase):
    def test_counts_split_by_kind(self):
        payload = ic._payload_from_items(ic.parse_active_html(ACTIVE_HTML))
        self.assertEqual(payload["total"], 8)
        # airdrop: Kalshi, Variational, Solflare, Tempo, Recall = 5
        self.assertEqual(payload["airdropCount"], 5)
        # sale: EarnBIT, CLIX, Slop Cash = 3
        self.assertEqual(payload["saleCount"], 3)
        self.assertEqual(payload["otherCount"], 0)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.snapshot = Path(self._tmp.name) / "icodrops_active_airdrops.json"
        patcher = mock.patch.object(ic, "ICODROPS_SNAPSHOT", self.snapshot)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 详情页补全缓存也要隔离，否则会读到仓库里真实的 .runtime-cache。
        funding = Path(self._tmp.name) / "icodrops_funding_detail.json"
        patcher2 = mock.patch.object(ic, "ICODROPS_FUNDING_CACHE", funding)
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def test_fetch_writes_snapshot(self):
        with mock.patch.object(ic, "_fetch_html", return_value=ACTIVE_HTML):
            payload = ic.fetch_active_airdrops(force=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total"], 8)
        self.assertTrue(self.snapshot.exists())

    def test_cached_payload_reused_without_refetch(self):
        with mock.patch.object(ic, "_fetch_html", return_value=ACTIVE_HTML):
            ic.fetch_active_airdrops(force=True)
        with mock.patch.object(ic, "_fetch_html", side_effect=AssertionError("不该重抓")):
            payload = ic.fetch_active_airdrops()
        self.assertEqual(payload["total"], 8)

    def test_failure_falls_back_to_cache_with_stale_flag(self):
        with mock.patch.object(ic, "_fetch_html", return_value=ACTIVE_HTML):
            ic.fetch_active_airdrops(force=True)
        with mock.patch.object(ic, "_fetch_html", side_effect=RuntimeError("HTTP 503")):
            payload = ic.fetch_active_airdrops(force=True)
        # 不许伪装成空榜：仍然返回上一次的真实数据，但明确标 stale + 原因。
        self.assertEqual(payload["total"], 8)
        self.assertTrue(payload["stale"])
        self.assertIn("503", payload["error"])

    def test_failure_without_cache_returns_error_not_empty_board(self):
        with mock.patch.object(ic, "_fetch_html", side_effect=RuntimeError("down")):
            payload = ic.fetch_active_airdrops(force=True)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["items"], [])
        self.assertIn("down", payload["error"])

    def test_empty_parse_treated_as_failure(self):
        """解析到 0 条说明页面结构变了，不能当成「今天没空投」。"""
        with mock.patch.object(ic, "_fetch_html", return_value="<html><body></body></html>"):
            payload = ic.fetch_active_airdrops(force=True)
        self.assertFalse(payload["ok"])
        self.assertIn("没有解析到", payload["error"])


class ParseDetailFundingTests(unittest.TestCase):
    def test_real_structure_one_section_many_boxes(self):
        """真实结构：一个 section 装多个 __box。

        踩过的坑：按 section 遍历 + select_one 只拿到第一个 box（凑巧是「—」），
        把「Pre-Valuation $16 M」整条丢掉。必须按 box 遍历。
        """
        html = """
        <section class="Overview-Section-Price-Block">
          <div class="Overview-Section-Price-Block__box">
            <p class="Overview-Section-Price-Block__title">Total Raised</p>
            <p class="Overview-Section-Price-Block__value"><span class="Empty-Data">—</span></p>
            <span class="Overview-Section-Price-Block__note">In 3 rounds</span>
          </div>
          <div class="Overview-Section-Price-Block__box">
            <p class="Overview-Section-Price-Block__title">Pre-Valuation</p>
            <p class="Overview-Section-Price-Block__value">$16 M</p>
          </div>
        </section>"""
        self.assertEqual(ic.parse_detail_funding(html), {"preval": "$16 M"})

    def test_reads_total_raised_and_pre_valuation(self):
        html = """
        <div class="Overview-Section-Price-Block">
          <div class="Overview-Section-Price-Block__title">Total Raised</div>
          <div class="Overview-Section-Price-Block__value">$19.5 M</div>
        </div>
        <div class="Overview-Section-Price-Block">
          <div class="Overview-Section-Price-Block__title">Pre-Valuation</div>
          <div class="Overview-Section-Price-Block__value">$16 M&nbsp;</div>
        </div>"""
        self.assertEqual(
            ic.parse_detail_funding(html), {"raised": "$19.5 M", "preval": "$16 M"}
        )

    def test_empty_data_marker_is_treated_as_absent(self):
        """``<span class="Empty-Data">—</span>`` 是「上游没有」，绝不能读成 0。"""
        html = """
        <div class="Overview-Section-Price-Block">
          <div class="Overview-Section-Price-Block__title">Total Raised</div>
          <div class="Overview-Section-Price-Block__value"><span class="Empty-Data">—</span></div>
        </div>"""
        self.assertEqual(ic.parse_detail_funding(html), {})

    def test_unknown_blocks_are_ignored(self):
        html = """
        <div class="Overview-Section-Price-Block">
          <div class="Overview-Section-Price-Block__title">Token Price</div>
          <div class="Overview-Section-Price-Block__value">$0.02</div>
        </div>"""
        self.assertEqual(ic.parse_detail_funding(html), {})

    def test_empty_html(self):
        self.assertEqual(ic.parse_detail_funding(""), {})


class ApplyDetailFundingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "icodrops_funding_detail.json"
        patcher = mock.patch.object(ic, "ICODROPS_FUNDING_CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, entries):
        import json

        self.cache.write_text(json.dumps({"entries": entries}), encoding="utf-8")

    def test_fills_only_missing_fields(self):
        self._write({"zns-connect": {"raised": "", "preval": "$16 M", "fetchedAt": 9999999999999}})
        items = [{"name": "ZNS Connect", "url": "https://icodrops.com/zns-connect/", "raised": "", "preval": ""}]
        self.assertEqual(ic.apply_detail_funding(items), 1)
        self.assertEqual(items[0]["preval"], "$16 M")
        self.assertEqual(items[0]["fundingSource"], "detail")

    def test_never_overwrites_list_page_value(self):
        self._write({"zns-connect": {"raised": "", "preval": "$99 M", "fetchedAt": 9999999999999}})
        items = [{"name": "ZNS Connect", "url": "https://icodrops.com/zns-connect/", "raised": "$1 M", "preval": "$16 M"}]
        self.assertEqual(ic.apply_detail_funding(items), 0)
        self.assertEqual(items[0]["preval"], "$16 M")

    def test_skips_failed_entries(self):
        self._write({"zns-connect": {"error": "HTTP 503", "fetchedAt": 9999999999999}})
        items = [{"name": "ZNS Connect", "url": "https://icodrops.com/zns-connect/", "raised": "", "preval": ""}]
        self.assertEqual(ic.apply_detail_funding(items), 0)
        self.assertEqual(items[0]["preval"], "")

    def test_skips_expired_entries(self):
        self._write({"zns-connect": {"preval": "$16 M", "fetchedAt": 1000}})
        items = [{"name": "ZNS Connect", "url": "https://icodrops.com/zns-connect/", "raised": "", "preval": ""}]
        self.assertEqual(ic.apply_detail_funding(items), 0)

    def test_no_cache_file_is_noop(self):
        items = [{"name": "X", "url": "https://icodrops.com/x/", "raised": "", "preval": ""}]
        self.assertEqual(ic.apply_detail_funding(items), 0)


class PendingDetailUrlsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(ic, "ICODROPS_FUNDING_CACHE", Path(self._tmp.name) / "f.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_skips_rows_that_already_have_both_numbers(self):
        items = [
            {"url": "https://icodrops.com/full/", "raised": "$1 M", "preval": "$10 M"},
            {"url": "https://icodrops.com/half/", "raised": "$1 M", "preval": ""},
        ]
        self.assertEqual(ic._pending_detail_urls(items, 10), ["https://icodrops.com/half/"])

    def test_respects_batch_limit(self):
        items = [{"url": f"https://icodrops.com/p{i}/", "raised": "", "preval": ""} for i in range(5)]
        self.assertEqual(len(ic._pending_detail_urls(items, 2)), 2)

    def test_ignores_non_icodrops_urls(self):
        items = [{"url": "https://example.com/x/", "raised": "", "preval": ""}]
        self.assertEqual(ic._pending_detail_urls(items, 10), [])


if __name__ == "__main__":
    unittest.main()
