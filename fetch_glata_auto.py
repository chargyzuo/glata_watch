#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Glata 群消息自动增量拉取（LaunchAgent 常驻调度版）

鉴权方式: 通过 lark-cli 的钥匙串登录态，不依赖豆包会话注入的 token，
          因此在 launchd 环境下也能长期运行。
群:       <Glata p2p 会话 chat_id>
过滤:     @本人 且卡片标题含「你有一条新工单需要处理」
增量:     message_position 游标本地比较

用法: python3 fetch_glata_auto.py
"""
import datetime
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import time

from project_config import get_setting

CHAT_ID = get_setting("chat_id")
TARGET_USER_ID = get_setting("target_user_id")
TARGET_USER_NAME = get_setting("target_user_name")
NEW_TICKET_KEYWORD = "你有一条新工单需要处理"
PAGE_SIZE = 50

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CURSOR_FILE = os.path.join(BASE_DIR, ".last_position")
RAW_FILE = os.path.join(BASE_DIR, "glata_messages.jsonl")
FILTERED_FILE = os.path.join(BASE_DIR, "new_tickets.jsonl")
SUMMARY_FILE = os.path.join(BASE_DIR, "new_tickets_summary.log")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOCK_FILE = os.path.join(BASE_DIR, ".run.lock")


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "glata-%s.log" % datetime.date.today().isoformat())
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def rotate_logs():
    """清理 30 天前的每日日志；launchd.log 超过 2MB 时只保留最后 500 行"""
    try:
        cutoff = datetime.date.today() - datetime.timedelta(days=30)
        for p in glob.glob(os.path.join(LOG_DIR, "glata-*.log")):
            name = os.path.basename(p)[len("glata-"):-len(".log")]
            try:
                if datetime.date.fromisoformat(name) < cutoff:
                    os.remove(p)
            except (ValueError, OSError):
                continue
        launchd_log = os.path.join(LOG_DIR, "launchd.log")
        if os.path.exists(launchd_log) and os.path.getsize(launchd_log) > 2 * 1024 * 1024:
            with open(launchd_log, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-500:]
            with open(launchd_log, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception:
        pass


def find_lark_clis():
    """与主监控共用固定 CLI，不因授权失效换应用。"""
    import read_oncall_chat as core
    return core.find_lark_clis()


def read_cursor():
    if not os.path.exists(CURSOR_FILE):
        return 0
    try:
        return int(open(CURSOR_FILE).read().strip())
    except (ValueError, OSError):
        return 0


def write_cursor(pos):
    with open(CURSOR_FILE, "w") as f:
        f.write(str(pos))


def fetch_messages(lark):
    import read_oncall_chat as core
    cmd = ["im", "+chat-messages-list",
           "--chat-id", CHAT_ID,
           "--page-size", str(PAGE_SIZE),
           "--format", "json", "--no-reactions"]
    d = core.LarkRunner()._run(lark, cmd)
    return (d.get("data") or {}).get("messages") or []


def card_title(content):
    if not content:
        return ""
    m = re.match(r'<card\s+title="([^"]*)"', content)
    if m:
        return m.group(1)
    try:
        return json.loads(content).get("title", "") or ""
    except Exception:
        return ""


def card_text(content):
    """把 <card title="...">...</card> 转成可读多行文本"""
    if not content:
        return ""
    title = card_title(content)
    body = re.sub(r'^<card[^>]*>', '', content, count=1)
    body = re.sub(r'</card>\s*$', '', body)
    body = re.sub(r'<[^>]+>', '', body)          # 去掉残留标签
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    return "\n".join([title] + lines) if title else "\n".join(lines)


def position(m):
    try:
        return int(str(m.get("message_position", "0") or 0))
    except ValueError:
        return 0


def main():
    t0 = time.time()
    if not TARGET_USER_ID or "YOUR_" in TARGET_USER_ID or not CHAT_ID or "YOUR_" in CHAT_ID:
        log("[error] 配置未填写：先运行 python3 deploy_check.py，"
            "把探测出的身份配置填写到本机 config.local.json")
        return 4
    os.makedirs(LOG_DIR, exist_ok=True)
    rotate_logs()

    # 防并发：两个实例不重叠
    lock_f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("[skip] 已有实例在运行，跳过本次")
        return 0

    clis = find_lark_clis()
    if not clis:
        log("[error] 未找到可用的 lark-cli，检查 LarkShell 是否安装")
        return 2

    last_pos = read_cursor()
    msgs, used, errs = None, None, []
    for p in clis:
        try:
            msgs = fetch_messages(p)
            used = p
            break
        except Exception as e:
            errs.append("%s -> %s" % (os.path.basename(os.path.dirname(os.path.dirname(p))), e))
            continue
    if msgs is None:
        log("[error] 所有 lark-cli 候选都失败: " + " | ".join(errs))
        return 3

    msgs.sort(key=position)
    new_msgs = [m for m in msgs if position(m) > last_pos]
    max_pos = max([position(m) for m in msgs], default=last_pos)

    kept = []
    for m in new_msgs:
        ids = [x.get("id") for x in (m.get("mentions") or [])]
        if TARGET_USER_ID in ids and NEW_TICKET_KEYWORD in card_title(m.get("content", "")):
            kept.append(m)

    log("[sync] 上次位置 %d | 最新页 %d 条 | 新消息 %d 条 | 命中 @%s 新工单通知 %d 条 | 游标 -> %d"
        % (last_pos, len(msgs), len(new_msgs), TARGET_USER_NAME, len(kept), max_pos))

    # 积压超过一页的安全窗口，提示可能漏消息
    if msgs and len(new_msgs) >= PAGE_SIZE:
        log("[warn] 本次新消息达到一页上限(%d)，两次运行之间可能积压超过 %d 条，存在漏拉风险"
            % (PAGE_SIZE, PAGE_SIZE))

    if new_msgs:
        with open(RAW_FILE, "a", encoding="utf-8") as f:
            for m in new_msgs:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    if kept:
        with open(FILTERED_FILE, "a", encoding="utf-8") as f:
            for m in kept:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
            for m in kept:
                f.write("=" * 60 + "\n")
                f.write("时间: %s\n" % m.get("create_time", "?"))
                f.write("消息ID: %s\n" % m.get("message_id", "?"))
                f.write(card_text(m.get("content", "")) + "\n\n")
        for m in kept:
            log("[hit] %s | %s | %s" % (
                m.get("create_time", "?"), card_title(m.get("content", "")), m.get("message_id", "?")))

    write_cursor(max_pos)
    log("[done] 游标已更新: %d | 总耗时 %.3fs" % (max_pos, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
