"""通过 CDP 读取 Glata 工单详情页的权威字段（chat_id / 转人工时间）。

为什么需要它：
本项目读取工单页可见 DOM 来取得权威字段，不提取或复用页面认证 header。
这里复用一个常驻的专用浏览器（独立 profile，登录态过期需本人验证），
从渲染后的页面文本抽取字段。

依赖：websocket-client
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

CDP_PORT = int(os.environ.get("GLATA_CDP_PORT", "9222"))
CHROME_BIN = os.environ.get(
    "GLATA_CHROME_BIN",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
PROFILE_DIR = os.path.expanduser(os.environ.get("GLATA_CDP_PROFILE", "~/.glata_cdp_profile"))
DETAIL_URL = "https://glata.bytedance.com/ens/engineer/serviceTicket/detail/%s"

BASE = "http://127.0.0.1:%d" % CDP_PORT


def _gj(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def is_alive():
    try:
        _gj("/json/version", timeout=2)
        return True
    except Exception:
        return False


def browser_mode():
    value = os.environ.get("GLATA_BROWSER_MODE", "headless").strip().lower()
    if value not in ("headless", "headed"):
        raise ValueError("GLATA_BROWSER_MODE 仅支持 headless 或 headed")
    return value


def _browser_command(mode):
    if mode not in ("headless", "headed"):
        raise ValueError("浏览器模式无效")
    cmd = [CHROME_BIN, "--remote-debugging-address=127.0.0.1",
           "--remote-debugging-port=%d" % CDP_PORT,
           "--user-data-dir=" + PROFILE_DIR,
           "--no-first-run", "--no-default-browser-check"]
    if mode == "headless":
        cmd.append("--headless=new")
    cmd.append("about:blank")
    return cmd


def _matches_browser_command(line):
    # ps 的 command 不会为应用路径中的空格补引号，不能用 shlex 拆可执行路径。
    if not line.startswith(CHROME_BIN + " ") or re.search(r"(?:^|\s)--type=", line):
        return False
    port = "--remote-debugging-port=%d" % CDP_PORT
    profile = "--user-data-dir=" + PROFILE_DIR
    return (bool(re.search(r"(?:^|\s)" + re.escape(port) + r"(?:\s|$)", line))
            and bool(re.search(r"(?:^|\s)" + re.escape(profile) + r"(?:\s--|\s(?:about:|https?://)|$)", line)))


def running_browser_mode():
    """只查询进程参数，不读取 profile / cookie / token；未知时保守复用。"""
    result = subprocess.run(["ps", "-ax", "-o", "command="], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if _matches_browser_command(line.strip()):
            return "headless" if "--headless" in line else "headed"
    return "unknown"


def ensure_chrome(start=True, wait=15, mode=None):
    """后台复用专用浏览器；默认无窗口，首次登录显式选择 headed。"""
    requested = mode or browser_mode()
    if requested not in ("headless", "headed"):
        raise ValueError("浏览器模式无效")
    if is_alive():
        if requested == "headed" and running_browser_mode() == "headless":
            raise RuntimeError("后台浏览器已在线，不能同时用同一 profile 开登录窗口。"
                               "先 bash stop.sh --all，再 GLATA_BROWSER_MODE=headed bash start.sh 登录。")
        return True  # 不导航、不新开标签、不重启已有浏览器。
    if not start or not os.path.isfile(CHROME_BIN):
        return False
    os.makedirs(os.path.dirname(PROFILE_DIR), exist_ok=True)
    # 多个 AI/启动进程可能同时恢复浏览器；锁只保护 Chrome 启动，不串行飞书调用。
    with open(PROFILE_DIR + ".launch.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if is_alive():
            return True
        os.makedirs(PROFILE_DIR, exist_ok=True)
        try:
            process = subprocess.Popen(_browser_command(requested),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
        except OSError:
            return False
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if is_alive():
                return True
            if process.poll() is not None:
                return False
            time.sleep(0.25)
    return False


def stop_browser(wait=5):
    """只结束同时匹配 Chrome 路径、端口和专用 profile 的主进程。"""
    import signal
    result = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True)
    pids = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        if _matches_browser_command(fields[1]):
            pids.append(int(fields[0]))
    if not pids:
        print("没有匹配的专用浏览器，未操作其他 Chrome 实例")
        return True
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not is_alive():
            print("专用调试浏览器已关闭（保留 profile 登录数据）")
            return True
        time.sleep(0.25)
    print("[警告] 专用浏览器尚未退出，未强杀；请确认后再切换模式", file=sys.stderr)
    return False


def login_help():
    return ("工单页需要重新登录 Glata（飞书 CLI 授权与网页登录是两回事）。"
            "先 bash stop.sh --all；再 GLATA_BROWSER_MODE=headed bash start.sh，"
            "在专用窗口中完成登录；登录后 bash stop.sh --all，再按原参数 bash start.sh，"
            "即恢复后台浏览器。独立 profile 会保留，不删除登录数据。")


def _pages():
    return [t for t in _gj("/json/list") if t.get("type") == "page"]


def _pick_page():
    """优先复用已打开的 Glata 页，避免每次新开 tab。"""
    pages = _pages()
    for t in pages:
        if "glata.bytedance.com" in (t.get("url") or ""):
            return t
    for t in pages:
        if (t.get("url") or "").startswith("about:") or t.get("url", "").startswith("chrome://newtab"):
            return t
    if pages:
        return pages[0]
    return None


def _open_page(url):
    """浏览器的 tab 可能被用户全部关掉。此时新开一个，否则读工单页会一直失败。

    Chrome 新版本要求 PUT /json/new，老一些的只认 GET，两者都试一遍。
    """
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/json/new?%s" % (CDP_PORT, url), method=method)
            urllib.request.urlopen(req, timeout=10).read()
            time.sleep(2.5)
            return True
        except Exception:
            continue
    return False


class _Conn(object):
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=25)
        self.mid = 0

    def call(self, method, params=None):
        self.mid += 1
        mid = self.mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                return m

    def ev(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return (r.get("result", {}).get("result") or {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ---------- 字段解析 ----------

CHAT_ID_PATS = [
    r"Helpdesk Session Chat ID\s*\n?\s*(oc_[0-9a-f]{32})",
    r"值班号会话编号\s*\n?\s*(oc_[0-9a-f]{32})",
]
TRANSFER_PATS = [
    r"Transfer to agent at\s*\n?\s*(\d{4}-\d{2}-\d{2})\s*\n?\s*(\d{2}:\d{2}:\d{2})",
    r"转人工时间\s*\n?\s*(\d{4}-\d{2}-\d{2})\s*\n?\s*(\d{2}:\d{2}:\d{2})",
]
SESSION_END_PATS = [
    r"conversation ended at\s*\n?\s*(\d{4}-\d{2}-\d{2})\s*\n?\s*(\d{2}:\d{2}:\d{2})",
    r"会话结束时间\s*\n?\s*(\d{4}-\d{2}-\d{2})\s*\n?\s*(\d{2}:\d{2}:\d{2})",
]
CLOSURE_PATS = [
    r"Closure Type\s*\n\s*([A-Za-z][A-Za-z \-]{0,20})",
    r"关闭类型\s*\n\s*([^\n]{1,20})",
]
REQUESTER_PATS = [
    r"Requester\s*\n\s*([^\n]{1,60})",
    r"报告人\s*\n\s*([^\n]{1,60})",
]


def _first(patterns, text, groups=1):
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1) if groups == 1 else m.groups()
    return None


def parse_fields(text):
    """从页面文本里抽字段。中英文界面都兼容。"""
    out = {}

    cid = _first(CHAT_ID_PATS, text)
    if not cid:
        m = re.search(r"oc_[0-9a-f]{32}", text)
        cid = m.group(0) if m else None
    out["chat_id"] = cid

    tr = _first(TRANSFER_PATS, text, groups=2)
    out["transfer_time"] = ("%s %s" % tr) if tr else None

    se = _first(SESSION_END_PATS, text, groups=2)
    out["session_end_time"] = ("%s %s" % se) if se else None

    # 工单是否已结束：结束后值班号群会被关闭，再拉聊天记录必然 230002
    ct = _first(CLOSURE_PATS, text)
    out["closure_type"] = ct.strip() if ct else None
    out["closed"] = bool(out["closure_type"] or out["session_end_time"])

    m = re.search(r"(IT-\d{6,})", text)
    out["ticket_id"] = m.group(1) if m else None

    req = _first(REQUESTER_PATS, text)
    if req:
        req = req.strip()
        if req.lower() in ("created by", "创建人"):
            req = None
    out["requester"] = req

    ft = _first([r"Form\s*Type\s*\n\s*([A-Za-z ]+)", r"表单类型\s*\n\s*([^\n]+)"], text)
    out["form_type"] = ft.strip() if ft else None

    # 是否看起来像登录页
    out["_login_page"] = ("Log In With QR Code" in text) or ("扫码登录" in text) or ("Scan this QR code" in text)
    return out


def read_ticket(ticket_no, timeout=30, poll=0.8, verbose=False):
    """打开工单详情页并抽取字段。返回 dict；失败抛 RuntimeError。"""
    if not ensure_chrome():
        raise RuntimeError("调试浏览器未启动，且自动拉起失败（检查 Chrome 路径/端口 %d）" % CDP_PORT)

    page = _pick_page()
    if not page:
        # tab 被关光了：直接开一个工单详情页，顺带省掉后面一次导航
        _open_page(DETAIL_URL % str(ticket_no))
        page = _pick_page()
    if not page:
        raise RuntimeError("调试浏览器里没有可用页面，自动新建 tab 也失败")

    conn = _Conn(page["webSocketDebuggerUrl"])
    try:
        conn.call("Page.enable")
        target = DETAIL_URL % str(ticket_no)
        conn.call("Page.navigate", {"url": target})

        deadline = time.time() + timeout
        text = ""
        login_hits = 0
        while time.time() < deadline:
            time.sleep(poll)
            text = conn.ev("document.body.innerText") or ""
            f = parse_fields(text)
            # 导航瞬间可能读到上一页残留的登录文案，连续命中才算真的未登录
            if f.get("_login_page") and not f.get("chat_id"):
                login_hits += 1
                if login_hits >= 3:
                    raise RuntimeError(login_help())
                continue
            login_hits = 0
            # 告警工单（Form Type=Alert）没有值班号群，等 chat_id 只会白等 30 秒
            if f.get("form_type") and re.match(r"^(Alert|告警)$", f["form_type"].strip(), re.I):
                f["ticket_no"] = str(ticket_no)
                f["url"] = target
                f.pop("_login_page", None)
                if verbose:
                    sys.stderr.write("[ticket] %s 是告警工单（Form Type=%s），无需值班号\n"
                                     % (ticket_no, f["form_type"]))
                return f
            if f.get("chat_id") and f.get("transfer_time"):
                f["ticket_no"] = str(ticket_no)
                f["url"] = target
                f.pop("_login_page", None)
                if verbose:
                    sys.stderr.write("[ticket] %s chat_id=%s transfer=%s\n"
                                     % (ticket_no, f["chat_id"], f["transfer_time"]))
                return f

        f = parse_fields(text)
        if f.get("_login_page"):
            raise RuntimeError(login_help())
        raise RuntimeError("页面字段未就绪（%ss 超时，页面长度 %d）：%s"
                           % (timeout, len(text), target))
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="专用调试浏览器：默认后台运行；飞书 CLI 授权不等于 Glata 网页登录")
    parser.add_argument("ticket_no", nargs="?", help="需要读取的工单号")
    parser.add_argument("--ensure-browser", action="store_true", help="仅确保浏览器在线，不读取工单或触发 AI")
    parser.add_argument("--stop-browser", action="store_true", help="仅关闭匹配端口/profile 的专用浏览器，不删登录数据")
    args = parser.parse_args(argv)
    try:
        if args.stop_browser:
            return 0 if stop_browser() else 4
        if args.ensure_browser:
            mode = browser_mode()
            was_alive = is_alive()
            if not ensure_chrome(mode=mode):
                raise RuntimeError("专用浏览器启动失败：检查 Chrome 路径、端口、profile 是否被占用；未删除任何登录数据")
            if mode == "headed" and not _pages():
                if not _open_page(os.environ.get("GLATA_HOME_URL", "https://glata.bytedance.com")):
                    raise RuntimeError("浏览器已在线但登录标签创建失败")
            elif mode == "headed" and not was_alive:
                _open_page(os.environ.get("GLATA_HOME_URL", "https://glata.bytedance.com"))
            if mode == "headless":
                if was_alive:
                    current = running_browser_mode()
                    label = {"headless": "无窗口后台", "headed": "原有窗口，未强制重启", "unknown": "保持现有模式"}[current]
                    print("调试浏览器已在线，复用现有实例 (端口 %d，%s)；不重复开页" % (CDP_PORT, label))
                else:
                    print("调试浏览器已在后台启动 (端口 %d，无窗口)" % CDP_PORT)
            else:
                print("调试浏览器登录窗口已在线 (端口 %d)，请完成 Glata 登录；日常无需此模式" % CDP_PORT)
            return 0
        if not args.ticket_no or not str(args.ticket_no).isdigit():
            parser.error("请提供数字工单号，或使用 --ensure-browser")
        print(json.dumps(read_ticket(args.ticket_no, verbose=True), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError) as exc:
        print("[错误] %s" % exc, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
