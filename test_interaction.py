import json
import os
from datetime import datetime,timedelta
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from elf import TAIPEI
from public_groups import event_body,base_signature
from interaction import PREFIX,key,wizard
import test_public_groups as fixtures

class NativeFlowTests(unittest.TestCase):
    setUp=fixtures.PublicGroupsTests.setUp
    tearDown=fixtures.PublicGroupsTests.tearDown
    send=fixtures.PublicGroupsTests.send
    child=fixtures.PublicGroupsTests.child
    register=fixtures.PublicGroupsTests.register
    def click(self,cmd,uid='alice',sid=None,kind='user',date=None):
        self.count+=1
        event={'webhookEventId':'click-'+str(self.count),'type':'postback','timestamp':int(time.time()*1000),'replyToken':'reply',
            'source':{'type':kind,'userId':uid},'postback':{'data':PREFIX+cmd}}
        if kind!='user':event['source']['groupId' if kind=='group' else 'roomId']=sid
        if date:event['postback']['params']={'datetime':date}
        raw=event_body(event);return self.elf.ingest(raw,base_signature(raw))
    def test_button_binding_uses_signed_actor_and_only_new_messages(self):
        a=self.register('alice');b=self.register('bob')
        self.click('/綁定此群','alice','real-group','group')
        self.assertEqual(a.db.execute('SELECT count(*) FROM group_bindings').fetchone()[0],1)
        self.assertEqual(b.db.execute('SELECT count(*) FROM group_bindings').fetchone()[0],0)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)
        self.send('討論重點','colleague','real-group','group',stamp=int(time.time()*1000)+10)
        self.assertEqual(a.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(b.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
    def test_guest_cannot_bind_without_opt_in(self):
        self.click('/綁定此群','outsider','real-group','group')
        self.assertEqual(len(self.elf.public.children),0)
    def test_source_actions_cannot_control_someone_elses_database(self):
        a=self.register('alice');b=self.register('bob');self.click('/綁定此群','alice','g','group')
        self.click('/來源暫停 '+key('g'),'bob')
        self.assertEqual(a.db.execute('SELECT enabled FROM sources').fetchone()[0],1)
        self.click('/來源暫停 '+key('g'),'alice')
        self.assertEqual(a.db.execute('SELECT enabled FROM sources').fetchone()[0],0)
        self.click('/來源移除 '+key('g'),'alice')
        self.assertEqual(a.db.execute('SELECT count(*) FROM sources').fetchone()[0],1)
        self.click('/來源移除確認 '+key('g'),'alice');self.click('/來源移除 '+key('g'),'alice')
        self.assertEqual(a.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)
    def test_date_picker_requires_confirmation_and_is_idempotent(self):
        a=self.register('alice')
        self.click('/新增行程');self.send('專案討論')
        self.assertEqual(wizard(a)['step'],'start')
        start=(datetime.now(TAIPEI)+timedelta(days=2)).replace(hour=10,minute=0)
        self.click('/行程開始',date=start.strftime('%Y-%m-%dT%H:%M'))
        self.click('/行程結束',date=(start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'))
        self.assertEqual(a.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0],0)
        self.click('/確認新增');self.click('/確認新增')
        self.assertEqual(a.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0],1)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0],0)
        self.assertEqual(a.db.execute('SELECT count(*) FROM assistant_jobs').fetchone()[0],0)
    def test_owner_buttons_work_and_non_owner_postback_cannot_inject_command(self):
        self.click('/開始使用','host')
        self.assertEqual(self.calls[-1][1]['messages'][0]['type'],'flex')
        self.register('alice');self.click('/我的ID')
        self.assertNotIn('你的 LINE user ID',json.dumps(self.calls[-1][1],ensure_ascii=False))
    def test_member_calendar_does_not_use_owner_tokens_or_calendar_id(self):
        a=self.register('alice');b=self.register('bob')
        a.calendar().save({'access_token':'alice-only','refresh_token':'refresh-alice','expires_at':time.time()+3600})
        b.calendar().save({'access_token':'bob-only','refresh_token':'refresh-bob','expires_at':time.time()+3600})
        self.assertEqual(a.calendar().access_token(),'alice-only');self.assertEqual(b.calendar().access_token(),'bob-only')
        with patch.dict(os.environ,{'GOOGLE_CALENDAR_ID':'owner-private'}):
            self.assertEqual(a.calendar().calendar_path(),'/calendar/v3/calendars/primary')
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM calendar_tokens').fetchone()[0],0)
    def test_calendar_disconnect_removes_only_own_grant(self):
        a=self.register('alice');b=self.register('bob')
        a.calendar().save({'refresh_token':'a'});b.calendar().save({'refresh_token':'b'})
        self.click('/解除日曆')
        self.assertFalse(a.calendar().status()['connected']);self.assertTrue(b.calendar().status()['connected'])

if __name__=='__main__':unittest.main()

class MemberOAuthTests(unittest.TestCase):
    setUp=fixtures.PublicGroupsTests.setUp
    tearDown=fixtures.PublicGroupsTests.tearDown
    send=fixtures.PublicGroupsTests.send
    child=fixtures.PublicGroupsTests.child
    register=fixtures.PublicGroupsTests.register
    def test_ticket_is_scoped_expiring_and_revoked_with_account(self):
        from member_calendar import link
        from member_oauth import validate
        from urllib.parse import urlsplit,parse_qs
        a=self.register('alice');b=self.register('bob')
        with patch('member_calendar.web_client',return_value={}),patch.dict(os.environ,{'WEBHOOK_URL':'https://synthetic-demo.trycloudflare.com','ELF_DB':str(Path(self.temp.name)/'owner.sqlite3')}):
            ticket=parse_qs(urlsplit(link(a)).fragment)['ticket'][0];a.db.commit()
            self.assertEqual(validate(ticket)[0],a.member_key)
            with self.assertRaises(ValueError):validate(b.member_key+ticket[64:])
            self.elf.public.forget(a.member_key);self.elf.db.commit()
            with self.assertRaises(ValueError):validate(ticket)
    def test_callback_requires_browser_session_and_saves_only_its_tenant(self):
        import member_oauth as oauth
        from types import SimpleNamespace
        a=self.register('alice');b=self.register('bob')
        a.db.execute('CREATE TABLE calendar_tickets(nonce TEXT PRIMARY KEY,expires REAL)')
        a.db.execute('INSERT INTO calendar_tickets VALUES(?,?)',('one-use',time.time()+600));a.db.commit()
        oauth.sessions['browser']={'tenant':a.member_key,'nonce':'one-use','expires':time.time()+600,'phase':'google','state':'expected','verifier':'pkce'}
        pages=[]
        h=SimpleNamespace(path='/calendar/callback?state=wrong&code=fake',headers={'Cookie':'elf_cal_session=browser'},page=lambda text,status=200:pages.append(status))
        with patch.dict(os.environ,{'ELF_DB':str(Path(self.temp.name)/'owner.sqlite3')}),patch('member_oauth.web_client',return_value={'client_id':'synthetic.apps.googleusercontent.com','client_secret':'synthetic'}),patch('member_oauth.exchange',return_value={'access_token':'a-only','refresh_token':'a-refresh','scope':' '.join(__import__('calendar_client').SCOPES)}) as exchange:
            oauth.handle(h,'GET');self.assertEqual(pages[-1],400);exchange.assert_not_called()
            h.path='/calendar/callback?state=expected&code=fake';oauth.handle(h,'GET');self.assertEqual(pages[-1],200)
            self.assertEqual(a.calendar().saved()['access_token'],'a-only');self.assertEqual(b.calendar().saved(),{})
            oauth.handle(h,'GET');self.assertEqual(pages[-1],400);self.assertEqual(exchange.call_count,1)
        oauth.sessions.clear()
