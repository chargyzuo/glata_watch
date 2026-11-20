#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过 Glata「@我 的新工单通知」卡片，读取对应 IT 值班号（IT On Call）的聊天记录。

链路:
  1. 拉取 Glata 群消息，筛出「@本人 + 新工单通知」卡片
  2. 从卡片正文的「查看详情」链接正则抓工单号（detail/(\\d+)）
  3. 用工单号打开 Glata 工单详情页（cdp_client，需常驻调试浏览器），取权威字段:
       - 值班号会话编号 (Helpdesk Session Chat ID) -> chat_id
       - 转人工时间 (Transfer to agent at)          -> 会话起点
     结果缓存进 .ticket_cache.json，同一单不再重复读页面
  4. 用飞书 OpenAPI 按 chat_id + (转人工时间 - 90s) 精确拉取聊天记录
     起点向前留 90s 缓冲，是为了包含 17:11:04 那种开场欢迎卡

不再按报告人姓名猜群名：工单页拿不到的，直接标记失败/重试。

鉴权: lark-cli 用户授权 + 专用浏览器的 Glata 登录态（过期需本人重新验证）

用法:
  python3 read_oncall_chat.py                              # 最新一条卡片
  python3 read_oncall_chat.py --count 3                    # 最近 3 条卡片
  python3 read_oncall_chat.py --message-id om_xxx          # 指定卡片
  python3 read_oncall_chat.py --chat-id oc_xxx             # 直接读取指定群（跳过卡片与工单页）
  python3 read_oncall_chat.py --page-size 50               # 每页条数（自动翻页）
"""
import argparse
import datetime
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import time

from project_config import get_setting, user_ids

# 个人标识由本机配置提供，源码和 Git 历史不得包含真实会话/用户 ID。
# 一个用户在不同应用下的 open_id 不同，集合加姓名兜底避免漏单。
CHAT_ID = get_setting("chat_id")
TARGET_USER_IDS = user_ids()
TARGET_USER_ID = get_setting("target_user_id")
TARGET_USER_NAME = get_setting("target_user_name")
NEW_TICKET_KEYWORD = "你有一条新工单需要处理"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ONCALL_DIR = os.path.join(BASE_DIR, "oncall_chats")
LOG_DIR = os.path.join(BASE_DIR, "logs")
TICKET_CACHE_FILE = os.path.join(BASE_DIR, ".ticket_cache.json")
TICKET_CACHE_TTL = 7 * 24 * 3600   # 工单号 -> chat_id/转人工时间，7 天有效

# 卡片正文里的详情链接：https://glata.bytedance.com/ens/engineer/serviceTicket/detail/<工单号>?...
DETAIL_LINK_RE = re.compile(
    r"https://glata\.bytedance\.com/ens/engineer/serviceTicket/detail/(\d+)")
# 转人工时间做会话起点时往前留的缓冲，确保包含「欢迎来到 IT 值班号」那张开场卡
TRANSFER_BUFFER_SEC = 90

ONCALL_NAME_PATTERN = re.compile(r"IT\s*On\s*Call|IT\s*值班号|值班号", re.I)


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "oncall-%s.log" % datetime.date.today().isoformat())
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def find_lark_clis():
    cands = []
    env = os.environ.get("LARK_CLI_BIN")
    if env and os.access(env, os.X_OK):
        cands.append(env)
    patterns = [
        os.path.expanduser(
            "~/Library/Application Support/LarkShell/aha/users/*/doubao/"
            "sandbox_envs_dir/envs/*/override_dlcs/bin/lark-cli"),
        os.path.expanduser(
            "~/Library/Application Support/LarkShell/aha/users/*/doubao/"
            "sandbox_envs_dir/.prepared_packages/*/dlcs/*/bin/lark-cli"),
    ]
    found = []
    for pat in patterns:
        for p in glob.glob(pat):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                found.append((os.path.getmtime(p), p))
    found.sort(reverse=True)
    seen = set(cands)
    for _, p in found:
        if p not in seen:
            cands.append(p)
            seen.add(p)
    return cands[:5]


def lark_env():
    """lark-cli 专用环境。

    豆包平台会给进程注入一批 DOUBAO_OFFICE_* 变量（app_id / forward_proxy / user_access_token 等）。
    lark-cli 一旦看到它们就不再使用 ~/.lark-cli 的持久凭据，改用这些临时 token；而临时 token
    会过期，表现为长时间运行后所有请求被网关拒绝（exit 5，parse temporary token from
    Authorization failed）。清掉它们，让 CLI 走持久凭据即可长期稳定。
    注意：只对 lark-cli 生效，doubao cli 仍然需要原始环境，所以不能全局 unset。
    """
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("DOUBAO_OFFICE_"):
            env.pop(k)
    return env


class LarkRunner(object):
    def __init__(self):
        self.candidates = find_lark_clis()
        self.lark = None
        self.last_err = ""

    def _run(self, lark, args, timeout=40):
        r = subprocess.run([lark] + args, capture_output=True, text=True,
                           timeout=timeout, env=lark_env())
        if r.returncode != 0:
            raise RuntimeError("lark-cli exit %d: %s" % (
                r.returncode, (r.stderr or r.stdout or "")[:300].replace("\n", " ")))
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError("lark-cli 输出非 JSON: %s | %s" % (e, r.stdout[:200]))
        if not d.get("ok"):
            raise RuntimeError("lark-cli 返回失败: %s" % str(d)[:260])
        return d

    def reset(self):
        """CLI 会话疑似异常时清空缓存，下次调用重新探测候选。"""
        self.lark = None

    def call(self, args):
        if self.lark:
            return self._run(self.lark, args)
        for p in self.candidates:
            try:
                d = self._run(p, args)
                self.lark = p
                return d
            except Exception as e:
                self.last_err = str(e)
                continue
        raise RuntimeError("所有 lark-cli 候选都失败: %s" % self.last_err)


# ---------- 卡片解析 ----------

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


def _tokens_from_json_card(obj):
    toks = []

    def walk(node):
        if isinstance(node, list):
            for it in node:
                walk(it)
        elif isinstance(node, dict):
            if node.get("tag") == "text":
                toks.append(("text", node.get("text", ""), None, None))
            elif node.get("tag") == "at":
                toks.append(("at", node.get("user_name", ""), node.get("user_name", ""),
                             node.get("user_id", "")))

    walk(obj.get("elements") or [])
    return toks


def _tokens_from_xml_card(content):
    body = re.sub(r'^<card[^>]*>', '', content, count=1)
    body = re.sub(r'</card>\s*$', '', body)
    toks = []
    at_re = re.compile(r'@([^@()\n]+?)\s*\((ou_[0-9a-zA-Z]+)\)')
    pos = 0
    for mt in at_re.finditer(body):
        if mt.start() > pos:
            toks.append(("text", body[pos:mt.start()], None, None))
        toks.append(("at", mt.group(1), mt.group(1), mt.group(2)))
        pos = mt.end()
    if pos < len(body):
        toks.append(("text", body[pos:], None, None))
    return toks


def _value_after_label(toks, label):
    """返回 (kind, name, open_id)；只取同行内容，避免把后续字段串进来。"""
    for i, t in enumerate(toks):
        if t[0] != "text" or not t[1]:
            continue
        if not re.search(label, t[1]):
            continue
        tail = re.split(label, t[1], maxsplit=1)[-1]
        tail = tail.split("\n")[0].replace("**", "").strip(" :：\u3000")
        if tail:
            if tail in ("--", "-", "—"):
                return ("empty", None, None)
            return ("text", tail, None)
        for nxt in toks[i + 1:i + 3]:
            if nxt[0] == "at":
                return ("at", nxt[1], nxt[3])
            if nxt[0] == "text":
                s = nxt[1].split("\n")[0].replace("**", "").strip(" :：\u3000")
                if s in ("--", "-", "—"):
                    return ("empty", None, None)
                if s:
                    return ("text", s, None)
        return ("empty", None, None)
    return (None, None, None)


def parse_card(content, mentions=None):
    info = {
        "title": card_title(content),
        "ticket_title": None,
        "status": None,
        "priority": None,
        "reporter_name": None,
        "reporter_open_id": None,
        "creator_name": None,
        "assignee_name": None,
        "ticket_no": None,
        "detail_url": None,
    }
    if not content:
        return info

    # 卡片正文自带「查看详情」链接，工单号直接从 URL 里取，不靠标题反查
    mt = DETAIL_LINK_RE.search(content)
    if mt:
        info["ticket_no"] = mt.group(1)
        info["detail_url"] = mt.group(0)

    if content.lstrip().startswith("{"):
        try:
            obj = json.loads(content)
        except Exception:
            obj = {}
        toks = _tokens_from_json_card(obj)
        key2id = {}
        for mt in (mentions or []):
            if mt.get("key"):
                key2id[mt["key"]] = mt.get("id")
        fixed = []
        for t in toks:
            if t[0] == "at" and t[3] in key2id:
                fixed.append(("at", t[1], t[1], key2id[t[3]]))
            else:
                fixed.append(t)
        toks = fixed
    else:
        toks = _tokens_from_xml_card(content)

    for key, label in (("reporter_name", r"报告人[：:]"),
                       ("ticket_title", r"工单标题[：:]"),
                       ("status", r"工单状态[：:]"),
                       ("priority", r"优先级[：:]"),
                       ("creator_name", r"创建人[：:]"),
                       ("assignee_name", r"经办人[：:]")):
        kind, name, oid = _value_after_label(toks, label)
        if name:
            info[key] = name
            if key == "reporter_name":
                info["reporter_open_id"] = oid

    return info


def mentions_me(m):
    """卡片是否 @ 了本人。open_id 分应用维度，同一人可能不同，因此再用姓名兜底。"""
    for x in (m.get("mentions") or []):
        if x.get("id") in TARGET_USER_IDS:
            return True
        if TARGET_USER_NAME and (x.get("name") or "").strip() == TARGET_USER_NAME:
            return True
    return False


def is_alert_card(m):
    """@我 的告警类卡片（不是新工单），这类不需要处理。"""
    if not mentions_me(m):
        return False
    title = card_title(m.get("content", ""))
    if NEW_TICKET_KEYWORD in title:
        return False
    return bool(re.search(r"(告警|报警|alert|incident|故障)", title, re.I))


def is_target_card(m):
    if not mentions_me(m):
        return False
    title = card_title(m.get("content", ""))
    # 只认「你有一条新工单需要处理」；告警/通知类卡片一律不碰
    return NEW_TICKET_KEYWORD in title and not re.search(r"(告警|报警|alert|incident)", title, re.I)


def position(m):
    try:
        return int(str(m.get("message_position", "0") or 0))
    except ValueError:
        return 0


# ---------- 工单号 -> chat_id（Glata 工单页权威字段）----------

_ticket_cache = None


def _load_ticket_cache():
    global _ticket_cache
    if _ticket_cache is None:
        try:
            _ticket_cache = json.load(open(TICKET_CACHE_FILE))
        except Exception:
            _ticket_cache = {}
    return _ticket_cache


def _merge_json(path, new):
    """并发安全写入：加文件锁 + 先读后并 + 原子替换。

    多张卡片并发分析时会有多个进程同时写同一份缓存，直接覆盖会丢别人的条目。
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".lock", "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}
                data.update(new or {})
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception as e:
        log("[warn] 缓存写入失败 %s: %s" % (path, e))


def _save_ticket_cache():
    _merge_json(TICKET_CACHE_FILE, _ticket_cache or {})


def ticket_info(ticket_no, force=False):
    """用工单号打开 Glata 工单页，取权威字段：值班号会话编号 + 转人工时间。

    读页面依赖一个已登录的调试浏览器（cdp_client，登录一次长期复用）。
    命中缓存时不碰浏览器；读不到返回 None，由调用方按定位失败处理。
    """
    if not ticket_no:
        return None
    cache = _load_ticket_cache()
    hit = cache.get(str(ticket_no))
    if hit and not force:
        # 旧版缓存没有 closed 字段，命中也要重读一次，避免用过期结构
        if (time.time() - hit.get("fetched_at", 0) < TICKET_CACHE_TTL
                and "closed" in hit):
            return hit
    try:
        import cdp_client
    except Exception as e:
        log("[ticket] cdp_client 不可用: %s" % e)
        return None
    try:
        meta = cdp_client.read_ticket(ticket_no)
    except Exception as e:
        log("[ticket] 读工单页失败 %s: %s" % (ticket_no, e))
        return None
    ft = (meta.get("form_type") or "").strip()
    if ft and re.match(r"^(Alert|告警)$", ft, re.I):
        meta["fetched_at"] = time.time()
        meta["alert_only"] = True
        cache[str(ticket_no)] = meta
        _save_ticket_cache()
        log("[skip] %s 是告警工单（Form Type=%s），无需值班号记录" % (ticket_no, ft))
        return meta
    if not meta.get("chat_id"):
        log("[ticket] 工单页未给出会话编号: %s" % ticket_no)
        return None
    meta["fetched_at"] = time.time()
    cache[str(ticket_no)] = meta
    _save_ticket_cache()
    log("[ticket] %s -> chat_id=%s transfer=%s"
        % (ticket_no, meta.get("chat_id"), meta.get("transfer_time")))
    return meta


def iso_since(transfer_time, buffer_sec=TRANSFER_BUFFER_SEC):
    """把「2026-11-05 17:11:30」转成 lark-cli 的 --start（ISO8601，带本地时区）。"""
    if not transfer_time:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.datetime.strptime(transfer_time, fmt)
        except Exception:
            continue
        dt -= datetime.timedelta(seconds=buffer_sec)
        return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return None


# ---------- 消息 ----------

def fetch_chat_messages(runner, chat_id, page_size=50, page_all=False, page_limit=20, since=None):
    """拉取聊天记录。page_all=True 时自动翻页（--page-limit 控制上限）。
    since 传入 ISO8601 起始时间时只取该时间之后的消息（用于按转人工时间精确切片）。"""
    cmd = ["im", "+chat-messages-list", "--chat-id", chat_id,
           "--page-size", str(page_size), "--format", "json", "--no-reactions"]
    if since:
        cmd += ["--start", since]
    if page_all:
        cmd += ["--page-all", "--page-limit", str(page_limit)]
    d = runner.call(cmd)
    return (d.get("data") or {}).get("messages") or []


# ---------- 会话切片 ----------

WELCOME_MARK = "欢迎来到 IT 值班号"
BOT_NAMES = {"IT 值班号", "工程师", "系统", "值班号", "机器人", ""}
IMG_DIR = os.environ.get("ONCALL_IMG_DIR") or "/private/tmp/oncall_imgs"   # 注意: tesseract 读不了 /tmp 软链
TESSERACT = os.environ.get("TESSERACT_BIN") or "tesseract"
OCR_LANG = os.environ.get("OCR_LANG", "eng+chi_sim")
# macOS 原生 Vision 引擎（本地离线、中文识别明显优于 tesseract），缺失时回退 tesseract
VISION_OCR = os.environ.get("VISION_OCR_BIN") or os.path.join(BASE_DIR, "bin", "vision_ocr")
OCR_CACHE_VER = "v5"          # 换引擎/改算法后递增，旧缓存自动作废


def _to_ts(m):
    t = (m.get("create_time") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(t, fmt))
        except Exception:
            continue
    return 0


def is_human_msg(m):
    """是否真人发言（排除值班号/工程师/系统这类机器人身份）。"""
    if m.get("msg_type") not in ("text", "post", "image"):
        return False
    name = ((m.get("sender") or {}).get("name") or "").strip()
    return name not in BOT_NAMES


def find_session_start(messages, max_gap=600):
    """messages 为 desc 顺序（最新在前）。

    以「欢迎来到 IT 值班号」卡片为锚点，取**时间最晚的一个有效锚点**：
    锚点后 max_gap 秒内必须出现真人发言，否则视为换班/机器人接管，不算新会话。
    返回该锚点的下标（从它开始就是本次问题），找不到返回 0。
    """
    for i, m in enumerate(messages):
        if m.get("msg_type") != "interactive":
            continue
        if WELCOME_MARK not in (m.get("content") or ""):
            continue
        t0 = _to_ts(m)
        for j in range(i - 1, max(-1, i - 8), -1):
            if _to_ts(messages[j]) - t0 > max_gap:
                break
            if is_human_msg(messages[j]):
                return i
    return 0


# ---------- 图片 OCR ----------

OCR_CACHE = None


def _ocr_cache():
    global OCR_CACHE
    if OCR_CACHE is None:
        path = os.path.join(ONCALL_DIR, ".ocr_cache.json")
        try:
            with open(path, encoding="utf-8") as f:
                OCR_CACHE = json.load(f)
        except Exception:
            OCR_CACHE = {}
    return OCR_CACHE


def _save_ocr_cache():
    try:
        _merge_json(os.path.join(ONCALL_DIR, ".ocr_cache.json"), _ocr_cache())
    except Exception as e:
        log("[warn] OCR 缓存写入失败: %s" % e)


def extract_img_key(content):
    m = re.search(r"img_[A-Za-z0-9_\-]+", content or "")
    return m.group(0) if m else None


def _oclark(runner):
    """取 lark-cli 可执行路径（不依赖 runner.call 的 JSON 解析）。"""
    return getattr(runner, "lark", None) or ((runner.candidates or [None])[0])


# OCR 里常见的水印/编号噪声：纯数字串、账号名、纯符号
OCR_NOISE_RE = re.compile(r"^(?:[0-9]{4,}|[a-z0-9_.@\-]{6,24}|[\W_]{1,})$", re.I)
# 这些词出现就一律保留，哪怕整行看着像噪声
OCR_KEY_RE = re.compile(
    r"(error|denied|reject|forbidden|invalid|fail|unauthor|权限|拒绝|失败|无法|报错|错误|"
    r"log\s*id|code|status|page\s*owner|exception|timeout|未开|不通|打不开)", re.I)


def clean_ocr_text(t, max_len=520):
    """统一 OCR 输出：符号半角化、去水印噪声行、保留换行。
    截图里的界面噪声对判断没价值，但会显著拉长给 AI 的输入（实测占 40% 导致 AI 慢 37s）。"""
    if not t:
        return ""
    t = t.replace("（", "(").replace("）", ")").replace("＇", "'").replace("＂", '"')
    lines = []
    seen = set()
    for ln in t.splitlines():
        ln = re.sub(r"[ \t]+", " ", ln).strip()
        if not ln:
            continue
        if len(ln) <= 1 and not ln.isdigit():
            continue
        if OCR_NOISE_RE.match(ln) and not OCR_KEY_RE.search(ln):
            continue
        if ln in seen:            # 同一张图内重复出现的界面文本只留一次
            continue
        seen.add(ln)
        lines.append(ln)
    text = "\n".join(lines)
    # 超长时保头保尾，避免把结尾的 log id / 报错结论截掉
    if len(text) > max_len:
        text = text[: max_len - 160] + "\n…\n" + text[-160:]
    return text


def _ocr_vision(path, timeout):
    """调 macOS Vision 引擎；二进制不存在或失败时返回 None 交由 tesseract 兜底。
    中文截图用 zh+en 模型；纯英文/代码截图（汉字极少）追加跑一次 en-US 模型，
    后者对英文小字明显更准（Pearl/and 不再误识）。"""
    if not os.path.exists(VISION_OCR):
        return None
    try:
        p = subprocess.run([VISION_OCR, path], capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            return None
        text = p.stdout or ""
        body = text.replace("\n", "").replace(" ", "")
        cjk = sum(1 for ch in body if "\u4e00" <= ch <= "\u9fff")
        if body and cjk / len(body) < 0.05:
            p2 = subprocess.run([VISION_OCR, path, "0.3", "en-US"],
                                capture_output=True, text=True, timeout=timeout)
            t2 = (p2.stdout or "") if p2.returncode == 0 else ""
            # 英文模型没丢掉内容才替换，避免把中英混排图的中文部分抹掉
            if len(t2.strip()) >= len(text.strip()) * 0.8:
                text = t2
        return text
    except Exception as e:
        log("[warn] Vision OCR 异常，回退 tesseract: %s" % e)
        return None


def ocr_image(runner, message_id, img_key, timeout=60):
    """下载群图片并本地 OCR（优先 macOS Vision，回退 tesseract）。失败返回空串。"""
    cache = _ocr_cache()
    ck = "%s:%s" % (OCR_CACHE_VER, img_key)
    if ck in cache:
        return cache[ck]
    os.makedirs(IMG_DIR, exist_ok=True)
    out_path = os.path.join(IMG_DIR, img_key + ".png")
    try:
        if not os.path.exists(out_path):
            lark = _oclark(runner)
            if not lark:
                return ""
            # 下载命令的 stderr 可能带二进制，这里用字节模式且不解析输出
            subprocess.run([lark, "im", "+messages-resources-download",
                            "--message-id", message_id, "--file-key", img_key,
                            "--type", "image", "--output", out_path],
                           capture_output=True, timeout=timeout, env=lark_env())
        if not os.path.exists(out_path):
            return ""
        text = _ocr_vision(out_path, timeout)
        if text is None:
            text = subprocess.run([TESSERACT, out_path, "stdout", "-l", OCR_LANG, "--psm", "6"],
                                  capture_output=True, text=True, timeout=timeout).stdout or ""
            text = " ".join(text.split())
            # tesseract 逐字识别中文时会插空格，这里把汉字之间的空格收掉
            text = re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", text)
        text = clean_ocr_text(text)
    except Exception as e:
        log("[warn] 图片 OCR 失败 %s: %s" % (img_key, e))
        return ""
    cache[ck] = text
    _save_ocr_cache()
    return text


def msg_text(m):
    if not m.get("content"):
        return ""
    c = re.sub(r'^<card[^>]*>', '[卡片] ', m["content"])
    c = re.sub(r'</card>\s*$', '', c)
    c = c.replace("\n", " ").strip()
    if not c and m.get("msg_type") == "system":
        c = "[系统消息]"
    return c[:400]


def save_result(result):
    os.makedirs(ONCALL_DIR, exist_ok=True)
    key = result.get("message_id") or (result.get("oncall") or {}).get("chat_id") or "manual"
    path = os.path.join(ONCALL_DIR, "%s.json" % key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path


def print_chat(result, show=15):
    t = result.get("ticket") or {}
    print("")
    print("=" * 72)
    print("卡片: %s" % (t.get("title") or "-"))
    print("工单: %s" % (t.get("ticket_title") or "-"))
    print("状态: %s | 优先级: %s" % (t.get("status") or "-", t.get("priority") or "-"))
    print("报告人: %s (%s)" % (t.get("reporter_name") or "-", t.get("reporter_open_id") or "-"))
    oc = result.get("oncall")
    if oc:
        print("值班号: %s (%s)" % (oc.get("name") or "工单页直取", oc.get("chat_id")))
        print("定位方式: %s" % result.get("match_by", "-"))
        meta = result.get("ticket_meta") or {}
        if meta:
            print("工单号: %s | 转人工: %s | 会话结束: %s"
                  % (meta.get("ticket_no") or "-", meta.get("transfer_time") or "-",
                     meta.get("session_end_time") or "-"))
        if result.get("since"):
            print("拉取起点: %s（转人工时间 - 90s 缓冲）" % result["since"])
        if result.get("status") != "ok":
            print("结果: %s — %s" % (result.get("status"), result.get("hint") or "-"))
        print("聊天记录 %d 条（展示最近 %d 条）:" % (result.get("message_count", 0), show))
        for m in (result.get("messages") or [])[:show]:
            print("  [%s] %s: %s" % (m.get("create_time", "?"),
                                     (m.get("sender") or {}).get("name") or "系统",
                                     msg_text(m)))
    else:
        print("值班号: 未定位（%s）" % (result.get("status") or "-"))
        if result.get("expect_group"):
            print("期望群名: %s" % result["expect_group"])
        if result.get("hint"):
            print("提示: %s" % result["hint"])
    print("=" * 72)


def process_card(runner, card, cache, chat_page_size):
    mid = card.get("message_id")
    info = parse_card(card.get("content", ""), card.get("mentions") or [])
    result = {
        "message_id": mid,
        "card_time": card.get("create_time"),
        "message_position": card.get("message_position"),
        "ticket": info,
        "oncall": None,
        "messages": [],
        "status": "ok",
    }

    log("[card] %s | 工单=%s | 报告人=%s(%s)"
        % (info.get("title"), info.get("ticket_title"),
           info.get("reporter_name") or "-", info.get("reporter_open_id") or "-"))

    rname, rid = info.get("reporter_name"), info.get("reporter_open_id")
    tno = info.get("ticket_no")
    group, match_by, tmeta = None, None, None

    # 1) 主路径：卡片里的工单号 -> Glata 工单页拿权威字段
    #    好处是不依赖群名、也不依赖 Glata 是否已经把你拉进群
    if tno:
        tmeta = ticket_info(tno)
        if tmeta and tmeta.get("chat_id"):
            group = {"chat_id": tmeta["chat_id"], "name": None}
            match_by = "ticket_page"
            result["ticket_meta"] = {
                "ticket_no": str(tno),
                "chat_id": tmeta.get("chat_id"),
                "transfer_time": tmeta.get("transfer_time"),
                "session_end_time": tmeta.get("session_end_time"),
                "requester": tmeta.get("requester"),
            }

    # 不再按报告人猜群名：工单页取不到 chat_id 就明确失败（可重试），不做启发式兜底
    if not group:
        if not tno:
            result["status"] = "no_ticket_no"
            result["hint"] = "卡片正文没有工单详情链接，无法拿到工单号"
            log("[skip] 卡片未带工单详情链接")
        else:
            result["status"] = "ticket_page_failed"
            result["hint"] = "工单页未取到值班号会话编号（可重试）"
            log("[retry] 工单页未取到会话编号: %s" % tno)
        return result, cache

    result["oncall"] = {
        "chat_id": group["chat_id"],
        "name": group.get("name"),
    }
    result["match_by"] = match_by
    log("[oncall] %s -> %s (%s) via %s"
        % (rname or tno, group.get("name"), group["chat_id"], match_by))

    # 工单已结束 -> 值班号群随之关闭，再拉必然 230002，直接跳过（不需要重试）
    if (tmeta or {}).get("closed"):
        result["status"] = "resolved_skip"
        result["hint"] = "工单已结束（%s，会话结束 %s），值班号群已关闭，无需拉取" % (
            tmeta.get("closure_type") or "-", tmeta.get("session_end_time") or "-")
        log("[resolved] %s 已结束（%s），跳过拉取"
            % (tno, tmeta.get("closure_type") or "-"))
        return result, cache

    # 用转人工时间做会话起点（留 90s 缓冲，确保包含开场欢迎卡）
    since = iso_since((tmeta or {}).get("transfer_time"))
    result["since"] = since
    if since:
        log("[chat] 按转人工时间切片, start=%s" % since)

    try:
        msgs = fetch_chat_messages(runner, group["chat_id"], chat_page_size,
                                   page_all=True, page_limit=20, since=since)
    except Exception as e:
        err = str(e)
        if "230002" in err:
            # chat_id 是准的，但调用方不在这个群里：Glata 还没拉你进去，或值班号群已关闭
            result["status"] = "not_in_chat"
            result["hint"] = "你不在该群（Glata 尚未拉你进群，或值班号群已关闭）"
            result["error"] = err
            log("[not_in_chat] %s: 不在群 %s" % (group["chat_id"], "230002"))
        else:
            result["status"] = "chat_fetch_failed"
            result["error"] = err
            log("[error] 拉取聊天记录失败: %s" % err)
        return result, cache

    result["messages"] = msgs
    result["message_count"] = len(msgs)
    log("[chat] 拉到 %d 条聊天记录" % len(msgs))
    return result, cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message-id", default=None, help="指定卡片 message_id")
    ap.add_argument("--chat-id", default=None, help="直接读取指定群，跳过卡片映射")
    ap.add_argument("--count", type=int, default=1, help="处理最近 N 条命中卡片，默认 1")
    ap.add_argument("--page-size", type=int, default=50, help="每群拉取聊天记录条数，默认 50")
    args = ap.parse_args()

    if not args.chat_id and (not TARGET_USER_ID or "YOUR_" in str(TARGET_USER_ID)
                             or not CHAT_ID or "YOUR_" in str(CHAT_ID)):
        log("[error] 配置未填写：先运行 python3 deploy_check.py，"
            "把探测出的身份配置填写到本机 config.local.json")
        return 4

    t0 = time.time()
    runner = LarkRunner()

    # 直接指定群：不依赖卡片
    if args.chat_id:
        result = {
            "message_id": None,
            "ticket": {},
            "oncall": {"chat_id": args.chat_id, "name": None},
            "status": "ok",
        }
        try:
            info = runner.call(["im", "chats", "get", "--chat-id", args.chat_id, "--format", "json"])
            result["oncall"]["name"] = ((info.get("data") or {}).get("name"))
        except Exception:
            pass
        try:
            msgs = fetch_chat_messages(runner, args.chat_id, args.page_size)
        except Exception as e:
            result["status"] = "chat_fetch_failed"
            result["error"] = str(e)
            log("[error] 拉取聊天记录失败: %s" % e)
            print_chat(result)
            return 3
        result["messages"] = msgs
        result["message_count"] = len(msgs)
        save_result(result)
        log("[chat] %s 拉到 %d 条聊天记录，耗时 %.1fs"
            % (result["oncall"]["name"] or args.chat_id, len(msgs), time.time() - t0))
        print_chat(result)
        return 0

    # 卡片驱动
    if args.message_id:
        # 按 ID 直接取，避免翻页（卡片可能早于最新一页）
        d = runner.call(["im", "+messages-mget", "--message-ids", args.message_id,
                         "--format", "json", "--no-reactions"])
        cards = (d.get("data") or {}).get("messages") or []
        if not cards:
            log("[error] 取不到 message_id=%s 的消息" % args.message_id)
            return 2
        if not is_target_card(cards[0]):
            log("[warn] 该消息不是 @%s 的新工单卡片，仍按卡片解析处理" % TARGET_USER_NAME)
    else:
        d = runner.call(["im", "+chat-messages-list", "--chat-id", CHAT_ID,
                         "--page-size", "50", "--format", "json", "--no-reactions"])
        msgs = (d.get("data") or {}).get("messages") or []
        cards = sorted([m for m in msgs if is_target_card(m)], key=position, reverse=True)
        # 默认跳过没有报告人的卡片（Monisight 自动告警），取最近 N 条有效卡片
        valid = [m for m in cards
                 if parse_card(m.get("content", ""), m.get("mentions") or []).get("reporter_open_id")
                 or parse_card(m.get("content", ""), m.get("mentions") or []).get("reporter_name")]
        cards = (valid or cards)[:max(1, args.count)]

    if not cards:
        log("[warn] 最新一页没有 @%s 的新工单卡片" % TARGET_USER_NAME)
        return 0

    # 兼容保留的返回值；不再使用或加载群名索引。
    cache = None

    log("[start] 待处理卡片 %d 条" % len(cards))
    results = []
    for c in cards:
        r, cache = process_card(runner, c, cache, args.page_size)
        path = save_result(r)
        print_chat(r)
        log("[save] %s" % path)
        results.append(r)

    ok = sum(1 for r in results if r.get("status") == "ok")
    log("[done] 成功 %d/%d 条，耗时 %.1fs" % (ok, len(results), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
