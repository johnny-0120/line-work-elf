import json
import time
import unittest
from unittest.mock import Mock
from datetime import datetime
import test_elf
from natural_assistant import process_one, validate
from calendar_actions import TZ


def answer(**changes):
    data = dict(category='work',intent='note',title='活動文案',summary='明天交活動文案',
                evidence='明天交活動文案',needs_reply=False,starts_at=None,ends_at=None,period=None,question='')
    data.update(changes)
    return data


class NaturalTests(unittest.TestCase):
    setUp = test_elf.ElfTests.setUp
    tearDown = test_elf.ElfTests.tearDown
    event = test_elf.ElfTests.event
    ingest = test_elf.ElfTests.ingest

    def ai(self, **changes):
        data = answer(**changes)
        self.elf.proxy = Mock(return_value={'choices':[{'message':{'content':json.dumps(data,ensure_ascii=False)}}]})

    def private(self, text):
        return self.ingest(self.event(text, source='user'))

    def count(self, table):
        return self.elf.db.execute('SELECT count(*) FROM '+table).fetchone()[0]

    def test_plain_owner_dm_queued_nonowner_and_group_not_queued(self):
        self.private('明天交活動文案')
        self.ingest(self.event('明天交活動文案',uid='other',source='user'))
        self.ingest(self.event('明天交活動文案'))
        self.assertEqual(self.count('assistant_jobs'),1)
        self.assertEqual(self.count('messages'),0)

    def test_explicit_private_never_sent_to_ai(self):
        self.private('私事：今天看醫生')
        self.private('#private 今天看醫生')
        self.assertEqual(self.count('assistant_jobs'),0)

    def test_work_without_prefix_enters_digest(self):
        self.private('明天交活動文案')
        self.ai()
        process_one(self.elf)
        self.assertEqual(self.count('messages'),1)
        self.assertTrue(self.elf.prepare()['run'])

    def test_request_digest_before_ai_finishes_is_preserved(self):
        self.private('明天交活動文案')
        self.private('/整理')
        self.assertFalse(self.elf.prepare()['run'])
        self.ai()
        process_one(self.elf)
        self.assertTrue(self.elf.recover()['run'])

    def test_old_work_prefix_cannot_bypass_private_classification(self):
        self.private('公事：今天跟家人吃飯')
        self.ai(category='personal',intent='chat',title='',summary='',evidence='跟家人吃飯')
        process_one(self.elf)
        self.assertEqual(self.count('messages'),0)

    def test_private_raw_erased_and_no_work_summary(self):
        self.private('今天跟家人吃飯')
        self.ai(category='personal',intent='chat',title='',summary='',evidence='跟家人吃飯')
        process_one(self.elf)
        row = self.elf.db.execute('SELECT text,analysis FROM assistant_jobs').fetchone()
        self.assertEqual(tuple(row), ('','{}'))
        self.assertEqual(self.count('messages'),0)

    def event_answer(self, text, intent='calendar_create'):
        from datetime import timedelta
        day = (datetime.now(TZ)+timedelta(days=1)).strftime('%Y-%m-%d')
        self.ai(intent=intent,title='活動會議',summary='活動會議',evidence=text,
                starts_at=day+'T15:00:00+08:00',ends_at=day+'T16:00:00+08:00')

    def test_explicit_complete_request_queues_calendar_once(self):
        text = '幫我排明天下午三點到四點的活動會議'
        self.private(text)
        self.event_answer(text)
        process_one(self.elf)
        process_one(self.elf)
        self.assertEqual(self.count('calendar_jobs'),1)
        self.assertEqual(self.count('proposals'),0)

    def test_mention_only_requires_confirmation(self):
        text = '明天下午三點到四點有活動會議'
        self.private(text)
        self.event_answer(text)  # Even a model's incorrect create label cannot write.
        process_one(self.elf)
        self.assertEqual(self.count('calendar_jobs'),0)
        self.assertEqual(self.count('proposals'),1)
        self.private('確認')
        self.assertEqual(self.count('calendar_jobs'),1)
        self.private('確認')
        self.assertEqual(self.count('calendar_jobs'),1)

    def test_tentative_direct_request_does_not_write(self):
        text = '可能幫我排明天下午三點到四點的活動會議'
        self.private(text)
        self.event_answer(text)
        process_one(self.elf)
        self.assertEqual(self.count('calendar_jobs'),0)
        self.assertEqual(self.count('proposals'),1)

    def test_location_and_notes_are_saved_for_calendar_execution(self):
        text = '幫我排明天下午三點到四點在圖書館的活動會議，討論宣傳文案'
        self.private(text)
        self.event_answer(text)
        data = json.loads(self.elf.proxy.return_value['choices'][0]['message']['content'])
        data.update(location='圖書館',details='討論宣傳文案')
        self.elf.proxy.return_value['choices'][0]['message']['content'] = json.dumps(data)
        process_one(self.elf)
        detail = json.loads(self.elf.db.execute('SELECT data FROM calendar_details').fetchone()[0])
        self.assertEqual(detail['location'],'圖書館')
        self.assertIn('討論宣傳文案',detail['description'])

    def test_proposal_can_be_corrected_in_followup(self):
        from datetime import timedelta
        first = '明天下午三點到四點有活動會議'
        self.private(first)
        self.event_answer(first,intent='calendar_suggest')
        process_one(self.elf)
        self.private('改成下午五點到六點')
        day = (datetime.now(TZ)+timedelta(days=1)).strftime('%Y-%m-%d')
        self.ai(intent='calendar_suggest',title='活動會議',summary='活動會議',evidence='改成下午五點到六點',starts_at=day+'T17:00:00+08:00',ends_at=day+'T18:00:00+08:00')
        process_one(self.elf)
        self.assertEqual(self.elf.db.execute("SELECT count(*) FROM proposals WHERE state='pending'").fetchone()[0],1)
        self.private('確認')
        command = self.elf.db.execute('SELECT command FROM calendar_jobs').fetchone()[0]
        self.assertIn('17:00到18:00', command)

    def test_followup_duration_uses_persisted_context(self):
        first = '幫我排明天下午三點的活動會議'
        self.private(first)
        self.ai(intent='clarify',title='活動會議',summary='活動會議',evidence=first,question='要開多久？')
        process_one(self.elf)
        self.assertEqual(self.count('assistant_context'),1)
        self.private('一個小時')
        self.event_answer('一個小時')
        process_one(self.elf)
        self.assertEqual(self.count('calendar_jobs'),1)
        prompt = json.loads(self.elf.proxy.call_args[0][0]['messages'][1]['content'])
        self.assertEqual(prompt['context'][0]['text'],first)
        self.assertEqual(self.count('assistant_context'),0)

    def test_hallucinated_complete_times_become_question(self):
        text = '幫我排個活動會議'
        self.private(text)
        self.event_answer(text)
        process_one(self.elf)
        self.assertEqual(self.count('calendar_jobs'),0)
        self.assertEqual(self.count('assistant_context'),1)

    def test_model_failure_preserves_input_no_tools(self):
        self.private('明天交活動文案')
        self.elf.proxy = Mock(side_effect=RuntimeError('busy'))
        process_one(self.elf)
        row = self.elf.db.execute('SELECT text,state,attempts FROM assistant_jobs').fetchone()
        self.assertEqual(tuple(row),('明天交活動文案','pending',1))
        self.assertEqual(self.count('calendar_jobs'),0)

    def test_ungrounded_evidence_rejected(self):
        with self.assertRaises(ValueError):
            validate(answer(evidence='完全沒說過'),['明天交活動文案'],datetime.now(TZ))

    def test_pause_resume_and_cancel(self):
        self.private('/暫停私訊辨識')
        self.private('明天交活動文案')
        self.assertEqual(self.count('assistant_jobs'),0)
        self.private('/恢復私訊辨識')
        text = '明天下午三點到四點有活動會議'
        self.private(text)
        self.event_answer(text,intent='calendar_suggest')
        process_one(self.elf)
        self.private('取消')
        self.assertEqual(self.count('calendar_jobs'),0)
        self.assertEqual(self.elf.db.execute('SELECT state FROM proposals').fetchone()[0],'cancelled')

    def test_unsend_while_inference_cannot_create_event(self):
        text = '幫我排明天下午三點到四點的活動會議'
        self.private(text)
        self.event_answer(text)
        value = self.elf.proxy.return_value
        def withdraw(*args):
            self.elf.db.execute('DELETE FROM assistant_jobs')
            self.elf.db.commit()
            return value
        self.elf.proxy.side_effect = withdraw
        process_one(self.elf)
        self.assertEqual(self.count('calendar_jobs'),0)


if __name__ == '__main__':
    unittest.main()
