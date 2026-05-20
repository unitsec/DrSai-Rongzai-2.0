#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <workspace_root>" >&2
    exit 2
fi

workspace_root=$1
obj_root="${workspace_root}/.build/lib-obj-v2"
lib_root="${workspace_root}/.build/lib-v2"

mkdir -p "${obj_root}" "${lib_root}"

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

cflags=(
    -g
    -fPIC
    -pthread
    "${common_includes[@]}"
)

cxxflags=(
    -g
    -fPIC
    -pthread
    -std=gnu++2a
    -include
    bits/stdc++.h
    "${common_includes[@]}"
)

compile_c_if_needed() {
    local src=$1
    local rel=${src#"${workspace_root}/"}
    local obj="${obj_root}/${rel}.o"

    mkdir -p "$(dirname "${obj}")"
    if [[ ! -f "${obj}" || "${src}" -nt "${obj}" ]]; then
        gcc "${cflags[@]}" -c "${src}" -o "${obj}"
    fi
}

compile_cxx_if_needed() {
    local src=$1
    local rel=${src#"${workspace_root}/"}
    local obj="${obj_root}/${rel}.o"

    mkdir -p "$(dirname "${obj}")"
    if [[ ! -f "${obj}" || "${src}" -nt "${obj}" ]]; then
        g++ "${cxxflags[@]}" -c "${src}" -o "${obj}"
    fi
}

collect_objects() {
    local base=$1
    local pattern=$2
    find "${base}" -type f -name "${pattern}" | sort
}

build_archive() {
    local lib_path=$1
    shift
    local -a objects=("$@")

    if [[ ${#objects[@]} -eq 0 ]]; then
        return
    fi

    ar rcs "${lib_path}" "${objects[@]}"
}

while IFS= read -r src; do
    compile_c_if_needed "${src}"
done < <(collect_objects "${workspace_root}/Z-Foundation/Z-Foundation" '*.c')

while IFS= read -r src; do
    compile_c_if_needed "${src}"
done < <(collect_objects "${workspace_root}/Z-CIF/Z-CIF" '*.c')

while IFS= read -r src; do
    compile_c_if_needed "${src}"
done < <(collect_objects "${workspace_root}/Z-Rietveld/Z-Rietveld" '*.c')

if [[ -f "${workspace_root}/Z-Rietveld/Z-Rietveld/zstd_compat_stub.c" ]]; then
    compile_c_if_needed "${workspace_root}/Z-Rietveld/Z-Rietveld/zstd_compat_stub.c"
fi

while IFS= read -r src; do
    compile_cxx_if_needed "${src}"
done < <(find "${workspace_root}/Z-Rietveld/Z-Rietveld" -type f \( -name '*.cc' -o -name '*.cpp' \) ! -path '*/main_rietveld/main_rietveld.cc' | sort)

mapfile -t zfoundation_objs < <(find "${obj_root}/Z-Foundation/Z-Foundation" -type f -name '*.o' | sort)
mapfile -t zcif_objs < <(find "${obj_root}/Z-CIF/Z-CIF" -type f -name '*.o' | sort)
mapfile -t zrietveld_objs < <(find "${obj_root}/Z-Rietveld/Z-Rietveld" -type f -name '*.o' | sort)

build_archive "${lib_root}/libzfoundation.a" "${zfoundation_objs[@]}"
build_archive "${lib_root}/libzcif.a" "${zcif_objs[@]}"
build_archive "${lib_root}/libzrietveld.a" "${zrietveld_objs[@]}"

echo "Built libs in ${lib_root}"
