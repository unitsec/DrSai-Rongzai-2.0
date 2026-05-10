#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <workspace_root> <source_file> [output_file]" >&2
    exit 2
fi

workspace_root=$1
source_file=$2
output_file=${3:-"${source_file%.*}"}

build_root="${workspace_root}/.build/full-link"
obj_root="${build_root}/obj"

mkdir -p "${obj_root}"

cflags=(
    -g
    -pthread
    "-I${workspace_root}"
    "-I${workspace_root}/Library/include"
    "-I${workspace_root}/Z-Rietveld"
    "-I${workspace_root}/Z-Rietveld/Z-Rietveld"
    "-I${workspace_root}/Z-Foundation"
    "-I${workspace_root}/Z-CIF"
    "-I${workspace_root}/Z_DensityDistribution"
    "-I${workspace_root}/Z_DensityDistribution/Z_DensityDistribution"
)

cxxflags=(
    -g
    -pthread
    -std=gnu++2a
    -include
    bits/stdc++.h
    "-I${workspace_root}"
    "-I${workspace_root}/Library/include"
    "-I${workspace_root}/Z-Rietveld"
    "-I${workspace_root}/Z-Rietveld/Z-Rietveld"
    "-I${workspace_root}/Z-Foundation"
    "-I${workspace_root}/Z-CIF"
    "-I${workspace_root}/Z_DensityDistribution"
    "-I${workspace_root}/Z_DensityDistribution/Z_DensityDistribution"
)

compile_c() {
    local src=$1
    local rel=${src#"${workspace_root}/"}
    local obj="${obj_root}/${rel}.o"

    mkdir -p "$(dirname "${obj}")"
    gcc "${cflags[@]}" -c "${src}" -o "${obj}"
}

compile_cxx() {
    local src=$1
    local rel=${src#"${workspace_root}/"}
    local obj="${obj_root}/${rel}.o"

    mkdir -p "$(dirname "${obj}")"
    g++ "${cxxflags[@]}" -c "${src}" -o "${obj}"
}

while IFS= read -r -d '' src; do
    compile_c "${src}"
done < <(find "${workspace_root}/Z-Foundation/Z-Foundation" -type f -name '*.c' -print0)

while IFS= read -r -d '' src; do
    compile_c "${src}"
done < <(find "${workspace_root}/Z-CIF/Z-CIF" -type f -name '*.c' -print0)

while IFS= read -r -d '' src; do
    compile_c "${src}"
done < <(find "${workspace_root}/Z-Rietveld/Z-Rietveld" -type f -name '*.c' -print0)

if [[ -f "${workspace_root}/Z-Rietveld/Z-Rietveld/zstd_compat_stub.c" ]]; then
    compile_c "${workspace_root}/Z-Rietveld/Z-Rietveld/zstd_compat_stub.c"
fi

while IFS= read -r -d '' src; do
    compile_cxx "${src}"
done < <(find "${workspace_root}/Z-Rietveld/Z-Rietveld" -type f \( -name '*.cc' -o -name '*.cpp' \) ! -path '*/main_rietveld/main_rietveld.cc' -print0)

compile_cxx "${source_file}"

source_obj="${obj_root}/${source_file#"${workspace_root}/"}.o"
objects=("${source_obj}")

while IFS= read -r -d '' obj; do
    if [[ "${obj}" != "${source_obj}" ]]; then
        objects+=("${obj}")
    fi
done < <(find "${obj_root}" -type f -name '*.o' -print0 | sort -z)

mkdir -p "$(dirname "${output_file}")"
g++ -g -pthread -std=gnu++2a -o "${output_file}" "${objects[@]}" -lstdc++fs -lm

echo "Built ${output_file}"