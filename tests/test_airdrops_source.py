"""airdrops_source：解析 airdrops.io 首页 + 概览统计 + 失败降级。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import airdrops_source as ad

AIRDROP_HTML = """
<html><body>
<ul class="adio-wire__list">
  <li class="adio-wire__item"><span class="adio-wire__tag adio-wire__tag--live">Claim live</span><a class="adio-wire__link" href="https://airdrops.io/mint-io/">MINT</a></li>
  <li class="adio-wire__item"><span class="adio-wire__tag">Updated</span><a class="adio-wire__link" href="https://airdrops.io/onre/">OnRe</a></li>
  <li class="adio-wire__item"><span class="adio-wire__tag">Confirmed</span><a class="adio-wire__link" href="https://airdrops.io/sectora/">Sectora</a></li>
  <li class="adio-wire__item"><span class="adio-wire__tag">Claim live</span><a class="adio-wire__link" href="https://airdrops.io/genlayer/">GenLayer</a></li>
  <li class="adio-wire__item"><span class="adio-wire__tag">New</span><a class="adio-wire__link" href="https://airdrops.io/stablestock/">StableStock</a></li>
  <li class="adio-wire__item"><span class="adio-wire__tag">New</span><a class="adio-wire__link" href="https://airdrops.io/squeekchain/">Squeekchain</a></li>
</ul>
</body></html>
"""


class AirdropsParseTests(unittest.TestCase):
    def test_parse_extracts_name_status_url(self):
        items = ad.parse_airdrops_html(AIRDROP_HTML)
        self.assertEqual(len(items), 6)
        self.assertEqual(items[0]["name"], "MINT")
        self.assertEqual(items[0]["status"], "Claim live")
        self.assertEqual(items[0]["url"], "https://airdrops.io/mint-io/")

    def test_overview_counts_by_status(self):
        items = ad.parse_airdrops_html(AIRDROP_HTML)
        overview = ad._overview_from_items(items)
        self.assertEqual(overview["total"], 6)
        self.assertEqual(overview["new"], 2)
        # confirmed = 明确 confirmed(1) + claim live(2) = 3
        self.assertEqual(overview["confirmed"], 3)
        self.assertEqual(overview["claim_live"], 2)
        self.assertEqual(overview["updated"], 1)


class AirdropsFetchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.snapshot = Path(self._tmp.name) / "airdrops_overview.json"
        self.patcher = mock.patch.object(ad, "AIRDROP_SNAPSHOT", self.snapshot)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_fetch_parses_and_writes_snapshot(self):
        with mock.patch.object(ad, "_fetch_html", return_value=AIRDROP_HTML):
            overview = ad.fetch_airdrops_overview(force=True)
        self.assertEqual(overview["total"], 6)
        self.assertTrue(self.snapshot.exists())

    def test_fetch_failure_returns_empty_gracefully(self):
        with mock.patch.object(ad, "_fetch_html", side_effect=RuntimeError("down")):
            overview = ad.fetch_airdrops_overview(force=True)
        self.assertEqual(overview["total"], 0)
        self.assertTrue(overview["stale"])


if __name__ == "__main__":
    unittest.main()
