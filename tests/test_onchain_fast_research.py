import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from chain_ecosystem_monitor import ChainEcosystemStore, evaluate_onchain_candidate, fetch_geckoterminal_new_pools, scan_onchain_research
from onchain_fast_research import (
    DISCORD_MONITOR_PURGE_MIGRATION, FastResearch, NARRATIVE_VERSION, ResearchCapacityBusy,
    evidence_digest, FLAP_PORTAL, FLAP_CREATED,
    breakout_research_signal, candidate_key, decode_launch_log, news_trigger_signal, paginate_research,
    resolve_same_symbol_leaders, sort_research_recommendations,
)
from onchain_research_framework import FRAMEWORK_VERSION

NOW = 1_788_836_000_000


def detailed_analysis(**extra):
    return {"verdict": "strong", "confidence": 80, "summary": "有具体产品与传播线索，仍需复核合约和产品交付",
            "narrativeStrength": 75, "evidenceStatus": "supported", "evidenceRefs": ["story"], "narrativeVersion": NARRATIVE_VERSION,
            "frameworkAssessment": {
                "version": FRAMEWORK_VERSION, "potentialTier": "leader",
                "candidatePath": "early-cultural-leader", "currentStage": "S6",
                "attentionState": "A6", "previousAttentionState": "A5",
                "attentionTransition": "链上买方开始承接链外注意力",
                "transitionTrigger": "TR_BUYER_RESPONSE",
                "narrativeDiscovery": "题材已出现可核验的跨来源传播",
                "mappingFit": "当前CA与事件存在来源支持的映射",
                "leaderElection": "独立买方和成交扩散暂时领先",
                "candidateSet": ["当前CA · leader-candidate"],
                "currentLeader": "当前CA", "leaderRelation": "允许后续轮动",
                "nextTrigger": "扩大独立持币和跨社区传播",
                "riskTags": ["身份仍需持续复核"], "hardBlockReason": "",
                "primaryDriver": "题材传播与独立买方同步加速", "leaderReason": "同题材综合领先",
                "nextTransition": "扩大独立持币和跨社区传播", "invalidation": "买家与流动性同步衰减",
                "discoveryScore": 82, "stateTransitionBonus": 14, "metaScore": 78,
                "opportunityScore": 85, "leaderScore": 82, "executionScore": 68, "riskScore": 42,
                "executionPermission": "CAUTION", "auditStatus": "partial",
            },
            "narrative": {key: "测试来源支持具体线索，原文声称需进一步验证，不能只凭行情判断。"
                          for key in ("thesis", "attention", "evidence", "invalidation")}, **extra}


def candidate(symbol="EARLY", network="bsc", address=None):
    return {"network": network, "contractAddress": address or "0x" + "a" * 40,
            "symbol": symbol, "name": symbol, "firstSeenAt": NOW, "poolCreatedAt": NOW - 20_000,
            "observedAt": NOW, "providers": ["geckoterminal"], "dexId": "test",
            "metrics": {"liquidityUsd": 25000, "volumeH1Usd": 40000, "volumeM5Usd": 40000,
                        "transactionsH1": 60, "buysH1": 45, "sellsH1": 15}}


class FastResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChainEcosystemStore(Path(self.temp.name) / "chain.db")
        self.store.initialize()
        self.analyzer = Mock(side_effect=lambda rows: {candidate_key(row): detailed_analysis() for row in rows})
        self.sink = Mock(return_value={"ok": True})
        self.resonance_sink = Mock(return_value={"ok": True})
        self.fast = FastResearch(
            self.store, self.analyzer, self.sink, resonance_sink=self.resonance_sink
        )
        self.fast.initialize()
        self.clock = patch("onchain_fast_research._now_ms", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.temp.cleanup()

    def test_more_than_eight_candidates_all_receive_background_analysis_once(self):
        rows = [candidate(str(n), address="0x" + f"{n:040x}") for n in range(12)]
        self.fast.ingest(rows)
        for _ in range(6):
            self.fast.analyze_batch()
        self.assertEqual(sum(len(call.args[0]) for call in self.analyzer.call_args_list), 12)
        self.fast.ingest(rows)
        self.fast.analyze_batch()
        self.assertEqual(self.analyzer.call_count, 6)
        self.fast.emit_alerts()
        self.fast.emit_alerts()
        self.assertEqual(self.sink.call_count, 12)

    def test_ai_reconnect_requeues_recent_unavailable_candidates(self):
        self.fast.ingest([candidate("RETRY")])
        self.fast._write(
            "UPDATE onchain_fast_jobs SET status='unavailable',attempts=3,error='quota unavailable'"
        )

        count = self.fast.retry_unavailable_after_ai_reconnect()
        job = self.fast._query("SELECT status,attempts,next_due_at,error FROM onchain_fast_jobs")[0]

        self.assertEqual(count, 1)
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["attempts"], 0)
        self.assertLessEqual(job["next_due_at"], NOW)
        self.assertIn("重新分析", job["error"])

    def test_ai_reconnect_retry_is_bounded_and_keeps_only_the_best_batch_pending(self):
        rows = []
        for number in range(30):
            row = candidate(f"RETRY-{number}", address="0x" + f"{number + 1:040x}")
            row["selectedScore"] = number
            rows.append(row)
        self.fast.ingest(rows)
        for score, job in enumerate(self.fast._query("SELECT key FROM onchain_fast_jobs ORDER BY key")):
            self.fast._write(
                "UPDATE onchain_fast_jobs SET candidate_json=json_set(candidate_json,'$.selectedScore',?) WHERE key=?",
                (score, job["key"]),
            )
        self.fast._write(
            "UPDATE onchain_fast_jobs SET status='unavailable',attempts=3,error='quota unavailable'"
        )

        first_count = self.fast.retry_unavailable_after_ai_reconnect(limit=7)
        second_count = self.fast.retry_unavailable_after_ai_reconnect(limit=7)
        jobs = self.fast._query(
            "SELECT status,candidate_json FROM onchain_fast_jobs ORDER BY key"
        )
        pending_scores = sorted(
            json.loads(job["candidate_json"])["selectedScore"]
            for job in jobs if job["status"] == "pending"
        )

        self.assertEqual(first_count, 7)
        self.assertEqual(second_count, 7)
        self.assertEqual(pending_scores, list(range(23, 30)))
        self.assertEqual(sum(job["status"] == "unavailable" for job in jobs), 23)

    def test_recommendations_are_ranked_best_to_worst_before_pagination(self):
        def row(symbol, verdict, narrative, confidence, evidence="partial", score=70, tier="ai-recommended"):
            return {**candidate(symbol, address="0x" + symbol.encode().hex().ljust(40, "0")[:40]),
                    "researchTier": tier, "selectedScore": score,
                    "aiAnalysis": {**detailed_analysis(), "verdict": verdict,
                                   "narrativeStrength": narrative, "confidence": confidence,
                                   "evidenceStatus": evidence}}
        rows = [
            row("NEWS", "", 99, 99, tier="news-triggered"),
            row("WATCH", "watch", 95, 99, score=95),
            row("STRONG_PART", "strong", 98, 99, evidence="partial", score=98),
            row("STRONG_LOW", "strong", 60, 70, evidence="supported", score=60),
            row("STRONG_HIGH", "strong", 80, 90, evidence="supported", score=80),
        ]

        ranked = sort_research_recommendations(rows)
        self.assertEqual([item["symbol"] for item in ranked],
                         ["STRONG_HIGH", "STRONG_LOW", "STRONG_PART", "WATCH", "NEWS"])
        paged = paginate_research({"selected": rows}, page=1, page_size=2)
        self.assertEqual([item["symbol"] for item in paged["selected"]], ["STRONG_HIGH", "STRONG_LOW"])
        self.assertEqual(paged["pagination"], {"page": 1, "pageSize": 2, "pages": 3, "total": 5})

    def test_candidate_enricher_runs_after_quantitative_screening(self):
        seen = []

        def enrich(row):
            seen.append(row["decision"])
            return {**row, "decision": "shortlisted", "researchEvidence": {"text": "热点证据"}}

        fast = FastResearch(self.store, self.analyzer, self.sink, candidate_enricher=enrich)
        fast.initialize()
        fast.ingest([candidate("HOT")])
        job = fast._query("SELECT * FROM onchain_fast_jobs")[0]

        self.assertTrue(seen)
        self.assertEqual(job["status"], "pending")
        self.assertEqual(json.loads(job["candidate_json"])["researchEvidence"]["text"], "热点证据")

    def test_realtime_claims_are_disjoint_and_do_not_wait_behind_old_or_review_jobs(self):
        from concurrent.futures import ThreadPoolExecutor
        old = [candidate(str(n), address='0x'+f'{n:040x}') for n in range(30)]
        self.fast.ingest(old)
        with patch('onchain_fast_research._now_ms', return_value=NOW+600_000):
            fresh = [{**candidate('NEW'+str(n),address='0x'+f'{100+n:040x}'),
                'firstSeenAt':NOW+600_000,'observedAt':NOW+600_000,'poolCreatedAt':NOW+590_000} for n in range(4)]
            self.fast.ingest(fresh)
            with ThreadPoolExecutor(max_workers=3) as pool:
                live1,live2,history = [f.result() for f in [pool.submit(self.fast._claim_analysis_jobs,lane)
                    for lane in ('live','live','history')]]
            claimed=[j['key'] for j in [*live1,*live2,*history]]
            self.assertEqual(len(claimed),6)
            self.assertEqual(len(set(claimed)),6)
            self.assertEqual({j['key'] for j in [*live1,*live2]}, {candidate_key(r) for r in fresh})
            self.assertTrue(all(j['key'] in {candidate_key(r) for r in old} for j in history))
            self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_fast_jobs WHERE status='pending'")[0]['n'],28)

    def test_busy_is_queueing_not_three_failed_analyses(self):
        self.fast.ingest([candidate()])
        self.analyzer.side_effect = ResearchCapacityBusy('occupied')
        for n in range(5):
            with patch('onchain_fast_research._now_ms',return_value=NOW+n*3_000): self.fast.analyze_batch('live')
        row=self.fast._query('SELECT * FROM onchain_fast_jobs')[0]
        self.assertEqual(row['status'],'pending')
        self.assertEqual(row['attempts'],0)
        self.assertEqual(row['first_analyzed_at'],0)
        self.sink.assert_not_called()

    def test_first_analysis_timing_survives_later_reviews(self):
        self.fast.ingest([candidate()])
        with patch('onchain_fast_research._now_ms',return_value=NOW+30_000): self.fast.analyze_batch()
        self.fast._write("UPDATE onchain_fast_jobs SET status='pending',next_due_at=0,attempts=0")
        with patch('onchain_fast_research._now_ms',return_value=NOW+600_000): self.fast.analyze_batch()
        job=self.fast._query('SELECT * FROM onchain_fast_jobs')[0]
        self.assertEqual(job['first_analyzed_at'],NOW+30_000)
        self.assertEqual(job['analyzed_at'],NOW+600_000)

    def test_late_recovered_worker_cannot_replace_newer_result(self):
        self.fast.ingest([candidate()])
        def replaced(rows):
            self.fast._write("UPDATE onchain_fast_jobs SET started_at=?,analysis_json=?", (NOW+130_000,json.dumps({'summary':'newer'})))
            return {candidate_key(rows[0]):detailed_analysis()}
        self.analyzer.side_effect=replaced
        self.fast.analyze_batch()
        job=self.fast._query('SELECT * FROM onchain_fast_jobs')[0]
        self.assertEqual(json.loads(job['analysis_json'])['summary'],'newer')
        self.assertEqual(job['first_analyzed_at'],0)
        self.assertEqual(self.fast._query('SELECT COUNT(*) AS n FROM onchain_research_recommendations')[0]['n'],0)

    def test_text_and_content_news_evidence_are_equivalent_but_new_text_requeues(self):
        row=candidate(); row['researchEvidence']={'text':'original','url':'https://example.test'}
        other={**row,'researchEvidence':{'content':'original','url':'https://example.test'}}
        self.assertEqual(evidence_digest(row),evidence_digest(other))
        self.fast.ingest([row]); self.fast.analyze_batch()
        row['researchEvidence']['text']='new catalyst'
        self.fast.ingest([row])
        self.assertEqual(self.fast._query('SELECT status FROM onchain_fast_jobs')[0]['status'],'pending')

    def test_retry_survives_restart_and_empty_result_is_not_a_good_coin(self):
        self.fast.ingest([candidate()])
        self.analyzer.side_effect = TimeoutError("busy")
        self.fast.analyze_batch()
        self.fast.emit_alerts()
        self.sink.assert_not_called()
        restarted = FastResearch(self.store, lambda rows: {candidate_key(rows[0]): {"verdict": "avoid", "summary": "没有可验证题材"}}, self.sink)
        restarted.initialize()
        with patch("onchain_fast_research._now_ms", return_value=NOW + 12_000):
            restarted.analyze_batch()
            restarted.emit_alerts()
        row = restarted._query("SELECT * FROM onchain_fast_jobs")[0]
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["attempts"], 2)
        self.sink.assert_not_called()

    def test_empty_market_quote_keeps_launch_in_retry_scope(self):
        self.fast.ingest([{**candidate(), "metrics": {}}])
        self.fast.quote_fetcher = Mock(return_value={"pairs": []})
        with patch("onchain_fast_research._now_ms", return_value=NOW + 16_000):
            self.fast.refresh_quotes()
        self.assertGreater(self.fast._query("SELECT quote_due_at FROM onchain_fast_jobs")[0]["quote_due_at"], NOW + 16_000)
        self.fast.analyze_batch()
        self.analyzer.assert_not_called()

    def test_news_candidate_gets_review_slot_without_cross_chain_or_identity_claim(self):
        row = evaluate_onchain_candidate(candidate("CME", "robinhood"), now_ms=NOW)
        wrong = evaluate_onchain_candidate(candidate("CME", "bsc", "0x" + "b" * 40), now_ms=NOW)
        self.store.save_onchain_research_scan([row, wrong], observed_at=NOW)
        item = {"id": "news1", "add_time": NOW // 1000, "title": "Robinhood 链代币CME上线", "content": "商品市场交易所题材"}
        self.fast.ingest_news(item)
        self.fast.ingest_news(item)
        jobs = self.fast._query("SELECT * FROM onchain_fast_jobs")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["network"], "robinhood")
        self.fast.analyze_batch()
        self.fast.emit_alerts()
        self.sink.assert_not_called()  # Exact news/contract identity remains unverified.
        research = self.fast.attach({"day": "2026-09-08", "selected": [candidate(str(n), address="0x" + f"{n:040x}") for n in range(8)]})
        self.assertEqual(research["selected"][0]["symbol"], "CME")
        self.assertEqual(len(research["selected"]), 1)  # Unreviewed rows are not recommendations.
        self.assertEqual(research["reviewQueue"]["pending"], 8)
        self.assertEqual(self.analyzer.call_count, 1)

    def test_exact_contract_news_enters_selected_before_ai_finishes(self):
        address = "0x" + "d" * 40
        row = evaluate_onchain_candidate(candidate("JACOB", "robinhood", address), now_ms=NOW)
        self.store.save_onchain_research_scan([row], observed_at=NOW)

        self.fast.ingest_news({
            "id": "formula-jacob", "source": "方程式新闻", "add_time": NOW // 1000,
            "title": "JACOB 热点事件发酵", "content": f"合约地址：{address}",
            "url": "https://example.test/jacob",
        })

        stored = json.loads(self.fast._query("SELECT candidate_json FROM onchain_fast_jobs")[0]["candidate_json"])
        self.assertEqual(news_trigger_signal(stored, now_ms=NOW)["tier"], "news-triggered")
        research = self.fast.attach({"day": "2026-09-08", "selected": [], "watching": []})
        self.assertEqual(research["selectedTotal"], 1)
        self.assertEqual(research["selected"][0]["researchTier"], "news-triggered")
        self.assertEqual(research["selected"][0]["researchEvidence"]["identityStatus"], "news-contract-explicit")
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_research_recommendations")[0]["n"], 0)
        self.analyzer.assert_not_called()

    def test_person_event_news_can_surface_active_same_name_coin_with_warning(self):
        row = evaluate_onchain_candidate(candidate("JACOB", "robinhood", "0x" + "e" * 40), now_ms=NOW)
        self.store.save_onchain_research_scan([row], observed_at=NOW)

        self.fast.ingest_news({
            "id": "person-jacob", "source": "聚合快讯", "add_time": NOW // 1000,
            "title": "Jacob researcher resigns after public statement",
            "content": "The event is going viral and has become a market narrative.",
            "url": "https://example.test/person-jacob",
        })

        research = self.fast.attach({"day": "2026-09-08", "selected": [], "watching": []})
        self.assertEqual(research["selected"][0]["symbol"], "JACOB")
        self.assertEqual(research["selected"][0]["researchTier"], "news-triggered")
        self.assertTrue(research["selected"][0]["identityAmbiguous"])
        self.assertEqual(research["selected"][0]["researchEvidence"]["identityStatus"], "news-name-contract-unverified")

    def test_chinese_name_4stock_and_explicit_meme_resonate_without_ai_wait(self):
        rows = [
            {**candidate("NIULAI", address="0x" + "1" * 40), "name": "牛来"},
            candidate("4STOCK", address="0x" + "2" * 40),
            candidate("MEME", address="0x" + "3" * 40),
        ]
        self.store.save_onchain_research_scan(rows, observed_at=NOW)
        stories = (
            {"id": "niulai", "add_time": (NOW - 48 * 3_600_000) // 1000,
             "title": "牛来热度持续发酵", "content": "相关代币交易活跃"},
            {"id": "4stock", "add_time": NOW // 1000,
             "title": "4STOCK 热点事件继续发酵", "content": "市场关注快速升温"},
            {"id": "meme", "add_time": (NOW - 60 * 3_600_000) // 1000,
             "title": "交易平台上线 MEME/USDT", "content": "MEME 代币交易正式推出"},
        )

        for story in stories:
            self.fast.ingest_news(story)

        alerted_symbols = {call.args[0]["symbol"] for call in self.resonance_sink.call_args_list}
        self.assertEqual(alerted_symbols, {"NIULAI", "4STOCK", "MEME"})
        self.assertTrue(all(call.args[1]["confidence"] >= 70 for call in self.resonance_sink.call_args_list))

    def test_generic_lowercase_meme_word_does_not_match_meme_ticker(self):
        row = candidate("MEME", address="0x" + "4" * 40)
        self.store.save_onchain_research_scan([row], observed_at=NOW)

        self.fast.ingest_news({
            "id": "generic-meme", "add_time": NOW // 1000,
            "title": "市场讨论 meme 文化事件", "content": "这是泛指，不是具体代币",
        })

        self.resonance_sink.assert_not_called()

    def test_same_news_identity_selects_one_strongest_contract(self):
        weak = {**candidate("NIULAI", address="0x" + "7" * 40), "name": "牛来"}
        strong = {
            **candidate("牛来", address="0x" + "8" * 40), "name": "牛来",
            "metrics": {**candidate()["metrics"], "liquidityUsd": 90_000},
        }
        self.store.save_onchain_research_scan([weak, strong], observed_at=NOW)

        self.fast.ingest_news({
            "id": "one-niulai", "add_time": NOW // 1000,
            "title": "牛来热点事件持续发酵", "content": "相关代币成交活跃",
        })

        self.assertEqual(self.resonance_sink.call_count, 1)
        self.assertEqual(self.resonance_sink.call_args.args[0]["contractAddress"], strong["contractAddress"])

    def test_news_first_is_persisted_and_matches_when_candidate_arrives(self):
        self.fast.ingest_news({
            "id": "news-first-niulai", "add_time": (NOW - 2 * 3_600_000) // 1000,
            "title": "牛来宣布推出新叙事", "content": "相关事件正在热议",
            "url": "https://example.test/niulai",
        })
        self.resonance_sink.assert_not_called()

        self.fast.ingest([{**candidate("NIULAI", address="0x" + "5" * 40), "name": "牛来"}])
        self.assertEqual(self.resonance_sink.call_count, 1)
        self.assertEqual(self.resonance_sink.call_args.args[1]["matchType"], "exact-name")
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_recent_news")[0]["n"], 1)

        self.fast.ingest([{**candidate("NIULAI", address="0x" + "5" * 40), "name": "牛来"}])
        self.fast.ingest_news({
            "id": "news-first-niulai", "add_time": (NOW - 2 * 3_600_000) // 1000,
            "title": "牛来宣布推出新叙事", "content": "相关事件正在热议",
            "url": "https://example.test/niulai",
        })
        self.assertEqual(self.resonance_sink.call_count, 1)

    def test_news_older_than_three_days_is_not_retained_or_alerted(self):
        row = candidate("OLD", address="0x" + "6" * 40)
        self.store.save_onchain_research_scan([row], observed_at=NOW)

        self.fast.ingest_news({
            "id": "too-old", "add_time": (NOW - 73 * 3_600_000) // 1000,
            "title": "OLD 代币热点事件", "content": "已经超过三天",
        })

        self.resonance_sink.assert_not_called()
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_recent_news")[0]["n"], 0)

    def test_contract_news_received_before_pool_keeps_event_on_resolved_candidate(self):
        address = "0x" + "f" * 40
        self.fast.ingest_news({
            "id": "early-ca", "source": "BlockBeats 律动", "add_time": NOW // 1000,
            "title": "新 Meme 代币即将上线", "content": f"CA: {address}",
            "url": "https://example.test/early-ca",
        })
        resolved = candidate("EARLY", "bsc", address)
        with patch("onchain_fast_research.fetch_dexscreener_token", return_value={}), patch(
            "onchain_fast_research.normalize_onchain_dexscreener", return_value=[resolved]
        ):
            self.fast.resolve_clues()

        research = self.fast.attach({"day": "2026-09-08", "selected": [], "watching": []})
        self.assertEqual(research["selected"][0]["researchTier"], "news-triggered")
        self.assertEqual(research["selected"][0]["newsSignal"]["publishedAt"], NOW)

    def test_plain_kol_text_with_ca_does_not_create_news_trigger(self):
        address = "0x" + "9" * 40
        self.fast.ingest_text(f"watch {address}", "x-monitor")
        clue = self.fast._query("SELECT event_json FROM onchain_contract_clues")[0]
        self.assertEqual(json.loads(clue["event_json"]), {})

    def test_group_chat_is_deduped_and_only_attached_as_cross_validation(self):
        address = "0x" + "a" * 40
        self.fast.ingest([candidate("CHAT", address=address)])
        self.fast.analyze_batch()

        repeated = f"群里看到 CHAT，CA {address}，成交开始放大"
        self.fast.ingest_chat_context(
            repeated, "梦之队", platform="wechat", sender="甲",
            observed_at=NOW, contracts=[{"contractAddress": address}], symbols=["CHAT"],
        )
        self.fast.ingest_chat_context(
            repeated, "另一个转发群", platform="qq", sender="乙",
            observed_at=NOW + 1000, contracts=[{"contractAddress": address}], symbols=["CHAT"],
        )
        first = json.loads(self.fast._query(
            "SELECT candidate_json FROM onchain_fast_jobs WHERE key=?", (candidate_key(candidate()),)
        )[0]["candidate_json"])

        self.assertEqual(first["crossValidation"]["independentSourceCount"], 1)
        self.assertEqual(first["crossValidation"]["duplicateMentions"], 1)
        self.assertNotIn("newsSignal", first)
        self.assertEqual(first["decision"], "shortlisted")

        self.fast.ingest_chat_context(
            f"我核过链上地址，CHAT 对应 {address}，社区讨论在升温",
            "研究群", platform="wechat", sender="丙", observed_at=NOW + 2000,
            contracts=[{"contractAddress": address}], symbols=["CHAT"],
        )
        second = json.loads(self.fast._query(
            "SELECT candidate_json FROM onchain_fast_jobs WHERE key=?", (candidate_key(candidate()),)
        )[0]["candidate_json"])
        self.assertEqual(second["crossValidation"]["independentSourceCount"], 2)
        self.assertTrue(second["crossValidation"]["corroborated"])
        self.assertIn("2 条独立讨论", second["crossValidation"]["summary"])

    def test_symbol_only_group_chat_never_creates_a_candidate_or_news_trigger(self):
        result = self.fast.ingest_chat_context(
            "$GHOST 可能会火", "普通群", platform="qq", sender="群友",
            observed_at=NOW, symbols=["GHOST"],
        )

        self.assertEqual(result["matchedCandidates"], 0)
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_fast_jobs")[0]["n"], 0)

    def test_group_chat_drops_venue_stablecoin_and_emoji_code_noise(self):
        result = self.fast.ingest_chat_context(
            "Robinhood 🔥 USDG", "普通群", platform="wechat", sender="群友",
            observed_at=NOW, symbols=["ROBINHOOD", "USDG", "1F525", "FE0F", "PAIREX"],
        )

        self.assertEqual(result["recorded"], 0)
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_chat_evidence")[0]["n"], 0)

    def test_discord_input_is_rejected_but_direct_formula_rss_news_remains_valid(self):
        address = "0x" + "6" * 40
        self.store.save_onchain_research_scan(
            [evaluate_onchain_candidate(candidate("FORMULA", "bsc", address), now_ms=NOW)],
            observed_at=NOW,
        )

        chat = self.fast.ingest_chat_context(
            f"FORMULA CA {address}", "华尔街聚合 / 方程式新闻",
            platform="discord", sender="机器人", observed_at=NOW,
            contracts=[{"contractAddress": address}], symbols=["FORMULA"],
        )
        self.fast.ingest_news({
            "id": "legacy-dc", "source": "方程式新闻", "platform": "discord",
            "add_time": NOW // 1000, "title": "FORMULA 热点", "content": f"CA {address}",
            "url": "https://discord.com/channels/1/2/3",
        })

        self.assertEqual(chat["ignored"], "discord-monitor-disabled")
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_chat_evidence")[0]["n"], 0)
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_recent_news")[0]["n"], 0)
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_fast_jobs")[0]["n"], 0)

        self.fast.ingest_news({
            "id": "formula-rss", "sourceId": "bwenews", "source": "方程式新闻",
            "add_time": NOW // 1000, "title": "FORMULA 热点", "content": f"CA {address}",
            "url": "https://t.me/BWEnews/123",
        })
        self.assertEqual(self.fast._query("SELECT COUNT(*) AS n FROM onchain_recent_news")[0]["n"], 1)
        stored = json.loads(self.fast._query("SELECT candidate_json FROM onchain_fast_jobs")[0]["candidate_json"])
        self.assertEqual(stored["researchEvidence"]["source"], "方程式新闻")

    def test_initialize_purges_legacy_discord_monitor_rows_and_derivatives(self):
        address = "0x" + "7" * 40
        dirty = evaluate_onchain_candidate(candidate("LEGACYDC", "bsc", address), now_ms=NOW)
        dirty["providers"] = ["chat:discord:华尔街聚合 / 方程式新闻"]
        dirty["reasons"] = ["外部线索：chat:discord:旧频道"]
        self.store.save_onchain_research_scan([dirty], observed_at=NOW)
        self.fast.ingest([dirty])
        key = candidate_key(dirty)
        with self.store._lock:
            conn = self.store._connect()
            try:
                candidate_id = conn.execute(
                    "SELECT id FROM onchain_research_candidates WHERE network=? AND contract_address=?",
                    ("bsc", address),
                ).fetchone()["id"]
                conn.execute(
                    "INSERT OR REPLACE INTO onchain_chat_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("dc-evidence", "contract", address, "discord", "方程式新闻", "bot",
                     f"CA {address}", NOW, NOW, 1, NOW),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO onchain_contract_clues VALUES(?,?,?,?,?,?,?,?)",
                    (address, "discord-方程式新闻", f"CA {address}", NOW, NOW, 0, 0, "{}"),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO onchain_recent_news VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("dc-news", "dc-news", "方程式新闻", "旧DC消息", f"CA {address}",
                     "https://discord.com/channels/1/2/3", NOW, "[]", json.dumps([address]),
                     NOW + 1000, NOW, NOW),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO onchain_research_recommendations VALUES(?,?,?,?)",
                    (key, NOW, json.dumps(dirty, ensure_ascii=False), json.dumps({"source": "discord"})),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO onchain_research_story_matches VALUES(?,?,?,?,?,?,?)",
                    ("dc-story", key, "旧DC消息", "https://discord.com/channels/1/2/3", NOW, "{}", 0),
                )
                conn.execute(
                    "UPDATE onchain_research_snapshots SET reasons_json=? WHERE candidate_id=?",
                    (json.dumps(["chat:discord:旧频道"], ensure_ascii=False), candidate_id),
                )
                conn.execute("DELETE FROM onchain_data_migrations WHERE name=?", (DISCORD_MONITOR_PURGE_MIGRATION,))
                conn.commit()
            finally:
                conn.close()

        self.fast.initialize()

        for table in (
            "onchain_chat_evidence", "onchain_contract_clues", "onchain_recent_news",
            "onchain_fast_jobs", "onchain_research_candidates", "onchain_research_recommendations",
            "onchain_research_snapshots", "onchain_research_story_matches",
        ):
            self.assertEqual(self.fast._query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"], 0, table)

    def test_all_results_beyond_eight_and_forty_are_reachable_by_pages(self):
        rows = [candidate(str(n), address="0x" + f"{n:040x}") for n in range(53)]
        self.fast.ingest(rows)
        for _ in range(27):
            self.fast.analyze_batch()
        research = self.fast.attach({"day": "2026-09-08", "selected": [], "watching": []})
        self.assertEqual(research["selectedTotal"], 53)
        keys = []
        for page in range(1, 6):
            payload = paginate_research(research, page)
            keys += [candidate_key(row) for row in payload["selected"]]
            self.assertLessEqual(len(payload["selected"]), 12)
            self.assertEqual(payload["pagination"]["total"], 53)
        self.assertEqual(len(set(keys)), 53)
        restarted = FastResearch(self.store, self.analyzer, self.sink)
        restarted.initialize()
        restarted.restore_review_queue()
        restarted.analyze_batch()
        self.assertEqual(self.analyzer.call_count, 27)

    def test_late_qualification_and_oldest_job_do_not_expire(self):
        self.fast.ingest([{**candidate(), "metrics": {}}])
        with patch("onchain_fast_research._now_ms", return_value=NOW + 3_600_000):
            self.fast.ingest([candidate()])
            self.fast.ingest([candidate(str(n), address="0x" + f"{n:040x}") for n in range(6)])
            self.fast.analyze_batch()
        self.assertIn(candidate_key(candidate()), [candidate_key(row) for row in self.analyzer.call_args.args[0]])

    def test_unsubstantiated_strong_verdict_cannot_recommend_or_alert(self):
        self.analyzer.side_effect = lambda rows: {candidate_key(row): detailed_analysis(evidenceStatus="insufficient", evidenceRefs=[]) for row in rows}
        self.fast.ingest([candidate()])
        self.fast.analyze_batch()
        self.fast.emit_alerts()
        self.sink.assert_not_called()
        research = self.fast.attach({"day": "2026-09-08", "selected": []})
        self.assertEqual(research["selected"], [])
        self.assertEqual(research["reviewQueue"]["needsEvidence"], 1)

    def test_extreme_early_quantitative_breakout_is_visible_while_narrative_is_pending(self):
        row = {
            **candidate("JACOB", "robinhood"),
            "name": "Be like Jacob",
            "providers": ["geckoterminal", "dexscreener"],
            "metrics": {
                "liquidityUsd": 49_467,
                "volumeH1Usd": 489_470,
                "volumeM5Usd": 95_000,
                "volumeH6Usd": 489_470,
                "transactionsH1": 3_496,
                "buysH1": 2_100,
                "sellsH1": 1_396,
                "buyersM5": 300,
            },
        }
        self.analyzer.side_effect = lambda rows: {
            candidate_key(item): detailed_analysis(
                verdict="watch", confidence=48, narrativeStrength=24,
                evidenceStatus="insufficient", evidenceRefs=[],
            ) for item in rows
        }

        self.fast.ingest([row])
        stored = json.loads(self.fast._query("SELECT candidate_json FROM onchain_fast_jobs")[0]["candidate_json"])
        self.assertTrue(stored["breakoutObservedAt"])
        self.assertEqual(breakout_research_signal(stored)["tier"], "quantitative-breakout")
        self.fast.analyze_batch()
        self.fast.emit_alerts()

        research = self.fast.attach({"day": "2026-09-08", "selected": [], "watching": []})
        self.assertEqual(research["selected"], [])
        self.assertEqual(research["provisionalTotal"], 1)
        self.assertEqual(research["provisional"][0]["symbol"], "JACOB")
        self.assertEqual(research["provisional"][0]["researchTier"], "quantitative-breakout")
        self.assertEqual(research["funnel"]["provisional"], 1)
        self.sink.assert_not_called()

    def test_breakout_requires_cross_source_confirmation_and_dedupes_same_symbol_warning(self):
        strong = {
            **candidate("JACOB", "robinhood"),
            "providers": ["geckoterminal", "dexscreener"],
            "metrics": {
                "liquidityUsd": 50_000, "volumeH1Usd": 500_000,
                "volumeH6Usd": 500_000, "transactionsH1": 3_500,
                "buysH1": 2_100, "sellsH1": 1_400, "buyersM5": 300,
            },
        }
        single_source = {**strong, "contractAddress": "0x" + "b" * 40, "providers": ["geckoterminal"]}
        self.fast.ingest([strong, single_source])

        self.assertTrue(breakout_research_signal(json.loads(
            self.fast._query("SELECT candidate_json FROM onchain_fast_jobs WHERE contract=?", (strong["contractAddress"],))[0]["candidate_json"]
        )))
        self.assertFalse(breakout_research_signal(json.loads(
            self.fast._query("SELECT candidate_json FROM onchain_fast_jobs WHERE contract=?", (single_source["contractAddress"],))[0]["candidate_json"]
        )))

        second = {**strong, "contractAddress": "0x" + "c" * 40, "metrics": {**strong["metrics"], "liquidityUsd": 55_000}}
        self.fast.ingest([second])
        research = self.fast.attach({"day": "2026-09-08", "selected": [], "watching": []})
        self.assertEqual(research["provisionalTotal"], 2)
        self.assertTrue(all(row["identityAmbiguous"] for row in research["provisional"]))
        self.assertTrue(all(row["sameSymbolContractCount"] == 2 for row in research["provisional"]))

    def test_same_name_candidates_keep_one_leader_and_prefer_explicit_ca_evidence(self):
        richer = {**candidate("MASCOT", address="0x" + "b" * 40),
                  "metrics": {**candidate()["metrics"], "marketCapUsd": 2_000_000, "liquidityUsd": 90_000},
                  "poolCreatedAt": NOW - 120_000,
                  "researchEvidence": {"identityStatus": "same-chain-symbol-unverified"}}
        explicit = {**candidate("MASCOT", address="0x" + "c" * 40),
                    "metrics": {**candidate()["metrics"], "marketCapUsd": 400_000, "liquidityUsd": 30_000},
                    "poolCreatedAt": NOW - 60_000,
                    "researchEvidence": {"identityStatus": "news-contract-explicit"}}

        visible, suppressed = resolve_same_symbol_leaders([richer, explicit])

        self.assertEqual(len(visible), 1)
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(visible[0]["contractAddress"], explicit["contractAddress"])
        self.assertEqual(visible[0]["sameSymbolRole"], "leader-candidate")
        self.assertIn("CA", visible[0]["sameSymbolLeaderReason"])
        self.assertEqual(suppressed[0]["sameSymbolRole"], "challenger")

    def test_withdrawn_recommendation_keeps_reason_and_original_analysis_after_restart(self):
        self.fast.ingest([candidate()])
        self.fast.analyze_batch()
        with patch("onchain_fast_research._now_ms", return_value=NOW + 3_600_000):
            self.fast.ingest([{**candidate(), "metrics": {"liquidityUsd": 10, "transactionsH1": 1}}])
        restarted = FastResearch(self.store, self.analyzer, self.sink)
        restarted.initialize()
        research = restarted.attach({"day": "2026-09-08", "selected": []})
        self.assertEqual(research["selected"], [])
        self.assertEqual(len(research["recommendationHistory"]), 1)
        self.assertIn("初筛条件", research["recommendationHistory"][0]["withdrawalReason"])
        self.assertEqual(research["recommendationHistory"][0]["previousAnalysis"]["summary"], detailed_analysis()["summary"])

    def test_solana_case_is_not_folded_and_all_message_contracts_are_retained(self):
        a, b = "A" * 32, "a" * 32
        self.fast.ingest_text(f"{a} {b} 0x{'a'*40}", "qq")
        self.assertEqual(len(self.fast._query("SELECT * FROM onchain_contract_clues")), 3)

    def test_rpc_failure_does_not_skip_checkpoint(self):
        self.fast._write("INSERT INTO onchain_launch_cursor VALUES('bsc-launch',100,?, '')", (NOW,))
        self.fast.rpc = Mock(side_effect=[hex(150), TimeoutError("offline")])
        self.fast.poll_launches()
        self.assertEqual(self.fast._query("SELECT block FROM onchain_launch_cursor")[0]["block"], 100)
        with patch("onchain_fast_research._now_ms", return_value=NOW + 20_000):
            self.fast.rpc = Mock(side_effect=[hex(170), []])
            self.fast.poll_launches()
        self.assertEqual(self.fast._query("SELECT block FROM onchain_launch_cursor")[0]["block"], 168)
        self.assertEqual(self.fast.rpc.call_args_list[1].args[1][0]["fromBlock"], hex(98))

    def test_decode_launch_identity_and_reject_unrecognized_emitter(self):
        def uint(n): return n.to_bytes(32, "big")
        def dynamic(text):
            data = text.encode()
            return uint(len(data)) + data + b"\0" * ((-len(data)) % 32)
        name, symbol, meta = dynamic("LUNA"), dynamic("LUNA"), dynamic("ipfs")
        data = b"".join([uint(NOW // 1000), uint(123), uint(1), bytes.fromhex("00"*12 + "ab"*20), uint(224), uint(224+len(name)), uint(224+len(name)+len(symbol)), name, symbol, meta])
        log = {"address": FLAP_PORTAL, "topics": [FLAP_CREATED], "data": "0x"+data.hex()}
        row = decode_launch_log(log, received_at=NOW + 1000)
        self.assertEqual(row["symbol"], "LUNA")
        self.assertEqual(row["contractAddress"], "0x"+"ab"*20)
        self.assertEqual(row["firstSeenAt"], NOW + 1000)
        self.assertIsNone(decode_launch_log({**log, "removed": True}))
        self.assertIsNone(decode_launch_log({**log, "address": "0x"+"ab"*20}))

    def test_missing_links_does_not_drop_second_page(self):
        with patch("chain_ecosystem_monitor._get_json", side_effect=[{"data": [{"id": "new"}]}, {"data": [{"id": "known"}]}]) as fetch:
            payload = fetch_geckoterminal_new_pools("bsc", pages=3, known_pool_ids=["known"])
        self.assertEqual(len(payload["data"]), 2)
        self.assertEqual(fetch.call_count, 2)

    def test_first_screen_is_persisted_and_queued_before_market_enrichment(self):
        def enrich(*args):
            jobs = self.fast._query("SELECT * FROM onchain_fast_jobs")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "pending")
            self.assertLess(jobs[0]["screened_at"] - jobs[0]["first_seen_at"], 60_000)
            return {"pairs": []}
        with patch("chain_ecosystem_monitor.normalize_gmgn_migrated_trenches", return_value=[candidate()]):
            scan_onchain_research(self.store, networks=["bsc"], observed_at=NOW,
                binance_launch_fetcher=lambda *_args, **_kwargs: {"data": []},
                gmgn_trenches_fetcher=lambda *args, **kwargs: {}, dexscreener_fetcher=enrich,
                candidate_sink=self.fast.ingest)


if __name__ == "__main__":
    unittest.main()
