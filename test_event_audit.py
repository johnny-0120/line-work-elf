import base64
import hashlib
import hmac
import json
import os
import re
import time
import unittest
from unittest.mock import patch
import test_elf
from elf import Problem
from agent_tools import groups,refresh_groups


class EventAuditTests(unittest.TestCase):
    setUp=test_elf.ElfTests.setUp
    tearDown=test_elf.ElfTests.tearDown
    event=test_elf.ElfTests.event
    ingest=test_elf.ElfTests.ingest

    def receive(self,event,destination='U-bot'):
        raw=json.dumps({'destination':destination,'events':[event]}).encode()
        sig=base64.b64encode(hmac.new(b'test-secret',raw,hashlib.sha256).digest()).decode()
        return self.elf.ingest(raw,sig)

    def test_valid_signature_wrong_destination_cannot_process(self):
        with patch.dict(os.environ,{'LINE_BOT_USER_ID':'U-bot'}):
            with self.assertRaises(Problem):
                self.receive(self.event('/工作群組 test'),'U-other-bot')
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)
        row=self.elf.db.execute('SELECT disposition,destination_verified FROM event_audit').fetchone()
        self.assertEqual(tuple(row),('destination_mismatch',0))

    def test_source_types_and_line_timestamp_are_audited_without_body(self):
        event=self.event('private body not for audit',source='user')
        event['timestamp']=1234567890000
        event['deliveryContext']={'isRedelivery':True}
        with patch.dict(os.environ,{'LINE_BOT_USER_ID':'U-bot'}):
            self.receive(event)
        row=self.elf.db.execute('SELECT * FROM event_audit').fetchone()
        self.assertEqual(row['source_type'],'user')
        self.assertEqual(row['line_timestamp'],1234567890000)
        self.assertTrue(row['destination_verified'])
        self.assertTrue(row['redelivery'])
        self.assertNotIn('private body',str(tuple(row)))
        self.assertNotIn('U-owner',str(tuple(row)))

    def test_blocked_source_stays_excluded_on_new_owner_event(self):
        self.elf.db.execute('INSERT INTO blocked_sources VALUES(?,?,?)',('G-work','owner_denied',time.time()))
        self.receive(self.event('/工作群組 test'))
        self.receive(self.event('開始整理'))
        self.assertEqual(groups(self.elf),[])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.elf.db.execute("SELECT count(*) FROM event_audit WHERE disposition='blocked_source'").fetchone()[0],2)

    def test_mixed_user_and_group_source_is_not_treated_as_group(self):
        event=self.event('text',source='user')
        event['source']['groupId']='G-other'
        self.receive(event)
        self.assertEqual(groups(self.elf),[])
        self.assertEqual(self.elf.db.execute('SELECT disposition FROM event_audit').fetchone()[0],'invalid_source')

    def test_duplicate_delivery_keeps_first_provenance(self):
        event=self.event('hi',source='user')
        self.receive(event)
        self.receive(event)
        self.assertEqual(self.elf.db.execute('SELECT deliveries FROM event_audit').fetchone()[0],2)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM assistant_jobs').fetchone()[0],1)

    def test_room_does_not_use_group_summary_endpoint(self):
        event=self.event('hi',source='user')
        event['source']={'type':'room','roomId':'R-room','userId':'U-owner'}
        self.receive(event)
        from unittest.mock import Mock
        getter=Mock()
        refresh_groups(self.elf,getter)
        getter.assert_not_called()

    def pairing_code(self):
        self.ingest(self.event('/綁定群組',source='user'))
        text=self.calls[-1][1]['messages'][0]['text']
        return re.search(r'/綁定群組 ([A-F0-9]{10})',text)[1]

    def test_one_time_pairing_binds_only_the_selected_group(self):
        code=self.pairing_code()
        self.ingest(self.event('/綁定群組 '+code))
        self.assertEqual(self.elf.db.execute('SELECT enabled FROM sources WHERE id=?',('G-work',)).fetchone()[0],1)
        self.assertEqual(self.elf.db.execute('SELECT method FROM group_bindings').fetchone()[0],'one_time_code')
        self.ingest(self.event('/綁定群組 '+code,sid='G-other'))
        self.assertIsNone(self.elf.db.execute('SELECT 1 FROM sources WHERE id=?',('G-other',)).fetchone())

    def test_other_user_cannot_consume_owner_code(self):
        code=self.pairing_code()
        self.ingest(self.event('/綁定群組 '+code,uid='U-other'))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)
        self.ingest(self.event('/綁定群組 '+code))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM group_bindings').fetchone()[0],1)

    def test_expired_code_does_not_enable(self):
        code=self.pairing_code()
        self.elf.db.execute('UPDATE group_pairings SET expires=0')
        self.ingest(self.event('/綁定群組 '+code))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_binding_cannot_reenable_denied_source(self):
        code=self.pairing_code()
        self.elf.db.execute('INSERT INTO blocked_sources VALUES(?,?,?)',('G-work','owner_denied',time.time()))
        self.ingest(self.event('/綁定群組 '+code))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM group_bindings').fetchone()[0],0)
