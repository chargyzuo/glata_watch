"""集中读取非密钥部署配置：环境变量 > 本机 JSON > 安全空默认。

config.local.json 始终不入 Git。缺少配置时启动校验失败，而不是读取他人会话。
只缓存一次读取，避免每两秒扫描触发文件 IO；更改配置后正常重启监控。
"""
import argparse
import json
import os
import re
import shlex
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_KEYS = {
    "lark_cli_bin": "LARK_CLI_BIN",
    "chat_id": "GLATA_CHAT_ID",
    "target_user_id": "GLATA_TARGET_USER_ID",
    "target_user_ids": "GLATA_TARGET_USER_IDS",
    "target_user_name": "GLATA_TARGET_USER_NAME",
    "bot_app_id": "GLATA_BOT_APP_ID",
    "notify_chat_id": "GLATA_NOTIFY_CHAT_ID",
    "alert_reference_chat_id": "GLATA_ALERT_REFERENCE_CHAT_ID",
}


@lru_cache(maxsize=1)
def load_local():
    path = Path(os.environ.get("GLATA_CONFIG_FILE") or ROOT / "config.local.json").expanduser()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ValueError("本机配置无法读取或不是合法 JSON；未输出配置内容") from None
    if not isinstance(data, dict):
        raise ValueError("本机配置必须为 JSON 对象")
    return data


def get_setting(key, default=""):
    env = ENV_KEYS.get(key)
    if env and env in os.environ:
        return os.environ[env]
    return load_local().get(key, default)


def user_ids():
    value = get_setting("target_user_ids", [])
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError("target_user_ids 应为字符串数组或逗号分隔字符串")
    result = {x.strip() for x in value if x.strip()}
    primary = get_setting("target_user_id")
    if isinstance(primary, str) and primary:
        result.add(primary)
    return result


def check_config():
    """只检查字段格式，不联网、不输出姓名/真实标识或密钥。"""
    cli = get_setting("lark_cli_bin")
    if cli and (not isinstance(cli, str) or not os.path.isabs(os.path.expanduser(cli))):
        raise ValueError("lark_cli_bin 应为独立 CLI 的绝对路径，不接受命令片段")
    chat = get_setting("chat_id")
    name = get_setting("target_user_name")
    ids = user_ids()
    if not isinstance(chat, str) or not re.fullmatch(r"oc_[A-Za-z0-9]+", chat) or "替换" in chat:
        raise ValueError("请在 config.local.json 填写 chat_id（Glata 私聊 ID）")
    if not isinstance(name, str) or not name.strip() or name.startswith("YOUR_"):
        raise ValueError("请在 config.local.json 填写 target_user_name")
    primary = get_setting("target_user_id")
    if (not isinstance(primary, str) or not re.fullmatch(r"ou_[A-Za-z0-9]+", primary)
            or not ids or any(not re.fullmatch(r"ou_[A-Za-z0-9]+", x) for x in ids)):
        raise ValueError("请在 config.local.json 填写本人 target_user_id / target_user_ids")
    for key, prefix in (("bot_app_id", "cli"), ("notify_chat_id", "oc"),
                        ("alert_reference_chat_id", "oc")):
        value = get_setting(key)
        if value and (not isinstance(value, str) or not re.fullmatch(prefix + r"_[A-Za-z0-9]+", value)):
            raise ValueError("可选配置 %s 格式无效，请填写真实值或留空" % key)


def check_guidance(valid):
    """离线检查只输出下一步，不将格式正确误报为授权或访问成功。"""
    if not valid:
        print("解决办法：先用编辑器修正 config.local.json 中上述字段；已有文件不要覆盖。")
        print("若设置 GLATA_CONFIG_FILE，请编辑该文件；环境变量覆盖本机 JSON，需同时核对。")
        print("修正后重新运行：python3 project_config.py --check")
        print("本人 ID 不确定：先完成独立 CLI 用户授权，再运行 python3 deploy_check.py 获取配置建议。")
        return
    print("格式通过不代表用户已授权、ID 属于当前应用或 Glata 会话可读。")
    print("下一步：python3 deploy_check.py --preflight")
    print("  预检通过：bash start.sh（需要电话提醒时用 -at 或 -at all）")
    print("  提示用户授权不可用：按下面命令检查并完成本人 OAuth，不能重登 Chrome 代替。")
    cli = shlex.quote(os.path.expanduser(get_setting("lark_cli_bin") or "~/bin/lark-cli"))
    print("    %s auth status --json --verify" % cli)
    print('    %s auth login --scope "offline_access im:chat:read im:message:readonly im:message.p2p_msg:get_as_user im:message.group_msg:get_as_user contact:user.base:readonly contact:user:search"' % cli)
    print("  提示本人不一致：用同一份 CLI 查询本人，再填写其 open_id/姓名：")
    print("    %s contact +search-user --user-ids me --as user --format json" % cli)
    print("    python3 deploy_check.py")
    print("  CLI 没有 auth 命令：路径选中了内置裁剪版，改 lark_cli_bin 为已安装独立版的绝对路径。")
    print("  Glata 不可读：核对本人实际会话 ID 与授权读取权限；不要换机器人身份或删除去重状态。")
    print("处理后再次运行 --preflight；通过才启动。以上命令仅供下一步执行，本次没有联网。")


def main():
    parser = argparse.ArgumentParser(description="本机配置校验；不读取群消息或调用机器人")
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--quiet", action="store_true", help="供一键入口使用，只打印格式检查结果")
    args = parser.parse_args()
    try:
        check_config()
    except ValueError as exc:
        print("[错误] %s" % exc)
        if not args.quiet:
            check_guidance(False)
        return 4
    print("本机配置格式校验通过（未联网、未输出个人信息）")
    if not args.quiet:
        check_guidance(True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
