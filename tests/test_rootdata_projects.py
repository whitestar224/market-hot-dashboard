"""RootData 项目热门榜：排序 / 过滤 / 行映射。

真正的取数靠专用 Chrome（腾讯云 WAF 滑块），这里只测纯逻辑，不碰网络。
"""
import unittest

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
        self.assertEqual(rows[0]["change"], "+724")
        self.assertEqual(rows[0]["heat"], 100)
        self.assertEqual(rows[0]["url"], "https://www.rootdata.com/Projects/detail/Elysium")
        self.assertIn("热度 214", [row["turnover"] for row in rows])
        # 老大哥一个都不许出现在行里
        self.assertFalse({"Binance", "OKX", "Tether"} & {row["symbol"] for row in rows})

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


if __name__ == "__main__":
    unittest.main()
