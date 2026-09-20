import os
import unittest
from unittest.mock import patch
import server


class CostApprovalTests(unittest.TestCase):
    def test_stored_tokens_and_old_flags_cannot_reenable_x_api(self):
        self.assertFalse(server.X_OFFICIAL_API_USER_APPROVED)
        self.assertFalse(server.X_OFFICIAL_API_REST_POLL_USER_APPROVED)
        self.assertFalse(server.X_OFFICIAL_API_HISTORY_USER_APPROVED)
        self.assertFalse(server.X_OFFICIAL_API_ALL_ACCOUNTS_USER_APPROVED)
        with patch.dict(os.environ, {'X_BEARER_TOKEN':'configured', 'X_KOL_OFFICIAL_API_ENABLED':'1',
                                     'X_KOL_OFFICIAL_REST_POLL_ENABLED':'1','X_KOL_FILTERED_STREAM_ENABLED':'1'}), \
             patch.object(server.requests,'get',side_effect=AssertionError('must not access X')) as get, \
             patch.object(server.requests,'post',side_effect=AssertionError('must not access X')) as post:
            self.assertEqual(server.x_kol_token(),'')
            self.assertFalse(server.x_kol_official_api_enabled())
            self.assertFalse(server.x_kol_official_rest_poll_enabled())
            self.assertFalse(server.x_kol_official_stream_available())
            with self.assertRaisesRegex(RuntimeError,'停用'):
                server.x_kol_fetch_api_source({'handle':'test'},'configured')
            with self.assertRaisesRegex(RuntimeError,'停用'):
                server.x_kol_reconcile_official_stream_rules('configured',{'test'})
            get.assert_not_called();post.assert_not_called()


if __name__ == '__main__': unittest.main()
