#!/usr/bin/env python3
"""发布白名单与文本隐私检查；不会打印命中的原值。

覆盖常见用户/会话/应用标识、内部文档链接、密钥字段赋值与本机身份值。
不声称覆盖所有秘密；需人工 diff 审阅。Git 模式检查 blob 而非仅工作目录。
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES = {
    "真实资源标识": re.compile(r"\b(?:oc|ou|om|cli)_[0-9a-f]{16,}\b", re.I),
    "内部文档链接": re.compile(r"https://[^\s\"<>]*(?:larkoffice|feishu)\.(?:com|cn)/(?:wiki|docx|docs)/[A-Za-z0-9]{10,}"),
    "私钥正文": re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"),
    "密钥字面量": re.compile(r"[\"']?(?:app_secret|user_access_token|tenant_access_token)[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_-]{16,256})[\"']", re.I),
}


def release_files(root=ROOT):
    files = [x.strip() for x in (root / "release_files.txt").read_text().splitlines()
             if x.strip() and not x.lstrip().startswith("#")]
    if len(files) != len(set(files)):
        raise ValueError("发行白名单有重复")
    for name in files:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or not (root / p).is_file() or (root / p).is_symlink():
            raise ValueError("发行白名单路径无效或文件缺失：" + name)
    return files


def scan_text(text, private_values=()):
    errors = [label for label, pattern in RULES.items() if pattern.search(text)]
    if any(value and value in text for value in private_values):
        errors.append("本机个人配置值")
    return errors


def local_private_values(root):
    path = root / "config.local.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    values = []
    for value in data.values():
        values.extend(value if isinstance(value, list) else [value])
    return [v for v in values if isinstance(v, str) and len(v) >= 2]


def git(root, args):
    return subprocess.check_output(["git", "-C", str(root)] + args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="审查暂存区文件与内容")
    group.add_argument("--tracked", action="store_true", help="审查当前提交树文件与内容")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        allowed = release_files(root)
        private = local_private_values(root)
        names = allowed
        prefix = None
        if args.staged:
            names = git(root, ["ls-files", "-z"]).decode().strip("\0").split("\0")
            prefix = ":"
        elif args.tracked:
            names = git(root, ["ls-tree", "-rz", "--name-only", "HEAD"]).decode().strip("\0").split("\0")
            prefix = "HEAD:"
        errors = []
        if prefix:
            for name in sorted(set(names) - set(allowed)):
                errors.append((name, "不在发行白名单"))
            for name in sorted(set(allowed) - set(names)):
                errors.append((name, "发行文件未跟踪"))
        for name in names:
            if not name:
                continue
            raw = git(root, ["show", prefix + name]) if prefix else (root / name).read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                errors.append((name, "非 UTF-8 文本/二进制不可发行"))
                continue
            for label in scan_text(text, private):
                errors.append((name, label))
        if errors:
            for name, label in errors:
                print("[错误] %s：%s（未输出原值）" % (name, label))
            return 1
        print("发行检查通过：%d 个白名单文本文件；未命中所覆盖隐私规则" % len(names))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("[错误] 发行检查未完成：%s" % type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
