#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <workspace_root> <source_file> [module_name] [python_path]" >&2
    exit 2
fi

workspace_root=$1
source_file=$2
module_name=${3:-refineTool}
requested_python=${4:-}

python_candidates=(
    "${workspace_root}/.venv/bin/python"
    "/home/wangh/software/anaconda3/bin/python3"
    "/usr/bin/python3"
    "python3"
)

python_cmd=""

if [[ -n "${requested_python}" ]]; then
    python_candidates=("${requested_python}")
fi

for candidate in "${python_candidates[@]}"; do
    if ! command -v "${candidate}" >/dev/null 2>&1; then
        continue
    fi

    if "${candidate}" - <<'PY' >/dev/null 2>&1
import os
import pybind11
import sysconfig
inc = sysconfig.get_config_var('INCLUDEPY')
assert inc and os.path.exists(os.path.join(inc, 'Python.h'))
print(pybind11.get_include())
PY
    then
        python_cmd="${candidate}"
        break
    fi
done

if [[ -z "${python_cmd}" ]]; then
    echo "No suitable Python found (need pybind11 + Python.h)." >&2
    exit 1
fi

pybind_includes=$(${python_cmd} -m pybind11 --includes)
ext_suffix=$(${python_cmd} -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX') or '.so')")

bash "${workspace_root}/scripts/build_zlibs.sh" "${workspace_root}"

obj_root="${workspace_root}/.build/pybind-obj"
lib_root="${workspace_root}/.build/lib-v2"
out_dir="${workspace_root}"
mkdir -p "${obj_root}" "${out_dir}"

common_includes=(
    "-I${workspace_root}"
    "-I${workspace_root}/Library/include"
    "-I${workspace_root}/Z-Rietveld"
    "-I${workspace_root}/Z-Rietveld/Z-Rietveld"
    "-I${workspace_root}/Z-Foundation"
    "-I${workspace_root}/Z-Foundation/Z-Foundation"
    "-I${workspace_root}/Z-CIF"
    "-I${workspace_root}/Z-CIF/Z-CIF"
    "-I${workspace_root}/Z_DensityDistribution"
    "-I${workspace_root}/Z_DensityDistribution/Z_DensityDistribution"
)

rel=${source_file#"${workspace_root}/"}
source_obj="${obj_root}/${rel}.o"
mkdir -p "$(dirname "${source_obj}")"

if [[ ! -f "${source_obj}" || "${source_file}" -nt "${source_obj}" ]]; then
    g++ -g -fPIC -pthread -std=gnu++2a -DBUILD_PYBIND -include bits/stdc++.h \
        ${pybind_includes} "${common_includes[@]}" -c "${source_file}" -o "${source_obj}"
fi

out_file="${out_dir}/${module_name}${ext_suffix}"
g++ -shared -fPIC -g -pthread -std=gnu++2a -o "${out_file}" \
    "${source_obj}" \
    "${lib_root}/libzrietveld.a" \
    "${lib_root}/libzcif.a" \
    "${lib_root}/libzfoundation.a" \
    -lstdc++fs -lm

echo "Built Python module ${out_file}"
