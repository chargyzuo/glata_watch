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


if __name__=='__main__':unittest.main()
