import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server
from x_tweet_analysis import CHAT_TITLE, XTweetAnalysisQueue, normalize_analysis


NOW = 1_800_000_000_000


def event(key="tweet-1"):
    return {
        "key": key,
        "tweetId": key,
        "source": "Vitalik Buterin",
        "authorHandle": "VitalikButerin",
        "xCategory": "kol",
        "url": f"https://x.com/VitalikButerin/status/{key}",
        "time": NOW,
        "originalText": "PeerDAS has now been running for nearly a year.",
        "quoteText": "",
    }


def analysis(job_id):
    return {
        "jobId": job_id,
        "worthWatching": True,
        "urgency": "high",
        "oneLine": "以太坊扩容叙事获得重要人物背书",
        "meaning": "Vitalik 强调 PeerDAS 已稳定运行，重新强化以太坊扩容叙事。",
        "novelty": "high",
        "genericTheme": False,
        "noveltyReason": "包含可验证的协议运行进展",
        "keywords": ["PeerDAS", "扩容"],
        "memePotential": "strong",
        "memeReason": "有清晰名词和重要人物传播",
        "tokens": [{
            "symbol": "ETH", "chain": "eth", "ca": "0xabc",
            "relationship": "生态代表币", "official": True, "evidence": "原生资产",
        }],
        "risk": "不是新发布事件，传播持续性待观察",
    }


class XTweetAnalysisQueueTests(unittest.TestCase):
    def make_queue(self, folder):
        return XTweetAnalysisQueue(f"{folder}/queue.sqlite")

    def test_enqueue_is_restart_safe_and_claim_is_bound_to_exact_chat(self):
        with TemporaryDirectory() as folder:
            queue = self.make_queue(folder)
            first = queue.enqueue([event()], min_published_at=NOW - 1, now_ms=NOW)
            second = queue.enqueue([event()], min_published_at=NOW - 1, now_ms=NOW + 1)
            self.assertEqual(first["accepted"], 1)
            self.assertEqual(second["accepted"], 0)
            with self.assertRaises(ValueError):
                queue.claim(target_thread_id="wrong", target_thread_title="别的聊天", now_ms=NOW + 2)
            claimed = queue.claim(
                target_thread_id="chat-x", target_thread_title=CHAT_TITLE, now_ms=NOW + 2
            )
            self.assertEqual(claimed["status"], "claimed")
            self.assertIn("PeerDAS", claimed["prompt"])
            self.assertIn(claimed["jobId"], claimed["prompt"])
            recovered = XTweetAnalysisQueue(f"{folder}/queue.sqlite").claim(
                target_thread_id="chat-x", target_thread_title=CHAT_TITLE, now_ms=NOW + 3
            )
            self.assertTrue(recovered["recover"])
            self.assertEqual(recovered["jobId"], claimed["jobId"])

    def test_incremental_mode_retires_backlog_and_rejects_pre_baseline_posts(self):
        with TemporaryDirectory() as folder:
            queue = self.make_queue(folder)
            queue.enqueue([event("history")], now_ms=NOW)

            state = queue.enable_incremental_only(now_ms=NOW + 100)
            rejected = queue.enqueue([event("older")], now_ms=NOW + 101)
            new_event = event("new") | {"time": NOW + 200}
            accepted = queue.enqueue([new_event], now_ms=NOW + 201)

            self.assertTrue(state["initialized"])
            self.assertEqual(state["suppressed"], 1)
            self.assertEqual(rejected["accepted"], 0)
            self.assertEqual(accepted["accepted"], 1)
            self.assertEqual(queue.status()["counts"]["suppressed"], 1)
            reopened = XTweetAnalysisQueue(f"{folder}/queue.sqlite")
            self.assertEqual(
                reopened.enable_incremental_only(now_ms=NOW + 500)["cutoffMs"],
                NOW + 100,
            )

    def test_complete_requires_matching_job_id_and_persists_result(self):
        with TemporaryDirectory() as folder:
            queue = self.make_queue(folder)
            queue.enqueue([event()], now_ms=NOW)
            claimed = queue.claim(target_thread_id="chat-x", now_ms=NOW + 1)
            self.assertTrue(queue.mark_sent(
                claimed["jobId"], claimed["claimToken"], now_ms=NOW + 2
            )["ok"])
            with self.assertRaises(ValueError):
                queue.complete(
                    claimed["jobId"], claimed["claimToken"], analysis("wrong-job"), now_ms=NOW + 3
                )
            completed = queue.complete(
                claimed["jobId"], claimed["claimToken"], analysis(claimed["jobId"]), now_ms=NOW + 4
            )
            self.assertTrue(completed["ok"])
            self.assertEqual(completed["job"]["analysis"]["memePotential"], "strong")
            self.assertEqual(queue.status()["counts"]["completed"], 1)

    def test_fail_requeues_without_duplicating_the_post(self):
        with TemporaryDirectory() as folder:
            queue = self.make_queue(folder)
            queue.enqueue([event()], now_ms=NOW)
            claimed = queue.claim(target_thread_id="chat-x", now_ms=NOW + 1)
            failed = queue.fail(
                claimed["jobId"], claimed["claimToken"], "temporary", now_ms=NOW + 2
            )
            self.assertTrue(failed["ok"])
            self.assertEqual(queue.claim(target_thread_id="chat-x", now_ms=NOW + 30_000)["status"], "empty")
            retried = queue.claim(target_thread_id="chat-x", now_ms=NOW + 62_001)
            self.assertEqual(retried["jobId"], claimed["jobId"])

    def test_suppress_disallowed_handles_removes_official_and_minor_jobs(self):
        with TemporaryDirectory() as folder:
            queue = self.make_queue(folder)
            official = event("official") | {
                "source": "NEAR Protocol",
                "authorHandle": "NEARProtocol",
                "xCategory": "project_official",
            }
            minor = event("minor") | {
                "source": "Minor KOL",
                "authorHandle": "minor_kol",
                "xCategory": "notable",
            }
            queue.enqueue([event("leader"), official, minor], now_ms=NOW)

            changed = queue.suppress_disallowed_handles(
                frozenset({"vitalikbuterin"}), now_ms=NOW + 1,
            )

            self.assertEqual(changed, 2)
            self.assertEqual(queue.status()["counts"]["suppressed"], 2)
            claimed = queue.claim(target_thread_id="chat-x", now_ms=NOW + 2)
            self.assertEqual(claimed["status"], "claimed")
            self.assertIn("PeerDAS", claimed["prompt"])

    def test_normalizer_keeps_non_official_token_identity(self):
        value = analysis("job")
        value["tokens"][0]["official"] = False
        normalized = normalize_analysis(value, job_id="job")
        self.assertIsNotNone(normalized)
        self.assertFalse(normalized["tokens"][0]["official"])

    def test_normalizer_forces_generic_low_novelty_theme_to_not_watching(self):
        value = analysis("job")
        value.update({
            "worthWatching": True,
            "novelty": "low",
            "genericTheme": True,
            "noveltyReason": "只是常见 AGI 年份预测，没有新事实",
        })

        normalized = normalize_analysis(value, job_id="job")

        self.assertFalse(normalized["worthWatching"])
        self.assertEqual(normalized["urgency"], "low")
        self.assertTrue(normalized["genericTheme"])


class XTweetAnalysisPopupTests(unittest.TestCase):
    def test_completed_high_potential_analysis_uses_red_popup(self):
        job = {
            "job_id": "job-1",
            "payload": {
                "author": "Vitalik Buterin", "handle": "VitalikButerin",
                "url": "https://x.com/VitalikButerin/status/1",
                "originalText": "PeerDAS update",
            },
            "analysis": analysis("job-1"),
        }
        with (
            patch.object(server, "launch_desktop_alert", return_value={"ok": True}) as launch,
            patch.object(server.X_TWEET_ANALYSIS_QUEUE, "mark_alerted") as marked,
        ):
            result = server.x_tweet_analysis_popup(job)
        self.assertTrue(result["ok"])
        popup = launch.call_args.args[0]
        self.assertEqual(popup["alertTone"], "red")
        self.assertEqual(popup["sourceType"], "x-tweet-analysis")
        self.assertEqual(popup["contractAddress"], "0xabc")
        self.assertEqual(popup["title"], "Vitalik Buterin：PeerDAS")
        self.assertNotIn("以太坊扩容叙事", popup["title"])
        self.assertIn("红色机会｜以太坊扩容叙事获得重要人物背书", popup["body"])
        self.assertIn("Vitalik 强调 PeerDAS", popup["body"])
        marked.assert_called_once_with("job-1")

    def test_official_account_analysis_is_suppressed_before_popup(self):
        job = {
            "job_id": "job-official",
            "payload": {
                "author": "NEAR Protocol", "handle": "NEARProtocol",
                "category": "project_official",
                "url": "https://x.com/NEARProtocol/status/1",
                "originalText": "Privacy AI update",
            },
            "analysis": analysis("job-official"),
        }
        with (
            patch.object(server, "launch_desktop_alert") as launch,
            patch.object(server.X_TWEET_ANALYSIS_QUEUE, "mark_alerted") as marked,
        ):
            result = server.x_tweet_analysis_popup(job)

        self.assertTrue(result["suppressed"])
        launch.assert_not_called()
        marked.assert_called_once_with("job-official")

    def test_generic_agi_analysis_is_suppressed_by_ai_novelty_result(self):
        generic = analysis("job-agi") | {
            "worthWatching": True,
            "oneLine": "马斯克再次预测 AGI 可能在 2027 年到来",
            "keywords": ["AGI 2027"],
            "novelty": "low",
            "genericTheme": True,
            "noveltyReason": "只有常见时间预测，没有产品落地或新增市场事实",
        }
        job = {
            "job_id": "job-agi",
            "payload": {
                "author": "Elon Musk", "handle": "elonmusk",
                "url": "https://x.com/elonmusk/status/agi",
                "originalText": "AGI maybe 2027",
            },
            "analysis": generic,
        }
        with (
            patch.object(server, "launch_desktop_alert") as launch,
            patch.object(server.X_TWEET_ANALYSIS_QUEUE, "mark_alerted") as marked,
        ):
            result = server.x_tweet_analysis_popup(job)

        self.assertTrue(result["suppressed"])
        self.assertIn("没有产品落地", result["reason"])
        launch.assert_not_called()
        marked.assert_called_once_with("job-agi")


if __name__ == "__main__":
    unittest.main()
