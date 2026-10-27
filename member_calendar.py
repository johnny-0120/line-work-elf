"""Member Calendar credentials live ONLY in that member's own SQLite database."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode
from calendar_client import GoogleCalendar, CalendarError, SCOPES, exchange

CALLBACK='https://johnny-0120.github.io/line-work-elf/calendar-callback.html'

def web_client():
    path=os.getenv('GOOGLE_WEB_OAUTH_CLIENT_FILE','')
    try:
        data=json.loads(Path(path).read_text(encoding='utf-8-sig'))['web']
        if not data['client_id'].endswith('.apps.googleusercontent.com') or CALLBACK not in data.get('redirect_uris',[]):raise ValueError()
        return data
    except (OSError,KeyError,ValueError):raise CalendarError('手機版個人日曆連線尚未完成服務設定。') from None

class MemberCalendar(GoogleCalendar):
    def __init__(self,elf):
        self.elf=elf;self.lock=elf.lock
    def saved(self):
        row=self.elf.db.execute('SELECT data FROM calendar_tokens WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}
    def replace_if_current(self,previous,data):
        row=self.elf.db.execute('SELECT data FROM calendar_tokens WHERE id=1').fetchone()
        if not row or json.loads(row[0])!=previous:raise CalendarError('日曆連線已解除或更新，請重新操作。')
        cur=self.elf.db.execute('UPDATE calendar_tokens SET data=? WHERE id=1 AND data=?',(json.dumps(data),row[0]))
        self.elf.db.commit()
        if not cur.rowcount:raise CalendarError('日曆連線已解除或更新，請重新操作。')

    def save(self,data):
        self.elf.db.execute('INSERT OR REPLACE INTO calendar_tokens VALUES(1,?)',(json.dumps(data),));self.elf.db.commit()
    def status(self):
        saved=self.saved()
        try:web_client();configured=True
        except CalendarError:configured=False
        return {'connected':bool(saved.get('refresh_token') and saved.get('authorization_error')!='invalid_grant'), 'client_configured':configured,'calendar':'primary'}
    def access_token(self):
        with self.lock:
            saved=self.saved()
            if not saved.get('refresh_token') or saved.get('authorization_error')=='invalid_grant':raise CalendarError('請從「使用設定 → Google Calendar」重新連線你的個人日曆。')
            if saved.get('access_token') and saved.get('expires_at',0)>time.time()+60:return saved['access_token']
            client=web_client()
            try:fresh=exchange({'grant_type':'refresh_token','client_id':client['client_id'],'client_secret':client['client_secret'],'refresh_token':saved['refresh_token']})
            except CalendarError as exc:
                if exc.code=='invalid_grant':self.replace_if_current(saved,{**saved,'authorization_error':'invalid_grant'})
                raise CalendarError('你的日曆授權需要重新連線，請回 Google Calendar 卡片操作。') from None
            self.replace_if_current(saved,{**saved,**fresh,'expires_at':time.time()+int(fresh.get('expires_in',3600)),'authorization_error':None})
            return fresh['access_token']
    def calendar_path(self):return '/calendar/v3/calendars/primary'
    def busy(self,start,end):
        data=self.api('POST','/calendar/v3/freeBusy',{'timeMin':start,'timeMax':end,'items':[{'id':'primary'}]})
        result=data.get('calendars',{}).get('primary')
        if not isinstance(result,dict) or result.get('errors'):raise CalendarError('無法確認你的日曆空閒時間。')
        return result.get('busy',[])

def link(elf):
    web_client()
    from live_settings import public_base
    base=public_base()
    if not base.startswith('https://'):raise CalendarError('日曆公開入口尚未準備好。')
    tenant=elf.member_key
    nonce=secrets.token_hex(16);expiry=str(int(time.time()+600))
    message='.'.join((tenant,expiry,nonce))
    signature=hmac.new(os.environ['INTERNAL_TOKEN'].encode(),message.encode(),hashlib.sha256).hexdigest()
    elf.db.execute('CREATE TABLE IF NOT EXISTS calendar_tickets(nonce TEXT PRIMARY KEY,expires REAL)')
    elf.db.execute('DELETE FROM calendar_tickets WHERE expires<?',(time.time(),))
    elf.db.execute('INSERT INTO calendar_tickets VALUES(?,?)',(nonce,int(expiry)))
    return 'https://johnny-0120.github.io/line-work-elf/calendar-connect.html?openExternalBrowser=1#'+urlencode({'origin':base,'ticket':message+'.'+signature})

def calendar_card(elf):
    import onboarding as ui
    member=hasattr(elf,'member_key')
    client=elf.calendar();connected=client.status()['connected']
    buttons=[];paragraphs=['只操作你授權的日曆，不會連到其他使用者的帳號。登入會開啟手機外部瀏覽器，請在同一個瀏覽器完成授權。']
    if member:
        try:url=link(elf);buttons.append(ui.button('重新連接 Google' if connected else '登入 Google 並連線',uri=url,primary=True))
        except CalendarError:paragraphs.append('手機登入功能正在完成服務設定；未啟用前，不會讓你連到管理者的日曆。')
    elif not connected:
        paragraphs.append('你的管理者日曆使用本機桌面授權，請在這台電腦開啟連線頁。手機版個人授權使用另一個獨立入口。')
        buttons.append(ui.button('在這台電腦連線',uri='http://127.0.0.1:8791',primary=True))
    if connected:
        buttons += [ui.button('今天的行程','/行程 今天'),ui.button('明天的行程','/行程 明天'),ui.button('本週行程','/行程 本週'),ui.button('新增行程','/新增行程')]
    if member and connected:buttons.append(ui.button('解除我的日曆連線','/解除日曆確認'))
    buttons.append(ui.button('返回使用設定','/使用設定'))
    return ui.card('Google Calendar · 個人連線','日曆已連接' if connected else '連接你的 Google Calendar',paragraphs,buttons,'Google Calendar 個人連線：'+('已連接' if connected else '尚未連接'))
