import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import server
from alert_delivery import AlertDeliveryStore, VisibleTimer


class FakeProcess:
    def __init__(self, dead=False):
        self.dead = dead
    def poll(self):
        return 1 if self.dead else None
    def terminate(self):
        self.dead = True


class DurableDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AlertDeliveryStore(Path(self.temp.name) / 'alerts.sqlite')
        self.now = time.time()

    def admit(self, key='test:1', priority=100, ttl=600):
        payload = {'key': key, 'title': '机会 '+key, 'source': '测试', 'time': int(self.now*1000), 'queuePriority': priority}
        return self.store.admit(payload, [key], priority, now=self.now, ttl=ttl)['deliveryId']

    def test_admission_survives_restart_and_dedupes(self):
        identity = self.admit()
        restored = AlertDeliveryStore(self.store.path)
        self.assertEqual(restored.pending(now=self.now)['id'], identity)
        self.assertTrue(restored.admit({}, ['test:1'], 1)['deduped'])
        self.assertEqual(restored.inbox()['unread'], 1)

    def test_discord_monitor_history_is_purged_without_touching_other_alerts(self):
        dc = self.store.admit(
            {'kind': 'DC机会', 'title': '新 CA', 'url': 'https://discord.com/channels/1/2/3'},
            ['discord:message:3'], 20, now=self.now,
        )['deliveryId']
        kept = self.admit('price:kept', priority=20)

        removed = self.store.purge_discord_monitor_history()

        self.assertEqual(removed['deliveries'], 1)
        self.assertIsNone(self.store.get(dc))
        self.assertIsNotNone(self.store.get(kept))

    def test_newest_message_wins_within_the_same_priority_tier(self):
        older = self.store.admit(
            {'key': 'old', 'title': '旧消息'}, ['old'], 10, now=self.now, ttl=600
        )['deliveryId']
        newer = self.store.admit(
            {'key': 'new', 'title': '新消息'}, ['new'], 10, now=self.now + 1, ttl=600
        )['deliveryId']

        self.assertNotEqual(older, newer)
        self.assertEqual(self.store.pending(now=self.now + 2)['id'], newer)

    def test_deduped_delivery_backfills_new_stable_aliases(self):
        first = self.store.admit(
            {'key': 'flash:215217|old title|1788928663'},
            ['flash:215217|old title|1788928663'],
            1,
            now=self.now,
        )
        stable = 'alert-newsflash-id:215217|1788928663'
        second = self.store.admit(
            {'key': 'flash:215217|old title|1788928663'},
            ['flash:215217|old title|1788928663', stable],
            1,
            now=self.now + 1,
        )
        edited = self.store.admit(
            {'key': 'flash:215217|edited title|1788928663'},
            ['flash:215217|edited title|1788928663', stable],
            1,
            now=self.now + 2,
        )

        self.assertTrue(second['deduped'])
        self.assertTrue(edited['deduped'])
        self.assertEqual(first['deliveryId'], edited['deliveryId'])
        self.assertEqual(self.store.inbox()['unread'], 1)

    def test_atomic_concurrent_admission(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: self.admit(), range(20)))
        self.assertEqual(len(set(ids)), 1)

    def test_failure_retries_without_permanent_seen(self):
        identity = self.admit()
        for attempt in range(3):
            now = self.now + attempt*10
            lease = self.store.lease(identity, now=now)
            self.assertIsNotNone(lease)
            self.store.fail(identity, lease['token'], 'injected', now=now)
        row = self.store.get(identity)
        self.assertEqual((row['state'], row['attempts']), ('failed', 3))
        self.assertEqual(self.store.inbox()['unread'], 1)
        self.assertIsNone(self.store.pending(now=self.now+100))

    def test_missing_receipt_recovers_after_lease_not_spawn_success(self):
        identity = self.admit()
        lease = self.store.lease(identity, now=self.now)
        self.assertEqual(self.store.get(identity)['shown'], 0)
        restarted = AlertDeliveryStore(self.store.path)
        lease_expired_at = self.now + restarted.DISPLAY_LEASE_SECONDS + 1
        restarted.sweep(now=lease_expired_at)
        self.assertEqual(restarted.pending(now=lease_expired_at + 2)['id'], identity)
        self.assertFalse(restarted.receipt(identity, lease['token'], 'visible'))

    def test_wrong_and_old_attempt_tokens_rejected(self):
        identity = self.admit()
        first = self.store.lease(identity, now=self.now)
        self.assertFalse(self.store.receipt(identity, 'wrong', 'read'))
        self.store.fail(identity, first['token'], 'test', now=self.now)
        second = self.store.lease(identity, now=self.now+5)
        self.assertFalse(self.store.receipt(identity, first['token'], 'visible'))
        self.assertTrue(self.store.receipt(identity, second['token'], 'visible', 1000))

    def test_display_is_not_read_and_recycle_preserves_unread(self):
        identity = self.admit()
        lease = self.store.lease(identity, now=self.now)
        self.store.receipt(identity, lease['token'], 'visible', 3000)
        self.assertFalse(self.store.archive_window(identity))
        self.store.receipt(identity, lease['token'], 'visible', 4000)
        self.assertTrue(self.store.archive_window(identity))
        self.assertEqual(self.store.inbox()['unread'], 1)
        self.store.mark_read(identity)
        self.assertEqual(self.store.inbox()['unread'], 0)

    def test_read_pending_cancels_future_popup(self):
        identity = self.admit()
        self.store.mark_read(identity)
        self.assertIsNone(self.store.pending(now=self.now))

    def test_suppress_matching_cancels_only_live_matching_rows(self):
        matching = self.store.admit(
            {'key': 'price-watch:dragon-wave:HIVE:15m:1', 'excludeSymbol': 'HIVE'},
            ['matching'], 100, now=self.now,
        )['deliveryId']
        kept = self.store.admit(
            {'key': 'price-watch:dragon-wave:KEEP:15m:1', 'excludeSymbol': 'KEEP'},
            ['kept'], 100, now=self.now + 1,
        )['deliveryId']
        historical = self.store.admit(
            {'key': 'price-watch:structure-first:HIVE:1h:old', 'excludeSymbol': 'HIVE'},
            ['historical'], 100, now=self.now + 2,
        )['deliveryId']
        with self.store.db() as db:
            db.execute(
                "UPDATE deliveries SET state='displayed',closed=1 WHERE id=?",
                (historical,),
            )

        suppressed = self.store.suppress_matching(
            lambda payload: payload.get('excludeSymbol') == 'HIVE',
            'removed',
        )

        self.assertEqual(suppressed, [matching])
        self.assertEqual(self.store.get(matching)['state'], 'suppressed')
        self.assertEqual(self.store.get(kept)['state'], 'pending')
        self.assertEqual(self.store.get(historical)['state'], 'displayed')

    def test_old_opportunity_not_revived_but_kept_in_records(self):
        identity = self.admit(ttl=30)
        self.store.sweep(now=self.now+31)
        self.assertEqual(self.store.get(identity)['state'], 'expired')
        self.assertIsNone(self.store.pending(now=self.now+40))
        self.assertEqual(self.store.inbox()['items'][0]['id'], identity)

    def test_low_priority_arrival_never_evicts_critical(self):
        for n in range(100):
            self.admit('critical:'+str(n), priority=1000)
        self.admit('noise', priority=0)
        self.assertEqual(self.store.inbox()['pending'], 101)
        self.assertEqual(self.store.pending(now=self.now)['payload']['key'], 'critical:99')

    def test_receipts_and_token_never_exposed_by_inbox(self):
        identity = self.admit()
        lease = self.store.lease(identity, now=self.now)
        data = json.dumps(self.store.inbox())
        self.assertNotIn(lease['token'], data)
        self.assertNotIn(str(self.store.path), data)

    def test_pagination_does_not_skip_unread(self):
        for n in range(56): self.admit(str(n))
        first = self.store.inbox()
        second = self.store.inbox(before=first['nextBefore'])
        self.assertEqual(len(first['items'])+len(second['items']), 56)

    def test_hidden_time_not_consumed(self):
        timer=VisibleTimer()
        for n in range(5): timer.tick(True, n)
        self.assertEqual(timer.milliseconds, 4000)
        for n in range(5, 60): timer.tick(False, n)
        self.assertEqual(timer.milliseconds, 4000)


class DeliveryIntegrationTests(DurableDeliveryTests):
    def test_closed_supervisor_log_pipe_cannot_abort_http(self):
        handler=object.__new__(server.Handler)
        with patch('http.server.BaseHTTPRequestHandler.log_message',side_effect=BrokenPipeError('closed pipe')):
            handler.log_message('request complete')

    def setUp(self):
        super().setUp()
        for name, value in (
            ('ALERT_DELIVERY_STORE', self.store), ('DESKTOP_ALERT_DELIVERIES', {}),
            ('DESKTOP_ALERT_GENERAL_PROCESSES', {}), ('DESKTOP_ALERT_STRUCTURE_PROCESSES', {}),
            ('DESKTOP_ALERT_ACTIVE_PROCESS', None), ('DESKTOP_ALERT_LAST_LAUNCHED_AT', 0),
            ('DESKTOP_ALERT_LAST_LAUNCHED_PRIORITY', 0), ('DESKTOP_ALERT_SEEN', {}), ('DESKTOP_ALERT_SEEN_LOADED', True),
        ):
            p=patch.object(server,name,value);p.start();self.addCleanup(p.stop)
        for name in ('record_event_flow_popup','record_desktop_alert_news_trade_intake','ensure_desktop_alert_worker'):
            p=patch.object(server,name);p.start();self.addCleanup(p.stop)
        p=patch.object(server,'price_structure_symbol_excluded',return_value=False);p.start();self.addCleanup(p.stop)
        p=patch.object(server,'env_flag',return_value=False);p.start();self.addCleanup(p.stop)

    def test_launch_failure_and_next_scan_keep_same_durable_message(self):
        payload={'key':'failure','title':'test'}
        with patch.object(server,'spawn_desktop_alert_process',side_effect=OSError('injected')):
            first=server.launch_desktop_alert(payload)
        retry=server.launch_desktop_alert(payload)
        self.assertEqual(first['deliveryId'],retry['deliveryId'])
        self.assertEqual(self.store.get(first['deliveryId'])['state'],'pending')
        self.assertEqual(server.DESKTOP_ALERT_SEEN,{})

    def test_already_dead_child_is_not_displayed(self):
        identity=self.admit()
        with patch.object(server,'spawn_desktop_alert_process',return_value=FakeProcess(dead=True)):
            server.desktop_alert_delivery_tick()
        self.assertEqual(self.store.get(identity)['state'],'pending')
        self.assertEqual(self.store.get(identity)['shown'],0)

    def test_preexisting_holding_change_is_suppressed_before_spawn(self):
        payload = {
            'key': 'flash:old-holding-change',
            'kind': '聚合快讯',
            'source': 'BlockBeats 律动',
            'title': '某巨鲸减持500枚BTC，总持仓降至1,000枚',
        }
        identity = self.store.admit(
            payload,
            [payload['key']],
            50,
            now=self.now,
            ttl=600,
        )['deliveryId']

        with patch.object(server, 'spawn_desktop_alert_process') as spawn:
            server.desktop_alert_delivery_tick()

        self.assertEqual(self.store.get(identity)['state'], 'suppressed')
        spawn.assert_not_called()

    def test_preexisting_personal_profit_report_is_suppressed_before_spawn(self):
        payload = {
            'key': 'flash:old-personal-profit',
            'kind': '聚合快讯',
            'source': 'BlockBeats 律动',
            'title': '某交易者花费1.9万美元买入JINQIAN，获利约10倍',
            'speech': '某交易者获利约十倍',
        }
        identity = self.store.admit(
            payload,
            [payload['key']],
            50,
            now=self.now,
            ttl=600,
        )['deliveryId']

        with patch.object(server, 'spawn_desktop_alert_process') as spawn:
            server.desktop_alert_delivery_tick()

        stored = self.store.get(identity)
        self.assertEqual(stored['state'], 'suppressed')
        self.assertEqual(stored['reason'], 'personal profit/loss update filtered')
        spawn.assert_not_called()

    def test_preexisting_stock_rank_and_company_alerts_are_suppressed_before_spawn(self):
        payloads = [
            {
                'key': 'rank-monitor:hot:futu-hk:HK:00992:new:10',
                'sourceId': 'futu-hk',
                'source': '富途港股热门榜',
                'sourceLabel': 'HK',
                'kind': '港股热门榜新进',
                'title': '港股热门榜新进：联想集团',
            },
            {
                'key': 'gainers-leader:futu-us-gainers:NVDA',
                'source': '富途美股涨幅榜',
                'sourceLabel': 'US',
                'kind': '涨幅榜异动',
                'title': 'US 涨幅榜榜首变为英伟达',
            },
            {
                'key': 'company-announcement:cn:example',
                'source': '交易所公告',
                'sourceLabel': 'CN',
                'kind': '公司公告',
                'title': '测试公司发布业绩公告',
            },
        ]
        identities = [
            self.store.admit(payload, [payload['key']], 50, now=self.now, ttl=600)['deliveryId']
            for payload in payloads
        ]

        with patch.object(server, 'spawn_desktop_alert_process') as spawn:
            server.desktop_alert_delivery_tick()

        self.assertEqual([self.store.get(identity)['state'] for identity in identities], ['suppressed'] * 3)
        spawn.assert_not_called()

    def test_ten_critical_signals_all_get_display_opportunity(self):
        for n in range(10): self.admit('critical:'+str(n),1000,180)
        shown=set()
        with patch.object(server,'spawn_desktop_alert_process',side_effect=lambda *args:FakeProcess()):
            for step in range(100):
                now=self.now+step
                with patch.object(server.time,'time',return_value=now):
                    server.desktop_alert_delivery_tick()
                    for identity,active in list(server.DESKTOP_ALERT_DELIVERIES.items()):
                        self.store.receipt(identity,active['token'],'visible',(step+1)*1000,now=now)
                        shown.add(identity)
        self.assertEqual(len(shown),10)
        self.assertEqual(self.store.inbox()['unread'],10)
        self.assertTrue(all(item['state']=='displayed' for item in self.store.inbox()['items']))

    def test_fresh_popups_launch_concurrently_before_visibility_receipts(self):
        self.admit('one');self.admit('two')
        with patch.object(server,'spawn_desktop_alert_process',side_effect=lambda *args:FakeProcess()) as spawn:
            server.desktop_alert_delivery_tick()
            with patch.object(server.time,'time',return_value=self.now+5):server.desktop_alert_delivery_tick()
            self.assertEqual(spawn.call_count,2)

    def test_unknown_window_is_not_killed_to_make_room(self):
        processes={n:(FakeProcess(),self.now) for n in range(8)}
        with patch.object(server,'DESKTOP_ALERT_GENERAL_PROCESSES',processes):
            self.assertIsNone(server.next_desktop_alert_slot())
            self.assertTrue(all(not p.dead for p,t in processes.values()))

    def test_generic_x_titles_do_not_merge_distinct_posts(self):
        first={'key':'x:first','title':'转推了某人的动态','sourceId':'author','kind':'X KOL动态','url':'https://x.com/a/status/1234567890123'}
        second={**first,'key':'x:second','url':'https://x.com/a/status/1234567890124'}
        self.assertFalse(set(server.alert_dedupe_keys(first)) & set(server.alert_dedupe_keys(second)))

    def test_upstream_does_not_commit_failed_admission(self):
        state={'ready':['test'],'seen':{}}
        feed={'name':'test','maxAgeMs':60000,'fetch':lambda:{},'parse':lambda _:[{'key':'upstream','title':'new','time':int(time.time()*1000)}]}
        with patch.object(server,'SITE_ALERT_STATE',state),patch.object(server,'load_site_alert_state',return_value=state),patch.object(server,'save_site_alert_state'),patch.object(server,'launch_desktop_alert',return_value={'ok':False}):
            server.sync_site_alert_feed(feed)
        self.assertNotIn('upstream',state['seen'])


if __name__ == '__main__':unittest.main()
