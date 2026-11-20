"""集中读取非密钥部署配置：环境变量 > 本机 JSON > 安全空默认。

config.local.json 始终不入 Git。缺少配置时启动校验失败，而不是读取他人会话。
只缓存一次读取，避免每两秒扫描触发文件 IO；更改配置后正常重启监控。
"""
import argparse
import json
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_KEYS = {
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


def main():
    parser = argparse.ArgumentParser(description="本机配置校验；不读取群消息或调用机器人")
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args()
    try:
        check_config()
    except ValueError as exc:
        print("[错误] %s" % exc)
        return 4
    print("本机配置格式校验通过（未联网、未输出个人信息）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
