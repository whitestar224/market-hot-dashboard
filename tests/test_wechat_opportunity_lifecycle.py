import gc
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


DAY_MS = 24 * 60 * 60 * 1000


class WechatOpportunityLifecycleTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        self.original_db_path = server.AUTH_DB_PATH
        server.AUTH_DB_PATH = self.db_path
        server.init_auth_db()

    def tearDown(self):
        server.AUTH_DB_PATH = self.original_db_path
        gc.collect()
        self.db_path.unlink(missing_ok=True)

    def test_wechat_auth_popup_can_be_fully_suppressed(self):
        with patch.dict(os.environ, {"XINGYUN_DISABLE_WECHAT_AUTH_ALERTS": "1"}), patch.object(
            server, "wechat_login_begin_payload"
        ) as begin_login:
            result = server.notify_wechat_auth_required("授权失效", force=True)

        self.assertTrue(result["suppressed"])
        self.assertTrue(result["skipped"])
        begin_login.assert_not_called()

    def insert_opportunity(self, symbol="TUT", captured_at=None):
        captured_at = int(captured_at or time.time())
        now = int(time.time())
        with server.auth_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, password_hash, role, created_at, updated_at)
                VALUES (?, 'test', 'user', ?, ?)
                """,
                (f"tester-{symbol.lower()}", now, now),
            )
            user_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO wechat_group_messages (
                    user_id, group_name, message_hash, sender, content, captured_at,
                    candidate_score, is_opportunity, opportunity_score, category,
                    symbols_json, thesis, catalysts_json, risks_json, action_hint,
                    urgency, analysis_source, analyzed_at, created_at
                ) VALUES (?, '梦之队', ?, '群成员', '机会线索', ?, 90, 1, 90,
                          '项目', ?, '测试机会', '[]', '[]', '持续观察', 'normal',
                          'rules', ?, ?)
                """,
                (
                    user_id,
                    f"hash-{symbol.lower()}",
                    captured_at,
                    json.dumps([symbol]),
                    now,
                    now,
                ),
            )

    def create_user(self, suffix="dedupe"):
        now = int(time.time())
        with server.auth_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, password_hash, role, created_at, updated_at)
                VALUES (?, 'test', 'user', ?, ?)
                """,
                (f"tester-{suffix}", now, now),
            )
        return int(cursor.lastrowid)

    def insert_message(self, user_id, content, captured_at, symbols, message_hash, score=88):
        with server.auth_db() as conn:
            conn.execute(
                """
                INSERT INTO wechat_group_messages (
                    user_id, group_name, message_hash, sender, content, captured_at,
                    candidate_score, is_opportunity, opportunity_score, category,
                    symbols_json, thesis, catalysts_json, risks_json, action_hint,
                    urgency, analysis_source, analyzed_at, created_at
                ) VALUES (?, '梦之队🌙', ?, '群成员', ?, ?, 88, 1, ?,
                          '市场事件', ?, ?, '[]', '[]', '核对公告', 'normal',
                          'rules', ?, ?)
                """,
                (
                    user_id, message_hash, content, captured_at, score,
                    json.dumps(symbols), content[:30], captured_at, captured_at,
                ),
            )

    def asset(self, symbol):
        with server.auth_db() as conn:
            row = conn.execute(
                "SELECT * FROM price_watch_assets WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return dict(row) if row else None

    def test_backfill_restores_historical_opportunity_without_alert_replay(self):
        self.insert_opportunity("TUT")

        restored = server.backfill_wechat_group_opportunity_watch_pool()
        asset = self.asset("TUT")

        self.assertEqual(restored, ["TUT"])
        self.assertEqual(asset["opportunity_active"], 1)
        self.assertGreater(asset["opportunity_first_seen_at"], 0)
        self.assertEqual(asset["opportunity_manual_removed_at"], 0)

    def test_manual_removal_is_not_revived_by_historical_backfill(self):
        self.insert_opportunity("TUT")
        server.backfill_wechat_group_opportunity_watch_pool()
        server.remove_price_watch_symbol("TUT")

        restored = server.backfill_wechat_group_opportunity_watch_pool()
        asset = self.asset("TUT")

        self.assertNotIn("TUT", restored)
        self.assertEqual(asset["opportunity_active"], 0)
        self.assertGreater(asset["opportunity_manual_removed_at"], 0)

    def test_opportunity_dies_only_after_thirty_days_without_any_quote(self):
        now_ms = int(time.time() * 1000)
        first_seen_seconds = int((now_ms - 31 * DAY_MS) / 1000)
        server.upsert_group_opportunity_price_watch_symbol(
            "BMT", "梦之队", "hash-bmt", first_seen_seconds
        )

        server.update_price_watch_snapshot({
            "symbol": "BMT",
            "status": "unavailable",
            "checkedAt": now_ms,
            "error": "三家合约均无行情",
        })
        asset = self.asset("BMT")

        self.assertEqual(asset["opportunity_active"], 0)
        self.assertGreater(asset["dead_at"], 0)
        self.assertIn("30天", asset["dead_reason"])

    def test_recent_opportunity_stays_active_when_quote_temporarily_fails(self):
        now_ms = int(time.time() * 1000)
        server.upsert_group_opportunity_price_watch_symbol(
            "RIVER", "梦之队", "hash-river", int(time.time())
        )

        server.update_price_watch_snapshot({
            "symbol": "RIVER",
            "status": "unavailable",
            "checkedAt": now_ms,
            "error": "临时连接失败",
        })
        asset = self.asset("RIVER")

        self.assertEqual(asset["opportunity_active"], 1)
        self.assertEqual(asset["dead_at"], 0)
        self.assertEqual(asset["quote_failure_streak"], 1)

    def test_payload_merges_existing_ocr_fragments_into_one_listing_card(self):
        user_id = self.create_user("payload-dedupe")
        captured_at = int(time.time())
        self.insert_message(
            user_id,
            "【重要】Bitget关于上线CXMTUSDT 热门股票永续合约的公告 CXMTUSDT /coin_listings",
            captured_at,
            ["CXMTUSDT"],
            "cxmt-full",
        )
        self.insert_message(
            user_id,
            "【重要】Bitget 关于上线GXMTUSDT",
            captured_at + 2400,
            ["GXMTUSDT"],
            "cxmt-ocr-typo",
        )
        self.insert_message(
            user_id,
            "【重要】Bitget关于上线CXMTUSDT 热门股票永续合约的公告",
            captured_at + 2480,
            ["CXMTUSDT"],
            "cxmt-corrected",
        )
        self.insert_message(
            user_id,
            "交易所上新 【重要】Bitget关于上线CXMTUSDTMTUsDT",
            captured_at + 2489,
            [],
            "cxmt-title",
        )
        self.insert_message(
            user_id,
            "票永续合约的公告",
            captured_at + 2489,
            [],
            "cxmt-tail",
            score=77,
        )

        payload = server.wechat_group_monitor_payload({"id": user_id})

        self.assertEqual(len(payload["opportunities"]), 1)
        self.assertIn("Bitget关于上线CXMTUSDT", payload["opportunities"][0]["content"])
        self.assertEqual(payload["opportunities"][0]["symbols"], ["CXMTUSDT"])

    def test_persist_merges_semantic_duplicate_and_alerts_only_once(self):
        user_id = self.create_user("persist-dedupe")
        captured_at = int(time.time())

        def analysis(_user_id, _group_name, message, _score):
            return {
                "isOpportunity": True,
                "confidence": 88,
                "category": "市场事件",
                "symbols": ["CXMTUSDT"],
                "thesis": str(message["content"])[:40],
                "catalysts": [],
                "risks": [],
                "actionHint": "核对公告",
                "urgency": "normal",
                "analysisSource": "rules",
            }

        first = {
            "sender": "群成员",
            "content": "【重要】Bitget关于上线CXMTUSDT 热门股票永续合约的公告",
            "capturedAt": captured_at,
            "hash": "persist-cxmt-full",
        }
        second = {
            "sender": "群成员",
            "content": "交易所上新 【重要】Bitget关于上线CXMTUSDT",
            "capturedAt": captured_at + 20,
            "hash": "persist-cxmt-short",
        }
        with patch.object(server, "wechat_group_analysis_result", side_effect=analysis), \
             patch.object(server, "upsert_group_opportunity_price_watch_symbol", return_value=False), \
             patch.object(server, "launch_desktop_alert") as alert:
            server.persist_wechat_group_analysis(user_id, "梦之队🌙", first, 88)
            server.persist_wechat_group_analysis(user_id, "梦之队🌙", second, 88)

        with server.auth_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM wechat_group_messages WHERE user_id = ? AND is_opportunity = 1",
                (user_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(alert.call_count, 1)

    def test_different_symbols_remain_separate_opportunities(self):
        left = {
            "group_name": "梦之队🌙",
            "content": "Bitget上线ALPHAUSDT永续合约",
            "captured_at": 1000,
            "symbols_json": '["ALPHAUSDT"]',
            "category": "市场事件",
        }
        right = {
            "group_name": "梦之队🌙",
            "content": "Bitget上线BETAUSDT永续合约",
            "captured_at": 1010,
            "symbols_json": '["BETAUSDT"]',
            "category": "市场事件",
        }

        self.assertFalse(server.wechat_opportunities_match(left, right))

    def test_default_qq_sender_monitor_is_seeded_with_wechat_forwarding(self):
        user_id = self.create_user("qq-seed")

        inserted = server.ensure_default_qq_group_monitor(user_id)
        payload = server.wechat_group_monitor_payload({"id": user_id})

        self.assertEqual(inserted, 1)
        monitor = next(item for item in payload["monitors"] if item["platform"] == "qq")
        self.assertEqual(monitor["groupName"], "地表最强bsc eth")
        self.assertEqual(monitor["senderFilter"], "鲸鱼🐳PP")
        self.assertTrue(monitor["forwardToWechat"])
        self.assertEqual(monitor["forwardTarget"], "文件传输助手")

    def test_targeted_qq_symbol_enters_structure_pool_and_forward_outbox(self):
        user_id = self.create_user("qq-poll")
        server.ensure_default_qq_group_monitor(user_id)
        now = int(time.time())
        with server.auth_db() as conn:
            conn.execute(
                """
                UPDATE wechat_group_monitors
                SET baseline_ready = 1, last_status = 'connected', last_seen_at = ?
                WHERE user_id = ? AND group_name = ?
                """,
                (now, user_id, "地表最强bsc eth"),
            )
        collected = {
            "ok": True,
            "status": "connected",
            "messages": [{
                "sender": "鲸鱼🐳PP",
                "content": "$PONS",
                "capturedAt": now,
                "hash": "qq-pons-first",
                "platform": "qq",
            }],
        }
        with patch.object(server, "collect_visible_group_messages", return_value=collected), \
             patch.object(server, "process_chat_message_forward_outbox", return_value={"sent": 0, "failed": 0}), \
             patch.object(server, "sync_price_watch_monitor", return_value={}), \
             patch.object(server.WECHAT_GROUP_ANALYSIS_POOL, "submit"):
            result = server.poll_wechat_group_monitors_once(user_id=user_id)

        asset = self.asset("PONS")
        self.assertEqual(result["directlyMonitoredSymbols"], ["PONS"])
        self.assertEqual(asset["opportunity_active"], 1)
        self.assertIn("Q群 · 地表最强bsc eth · 鲸鱼🐳PP", asset["opportunity_source"])
        with server.auth_db() as conn:
            forward = conn.execute(
                "SELECT * FROM chat_message_forwards WHERE user_id = ? AND message_hash = ?",
                (user_id, "qq-pons-first"),
            ).fetchone()
        self.assertIsNotNone(forward)
        self.assertEqual(forward["target"], "文件传输助手")
        self.assertEqual(forward["status"], "pending")

    def test_forward_outbox_sends_once_and_records_success(self):
        user_id = self.create_user("qq-forward")
        server.ensure_default_qq_group_monitor(user_id)
        with server.auth_db() as conn:
            monitor = dict(conn.execute(
                "SELECT * FROM wechat_group_monitors WHERE user_id = ? AND platform = 'qq'",
                (user_id,),
            ).fetchone())
        message = {
            "sender": "鲸鱼🐳PP",
            "content": "关注 $PONS 的结构",
            "capturedAt": int(time.time()),
            "hash": "qq-forward-pons",
            "platform": "qq",
        }
        self.assertTrue(server.enqueue_chat_message_forward(user_id, monitor, message))

        with patch.object(server, "send_text_to_wechat", return_value={"ok": True, "sentAt": 123}) as sender:
            result = server.process_chat_message_forward_outbox(limit=2)

        self.assertEqual(result["sent"], 1)
        sent_text = sender.call_args.args[1]
        self.assertEqual(sent_text, "关注 $PONS 的结构")
        self.assertNotIn("Q群监控", sent_text)
        self.assertNotIn("ID：", sent_text)
        with server.auth_db() as conn:
            row = conn.execute(
                "SELECT status, attempts, sent_at FROM chat_message_forwards WHERE message_hash = ?",
                ("qq-forward-pons",),
            ).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["sent_at"], 123)

    def test_forward_outbox_never_retries_an_uncertain_wechat_send(self):
        user_id = self.create_user("qq-forward-uncertain")
        server.ensure_default_qq_group_monitor(user_id)
        with server.auth_db() as conn:
            monitor = dict(conn.execute(
                "SELECT * FROM wechat_group_monitors WHERE user_id = ? AND platform = 'qq'",
                (user_id,),
            ).fetchone())
        message = {
            "sender": "鲸鱼🐳PP",
            "content": "关注 $PONS 的结构",
            "capturedAt": int(time.time()),
            "hash": "qq-forward-uncertain",
            "platform": "qq",
        }
        self.assertTrue(server.enqueue_chat_message_forward(user_id, monitor, message))

        with patch.object(
            server,
            "send_text_to_wechat",
            side_effect=server.WechatDeliveryUncertainError("已执行发送但没有回读"),
        ) as sender:
            first = server.process_chat_message_forward_outbox(limit=2)
            second = server.process_chat_message_forward_outbox(limit=2)

        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(sender.call_count, 1)
        with server.auth_db() as conn:
            row = conn.execute(
                "SELECT status, attempts, next_attempt_at FROM chat_message_forwards WHERE message_hash = ?",
                ("qq-forward-uncertain",),
            ).fetchone()
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["next_attempt_at"], 0)

    def test_forwarding_keeps_only_original_crypto_or_project_content(self):
        self.assertEqual(
            server.format_chat_forward_message("群名", "鲸鱼🐳PP", "  $PONS   这个项目有机会  ", ["PONS"]),
            "$PONS   这个项目有机会",
        )
        self.assertTrue(server.chat_message_is_forwardable("这个链上项目的流动性起来了"))
        self.assertTrue(server.chat_message_is_forwardable("关注 $PONS", ["PONS"]))
        self.assertFalse(server.chat_message_is_forwardable("你就没有亏过，早买晚买都有收入"))
        self.assertFalse(server.chat_message_is_forwardable("晚上一起吃饭吗"))

    def test_idle_qq_chat_is_not_added_to_wechat_forward_outbox(self):
        user_id = self.create_user("qq-forward-idle")
        server.ensure_default_qq_group_monitor(user_id)
        with server.auth_db() as conn:
            monitor = dict(conn.execute(
                "SELECT * FROM wechat_group_monitors WHERE user_id = ? AND platform = 'qq'",
                (user_id,),
            ).fetchone())
        message = {
            "sender": "鲸鱼🐳PP",
            "content": "你就没有亏过，早买晚买都有收入",
            "capturedAt": int(time.time()),
            "hash": "qq-forward-idle",
            "platform": "qq",
        }

        self.assertFalse(server.enqueue_chat_message_forward(user_id, monitor, message))
        with server.auth_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM chat_message_forwards WHERE message_hash = ?",
                (message["hash"],),
            ).fetchone()[0]
        self.assertEqual(total, 0)

    def test_forward_outbox_deduplicates_same_qq_message_across_users(self):
        first_user_id = self.create_user("qq-forward-dedupe-a")
        second_user_id = self.create_user("qq-forward-dedupe-b")
        server.ensure_default_qq_group_monitor(first_user_id)
        server.ensure_default_qq_group_monitor(second_user_id)
        with server.auth_db() as conn:
            first_monitor = dict(conn.execute(
                "SELECT * FROM wechat_group_monitors WHERE user_id = ? AND platform = 'qq'",
                (first_user_id,),
            ).fetchone())
            second_monitor = dict(conn.execute(
                "SELECT * FROM wechat_group_monitors WHERE user_id = ? AND platform = 'qq'",
                (second_user_id,),
            ).fetchone())
        message = {
            "sender": "鲸鱼🐳PP",
            "content": "关注 $PONS 的结构",
            "capturedAt": int(time.time()),
            "hash": "qq-forward-global-dedupe",
            "platform": "qq",
        }

        self.assertTrue(server.enqueue_chat_message_forward(first_user_id, first_monitor, message))
        self.assertFalse(server.enqueue_chat_message_forward(second_user_id, second_monitor, message))
        with server.auth_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM chat_message_forwards WHERE message_hash = ?",
                (message["hash"],),
            ).fetchone()[0]

        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
