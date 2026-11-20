"""主循环离线回归：新装基线、后续处理、并发与失败收集。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import watch_and_run as watch
import read_oncall_chat as core
from test_ticket_notify import card


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='glata-workflow-test-')
        self.root = Path(self.tmp.name)
        self.identity = patch.multiple(core, TARGET_USER_ID='ou_fixture', TARGET_USER_IDS={'ou_fixture'},
                                       TARGET_USER_NAME='测试本人')
        self.identity.start()

    def tearDown(self):
        self.identity.stop();self.tmp.cleanup()

    def test_new_install_baseline_before_save_then_resume(self):
        path = self.root / 'state.json'
        flags = []
        def process(*args, **kwargs):
            flags.append(kwargs['baseline_only'])
            return None
        with patch.object(watch, 'STATE_PATH', str(path)),patch.object(watch.core, 'ONCALL_DIR', str(self.root)),\
             patch.object(watch, 'acquire_single_instance'),patch.object(watch.core, 'LarkRunner'),\
             patch.object(watch, 'log'),patch.object(watch, 'process_new', side_effect=process),\
             patch.object(sys, 'argv', ['watch_and_run.py','--once']):
            watch.main()
            self.assertTrue(path.exists())
            watch.main()
        self.assertEqual(flags, [True, False])

    def test_baseline_registers_existing_without_page_or_ai(self):
        state = {'seen':[], 'pending':{}}
        with patch.object(watch,'fetch_cards',return_value=[card(1)]),patch.object(watch,'save_state'),\
             patch.object(watch,'log'),patch.object(watch,'locate_group') as page,patch.object(watch,'start_ai_job') as ai:
            watch.process_new(Mock(),None,SimpleNamespace(workers=3),state,{},baseline_only=True)
        page.assert_not_called();ai.assert_not_called()
        self.assertIn(card(1)['message_id'],state['seen'])

    def test_two_tickets_start_concurrently_without_wait(self):
        state={'seen':[], 'pending':{}};jobs={}
        def locate(runner,c,cache):
            return {'chat_id':'oc_fixture'+c['message_position']},None,{'transfer_time':'2026-11-15 10:00:00'},'ticket_page'
        with patch.object(watch,'fetch_cards',return_value=[card(1),card(2)]),\
             patch.object(watch,'locate_group',side_effect=locate),patch.object(watch,'save_state'),\
             patch.object(watch,'log'),patch.object(watch,'start_ai_job',return_value={'log_path':'synthetic.log'}) as ai:
            watch.process_new(Mock(),None,SimpleNamespace(workers=3),state,jobs)
        self.assertEqual(ai.call_count,2);self.assertEqual(len(jobs),2)
        self.assertTrue(all(entry['state']=='running' for entry in state['pending'].values()))

    def test_failed_ai_not_marked_success_and_is_retryable(self):
        p=Mock();p.poll.return_value=7
        jobs={'om_fixture':{'p':p,'fh':Mock(),'chat_id':'oc_fixture','started':0,'forced':False,'log_path':'fixture.log'}}
        state={'seen':[], 'pending':{'om_fixture':{'retry':0}}}
        with patch.object(watch,'log'),patch.object(watch,'save_state'),patch.object(watch.time,'time',return_value=100):
            watch.collect_jobs(jobs,state,SimpleNamespace(timeout=300))
        self.assertEqual(jobs,{})
        self.assertNotIn('om_fixture',state['seen'])
        self.assertEqual(state['pending']['om_fixture']['next_retry'],220)


if __name__ == '__main__':unittest.main()
