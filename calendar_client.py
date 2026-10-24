"""Google Calendar client with local OAuth storage; no credentials in logs."""
import json
import os
from pathlib import Path
import threading
import time
from urllib import request, parse, error

BASE = Path(__file__).resolve().parent
SCOPES = ['https://www.googleapis.com/auth/calendar.events.owned',
          'https://www.googleapis.com/auth/calendar.freebusy']


class CalendarError(Exception):
    def __init__(self, message, status=None, code=None):
        self.status, self.code = status, code
        super().__init__(message)


def state_dir():
    return Path(os.getenv('ELF_STATE_DIR', str(Path(os.getenv('LOCALAPPDATA', str(BASE))) / 'LineWorkElf/state')))


def client_file():
    return Path(os.getenv('GOOGLE_OAUTH_CLIENT_FILE', str(state_dir() / 'google-client.json')))


def token_file():
    return state_dir() / 'google-token.json'


def read_client():
    try:
        data = json.loads(client_file().read_text(encoding='utf-8-sig'))
        client = data['installed']
        if not client.get('client_id', '').endswith('.apps.googleusercontent.com'):
            raise ValueError()
        return client
    except (OSError, ValueError, KeyError):
        raise CalendarError('尚未設定 Google OAuth 桌面應用程式憑證；請開啟本機行事曆連線頁。') from None


def save_token(data):
    data = dict(data)
    if data.get('refresh_token_expires_in') and not data.get('refresh_expires_at'):
        issued = data.get('expires_at',time.time()) - int(data.get('expires_in',0))
        data['refresh_expires_at'] = issued + int(data['refresh_token_expires_in'])
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def exchange(form):
    req = request.Request('https://oauth2.googleapis.com/token', parse.urlencode(form).encode(),
                          {'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with request.urlopen(req, timeout=25) as response:
            return json.load(response)
    except error.HTTPError as exc:
        try:
            reason = json.loads(exc.read()).get('error')
        except (ValueError,TypeError):
            reason = None
        if reason == 'invalid_grant':
            raise CalendarError('Google 的自動續期授權已過期或遭撤銷，請在電腦開啟 http://127.0.0.1:8791 重新連線。若 Google 專案仍在測試模式，授權通常七天到期。',exc.code,'invalid_grant') from None
        raise CalendarError('Google 授權未完成，請確認 OAuth 用戶端設定後重新連線。', exc.code) from None
    except (error.URLError, TimeoutError, ValueError):
        raise CalendarError('目前無法連線 Google，請稍後重試。') from None


class GoogleCalendar:
    def __init__(self):
        self.lock = threading.RLock()

    def status(self):
        try:
            saved=json.loads(token_file().read_text(encoding='utf-8'))
        except (OSError,ValueError):
            saved={}
        expiry=saved.get('refresh_expires_at')
        if not expiry and saved.get('refresh_token_expires_in'):
            expiry=saved.get('expires_at',0)-int(saved.get('expires_in',0))+int(saved['refresh_token_expires_in'])
        expired=bool(expiry and expiry<=time.time())
        return {'client_configured': client_file().is_file(),
                'connected':bool(saved.get('refresh_token') and not expired and saved.get('authorization_error')!='invalid_grant'),
                'refresh_expires_at':expiry,'reauthorization_required':expired or saved.get('authorization_error')=='invalid_grant',
                'calendar': os.getenv('GOOGLE_CALENDAR_ID', 'primary')}

    def access_token(self):
        with self.lock:
            try:
                saved = json.loads(token_file().read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise CalendarError('尚未連接 Google Calendar，請在電腦開啟 http://127.0.0.1:8791 完成授權。') from None
            if saved.get('authorization_error')=='invalid_grant':
                raise CalendarError('Google 授權已失效，請在電腦開啟 http://127.0.0.1:8791 重新連線。',400,'invalid_grant')
            if saved.get('access_token') and saved.get('expires_at', 0) > time.time() + 60:
                return saved['access_token']
            client = read_client()
            if not saved.get('refresh_token'):
                raise CalendarError('Google 授權已到期，請重新連線。')
            try:
                fresh = exchange({'grant_type': 'refresh_token', 'client_id': client['client_id'],
                                  'client_secret': client.get('client_secret', ''), 'refresh_token': saved['refresh_token']})
            except CalendarError as exc:
                if exc.code=='invalid_grant':
                    save_token({**saved,'authorization_error':'invalid_grant'})
                raise
            fresh['refresh_token'] = fresh.get('refresh_token', saved['refresh_token'])
            fresh['expires_at'] = time.time() + int(fresh.get('expires_in', 3600))
            if fresh.get('refresh_token_expires_in'):
                fresh['refresh_expires_at']=time.time()+int(fresh['refresh_token_expires_in'])
            save_token({**saved,**fresh})
            return fresh['access_token']

    def api(self, method, path, body=None, query=None):
        if not path.startswith('/calendar/v3/'):
            raise CalendarError('不支援的行事曆操作。')
        url = 'https://www.googleapis.com' + path
        if query:
            url += '?' + parse.urlencode(query)
        req = request.Request(url, json.dumps(body).encode() if body is not None else None,
            {'Authorization': 'Bearer ' + self.access_token(), 'Content-Type': 'application/json'}, method=method)
        try:
            with request.urlopen(req, timeout=25) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            if exc.code == 404:
                raise CalendarError('找不到行程或行事曆。', 404) from None
            if exc.code == 409:
                raise CalendarError('行程已存在。', 409) from None
            if exc.code in (401, 403):
                raise CalendarError('Google Calendar 授權不足或已到期，請重新連線並確認 Calendar API 已啟用。', exc.code) from None
            raise CalendarError('Google Calendar 暫時無法完成操作，請稍後重試。', exc.code) from None
        except (error.URLError, TimeoutError, ValueError):
            raise CalendarError('Google Calendar 連線中斷；稍後重試會先檢查是否已建立，避免重複。') from None

    def calendar_path(self):
        return '/calendar/v3/calendars/' + parse.quote(os.getenv('GOOGLE_CALENDAR_ID', 'primary'), safe='')

    def busy(self, start, end):
        cid = os.getenv('GOOGLE_CALENDAR_ID', 'primary')
        data = self.api('POST', '/calendar/v3/freeBusy', {
            'timeMin': start, 'timeMax': end, 'timeZone': 'Asia/Taipei', 'items': [{'id': cid}]})
        calendar = data.get('calendars', {}).get(cid)
        if calendar is None or calendar.get('errors'):
            raise CalendarError('無法確認這段時間是否空閒，因此尚未新增行程。')
        return calendar.get('busy', [])

    def list_events(self, start, end):
        data = self.api('GET', self.calendar_path() + '/events', query={
            'timeMin': start, 'timeMax': end, 'singleEvents': 'true', 'orderBy': 'startTime', 'maxResults': 50})
        return [{'title': e.get('summary', '未命名行程')[:160], 'start': e.get('start', {}),
                 'end': e.get('end', {}), 'url': e.get('htmlLink', '')} for e in data.get('items', []) if e.get('status') != 'cancelled']

    def get_event(self, event_id):
        try:
            return self.api('GET', self.calendar_path() + '/events/' + parse.quote(event_id, safe=''))
        except CalendarError as exc:
            if exc.status == 404:
                return None
            raise

    def create_event(self, event_id, job_id, title, start, end, description=None, location=None):
        existing = self.get_event(event_id)
        if existing:
            if existing.get('extendedProperties', {}).get('private', {}).get('elf_job') != job_id:
                raise CalendarError('行程識別碼衝突，未進行變更。')
            return existing
        if self.busy(start, end):
            raise CalendarError('這段時間已有行程；我沒有新增，請改一個時間。')
        body = {'id': event_id, 'summary': title[:160],
                'start': {'dateTime': start, 'timeZone': 'Asia/Taipei'},
                'end': {'dateTime': end, 'timeZone': 'Asia/Taipei'},
                'description': (description or '由 LINE 公事小精靈依擁有者的指令建立。')[:1800],
                'extendedProperties': {'private': {'elf_job': job_id}}}
        if location:
            body['location'] = location[:160]
        try:
            return self.api('POST', self.calendar_path() + '/events', body, {'sendUpdates': 'none'})
        except CalendarError as exc:
            if exc.status == 409:
                existing = self.get_event(event_id)
                if existing and existing.get('extendedProperties', {}).get('private', {}).get('elf_job') == job_id:
                    return existing
            raise
