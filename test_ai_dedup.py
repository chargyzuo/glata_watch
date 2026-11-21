"""同工单去重、进程锁、完成凭据和会话保存回归；仅合成数据，无真实CLI/电话。"""
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import ai_task_guard as guard
import oncall_ai_summary as ai
import read_oncall_chat as core
import watch_and_run as watch
from test_ticket_notify import card

ROOT = Path(__file__).resolve().parent


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='glata-ai-dedup-')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def process(self, batches, meta, state=None, jobs=None):
        state = state if state is not None else {'seen':[], 'pending':{}}
        jobs = jobs if jobs is not None else {}
        with patch.object(watch,'fetch_cards',side_effect=batches),patch.object(watch,'locate_group',side_effect=meta),\
             patch.object(watch,'save_state'),patch.object(watch,'log'),\
             patch.object(watch,'start_ai_job',side_effect=lambda *a,**kw:{'log_path':'fixture.log'}) as start:
            for _ in batches:
                watch.process_new(Mock(),None,SimpleNamespace(workers=3),state,jobs)
            return start.call_count, state, jobs

    def locate(self, runner, c, cache):
        return ({'chat_id':'oc_fixture'}, None,
                {'ticket_no':'51234567','transfer_time':'2026-11-20 15:04:31'}, 'ticket_page')

    def test_same_ticket_different_messages_same_batch_once(self):
        calls,state,jobs=self.process([[card(1),card(2)]],self.locate)
        self.assertEqual(calls,1);self.assertEqual(len(jobs),1)
        self.assertEqual(len(state['seen']),1)

    def test_duplicate_across_rounds_once(self):
        calls,state,jobs=self.process([[card(1)],[card(2)]],self.locate)
        self.assertEqual(calls,1);self.assertEqual(len(jobs),1)

    def test_completed_task_after_restart_ignores_new_message(self):
        key=guard.task_key('51234567','2026-11-20 15:04:31')
        state={'seen':[], 'pending':{}, 'tasks':{key:{'owner':'om_old','status':'done'}}}
        calls,state,jobs=self.process([[card(2)]],self.locate,state)
        self.assertEqual(calls,0);self.assertIn(card(2)['message_id'],state['seen'])

    def test_new_transfer_time_is_new_task_same_chat(self):
        def locate(r,c,cache):
            group,_,info,err=self.locate(r,c,cache)
            info['transfer_time']='2026-11-20 15:04:3'+c['message_position']
            return group,None,info,err
        calls,state,jobs=self.process([[card(1),card(2)]],locate)
        self.assertEqual(calls,2);self.assertEqual(len(state['tasks']),2)

    def test_retry_budget_preserved_and_duplicate_does_not_bypass_backoff(self):
        key=guard.task_key('51234567','2026-11-20 15:04:31')
        original=card(1)
        state={'seen':[], 'pending':{original['message_id']:{'retry':4,'next_retry':0,'task_key':key}},
               'tasks':{key:{'owner':original['message_id'],'status':'retry'}}}
        calls,state,jobs=self.process([[card(2),original]],self.locate,state)
        self.assertEqual(calls,1)
        self.assertEqual(state['pending'][original['message_id']]['retry'],4)
        self.assertIn(card(2)['message_id'],state['seen'])

    def test_repeated_failures_reach_limit_without_reset(self):
        key=guard.task_key('51234567','2026-11-20 15:04:31')
        original=card(1);mid=original['message_id']
        state={'seen':[], 'pending':{mid:{'retry':5,'next_retry':0,'task_key':key}},
               'tasks':{key:{'owner':mid,'status':'retry'}}}
        calls,state,jobs=self.process([[original]],self.locate,state)
        self.assertEqual(calls,1)
        jobs[mid].update(p=Mock(poll=lambda:7),fh=Mock(),chat_id='oc_fixture',started=0,forced=False)
        with patch.object(watch,'save_state'),patch.object(watch,'log'):
            watch.collect_jobs(jobs,state,SimpleNamespace(timeout=300))
        self.assertIn(mid,state['seen'])
        self.assertNotIn(mid,state['pending'])
        self.assertEqual(state['tasks'][key]['status'],'giveup')

    def test_state_atomic_write_roundtrip_preserves_existing_fields(self):
        state={'seen':['om_fixture'],'pending':{},'tasks':{},'legacy':'keep'}
        with patch.object(watch,'STATE_PATH',str(self.root/'state.json')):
            watch.save_state(state)
            self.assertEqual(watch.load_state(),state)

    def test_busy_result_does_not_consume_retry(self):
        key=guard.task_key('51234567','2026-11-20 15:04:31')
        mid='om_fixture'
        state={'seen':[], 'pending':{mid:{'retry':2,'state':'running','task_key':key}},
               'tasks':{key:{'owner':mid,'status':'running'}}}
        jobs={mid:{'p':Mock(poll=lambda:9),'fh':Mock(),'chat_id':'oc_fixture',
                   'task_key':key,'started':0,'forced':False}}
        with patch.object(watch,'save_state'),patch.object(watch,'log'),patch.object(watch.time,'time',return_value=100):
            watch.collect_jobs(jobs,state,SimpleNamespace(timeout=300))
        self.assertEqual(state['pending'][mid]['retry'],2)
        self.assertEqual(state['pending'][mid]['next_retry'],110)
        self.assertNotIn(mid,state['seen'])

    def test_real_process_lock_blocks_duplicate_and_completion_reuses_report(self):
        key=guard.task_key('51234567','fixture_time')
        code='''
import sys,time
from pathlib import Path
from unittest.mock import patch
import oncall_ai_summary as ai
import read_oncall_chat as core
from ai_task_guard import atomic_text
root=Path(sys.argv[1]); key=sys.argv[2]
def analyze(args,t0,runner,cid,name,outkey,ticket):
    with open(root/'calls','a') as f:f.write('call\\n')
    (root/'started').write_text('yes')
    time.sleep(0.8)
    atomic_text(root/(outkey+'_summary.md'),'synthetic report')
    return 0
with patch.object(core,'ONCALL_DIR',str(root)),patch.object(core,'log'),patch.object(core,'LarkRunner'),patch.object(ai,'resolve_target',return_value=('oc_fixture','fixture','oc_fixture',{})),patch.object(ai,'analyze',side_effect=analyze),patch('sys.argv',['ai','--chat-id','oc_fixture','--task-key',key]):
    sys.exit(ai.main())
'''
        command=[sys.executable,'-c',code,str(self.root),key]
        env=dict(os.environ,PYTHONPATH=str(ROOT))
        p=subprocess.Popen(command,cwd=ROOT,env=env)
        try:
            deadline=time.monotonic()+5
            while not (self.root/'started').exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue((self.root/'started').exists())
            busy=subprocess.run(command,cwd=ROOT,env=env,timeout=5)
            self.assertEqual(busy.returncode,9)
            self.assertEqual(p.wait(timeout=5),0)
            cached=subprocess.run(command,cwd=ROOT,env=env,timeout=5)
            self.assertEqual(cached.returncode,0)
            self.assertEqual((self.root/'calls').read_text().splitlines(),['call'])
        finally:
            if p.poll() is None:p.terminate();p.wait(timeout=5)

    def test_other_task_lock_not_blocked(self):
        with guard.task_lock(self.root,'fixture1'):
            with guard.task_lock(self.root,'fixture2'):pass
            with self.assertRaises(guard.TaskBusy):
                with guard.task_lock(self.root,'fixture1'):pass

    def test_completed_receipt_checks_report_hash(self):
        p=self.root/'fixture_summary.md';p.write_text('original')
        guard.record_completed(self.root,'fixture',p)
        self.assertEqual(guard.completed_report(self.root,'fixture'),str(p))
        p.write_text('changed')
        with self.assertRaises(guard.StateError):guard.completed_report(self.root,'fixture')

    def test_parallel_process_session_updates_not_lost(self):
        path=self.root/'sessions.json'
        code='from ai_task_guard import merge_json; import sys; merge_json(sys.argv[1],{sys.argv[2]:sys.argv[2]})'
        def run(i):
            return subprocess.run([sys.executable,'-c',code,str(path),'fixture_'+str(i)],cwd=ROOT,check=True).returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(run,range(16)))
        self.assertEqual(len(guard.read_json(path)),16)

    def test_corrupt_sessions_not_silently_overwritten(self):
        path=self.root/'sessions.json';path.write_text('bad json')
        with self.assertRaises(guard.StateError):guard.merge_json(path,{'fixture':'value'})
        self.assertEqual(path.read_text(),'bad json')

    def test_corrupt_monitor_state_not_treated_as_new_install(self):
        path=self.root/'watch.json';path.write_text('{broken')
        with patch.object(watch,'STATE_PATH',str(path)):
            with self.assertRaises(guard.StateError):watch.load_state()
        self.assertEqual(path.read_text(),'{broken')

    def test_real_subprocess_timeout_metadata_saved(self):
        code='import sys,time; print(\'{"type":"session","session_id":"fixture_real_timeout"}\',file=sys.stderr,flush=True); time.sleep(10)'
        with patch.object(ai,'SESS_FILE',str(self.root/'sessions.json')):
            with self.assertRaises(subprocess.TimeoutExpired):
                ai.run_doubao([sys.executable,'-c',code],1,'fixture_real')
            self.assertEqual(ai.load_sessions()['fixture_real'],'fixture_real_timeout')

    def test_timeout_saves_byte_metadata_and_next_attempt_resumes(self):
        with patch.object(ai,'SESS_FILE',str(self.root/'sessions.json')),patch.object(core,'log'):
            timeout=subprocess.TimeoutExpired(['fixture'],300,output=b'',stderr=b'{"type": "session", "session_id": "fixture_timeout"}\n')
            with patch.object(ai.subprocess,'run',side_effect=timeout):
                with self.assertRaises(subprocess.TimeoutExpired):ai.run_doubao(['fixture'],300,'fixture_key')
            self.assertEqual(ai.load_sessions()['fixture_key'],'fixture_timeout')
            msgs=[{'content':'synthetic', 'sender':{'name':'fixture'}, 'create_time':'fixture'}]
            args=SimpleNamespace(since=None,no_session=True,no_ocr=True,limit=0,dry_run=False,
                                 model='fixture',timeout=300,raw=False)
            result=SimpleNamespace(returncode=0,stdout='synthetic answer',stderr='{"type":"session","session_id":"fixture_timeout"}')
            with patch.object(core,'ONCALL_DIR',str(self.root)),patch.object(ai,'load_stage2_messages',return_value=msgs),\
                 patch.object(ai.subprocess,'run',return_value=result) as run:
                self.assertEqual(ai.analyze(args,time.time(),Mock(),'oc_fixture','fixture','fixture_key',{}),0)
                self.assertIn('--resume',run.call_args.args[0])
                self.assertIn('fixture_timeout',run.call_args.args[0])


if __name__=='__main__':unittest.main()
