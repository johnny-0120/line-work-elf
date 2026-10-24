import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import calendar_client as c


class CalendarAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'token.json'
        self.target=patch('calendar_client.token_file',return_value=self.path)
        self.target.start()

    def tearDown(self):
        self.target.stop()
        self.temp.cleanup()

    def test_invalid_grant_is_actionable_without_echoing_provider_secrets(self):
        error=HTTPError('https://oauth2.googleapis.com/token',400,'error',{},
                        io.BytesIO(b'{"error":"invalid_grant","error_description":"secret text"}'))
        with patch('calendar_client.request.urlopen',side_effect=error),self.assertRaises(c.CalendarError) as result:
            c.exchange({'grant_type':'refresh_token'})
        self.assertEqual(result.exception.code,'invalid_grant')
        self.assertIn('8791',str(result.exception))
        self.assertNotIn('secret text',str(result.exception))

    def test_saved_file_is_not_connected_when_refresh_permission_expired(self):
        c.save_token({'refresh_token':'fake','expires_at':time.time()-1,
                      'expires_in':3600,'refresh_token_expires_in':100})
        status=c.GoogleCalendar().status()
        self.assertFalse(status['connected'])
        self.assertTrue(status['reauthorization_required'])

    def test_refresh_retains_known_permission_expiry_and_scopes(self):
        expiry=time.time()+5000
        c.save_token({'refresh_token':'fake','expires_at':0,'refresh_expires_at':expiry,'scope':'original'})
        with patch('calendar_client.read_client',return_value={'client_id':'fake'}),patch(
                'calendar_client.exchange',return_value={'access_token':'new','expires_in':3600}):
            self.assertEqual(c.GoogleCalendar().access_token(),'new')
        saved=json.loads(self.path.read_text())
        self.assertEqual(saved['refresh_expires_at'],expiry)
        self.assertEqual(saved['scope'],'original')
        self.assertEqual(saved['refresh_token'],'fake')

    def test_revoked_token_is_marked_and_not_retried_until_reconnected(self):
        c.save_token({'refresh_token':'fake','expires_at':0})
        client=c.GoogleCalendar()
        with patch('calendar_client.read_client',return_value={'client_id':'fake'}),patch(
                'calendar_client.exchange',side_effect=c.CalendarError('expired',400,'invalid_grant')) as exchange:
            for _ in range(2):
                with self.assertRaises(c.CalendarError):client.access_token()
            self.assertEqual(exchange.call_count,1)
        self.assertTrue(client.status()['reauthorization_required'])

    def test_new_authorization_replaces_invalid_marker_and_records_lifetime(self):
        c.save_token({'refresh_token':'old','authorization_error':'invalid_grant'})
        now=time.time()
        c.save_token({'refresh_token':'new','access_token':'current','expires_at':now+3600,
                      'expires_in':3600,'refresh_token_expires_in':604800})
        saved=json.loads(self.path.read_text())
        self.assertNotIn('authorization_error',saved)
        self.assertAlmostEqual(saved['refresh_expires_at'],now+604800,delta=1)
        self.assertTrue(c.GoogleCalendar().status()['connected'])

    def test_no_saved_token_is_disconnected(self):
        self.assertFalse(c.GoogleCalendar().status()['connected'])


if __name__=='__main__':unittest.main()
