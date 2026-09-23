import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gmgn_agentic
import server


def trench_row(
    symbol: str,
    contract: str,
    observed_at: int,
    *,
    volume: float = 1000,
    network: str = "solana",
    image_url: str | None = None,
) -> dict:
    return {
        "network": network,
        "contractAddress": contract,
        "symbol": symbol,
        "name": f"{symbol} token",
        "imageUrl": image_url or f"https://img.example/{symbol}.png",
        "poolCreatedAt": observed_at - 60_000,
        "tradeUrl": f"https://gmgn.ai/sol/token/{contract}",
        "gmgnNarrative": f"{symbol} narrative",
        "filterSignals": {
            "profileVersion": gmgn_agentic.GMGN_TRENCH_FILTER_PROFILE_VERSION,
            "isOg": True,
            "imageDuplicateCount": 0,
            "washTrading": False,
            "ratTraderRate": 0,
            "socialCount": 1,
            "honeypot": None,
            "openSource": None,
            "ownerRenounced": None,
            "burnStatus": "burn",
            "creationTool": "",
        },
        "xOriginal": {
            "url": f"https://x.com/{symbol.lower()}/status/123",
            "handle": symbol.lower(),
            "isTweet": True,
            "text": f"{symbol} original post",
        },
        "narrativeContext": {
            "links": {
                "twitter": f"https://x.com/{symbol.lower()}/status/123",
                "website": f"https://{symbol.lower()}.example",
            }
        },
        "launchFacts": {"holders": 42},
        "metrics": {
            "priceUsd": 0.01,
            "marketCapUsd": 50_000,
            "liquidityUsd": 20_000,
            "volumeH24Usd": volume,
            "priceChangeH1": 3.2,
            "transactionsH24": 18,
        },
    }


class GmgnTrenchHotBoardTests(unittest.TestCase):
    def tearDown(self):
        with server.GMGN_TRENCH_X_POST_CACHE_LOCK:
            server.GMGN_TRENCH_X_POST_CACHE.clear()
        server.GMGN_TRENCH_X_POST_NEXT_REQUEST_AT = 0.0

    def test_public_original_post_parser_keeps_text_author_metrics_and_media(self):
        post = server.gmgn_trench_x_post_from_fxtwitter({
            "code": 200,
            "status": {
                "id": "2101445889147424969",
                "url": "https://x.com/example/status/2101445889147424969",
                "text": "The corresponding GMGN-linked original post",
                "created_timestamp": 1_789_858_717,
                "likes": 42,
                "reposts": 7,
                "views": 900,
                "author": {
                    "name": "Example Author",
                    "screen_name": "example",
                    "avatar_url": "https://img.example/avatar.jpg",
                },
                "media": {
                    "photos": [{"url": "https://img.example/post.jpg", "width": 1200, "height": 800}],
                    "videos": [{
                        "thumbnail_url": "https://img.example/video.jpg",
                        "formats": [{"url": "https://video.example/post.mp4"}],
                    }],
                },
            },
        }, "2101445889147424969")

        self.assertEqual(post["text"], "The corresponding GMGN-linked original post")
        self.assertEqual(post["author"]["handle"], "example")
        self.assertEqual(post["metrics"]["like"], 42)
        self.assertEqual(post["media"][0]["type"], "photo")
        self.assertEqual(post["media"][1]["type"], "video")
        self.assertEqual(post["media"][1]["playbackUrl"], "https://video.example/post.mp4")

    def test_original_post_lookup_is_lazy_cached_and_single_request(self):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": 200,
            "status": {
                "id": "2101445889147424969",
                "text": "cached original",
                "author": {"screen_name": "example"},
            },
        }
        session = Mock()
        session.get.return_value = response

        first = server.gmgn_trench_x_post_payload("2101445889147424969", session=session)
        second = server.gmgn_trench_x_post_payload("2101445889147424969", session=session)

        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["post"]["text"], "cached original")
        session.get.assert_called_once()

    def test_history_keeps_first_received_time_and_updates_market_fields(self):
        first_seen = 1_800_000_000_000
        contract = "AbCd123456789"
        original = server.merge_gmgn_trench_history(
            [], [trench_row("ONE", contract, first_seen, volume=1000)], observed_at=first_seen,
        )
        updated = server.merge_gmgn_trench_history(
            original,
            [trench_row("ONE", contract, first_seen + 60_000, volume=9000)],
            observed_at=first_seen + 60_000,
        )

        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["receivedAt"], first_seen)
        self.assertEqual(updated[0]["lastSeenAt"], first_seen + 60_000)
        self.assertEqual(updated[0]["metrics"]["volumeH24Usd"], 9000)

    def test_refresh_persists_received_rows_and_builds_scrollable_source(self):
        observed_at = 1_800_000_000_000
        payload = {
            "ok": True,
            "items": [trench_row("NEW", "SoLaNaContract123", observed_at)],
            "sourceStatus": {
                "solana/gmgn-trenches": "ok",
                "eth/gmgn-trenches": "rate_limited",
            },
            "rateLimited": True,
            "retryAfterSeconds": 90,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "gmgn-history.json"
            with (
                patch.object(server, "GMGN_TRENCH_HISTORY_PATH", history_path),
                patch.object(server, "fetch_live_onchain_trenches", return_value=payload) as fetch_live,
                patch.object(server.time, "time", return_value=observed_at / 1000),
            ):
                source = server.refresh_gmgn_trenches_hot_board()

            fetch_live.assert_called_once()
            self.assertEqual(fetch_live.call_args.kwargs["page_size"], 480)
            self.assertIsNone(fetch_live.call_args.kwargs["per_network_limit"])
            self.assertTrue(fetch_live.call_args.kwargs["include_recent_rank_supplement"])
            self.assertTrue(history_path.exists())
            self.assertEqual(source["id"], "gmgn-trenches")
            self.assertTrue(source["scrollableHistory"])
            self.assertTrue(source["excludeFromTotal"])
            self.assertEqual(source["historyCount"], 1)
            self.assertEqual(source["rows"][0]["symbol"], "NEW")
            self.assertEqual(source["rows"][0]["icon"], "https://img.example/NEW.png")
            self.assertEqual(
                source["rows"][0]["binanceWalletUrl"],
                "https://web3.binance.com/en/token/sol/SoLaNaContract123?ref=MQ6JD2X4",
            )
            self.assertEqual(source["rows"][0]["xOriginal"]["text"], "NEW original post")
            self.assertEqual(source["refreshIntervalSeconds"], server.GMGN_TRENCH_BOARD_REFRESH_SECONDS)
            self.assertEqual(source["aiProvider"], "binance")
            self.assertEqual(source["aiPolicy"], "visible-latest-10-cached")
            self.assertEqual(source["currentFetchedCount"], 1)

    def test_unprofiled_history_is_not_displayed(self):
        row = trench_row("OLD", "OldContract123", 1_800_000_000_000)
        row.pop("filterSignals")
        self.assertEqual(server.gmgn_trench_board_rows([row]), [])

    def test_base_history_is_removed_from_the_five_chain_board(self):
        observed_at = 1_800_000_000_000
        base = trench_row(
            "BASEOLD",
            "0x" + "a" * 40,
            observed_at,
            network="base",
        )

        self.assertEqual(server.gmgn_trench_board_rows([base], current_rows=[base]), [])

    def test_market_response_overlays_latest_gmgn_board_without_waiting_for_all_sources(self):
        stale = {"id": "gmgn-trenches", "rows": [{"symbol": "OLD"}]}
        latest = {"id": "gmgn-trenches", "rows": [{"symbol": "NEW"}]}
        cached_market = {"sources": [{"id": "binance-wallet-hot", "rows": []}, stale]}
        with (
            patch.object(server, "cached_api_payload", return_value=cached_market),
            patch.object(server, "fetch_gmgn_trenches_hot_board", return_value=latest),
        ):
            payload = server.market_hot_response_payload()

        gmgn = next(source for source in payload["sources"] if source["id"] == "gmgn-trenches")
        self.assertEqual(gmgn["rows"][0]["symbol"], "NEW")

    def test_v44_good_research_candidate_is_marked_without_mutating_cached_board(self):
        observed_at = 1_800_000_000_000
        contract = "SoLaNaResearchPick123"
        source = {
            "id": "gmgn-trenches",
            "rows": [trench_row("PICK", contract, observed_at)],
            "summaryRows": [trench_row("PICK", contract, observed_at)],
        }
        mark = {
            "label": "V4.4 好标的",
            "potentialTier": "leader",
            "summary": "链外注意力与独立买方同步承接",
            "nextTrigger": "跨社区传播继续扩大",
            "opportunityScore": 86,
        }
        key = server.onchain_candidate_key(source["rows"][0])
        with patch.object(server, "load_v44_research_marks", return_value={key: mark}):
            result = server.attach_v44_research_marks_to_gmgn_trenches(source)

        self.assertNotIn("researchMark", source["rows"][0])
        self.assertEqual(result["rows"][0]["researchMark"]["label"], "V4.4 好标的")
        self.assertEqual(result["summaryRows"][0]["researchMark"]["potentialTier"], "leader")

    def test_current_snapshot_filters_reused_artwork_like_gmgn_avatar_filter(self):
        observed_at = 1_800_000_000_000
        current = [
            trench_row("STACK", "SolContractA", observed_at, network="solana", image_url="https://img.example/stack-a.png"),
            trench_row("STACK", "SolContractB", observed_at, network="solana", image_url="https://img.example/stack-b.png"),
            trench_row("COPY", "SolContractD", observed_at, network="solana", image_url="https://img.example/stack-a.png"),
            trench_row("SOL2", "SolContractC", observed_at, network="solana"),
            trench_row("BSCX", "0x" + "b" * 40, observed_at, network="bsc"),
            trench_row("HOODX", "0x" + "c" * 40, observed_at, network="robinhood"),
        ]
        history = server.merge_gmgn_trench_history([], current, observed_at=observed_at)

        rows = server.gmgn_trench_board_rows(history, current_rows=current)

        self.assertEqual([row["symbol"] for row in rows], ["STACK", "STACK", "COPY", "SOL2", "BSCX", "HOODX"])
        self.assertEqual(sum(1 for row in rows if row["symbol"] in {"STACK", "COPY"}), 3)
        self.assertTrue(all(row["isCurrent"] for row in rows))

    def test_small_rat_ratio_stays_visible_as_risk_instead_of_being_filtered(self):
        observed_at = 1_800_000_000_000
        bpack = trench_row("BPACK", "E2D57Ti27fBTsSxFktnmE2FH5CKgEh1HfwoqYuDGrRr5", observed_at)
        bpack["filterSignals"]["ratTraderRate"] = 0.0468
        bpack["filterSignals"]["ratWashTrading"] = None
        bpack["filterWarnings"] = ["老鼠仓占比 4.68%"]

        rows = server.gmgn_trench_board_rows([bpack], current_rows=[bpack])

        self.assertEqual([row["symbol"] for row in rows], ["BPACK"])
        self.assertEqual(rows[0]["filterWarnings"], ["老鼠仓占比 4.68%"])

    def test_board_is_strictly_sorted_by_open_time_across_current_and_history(self):
        now = 1_800_000_000_000
        current_old = trench_row("CURRENT_OLD", "SolCurrentOld", now - 10 * 86_400_000)
        history_new = trench_row("HISTORY_NEW", "SolHistoryNew", now)
        current_old["poolCreatedAt"] = now - 20 * 86_400_000
        history_new["poolCreatedAt"] = now - 2 * 60 * 60_000
        history = server.merge_gmgn_trench_history([], [current_old, history_new], observed_at=now)

        rows = server.gmgn_trench_board_rows(history, current_rows=[current_old])

        self.assertEqual([row["symbol"] for row in rows], ["HISTORY_NEW", "CURRENT_OLD"])
        self.assertGreaterEqual(rows[0]["poolCreatedAt"], rows[1]["poolCreatedAt"])


if __name__ == "__main__":
    unittest.main()
