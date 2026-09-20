import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.content = b"{}"
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class XApiCostControlTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "x-cost.json"
        self.path_patch = patch.object(server, "X_KOL_API_COST_STATE_PATH", self.state_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def write_state(self, payload):
        self.state_path.write_text(json.dumps(payload), encoding="utf-8")

    def base_state(self):
        return {
            "date": server.x_kol_api_cost_day(),
            "requestCount": 0,
            "postReads": 0,
            "userReads": 0,
            "profileLookupAttempts": 0,
            "seenPostIds": [],
            "endpoints": {},
            "profiles": {},
            "sinceIds": {},
        }

    def test_history_recovery_fetches_only_five_new_posts_and_drops_quote_posts(self):
        state = self.base_state()
        state["profiles"] = {
            "whitestar224": {
                "id": "42",
                "name": "White Star",
                "username": "whitestar224",
                "tweetCount": 3000,
                "refreshedDate": "2026-09-08",
            }
        }
        state["sinceIds"] = {"whitestar224": "100"}
        self.write_state(state)
        profile_response = FakeResponse(
            {
                "data": {
                    "id": "42",
                    "name": "White Star",
                    "username": "whitestar224",
                    "profile_image_url": "https://example.com/avatar.jpg",
                    "public_metrics": {"tweet_count": 3005},
                }
            }
        )
        timeline_response = FakeResponse(
            {
                "data": [
                    {"id": "105", "text": "new 5", "created_at": "2026-09-09T01:05:00Z"},
                    {
                        "id": "104",
                        "text": "quoted post",
                        "created_at": "2026-09-09T01:04:00Z",
                        "referenced_tweets": [{"type": "quoted", "id": "9"}],
                    },
                    {"id": "103", "text": "new 3", "created_at": "2026-09-09T01:03:00Z"},
                    {"id": "102", "text": "new 2", "created_at": "2026-09-09T01:02:00Z"},
                    {"id": "101", "text": "new 1", "created_at": "2026-09-09T01:01:00Z"},
                ]
            }
        )
        source = {"id": "x-owner", "handle": "whitestar224", "displayName": "White Star", "enabled": True}

        with (
            patch.object(server, "X_OFFICIAL_API_USER_APPROVED", True),
            patch.dict(server.os.environ, {"X_KOL_PRIORITY_HANDLES": "whitestar224"}),
            patch.object(server.requests, "get", side_effect=[profile_response, timeline_response]) as request,
        ):
            payload = server.x_kol_fetch_api_source(source, "token", history_recovery=True)

        self.assertEqual(request.call_count, 2)
        profile_params = request.call_args_list[0].kwargs["params"]
        timeline_params = request.call_args_list[1].kwargs["params"]
        self.assertIn("public_metrics", profile_params["user.fields"])
        self.assertEqual(timeline_params["max_results"], "5")
        self.assertEqual(timeline_params["since_id"], "100")
        self.assertNotIn("expansions", timeline_params)
        self.assertEqual(payload["source"]["historyDelta"], 5)
        self.assertEqual(payload["source"]["resourcesReturned"], 5)
        self.assertEqual([row["tweetId"] for row in payload["items"]], ["105", "103", "102", "101"])
        self.assertTrue(payload["source"]["quotedPostsExcluded"])

        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["profiles"]["whitestar224"]["tweetCount"], 3005)
        self.assertEqual(saved["sinceIds"]["whitestar224"], "105")
        self.assertEqual(saved["postReads"], 5)
        self.assertEqual(saved["userReads"], 1)
        self.assertEqual(saved["requestCount"], 2)
        self.assertAlmostEqual(server.x_kol_api_cost_snapshot()["estimatedUsd"], 0.035)

    def test_same_count_skips_timeline_request(self):
        state = self.base_state()
        state["profiles"] = {
            "whitestar224": {
                "id": "42",
                "name": "White Star",
                "username": "whitestar224",
                "tweetCount": 3005,
                "remoteTweetCount": 3005,
                "refreshedDate": server.x_kol_api_cost_day(),
            }
        }
        self.write_state(state)
        source = {"id": "x-owner", "handle": "whitestar224", "displayName": "White Star", "enabled": True}

        with (
            patch.object(server, "X_OFFICIAL_API_USER_APPROVED", True),
            patch.dict(server.os.environ, {"X_KOL_PRIORITY_HANDLES": "whitestar224"}),
            patch.object(server.requests, "get") as request,
        ):
            payload = server.x_kol_fetch_api_source(source, "token", history_recovery=True)

        request.assert_not_called()
        self.assertEqual(payload["source"]["historyDelta"], 0)
        self.assertEqual(payload["items"], [])

    def test_non_owner_source_is_never_sent_to_paid_api(self):
        with (
            patch.object(server, "X_OFFICIAL_API_USER_APPROVED", True),
            patch.dict(server.os.environ, {"X_KOL_PRIORITY_HANDLES": "whitestar224"}),
            patch.object(server.requests, "get") as request,
        ):
            with self.assertRaisesRegex(RuntimeError, "仅允许读取个人账号"):
                server.x_kol_fetch_api_source({"id": "x-other", "handle": "other"}, "token")
        request.assert_not_called()

    def test_paid_scope_stays_pinned_to_approved_owner_if_priority_order_changes(self):
        with patch.dict(
            server.os.environ,
            {"X_KOL_PRIORITY_HANDLES": "other,whitestar224"},
        ):
            self.assertEqual(server.x_kol_personal_api_handle(), "whitestar224")
            self.assertTrue(server.x_kol_official_paid_source_allowed({"handle": "whitestar224"}))
            self.assertFalse(server.x_kol_official_paid_source_allowed({"handle": "other"}))

    def test_user_approval_enables_only_owner_live_stream(self):
        self.assertTrue(server.X_OFFICIAL_API_USER_APPROVED)
        self.assertFalse(server.X_OFFICIAL_API_REST_POLL_USER_APPROVED)
        self.assertFalse(server.X_OFFICIAL_API_HISTORY_USER_APPROVED)
        self.assertFalse(server.X_OFFICIAL_API_ALL_ACCOUNTS_USER_APPROVED)

    def test_paid_stream_rule_excludes_quote_posts_upstream(self):
        with patch.dict(server.os.environ, {"X_KOL_INCLUDE_REPLIES": "1", "X_KOL_INCLUDE_RETWEETS": "1"}):
            rule = server.x_kol_official_rule_value("whitestar224")
        self.assertEqual(rule, "from:whitestar224 -is:quote")

    def test_daily_budget_includes_profile_read_and_hard_stops_stream(self):
        state = self.base_state()
        state["postReads"] = 18
        state["userReads"] = 1
        state["seenPostIds"] = [str(value) for value in range(18)]
        self.write_state(state)

        snapshot = server.x_kol_api_cost_snapshot()
        claimed, after = server.x_kol_api_cost_claim_request(
            "filtered-stream-connect",
            requires_post_capacity=True,
        )

        self.assertAlmostEqual(snapshot["estimatedUsd"], 0.10)
        self.assertEqual(snapshot["remainingPostReads"], 0)
        self.assertTrue(snapshot["blocked"])
        self.assertFalse(claimed)
        self.assertEqual(after["requestCount"], 0)


if __name__ == "__main__":
    unittest.main()
