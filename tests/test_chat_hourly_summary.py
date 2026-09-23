import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from wechat_group_monitor import extract_candidate_symbols, message_is_idle_chat


class ChatHourlySummaryTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = self.db_path
        server.init_auth_db()
        now = int(time.time())
        with server.auth_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, password_hash, role, created_at, updated_at)
                VALUES ('chat-hourly-user', 'test', 'user', ?, ?)
                """,
                (now, now),
            )
            self.user_id = int(cursor.lastrowid)

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.db_path.unlink(missing_ok=True)

    def test_symbol_filter_keeps_explicit_tokens_but_drops_uppercase_chat_words(self):
        symbols = extract_candidate_symbols(
            "BREAKING NEWS UPDATE FROM TEAM ROBINHOOD WETH V2 TIP：Binance 将上线 $AI 和 LUMPYUSDT"
        )

        self.assertEqual(symbols, ["AI", "LUMPYUSDT"])
        self.assertTrue(message_is_idle_chat("OK THANKS GM"))
        self.assertFalse(message_is_idle_chat("BSC 新币 CA: 0x1234567890abcdef1234567890abcdef12345678"))

    def test_rich_bot_message_keeps_main_ca_and_drops_holder_addresses_and_labels(self):
        contract = "0x10b409f69989bc34e36a5105874f6d64e3eb0bff"
        holder = "0x267444d099b10fb5ed7c3cc7b7c767adca574952"
        pvp = "0x6b09c294ecc9fbe3285f0421eb76ebb6222ade64"
        profile = server.chat_message_signal_profile(
            f"回复 CA: {contract} [Maple](https://ponsfamily.com/launchpad/{contract}) "
            f"Robinhood USDG V2 TH TIP [持仓](https://rh-scan.com/address/{holder}) "
            f"[PVP](https://t.me/rick?start={pvp}) [行情](https://defined.fi/token/robinhood/{contract})"
        )

        self.assertEqual(profile["symbols"], ["MAPLE"])
        self.assertEqual([item["contractAddress"] for item in profile["contracts"]], [contract])

    def test_all_group_sources_record_source_backed_symbols_and_contracts(self):
        contract = "0x1234567890abcdef1234567890abcdef12345678"
        signals = []
        for index, platform in enumerate(("wechat", "qq")):
            signals.append(server.record_chat_group_signal_mention(
                self.user_id,
                f"{platform}-链上群",
                {
                    "platform": platform,
                    "sender": "caller",
                    "content": f"BREAKING NEWS $LUMPY MCAP: 1M CA: {contract}",
                    "capturedAt": 4_000 + index,
                    "hash": f"signal-{platform}",
                },
            ))
        ignored = server.record_chat_group_signal_mention(
            self.user_id,
            "链上群",
            {
                "platform": "qq", "sender": "caller", "content": "OK THANKS GM",
                "capturedAt": 4_001, "hash": "idle-one",
            },
        )

        with server.auth_db() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_group_signal_mentions WHERE user_id = ?",
                (self.user_id,),
            ).fetchall()
        self.assertTrue(all(signal["recorded"] for signal in signals))
        self.assertFalse(ignored["trackable"])
        self.assertEqual(len(rows), 2)
        self.assertIn("LUMPY", rows[0]["symbols_json"])
        self.assertIn(contract, rows[0]["contracts_json"])

    def test_previous_hour_is_ranked_once_with_first_to_current_market_cap(self):
        contract = "0x1234567890abcdef1234567890abcdef12345678"
        for platform, group, sender, captured_at, market_cap, message_hash in (
            ("qq", "链上群", "alice", 4_000, "1M", "rank-a"),
            ("wechat", "金CA产生器", "bob", 4_600, "1.2M", "rank-b"),
        ):
            server.record_chat_group_signal_mention(
                self.user_id,
                group,
                {
                    "platform": platform,
                    "sender": sender,
                    "content": f"$LUMPY MCAP: {market_cap} CA: {contract}",
                    "capturedAt": captured_at,
                    "hash": message_hash,
                },
            )

        def market_lookup(_identity):
            return {
                "symbol": "LUMPY", "name": "Lumpy", "chain": "56", "chainLabel": "BSC",
                "marketCapUsd": 2_000_000, "sourceUrl": "https://example.test/lumpy",
            }

        with patch.object(server, "launch_desktop_alert") as alert:
            result = server.generate_chat_hourly_summaries(
                now_seconds=7_275,
                user_id=self.user_id,
                market_lookup=market_lookup,
            )
            repeated = server.generate_chat_hourly_summaries(
                now_seconds=7_290,
                user_id=self.user_id,
                market_lookup=market_lookup,
            )

        summaries = server.chat_hourly_summary_rows(self.user_id)
        combined = next(item for item in summaries if item["scopeKey"] == "all")
        ranked = combined["items"][0]
        self.assertEqual(result["generated"], 3)
        self.assertEqual(repeated["generated"], 0)
        self.assertEqual(ranked["symbol"], "LUMPY")
        self.assertEqual(ranked["mentionCount"], 2)
        self.assertEqual(ranked["initialMarketCapUsd"], 1_000_000)
        self.assertEqual(ranked["currentMarketCapUsd"], 2_000_000)
        self.assertEqual(ranked["changePct"], 100.0)
        self.assertIn("1M → 2M Δ+100%", combined["text"])
        self.assertIn("alice", combined["text"])
        self.assertEqual(alert.call_count, 1)

    def test_multiple_local_accounts_share_one_hourly_popup(self):
        now = int(time.time())
        contract = "0x9999999999999999999999999999999999999999"
        with server.auth_db() as conn:
            second = conn.execute(
                """
                INSERT INTO users (username, password_hash, role, created_at, updated_at)
                VALUES ('chat-hourly-second', 'test', 'user', ?, ?)
                """,
                (now, now),
            ).lastrowid
            for user_id, group_name in ((self.user_id, "群 A"), (int(second), "群 B")):
                conn.execute(
                    """
                    INSERT INTO wechat_group_monitors
                        (user_id, group_name, enabled, baseline_ready, last_status, created_at, updated_at)
                    VALUES (?, ?, 1, 1, 'connected', ?, ?)
                    """,
                    (user_id, group_name, now, now),
                )
                server.record_chat_group_signal_mention(
                    user_id,
                    group_name,
                    {
                        "platform": "wechat", "sender": "caller",
                        "content": f"$ONE CA: {contract}", "capturedAt": 4_000,
                        "hash": f"hourly-{user_id}",
                    },
                    conn=conn,
                )

        with patch.object(server, "launch_desktop_alert") as alert:
            result = server.generate_chat_hourly_summaries(
                now_seconds=7_275,
                market_lookup=lambda _identity: {
                    "symbol": "ONE", "chain": "56", "chainLabel": "BSC",
                    "marketCapUsd": 1_000_000,
                },
            )

        self.assertEqual(result["users"], 2)
        self.assertEqual(alert.call_count, 1)
        self.assertEqual(alert.call_args.args[0]["key"], "chat-ca-hourly:3600")

    def test_late_account_cannot_reopen_an_already_delivered_hour(self):
        contract = "0x8888888888888888888888888888888888888888"
        server.record_chat_group_signal_mention(
            self.user_id,
            "群 A",
            {
                "platform": "wechat", "sender": "alice",
                "content": f"$ONCE CA: {contract}", "capturedAt": 4_000,
                "hash": "once-first",
            },
        )
        now = int(time.time())
        with server.auth_db() as conn:
            second = int(conn.execute(
                """INSERT INTO users (username,password_hash,role,created_at,updated_at)
                   VALUES ('chat-hourly-late','test','user',?,?)""",
                (now, now),
            ).lastrowid)
        server.record_chat_group_signal_mention(
            second,
            "群 B",
            {
                "platform": "qq", "sender": "bob",
                "content": f"$ONCE CA: {contract}", "capturedAt": 4_100,
                "hash": "once-late",
            },
        )

        market = lambda _identity: {"symbol": "ONCE", "chain": "56", "marketCapUsd": 1_000_000}
        with patch.object(server, "launch_desktop_alert") as alert:
            server.generate_chat_hourly_summaries(
                now_seconds=7_275, user_id=self.user_id, market_lookup=market,
            )
            server.generate_chat_hourly_summaries(
                now_seconds=7_275, user_id=second, market_lookup=market,
            )

        self.assertEqual(alert.call_count, 1)

    def test_frontend_renders_hourly_ca_rank_and_filter_note(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "price-watch.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        html = (root / "price-watch.html").read_text(encoding="utf-8")

        self.assertIn("chatHourlySummaryTemplate", source)
        self.assertIn("闲聊与非币种英文词已过滤", source)
        self.assertIn("chat-hourly-rank-row", styles)
        self.assertIn("price-watch.js?v=106", html)


if __name__ == "__main__":
    unittest.main()
