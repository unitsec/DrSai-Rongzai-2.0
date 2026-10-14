#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fixture=$(mktemp -d /tmp/rongzai-tools-test.XXXXXXXX)
cp "$ROOT/start_tools.sh" "$fixture/start_tools.sh"
mkdir -p "$fixture/bin" "$fixture/tools/pyZRietveld" \
    "$fixture/tools/GSAS-II-master/GSASII" "$fixture/tools/sasview" \
    "$fixture/tools/pdf_refinement" "$fixture/tools/Irena" \
    "$fixture/tools"

cleanup() {
    bash "$fixture/start_tools.sh" stop all >/dev/null || return
    rm -f -- "$fixture/bin/conda" "$fixture/bin/reflection-python" "$fixture/start_tools.sh" \
        "$fixture/tools/pyZRietveld/0zrietveld_refine.py" \
        "$fixture/tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py" \
        "$fixture/tools/sasview/0sasview_refine.py" \
        "$fixture/tools/pdf_refinement/0pdf_refine.py" \
        "$fixture/tools/Irena/0Irena_fit.py" \
        "$fixture/tools/magnetic_structure_mcp.py" \
        "$fixture/tmp/tools/manager.lock"
    local name
    for name in zrietveld-refine gsas2-refine sasview-refine pdf-refine Irena-refine magnetic-analysis; do
        rm -f -- "$fixture/tmp/tools/logs/$name.log" "$fixture/tmp/tools/$name.pid"
    done
    rmdir "$fixture/bin" "$fixture/tools/pyZRietveld" \
        "$fixture/tools/GSAS-II-master/GSASII" "$fixture/tools/GSAS-II-master" \
        "$fixture/tools/sasview" "$fixture/tools/pdf_refinement" "$fixture/tools/Irena" \
        "$fixture/tools" "$fixture/tmp/tools/logs" "$fixture/tmp/tools" \
        "$fixture/tmp" "$fixture"
}
trap cleanup EXIT

cat > "$fixture/bin/conda" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == run && "$2" == --no-capture-output && "$3" == -n ]]
environment=$4
shift 4
[[ "${FAIL_ENV:-}" != "$environment" ]] || exit 2
if [[ "$1" == /bin/true ]]; then exit 0; fi
[[ "$1" == python && "$2" == -u ]]
shift 2
bash "$@" &
wait "$!"
EOF
chmod +x "$fixture/bin/conda"
export CONDA_EXE="$fixture/bin/conda"

cat > "$fixture/bin/reflection-python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == -c ]]; then exit 0; fi
[[ "$1" == -u ]]
shift
bash "$@" &
wait "$!"
EOF
chmod +x "$fixture/bin/reflection-python"
export NEUTRON_REFLECTION_PYTHON="$fixture/bin/reflection-python"

for entry in tools/pyZRietveld/0zrietveld_refine.py \
    tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py \
    tools/sasview/0sasview_refine.py tools/pdf_refinement/0pdf_refine.py \
    tools/Irena/0Irena_fit.py tools/magnetic_structure_mcp.py; do
    cat > "$fixture/$entry" <<'EOF'
#!/usr/bin/env bash
trap 'exit 0' TERM
echo "stdout ready"
echo "stderr ready" >&2
while :; do echo "tick"; sleep 0.2; done
EOF
done

manager() { bash "$fixture/start_tools.sh" "$@"; }
pid_of() { local pid stamp; read -r pid stamp < "$fixture/tmp/tools/$1.pid"; echo "$pid"; }

cd /
manager start all
first=$(pid_of pdf-refine)
child=$(ps -o pid= --ppid "$first")
child=${child// /}
[[ "$child" =~ ^[0-9]+$ ]]
manager start pdf-refine | grep 'already running' >/dev/null
[[ "$(pid_of pdf-refine)" == "$first" ]]
grep -q 'stdout ready' "$fixture/tmp/tools/logs/pdf-refine.log"
grep -q 'stderr ready' "$fixture/tmp/tools/logs/pdf-refine.log"
before=$(wc -l < "$fixture/tmp/tools/logs/pdf-refine.log")
sleep 1
after=$(wc -l < "$fixture/tmp/tools/logs/pdf-refine.log")
((after > before))
manager status pdf-refine | grep "$first" >/dev/null

if FAIL_ENV=diffpy.pdffit2_env manager restart pdf-refine; then
    echo "Unavailable environment must fail before stopping a tool" >&2
    exit 1
fi
kill -0 "$first"
manager restart pdf-refine
[[ "$(pid_of pdf-refine)" != "$first" ]]
if [[ -r "/proc/$child/stat" ]]; then
    state=$(ps -o stat= -p "$child")
    [[ "$state" == Z* ]]
fi
[[ "$(grep -c 'starting pdf-refine' "$fixture/tmp/tools/logs/pdf-refine.log")" == 2 ]]

set +e
output=$(timeout 1 bash "$fixture/start_tools.sh" logs pdf-refine)
code=$?
set -e
[[ "$code" == 124 && "$output" == *tick* ]]

manager stop pdf-refine
manager status pdf-refine | grep stopped >/dev/null
printf '%s 0\n' "$$" > "$fixture/tmp/tools/pdf-refine.pid"
manager status pdf-refine | grep stopped >/dev/null
rm -f -- "$fixture/tmp/tools/pdf-refine.pid"
bash "$fixture/tools/pdf_refinement/0pdf_refine.py" >/dev/null 2>&1 &
legacy=$!
sleep 0.2
manager start pdf-refine | grep 'already running' >/dev/null
[[ ! -f "$fixture/tmp/tools/pdf-refine.pid" ]]
manager status pdf-refine | grep "$legacy" >/dev/null
manager restart pdf-refine
wait "$legacy"
[[ -f "$fixture/tmp/tools/pdf-refine.pid" ]]

if manager start unknown-tool; then exit 1; fi
manager stop pdf-refine
cat > "$fixture/tools/pdf_refinement/0pdf_refine.py" <<'EOF'
#!/usr/bin/env bash
echo 'intentional startup failure' >&2
exit 7
EOF
if manager start pdf-refine; then exit 1; fi
[[ ! -f "$fixture/tmp/tools/pdf-refine.pid" ]]
grep -q 'intentional startup failure' "$fixture/tmp/tools/logs/pdf-refine.log"
manager stop all
echo 'PASS: background startup, idempotency, parent/child restart, stale PIDs, unmanaged adoption, live/append logs, preflight and failures'
