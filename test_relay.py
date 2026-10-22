import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request, error
from webhook_relay import Relay, WEBHOOK_PATH


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calls = self.calls
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                calls.append((self.path, self.rfile.read(int(self.headers['Content-Length'])), self.headers.get('X-Line-Signature')))
                body = b'private-upstream-details'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        class TestRelay(Relay):
            upstream = f'http://127.0.0.1:{self.upstream.server_address[1]}' + WEBHOOK_PATH
        self.relay = ThreadingHTTPServer(('127.0.0.1', 0), TestRelay)
        self.threads = []
        for server in (self.upstream, self.relay):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.url = f'http://127.0.0.1:{self.relay.server_address[1]}'

    def tearDown(self):
        for server in (self.relay, self.upstream):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=3)

    def test_preserves_raw_body_and_signature_without_leaking_response(self):
        raw = '{ "events":[], "text":"測試" }'.encode()
        req = request.Request(self.url + WEBHOOK_PATH, raw, {'X-Line-Signature': 'test-signature'})
        with request.urlopen(req, timeout=3) as response:
            self.assertEqual(json.load(response), {'ok': True})
        self.assertEqual(self.calls, [(WEBHOOK_PATH, raw, 'test-signature')])

    def test_editor_and_internal_paths_are_not_forwarded(self):
        for path in ('/rest/settings', '/setup', '/prepare', '/v1/chat/completions', '/webhook/elf-internal-digest'):
            with self.assertRaises(error.HTTPError) as cm:
                request.urlopen(self.url + path, timeout=3)
            self.assertEqual(cm.exception.code, 404)
        self.assertFalse(self.calls)

    def test_unsigned_webhook_is_rejected(self):
        with self.assertRaises(error.HTTPError) as cm:
            request.urlopen(request.Request(self.url + WEBHOOK_PATH, b'{"events":[]}'), timeout=3)
        self.assertEqual(cm.exception.code, 401)
        self.assertFalse(self.calls)


if __name__ == '__main__':
    unittest.main()
