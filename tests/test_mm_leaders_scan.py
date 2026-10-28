"""「龙头 → 做市商」扫描器的纯判定部分。

三件事最容易错，各有对应用例：

1. **ticker 归一**：TradingView 写 `BINANCE:PEPEUSDT`，剥错就整列查不到东西。
2. **符号撞车**：`CAKE` 在 DexScreener 上排第一的是一条 Solana 假币，`liquidity` 恰好
   等于市值（真实池子不可能持有全部流通）—— 只按流动性挑必错，得先用市值筛。
3. **厂家名匹配**：Blockscout 的标签是 `Wintermute 3` / `Cumberland (DRW)` 这种，
   匹配太宽会把 `Jump` 之外的东西也吸进来。
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import mm_leaders_scan as scan  # noqa: E402
import mm_seed_addresses as seed  # noqa: E402


class NormalizeSymbolTest(unittest.TestCase):
    def test_strips_exchange_and_quote(self):
        cases = {
            "BINANCE:PEPEUSDT": "PEPE",
            "OKX:CAKEUSDT": "CAKE",
            "PEPEUSDT.P": "PEPE",
            "BTCUSDT": "BTC",
            "ETHUSD": "ETH",
            "WIFUSDC": "WIF",
            "SOL": "SOL",
            " pepe ": "PEPE",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(scan.normalize_symbol(raw), expected)

    def test_does_not_eat_short_symbols(self):
        # `USD` 本身就是符号，不能被剥成空串。
        self.assertEqual(scan.normalize_symbol("USD"), "USD")


class MatchFirmTest(unittest.TestCase):
    def test_recognises_blockscout_labels(self):
        cases = {
            "Wintermute 3": "wintermute",
            "Wintermute Trading": "wintermute",
            "GSR Markets": "gsr",
            "Cumberland (DRW)": "cumberland",
            "DWF Labs": "dwf",
            "Amber Group": "amber",
            "B2C2": "b2c2",
            "Flow Traders": "flow",
            "Jump Trading": "jump",
            "Jump Crypto": "jump",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(scan.match_firm(raw), expected)

    def test_ignores_unrelated_labels(self):
        for raw in ("Uniswap V3 Pool", "Binance 14", "", "Random Wallet"):
            with self.subTest(raw=raw):
                self.assertEqual(scan.match_firm(raw), "")

    def test_extra_market_makers_are_recognised_but_not_mapped(self):
        """名册外的做市商要认得出（好报告给人），但不能自动落成某个 firm 键。"""
        for name in ("Dexterity Capital", "Tokka Labs", "Presto Labs"):
            with self.subTest(name=name):
                self.assertTrue(scan.looks_like_extra_mm(name))
                self.assertEqual(scan.match_firm(name), "")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return _FakeResponse(self.payload)


def pair(chain_id, symbol, address, *, mcap, liq):
    return {
        "chainId": chain_id,
        "baseToken": {"symbol": symbol, "address": address},
        "marketCap": mcap,
        "liquidity": {"usd": liq},
    }


class ResolveDexTest(unittest.TestCase):
    def test_picks_by_market_cap(self):
        session = _FakeSession({"pairs": [
            pair("ethereum", "PEPE", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", mcap=1_900_000_000, liq=31_000_000),
            pair("solana", "PEPE", "FgLDooU9NUiAarwcrV7GVzJ5Sib9AHgoTSGT2VZGA7PQ", mcap=9_000_000, liq=1_700_000),
        ]})
        got = scan.resolve_dex("PEPE", session=session)
        self.assertEqual(got["chain"], "ethereum")
        self.assertEqual(got["contract"], "0x6982508145454Ce325dDbE47a25d4ec3d2311933")

    def test_drops_pairs_whose_liquidity_near_market_cap(self):
        """真实池子不可能持有全部流通 —— 这种对全是假币/貔貅。"""
        session = _FakeSession({"pairs": [
            pair("solana", "CAKE", "2Syibr4EcKieM4f717Y4m3SRTQ1n4fNc4Q7fJvHhPzZ9", mcap=2_550_154_772, liq=2_549_842_415),
            pair("bsc", "CAKE", "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", mcap=842_301_768, liq=5_007_083),
        ]})
        got = scan.resolve_dex("CAKE", session=session)
        self.assertEqual(got["chain"], "bsc")

    def test_ignores_unsupported_chains_and_wrong_symbols(self):
        session = _FakeSession({"pairs": [
            pair("tron", "PEPE", "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf", mcap=9e9, liq=1e8),
            pair("ethereum", "PEPE2", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", mcap=9e9, liq=1e8),
            pair("ethereum", "PEPE", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", mcap=1e8, liq=1e6),
        ]})
        got = scan.resolve_dex("PEPE", session=session)
        self.assertEqual(got["chain"], "ethereum")

    def test_returns_none_when_nothing_survives(self):
        session = _FakeSession({"pairs": [
            pair("ethereum", "PEPE", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", mcap=50_000, liq=10_000),
        ]})
        self.assertIsNone(scan.resolve_dex("PEPE", session=session))

    def test_address_shape_guard_rejects_solana_address_on_evm(self):
        session = _FakeSession({"pairs": [
            pair("ethereum", "X", "FgLDooU9NUiAarwcrV7GVzJ5Sib9AHgoTSGT2VZGA7PQ", mcap=9e8, liq=1e7),
        ]})
        self.assertIsNone(scan.resolve_dex("X", session=session))


class LocalPositionsTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "mm.sqlite"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE mm_positions (
                firm TEXT, chain TEXT, token TEXT, symbol TEXT, name TEXT,
                decimals INTEGER DEFAULT 18, amount_in REAL, amount_out REAL,
                usd_in REAL, usd_out REAL, transfers INTEGER, addresses INTEGER,
                price_usd REAL, first_seen_at INTEGER, last_seen_at INTEGER,
                announced_at INTEGER, PRIMARY KEY (firm, chain, token)
            );
            """
        )
        conn.executemany(
            "INSERT INTO mm_positions (firm, chain, token, symbol, usd_in, usd_out, transfers, last_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("wintermute", "robinhood", "0xabc", "PONS", 40_000, 32_000, 129, 100),
                ("gsr", "ethereum", "0xdef", "PONS", 9_000, 0, 12, 90),
                ("wintermute", "ethereum", "0x999", "PEPE", 1_000, 0, 3, 80),
            ],
        )
        conn.commit()
        conn.close()
        self._old = scan.MM_DB
        scan.MM_DB = path

    def tearDown(self):
        scan.MM_DB = self._old
        self._dir.cleanup()

    def test_returns_positions_sorted_by_usd(self):
        rows = scan.local_positions("pons")
        self.assertEqual([r["firm"] for r in rows], ["wintermute", "gsr"])
        self.assertAlmostEqual(rows[0]["usd"], 72_000)
        self.assertEqual(rows[0]["transfers"], 129)

    def test_missing_symbol_returns_empty_list(self):
        self.assertEqual(scan.local_positions("NOPE"), [])


class JunkAddressTest(unittest.TestCase):
    """Robinhood 是 Orbit 链，预编译地址长 `0x000000…0064`，Arkham 会把它们列进实体。"""

    def test_rejects_zero_burn_and_precompiles(self):
        for raw in (
            "0x0000000000000000000000000000000000000000",
            "0x0000000000000000000000000000000000000064",
            "0x00000000ae34" + "0" * 28,
            "0x000000000000000000000000000000000000dead",
        ):
            with self.subTest(raw=raw):
                self.assertTrue(seed.JUNK_ADDRESS.match(raw))

    def test_keeps_normal_addresses(self):
        for raw in (
            "0x0019b435060b8e0e0e0e0e0e0e0e0e0e0e0e0e0e",
            "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            "0x0078f0cb76ff0e0e0e0e0e0e0e0e0e0e0e0e0e0e",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(seed.JUNK_ADDRESS.match(raw))


if __name__ == "__main__":
    unittest.main()
