#!/usr/bin/env bash
# =============================================================================
# 01_env_check.sh —— 环境自检（第 3 章）
#
# 跑法：  bash 01_env_check.sh
#
# 它检查你"要开始做实验"所需的全部东西，并且对每个缺失项给出安装命令。
# 任何时候觉得"环境不对"，先跑这个，再去看书。
# 这个习惯在真实项目里同样适用：把环境依赖写成一个脚本，
# 比在 README 里写一段话可靠得多。
# =============================================================================
set -u

ok=0; fail=0
check() {  # 名称, 命令
    local name=$1; shift
    if "$@" >/dev/null 2>&1; then
        printf '  ✓ %s\n' "$name"; ok=$((ok+1))
    else
        printf '  ✗ %s   ← 缺失\n' "$name"; fail=$((fail+1))
    fi
}

echo "=== 1. EPICS Base 命令行工具 ==="
for tool in softIoc softIocPVA caget caput camonitor pvget pvput pvmonitor; do
    check "$tool" command -v "$tool"
done
if command -v softIoc >/dev/null 2>&1; then
    echo "     EPICS_BASE=${EPICS_BASE:-（未设置，通常没问题）}"
    echo "     softIoc 路径: $(command -v softIoc)"
fi

echo
echo "=== 2. Python 与库 ==="
check "python3" command -v python3
python3 - <<'PY' 2>/dev/null || echo "  ✗ 下面这些 Python 库有缺失"
import importlib, sys
mods = {"epics": "pyepics", "caproto": "caproto", "numpy": "numpy",
        "matplotlib": "matplotlib", "serial": "pyserial", "mcp": "mcp"}
missing = []
for mod, pkg in mods.items():
    try:
        m = importlib.import_module(mod)
        ver = getattr(m, "__version__", "?")
        print(f"  ✓ {mod:<12} {ver}")
    except ImportError:
        print(f"  ✗ {mod:<12} ← 安装： pip install {pkg}")
        missing.append(pkg)
sys.exit(1 if missing else 0)
PY

echo
echo "=== 3. 编译 EPICS 需要的工具（只有源码安装才需要）==="
for tool in gcc g++ make perl; do
    check "$tool" command -v "$tool"
done

echo
echo "=== 4. CA 网络环境（最容易被忽略，也最容易出怪问题）==="
echo "  EPICS_CA_ADDR_LIST      = ${EPICS_CA_ADDR_LIST:-（未设置 → 用自动地址列表，推荐）}"
echo "  EPICS_CA_AUTO_ADDR_LIST = ${EPICS_CA_AUTO_ADDR_LIST:-（未设置 → 默认 YES）}"
if [[ -n "${EPICS_CA_ADDR_LIST:-}" && "${EPICS_CA_AUTO_ADDR_LIST:-YES}" != "YES" ]]; then
    echo "  ⚠️  你手工指定了单播地址列表："
    echo "     如果同一台机器上跑多个 IOC/CA server，会有 PV 读不到。"
    echo "     除非你在用 CA Gateway 跨网段，否则建议 unset EPICS_CA_ADDR_LIST"
fi

echo
echo "=== 5. 结论 ==="
echo "  通过 $ok 项，缺失 $fail 项"
if [[ $fail -gt 0 ]]; then
    echo
    echo "  最快补齐方式（任选一条安装路线）："
    echo "    conda create -n epics -c conda-forge epics-base && conda activate epics"
    echo "    pip install pyepics caproto pyserial matplotlib mcp"
    echo
    echo "  详见第 3 章。"
fi
