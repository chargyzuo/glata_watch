#!/usr/bin/env python3
"""离线回归：不读取真实密钥、不发送飞书消息、不拨电话。"""
import concurrent.futures
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import read_oncall_chat as core
import watch_and_run as watch
from ticket_notify import BotClient, NotificationManager, NotifyError, load_credentials, MESSAGE_TEXT


def card(pos, title=None, mine=True, ticket="51234567", mid=None):
    return {"message_id": mid or "om_fixture_%s" % pos, "message_position": str(pos),
            "msg_type": "interactive", "create_time": "2026-11-15 10:00",
            "mentions": [{"id": "ou_fixture", "name": "测试本人"}]
            if mine else [{"id": "ou_other", "name": "其他同事"}],
            "content": '<card title="%s">https://glata.bytedance.com/ens/engineer/serviceTicket/detail/%s</card>' %
            (title or core.NEW_TICKET_KEYWORD, ticket)}


class FakeClient:
    app_id = "cli_fixture"
    chat_id = "oc_fixture"

    def __init__(self, fail=None):
        self.sent = []
        self.urgents = []
        self.fail = fail

    def send(self, key):
        self.sent.append(key)
        return "om_sent", "ou_self"

    def urgent_phone(self, mid, target):
        self.urgents.append((mid, target))
        if self.fail:
            raise self.fail


class ImmediateExecutor:
    def submit(self, fn, *args):
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait=True):
        pass


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="glata-notify-test-")
        self.root = Path(self.tmp.name)
        self.logs = []
        # 测试身份固定使用合成值，不依赖维护者本机配置。
        self.identity_patch = patch.multiple(core, TARGET_USER_ID="ou_fixture", TARGET_USER_IDS={"ou_fixture"},
                                             TARGET_USER_NAME="测试本人")
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, mode="human", client=None, executor=None):
        return NotificationManager(mode, client or FakeClient(), self.root / "state.json", core,
                                   self.logs.append, executor or ImmediateExecutor())

    def test_human_filters_alert_other_and_requires_form_type(self):
        c = FakeClient(); m = self.manager(client=c)
        m.observe([card(1)]); m.poll()
        self.assertEqual(c.sent, [])
        human = card(2); disguised = card(3)
        m.observe([human, disguised, card(4, "你有一条告警"), card(5, mine=False)])
        m.confirm_manual(human, {"ticket_metadata_loaded": True, "form_type": "Request"})
        m.confirm_manual(disguised, {"ticket_metadata_loaded": True, "form_type": "Alert", "alert_only": True})
        m.poll();m.poll()
        self.assertEqual(len(c.sent), 1);self.assertEqual(len(c.urgents), 1)
        self.assertEqual(m.state['entries'][disguised['message_id']]['status'], 'filtered_alert')

    def test_all_includes_alert_but_not_other_or_plain_messages(self):
        c=FakeClient();m=self.manager('all',c)
        m.observe([card(1)])
        m.observe([card(2),card(3,'你有一条告警'),card(4,mine=False),card(5,'普通消息')])
        m.poll();m.poll();m.poll()
        self.assertEqual(len(c.sent),2);self.assertEqual(len(c.urgents),2)
        m.observe([card(2),card(3,'你有一条告警')]);m.poll()
        self.assertEqual(len(c.sent),2)

    def test_all_handles_new_ticket_alert_combined_title(self):
        c=FakeClient();m=self.manager('all',c);m.observe([card(1)])
        m.observe([card(2,core.NEW_TICKET_KEYWORD+'（告警）')]);m.poll()
        self.assertEqual(len(c.urgents),1)

    def test_idle_scans_do_not_rewrite_state(self):
        m=self.manager('all');m.observe([card(1)])
        with patch.object(m,'_save') as save:
            m.observe([card(1)]);save.assert_not_called()

    def test_human_unknown_type_does_not_guess(self):
        c=FakeClient();m=self.manager(client=c);m.observe([card(1)]);m.observe([card(2)])
        m.confirm_manual(card(2),{'ticket_metadata_loaded':False});m.poll()
        self.assertEqual(c.sent,[])

    def test_restart_and_mode_switch_no_replay(self):
        c=FakeClient();m=self.manager('all',c)
        m.observe([card(1)]);m.observe([card(2)]);m.poll();m.close()
        m2=self.manager('human',c);m2.observe([card(1),card(2),card(3)])
        m2.poll();self.assertEqual(len(c.sent),1)
        m2.observe([card(4)]);m2.confirm_manual(card(4),{'ticket_metadata_loaded':True,'form_type':'Request'})
        m2.poll();self.assertEqual(len(c.sent),2)

    def test_uncertain_phone_not_automatically_retried(self):
        c=FakeClient(NotifyError('timeout',uncertain=True));m=self.manager('all',c)
        m.observe([card(1)]);m.observe([card(2)]);m.poll();m.poll()
        self.assertEqual(len(c.urgents),1)
        self.assertEqual(m.state['entries'][card(2)['message_id']]['status'],'uncertain')
        m.close();m2=self.manager('all',c);m2.observe([card(2)]);m2.poll()
        self.assertEqual(len(c.urgents),1)

    def test_corrupt_state_fails_closed(self):
        (self.root/'state.json').write_text('not json')
        with self.assertRaises(NotifyError):self.manager()

    def test_credentials_legacy_json_security(self):
        p=self.root/'secret.json';secret='A'*32
        p.write_text('[{app_ip:'+secret+'}]');p.chmod(0o600)
        with patch('ticket_notify.DEFAULT_APP_ID','cli_fixture'):
            self.assertEqual(load_credentials(p)[1],secret)
        p.write_text(json.dumps({'app_id':'cli_fixture','app_secret':secret}))
        self.assertEqual(load_credentials(p),('cli_fixture',secret))
        p.chmod(0o644)
        with self.assertRaises(NotifyError):load_credentials(p)
        p.chmod(0o600);p.write_text('{bad:secret}')
        with self.assertRaises(NotifyError):load_credentials(p)

    def test_fixed_message_identity_and_patch_contract(self):
        p=self.root/'secret.json';p.write_text(json.dumps({'app_id':'cli_fixture','app_secret':'A'*32}));p.chmod(0o600)
        b=BotClient(p,'oc_fixture','本人');calls=[]
        def req(method,path,body=None,auth=True):
            calls.append((method,path,body))
            if method=='POST':
                return {'code':0,'data':{'message_id':'om_fixture','chat_id':'oc_fixture',
                    'sender':{'id':'cli_fixture','sender_type':'app'},'mentions':[{'id':'ou_self'}]}}
            return {'code':0,'data':{'invalid_user_id_list':[]}}
        with patch.object(b,'preflight',return_value='ou_self'),patch.object(b,'_request',side_effect=req):
            mid,target=b.send('fixture-unique');b.urgent_phone(mid,target)
        self.assertEqual(json.loads(calls[0][2]['content'])['text'],'<at user_id="ou_self">本人</at> '+MESSAGE_TEXT)
        self.assertEqual(calls[1][0],'PATCH')
        self.assertEqual(calls[1][2],{'user_id_list':['ou_self']})
        self.assertIn('/urgent_phone?user_id_type=open_id',calls[1][1])

    def test_ai_filter_and_single_fetch_unchanged(self):
        fake=FakeClient();m=self.manager('all',fake)
        with patch.object(core,'fetch_chat_messages',return_value=[card(1),card(2,'告警')]) as fetch,patch.object(watch,'log'):
            cards=watch.fetch_cards(object(),notifier=m)
            self.assertEqual(len(cards),1);fetch.assert_called_once()

    def test_notify_failure_does_not_break_ai_filter(self):
        manager=SimpleNamespace(observe=lambda msgs: (_ for _ in ()).throw(OSError('disk error')))
        with patch.object(core,'fetch_chat_messages',return_value=[card(1)]),patch.object(watch,'log'):
            self.assertEqual(len(watch.fetch_cards(object(),notifier=manager)),1)

    def test_notify_not_blocked_by_ai_workers_full(self):
        c=FakeClient();m=self.manager('human',c);m.observe([card(1)])
        with (patch.object(core,'fetch_chat_messages',return_value=[card(2)]),
              patch.object(watch,'locate_group',return_value=(
                  {'chat_id':'oc_fixture'},None,{'ticket_metadata_loaded':True,'form_type':'Request'},'ticket_page')),
              patch.object(watch,'save_state'),patch.object(watch,'log'),patch.object(watch,'start_ai_job') as ai):
            state={'seen':[],'pending':{}}
            watch.process_new(object(),None,SimpleNamespace(workers=1),state,{'busy':{}},notifier=m)
            ai.assert_not_called();self.assertEqual(len(c.urgents),1)

    def test_background_delivery_does_not_block_observe(self):
        import threading
        release=threading.Event()
        class SlowClient(FakeClient):
            def send(self,key):
                release.wait(3);return super().send(key)
        m=self.manager('all',SlowClient(),concurrent.futures.ThreadPoolExecutor(max_workers=2))
        try:
            m.observe([card(1)]);start=time.monotonic();m.observe([card(2)]);m.poll()
            self.assertLess(time.monotonic()-start,1)
        finally:release.set();m.close()

    def test_start_sh_real_entry_argument_forwarding_and_invalid(self):
        project=Path(__file__).resolve().parent
        (self.root/'start.sh').write_text((project/'start.sh').read_text())
        (self.root/'read_oncall_chat.py').write_text('CHAT_ID = "configured"\n')
        (self.root/'fetch_glata_auto.py').write_text('CHAT_ID = "configured"\n')
        bindir=self.root/'fakebin';bindir.mkdir()
        fakepy=bindir/'python3'
        fakepy.write_text('#!/bin/bash\nif [ "$1" = "-c" ]; then exit 0; fi\nif [ "$1" = "watch_and_run.py" ]; then printf "%s\\n" "$@" > "$TEST_ARGS"; exec /bin/sleep 30; fi\nexit 0\n')
        fakepy.chmod(0o755)
        fakecurl=bindir/'curl';fakecurl.write_text('#!/bin/bash\nprintf "glata.bytedance.com"\n');fakecurl.chmod(0o755)
        env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],GLATA_BG='1',TEST_ARGS=str(self.root/'args.txt'))
        # 使用真实Python进程保留watch_and_run.py参数；sleep会丢argv，无法验收PID身份校验。
        sleeper=bindir/'watch_and_run.py'
        sleeper.write_text('import time\ntime.sleep(30)\n')
        fakepy.write_text('#!/bin/bash\nif [ "$1" = "watch_and_run.py" ]; then printf "%s\\n" "$@" > "$TEST_ARGS"; shift; exec '+__import__('shlex').quote(__import__('sys').executable)+' '+__import__('shlex').quote(str(sleeper))+' "$@"; fi\nexit 0\n')
        for argv,expected in [([],None),(['-at'],'human'),(['-at','all'],'all')]:
            r=subprocess.run(['/bin/bash','start.sh']+argv,cwd=self.root,env=env,capture_output=True,text=True,timeout=8)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            args=(self.root/'args.txt').read_text().splitlines()
            mismatch=['-at','all'] if expected!='all' else ['-at']
            wrong=subprocess.run(['bash','start.sh']+mismatch,cwd=self.root,env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(wrong.returncode,2)
            self.assertIn('参数未生效',wrong.stdout)
            self.assertEqual(args[-2:],['--notify-mode',expected or 'off'])
            pid=int((self.root/'.watch.pid').read_text());os.kill(pid,15)
            (self.root/'.watch.pid').unlink()
        for argv in [['-at','bad'],['-unknown'],['-at','all','extra']]:
            r=subprocess.run(['bash','start.sh']+argv,cwd=self.root,env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,2)


if __name__=='__main__':unittest.main(verbosity=2)
