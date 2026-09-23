import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import gmgn_agentic
from chain_ecosystem_monitor import ONCHAIN_RESEARCH_DEFAULT_NETWORKS, scan_onchain_research
from gmgn_agentic import (
    GmgnRateLimitError,
    GMGN_PUBLIC_READONLY_API_KEY,
    fetch_gmgn_migrated_trenches,
    fetch_gmgn_recent_market_rank,
    gmgn_trench_passes_chain_filters,
    normalize_gmgn_migrated_trenches,
)


NOW = 1_789_200_000_000
BSC_TOKEN = "0x" + "b" * 40
ARC_TIGRINO = "0xBc57a14bD86572b2181A71ef75139E7432aCa846"


def trenches_payload():
    return {
        "code": 0,
        "data": {
            "completed": [{
                "address": BSC_TOKEN,
                "symbol": "DONE",
                "name": "Migrated Token",
                "logo": "https://cdn.example.test/done.png",
                "launchpad_platform": "fourmeme",
                "exchange": "pancakeswap",
                "created_timestamp": NOW // 1000 - 600,
                "open_timestamp": NOW // 1000 - 120,
                "price": "0.002",
                "usd_market_cap": "200000",
                "liquidity": "45000",
                "volume_1h": "24000",
                "volume_24h": "100000",
                "swaps_1h": 120,
                "swaps_24h": 600,
                "buys_24h": 380,
                "sells_24h": 220,
                "holder_count": 850,
                "top_10_holder_rate": 0.32,
                "smart_degen_count": 4,
                "renowned_count": 2,
                "bundler_trader_amount_rate": 0.03,
                "creator_balance_rate": 0.025,
                "creator_token_status": "creator_hold",
                "sniper_count": 7,
                "top70_sniper_hold_rate": 0.06,
                "fresh_wallet_rate": 0.12,
                "bluechip_owner_percentage": 0.02,
                "buy_tax": 0.01,
                "sell_tax": 0.02,
                "total_fee": 5300,
                "dexscr_ad": 1,
                "dexscr_boost_fee": 500,
                "x_user_follower": 8800,
                "rug_ratio": 0.1,
                "is_og": 1,
                "is_wash_trading": False,
                "twitter": "https://x.com/migrated_token/status/1234567890",
                "twitter_handle": "migrated_token",
                "twitter_is_tweet": True,
                "tweet_publish_time": 1_789_858_717_606,
                "ai_narrative": {"cn": "GMGN 原生 AI 叙事样例。"},
            }],
            "new_creation": [{"address": "0x" + "c" * 40}],
        },
    }


def arc_rank_payload(*, launchpad_status="1"):
    return {
        "code": 0,
        "data": {
            "rank": [{
                "address": ARC_TIGRINO,
                "chain": "arc",
                "symbol": "TIGRINO",
                "name": "Leopardus Tilcayo",
                "logo": "https://cdn.example.test/tigrino.png",
                "creation_timestamp": NOW // 1000 - 3600,
                "open_timestamp": NOW // 1000 - 3000,
                "launchpad_status": launchpad_status,
                "launchpad_platform": "argus",
                "is_og": 1,
                "image_dup": "0",
                "is_wash_trading": False,
                "is_rat_trading": None,
                "rat_trader_amount_rate": 0,
                "is_honeypot": 0,
                "is_open_source": 1,
                "is_renounced": 1,
                "burn_status": "yes",
                "market_cap": 31200,
                "liquidity": 7800,
                "volume": 565400,
                "swaps": 16500,
                "buys": 9200,
                "sells": 7300,
                "holder_count": 1507,
                "bundler_rate": 0.17,
                "twitter_username": "Tigrino_arc",
            }],
        },
    }


class RecordingStore:
    def __init__(self):
        self.calls = []

    def save_onchain_research_scan(self, rows, **metadata):
        self.calls.append((list(rows), dict(metadata)))


class GmgnAgenticTests(unittest.TestCase):
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

    def test_fetch_requests_completed_only_and_never_uses_personal_key(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = trenches_payload()
        session = Mock()
        session.post.return_value = response

        with patch.dict(os.environ, {"GMGN_API_KEY": "personal-paid-key"}), patch(
            "gmgn_agentic._wait_for_readonly_slot"
        ):
            result = fetch_gmgn_migrated_trenches("bsc", limit=12, session=session)

        self.assertEqual(result["code"], 0)
        request = session.post.call_args
        self.assertEqual(request.kwargs["headers"]["X-APIKEY"], GMGN_PUBLIC_READONLY_API_KEY)
        self.assertFalse(result["_gmgnMeta"]["personalKeyUsed"])
        self.assertEqual(set(request.kwargs["json"]), {"version", "completed"})
        self.assertEqual(request.kwargs["json"]["completed"]["limit"], 12)
        self.assertIn("has_social", request.kwargs["json"]["completed"]["filters"])
        self.assertNotIn("is_og", request.kwargs["json"]["completed"]["filters"])

    def test_robinhood_request_does_not_pin_gmgn_quote_type_ids(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = trenches_payload()
        session = Mock()
        session.post.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            fetch_gmgn_migrated_trenches("robinhood", session=session)

        completed = session.post.call_args.kwargs["json"]["completed"]
        self.assertNotIn("quote_address_type", completed)

    def test_recent_market_rank_supplements_all_opened_tokens_before_local_filters(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": 0,
            "data": {
                "rank": [
                    {
                        "address": "0x" + "1" * 40,
                        "symbol": "OGNEW",
                        "is_og": 1,
                        "launchpad_status": 1,
                        "open_timestamp": NOW // 1000 - 120,
                        "market_cap": 25_000,
                        "twitter_username": "ognew",
                    },
                    {
                        "address": "0x" + "2" * 40,
                        "symbol": "NONOG",
                        "is_og": 0,
                        "launchpad_status": 1,
                        "open_timestamp": NOW // 1000 - 180,
                        "market_cap": 30_000,
                        "website": "https://nonog.example",
                    },
                    {
                        "address": "0x" + "3" * 40,
                        "symbol": "NOTOPEN",
                        "launchpad_status": 0,
                        "creation_timestamp": NOW // 1000 - 60,
                        "market_cap": 40_000,
                    },
                ],
            },
        }
        session = Mock()
        session.get.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            payload = fetch_gmgn_recent_market_rank("bsc", session=session)

        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["order_by"], "creation_timestamp")
        self.assertEqual(params["direction"], "desc")
        self.assertEqual(params["min_marketcap"], 10_000)
        self.assertEqual(params["filters"], ["has_social"])
        self.assertEqual(
            [row["symbol"] for row in payload["data"]["completed"]],
            ["OGNEW", "NONOG"],
        )
        self.assertEqual(payload["_gmgnMeta"]["sourceRoute"], "market-rank-recent-opened")

    def test_recent_market_rank_forwards_age_window_for_busy_chain_backfill(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "data": {"rank": []}}
        session = Mock()
        session.get.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            fetch_gmgn_recent_market_rank(
                "solana",
                min_created="2h",
                max_created="6h",
                session=session,
            )

        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["min_created"], "2h")
        self.assertEqual(params["max_created"], "6h")

    def test_arc_opened_tape_uses_official_rank_without_hardcoded_allowlists(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "data": arc_rank_payload()}
        session = Mock()
        session.get.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            payload = fetch_gmgn_migrated_trenches("arc", session=session)

        session.post.assert_not_called()
        request = session.get.call_args
        self.assertTrue(request.args[0].endswith("/v1/market/rank"))
        self.assertNotIn("json", request.kwargs)
        params = request.kwargs["params"]
        self.assertEqual(params["chain"], "arc")
        self.assertEqual(params["order_by"], "creation_timestamp")
        self.assertEqual(params["direction"], "desc")
        self.assertNotIn("quote_address_type", params)
        self.assertNotIn("launchpad_platform", params)
        self.assertIn("has_social", params["filters"])
        self.assertNotIn("is_og", params["filters"])
        self.assertNotIn("not_wash_trading", params["filters"])

        rows = normalize_gmgn_migrated_trenches(payload, "arc", observed_at=NOW)
        self.assertEqual([row["contractAddress"] for row in rows], [ARC_TIGRINO])
        self.assertEqual(rows[0]["metrics"]["volumeH24Usd"], 565400)
        self.assertEqual(rows[0]["metrics"]["transactionsH24"], 16500)
        self.assertTrue(gmgn_trench_passes_chain_filters(rows[0]))
        self.assertEqual(payload["_gmgnMeta"]["sourceRoute"], "market-rank")

    def test_arc_rank_keeps_only_opened_tokens(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "data": arc_rank_payload(launchpad_status=0)}
        session = Mock()
        session.get.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            payload = fetch_gmgn_migrated_trenches("arc", session=session)

        self.assertEqual(payload["data"]["completed"], [])

    def test_arc_rank_drops_unknown_age_rows_instead_of_stamping_poll_time(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        payload = arc_rank_payload()
        unknown_age = dict(payload["data"]["rank"][0])
        unknown_age.update({
            "address": "0x" + "9" * 40,
            "symbol": "OLDARC",
            "creation_timestamp": 0,
            "open_timestamp": 0,
            "created_timestamp": 0,
        })
        payload["data"]["rank"].append(unknown_age)
        response.json.return_value = payload
        session = Mock()
        session.get.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            result = fetch_gmgn_migrated_trenches("arc", session=session)

        self.assertEqual([row["symbol"] for row in result["data"]["completed"]], ["TIGRINO"])

    def test_rate_limit_stops_followup_requests_during_cooldown(self):
        response = Mock()
        response.status_code = 429
        response.headers = {"Retry-After": "90"}
        response.json.return_value = {"message": "too many requests"}
        session = Mock()
        session.post.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot", wraps=gmgn_agentic._wait_for_readonly_slot):
            with self.assertRaises(GmgnRateLimitError) as first:
                fetch_gmgn_migrated_trenches("bsc", session=session)
            with self.assertRaises(GmgnRateLimitError):
                fetch_gmgn_migrated_trenches("base", session=session)

        self.assertEqual(first.exception.retry_after_seconds, 90)
        self.assertEqual(session.post.call_count, 1)

    def test_short_memory_cache_avoids_duplicate_api_calls(self):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = trenches_payload()
        session = Mock()
        session.post.return_value = response

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            fetch_gmgn_migrated_trenches("bsc", session=session)
            fetch_gmgn_migrated_trenches("bsc", session=session)

        self.assertEqual(session.post.call_count, 1)

    def test_concurrent_identical_requests_collapse_to_one_upstream_call(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = trenches_payload()
        session = Mock()

        def slow_post(*_args, **_kwargs):
            time.sleep(0.03)
            return response

        session.post.side_effect = slow_post
        with patch("gmgn_agentic._wait_for_readonly_slot"):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(
                    lambda _index: fetch_gmgn_migrated_trenches("bsc", session=session),
                    range(8),
                ))

        self.assertTrue(all(result["code"] == 0 for result in results))
        self.assertEqual(session.post.call_count, 1)

    def test_trenches_do_not_serve_expired_cache_after_rate_limit(self):
        success = Mock(status_code=200)
        success.raise_for_status.return_value = None
        success.json.return_value = trenches_payload()
        limited = Mock(status_code=429)
        limited.headers = {"Retry-After": "90"}
        limited.json.return_value = {"code": 429, "message": "too many requests"}
        session = Mock()
        session.post.side_effect = [success, limited]

        with patch("gmgn_agentic._wait_for_readonly_slot"):
            fetch_gmgn_migrated_trenches("bsc", session=session)
            cache_key = "trenches:bsc:80"
            updated_at, value = gmgn_agentic._GMGN_RESPONSE_CACHE[cache_key]
            # Stay expired even when importing the full server test suite loads
            # a larger cache TTL from the local .env file.
            gmgn_agentic._GMGN_RESPONSE_CACHE[cache_key] = (updated_at - 3600, value)
            with self.assertRaises(GmgnRateLimitError):
                fetch_gmgn_migrated_trenches("bsc", session=session)
            with self.assertRaises(GmgnRateLimitError):
                fetch_gmgn_migrated_trenches("base", session=session)

        self.assertEqual(session.post.call_count, 2)

    def test_personal_key_requires_explicit_mode(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = trenches_payload()
        session = Mock()
        session.post.return_value = response

        with patch.dict(os.environ, {
            "GMGN_READONLY_KEY_MODE": "personal",
            "GMGN_API_KEY": "personal-paid-key",
        }), patch("gmgn_agentic._wait_for_readonly_slot"):
            result = fetch_gmgn_migrated_trenches("bsc", session=session)

        self.assertEqual(session.post.call_args.kwargs["headers"]["X-APIKEY"], "personal-paid-key")
        self.assertTrue(result["_gmgnMeta"]["personalKeyUsed"])
        self.assertEqual(result["_gmgnMeta"]["authMode"], "personal")

    def test_dedicated_proxy_is_applied_without_exposing_personal_key(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = trenches_payload()
        session = Mock()
        session.post.return_value = response

        with patch.dict(os.environ, {"GMGN_PROXY_URL": "http://user:pass@203.0.113.8:8443"}), patch(
            "gmgn_agentic._wait_for_readonly_slot"
        ):
            result = fetch_gmgn_migrated_trenches("bsc", session=session)

        request = session.post.call_args
        self.assertEqual(request.kwargs["proxies"]["https"], "http://user:pass@203.0.113.8:8443")
        self.assertEqual(request.kwargs["headers"]["X-APIKEY"], GMGN_PUBLIC_READONLY_API_KEY)
        self.assertTrue(result["_gmgnMeta"]["dedicatedEgressConfigured"])

    def test_normalizer_ignores_non_completed_categories(self):
        rows = normalize_gmgn_migrated_trenches(trenches_payload(), "bsc", observed_at=NOW)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["contractAddress"], BSC_TOKEN)
        self.assertEqual(row["provider"], "gmgn-trenches")
        self.assertEqual(row["launchStage"], "migrated")
        self.assertEqual(row["metrics"]["transactionsH1"], 120)
        self.assertEqual(row["launchFacts"]["top10Percent"], 32)
        self.assertEqual(row["launchFacts"]["smartMoneyHolders"], 4)
        self.assertEqual(row["imageUrl"], "https://cdn.example.test/done.png")
        self.assertEqual(row["launchFacts"]["creatorHoldingPercent"], 2.5)
        self.assertEqual(row["launchFacts"]["sniperHoldingPercent"], 6)
        self.assertEqual(row["launchFacts"]["totalFeeUsd"], 5300)
        self.assertTrue(row["launchFacts"]["dexAd"])
        self.assertEqual(row["launchFacts"]["xFollowers"], 8800)
        self.assertEqual(row["gmgnNarrative"], "GMGN 原生 AI 叙事样例。")
        self.assertEqual(row["gmgnNarrativeSource"], "GMGN AI")
        self.assertTrue(row["xOriginal"]["isTweet"])
        self.assertEqual(row["xOriginal"]["statusId"], "1234567890")
        self.assertEqual(row["xOriginal"]["handle"], "migrated_token")
        self.assertEqual(row["xOriginal"]["publishedAt"], 1_789_858_717_606)
        self.assertEqual(
            row["narrativeContext"]["links"]["twitter"],
            "https://x.com/migrated_token/status/1234567890",
        )

    def test_image_duplicate_aliases_and_same_batch_artwork_are_filtered(self):
        payload = {
            "code": 0,
            "data": {
                "completed": [
                    {
                        "address": "0x" + "1" * 40,
                        "symbol": "SENDR",
                        "logo": "https://cdn.example.test/sendr.png?v=1",
                        "twitter": "https://x.com/sendr",
                        "is_og": 1,
                        "usd_market_cap": 50_000,
                        "image_duplicate_count": 2,
                    },
                    {
                        "address": "0x" + "2" * 40,
                        "symbol": "SEN",
                        # A cache-buster must not make identical artwork pass.
                        "logo": "https://cdn.example.test/sendr.png?v=2",
                        "twitter": "https://x.com/sen",
                        "is_og": 1,
                        "usd_market_cap": 50_000,
                    },
                    {
                        "address": "0x" + "3" * 40,
                        "symbol": "UNIQUE",
                        "logo": "https://cdn.example.test/unique.png",
                        "twitter": "https://x.com/unique",
                        "is_og": 1,
                        "usd_market_cap": 50_000,
                        "dup_image": 1,
                    },
                ],
            },
        }

        rows = normalize_gmgn_migrated_trenches(payload, "solana", observed_at=NOW)

        self.assertEqual([row["filterSignals"]["imageDuplicateCount"] for row in rows], [2, 2, 1])
        self.assertTrue(gmgn_trench_passes_chain_filters(rows[0]))
        self.assertTrue(gmgn_trench_passes_chain_filters(rows[1]))
        # A small same-avatar family is allowed; the fourth occurrence is
        # still filtered to prevent a clone flood.
        self.assertFalse(gmgn_trench_passes_chain_filters({
            **rows[0],
            "filterSignals": {**rows[0]["filterSignals"], "imageDuplicateCount": 4},
        }))
        self.assertTrue(gmgn_trench_passes_chain_filters(rows[2]))
        self.assertIn("图片重复 2", rows[1]["filterWarnings"])

    def test_og_status_does_not_affect_the_saved_market_cap_and_social_filter(self):
        def token(address, symbol, *, is_og, market_cap):
            return {
                "address": address,
                "symbol": symbol,
                "logo": f"https://cdn.example.test/{symbol}.png",
                "twitter": f"https://x.com/{symbol.lower()}",
                "is_og": is_og,
                "usd_market_cap": market_cap,
            }

        payload = {"code": 0, "data": {"completed": [
            token("0x" + "4" * 40, "OGBASE", is_og=1, market_cap=50_000),
            token("0x" + "5" * 40, "NONOG", is_og=0, market_cap=50_000),
            token("0x" + "6" * 40, "TOOSMALL", is_og=0, market_cap=10_000),
        ]}}

        rows = normalize_gmgn_migrated_trenches(payload, "bsc", observed_at=NOW)

        self.assertTrue(gmgn_trench_passes_chain_filters(next(row for row in rows if row["symbol"] == "OGBASE")))
        self.assertTrue(gmgn_trench_passes_chain_filters(next(row for row in rows if row["symbol"] == "NONOG")))
        self.assertFalse(gmgn_trench_passes_chain_filters(next(row for row in rows if row["symbol"] == "TOOSMALL")))

    def test_six_chain_trench_profiles_match_saved_gmgn_filters(self):
        def row(network, **overrides):
            signals = {
                "profileVersion": gmgn_agentic.GMGN_TRENCH_FILTER_PROFILE_VERSION,
                "isOg": True,
                "imageDuplicateCount": 0,
                "washTrading": False,
                "ratTraderRate": 0,
                "honeypot": False,
                "openSource": True,
                "ownerRenounced": True,
                "burnStatus": "burn",
                "creationTool": "",
                "socialCount": 1,
            }
            signals.update(overrides)
            return {
                "network": network,
                "launchpad": "native",
                "dexId": "dex",
                "filterSignals": signals,
                "metrics": {"marketCapUsd": 50_000},
            }

        for network in ("solana", "bsc", "robinhood", "base", "eth", "arc"):
            self.assertTrue(gmgn_trench_passes_chain_filters(row(network)), network)

        self.assertTrue(gmgn_trench_passes_chain_filters(row("solana", imageDuplicateCount=2)))
        self.assertFalse(gmgn_trench_passes_chain_filters(row("solana", imageDuplicateCount=4)))
        self.assertTrue(gmgn_trench_passes_chain_filters(row("bsc", washTrading=True)))
        self.assertFalse(gmgn_trench_passes_chain_filters(row("robinhood", honeypot=True)))
        # These Base security switches are optional in the native profile;
        # missing/negative values do not hide a row unless the user enables
        # the corresponding checkbox.
        self.assertTrue(gmgn_trench_passes_chain_filters(row("base", openSource=None)))
        self.assertTrue(gmgn_trench_passes_chain_filters(row("base", ownerRenounced=False)))
        self.assertTrue(gmgn_trench_passes_chain_filters(row("base", burnStatus="none")))
        self.assertTrue(gmgn_trench_passes_chain_filters(row("eth", washTrading=True, honeypot=True)))
        # A non-zero reported ratio is a risk metric, not proof that GMGN
        # classified the token as rat trading. BPACK is visible in the source
        # UI with a small ratio and must not be silently removed here.
        self.assertTrue(gmgn_trench_passes_chain_filters(row("arc", ratTraderRate=0.0468)))
        self.assertTrue(gmgn_trench_passes_chain_filters(row("arc", ratWashTrading=True)))
        self.assertFalse(gmgn_trench_passes_chain_filters(row("arc", socialCount=0)))
        self.assertTrue(gmgn_trench_passes_chain_filters(row("arc", isOg=False)))
        rapid = row("solana")
        rapid["launchpad"] = "RapidLaunch"
        self.assertFalse(gmgn_trench_passes_chain_filters(rapid))
        self.assertFalse(gmgn_trench_passes_chain_filters({"network": "eth"}))

    def test_unchecked_wash_signals_do_not_hide_bsc_or_robinhood(self):
        # GMGN omits some negative flags on parts of the BSC/Robinhood tape.
        # Absence means "not reported", not "the bad condition is present".
        base_signals = {
            "profileVersion": gmgn_agentic.GMGN_TRENCH_FILTER_PROFILE_VERSION,
            "isOg": True,
            "imageDuplicateCount": None,
            "washTrading": None,
            "ratTraderRate": None,
            "honeypot": None,
            "creationTool": "",
        }
        for network in ("bsc", "robinhood"):
            row = {"network": network, "launchpad": "native", "dexId": "dex", "filterSignals": {**base_signals, "socialCount": 1}, "metrics": {"marketCapUsd": 50_000}}
            self.assertTrue(gmgn_trench_passes_chain_filters(row), network)

        flagged = {"network": "bsc", "launchpad": "native", "dexId": "dex", "filterSignals": {**base_signals, "socialCount": 1, "washTrading": True}, "metrics": {"marketCapUsd": 50_000}}
        self.assertTrue(gmgn_trench_passes_chain_filters(flagged))

    def test_all_six_saved_profiles_require_at_least_one_social_channel(self):
        for network, profile in gmgn_agentic.GMGN_TRENCH_CHAIN_FILTERS.items():
            self.assertTrue(profile.get("requireSocial"), network)
            self.assertNotIn("onlyOG", profile)
            self.assertEqual(profile.get("minMarketCapUsd"), 10_000)
        row = {
            "network": "eth",
            "launchpad": "native",
            "dexId": "dex",
            "filterSignals": {
                "profileVersion": gmgn_agentic.GMGN_TRENCH_FILTER_PROFILE_VERSION,
                "isOg": True,
                "imageDuplicateCount": 0,
                "socialCount": 0,
            },
            "metrics": {"marketCapUsd": 50_000},
        }
        self.assertFalse(gmgn_trench_passes_chain_filters(row))
        row["filterSignals"]["socialCount"] = 1
        self.assertTrue(gmgn_trench_passes_chain_filters(row))
        row["metrics"]["marketCapUsd"] = 10_000
        self.assertFalse(gmgn_trench_passes_chain_filters(row))
        row["metrics"]["marketCapUsd"] = 10_000.01
        self.assertTrue(gmgn_trench_passes_chain_filters(row))
        row["metrics"].clear()
        self.assertFalse(gmgn_trench_passes_chain_filters(row))

    def test_flybook_quote_group_26_passes_robinhood_risk_filters(self):
        payload = {
            "code": 0,
            "data": {
                "completed": [{
                    "address": "0x9dc2cf0f027ddf4c591100419384b47c1a034ba3",
                    "symbol": "FLYBOOK",
                    "name": "Flybook",
                    "image_dup": 0,
                    "is_wash_trading": False,
                    "is_rat_trading": None,
                    "rat_trader_amount_rate": 0,
                    "is_honeypot": "no",
                    "is_og": 1,
                    "quote_address_type": 26,
                    "launchpad_platform": "bankr",
                    "twitter": "https://x.com/flybook",
                    "usd_market_cap": 50_000,
                    "open_timestamp": NOW // 1000,
                }],
            },
        }

        rows = normalize_gmgn_migrated_trenches(payload, "robinhood", observed_at=NOW)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filterSignals"]["quoteAddressType"], 26)
        self.assertTrue(gmgn_trench_passes_chain_filters(rows[0]))

    def test_scanner_scope_is_exactly_the_five_requested_chains_without_base(self):
        self.assertEqual(
            ONCHAIN_RESEARCH_DEFAULT_NETWORKS,
            ("eth", "solana", "robinhood", "arc", "bsc"),
        )

    def test_gmgn_covers_chain_when_binance_trenches_is_unsupported(self):
        store = RecordingStore()
        result = scan_onchain_research(
            store,
            networks=["arc"],
            observed_at=NOW,
            gmgn_trenches_fetcher=lambda _network: trenches_payload(),
            dexscreener_fetcher=lambda *_args, **_kwargs: {"pairs": []},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["sourceStatus"]["arc"], "ok")
        self.assertEqual(result["sourceStatus"]["arc/binance-meme-rush"], "scope-excluded")
        self.assertEqual(result["candidateScope"], "gmgn-trenches")
        self.assertEqual(result["sourceStatus"]["arc/gmgn-trenches"], "ok")
        self.assertTrue(any(row["provider"] == "gmgn-trenches" for rows, _ in store.calls for row in rows))


if __name__ == "__main__":
    unittest.main()
