#!/bin/bash
# 使用共享发行白名单打包，不含本机配置、凭证、业务数据与二进制。
set -e
cd "$(dirname "$0")"
python3 check_release.py
python3 - <<'PY'
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from check_release import ROOT, release_files

files = release_files()
outdir = ROOT / 'dist'
outdir.mkdir(exist_ok=True)
out = outdir / 'glata_watch_dist.tar.gz'
# 临时目录为本次唯一创建，只清理本次自己的临时文件，不碰固定 /tmp 目录。
with tempfile.TemporaryDirectory(prefix='glata-release-') as tmp:
    stage = Path(tmp) / 'glata_watch'
    stage.mkdir()
    for name in files:
        dest = stage / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith('GLATA_'):
            env.pop(key)
    env['GLATA_CONFIG_FILE'] = str(stage / 'absent.local.json')
    subprocess.run([sys.executable, 'check_release.py'], cwd=stage, env=env, check=True)
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-p', 'test_*.py'],
                   cwd=stage, env=env, check=True)
    temporary = Path(tmp) / 'release.tar.gz'
    with tarfile.open(temporary, 'w:gz') as archive:
        # 不打包测试产生的 pycache 或任何运行文件，只逐项添加发行白名单。
        for name in files:
            archive.add(stage / name, arcname='glata_watch/' + name, recursive=False)
    with tarfile.open(temporary, 'r:gz') as archive:
        names = {member.name for member in archive.getmembers()}
        assert names == {'glata_watch/' + name for name in files}
    shutil.copy2(temporary, out)
print('已生成干净分发包：' + str(out))
print('白名单文件：%d；无本机配置；干净目录离线回归通过' % len(files))
PY
