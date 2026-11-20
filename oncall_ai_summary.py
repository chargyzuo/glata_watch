#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第三阶段：拿到 IT 值班号聊天记录 -> 调 doubao cli 做问题浓缩 + 内部资料/网络检索。

流程:
  1. 取聊天记录（--chat-id 直接读 / --message-id 指定卡片 / 默认最新卡片）
  2. 聊天记录写入临时文件（oncall_chats/<key>_chat.txt）
  3. 组装提示词，调用 `doubao -p <prompt> --model "DeepSeek V4.1 Flash"`
  4. 过滤 cli 元数据，输出并落盘（oncall_chats/<key>_summary.md）

用法:
  python3 oncall_ai_summary.py --chat-id <chat_id>
  python3 oncall_ai_summary.py --message-id om_xxx
  python3 oncall_ai_summary.py                    # 最新一条 @我 的工单卡片
  python3 oncall_ai_summary.py --chat-id oc_xxx --limit 30 --timeout 600
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import read_oncall_chat as core

from project_config import get_setting

DOUBAO_BIN = os.environ.get("DOUBAO_CLI_BIN", "doubao")
MODEL = "DeepSeek V4.1 Flash"
ALERT_REFERENCE_CHAT_ID = get_setting("alert_reference_chat_id")

PROMPT_TPL = """你现在是一个字节跳动网络工程师，需要根据聊天记录生成浓缩问题和补充信息，均不超过100字，然后根据浓缩后的问题查询内部资料，是否有对应的案例和解决办法，如果内部资料搜索不到再搜索互联网。

【告警参考来源】
告警群会话 ID：{alert_reference_chat_id}。查询内部资料时，同时将此群作为告警参考来源，按本单问题涉及的系统/服务、故障现象和相关时间检索直接相关的告警消息。
只纳入与当前工单问题直接相关的告警，不因正文偶然提及同一个词就判定相关。告警群未配置或会话 ID 为占位符时跳过告警查询。告警仅作辅助证据，不替代本单聊天记录；未能关联、未检索到或无权限读取时如实说明，不得声称已查阅或编造告警。告警查询不成功也继续原来的工单分析和资料检索。
引用告警时标明“告警参考来源”，并给出消息时间、告警摘要与可获得的原始消息链接；明确说明与本单的关联依据。告警发生不代表本单根因已确定，证据冲突要分别列出。

【引用约束（必须遵守）】
1. 引用任何报错原文时，必须标明来源归属：①本单聊天记录原文；②本单截图（聊天记录里只有 [Image: img_xxx] 占位符，看不到图中文字）；③同类案例工单/内部文档；④告警参考来源。不得把其他工单或告警群的报错原文当作本单原文复述。
2. 聊天记录中的 [Image: img_xxx] 只是图片占位符，你无法看到图中文字。不得声称读到了图片内容；若需要图中信息，只能标注"需人工看图确认"。
3. 引用内部检索结果时，给出工单号或文档标题/链接。当内部案例结论与本单可见证据不一致时，明确指出不一致，不要强行统一。
4. 本单证据不足以判断的部分，直接写"未确认"，不要用其他案例的结论补齐。

/#聊天记录内容 #/
{chat}
"""

def render_chat(messages, limit, runner=None, ocr=True):
    """按时间正序渲染；limit<=0 表示全部。图片消息会附带本地 OCR 文本。"""
    rows = messages if limit <= 0 else messages[:limit]
    rows = list(reversed(rows))
    out = []
    for m in rows:
        t = m.get("create_time") or "?"
        sender = (m.get("sender") or {}).get("name") or "系统"
        body = (m.get("content") or "").replace("\r", " ").strip()
        body = " ".join(body.split("\n"))
        if len(body) > 600:
            body = body[:600] + "…"
        if ocr and runner and "[Image" in body:
            key = core.extract_img_key(body)
            if key:
                txt = core.ocr_image(runner, m.get("message_id"), key)
                if txt:
                    # OCR 是多行排版文本，压成一行以免破坏「一条消息一行」的结构
                    txt = " / ".join(x.strip() for x in txt.splitlines() if x.strip())
                    body = body + " ［图中文字］" + txt
                else:
                    body = body + " ［图中文字识别为空］"
        out.append("[%s] %s: %s" % (t, sender, body))
    return "\n".join(out)


def load_stage2_messages(key, max_age=900):
    """复用阶段二刚拉下来的聊天记录，避免同一次流程里重复请求 OpenAPI。"""
    path = os.path.join(core.ONCALL_DIR, "%s.json" % key)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > max_age:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        msgs = d.get("messages")
        return msgs if msgs else None
    except Exception:
        return None


def resolve_target(args, runner):
    """返回 (chat_id, chat_name, key, ticket_info)"""
    if args.chat_id:
        name = None
        try:
            d = runner.call(["im", "chats", "get", "--chat-id", args.chat_id, "--format", "json"])
            name = (d.get("data") or {}).get("name")
        except Exception:
            pass
        return args.chat_id, name, args.chat_id, {}

    # 卡片驱动
    if args.message_id:
        d = runner.call(["im", "+messages-mget", "--message-ids", args.message_id,
                         "--format", "json", "--no-reactions"])
        cards = (d.get("data") or {}).get("messages") or []
        if not cards:
            raise RuntimeError("取不到 message_id=%s 的消息" % args.message_id)
    else:
        d = runner.call(["im", "+chat-messages-list", "--chat-id", core.CHAT_ID,
                         "--page-size", "50", "--format", "json", "--no-reactions"])
        msgs = (d.get("data") or {}).get("messages") or []
        cards = sorted([m for m in msgs if core.is_target_card(m)], key=core.position, reverse=True)
        if not cards:
            raise RuntimeError("最新一页没有 @%s 的新工单卡片" % core.TARGET_USER_NAME)
        cards = cards[:1]

    card = cards[0]
    info = core.parse_card(card.get("content", ""), card.get("mentions") or [])

    # 主路径：卡片工单号 -> Glata 工单页拿权威 chat_id 与转人工时间
    tno = info.get("ticket_no")
    tmeta = core.ticket_info(tno) if tno else None
    if tmeta and tmeta.get("chat_id"):
        if tmeta.get("closed"):
            raise RuntimeError("工单 %s 已结束（%s），值班号群已关闭，无需分析"
                               % (tno, tmeta.get("closure_type") or "-"))
        info["transfer_time"] = tmeta.get("transfer_time")
        info["session_end_time"] = tmeta.get("session_end_time")
        core.log("[ticket] %s -> %s (转人工 %s)"
                 % (tno, tmeta["chat_id"], tmeta.get("transfer_time")))
        return (tmeta["chat_id"], (tmeta.get("requester") or "工单页直取"),
                card.get("message_id") or tmeta["chat_id"], info)

    # 不再按报告人猜群名：拿不到工单页 chat_id 就直接报错
    if not tno:
        raise RuntimeError("卡片未带工单详情链接，无法拿到工单号与值班号会话编号")
    raise RuntimeError("工单页未取到值班号会话编号（工单号 %s）" % tno)


SESS_FILE = os.path.join(core.ONCALL_DIR, ".ai_sessions.json")


def load_sessions():
    try:
        with open(SESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_sessions(d):
    try:
        with open(SESS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def extract_session_id(raw):
    """doubao cli 在 -p/--resume 模式下第一行会输出 {"type":"session","session_id":...}。"""
    for line in (raw or "").splitlines():
        s = line.strip()
        if s.startswith('{"type":"session"'):
            try:
                return json.loads(s).get("session_id")
            except Exception:
                return None
    return None


def clean_doubao_output(raw):
    """去掉 cli 的会话/元数据行，保留正文。"""
    keep = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith('{"type":"session"') or s.startswith('{"type":"result"'):
            continue
        if s.startswith("[doubao]") and ("session" in s or "session_id" in s):
            continue
        keep.append(line.rstrip())
    return "\n".join(keep).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", default=None, help="IT 值班号群 chat_id")
    ap.add_argument("--message-id", default=None, help="Glata 卡片 message_id")
    ap.add_argument("--limit", type=int, default=0, help="传给 AI 的聊天记录条数，默认 0=全部（会话已按锚点裁剪）")
    ap.add_argument("--page-size", type=int, default=50, help="从飞书拉取的消息每页条数，默认 50（自动翻页）")
    ap.add_argument("--no-session", action="store_true", help="不按「欢迎来到 IT 值班号」锚点裁剪，保留全部历史")
    ap.add_argument("--since", default=None,
                    help="聊天记录起始时间（ISO8601，如 2026-11-05T17:10:00+08:00）；不传则用工单页的转人工时间")
    ap.add_argument("--no-ocr", action="store_true", help="不对群内图片做本地 OCR")
    ap.add_argument("--timeout", type=int, default=300, help="doubao cli 超时秒数，默认 300（实测 100-120s）")
    ap.add_argument("--model", default=MODEL, help="模型，默认 DeepSeek V4.1 Flash")
    ap.add_argument("--dry-run", action="store_true", help="只打印提示词，不调 doubao")
    ap.add_argument("--raw", action="store_true", help="原样输出 doubao cli 的 stdout（不过滤元数据）")
    args = ap.parse_args()

    t0 = time.time()
    runner = core.LarkRunner()

    try:
        chat_id, chat_name, key, ticket = resolve_target(args, runner)
    except RuntimeError as e:
        core.log("[error] %s" % e)
        return 2
    core.log("[target] %s (%s)" % (chat_name or "-", chat_id))

    # 有权威转人工时间就直接按时间窗口拉，避免把上一段历史混进来
    since = args.since or core.iso_since((ticket or {}).get("transfer_time"))
    msgs = None
    if not args.since:
        msgs = load_stage2_messages(key)
        if msgs:
            core.log("[chat] 复用阶段二已拉的 %d 条记录（省一次 OpenAPI 往返）" % len(msgs))
    if not msgs:
        if since:
            core.log("[chat] 按转人工时间拉取, start=%s" % since)
        try:
            msgs = core.fetch_chat_messages(runner, chat_id, args.page_size, page_all=True, since=since)
        except RuntimeError as e:
            if "230002" in str(e):
                # 工单 resolve 后值班号群会关闭/把成员移出，属于正常终态，不该反复重试
                core.log("[closed] 值班号群不可读（230002），工单应已关闭: %s" % chat_id)
                return 8
            raise
        core.log("[chat] 拉到 %d 条聊天记录（含历史页）" % len(msgs))
    if not msgs:
        core.log("[warn] 该群没有聊天记录，仍会调用 AI（内容为空）")

    if not args.no_session:
        start = core.find_session_start(msgs)
        if start > 0:
            core.log("[session] 本次问题起点 %s，保留 %d 条，切掉更早的 %d 条" %
                     (msgs[start].get("create_time"), start + 1, len(msgs) - start - 1))
            msgs = msgs[:start + 1]
        else:
            core.log("[session] 未找到「%s」锚点，保留全部历史" % core.WELCOME_MARK)

    chat_text = render_chat(msgs, args.limit, runner=runner, ocr=not args.no_ocr)
    os.makedirs(core.ONCALL_DIR, exist_ok=True)
    chat_path = os.path.join(core.ONCALL_DIR, "%s_chat.txt" % key)
    with open(chat_path, "w", encoding="utf-8") as f:
        f.write(chat_text)
    core.log("[temp] 聊天记录已临时存储: %s (%d 字)" % (chat_path, len(chat_text)))

    # 只用纯聊天记录（Copilot 摘要段已略去，OpenAPI 取不到该消息）
    prompt = PROMPT_TPL.format(chat=chat_text, alert_reference_chat_id=ALERT_REFERENCE_CHAT_ID)

    if args.dry_run:
        print(prompt)
        return 0

    core.log("[ai] 调用 doubao cli（model=%s）…" % args.model)
    try:
        # 同一张工单复用同一个 doubao 会话：重试时用 --resume，避免每次重试都新建一个
        # 同名会话（历史列表会被同一张单刷出好几条一模一样的记录）。
        sid = load_sessions().get(key)
        base_args = ["--model", args.model,
                     "--permission-mode", "always_allow", "-C", core.BASE_DIR]
        if sid:
            cmd = [DOUBAO_BIN, "--resume", str(sid), "-p", prompt] + base_args
            core.log("[ai] 复用会话 %s" % sid)
        else:
            cmd = [DOUBAO_BIN, "-p", prompt] + base_args
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
        # 无论成败都记住会话号：失败时可能已经建了会话，下次直接复用它，不再新建。
        # 注意：{"type":"session"} 这行走的是 stderr，stdout 里只有正文。
        new_sid = extract_session_id(r.stdout) or extract_session_id(r.stderr)
        if new_sid:
            sess = load_sessions()
            if sess.get(key) != new_sid:
                sess[key] = new_sid
                save_sessions(sess)
        else:
            core.log("[warn] 未解析到 doubao 会话号，下次仍会新建会话")
    except FileNotFoundError:
        core.log("[error] 找不到 doubao cli，请先安装或设置 DOUBAO_CLI_BIN")
        return 3
    except subprocess.TimeoutExpired:
        core.log("[error] doubao cli 超时（%ds）" % args.timeout)
        return 4

    if r.returncode != 0:
        core.log("[error] doubao cli 失败 exit=%d: %s" % (r.returncode, (r.stderr or "")[:400]))
        return 6

    answer = r.stdout if args.raw else clean_doubao_output(r.stdout)
    if not answer.strip() and r.stderr:
        answer = r.stderr if args.raw else clean_doubao_output(r.stderr)
    if not answer:
        core.log("[error] doubao cli 没有返回内容")
        return 5

    # 上游有时把错误正文当正常输出返回（rc 仍为 0）。这种内容不能当成分析结果落盘，
    # 否则主循环会误判成功、标记已处理，永远不会重试。
    upstream_err = ("service_unavailable", "连接共享服务超时", "shared service timeout",
                    "upstream timeout", "internal error")
    low = answer.lower()
    if any(k in low for k in upstream_err):
        core.log("[error] doubao 上游错误，判为失败待重试: %s" % answer[:120].replace("\n", " "))
        return 7

    summary_path = os.path.join(core.ONCALL_DIR, "%s_summary.md" % key)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("# %s 值班号问题分析\n\n" % (chat_name or chat_id))
        if ticket:
            f.write("- 工单: %s\n- 报告人: %s\n" % (ticket.get("ticket_title"), ticket.get("reporter_name")))
        f.write("- 群: %s (%s)\n\n---\n\n%s\n" % (chat_name or "-", chat_id, answer))

    print("")
    print("=" * 72)
    print("值班号: %s (%s)" % (chat_name or "-", chat_id))
    sent = len(msgs) if args.limit <= 0 else min(len(msgs), args.limit)
    print("聊天记录: %d 条（传入 AI %d 条）" % (len(msgs), sent))
    print("-" * 72)
    print(answer)
    print("=" * 72)
    print("聊天记录临时文件: %s" % chat_path)
    print("AI 输出: %s" % summary_path)
    core.log("[done] 耗时 %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
