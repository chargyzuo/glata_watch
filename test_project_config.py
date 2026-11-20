"""集中配置与空输入回归：只使用合成值，不请求接口。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import project_config as cfg
import read_oncall_chat as core
from check_release import scan_text


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='glata-config-test-')
        self.root = Path(self.tmp.name)
        self.path = self.root / 'config.json'
        self.env = patch.dict(os.environ, {'GLATA_CONFIG_FILE': str(self.path)}, clear=True)
        self.env.start()
        cfg.load_local.cache_clear()

    def tearDown(self):
        cfg.load_local.cache_clear()
        self.env.stop()
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        cfg.load_local.cache_clear()

    def valid(self):
        return {'chat_id':'oc_fixture', 'target_user_name':'测试本人',
                'target_user_id':'ou_fixture', 'target_user_ids':['ou_second']}

    def test_missing_config_rejected_without_network(self):
        self.assertEqual(cfg.get_setting('chat_id'), '')
        with self.assertRaises(ValueError):cfg.check_config()

    def test_json_types_invalid_and_error_does_not_echo_content(self):
        self.path.write_text('{private malformed}')
        with self.assertRaises(ValueError) as err:cfg.load_local()
        self.assertNotIn('private', str(err.exception))
        self.write([])
        with self.assertRaises(ValueError):cfg.load_local()
        self.write(dict(self.valid(), target_user_ids=[123]))
        with self.assertRaises(ValueError):cfg.check_config()

    def test_valid_config_and_primary_merge(self):
        self.write(self.valid());cfg.check_config()
        self.assertEqual(cfg.user_ids(), {'ou_fixture', 'ou_second'})

    def test_environment_overrides_cached_json(self):
        self.write(self.valid())
        self.assertEqual(cfg.get_setting('chat_id'), 'oc_fixture')
        with patch.dict(os.environ, {'GLATA_CHAT_ID':'oc_override','GLATA_TARGET_USER_IDS':'ou_env,ou_third'}):
            self.assertEqual(cfg.get_setting('chat_id'), 'oc_override')
            self.assertEqual(cfg.user_ids(), {'ou_fixture','ou_env','ou_third'})

    def test_json_cached_once_until_restart_or_clear(self):
        self.write(self.valid());cfg.get_setting('chat_id')
        self.path.write_text('{}')
        self.assertEqual(cfg.get_setting('chat_id'), 'oc_fixture')
        cfg.load_local.cache_clear()
        self.assertEqual(cfg.get_setting('chat_id'), '')

    def test_optional_invalid_placeholder_rejected(self):
        self.write(dict(self.valid(), notify_chat_id='oc_替换'))
        with self.assertRaises(ValueError):cfg.check_config()

    def test_empty_identity_never_matches_unnamed_mention(self):
        with patch.multiple(core, TARGET_USER_NAME='', TARGET_USER_IDS=set()):
            self.assertFalse(core.mentions_me({'mentions':[{'id':'ou_other'}]}))

    def test_real_start_missing_config_fails_before_browser(self):
        project = Path(__file__).resolve().parent
        for name in ('start.sh', 'project_config.py'):
            (self.root / name).write_text((project / name).read_text())
        env = dict(os.environ, PATH=os.environ.get('PATH', '/usr/bin:/bin'), GLATA_BG='1')
        result = subprocess.run(['/bin/bash','start.sh'], cwd=self.root, env=env,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertFalse((self.root / '.watch.pid').exists())
        self.assertNotIn('浏览器已', result.stdout)

    def test_release_rules_find_generated_identity_and_secret_without_echo(self):
        self.assertTrue(scan_text('oc_' + 'a' * 32))
        self.assertTrue(scan_text('app_secret = "' + 'Z'*32 + '"'))
        self.assertTrue(scan_text('说明 contains 个人名', ['个人名']))
        self.assertFalse(scan_text('合成：oc_fixture；配置由本机读取'))


if __name__ == '__main__':unittest.main()
