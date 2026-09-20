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
        self.assertTrue(server.price_structure_symbol_excluded("TUT"))
        self.assertEqual(server.price_watch_active_rows(), [])

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

    def test_same_generic_ai_key_does_not_merge_disjoint_symbol_batches(self):
        captured_at = int(time.time())
        left = {
            "group_name": "华尔街聚合 / 方程式新闻",
            "captured_at": captured_at,
            "opportunity_key": "binance-alpha-listing",
            "symbols": ["CNPY"],
            "content": "Binance Alpha 上线 CNPY",
        }
        right = {
            "group_name": "华尔街聚合 / 方程式新闻",
            "captured_at": captured_at + 3600,
            "opportunity_key": "binance-alpha-listing",
            "symbols": ["CASHCAT", "AINVDA"],
            "content": "Binance Alpha 上线 CASHCAT 与 AINVDA",
        }

        self.assertFalse(server.wechat_opportunities_match(left, right))

    def test_merged_duplicate_uses_latest_signal_time(self):
        merged = server.merge_wechat_opportunity_records(
            {"captured_at": 100, "content": "PONS 上新", "symbols": ["PONS"]},
            {"captured_at": 160, "content": "PONS 上新公告", "symbols": ["PONS"]},
        )

        self.assertEqual(merged["captured_at"], 160)

    def test_merged_duplicate_replaces_generic_ca_narrative_with_new_research(self):
        merged = server.merge_wechat_opportunity_records(
            {
                "captured_at": 100,
                "analyzed_at": 110,
                "content": "0x4c3edaf84bf311f5a4bc9dfc135cd32c24b71e18",
                "symbols": ["PUGCOIN"],
                "analysis_source": "Codex CLI",
                "thesis": "叙事未识别：目前只确认 PUGCOIN 是 Robinhood Chain 新代币。",
            },
            {
                "captured_at": 100,
                "analyzed_at": 160,
                "content": "0x4c3edaf84bf311f5a4bc9dfc135cd32c24b71e18",
                "symbols": ["PUGCOIN"],
                "analysis_source": "Codex CLI",
                "thesis": "这是 Robinhood Chain 上的巴哥犬 Meme，借 OpenAI 规范中的示例币名传播 AI 梗，不是 OpenAI 官方币。",
            },
        )

        self.assertIn("巴哥犬 Meme", merged["thesis"])

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
                "analysisSource": "Codex CLI",
                "opportunityKey": "CXMTUSDT|Bitget永续上线",
                "narrativeStrength": 91,
                "memePotential": 84,
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

    def test_rules_fallback_and_symbol_free_ai_results_never_popup(self):
        user_id = self.create_user("strict-ai-popup")
        captured_at = int(time.time())
        base_message = {
            "platform": "qq",
            "senderFilter": "鲸鱼🐳PP",
            "sender": "鲸鱼🐳PP",
            "capturedAt": captured_at,
        }
        rules_result = {
            "isOpportunity": True,
            "confidence": 92,
            "opportunityKey": "PONS|项目进展",
            "category": "项目进展",
            "symbols": ["PONS"],
            "thesis": "PONS出现新的可核验催化",
            "catalysts": [],
            "risks": [],
            "actionHint": "核验原始来源",
            "urgency": "normal",
            "analysisSource": "rules",
            "narrativeStrength": 88,
            "memePotential": 30,
        }
        no_symbol_result = {
            **rules_result,
            "analysisSource": "Codex CLI",
            "symbols": [],
            "opportunityKey": "",
        }

        with patch.object(server, "upsert_group_opportunity_price_watch_symbol", return_value=False), \
             patch.object(server, "launch_desktop_alert") as alert:
            with patch.object(server, "wechat_group_analysis_result", return_value=rules_result):
                server.persist_wechat_group_analysis(
                    user_id, "地表最强bsc eth",
                    {**base_message, "content": "$PONS 有新进展", "hash": "rules-only"}, 80,
                )
            with patch.object(server, "wechat_group_analysis_result", return_value=no_symbol_result):
                server.persist_wechat_group_analysis(
                    user_id, "地表最强bsc eth",
                    {**base_message, "content": "大家聊得挺热闹", "hash": "no-symbol-ai"}, 80,
                )

        alert.assert_not_called()

    def test_ai_false_string_is_not_treated_as_opportunity(self):
        user_id = self.create_user("ai-false-string")
        response = {
            "choices": [{"message": {"content": json.dumps({
                "isOpportunity": "false",
                "confidence": 91,
                "opportunityKey": "",
                "category": "闲聊",
                "symbols": ["PONS"],
                "thesis": "普通聊天",
                "catalysts": [],
                "risks": [],
                "actionHint": "",
                "urgency": "low",
                "narrativeStrength": 5,
                "memePotential": 0,
            }, ensure_ascii=False)}}],
            "_provider": "codex-cli",
        }
        with patch.object(server, "llm_settings_for_user", return_value={"apiKey": "test"}), \
             patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "deepseek_chat", return_value=response):
            analysis = server.wechat_group_analysis_result(
                user_id,
                "地表最强bsc eth",
                {"platform": "qq", "senderFilter": "鲸鱼🐳PP", "content": "$PONS 早上好"},
                45,
            )

        self.assertFalse(analysis["isOpportunity"])

    def test_ai_semantic_key_deduplicates_same_opportunity_across_groups(self):
        user_id = self.create_user("cross-group-ai-dedupe")
        captured_at = int(time.time())

        def analysis(_user_id, _group_name, message, _score):
            return {
                "isOpportunity": True,
                "confidence": 90,
                "opportunityKey": "PONS|官方公布新产品",
                "category": "项目进展",
                "symbols": ["PONS"],
                "thesis": str(message["content"])[:40],
                "catalysts": ["官方产品发布"],
                "risks": ["需核验公告"],
                "actionHint": "核验官方公告",
                "urgency": "normal",
                "analysisSource": "Codex CLI",
                "narrativeStrength": 86,
                "memePotential": 20,
            }

        with patch.object(server, "wechat_group_analysis_result", side_effect=analysis), \
             patch.object(server, "upsert_group_opportunity_price_watch_symbol", return_value=False), \
             patch.object(server, "launch_desktop_alert") as alert:
            server.persist_wechat_group_analysis(user_id, "A群", {
                "content": "PONS官方公布新产品", "capturedAt": captured_at, "hash": "pons-a",
            }, 85)
            server.persist_wechat_group_analysis(user_id, "B群", {
                "content": "PONS刚发布了同一项新产品", "capturedAt": captured_at + 30, "hash": "pons-b",
            }, 85)

        with server.auth_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM wechat_group_messages WHERE user_id = ? AND is_opportunity = 1",
                (user_id,),
            ).fetchone()[0]
        self.assertEqual(total, 1)
        self.assertEqual(alert.call_count, 1)
        self.assertEqual(alert.call_args.args[0]["title"], "机会标的：PONS")

    def test_same_qq_message_is_analyzed_once_for_multiple_dashboard_users(self):
        first_user_id = self.create_user("shared-ai-a")
        second_user_id = self.create_user("shared-ai-b")
        server.ensure_default_qq_group_monitor(first_user_id)
        server.ensure_default_qq_group_monitor(second_user_id)
        now = int(time.time())
        with server.auth_db() as conn:
            conn.execute(
                """
                UPDATE wechat_group_monitors
                SET baseline_ready = 1, last_status = 'connected', last_seen_at = ?
                WHERE user_id IN (?, ?) AND group_name = ?
                """,
                (now, first_user_id, second_user_id, "地表最强bsc eth"),
            )
        collected = {
            "ok": True,
            "status": "connected",
            "messages": [{
                "sender": "鲸鱼🐳PP",
                "content": "$PONS 官方公布新产品",
                "capturedAt": now,
                "hash": "shared-ai-pons",
                "platform": "qq",
            }],
        }

        with patch.object(server, "collect_visible_group_messages", return_value=collected), \
             patch.object(server, "process_chat_message_forward_outbox", return_value={"sent": 0, "failed": 0}), \
             patch.object(server, "upsert_group_opportunity_price_watch_symbol", return_value=False), \
             patch.object(server.WECHAT_GROUP_ANALYSIS_POOL, "submit") as submit:
            result = server.poll_wechat_group_monitors_once()

        self.assertEqual(result["queued"], 1)
        self.assertEqual(submit.call_count, 1)
        submitted_jobs = submit.call_args.args[1]
        self.assertEqual(len(submitted_jobs), 2)

    def test_group_chat_ai_analysis_scores_narrative_and_meme_potential(self):
        user_id = self.create_user("group-ai")
        model_payload = {
            "isOpportunity": True,
            "confidence": 89,
            "category": "链上 Meme",
            "symbols": ["DOGAI"],
            "thesis": "热点人物与链上同名币形成传播映射",
            "catalysts": ["社区二创扩散"],
            "risks": ["合约归属仍需核验"],
            "actionHint": "核对合约后观察成交放量",
            "urgency": "high",
            "narrativeStrength": 93,
            "memePotential": 88,
        }
        response = {
            "choices": [{"message": {"content": json.dumps(model_payload, ensure_ascii=False)}}],
            "_provider": "codex-cli",
        }
        message = {
            "platform": "qq",
            "senderFilter": "鲸鱼🐳PP",
            "content": "热点人物事件发酵，链上出现 $DOGAI 同名 Meme 币",
        }
        with patch.object(server, "llm_settings_for_user", return_value={"apiKey": "test"}), \
             patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "deepseek_chat", return_value=response):
            analysis = server.wechat_group_analysis_result(user_id, "地表最强bsc eth", message, 86)

        self.assertTrue(analysis["isOpportunity"])
        self.assertEqual(analysis["analysisSource"], "Codex CLI")
        self.assertEqual(analysis["narrativeStrength"], 93)
        self.assertEqual(analysis["memePotential"], 88)

    def test_ai_analysis_validates_ca_and_accepts_contract_without_symbol(self):
        user_id = self.create_user("group-ai-ca")
        contract = "0x1234567890abcdef1234567890abcdef12345678"
        response = {
            "choices": [{"message": {"content": json.dumps({
                "isOpportunity": True,
                "confidence": 89,
                "opportunityKey": "新CA|项目催化",
                "category": "链上新币",
                "symbols": [],
                "contractAddress": contract,
                "chain": "bsc",
                "plainNarrative": "这是一个刚出现的链上新币，社区话题升温但合约和流动性仍需核验。",
                "thesis": "链上出现新币",
                "catalysts": ["社区讨论升温"],
                "risks": ["合约归属待核验"],
                "actionHint": "先核验合约",
                "urgency": "normal",
                "narrativeStrength": 82,
                "memePotential": 70,
            }, ensure_ascii=False)}}],
            "_provider": "codex-cli",
        }
        message = {
            "platform": "wechat",
            "content": f"BSC 新币刚开始传播，CA: {contract}",
        }
        with patch.object(server, "llm_settings_for_user", return_value={"apiKey": "test"}), \
             patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "deepseek_chat", return_value=response):
            analysis = server.wechat_group_analysis_result(user_id, "华尔街聚合", message, 75)

        self.assertTrue(analysis["isOpportunity"])
        self.assertEqual(analysis["contractAddress"], contract)
        self.assertEqual(analysis["chain"], "56")
        self.assertEqual(
            analysis["plainNarrative"],
            "叙事未识别：目前只确认发现了新 CA，公开资料还没有说明它在讲什么故事。",
        )
        self.assertNotIn("核验合约", analysis["plainNarrative"])
        self.assertEqual(analysis["thesis"], analysis["plainNarrative"])

    def test_popup_narrative_keeps_only_the_first_sentence(self):
        narrative = server.chat_opportunity_plain_narrative(
            "这是一个由社区事件带动的新币。第二句不应出现在弹窗里。"
        )

        self.assertEqual(narrative, "这是一个由社区事件带动的新币。")

    def test_ca_research_can_keep_identity_and_official_relation_in_one_sentence(self):
        narrative = server.chat_opportunity_plain_narrative(
            "这是 Robinhood Chain 上的 PUGCOIN，Pug 字面是巴哥犬；它借 OpenAI Model Spec 里的示例传播 AI 梗，但不是 OpenAI 官方币或官方背书。",
            max_chars=96,
        )

        self.assertIn("巴哥犬", narrative)
        self.assertIn("不是 OpenAI 官方币", narrative)
        self.assertLessEqual(len(narrative), 96)

    def test_ai_cannot_replace_multiple_message_contracts_with_invented_ca(self):
        user_id = self.create_user("group-ai-ca-guard")
        first = "0x1111111111111111111111111111111111111111"
        second = "0x2222222222222222222222222222222222222222"
        invented = "0x9999999999999999999999999999999999999999"
        response = {
            "choices": [{"message": {"content": json.dumps({
                "isOpportunity": True,
                "confidence": 91,
                "category": "链上新币",
                "symbols": [],
                "contractAddress": invented,
                "chain": "bsc",
                "plainNarrative": "消息同时给了多个地址，暂时不能确认哪个才是目标合约。",
            }, ensure_ascii=False)}}],
            "_provider": "codex-cli",
        }
        with patch.object(server, "llm_settings_for_user", return_value={"apiKey": "test"}), \
             patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "deepseek_chat", return_value=response):
            analysis = server.wechat_group_analysis_result(user_id, "华尔街聚合", {
                "platform": "wechat",
                "content": f"候选一 {first}，候选二 {second}",
            }, 75)

        self.assertEqual(analysis["contractAddress"], "")
        self.assertFalse(analysis["isOpportunity"])

    def test_repeated_token_link_selects_main_ca_from_bot_message_with_holder_addresses(self):
        contract = "0x10b409f69989bc34e36a5105874f6d64e3eb0bff"
        holder = "0x267444d099b10fb5ed7c3cc7b7c767adca574952"
        pvp = "0x6b09c294ecc9fbe3285f0421eb76ebb6222ade64"
        content = (
            f"回复 CA: {contract} "
            f"[Maple](https://ponsfamily.com/launchpad/{contract}) Robinhood @ Pons V2 "
            f"[持仓](https://rh-scan.com/address/{holder}) "
            f"[PVP](https://t.me/rick?start={pvp}) "
            f"[行情](https://defined.fi/token/robinhood/{contract}) "
            f"`{contract}` [GMGN](https://gmgn.ai/robinhood/token/ref_{contract})"
        )

        identity = server.chat_opportunity_contract_identity(content)

        self.assertEqual(identity["contractAddress"], contract)
        self.assertEqual(identity["chain"], "4663")

    def test_ca_analysis_uses_unbounded_web_research_lane(self):
        user_id = self.create_user("ca-dedicated-lane")
        contract = "0x1234567890abcdef1234567890abcdef12345678"
        response = {
            "choices": [{"message": {"content": json.dumps({
                "isOpportunity": False,
                "confidence": 55,
                "symbols": ["TEST"],
                "contractAddress": contract,
                "chain": "bsc",
                "plainNarrative": "这是一个刚出现的链上代币，当前热度有限，先核验合约安全和流动性。",
            }, ensure_ascii=False)}}],
            "_provider": "codex-cli",
        }
        with patch.object(server, "llm_settings_for_user", return_value={"apiKey": "test"}), \
             patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "deepseek_chat", return_value=response) as chat:
            server.wechat_group_analysis_result(
                user_id,
                "华尔街聚合",
                {"platform": "wechat", "content": f"BSC 新 CA: {contract}"},
                75,
            )

        settings = chat.call_args.args[1]
        self.assertEqual(settings["_analysisLane"], "chat-ca")
        self.assertTrue(settings["_analysisNoTimeout"])
        self.assertTrue(settings["_preferCodexCli"])
        self.assertTrue(settings["_codexWebSearch"])
        self.assertTrue(settings["_codexAllowDuringCooldown"])
        self.assertEqual(settings["_codexModel"], "gpt-6-astra")
        self.assertEqual(settings["_codexReasoningEffort"], "high")
        self.assertNotIn("_analysisDeadline", settings)

    def test_rich_ca_message_still_fetches_profile_and_requests_identity_first(self):
        user_id = self.create_user("rich-ca-profile")
        contract = "0xeccbb861c0dda7efd964010085488b69317e4444"
        metadata = {
            "symbol": "龙虾",
            "name": "龙虾",
            "chain": "56",
            "chainLabel": "BNB Chain",
            "quoteAssets": [{"symbol": "WBNB", "name": "Wrapped BNB"}],
            "narrativeContext": {
                "description": "A community lobster mascot meme with an AI-claw story.",
                "websites": ["https://example.test/lobster"],
                "socials": ["https://x.com/example_lobster"],
            },
        }
        response = {
            "choices": [{"message": {"content": json.dumps({
                "isOpportunity": True,
                "confidence": 84,
                "symbols": ["龙虾"],
                "contractAddress": contract,
                "chain": "bsc",
                "plainNarrative": "这是 BNB Chain 上的龙虾社区 Meme，核心炒吉祥物、AI 与 BSC 生态复兴，不是功能型项目。",
            }, ensure_ascii=False)}}],
            "_provider": "codex-cli",
        }

        def inspect_prompt(messages, settings):
            prompt = json.loads(messages[1]["content"])
            self.assertEqual(prompt["contractMetadata"]["narrativeContext"]["description"], metadata["narrativeContext"]["description"])
            self.assertIn("先确认这个 CA 到底是谁", " ".join(prompt["rules"]))
            self.assertTrue(settings["_codexWebSearch"])
            return response

        with patch.object(server, "llm_settings_for_user", return_value={"apiKey": "test"}), \
             patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "chat_contract_market_identity", return_value=metadata) as identity, \
             patch.object(server, "deepseek_chat", side_effect=inspect_prompt):
            analysis = server.wechat_group_analysis_result(user_id, "华尔街聚合", {
                "platform": "wechat",
                "content": f"$LOBSTER BNB @ Four.meme CA: {contract}",
            }, 92)

        identity.assert_called_once()
        self.assertEqual(analysis["symbols"], ["龙虾"])
        self.assertIn("不是功能型项目", analysis["plainNarrative"])

    def test_bare_ca_uses_market_metadata_to_fill_symbol_for_ai_and_popup(self):
        user_id = self.create_user("bare-ca-symbol")
        contract = "0x6666666666666666666666666666666666666666"
        response = {
            "choices": [{"message": {"content": json.dumps({
                "isOpportunity": False,
                "confidence": 50,
                "symbols": [],
                "contractAddress": contract,
                "chain": "robinhood",
                "plainNarrative": "消息仅包含 NEMO 合约地址，未说明项目进展、催化或可核验事件。",
            }, ensure_ascii=False)}}],
            "_provider": "codex-cli",
        }
        with patch.object(server, "llm_settings_for_user", return_value={"apiKey": "test"}), \
             patch.object(server, "deepseek_enabled", return_value=True), \
             patch.object(server, "chat_contract_market_identity", return_value={
                 "symbol": "NEMO", "name": "NVIDIA NeMo", "chain": "4663", "chainLabel": "Robinhood Chain",
                 "quoteAssets": [{"symbol": "NVDA", "name": "NVIDIA • Robinhood Token"}],
             }) as metadata, patch.object(server, "deepseek_chat", return_value=response) as chat:
            analysis = server.wechat_group_analysis_result(
                user_id,
                "华尔街聚合",
                {"platform": "wechat", "content": contract},
                40,
            )

        metadata.assert_called_once()
        self.assertEqual(analysis["symbols"], ["NEMO"])
        self.assertEqual(analysis["chain"], "4663")
        self.assertEqual(
            analysis["plainNarrative"],
            "NEMO 以“NVIDIA NeMo”这一英伟达相关概念为叙事，并在 Robinhood Chain 与 NVDA 股票代币配对。",
        )
        prompt = json.loads(chat.call_args.args[0][1]["content"])
        self.assertEqual(prompt["contractMetadata"]["name"], "NVIDIA NeMo")
        self.assertEqual(prompt["contractMetadata"]["quoteAssets"][0]["symbol"], "NVDA")

    def test_market_identity_keeps_pair_context_for_ca_narrative(self):
        contract = "0x7777777777777777777777777777777777777777"
        payload = {"pairs": [{
            "chainId": "robinhood",
            "pairAddress": "0x8888888888888888888888888888888888888888",
            "dexId": "uniswap",
            "baseToken": {"address": contract, "symbol": "NEMO", "name": "NVIDIA NeMo"},
            "quoteToken": {"address": "0x9999999999999999999999999999999999999999", "symbol": "NVDA", "name": "NVIDIA • Robinhood Token"},
            "liquidity": {"usd": 500000},
            "url": "https://dexscreener.com/robinhood/test",
        }]}
        server.CHAT_CONTRACT_MARKET_CACHE.clear()

        with patch.object(server, "fetch_dexscreener_token", return_value=payload):
            identity = server.chat_contract_market_identity({"contractAddress": contract, "chain": ""})

        self.assertEqual(identity["name"], "NVIDIA NeMo")
        self.assertEqual(identity["chain"], "4663")
        self.assertEqual(identity["quoteAssets"], [{"symbol": "NVDA", "name": "NVIDIA • Robinhood Token"}])

    def test_market_identity_preserves_non_latin_token_symbol(self):
        contract = "0x7777777777777777777777777777777777777777"
        payload = {"pairs": [{
            "chainId": "bsc",
            "pairAddress": "0x8888888888888888888888888888888888888888",
            "dexId": "pancakeswap",
            "baseToken": {"address": contract, "symbol": "龙虾", "name": "龙虾"},
            "quoteToken": {"address": "0x9999999999999999999999999999999999999999", "symbol": "WBNB", "name": "Wrapped BNB"},
            "liquidity": {"usd": 500000},
            "url": "https://dexscreener.com/bsc/test",
        }]}
        server.CHAT_CONTRACT_MARKET_CACHE.clear()

        with patch.object(server, "fetch_dexscreener_token", return_value=payload):
            identity = server.chat_contract_market_identity({"contractAddress": contract, "chain": "56"})

        self.assertEqual(identity["symbol"], "龙虾")
        self.assertEqual(identity["sourceUrl"], "https://dexscreener.com/bsc/test")

    def test_new_ca_alert_does_not_require_opportunity_score_and_is_sent_only_once(self):
        user_id = self.create_user("new-ca-always-alert")
        captured_at = int(time.time())
        contract = "0x4444444444444444444444444444444444444444"
        analysis = {
            "isOpportunity": False,
            "confidence": 52,
            "opportunityKey": "",
            "category": "链上新币",
            "symbols": ["MAPLE"],
            "contractAddress": contract,
            "chain": "4663",
            "plainNarrative": "这是一个刚出现的 Maple 链上代币，热度尚弱，先核验合约安全和流动性。",
            "thesis": "这是一个刚出现的 Maple 链上代币，热度尚弱，先核验合约安全和流动性。",
            "catalysts": [],
            "risks": ["热度尚弱"],
            "actionHint": "核验合约",
            "urgency": "normal",
            "analysisSource": "Codex CLI",
            "narrativeStrength": 45,
            "memePotential": 30,
        }
        with patch.object(server, "launch_desktop_alert") as alert:
            server.persist_wechat_group_analysis(user_id, "频道 A", {
                "platform": "wechat", "content": f"Robinhood Maple {contract}",
                "capturedAt": captured_at, "hash": "new-ca-low-score-a",
            }, 72, analysis_override=analysis)
            server.persist_wechat_group_analysis(user_id, "频道 B", {
                "platform": "wechat", "content": f"Robinhood Maple CA: {contract}",
                "capturedAt": captured_at + 10, "hash": "new-ca-low-score-b",
            }, 72, analysis_override=analysis)

        self.assertEqual(alert.call_count, 1)
        payload = alert.call_args.args[0]
        self.assertEqual(payload["title"], "新 CA：MAPLE")
        self.assertEqual(payload["contractAddress"], contract)
        self.assertEqual(payload["chain"], "4663")
        self.assertTrue(payload["key"].startswith("chat-new-ca:v2:wechat:4663:"))
        self.assertIn(f"/token/robinhood/{contract}", payload["url"])

    def test_ca_popup_uses_plain_narrative_and_exact_binance_wallet_page(self):
        user_id = self.create_user("ca-popup-route")
        contract = "0x1234567890abcdef1234567890abcdef12345678"
        narrative = f"这是一个受社区事件带动的新 Meme 币，CA 是 {contract}，热度在升但安全和流动性仍需核验。"
        analysis = {
            "isOpportunity": True,
            "confidence": 90,
            "opportunityKey": "DOGAI|新CA",
            "category": "链上 Meme",
            "symbols": ["DOGAI"],
            "contractAddress": contract,
            "chain": "56",
            "plainNarrative": narrative,
            "thesis": narrative,
            "catalysts": [],
            "risks": ["合约安全待核验"],
            "actionHint": "核验合约",
            "urgency": "high",
            "analysisSource": "Codex CLI",
            "narrativeStrength": 88,
            "memePotential": 91,
        }
        with patch.object(server, "upsert_group_opportunity_price_watch_symbol", return_value=False), \
             patch.object(server, "launch_desktop_alert") as alert:
            server.persist_wechat_group_analysis(user_id, "华尔街聚合", {
                "platform": "wechat",
                "content": f"BSC 上出现 $DOGAI，CA: {contract}",
                "capturedAt": int(time.time()),
                "hash": "dogai-ca-popup",
            }, 88, analysis_override=analysis)

        payload = alert.call_args.args[0]
        self.assertEqual(payload["title"], "新 CA 机会：DOGAI")
        self.assertEqual(payload["contractAddress"], contract)
        self.assertEqual(payload["chain"], "56")
        self.assertNotIn(contract.lower(), payload["body"].lower())
        self.assertNotIn(contract.lower(), payload["speech"].lower())
        self.assertEqual(payload["speech"], "群聊发现新 CA：DOGAI")
        self.assertEqual(
            payload["url"],
            f"https://web3.binance.com/en/token/bsc/{contract}?ref=MQ6JD2X4",
        )
        self.assertNotIn("price-watch", payload["url"])

    def test_unresolved_address_waits_for_a_token_name_before_popup(self):
        user_id = self.create_user("ca-popup-no-symbol")
        contract = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
        analysis = {
            "isOpportunity": True,
            "confidence": 86,
            "opportunityKey": "新CA|待识别",
            "category": "链上新币",
            "symbols": [],
            "contractAddress": contract,
            "chain": "56",
            "plainNarrative": "这是一个刚出现的链上新币，币名尚未确认，先核验合约和流动性。",
            "thesis": "这是一个刚出现的链上新币，币名尚未确认，先核验合约和流动性。",
            "catalysts": [],
            "risks": [],
            "actionHint": "核验合约",
            "urgency": "normal",
            "analysisSource": "Codex CLI",
            "narrativeStrength": 70,
            "memePotential": 50,
        }
        with patch.object(server, "launch_desktop_alert") as alert:
            server.persist_wechat_group_analysis(user_id, "华尔街聚合", {
                "platform": "wechat",
                "content": f"这个地址给交易所充值地址转了钱 {contract}",
                "capturedAt": int(time.time()),
                "hash": "unknown-ca-popup",
            }, 70, analysis_override=analysis)

        self.assertEqual(alert.call_count, 0)
        with server.auth_db() as conn:
            claim_count = conn.execute(
                "SELECT COUNT(*) FROM chat_contract_alerts WHERE contract_address = ?",
                (contract,),
            ).fetchone()[0]
        self.assertEqual(claim_count, 0)

    def test_unidentified_ca_boilerplate_is_rejected_as_a_narrative(self):
        self.assertTrue(server.chat_ca_narrative_is_generic(
            "叙事未识别，目前唯一能确认的是一笔待核验的链上地址转账关系。"
        ))

    def test_same_ca_across_channels_is_persisted_and_alerted_once(self):
        user_id = self.create_user("cross-channel-ca-dedupe")
        captured_at = int(time.time())
        contract = "0x3333333333333333333333333333333333333333"
        analysis = {
            "isOpportunity": True,
            "confidence": 88,
            "opportunityKey": "",
            "category": "链上新币",
            "symbols": ["NEWCA"],
            "contractAddress": contract,
            "chain": "56",
            "plainNarrative": "这是一个刚出现的链上项目，当前只有初步传播，仍要核验合约和资金情况。",
            "thesis": "这是一个刚出现的链上项目，当前只有初步传播，仍要核验合约和资金情况。",
            "catalysts": [],
            "risks": [],
            "actionHint": "核验合约",
            "urgency": "normal",
            "analysisSource": "Codex CLI",
            "narrativeStrength": 72,
            "memePotential": 55,
        }
        with patch.object(server, "upsert_group_opportunity_price_watch_symbol", return_value=False), \
             patch.object(server, "launch_desktop_alert") as alert:
            server.persist_wechat_group_analysis(user_id, "频道 A", {
                "platform": "wechat", "content": f"BSC NEWCA {contract}",
                "capturedAt": captured_at, "hash": "same-ca-a",
            }, 80, analysis_override=analysis)
            server.persist_wechat_group_analysis(user_id, "频道 B", {
                "platform": "wechat", "content": f"发现新币，CA: {contract}",
                "capturedAt": captured_at + 10, "hash": "same-ca-b",
            }, 80, analysis_override=analysis)

        with server.auth_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM wechat_group_messages WHERE user_id = ? AND is_opportunity = 1",
                (user_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(alert.call_count, 1)

    def test_group_opportunity_payload_exposes_saved_ai_scores(self):
        user_id = self.create_user("group-ai-payload")
        captured_at = int(time.time())
        analysis = {
            "isOpportunity": True,
            "confidence": 90,
            "category": "链上 Meme",
            "symbols": ["DOGAI"],
            "thesis": "链上同名币形成叙事映射",
            "catalysts": ["社群传播"],
            "risks": ["流动性风险"],
            "actionHint": "核验合约",
            "urgency": "normal",
            "analysisSource": "Codex CLI",
            "narrativeStrength": 87,
            "memePotential": 92,
        }
        with patch.object(server, "wechat_group_analysis_result", return_value=analysis), \
             patch.object(server, "upsert_group_opportunity_price_watch_symbol", return_value=False), \
             patch.object(server, "launch_desktop_alert"):
            server.persist_wechat_group_analysis(user_id, "地表最强bsc eth", {
                "platform": "qq",
                "senderFilter": "鲸鱼🐳PP",
                "sender": "鲸鱼🐳PP",
                "content": "链上出现 $DOGAI 同名 Meme 币",
                "capturedAt": captured_at,
                "hash": "group-ai-dogai",
            }, 88)

        opportunity = server.wechat_group_monitor_payload({"id": user_id})["opportunities"][0]
        self.assertTrue(opportunity["aiAnalyzed"])
        self.assertEqual(opportunity["narrativeStrength"], 87)
        self.assertEqual(opportunity["memePotential"], 92)

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

    def test_contract_opportunity_prefers_exchange_named_in_message(self):
        action = server.wechat_opportunity_trade_action(
            "BYDUSDT",
            content="Bitget 上线 BYDUSDT 股票永续合约",
            watch_state={"provider": "Gate Futures", "newContractSource": "HTX 新合约"},
        )

        self.assertEqual(action["kind"], "exchange")
        self.assertEqual(action["venue"], "Bitget")
        self.assertEqual(
            action["url"],
            "https://www.bitget.com/zh-CN/futures/usdt/BYDUSDT",
        )

    def test_contract_address_word_does_not_override_onchain_buy_route(self):
        action = server.wechat_opportunity_trade_action(
            "CASHCAT",
            content="Binance Alpha lists Cash Cat. Contract Address: 0x020bfc650a365f8bb26819deaabf3e21291018b4",
            watch_state={
                "provider": "链上多源 K线",
                "chain": "4663",
                "chainLabel": "Robinhood",
                "contractAddress": "0x020bfc650a365f8bb26819deaabf3e21291018b4",
            },
        )

        self.assertEqual(action["kind"], "onchain")
        self.assertEqual(action["venue"], "Robinhood")

    def test_payload_matches_usdt_display_symbol_to_base_watch_asset(self):
        user_id = self.create_user("contract-route")
        captured_at = int(time.time())
        self.insert_message(
            user_id,
            "Bitget 上线 BYDUSDT 股票永续合约",
            captured_at,
            ["BYDUSDT"],
            "bitget-byd-contract",
        )
        server.upsert_group_opportunity_price_watch_symbol(
            "BYD", "梦之队🌙", "bitget-byd-contract", captured_at
        )
        with server.auth_db() as conn:
            conn.execute(
                "UPDATE price_watch_assets SET provider = 'Gate Futures' WHERE symbol = 'BYD'"
            )

        payload = server.wechat_group_monitor_payload({"id": user_id})
        state = payload["opportunities"][0]["symbolStates"][0]

        self.assertTrue(state["active"])
        self.assertEqual(state["provider"], "Gate Futures")
        self.assertEqual(state["tradeAction"]["kind"], "exchange")
        self.assertEqual(state["tradeAction"]["venue"], "Bitget")


if __name__ == "__main__":
    unittest.main()
