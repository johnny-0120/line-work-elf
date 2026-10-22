import base64
import hashlib
import hmac
import json
import os
import time
import unittest
import threading
import tempfile
from http.server import ThreadingHTTPServer
from urllib import request, error
from pathlib import Path
from unittest.mock import patch

from elf import Elf, Problem, Handler, load_env


class ElfTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'LINE_CHANNEL_SECRET': 'test-secret',
            'LINE_BOT_USER_ID': '',
            'LINE_CHANNEL_ACCESS_TOKEN': 'test-line-token', 'LINE_OWNER_USER_ID': 'U-owner',
            'LINE_TOKEN_EXPIRES_AT': '0',
            'OPENROUTER_API_KEY': 'test-openrouter-key', 'INTERNAL_TOKEN': 'test-internal',
            'OPENROUTER_FREE_MODEL':'openrouter/free', 'OBSIDIAN_VAULT':''})
        self.env.start()
        self.calls = []
        def sender(url, body, headers):
            self.calls.append((url, body, headers))
            return {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]}
        self.elf = Elf(':memory:', sender)
        self.counter = 0

    def tearDown(self):
        self.elf.db.close()
        self.env.stop()

    def event(self, text, uid='U-owner', source='group', sid='G-work'):
        self.counter += 1
        return {'webhookEventId': 'event-' + str(self.counter), 'type': 'message',
                'timestamp': int(time.time()*1000), 'replyToken': 'reply-test',
                'source': {'type': source, 'userId': uid, **({'groupId': sid} if source == 'group' else {})},
                'message': {'type': 'text', 'id': 'msg-' + str(self.counter), 'text': text}}

    def ingest(self, event):
        raw = json.dumps({'events': [event]}, ensure_ascii=False).encode()
        sig = base64.b64encode(hmac.new(b'test-secret', raw, hashlib.sha256).digest()).decode()
        return self.elf.ingest(raw, sig)

    def enable(self):
        self.ingest(self.event('/工作群組 NUVA'))

    def seed(self):
        self.enable()
        event = self.event('校園大使文案請 U-owner 明天交給我', uid='U-colleague')
        self.ingest(event)
        return event['message']['id']

    def output(self, mid, **changes):
        item = {'message_id': mid, 'evidence': '文案請 U-owner 明天交給我',
                'summary': '校園大使文案明天交件，請確認是否已回覆。', 'needs_reply': True}
        item.update(changes)
        return json.dumps({'items': [item]}, ensure_ascii=False)

    def test_reject_forged_signature(self):
        with self.assertRaises(Problem) as cm:
            self.elf.ingest(b'{"events":[]}', 'fake')
        self.assertEqual(cm.exception.status, 401)

    def test_signature_uses_original_bytes(self):
        raw = b'{ "events" : [] }'
        sig = base64.b64encode(hmac.new(b'test-secret', raw, hashlib.sha256).digest()).decode()
        self.assertEqual(self.elf.ingest(raw, sig), {'run': False})

    def test_group_opt_in_required(self):
        self.ingest(self.event('工作文案'))
        self.assertFalse(self.elf.prepare()['run'])

    def test_non_owner_cannot_enable(self):
        self.ingest(self.event('/工作群組 secret', uid='U-other'))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM sources').fetchone()[0], 0)

    def test_private_prefix_excluded(self):
        self.enable()
        self.ingest(self.event('私事 今天看醫生'))
        self.ingest(self.event('#private relationship'))
        self.assertFalse(self.elf.prepare()['run'])

    def test_dm_requires_owner_and_ai_classification(self):
        self.ingest(self.event('公事：交文案', uid='U-other', source='user'))
        self.ingest(self.event('私人聊天', source='user'))
        self.assertFalse(self.elf.prepare()['run'])
        self.ingest(self.event('公事：明天交文案', source='user'))
        self.assertFalse(self.elf.prepare()['run'])
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM assistant_jobs').fetchone()[0], 2)

    def test_duplicate_event_not_stored_twice(self):
        self.enable()
        event = self.event('公事')
        self.ingest(event)
        self.ingest(event)
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM messages').fetchone()[0], 1)

    def test_deliver_private_idempotent_and_tasks_complete(self):
        mid = self.seed()
        job = self.elf.prepare()
        self.assertEqual(job['job_id'], self.elf.prepare()['job_id'])
        self.elf.deliver(job['job_id'], self.output(mid))
        last = self.calls[-1]
        self.assertTrue(last[0].endswith('/push'))
        self.assertEqual(last[1]['to'], 'U-owner')
        self.assertIn('X-Line-Retry-Key', last[2])
        count = len(self.calls)
        self.assertTrue(self.elf.deliver(job['job_id'], self.output(mid))['duplicate'])
        self.assertEqual(len(self.calls), count)
        tid = self.elf.db.execute('SELECT id FROM tasks').fetchone()[0]
        self.ingest(self.event('/已處理 ' + tid, source='user'))
        self.assertEqual(self.elf.db.execute('SELECT done FROM tasks').fetchone()[0], 1)
        self.assertFalse(self.elf.prepare()['run'])

    def test_fabricated_evidence_not_sent_or_consumed(self):
        mid = self.seed()
        job = self.elf.prepare()
        count = len(self.calls)
        with self.assertRaises(Problem) as cm:
            self.elf.deliver(job['job_id'], self.output(mid, evidence='根本不存在的句子'))
        self.assertEqual(cm.exception.status, 422)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.elf.db.execute('SELECT seen FROM messages').fetchone()[0], 0)

    def test_private_tasks_not_replied_in_group(self):
        self.seed()
        self.ingest(self.event('/待回覆'))
        self.assertEqual(self.calls[-1][1]['messages'][0]['text'], '請私訊小精靈使用此指令。')

    def test_unsend_invalidates_pending_snapshot(self):
        mid = self.seed()
        job = self.elf.prepare()
        self.ingest({'webhookEventId': 'unsend', 'type': 'unsend',
                     'source': {'type': 'group', 'groupId': 'G-work'}, 'unsend': {'messageId': mid}})
        with self.assertRaises(Problem):
            self.elf.deliver(job['job_id'], self.output(mid))
        self.assertFalse(self.elf.prepare()['run'])

    def test_stop_deletes_stored_group_content(self):
        self.seed()
        self.elf.prepare()
        self.ingest(self.event('/停止收集'))
        for table in ('messages', 'sources', 'jobs'):
            self.assertEqual(self.elf.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_unsend_erases_sent_snapshot_too(self):
        mid = self.seed()
        job = self.elf.prepare()
        self.elf.deliver(job['job_id'], self.output(mid))
        self.ingest({'webhookEventId': 'unsend-sent', 'type': 'unsend',
                     'source': {'type': 'group', 'groupId': 'G-work'}, 'unsend': {'messageId': mid}})
        for table in ('messages', 'tasks', 'jobs'):
            self.assertEqual(self.elf.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_paid_model_blocked_before_network(self):
        with self.assertRaises(Problem):
            self.elf.proxy({'model': 'paid/model', 'messages': []})
        self.assertFalse(self.calls)

    def test_local_secret_overrides_injected_key_but_runtime_paths_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.env').write_text('OPENROUTER_API_KEY=local-test-key\nELF_DB=wrong-path\n', encoding='utf-8')
            with patch('elf.ROOT', Path(tmp)), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'injected-test-key', 'ELF_DB': 'runtime-path'}):
                load_env()
                self.assertEqual(os.environ['OPENROUTER_API_KEY'], 'local-test-key')
                self.assertEqual(os.environ['ELF_DB'], 'runtime-path')

    def test_only_free_router_and_zero_price_pass(self):
        self.elf.proxy({'model': 'openrouter/free', 'messages': [], 'stream': True,
                        'models': ['paid/model'], 'route': 'fallback', 'plugins': [{'id': 'web'}],
                        'provider': {'max_price': {'prompt': 999}}})
        url, body, _ = self.calls[-1]
        self.assertEqual(url, 'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(body['model'], 'openrouter/free')
        self.assertEqual(body['provider']['max_price'], {'prompt': 0, 'completion': 0})
        self.assertFalse(body['stream'])
        self.assertNotIn('models', body)
        self.assertNotIn('plugins', body)

    def test_failed_push_keeps_job_and_retry_key(self):
        mid = self.seed()
        job = self.elf.prepare()
        original_sender = self.elf.send
        def fail(url, body, headers):
            raise Problem(502, 'Upstream HTTP 500; no paid fallback was attempted.')
        self.elf.send = fail
        with self.assertRaises(Problem):
            self.elf.deliver(job['job_id'], self.output(mid))
        self.assertEqual(self.elf.db.execute('SELECT seen FROM messages').fetchone()[0], 0)
        retry = self.elf.db.execute('SELECT retry_key FROM jobs').fetchone()[0]
        self.elf.send = original_sender
        self.elf.deliver(job['job_id'], 'ignored: cached digest reused')
        self.assertEqual(self.calls[-1][2]['X-Line-Retry-Key'], retry)

    def test_bounded_batch_does_not_skip_overflow(self):
        self.enable()
        for i in range(130):
            self.ingest(self.event('工作 ' + str(i)))
        job = self.elf.prepare()
        self.assertEqual(len(json.loads(job['prompt'])['messages']), 120)
        self.elf.deliver(job['job_id'], '{"items":[]}')
        self.assertEqual(len(json.loads(self.elf.prepare()['prompt'])['messages']), 10)

    def test_http_auth_and_stream_adapter(self):
        Handler.elf = self.elf
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}/v1/chat/completions'
        body = json.dumps({'model': 'openrouter/free', 'messages': [], 'stream': True}).encode()
        try:
            with self.assertRaises(error.HTTPError) as cm:
                request.urlopen(request.Request(url, body), timeout=3)
            self.assertEqual(cm.exception.code, 401)
            req = request.Request(url, body, {'Authorization': 'Bearer test-internal'})
            with request.urlopen(req, timeout=3) as response:
                self.assertEqual(response.headers['Content-Type'], 'text/event-stream')
                text = response.read().decode()
                self.assertIn('"content": "ok"', text)
                self.assertIn('data: [DONE]', text)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_workflow_connections_free_model_and_schedule(self):
        workflow = json.loads(Path(__file__).with_name('workflow.json').read_text(encoding='utf-8'))
        names = {node['name'] for node in workflow['nodes']}
        for source, ports in workflow['connections'].items():
            self.assertIn(source, names)
            for outputs in ports.values():
                for edges in outputs:
                    for edge in edges:
                        self.assertIn(edge['node'], names)
        model = next(n for n in workflow['nodes'] if 'lmChatOpenAi' in n['type'])
        self.assertEqual(model['parameters']['model']['value'], 'openrouter/free')
        self.assertEqual(workflow['settings']['timezone'], 'Asia/Taipei')
        self.assertFalse(workflow['active'])

    def test_redelivered_digest_command_can_resume_before_job_creation(self):
        self.seed()
        event = self.event('/整理', source='user')
        self.assertTrue(self.ingest(event)['run'])
        self.assertTrue(self.ingest(event)['run'])

    def test_operation_error_audit_removes_credentials_and_chat_text(self):
        mid=self.seed()
        text=self.elf.db.execute('SELECT text FROM messages WHERE id=?',(mid,)).fetchone()[0]
        self.elf.operation_audit('test','failed','test-openrouter-key '+text)
        detail=self.elf.db.execute('SELECT detail FROM operation_audit').fetchone()[0]
        self.assertNotIn('test-openrouter-key',detail)
        self.assertNotIn(text,detail)

    def test_local_digest_test_requires_authentication(self):
        import build_workflow
        n=next(n for n in build_workflow.nodes if n['name']=='Local authenticated digest test')
        self.assertEqual(n['parameters']['authentication'],'headerAuth')
        self.assertIn('httpHeaderAuth',n['credentials'])

    def test_digest_json_retry_free_only_and_evidence_still_required(self):
        self.seed()
        p=self.elf.prepare()
        self.elf.retry_digest(p['job_id'])
        call=self.calls[-1][1]
        self.assertEqual(call['model'],'openrouter/free')
        self.assertEqual(call['response_format'],{'type':'json_object'})
        self.assertNotIn('tools',call)
        self.assertEqual(call['provider']['max_price'],{'prompt':0,'completion':0})
        with self.assertRaises(Problem):
            self.elf.deliver(p['job_id'],'{"items":[{}]}')
        self.assertEqual(self.elf.db.execute('SELECT state FROM jobs').fetchone()[0],'pending')

    def test_digest_retry_cannot_restart_completed_job(self):
        mid=self.seed();p=self.elf.prepare()
        self.elf.deliver(p['job_id'],self.output(mid))
        before=len(self.calls)
        with self.assertRaises(Problem): self.elf.retry_digest(p['job_id'])
        self.assertEqual(len(self.calls),before)

    def test_completed_digest_clears_manual_request(self):
        mid=self.seed();self.elf.command('/整理','U-owner','user');p=self.elf.prepare()
        self.elf.deliver(p['job_id'],self.output(mid))
        self.assertEqual(self.elf.db.execute("SELECT value FROM controls WHERE id='digest_requested'").fetchone()[0],0)
        self.assertFalse(self.elf.recover()['run'])

    def test_digest_preserves_request_for_pending_understanding(self):
        mid=self.seed();self.elf.command('/整理','U-owner','user');p=self.elf.prepare()
        self.elf.db.execute("INSERT INTO assistant_jobs(id,text,timestamp,state,created) VALUES('pending-dm','work',0,'pending',?)",(time.time(),))
        self.elf.deliver(p['job_id'],self.output(mid))
        self.assertEqual(self.elf.db.execute("SELECT value FROM controls WHERE id='digest_requested'").fetchone()[0],1)

    def test_empty_digest_command_explains_missing_content(self):
        result = self.ingest(self.event('/整理', source='user'))
        self.assertFalse(result['run'])
        self.assertIn('目前沒有待整理', self.calls[-1][1]['messages'][0]['text'])

    def test_owner_mentions_and_names_reach_the_ai_context(self):
        self.enable()
        event = self.event('@本人 請確認活動時段', uid='U-colleague')
        event['message']['mention'] = {'mentionees': [{'type': 'user', 'userId': 'U-owner'}]}
        self.ingest(event)
        with patch.dict(os.environ, {'LINE_OWNER_NAMES': 'Test Owner'}):
            data = json.loads(self.elf.prepare()['prompt'])
        self.assertEqual(data['messages'][0]['owner_mentioned'], 1)
        self.assertEqual(data['owner_names'], ['Test Owner'])


if __name__ == '__main__':
    unittest.main()
