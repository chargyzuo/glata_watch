#!/bin/bash
# 一键启动：调试浏览器（CDP）+ 工单监控
# 用法: bash start.sh | bash start.sh -at | bash start.sh -at all
set -u
cd "$(dirname "$0")"

AT_MODE="off"
WATCH_ARGS=(--notify-mode off)
case "$#" in
    0) ;;
    1)
        case "$1" in
            -at) AT_MODE="human"; WATCH_ARGS=(--notify-mode human) ;;
            -h|--help)
                printf '%s\n' '用法：bash start.sh          原有人工工单自动分析，不提醒' \
                    '      bash start.sh -at      @本人的人工工单：机器人 @ + 电话加急' \
                    '      bash start.sh -at all  @本人的所有工单（含告警）：机器人 @ + 电话加急' \
                    '浏览器默认后台无窗口；首次/重新登录：GLATA_BROWSER_MODE=headed bash start.sh'
                exit 0 ;;
            *) printf '[错误] 不支持的参数：%s；用 bash start.sh --help 查看用法\n' "$1"; exit 2 ;;
        esac ;;
    2)
        if [ "$1" = "-at" ] && [ "$2" = "all" ]; then
            AT_MODE="all"; WATCH_ARGS=(--notify-mode all)
        else
            printf '%s\n' '[错误] 仅支持 -at 或 -at all，参数未生效'; exit 2
        fi ;;
    *) printf '%s\n' '[错误] 参数过多，仅支持 -at 或 -at all'; exit 2 ;;
esac

# 提醒模式先检查本机配置；错误时不拉浏览器、不起监控。
if [ "${AT_MODE}" != "off" ]; then
    python3 ticket_notify.py --check || exit 4
fi

BROWSER_MODE="${GLATA_BROWSER_MODE:-headless}"
case "${BROWSER_MODE}" in
    headless|headed) ;;
    *) printf '%s\n' '[错误] GLATA_BROWSER_MODE 仅支持 headless 或 headed'; exit 2 ;;
esac
PID_FILE=".watch.pid"
mkdir -p logs

# 0. 配置只存在本机忽略文件；统一校验避免缺配置静默空转。
python3 project_config.py --check || exit 4

# 1. 已经在跑就不重复启
if [ -f "$PID_FILE" ] && ps -p "$(cat "$PID_FILE")" >/dev/null 2>&1; then
    CMD="$(ps -p "$(cat "$PID_FILE")" -o command= 2>/dev/null || true)"
    RUNNING_MODE="off"
    case "${CMD}" in
        *--notify-mode\ all*|*-at\ all*) RUNNING_MODE="all" ;;
        *--notify-mode\ human*|*-at*) RUNNING_MODE="human" ;;
    esac
    if [ "${RUNNING_MODE}" != "${AT_MODE}" ]; then
        printf '[错误] 监控已在运行，当前提醒模式=%s，本次要求=%s；参数未生效。先 bash stop.sh，再按新参数启动。\n' "${RUNNING_MODE}" "${AT_MODE}"
        exit 2
    fi
    echo "监控已在运行 (pid $(cat "$PID_FILE"))，提醒模式=${RUNNING_MODE}，无需重复启动。"
    exit 0
fi

# 2. 专用浏览器的启动/复用逻辑与读工单阶段统一，默认 headless 不弹窗。
# 只检测在线状态，不因缺少 Glata tab 就在每次启动时打开网页。
python3 cdp_client.py --ensure-browser || exit 4

# 3. 监控主流程
nohup python3 watch_and_run.py --interval "${GLATA_INTERVAL:-2}" "${WATCH_ARGS[@]}" >> "logs/console-$(date +%F).log" 2>&1 &
echo $! > "$PID_FILE"
sleep 1
if ps -p "$(cat "$PID_FILE")" >/dev/null 2>&1; then
    echo "监控已启动 (pid $(cat "$PID_FILE"))"
    echo "  日志: logs/watch-$(date +%F).log"
    echo "  工单增强提示: ${AT_MODE}（off=关闭，human=仅人工，all=含告警）"
    echo "  停止: bash stop.sh"
    echo ""
    if [ "${GLATA_BG:-0}" = "1" ]; then
        echo "  （GLATA_BG=1 纯后台模式，不进入控制台）"
        exit 0
    fi
    # 4. 前台实时控制台：只打关键事件，Ctrl+C 只退出控制台，监控继续
    exec python3 console.py
else
    echo "[错误] 监控启动失败，查看 logs/console-$(date +%F).log"
    rm -f "$PID_FILE"
    exit 1
fi
