"""Popup delivery regression entry point. Legacy reproductions below are historical only."""
from collections import deque
from contextlib import ExitStack, contextmanager, redirect_stderr
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


@contextmanager
def isolated():
    journal = Mock()
    values = {
        "DESKTOP_ALERT_QUEUE": deque(), "DESKTOP_ALERT_QUEUE_ACTIVE": True,
        "DESKTOP_ALERT_SEEN": {}, "DESKTOP_ALERT_SEEN_LOADED": True,
        "DESKTOP_ALERT_LAST_LAUNCHED_AT": 0, "DESKTOP_ALERT_LAST_LAUNCHED_PRIORITY": 0,
        "DESKTOP_ALERT_ACTIVE_PROCESS": None, "DESKTOP_ALERT_ACTIVE_PROCESS_SLOT": 0,
        "DESKTOP_ALERT_GENERAL_PROCESSES": {}, "DESKTOP_ALERT_STRUCTURE_PROCESSES": {},
        "price_structure_symbol_excluded": Mock(return_value=False),
        "write_json_cache": Mock(), "record_desktop_alert_news_trade_intake": Mock(),
        "record_event_flow_popup": journal, "claim_desktop_alert_marker": Mock(return_value=True),
        "cleanup_desktop_alert_markers": Mock(), "desktop_alert_interval_seconds": Mock(return_value=0),
        "spawn_desktop_alert_process": Mock(return_value=SimpleNamespace(poll=lambda: None)),
    }
    with ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(patch.object(server, name, value))
        stack.enter_context(patch.dict(server.os.environ, {"XINGYUN_DISABLE_DESKTOP_ALERT": "0"}))
        yield journal


def alert(key="price-watch:AUDIT:episode:1", priority=100):
    return {"key": key, "title": "AUDIT 新触发", "kind": "价格监控", "queuePriority": priority}


def main():
    report = {}
    posts = [{"key": f"audit-x:{number}", "kind": "X KOL动态", "source": "测试作者", "sourceId": "audit",
              "title": "测试作者：转发项目动态", "body": body,
              "url": f"https://x.com/audit/status/{number}"}
             for number, body in ((10001, "项目甲发布全新产品"), (10002, "项目乙公布不同合作"))]
    shared = set(server.alert_dedupe_keys(server.normalize_desktop_alert(posts[0]))) & set(server.alert_dedupe_keys(server.normalize_desktop_alert(posts[1])))
    report["different_posts_and_content_collide_on_generic_title"] = any(key.startswith("alert-global-title:") for key in shared)
    with isolated() as journal:
        server.launch_desktop_alert(alert())
        server.spawn_desktop_alert_process.side_effect = OSError("synthetic spawn failure")
        with redirect_stderr(io.StringIO()):
            server.desktop_alert_queue_worker()
        result = server.launch_desktop_alert(alert())
        report["spawn_failure_is_then_permanently_deduped"] = bool(result.get("deduped") and any(call.args[1] == "failed" for call in journal.call_args_list))

    with isolated():
        server.launch_desktop_alert(alert())
        server.DESKTOP_ALERT_QUEUE.clear()  # Model process loss, retaining the durable seen set.
        report["restart_loses_queued_event_but_keeps_seen"] = bool(server.launch_desktop_alert(alert()).get("deduped"))

    with isolated() as journal, patch.object(server, "DESKTOP_ALERT_QUEUE_LIMIT", 3):
        for index in range(3):
            server.enqueue_desktop_alert(server.normalize_desktop_alert(alert(f"price-watch:AUDIT{index}:episode:1", 1000)))
        server.launch_desktop_alert({"key": "audit-low-priority", "title": "一般资讯", "kind": "市场快讯"})
        report["low_priority_arrival_evicts_a_critical_signal_when_full"] = any(call.args[1] == "skipped" and call.args[0]["queuePriority"] == 1000 for call in journal.call_args_list)

    with isolated() as journal:
        server.spawn_desktop_alert_process.return_value = SimpleNamespace(poll=lambda: 2)
        server.enqueue_desktop_alert(server.normalize_desktop_alert(alert()))
        server.desktop_alert_queue_worker()
        report["already_failed_child_recorded_as_dispatched"] = any(call.args[1] == "dispatched" for call in journal.call_args_list)

    with isolated():
        processes = [SimpleNamespace(poll=lambda: None, terminate=Mock()) for _ in range(8)]
        server.DESKTOP_ALERT_GENERAL_PROCESSES.update({index: (process, 100 + index) for index, process in enumerate(processes)})
        server.next_desktop_alert_slot()
        report["ninth_popup_terminates_oldest_without_read_ack"] = processes[0].terminate.called

    with isolated() as journal:
        clock = [1000.0]
        def wait(seconds):
            clock[0] += seconds
            return False
        with patch.object(server.time, "time", side_effect=lambda: clock[0]), \
             patch.object(server, "desktop_alert_interval_seconds", return_value=6.5), \
             patch.object(server, "DESKTOP_ALERT_QUEUE_WAKE", SimpleNamespace(wait=wait, clear=lambda: None)):
            for index in range(10):
                server.enqueue_desktop_alert(server.normalize_desktop_alert(alert(f"price-watch:BURST{index}:episode:1", 1000)))
            server.desktop_alert_queue_worker()
        report["ten_simultaneous_critical_signals"] = {
            "spawned": server.spawn_desktop_alert_process.call_count,
            "expired": sum(call.args[1] == "skipped" for call in journal.call_args_list),
        }

    state = {"ready": ["audit-feed"], "seen": {}}
    feed = {"name": "audit-feed", "fetch": lambda: {}, "parse": lambda _: [{**alert(), "time": int(time.time()*1000)}], "maxAgeMs": 60000}
    with patch.object(server, "load_site_alert_state", return_value=state), \
         patch.object(server, "save_site_alert_state"), \
         patch.object(server, "launch_desktop_alert", side_effect=OSError("synthetic unavailable")) as launch, \
         redirect_stderr(io.StringIO()):
        server.sync_site_alert_feed(feed)
        server.sync_site_alert_feed(feed)
        report["upstream_marks_seen_before_failed_delivery_and_never_retries"] = launch.call_count == 1
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # The legacy worker is now persistent. Never invoke old in-memory diagnostics
    # against the new scheduler or production outbox; use isolated temp-DB tests.
    import unittest
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        'tests.test_alert_delivery', 'tests.test_desktop_alert_structure_stack',
    ])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
