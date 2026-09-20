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

    def test_x_kol_ai_value_filter_suppresses_noise_and_keeps_information(self):
        events = [
            {"key": "x-noise", "source": "A", "xCategory": "kol", "originalText": "gm everyone"},
            {"key": "x-value", "source": "B", "xCategory": "kol", "originalText": "项目主网上线并公布回购"},
        ]
        response = {
            "choices": [{"message": {"content": '{"items":['
                '{"key":"x-1","valuable":false,"valueScore":10,"informationType":"噪音","reason":"日常问候"},'
                '{"key":"x-2","valuable":true,"valueScore":90,"informationType":"项目进展","reason":"主网上线与回购"}'
                ']}'}}],
        }
        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(server, "deepseek_enabled", return_value=True),
                patch.object(server, "deepseek_chat", return_value=response),
            ):
                accepted = server.x_kol_ai_filter_events(
                    events,
                    cache_path=Path(temp_dir) / "x-filter.json",
                    settings={"provider": "test", "model": "test", "apiKey": "test", "maxTokens": 1000},
                )
        self.assertEqual([event["key"] for event in accepted], ["x-value"])

    def test_x_tracking_never_emits_raw_popup(self):
        now_ms = 1_800_000_000_000
        events = [
            {"key": "official", "title": "官方更新", "time": now_ms, "xCategory": "project_official"},
            {"key": "kol", "title": "普通 KOL 有价值观点", "time": now_ms, "xCategory": "kol"},
        ]
        state = {"ready": ["x-kol"], "seen": {}}
        feed = {"name": "x-kol", "maxAgeMs": 10 * 60 * 1000, "fetch": lambda: {}, "parse": lambda payload: events}
        with (
            patch.object(server, "load_site_alert_state", return_value=state),
            patch.object(server, "save_site_alert_state"),
            patch.object(server, "x_kol_ai_filter_events", side_effect=lambda rows: rows) as filter_events,
            patch.object(server, "launch_desktop_alert") as launch_alert,
            patch.object(server.time, "time", return_value=now_ms / 1000),
        ):
            server.sync_site_alert_feed(feed)
        filter_events.assert_not_called()
        launch_alert.assert_not_called()

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
                patch.object(server, "x_kol_fetch_rss_source", return_value=x_payload),
                patch.object(server, "x_kol_fetch_api_source", side_effect=AssertionError("Aster must not use paid X API")),
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

        self.assertEqual(server.parse_site_aster_contract_events(payload), [])

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

        self.assertEqual(launch_alert.call_count, 0)

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
            "url": "https://www.binance.com/zh-CN/futures/TESTUSDT",
        }

        snapshot = server.rank_monitor_snapshot(source, row, 2, "hot")
        event = server.rank_monitor_event("hot", snapshot, "new")

        self.assertEqual(snapshot["assetKey"], "WALLET:56:0xff673079235560e4de3fe4554c9981d759af7777")
        self.assertEqual(event["kind"], "币安钱包24小时热门榜新进")
        self.assertEqual(event["title"], "币安钱包24小时热门榜新进：我的女友景甜")
        self.assertEqual(event["speech"], "币安钱包热门榜新进，我的女友景甜 新进入24小时热门榜前十。")
        self.assertEqual(
            event["url"],
            "https://web3.binance.com/en/token/bsc/0xFf673079235560e4de3fe4554c9981d759Af7777?ref=MQ6JD2X4",
        )
        self.assertEqual(event["contractAddress"], row["contractAddress"])
        self.assertEqual(event["chain"], row["chain"])
        self.assertEqual(event["queuePriority"], 74)

    def test_binance_wallet_four_hour_popup_body_is_one_sentence_project_intro(self):
        source = {
            "id": "binance-wallet-hot",
            "title": "币安钱包热门榜",
            "sourceLabel": "BW",
            "group": "crypto",
            "period": "4h",
            "periodLabel": "4 小时",
        }
        row = {
            "rank": 9,
            "symbol": "PERPSPAD",
            "name": "PERPSPAD",
            "price": "$0.00023594",
            "change": "+28.99%",
            "amount": 2_720_000,
            "turnover": "4 小时成交 $2.72M",
            "chain": "4663",
            "chainLabel": "Robinhood",
            "contractAddress": "0x14778a17d61ccc17a22265b6d7d434c7a5559700",
            "narrativeLabel": "链上生态 / 交易平台",
            "narrativeLabels": ["链上生态 / 交易平台"],
            "url": "https://web3.binance.com/en/token/robinhood/0x14778a17d61ccc17a22265b6d7d434c7a5559700",
        }

        snapshot = server.rank_monitor_snapshot(source, row, 8, "hot")
        event = server.rank_monitor_event("hot", snapshot, "new")

        self.assertEqual(
            event["body"],
            "PERPSPAD 是 Robinhood 上主打链上生态、交易平台叙事的代币。",
        )
        self.assertNotIn("排名", event["body"])
        self.assertNotIn("$", event["body"])
        self.assertIn("榜单排名 #9", event["explanationContext"]["marketSnapshot"])
        self.assertIn("价格 $0.00023594", event["explanationContext"]["marketSnapshot"])

    def test_binance_wallet_four_hour_popup_prefers_official_binance_ai_narrative(self):
        source = {
            "id": "binance-wallet-hot",
            "title": "币安钱包热门榜",
            "sourceLabel": "BW",
            "group": "crypto",
            "period": "4h",
            "periodLabel": "4 小时",
        }
        row = {
            "rank": 4,
            "symbol": "STONK",
            "name": "STONK",
            "chain": "CT_501",
            "chainLabel": "Solana",
            "contractAddress": "6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
            "binanceAiNarrative": "STONK 源自互联网迷因 stonks，聚焦社区驱动的金融幽默文化。",
            "narrativeLabel": "链上生态 / 交易平台",
        }

        snapshot = server.rank_monitor_snapshot(source, row, 3, "hot")
        event = server.rank_monitor_event("hot", snapshot, "new")

        self.assertEqual(
            event["body"],
            "STONK 源自互联网迷因 stonks，聚焦社区驱动的金融幽默文化。",
        )
        self.assertNotIn("链上生态", event["body"])

    def test_binance_wallet_monitor_baselines_then_broadcasts_every_new_top_ten_entry(self):
        def source(rows):
            return {
                "id": "binance-wallet-hot",
                "title": "币安钱包热门榜",
                "sourceLabel": "BW",
                "group": "crypto",
                "period": "4h",
                "periodLabel": "4 小时",
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
        self.assertIn("币安钱包4小时热门榜新进：我的女友景甜", titles)
        self.assertIn("币安钱包4小时热门榜新进：NEWMEME", titles)

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

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["speech"], "上市信息提醒，测试科技将在纳斯达克上市。")

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

    def test_rotation_map_uses_dynamic_semantic_candidates(self):
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
                            "themes": ["Animal Meme"],
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
                            "themes": ["Animal Meme"],
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

    def test_rotation_map_does_not_treat_source_or_ranking_tags_as_a_theme(self):
        market = {
            "sources": [{
                "id": "okx",
                "title": "OKX 热门币种",
                "rows": [
                    {"rank": 1, "symbol": "PONS", "name": "PONS", "tags": ["OKX official hot", "24 小时"]},
                    {"rank": 2, "symbol": "ARB", "name": "ARB", "tags": ["OKX official hot", "24 小时"]},
                ],
            }],
        }
        tickers = {
            symbol: {
                "symbol": symbol,
                "priceValue": 1,
                "changeValue": 2,
                "turnoverValue": 2_000_000,
                "exchange": "OKX SWAP",
            }
            for symbol in ("PONS", "ARB")
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers=tickers,
            leader_metrics={"PONS": {"impulseGainPct": 3738.2}},
        )

        self.assertEqual(payload["maps"][0]["leader"]["symbol"], "PONS")
        self.assertEqual(payload["maps"][0]["candidates"], [])

    def test_rotation_discussion_does_not_confuse_meme_word_with_meme_token(self):
        discussion = [{
            "source": "X/KOL",
            "time": 1_800_000_000_000,
            "text": "Aster 吉祥物形成社区 MEME，真正被提及的标的是 $DUST。",
        }]

        meme_context = server.deepseek_row_discussion_context(
            {"symbol": "MEME", "name": "A Meme Coin"},
            discussion,
        )
        dust_context = server.deepseek_row_discussion_context(
            {"symbol": "DUST", "name": "Dust"},
            discussion,
        )

        self.assertEqual(meme_context, "")
        self.assertIn("$DUST", dust_context)

    def test_rotation_rejects_cached_aster_mascot_claim_for_meme_token(self):
        leaders = server.normalize_rotation_ai_leaders(
            [{
                "symbol": "MEME",
                "confidence": 88,
                "family": "Aster 关联 Meme",
                "narratives": ["Aster 吉祥物叙事"],
                "reason": "借助 Aster 吉祥物形成社区 Meme",
                "peers": [],
            }],
            {"MEME", "DUST"},
        )

        self.assertEqual(leaders, [])

    def test_real_animal_meme_family_requires_real_subject_evidence(self):
        evidenced = server.rotation_meme_family_context({
            "name": "KABOSU MOM",
            "tags": ["动物 Meme"],
            "discussion": "这是现实中的宠物狗 Kabosu 的妈妈，同一家族原型。",
        })
        generic = server.rotation_meme_family_context({
            "name": "MOM COIN",
            "tags": ["妈妈主题 Meme"],
            "discussion": "社区把它叫作妈妈币。",
        })

        self.assertEqual(evidenced["entityType"], "real-animal")
        self.assertIn("妈妈", evidenced["roles"])
        self.assertEqual(generic, {})

    def test_rotation_ai_preserves_real_meme_family_subject_and_role(self):
        leaders = server.normalize_rotation_ai_leaders(
            [{
                "symbol": "DOGEONE",
                "confidence": 90,
                "memeEntityType": "real-animal",
                "familySubject": "Kabosu",
                "narratives": ["真实动物家族 Meme"],
                "reason": "基于同一真实宠物家族",
                "peers": [{
                    "symbol": "DOGEMOM",
                    "relationshipType": "family",
                    "familyRole": "妈妈",
                    "reason": "现实中属于同一宠物家族，角色是妈妈",
                }],
            }],
            {"DOGEONE", "DOGEMOM"},
        )

        self.assertEqual(leaders[0]["memeEntityType"], "real-animal")
        self.assertEqual(leaders[0]["familySubject"], "Kabosu")
        self.assertEqual(leaders[0]["peers"][0]["familyRole"], "妈妈")

        payload = server.rotation_map_payload(
            market={"sources": [{
                "id": "binance-wallet-hot",
                "title": "币安钱包热门",
                "rows": [
                    {"symbol": "DOGEONE", "name": "Doge One", "chain": "bsc"},
                    {"symbol": "DOGEMOM", "name": "Doge Mom", "chain": "solana"},
                ],
            }]},
            tickers={
                "DOGEONE": {"symbol": "DOGEONE", "priceValue": 1, "changeValue": 8, "turnoverValue": 2_000_000, "exchange": "Ave.ai"},
                "DOGEMOM": {"symbol": "DOGEMOM", "priceValue": 0.2, "changeValue": 2, "turnoverValue": 1_000_000, "exchange": "Ave.ai"},
            },
            leader_metrics={"DOGEONE": {"impulseGainPct": 450}},
            ai_snapshot={"status": "ready", "leaders": leaders},
        )
        mapping = payload["maps"][0]
        self.assertIn("妈妈 · Doge Mom", mapping["familyLabels"])
        self.assertEqual(mapping["candidates"][0]["familyRole"], "妈妈")

    def test_rotation_map_keeps_confirmed_pair_and_reverses_cross_chain_theme(self):
        market = {
            "sources": [{
                "id": "binance-wallet-hot",
                "title": "币安钱包热门",
                "rows": [
                    {"symbol": "PONS", "name": "PONS", "chain": "robinhood", "chainLabel": "Robinhood"},
                    {"symbol": "PAIR", "name": "PAIR", "chain": "robinhood", "chainLabel": "Robinhood"},
                    {"symbol": "BREW", "name": "BREW", "chain": "bsc", "chainLabel": "BNB Chain"},
                ],
            }],
        }
        tickers = {
            "PONS": {"symbol": "PONS", "priceValue": 1, "changeValue": 10, "turnoverValue": 5_000_000, "exchange": "Ave.ai"},
            "BREW": {"symbol": "BREW", "priceValue": 0.2, "changeValue": 3, "turnoverValue": 2_000_000, "exchange": "Ave.ai"},
        }
        ai_snapshot = {
            "status": "ready",
            "leaders": [
                {
                    "symbol": "PONS", "confidence": 96, "family": "币股配对发行平台", "narratives": ["币股配对发行平台"],
                    "reason": "主线龙头", "peers": [{
                        "symbol": "PAIR", "relationshipType": "theme", "reason": "同属币股配对发行平台题材",
                    }],
                },
                {
                    "symbol": "BREW", "confidence": 72, "family": "BSC Launchpad", "narratives": ["发行平台"],
                    "reason": "BSC 平台币", "peers": [{
                        "symbol": "PONS", "relationshipType": "theme", "reason": "复制 PONS 的发行平台路径",
                    }],
                },
            ],
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers=tickers,
            leader_metrics={"PONS": {"impulseGainPct": 500}},
            ai_snapshot=ai_snapshot,
        )
        pons = next(item for item in payload["maps"] if item["leader"]["symbol"] == "PONS")
        candidates = {item["symbol"]: item for item in pons["candidates"]}

        self.assertIn("PAIR", candidates)
        self.assertFalse(candidates["PAIR"]["marketDataAvailable"])
        self.assertIn("BREW", candidates)
        self.assertIn("cross-chain-type", candidates["BREW"]["signals"])

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

    def test_rotation_map_does_not_use_gainer_board_as_mapping_evidence(self):
        market = {
            "sources": [
                {
                    "id": "binance",
                    "title": "Binance 热门币种",
                    "rows": [{"rank": 1, "symbol": "LEAD", "name": "Leader", "change": "+30%"}],
                },
                {
                    "id": "binance-gainers",
                    "title": "Binance 涨幅榜",
                    "rows": [{"rank": 1, "symbol": "TOP", "name": "Top Coin", "change": "+900%"}],
                },
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
            for symbol in ("LEAD", "TOP")
        }

        payload = server.rotation_map_payload(
            market=market,
            tickers=tickers,
            leader_metrics={
                "LEAD": {
                    "impulseGainPct": 410,
                    "launchLow": 1,
                    "swingHigh": 5.1,
                    "launchAt": 1_799_000_000_000,
                }
            },
        )

        self.assertNotIn("TOP", {item["symbol"] for item in payload["maps"][0]["candidates"]})

    def test_rotation_map_ai_can_select_real_leader_below_fallback_threshold(self):
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
                    "impulseGainPct": 220,
                    "launchLow": 0.01,
                    "swingHigh": 0.08,
                    "launchAt": 1_799_000_000_000,
                }
            },
            ai_snapshot={
                "status": "ready",
                "provider": "test-ai",
                "updatedAt": 1_800_000_000_000,
                "leaders": [{
                    "symbol": "TUT",
                    "confidence": 91,
                    "leaderType": "Meme 叙事龙头",
                    "family": "Four.meme 家族",
                    "narratives": ["Four.meme", "Meme"],
                    "reason": "近期叙事心智与多源持续性领先",
                    "peers": [{
                        "symbol": "TST",
                        "relationshipType": "family",
                        "reason": "同属 Four.meme 家族",
                    }],
                }],
            },
        )

        matches = [item for item in payload["maps"][0]["candidates"] if item["symbol"] == "TST"]
        self.assertEqual(len(matches), 1)
        self.assertIn("family", matches[0]["signals"])
        self.assertEqual(payload["maps"][0]["leader"]["aiConfidence"], 91)

    def test_rotation_map_keeps_300_percent_onchain_leader_missing_from_old_ai_snapshot(self):
        market = {
            "sources": [{
                "id": "binance-wallet-hot",
                "title": "币安钱包热门",
                "rows": [
                    {"rank": 1, "symbol": "PONS", "name": "PONS", "themes": ["Robinhood Meme"]},
                    {"rank": 2, "symbol": "MARSCOIN", "name": "MarsCoin", "themes": ["Space Meme"]},
                ],
            }],
        }
        payload = server.rotation_map_payload(
            market=market,
            tickers={},
            leader_metrics={
                "PONS": {"impulseGainPct": 3738.2, "provider": "链上多源 K线"},
                "MARSCOIN": {"impulseGainPct": 630.42, "provider": "链上多源 K线"},
            },
            ai_snapshot={
                "status": "ready",
                "leaders": [{
                    "symbol": "PONS",
                    "confidence": 96,
                    "leaderType": "近期真实龙头",
                    "family": "Robinhood Meme",
                    "narratives": ["Robinhood Meme"],
                    "reason": "链上主升和叙事心智共振",
                    "peers": [],
                }],
            },
        )

        leaders = {item["leader"]["symbol"]: item["leader"] for item in payload["maps"]}
        self.assertEqual(leaders["PONS"]["impulseGainPct"], 3738.2)
        self.assertEqual(leaders["MARSCOIN"]["impulseGainPct"], 630.42)

    def test_rotation_ai_worker_merges_increment_without_losing_existing_result(self):
        pons = {
            "symbol": "PONS",
            "confidence": 96,
            "leaderType": "近期真实龙头",
            "family": "Robinhood Meme",
            "narratives": ["Robinhood Meme"],
            "reason": "既有结论",
            "peers": [],
        }
        mars_row = {"symbol": "MARSCOIN", "sources": ["币安钱包热门"], "impulseGainPct": 630.42}
        response = {
            "choices": [{"message": {"content": '{"leaders":[{"symbol":"MARSCOIN","confidence":92,"leaderType":"链上Meme龙头","family":"Space Meme","narratives":["Space Meme"],"reason":"链上主升超过六倍","peers":[]}]}'}}],
            "_provider": "codex-cli",
        }
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "rotation-ai.json"
            server.write_json_cache(cache_path, {
                "version": 3,
                "updatedAt": 1,
                "provider": "codex-cli",
                "analyzed": {},
                "leaders": [pons],
            })
            with patch.object(server, "ROTATION_AI_CACHE_PATH", cache_path), patch.object(server, "deepseek_chat", return_value=response):
                server.rotation_ai_worker([mars_row], [mars_row], {})
            no_leader_row = {"symbol": "OTHER", "sources": ["Binance"], "impulseGainPct": 20}
            empty_response = {
                "choices": [{"message": {"content": '{"leaders":[]}'}}],
                "_provider": "codex-cli",
            }
            with patch.object(server, "ROTATION_AI_CACHE_PATH", cache_path), patch.object(server, "deepseek_chat", return_value=empty_response):
                server.rotation_ai_worker([no_leader_row], [no_leader_row], {})
            stored = server.read_json_cache(cache_path)

        self.assertEqual({item["symbol"] for item in stored["leaders"]}, {"PONS", "MARSCOIN"})
        self.assertEqual(stored["version"], 3)
        self.assertEqual(
            stored["analyzed"]["MARSCOIN"]["fingerprint"],
            server.rotation_ai_row_fingerprint(mars_row),
        )
        self.assertIn("OTHER", stored["analyzed"])

    def test_rotation_ai_snapshot_has_no_ttl_rerun_for_unchanged_rows(self):
        row = {"symbol": "PONS", "sources": ["币安钱包热门"], "impulseGainPct": 3738.2}
        leader = {
            "symbol": "PONS",
            "confidence": 96,
            "leaderType": "近期真实龙头",
            "family": "Robinhood Meme",
            "narratives": ["Robinhood Meme"],
            "reason": "既有结论",
            "peers": [],
        }
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "rotation-ai.json"
            server.write_json_cache(cache_path, {
                "version": 3,
                "updatedAt": 1,
                "provider": "codex-cli",
                "analyzed": {"PONS": {"fingerprint": server.rotation_ai_row_fingerprint(row), "analyzedAt": 1}},
                "leaders": [leader],
            })
            with (
                patch.object(server, "ROTATION_AI_CACHE_PATH", cache_path),
                patch.object(server, "deepseek_enabled", return_value=True),
                patch.object(server.ROTATION_AI_POOL, "submit") as submit,
                patch.object(server, "ROTATION_AI_INFLIGHT", False),
                patch.object(server, "ROTATION_AI_RETRY_AFTER", 0.0),
            ):
                snapshot = server.rotation_ai_leader_snapshot([row])

        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["leaders"][0]["symbol"], "PONS")
        submit.assert_not_called()

    def test_rotation_alerts_baseline_then_reports_new_leader_and_semantic_change(self):
        initial = {"updatedAt": 1_800_000_000_000, "maps": []}
        leader = {
            "updatedAt": 1_800_000_060_000,
            "maps": [{
                "family": "PONS 家族",
                "themes": ["Robinhood Chain Meme"],
                "leader": {"symbol": "PONS", "aiConfidence": 92, "aiReason": "近期叙事心智领先"},
                "candidates": [{
                    "symbol": "AI",
                    "signals": ["family"],
                    "semanticReasons": ["同属 Robinhood Chain Meme 家族"],
                }],
            }],
        }
        changed = {
            **leader,
            "updatedAt": 1_800_000_120_000,
            "maps": [{
                **leader["maps"][0],
                "candidates": [
                    *leader["maps"][0]["candidates"],
                    {"symbol": "BONER", "signals": ["theme"], "semanticReasons": ["共享社区 Meme 题材"]},
                ],
            }],
        }
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "rotation-alert.json"
            self.assertEqual(server.parse_site_rotation_map_events(initial, state_path=state_path), [])
            new_events = server.parse_site_rotation_map_events(leader, state_path=state_path)
            self.assertEqual(server.parse_site_rotation_map_events(leader, state_path=state_path), [])
            changed_events = server.parse_site_rotation_map_events(changed, state_path=state_path)
        self.assertEqual(new_events[0]["title"], "新龙头入池：PONS")
        self.assertIn("BONER", changed_events[0]["body"])

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
