#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
STATE_DIR="$ROOT/tmp/tools"
LOG_DIR="$STATE_DIR/logs"
NEUTRON_REFLECTION_PYTHON=${NEUTRON_REFLECTION_PYTHON:-/dg_workfs/domassy/rongzai/miniconda3/envs/rongzai-agent/bin/python}
NAMES=(zrietveld-refine gsas2-refine sasview-refine pdf-refine Irena-refine neutron-reflection)
ENVS=(gsas2_py311 gsas2_py311 sasmodels diffpy.pdffit2_env sasmodels rongzai-agent)
SCRIPTS=(
    tools/pyZRietveld/0zrietveld_refine.py
    tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py
    tools/sasview/0sasview_refine.py
    tools/pdf_refinement/0pdf_refine.py
    tools/Irena/0Irena_fit.py
    tools/neutron_reflection/reflection_mcp.py
)

usage() {
    cat <<'EOF'
Usage: bash start_tools.sh [start|restart|stop|status|logs] [all|TOOL ...]

Default: start all
Tools: zrietveld-refine gsas2-refine sasview-refine pdf-refine Irena-refine neutron-reflection
start   Start missing tools; leave existing processes untouched.
restart Stop existing tools (including manually started ones), then start them.
stop    Stop selected tools and their current child processes.
status  Show running PIDs and log locations.
logs    Follow selected log files (Ctrl-C only stops the viewer).

Logs: tmp/tools/logs/<tool>.log (appended across restarts)
CONDA_EXE may specify an absolute conda executable path.
NEUTRON_REFLECTION_PYTHON may override the reflection Python interpreter.
Stop/restart may interrupt active refinement tasks.
After 15 seconds, remaining verified processes receive KILL; wait up to 5 more seconds.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

action=${1:-start}
if (($#)); then shift; fi
case "$action" in
    -h|--help|help) usage; exit 0 ;;
    start|restart|stop|status|logs) ;;
    *) usage >&2; fail "Unknown action: $action" ;;
esac

selected=()
if (($# == 0)) || [[ "$*" == all ]]; then
    selected=("${!NAMES[@]}")
else
    for name in "$@"; do
        found=false
        for index in "${!NAMES[@]}"; do
            if [[ "$name" == "${NAMES[$index]}" ]]; then
                found=true
                if [[ " ${selected[*]} " != *" $index "* ]]; then selected+=("$index"); fi
                break
            fi
        done
        "$found" || fail "Unknown tool: $name"
    done
fi

mkdir -p -- "$LOG_DIR"
if [[ "$action" != logs ]]; then
    command -v flock >/dev/null || fail "flock is required."
    exec 9>"$STATE_DIR/manager.lock"
    flock -n 9 || fail "Another start_tools.sh command is running."
fi
cd -- "$ROOT"

# The start time prevents a stale PID file from targeting a reused PID.
process_stamp() {
    local stat rest
    [[ -r "/proc/$1/stat" ]] || return 1
    { IFS= read -r stat < "/proc/$1/stat"; } 2>/dev/null || return 1
    rest=${stat##*) }
    local -a fields
    read -r -a fields <<< "$rest"
    [[ "${fields[0]}" != Z ]] || return 1
    printf '%s\n' "${fields[19]}"
}

same_process() {
    local stamp
    stamp=$(process_stamp "$1") || return 1
    [[ "$stamp" == "$2" ]]
}

find_processes() {
    local index=$1 file pid cwd argument resolved stamp saved_pid saved_stamp
    PIDS=()
    STAMPS=()
    file="$STATE_DIR/${NAMES[$index]}.pid"
    if [[ -f "$file" ]]; then
        read -r saved_pid saved_stamp < "$file" || fail "Invalid PID file: $file"
        [[ "$saved_pid" =~ ^[0-9]+$ && "$saved_stamp" =~ ^[0-9]+$ ]] ||
            fail "Invalid PID file: $file"
        if same_process "$saved_pid" "$saved_stamp"; then
            PIDS+=("$saved_pid")
            STAMPS+=("$saved_stamp")
        fi
    fi
    local -a args
    for file in /proc/[0-9]*/cmdline; do
        pid=${file#/proc/}
        pid=${pid%/cmdline}
        [[ -O "/proc/$pid" && "$pid" != "$$" ]] || continue
        args=()
        { mapfile -d '' -t args < "$file"; } 2>/dev/null || continue
        ((${#args[@]} >= 2)) || continue
        # Only an interpreter, not grep/editors or the conda command, can be adopted.
        case "${args[0]##*/}" in python|python[0-9]*|bash) ;; *) continue ;; esac
        cwd=$(readlink "/proc/$pid/cwd") || continue
        for argument in "${args[@]:1}"; do
            [[ "$argument" == -* ]] && continue
            if [[ "$argument" == /* ]]; then
                resolved=$(readlink -m -- "$argument")
            else
                resolved=$(readlink -m -- "$cwd/$argument")
            fi
            if [[ "$resolved" == "$ROOT/${SCRIPTS[$index]}" ]]; then
                if [[ " ${PIDS[*]} " != *" $pid "* ]]; then
                    stamp=$(process_stamp "$pid") || break
                    PIDS+=("$pid")
                    STAMPS+=("$stamp")
                fi
                break
            fi
        done
    done
}

stop_tool() {
    local index=$1 pid child parent stamp deadline alive position forced=false
    find_processes "$index"
    if ((${#PIDS[@]} == 0)); then
        printf '%s: already stopped\n' "${NAMES[$index]}"
        rm -f -- "$STATE_DIR/${NAMES[$index]}.pid"
        return
    fi
    # Snapshot descendants before stopping their parents; never kill by process name.
    local tree
    tree=$(ps -eo pid=,ppid=)
    for ((position=0; position<${#PIDS[@]}; position++)); do
        parent=${PIDS[$position]}
        while read -r child pid; do
            if [[ "$pid" == "$parent" && " ${PIDS[*]} " != *" $child "* ]]; then
                stamp=$(process_stamp "$child") || continue
                PIDS+=("$child")
                STAMPS+=("$stamp")
            fi
        done <<< "$tree"
    done
    for position in "${!PIDS[@]}"; do
        pid=${PIDS[$position]}
        if same_process "$pid" "${STAMPS[$position]}"; then
            if ! kill -TERM "$pid" 2>/dev/null; then
                same_process "$pid" "${STAMPS[$position]}" &&
                    fail "Cannot stop PID $pid for ${NAMES[$index]}"
            fi
        fi
    done
    deadline=$((SECONDS + 15))
    while :; do
        alive=false
        for position in "${!PIDS[@]}"; do
            if same_process "${PIDS[$position]}" "${STAMPS[$position]}"; then alive=true; fi
        done
        "$alive" || break
        if ((SECONDS >= deadline)); then
            "$forced" &&
                fail "${NAMES[$index]} did not exit after KILL; no replacement was started."
            printf 'WARNING: %s did not stop within 15 seconds; sending KILL to remaining verified PIDs. Active requests may be interrupted.\n' \
                "${NAMES[$index]}" >&2
            for position in "${!PIDS[@]}"; do
                pid=${PIDS[$position]}
                if same_process "$pid" "${STAMPS[$position]}"; then
                    if ! kill -KILL "$pid" 2>/dev/null; then
                        same_process "$pid" "${STAMPS[$position]}" &&
                            fail "Cannot force-stop PID $pid for ${NAMES[$index]}"
                    fi
                fi
            done
            forced=true
            deadline=$((SECONDS + 5))
        fi
        sleep 0.2
    done
    rm -f -- "$STATE_DIR/${NAMES[$index]}.pid"
    printf '%s: stopped\n' "${NAMES[$index]}"
}

start_tool() {
    local index=$1 pid stamp log
    find_processes "$index"
    if ((${#PIDS[@]})); then
        printf '%s: already running (PIDs: %s); use restart to relaunch with file logging\n' \
            "${NAMES[$index]}" "${PIDS[*]}"
        return
    fi
    log="$LOG_DIR/${NAMES[$index]}.log"
    printf '\n=== %s starting %s ===\n' "$(date -Is)" "${NAMES[$index]}" >> "$log"
    local -a runner=("$conda" run --no-capture-output -n "${ENVS[$index]}" python)
    if [[ "${NAMES[$index]}" == neutron-reflection && -n "${NEUTRON_REFLECTION_PYTHON:-}" ]]; then
        runner=("$NEUTRON_REFLECTION_PYTHON")
    fi
    PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
        nohup "${runner[@]}" -u "$ROOT/${SCRIPTS[$index]}" </dev/null >> "$log" 2>&1 9>&- &
    pid=$!
    stamp=$(process_stamp "$pid") || fail "${NAMES[$index]} failed to start; see $log"
    printf '%s %s\n' "$pid" "$stamp" > "$STATE_DIR/${NAMES[$index]}.pid"
    sleep 3
    if ! same_process "$pid" "$stamp"; then
        rm -f -- "$STATE_DIR/${NAMES[$index]}.pid"
        tail -n 20 -- "$log" >&2
        fail "${NAMES[$index]} exited during startup; see $log"
    fi
    printf '%s: started (PID: %s), log: %s\n' "${NAMES[$index]}" "$pid" "$log"
}

if [[ "$action" == start || "$action" == restart ]]; then
    conda=${CONDA_EXE:-}
    need_conda=false
    for index in "${selected[@]}"; do
        if [[ "${NAMES[$index]}" != neutron-reflection || -z "$NEUTRON_REFLECTION_PYTHON" ]]; then
            need_conda=true
        fi
    done
    if "$need_conda"; then
        if [[ -z "$conda" ]]; then conda=$(command -v conda) || fail "conda not found; set CONDA_EXE."; fi
        [[ -x "$conda" ]] || fail "Conda executable not found: $conda"
    fi
    for index in "${selected[@]}"; do
        [[ -f "$ROOT/${SCRIPTS[$index]}" ]] || fail "Missing script: ${SCRIPTS[$index]}"
        find_processes "$index"
        if [[ "$action" == restart ]] || ((${#PIDS[@]} == 0)); then
            if [[ "${NAMES[$index]}" == neutron-reflection && -n "${NEUTRON_REFLECTION_PYTHON:-}" ]]; then
                [[ "$NEUTRON_REFLECTION_PYTHON" == /* && -x "$NEUTRON_REFLECTION_PYTHON" ]] ||
                    fail "NEUTRON_REFLECTION_PYTHON must be an executable absolute path."
                "$NEUTRON_REFLECTION_PYTHON" -c 'from rongzai.workflows.reflection.bl08a import BL08AReflectionOffline; import mcp, scipy, xarray, h5py, dotenv' ||
                    fail "Reflection dependencies unavailable; no processes were stopped."
                continue
            fi
            "$conda" run --no-capture-output -n "${ENVS[$index]}" /bin/true ||
                fail "Conda environment ${ENVS[$index]} is unavailable; no processes were stopped."
        fi
    done
fi

logs=()
for index in "${selected[@]}"; do
    case "$action" in
        start) start_tool "$index" ;;
        restart) stop_tool "$index"; start_tool "$index" ;;
        stop) stop_tool "$index" ;;
        status)
            find_processes "$index"
            printf '%s: %s\n' "${NAMES[$index]}" "${PIDS[*]:-stopped}"
            log="$LOG_DIR/${NAMES[$index]}.log"
            if [[ -f "$log" ]]; then printf '  saved log: %s\n' "$log"; fi
            for pid in "${PIDS[@]}"; do
                destination=$(readlink "/proc/$pid/fd/1") ||
                    fail "Cannot read stdout destination of PID $pid; retry status."
                printf '  PID %s stdout: %s\n' "$pid" "$destination"
            done
            ;;
        logs)
            log="$LOG_DIR/${NAMES[$index]}.log"
            [[ -f "$log" ]] || fail "No saved log for ${NAMES[$index]}; start/restart it first."
            logs+=("$log")
            ;;
    esac
done
if [[ "$action" == logs ]]; then exec tail -n 50 -F -- "${logs[@]}"; fi
