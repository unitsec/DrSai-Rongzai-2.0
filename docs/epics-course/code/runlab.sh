#!/usr/bin/env bash
# =============================================================================
# runlab.sh —— 一键管理"虚拟实验站"的四个进程（第 3、6、10 章）
#
#   bash runlab.sh start     启动全部（IOC + 恒温箱 + 探测器 + PID 控制器）
#   bash runlab.sh start sim 启动全部 + 开发板仿真桥（第 7 章）
#   bash runlab.sh status    看谁在跑
#   bash runlab.sh stop      全部停掉（会自动把加热写回 0）
#   bash runlab.sh logs      跟踪日志
#
# 为什么要有这个脚本？
#   真实实验站上，你要启动/停止十几个进程，还必须保证顺序（IOC 先起）。
#   把它写成脚本是"可复现"的第一步：实验记录里写 "bash runlab.sh start"，
#   三个月后你还能一模一样地还原当时的环境。
# =============================================================================
set -uo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RUN_DIR="$HERE/.run"
LOG_DIR="$HERE/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

# --- 解释器：优先用当前 python，其次用环境变量指定的 ---
PY="${PYTHON:-python3}"
PREFIX="${EPICS_PREFIX:-LAB:}"

# --- 找 EPICS 工具（conda 环境里直接就有）---
SOFTIOC="${SOFTIOC:-}"
if [[ -z "$SOFTIOC" ]]; then
    if command -v softIocPVA >/dev/null 2>&1; then SOFTIOC="softIocPVA"
    elif command -v softIoc >/dev/null 2>&1; then SOFTIOC="softIoc"
    else
        echo "❌ 找不到 softIoc。先装 EPICS（第 3 章），或设置 SOFTIOC=/路径/softIocPVA"
        exit 1
    fi
fi

start_one() {  # name, workdir, command...
    local name=$1; shift
    if [[ -f "$RUN_DIR/$name.pid" ]] && kill -0 "$(cat "$RUN_DIR/$name.pid")" 2>/dev/null; then
        echo "  · $name 已在运行（pid $(cat "$RUN_DIR/$name.pid")）"
        return 0
    fi
    ( cd "$1" && shift && exec "$@" ) >"$LOG_DIR/$name.log" 2>&1 &
    echo $! > "$RUN_DIR/$name.pid"
    echo "  ✓ $name 启动（pid $!，日志 logs/$name.log）"
}

stop_one() {
    local name=$1
    if [[ -f "$RUN_DIR/$name.pid" ]]; then
        local pid; pid=$(cat "$RUN_DIR/$name.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            echo "  ✗ $name 已停止（pid $pid）"
        fi
        rm -f "$RUN_DIR/$name.pid"
    fi
}

case "${1:-start}" in
start)
    echo "启动虚拟实验站（前缀 $PREFIX，IOC=$SOFTIOC）"
    # 1) IOC 必须最先起（其它进程都要连它的 PV）
    ( cd "$HERE/softioc" && setsid bash -c "sleep 86400 | exec $SOFTIOC -S st.cmd" \
        >"$LOG_DIR/ioc.log" 2>&1 & echo $! > "$RUN_DIR/ioc.pid" )
    echo "  ✓ ioc 启动（pid $(cat "$RUN_DIR/ioc.pid")，日志 logs/ioc.log）"
    sleep 3
    # 2) 常驻 PID 控制器（它写 Power）
    start_one pid       "$HERE" "$PY" -u c06_pid_service.py --log "$LOG_DIR/pid_service.csv"
    # 3) 虚拟设备
    start_one plant     "$HERE" "$PY" -u c06_thermal_plant.py --prefix "$PREFIX"
    start_one detector  "$HERE" "$PY" -u c06_detector_sim.py  --prefix "$PREFIX"
    # 4) 可选的开发板桥（没有真硬件时用 --sim）
    if [[ "${2:-}" == "sim" ]]; then
        start_one bridge "$HERE" "$PY" -u c07_pv_bridge.py --sim --prefix "$PREFIX"
    fi
    echo
    echo "试试：caget ${PREFIX}TempRaw ${PREFIX}Power ${PREFIX}Counts"
    ;;
stop)
    echo "停止中（先把加热写回 0，再停进程）……"
    if command -v caput >/dev/null 2>&1; then
        caput "${PREFIX}Power" 0    >/dev/null 2>&1
        caput "${PREFIX}SetPointTemp" 25 >/dev/null 2>&1
        caput "${PREFIX}RunPermit" 0 >/dev/null 2>&1
    fi
    stop_one bridge; stop_one detector; stop_one plant; stop_one pid
    # IOC 最后停
    if [[ -f "$RUN_DIR/ioc.pid" ]]; then
        pkill -P "$(cat "$RUN_DIR/ioc.pid")" 2>/dev/null
        stop_one ioc
    fi
    pkill -x softIoc 2>/dev/null; pkill -x softIocPVA 2>/dev/null
    rm -f "$RUN_DIR/ioc.pid"
    ;;
status)
    echo "服务状态："
    for name in ioc pid plant detector bridge; do
        if [[ -f "$RUN_DIR/$name.pid" ]] && kill -0 "$(cat "$RUN_DIR/$name.pid")" 2>/dev/null; then
            echo "  ✓ $name  (pid $(cat "$RUN_DIR/$name.pid"))"
        else
            echo "  · $name  未运行"
        fi
    done
    if command -v caget >/dev/null 2>&1; then
        echo "PV 抽查："
        caget "${PREFIX}TempRaw" "${PREFIX}Power" "${PREFIX}Counts" "${PREFIX}HeaterEnable" 2>&1 | sed 's/^/  /'
    fi
    ;;
logs)
    tail -f "$LOG_DIR"/*.log
    ;;
*)
    sed -n '2,16p' "$0"
    exit 1
    ;;
esac
