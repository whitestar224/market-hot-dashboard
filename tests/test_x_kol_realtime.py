import json
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import server


class XKolRealtimeTests(unittest.TestCase):
    def setUp(self):
        self.read_cache_patcher = patch.object(server, "read_json_cache", return_value={})
        self.read_cache_patcher.start()

    def tearDown(self):
        self.read_cache_patcher.stop()
        with server.X_KOL_REALTIME_CONDITION:
            server.X_KOL_REALTIME_SNAPSHOTS.clear()
            server.X_KOL_REALTIME_DISK_HYDRATED.clear()
        with server.X_KOL_OFFICIAL_STREAM_LOCK:
            server.X_KOL_OFFICIAL_STREAM_USERS.clear()
            server.X_KOL_OFFICIAL_STREAM_STARTED = False
            server.X_KOL_OFFICIAL_STREAM_HEALTH.update({
                "connected": False,
                "rulesReady": False,
                "targetCount": 0,
                "lastConnectedAt": 0,
                "lastEventAt": 0,
                "lastErrorAt": 0,
                "lastError": "",
                "updatedAt": 0,
            })
        with server.X_KOL_RSS_MIRROR_HEALTH_LOCK:
            server.X_KOL_RSS_MIRROR_HEALTH.clear()

    def test_sources_are_fetched_in_parallel_and_returned_in_saved_order(self):
        sources = [
            {"id": "x-alpha", "handle": "alpha", "displayName": "Alpha", "enabled": True},
            {"id": "x-beta", "handle": "beta", "displayName": "Beta", "enabled": True},
        ]
        lock = threading.Lock()
        active = 0
        max_active = 0

        def fake_fetch(source):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return {
                "source": {**source, "status": "ok", "provider": "rss", "limited": True},
                "items": [
                    {
                        "id": f"post-{source['handle']}",
                        "text": source["handle"],
                        "publishedAt": int(time.time() * 1000),
                    }
                ],
            }

        with (
            patch.object(server, "load_x_kol_sources", return_value=sources),
            patch.object(server, "x_kol_token", return_value=""),
            # Python 3.14 serializes MagicMock side effects internally, which
            # would make this test measure the mock lock instead of our pool.
            patch.object(server, "x_kol_fetch_rss_source", new=fake_fetch),
        ):
            payload = server.x_kol_feed_payload({"id": 101})

        self.assertGreaterEqual(max_active, 2)
        self.assertEqual([row["id"] for row in payload["sources"]], ["x-alpha", "x-beta"])
        self.assertEqual({row["id"] for row in payload["items"]}, {"post-alpha", "post-beta"})

    def test_signature_ignores_refresh_timestamp_but_tracks_content(self):
        base = {
            "items": [{"id": "post-1", "publishedAt": 1234, "text": "first"}],
            "sources": [{"id": "x-alpha", "status": "ok", "provider": "rss", "itemsReturned": 1}],
            "updatedAt": 1000,
        }
        refreshed = {**base, "updatedAt": 2000}
        changed = {
            **refreshed,
            "items": [{"id": "post-2", "publishedAt": 2345, "text": "second"}],
        }

        self.assertEqual(server.x_kol_payload_signature(base), server.x_kol_payload_signature(refreshed))
        self.assertNotEqual(server.x_kol_payload_signature(base), server.x_kol_payload_signature(changed))

    def test_snapshot_returns_pending_immediately_before_first_fetch(self):
        user = {"id": 99101}
        with patch.object(server, "ensure_x_kol_realtime_worker"):
            started_at = time.monotonic()
            payload, signature = server.x_kol_realtime_snapshot(user, wait_seconds=0)

        self.assertLess(time.monotonic() - started_at, 0.1)
        self.assertTrue(payload["pending"])
        self.assertTrue(signature)

    def test_official_stream_item_is_built_from_expanded_event(self):
        source = {
            "id": "x-alpha",
            "handle": "alpha",
            "displayName": "Alpha",
            "keywords": [],
            "enabled": True,
        }
        event = {
            "data": {
                "id": "1234567890",
                "author_id": "42",
                "text": "A new market update",
                "created_at": "2026-08-11T08:00:00.000Z",
                "public_metrics": {"like_count": 7},
            },
            "includes": {
                "users": [
                    {
                        "id": "42",
                        "username": "alpha",
                        "name": "Alpha Name",
                        "profile_image_url": "https://example.com/avatar_normal.jpg",
                    }
                ]
            },
        }

        item = server.x_kol_build_official_stream_item(source, event)

        self.assertIsNotNone(item)
        self.assertEqual(item["provider"], "x-stream")
        self.assertEqual(item["handle"], "alpha")
        self.assertEqual(item["sourceName"], "Alpha Name")
        self.assertEqual(item["metrics"]["like"], 7)
        self.assertIn("x.com/alpha/status/1234567890", item["url"])

    def test_stream_and_poll_payloads_merge_without_duplicate_items(self):
        now = int(time.time() * 1000)
        previous = {
            "items": [
                {"id": "post-1", "text": "first", "publishedAt": now - 1000},
                {"id": "post-shared", "text": "shared", "publishedAt": now - 500},
            ],
            "sources": [{"id": "x-alpha", "provider": "rss"}],
        }
        incoming = {
            "items": [
                {"id": "post-2", "text": "second", "publishedAt": now},
                {"id": "post-shared", "text": "shared", "publishedAt": now - 500},
            ],
            "sources": [{"id": "x-alpha", "provider": "x-stream"}],
        }

        merged = server.x_kol_merge_realtime_payload(previous, incoming)

        self.assertEqual([row["id"] for row in merged["items"]], ["post-2", "post-shared", "post-1"])
        self.assertEqual(len(merged["sources"]), 1)
        self.assertEqual(merged["sources"][0]["provider"], "x-stream")

    def test_failed_rss_refresh_keeps_last_successful_items_while_reconnecting(self):
        now = int(time.time() * 1000)
        previous = {
            "updatedAt": now - 5_000,
            "items": [{
                "id": "post-1", "sourceId": "x-alpha", "text": "last good",
                "publishedAt": now - 1_000, "provider": "rss",
            }],
            "sources": [{
                "id": "x-alpha", "handle": "alpha", "status": "ok",
                "provider": "rss", "itemsReturned": 1, "lastOkAt": now - 5_000,
            }],
        }
        incoming = {
            "items": [],
            "sources": [{
                "id": "x-alpha", "handle": "alpha", "status": "error",
                "provider": "rss", "error": "上游暂时波动，正在自动换源",
                "upstreamError": "timeout",
            }],
        }

        merged = server.x_kol_merge_realtime_payload(previous, incoming)

        self.assertEqual([row["id"] for row in merged["items"]], ["post-1"])
        self.assertEqual(merged["sources"][0]["status"], "recovering")
        self.assertTrue(merged["sources"][0]["stale"])
        self.assertIn("保留最近成功数据", merged["sources"][0]["error"])

    def test_empty_startup_poll_restores_persisted_personal_x_history(self):
        now = int(time.time() * 1000)
        cached = {
            "savedAt": now - 5_000,
            "payload": {
                "items": [{
                    "id": "post-kept",
                    "sourceId": "x:whitestar224",
                    "handle": "whitestar224",
                    "text": "重启后也要保留",
                    "publishedAt": now - 10_000,
                    "provider": "x-stream",
                }],
                "sources": [{
                    "id": "x:whitestar224",
                    "handle": "whitestar224",
                    "status": "ok",
                    "provider": "x-stream",
                    "itemsReturned": 1,
                }],
            },
        }
        empty_poll = {
            "items": [],
            "sources": [{
                "id": "x:whitestar224",
                "handle": "whitestar224",
                "status": "error",
                "provider": "rss",
                "error": "上游暂时波动",
            }],
            "upstreamMode": "priority-rss-race",
        }

        with (
            patch.object(server, "read_json_cache", return_value=cached),
            patch.object(server, "update_strategy_contexts_from_personal_x_payload") as update_contexts,
        ):
            published = server.publish_x_kol_realtime_payload(None, empty_poll)

        self.assertEqual([row["id"] for row in published["items"]], ["post-kept"])
        self.assertTrue(published["historyRestored"])
        self.assertEqual(published["sources"][0]["status"], "recovering")
        self.assertIn("保留最近成功数据", published["sources"][0]["error"])
        self.assertTrue(any(
            call.args and call.args[0] is cached["payload"]
            and call.kwargs.get("monitor_max_age_seconds") == server.personal_x_monitor_backfill_seconds()
            for call in update_contexts.call_args_list
        ))

    def test_failed_rss_mirror_enters_circuit_breaker_and_other_mirror_takes_over(self):
        templates = ["https://bad.example/{handle}/rss", "https://good.example/{handle}/rss"]
        first = server.x_kol_rss_template_candidates("alpha", templates, limit=2)
        self.assertEqual(first, templates)

        server.x_kol_record_rss_mirror_result({
            "template": templates[0], "ok": False, "elapsed": 0.2, "error": "timeout",
        })
        next_candidates = server.x_kol_rss_template_candidates("alpha", templates, limit=2)

        self.assertEqual(next_candidates, [templates[1]])

    def test_rss_candidate_pool_half_opens_earliest_mirror_when_all_are_cooling_down(self):
        templates = ["https://first.example/{handle}/rss", "https://second.example/{handle}/rss"]
        now = time.time()
        with server.X_KOL_RSS_MIRROR_HEALTH_LOCK:
            server.X_KOL_RSS_MIRROR_HEALTH.update({
                templates[0]: {"failures": 3, "retryAt": now + 30},
                templates[1]: {"failures": 2, "retryAt": now + 10},
            })

        candidates = server.x_kol_rss_template_candidates("alpha", templates, limit=2)

        self.assertEqual(candidates, [templates[1]])

    def test_valid_empty_rss_feed_is_healthy_instead_of_an_upstream_error(self):
        source = {"id": "x-alpha", "handle": "alpha", "displayName": "Alpha", "enabled": True}
        result = server.x_kol_rss_result_payload(source, [{
            "ok": True,
            "template": "https://rss.example/{handle}",
            "url": "https://rss.example/alpha",
            "rows": [],
            "elapsed": 0.1,
        }])

        self.assertEqual(result["source"]["status"], "ok")
        self.assertEqual(result["source"]["itemsReturned"], 0)
        self.assertEqual(result["items"], [])

    def test_public_timeline_fallback_parses_current_posts_without_paid_api(self):
        source = {"id": "x-long", "handle": "longdotxyz", "displayName": "long.xyz", "enabled": True}
        page_props = {
            "timeline": {
                "entries": [{
                    "content": {
                        "tweet": {
                            "id_str": "2094955556545122753",
                            "created_at": "Wed Sep 02 01:08:21 +0000 2026",
                            "full_text": "LONG passed $425M in tokenized stock volume",
                            "favorite_count": 12,
                            "retweet_count": 3,
                            "reply_count": 2,
                            "quote_count": 1,
                            "permalink": "https://x.com/longdotxyz/status/2094955556545122753",
                            "user": {
                                "name": "long.xyz",
                                "screen_name": "longdotxyz",
                                "profile_image_url_https": "https://pbs.twimg.com/profile_images/long.jpg",
                            },
                        }
                    }
                }]
            },
            "headerProps": {},
        }
        body = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": page_props}})
            + "</script></html>"
        ).encode()

        result = server.x_kol_parse_public_timeline(source, body, "https://syndication.twitter.com/test")

        self.assertEqual(result["source"]["status"], "ok")
        self.assertEqual(result["source"]["upstreamSource"], "x-public-timeline")
        self.assertEqual(result["items"][0]["tweetId"], "2094955556545122753")
        self.assertEqual(result["items"][0]["provider"], "x-public-timeline")

    def test_fxtwitter_fallback_parses_profile_statuses_without_a_key(self):
        source = {"id": "x-long", "handle": "longdotxyz", "displayName": "long.xyz", "enabled": True}
        payload = {
            "code": 200,
            "results": [{
                "type": "status",
                "id": "2094955556545122753",
                "url": "https://x.com/longdotxyz/status/2094955556545122753",
                "text": "LONG passed $425M in tokenized stock volume",
                "created_at": "Wed Sep 02 01:08:21 +0000 2026",
                "created_timestamp": 1788311301,
                "replies": 30,
                "reposts": 18,
                "likes": 206,
                "quotes": 13,
                "views": 27343,
                "author": {
                    "name": "long.xyz",
                    "screen_name": "longdotxyz",
                    "avatar_url": "https://pbs.twimg.com/profile_images/long.jpg",
                },
            }],
        }

        result = server.x_kol_parse_fxtwitter_timeline(source, payload, "https://api.fxtwitter.com/test")

        self.assertEqual(result["source"]["status"], "ok")
        self.assertEqual(result["source"]["upstreamSource"], "fxtwitter")
        self.assertEqual(result["items"][0]["metrics"]["view"], 27343)
        self.assertEqual(result["items"][0]["provider"], "fxtwitter")

    def test_rss_source_uses_public_timeline_when_every_mirror_fails(self):
        source = {"id": "x-long", "handle": "longdotxyz", "displayName": "long.xyz", "enabled": True}
        fallback = {
            "source": {
                **source,
                "status": "ok",
                "provider": "rss",
                "upstreamSource": "x-public-timeline",
                "itemsReturned": 1,
            },
            "items": [{"id": "post-1", "tweetId": "1", "publishedAt": 1, "text": "fresh"}],
        }
        with (
            patch.object(server, "x_rss_templates", return_value=["https://bad.example/{handle}/rss"]),
            patch.object(server, "x_kol_rss_template_candidates", return_value=["https://bad.example/{handle}/rss"]),
            patch.object(server, "x_kol_fetch_rss_template", return_value={
                "ok": False,
                "template": "https://bad.example/{handle}/rss",
                "url": "https://bad.example/longdotxyz/rss",
                "rows": [],
                "elapsed": 0.1,
                "error": "timeout",
            }),
            patch.object(server, "x_kol_public_timeline_fallback_enabled", return_value=True),
            patch.object(server, "x_kol_fxtwitter_fallback_enabled", return_value=False),
            patch.object(server, "x_kol_fetch_public_timeline", return_value=fallback) as fetch_public,
        ):
            result = server.x_kol_fetch_rss_source(source)

        self.assertEqual(result, fallback)
        fetch_public.assert_called_once_with(source)

    def test_priority_rss_source_uses_free_timeline_when_mirrors_fail(self):
        source = {"id": "x-owner", "handle": "whitestar224", "displayName": "白星", "enabled": True}
        fallback = {
            "source": {
                **source,
                "status": "ok",
                "provider": "rss",
                "upstreamSource": "fxtwitter",
                "itemsReturned": 1,
            },
            "items": [{"id": "post-1", "tweetId": "1", "publishedAt": 1, "text": "fresh"}],
        }
        failed = {
            "ok": False,
            "template": "https://bad.example/{handle}/rss",
            "url": "https://bad.example/whitestar224/rss",
            "rows": [],
            "elapsed": 0.1,
            "error": "timeout",
        }
        with (
            patch.object(server, "x_kol_rss_template_candidates", return_value=[failed["template"]]),
            patch.object(server, "x_kol_fetch_rss_template", return_value=failed),
            patch.object(server, "x_kol_fxtwitter_fallback_enabled", return_value=True),
            patch.object(server, "x_kol_fetch_fxtwitter_timeline", return_value=fallback) as fetch_fx,
        ):
            result = server.x_kol_fetch_priority_rss_source(source)

        self.assertEqual(result, fallback)
        fetch_fx.assert_called_once_with(source)

    def test_stream_and_rss_use_tweet_id_for_cross_provider_deduplication(self):
        now = int(time.time() * 1000)
        previous = {
            "items": [
                {
                    "id": "stream-hash",
                    "tweetId": "1234567890",
                    "url": "https://x.com/whitestar224/status/1234567890",
                    "text": "fresh",
                    "publishedAt": now,
                    "provider": "x-stream",
                }
            ]
        }
        incoming = {
            "items": [
                {
                    "id": "rss-hash",
                    "tweetId": "https://nitter.net/whitestar224/status/1234567890#m",
                    "url": "https://nitter.net/whitestar224/status/1234567890#m",
                    "text": "fresh from rss",
                    "publishedAt": now,
                    "provider": "rss",
                }
            ]
        }

        merged = server.x_kol_merge_realtime_payload(previous, incoming)

        self.assertEqual(len(merged["items"]), 1)
        self.assertEqual(merged["items"][0]["provider"], "x-stream")

    def test_global_stream_event_publishes_global_and_admin_snapshots(self):
        source = {
            "id": "x-owner",
            "handle": "whitestar224",
            "displayName": "Owner",
            "enabled": True,
        }
        event = {
            "data": {
                "id": "1234567890",
                "author_id": "42",
                "text": "A new live update",
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "includes": {
                "users": [
                    {
                        "id": "42",
                        "username": "whitestar224",
                        "name": "White Star",
                    }
                ]
            },
        }
        administrator = {"id": 7, "username": "admin"}

        with patch.object(server, "admin_user", return_value=administrator):
            server.x_kol_publish_official_stream_event(None, source, event)

        self.assertIn(0, server.X_KOL_REALTIME_SNAPSHOTS)
        self.assertIn(7, server.X_KOL_REALTIME_SNAPSHOTS)
        self.assertEqual(
            server.X_KOL_REALTIME_SNAPSHOTS[7]["payload"]["items"][0]["tweetId"],
            "1234567890",
        )

    def test_priority_source_uses_whitestar_and_disables_keyword_filtering(self):
        saved = {
            "id": "x-owner",
            "handle": "whitestar224",
            "displayName": "Owner",
            "keywords": ["crypto"],
            "enabled": True,
        }
        with (
            patch.object(server, "load_x_kol_sources", return_value=[saved]),
            patch.dict(server.os.environ, {"X_KOL_PRIORITY_HANDLES": "whitestar224"}),
        ):
            sources = server.x_kol_priority_sources()

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["handle"], "whitestar224")
        self.assertEqual(sources[0]["keywords"], [])
        self.assertTrue(sources[0]["priority"])

    def test_personal_x_monitor_payload_only_exposes_configured_owner_account(self):
        payload = {
            "ok": True,
            "realtime": True,
            "upstreamMode": "official-stream",
            "transport": "sse",
            "updatedAt": 1234,
            "sources": [
                {"id": "x-owner", "handle": "whitestar224", "displayName": "白星", "status": "ok"},
                {"id": "x-other", "handle": "other", "displayName": "Other", "status": "ok"},
            ],
            "items": [
                {"id": "owner-post", "handle": "whitestar224", "text": "owner", "publishedAt": 1200},
                {"id": "other-post", "handle": "other", "text": "other", "publishedAt": 1100},
            ],
        }
        tactical = [{"symbol": "ETHFI", "signalType": "watch", "signalLabel": "重点看"}]
        with (
            patch.dict(server.os.environ, {"X_KOL_PRIORITY_HANDLES": "whitestar224"}),
            patch.object(server, "strategy_tactical_signal_rows", return_value=tactical),
        ):
            result = server.personal_x_monitor_payload(payload)

        self.assertEqual(result["account"]["handle"], "whitestar224")
        self.assertEqual([row["id"] for row in result["items"]], ["owner-post"])
        self.assertEqual([row["id"] for row in result["sources"]], ["x-owner"])
        self.assertEqual(result["summary"]["posts"], 1)
        self.assertEqual(result["summary"]["transport"], "official-stream")
        self.assertEqual(result["tacticalSignals"], tactical)
        self.assertEqual(result["summary"]["tacticalSignals"], 1)

        with patch.dict(server.os.environ, {"X_KOL_PRIORITY_HANDLES": "whitestar224"}):
            empty_result = server.personal_x_monitor_payload(
                {"ok": False, "realtime": True, "items": [], "sources": [], "upstreamMode": "priority-rss-race"}
            )
        self.assertTrue(empty_result["ok"])
        self.assertEqual(empty_result["items"], [])

    def test_global_priority_account_starts_official_stream_at_boot(self):
        with (
            patch.object(server, "x_kol_official_stream_available", return_value=True),
            patch.object(server.threading, "Thread") as thread_class,
        ):
            server.register_x_kol_official_stream_user(None)

        self.assertIn(0, server.X_KOL_OFFICIAL_STREAM_USERS)
        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once()

    def test_global_stream_targets_all_enabled_accounts_when_opted_in(self):
        sources = [
            {"id": "x-alpha", "handle": "alpha", "enabled": True},
            {"id": "x-beta", "handle": "beta", "enabled": True},
            {"id": "x-off", "handle": "disabled", "enabled": False},
        ]
        with server.X_KOL_OFFICIAL_STREAM_LOCK:
            server.X_KOL_OFFICIAL_STREAM_USERS[0] = {}

        with (
            patch.dict(server.os.environ, {"X_KOL_STREAM_ALL_TRACKED_ACCOUNTS": "1"}),
            patch.object(server, "load_x_kol_sources", return_value=sources),
            patch.object(server, "x_kol_priority_sources", return_value=[sources[0]]),
        ):
            targets = server.x_kol_official_stream_targets()

        self.assertEqual(set(targets), {"alpha", "beta"})

    def test_registering_user_with_existing_handles_does_not_restart_stream(self):
        source = {"id": "x-alpha", "handle": "alpha", "enabled": True}
        targets = {"alpha": [({}, source)]}
        with server.X_KOL_OFFICIAL_STREAM_LOCK:
            server.X_KOL_OFFICIAL_STREAM_USERS[0] = {}
            server.X_KOL_OFFICIAL_STREAM_STARTED = True

        with (
            patch.object(server, "x_kol_official_stream_available", return_value=True),
            patch.dict(server.os.environ, {"X_KOL_STREAM_ALL_TRACKED_ACCOUNTS": "1"}),
            patch.object(server, "x_kol_official_stream_targets", return_value=targets),
            patch.object(server, "wake_x_kol_official_stream") as wake,
            patch.object(server.threading, "Thread") as thread_class,
        ):
            server.register_x_kol_official_stream_user({"id": 7, "username": "admin"})

        self.assertIn(7, server.X_KOL_OFFICIAL_STREAM_USERS)
        wake.assert_not_called()
        thread_class.assert_not_called()

    def test_snapshot_immediately_overlays_connected_stream_on_cached_rss_errors(self):
        user = {"id": 808}
        source = {
            "id": "x-alpha",
            "handle": "alpha",
            "enabled": True,
            "status": "error",
            "provider": "rss",
            "error": "上游暂时波动",
        }
        payload = {
            "ok": True,
            "sources": [source],
            "items": [{"id": "post-1", "sourceId": "x-alpha", "publishedAt": 1, "text": "cached"}],
            "enabledCount": 1,
            "provider": "rss",
            "upstreamMode": "rss-resilient",
        }
        with server.X_KOL_REALTIME_CONDITION:
            server.X_KOL_REALTIME_DISK_HYDRATED.add(808)
            server.X_KOL_REALTIME_SNAPSHOTS[808] = {
                "payload": payload,
                "signature": server.x_kol_payload_signature(payload),
            }

        with (
            patch.object(server, "ensure_x_kol_realtime_worker"),
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {
                    "X_KOL_OFFICIAL_API_ENABLED": "1",
                    "X_KOL_STREAM_ALL_TRACKED_ACCOUNTS": "1",
                },
            ),
            patch.object(
                server,
                "x_kol_official_stream_health_snapshot",
                return_value={
                    "connected": True,
                    "rulesReady": True,
                    "targetCount": 1,
                    "lastConnectedAt": int(time.time() * 1000),
                },
            ),
        ):
            result, _ = server.x_kol_realtime_snapshot(user, wait_seconds=0)

        self.assertEqual(result["provider"], "x-stream")
        self.assertEqual(result["upstreamMode"], "official-stream")
        self.assertEqual(result["sources"][0]["status"], "ok")
        self.assertEqual(result["sources"][0]["error"], "")

    def test_connected_official_stream_overrides_failed_rss_source_status(self):
        source = {"id": "x-alpha", "handle": "alpha", "displayName": "Alpha", "enabled": True}
        failed = {
            "source": {**source, "status": "error", "provider": "rss", "error": "上游暂时波动"},
            "items": [],
        }
        with (
            patch.object(server, "load_x_kol_sources", return_value=[source]),
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {
                    "X_KOL_OFFICIAL_API_ENABLED": "1",
                    "X_KOL_STREAM_ALL_TRACKED_ACCOUNTS": "1",
                    "X_KOL_OFFICIAL_REST_POLL_ENABLED": "0",
                },
            ),
            patch.object(server, "x_kol_fetch_rss_source", return_value=failed),
            patch.object(
                server,
                "x_kol_official_stream_health_snapshot",
                return_value={"connected": True, "lastConnectedAt": 1234, "targetCount": 1},
            ),
        ):
            payload = server.x_kol_feed_payload({"id": 101})

        self.assertEqual(payload["provider"], "x-stream")
        self.assertEqual(payload["upstreamMode"], "official-stream")
        self.assertEqual(payload["sources"][0]["status"], "ok")
        self.assertEqual(payload["sources"][0]["provider"], "x-stream")
        self.assertEqual(payload["sources"][0]["error"], "")

    def test_priority_poll_publishes_global_and_admin_snapshots(self):
        payload = {
            "ok": True,
            "sources": [{"id": "x-owner", "handle": "whitestar224"}],
            "items": [{"id": "post-1", "text": "fresh", "publishedAt": 1}],
        }
        administrator = {"id": 7, "username": "admin"}
        with (
            patch.object(server, "x_kol_priority_payload", return_value=payload),
            patch.object(server, "admin_user", return_value=administrator),
            patch.object(server, "publish_x_kol_realtime_payload") as publish,
        ):
            result = server.x_kol_priority_poll_once()

        self.assertIs(result, payload)
        self.assertEqual(publish.call_count, 2)
        expected_age = server.personal_x_monitor_backfill_seconds()
        publish.assert_any_call(None, payload, monitor_max_age_seconds=expected_age)
        publish.assert_any_call(administrator, payload, monitor_max_age_seconds=expected_age)

    def test_priority_monitor_starts_saved_sources_worker_for_admin(self):
        administrator = {"id": 7, "username": "admin"}
        previous_started = server.X_KOL_PRIORITY_STARTED
        server.X_KOL_PRIORITY_STARTED = False
        try:
            with (
                patch.object(server, "admin_user", return_value=administrator),
                patch.object(server, "register_x_kol_official_stream_user"),
                patch.object(server, "ensure_x_kol_realtime_worker") as ensure_worker,
                patch.object(server.threading, "Thread") as thread,
            ):
                server.start_x_kol_priority_monitor()

            thread.assert_called_once()
            thread.return_value.start.assert_called_once_with()
            ensure_worker.assert_called_once_with(administrator)
        finally:
            server.X_KOL_PRIORITY_STARTED = previous_started

    def test_priority_payload_uses_rss_when_token_exists_but_paid_api_is_disabled(self):
        source = {
            "id": "x-owner",
            "handle": "whitestar224",
            "displayName": "Owner",
            "enabled": True,
        }
        fetched = {
            "source": {**source, "provider": "rss", "status": "ok"},
            "items": [{"id": "post-1", "publishedAt": 1, "text": "fresh"}],
        }
        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(server.os.environ, {"X_KOL_OFFICIAL_API_ENABLED": "0"}),
            patch.object(server, "x_kol_priority_sources", return_value=[source]),
            patch.object(server, "x_kol_fetch_api_source") as fetch_api,
            patch.object(server, "x_kol_fetch_priority_rss_source", return_value=fetched) as fetch_rss,
        ):
            payload = server.x_kol_priority_payload()

        fetch_api.assert_not_called()
        fetch_rss.assert_called_once_with(source)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "rss")
        self.assertFalse(payload["paidApiEnabled"])

    def test_priority_payload_passes_bearer_token_only_after_paid_api_opt_in(self):
        source = {
            "id": "x-owner",
            "handle": "whitestar224",
            "displayName": "Owner",
            "enabled": True,
        }
        fetched = {
            "source": {**source, "provider": "x-api", "status": "ok"},
            "items": [{"id": "post-1", "publishedAt": 1, "text": "fresh"}],
        }
        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {
                    "X_KOL_OFFICIAL_API_ENABLED": "1",
                    "X_KOL_OFFICIAL_REST_POLL_ENABLED": "1",
                },
            ),
            patch.object(server, "x_kol_priority_sources", return_value=[source]),
            patch.object(server, "x_kol_fetch_api_source", return_value=fetched) as fetch_api,
            patch.object(server, "x_kol_fetch_priority_rss_source") as fetch_rss,
        ):
            payload = server.x_kol_priority_payload()

        fetch_api.assert_called_once_with(source, "test-token")
        fetch_rss.assert_not_called()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "x-api")
        self.assertTrue(payload["paidApiEnabled"])
        self.assertTrue(payload["paidRestPollingEnabled"])

    def test_live_stream_opt_in_does_not_enable_recent_history_reads(self):
        source = {
            "id": "x-owner",
            "handle": "whitestar224",
            "displayName": "Owner",
            "enabled": True,
        }
        fetched = {
            "source": {**source, "provider": "rss", "status": "ok"},
            "items": [{"id": "post-1", "publishedAt": 1, "text": "cached"}],
        }
        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {
                    "X_KOL_OFFICIAL_API_ENABLED": "1",
                    "X_KOL_FILTERED_STREAM_ENABLED": "1",
                    "X_KOL_OFFICIAL_REST_POLL_ENABLED": "0",
                },
            ),
            patch.object(server, "x_kol_priority_sources", return_value=[source]),
            patch.object(server, "x_kol_fetch_api_source") as fetch_api,
            patch.object(server, "x_kol_fetch_priority_rss_source", return_value=fetched) as fetch_rss,
        ):
            payload = server.x_kol_priority_payload()

        fetch_api.assert_not_called()
        fetch_rss.assert_called_once_with(source)
        self.assertTrue(payload["paidApiEnabled"])
        self.assertFalse(payload["paidRestPollingEnabled"])
        self.assertEqual(payload["provider"], "rss")

    def test_startup_history_recovery_reads_official_api_once_without_enabling_continuous_polling(self):
        source = {
            "id": "x-owner",
            "handle": "whitestar224",
            "displayName": "Owner",
            "enabled": True,
        }
        fetched = {
            "source": {**source, "provider": "x-api", "status": "ok"},
            "items": [{"id": "longxia-post", "publishedAt": 1, "text": "$LONGXIA 重点看"}],
        }
        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {
                    "X_KOL_OFFICIAL_API_ENABLED": "1",
                    "X_KOL_OFFICIAL_REST_POLL_ENABLED": "0",
                    "X_KOL_STARTUP_HISTORY_RECOVERY_ENABLED": "1",
                },
            ),
            patch.object(server, "x_kol_priority_sources", return_value=[source]),
            patch.object(server, "x_kol_fetch_api_source", return_value=fetched) as fetch_api,
            patch.object(server, "x_kol_fetch_priority_rss_source") as fetch_rss,
        ):
            payload = server.x_kol_priority_payload(history_recovery=True)

        fetch_api.assert_called_once_with(source, "test-token")
        fetch_rss.assert_not_called()
        self.assertEqual(payload["provider"], "x-api")
        self.assertTrue(payload["startupHistoryRecovery"])
        self.assertFalse(payload["paidRestPollingEnabled"])
        self.assertEqual(payload["upstreamMode"], "official-api-startup-recovery")

    def test_official_rest_polling_requires_its_own_opt_in(self):
        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {
                    "X_KOL_OFFICIAL_API_ENABLED": "1",
                    "X_KOL_OFFICIAL_REST_POLL_ENABLED": "0",
                },
            ),
        ):
            self.assertFalse(server.x_kol_official_rest_poll_enabled())

        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {
                    "X_KOL_OFFICIAL_API_ENABLED": "1",
                    "X_KOL_OFFICIAL_REST_POLL_ENABLED": "1",
                },
            ),
        ):
            self.assertTrue(server.x_kol_official_rest_poll_enabled())

    def test_official_stream_requires_both_paid_api_and_stream_opt_in(self):
        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {"X_KOL_OFFICIAL_API_ENABLED": "0", "X_KOL_FILTERED_STREAM_ENABLED": "1"},
            ),
        ):
            self.assertFalse(server.x_kol_official_stream_available())

        with (
            patch.object(server, "x_kol_token", return_value="test-token"),
            patch.dict(
                server.os.environ,
                {"X_KOL_OFFICIAL_API_ENABLED": "1", "X_KOL_FILTERED_STREAM_ENABLED": "1"},
            ),
        ):
            self.assertTrue(server.x_kol_official_stream_available())

    def test_logged_in_user_is_not_registered_on_paid_stream_by_default(self):
        with (
            patch.object(server, "x_kol_official_stream_available", return_value=True),
            patch.dict(server.os.environ, {"X_KOL_STREAM_ALL_TRACKED_ACCOUNTS": "0"}),
            patch.object(server.threading, "Thread") as thread_class,
        ):
            server.register_x_kol_official_stream_user({"id": 101})

        self.assertNotIn(101, server.X_KOL_OFFICIAL_STREAM_USERS)
        thread_class.assert_not_called()

    def test_x_source_preserves_each_supported_monitor_category(self):
        expected = {
            "普通KOL": "kol",
            "明星": "celebrity",
            "名人": "notable",
            "项目创始人/联合创始人": "founder",
            "项目官方X": "project_official",
        }
        for label, category_id in expected.items():
            with self.subTest(label=label):
                source = server.normalize_x_source({
                    "handle": f"category_{category_id}",
                    "displayName": label,
                    "category": label,
                })
                self.assertEqual(source["category"], category_id)

    def test_existing_x_sources_receive_identity_based_categories(self):
        cases = {
            "RobinhoodApp": "project_official",
            "Natan_benish": "founder",
            "elonmusk": "notable",
            "alpha_pls": "kol",
        }
        for handle, expected in cases.items():
            with self.subTest(handle=handle):
                source = server.normalize_x_source({"handle": handle, "displayName": handle})
                self.assertEqual(source["category"], expected)


if __name__ == "__main__":
    unittest.main()
