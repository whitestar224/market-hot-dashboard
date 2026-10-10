"""X 追踪直连弹窗：被点名的 KOL 一发新帖就直接弹窗。

背景：X 追踪在设计上是「输入源」而不是弹窗通道（server.py sync_site_alert_feed
里对 x-kol 强制 ``fresh = []``），帖子先进固定群做 AI 分析，只有结构化结果回来
才由 ``x_tweet_analysis_popup`` 弹窗。用户点名要求「X追踪里的 CryptoCharming，
有最新 X 信息的时候也弹窗」，于是新增一条**按账号**的直连弹窗通道：

  * 常量 ``X_KOL_DIRECT_ALERT_DEFAULT_HANDLES`` 放用户明确要求过的账号；
  * 名册来源行上的 ``directAlert: true`` 让用户不改代码也能加账号；
  * 环境变量 ``X_KOL_DIRECT_ALERT_HANDLES`` 可临时加账号。

这些用例把该契约钉住：只有被点名的账号直连弹窗，其他人仍然只走分析桥。
"""

import os
import time
import unittest
from unittest.mock import patch

import server

CRYPTO_CHARMING = "CryptoCharming"
OTHER_KOL = "ikunxvwfz"
ROBINHOOD_CA = "0x316fa3ab9a8fd8d7567a823dedecf28d9fee2894"


def sample_event(original_text: str = "行情被我前几天一讲还真就冷炸了") -> dict:
    return {
        "key": "x-kol-status:x:cryptocharming:1791545000000",
        "kind": "X KOL动态",
        "sourceType": "x-kol",
        "source": "CryptoCharming",
        "sourceLabel": "X",
        "title": f"CryptoCharming：{original_text}",
        "body": f"{original_text} / 赞 12",
        "url": "https://x.com/CryptoCharming/status/1791545000000",
        "time": 1_791_545_000_000,
        "xCategory": "kol",
        "authorHandle": CRYPTO_CHARMING,
        "originalText": original_text,
        "quoteText": "",
    }


class DirectAlertHandleSetTests(unittest.TestCase):
    def _handles(self, sources=None, env: dict | None = None):
        with patch.object(server, "load_x_kol_sources", return_value=sources or []):
            with patch.dict(os.environ, env or {}, clear=False):
                if not env:
                    os.environ.pop("X_KOL_DIRECT_ALERT_HANDLES", None)
                return server.x_kol_direct_alert_handles()

    def test_user_requested_kol_is_on_by_default(self):
        # The constant alone must be enough: a roster read failure or an empty
        # roster must not silently drop the account the user asked for.
        self.assertIn("cryptocharming", self._handles())

    def test_other_tracked_kols_are_not_promoted(self):
        self.assertNotIn(OTHER_KOL, self._handles())

    def test_source_row_flag_opts_a_kol_in(self):
        handles = self._handles(sources=[
            {"id": f"x:{OTHER_KOL}", "handle": OTHER_KOL, "directAlert": True, "enabled": True},
        ])
        self.assertIn(OTHER_KOL, handles)

    def test_disabled_source_flag_is_ignored(self):
        handles = self._handles(sources=[
            {"id": f"x:{OTHER_KOL}", "handle": OTHER_KOL, "directAlert": True, "enabled": False},
        ])
        self.assertNotIn(OTHER_KOL, handles)

    def test_env_override_adds_a_handle(self):
        handles = self._handles(env={"X_KOL_DIRECT_ALERT_HANDLES": "@SomeNewKOL, another_one"})
        self.assertIn("somenewkol", handles)
        self.assertIn("another_one", handles)

    def test_broken_roster_store_never_mutes_the_requested_account(self):
        with patch.object(server, "load_x_kol_sources", side_effect=RuntimeError("db down")):
            os.environ.pop("X_KOL_DIRECT_ALERT_HANDLES", None)
            self.assertIn("cryptocharming", server.x_kol_direct_alert_handles())

    def test_normalize_x_source_keeps_the_flag(self):
        # merge_x_kol_manual_source_payloads re-normalises every stored row on
        # load, so an unknown key is dropped here and the flag would vanish
        # after the first save.
        source = server.normalize_x_source({"handle": OTHER_KOL, "directAlert": True})
        self.assertIs(source.get("directAlert"), True)
        plain = server.normalize_x_source({"handle": OTHER_KOL})
        self.assertNotIn("directAlert", plain)


class DirectAlertMatchingTests(unittest.TestCase):
    def test_only_the_opted_in_handle_matches(self):
        handles = frozenset({"cryptocharming"})
        self.assertTrue(server.x_kol_direct_alert_allowed(sample_event(), handles))
        other = {**sample_event(), "authorHandle": OTHER_KOL}
        self.assertFalse(server.x_kol_direct_alert_allowed(other, handles))

    def test_non_x_kol_sources_never_match(self):
        handles = frozenset({"cryptocharming"})
        for source_type in ("x-tweet-analysis", "trench-person-signal", "smart-money-buy", ""):
            self.assertFalse(server.x_kol_direct_alert_allowed(
                {**sample_event(), "sourceType": source_type}, handles,
            ))

    def test_handle_without_at_prefix_or_trailing_junk_matches(self):
        handles = frozenset({"cryptocharming"})
        self.assertTrue(server.x_kol_direct_alert_allowed(
            {**sample_event(), "authorHandle": "@CryptoCharming"}, handles,
        ))


class DirectAlertPopupPayloadTests(unittest.TestCase):
    def _payload(self, event: dict | None = None) -> dict:
        captured: dict = {}

        def fake_launch(payload, *args, **kwargs):
            captured.update(payload)
            return {"ok": True}

        with patch.object(server, "launch_desktop_alert", side_effect=fake_launch):
            server.send_x_kol_direct_alert(event or sample_event())
        return captured

    def test_title_shows_the_kol_and_the_post_text(self):
        payload = self._payload(sample_event("$MarsCoin 我买了一点"))
        self.assertIn("CryptoCharming", payload["title"])
        self.assertIn("$MarsCoin", payload["title"])
        self.assertEqual(payload["source"], "CryptoCharming")
        self.assertEqual(payload["authorHandle"], CRYPTO_CHARMING)

    def test_popup_keeps_the_post_body_link_and_original_text(self):
        event = sample_event()
        payload = self._payload(event)
        self.assertEqual(payload["url"], event["url"])
        self.assertEqual(payload["originalText"], event["originalText"])
        self.assertEqual(payload["body"], event["body"])
        self.assertEqual(payload["time"], event["time"])

    def test_key_is_prefixed_so_it_maps_to_the_personalx_board(self):
        payload = self._payload()
        self.assertTrue(payload["key"].startswith("x-kol:"))
        normalized = server.normalize_desktop_alert(payload)
        self.assertEqual(server.price_watch_alert_board(normalized), "personalx")

    def test_popup_carries_the_ai_narrative_identity_when_a_ca_is_present(self):
        payload = self._payload(sample_event(
            f"robinhood 上车 {ROBINHOOD_CA} 一起冲"
        ))
        self.assertEqual(payload["contractAddress"], ROBINHOOD_CA)
        self.assertEqual(payload["chain"], "4663")
        self.assertIn(payload["sourceId"], server.EXCHANGE_AI_NARRATIVE_ALLOWED_SOURCES)
        self.assertEqual(server.normalize_exchange_ai_binance_chain(payload["chain"]), "4663")

    def test_popup_without_a_ca_has_no_ai_narrative_identity(self):
        payload = self._payload(sample_event("今天行情有点冷"))
        self.assertEqual(payload["contractAddress"], "")
        self.assertEqual(payload["chain"], "")

    def test_traditional_post_is_popped_in_simplified_chinese(self):
        # 港台 KOL（CryptoCharming）的推文是繁体。转换挂在 X 快照入站漏斗上，
        # 这里再兜一次，防止事件来自转换上线之前写下的磁盘历史。
        traditional = "行情被我前幾天一講還真就冷炸了，這個車頭真的猛 $MarsCoin"
        payload = self._payload(sample_event(traditional))
        self.assertIn("这个车头真的猛", payload["title"])
        self.assertIn("$MarsCoin", payload["title"])
        self.assertEqual(payload["originalText"], "行情被我前几天一讲还真就冷炸了，这个车头真的猛 $MarsCoin")
        self.assertIn("这个车头真的猛", payload["speech"])

    def test_popup_is_not_muted_by_source_or_board_rules(self):
        normalized = server.normalize_desktop_alert(self._payload())
        self.assertEqual(normalized["kind"], "X 追踪动态")
        self.assertEqual(normalized["sourceType"], "x-kol")
        self.assertFalse(server.desktop_alert_source_is_muted(normalized))
        self.assertEqual(server.price_watch_alert_board_muted(normalized), "")
        self.assertEqual(
            server.desktop_alert_runtime_suppression_reason(normalized, pending=False), "",
        )


class FeedDispatchTests(unittest.TestCase):
    """The x-kol feed must hand opted-in posts to the popup launcher.

    The feed normally forces ``fresh = []`` for x-kol (input source, not a
    popup channel), so this is the one place where the direct-alert opt-in is
    actually wired in — and the one place a refactor can silently unhook it.
    """

    def _run_feed(self, event: dict, ready: bool = True):
        state = {
            "seen": {},
            "ready": ["x-kol"] if ready else [],
            "newboardReadyScopes": [],
            "xTweetAnalysisWatermarks": {},
        }
        launched: list[dict] = []
        feed = {
            "name": "x-kol",
            "interval": 2,
            "maxAgeMs": server.X_KOL_DESKTOP_ALERT_MAX_AGE_MS,
            "fetch": lambda: {"items": [event]},
            "parse": lambda payload: [event],
        }
        with patch.object(server, "load_site_alert_state", return_value=state), \
             patch.object(server, "save_site_alert_state", return_value=None), \
             patch.object(server, "SITE_ALERT_STATE", state), \
             patch.object(server, "launch_desktop_alert",
                          side_effect=lambda payload, *a, **k: launched.append(payload) or {"ok": True}):
            server.sync_site_alert_feed(feed)
        return launched

    def test_opted_in_post_pops_and_is_marked_seen(self):
        event = sample_event()
        event["time"] = int(time.time() * 1000)
        launched = self._run_feed(event)
        self.assertEqual(len(launched), 1, "opt-in X post did not reach the popup launcher")
        self.assertEqual(launched[0]["kind"], "X 追踪动态")
        self.assertEqual(launched[0]["authorHandle"], CRYPTO_CHARMING)

    def test_other_kols_stay_on_the_analysis_bridge_only(self):
        event = sample_event()
        event["time"] = int(time.time() * 1000)
        event["authorHandle"] = OTHER_KOL
        event["key"] = f"x-kol-status:x:{OTHER_KOL}:1"
        self.assertEqual(self._run_feed(event), [])

    def test_first_pass_only_baselines_and_never_replays_history(self):
        event = sample_event()
        event["time"] = int(time.time() * 1000)
        self.assertEqual(self._run_feed(event, ready=False), [])


if __name__ == "__main__":
    unittest.main()
