import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import test_elf
from background_context import search_notes
from agent_tools import answer_with_context, is_action_request, process, is_advice_followup


class NotesTests(unittest.TestCase):
    def test_retrieval_excludes_private_and_redacts_keys(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'OBSIDIAN_VAULT':folder}):
            root=Path(folder)
            (root/'TRC 競賽.md').write_text('TRC 競賽：機器人整合與團隊分工。\nkey=sk-or-v1-'+'a'*32,encoding='utf-8')
            (root/'私事').mkdir()
            (root/'私事'/'TRC.md').write_text('TRC 秘密',encoding='utf-8')
            (root/'hidden.md').write_text('---\nprivate: true\n---\nTRC 隱私',encoding='utf-8')
            found=search_notes('TRC 競賽')
            self.assertTrue(found['available'])
            self.assertEqual([n['path'] for n in found['notes']],['TRC 競賽.md'])
            self.assertNotIn('sk-or-v1-',found['notes'][0]['excerpt'])

    def test_unavailable_drive_is_explicit(self):
        with patch.dict(os.environ,{'OBSIDIAN_VAULT':'Z:/does-not-exist-work-elf'}):
            self.assertFalse(search_notes('TRC')['available'])

    def test_vague_question_does_not_return_random_notes(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'OBSIDIAN_VAULT':folder}):
            (Path(folder)/'project.md').write_text('機器人',encoding='utf-8')
            self.assertEqual(search_notes('你覺得呢')['notes'],[])


class ContextTests(unittest.TestCase):
    setUp=test_elf.ElfTests.setUp
    tearDown=test_elf.ElfTests.tearDown
    event=test_elf.ElfTests.event
    ingest=test_elf.ElfTests.ingest
    enable=test_elf.ElfTests.enable

    def seed_context(self):
        self.enable()
        self.ingest(self.event('TRC 競賽需要組隊準備機器人'))
        self.elf.db.execute('UPDATE messages SET seen=1')
        self.ingest(self.event('你覺得呢？'))
        self.elf.db.commit()

    def test_digest_includes_seen_context_but_cannot_reemit_it(self):
        self.seed_context()
        with patch('background_context.search_notes',return_value={'available':True,'notes':[]}):
            prepared=self.elf.prepare()
        data=json.loads(prepared['prompt'])
        self.assertEqual(len(data['messages']),1)
        self.assertIn('TRC',data['recent_context'][0]['text'])
        old=data['recent_context'][0]
        from elf import Problem
        with self.assertRaises(Problem):
            self.elf.deliver(prepared['job_id'],json.dumps({'items':[{'message_id':old['id'],'evidence':'TRC 競賽','summary':'舊待辦','needs_reply':True}]}))

    def test_digest_clarification_is_private_and_not_a_task(self):
        self.seed_context()
        with patch('background_context.search_notes',return_value={'available':True,'notes':[]}):
            prepared=self.elf.prepare()
        mid=json.loads(prepared['prompt'])['messages'][0]['id']
        self.elf.deliver(prepared['job_id'],json.dumps({'items':[],'questions':[{'message_id':mid,'evidence':'你覺得呢','question':'你想評估 TRC 的時間投入還是組隊方式？'}]}))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.assertIn('待釐清',self.calls[-1][1]['messages'][0]['text'])
        self.assertEqual(self.calls[-1][1]['to'],'U-owner')
        saved=json.loads(self.elf.db.execute('SELECT data FROM assistant_context').fetchone()[0])
        self.assertEqual(saved[-1]['kind'],'advice')

    def test_advice_is_grounded_and_followup_persists(self):
        self.seed_context()
        now=int(time.time()*1000)+1000
        self.elf.db.execute("INSERT INTO assistant_jobs(id,text,timestamp,state,created) VALUES('advice','你覺得呢？',?,'pending',?)",(now,time.time()))
        job=self.elf.db.execute("SELECT * FROM assistant_jobs WHERE id='advice'").fetchone()
        self.elf.proxy=Mock(return_value={'choices':[{'message':{'content':json.dumps({'answer':'我的建議是先確認隊友分工。','question':'你想評估 TRC 競賽的時間嗎？','citations':[{'ref':'M1','quote':'TRC 競賽'}]})}}]})
        with patch('background_context.search_notes',return_value={'available':True,'notes':[]}):
            process(self.elf,job)
        context=json.loads(self.elf.db.execute('SELECT data FROM assistant_context').fetchone()[0])
        self.assertTrue(is_advice_followup('對，我只有週末有空',context))
        self.assertFalse(is_advice_followup('幫我新增行程',context))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0],0)

    def test_fabricated_note_citation_is_rejected(self):
        self.seed_context()
        self.elf.proxy=Mock(return_value={'choices':[{'message':{'content':json.dumps({'answer':'報名免費','question':'','citations':[{'ref':'N1','quote':'免費報名'}]})}}]})
        with patch('background_context.search_notes',return_value={'available':True,'notes':[{'path':'TRC.md','excerpt':'TRC 需要組隊'}]}):
            with self.assertRaises(ValueError):
                answer_with_context(self.elf,{'text':'你覺得呢？','timestamp':int(time.time()*1000)+1000})

    def test_calendar_suggestion_does_not_get_routed_as_general_advice(self):
        self.assertFalse(is_action_request('明天下午三點到四點建議開文案會議'))

    def test_free_compatibility_retry_does_not_allow_paid_route(self):
        from elf import Problem
        self.elf.send=Mock(side_effect=[Problem(502,'Upstream HTTP 400; no paid fallback was attempted.'),
                                      {'choices':[{'message':{'content':'{}'}}],'usage':{'cost':0}}])
        self.elf.proxy({'model':'openrouter/free','response_format':{'type':'json_object'},'messages':[]})
        self.assertEqual(self.elf.send.call_count,2)
        retry=self.elf.send.call_args_list[1].args[1]
        self.assertEqual(retry['model'],'openrouter/free')
        self.assertEqual(retry['provider']['max_price'],{'prompt':0,'completion':0})
        self.assertNotIn('response_format',retry)

    def test_unauthorized_error_is_not_retried(self):
        from elf import Problem
        self.elf.send=Mock(side_effect=Problem(502,'Upstream HTTP 401; no paid fallback was attempted.'))
        with self.assertRaises(Problem):
            self.elf.proxy({'model':'openrouter/free','response_format':{'type':'json_object'},'messages':[]})
        self.assertEqual(self.elf.send.call_count,1)

    def test_server_pin_rejects_paid_model(self):
        from elf import Problem
        with patch.dict(os.environ,{'OPENROUTER_FREE_MODEL':'google/paid-model'}):
            with self.assertRaises(Problem):
                self.elf.proxy({'model':'openrouter/free','messages':[]})

    def test_free_server_pin_preserves_zero_price_limit(self):
        with patch.dict(os.environ,{'OPENROUTER_FREE_MODEL':'google/gemma-4-31b-it:free'}):
            self.elf.proxy({'model':'openrouter/free','messages':[]})
        sent=self.calls[-1][1]
        self.assertEqual(sent['model'],'google/gemma-4-31b-it:free')
        self.assertEqual(sent['provider']['max_price'],{'prompt':0,'completion':0})

    def test_source_index_quotes_actual_note(self):
        self.seed_context()
        self.elf.proxy=Mock(return_value={'choices':[{'message':{'content':json.dumps({'answer':'我的建議是先確認分工。','question':'','citations':[{'ref':'N1','quote_index':0}]})}}]})
        with patch('background_context.search_notes',return_value={'available':True,'notes':[{'path':'TRC.md','excerpt':'TRC 需要組隊\n分工待確認'}]}):
            reply=answer_with_context(self.elf,{'text':'你覺得呢？','timestamp':int(time.time()*1000)+1000})
        self.assertIn('TRC.md：TRC 需要組隊',reply)

    def test_fabricated_source_index_is_rejected(self):
        self.seed_context()
        self.elf.proxy=Mock(return_value={'choices':[{'message':{'content':json.dumps({'answer':'已完成','question':'','citations':[{'ref':'N1','quote_index':99}]})}}]})
        with patch('background_context.search_notes',return_value={'available':True,'notes':[{'path':'TRC.md','excerpt':'TRC 需要組隊'}]}):
            with self.assertRaises(ValueError):
                answer_with_context(self.elf,{'text':'你覺得呢？','timestamp':int(time.time()*1000)+1000})

    def test_answering_advice_cannot_confirm_unrelated_calendar(self):
        from natural_assistant import confirm
        self.elf.db.execute('INSERT INTO assistant_context VALUES(1,?,?)',(json.dumps([{'kind':'advice','text':'你指 TRC 嗎？'}]),time.time()))
        self.elf.db.execute("INSERT INTO proposals VALUES('old','message','{}','pending',?)",(time.time(),))
        self.assertIsNone(confirm(self.elf,'確認','new-message'))
        self.assertEqual(self.elf.db.execute("SELECT state FROM proposals WHERE id='old'").fetchone()[0],'pending')
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
