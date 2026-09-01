import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from chain_ecosystem_monitor import (
    ChainEcosystemStore,
    ChainEcosystemMonitor,
    DEFAULT_MARKETS,
    discover_chain_ecosystem,
    detect_high_value_alerts,
    fetch_github_repository,
    infer_market_classifications,
    is_valid_market_activity,
    is_valid_trading_pool,
    merge_provider_entities,
    normalize_provider_rows,
    rank_market_projects,
    resolve_chain_stage,
    resolve_project_token_stage,
    safe_provider_fetch,
    safe_monitor_error,
    seed_robinhood_chain,
    score_potential_project,
    score_traded_project,
)


class ChainEcosystemScoringTests(unittest.TestCase):
    def test_taxonomy_contains_all_confirmed_l0_to_l3_markets(self):
        keys = {row["key"] for row in DEFAULT_MARKETS}
        self.assertTrue(
            {
                "chain_token",
                "dex",
                "lending",
                "meme",
                "nft",
                "gamefi",
                "stablecoin",
                "derivatives",
                "points",
                "identity",
                "validators",
            }.issubset(keys)
        )

    def test_potential_project_uses_stage_specific_weights(self):
        result = score_potential_project(
            {
                "officialProgress": 100,
                "ecosystemRole": 80,
                "development": 70,
                "fundingPartners": 60,
                "community": 50,
            }
        )

        self.assertEqual(result["score"], 76.5)
        self.assertEqual(result["confidence"], 100)

    def test_missing_metric_rebalances_available_weights_and_reduces_confidence(self):
        result = score_traded_project({"liquidity": 80, "activity": 60})

        self.assertEqual(result["score"], 70)
        self.assertGreater(result["confidence"], 0)
        self.assertLess(result["confidence"], 100)

    def test_market_rank_is_deterministic_and_limited_to_five(self):
        rows = [{"projectId": f"p{index}", "score": 100 - index} for index in range(8)]

        self.assertEqual(
            [row["projectId"] for row in rank_market_projects(rows)],
            ["p0", "p1", "p2", "p3", "p4"],
        )

    def test_market_rank_uses_confidence_then_project_id_for_ties(self):
        rows = [
            {"projectId": "z", "score": 80, "confidence": 70},
            {"projectId": "b", "score": 80, "confidence": 90},
            {"projectId": "a", "score": 80, "confidence": 90},
        ]

        self.assertEqual(
            [row["projectId"] for row in rank_market_projects(rows)],
            ["a", "b", "z"],
        )


class ChainEcosystemStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "chain-ecosystem.db"
        self.store = ChainEcosystemStore(self.db_path)
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_chain(self):
        return self.store.upsert_chain(
            {
                "slug": "robinhood-chain",
                "name": "Robinhood Chain",
                "stage": "tradable_ecosystem",
                "chainId": "4663",
                "gasSymbol": "ETH",
            }
        )

    def test_store_seeds_taxonomy_once_and_preserves_evidence_identity(self):
        chain = self.add_chain()
        self.store.initialize()
        evidence = {
            "source": "official",
            "url": "https://docs.robinhood.com/chain/",
            "evidenceType": "official_docs",
            "confidence": 100,
        }

        first = self.store.add_evidence(chain["id"], "chain", chain["id"], evidence)
        second = self.store.add_evidence(chain["id"], "chain", chain["id"], evidence)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list_markets(chain["id"])), len(DEFAULT_MARKETS))

    def test_projects_can_belong_to_more_than_one_market(self):
        chain = self.add_chain()
        project = self.store.upsert_project(
            chain["id"],
            {"slug": "sample-protocol", "name": "Sample Protocol", "tokenStage": "potential"},
        )

        self.store.link_project_market(project["id"], "dex", confidence=90, source="manual")
        self.store.link_project_market(project["id"], "dex_liquidity", confidence=80, source="official")

        self.assertEqual(
            {row["marketKey"] for row in self.store.list_project_markets(project["id"])},
            {"dex", "dex_liquidity"},
        )

    def test_contract_upsert_uses_chain_and_address_as_identity(self):
        chain = self.add_chain()
        project = self.store.upsert_project(
            chain["id"],
            {"slug": "sample-token", "name": "Sample Token", "tokenStage": "contract_confirmed"},
        )
        asset = {
            "contractAddress": "0x0000000000000000000000000000000000001234",
            "symbol": "SAMPLE",
            "status": "contract_confirmed",
        }

        first = self.store.upsert_asset(chain["id"], project["id"], asset)
        second = self.store.upsert_asset(chain["id"], project["id"], {**asset, "symbol": "SMP"})

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["symbol"], "SMP")

    def test_source_health_and_manual_audit_are_append_safe(self):
        chain = self.add_chain()
        self.store.update_source_health(chain["id"], "geckoterminal", ok=False, error="timeout", checked_at=50)
        self.store.update_source_health(chain["id"], "geckoterminal", ok=True, checked_at=80)
        self.store.add_manual_audit(
            chain["id"],
            "correct_relation",
            "project",
            "sample",
            {"from": "meme", "to": "dex"},
            actor_id=7,
            created_at=90,
        )

        health = self.store.list_source_health(chain["id"])[0]
        audit = self.store.list_manual_audit(chain["id"])
        self.assertEqual(health["provider"], "geckoterminal")
        self.assertEqual(health["lastSuccessAt"], 80)
        self.assertEqual(health["failureStreak"], 0)
        self.assertEqual(audit[0]["payload"]["to"], "dex")

    def test_failed_refresh_does_not_replace_last_complete_snapshot(self):
        chain = self.add_chain()
        project = self.store.upsert_project(
            chain["id"],
            {"slug": "sample-dex", "name": "Sample DEX", "tokenStage": "trading"},
        )
        self.store.save_ranking_snapshot(
            chain["id"], "dex", project["id"], observed_at=100, rank=1, score=72, confidence=80
        )

        with self.assertRaises(RuntimeError):
            with self.store.refresh_transaction() as refresh:
                refresh.save_ranking_snapshot(
                    chain["id"], "dex", project["id"], observed_at=200, rank=1, score=95, confidence=90
                )
                raise RuntimeError("provider down")

        latest = self.store.latest_complete_snapshot(chain["id"], "dex")
        self.assertEqual(latest["observedAt"], 100)
        self.assertEqual(latest["rows"][0]["score"], 72)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class ChainEcosystemProviderTests(unittest.TestCase):
    @staticmethod
    def fixture(name):
        path = Path(__file__).parent / "fixtures" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def test_geckoterminal_and_dexscreener_merge_by_chain_and_pool(self):
        chain = {"slug": "robinhood-chain", "providerNetwork": "robinhood"}
        gecko = normalize_provider_rows(
            "geckoterminal",
            self.fixture("chain_ecosystem_geckoterminal.json"),
            chain,
            observed_at=100,
        )
        dex = normalize_provider_rows(
            "dexscreener",
            self.fixture("chain_ecosystem_dexscreener.json"),
            chain,
            observed_at=110,
        )

        merged = merge_provider_entities([*gecko, *dex])
        shared = next(row for row in merged if row["poolAddress"].endswith("00a1"))

        self.assertEqual(len(gecko), 1)
        self.assertEqual(len(merged), 2)
        self.assertEqual(shared["contractAddress"], "0x0000000000000000000000000000000000000011")
        self.assertEqual(set(shared["providers"]), {"geckoterminal", "dexscreener"})
        self.assertEqual(shared["metrics"]["liquidityUsd"], 255000)

    def test_defillama_categories_map_to_confirmed_markets(self):
        rows = normalize_provider_rows(
            "defillama",
            self.fixture("chain_ecosystem_defillama.json"),
            {"slug": "robinhood-chain", "name": "Robinhood Chain"},
            observed_at=100,
        )

        self.assertEqual({row["marketKey"] for row in rows}, {"dex", "lending"})
        self.assertTrue(all(row["subjectType"] == "project" for row in rows))

    def test_github_activity_does_not_claim_token_or_trading_status(self):
        rows = normalize_provider_rows(
            "github",
            self.fixture("chain_ecosystem_github.json"),
            {"slug": "robinhood-chain"},
            observed_at=100,
        )

        self.assertEqual(len(rows), 1)
        self.assertNotIn("tokenStage", rows[0])
        self.assertEqual(rows[0]["subjectType"], "project")
        self.assertIn("development", rows[0]["metrics"])

    def test_blockscout_contract_is_confirmed_but_not_trading(self):
        rows = normalize_provider_rows(
            "blockscout",
            self.fixture("chain_ecosystem_blockscout.json"),
            {"slug": "robinhood-chain"},
            observed_at=100,
        )

        self.assertEqual(rows[0]["tokenStage"], "contract_confirmed")
        self.assertNotEqual(rows[0]["tokenStage"], "trading")
        self.assertEqual(rows[0]["evidence"][0]["evidenceType"], "explorer_contract")

    def test_opensea_robinhood_collection_normalizes_floor_volume_and_sales(self):
        rows = normalize_provider_rows(
            "opensea",
            {
                "collections": [
                    {
                        "score": "9.8",
                        "collection": {
                            "slug": "onchainhoodies",
                            "name": "OnChainHoodies",
                            "chain": {"identifier": "robinhood"},
                            "floorPrice": {
                                "pricePerItem": {
                                    "token": {"unit": 0.041, "symbol": "ETH"},
                                    "usd": 77.08,
                                }
                            },
                            "stats": {
                                "oneDay": {"volume": {"native": {"unit": 18.31}, "usd": 34422}, "sales": 592},
                                "sevenDays": {"volume": {"native": {"unit": 72.4}, "usd": 136112}, "sales": 2110},
                                "ownerCount": 1767,
                                "totalSupply": 5999,
                                "listedItemCount": 411,
                            },
                        },
                    }
                ]
            },
            {"slug": "robinhood-chain", "name": "Robinhood Chain"},
            observed_at=100,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["marketKey"], "nft")
        self.assertEqual(rows[0]["metrics"]["floorPriceNative"], 0.041)
        self.assertEqual(rows[0]["metrics"]["volume24hUsd"], 34422)
        self.assertEqual(rows[0]["metrics"]["transactions24h"], 592)
        self.assertTrue(is_valid_market_activity(rows[0]))

    def test_opensea_activity_creates_robinhood_nft_top_five(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChainEcosystemStore(Path(temp_dir) / "chain.db")
            store.initialize()
            chain = store.upsert_chain({"slug": "robinhood-chain", "name": "Robinhood Chain"})
            opensea_rows = normalize_provider_rows(
                "opensea",
                {
                    "collections": [
                        {
                            "collection": {
                                "slug": "cash-cats-rh",
                                "name": "Cash Cats",
                                "chain": {"identifier": "robinhood"},
                                "floorPrice": {"pricePerItem": {"token": {"unit": 0.0125, "symbol": "ETH"}, "usd": 24}},
                                "stats": {
                                    "oneDay": {"volume": {"native": {"unit": 7.67}, "usd": 14500}, "sales": 912},
                                    "ownerCount": 2457,
                                    "totalSupply": 10000,
                                },
                            }
                        }
                    ]
                },
                chain,
                observed_at=100,
            )

            result = discover_chain_ecosystem(chain, store, {"opensea": lambda: opensea_rows})

            self.assertTrue(result["complete"])
            self.assertEqual(store.list_projects(chain["id"])[0]["tokenStage"], "trading")
            snapshot = store.latest_complete_snapshot(chain["id"], "nft")
            self.assertEqual(snapshot["rows"][0]["name"], "Cash Cats")
            self.assertEqual(result["rankings"]["nft"]["top"][0]["metrics"]["floorPriceNative"], 0.0125)

    def test_timeout_updates_health_and_preserves_previous_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChainEcosystemStore(Path(temp_dir) / "chain.db")
            store.initialize()
            chain = store.upsert_chain({"slug": "sample", "name": "Sample Chain"})
            previous = [{"externalId": "kept", "metrics": {"liquidityUsd": 10}}]

            def fail():
                raise TimeoutError("provider timeout")

            result = safe_provider_fetch(store, chain["id"], "geckoterminal", fail, previous_rows=previous)

            self.assertEqual(result["rows"], previous)
            self.assertTrue(result["stale"])
            self.assertEqual(store.list_source_health(chain["id"])[0]["failureStreak"], 1)

    def test_fetcher_accepts_injected_session_and_optional_github_token(self):
        session = FakeSession(self.fixture("chain_ecosystem_github.json"))

        payload = fetch_github_repository("sample/protocol", session=session, token="secret-token")

        self.assertEqual(payload["full_name"], "sample/protocol")
        self.assertIn("/repos/sample/protocol", session.calls[0][0])
        self.assertEqual(session.calls[0][1]["headers"]["Authorization"], "Bearer secret-token")

    def test_pool_and_explorer_contract_evidence_create_a_ranked_trading_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChainEcosystemStore(Path(temp_dir) / "chain.db")
            store.initialize()
            chain = store.upsert_chain(
                {"slug": "sample", "name": "Sample Chain", "geckoterminalNetwork": "robinhood"}
            )
            blockscout = {
                "items": [
                    {
                        "address_hash": "0x0000000000000000000000000000000000000011",
                        "name": "Sample Protocol",
                        "symbol": "SMP",
                        "holders_count": "1200",
                    }
                ]
            }

            result = discover_chain_ecosystem(
                chain,
                store,
                {
                    "geckoterminal": lambda: self.fixture("chain_ecosystem_geckoterminal.json"),
                    "blockscout": lambda: blockscout,
                },
            )

            self.assertTrue(result["complete"])
            self.assertEqual(store.list_projects(chain["id"])[0]["tokenStage"], "trading")
            self.assertEqual(store.latest_complete_snapshot(chain["id"], "dex_liquidity")["rows"][0]["rank"], 1)

    def test_market_classifier_discovers_protocol_meme_rwa_and_stablecoin_markets(self):
        cases = {
            "Lighter Bridge": {"infrastructure", "bridge_assets", "dex"},
            "Morpho Blue Lending": {"lending", "dex"},
            "Cash Cat": {"meme"},
            "Apple • Robinhood Token": {"ai_depin_rwa"},
            "USDG Stablecoin": {"stablecoin"},
            "HOODZ Game": {"gamefi"},
        }

        for name, expected in cases.items():
            with self.subTest(name=name):
                found = {row["marketKey"] for row in infer_market_classifications({"projectName": name})}
                self.assertTrue(expected.issubset(found), found)


class ChainEcosystemLifecycleTests(unittest.TestCase):
    def test_official_mainnet_announcement_advances_early_chain(self):
        stage = resolve_chain_stage(
            "early_watch",
            [{"evidenceType": "official_mainnet_announcement", "source": "official", "confidence": 100}],
        )

        self.assertEqual(stage, "mainnet_focus")

    def test_public_mainnet_and_valid_pool_advance_to_tradable(self):
        stage = resolve_chain_stage(
            "mainnet_focus",
            [
                {"evidenceType": "public_mainnet", "source": "official", "confidence": 100},
                {
                    "evidenceType": "trading_pool",
                    "source": "geckoterminal",
                    "confidence": 85,
                    "metrics": {"liquidityUsd": 100000, "transactions24h": 120},
                },
            ],
        )

        self.assertEqual(stage, "tradable_ecosystem")

    def test_project_needs_contract_and_valid_pool_before_trading(self):
        project = {"tokenStage": "potential"}
        announced = [{"evidenceType": "token_announcement", "source": "official", "confidence": 100}]
        contracted = [*announced, {"evidenceType": "explorer_contract", "confidence": 80}]
        traded = [
            *contracted,
            {
                "evidenceType": "trading_pool",
                "confidence": 85,
                "metrics": {"liquidityUsd": 50000, "transactions24h": 50},
            },
        ]

        self.assertEqual(resolve_project_token_stage(project, announced), "announced")
        self.assertEqual(resolve_project_token_stage(project, contracted), "contract_confirmed")
        self.assertEqual(resolve_project_token_stage(project, traded), "trading")
        self.assertTrue(is_valid_trading_pool(traded[-1]["metrics"]))

    def test_new_market_and_stable_leader_create_high_value_events(self):
        previous = {
            "complete": True,
            "chain": {"id": 1, "stage": "tradable_ecosystem"},
            "markets": {"dex": {"leader": {"projectId": 10, "score": 80}}},
            "projects": {},
        }
        current = {
            "complete": True,
            "observedAt": 200,
            "chain": {"id": 1, "stage": "tradable_ecosystem"},
            "markets": {
                "dex": {"leader": {"projectId": 11, "score": 86}, "leaderStreak": 2},
                "meme": {"leader": {"projectId": 20, "score": 75}, "leaderStreak": 1},
            },
            "projects": {},
        }

        events = detect_high_value_alerts(previous, current)

        self.assertEqual({event["eventType"] for event in events}, {"new_market", "leader_change"})

    def test_leader_change_waits_for_margin_and_two_snapshots(self):
        previous = {
            "complete": True,
            "chain": {"id": 1, "stage": "tradable_ecosystem"},
            "markets": {"dex": {"leader": {"projectId": 10, "score": 80}}},
            "projects": {},
        }
        base_current = {
            "complete": True,
            "observedAt": 200,
            "chain": {"id": 1, "stage": "tradable_ecosystem"},
            "projects": {},
        }

        one_cycle = detect_high_value_alerts(
            previous,
            {**base_current, "markets": {"dex": {"leader": {"projectId": 11, "score": 90}, "leaderStreak": 1}}},
        )
        narrow_lead = detect_high_value_alerts(
            previous,
            {**base_current, "markets": {"dex": {"leader": {"projectId": 11, "score": 84}, "leaderStreak": 2}}},
        )

        self.assertFalse(any(event["eventType"] == "leader_change" for event in one_cycle))
        self.assertFalse(any(event["eventType"] == "leader_change" for event in narrow_lead))

    def test_metric_surge_alert_excludes_token_trading_transition(self):
        previous = {
            "complete": True,
            "chain": {"id": 1, "stage": "tradable_ecosystem"},
            "markets": {},
            "projects": {
                "7": {"id": 7, "tokenStage": "contract_confirmed", "metrics": {"volumeMedian24hUsd": 50000, "liquidityUsd": 50000}}
            },
        }
        current = {
            "complete": True,
            "observedAt": 300,
            "chain": {"id": 1, "stage": "tradable_ecosystem"},
            "markets": {},
            "projects": {
                "7": {
                    "id": 7,
                    "name": "Sample Token",
                    "tokenStage": "trading",
                    "metrics": {"volume24hUsd": 150000, "liquidityUsd": 80000},
                }
            },
        }

        events = detect_high_value_alerts(previous, current)

        self.assertEqual({event["eventType"] for event in events}, {"market_surge"})
        self.assertEqual(len({event["dedupeKey"] for event in events}), len(events))

    def test_partial_scan_cannot_change_stage_or_leader(self):
        previous = {
            "complete": True,
            "chain": {"id": 1, "stage": "early_watch"},
            "markets": {"dex": {"leader": {"projectId": 1, "score": 50}}},
            "projects": {},
        }
        current = {
            "complete": False,
            "observedAt": 400,
            "chain": {"id": 1, "stage": "tradable_ecosystem"},
            "markets": {"dex": {"leader": {"projectId": 2, "score": 99}, "leaderStreak": 3}},
            "projects": {},
        }

        self.assertEqual(detect_high_value_alerts(previous, current), [])

    def test_robinhood_seed_contains_only_official_chain_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChainEcosystemStore(Path(temp_dir) / "chain.db")
            store.initialize()

            chain = seed_robinhood_chain(store)

            self.assertEqual(chain["stage"], "tradable_ecosystem")
            self.assertEqual(chain["chainId"], "4663")
            self.assertEqual(chain["gasSymbol"], "ETH")
            self.assertEqual(chain["geckoterminalNetwork"], "robinhood")
            self.assertEqual(store.list_projects(chain["id"]), [])

    def test_replaying_same_alert_keeps_one_database_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChainEcosystemStore(Path(temp_dir) / "chain.db")
            store.initialize()
            chain = store.upsert_chain({"slug": "sample", "name": "Sample Chain"})
            payload = {
                "chainId": chain["id"],
                "eventType": "new_market",
                "dedupeKey": f"chain:{chain['id']}:market:meme",
                "title": "发现新细分市场：meme",
                "observedAt": 500,
                "confidence": 90,
            }

            first = store.upsert_alert(payload)
            second = store.upsert_alert(payload)

            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(store.list_alerts(chain["id"])), 1)

    def test_scan_baseline_and_alert_delivery_state_are_persistent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChainEcosystemStore(Path(temp_dir) / "chain.db")
            store.initialize()
            chain = store.upsert_chain({"slug": "sample", "name": "Sample Chain"})

            self.assertFalse(store.get_scan_state(chain["id"])["baselineReady"])
            store.mark_scan_complete(chain["id"], completed_at=600)
            self.assertTrue(store.get_scan_state(chain["id"])["baselineReady"])

            alert = store.upsert_alert(
                {
                    "chainId": chain["id"],
                    "eventType": "new_market",
                    "dedupeKey": f"chain:{chain['id']}:market:meme",
                    "title": "发现新细分市场：meme",
                    "observedAt": 700,
                }
            )
            self.assertEqual([row["id"] for row in store.list_pending_alerts(chain["id"])], [alert["id"]])

            store.upsert_alert(
                {
                    "chainId": chain["id"],
                    "eventType": "token_trading",
                    "dedupeKey": f"chain:{chain['id']}:project:8:trading",
                    "title": "项目已形成有效交易",
                    "observedAt": 750,
                }
            )
            self.assertEqual([row["eventType"] for row in store.list_alerts(chain["id"])], ["new_market"])
            self.assertEqual([row["id"] for row in store.list_pending_alerts(chain["id"])], [alert["id"]])

            delivered = store.mark_alert_delivered(alert["id"], delivered_at=800)
            self.assertEqual(delivered["deliveredAt"], 800)
            self.assertEqual(store.list_pending_alerts(chain["id"]), [])


class ChainEcosystemMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ChainEcosystemStore(Path(self.temp_dir.name) / "chain.db")
        self.scheduled = []
        self.monitor = ChainEcosystemMonitor(
            self.store,
            provider_factory=lambda chain: {},
            submitter=lambda target, *args: self.scheduled.append((target, args)),
        )
        self.monitor.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_payload_exposes_complete_api_contract(self):
        payload = self.monitor.payload("robinhood-chain")

        self.assertTrue(
            {
                "ok",
                "chains",
                "selectedChain",
                "markets",
                "potentialProjects",
                "alerts",
                "sourceHealth",
                "warnings",
                "updatedAt",
                "stale",
            }.issubset(payload)
        )
        self.assertEqual(payload["selectedChain"]["chainId"], "4663")
        self.assertEqual(len(payload["markets"]), len(DEFAULT_MARKETS))
        chain_token = next(row for row in payload["markets"] if row["key"] == "chain_token")
        self.assertEqual(chain_token["candidates"][0]["symbol"], "ETH")

    def test_market_payload_exposes_discovered_projects_before_they_are_rankable(self):
        chain = self.store.get_chain("robinhood-chain")
        project = self.store.upsert_project(
            chain["id"],
            {"slug": "hoodz", "name": "HOODZ", "tokenStage": "potential"},
        )
        self.store.link_project_market(project["id"], "gamefi", confidence=75, source="classifier")

        payload = self.monitor.payload(chain["id"])
        market = next(row for row in payload["markets"] if row["key"] == "gamefi")

        self.assertEqual(market["top"], [])
        self.assertEqual(market["candidates"][0]["name"], "HOODZ")

    def test_force_refresh_returns_snapshot_and_schedules_only_one_worker(self):
        first = self.monitor.refresh("robinhood-chain", force=True)
        second = self.monitor.refresh("robinhood-chain", force=True)

        self.assertTrue(first["refreshScheduled"])
        self.assertTrue(second["refreshScheduled"])
        self.assertEqual(len(self.scheduled), 1)

    def test_background_cycle_respects_due_time_and_source_backoff(self):
        chain = self.store.get_chain("robinhood-chain")

        first = self.monitor.run_cycle(now_ms=1_000)
        second = self.monitor.run_cycle(now_ms=1_001)

        self.assertEqual(first["scheduled"], [chain["id"]])
        self.assertEqual(second["scheduled"], [])

    def test_pending_alerts_are_delivered_once(self):
        chain = self.store.get_chain("robinhood-chain")
        delivered = []
        self.monitor.alert_sink = lambda alert: delivered.append(alert) or {"ok": True}
        alert = self.store.upsert_alert(
            {
                "chainId": chain["id"],
                "eventType": "stage_upgrade",
                "dedupeKey": f"chain:{chain['id']}:stage:mainnet_focus",
                "title": "公链阶段升级",
                "observedAt": 900,
            }
        )

        self.monitor.deliver_pending_alerts(chain["id"])
        self.monitor.deliver_pending_alerts(chain["id"])

        self.assertEqual([row["id"] for row in delivered], [alert["id"]])
        self.assertGreater(self.store.list_alerts(chain["id"])[0]["deliveredAt"], 0)

    def test_first_complete_scan_builds_baseline_then_later_change_alerts(self):
        chain = self.store.upsert_chain({"slug": "sample", "name": "Sample Chain", "stage": "early_watch"})
        delivered = []
        self.monitor.alert_sink = lambda alert: delivered.append(alert) or {"ok": True}
        calls = 0

        def fake_discovery(chain_row, store, providers):
            nonlocal calls
            calls += 1
            observed_at = calls * 1_000
            project = store.upsert_project(
                chain_row["id"],
                {"slug": "sample-dex", "name": "Sample DEX", "tokenStage": "trading"},
            )
            store.link_project_market(project["id"], "dex", confidence=100, source="test")
            store.save_ranking_snapshot(
                chain_row["id"],
                "dex",
                project["id"],
                observed_at=observed_at,
                rank=1,
                score=80,
                confidence=100,
                metrics={"market": {"liquidityUsd": 500_000, "volume24hUsd": 200_000}},
            )
            if calls == 2:
                chain_row = store.update_chain_stage(chain_row["id"], "mainnet_focus", observed_at=observed_at)
            return {"complete": True, "observedAt": observed_at, "chain": chain_row, "rankings": {}}

        with patch("chain_ecosystem_monitor.discover_chain_ecosystem", side_effect=fake_discovery):
            self.monitor._refresh_worker(chain["id"])
            self.assertEqual(self.store.list_alerts(chain["id"]), [])
            self.monitor._refresh_worker(chain["id"])

        self.assertEqual([row["eventType"] for row in delivered], ["stage_upgrade"])

    def test_manual_mutations_require_actor(self):
        with self.assertRaises(PermissionError):
            self.monitor.apply_action(
                {"action": "add_chain", "name": "Sample Chain", "officialUrl": "https://sample.example"},
                actor_id=0,
            )

    def test_add_chain_returns_immediately_and_schedules_background_scan(self):
        result = self.monitor.apply_action(
            {"action": "add_chain", "name": "Zero", "officialUrl": "https://layerzero.network/"},
            actor_id=7,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["chain"]["slug"], "zero")
        self.assertTrue(result["refreshScheduled"])
        self.assertNotIn("payload", result)
        self.assertEqual(len(self.scheduled), 1)

    def test_manual_chain_validation_rejects_unsafe_url_and_bad_chain_id(self):
        with self.assertRaises(ValueError):
            self.monitor.apply_action(
                {"action": "add_chain", "name": "Unsafe", "officialUrl": "file:///tmp/secret", "chainId": "abc"},
                actor_id=7,
            )

    def test_manual_project_and_evidence_are_audited(self):
        chain = self.store.get_chain("robinhood-chain")
        result = self.monitor.apply_action(
            {
                "action": "add_project",
                "chainId": chain["id"],
                "name": "Candidate Protocol",
                "marketKey": "dex",
                "officialUrl": "https://candidate.example",
            },
            actor_id=7,
        )
        project_id = result["project"]["id"]
        self.monitor.apply_action(
            {
                "action": "add_evidence",
                "chainId": chain["id"],
                "subjectType": "project",
                "subjectId": project_id,
                "evidenceType": "token_announcement",
                "source": "manual",
                "url": "https://candidate.example/token",
                "title": "Token plan",
            },
            actor_id=7,
        )

        self.assertEqual(len(self.store.list_manual_audit(chain["id"])), 2)
        self.assertEqual(self.monitor.payload(chain["id"])["potentialProjects"][0]["name"], "Candidate Protocol")

    def test_public_error_redacts_local_paths_and_tokens(self):
        message = safe_monitor_error(RuntimeError(r"C:\Users\Alice\secret.db token=abcdef1234567890"))

        self.assertNotIn("C:\\Users", message)
        self.assertNotIn("abcdef1234567890", message)


if __name__ == "__main__":
    unittest.main()
