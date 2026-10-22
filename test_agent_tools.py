import json
import time
import unittest
from unittest.mock import Mock,patch
import test_elf
from agent_tools import execute,run,is_action_request,refresh_groups
from natural_assistant import process_one


def calls(*specs):
    return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[
        {'id':'call-'+str(i),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}
        for i,(name,args) in enumerate(specs)]}}]}


class AgentTests(unittest.TestCase):
    setUp = test_elf.ElfTests.setUp
    tearDown = test_elf.ElfTests.tearDown
    event = test_elf.ElfTests.event
    ingest = test_elf.ElfTests.ingest

    def job(self,text='幫我開啟 NUVA 工作群組'):
        self.ingest(self.event(text,source='user'))
        return self.elf.db.execute('SELECT * FROM assistant_jobs ORDER BY rowid DESC LIMIT 1').fetchone()

    def group(self,sid='G-work',name='NUVA',owner=True,bound=True):
        self.ingest(self.event('群組的新訊息',uid='U-owner' if owner else 'other',sid=sid))
        self.elf.db.execute('UPDATE known_groups SET name=?,refreshed=? WHERE id=?',(name,time.time(),sid))
        if owner and bound:
            self.elf.db.execute('INSERT OR REPLACE INTO group_bindings VALUES(?,?,?)',(sid,'test',time.time()))
        self.elf.db.commit()

    def test_screenshot_request_routes_to_tools_not_note(self):
        text='幫我開那個徐專洧和公事小精靈的工作群組'
        self.assertTrue(is_action_request(text))
        self.group(name='徐專洧, 公事小精靈')
        self.job(text)
        self.elf.proxy=Mock(side_effect=[calls(('list_groups',{})),calls(('enable_group',{'query':'徐專洧和公事小精靈'})),{'choices':[{'message':{'content':'done'}}]}])
        process_one(self.elf)
        self.assertEqual(self.elf.db.execute('SELECT enabled FROM sources WHERE id=?',('G-work',)).fetchone()[0],1)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.elf.proxy.call_count,3)
        third = self.elf.proxy.call_args_list[2][0][0]['messages']
        self.assertTrue(any(m['role']=='tool' for m in third))

    def test_discovery_does_not_store_unapproved_chat(self):
        self.group()
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM known_groups').fetchone()[0],1)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_name_refreshed_via_official_line_api(self):
        self.ingest(self.event('hi'))
        getter=Mock(return_value={'groupName':'正式工作組'})
        refresh_groups(self.elf,getter)
        self.assertEqual(self.elf.db.execute('SELECT name FROM known_groups').fetchone()[0],'正式工作組')
        getter.assert_called_once_with('GET','/v2/bot/group/G-work/summary')

    def test_group_without_owner_evidence_cannot_enable(self):
        self.group(owner=False)
        result=execute(self.elf,self.job(),'enable_group',{'query':'NUVA'})
        self.assertFalse(result['ok'])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_observed_but_unbound_group_cannot_enable(self):
        self.group(bound=False)
        result=execute(self.elf,self.job(),'enable_group',{'query':'NUVA'})
        self.assertFalse(result['ok'])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_status_question_cannot_enable_group(self):
        self.group()
        job=self.job('幫我看看哪個群組已經開啟了')
        result=execute(self.elf,job,'enable_group',{'query':'NUVA'})
        self.assertFalse(result['ok'])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_only_discovered_group_does_not_override_named_request(self):
        self.group(name='其他工作組')
        job=self.job('請開啟徐專洧和公事小精靈的工作群組')
        self.assertFalse(execute(self.elf,job,'enable_group',{'query':''})['ok'])
        self.assertFalse(execute(self.elf,job,'enable_group',{'query':'其他工作組'})['ok'])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_ambiguous_group_does_not_enable(self):
        self.group('G1','NUVA 文案')
        self.group('G2','NUVA 活動')
        result=execute(self.elf,self.job(),'enable_group',{'query':'NUVA'})
        self.assertFalse(result['ok'])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_tool_argument_cannot_trigger_arbitrary_http_or_group_send(self):
        job=self.job()
        self.assertFalse(execute(self.elf,job,'http_request',{'url':'https://example.com'})['ok'])
        self.assertFalse(execute(self.elf,job,'enable_group',{'query':'NUVA','group_id':'another'})['ok'])

    def test_model_success_without_tools_is_not_reported_as_success(self):
        job=self.job()
        self.elf.proxy=Mock(return_value={'choices':[{'message':{'content':'已啟用全部群組'}}]})
        reply=run(self.elf,job)
        self.assertIn('沒有變更',reply)
        self.assertNotIn('已啟用全部群組',reply)

    def test_partial_failure_reports_completed_tool_receipts(self):
        self.group()
        job=self.job()
        self.elf.proxy=Mock(side_effect=[calls(('enable_group',{'query':'NUVA'})),RuntimeError('busy')])
        from agent_tools import process
        process(self.elf,job)
        response=self.elf.db.execute('SELECT text FROM outbox').fetchone()[0]
        self.assertIn('已啟用',response)
        self.assertIn('後續模型步驟暫時未完成',response)

    def test_repeated_write_is_idempotent(self):
        self.group()
        job=self.job()
        first=execute(self.elf,job,'enable_group',{'query':'NUVA'})
        second=execute(self.elf,job,'enable_group',{'query':'NUVA'})
        self.assertEqual(first,second)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM agent_actions').fetchone()[0],1)

    def test_empty_no_argument_tool_string_is_supported(self):
        self.group()
        job=self.job('幫我查看群組')
        first=calls(('list_groups',{}))
        first['choices'][0]['message']['tool_calls'][0]['function']['arguments']=''
        self.elf.proxy=Mock(side_effect=[first,{'choices':[{'message':{'content':'done'}}]}])
        self.assertIn('NUVA',run(self.elf,job))

    def test_waiting_group_request_resumes_on_first_owner_group_event(self):
        job=self.job()
        self.elf.proxy=Mock(side_effect=[calls(('list_groups',{})),{'choices':[{'message':{'content':'not found'}}]}])
        from agent_tools import process
        process(self.elf,job)
        self.assertEqual(self.elf.db.execute('SELECT state FROM assistant_jobs').fetchone()[0],'waiting_group')
        self.group()
        row=self.elf.db.execute('SELECT state,attempts FROM assistant_jobs').fetchone()
        self.assertEqual(tuple(row),('pending',1))

    def test_read_search_only_enabled_sources(self):
        self.group()
        self.ingest(self.event('/工作群組 NUVA'))
        self.ingest(self.event('活動文案週五交件',uid='other'))
        job=self.job('幫我查已收集的活動文案')
        result=execute(self.elf,job,'read_messages',{'query':'文案','group':'NUVA'})
        self.assertIn('活動文案週五交件',result['message'])
        self.elf.db.execute("UPDATE sources SET enabled=0")
        self.assertNotIn('活動文案週五交件',execute(self.elf,job,'read_messages',{'query':'文案','group':'NUVA'})['message'])

    def test_complete_task_needs_explicit_request_and_matching_topic(self):
        self.elf.db.execute("INSERT INTO tasks VALUES('t1','m1','G','活動文案確認',0,?)",(time.time(),))
        job=self.job('幫我看待辦')
        self.assertFalse(execute(self.elf,job,'complete_task',{'task_id':'t1'})['ok'])
        job=self.job('活動文案已完成')
        self.assertTrue(execute(self.elf,job,'complete_task',{'task_id':'t1'})['ok'])
