import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from elf import Elf,Problem
from public_groups import base_signature,event_body


class PublicGroupsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PUBLIC_GROUPS_ENABLED':'1','PUBLIC_MAX_USERS':'50',
            'PUBLIC_USER_DAILY_CALLS':'30','PUBLIC_GLOBAL_DAILY_CALLS':'900',
            'LINE_OWNER_USER_ID':'host','LINE_BOT_USER_ID':'bot',
            'LINE_CHANNEL_SECRET':'synthetic-secret','INTERNAL_TOKEN':'synthetic-internal',
            'LINE_CHANNEL_ACCESS_TOKEN':'synthetic-token','LINE_TOKEN_EXPIRES_AT':'0',
            'OPENROUTER_API_KEY':'synthetic-free-key','OPENROUTER_FREE_MODEL':'openrouter/free',
            'OBSIDIAN_VAULT':'private-vault-must-never-be-searched'})
        self.env.start()
        self.membership=patch('public_groups.member_exists',return_value=True)
        self.membership.start()
        self.calls=[]
        def sender(url,body,headers):
            self.calls.append((url,body))
            return {'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'usage':{'cost':0}}
        self.elf=Elf(str(Path(self.temp.name)/'owner.sqlite3'),sender)
        self.count=0

    def tearDown(self):
        self.elf.public.close()
        self.elf.db.close()
        self.membership.stop()
        self.env.stop()
        self.temp.cleanup()

    def send(self,text='',uid='alice',sid=None,kind='user',etype='message',stamp=None,mid=None):
        self.count+=1
        event={'webhookEventId':'event-'+str(self.count),'type':etype,
               'timestamp':stamp or int(time.time()*1000),'replyToken':'reply-'+str(self.count),
               'source':{'type':kind,'userId':uid},
               'message':{'type':'text','id':mid or 'msg-'+str(self.count),'text':text}}
        if kind in ('group','room'): event['source']['groupId' if kind=='group' else 'roomId']=sid
        raw=event_body(event)
        result=self.elf.ingest(raw,base_signature(raw))
        return event,result

    def child(self,uid):
        member=self.elf.db.execute('SELECT tenant FROM public_members WHERE uid=?',(uid,)).fetchone()
        return self.elf.public.children[member[0]]

    def register(self,uid):
        self.send('/同意並開始',uid)
        return self.child(uid)

    def bind(self,uid,group,name):
        child=self.child(uid)
        code,_=child.command('/綁定群組',uid,'user')
        code=code.split('/綁定群組 ')[1].split()[0]
        self.send('/綁定群組 '+code,uid,group,'group')
        self.send('/工作群組 '+name,uid,group,'group')
        return code

    def message(self,group,text,uid='colleague'):
        return self.send(text,uid,group,'group',stamp=int(time.time()*1000)+10)[0]

    def request(self,uid):
        self.send('/整理',uid)
        return self.elf.prepare()

    @staticmethod
    def output(event,reply=False):
        text=event['message']['text']
        return json.dumps({'topics':[{'topic':'工作討論','discussion':text,
            'decisions':'','open_questions':'','suggested_next_steps':['確認討論中的方案'],
            'references':[{'message_id':event['message']['id'],'evidence':text}]}],
            'items':[{'message_id':event['message']['id'],'evidence':text,
                      'summary':text,'needs_reply':reply}]},ensure_ascii=False)

    def test_consent_required_and_general_dm_not_retained(self):
        self.send('私人訊息')
        self.assertEqual(len(self.elf.public.children),0)
        child=self.register('alice')
        self.send('我的個人私訊')
        self.assertEqual(child.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(child.db.execute('SELECT count(*) FROM assistant_jobs').fetchone()[0],0)

    def test_databases_are_separate_and_owner_is_not_a_member(self):
        a=self.register('alice'); b=self.register('bob')
        self.assertNotEqual(a.db.execute('PRAGMA database_list').fetchone()[2],b.db.execute('PRAGMA database_list').fetchone()[2])
        self.assertEqual(a.owner_id,'alice'); self.assertEqual(b.owner_id,'bob')
        self.assertEqual(self.elf.owner_id,'host')

    def test_binding_requires_code_owner_signed_actor(self):
        a=self.register('alice'); self.register('bob')
        result,_=a.command('/綁定群組','alice','user')
        code=result.split('/綁定群組 ')[1].split()[0]
        self.send('/綁定群組 '+code,'bob','group-a','group')
        self.assertFalse(a.db.execute('SELECT 1 FROM sources').fetchone())
        self.assertFalse(self.child('bob').db.execute('SELECT 1 FROM sources').fetchone())
        self.send('/綁定群組 '+code,'alice','group-a','group')
        self.assertTrue(a.db.execute('SELECT 1 FROM sources').fetchone())

    def test_unbound_group_and_past_delivery_not_collected(self):
        a=self.register('alice')
        self.message('group-a','綁定前的工作內容')
        self.bind('alice','group-a','A 組')
        self.send('延遲送達的舊工作訊息','colleague','group-a','group',stamp=int(time.time()*1000)-60000)
        self.assertEqual(a.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_two_users_and_owner_prompts_are_isolated(self):
        a=self.register('alice'); b=self.register('bob')
        self.bind('alice','group-a','A 組'); self.bind('bob','group-b','B 組')
        ea=self.message('group-a','甲組討論校園活動文案')
        self.message('group-b','乙組討論專題海報')
        self.send('/工作群組 主人群','host','group-host','group')
        self.message('group-host','主人獨有的工作資料')
        # Owner jobs retain their existing route; public jobs are requested separately.
        owner=self.elf.prepare()
        self.assertNotIn('甲組',owner['prompt']); self.assertNotIn('乙組',owner['prompt'])
        self.elf.deliver(owner['job_id'],json.dumps({'items':[]}))
        public=self.request('alice')
        self.assertIn('甲組',public['prompt']); self.assertNotIn('乙組',public['prompt'])
        self.assertNotIn('主人獨有',public['prompt']); self.assertEqual(json.loads(public['prompt'])['background'],[])
        self.assertFalse(b.db.execute('SELECT 1 FROM messages WHERE source=?',('group-a',)).fetchone())
        self.elf.deliver(public['job_id'],self.output(ea,True))
        pushes=[body for url,body in self.calls if url.endswith('/push')]
        self.assertEqual(pushes[-1]['to'],'alice')
        self.assertNotIn('乙組',pushes[-1]['messages'][0]['text'])
        self.assertEqual(b.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)

    def test_same_group_new_subscriber_cannot_get_older_messages(self):
        a=self.register('alice'); b=self.register('bob')
        self.bind('alice','group-shared','共同組')
        self.message('group-shared','只有甲先綁定時的討論')
        self.bind('bob','group-shared','共同組')
        self.message('group-shared','兩人綁定後的討論')
        self.assertEqual(a.db.execute('SELECT count(*) FROM messages').fetchone()[0],2)
        self.assertEqual(b.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

    def test_member_cannot_call_private_tools_or_run_other_users_commands(self):
        a=self.register('alice'); b=self.register('bob')
        self.bind('alice','group-a','A 組'); self.bind('bob','group-b','B 組')
        for text in ('/行程 今天','/新增行程 明天三點','/來源紀錄','幫我找 Obsidian 筆記'):
            self.send(text,'alice')
        self.send('/停止收集','bob','group-a','group')
        self.assertEqual(a.db.execute('SELECT enabled FROM sources').fetchone()[0],1)
        for child in (a,b):
            self.assertEqual(child.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0],0)
            self.assertEqual(child.db.execute('SELECT count(*) FROM assistant_jobs').fetchone()[0],0)

    def test_private_prefix_excluded_and_injected_foreign_evidence_rejected(self):
        self.register('alice'); self.register('bob')
        self.bind('alice','group-a','A 組'); self.bind('bob','group-b','B 組')
        self.message('group-a','私事：不要分析')
        self.message('group-a','這是公事討論')
        foreign=self.message('group-b','乙的公事討論')
        job=self.request('alice')
        self.assertNotIn('不要分析',job['prompt'])
        with self.assertRaises(Problem): self.elf.deliver(job['job_id'],self.output(foreign))
        self.assertFalse(any(url.endswith('/push') for url,_ in self.calls))

    def test_deletion_invalidates_jobs_and_preserves_other_member(self):
        a=self.register('alice'); b=self.register('bob')
        self.bind('alice','group-a','A 組'); self.bind('bob','group-b','B 組')
        event=self.message('group-a','甲的工作討論')
        self.message('group-b','乙的工作討論')
        job=self.request('alice')
        self.send('/刪除我的資料','alice')
        with self.assertRaises(Problem): self.elf.deliver(job['job_id'],self.output(event))
        self.assertEqual(b.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertFalse(self.elf.db.execute('SELECT 1 FROM public_members WHERE uid=?',('alice',)).fetchone())

    def test_stop_and_unsend_invalidate_only_authorized_member_snapshots(self):
        a=self.register('alice'); self.bind('alice','group-a','A 組')
        event=self.message('group-a','活動文案討論')
        job=self.request('alice')
        unsend={'webhookEventId':'unsend-event','type':'unsend','source':{'type':'group','groupId':'group-a'},'unsend':{'messageId':event['message']['id']}}
        raw=event_body(unsend); self.elf.ingest(raw,base_signature(raw))
        with self.assertRaises(Problem): self.elf.deliver(job['job_id'],self.output(event))
        self.send('/停止收集','alice','group-a','group')
        self.assertFalse(a.db.execute('SELECT 1 FROM sources').fetchone())
        self.assertFalse(a.db.execute('SELECT 1 FROM group_bindings').fetchone())

    def test_manual_only_and_daily_opt_in(self):
        a=self.register('alice'); self.bind('alice','group-a','A 組')
        self.message('group-a','活動文案討論')
        self.assertFalse(self.elf.prepare()['run'])
        self.assertFalse(self.elf.recover()['run'])
        self.send('/每日整理 開啟','alice')
        self.assertEqual(self.elf.db.execute('SELECT automatic FROM public_members').fetchone()[0],1)

    def test_job_lease_prevents_duplicate_ai_work(self):
        self.register('alice'); self.bind('alice','group-a','A 組')
        self.message('group-a','活動文案討論')
        first=self.request('alice'); self.assertTrue(first['run'])
        self.assertFalse(self.request('alice')['run'])
        self.child('alice').db.execute("UPDATE controls SET value=0 WHERE id='public_lease'")
        self.assertEqual(self.elf.prepare()['job_id'],first['job_id'])

    def test_daily_budget_is_real_gateway_calls_and_independent(self):
        self.register('alice'); self.bind('alice','group-a','A 組')
        self.message('group-a','活動文案討論')
        job=self.request('alice')
        body={'model':'openrouter/free','messages':[{'role':'user','content':job['prompt']}]}
        self.elf.public.user_limit=1
        self.elf.proxy(body)
        with self.assertRaises(Problem) as cm: self.elf.proxy(body)
        self.assertEqual(cm.exception.status,429)
        self.assertEqual(self.elf.public.used(self.child('alice').member_key),1)
        self.assertEqual(sum('openrouter.ai' in url for url,_ in self.calls),1)

    def test_revoked_job_cannot_spend_ai_budget(self):
        self.register('alice'); self.bind('alice','group-a','A 組')
        self.message('group-a','活動文案討論')
        job=self.request('alice')
        self.send('/暫停收集','alice','group-a','group')
        with self.assertRaises(Problem):
            self.elf.proxy({'model':'openrouter/free','messages':[{'role':'user','content':job['prompt']}]})

    def test_destination_and_signature_checks_apply_before_onboarding(self):
        raw=json.dumps({'destination':'wrong','events':[{'webhookEventId':'forged','type':'message','source':{'type':'user','userId':'alice'},'message':{'type':'text','text':'/同意並開始'}}]}).encode()
        with self.assertRaises(Problem): self.elf.ingest(raw,base_signature(raw))
        self.assertFalse(self.elf.db.execute('SELECT 1 FROM public_members').fetchone())

    def test_event_redelivery_does_not_duplicate_message(self):
        a=self.register('alice'); self.bind('alice','group-a','A 組')
        event=self.message('group-a','活動文案討論')
        raw=event_body(event); self.elf.ingest(raw,base_signature(raw))
        self.assertEqual(a.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

    def test_restart_retains_only_each_members_database(self):
        self.register('alice'); self.register('bob')
        self.bind('alice','group-a','A 組'); self.bind('bob','group-b','B 組')
        self.message('group-a','甲組重啟後保留')
        self.message('group-b','乙組重啟後保留')
        path=self.elf.db.execute('PRAGMA database_list').fetchone()[2]
        sender=self.elf.send
        self.elf.public.close(); self.elf.db.close()
        self.elf=Elf(path,sender)
        self.assertEqual(self.child('alice').db.execute('SELECT text FROM messages').fetchone()[0],'甲組重啟後保留')
        self.assertEqual(self.child('bob').db.execute('SELECT text FROM messages').fetchone()[0],'乙組重啟後保留')

    def test_same_task_id_cannot_complete_another_users_task(self):
        a=self.register('alice'); b=self.register('bob')
        self.bind('alice','group-a','A 組'); self.bind('bob','group-b','B 組')
        event=self.message('group-b','乙請確認活動文案')
        job=self.request('bob'); self.elf.deliver(job['job_id'],self.output(event,True))
        task=b.db.execute('SELECT id FROM tasks').fetchone()[0]
        self.send('/已處理 '+task,'alice')
        self.assertEqual(b.db.execute('SELECT done FROM tasks').fetchone()[0],0)

    def test_default_daily_runs_once_at_21_only_after_opt_in(self):
        from datetime import datetime
        self.register('alice'); self.bind('alice','group-a','A 組')
        self.message('group-a','活動文案討論')
        self.send('/每日整理 開啟','alice')
        with patch('public_groups.datetime') as clock:
            clock.now.return_value=datetime(2026,10,24,21,0)
            clock.now.return_value=clock.now.return_value.replace(tzinfo=__import__('elf').TAIPEI)
            self.assertTrue(self.elf.public.ready())
            first=self.elf.db.execute('SELECT last_daily FROM public_members').fetchone()[0]
            self.assertEqual(first,'2026-10-24')
            self.elf.public.ready()
            self.assertEqual(self.elf.db.execute('SELECT last_daily FROM public_members').fetchone()[0],first)

    def test_forged_group_scope_and_global_daily_cap(self):
        self.register('alice'); self.bind('alice','group-a','A 組')
        self.message('group-a','活動文案討論')
        job=self.request('alice')
        data=json.loads(job['prompt']); data['public_job_id']='mt:missing:invented'
        with self.assertRaises(Problem):
            self.elf.proxy({'model':'openrouter/free','messages':[{'role':'user','content':json.dumps(data)}]})
        self.elf.public.public_limit=1
        body={'model':'openrouter/free','messages':[{'role':'user','content':job['prompt']}]}
        self.elf.proxy(body)
        with self.assertRaises(Problem): self.elf.proxy(body)

    def test_leaving_group_purges_all_subscribers(self):
        a=self.register('alice'); b=self.register('bob')
        self.bind('alice','group-shared','同組'); self.bind('bob','group-shared','同組')
        self.message('group-shared','活動文案討論')
        self.send('',None,'group-shared','group',etype='leave')
        self.assertEqual(a.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(b.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_member_left_event_revokes_only_departing_member(self):
        a=self.register('alice'); b=self.register('bob')
        self.bind('alice','group-shared','同組'); self.bind('bob','group-shared','同組')
        self.message('group-shared','離群前討論')
        event={'webhookEventId':'member-left-1','type':'memberLeft',
               'source':{'type':'group','groupId':'group-shared'},
               'left':{'members':[{'type':'user','userId':'alice'}]}}
        raw=event_body(event);self.elf.ingest(raw,base_signature(raw))
        self.message('group-shared','離群後只有乙可讀')
        self.assertEqual(a.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(b.db.execute('SELECT count(*) FROM messages').fetchone()[0],2)

    def test_membership_checked_before_ai_and_before_delivery(self):
        self.register('alice'); self.bind('alice','group-a','A 組')
        event=self.message('group-a','活動文案討論')
        job=self.request('alice')
        with patch('public_groups.member_exists',return_value=False):
            with self.assertRaises(Problem): self.elf.deliver(job['job_id'],self.output(event))
        self.assertFalse(any(url.endswith('/push') for url,_ in self.calls))
        self.assertEqual(self.child('alice').db.execute('SELECT count(*) FROM messages').fetchone()[0],0)


if __name__=='__main__': unittest.main()
