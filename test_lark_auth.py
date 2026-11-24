"""后台授权回归：合成响应，不读取真实凭证/消息，不发电话。"""
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import read_oncall_chat as core
import deploy_check as deploy
import project_config as config


class AuthTests(unittest.TestCase):
    def test_user_flag_is_mandatory_and_bot_rejected(self):
        self.assertEqual(core.lark_user_args(['im', '+chat-list'])[-2:], ['--as','user'])
        self.assertEqual(core.lark_user_args(['im','--as','user']).count('--as'), 1)
        for args in (['im','--as','bot'], ['im','--as=bot'], ['im','--as'],
                     ['im','--as','user','--as','bot']):
            with self.assertRaises(ValueError):core.lark_user_args(args)

    def test_private_doc_screenshot_links_block_release(self):
        from check_release import scan_text
        self.assertIn('飞书私有素材链接',scan_text('https://feishu.cn/file/'+'FixtureToken'*3))

    def test_subprocess_forces_user_and_cleans_only_lark_environment(self):
        response=SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'identity':'user','data':{}}),stderr='')
        with patch.dict(os.environ, {'DOUBAO_OFFICE_USER_ACCESS_TOKEN':'fixture','KEEP_THIS':'yes'}),patch.object(core.subprocess,'run',return_value=response) as run:
            core.LarkRunner()._run('/fixture/cli',['im','+chat-list'])
            self.assertEqual(run.call_args.args[0][-2:],['--as','user'])
            self.assertNotIn('DOUBAO_OFFICE_USER_ACCESS_TOKEN',run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.kwargs['env']['KEEP_THIS'],'yes')
            self.assertIn('DOUBAO_OFFICE_USER_ACCESS_TOKEN',os.environ)

    def test_bot_success_cannot_be_consumed_as_user(self):
        response=SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'identity':'bot','data':{}}),stderr='')
        with patch.object(core.subprocess,'run',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'不是 user'):
                core.LarkRunner()._run('/fixture/cli',['im','+chat-list'])

    def test_auth_error_does_not_echo_private_values(self):
        response=json.dumps({'error':{'type':'authentication','message':'need_user_authorization (user: ou_fixture)','hint':'private-link'}})
        error=core.safe_lark_error(response,3)
        self.assertNotIn('ou_fixture',error);self.assertNotIn('private-link',error)
        self.assertIn('OAuth',error)

    def test_pinned_executable_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli=Path(tmp)/'cli';cli.write_text('fixture');cli.chmod(0o755)
            with patch.object(core,'get_setting',return_value=str(cli)),patch.object(core.glob,'glob') as glob:
                self.assertEqual(core.find_lark_clis(),[str(cli)]);glob.assert_not_called()
            with patch.object(core,'get_setting',return_value=str(cli)+'-missing'),patch.object(core.glob,'glob') as glob:
                with self.assertRaisesRegex(RuntimeError,'不会回退'):core.find_lark_clis()
                glob.assert_not_called()

    def fake_runner(self, user='ou_fixture', name='测试本人', messages=None):
        runner=Mock()
        runner.call.side_effect=[{'data':{'users':[{'open_id':user,'localized_name':name}]}}, {'data':{'messages':[] if messages is None else messages}}]
        return runner

    def settings(self,key,default=''):
        return {'target_user_id':'ou_fixture','target_user_name':'测试本人','chat_id':'oc_fixture'}.get(key,default)

    def test_preflight_success_is_read_only_and_redacted(self):
        runner=self.fake_runner();out=io.StringIO()
        with patch.object(config,'check_config'),patch.object(config,'get_setting',side_effect=self.settings),patch.object(core,'LarkRunner',return_value=runner),contextlib.redirect_stdout(out):
            self.assertEqual(deploy.preflight(),0)
        self.assertEqual(runner.call.call_count,2)
        self.assertIn('--page-size',runner.call.call_args.args[0])
        self.assertNotIn('ou_fixture',out.getvalue());self.assertNotIn('oc_fixture',out.getvalue())

    def test_preflight_other_user_rejected_before_chat_read(self):
        runner=self.fake_runner(user='ou_other')
        with patch.object(config,'check_config'),patch.object(config,'get_setting',side_effect=self.settings),patch.object(core,'LarkRunner',return_value=runner):
            with self.assertRaisesRegex(RuntimeError,'不一致'):deploy.preflight()
        self.assertEqual(runner.call.call_count,1)

    def test_preflight_missing_messages_is_not_success(self):
        runner=self.fake_runner();runner.call.side_effect=[{'data':{'users':[{'open_id':'ou_fixture','localized_name':'测试本人'}]}},{'data':{}}]
        with patch.object(config,'check_config'),patch.object(config,'get_setting',side_effect=self.settings),patch.object(core,'LarkRunner',return_value=runner):
            with self.assertRaisesRegex(RuntimeError,'messages'):deploy.preflight()

    def test_real_start_auth_failure_never_starts_browser_or_monitor(self):
        project=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'start.sh').write_text((project/'start.sh').read_text())
            (root/'project_config.py').write_text('raise SystemExit(0)\n')
            (root/'deploy_check.py').write_text('print("[错误] 合成授权失效");raise SystemExit(4)\n')
            (root/'cdp_client.py').write_text('raise RuntimeError("不应启动")\n')
            bindir=root/'bin';bindir.mkdir();wrapper=bindir/'python3'
            wrapper.write_text('#!/bin/bash\nexec '+shlex.quote(sys.executable)+' "$@"\n');wrapper.chmod(0o755)
            env=dict(os.environ,PATH=str(bindir)+os.pathsep+'/usr/bin:/bin',GLATA_BG='1')
            result=subprocess.run(['/bin/bash','start.sh'],cwd=root,env=env,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,4,result.stdout+result.stderr)
            self.assertNotIn('不应启动',result.stderr)
            self.assertFalse((root/'.watch.pid').exists())
            # 旧僵尸/PID复用也不能绕过授权检查并打印已运行。
            (root/'.watch.pid').write_text(str(os.getpid()))
            result=subprocess.run(['/bin/bash','start.sh'],cwd=root,env=env,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,4)
            self.assertNotIn('监控已在运行',result.stdout)

    def test_legacy_fetch_and_deploy_share_user_runner(self):
        import fetch_glata_auto as fetch
        with patch.object(core.LarkRunner,'_run',return_value={'data':{'messages':[]}}) as run:
            self.assertEqual(fetch.fetch_messages('/fixture/cli'),[])
            deploy.lark_json('/fixture/cli',['im','+chat-list'])
        self.assertEqual(run.call_count,2)

    def test_download_user_and_relative_path_preserve_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner=SimpleNamespace(lark='/fixture/cli',candidates=[])
            def download(cmd,**kwargs):
                self.assertIn('--as',cmd);self.assertEqual(cmd[cmd.index('--as')+1],'user')
                self.assertFalse(os.path.isabs(cmd[-1]))
                (Path(kwargs['cwd'])/cmd[-1]).write_bytes(b'fixture')
                return SimpleNamespace(returncode=0)
            with patch.object(core,'IMG_DIR',tmp),patch.object(core,'_ocr_cache',return_value={}),patch.object(core,'_save_ocr_cache'),patch.object(core,'_ocr_vision',return_value='fixture error'),patch.object(core.subprocess,'run',side_effect=download):
                self.assertEqual(core.ocr_image(runner,'om_fixture','img_fixture'),'fixture error')


class AuthRecoveryTests(unittest.TestCase):
    """只用合成 CLI/终端，不发起真实授权、不读钥匙串。"""
    url = 'https://accounts.feishu.cn/oauth/v1/device/verify?flow_id=fixture&user_code=FIXTURE'

    def test_auth_error_preserves_class_and_redacts_raw_fields(self):
        raw = json.dumps({'error': {'type': 'authentication', 'subtype': 'token_missing',
                                    'message': 'ou_fixture token=secret', 'hint': self.url}})
        response = SimpleNamespace(returncode=3, stdout=raw, stderr='')
        with patch.object(core.subprocess, 'run', return_value=response):
            with self.assertRaises(core.LarkAuthError) as failure:
                core.LarkRunner()._run('/fixture/cli', ['contact', '+search-user'])
        self.assertIn('找不到', failure.exception.reason)
        self.assertNotIn('secret', str(failure.exception))
        self.assertNotIn(self.url, str(failure.exception))
        self.assertNotIn('ou_fixture', str(failure.exception))

    def test_auth_failure_never_falls_back_to_other_cli(self):
        with patch.object(core, 'find_lark_clis', return_value=['/first', '/second']), \
                patch.object(core.LarkRunner, '_run', side_effect=core.LarkAuthError('token_missing')) as run:
            with self.assertRaises(core.LarkAuthError): core.LarkRunner().call(['contact'])
            self.assertEqual(run.call_count, 1)

    def test_noninteractive_start_never_requests_authorization(self):
        out = io.StringIO()
        with patch.object(sys.stdin, 'isatty', return_value=False), \
                patch.object(deploy, 'auth_json') as auth, contextlib.redirect_stdout(out):
            self.assertEqual(deploy.recover_user_auth(core.LarkAuthError('token_missing')), 4)
            auth.assert_not_called()
        self.assertIn('找不到', out.getvalue())
        self.assertIn('本机终端', out.getvalue())

    def test_plain_preflight_stays_readonly(self):
        with patch.object(sys, 'argv', ['deploy_check.py', '--preflight']), \
                patch.object(deploy, 'preflight', side_effect=core.LarkAuthError('token_missing')), \
                patch.object(deploy, 'recover_user_auth') as recover, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy.main(), 4); recover.assert_not_called()

    def test_non_auth_failure_never_requests_oauth(self):
        with patch.object(sys, 'argv', ['deploy_check.py', '--preflight', '--auth-on-failure']), \
                patch.object(deploy, 'preflight', side_effect=RuntimeError('Glata 不可读')), \
                patch.object(deploy, 'recover_user_auth') as recover, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy.main(), 4); recover.assert_not_called()

    def run_recovery(self, url=None, poll_error=None, qr_fail=False, final_error=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); events = []; out = io.StringIO()
            calls = [core.LarkAuthError('token_missing'), final_error or 0]
            def run(cmd, **kwargs):
                events.append(cmd)
                if 'qrcode' in cmd and not qr_fail:
                    (root / cmd[cmd.index('--output') + 1]).write_bytes(b'fixture-png')
                return SimpleNamespace(returncode=1 if qr_fail and 'qrcode' in cmd else 0, stdout='{}', stderr='')
            auth_result = [{'verification_url': url or self.url, 'device_code': 'fixture-device'},
                           poll_error or {'ok': True}]
            with patch.object(sys.stdin, 'isatty', return_value=True), \
                    patch.object(core, 'find_lark_clis', return_value=['/fixture/cli']), \
                    patch.object(deploy, 'ROOT', str(root)), \
                    patch.object(deploy, 'preflight', side_effect=calls), \
                    patch.object(deploy, 'auth_json', side_effect=auth_result) as auth, \
                    patch.object(deploy.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(out):
                try:
                    value = deploy.recover_user_auth(core.LarkAuthError('token_missing'))
                except BaseException as exc:
                    value = exc
                self.assertFalse(list(root.rglob('*.png')), '临时二维码必须清理')
                return value, out.getvalue(), events, auth.call_args_list

    def test_interactive_link_qr_poll_and_recheck(self):
        value, text, events, calls = self.run_recovery()
        self.assertEqual(value, 0)
        self.assertIn(self.url, text)
        self.assertNotIn('fixture-device', text)
        self.assertEqual(events[0][1:3], ['auth', 'qrcode'])
        self.assertEqual(events[0][3], self.url)
        self.assertFalse(os.path.isabs(events[0][events[0].index('--output') + 1]))
        self.assertEqual(events[1][0], 'open')
        self.assertIn('--device-code', calls[1].args[1])
        self.assertIn('授权已保存', text)

    def test_qr_failure_still_gives_link_without_false_success(self):
        value, text, _, _ = self.run_recovery(qr_fail=True, poll_error=RuntimeError('授权过期'))
        self.assertIsInstance(value, RuntimeError)
        self.assertIn('二维码生成失败', text)
        self.assertIn(self.url, text)
        self.assertNotIn('授权已保存', text)

    def test_cancel_or_wrong_identity_does_not_continue(self):
        for opts in ({'poll_error': KeyboardInterrupt()}, {'final_error': RuntimeError('本人不一致')}):
            value, text, _, _ = self.run_recovery(**opts)
            self.assertIsInstance(value, BaseException)
            self.assertNotIn('授权已保存', text)

    def test_bad_verification_url_not_displayed_or_opened(self):
        for url in ('http://accounts.feishu.cn/fixture', 'https://evil.example/fixture',
                    'https://accounts.feishu.cn@evil.example/fixture', self.url + '\nunsafe'):
            value, text, events, _ = self.run_recovery(url=url)
            self.assertIsInstance(value, RuntimeError)
            self.assertNotIn(url, text)
            self.assertEqual(events, [])

    def test_real_terminal_recovery_with_fake_cli(self):
        import pty
        project = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for file in ('start.sh', 'deploy_check.py', 'read_oncall_chat.py', 'project_config.py'):
                (root / file).write_text((project / file).read_text())
            (root / 'bin').mkdir()
            cli = root / 'bin' / 'lark-cli'
            cli.write_text('#!' + sys.executable + '\n' + '''import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
a = sys.argv[1:]
with (root/'events').open('a') as f: f.write(' '.join(a[:3])+'\\n')
if a[:2] == ['auth', 'login']:
    if '--no-wait' in a:
        print(json.dumps({'verification_url':'https://accounts.feishu.cn/oauth/v1/device/verify?user_code=FIXTURE','device_code':'fixture-device'}))
    else:
        (root/'authorized').touch(); print(json.dumps({'ok':True}))
elif a[:2] == ['auth', 'qrcode']:
    Path(a[a.index('--output')+1]).write_bytes(b'fixture-png'); print(json.dumps({'ok':True}))
elif not (root/'authorized').exists():
    print(json.dumps({'error':{'type':'authentication','subtype':'token_missing'}})); sys.exit(3)
elif a[0] == 'contact':
    print(json.dumps({'ok':True,'identity':'user','data':{'users':[{'open_id':'ou_fixture','localized_name':'测试本人'}]}}))
else:
    print(json.dumps({'ok':True,'identity':'user','data':{'messages':[]}}))
'''); cli.chmod(0o755)
            opener = root / 'bin' / 'open'
            opener.write_text('#!/bin/sh\nexit 0\n'); opener.chmod(0o755)
            (root / 'config.local.json').write_text(json.dumps({'lark_cli_bin':str(cli), 'chat_id':'oc_fixture',
                    'target_user_id':'ou_fixture','target_user_name':'测试本人'}))
            env = {k:v for k,v in os.environ.items() if not k.startswith(('GLATA_', 'LARK_', 'DOUBAO_OFFICE_'))}
            env['PATH'] = str(root/'bin') + os.pathsep + env.get('PATH', '')
            master, slave = pty.openpty()
            try:
                result = subprocess.run([sys.executable,'deploy_check.py','--preflight','--auth-on-failure'],
                            cwd=root, env=env, stdin=slave, capture_output=True, text=True, timeout=20)
            finally:
                os.close(master); os.close(slave)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('本机找不到', result.stdout)
            self.assertIn('https://accounts.feishu.cn/', result.stdout)
            self.assertIn('二维码：', result.stdout)
            self.assertIn('授权已保存', result.stdout)
            self.assertNotIn('fixture-device', result.stdout)
            self.assertTrue((root/'authorized').exists())
            self.assertIn('auth qrcode', (root/'events').read_text())
            self.assertFalse(list((root/'logs').glob('*.png')))
            self.assertFalse((root/'.watch.pid').exists())
            # 同一个真实 start.sh 入口：扫码流程成功后再启动合成监控。
            (root/'authorized').unlink()
            (root/'cdp_client.py').write_text('print("fixture browser ready")\n')
            (root/'watch_and_run.py').write_text('import time; time.sleep(30)\n')
            wrapper = root/'bin'/'python3'
            wrapper.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' "$@"\n'); wrapper.chmod(0o755)
            env['GLATA_BG'] = '1'
            master, slave = pty.openpty()
            try:
                result = subprocess.run(['/bin/bash','start.sh'], cwd=root, env=env, stdin=slave,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('监控已启动', result.stdout)
                self.assertLess(result.stdout.index('授权已保存'), result.stdout.index('fixture browser ready'))
                self.assertTrue((root/'.watch.pid').exists())
            finally:
                os.close(master); os.close(slave)
                if (root/'.watch.pid').exists():
                    import signal
                    try: os.kill(int((root/'.watch.pid').read_text()), signal.SIGTERM)
                    except ProcessLookupError: pass


if __name__=='__main__':unittest.main()
