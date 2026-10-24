import json
import os
import unittest
from unittest.mock import patch

import onboarding as ui
from rich_menu import definition,PUBLIC_BUTTONS
import test_public_groups as fixtures


class OnboardingTests(unittest.TestCase):
    setUp=fixtures.PublicGroupsTests.setUp
    tearDown=fixtures.PublicGroupsTests.tearDown
    send=fixtures.PublicGroupsTests.send
    child=fixtures.PublicGroupsTests.child
    register=fixtures.PublicGroupsTests.register
    def test_follow_has_welcome_without_creating_database(self):
        self.send('',uid='alice',etype='follow')
        message=self.calls[-1][1]['messages'][0]
        self.assertEqual(message['type'],'flex')
        self.assertIn('了解並開始',json.dumps(message,ensure_ascii=False))
        self.assertEqual(len(self.elf.public.children),0)

    def test_start_requires_visible_consent_button(self):
        self.send('/開始使用','alice')
        message=self.calls[-1][1]['messages'][0]
        self.assertIn('/同意並開始',json.dumps(message,ensure_ascii=False))
        self.assertFalse(self.elf.db.execute('SELECT 1 FROM public_members').fetchone())

    def test_pairing_clipboard_contains_only_own_one_time_code(self):
        self.register('alice')
        self.send('/綁定群組','alice')
        message=self.calls[-1][1]['messages'][0]
        actions=[b['action'] for b in message['contents']['footer']['contents']]
        clipboard=[a for a in actions if a['type']=='clipboard'][0]
        self.assertRegex(clipboard['clipboardText'],r'^/綁定群組 [0-9A-F]{10}$')
        self.assertNotIn('host',json.dumps(message))

    def test_group_join_explains_individual_opt_in_without_collecting(self):
        with patch.dict(os.environ,{'PUBLIC_BOT_BASIC_ID':'@synthetic'}):
            self.send('',None,'new-group','group',etype='join')
        message=self.calls[-1][1]['messages'][0]
        self.assertIn('oaMessage/%40synthetic',json.dumps(message))
        self.assertEqual(self.elf.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(len(self.elf.public.children),0)

    def test_menu_and_settings_do_not_change_another_member(self):
        self.register('alice');self.register('bob')
        self.send('/使用設定','alice')
        message=self.calls[-1][1]['messages'][0]
        self.assertIn('/每日整理 開啟',json.dumps(message,ensure_ascii=False))
        self.send('/每日整理 開啟','alice')
        rows=dict(self.elf.db.execute('SELECT uid,automatic FROM public_members'))
        self.assertEqual(rows,{'alice':1,'bob':0})

    def test_unknown_and_registered_people_have_working_start_routes(self):
        self.send('怎麼用','alice')
        self.assertEqual(self.calls[-1][1]['messages'][0]['type'],'flex')
        self.register('alice');self.send('/開始使用','alice')
        self.assertIn('目前還沒有綁定群組',json.dumps(self.calls[-1][1],ensure_ascii=False))

    def test_async_flex_messages_survive_database_queue(self):
        self.elf.asynchronous=True
        self.send('/開始使用','alice')
        row=self.elf.db.execute('SELECT messages_json FROM outbox').fetchone()
        self.assertEqual(json.loads(row[0])[0]['type'],'flex')
        self.elf.background_once()
        self.assertEqual(self.calls[-1][1]['messages'][0]['type'],'flex')


class MenuBuilderTests(unittest.TestCase):
    def test_public_six_buttons_are_safe_member_commands(self):
        data=definition(public=True)
        self.assertEqual([x['action']['text'] for x in data['areas']],[x[2] for x in PUBLIC_BUTTONS])
        self.assertNotIn('/行程 今天',json.dumps(data))
        self.assertEqual(sum(a['bounds']['width']*a['bounds']['height'] for a in data['areas']),2500*1686)

    def test_all_cards_are_small_serializable_and_have_fallback(self):
        for card in (ui.welcome(),ui.consent(),ui.pairing('/綁定群組 ABCDEF1234'),ui.invitation(),
                     ui.invitation(True),ui.dashboard([]),ui.group_list([]),ui.settings(False),ui.settings(True),ui.data_controls()):
            self.assertEqual(card['type'],'flex')
            self.assertTrue(1<=len(card['altText'])<=400)
            self.assertLess(len(json.dumps(card).encode()),30000)


if __name__=='__main__':unittest.main()
