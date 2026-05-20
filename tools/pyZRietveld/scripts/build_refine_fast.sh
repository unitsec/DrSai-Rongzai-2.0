#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <workspace_root> <source_file> [output_file]" >&2
    exit 2
fi

workspace_root=$1
source_file=$2
output_file=${3:-"${source_file%.*}"}

bash "${workspace_root}/scripts/build_zlibs.sh" "${workspace_root}"

obj_root="${workspace_root}/.build/app-obj"
lib_root="${workspace_root}/.build/lib-v2"
mkdir -p "${obj_root}"

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
    g++ -g -pthread -std=gnu++2a -include bits/stdc++.h "${common_includes[@]}" -c "${source_file}" -o "${source_obj}"
fi

mkdir -p "$(dirname "${output_file}")"
g++ -g -pthread -std=gnu++2a -o "${output_file}" \
    "${source_obj}" \
    "${lib_root}/libzrietveld.a" \
    "${lib_root}/libzcif.a" \
    "${lib_root}/libzfoundation.a" \
    -lstdc++fs -lm

echo "Built ${output_file}"
