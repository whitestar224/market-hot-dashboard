"""跨源补录：GMGN 战壕榜把外部线索抓到的漏采新币标注补录进榜。

背景（2026-10-05 排查）：GMGN ``trenches/completed`` 上游只保留约 19 分钟，
本机一旦出现采集空档，空档内开盘的币就永久补不回来（solana 无法回补）。
Ave 热搜 / 币安钱包热门榜 / 个人 X 监控等常驻通道仍能抓到它们，于是把它们
按真实开盘时间并入战壕榜，并显式标注来源。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gmgn_agentic
import server


def trench_row(symbol: str, contract: str, observed_at: int, *, network: str = "solana") -> dict:
    return {
        "network": network,
        "contractAddress": contract,
        "symbol": symbol,
        "name": f"{symbol} token",
        "imageUrl": f"https://img.example/{symbol}.png",
        "poolCreatedAt": observed_at - 60_000,
        "tradeUrl": f"https://gmgn.ai/sol/token/{contract}",
        "filterSignals": {
            "profileVersion": gmgn_agentic.GMGN_TRENCH_FILTER_PROFILE_VERSION,
            "isOg": True,
            "imageDuplicateCount": 0,
            "washTrading": False,
            "socialCount": 1,
            "honeypot": None,
            "burnStatus": "burn",
        },
        "metrics": {
            "priceUsd": 0.01,
            "marketCapUsd": 50_000,
            "liquidityUsd": 20_000,
            "volumeH24Usd": 5_000,
            "priceChangeH1": 3.2,
            "transactionsH24": 18,
        },
    }


def candidate_row(
    symbol: str,
    contract: str,
    pool_created_at: int,
    *,
    network: str = "solana",
    providers: list[str] | None = None,
    market_cap: float = 80_000,
    decision: str = "shortlisted",
) -> dict:
    provider_list = list(providers or ["ave-hot"])
    return {
        "network": network,
        "contract_address": contract,
        "pool_address": f"pool-{contract}",
        "dex_id": "pumpswap",
        "symbol": symbol,
        "name": f"{symbol} token",
        "candidate_type": "project",
        "decision": decision,
        "selected_score": 72.5,
        "confidence": 88.0,
        "first_seen_at": pool_created_at + 90_000,
        "pool_created_at": pool_created_at,
        "last_seen_at": pool_created_at + 600_000,
        "trade_url": f"https://dexscreener.com/solana/{contract}",
        "providers_json": json.dumps([*provider_list, "dexscreener"]),
        "metrics_json": json.dumps({
            "priceUsd": 0.001,
            "marketCapUsd": market_cap,
            "liquidityUsd": 30_000,
            "volumeH24Usd": 12_000,
            "priceChangeH1": 1.5,
            "transactionsH24": 42,
            "holders": 77,
        }),
        "reasons_json": '["流动性达到早期研究门槛"]',
        "risks_json": '["团队信息不足"]',
    }


class FakeConnection:
    def __init__(self, records: list[dict]) -> None:
        self.records = records
        self.closed = False

    def execute(self, sql, params=None):  # noqa: ANN001
        cursor = Mock()
        cursor.fetchall.return_value = list(self.records)
        return cursor

    def close(self) -> None:
        self.closed = True


def patched_store(records: list[dict]):
    conn = FakeConnection(records)
    store = Mock()
    store._connect.return_value = conn
    return Mock(store=store), conn


class GmgnTrenchBackfillTests(unittest.TestCase):
    def test_external_clue_rows_are_backfilled_with_source_labels(self):
        now = 1_800_000_000_000
        payload, conn = patched_store([
            candidate_row("SPLICE", "SoLspliceContract", now - 3_600_000),
        ])
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", payload):
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=now - 7_200_000,
                window_end_ms=now,
                known_identities=set(),
                limit=10,
            )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["backfilled"])
        self.assertEqual(row["provider"], "external-backfill")
        self.assertEqual(row["originLabel"], "Ave.ai 热搜")
        self.assertEqual(row["backfillProviders"], ["ave-hot"])
        self.assertEqual(row["symbol"], "SPLICE")
        self.assertEqual(row["poolCreatedAt"], now - 3_600_000)
        # 真实开盘时间早于本机首次看到，说明不是兜底时间戳。
        self.assertLess(row["poolCreatedAt"], row["firstSeenAt"])
        self.assertFalse(row["isCurrent"])
        self.assertEqual(row["chainLabel"], "SOL")
        self.assertTrue(row["reasons"][0].startswith("外部线索补录"))
        self.assertIn("外部线索补录", row["note"])
        self.assertTrue(row["binanceWalletUrl"].startswith("https://web3.binance.com/"))
        self.assertTrue(conn.closed)

    def test_binance_wallet_clue_is_labelled_first(self):
        now = 1_800_000_000_000
        payload, _ = patched_store([
            candidate_row(
                "WALLETED",
                "SoLWallet",
                now - 3_600_000,
                providers=["ave-hot", "binance-wallet-hot"],
            ),
        ])
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", payload):
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=now - 7_200_000,
                window_end_ms=now,
                known_identities=set(),
                limit=10,
            )
        self.assertEqual(rows[0]["originLabel"], "币安钱包热门榜")
        self.assertEqual(rows[0]["backfillProviders"], ["binance-wallet-hot", "ave-hot"])

    def test_rows_gmgn_already_delivered_are_not_backfilled(self):
        now = 1_800_000_000_000
        payload, _ = patched_store([
            candidate_row("SEEN", "SoLSeen", now - 600_000, providers=["gmgn-trenches", "ave-hot"]),
        ])
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", payload):
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=now - 7_200_000,
                window_end_ms=now,
                known_identities=set(),
                limit=10,
            )
        self.assertEqual(rows, [])

    def test_rows_without_an_external_clue_provider_are_skipped(self):
        now = 1_800_000_000_000
        payload, _ = patched_store([
            candidate_row("BARE", "SoLBare", now - 600_000, providers=["dexscreener"]),
        ])
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", payload):
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=now - 7_200_000,
                window_end_ms=now,
                known_identities=set(),
                limit=10,
            )
        self.assertEqual(rows, [])

    def test_known_trench_identities_are_skipped_case_insensitively(self):
        now = 1_800_000_000_000
        payload, _ = patched_store([
            candidate_row("DUP", "0xAbCdEf", now - 600_000, network="bsc", providers=["ave-hot"]),
        ])
        known = {server.gmgn_trench_history_identity({"network": "bsc", "contractAddress": "0xabcdef"})}
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", payload):
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=now - 7_200_000,
                window_end_ms=now,
                known_identities=known,
                limit=10,
            )
        self.assertEqual(rows, [])

    def test_market_cap_floor_is_honoured(self):
        now = 1_800_000_000_000
        payload, _ = patched_store([
            candidate_row("TINY", "SoLTiny", now - 600_000, market_cap=9_999),
            candidate_row("OK", "SoLOk", now - 600_000, market_cap=10_001),
        ])
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", payload):
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=now - 7_200_000,
                window_end_ms=now,
                known_identities=set(),
                limit=10,
            )
        self.assertEqual([row["symbol"] for row in rows], ["OK"])

    def test_limit_zero_disables_backfill_without_touching_the_store(self):
        payload, _ = patched_store([candidate_row("X", "SoLX", 1_700_000_000_000)])
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", payload) as monitor:
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=1_700_000_000_000,
                window_end_ms=1_800_000_000_000,
                known_identities=set(),
                limit=0,
            )
        self.assertEqual(rows, [])
        monitor.store._connect.assert_not_called()

    def test_store_failure_degrades_to_no_backfill(self):
        store = Mock()
        store._connect.side_effect = RuntimeError("locked")
        with patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", Mock(store=store)):
            rows = server.gmgn_trench_external_backfill_rows(
                window_start_ms=1_700_000_000_000,
                window_end_ms=1_800_000_000_000,
                known_identities=set(),
                limit=10,
            )
        self.assertEqual(rows, [])

    def test_merge_interleaves_by_open_time_and_reranks(self):
        now = 1_800_000_000_000
        newest = trench_row("NEWEST", "SoLNewest", now)
        newest["poolCreatedAt"] = now
        oldest = trench_row("OLDEST", "SoLOldest", now)
        oldest["poolCreatedAt"] = now - 6 * 3_600_000
        board = server.gmgn_trench_board_rows([newest, oldest], current_rows=[newest, oldest])
        backfill = [{
            "network": "solana",
            "contractAddress": "SoLFill",
            "symbol": "FILL",
            "name": "FILL token",
            "poolCreatedAt": now - 3 * 3_600_000,
            "receivedAt": now - 3 * 3_600_000,
            "gmgnSourceIndex": 9999,
            "backfilled": True,
        }]

        merged = server.gmgn_trench_merge_backfill_rows(board, backfill, cap=10)

        self.assertEqual([row["symbol"] for row in merged], ["NEWEST", "FILL", "OLDEST"])
        self.assertEqual([row["rank"] for row in merged], [1, 2, 3])
        # 原榜的 rank 不能被就地改写
        self.assertEqual([row["rank"] for row in board], [1, 2])

    def test_merge_caps_the_tape(self):
        now = 1_800_000_000_000
        board_rows = []
        for index in range(5):
            row = trench_row(f"B{index}", f"SoLBoard{index}", now)
            row["poolCreatedAt"] = now - index * 1000
            board_rows.append(row)
        board = server.gmgn_trench_board_rows(board_rows, current_rows=board_rows)
        backfill = [{
            "network": "solana",
            "contractAddress": "SoLFillCap",
            "symbol": "FILLCAP",
            "name": "FILLCAP token",
            "poolCreatedAt": now + 5000,
            "receivedAt": now + 5000,
            "gmgnSourceIndex": 9999,
            "backfilled": True,
        }]

        merged = server.gmgn_trench_merge_backfill_rows(board, backfill, cap=3)

        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["symbol"], "FILLCAP")
        self.assertEqual([row["rank"] for row in merged], [1, 2, 3])

    def test_no_backfill_rows_returns_the_board_unchanged(self):
        now = 1_800_000_000_000
        board = server.gmgn_trench_board_rows(
            [trench_row("ONLY", "SoLOnly", now)],
            current_rows=[trench_row("ONLY", "SoLOnly", now)],
        )
        self.assertIs(server.gmgn_trench_merge_backfill_rows(board, [], cap=300), board)

    def test_refresh_marks_backfilled_rows_and_keeps_research_stream_gmgn_only(self):
        observed_at = 1_800_000_000_000
        newer = trench_row("NEWER", "SoLNewer", observed_at)
        newer["poolCreatedAt"] = observed_at - 600_000
        older = trench_row("OLDER", "SoLOlder", observed_at)
        older["poolCreatedAt"] = observed_at - 3_600_000
        payload = {
            "ok": True,
            "items": [newer, older],
            "sourceStatus": {"solana/gmgn-trenches": "ok"},
        }
        store_payload, _ = patched_store([
            candidate_row("SPLICE", "SoLBackfill", observed_at - 2_000_000),
        ])
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "GMGN_TRENCH_HISTORY_PATH", Path(temp_dir) / "gmgn-history.json"),
                patch.object(server, "fetch_live_onchain_trenches", return_value=payload),
                patch.object(server.time, "time", return_value=observed_at / 1000),
                patch.object(server, "ONCHAIN_FAST_RESEARCH", Mock()),
                patch.object(server, "queue_onchain_research_ingest", return_value=1) as queue_ingest,
                patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", store_payload),
            ):
                source = server.refresh_gmgn_trenches_hot_board()

        self.assertEqual(source["backfilledCount"], 1)
        self.assertEqual(source["historyCount"], 3)
        symbols = [row["symbol"] for row in source["rows"]]
        self.assertEqual(symbols, ["NEWER", "SPLICE", "OLDER"])
        # 补录条目已经在投研库里（带着外部线索 provider），不能再按 gmgn 通道重投。
        ingested = queue_ingest.call_args.args[0]
        self.assertEqual([item["symbol"] for item in ingested], ["NEWER", "OLDER"])

    def test_refresh_without_backfill_is_unchanged(self):
        observed_at = 1_800_000_000_000
        payload = {
            "ok": True,
            "items": [trench_row("NEW", "SoLaNaContract123", observed_at)],
            "sourceStatus": {"solana/gmgn-trenches": "ok"},
        }
        store_payload, _ = patched_store([])
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "GMGN_TRENCH_HISTORY_PATH", Path(temp_dir) / "gmgn-history.json"),
                patch.object(server, "fetch_live_onchain_trenches", return_value=payload),
                patch.object(server.time, "time", return_value=observed_at / 1000),
                patch.object(server, "ONCHAIN_FAST_RESEARCH", Mock()),
                patch.object(server, "queue_onchain_research_ingest", return_value=1),
                patch.object(server, "CHAIN_ECOSYSTEM_MONITOR", store_payload),
            ):
                source = server.refresh_gmgn_trenches_hot_board()

        self.assertEqual(source["backfilledCount"], 0)
        self.assertEqual(source["historyCount"], 1)
        self.assertEqual(source["rows"][0]["symbol"], "NEW")


if __name__ == "__main__":
    unittest.main()
