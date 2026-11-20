#!/bin/bash
# 一键停止：工单监控（加 --all 连调试浏览器一起关）
# 用法: bash stop.sh        只停监控
#       bash stop.sh --all  连调试浏览器一起停
#
# 两个 macOS 特有的坑，这里都做了处理：
# 1. 自带 bash 3.2 在 $VAR 后面直接跟中文标点时，会把多字节字符当成变量名的一部分
#    （$PID， -> 变量 PID<乱码>），配合 set -u 直接报 unbound variable。变量一律写 ${VAR}。
# 2. pid 文件可能过期，而 pid 会被系统复用。不做校验就 kill 会误杀无关进程，
#    所以先确认这个 pid 的命令行确实是 watch_and_run.py。
set -u
cd "$(dirname "$0")"

PID_FILE=".watch.pid"
STOPPED=0

if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "${PID}" ] && ps -p "${PID}" >/dev/null 2>&1; then
        CMD="$(ps -p "${PID}" -o command= 2>/dev/null || true)"
        case "${CMD}" in
            *watch_and_run.py*) ;;
            *)
                echo "pid ${PID} 已被其他进程占用（${CMD:0:50}…），跳过，避免误杀"
                PID=""
                ;;
        esac
    else
        echo "进程 ${PID:-未知} 已不存在"
        PID=""
    fi

    if [ -n "${PID}" ]; then
        kill "${PID}" 2>/dev/null || true
        # 等它自己退（Python 可能在收尾），最多等 5 秒
        for _ in $(seq 1 10); do
            sleep 0.5
            ps -p "${PID}" >/dev/null 2>&1 || break
        done
        if ps -p "${PID}" >/dev/null 2>&1; then
            echo "常规停止未生效，强制结束"
            kill -9 "${PID}" 2>/dev/null || true
            sleep 0.5
        fi
        if ps -p "${PID}" >/dev/null 2>&1; then
            echo "[警告] 无法停止 pid ${PID}，转为按进程名兜底"
        else
            echo "已停止监控 (pid ${PID})"
            STOPPED=1
        fi
    fi
    rm -f "$PID_FILE"
fi

# 兜底：pid 文件缺失 / 内容过期时按进程名收尾，保证幂等
if pkill -f "watch_and_run.py" >/dev/null 2>&1; then
    echo "已停止监控（按进程名匹配）"
    STOPPED=1
fi

if [ "$STOPPED" = "0" ]; then
    echo "没有在运行的监控"
fi

if [ "${1:-}" = "--all" ]; then
    python3 cdp_client.py --stop-browser
fi
