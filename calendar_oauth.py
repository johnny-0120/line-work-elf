"""Loopback-only Google Desktop OAuth with state + PKCE. Open in system Chrome."""
import base64
import hashlib
import html
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse

from calendar_client import read_client, client_file, token_file, save_token, exchange, SCOPES, CalendarError, GoogleCalendar

sessions = {}
connect_csrf = secrets.token_urlsafe(32)
session_lock = threading.Lock()
REDIRECT = 'http://127.0.0.1:8791/callback'


class OAuthHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def page(self, content, status=200):
        body = ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>公事小精靈｜Google Calendar</title><style>body{font:17px/1.8 system-ui;max-width:760px;margin:60px auto;padding:24px;background:#edf4fc;color:#17243a}main{padding:32px;background:white;border-radius:20px}a{color:#516d98}button{font:inherit;padding:10px 24px;background:#516d98;color:white;border:0;border-radius:10px}code{background:#eef2f5;padding:3px 7px}</style><main>' + content + '</main></html>').encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://accounts.google.com; base-uri 'none'; frame-ancestors 'none'")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from member_oauth import handle
        if handle(self,'GET'):return
        url = parse.urlsplit(self.path)
        if url.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if url.path == '/':
            connection=GoogleCalendar().status()
            connected = connection['connected']
            try:
                read_client()
                configured = True
            except CalendarError:
                configured = False
            content = '<h1>公事小精靈 × Google Calendar</h1><p>連線狀態：' + ('已儲存授權' if connected else '尚未授權') + '</p>'
            if connection.get('reauthorization_required'):
                content += '<p><strong>原有授權已失效，請重新連接。</strong></p>'
            if connected and connection.get('refresh_expires_at'):
                days=max(0,int((connection['refresh_expires_at']-time.time()+86399)//86400))
                content += '<p>Google 回傳的自動續期授權約剩 '+str(days)+' 天。測試模式授權有七天限制；期限到後需重新連線。</p>'
            if configured:
                content += '<p>授權後，小精靈可以讀取你擁有的行事曆事件、檢查空閒時間，並依你在 LINE 的指令新增行程。程式只提供查詢與新增，不提供刪除行程或邀請他人。</p><form method="post" action="/connect"><input type="hidden" name="csrf" value="' + connect_csrf + '"><button>連接 Google Calendar</button></form>'
            else:
                content += '<p>先在你自己的 Google Cloud 專案啟用 Calendar API，建立 <strong>Desktop app（桌面應用程式）OAuth 用戶端</strong>，下載 JSON 後放到專案的 <code>.secrets/google-client.json</code>。</p><ol><li><a href="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com" target="_blank" rel="noreferrer">啟用 Google Calendar API</a></li><li><a href="https://console.cloud.google.com/auth/overview" target="_blank" rel="noreferrer">設定 Google Auth Platform 與測試使用者</a></li><li><a href="https://console.cloud.google.com/auth/clients" target="_blank" rel="noreferrer">建立 Desktop app OAuth 用戶端並下載 JSON</a></li><li>把 JSON 放入指定位置後，重新整理這一頁。</li></ol>'
            content = content.replace('.secrets/google-client.json', html.escape(str(client_file())))
            content += '<p>請使用系統 Chrome 或 Edge 完成 Google 登入。此連線頁只在你的電腦開啟，不會透過 LINE 的公開 Webhook 暴露。Google 密碼與驗證碼直接輸入 Google 頁面。</p>'
            return self.page(content)
        if url.path == '/callback':
            args = parse.parse_qs(url.query)
            state = args.get('state', [''])[0]
            with session_lock:
                session = sessions.pop(state, None)
            if not session or session['expires'] < time.time():
                return self.page('<h1>連線已過期</h1><p>請回到 <a href="/">連線頁</a>重新開始。</p>', 400)
            if 'error' in args:
                return self.page('<h1>尚未完成授權</h1><p>你的選擇已保留，沒有新增任何行程。可回到 <a href="/">連線頁</a>重試。</p>', 400)
            try:
                client = read_client()
                token = exchange({'grant_type': 'authorization_code', 'client_id': client['client_id'],
                    'client_secret': client.get('client_secret', ''), 'code': args['code'][0],
                    'redirect_uri': REDIRECT, 'code_verifier': session['verifier']})
                if not token.get('refresh_token') or not set(SCOPES).issubset(set(token.get('scope', '').split())):
                    raise CalendarError('請允許所列行事曆權限；目前沒有儲存不完整的授權。')
                token['expires_at'] = time.time() + int(token.get('expires_in', 3600))
                save_token(token)
                return self.page('<h1>Google Calendar 已連接</h1><p>回到 LINE，傳 <code>/行程</code> 查詢今天，或傳 <code>/新增行程 明天下午三點到四點 活動會議</code>。只會對你設定的行事曆執行。</p>')
            except (CalendarError, KeyError) as exc:
                return self.page('<h1>未完成連線</h1><p>' + html.escape(str(exc) if isinstance(exc, CalendarError) else '授權回傳不完整。') + '</p><a href="/">回到連線頁</a>', 400)
        self.page('<h1>找不到此頁</h1>', 404)

    def do_POST(self):
        from member_oauth import handle
        if handle(self,'POST'):return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= 4096:
            return self.page('<h1>無效的連線請求</h1>', 403)
        form = parse.parse_qs(self.rfile.read(length).decode('utf-8', errors='replace'))
        if (self.path != '/connect' or self.headers.get('Host') not in ('127.0.0.1:8791', 'localhost:8791')
                or self.headers.get('Origin') not in (None, 'null', 'http://127.0.0.1:8791', 'http://localhost:8791')
                or not secrets.compare_digest(form.get('csrf', [''])[0], connect_csrf)):
            return self.page('<h1>無效的連線請求</h1>', 403)
        try:
            client = read_client()
        except CalendarError as exc:
            return self.page('<p>' + html.escape(str(exc)) + '</p>', 400)
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with session_lock:
            for key in list(sessions):
                if sessions[key]['expires'] < time.time():
                    sessions.pop(key)
            sessions[state] = {'verifier': verifier, 'expires': time.time()+600}
        target = 'https://accounts.google.com/o/oauth2/v2/auth?' + parse.urlencode({
            'client_id': client['client_id'], 'redirect_uri': REDIRECT, 'response_type': 'code',
            'scope': ' '.join(SCOPES), 'access_type': 'offline', 'prompt': 'consent',
            'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})
        self.send_response(303)
        self.send_header('Location', target)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()


if __name__ == '__main__':
    from elf import load_env
    load_env()
    print('Calendar connection page: http://127.0.0.1:8791', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8791), OAuthHandler).serve_forever()
