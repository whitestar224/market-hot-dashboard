"""Regression tests for GMGN's timestamp tolerance.

GMGN's OpenAPI rejects any read-only request whose ``timestamp`` query
parameter drifts past its own tolerance with ``401 AUTH_TIMESTAMP_EXPIRED``.
A host clock that is only ~18 seconds off therefore takes the whole GMGN
pipeline down while the API key stays perfectly valid.  These tests pin the
calibrated-skew behaviour that keeps the source alive.
"""
import os
import time
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch

import requests

import gmgn_agentic


def completed_payload():
    return {"code": 0, "data": {"completed": [{"address": "S" * 43, "symbol": "SKEW"}]}}


def expired_timestamp_response():
    response = Mock(status_code=401)
    response.raise_for_status.side_effect = requests.HTTPError("401 Client Error: Unauthorized")
    response.json.return_value = {
        "code": 401,
        "error": "AUTH_TIMESTAMP_EXPIRED",
        "message": "timestamp expired",
    }
    return response


def ok_response():
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = completed_payload()
    return response


class GmgnClockSkewTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "GMGN_READONLY_PERSIST_CACHE": "0",
            "GMGN_PERSIST_RATE_STATE": "0",
            "GMGN_READONLY_KEY_MODE": "public",
            "GMGN_PROXY_URL": "",
            "GMGN_READONLY_MIN_INTERVAL_SECONDS": "1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        gmgn_agentic.reset_gmgn_runtime_state()

    def unsync_clock(self):
        """Pretend the skew was never measured so calibration must run."""
        gmgn_agentic._GMGN_CLOCK_OFFSET = 0.0
        gmgn_agentic._GMGN_CLOCK_SYNCED_AT = 0.0

    def pin_clock(self, offset):
        """Pretend the skew was just measured as ``offset`` seconds."""
        gmgn_agentic._GMGN_CLOCK_OFFSET = float(offset)
        gmgn_agentic._GMGN_CLOCK_SYNCED_AT = time.time()

    # --- detector ---------------------------------------------------------
    def test_clock_error_detector_matches_gmgn_payload(self):
        self.assertTrue(gmgn_agentic._gmgn_clock_error(
            {"code": 401, "error": "AUTH_TIMESTAMP_EXPIRED", "message": "timestamp expired"}
        ))
        self.assertTrue(gmgn_agentic._gmgn_clock_error({"message": "Timestamp Invalid"}))
        self.assertTrue(gmgn_agentic._gmgn_clock_error({"reason": "timestamp mismatch"}))
        self.assertFalse(gmgn_agentic._gmgn_clock_error({"error": "AUTH_INVALID_KEY"}))
        self.assertFalse(gmgn_agentic._gmgn_clock_error(None))
        self.assertFalse(gmgn_agentic._gmgn_clock_error("plain string"))

    # --- calibration ------------------------------------------------------
    def test_clock_offset_is_measured_from_gmgn_date_header(self):
        server_now = time.time() + 18.0
        response = Mock()
        response.headers = {
            "Date": format_datetime(datetime.fromtimestamp(server_now, tz=timezone.utc), usegmt=True)
        }
        session = Mock()
        session.get.return_value = response

        offset = gmgn_agentic._measure_gmgn_clock_offset(session)

        # The Date header is second-granular, so allow a generous tolerance.
        self.assertGreater(offset, 17.0)
        self.assertLess(offset, 19.5)

    def test_clock_offset_survives_a_missing_date_header(self):
        response = Mock()
        response.headers = {}
        session = Mock()
        session.get.return_value = response

        with self.assertRaises(RuntimeError):
            gmgn_agentic._measure_gmgn_clock_offset(session)

    def test_calibration_is_cached_between_requests(self):
        self.unsync_clock()
        session = Mock()
        with patch("gmgn_agentic._measure_gmgn_clock_offset", return_value=17.5) as measure:
            first = gmgn_agentic._gmgn_clock_offset(session)
            second = gmgn_agentic._gmgn_clock_offset(session)

        self.assertEqual(first, 17.5)
        self.assertEqual(second, 17.5)
        self.assertEqual(measure.call_count, 1)

    def test_forced_calibration_refreshes_the_cached_offset(self):
        session = Mock()
        with patch("gmgn_agentic._measure_gmgn_clock_offset", side_effect=[17.5, 0.0]) as measure:
            self.assertEqual(gmgn_agentic._gmgn_clock_offset(session, force=True), 17.5)
            self.assertEqual(gmgn_agentic._gmgn_clock_offset(session, force=True), 0.0)
        self.assertEqual(measure.call_count, 2)

    def test_failed_calibration_keeps_the_last_known_offset(self):
        session = Mock()
        with patch("gmgn_agentic._measure_gmgn_clock_offset", side_effect=[17.5, RuntimeError("boom")]):
            self.assertEqual(gmgn_agentic._gmgn_clock_offset(session, force=True), 17.5)
            self.assertEqual(gmgn_agentic._gmgn_clock_offset(session, force=True), 17.5)

    # --- request path -----------------------------------------------------
    def test_expired_timestamp_is_replayed_once_with_a_calibrated_stamp(self):
        session = Mock()
        session.post.side_effect = [expired_timestamp_response(), ok_response()]

        with patch("gmgn_agentic._wait_for_readonly_slot"), patch(
            "gmgn_agentic._measure_gmgn_clock_offset", return_value=17.6
        ) as measure:
            result = gmgn_agentic.gmgn_readonly_post(
                "/v1/trenches",
                cache_key="clock-retry",
                body={"version": "v2", "completed": {"limit": 1}},
                params={"chain": "sol"},
                cache_ttl_seconds=1,
                session=session,
            )

        self.assertEqual(result["code"], 0)
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(measure.call_count, 1)
        self.assertEqual(result["_gmgnMeta"]["clockOffsetSeconds"], 17.6)

        first_stamp = session.post.call_args_list[0].kwargs["params"]["timestamp"]
        second_stamp = session.post.call_args_list[1].kwargs["params"]["timestamp"]
        self.assertGreaterEqual(second_stamp - first_stamp, 17)
        self.assertLessEqual(second_stamp - first_stamp, 19)

    def test_expired_timestamp_is_replayed_at_most_once(self):
        session = Mock()
        session.post.side_effect = [
            expired_timestamp_response(),
            expired_timestamp_response(),
            ok_response(),
        ]

        with patch("gmgn_agentic._wait_for_readonly_slot"), patch(
            "gmgn_agentic._measure_gmgn_clock_offset", return_value=17.6
        ):
            with self.assertRaises(requests.HTTPError):
                gmgn_agentic.gmgn_readonly_post(
                    "/v1/trenches",
                    cache_key="clock-retry-cap",
                    body={"version": "v2", "completed": {"limit": 1}},
                    params={"chain": "sol"},
                    cache_ttl_seconds=1,
                    session=session,
                )

        self.assertEqual(session.post.call_count, 2)

    def test_healthy_request_uses_the_calibrated_stamp_directly(self):
        self.pin_clock(17.6)
        session = Mock()
        session.post.return_value = ok_response()

        before = int(time.time())
        with patch("gmgn_agentic._wait_for_readonly_slot"), patch(
            "gmgn_agentic._measure_gmgn_clock_offset"
        ) as measure:
            gmgn_agentic.gmgn_readonly_post(
                "/v1/trenches",
                cache_key="clock-healthy",
                body={"version": "v2", "completed": {"limit": 1}},
                params={"chain": "sol"},
                cache_ttl_seconds=1,
                session=session,
            )
        after = int(time.time())

        self.assertEqual(measure.call_count, 0)
        self.assertEqual(session.post.call_count, 1)
        stamp = session.post.call_args.kwargs["params"]["timestamp"]
        self.assertGreaterEqual(stamp, before + 17)
        self.assertLessEqual(stamp, after + 18)

    def test_get_route_also_carries_the_calibrated_stamp(self):
        self.pin_clock(17.6)
        session = Mock()
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "data": {"rank": []}}
        session.get.return_value = response

        before = int(time.time())
        with patch("gmgn_agentic._wait_for_readonly_slot"):
            gmgn_agentic.gmgn_readonly_get(
                "/v1/market/rank",
                cache_key="clock-get",
                params={"chain": "sol"},
                cache_ttl_seconds=1,
                session=session,
            )

        stamp = session.get.call_args.kwargs["params"]["timestamp"]
        self.assertGreaterEqual(stamp, before + 17)

    def test_reset_marks_the_clock_as_synced_without_a_round_trip(self):
        session = Mock()
        session.post.return_value = ok_response()

        with patch("gmgn_agentic._wait_for_readonly_slot"), patch(
            "gmgn_agentic._measure_gmgn_clock_offset"
        ) as measure:
            gmgn_agentic.gmgn_readonly_post(
                "/v1/trenches",
                cache_key="clock-reset",
                body={"version": "v2", "completed": {"limit": 1}},
                params={"chain": "sol"},
                cache_ttl_seconds=1,
                session=session,
            )

        self.assertEqual(measure.call_count, 0)
        self.assertEqual(gmgn_agentic.gmgn_clock_status()["clockOffsetSeconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
