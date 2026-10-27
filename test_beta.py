import json,time,unittest,tempfile,os
from pathlib import Path
from unittest.mock import Mock,patch
import test_public_groups as fixtures
from calendar_client import CalendarError
from dm_companion import process_one as analyze
from member_assistant import process_one as assistant
from interaction import handle,render,wizard

class BetaTests(unittest.TestCase):
    setUp=fixtures.PublicGroupsTests.setUp
    tearDown=fixtures.PublicGroupsTests.tearDown
    send=fixtures.PublicGroupsTests.send
    child=fixtures.PublicGroupsTests.child
    register=fixtures.PublicGroupsTests.register
    def test_empty_calendar_response_cannot_allow_a_write(self):
        a=self.register('alice');a.calendar().api=Mock(return_value={})
        cal=a.calendar();cal.api=Mock(return_value={'calendars':{}})
        with self.assertRaises(CalendarError):cal.busy('start','end')
    def test_refresh_cannot_resurrect_removed_or_replaced_grant(self):
        a=self.register('alice');cal=a.calendar();original={'refresh_token':'old'}
        cal.save(original);a.db.execute('DELETE FROM calendar_tokens');a.db.commit()
        with self.assertRaises(CalendarError):cal.replace_if_current(original,{'refresh_token':'old','access_token':'fresh'})
        self.assertEqual(cal.saved(),{})
        cal.save({'refresh_token':'new'})
        with self.assertRaises(CalendarError):cal.replace_if_current(original,{'refresh_token':'old','access_token':'fresh'})
        self.assertEqual(cal.saved()['refresh_token'],'new')
    def test_aggregate_length_limit_keeps_the_existing_buffer(self):
        a=self.register('alice');self.send('/整理雙人對話','alice')
        self.send('甲'*6000,'alice');self.send('乙'*6000,'alice')
        state=wizard(a);self.assertEqual(len(state['parts']),1)
        self.assertEqual(state['parts'][0]['text'],'甲'*6000)
        self.send('/完成對話輸入','alice')
        self.assertEqual(a.db.execute('SELECT text FROM conversation_imports').fetchone()[0],'甲'*6000)
    def test_failed_analysis_can_retry_without_duplicate_model_calls(self):
        a=self.register('alice');self.send('/整理雙人對話','alice');self.send('甲：確認文案','alice');self.send('/完成對話輸入','alice')
        a.proxy=Mock(side_effect=ValueError());analyze(a)
        row=a.db.execute('SELECT id,state FROM conversation_imports').fetchone();self.assertEqual(row['state'],'failed')
        self.send('/重試對話 '+row['id'],'alice')
        a.proxy=Mock(return_value={'choices':[{'message':{'content':json.dumps({'summary':'確認文案','actions':[]})}}]})
        analyze(a);analyze(a)
        self.assertEqual(a.proxy.call_count,1)
    def test_long_notes_are_displayed_without_invalid_flex_text_lengths(self):
        a=self.register('alice');a.db.execute('INSERT INTO personal_notes VALUES(?,?,?)',('a'*16,'文'*4000,time.time()))
        cards=render(a,{'source':{'type':'user'},'message':{'text':'/我的記錄'}},'')
        def check(node):
            if isinstance(node,dict):
                if node.get('type')=='text':self.assertLessEqual(len(node['text']),1800)
                for v in node.values():check(v)
            elif isinstance(node,list):
                for v in node:check(v)
        check(cards)
    def test_every_member_must_opt_in_before_dm_inference(self):
        a=self.register('alice');b=self.register('bob');self.send('你好','alice')
        self.assertEqual(a.db.execute('SELECT count(*) FROM assistant_jobs').fetchone()[0],0)
        self.send('/啟用私訊助理','alice');self.send('你好，今天有點累','alice')
        a.proxy=Mock(return_value={'choices':[{'message':{'content':json.dumps({'action':'chat','reply':'今天辛苦了，想聊聊發生什麼事嗎？'},ensure_ascii=False)}}]})
        assistant(a)
        self.assertEqual(a.db.execute("SELECT state FROM assistant_jobs").fetchone()[0],'done')
        self.assertEqual(a.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(b.db.execute('SELECT count(*) FROM assistant_context').fetchone()[0],0)
        sent=json.loads(a.proxy.call_args[0][0]['messages'][-1]['content'])
        self.assertEqual(sent['public_job_id'].split(':')[1],a.member_key)
    def test_member_agent_reads_only_the_current_members_notes(self):
        a=self.register('alice');b=self.register('bob')
        b.db.execute('INSERT INTO personal_notes VALUES(?,?,?)',('b'*16,'別人的秘密',time.time()))
        self.send('/啟用私訊助理','alice');self.send('查我的記錄','alice')
        a.proxy=Mock(return_value={'choices':[{'message':{'content':json.dumps({'action':'notes','reply':'查詢記錄'})}}]});assistant(a)
        result=a.db.execute("SELECT messages_json FROM outbox WHERE id LIKE 'member-dm:%'").fetchone()[0]
        self.assertNotIn('別人的秘密',result);self.assertIn('還沒有保存',result)
    def test_pause_during_inference_prevents_action_and_clears_context(self):
        a=self.register('alice');self.send('/啟用私訊助理','alice');self.send('看看今天行程','alice')
        def response(_):
            self.send('/暫停私訊助理','alice')
            return {'choices':[{'message':{'content':json.dumps({'action':'calendar_today','reply':'查詢日曆'})}}]}
        a.proxy=Mock(side_effect=response);assistant(a)
        self.assertEqual(a.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0],0)
        self.assertEqual(a.db.execute('SELECT text FROM assistant_jobs').fetchone()[0],'')
    def test_private_prefix_never_reaches_member_ai(self):
        a=self.register('alice');self.send('/啟用私訊助理','alice');self.send('私事：不分析這段','alice')
        self.assertEqual(a.db.execute('SELECT count(*) FROM assistant_jobs').fetchone()[0],0)
    def test_payment_is_disabled_for_any_requested_plan(self):
        from service_plans import current_plan
        for supplied in ('paid','premium',None,'malicious'):
            p=current_plan(supplied);self.assertEqual(p.code,'free');self.assertEqual(p.price_minor,0);self.assertFalse(p.payments_enabled)
    def test_tunnel_url_reads_current_config_instead_of_stale_environment(self):
        from live_settings import public_base
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'config';p.write_text('WEBHOOK_URL=https://new-tunnel.trycloudflare.com\n')
            with patch.dict(os.environ,{'WEBHOOK_URL':'https://old-tunnel.trycloudflare.com','ELF_CONFIG_FILE':str(p)}):
                self.assertEqual(public_base(),'https://new-tunnel.trycloudflare.com')
