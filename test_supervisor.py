import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
import supervisor as s


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.state=self.base/'state';self.state.mkdir()
        self.root=self.base/'project';self.root.mkdir()
        self.patches=[patch.object(s,'STATE',self.state),patch.object(s.runtime,'ROOT',self.root)]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def seed(self,stamp):
        db=sqlite3.connect(self.state/'elf.sqlite3')
        db.executescript('CREATE TABLE sources(id TEXT,enabled INTEGER);CREATE TABLE messages(source TEXT,seen INTEGER,timestamp INTEGER);CREATE TABLE blocked_sources(id TEXT);CREATE TABLE controls(id TEXT PRIMARY KEY,value INTEGER);')
        db.execute("INSERT INTO sources VALUES('work',1)")
        db.execute("INSERT INTO messages VALUES('work',0,?)",(int(stamp.timestamp()*1000),))
        db.commit();db.close()

    def test_cloudflare_url_only(self):
        self.assertEqual(s.public_url('https://hello-world.trycloudflare.com |'),'https://hello-world.trycloudflare.com')
        self.assertIsNone(s.public_url('https://untrusted.example/path'))

    def test_online_database_backup_readable_and_daily(self):
        db=sqlite3.connect(self.state/'elf.sqlite3');db.execute('CREATE TABLE proof(value INTEGER)');db.execute('INSERT INTO proof VALUES(42)');db.commit();db.close()
        (self.root/'.env').write_text('DUMMY=not-a-real-key')
        s.backup_daily();s.backup_daily()
        backups=list((self.state/'backups').glob('*/complete.json'));self.assertEqual(len(backups),1)
        backup=sqlite3.connect(backups[0].parent/'elf.sqlite3')
        self.assertEqual(backup.execute('SELECT value FROM proof').fetchone()[0],42);backup.close()

    def test_member_backups_stay_in_separate_files(self):
        member=self.state/'members'/('a'*64+'.sqlite3')
        member.parent.mkdir()
        db=sqlite3.connect(member);db.execute('CREATE TABLE proof(value INTEGER)');db.execute('INSERT INTO proof VALUES(7)');db.commit();db.close()
        s.backup_daily()
        copies=list((self.state/'backups').glob('*/members/*.sqlite3'))
        self.assertEqual(len(copies),1)
        db=sqlite3.connect(copies[0]);self.assertEqual(db.execute('SELECT value FROM proof').fetchone()[0],7);db.close()

    def test_missed_digest_requested_once(self):
        now=datetime(2026,10,21,9,tzinfo=s.TZ);self.seed(now-timedelta(hours=15))
        checked=s.request_missed_digest(now,'');self.assertEqual(checked,'2026-10-20')
        db=sqlite3.connect(self.state/'elf.sqlite3');self.assertEqual(db.execute('SELECT value FROM controls').fetchone()[0],1);db.close()
        self.assertEqual(s.request_missed_digest(now,checked),checked)

    def test_new_messages_do_not_trigger_missed_digest(self):
        now=datetime(2026,10,21,9,tzinfo=s.TZ);self.seed(now-timedelta(hours=1))
        s.request_missed_digest(now,'')
        db=sqlite3.connect(self.state/'elf.sqlite3');self.assertEqual(db.execute('SELECT count(*) FROM controls').fetchone()[0],0);db.close()

    def test_unowned_pid_never_terminated(self):
        manager=s.Supervisor();manager.bad={n:7 for n in ('elf','n8n','calendar','relay')};manager.last_restart={n:0 for n in manager.bad}
        with patch.object(s,'process_map',return_value={'elf':9999}),patch.object(s.runtime,'healthy',return_value=False),patch.object(s.runtime,'starting',return_value=False),patch.object(s.runtime,'start'),patch.object(s.os,'kill') as kill:
            manager.local_services();kill.assert_not_called()

    def test_endpoint_repair_only_line_webhook(self):
        (self.state/'tunnel.log').write_text('https://tested-connection.trycloudflare.com')
        manager=s.Supervisor();manager.health={'elf':True,'n8n':True,'relay':True,'calendar':True}
        with patch.object(s,'process_map',return_value={'cloudflared':123}),patch.object(s.runtime,'starting',return_value=True),patch.object(s,'get_json',return_value={'ok':True}),patch.object(s,'load_env'),patch.object(s,'api',side_effect=[{'endpoint':'https://old.example','active':True},{},{'success':True,'statusCode':200}]) as api,patch.object(s,'save_settings') as save:
            manager.tunnel();self.assertTrue(manager.webhook_ok)
            self.assertEqual(api.call_args_list[1].args[2],{'endpoint':'https://tested-connection.trycloudflare.com/webhook/line-work-elf'})
            save.assert_called_once_with({'WEBHOOK_URL':'https://tested-connection.trycloudflare.com'})


if __name__=='__main__':unittest.main()
