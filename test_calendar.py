import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from calendar_actions import parse_event, execute, TZ
from calendar_client import GoogleCalendar, CalendarError
import test_elf


class CalendarTests(unittest.TestCase):
    now = datetime(2026, 10, 16, 18, tzinfo=TZ)

    def test_chinese_and_absolute_dates(self):
        e = parse_event('明天下午三點到四點 活動會議', self.now)
        self.assertEqual(e['start'], '2026-10-17T15:00:00+08:00')
        self.assertEqual(e['end'], '2026-10-17T16:00:00+08:00')
        self.assertEqual(parse_event('2026-10-20 15:30到16:45 文案會議', self.now)['start'], '2026-10-20T15:30:00+08:00')

    def test_ambiguous_missing_past_invalid_times_blocked(self):
        for text in ('明天三點到四點 會議', '明天下午三點 會議', '下午三點到四點 會議',
                     '今天 15:00到16:00 會議', '明天 25:00到26:00 會議',
                     '明天下午四點到三點 會議', '2026-02-30 15:00到16:00 會議',
                     '明天 15:00到16:00'):
            with self.subTest(text=text), self.assertRaises(CalendarError):
                parse_event(text, self.now)

    def test_conflict_never_inserts(self):
        c = GoogleCalendar()
        c.get_event = Mock(return_value=None)
        c.busy = Mock(return_value=[{'start': 'busy'}])
        c.api = Mock()
        with self.assertRaises(CalendarError):
            c.create_event('abc', 'job', 'work', 'start', 'end')
        c.api.assert_not_called()

    def test_retry_existing_uses_same_event_and_no_insert(self):
        c = GoogleCalendar()
        expected = {'extendedProperties': {'private': {'elf_job': 'job'}}}
        c.get_event = Mock(return_value=expected)
        c.api = Mock()
        self.assertEqual(c.create_event('abc', 'job', 'work', 'start', 'end'), expected)
        c.api.assert_not_called()

    def test_success_only_after_api_result(self):
        c = Mock()
        c.create_event.return_value = {'htmlLink': 'https://calendar.google.com/example'}
        output = execute(c, '/新增行程 明天下午三點到四點 會議', 'job', self.now)
        self.assertIn('✅ 已新增', output)
        c.create_event.assert_called_once()
        c.create_event.side_effect = CalendarError('衝突')
        with self.assertRaises(CalendarError):
            execute(c, '/新增行程 明天下午三點到四點 會議', 'job', self.now)

    def test_retry_after_start_reports_existing_real_event(self):
        c=Mock()
        c.get_event.return_value={'summary':'活動會議','start':{'dateTime':'2026-10-16T15:00:00+08:00'},'extendedProperties':{'private':{'elf_job':'job'}}}
        result=execute(c,'/新增行程 2026-10-16 15:00到16:00 活動會議','job',self.now)
        self.assertIn('原行程已存在',result)
        c.create_event.assert_not_called()


class IntegrationTests(unittest.TestCase):
    setUp = test_elf.ElfTests.setUp
    tearDown = test_elf.ElfTests.tearDown
    event = test_elf.ElfTests.event
    ingest = test_elf.ElfTests.ingest
    enable = test_elf.ElfTests.enable
    def test_calendar_group_and_nonowner_cannot_execute(self):
        self.ingest(self.event('/新增行程 明天下午三點到四點 會議'))
        self.ingest(self.event('/新增行程 明天下午三點到四點 會議', uid='other', source='user'))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM calendar_jobs').fetchone()[0], 0)

    def test_calendar_owner_job_and_real_failure_report(self):
        self.ingest(self.event('/行程 明天', source='user'))
        with patch('calendar_actions.execute', side_effect=CalendarError('尚未授權')):
            self.elf.background_once()
        job = self.elf.db.execute('SELECT * FROM calendar_jobs').fetchone()
        self.assertEqual(job['state'], 'failed')
        self.assertIn('尚未授權', self.calls[-1][1]['messages'][0]['text'])

    def test_async_ingest_does_not_wait_for_line_network(self):
        self.elf.asynchronous = True
        self.ingest(self.event('/狀態', source='user'))
        self.assertEqual(len(self.calls), 0)
        self.elf.background_once()
        self.assertEqual(len(self.calls), 1)

    def test_pause_stops_new_collection(self):
        self.enable()
        self.ingest(self.event('/暫停收集'))
        self.ingest(self.event('工作交件', uid='other'))
        self.assertFalse(self.elf.has_pending_content())

    def test_batch_remaining_is_signalled(self):
        self.enable()
        for i in range(125):
            self.ingest(self.event('公事工作會議 ' + str(i), uid='other'))
        job = self.elf.prepare()
        result = self.elf.deliver(job['job_id'], '{"items":[]}')
        self.assertTrue(result['run'])
        self.assertEqual(len(__import__('json').loads(self.elf.prepare()['prompt'])['messages']), 5)

    def test_failure_retains_messages_and_backs_off(self):
        self.enable()
        self.ingest(self.event('工作會議請確認', uid='other'))
        job = self.elf.prepare()
        self.elf.failed(job['job_id'])
        self.assertTrue(self.elf.has_pending_content())
        self.assertFalse(self.elf.recover()['run'])
        self.elf.failed(job['job_id'])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
