#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署自检：检查依赖是否齐全，并探测出你本人需要填的三项配置。

用法:
    python3 deploy_check.py

输出末尾会打印建议，写入本机 config.local.json；不再修改受版本控制的源码。
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import fcntl
import tempfile
from urllib.parse import urlsplit

NEW_TICKET_KEYWORD = "你有一条新工单需要处理"
ROOT = os.path.dirname(os.path.abspath(__file__))

results = []


def ok(name, detail=""):
    results.append(("OK", name, detail))
    print("  [OK]   %-22s %s" % (name, detail))


def miss(name, detail=""):
    results.append(("MISS", name, detail))
    print("  [MISS] %-22s %s" % (name, detail))


def warn(name, detail=""):
    results.append(("WARN", name, detail))
    print("  [WARN] %-22s %s" % (name, detail))


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def find_lark_clis():
    """与主监控共用固定路径解析，避免自检通过的是另一个 CLI。"""
    import read_oncall_chat as core
    return core.find_lark_clis()


def lark_json(cli, args, timeout=60):
    import read_oncall_chat as core
    return core.LarkRunner()._run(cli, args + ["--format", "json"], timeout=timeout)


def preflight():
    """只读验证后台真实环境；不归档消息、不修改游标、不触发任何任务。"""
    import project_config
    import read_oncall_chat as core
    project_config.check_config()
    runner = core.LarkRunner()
    data = runner.call(["contact", "+search-user", "--user-ids", "me", "--format", "json"])
    users = (data.get("data") or {}).get("users") or []
    if len(users) != 1:
        raise RuntimeError("本人查询未返回唯一用户；未启动监控")
    me = users[0]
    if (me.get("open_id") != project_config.get_setting("target_user_id") or
            (me.get("localized_name") or "").strip() != project_config.get_setting("target_user_name").strip()):
        raise RuntimeError("CLI 登录用户与本机本人配置不一致；先运行 deploy_check.py 获取同应用本人配置")
    data = runner.call(["im", "+chat-messages-list", "--chat-id", project_config.get_setting("chat_id"),
                        "--page-size", "1", "--no-reactions", "--format", "json"])
    if not isinstance((data.get("data") or {}).get("messages"), list):
        raise RuntimeError("Glata 读取响应缺少 messages；未启动监控")
    print("[OK] 后台用户授权、本人与 Glata 可读性检查通过（不显示个人值，不触发 AI/电话）")
    return 0


AUTH_SCOPE = ("offline_access im:chat:read im:message:readonly "
              "im:message.p2p_msg:get_as_user im:message.group_msg:get_as_user "
              "contact:user.base:readonly contact:user:search")


def auth_json(cli, args, timeout=60):
    """授权输出仅在内存解析，绝不把原始响应写日志或回显。"""
    import read_oncall_chat as core
    try:
        result = subprocess.run([cli] + args, capture_output=True, text=True,
                                timeout=timeout, env=core.lark_env())
    except subprocess.TimeoutExpired:
        raise RuntimeError("授权请求超时；未自动重试，请重新运行 bash start.sh") from None
    try:
        data = json.loads(result.stdout or result.stderr)
    except ValueError:
        raise RuntimeError("授权命令未返回有效 JSON；请核对独立 CLI 版本和网络") from None
    if result.returncode or data.get("ok") is False:
        raise core.lark_error(result.stderr or result.stdout, result.returncode)
    return data


def recover_user_auth(error):
    """只由显式本机启动入口调用；独立 --preflight 仍然只读。"""
    import read_oncall_chat as core
    print("[原因] %s" % error.reason, flush=True)
    if not sys.stdin.isatty():
        print("[未授权] 当前无交互终端；请在本机终端运行 bash start.sh 扫码，未启动监控。")
        return 4
    paths = core.find_lark_clis()
    if len(paths) != 1:
        print("[错误] 先在 config.local.json 固定独立 lark_cli_bin；不对临时候选发起授权。")
        return 4
    cli = paths[0]
    logdir = os.path.join(ROOT, "logs")
    os.makedirs(logdir, exist_ok=True)
    lockpath = os.path.join(logdir, ".lark-user-auth.local.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(lockpath, flags, 0o600), "a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[未授权] 另一个终端正在授权，请在原终端完成；未生成重复链接。")
            return 4
        # 拿锁期间另一启动可能已完成授权；只读再查，不生成多余二维码。
        try:
            return preflight()
        except core.LarkAuthError:
            pass
        data = auth_json(cli, ["auth", "login", "--scope", AUTH_SCOPE, "--no-wait", "--json"])
        url, device = data.get("verification_url"), data.get("device_code")
        if not isinstance(url, str) or not isinstance(device, str) or not device:
            raise RuntimeError("授权响应缺少链接/设备码；未保存或回显原始响应")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ("accounts.feishu.cn", "accounts.larksuite.com")
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or any(ord(c) < 32 or ord(c) == 127 for c in url)):
            raise RuntimeError("授权链接不是支持的飞书 HTTPS 地址；未打开或显示")
        print("\n授权链接（本次有效，过期请重新运行 bash start.sh）：\n%s" % url, flush=True)
        fd, image = tempfile.mkstemp(prefix="lark-user-auth-", suffix=".local.png", dir=logdir)
        os.close(fd)
        try:
            relative = os.path.relpath(image, ROOT)
            qr = subprocess.run([cli, "auth", "qrcode", url, "--output", relative, "--size", "512"],
                                cwd=ROOT, env=core.lark_env(), capture_output=True, text=True, timeout=30)
            if qr.returncode or os.path.getsize(image) == 0:
                print("[提示] 二维码生成失败；请打开上面的授权链接，未降低权限或切换身份。", flush=True)
            else:
                os.chmod(image, 0o600)
                print("二维码：%s" % image, flush=True)
                opened = subprocess.run(["open", image], capture_output=True, timeout=10)
                if opened.returncode:
                    print("[提示] 无法自动显示二维码，请手动打开上述图片或授权链接。", flush=True)
            print("请本人扫码确认，并允许 CLI 的钥匙串访问；正在等待授权，Ctrl+C 可取消。", flush=True)
            auth_json(cli, ["auth", "login", "--device-code", device, "--json"], timeout=660)
            # 只在持久化令牌与同应用身份/会话真实可读后允许启动。
            preflight()
            print("[OK] 本机本人授权已保存，继续启动。", flush=True)
            return 0
        finally:
            if os.path.isfile(image):
                os.unlink(image)  # 只删除本次临时二维码，不动教程截图/凭证/业务状态。


def main():
    parser = argparse.ArgumentParser(description="部署自检；默认只读联网探测，--offline 只检查本机依赖和配置")
    parser.add_argument("--offline", action="store_true", help="不读取飞书消息/成员，不触发 AI 或电话")
    parser.add_argument("--preflight", action="store_true", help="启动前仅检查本人授权与 Glata 可读性；不显示真实值")
    parser.add_argument("--auth-on-failure", action="store_true", help="仅配合 --preflight：交互终端授权失败时显示原因/链接/二维码并等待本人扫码")
    args = parser.parse_args()
    if args.auth_on_failure and (not args.preflight or args.offline):
        parser.error("--auth-on-failure 仅可与 --preflight 配合，不支持 --offline")
    if args.preflight:
        import read_oncall_chat as core
        try:
            return preflight()
        except core.LarkAuthError as exc:
            if args.auth_on_failure:
                try:
                    return recover_user_auth(exc)
                except KeyboardInterrupt:
                    print("\n[未授权] 已取消；未启动监控，未清理业务状态。")
                    return 4
                except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as failure:
                    print("[错误] 本机授权未完成：%s；未启动监控。" % failure)
                    return 4
            print("[错误] 后台预检失败：%s" % exc)
            return 4
        except (RuntimeError, ValueError, OSError) as exc:
            print("[错误] 后台预检失败：%s" % exc)
            return 4
    results.clear()
    print("=" * 64)
    print("Glata 工单自动分析 · 部署自检")
    print("=" * 64)
    print("\n[1] 运行环境")

    pyver = sys.version.split()[0]
    if sys.version_info >= (3, 9):
        ok("python3", pyver + "  " + sys.executable)
    else:
        miss("python3", pyver + " 需要 >= 3.9")

    try:
        import websocket
        ok("websocket-client", getattr(websocket, "__version__", "?"))
    except Exception:
        miss("websocket-client", "pip install websocket-client")

    print("\n[2] 外部命令")

    doubao = shutil.which("doubao") or shutil.which("doubao", path=os.environ.get("PATH", ""))
    if doubao:
        ok("doubao cli", doubao)
    elif os.environ.get("DOUBAO_CLI_BIN"):
        ok("doubao cli", "DOUBAO_CLI_BIN=" + os.environ["DOUBAO_CLI_BIN"])
    else:
        miss("doubao cli", "不在 PATH，需安装豆包客户端，或设 DOUBAO_CLI_BIN")

    import cdp_client
    chrome = cdp_client.CHROME_BIN
    if os.path.exists(chrome):
        ok("Google Chrome", chrome)
    else:
        miss("Google Chrome", "未找到，CDP 读工单页需要它")

    swiftc = shutil.which("swiftc")
    ok("swiftc", swiftc) if swiftc else miss("swiftc", "需安装 Xcode Command Line Tools")

    vision = os.path.join(ROOT, "bin", "vision_ocr")
    if os.path.exists(vision) and os.access(vision, os.X_OK):
        ok("bin/vision_ocr", "已编译")
    else:
        warn("bin/vision_ocr", "未编译，执行 bash build_vision_ocr.sh")

    tess = shutil.which("tesseract")
    if tess:
        ok("tesseract（可选）", tess)
    else:
        warn("tesseract（可选）", "未安装，OCR 只剩 Vision 单引擎")

    if args.offline:
        import project_config
        try:
            project_config.check_config()
            ok("本机配置", "格式通过；未输出个人值")
        except ValueError as exc:
            miss("本机配置", str(exc))
        clis = find_lark_clis()
        if clis:
            ok("lark-cli", "已找到可执行候选；未测试授权")
        else:
            miss("lark-cli", "未找到可执行候选")
        print("离线检查结束：未读取消息、未调 AI、未发消息/电话；授权与真实链路未验证。")
        return 1 if any(r[0] == "MISS" for r in results) else 0

    print("\n[3] 飞书访问（lark-cli）")

    clis = find_lark_clis()
    if not clis:
        miss("lark-cli", "未找到独立 CLI；安装后配置本机 lark_cli_bin")
        print("\n无法继续探测配置：lark-cli 不可用。")
        return 2
    ok("lark-cli", clis[0])

    cli = clis[0]
    me = None
    for c in clis:
        try:
            d = lark_json(c, ["contact", "+search-user", "--user-ids", "me", "--as", "user"])
            users = (d.get("data") or {}).get("users") or []
            if users:
                me, cli = users[0], c
                break
        except Exception:
            continue
    if not me:
        miss("本人身份", "取 open_id 失败，可能登录态失效")
    else:
        ok("本人身份", "%s (%s)" % (me.get("localized_name"), me.get("open_id")))

    print("\n[4] 定位 Glata 通知会话")

    glata_chat = None
    if me:
        token, pages = None, 0
        while pages < 6:
            pages += 1
            try:
                args = ["im", "+chat-list", "--types", "p2p", "--page-size", "100"]
                if token:
                    args += ["--page-token", token]
                d = lark_json(cli, args)
            except Exception as e:
                warn("chat-list", "第 %d 页失败: %s" % (pages, str(e)[:80]))
                break
            data = d.get("data") or {}
            for c in data.get("chats") or []:
                if (c.get("name") or "").strip().lower() == "glata":
                    glata_chat = c
                    break
            if glata_chat or not data.get("has_more"):
                break
            token = data.get("page_token")
            if not token:
                break
    if glata_chat:
        ok("Glata 会话", glata_chat.get("chat_id"))
    else:
        miss("Glata 会话", "p2p 里没找到名为 Glata 的会话，确认你收到过 Glata 通知")

    print("\n[5] 卡片格式校验")

    title, matched = None, False
    if glata_chat and me:
        mid = me.get("open_id")
        try:
            d = lark_json(cli, ["im", "+chat-messages-list",
                                "--chat-id", glata_chat.get("chat_id"),
                                "--page-size", "50", "--no-reactions"])
            msgs = (d.get("data") or {}).get("messages") or []
            for m in msgs:
                ids = [x.get("id") for x in (m.get("mentions") or [])]
                if mid not in ids:
                    continue
                raw = m.get("content") or ""
                import re
                mt = re.match(r'<card\s+title="([^"]*)"', raw)
                title = mt.group(1) if mt else ""
                if NEW_TICKET_KEYWORD in title:
                    matched = True
                    break
        except Exception as e:
            warn("卡片读取", str(e)[:100])

    if matched:
        ok("卡片关键词", "命中「%s」" % title)
    elif title:
        warn("卡片关键词", "最近 @你 的卡片标题是「%s」，不含「%s」，需核对" % (title, NEW_TICKET_KEYWORD))
    else:
        warn("卡片关键词", "最近 50 条没有 @你 的卡片，可能你不是当前值班人")

    print("\n[6] CDP 调试浏览器")

    try:
        import urllib.request
        with urllib.request.urlopen(cdp_client.BASE + "/json/version", timeout=3) as r:
            info = json.loads(r.read().decode("utf-8"))
        ok("CDP 9222", (info.get("Browser") or "")[:60])
    except Exception:
        warn("CDP 9222", "未运行，读工单页前需启动调试 Chrome 并扫码登录")

    print("\n" + "=" * 64)
    print("配置建议（填进本机 config.local.json，不提交 Git）")
    print("=" * 64)
    if me:
        print("  target_user_id   : %s" % json.dumps(me.get("open_id")))
        print("  target_user_name : %s" % json.dumps(me.get("localized_name"), ensure_ascii=False))
    if glata_chat:
        print("  chat_id          : %s" % json.dumps(glata_chat.get("chat_id")))
    if not (me and glata_chat):
        print("  探测不完整，先补齐 [3] [4] 两项")

    bads = [r for r in results if r[0] == "MISS"]
    print("\n" + "-" * 64)
    print("小结：%d 项通过 / %d 项缺失 / %d 项提示"
          % (len([r for r in results if r[0] == "OK"]), len(bads),
             len([r for r in results if r[0] == "WARN"])))
    if bads:
        print("缺失项：" + "、".join(r[1] for r in bads))
        return 1
    print("环境已就绪，可以进入冒烟测试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
