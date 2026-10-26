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

    def calendar_proxy(self):
        from urllib.parse import urlsplit
        path=urlsplit(self.path).path
        if (self.command,path) not in {('GET','/calendar/connect'),('GET','/calendar/callback'),('POST','/calendar/start')}:return False
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<=size<4096:return self.respond(413) or True
            headers={k:self.headers[k] for k in ('Cookie','Origin','Content-Type') if self.headers.get(k)}
            req=request.Request('http://127.0.0.1:8791'+self.path,self.rfile.read(size) if self.command=='POST' else None,headers,method=self.command)
            class NoRedirect(request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):return None
            try:response=request.build_opener(NoRedirect).open(req,timeout=30)
            except error.HTTPError as exc:response=exc
            with response:
                raw=response.read(131072);self.send_response(response.code)
                for key in ('Content-Type','Location','Set-Cookie','Content-Security-Policy'):
                    if response.headers.get(key):self.send_header(key,response.headers[key])
                self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except (ValueError,error.URLError,TimeoutError):self.respond(502)
        return True

    def do_GET(self):
        if self.calendar_proxy():return
        self.respond(200 if self.path == '/health' else 404)

    def do_POST(self):
        if self.calendar_proxy():return
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
