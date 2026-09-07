import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class MarketAlertSpeechTests(unittest.TestCase):
    def test_undated_x_rows_are_not_replayed_as_fresh_alerts(self):
        payload = {
            "updatedAt": 1_800_000_000_000,
            "sources": [{"id": "kol", "displayName": "Old KOL", "handle": "old_kol"}],
            "items": [{
                "id": "old-row",
                "sourceId": "kol",
                "text": "an old cached post",
                "url": "https://x.com/old_kol/status/2096555530281959754",
            }],
        }

        self.assertEqual(server.parse_site_x_kol_events(payload), [])

    def test_x_popup_feed_uses_short_freshness_window(self):
        feed = next(row for row in server.site_alert_feeds() if row["name"] == "x-kol")

        self.assertEqual(feed["maxAgeMs"], server.X_KOL_DESKTOP_ALERT_MAX_AGE_MS)
        self.assertLessEqual(feed["maxAgeMs"], 15 * 60 * 1000)

    def test_x_project_roles_are_explicit_in_alert_title_and_speech(self):
        payload = {
            "updatedAt": 1_800_000_000_000,
            "sources": [
                {"id": "official", "handle": "project_xyz", "displayName": "Project XYZ", "category": "project_official"},
                {"id": "founder", "handle": "alice_xyz", "displayName": "Alice", "category": "founder"},
                {"id": "kol", "handle": "watcher", "displayName": "Watcher", "category": "kol"},
            ],
            "items": [
                {"id": "1", "sourceId": "official", "text": "Meet our new mascot", "publishedAt": 1_800_000_000_000},
                {"id": "2", "sourceId": "founder", "text": "I named the character LOBSTER", "publishedAt": 1_800_000_000_000},
                {"id": "3", "sourceId": "kol", "text": "Market observation", "publishedAt": 1_800_000_000_000},
            ],
        }

        official, founder, kol = server.parse_site_x_kol_events(payload)

        self.assertTrue(official["title"].startswith("【项目官方】Project XYZ："))
        self.assertTrue(official["speech"].startswith("项目官方发布，Project XYZ："))
        self.assertEqual(official["xCategory"], "project_official")
        self.assertEqual(official["queuePriority"], 80)
        self.assertTrue(founder["title"].startswith("【创始人/联合创始人】Alice："))
        self.assertTrue(founder["speech"].startswith("项目创始人或联合创始人发布，Alice："))
        self.assertEqual(founder["xCategory"], "founder")
        self.assertEqual(founder["queuePriority"], 82)
        self.assertFalse(kol["title"].startswith("【"))
        self.assertEqual(kol["speech"], "")

    def test_aster_contract_rows_keep_perpetual_and_pending_contracts(self):
        class Response:
            def json(self):
                return {
                    "symbols": [
                        {
                            "symbol": "TUTUSDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                            "onboardDate": 1_786_000_000_000,
                            "baseAsset": "TUT",
                            "quoteAsset": "USDT",
                        },
                        {
                            "symbol": "NEXTUSDT",
                            "contractType": "",
                            "status": "PENDING_TRADING",
                            "onboardDate": 1_787_000_000_000,
                            "baseAsset": "NEXT",
                            "quoteAsset": "USDT",
                        },
                        {
                            "symbol": "OLDUSDT",
                            "contractType": "PERPETUAL",
                            "status": "BREAK",
                            "onboardDate": 1_785_000_000_000,
                            "baseAsset": "OLD",
                            "quoteAsset": "USDT",
                        },
                    ]
                }

        with patch.object(server.requests, "get", return_value=Response()):
            rows = server.aster_contract_rows()

        self.assertEqual([row["symbol"] for row in rows], ["NEXTUSDT", "TUTUSDT"])
        self.assertEqual(rows[0]["status"], "待上线")
        self.assertEqual(rows[1]["title"], "Aster 上线 TUT/USDT 永续合约")

    def test_aster_official_listing_rows_parse_one_announcement_with_multiple_contracts(self):
        now_seconds = 1_800_000_000

        class Response:
            def json(self):
                return {
                    "data": {
                        "rows": [
                            {
                                "id": 430,
                                "category": "NEW_LISTING",
                                "title": "New RWA Perp Listings: $MEITUAN(5x), $KUAISHOU(5x), $MUU(20x)",
                                "subtitle": "Official listing batch",
                                "publishTime": now_seconds * 1000 - 60_000,
                            }
                        ]
                    }
                }

        with (
            patch.object(server.requests, "post", return_value=Response()),
            patch.object(server.time, "time", return_value=now_seconds),
        ):
            rows = server.aster_official_listing_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbols"], ["MEITUAN", "KUAISHOU", "MUU"])
        self.assertEqual(rows[0]["contractSymbols"], ["MEITUANUSDT", "KUAISHOUUSDT", "MUUUSDT"])
        self.assertTrue(rows[0]["officialAnnouncement"])
        self.assertEqual(rows[0]["url"], "https://www.asterdex.com/en/announcement/430")

    def test_aster_official_x_listing_rows_include_mubarak_post(self):
        now_seconds = 1_800_000_000
        published_ms = now_seconds * 1000 - 60_000
        x_payload = {
            "items": [
                {
                    "id": "x-row",
                    "tweetId": "2086463769820135545",
                    "text": "New perp listing: $MUBARAK with up to 5x leverage.",
                    "fullText": "New perp listing: $MUBARAK with up to 5x leverage.",
                    "url": "https://x.com/Aster_DEX/status/2086463769820135545",
                    "publishedAt": published_ms,
                    "entryType": "tweet",
                    "metrics": {"view": 65000},
                }
            ]
        }

        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "aster-x.json"
            with (
                patch.object(server, "ASTER_X_LISTING_CACHE_PATH", cache_path),
                patch.object(server.time, "time", return_value=now_seconds),
                patch.object(server, "x_kol_token", return_value="configured"),
                patch.object(server, "x_kol_fetch_api_source", return_value=x_payload),
            ):
                rows = server.aster_official_x_listing_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbols"], ["MUBARAK"])
        self.assertEqual(rows[0]["officialChannel"], "x")
        self.assertEqual(rows[0]["status"], "官方 X")
        self.assertEqual(rows[0]["url"], "https://x.com/Aster_DEX/status/2086463769820135545")

    def test_aster_timeline_prefers_individual_x_post_over_matching_batch_asset(self):
        website = {
            "id": "aster-official-425",
            "announcementAt": 1_800_000_000_000,
            "date": 1_800_000_000_000,
            "symbols": ["IOTX", "MUBARAK"],
            "contractSymbols": ["IOTXUSDT", "MUBARAKUSDT"],
            "baseAsset": "IOTX · MUBARAK",
            "symbol": "IOTXUSDT,MUBARAKUSDT",
            "officialAnnouncement": True,
        }
        x_row = {
            "id": "aster-x-mubarak",
            "announcementAt": 1_800_000_060_000,
            "date": 1_800_000_060_000,
            "symbols": ["MUBARAK"],
            "contractSymbols": ["MUBARAKUSDT"],
            "baseAsset": "MUBARAK",
            "symbol": "MUBARAKUSDT",
            "officialAnnouncement": True,
            "officialChannel": "x",
        }

        with (
            patch.object(server, "aster_official_listing_rows", return_value=[website]),
            patch.object(server, "aster_official_x_listing_rows", return_value=[x_row]),
            patch.object(server, "aster_contract_announcement_rows", return_value=[]),
        ):
            rows = server.aster_listing_announcement_rows()

        self.assertEqual([row["id"] for row in rows], ["aster-x-mubarak", "aster-official-425"])
        website_row = next(row for row in rows if row["id"] == "aster-official-425")
        self.assertEqual(website_row["symbols"], ["IOTX"])
        self.assertEqual(website_row["contractSymbols"], ["IOTXUSDT"])

    def test_aster_contract_alert_has_speech_and_stable_symbol_key(self):
        payload = {
            "updatedAt": 1_786_000_000_000,
            "items": [
                {
                    "symbol": "TUTUSDT",
                    "baseAsset": "TUT",
                    "quoteAsset": "USDT",
                    "contractStatus": "TRADING",
                    "date": 1_786_000_000_000,
                }
            ],
        }

        event = server.parse_site_aster_contract_events(payload)[0]

        self.assertEqual(event["key"], "aster-contract:TUTUSDT")
        self.assertEqual(event["speech"], "Aster 合约上新公告，TUT 永续合约已上线。")
        self.assertEqual(event["queuePriority"], 80)

    def test_aster_announcements_bootstrap_recent_only_then_keep_new_additions(self):
        now_seconds = 1_800_000_000
        now_ms = now_seconds * 1000

        def row(symbol, onboard_ms, status="TRADING"):
            base = symbol.removesuffix("USDT")
            return {
                "id": f"aster-{symbol}",
                "source": "Aster",
                "sourceLabel": "AS",
                "symbol": symbol,
                "baseAsset": base,
                "quoteAsset": "USDT",
                "contractStatus": status,
                "status": "待上线" if status == "PENDING_TRADING" else "交易中",
                "date": onboard_ms,
                "url": server.ASTER_TRADE_URL,
            }

        old_contract = row("OLDUSDT", now_ms - 10 * 24 * 60 * 60 * 1000)
        recent_contract = row("NIULAIUSDT", now_ms - 24 * 60 * 60 * 1000)
        new_contract = row("NEWUSDT", now_ms + 20 * 24 * 60 * 60 * 1000, "PENDING_TRADING")

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "aster-announcements.json"
            with (
                patch.object(server, "ASTER_ANNOUNCEMENT_STATE_PATH", state_path),
                patch.object(server.time, "time", return_value=now_seconds),
                patch.object(server, "aster_contract_rows", return_value=[recent_contract, old_contract]),
            ):
                first_rows = server.aster_contract_announcement_rows()

            with (
                patch.object(server, "ASTER_ANNOUNCEMENT_STATE_PATH", state_path),
                patch.object(server.time, "time", return_value=now_seconds + 12),
                patch.object(server, "aster_contract_rows", return_value=[new_contract, recent_contract, old_contract]),
            ):
                second_rows = server.aster_contract_announcement_rows()

        self.assertEqual([item["symbol"] for item in first_rows], ["NIULAIUSDT"])
        self.assertEqual([item["symbol"] for item in second_rows], ["NEWUSDT", "NIULAIUSDT"])
        self.assertEqual(second_rows[0]["status"], "待上线")
        self.assertNotIn("OLDUSDT", [item["symbol"] for item in second_rows])

    def test_aster_feed_baselines_existing_contracts_then_alerts_once_for_new_symbol(self):
        state = {"seen": {}, "ready": []}
        existing = {
            "symbol": "TUTUSDT",
            "baseAsset": "TUT",
            "quoteAsset": "USDT",
            "contractStatus": "TRADING",
            "date": 1_786_000_000_000,
            "firstDiscoveredAt": 1_786_000_000_000,
        }
        new_contract = {
            "symbol": "NEWUSDT",
            "baseAsset": "NEW",
            "quoteAsset": "USDT",
            "contractStatus": "TRADING",
            "date": 1_787_000_000_000,
            "firstDiscoveredAt": 1_787_000_000_000,
        }
        payloads = iter(
            [
                {"updatedAt": 1_786_000_000_000, "items": [existing]},
                {"updatedAt": 1_787_000_000_000, "items": [new_contract, existing]},
            ]
        )
        feed = {
            "name": "aster-contracts",
            "maxAgeMs": 365 * 24 * 60 * 60 * 1000,
            "fetch": lambda: next(payloads),
            "parse": server.parse_site_aster_contract_events,
        }

        with (
            patch.object(server, "load_site_alert_state", return_value=state),
            patch.object(server, "save_site_alert_state"),
            patch.object(server, "launch_desktop_alert") as launch_alert,
            patch.object(server.time, "time", return_value=1_787_000_000),
        ):
            server.sync_site_alert_feed(feed)
            self.assertEqual(launch_alert.call_count, 0)
            server.sync_site_alert_feed(feed)

        self.assertEqual(launch_alert.call_count, 1)
        self.assertEqual(launch_alert.call_args.args[0]["key"], "aster-contract:NEWUSDT")

    def test_rank_new_entry_has_speech_but_other_rank_changes_do_not(self):
        row = {
            "key": "binance:CRYPTO:HYPE",
            "assetKey": "CRYPTO:HYPE",
            "sourceTitle": "Binance 热门币种",
            "sourceLabel": "BN",
            "group": "crypto",
            "symbol": "HYPE",
            "rank": 6,
            "price": "$42",
            "change": 8.5,
        }

        new_event = server.rank_monitor_event("hot", row, "new")
        rank_event = server.rank_monitor_event("hot", row, "rank")

        self.assertEqual(new_event["speech"], "榜单新进，HYPE 新进入热门榜前十。")
        self.assertNotIn("speech", rank_event)

    def test_binance_wallet_new_entry_uses_contract_identity_and_specific_speech(self):
        source = {
            "id": "binance-wallet-hot",
            "title": "币安钱包热门榜",
            "sourceLabel": "BW",
            "group": "crypto",
            "period": "24h",
            "periodLabel": "24 小时",
        }
        row = {
            "rank": 3,
            "symbol": "我的女友景甜",
            "name": "我的女友景甜",
            "chain": "56",
            "chainLabel": "BSC",
            "contractAddress": "0xFf673079235560e4de3fe4554c9981d759Af7777",
            "url": "https://web3.binance.com/en/token/bsc/0xff673079235560e4de3fe4554c9981d759af7777",
        }

        snapshot = server.rank_monitor_snapshot(source, row, 2, "hot")
        event = server.rank_monitor_event("hot", snapshot, "new")

        self.assertEqual(snapshot["assetKey"], "WALLET:56:0xff673079235560e4de3fe4554c9981d759af7777")
        self.assertEqual(event["kind"], "币安钱包24小时热门榜新进")
        self.assertEqual(event["title"], "币安钱包24小时热门榜新进：我的女友景甜")
        self.assertEqual(event["speech"], "币安钱包热门榜新进，我的女友景甜 新进入24小时热门榜前十。")
        self.assertEqual(event["url"], row["url"])
        self.assertEqual(event["queuePriority"], 74)

    def test_binance_wallet_monitor_baselines_then_broadcasts_every_new_top_ten_entry(self):
        def source(rows):
            return {
                "id": "binance-wallet-hot",
                "title": "币安钱包热门榜",
                "sourceLabel": "BW",
                "group": "crypto",
                "period": "24h",
                "periodLabel": "24 小时",
                "status": "ok",
                "rows": rows,
            }

        old_row = {
            "rank": 1,
            "symbol": "FONE",
            "chain": "CT_501",
            "contractAddress": "So11111111111111111111111111111111111111112",
        }
        first_new = {
            "rank": 2,
            "symbol": "我的女友景甜",
            "chain": "56",
            "contractAddress": "0xff673079235560e4de3fe4554c9981d759af7777",
        }
        second_new = {
            "rank": 3,
            "symbol": "NEWMEME",
            "chain": "8453",
            "contractAddress": "0x1111111111111111111111111111111111111111",
        }
        with (
            TemporaryDirectory() as directory,
            patch.object(
                server,
                "BINANCE_WALLET_HOT_ALERT_STATE_PATH",
                Path(directory) / "binance-wallet-hot-alert-state.json",
            ),
            patch.object(server, "launch_desktop_alert") as launch_alert,
        ):
            with patch.object(server.time, "time", return_value=1_800_000_000):
                events = server.sync_binance_wallet_hot_alert_feed(source([old_row]))
            self.assertEqual(events, [])
            self.assertEqual(launch_alert.call_count, 0)
            with patch.object(server.time, "time", return_value=1_800_000_031):
                events = server.sync_binance_wallet_hot_alert_feed(source([old_row, first_new, second_new]))

        self.assertEqual(len(events), 2)
        self.assertEqual(launch_alert.call_count, 2)
        titles = {call.args[0]["title"] for call in launch_alert.call_args_list}
        self.assertIn("币安钱包24小时热门榜新进：我的女友景甜", titles)
        self.assertIn("币安钱包24小时热门榜新进：NEWMEME", titles)

    def test_stock_rank_new_entry_uses_company_name(self):
        row = {
            "key": "futu-us:US:NVDA",
            "assetKey": "US:NVDA",
            "sourceTitle": "富途美股热门榜",
            "sourceLabel": "US",
            "group": "us",
            "symbol": "英伟达",
            "name": "英伟达",
            "rank": 3,
        }

        event = server.rank_monitor_event("hot", row, "new")

        self.assertEqual(event["speech"], "榜单新进，英伟达 新进入美股热门榜前十。")

    def test_listing_events_have_exchange_or_listing_speech(self):
        payload = {
            "updatedAt": 1_786_000_000_000,
            "sections": [
                {
                    "sourceName": "Listings",
                    "rows": [
                        {"id": "crypto-1", "group": "crypto", "title": "Binance 将上线 TEST"},
                        {"id": "ipo-1", "group": "ipo", "title": "测试科技将在纳斯达克上市"},
                    ],
                }
            ],
        }

        events = server.parse_site_listing_events(payload)

        self.assertEqual(events[0]["speech"], "交易所上新提醒，Binance 将上线 TEST。")
        self.assertEqual(events[1]["speech"], "上市信息提醒，测试科技将在纳斯达克上市。")

    def test_gainers_leader_has_speech(self):
        payload = {
            "updatedAt": 1_786_000_000_000,
            "sources": [
                {
                    "id": "binance-gainers",
                    "title": "Binance 涨幅榜",
                    "sourceLabel": "BN",
                    "group": "crypto",
                    "rows": [{"rank": 1, "symbol": "HYPE", "name": "Hyperliquid", "change": "+18.5%"}],
                }
            ],
        }

        event = server.parse_site_gainers_events(payload)[0]

        self.assertEqual(event["speech"], "涨幅榜榜首异动，HYPE 成为Binance 涨幅榜榜首。")

    def test_tut_rotation_map_keeps_family_and_real_market_candidates(self):
        market = {
            "sources": [
                {
                    "id": "binance",
                    "title": "Binance 热门币种",
                    "rows": [
                        {"rank": 1, "symbol": "TUT", "name": "Tutorial", "change": "+18%"},
                    ],
                }
            ]
        }
        tickers = {
            "TUT": {
                "symbol": "TUT",
                "priceValue": 0.082,
                "changeValue": 18.0,
                "turnoverValue": 12_000_000,
                "exchange": "Binance Futures",
                "marketSymbol": "TUTUSDT",
            },
            "TST": {
                "symbol": "TST",
                "priceValue": 0.031,
                "changeValue": 3.0,
                "turnoverValue": 4_000_000,
                "exchange": "Binance Futures",
                "marketSymbol": "TSTUSDT",
            },
            "MUBARAK": {
                "symbol": "MUBARAK",
                "priceValue": 0.044,
                "changeValue": 5.0,
                "turnoverValue": 3_500_000,
                "exchange": "Bitget Futures",
                "marketSymbol": "MUBARAKUSDT",
            },
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers=tickers,
            leader_metrics={
                "TUT": {
                    "impulseGainPct": 520.0,
                    "launchLow": 0.01,
                    "swingHigh": 0.082,
                }
            },
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["maps"][0]["family"], "Four.meme 家族")
        self.assertEqual(payload["maps"][0]["leader"]["symbol"], "TUT")
        self.assertIn("币安人生", payload["maps"][0]["familyLabels"])
        self.assertEqual(
            {item["symbol"] for item in payload["maps"][0]["candidates"]},
            {"TST", "MUBARAK"},
        )
        self.assertTrue(all(item["exchange"] for item in payload["maps"][0]["candidates"]))

    def test_binance_life_symbol_uses_native_contract_pair(self):
        self.assertEqual(server.clean_price_watch_symbol("BIANRENSHENGUSDT.P"), "BIANRENSHENG")
        self.assertEqual(server.clean_price_watch_symbol("币安人生USDT"), "BIANRENSHENG")
        self.assertEqual(server.binance_price_watch_pair("BIANRENSHENG"), "币安人生USDT")
        group = next(item for item in server.rotation_theme_groups() if item["id"] == "four-meme")
        self.assertIn({"symbol": "BIANRENSHENG", "name": "币安人生"}, group["members"])
        self.assertNotIn({"symbol": "BANANAS31", "name": "币安人生"}, group["members"])

    def test_rotation_map_uses_dynamic_same_chain_candidates(self):
        market = {
            "sources": [
                {
                    "id": "ave",
                    "title": "Ave.ai 热搜榜",
                    "rows": [
                        {
                            "rank": 1,
                            "symbol": "CYS",
                            "name": "CYS",
                            "change": "+12%",
                            "price": "$0.12",
                            "priceValue": 0.12,
                            "amount": "$2.00M",
                            "chain": "bsc",
                            "chainLabel": "BNB Chain",
                            "url": "https://ave.ai/token/cys",
                        },
                        {
                            "rank": 2,
                            "symbol": "TOAD",
                            "name": "TOAD",
                            "change": "+4%",
                            "price": "$0.03",
                            "priceValue": 0.03,
                            "amount": "$900.00K",
                            "chain": "bsc",
                            "chainLabel": "BNB Chain",
                            "url": "https://ave.ai/token/toad",
                        },
                    ],
                }
            ]
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers={},
            leader_metrics={
                "CYS": {
                    "impulseGainPct": 365.0,
                    "launchLow": 0.02,
                    "swingHigh": 0.12,
                }
            },
        )

        self.assertEqual(payload["summary"]["leaders"], 1)
        self.assertEqual(payload["maps"][0]["mappingStatus"], "mapped")
        self.assertEqual(payload["maps"][0]["candidates"][0]["symbol"], "TOAD")
        self.assertEqual(payload["maps"][0]["candidates"][0]["url"], "https://ave.ai/token/toad")

    def test_rotation_map_keeps_qualified_leader_while_candidates_are_analyzed(self):
        market = {
            "sources": [
                {
                    "id": "binance",
                    "title": "Binance 热门币种",
                    "rows": [
                        {
                            "rank": 1,
                            "symbol": "MMT",
                            "name": "MMT",
                            "change": "+9%",
                            "price": "$0.20",
                            "priceValue": 0.2,
                            "amountValue": 1_000_000,
                        }
                    ],
                }
            ]
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers={},
            leader_metrics={
                "MMT": {
                    "impulseGainPct": 367.0,
                    "launchLow": 0.04,
                    "swingHigh": 0.2,
                }
            },
        )

        self.assertEqual(payload["summary"]["leaders"], 1)
        self.assertEqual(payload["summary"]["analyzing"], 1)
        self.assertEqual(payload["maps"][0]["mappingStatus"], "analyzing")
        self.assertEqual(payload["maps"][0]["candidates"], [])

    def test_rotation_map_excludes_leaders_below_300_percent(self):
        market = {
            "sources": [
                {
                    "id": "binance",
                    "title": "Binance 热门币种",
                    "rows": [{"rank": 1, "symbol": "LOW", "name": "LOW", "change": "+8%"}],
                }
            ]
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers={},
            leader_metrics={"LOW": {"impulseGainPct": 299.9, "launchLow": 1, "swingHigh": 3.999}},
        )

        self.assertEqual(payload["summary"]["leaders"], 0)
        self.assertEqual(payload["maps"], [])

    def test_rotation_map_adds_only_gainer_leaders_observed_after_leader_launch(self):
        now_ms = 1_800_000_000_000
        launch_at = now_ms - 2 * 24 * 60 * 60 * 1000
        market = {
            "sources": [
                {
                    "id": "binance",
                    "title": "Binance 热门币种",
                    "rows": [{"rank": 1, "symbol": "LEAD", "name": "Leader", "change": "+30%"}],
                }
            ]
        }
        tickers = {
            symbol: {
                "symbol": symbol,
                "priceValue": 1,
                "changeValue": 5,
                "turnoverValue": 2_000_000,
                "exchange": "Binance Futures",
                "marketSymbol": f"{symbol}USDT",
            }
            for symbol in ("LEAD", "TOP", "OLD")
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers=tickers,
            leader_metrics={
                "LEAD": {
                    "impulseGainPct": 410,
                    "launchLow": 1,
                    "swingHigh": 5.1,
                    "launchAt": launch_at,
                }
            },
            gainer_history=[
                {
                    "assetKey": "CRYPTO:TOP",
                    "symbol": "TOP",
                    "name": "Top Coin",
                    "sourceTitle": "Binance 涨幅榜",
                    "observedAt": now_ms - 24 * 60 * 60 * 1000,
                },
                {
                    "assetKey": "CRYPTO:OLD",
                    "symbol": "OLD",
                    "name": "Old Coin",
                    "sourceTitle": "OKX 合约涨幅榜",
                    "observedAt": now_ms - 3 * 24 * 60 * 60 * 1000,
                },
            ],
            now_ms=now_ms,
        )

        candidates = {item["symbol"]: item for item in payload["maps"][0]["candidates"]}
        self.assertIn("TOP", candidates)
        self.assertNotIn("OLD", candidates)
        self.assertIn("post-leader-gainer-top", candidates["TOP"]["signals"])

    def test_rotation_map_merges_family_and_gainer_evidence(self):
        now_ms = 1_800_000_000_000
        launch_at = now_ms - 2 * 24 * 60 * 60 * 1000
        market = {
            "sources": [
                {
                    "id": "binance",
                    "title": "Binance 热门币种",
                    "rows": [{"rank": 1, "symbol": "TUT", "name": "Tutorial", "change": "+20%"}],
                }
            ]
        }
        tickers = {
            "TUT": {
                "symbol": "TUT",
                "priceValue": 0.08,
                "changeValue": 20,
                "turnoverValue": 12_000_000,
                "exchange": "Binance Futures",
                "marketSymbol": "TUTUSDT",
            },
            "TST": {
                "symbol": "TST",
                "priceValue": 0.03,
                "changeValue": 3,
                "turnoverValue": 4_000_000,
                "exchange": "Binance Futures",
                "marketSymbol": "TSTUSDT",
            },
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers=tickers,
            leader_metrics={
                "TUT": {
                    "impulseGainPct": 520,
                    "launchLow": 0.01,
                    "swingHigh": 0.08,
                    "launchAt": launch_at,
                }
            },
            gainer_history=[
                {
                    "assetKey": "CRYPTO:TST",
                    "symbol": "TST",
                    "name": "Test Token",
                    "sourceTitle": "Binance 涨幅榜",
                    "observedAt": now_ms - 60 * 60 * 1000,
                }
            ],
            now_ms=now_ms,
        )

        matches = [item for item in payload["maps"][0]["candidates"] if item["symbol"] == "TST"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            set(matches[0]["signals"]),
            {"family", "post-leader-gainer-top"},
        )

    def test_gainer_leader_history_baselines_then_records_real_change(self):
        first_payload = {
            "sources": [
                {
                    "id": "binance-gainers",
                    "title": "Binance 涨幅榜",
                    "group": "crypto",
                    "rows": [{"rank": 1, "symbol": "AAA", "name": "AAA", "change": "+30%"}],
                }
            ]
        }
        history, current = server.rank_monitor_update_gainer_leader_history({}, first_payload, 1000)

        self.assertEqual(history, [])
        self.assertEqual(current["binance-gainers"]["symbol"], "AAA")

        second_payload = {
            "sources": [
                {
                    "id": "binance-gainers",
                    "title": "Binance 涨幅榜",
                    "group": "crypto",
                    "rows": [{"rank": 1, "symbol": "BBB", "name": "BBB", "change": "+35%"}],
                }
            ]
        }
        history, current = server.rank_monitor_update_gainer_leader_history(
            {"gainerLeaderHistory": history, "gainerCurrentLeaders": current},
            second_payload,
            1100,
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["symbol"], "BBB")
        self.assertEqual(history[0]["observedAt"], 1_100_000)
        self.assertEqual(current["binance-gainers"]["symbol"], "BBB")


if __name__ == "__main__":
    unittest.main()
