"""做市商实体目录（Arkham）的索引行为。

这个模块的价值全在「一次哈希就能判某个地址属于哪家」—— 所以测的重点是**链名归一**
和**取不到时的三态**（有值 / 空 / 接口挂了沿用旧缓存），而不是网络。
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mm_entity_directory as mdir


def fake_dir(fetched_at: int | None = None) -> dict:
    # `fetchedAt` 是**毫秒**时间戳，缓存新鲜度按它算。写死一个过去的秒数会被当成过期。
    stamp = fetched_at if fetched_at is not None else int(time.time() * 1000)
    return {
        "fetchedAt": stamp,
        "firms": {
            "wintermute": {
                "entityId": "wintermute", "name": "Wintermute", "type": "fund",
                "addresses": {
                    "ethereum": ["0xAAA111", "0xBBB222"],
                    "bsc": ["0xCCC333"],
                    "solana": ["SolAddr111"],
                },
            },
            "cumberland": {
                "entityId": "cumberland", "name": "Cumberland (DRW)", "type": "fund",
                "addresses": {"ethereum": ["0xDDD444"]},
            },
        },
        "errors": [],
    }


class FirmOfTest(unittest.TestCase):
    def test_finds_firm_by_address_case_insensitively(self):
        data = fake_dir()
        self.assertEqual(mdir.firm_of("ethereum", "0xaaa111", data=data), "wintermute")
        self.assertEqual(mdir.firm_of("bsc", "0xCCC333", data=data), "wintermute")
        self.assertEqual(mdir.firm_of("ethereum", "0xddd444", data=data), "cumberland")

    def test_chain_aliases_are_normalised(self):
        data = fake_dir()
        # `eth` / `1` / `ethereum` 都是同一条链 —— 候选来源写法各不相同（踩过）。
        for alias in ("eth", "1", "ethereum", "以太坊"):
            with self.subTest(alias=alias):
                self.assertEqual(mdir.firm_of(alias, "0xAAA111", data=data), "wintermute")

    def test_unknown_address_or_chain_is_empty_not_wrong_firm(self):
        data = fake_dir()
        self.assertEqual(mdir.firm_of("ethereum", "0x999999", data=data), "")
        self.assertEqual(mdir.firm_of("solana", "0xAAA111", data=data), "")
        self.assertEqual(mdir.firm_of("ethereum", "", data=data), "")

    def test_addresses_of_returns_lowercase_set(self):
        data = fake_dir()
        self.assertEqual(mdir.addresses_of("wintermute", "eth", data=data), {"0xaaa111", "0xbbb222"})
        self.assertEqual(mdir.addresses_of("cumberland", "solana", data=data), set())


class CoverageTest(unittest.TestCase):
    def test_counts_per_firm_per_chain(self):
        cov = mdir.coverage(fake_dir())
        self.assertEqual(cov["wintermute"]["bsc"], 1)
        self.assertEqual(sum(cov["wintermute"].values()), 4)
        self.assertEqual(sum(cov["cumberland"].values()), 1)


class EnsureTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XINGYUN_RUNTIME_DIR")
        os.environ["XINGYUN_RUNTIME_DIR"] = self._dir.name
        mdir._CACHE = None

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XINGYUN_RUNTIME_DIR", None)
        else:
            os.environ["XINGYUN_RUNTIME_DIR"] = self._old
        mdir._CACHE = None
        self._dir.cleanup()

    def test_fresh_cache_is_used_without_network(self):
        mdir.cache_path().write_text(json.dumps(fake_dir()), encoding="utf-8")
        called = []
        original = mdir.refresh
        mdir.refresh = lambda **kwargs: called.append(1) or {"firms": {}, "errors": ["should not run"]}
        try:
            data = mdir.ensure()
        finally:
            mdir.refresh = original
        self.assertEqual(called, [])
        self.assertEqual(mdir.firm_of("ethereum", "0xaaa111", data=data), "wintermute")

    def test_expired_cache_is_refreshed(self):
        stale = fake_dir(int((time.time() - 40 * 86400) * 1000))
        mdir.cache_path().write_text(json.dumps(stale), encoding="utf-8")
        fresh = {"fetchedAt": 2, "firms": {"gsr": {"addresses": {"ethereum": ["0xNEW"]}}}, "errors": []}
        original = mdir.refresh
        mdir.refresh = lambda **kwargs: fresh
        try:
            data = mdir.ensure()
        finally:
            mdir.refresh = original
        self.assertEqual(mdir.firm_of("ethereum", "0xnew", data=data), "gsr")

    def test_refresh_failure_keeps_stale_cache_instead_of_wiping(self):
        """接口挂了不能表现成「这些厂家一个地址都没有」—— 那会让识别结果全空。"""
        stale = fake_dir(int((time.time() - 40 * 86400) * 1000))
        mdir.cache_path().write_text(json.dumps(stale), encoding="utf-8")
        original = mdir.refresh
        mdir.refresh = lambda **kwargs: {"fetchedAt": 3, "firms": {}, "errors": ["401"]}
        try:
            data = mdir.ensure()
        finally:
            mdir.refresh = original
        self.assertTrue(data.get("stale"))
        self.assertEqual(mdir.firm_of("ethereum", "0xaaa111", data=data), "wintermute")


if __name__ == "__main__":
    unittest.main()
