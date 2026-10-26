"""Opt-in group digests with a separate SQLite database for every member.

The broker never passes owner notes, calendar credentials, or an owner Elf to a
member. LINE source membership is proven by a signed event from the member.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone, timedelta

from elf import Elf, Problem, SYSTEM, TAIPEI

SITE = 'https://johnny-0120.github.io/line-work-elf/'
HELP = ('多人群組整理：\n'
        '1. 加我為好友，私訊 /同意並開始。\n'
        '2. 私訊 /綁定群組，將整行代碼由你本人貼到已加入我的工作群。\n'
        '3. 私訊 /整理，摘要只傳給你。只收集綁定後的新文字。\n'
        '/群組 查看自己的來源；群內 /工作群組 名稱 可改名。\n'
        '/待回覆、/已處理 編號 管理自己的待辦。\n'
        '/每日整理 開啟（或關閉）：台北時間 21:00 後自動整理。預設關閉。\n'
        '群內 /暫停收集、/停止收集 只影響你自己的收集；私訊 /解除群組 名稱 可移除。\n'
        '/刪除我的資料：停止使用並清除本機帳戶資料。\n'
        '目前多人版只整理群組，不分析一般私訊、不提供日曆或私人筆記工具。')
CONSENT = ('群組文字會經 OpenRouter 與免費模型供應者分析，摘要與待辦私訊你本人。'
           '請先取得群組成員同意；不要加入敏感對話。'
           '「私事」或 #private 開頭不送 AI，其餘私事過濾仍可能有誤。'
           '訊息與摘要工作紀錄保留最多七天，待辦約三十天；各人的資料獨立儲存，'
           '服務管理者仍能管理本機檔案，並非端對端加密。\n'
           '政策：'+SITE+'privacy.html\n'
           '同意上述處理方式後，私訊 /同意並開始；不會自動啟用任何群組。')


class MemberElf(Elf):
    def __init__(self, broker, uid, key, path):
        self.broker, self.member_key = broker, key
        super().__init__(path, broker.parent.send, asynchronous=broker.parent.asynchronous,
                         owner_id=uid, member=True)
        self.db.execute('PRAGMA secure_delete=ON')

    def own_groups(self):
        return [dict(row) for row in self.db.execute('SELECT s.name,s.enabled FROM sources s JOIN group_bindings b ON b.id=s.id ORDER BY s.name')]

    def calendar(self):
        from member_calendar import MemberCalendar
        return MemberCalendar(self)

    def reply_messages(self,event,text):
        from interaction import render
        rendered=render(self,event,text)
        if rendered:return rendered
        import onboarding as ui
        command=event.get('message',{}).get('text','').strip()
        if event.get('source',{}).get('type')!='user':
            msg=ui.text_message(text)
            msg['quickReply']={'items':[{'type':'action','action':{'type':'uri','label':'回私訊查看下一步','uri':ui.chat_url()}}]}
            return [msg]
        if command=='/綁定群組': return [ui.pairing(text)]
        if command in ('/說明','/開始','/開始使用','/加入試用'): return [ui.dashboard(self.own_groups())]
        if command=='/群組': return [ui.group_list(self.own_groups())]
        if command=='/邀請教學': return [ui.invitation()]
        if command=='/資料管理': return [ui.data_controls()]
        if command=='/使用設定':
            row=self.broker.parent.db.execute('SELECT automatic FROM public_members WHERE uid=?',(self.owner_id,)).fetchone()
            return [ui.settings(bool(row[0]))]
        return [ui.text_message(text)]

    def line(self,body,reply_token=None,retry_key=None,messages=None):
        if messages is None:
            from onboarding import text_message
            messages=[text_message(body)]
        return super().line(body,reply_token,retry_key,messages)

    def command(self, text, sid, source_type):
        from interaction import handle
        handled=handle(self,text,sid,source_type)
        if handled is not None:return handled
        if text=='/行程' or text.startswith(('/行程 ','/新增行程 ')):
            if not self.calendar().status()['connected']:return '請先按 Google Calendar 卡片完成個人授權。',False
            return super().command(text,sid,source_type)
        if source_type in ('group','room'):
            if not text.startswith(('/綁定群組 ', '/工作群組', '/暫停收集', '/停止收集')):
                return None, False
            if not text.startswith('/綁定群組 ') and not self.db.execute('SELECT 1 FROM group_bindings WHERE id=?',(sid,)).fetchone():
                return '請先私訊 /綁定群組，取得你自己的代碼後由你本人貼在這裡。',False
            if text.startswith('/綁定群組 ') and not self.db.execute('SELECT 1 FROM sources WHERE id=?',(sid,)).fetchone():
                if self.db.execute('SELECT count(*) FROM sources').fetchone()[0]>=5:
                    return '每人最多綁定五個群組；請先私訊 /解除群組 名稱。',False
            # Existing members cannot silently advance the evidence boundary by
            # re-pairing. A fresh binding after removal starts an empty source.
            if text.startswith('/綁定群組 ') and self.db.execute('SELECT 1 FROM sources WHERE id=?',(sid,)).fetchone():
                return '你已綁定這個群組；不用重複綁定。',False
            result, run = super().command(text,sid,source_type)
            if text=='/停止收集':
                self.db.execute('DELETE FROM group_bindings WHERE id=?',(sid,))
            if result:
                result=result.replace('擁有者','綁定者本人')
                if text.startswith('/綁定群組 '):
                    result+='\n這只啟用貼代碼者自己的收集，其他人須各自私訊同意並綁定；每日整理由本人選擇開啟。'
            return result,run
        if text in ('/說明','/開始','/開始使用','/加入試用'):
            return HELP,False
        if text in ('/使用設定','/資料管理','/邀請教學'):
            return {'/使用設定':'你可開關自己的每日摘要、查看用量與管理資料。',
                    '/資料管理':'可移除自己的群組，或清除本機帳戶；不影響他人。',
                    '/邀請教學':'先加我為好友，再在目標群組的邀請功能選擇我；回私訊取得並複製自己的綁定指令。'}[text],False
        if text.startswith('/解除群組 '):
            name=text[len('/解除群組 '):].strip()
            rows=self.db.execute('SELECT id FROM sources WHERE name=?',(name,)).fetchall()
            if len(rows)!=1:
                return '請用 /群組 查到的完整名稱，且名稱必須唯一。',False
            self.purge_source(rows[0]['id'])
            self.db.execute('DELETE FROM group_bindings WHERE id=?',(rows[0]['id'],))
            return '已解除你自己的群組收集並清除其訊息與待辦；不影響其他人的綁定。',False
        if text.startswith('/每日整理'):
            choice=text[len('/每日整理'):].strip()
            if choice not in ('開啟','關閉'):
                return '請傳 /每日整理 開啟 或 /每日整理 關閉。',False
            self.broker.parent.db.execute('UPDATE public_members SET automatic=? WHERE uid=?',(int(choice=='開啟'),self.owner_id))
            return ('已開啟每日台北時間 21:00 後的個人摘要；需有新訊息且在用量額度內。' if choice=='開啟' else '已關閉每日摘要；仍可私訊 /整理。'),False
        if text=='/狀態':
            groups=self.db.execute('SELECT count(*) FROM sources WHERE enabled=1').fetchone()[0]
            pending=self.db.execute('SELECT count(*) FROM messages WHERE seen=0').fetchone()[0]
            automatic=self.broker.parent.db.execute('SELECT automatic FROM public_members WHERE uid=?',(self.owner_id,)).fetchone()[0]
            used=self.broker.used(self.member_key)
            return f'你的群組：{groups} 個收集中；尚有 {pending} 則新文字。\n每日整理：'+('開啟' if automatic else '關閉')+f'\n今日 AI 請求：{used}/{self.broker.user_limit}（UTC 日界）。\n各人資料獨立；只使用免費模型。',False
        if text in ('/綁定群組','/整理','/立即整理','/待回覆','/群組') or text.startswith('/已處理 '):
            if text in ('/整理','/立即整理') and (self.broker.used(self.member_key)>=self.broker.user_limit or self.broker.total()>=self.broker.public_limit):
                return '今日免費 AI 整理額度已用完（UTC 日界），文字仍保留；額度重置後可再傳 /整理。沒有切換付費模型。',False
            result,run=super().command(text,sid,source_type)
            if text in ('/整理','/立即整理') and not self.has_pending_content():
                return '你目前沒有待整理的群組文字。請先綁定群組，並在綁定後傳入新訊息。',False
            return result,run
        return HELP,False

    def job_input(self, job):
        # Do not call the owner implementation, which loads Obsidian background.
        pending=json.loads(job['payload'])
        data={'owner_id':self.owner_id,'owner_names':[],
              'now':datetime.now(TAIPEI).isoformat(),'messages':pending,
              'recent_context':[],'background':[],
              'public_job_id':self.broker.wrap(self.member_key,job['id'])}
        return {'run':True,'job_id':job['id'],'system':SYSTEM,
                'prompt':json.dumps(data,ensure_ascii=False)}

    def proxy(self, body):
        return self.broker.parent.proxy(body)

    def deliver(self,jid,output):
        row=self.db.execute('SELECT payload FROM jobs WHERE id=?',(jid,)).fetchone()
        if row:
            self.broker.verify_membership(self,sorted({m['source'] for m in json.loads(row['payload'])}))
            for original in json.loads(row['payload']):
                if not self.db.execute('SELECT 1 FROM sources WHERE id=? AND enabled=1',(original['source'],)).fetchone():
                    raise Problem(409,'Member source no longer authorized.')
        return super().deliver(jid,output)


class PublicGroups:
    def __init__(self,parent,db_path):
        self.parent=parent
        self.memory=db_path==':memory:'
        self.root=Path(db_path).resolve().parent/'members'
        self.children={}
        self.cursor=0
        self.user_limit=max(1,min(100,int(os.getenv('PUBLIC_USER_DAILY_CALLS','30'))))
        self.public_limit=max(1,min(950,int(os.getenv('PUBLIC_GLOBAL_DAILY_CALLS','900'))))
        parent.db.executescript('''
        CREATE TABLE IF NOT EXISTS public_members(uid TEXT PRIMARY KEY,tenant TEXT UNIQUE,automatic INTEGER DEFAULT 0,last_daily TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS public_events(id TEXT PRIMARY KEY,created REAL);
        CREATE TABLE IF NOT EXISTS public_usage(tenant TEXT,day TEXT,calls INTEGER DEFAULT 0,PRIMARY KEY(tenant,day));
        ''')
        parent.db.commit()
        for row in parent.db.execute('SELECT uid,tenant FROM public_members').fetchall():
            self.open(row['uid'],row['tenant'])

    def open(self,uid,key):
        if key not in self.children:
            path=':memory:' if self.memory else str(self.root/(key+'.sqlite3'))
            self.children[key]=MemberElf(self,uid,key,path)
        return self.children[key]

    @staticmethod
    def wrap(key,jid):
        return 'mt:'+key+':'+jid

    @staticmethod
    def day():
        return datetime.now(timezone.utc).date().isoformat()

    def used(self,key):
        row=self.parent.db.execute('SELECT calls FROM public_usage WHERE tenant=? AND day=?',(key,self.day())).fetchone()
        return row[0] if row else 0

    def total(self):
        return self.parent.db.execute('SELECT COALESCE(SUM(calls),0) FROM public_usage WHERE day=?',(self.day(),)).fetchone()[0]

    def reply(self,event,text,messages=None):
        token=event.get('replyToken')
        if not token: return
        if self.parent.asynchronous:
            self.parent.db.execute('INSERT OR IGNORE INTO outbox(id,text,reply_token,created,messages_json) VALUES(?,?,?,?,?)',
                                   ('public-reply:'+event['webhookEventId'],text,token,time.time(),json.dumps(messages,ensure_ascii=False) if messages else None))
        else:
            self.parent.line(text,reply_token=token,messages=messages)

    def forget(self,key):
        child=self.children.pop(key)
        # Only fixed, validated table identifiers from this member database.
        tables=[r[0] for r in child.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        with child.lock,child.db:
            for table in tables:
                if table.replace('_','').isalnum(): child.db.execute('DELETE FROM "'+table+'"')
        child.db.execute('VACUUM')
        child.db.close()
        self.parent.db.execute('DELETE FROM public_members WHERE tenant=?',(key,))
        # Usage persists as non-content anti-abuse accounting until expiry.

    def ingest(self,payload):
        import onboarding as ui
        trigger=False
        for event in payload.get('events',[]):
            eid=event.get('webhookEventId')
            source=event.get('source',{})
            kind=source.get('type')
            uid=source.get('userId')
            key_name={'user':'userId','group':'groupId','room':'roomId'}.get(kind)
            sid=source.get(key_name) if key_name else None
            if (not isinstance(eid,str) or not eid or not isinstance(sid,str) or not sid
                or (kind!='group' and source.get('groupId')) or (kind!='room' and source.get('roomId'))):
                continue
            if self.parent.db.execute('SELECT 1 FROM blocked_sources WHERE id=?',(sid,)).fetchone(): continue
            if self.parent.db.execute('SELECT 1 FROM public_events WHERE id=?',(eid,)).fetchone():
                trigger=trigger or self.ready()
                continue
            text=event.get('message',{}).get('text','') if event.get('message',{}).get('type')=='text' else ''
            member=self.parent.db.execute('SELECT * FROM public_members WHERE uid=?',(uid,)).fetchone() if isinstance(uid,str) else None
            if kind=='user' and uid!=self.parent.owner_id:
                if event.get('type')=='unfollow' and member:
                    self.forget(member['tenant'])
                elif event.get('type')=='follow' or (text and not member):
                    if text=='/同意並開始':
                        maximum=max(1,min(100,int(os.getenv('PUBLIC_MAX_USERS','50'))))
                        if self.parent.db.execute('SELECT count(*) FROM public_members').fetchone()[0]>=maximum:
                            self.reply(event,'目前試用名額已滿，請稍後再試。')
                        else:
                            tenant=hmac.new(os.environ['INTERNAL_TOKEN'].encode(),uid.encode(),hashlib.sha256).hexdigest()
                            self.parent.db.execute('INSERT INTO public_members(uid,tenant,created) VALUES(?,?,?)',(uid,tenant,time.time()))
                            child=self.open(uid,tenant)
                            code,_=child.command('/綁定群組',uid,'user')
                            child.db.commit()
                            self.reply(event,'已建立你的獨立空間，未啟用任何群組。\n'+code+'\n\n'+HELP,
                                       [ui.pairing(code)])
                    else:
                        if member:
                            child=self.open(uid,member['tenant'])
                            message=ui.dashboard(child.own_groups())
                        elif event.get('type')=='follow': message=ui.welcome()
                        elif text=='/邀請教學': message=ui.invitation()
                        elif text in ('/開始使用','/開始','/綁定群組','/使用設定','/綁定此群'): message=ui.consent()
                        else: message=ui.welcome()
                        self.reply(event,CONSENT,[message])
                elif member and text=='/刪除我的資料':
                    self.forget(member['tenant'])
                    self.reply(event,'你的群組綁定、訊息、摘要與待辦已從本機清除，已停止使用。已送出的 LINE 訊息、第三方服務紀錄與既有備份不會因此撤回；不影響其他人。')
                elif member and event.get('type')=='message':
                    child=self.open(uid,member['tenant'])
                    from interaction import wizard
                    if text.startswith('/') or wizard(child):
                        body=event_body(event)
                        result=child.ingest(body,base_signature(body))
                        trigger=trigger or result['run']
                    elif text:
                        notice='多人版不收集或分析一般私訊。你可以按下方按鈕操作，或從底部選單開始。'
                        self.reply(event,notice,[ui.text_message(notice)])
            elif kind in ('group','room'):
                if event.get('type')=='join':
                    self.reply(event,'小精靈已加入。每位成員請各自開啟我的私訊，按「開始使用」；不會自動收集任何人的訊息。',[ui.invitation(group=True)])
                if (event.get('type')=='message' and uid!=self.parent.owner_id
                    and text in ('/開始','/開始使用','/同意並開始','/綁定群組','/整理','/立即整理','/說明','/使用設定','/綁定此群')):

                    if not member or text!='/綁定此群':
                        self.reply(event,'請先開啟我的私訊，建立自己的空間；摘要只私訊本人。',[ui.invitation(group=True)])
                if event.get('type')=='memberLeft':
                    departed={m.get('userId') for m in event.get('left',{}).get('members',[]) if isinstance(m,dict)}
                    for child in self.children.values():
                        if child.owner_id in departed:
                            child.purge_source(sid)
                            child.db.execute('DELETE FROM group_bindings WHERE id=?',(sid,))
                            child.db.execute('UPDATE known_groups SET present=0 WHERE id=?',(sid,))
                            child.db.commit()
                # Only the code's owner can bind. No raw group data enters a
                # database before this signed actor has enabled that source.
                if member and text.startswith(('/綁定群組 ','/工作群組','/暫停收集','/停止收集','/綁定此群')):
                    child=self.open(uid,member['tenant'])
                    body=event_body(event)
                    child.ingest(body,base_signature(body))
                for child in list(self.children.values()):
                    bound=child.db.execute('SELECT b.verified_at,s.enabled FROM sources s JOIN group_bindings b ON b.id=s.id WHERE s.id=?',(sid,)).fetchone()
                    if not bound: continue
                    if event.get('type')=='message':
                        if not bound['enabled']: continue
                        stamp=event.get('timestamp')
                        if not isinstance(stamp,int) or stamp<bound[0]*1000: continue
                        # Slash commands are handled for the actor above, not
                        # replayed into another member's command interface.
                        if text.startswith('/'): continue
                        if child.db.execute('SELECT count(*) FROM messages WHERE timestamp>=?',(int(time.time()*1000)-86400000,)).fetchone()[0]>=1000:
                            marker='notice:intake:'+self.day()
                            if not child.db.execute('SELECT 1 FROM controls WHERE id=?',(marker,)).fetchone():
                                import uuid
                                child.db.execute('INSERT OR IGNORE INTO outbox(id,text,created) VALUES(?,?,?)',
                                    (str(uuid.uuid4()),'你最近二十四小時已收集一千則群組文字，後續新文字暫時略過；請減少收集群組，摘要不代表完整紀錄。',time.time()))
                                child.db.execute('INSERT OR REPLACE INTO controls VALUES(?,?)',(marker,time.time()))
                                child.db.commit()
                            continue
                    body=event_body(event)
                    child.ingest(body,base_signature(body))
            self.parent.db.execute('INSERT OR IGNORE INTO public_events VALUES(?,?)',(eid,time.time()))
        self.parent.db.execute('DELETE FROM public_events WHERE created<?',(time.time()-7*86400,))
        self.parent.db.execute('DELETE FROM public_usage WHERE day<?',((datetime.now(timezone.utc)-timedelta(days=8)).date().isoformat(),))
        return trigger

    def ready(self):
        # Scheduled opt-in is checked by the existing five-minute n8n recovery.
        now=datetime.now(TAIPEI)
        for row in self.parent.db.execute('SELECT * FROM public_members').fetchall():
            child=self.children[row['tenant']]
            if row['automatic'] and now.hour>=21 and row['last_daily']!=now.date().isoformat():
                if child.has_pending_content():
                    child.db.execute("INSERT OR REPLACE INTO controls VALUES('digest_requested',1)")
                    child.db.commit()
                self.parent.db.execute('UPDATE public_members SET last_daily=? WHERE tenant=?',(now.date().isoformat(),row['tenant']))
        self.parent.db.commit()
        return any(self.available(child) and self.used(key)<self.user_limit and self.total()<self.public_limit
                   for key,child in self.children.items())

    @staticmethod
    def available(child):
        lease=child.db.execute("SELECT value FROM controls WHERE id='public_lease'").fetchone()
        return (not lease or lease[0]<=time.time()) and child.recover()['run']

    def prepare(self):
        self.ready()
        for key,child in self.children.items():
            if self.available(child) and self.used(key)<self.user_limit and self.total()<self.public_limit:
                sources=[r[0] for r in child.db.execute('SELECT DISTINCT m.source FROM messages m JOIN sources s ON s.id=m.source WHERE m.seen=0 AND s.enabled=1')]
                try:
                    self.verify_membership(child,sources)
                except Problem:
                    continue
                data=child.prepare()
                if data.get('run'):
                    child.db.execute("INSERT OR REPLACE INTO controls VALUES('public_lease',?)",(time.time()+180,))
                    child.db.commit()
                    data['job_id']=self.wrap(key,data['job_id'])
                    return data
        return {'run':False}

    def verify_membership(self,child,sources):
        for sid in sources:
            origin=child.db.execute('SELECT source_type FROM group_origins WHERE id=?',(sid,)).fetchone()
            try:
                valid=bool(origin and member_exists(origin[0],sid,child.owner_id))
            except Problem:
                self.membership_notice(child,sid,'目前無法向 LINE 核對群組成員身分，已保留資料並暫緩整理；約十五分鐘後重試。')
                child.db.execute("INSERT OR REPLACE INTO controls VALUES('retry_after',?)",(time.time()+900,))
                child.db.commit()
                raise
            if not valid:
                child.purge_source(sid)
                child.db.execute('DELETE FROM group_bindings WHERE id=?',(sid,))
                child.db.execute('UPDATE known_groups SET present=0 WHERE id=?',(sid,))
                self.membership_notice(child,sid,'LINE 無法確認你仍在已綁定的群組中，已停止該來源並清除其文字與待辦。重新加入後需重新綁定。')
                child.db.commit()
                raise Problem(403,'Group membership ended; content removed, no delivery.')

    @staticmethod
    def membership_notice(child,sid,text):
        notice=hashlib.sha256((sid+str(int(time.time()//900))).encode()).hexdigest()
        marker='notice:membership:'+notice
        if not child.db.execute('SELECT 1 FROM controls WHERE id=?',(marker,)).fetchone():
            import uuid
            child.db.execute('INSERT OR IGNORE INTO outbox(id,text,created) VALUES(?,?,?)',(str(uuid.uuid4()),text,time.time()))
            child.db.execute('INSERT OR REPLACE INTO controls VALUES(?,?)',(marker,time.time()))

    def dispatch(self,method,scope,*args):
        with self.parent.lock:
            parts=scope.split(':')
            if len(parts)!=3 or parts[0]!='mt' or parts[1] not in self.children:
                raise Problem(404,'Member job not found.')
            child=self.children[parts[1]]
            with child.lock:
                result=getattr(child,method)(parts[2],*args)
                if method in ('deliver','failed'):
                    child.db.execute("DELETE FROM controls WHERE id='public_lease'")
                    child.db.commit()
            if method=='deliver': result['run']=self.parent.has_pending_content() or self.ready()
            return result

    def reserve_model_call(self,body):
        scope=None
        for message in body.get('messages',[]):
            if message.get('role')!='user': continue
            content=message.get('content')
            if isinstance(content,list): content=''.join(x.get('text','') for x in content if isinstance(x,dict))
            try: data=json.loads(content) if isinstance(content,str) else None
            except ValueError: continue
            if isinstance(data,dict) and isinstance(data.get('public_job_id'),str): scope=data['public_job_id']
        with self.parent.lock:
            key='owner'
            if scope:
                parts=scope.split(':')
                if len(parts)!=3 or parts[0]!='mt' or parts[1] not in self.children: raise Problem(403,'Invalid member scope.')
                key=parts[1]
                row=self.children[key].db.execute("SELECT 1 FROM jobs WHERE id=? AND state='pending'",(parts[2],)).fetchone()
                if not row: raise Problem(403,'Member job is no longer active.')
                if self.used(key)>=self.user_limit or self.total()>=self.public_limit:
                    raise Problem(429,'Member daily free-AI allowance reached; retry next UTC day.')
            if self.total()>=950: raise Problem(429,'Application daily free-AI safety limit reached.')
            self.parent.db.execute('INSERT INTO public_usage VALUES(?,?,1) ON CONFLICT(tenant,day) DO UPDATE SET calls=calls+1',(key,self.day()))
            self.parent.db.commit()

    def background_once(self):
        with self.parent.lock:
            keys=list(self.children)
            if not keys: return
            self.cursor=(self.cursor+1)%len(keys)
            child=self.children[keys[self.cursor]]
            child.lock.acquire()
        try:
            # Deletion waits for this member lock; another user's webhook does
            # not wait for this member's outbound network request.
            child.prune()
            child.background_once()
        finally:
            child.lock.release()

    def close(self):
        for child in self.children.values(): child.db.close()


def base_signature(raw):
    import base64
    return base64.b64encode(hmac.new(os.environ['LINE_CHANNEL_SECRET'].encode(),raw,hashlib.sha256).digest()).decode()


def event_body(event):
    return json.dumps({'destination':os.getenv('LINE_BOT_USER_ID',''),
                       'events':[event]},ensure_ascii=False).encode()


def member_exists(kind,sid,uid):
    from urllib import request,error
    from urllib.parse import quote
    from line_api import ensure_token
    if kind not in ('group','room'): return False
    url='https://api.line.me/v2/bot/'+kind+'/'+quote(sid,safe='')+'/member/'+quote(uid,safe='')
    req=request.Request(url,headers={'Authorization':'Bearer '+ensure_token()})
    try:
        with request.urlopen(req,timeout=5) as response:
            return json.load(response).get('userId')==uid
    except error.HTTPError as exc:
        if exc.code==404: return False
        raise Problem(503,'Cannot verify LINE group membership; no summary generated.') from None
    except (error.URLError,TimeoutError,ValueError):
        raise Problem(503,'Cannot verify LINE group membership; no summary generated.') from None
