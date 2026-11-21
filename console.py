#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时控制台：跟随 watch / AI 日志，只打印关键事件。

用法：python3 console.py          # 跟随当天日志，Ctrl+C 退出（不影响后台监控）
     python3 console.py --all    # 连"空闲 N 轮"也打印
"""
import glob
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
RED, GREEN, YELLOW, BLUE, GREY = "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[90m"

# tag -> (阶段标签, 颜色)
TAGS = {
    "scan":     ("阶段1 扫描", BLUE),
    "card":     ("阶段1 卡片", BLUE),
    "baseline": ("初始化", GREY),
    "ticket":   ("阶段2 工单号", GREEN),
    "oncall":   ("阶段2 值班号", GREEN),
    "dedup":    ("分析去重", GREY),
    "queue":    ("阶段3 AI开始", YELLOW),
    "chat":     ("阶段3 拉记录", YELLOW),
    "session":  ("阶段3 会话", YELLOW),
    "temp":     ("阶段3 落盘", GREY),
    "ai":       ("阶段3 调模型", YELLOW),
    "done":     ("完成", GREEN),
    "file":     ("产物", GREEN),
    "fail":     ("失败", RED),
    "giveup":   ("放弃", RED),
    "error":    ("错误", RED),
    "timeout":  ("超时", RED),
    "skip":     ("跳过", GREY),
    "pending":  ("待重试", YELLOW),
    "wait":     ("排队", GREY),
    "recover":  ("恢复", YELLOW),
    "warn":     ("警告", YELLOW),
    "notify":   ("工单增强提示", GREEN),
    "notify_error": ("提醒失败", RED),
    "notify_warn": ("提醒警告", YELLOW),
    "exit":     ("退出", GREY),
}

LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)\] \[([a-z_]+)\] (.+)$")
NOISE_RE = re.compile(r"\[scan\] 空闲|\[scan\] 忽略")


class Tail(object):
    """按 inode + 偏移跟踪一个日志文件，只返回新增行。"""

    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.ino = None

    def read(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if self.ino != st.st_ino:
            self.ino = st.st_ino
            self.pos = 0
        if st.st_size < self.pos:
            self.pos = 0
        if st.st_size == self.pos:
            return []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.pos)
                lines = f.readlines()
                self.pos = f.tell()
        except OSError:
            return []
        return lines

    def seek_to_tail(self, max_bytes=18000):
        """启动时只回看最后若干字节，避免刷屏。"""
        try:
            st = os.stat(self.path)
        except OSError:
            return
        self.ino = st.st_ino          # 关键：先记 inode，否则下次 read 会当成新文件从头读
        self.pos = max(0, st.st_size - max_bytes)


def format_line(raw):
    raw = raw.rstrip("\n")
    m = LINE_RE.match(raw)
    if not m:
        return None
    date, ts, tag, msg = m.groups()
    if not SHOW_ALL and NOISE_RE.search(raw):
        return None
    stage, color = TAGS.get(tag, ("", RESET))
    stamp = DIM + ts + RESET
    label = (color + stage + RESET) if stage else ""
    # 长错误信息截断，保证一行一件事
    if len(msg) > 150:
        msg = msg[:150] + "…"
    body = msg
    if tag in ("done", "fail", "error", "giveup", "timeout", "ticket", "queue"):
        body = BOLD + body + RESET
    return "%s  %s  %s" % (stamp, label.ljust(18 + len(color) + len(RESET)), body)


SHOW_ALL = "--all" in sys.argv


def main():
    today = time.strftime("%Y-%m-%d")
    watch_path = os.path.join(LOG_DIR, "watch-%s.log" % today)

    if not os.path.exists(watch_path):
        print("%s还没找到日志 %s%s" % (YELLOW, watch_path, RESET))
        print("%s监控可能还没启动，先执行 bash start.sh%s" % (GREY, RESET))

    print("%s%s%s" % (BOLD, "=" * 74, RESET))
    print("%s   Glata 工单监控 · 实时控制台%s" % (BOLD, RESET))
    print("%s   Ctrl+C 只退出控制台，后台监控继续运行；停止监控用 bash stop.sh%s" % (GREY, RESET))
    print("%s%s%s" % (BOLD, "=" * 74, RESET))
    print("")

    watch = Tail(watch_path)
    watch.seek_to_tail()
    ai_tails = {}
    last_ai_scan = 0.0

    for line in watch.read()[-12:]:
        out = format_line(line)
        if out:
            print(out)

    try:
        while True:
            for line in watch.read():
                out = format_line(line)
                if out:
                    print(out)
                    sys.stdout.flush()

            # 每 3 秒扫一次 AI 子进程日志，只跟最近几个
            if time.time() - last_ai_scan > 3:
                last_ai_scan = time.time()
                files = sorted(glob.glob(os.path.join(LOG_DIR, "ai-*.log")),
                               key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                               reverse=True)[:4]
                for f in files:
                    if f not in ai_tails:
                        t = Tail(f)
                        t.seek_to_tail(max_bytes=6000)
                        ai_tails[f] = t

            for f, t in list(ai_tails.items()):
                lines = t.read()
                if getattr(t, "first", True):
                    lines = lines[-4:]      # AI 日志首次接入只看最后几步，避免回放历史
                    t.first = False
                for line in lines:
                    out = format_line(line)
                    if out:
                        print(out)
                        sys.stdout.flush()

            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\n%s控制台已退出，监控仍在后台运行（bash stop.sh 可停止）%s" % (GREY, RESET))


if __name__ == "__main__":
    main()
