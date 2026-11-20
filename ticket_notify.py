#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单增强提示：应用机器人 @本人并电话加急，与 AI 分析独立。

只用 Python 标准库；凭证/令牌仅在内存中。提醒状态独立持久化，
每次启动建立当前消息位置基线，不回放存量；发送结果不确定时不自动重拨。
"""
import concurrent.futures
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from project_config import get_setting

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_APP_ID = get_setting("bot_app_id")
DEFAULT_CHAT_ID = get_setting("notify_chat_id")
MESSAGE_TEXT = "你有新工单！！！！"
HOST = "https://open.feishu.cn"


class NotifyError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def load_credentials(path):
    """标准 JSON 对象或单元素数组；兼容现有 [{app_ip:密钥}] 文件。"""
    p = Path(path).expanduser()
    if not p.is_file():
        raise NotifyError("找不到机器人凭证文件，请检查 GLATA_BOT_CREDENTIALS")
    if stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise NotifyError("机器人凭证权限过宽，请先 chmod 600 凭证文件")
    raw = p.read_text(encoding="utf-8-sig").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.fullmatch(
            r"\[\s*\{\s*(?:app_ip|app_secret)\s*:\s*([A-Za-z0-9_-]{16,256})\s*\}\s*\]", raw)
        if not match:
            raise NotifyError("凭证格式无效：需要 app_id / app_secret JSON；未输出原文") from None
        data = {"app_secret": match.group(1)}
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise NotifyError("凭证格式无效：需要 JSON 对象")
    app_id = data.get("app_id") or os.environ.get("GLATA_BOT_APP_ID") or DEFAULT_APP_ID
    secret = data.get("app_secret") or data.get("app_ip")
    if not isinstance(app_id, str) or not re.fullmatch(r"cli_[A-Za-z0-9]+", app_id):
        raise NotifyError("机器人 App ID 无效")
    if not isinstance(secret, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", secret):
        raise NotifyError("机器人 App Secret 未配置或格式无效")
    return app_id, secret


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BotClient:
    def __init__(self, credentials, chat_id, target_name, target_open_id=None, timeout=15):
        self.app_id, self._secret = load_credentials(credentials)
        if not re.fullmatch(r"oc_[A-Za-z0-9]+", chat_id):
            raise NotifyError("提醒群 ID 无效")
        self.chat_id = chat_id
        self.target_name = target_name
        self.expected_open_id = target_open_id
        self.timeout = timeout
        self._token = None
        self._expires_at = 0
        self._target = None
        self._target_expires_at = 0
        self._auth_lock = threading.RLock()
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _safe(self, message):
        text = str(message).replace(self._secret, "[密钥已隐藏]")
        if self._token:
            text = text.replace(self._token, "[令牌已隐藏]")
        text = re.sub(r"(?:https?://|ou_|oc_|om_)[^\s,，\]）]+", "[资源信息已隐藏]", text)
        return text[:350]

    def _request(self, method, path, body=None, auth=True):
        if auth:
            self._ensure_token()
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(
            HOST + path, headers=headers, method=method,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"))
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                data = json.loads(exc.read())
            except Exception:
                raise NotifyError("机器人接口 HTTP %s" % exc.code,
                                  uncertain=method != "GET" and exc.code >= 500) from None
            raise NotifyError("机器人接口 code=%s：%s" %
                              (data.get("code", exc.code), self._safe(data.get("msg", "失败"))),
                              uncertain=method != "GET" and exc.code >= 500) from None
        except Exception as exc:
            raise NotifyError("机器人请求异常（%s）；未自动重试" % type(exc).__name__,
                              uncertain=method != "GET") from None
        if data.get("code") != 0:
            raise NotifyError("机器人接口 code=%s：%s" %
                              (data.get("code"), self._safe(data.get("msg", "失败"))))
        return data

    def _ensure_token(self):
        with self._auth_lock:
            if self._token and time.time() < self._expires_at:
                return
            result = self._request("POST", "/open-apis/auth/v3/tenant_access_token/internal",
                                   {"app_id": self.app_id, "app_secret": self._secret}, auth=False)
            token = result.get("tenant_access_token")
            if not token:
                raise NotifyError("鉴权响应未返回应用令牌")
            self._token = token
            self._expires_at = time.time() + max(0, int(result.get("expire", 7200)) - 120)

    def preflight(self):
        """从当前应用的群成员接口确认唯一目标，不复用其他应用的 open_id。"""
        with self._auth_lock:
            if self._target and time.time() < self._target_expires_at:
                return self._target
            members = []
            cursor = ""
            for _ in range(10):
                path = "/open-apis/im/v1/chats/%s/members?member_id_type=open_id&page_size=100" % self.chat_id
                if cursor:
                    import urllib.parse
                    path += "&page_token=" + urllib.parse.quote(cursor, safe="")
                data = self._request("GET", path).get("data") or {}
                members.extend(data.get("items") or [])
                if not data.get("has_more"):
                    break
                cursor = data.get("page_token")
                if not cursor:
                    raise NotifyError("群成员分页不完整，停止提醒以避免 @错人")
            else:
                raise NotifyError("群成员过多，无法完整确认目标，停止提醒")
            if self.expected_open_id:
                matched = [m for m in members if m.get("member_id") == self.expected_open_id
                           and m.get("name") == self.target_name]
            else:
                matched = [m for m in members if m.get("name") == self.target_name]
            if len(matched) != 1:
                raise NotifyError("提醒群里未找到唯一的本人；同名时配置 GLATA_NOTIFY_OPEN_ID（此应用下的 ID）")
            self._target = matched[0]["member_id"]
            self._target_expires_at = time.time() + 300
            return self._target

    def send(self, idempotency_key):
        target = self.preflight()
        text = '<at user_id="%s">%s</at> %s' % (target, self.target_name, MESSAGE_TEXT)
        response = self._request("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id", {
            "receive_id": self.chat_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False), "uuid": idempotency_key})
        message = response.get("data") or {}
        sender = message.get("sender") or {}
        if (not message.get("message_id") or message.get("chat_id") != self.chat_id
                or sender.get("id") != self.app_id or sender.get("sender_type") != "app"):
            raise NotifyError("消息返回的机器人身份或目标群不匹配，未电话加急", uncertain=True)
        if not any(m.get("id") == target for m in message.get("mentions") or []):
            raise NotifyError("消息返回的 @对象未通过校验，未电话加急", uncertain=True)
        return message["message_id"], target

    def urgent_phone(self, message_id, target):
        if not re.fullmatch(r"om_[A-Za-z0-9]+", message_id):
            raise NotifyError("待加急消息 ID 无效")
        result = self._request("PATCH", "/open-apis/im/v1/messages/%s/urgent_phone?user_id_type=open_id" % message_id,
                               {"user_id_list": [target]})
        if (result.get("data") or {}).get("invalid_user_id_list"):
            raise NotifyError("电话加急包含无效收件人，未成功")
        return result


class NotificationManager:
    """两个轻量工作线程；最多并发 2 个提醒，不阻塞扫描或 AI。"""
    def __init__(self, mode, client, state_path, core, logger, executor=None):
        self.mode, self.client, self.core, self.log = mode, client, core, logger
        self.path = Path(state_path)
        self._lock = threading.RLock()
        self._first_scan = True
        self._futures = {}
        self._executor = executor or concurrent.futures.ThreadPoolExecutor(max_workers=2,
                                                                         thread_name_prefix="glata-notify")
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.state.get("entries"), dict):
                    raise ValueError()
            except Exception:
                raise NotifyError("提醒状态文件损坏：停止启动以避免重复拨打；请先检查 .notify_state.json") from None
        else:
            self.state = {"cursor": 0, "entries": {}}
        for entry in self.state["entries"].values():
            if entry.get("status") in ("sending", "sent", "urgent_inflight"):
                entry["status"] = "uncertain"
            elif entry.get("status") in ("queued", "awaiting_classification"):
                entry["status"] = "skipped_restart"
        self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 只压缩已结束的旧记录；游标保留，防止旧卡片回放。
        entries = self.state["entries"]
        terminal = [k for k, v in entries.items() if v.get("status") not in
                    ("queued", "sending", "sent", "urgent_inflight", "awaiting_classification")]
        for key in terminal[:-1000]:
            entries.pop(key, None)
        fd, name = tempfile.mkstemp(prefix=".notify_state-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def observe(self, messages):
        with self._lock:
            newest = max((self.core.position(m) for m in messages), default=0)
            if self._first_scan:
                self.state["cursor"] = max(newest, self.state.get("cursor", 0))
                self._first_scan = False
                self._save()
                self.log("[notify] 工单增强提示：%s；已建立存量基线，不回放历史提醒" %
                         ("所有 @本人 工单（含告警）" if self.mode == "all" else "仅 @本人 人工工单"))
                return
            cursor = self.state.get("cursor", 0)
            dirty = newest > cursor
            for card in sorted(messages, key=self.core.position):
                mid = card.get("message_id")
                if not mid or self.core.position(card) <= cursor or mid in self.state["entries"]:
                    continue
                human = self.core.is_target_card(card)
                title = self.core.card_title(card.get("content", ""))
                alert = self.core.mentions_me(card) and bool(re.search(r"告警|报警|alert|incident|故障", title, re.I))
                # all 还接收标题同时含“新工单”与“告警”的卡片；原分析筛选保持不变。
                if not human and not (self.mode == "all" and alert):
                    continue
                info = self.core.parse_card(card.get("content", ""), card.get("mentions") or [])
                dirty = True
                self.state["entries"][mid] = {
                    "status": "queued" if self.mode == "all" else "awaiting_classification",
                    "ticket_no": info.get("ticket_no"), "created_at": time.time()}
                self.log("[notify] 检测到新%s，工单 %s，%s" %
                         ("告警卡片" if alert else "工单卡片", info.get("ticket_no") or "未带单号",
                          "等待异步提醒" if self.mode == "all" else "等待工单页确认人工类型"))
            self.state["cursor"] = max(cursor, newest)
            if dirty:
                self._save()  # 空转不写盘；仅新增消息或提醒状态变化时保存。

    def confirm_manual(self, card, info):
        if self.mode != "human":
            return
        with self._lock:
            entry = self.state["entries"].get(card.get("message_id"))
            if not entry or entry.get("status") != "awaiting_classification":
                return
            if info.get("alert_only"):
                entry["status"] = "filtered_alert"
                self.log("[notify] 工单 %s 为告警：人工模式不 @、不电话加急" % info.get("ticket_no"))
            elif info.get("ticket_metadata_loaded") and info.get("form_type"):
                entry["status"] = "queued"
            else:
                return  # 未能确认时不猜测，随现有工单页重试再确认。
            self._save()

    def poll(self):
        with self._lock:
            for key, future in list(self._futures.items()):
                if future.done():
                    self._futures.pop(key)
                    try:
                        future.result()
                    except Exception:
                        self.log("[notify_error] 提醒工作线程异常，未重复拨打")
            for mid, entry in self.state["entries"].items():
                if len(self._futures) >= 2:
                    break
                if entry.get("status") == "queued" and mid not in self._futures:
                    self._futures[mid] = self._executor.submit(self._deliver, mid)

    def _deliver(self, mid):
        with self._lock:
            entry = self.state["entries"][mid]
            entry["status"] = "sending"
            self._save()  # 必须成功落盘后才允许有外部副作用。
            ticket_no = entry.get("ticket_no") or "未带单号"
        try:
            key = "glata-at-" + hashlib.sha256((self.client.app_id + self.client.chat_id + mid).encode()).hexdigest()[:32]
            message_id, target = self.client.send(key)
            with self._lock:
                entry.update(status="sent", message_id=message_id)
                self._save()
                entry["status"] = "urgent_inflight"
                self._save()
            self.log("[notify] 工单 %s：机器人已 @本人，正在电话加急" % ticket_no)
            self.client.urgent_phone(message_id, target)
            with self._lock:
                entry.update(status="done", completed_at=time.time())
                self._save()
            self.log("[notify] 工单 %s：电话加急接口成功（未重复拨打）" % ticket_no)
        except Exception as exc:
            with self._lock:
                uncertain = (getattr(exc, "uncertain", False) or entry.get("status") == "urgent_inflight")
                entry["status"] = "uncertain" if uncertain else "failed"
                try:
                    self._save()
                except Exception:
                    pass
            detail = str(exc) if isinstance(exc, NotifyError) else type(exc).__name__
            self.log("[notify_error] 工单 %s：%s；%s，不自动重拨，AI 分析不受影响" %
                     (ticket_no, detail, "结果待确认" if uncertain else "提醒失败"))

    def close(self):
        self._executor.shutdown(wait=True)


def create_manager(mode, core, logger, preflight=True):
    credentials = os.environ.get("GLATA_BOT_CREDENTIALS") or str(BASE_DIR / "app_secret.json")
    client = BotClient(credentials, os.environ.get("GLATA_NOTIFY_CHAT_ID") or DEFAULT_CHAT_ID,
                       core.TARGET_USER_NAME, os.environ.get("GLATA_NOTIFY_OPEN_ID"))
    if preflight:
        client.preflight()  # 启动时只鉴权/读成员，不发消息、不加急。
    return NotificationManager(mode, client, BASE_DIR / "oncall_chats" / ".notify_state.json", core, logger)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="仅检查机器人配置，不发送消息或电话")
    parser.add_argument("--check", action="store_true", required=True)
    args = parser.parse_args()
    import read_oncall_chat as core
    try:
        client = BotClient(os.environ.get("GLATA_BOT_CREDENTIALS") or str(BASE_DIR / "app_secret.json"),
                           os.environ.get("GLATA_NOTIFY_CHAT_ID") or DEFAULT_CHAT_ID,
                           core.TARGET_USER_NAME, os.environ.get("GLATA_NOTIFY_OPEN_ID"))
        client.preflight()
        print("机器人鉴权、提醒群成员和本人身份校验通过（未发送消息、未拨打电话）")
    except NotifyError as exc:
        print("[错误] %s" % exc)
        raise SystemExit(4)
