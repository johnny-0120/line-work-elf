import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import line_api


class TokenTests(unittest.TestCase):
    def test_reuses_valid_token_without_network(self):
        with patch.dict(os.environ, {'LINE_CHANNEL_ACCESS_TOKEN': 'test-token', 'LINE_TOKEN_EXPIRES_AT': str(time.time()+3600)}):
            with patch('line_api.request.urlopen') as network:
                self.assertEqual(line_api.ensure_token(), 'test-token')
                network.assert_not_called()

    def test_renewal_preserves_other_local_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, '.env')
            path.write_text('OPENROUTER_API_KEY=unrelated-test-key\nLINE_CHANNEL_ACCESS_TOKEN=old-test\n', encoding='utf-8')
            response = io.BytesIO(json.dumps({'access_token': 'fresh-test-token', 'expires_in': 2592000}).encode())
            with patch('line_api.ROOT', Path(tmp)), patch.dict(os.environ, {
                'LINE_CHANNEL_ID': 'test-id', 'LINE_CHANNEL_SECRET': 'test-secret',
                'LINE_CHANNEL_ACCESS_TOKEN': 'old-test', 'LINE_TOKEN_EXPIRES_AT': '1'}):
                with patch('line_api.request.urlopen', return_value=response) as network:
                    self.assertEqual(line_api.ensure_token(), 'fresh-test-token')
                    self.assertEqual(network.call_args.args[0].full_url, 'https://api.line.me/v2/oauth/accessToken')
                saved = path.read_text()
                self.assertIn('OPENROUTER_API_KEY=unrelated-test-key', saved)
                self.assertIn('LINE_CHANNEL_ACCESS_TOKEN=fresh-test-token', saved)
                self.assertGreater(float(os.environ['LINE_TOKEN_EXPIRES_AT']), time.time())
                self.assertFalse(Path(tmp, '.env.pending').exists())

    def test_container_renewal_can_cache_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('line_api.ROOT', Path(tmp)), patch.dict(os.environ, {}, clear=False):
                line_api.save_settings({'LINE_CHANNEL_ACCESS_TOKEN': 'memory-test-token', 'LINE_TOKEN_EXPIRES_AT': '123'})
                self.assertEqual(os.environ['LINE_CHANNEL_ACCESS_TOKEN'], 'memory-test-token')
                self.assertFalse(Path(tmp, '.env').exists())


if __name__ == '__main__':
    unittest.main()
