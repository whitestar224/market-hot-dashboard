import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class RuntimeResourceLimitTests(unittest.TestCase):
    def test_chain_ecosystem_jobs_share_a_bounded_executor(self):
        monitor = server.CHAIN_ECOSYSTEM_MONITOR
        self.assertIsNotNone(monitor._executor)
        self.assertEqual(monitor._executor._max_workers, monitor.worker_count)
        self.assertLessEqual(monitor.worker_count, 6)

    def test_event_candidates_can_be_prepared_once_without_changing_output(self):
        raw = {
            "symbol": "TEST",
            "name": "Test Token",
            "chain": "bsc",
            "contractAddress": "0x" + "ab" * 20,
            "liquidityUsd": 250_000,
            "volume24hUsd": 500_000,
            "heat": 75,
        }
        expected = server.event_monitor_onchain_candidate(raw)
        prepared = {**expected, "_eventMonitorPrepared": True}
        self.assertEqual(server.event_monitor_onchain_candidate(prepared), expected)

        baseline = server.event_monitor_candidate_relevance(
            expected, "TEST launches today", ["TEST"], ["TEST"]
        )
        optimized = server.event_monitor_candidate_relevance(
            expected,
            "TEST launches today",
            ["TEST"],
            ["TEST"],
            combined_folded="test launches today",
            normalized_listing={"TEST"},
            entity_pairs=[("TEST", "test")],
        )
        self.assertEqual(optimized, baseline)

    def test_timestamped_cache_expires_and_caps_oldest_entries(self):
        now = 10_000.0
        cache = {
            "expired": (now - 61, {"value": 0}),
            "old": (now - 3, {"value": 1}),
            "middle": (now - 2, {"value": 2}),
            "new": (now - 1, {"value": 3}),
        }

        removed = server.prune_timestamped_cache(
            cache,
            ttl_seconds=60,
            max_entries=2,
            now=now,
        )

        self.assertEqual(removed, 2)
        self.assertEqual(set(cache), {"middle", "new"})

    def test_runtime_cleanup_keeps_state_and_only_latest_qr_images(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = root / "xingyunshe_auth.db"
            state.write_bytes(b"state")
            for index in range(12):
                path = root / f"wechat-auth-qr-u{index}.png"
                path.write_bytes(b"qr")
                os.utime(path, (now - index, now - index))
            expired_current = root / "wechat-auth-qr-current.png"
            expired_current.write_bytes(b"current")
            os.utime(expired_current, (now - 7200, now - 7200))
            abandoned = root / "payload.json.tmp"
            abandoned.write_text("partial", encoding="utf-8")
            os.utime(abandoned, (now - 7200, now - 7200))

            removed = server.cleanup_runtime_ephemeral_files(
                root,
                now=now,
                keep_qr_uuid="current",
            )

            self.assertTrue(state.exists())
            self.assertTrue(expired_current.exists())
            self.assertLessEqual(len(list(root.glob("wechat-auth-qr-u*.png"))), 8)
            self.assertFalse(abandoned.exists())
            self.assertGreaterEqual(removed["qr"], 4)
            self.assertEqual(removed["temp"], 1)

    def test_event_monitor_core_reuses_persisted_snapshot(self):
        payload = {"ok": True, "updatedAt": 123, "events": [], "newsTrades": []}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server,
            "PERSIST_CACHE_DIR",
            Path(temp_dir),
        ), patch.object(
            server,
            "build_event_monitor_core_payload",
            return_value=payload,
        ) as build, patch.object(
            server,
            "read_json_cache",
            wraps=server.read_json_cache,
        ) as read_cache:
            first = server.cached_event_monitor_core_payload()
            second = server.cached_event_monitor_core_payload()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(build.call_count, 1)
        self.assertEqual(read_cache.call_count, 1)

    def test_stuck_api_refresh_does_not_spawn_replacement_worker(self):
        key = "resource-limit-stuck-refresh"
        with server.API_REFRESH_LOCK:
            server.API_REFRESHING[key] = time.time() - server.API_REFRESH_STUCK_SECONDS - 1
            server.API_REFRESH_STALL_LOGGED.discard(key)
        try:
            with patch.object(server.API_REFRESH_POOL, "submit") as submit:
                server.trigger_api_refresh(key, lambda: {"ok": True})
            submit.assert_not_called()
            with server.API_REFRESH_LOCK:
                self.assertIn(key, server.API_REFRESHING)
                self.assertIn(key, server.API_REFRESH_STALL_LOGGED)
        finally:
            with server.API_REFRESH_LOCK:
                server.API_REFRESHING.pop(key, None)
                server.API_REFRESH_STALL_LOGGED.discard(key)

    def test_stuck_price_snapshot_builder_is_not_replaced(self):
        worker = Mock()
        worker.is_alive.return_value = True
        cached = {"ok": True, "items": [{"symbol": "KEEP"}]}
        with (
            patch.object(server, "PRICE_WATCH_SNAPSHOT_CACHE", cached),
            patch.object(server, "PRICE_WATCH_SNAPSHOT_CACHE_AT", 0.0),
            patch.object(server, "PRICE_WATCH_SNAPSHOT_BUILD_THREAD", worker),
            patch.object(server, "PRICE_WATCH_SNAPSHOT_BUILD_STARTED_AT", 1.0),
            patch.object(server, "PRICE_WATCH_SNAPSHOT_STALL_LOGGED", False),
            patch.object(server, "PRICE_WATCH_SNAPSHOT_BUILD_LOCK", threading.Lock()),
            patch.object(server.time, "monotonic", return_value=1000.0),
            patch.object(server.threading, "Thread") as thread_type,
        ):
            result = server.price_watch_snapshot_payload()

        self.assertIs(result, cached)
        thread_type.assert_not_called()
        worker.join.assert_called_once_with(server.PRICE_WATCH_SNAPSHOT_JOIN_SECONDS)

    def test_stuck_news_ingest_suppresses_duplicate_thread(self):
        state = {
            "active": True,
            "started_at": time.time() - server.NEWS_INGEST_BACKGROUND_STUCK_SECONDS - 1,
            "stall_logged": False,
        }
        with (
            patch.object(server, "NEWS_INGEST_BACKGROUND_STATE", state),
            patch.object(server.threading, "Thread") as thread_type,
        ):
            server.spawn_background_news_ingest([{"title": "latest"}])

        thread_type.assert_not_called()
        self.assertTrue(state["stall_logged"])

    def test_repeated_onchain_ingest_coalesces_by_contract(self):
        fake_thread = Mock()
        fake_thread.is_alive.return_value = True
        pending = {}
        first = {"network": "solana", "contractAddress": "CA-1", "symbol": "OLD"}
        latest = {"network": "solana", "contractAddress": "CA-1", "symbol": "NEW"}
        with (
            patch.object(server, "ONCHAIN_RESEARCH_INGEST_PENDING", pending),
            patch.object(server, "ONCHAIN_RESEARCH_INGEST_THREAD", None),
            patch.object(server, "SERVER_SHUTDOWN_EVENT", threading.Event()),
            patch.object(server.threading, "Thread", return_value=fake_thread) as thread_type,
        ):
            server.queue_onchain_research_ingest([first])
            server.queue_onchain_research_ingest([latest])

        self.assertEqual(len(pending), 1)
        self.assertEqual(next(iter(pending.values()))[0]["symbol"], "NEW")
        thread_type.assert_called_once()
        fake_thread.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
