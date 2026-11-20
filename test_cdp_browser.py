#!/usr/bin/env python3
"""浏览器生命周期离线回归：不访问工单，不读取真实profile或凭证。"""
import concurrent.futures
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import cdp_client as cdp


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="glata-browser-test-")
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()

    def test_default_command_headless_localhost_and_profile(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cdp.browser_mode(), 'headless')
        command = cdp._browser_command('headless')
        self.assertIn('--headless=new', command)
        self.assertIn('--remote-debugging-address=127.0.0.1', command)
        self.assertIn('--user-data-dir='+cdp.PROFILE_DIR, command)
        self.assertEqual(command[-1], 'about:blank')
        self.assertNotIn('--no-sandbox', command)
        self.assertNotIn('--ignore-certificate-errors', command)

    def test_headed_requires_explicit_mode(self):
        self.assertNotIn('--headless=new', cdp._browser_command('headed'))
        with patch.dict(os.environ, {'GLATA_BROWSER_MODE':'invalid'}):
            with self.assertRaises(ValueError):cdp.browser_mode()

    def test_existing_browser_reused_without_launch_or_page_creation(self):
        with patch.object(cdp,'is_alive',return_value=True),patch.object(cdp,'running_browser_mode',return_value='headless'),patch.object(cdp.subprocess,'Popen') as popen,patch.object(cdp,'_open_page') as page:
            self.assertTrue(cdp.ensure_chrome(mode='headless'))
            self.assertEqual(cdp.main(['--ensure-browser']),0)
            popen.assert_not_called();page.assert_not_called()

    def test_running_headless_not_restarted_for_login(self):
        with patch.object(cdp,'is_alive',return_value=True),patch.object(cdp,'running_browser_mode',return_value='headless'),patch.object(cdp.subprocess,'Popen') as popen:
            with self.assertRaises(RuntimeError):cdp.ensure_chrome(mode='headed')
            popen.assert_not_called()

    def test_auto_recovery_uses_same_headless_command(self):
        with patch.object(cdp,'PROFILE_DIR',str(self.root/'profile')),patch.object(cdp.os.path,'isfile',return_value=True),patch.object(cdp,'is_alive',side_effect=[False,False,False,True]),patch.object(cdp.subprocess,'Popen',return_value=Mock(poll=lambda:None)) as popen,patch.object(cdp.time,'sleep'):
            self.assertTrue(cdp.ensure_chrome(mode='headless'))
            self.assertIn('--headless=new',popen.call_args.args[0])
            self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_two_callers_launch_only_once(self):
        alive=False
        def probe():return alive
        def launch(*args,**kwargs):
            nonlocal alive
            time.sleep(0.05);alive=True
            return Mock(poll=lambda:None)
        with patch.object(cdp,'PROFILE_DIR',str(self.root/'profile')),patch.object(cdp.os.path,'isfile',return_value=True),patch.object(cdp,'is_alive',side_effect=probe),patch.object(cdp.subprocess,'Popen',side_effect=launch) as popen:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:cdp.ensure_chrome(mode='headless'),range(2)))
            self.assertEqual(results,[True,True]);self.assertEqual(popen.call_count,1)

    def test_start_failure_stops_instead_of_deleting_profile(self):
        profile=self.root/'profile';profile.mkdir();marker=profile/'keep';marker.write_text('keep')
        with patch.object(cdp,'PROFILE_DIR',str(profile)),patch.object(cdp.os.path,'isfile',return_value=True),patch.object(cdp,'is_alive',return_value=False),patch.object(cdp.subprocess,'Popen',return_value=Mock(poll=lambda:1)):
            self.assertFalse(cdp.ensure_chrome(wait=.1,mode='headless'))
        self.assertEqual(marker.read_text(),'keep')

    def test_stop_matches_exact_port_profile_and_executable(self):
        import signal
        # ps returns unquoted paths; exact Chrome path contains spaces.
        command='%s --remote-debugging-port=%s --user-data-dir=%s' % (cdp.CHROME_BIN,cdp.CDP_PORT,cdp.PROFILE_DIR)
        output='101 '+command+'\n102 '+command.replace(cdp.PROFILE_DIR,'/other/profile')+'\n103 sleep 30\n'
        with patch.object(cdp.subprocess,'run',return_value=Mock(stdout=output)),patch.object(cdp.os,'kill') as kill,patch.object(cdp,'is_alive',return_value=False):
            self.assertTrue(cdp.stop_browser())
            kill.assert_called_once_with(101,signal.SIGTERM)

    def test_original_parser_and_relogin_message(self):
        fields=cdp.parse_fields('值班号会话编号\noc_'+'a'*32+'\n转人工时间\n2026-11-18 10:00:00\n表单类型\nRequest')
        self.assertEqual(fields['transfer_time'],'2026-11-18 10:00:00')
        self.assertEqual(fields['form_type'],'Request')
        self.assertIn('GLATA_BROWSER_MODE=headed',cdp.login_help())
        self.assertIn('飞书 CLI',cdp.login_help())

    def test_start_shell_checks_browser_once_and_never_opens_page_itself(self):
        project=Path(__file__).resolve().parent
        (self.root/'start.sh').write_text((project/'start.sh').read_text())
        for name in ('fetch_glata_auto.py','read_oncall_chat.py'):
            (self.root/name).write_text('CHAT_ID = "configured"\n')
        bindir=self.root/'bin';bindir.mkdir()
        fake=bindir/'python3'
        fake.write_text('#!/bin/bash\nprintf "%s|%s|%s\\n" "$1" "${2:-}" "${GLATA_BROWSER_MODE:-headless}" >> "$TEST_TRACE"\nif [ "$1" = "cdp_client.py" ]; then exit "${TEST_BROWSER_RC:-0}"; fi\nif [ "$1" = "watch_and_run.py" ]; then exec /bin/sleep 30; fi\nexit 0\n')
        fake.chmod(0o755)
        curl=bindir/'curl';curl.write_text('#!/bin/bash\nexit 99\n');curl.chmod(0o755)
        env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],GLATA_BG='1',TEST_TRACE=str(self.root/'trace'),GLATA_BROWSER_MODE='headless')
        for args,mode in [([], 'headless'),(['-at'],'headless'),(['-at','all'],'headless'),([], 'headed')]:
            env['GLATA_BROWSER_MODE']=mode
            result=subprocess.run(['/bin/bash','start.sh']+args,cwd=self.root,env=env,text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            pid=int((self.root/'.watch.pid').read_text());os.kill(pid,15);(self.root/'.watch.pid').unlink()
        trace=(self.root/'trace').read_text()
        self.assertEqual(trace.count('cdp_client.py|--ensure-browser'),4)
        env['TEST_BROWSER_RC']='4'
        result=subprocess.run(['/bin/bash','start.sh'],cwd=self.root,env=env,text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,4);self.assertFalse((self.root/'.watch.pid').exists())


if __name__=='__main__':unittest.main(verbosity=2)
