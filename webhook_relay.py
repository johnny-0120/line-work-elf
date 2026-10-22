"""Expose only the LINE webhook, keeping the n8n editor off the public tunnel."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request, error

WEBHOOK_PATH = '/webhook/line-work-elf'


class Relay(BaseHTTPRequestHandler):
    upstream = os.getenv('RELAY_UPSTREAM', 'http://127.0.0.1:5678' + WEBHOOK_PATH)

    def log_message(self, *args):
        pass

    def respond(self, status):
        raw = json.dumps({'ok': status == 200}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.respond(200 if self.path == '/health' else 404)

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            return self.respond(404)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1048576:
                return self.respond(413)
            raw = self.rfile.read(size)
            signature = self.headers.get('X-Line-Signature', '')
            if not signature:
                return self.respond(401)
            req = request.Request(self.upstream, raw, {
                'Content-Type': 'application/json', 'X-Line-Signature': signature})
            with request.urlopen(req, timeout=20) as response:
                status = response.status
            self.respond(status)
        except error.HTTPError as exc:
            self.respond(exc.code)
        except (error.URLError, TimeoutError):
            self.respond(502)
        except (ValueError, TypeError):
            self.respond(400)


if __name__ == '__main__':
    bind = os.getenv('RELAY_BIND', '127.0.0.1')
    print('LINE webhook relay listening on ' + bind + ':8790', flush=True)
    ThreadingHTTPServer((bind, 8790), Relay).serve_forever()
