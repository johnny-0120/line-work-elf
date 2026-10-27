"""Restricted member OAuth endpoints. No owner grants are reachable through this handler."""
from contextlib import closing,contextmanager
import base64
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs,urlsplit,urlencode
from member_calendar import CALLBACK,web_client
from calendar_client import SCOPES,exchange,CalendarError

sessions={}
lock=threading.Lock()

@contextmanager
def database(tenant):
    if not re.fullmatch('[0-9a-f]{64}',tenant):raise ValueError()
    root=Path(os.environ['ELF_DB']).resolve()
    # Never create an account or a DB from an anonymous browser request.
    with closing(sqlite3.connect(root.as_uri()+'?mode=ro',uri=True)) as index:
        if not index.execute('SELECT 1 FROM public_members WHERE tenant=?',(tenant,)).fetchone():raise ValueError()
    path=root.parent/'members'/(tenant+'.sqlite3')
    if path.is_symlink() or not path.is_file():raise ValueError()
    db=sqlite3.connect(path,timeout=10)
    try:
        with db:yield db
    finally:db.close()

def validate(ticket):
    parts=ticket.split('.')
    if len(parts)!=4:raise ValueError()
    tenant,expiry,nonce,signature=parts
    if not re.fullmatch('[0-9a-f]{32}',nonce) or not expiry.isdigit() or not time.time()<int(expiry)<=time.time()+660:raise ValueError()
    expected=hmac.new(os.environ['INTERNAL_TOKEN'].encode(),'.'.join(parts[:3]).encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):raise ValueError()
    with database(tenant) as db:
        if not db.execute('SELECT 1 FROM calendar_tickets WHERE nonce=? AND expires>?',(nonce,time.time())).fetchone():raise ValueError()
    return tenant,nonce

def redirect(h,target,cookie=None):
    h.send_response(303);h.send_header('Location',target);h.send_header('Cache-Control','no-store');h.send_header('Referrer-Policy','no-referrer')
    if cookie:h.send_header('Set-Cookie',cookie)
    h.end_headers()

def handle(h,method):
    url=urlsplit(h.path)
    if not url.path.startswith('/calendar/'):return False
    try:
        if method=='GET' and url.path=='/calendar/connect':
            ticket=parse_qs(url.query).get('ticket',[''])[0];tenant,nonce=validate(ticket);web_client()
            session=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            with lock:
                for k in list(sessions):
                    if sessions[k]['expires']<time.time():sessions.pop(k)
                if len(sessions)>=500:raise ValueError()
                sessions[session]={'tenant':tenant,'nonce':nonce,'expires':time.time()+600,'csrf':csrf,'phase':'confirm'}
            h.send_response(200);h.send_header('Set-Cookie','elf_cal_session='+session+'; Secure; HttpOnly; SameSite=Lax; Path=/calendar/; Max-Age=600')
            body=('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>連接你的日曆</title><style>body{background:#edf4fc;color:#17243a;font:18px/1.8 system-ui;max-width:600px;margin:5vh auto;padding:24px}main{background:white;border-radius:24px;padding:28px}button{background:#516d98;color:white;border:0;border-radius:16px;padding:14px;font:inherit}</style><main><h1>連接你的 Google Calendar</h1><p>這次授權只會存入發出此連結的 LINE 使用者空間。請確認這是你自己剛剛在小精靈取得的連結，不要轉傳。</p><p>Google 將要求查看空閒時間，以及查看、建立、變更及刪除你擁有的日曆活動。小精靈目前提供查詢與新增，新增前會再確認內容。</p><p>可隨時回到 LINE 按「解除日曆連線」，並到 Google 帳號撤銷授權。</p><form action="/calendar/start" method="post"><input type="hidden" name="csrf" value="'+csrf+'"><button>繼續，登入我的 Google 帳號</button></form></main>').encode()
            h.send_header('Content-Type','text/html; charset=utf-8');h.send_header('Content-Length',str(len(body)));h.send_header('Cache-Control','no-store');h.send_header('Referrer-Policy','no-referrer');h.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");h.end_headers();h.wfile.write(body);return True
        cookie=dict(item.strip().split('=',1) for item in h.headers.get('Cookie','').split(';') if '=' in item).get('elf_cal_session','')
        with lock:session=sessions.get(cookie)
        if not session or session['expires']<time.time():raise ValueError()
        if method=='POST' and url.path=='/calendar/start':
            size=int(h.headers.get('Content-Length','0'))
            if not 0<size<4096:raise ValueError()
            form=parse_qs(h.rfile.read(size).decode())
            if not secrets.compare_digest(form.get('csrf',[''])[0],session['csrf']) or session['phase']!='confirm':raise ValueError()
            origin=h.headers.get('Origin')
            # Relay supplies the original browser origin; no foreign website may submit.
            from live_settings import public_base
            if origin!=public_base():raise ValueError()
            client=web_client();verifier=secrets.token_urlsafe(48);state=secrets.token_urlsafe(32)
            with lock:session.update(phase='google',state=state,verifier=verifier)
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            redirect(h,'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':client['client_id'],'redirect_uri':CALLBACK,'response_type':'code','scope':' '.join(SCOPES),'access_type':'offline','prompt':'consent','state':state,'code_challenge':challenge,'code_challenge_method':'S256'}));return True
        if method=='GET' and url.path=='/calendar/callback':
            args=parse_qs(url.query)
            if session['phase']!='google' or not secrets.compare_digest(args.get('state',[''])[0],session['state']):raise ValueError()
            with lock:sessions.pop(cookie,None)
            if 'error' in args:
                h.page('<h1>尚未連接日曆</h1><p>沒有儲存授權，回到 LINE 可重新開始。</p>');return True
            client=web_client()
            token=exchange({'grant_type':'authorization_code','client_id':client['client_id'],'client_secret':client['client_secret'],'redirect_uri':CALLBACK,'code':args.get('code',[''])[0],'code_verifier':session['verifier']})
            if not token.get('refresh_token') or not set(SCOPES).issubset(set(token.get('scope','').split())):raise CalendarError('請同意所列日曆權限後再連接。')
            token['expires_at']=time.time()+int(token.get('expires_in',3600))
            with database(session['tenant']) as db:
                db.execute('BEGIN IMMEDIATE')
                deleted=db.execute('DELETE FROM calendar_tickets WHERE nonce=? AND expires>?',(session['nonce'],time.time())).rowcount
                if deleted!=1:raise ValueError()
                db.execute('INSERT OR REPLACE INTO calendar_tokens VALUES(1,?)',(json.dumps(token),))
            h.page('<h1>你的 Google Calendar 已連接</h1><p>回到 LINE，按「使用設定 → Google Calendar」即可查詢行程或新增安排。授權只儲存在你的個人空間。</p><a href="https://line.me/R/oaMessage/%40526byspu/">回到公事小精靈</a>');return True
        raise ValueError()
    except (ValueError,KeyError,sqlite3.Error,CalendarError,OSError):
        h.page('<h1>這次連線尚未完成</h1><p>連結可能已過期、已使用，或 Google 授權尚未完成。請回 LINE 的 Google Calendar 卡片重新取得個人連結。</p>',400)
        return True
