#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全流程自动触发（阶段1 → 阶段2 → 阶段3）。

手动启动后常驻：轮询 Glata 私聊，发现新的「@我 新工单」卡片就自动：
  1. 解析工单号 → 工单页读取 chat_id / 转人工时间
  2. 精确拉取本次 IT 值班号聊天记录 → 图片本地 OCR
  3. 并发调用 doubao cli，落盘 _summary.md
可选工单增强提示：-at 仅人工工单，-at all 含告警；机器人 @本人并电话加急。

用法:
  python3 watch_and_run.py                    # 常驻，2s 轮询，只处理启动后新到的卡片
  python3 watch_and_run.py --once             # 只跑一轮（自测用）
  python3 watch_and_run.py --run-existing     # 启动时把已有未处理卡片也补跑一遍
  python3 watch_and_run.py --interval 30      # 30s 轮询
  python3 watch_and_run.py --no-ocr --model "DeepSeek V4.1 Flash"

退出：Ctrl+C
"""
import argparse
import datetime
import fcntl
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import read_oncall_chat as core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AI_SCRIPT = os.path.join(BASE_DIR, "oncall_ai_summary.py")
STATE_PATH = os.path.join(core.ONCALL_DIR, ".watch_state.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PY = sys.executable or "python3"

RETRY_MAX = 6        # 定位不到值班号群时最多重试次数
RETRY_GAP = 300      # 每次重试间隔（秒），等 Glata 把人拉进群


_idle_rounds = 0   # 连续空转轮数，用于日志降频


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, "watch-%s.log" % datetime.date.today().isoformat())
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------- 状态 ----------

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            s = json.load(f)
            s.setdefault("seen", [])
            s.setdefault("pending", {})
            return s
    except Exception:
        return {"seen": [], "pending": {}}


def save_state(s):
    try:
        os.makedirs(core.ONCALL_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("[warn] 状态写入失败: %s" % e)


# ---------- 取新卡片 ----------

def fetch_cards(runner, page_size=50, notifier=None):
    """共用一次最新页拉取：分析仍筛人工卡片，提醒按独立模式筛选。"""
    msgs = core.fetch_chat_messages(runner, core.CHAT_ID, page_size=page_size)
    if not msgs:
        runner.reset()
        raise RuntimeError("拉取返回 0 条消息，疑似 lark-cli 会话异常，已重置后重试")
    if notifier:
        try:
            notifier.observe(msgs)
            notifier.poll()
        except Exception as exc:
            log("[notify_error] 提醒状态处理失败（%s），本轮不拨打，继续原有分析" % type(exc).__name__)
    cards = [m for m in msgs if core.is_target_card(m)]
    alerts = [m for m in msgs if core.is_alert_card(m)]
    if alerts:
        log("[scan] 忽略 %d 条 @我 的告警卡片（不进入 AI 分析）" % len(alerts))
    cards.sort(key=core.position, reverse=True)
    return cards


# ---------- 定位值班号 ----------

def locate_group(runner, card, cache):
    """返回 (group, cache, info, reason)；group 为 None 表示定位失败。

    唯一路径：卡片工单号 -> 工单页权威 chat_id / 转人工时间，不猜群名。
    """
    info = core.parse_card(card.get("content", ""), card.get("mentions") or [])

    tno = info.get("ticket_no")
    if tno:
        tmeta = core.ticket_info(tno)
        if tmeta:
            info.update(ticket_metadata_loaded=True,
                        form_type=(tmeta.get("form_type") or "").strip(),
                        alert_only=bool(tmeta.get("alert_only")))
        if tmeta and tmeta.get("alert_only"):
            return None, cache, info, "alert_ticket"
        if tmeta and tmeta.get("chat_id"):
            info["transfer_time"] = tmeta.get("transfer_time")
            info["session_end_time"] = tmeta.get("session_end_time")
            info["closed"] = bool(tmeta.get("closed"))
            info["closure_type"] = tmeta.get("closure_type")
            log("[ticket] %s -> %s (转人工 %s%s)"
                % (tno, tmeta["chat_id"], tmeta.get("transfer_time"),
                   (", 已结束: %s" % tmeta.get("closure_type")) if tmeta.get("closed") else ""))
            return ({"chat_id": tmeta["chat_id"], "name": "工单页直取"},
                    cache, info, "ticket_page")

    rname, rid = info.get("reporter_name"), info.get("reporter_open_id")
    if not tno:
        return None, cache, info, "no_ticket_no"
    return None, cache, info, "ticket_page_failed"


# ---------- 后台 AI 任务 ----------

def start_ai_job(chat_id, args, since=None):
    """把阶段3 拉成独立后台进程，主循环不再等它。输出落 ai-<chat>-<时间>.log。"""
    cmd = [PY, AI_SCRIPT, "--chat-id", chat_id]
    if since:
        cmd += ["--since", since]
    if args.limit is not None:
        cmd += ["--limit", str(args.limit)]
    if args.no_ocr:
        cmd += ["--no-ocr"]
    if args.no_session:
        cmd += ["--no-session"]
    cmd += ["--model", args.model, "--timeout", str(args.timeout)]
    os.makedirs(LOG_DIR, exist_ok=True)
    tag = datetime.datetime.now().strftime("%H%M%S")
    log_path = os.path.join(LOG_DIR, "ai-%s-%s.log" % (chat_id, tag))
    fh = open(log_path, "w", encoding="utf-8")
    p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True, cwd=BASE_DIR)
    return {"p": p, "fh": fh, "log_path": log_path, "chat_id": chat_id,
            "started": time.time(), "forced": False}


def collect_jobs(jobs, state, args):
    """回收已结束的后台分析，更新 seen / pending。"""
    for mid, j in list(jobs.items()):
        rc = j["p"].poll()
        if rc is None:
            if time.time() - j["started"] > args.timeout + 180:
                log("[timeout] %s 超过 %ds 未结束，终止" % (j["chat_id"], args.timeout + 180))
                try:
                    j["p"].kill()
                except Exception:
                    pass
                j["forced"] = True
                rc = j["p"].poll()
                if rc is None:
                    continue
            else:
                continue
        try:
            j["fh"].close()
        except Exception:
            pass
        jobs.pop(mid, None)
        elapsed = time.time() - j["started"]
        if rc == 8:
            # 值班号群 230002 = 群已关闭/成员已被移出，工单 resolve 后的正常终态
            log("[skip] %s 值班号群不可读（工单已关闭），不再重试" % j["chat_id"])
            if mid not in state["seen"]:
                state["seen"].append(mid)
            state["pending"].pop(mid, None)
        elif rc == 0 and not j["forced"]:
            log("[done] %s 分析完成，耗时 %.1fs" % (j["chat_id"], elapsed))
            if mid not in state["seen"]:
                state["seen"].append(mid)
            state["pending"].pop(mid, None)
            log("[file] %s" % os.path.join(core.ONCALL_DIR, "%s_summary.md" % j["chat_id"]))
        else:
            n = state["pending"].get(mid, {}).get("retry", 0) + 1
            log("[fail] %s 分析失败 (rc=%s, %.1fs)，第 %d 次，日志 %s"
                % (j["chat_id"], rc, elapsed, n, j["log_path"]))
            if n >= RETRY_MAX:
                log("[giveup] %s 连续失败 %d 次，放弃" % (j["chat_id"], n))
                if mid not in state["seen"]:
                    state["seen"].append(mid)
                state["pending"].pop(mid, None)
            else:
                state["pending"][mid] = {"retry": n, "next_retry": time.time() + 120,
                                         "at": state["pending"].get(mid, {}).get("at")}
        save_state(state)


# ---------- 主循环 ----------

def process_new(runner, cache, args, state, jobs, baseline_only=False, notifier=None):
    """处理所有未处理的卡片；baseline_only=True 时只登记不执行。"""
    global _idle_rounds
    cards = fetch_cards(runner, notifier=notifier)
    if not cards:
        _idle_rounds += 1
        if _idle_rounds == 1 or _idle_rounds % 30 == 0:
            log("[scan] 空闲 %d 轮，Glata 群最新一页没有 @%s 的工单卡片"
                % (_idle_rounds, core.TARGET_USER_NAME))
        return cache

    todo = [c for c in cards
            if c.get("message_id") not in state["seen"]
            and c.get("message_id") not in state["pending"]
            and c.get("message_id") not in jobs]
    # 之前待定（Glata 还没拉进群）的卡片，到重试时间才重新尝试；正在后台跑的不重投
    now = time.time()
    retry_ids = [k for k, v in state["pending"].items()
                 if v.get("retry", 0) < RETRY_MAX
                 and v.get("state") != "running"
                 and v.get("next_retry", 0) <= now
                 and k not in jobs]
    retry_cards = [c for c in cards if c.get("message_id") in retry_ids]
    by_id = {}
    for c in todo + retry_cards:
        by_id[c.get("message_id")] = c
    todo = sorted(by_id.values(), key=core.position, reverse=True)

    if baseline_only:
        state["seen"] = list({c.get("message_id") for c in cards} | set(state["seen"]))
        save_state(state)
        log("[baseline] 已登记 %d 张存量卡片，只处理之后新到的" % len(cards))
        return cache

    if not todo:
        _idle_rounds += 1
        # 空转日志降频：2 秒一轮的话每秒都写会把日志撑到几十 MB
        if _idle_rounds == 1 or _idle_rounds % 30 == 0:
            log("[scan] 空闲 %d 轮，无新卡片（最新 %s）"
                % (_idle_rounds, cards[0].get("create_time") or "-"))
        return cache

    _idle_rounds = 0
    log("[scan] 发现 %d 张待处理卡片" % len(todo))
    for card in todo:
        mid = card.get("message_id")
        t0 = time.time()
        log("[card] ---- %s | %s ----" % (card.get("create_time"), mid))
        try:
            group, cache, info, err = locate_group(runner, card, cache)
            if notifier:
                try:
                    notifier.confirm_manual(card, info)
                    notifier.poll()
                except Exception as exc:
                    log("[notify_error] 提醒确认失败（%s），继续原有分析" % type(exc).__name__)
            if err == "no_ticket_no":
                log("[skip] 卡片未带工单详情链接，无法定位值班号，标记跳过")
                state["seen"].append(mid)
                state["pending"].pop(mid, None)
                save_state(state)
                continue
            if err == "alert_ticket":
                log("[skip] %s 是告警工单（Form Type=Alert），非人工报障，不处理"
                    % info.get("ticket_no"))
                state["seen"].append(mid)
                state["pending"].pop(mid, None)
                save_state(state)
                continue
            if group and info.get("closed"):
                log("[skip] %s 工单已结束（%s），值班号群已关闭，不再拉取"
                    % (info.get("ticket_no"), info.get("closure_type") or "-"))
                state["seen"].append(mid)
                state["pending"].pop(mid, None)
                save_state(state)
                continue
            if not group:
                n = state["pending"].get(mid, {}).get("retry", 0) + 1
                if n >= RETRY_MAX:
                    log("[giveup] %s 重试 %d 次仍读不到工单页 chat_id，放弃" % (mid, RETRY_MAX))
                    state["seen"].append(mid)
                    state["pending"].pop(mid, None)
                else:
                    state["pending"][mid] = {"retry": n, "at": card.get("create_time"),
                                             "next_retry": time.time() + RETRY_GAP}
                    log("[pending] 工单页未取到 chat_id，第 %d 次，%ds 后重试"
                        % (n, RETRY_GAP))
                save_state(state)
                continue

            chat_id = group["chat_id"]
            since = core.iso_since(info.get("transfer_time"))
            log("[oncall] %s -> %s (%s)%s"
                % (info.get("reporter_name") or info.get("ticket_no"), group.get("name"), chat_id,
                   (" start=%s" % since) if since else ""))

            if len(jobs) >= args.workers:
                # 已达并发上限：不登记，下一轮再投，避免把任务堆在无人监管的队列里
                log("[wait] %d 单正在并行分析（上限 %d），%s 留到下一轮"
                    % (len(jobs), args.workers, chat_id))
                continue
            j = start_ai_job(chat_id, args, since=since)
            jobs[mid] = j
            state["pending"][mid] = {"retry": 0, "next_retry": 0,
                                     "state": "running", "chat_id": chat_id,
                                     "at": card.get("create_time")}
            save_state(state)
            log("[queue] %s 已进入后台分析（当前并行 %d 单），日志 %s"
                % (chat_id, len(jobs), j["log_path"]))
        except Exception as e:
            log("[error] 处理卡片 %s 出错: %s" % (mid, e))
            state["pending"][mid] = {"retry": state["pending"].get(mid, {}).get("retry", 0) + 1,
                                     "next_retry": time.time() + 60}
            save_state(state)
    return cache


LOCK_PATH = os.path.join(core.ONCALL_DIR, ".watch.lock")


def acquire_single_instance():
    """独占锁防止双开监控重复处理卡片；不锁住其他手工 lark-cli 调用。"""
    os.makedirs(core.ONCALL_DIR, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        print("[错误] 已有监控实例在运行（%s 被占用）。要重启先 bash stop.sh。" % LOCK_PATH)
        sys.exit(3)
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=2, help="轮询间隔秒数，默认 2")
    ap.add_argument("--once", action="store_true", help="只跑一轮就退出（自测用）")
    ap.add_argument("--run-existing", action="store_true", help="启动时把已有未处理卡片也补跑")
    ap.add_argument("--model", default="DeepSeek V4.1 Flash", help="doubao 模型")
    ap.add_argument("--timeout", type=int, default=300, help="单次 AI 超时秒数")
    ap.add_argument("--workers", type=int, default=3, help="同时并行分析的工单数，默认 3")
    ap.add_argument("--limit", type=int, default=None, help="透传给阶段3 的 --limit（默认不传=全部）")
    ap.add_argument("--no-ocr", action="store_true", help="透传 --no-ocr")
    ap.add_argument("--no-session", action="store_true", help="透传 --no-session")
    ap.add_argument("--notify-mode", choices=("off", "human", "all"), default="off",
                    help="工单增强提示：off 关闭 / human 仅人工 / all 含告警；启动入口用 -at")
    ap.add_argument("-at", nargs="?", const="human", choices=("human", "all"), default=None,
                    help="-at 仅人工工单；-at all 包含告警（均仅 @本人）")
    args = ap.parse_args()
    if args.at:
        args.notify_mode = args.at

    _lock = acquire_single_instance()
    notifier = None
    if args.notify_mode != "off":
        import ticket_notify
        try:
            notifier = ticket_notify.create_manager(args.notify_mode, core, log)
        except ticket_notify.NotifyError as exc:
            log("[notify_error] 工单增强提示配置校验失败：%s；监控未启动" % exc)
            return 4

    log("=" * 60)
    log("全流程监控启动 | 轮询 %ds | 并行上限 %d 单 | 只处理启动后新到的卡片 | Ctrl+C 退出"
        % (args.interval, args.workers))

    log("[notify] 工单增强提示模式：%s" % args.notify_mode)
    runner = core.LarkRunner()
    cache = None   # 定位只走工单页，不再使用群名索引
    # 首次基线必须在创建状态文件之前判断，否则 save_state 会令条件永远为假。
    baseline = not args.run_existing and not os.path.exists(STATE_PATH)
    state = load_state()
    jobs = {}      # message_id -> 后台分析进程

    # 上次异常退出时可能留下 state=running 的条目（进程已死但没回收），这里恢复成可重试
    for k, v in list(state["pending"].items()):
        if v.get("state") == "running":
            v.pop("state", None)
            v["retry"] = v.get("retry", 0) + 1
            v["next_retry"] = 0
            log("[recover] 上次未跑完的卡片 %s 重新入队（第 %d 次）" % (k, v["retry"]))
    save_state(state)

    first_round = True
    fail_streak = 0

    try:
        while True:
            started = time.time()
            try:
                if notifier:
                    try:
                        notifier.poll()
                    except Exception as exc:
                        log("[notify_error] 提醒工作异常（%s），继续扫描" % type(exc).__name__)
                collect_jobs(jobs, state, args)
                cache = process_new(runner, cache, args, state, jobs,
                                    baseline_only=(first_round and baseline), notifier=notifier)
                first_round = False
                fail_streak = 0
            except KeyboardInterrupt:
                raise
            except Exception as e:
                fail_streak += 1
                # 连续失败时降频打印，避免 2 秒一条把日志刷爆
                if fail_streak <= 3 or fail_streak % 10 == 0:
                    log("[error] 本轮异常（连续 %d 次）: %s" % (fail_streak, e))
            if args.once:
                break
            # 扫描本身很快（秒级），按固定间隔巡检；AI 在后台跑，不再占用这里的时间
            base = args.interval
            if fail_streak:
                # 出错后指数退避，最多 60 秒，避免无效请求连打
                base = min(args.interval * (2 ** min(fail_streak, 5)), 60)
            elapsed = time.time() - started
            wait = base - elapsed
            if wait > 0:
                time.sleep(wait)
    finally:
        if notifier:
            notifier.close()
        if jobs:
            log("[exit] 仍有 %d 单在后台分析，进程独立于监控、会继续跑完：%s"
                % (len(jobs), ", ".join(j["chat_id"] for j in jobs.values())))


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        log("已退出监控")
        sys.exit(0)
