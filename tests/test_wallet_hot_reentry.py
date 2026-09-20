import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class WalletHotReentryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for target, kwargs in [('BINANCE_WALLET_HOT_ALERT_STATE_PATH', {'new':Path(self.directory.name)/'wallet.json'}),
                               ('persist_alert_events', {}), ('time.time', {'return_value':1800000000})]:
            patcher = patch('server.'+target, **kwargs)
            mocked = patcher.start(); self.addCleanup(patcher.stop)
            if target == 'time.time': self.clock = mocked

    def poll(self, *contracts, symbol='TOKEN'):
        return server.sync_binance_wallet_hot_alert_feed({'id':'binance-wallet-hot','status':'ok','period':'4h',
            'rows':[{'symbol':symbol,'chain':'56','contractAddress':ca,'rank':i+1} for i,ca in enumerate(contracts)]})

    def test_baseline_drop_and_return_days_later_is_not_new(self):
        self.assertEqual(self.poll('AAA'),[])
        self.poll('BBB')
        self.clock.return_value += 3 * 86400
        self.assertEqual(self.poll('AAA'),[])

    def test_continuing_appearance_refreshes_seven_day_window(self):
        self.poll('AAA')
        self.clock.return_value += 6 * 86400
        self.poll('AAA'); self.poll('BBB')
        self.clock.return_value += 2 * 86400
        self.assertEqual(self.poll('AAA'),[])

    def test_return_after_seven_full_days_absent_is_new(self):
        self.poll('AAA'); self.poll('BBB')
        self.clock.return_value += 7 * 86400 + 1
        self.assertEqual(len(self.poll('AAA')),1)

    def test_identity_uses_chain_and_contract_not_display_name(self):
        self.poll('AAA',symbol='OLD')
        self.assertEqual(self.poll('AAA',symbol='RENAMED'),[])
        self.assertEqual(len(self.poll('BBB',symbol='RENAMED')),1)

    def test_migration_retains_recent_alerts_after_restart(self):
        source={'id':'binance-wallet-hot'}
        key=server.rank_monitor_snapshot(source,{'symbol':'TOKEN','chain':'56','contractAddress':'AAA'},0,'hot')['key']
        server.write_json_cache(server.BINANCE_WALLET_HOT_ALERT_STATE_PATH,
            {'version':2,'ready':True,'period':'4h','membership':[],
             'lastAlerts':{'4h:new:'+key:self.clock.return_value-86400}})
        self.assertEqual(self.poll('AAA'),[])
        state=server.read_json_cache(server.BINANCE_WALLET_HOT_ALERT_STATE_PATH)
        self.assertEqual(state['lastSeen'][key],self.clock.return_value)


if __name__ == '__main__': unittest.main()
