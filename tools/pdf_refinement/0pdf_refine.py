#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF refinement tools organized like the neutron GSAS-II tool suite.

This module exposes three polling MCP tools:
- InitializeProject: validate inputs and create a project manifest
- Refine: run staged PDF refinement and write outputs
- ExportCIFs: collect or regenerate refined CIF outputs from a project

The architecture intentionally mirrors the GSAS-II neutron tool pattern:
pure business functions + ProcessPollingMcpToolBase runners + MCP registration.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from diffpy.pdffit2 import PdfFit
from diffpy.structure import Atom, PDFFitStructure, Structure
from diffpy.structure.parsers import getParser
from diffpy.structure.symmetryutilities import ExpandAsymmetricUnit
from mcp.server.fastmcp import FastMCP

try:
    from src.core.tools import ProcessPollingMcpToolBase
except ImportError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.core.tools import ProcessPollingMcpToolBase

from dotenv import load_dotenv
here = Path(__file__).parent.parent.parent.absolute()
print(f"Loading environment variables from: {here / '.env'}")
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
DEFAULT_WORKDIR = Path(os.getenv("WORKDIR", ""))
print(f"Using WORKER_SPACE: {DEFAULT_WORKDIR}")


def calcu_byte_size(file_path: str | Path) -> int:
    return Path(file_path).stat().st_size
    
def _detect_pdf_r_bounds(data_file: str | Path) -> tuple[float, float]:
    r_values: List[float] = []
    with Path(data_file).open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            try:
                r_values.append(float(parts[0]))
            except (ValueError, IndexError):
                continue

    if not r_values:
        raise ValueError(f"无法从数据文件读取 r 范围: {data_file}")

    rmin = min(r_values)
    rmax = max(r_values)
    safe_rmax = rmax - max(0.01, (rmax - rmin) * 0.001)
    if safe_rmax <= rmin:
        safe_rmax = rmax
    return rmin, safe_rmax


def _safe_json_dump(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _safe_json_load(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_pdf_source(source: Any) -> str:
    text = str(source or "N").strip().upper()
    if text in {"XRAY", "X-RAY", "X"}:
        return "X"
    if text in {"NEUTRON", "N"}:
        return "N"
    return "N"


def _normalize_project_manifest(raw_manifest: Dict[str, Any]) -> Dict[str, Any]:
    manifest = dict(raw_manifest or {})
    structure_files = manifest.get("structure_files")
    if not isinstance(structure_files, list):
        structure_files = []

    refine_config = manifest.get("refine_config") if isinstance(manifest.get("refine_config"), dict) else {}
    phase_count = max(len(structure_files), 1)
    if not refine_config:
        refine_config = _build_pdf_default_config(num_phases=phase_count)
    else:
        refine_config = _merge_refine_configs(_build_pdf_default_config(num_phases=phase_count), refine_config)

    params_current = refine_config.get("paramsCurrentValue", {}) if isinstance(refine_config.get("paramsCurrentValue"), dict) else {}
    params_current["pdf_source"] = _normalize_pdf_source(params_current.get("pdf_source"))
    refine_config["paramsCurrentValue"] = params_current

    return {
        "manifest_version": 1,
        "data_file": manifest.get("data_file"),
        "structure_files": [str(p) for p in structure_files],
        "refine_config": refine_config,
        "output_files": manifest.get("output_files") if isinstance(manifest.get("output_files"), dict) else {},
        "refined_params": manifest.get("refined_params") if isinstance(manifest.get("refined_params"), dict) else {},
    }


def _make_next_manifest_path(source_path: Path, task_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    safe_task_id = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in str(task_id))
    return source_path.with_name(f"{source_path.stem}__{safe_task_id}__{stamp}.json")


def create_output_directory(path: str | Path) -> Path:
    output_dir = Path(path).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_refined_cif_filename(original_cif_path: str | Path) -> str:
    base_name = Path(original_cif_path).stem
    return f"{base_name}_pdf_refined.cif"


def _update_cif_with_refined_params(
    original_cif_path: str | Path,
    refined_params: Dict[str, Any],
    output_cif_path: str | Path,
    phase_index: int = 1,
) -> bool:
    """Update a CIF file with refined lattice/temperature information.

    The original file is preserved structurally; we only patch summary values
    and append a refinement note, which keeps the implementation robust across
    varied CIF styles.
    """

    try:
        original_cif_path = Path(original_cif_path)
        output_cif_path = Path(output_cif_path)

        with open(original_cif_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        lattice_param = refined_params[f"lattice_parameter_{phase_index}"]
        temp_factor = refined_params[f"temperature_factor_{phase_index}"]
        scale_factor = refined_params.get(f"scale_{phase_index}", 1.0)
        qdamp = refined_params.get("qdamp", 0.03)
        delta2 = refined_params.get("delta2", 0.0003)
        rw = refined_params.get("rw", 0.0)

        output_lines: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("_cell_length_a"):
                output_lines.append(f"_cell_length_a                   {lattice_param:.6f}\n")
            elif stripped.startswith("_cell_length_b"):
                output_lines.append(f"_cell_length_b                   {lattice_param:.6f}\n")
            elif stripped.startswith("_cell_length_c"):
                output_lines.append(f"_cell_length_c                   {lattice_param:.6f}\n")
            elif stripped.startswith("_cell_volume"):
                output_lines.append(f"_cell_volume                     {lattice_param ** 3:.4f}\n")
            else:
                output_lines.append(line)

        info = [
            f"# PDF refinement results for phase {phase_index}\n",
            f"# Refined lattice parameter: {lattice_param:.6f} A\n",
            f"# Refined temperature factor: {temp_factor:.6f} A^2\n",
            f"# Refined scale factor: {scale_factor:.6f}\n",
            f"# Refined Qdamp: {qdamp:.6f}\n",
            f"# Refined Delta2: {delta2:.6f}\n",
            f"# R-weight: {rw:.6f}\n",
            f"# Refinement date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            "\n",
        ]

        output_cif_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_cif_path, "w", encoding="utf-8") as f:
            f.writelines(info + output_lines)

        return True
    except Exception:
        return False


def _build_pdf_default_config(num_phases: int = 1) -> Dict[str, Any]:
    return {
        "paramsCurrentValue": {
            "qmax": 25.0,
            "rmin": 1.0,
            "rmax": 20.0,
            "pdf_source": "N",
            "dscale": 1.0,
            "qdamp": 0.001,
            "qbroad": 0.0,
            "delta1": 0.0,
            "delta2": 0.0003,
        },
        "run_control": {
            "cycles": 1,
        },
        "parameter_controls": [],
    }


def _merge_refine_config(refine_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = _build_pdf_default_config()
    if isinstance(refine_config, dict):
        for key, value in refine_config.items():
            if key == "paramsCurrentValue" and isinstance(value, dict):
                merged_params = dict(merged["paramsCurrentValue"])
                merged_params.update(value)
                merged["paramsCurrentValue"] = merged_params
            elif key == "run_control" and isinstance(value, dict):
                merged_run = dict(merged["run_control"])
                merged_run.update(value)
                merged["run_control"] = merged_run
            elif key == "parameter_controls" and isinstance(value, list):
                merged["parameter_controls"] = list(value)
            else:
                merged[key] = value
    return merged


def _merge_refine_configs(
    base_config: Optional[Dict[str, Any]],
    override_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = _merge_refine_config(base_config)

    # Keep base snapshot state when caller does not provide override.
    if not isinstance(override_config, dict):
        return merged

    if isinstance(override_config.get("paramsCurrentValue"), dict):
        merged_params = dict(merged.get("paramsCurrentValue", {}))
        merged_params.update(override_config["paramsCurrentValue"])
        merged["paramsCurrentValue"] = merged_params

    if isinstance(override_config.get("run_control"), dict):
        merged_run_control = dict(merged.get("run_control", {}))
        merged_run_control.update(override_config["run_control"])
        merged["run_control"] = merged_run_control

    if isinstance(override_config.get("parameter_controls"), list):
        merged["parameter_controls"] = list(override_config["parameter_controls"])

    for key, value in override_config.items():
        if key not in {"paramsCurrentValue", "run_control", "parameter_controls"}:
            merged[key] = value

    return merged


def _build_phase_param_indices(num_phases: int) -> Dict[int, Dict[str, int]]:
    mapping: Dict[int, Dict[str, int]] = {}
    next_idx = 100
    for phase_idx in range(1, num_phases + 1):
        mapping[phase_idx] = {
            "lattice": next_idx,
            "temperature": next_idx + 1,
            "scale": next_idx + 2,
            "sratio": next_idx + 3,
            "spdiameter": next_idx + 4,
            "stepcut": next_idx + 5,
            "rcut": next_idx + 6,
        }
        next_idx += 7
    return mapping


def _safe_constrain(pf: PdfFit, variable: Any, token: str) -> bool:
    try:
        pf.constrain(variable, token)
        return True
    except Exception:
        return False


def _safe_getpar(pf: PdfFit, idx: int, default: Any = None) -> Any:
    try:
        return pf.getpar(idx)
    except Exception:
        return default


_PHASE_SELECTOR_RE = re.compile(r"^phase\[(\d+)\]\.(.+)$", re.IGNORECASE)
_ATOM_SELECTOR_RE = re.compile(r"^atom\[(\d+)\]\.(x|y|z|occ|u11|u22|u33|u12|u13|u23)$", re.IGNORECASE)


def _resolve_pf_variable(pf: PdfFit, selector: str):
    text = str(selector or "").strip()
    if not text:
        raise ValueError("Empty parameter selector")

    key = text.lower()
    global_vars = {
        "dscale": pf.dscale,
        "qdamp": pf.qdamp,
        "delta2": pf.delta2,
        "qbroad": pf.qbroad,
        "delta1": pf.delta1,
    }
    if key in global_vars:
        return global_vars[key]()

    m = _PHASE_SELECTOR_RE.match(text)
    if not m:
        raise ValueError(f"Unsupported selector: {selector}")

    phase_idx = int(m.group(1))
    phase_key = m.group(2).strip().lower()
    pf.setphase(phase_idx)

    if phase_key in {"pscale", "sratio", "spdiameter", "stepcut", "rcut"}:
        return getattr(pf, phase_key)()

    if phase_key in {"lat.a", "lat1", "lat.a1", "lata"}:
        return pf.lat(1)
    if phase_key in {"lat.b", "lat2", "lat.b2", "latb"}:
        return pf.lat(2)
    if phase_key in {"lat.c", "lat3", "lat.c3", "latc"}:
        return pf.lat(3)

    am = _ATOM_SELECTOR_RE.match(phase_key)
    if am:
        atom_idx = int(am.group(1))
        atom_key = am.group(2).lower()
        return getattr(pf, atom_key)(atom_idx)

    raise ValueError(f"Unsupported selector: {selector}")


def _coerce_param_index(value: Any, named_bindings: Dict[str, int], next_param_idx: int) -> tuple[Optional[int], int]:
    if value is None:
        return None, next_param_idx
    if isinstance(value, bool):
        return None, next_param_idx
    if isinstance(value, int):
        return int(value), next_param_idx

    token = str(value).strip()
    if not token:
        return None, next_param_idx
    if token.startswith("@"):
        return int(token[1:]), next_param_idx
    if token.lstrip("+-").isdigit():
        return int(token), next_param_idx

    if token not in named_bindings:
        named_bindings[token] = next_param_idx
        next_param_idx += 1
    return named_bindings[token], next_param_idx


def _apply_parameter_controls(pf: PdfFit, controls: Any, start_param_idx: int = 20000) -> int:
    if not isinstance(controls, list) or not controls:
        return start_param_idx

    named_bindings: Dict[str, int] = {}
    next_param_idx = start_param_idx
    initialized_params: set[int] = set()

    for control in controls:
        if not isinstance(control, dict):
            continue

        selector = control.get("selector")
        if not selector:
            continue

        var = _resolve_pf_variable(pf, str(selector))

        explicit_param = control.get("param_id")
        param_idx, next_param_idx = _coerce_param_index(explicit_param, named_bindings, next_param_idx)

        bind_value = control.get("bind")
        if bind_value is False or bool(control.get("unbind", False)):
            param_idx = next_param_idx
            next_param_idx += 1
        elif bind_value is not None:
            param_idx, next_param_idx = _coerce_param_index(bind_value, named_bindings, next_param_idx)

        if param_idx is not None:
            pf.constrain(var, f"@{param_idx}")

        has_initial = ("initial" in control) or ("set" in control)
        if has_initial:
            initial_value = control.get("initial", control.get("set"))
            if param_idx is None:
                raise ValueError(f"Control {selector} sets initial value but has no bound parameter")
            pf.setpar(param_idx, float(initial_value))
            initialized_params.add(param_idx)
        elif param_idx is not None and param_idx not in initialized_params:
            pf.setpar(param_idx, float(pf.getvar(var)))
            initialized_params.add(param_idx)

        refine_flag = control.get("refine")
        free_flag = control.get("free")
        fixed_flag = control.get("fixed")

        if isinstance(refine_flag, bool):
            if param_idx is None:
                raise ValueError(f"Control {selector} sets refine but has no bound parameter")
            if refine_flag:
                pf.freepar(param_idx)
            else:
                pf.fixpar(param_idx)

        if isinstance(free_flag, bool):
            if param_idx is None:
                raise ValueError(f"Control {selector} sets free but has no bound parameter")
            if free_flag:
                pf.freepar(param_idx)
            else:
                pf.fixpar(param_idx)

        if isinstance(fixed_flag, bool):
            if param_idx is None:
                raise ValueError(f"Control {selector} sets fixed but has no bound parameter")
            if fixed_flag:
                pf.fixpar(param_idx)
            else:
                pf.freepar(param_idx)

    return next_param_idx


def _plot_refinement_result(pf: PdfFit, output_path: Path, title: str) -> str:
    r = pf.getR()
    g_obs = np.asarray(pf.getpdf_obs())
    g_fit = np.asarray(pf.getpdf_fit())
    diff = g_obs - g_fit

    fig, (ax_main, ax_diff) = plt.subplots(
        2,
        1,
        figsize=(12, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
    )

    ax_main.plot(r, g_obs, "ko", markersize=2, label="Experimental", alpha=0.8)
    ax_main.plot(r, g_fit, "r-", linewidth=1.5, label="Fit")
    ax_main.set_ylabel("G (Å$^{-2}$)", fontsize=12)
    ax_main.set_title(title, fontsize=16)
    ax_main.legend(fontsize=11)
    ax_main.grid(True, alpha=0.3)

    ax_diff.plot(r, diff, "b-", linewidth=1.0, label="Difference")
    ax_diff.axhline(0.0, color="gray", linewidth=0.9, linestyle="--", alpha=0.8)
    ax_diff.set_xlabel("r (Å)", fontsize=14)
    ax_diff.set_ylabel("ΔG", fontsize=12)
    ax_diff.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=240, bbox_inches="tight")
    plt.close()
    return str(output_path)


def _parse_cif_number(token: str) -> float:
    text = str(token).strip()
    if not text:
        raise ValueError("empty CIF numeric token")
    if "(" in text:
        text = text.split("(", 1)[0]
    return float(text)


def _read_cif_atom_sites(cif_path: str | Path) -> Dict[str, Any]:
    lines = Path(cif_path).read_text(encoding="utf-8").splitlines()
    result: Dict[str, Any] = {
        "space_group_number": None,
        "space_group_name": "",
        "atom_sites": [],
    }

    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        lower = stripped.lower()
        if lower.startswith("_space_group_it_number") or lower.startswith("_symmetry_int_tables_number"):
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                try:
                    result["space_group_number"] = int(float(parts[1].strip().strip("'\"")))
                except ValueError:
                    pass
        elif lower.startswith("_space_group_name_h-m_alt") or lower.startswith("_symmetry_space_group_name_h-m"):
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                result["space_group_name"] = parts[1].strip().strip("'\"")
        elif lower == "loop_":
            headers: List[str] = []
            j = idx + 1
            while j < len(lines):
                candidate = lines[j].strip()
                if candidate.startswith("_"):
                    headers.append(candidate)
                    j += 1
                    continue
                break
            if headers and any(h.lower().startswith("_atom_site_") for h in headers):
                header_map = {header.lower(): pos for pos, header in enumerate(headers)}
                required = [
                    "_atom_site_type_symbol",
                    "_atom_site_fract_x",
                    "_atom_site_fract_y",
                    "_atom_site_fract_z",
                ]
                if all(key in header_map for key in required):
                    while j < len(lines):
                        record = lines[j].strip()
                        if not record or record == "loop_" or record.startswith("_") or record.lower().startswith("data_"):
                            break
                        parts = record.split()
                        if len(parts) >= len(headers):
                            result["atom_sites"].append(
                                {
                                    "element": parts[header_map["_atom_site_type_symbol"]],
                                    "x": _parse_cif_number(parts[header_map["_atom_site_fract_x"]]),
                                    "y": _parse_cif_number(parts[header_map["_atom_site_fract_y"]]),
                                    "z": _parse_cif_number(parts[header_map["_atom_site_fract_z"]]),
                                }
                            )
                        j += 1
                    idx = j - 1
        idx += 1

    return result


def _maybe_normalize_single_site_fd3m_cif(structure_file: str | Path, structure: Structure) -> Structure:
    cif_info = _read_cif_atom_sites(structure_file)
    space_group_number = cif_info.get("space_group_number")
    space_group_name = str(cif_info.get("space_group_name") or "").upper().replace(" ", "")
    atom_sites = cif_info.get("atom_sites") if isinstance(cif_info.get("atom_sites"), list) else []

    is_fd3m = space_group_number == 227 or "FD-3M" in space_group_name
    if not is_fd3m or len(atom_sites) != 1 or len(structure) != 16:
        return structure

    site = atom_sites[0]
    x = float(site["x"]) % 1.0
    y = float(site["y"]) % 1.0
    z = float(site["z"]) % 1.0
    if max(abs(x - y), abs(y - z), abs(x - z)) > 1e-6:
        return structure

    translations = [
        (0.0, 0.0, 0.0),
        (0.0, 0.5, 0.5),
        (0.5, 0.0, 0.5),
        (0.5, 0.5, 0.0),
    ]
    basis_sites = [
        (x, y, z),
        ((x + 0.25) % 1.0, (y + 0.25) % 1.0, (z + 0.25) % 1.0),
    ]

    normalized = Structure(lattice=structure.lattice)
    normalized.title = getattr(structure, "title", "")
    template_atom = structure[0]
    for basis in basis_sites:
        for shift in translations:
            atom = Atom(template_atom)
            atom.xyz = [((basis[i] + shift[i]) % 1.0) for i in range(3)]
            normalized.append(atom)
    return normalized


def _iter_cif_expansion_candidates(parser: Any):
    if getattr(parser, "asymmetric_unit", None) is None or getattr(parser, "spacegroup", None) is None:
        raise ValueError("CIF parser did not return asymmetric-unit metadata")

    corepos = [atom.xyz for atom in parser.asymmetric_unit]
    coreUijs = [atom.U for atom in parser.asymmetric_unit]

    base_offsets: List[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    diag_shifts = [0.0, 0.125, 0.25, 0.375, 0.5]
    for atom in parser.asymmetric_unit:
        xyz = tuple(float(v) % 1.0 for v in atom.xyz)
        base_offsets.append(xyz)
        for shift in diag_shifts[1:]:
            base_offsets.append(tuple((v + shift) % 1.0 for v in xyz))

    seen_offsets = set()
    for offset in base_offsets:
        if offset in seen_offsets:
            continue
        seen_offsets.add(offset)
        eau = ExpandAsymmetricUnit(parser.spacegroup, corepos, coreUijs, sgoffset=offset)
        total_atoms = int(sum(eau.multiplicity))
        yield offset, eau, total_atoms, len(eau.multiplicity)


def _select_best_cif_expansion(parser: Any):
    best_offset = None
    best_expanded = None
    best_score = None
    for offset, eau, total_atoms, site_count in _iter_cif_expansion_candidates(parser):
        score = (total_atoms, site_count, offset)
        if best_score is None or score < best_score:
            best_score = score
            best_offset = offset
            best_expanded = eau
    if best_expanded is None or best_offset is None:
        raise ValueError("Unable to select a CIF expansion candidate")
    return best_expanded, best_offset, best_score


def _load_cif_as_pdffit_structure(structure_file: str | Path) -> PDFFitStructure:
    parser = getParser("cif")
    parsed = parser.parseFile(str(structure_file))
    best_expanded, _, _ = _select_best_cif_expansion(parser)

    stru = PDFFitStructure(lattice=parsed.lattice, title=parsed.title)
    stru.pdffit["spcgr"] = parser.spacegroup.short_name
    template_atoms = list(parser.asymmetric_unit)
    for site_idx, site in enumerate(best_expanded.expandedpos):
        template_atom = template_atoms[site_idx]
        for pos_idx, pos in enumerate(site):
            atom = Atom(template_atom)
            atom.xyz = [float(v) for v in pos]
            if best_expanded.expandedUijs:
                atom.U = best_expanded.expandedUijs[site_idx][pos_idx]
            stru.append(atom)

    return stru


def _load_structure_files(pf: PdfFit, structure_files: List[str]) -> None:
    for idx, structure_file in enumerate(structure_files, start=1):
        if structure_file.lower().endswith(".cif"):
            try:
                structure = _load_cif_as_pdffit_structure(structure_file)
                pf.read_struct_string(structure.writeStr("pdffit"), name=Path(structure_file).stem)
            except Exception:
                # Fall back to the legacy path for unusual CIFs that the parser cannot normalize.
                structure = Structure()
                structure.read(structure_file)
                structure = _maybe_normalize_single_site_fd3m_cif(structure_file, structure)
                pf.add_structure(structure)
        else:
            pf.read_struct(structure_file)
        pf.setphase(idx)


def _configure_pdffit(
    pf: PdfFit,
    structure_files: List[str],
    qmax: float,
    qdamp: float,
    rmin: float,
    rmax: float,
    refine_config: Dict[str, Any],
) -> Dict[int, Dict[str, int]]:
    pf.read_data("__pending__", "X", qmax, qdamp)  # placeholder, replaced by caller
    raise RuntimeError("_configure_pdffit must not be called directly")


def initialize_pdf_project(
    data_file: str,
    structure_files: List[str],
    qmax: float = 25.0,
    rmin: float = 1.0,
    rmax: float = 20.0,
    pdf_source: str = "N",
    user_id: str = None,
    session_id: str = None,
    mission_name: str = None,
    task_id: str = None,
) -> Dict[str, Any]:
    """Create the first immutable PDF refinement snapshot file.

    This function prepares a minimal project JSON that serves as the starting
    point for the refinement chain. The returned `project_path` is the file that
    later `Refine` calls should read. Each subsequent refinement creates a new
    JSON snapshot next to this one, so the input file itself is never modified.

    Args:
        data_file: Experimental PDF data file.
        structure_files: Initial structure files, usually CIF files.
        qmax: Q cutoff used when reading PDF data.
        rmin: Lower r-range used for refinement.
        rmax: Upper r-range used for refinement.
        pdf_source: PDF source type, `X` or `N`.
        user_id: User namespace used to build the project directory.
        session_id: Session namespace used to build the project directory.
        mission_name: Mission namespace used to build the project directory.
        task_id: Sub-task identifier used to name the manifest file.

    Returns:
        A dictionary with the initial snapshot result.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: path to the created JSON snapshot
        - `manifest`: the snapshot content written to disk
        - `error`: error message when `status` is `failed`
        - `traceback`: debug traceback when `status` is `failed`
    """
    results = {
        "status": "failed"}

    try:
        project_dir = DEFAULT_WORKDIR / user_id / session_id / mission_name
        path = Path(project_dir)
        path.mkdir(parents=True, exist_ok=True)

        if not Path(data_file).exists():
            raise FileNotFoundError(f"数据文件不存在: {data_file}")
        for i, struct_file in enumerate(structure_files, start=1):
            if not Path(struct_file).exists():
                raise FileNotFoundError(f"结构文件 {i} 不存在: {struct_file}")
            
        project_path = str(project_dir / f"{task_id}.json")

        refineConfig = _build_pdf_default_config(num_phases=len(structure_files))
        refineConfig["paramsCurrentValue"]["qmax"] = qmax
        refineConfig["paramsCurrentValue"]["rmin"] = rmin
        refineConfig["paramsCurrentValue"]["rmax"] = rmax
        refineConfig["paramsCurrentValue"]["pdf_source"] = pdf_source
        
        manifest = {
            "manifest_version": 1,
            "data_file": data_file,
            "structure_files": structure_files,
            "refine_config": refineConfig,
            "output_files": {},
            "refined_params": {},
        }
        manifest = _normalize_project_manifest(manifest)
        _safe_json_dump(Path(project_path), manifest)

        results["status"] = "success"
        results["project_path"] = project_path
        results["manifest"] = manifest
        return results
    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results


def refine(
    project_path: str,
    refine_config: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one PDF refinement step from a snapshot and write a new snapshot.

        This function is intended to be usable by a stateless model that only reads
        this docstring.

        Execution flow:
        1. Load snapshot from project_path.
        2. Read PDF data and structures.
        3. Create baseline constraints (baseline parameters are fixed).
        4. Apply refine_config.parameter_controls in list order.
        5. Run pf.refine() for run_control.cycles iterations.
        6. Save outputs and write a new immutable snapshot.

        refine_config strict schema:

        A) paramsCurrentValue (dict, optional)
             Runtime numeric inputs.
             Supported keys and meaning:
             - qmax: float, Q cutoff in read_data.
             - rmin: float, fit lower bound.
             - rmax: float, fit upper bound.
             - pdf_source: str, "N" or "X".
             - dscale: float, initial global data scale value.
             - qdamp: float, initial qdamp.
             - qbroad: float, initial qbroad.
             - delta1: float, initial delta1.
             - delta2: float, initial delta2.

        B) run_control (dict, optional)
             - cycles: int >= 1, number of pf.refine() calls.

        C) parameter_controls (list[dict], optional)
             The only constraint-control interface.
             Each item controls one selector target:
             - selector (required): variable path.
             - bind (optional):
                 - int -> bind to that parameter index
                 - "@123" -> bind to parameter 123
                 - string symbol -> symbolic shared binding group
                 - false -> force unbind (allocate unique parameter)
             - param_id (optional): explicit int or "@int".
             - unbind (optional): bool, force unique parameter.
             - initial or set (optional): numeric initial value.
             - refine / free / fixed (optional): boolean state controls.

        Selector whitelist:

        Global selectors:
        - dscale
        - qdamp
        - delta2
        - qbroad
        - delta1

        Phase selectors:
        - phase[i].pscale
        - phase[i].sratio
        - phase[i].spdiameter
        - phase[i].stepcut
        - phase[i].rcut
        - phase[i].lat.a (aliases: lat1, lat.a1, lata)
        - phase[i].lat.b (aliases: lat2, lat.b2, latb)
        - phase[i].lat.c (aliases: lat3, lat.c3, latc)

        Atom selectors:
        - phase[i].atom[j].x
        - phase[i].atom[j].y
        - phase[i].atom[j].z
        - phase[i].atom[j].occ
        - phase[i].atom[j].u11
        - phase[i].atom[j].u22
        - phase[i].atom[j].u33
        - phase[i].atom[j].u12
        - phase[i].atom[j].u13
        - phase[i].atom[j].u23

        Precedence rules:
        - Controls are executed in list order.
        - For one control item, state fields are applied in this order:
            refine -> free -> fixed.
            Therefore fixed, if present, overrides free/refine in the same item.
        - If a bound parameter has no initial/set, it is auto-initialized from
            current variable value.

        Examples:

        Example 1: refine only phase-1 pscale
        {
            "run_control": {"cycles": 1},
            "parameter_controls": [
                {"selector": "phase[1].pscale", "bind": "ps1", "refine": true}
            ]
        }

        Example 2: tie a=b=c and refine lattice + pscale
        {
            "run_control": {"cycles": 2},
            "parameter_controls": [
                {"selector": "phase[1].lat.a", "bind": "lat1", "refine": true},
                {"selector": "phase[1].lat.b", "bind": "lat1"},
                {"selector": "phase[1].lat.c", "bind": "lat1"},
                {"selector": "phase[1].pscale", "bind": "ps1", "refine": true}
            ]
        }

        Example 3: unbind and fix b lattice
        {
            "parameter_controls": [
                {"selector": "phase[1].lat.b", "unbind": true, "initial": 5.43, "fixed": true}
            ]
        }
    """

    results = {
        "status": "failed"
    }
    if task_id is None:
        results["error"] = "task_id is required"
        return results

    project_path_obj = Path(project_path)
    if not project_path_obj.exists():
        results["error"] = f"project_path 不存在: {project_path}"
        return results
    
    # 加载精修前状态
    with open(project_path_obj, "r", encoding="utf-8") as f:
        manifest = _normalize_project_manifest(json.load(f))
    
    # 覆盖式合并 refine_config，优先级：默认配置 < 项目 manifest 中的配置 < refine() 调用参数中的配置
    merged_refine_config = _merge_refine_configs(manifest.get("refine_config"), refine_config)

    # 获取manifest中指定的data_file和structure_files路径
    data_file = manifest.get("data_file")
    structure_files = manifest.get("structure_files")
    if not data_file or not structure_files:
        results["error"] = "project manifest 缺少 data_file 或 structure_files"
        return results

    project_dir_path = project_path_obj.parent
    output_dir = str(project_dir_path)

    params_current = merged_refine_config.get("paramsCurrentValue", {}) if isinstance(merged_refine_config.get("paramsCurrentValue"), dict) else {}
    run_control = merged_refine_config.get("run_control", {}) if isinstance(merged_refine_config.get("run_control"), dict) else {}

    start_time = datetime.now()

    qmax = float(params_current.get("qmax", 25.0))
    qdamp = float(params_current.get("qdamp", 0.001))
    rmin = float(params_current.get("rmin", 1.0))
    rmax = float(params_current.get("rmax", 20.0))
    pdf_source = _normalize_pdf_source(params_current.get("pdf_source", "N"))

    pf = PdfFit()
    pf.read_data(data_file, pdf_source, qmax, qdamp)

    # pf中导入相信息，兼容 CIF 和非 CIF（例如 .rstr）
    _load_structure_files(pf, list(structure_files))

    num_phases = len(structure_files)
    phase_indices = _build_phase_param_indices(num_phases)


    # 全局参数约束和初始值设置
    pf.constrain(pf.dscale(), "@20")
    pf.setpar(20, float(params_current.get("dscale", params_current.get("dataScale", 1.0))))
    pf.constrain(pf.qdamp(), "@21")
    pf.setpar(21, float(params_current.get("qdamp", qdamp)))
    pf.constrain(pf.delta2(), "@22")
    pf.setpar(22, float(params_current.get("delta2", 0.0003)))
    pf.constrain(pf.qbroad(), "@23")
    pf.setpar(23, float(params_current.get("qbroad", 0.0)))
    pf.constrain(pf.delta1(), "@24")
    pf.setpar(24, float(params_current.get("delta1", 0.0)))

    pf.fixpar(20)
    pf.fixpar(21)
    pf.fixpar(22)
    pf.fixpar(23)
    pf.fixpar(24)

    phase_runtime_vars: Dict[int, Dict[str, Any]] = {}

    # 每相参数约束和初始值设置
    for phase_idx in range(1, num_phases + 1):
        pf.setphase(phase_idx)
        pmap = phase_indices[phase_idx]
        lat_a = pf.lat(1)
        lat_b = pf.lat(2)
        lat_c = pf.lat(3)
        pf.constrain(lat_a, f"@{pmap['lattice']}")
        pf.constrain(lat_b, f"@{pmap['lattice']}")
        pf.constrain(lat_c, f"@{pmap['lattice']}")
        pf.setpar(pmap["lattice"], float(pf.getvar(lat_a)))
        pf.fixpar(pmap["lattice"])

        num_atoms = len(pf.get_structure(phase_idx))
        for atom_idx in range(1, num_atoms + 1):
            pf.constrain(pf.u11(atom_idx), f"@{pmap['temperature']}")
            pf.constrain(pf.u22(atom_idx), f"@{pmap['temperature']}")
            pf.constrain(pf.u33(atom_idx), f"@{pmap['temperature']}")
        default_temp = float(pf.getvar(pf.u11(1))) if num_atoms >= 1 else 0.005
        pf.setpar(pmap["temperature"], default_temp)
        pf.fixpar(pmap["temperature"])

        scale_var = pf.pscale()
        pf.constrain(scale_var, f"@{pmap['scale']}")
        pf.setpar(pmap["scale"], float(1.0 / max(num_phases, 1)))
        pf.fixpar(pmap["scale"])

        phase_runtime_vars[phase_idx] = {
            "lat_a": lat_a,
            "lat_b": lat_b,
            "lat_c": lat_c,
            "scale": scale_var,
            "u11_first": pf.u11(1) if num_atoms >= 1 else None,
        }

        sratio_bound = _safe_constrain(pf, pf.sratio(), f"@{pmap['sratio']}")
        if sratio_bound:
            pf.setpar(pmap["sratio"], 1.0)
            pf.fixpar(pmap["sratio"])

        spdiameter_bound = _safe_constrain(pf, pf.spdiameter(), f"@{pmap['spdiameter']}")
        if spdiameter_bound:
            pf.setpar(pmap["spdiameter"], 0.0)
            pf.fixpar(pmap["spdiameter"])

        stepcut_bound = _safe_constrain(pf, pf.stepcut(), f"@{pmap['stepcut']}")
        if stepcut_bound:
            pf.setpar(pmap["stepcut"], 0.0)
            pf.fixpar(pmap["stepcut"])

        rcut_bound = _safe_constrain(pf, pf.rcut(), f"@{pmap['rcut']}")
        if rcut_bound:
            pf.setpar(pmap["rcut"], 0.0)
            pf.fixpar(pmap["rcut"])

    parameter_controls = merged_refine_config.get("parameter_controls")
    _apply_parameter_controls(pf, parameter_controls, start_param_idx=20000)

    pf.pdfrange(1, rmin, rmax)

    cycles = max(int(run_control.get("cycles", 1) or 1), 1)
    try:
        for _ in range(cycles):
            pf.refine()
    except Exception as e:
        results["error"] = f"refinement failed: {e}"
        results["traceback"] = traceback.format_exc()
        return {
            "status": "failed",
            "task_id": task_id,
            "project_path": str(project_path_obj),
            "source_project_path": str(project_path_obj),
            "project_dir": output_dir,
            "error": results["error"],
            "traceback": results["traceback"],
            "content": results,
        }

    stage_results: List[Dict[str, Any]] = [
        {
            "stage": "manual_single_step",
            "cycles": cycles,
            "rw": pf.getrw(),
        }
    ]

    refined_params: Dict[str, Any] = {
        "dataScale": pf.getpar(20),
        "pscale": pf.getpar(20) * 2,
        "qdamp": pf.getpar(21),
        "delta2": pf.getpar(22),
        "qbroad": pf.getpar(23),
        "delta1": pf.getpar(24),
        "rw": pf.getrw(),
        "stage_results": stage_results,
        "elapsed_sec": round((datetime.now() - start_time).total_seconds(), 3),
    }

    for phase_idx in range(1, num_phases + 1):
        pmap = phase_indices[phase_idx]
        runtime = phase_runtime_vars.get(phase_idx, {})
        lat_a_var = runtime.get("lat_a")
        lat_b_var = runtime.get("lat_b")
        lat_c_var = runtime.get("lat_c")
        scale_var = runtime.get("scale")
        temp_var = runtime.get("u11_first")

        lat_a_val = float(pf.getvar(lat_a_var)) if lat_a_var is not None else float(pf.getpar(pmap["lattice"]))
        lat_b_val = float(pf.getvar(lat_b_var)) if lat_b_var is not None else lat_a_val
        lat_c_val = float(pf.getvar(lat_c_var)) if lat_c_var is not None else lat_a_val
        scale_val = float(pf.getvar(scale_var)) if scale_var is not None else float(pf.getpar(pmap["scale"]))
        temp_val = float(pf.getvar(temp_var)) if temp_var is not None else float(pf.getpar(pmap["temperature"]))

        refined_params[f"lattice_parameter_{phase_idx}"] = lat_a_val
        refined_params[f"lattice_a_{phase_idx}"] = lat_a_val
        refined_params[f"lattice_b_{phase_idx}"] = lat_b_val
        refined_params[f"lattice_c_{phase_idx}"] = lat_c_val
        refined_params[f"temperature_factor_{phase_idx}"] = temp_val
        refined_params[f"scale_{phase_idx}"] = scale_val
        refined_params[f"sratio_{phase_idx}"] = _safe_getpar(pf, pmap["sratio"])
        refined_params[f"spdiameter_{phase_idx}"] = _safe_getpar(pf, pmap["spdiameter"])
        refined_params[f"stepcut_{phase_idx}"] = _safe_getpar(pf, pmap["stepcut"])
        refined_params[f"rcut_{phase_idx}"] = _safe_getpar(pf, pmap["rcut"])

    params_current_after = dict(params_current)
    params_current_after["dscale"] = float(refined_params["dataScale"])
    params_current_after["qdamp"] = float(refined_params["qdamp"])
    params_current_after["delta2"] = float(refined_params["delta2"])
    params_current_after["qbroad"] = float(refined_params["qbroad"])
    params_current_after["delta1"] = float(refined_params["delta1"])
    params_current_after["pdf_source"] = _normalize_pdf_source(params_current_after.get("pdf_source", "N"))

    output_prefix = Path(data_file).stem
    output_files = {
        "pdf": str(project_dir_path / f"{output_prefix}.fgr"),
        "results": str(project_dir_path / f"{output_prefix}.res"),
        "plot": str(project_dir_path / f"{output_prefix}.png"),
        "log": str(project_dir_path / f"{output_prefix}_log.txt"),
        "refined_structures": [],
        "refined_cif_files": [],
    }

    pf.save_pdf(1, output_files["pdf"])
    pf.save_res(output_files["results"])

    for phase_idx, original_structure in enumerate(list(structure_files), start=1):
        refined_structure_path = str(project_dir_path / f"{output_prefix}_phase{phase_idx}.rstr")
        pf.save_struct(phase_idx, refined_structure_path)
        output_files["refined_structures"].append(refined_structure_path)

        if original_structure.lower().endswith(".cif"):
            refined_cif_name = get_refined_cif_filename(original_structure)
            refined_cif_path = str(project_dir_path / refined_cif_name)
            if _update_cif_with_refined_params(original_structure, refined_params, refined_cif_path, phase_idx):
                output_files["refined_cif_files"].append(refined_cif_path)
                output_files[f"refined_cif_phase{phase_idx}"] = refined_cif_path

    output_files["plot"] = _plot_refinement_result(
        pf,
        Path(output_files["plot"]),
        title=f"PDF Refinement - Rw: {refined_params['rw']:.4f}",
    )

    with open(output_files["log"], "w", encoding="utf-8") as f:
        f.write("PDF refinement results\n")
        f.write("=" * 60 + "\n")
        f.write(f"data_file: {data_file}\n")
        f.write(f"pdf_source: {pdf_source}\n")
        f.write(f"qmax: {qmax}\n")
        f.write(f"qdamp: {qdamp}\n")
        f.write(f"rmin: {rmin}\n")
        f.write(f"rmax: {rmax}\n")
        f.write(f"rw: {refined_params['rw']:.6f}\n\n")
        f.write("stage_results:\n")
        for stage in stage_results:
            f.write(json.dumps(stage, ensure_ascii=False) + "\n")
        f.write("\nrefined_params:\n")
        f.write(json.dumps(refined_params, ensure_ascii=False, indent=2) + "\n")

    manifest_out = {
        "manifest_version": 1,
        "data_file": data_file,
        "structure_files": list(output_files["refined_structures"] or structure_files),
        "refine_config": merged_refine_config,
        "output_files": output_files,
        "refined_params": refined_params,
    }
    manifest_out["refine_config"]["paramsCurrentValue"] = dict(params_current_after)

    next_manifest_path = _make_next_manifest_path(project_path_obj, task_id)
    _safe_json_dump(next_manifest_path, _normalize_project_manifest(manifest_out))

    results["output_files"] = output_files
    results["refined_params"] = refined_params
    results["project_manifest"] = str(next_manifest_path)

    return {
        "status": "success",
        "task_id": task_id,
        "project_path": str(next_manifest_path),
        "source_project_path": str(project_path_obj),
        "project_dir": output_dir,
        "refined_params": results["refined_params"],
        "output_files": results["output_files"],
        "summary": f"PDF refinement completed with Rw={results['refined_params'].get('rw', 0.0):.6f}",
        "content": results,
    }


def export_cifs(
    project_path: str,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    mission_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect the files referenced by a refinement snapshot.

    This export step does not recompute refinement. It reads the JSON snapshot
    at `project_path`, resolves the output file paths recorded in that snapshot,
    and returns the files that currently exist on disk. In the snapshot-chain
    workflow, this is the simplest way to retrieve CIF or other refined outputs
    after a refinement run.

    Args:
        project_path: Path to a refinement snapshot JSON file.
        task_id: Sub-task identifier used by the caller.
        user_id: Unused in the snapshot-chain model, kept for tool shape.
        session_id: Unused in the snapshot-chain model, kept for tool shape.
        mission_name: Unused in the snapshot-chain model, kept for tool shape.

    Returns:
        A dictionary describing the exported files.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: snapshot that was read
        - `files`: list of existing output files
        - `total_size_bytes`: combined size of exported files
        - `refined_params`: refined values stored in the snapshot
        - `summary`: short human-readable description
    """

    if task_id is None:
        raise ValueError("task_id is required")

    project_path_obj = Path(project_path)
    if not project_path_obj.exists():
        raise FileNotFoundError(f"project_path 不存在: {project_path}")

    manifest = _normalize_project_manifest(_safe_json_load(project_path_obj))
    output_files = manifest.get("output_files", {})
    refined_params = manifest.get("refined_params", {})

    if not output_files:
        raise ValueError("项目尚未完成 refine，无法导出文件")

    files: List[Dict[str, Any]] = []
    total_size = 0
    for key, value in output_files.items():
        if isinstance(value, list):
            for item in value:
                if Path(item).exists():
                    size = calcu_byte_size(item)
                    total_size += size
                    files.append({
                        "type": key,
                        "path": item,
                        "size_bytes": size,
                    })
        elif isinstance(value, str) and Path(value).exists():
            size = calcu_byte_size(value)
            total_size += size
            files.append({
                "type": key,
                "path": value,
                "size_bytes": size,
            })

    return {
        "status": "success",
        "task_id": task_id,
        "project_path": str(project_path_obj),
        "files": files,
        "total_size_bytes": total_size,
        "refined_params": refined_params,
        "summary": "PDF outputs collected from the refinement project.",
    }

if __name__ == "__main__":
    initialize_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1.0,
        target_func=initialize_pdf_project,
    )

    refine_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1.0,
        target_func=refine,
    )

    export_cifs_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1.0,
        target_func=export_cifs,
    )

    mcp = FastMCP(
        name="PDFRefinementTools",
        instructions=(
            "Tools for PDF refinement."
            "Use InitializeProject to create a persistent project manifest, Refine to run staged refinement, "
            "and ExportCIFs to collect the generated outputs. "
            "Mission/task rule: mission_name identifies one high-level mission and should stay the same across multiple tool calls; "
            "task_id identifies a sub-task/step inside that mission."
        ),
        port=42502,
    )

    initialize_runner.register_mcp_tool(mcp, name="InitializeProject")
    refine_runner.register_mcp_tool(mcp, name="Refine")
    export_cifs_runner.register_mcp_tool(mcp, name="ExportCIFs")
    mcp.run(transport="sse")

    # pdf_file = "/home/wangh/rongzai-agent/datasets/BL16/RUNdataFiles/RUN0023568/Gr_Hermite.txt"
    # cif_files = ["/home/wangh/rongzai-agent/datasets/BL16/RUNdataFiles/RUN0023568/cifFiles/Si.cif"]
    # user_id = "wanghao96"
    # session_id = "local_pdf_refine_demo"
    # mission_name = "si_pdf_refine"

    # init_result = initialize_pdf_project(
    #     data_file=pdf_file,
    #     structure_files=cif_files,
    #     qmax=25.0,
    #     rmin=1.0,
    #     rmax=15.0,
    #     pdf_source="N",
    #     user_id=user_id,
    #     session_id=session_id,
    #     mission_name=mission_name,
    #     task_id="init",
    # )
    # print("\n===== InitializeProject =====")
    # print(f"status: {init_result.get('status')}")
    # print(f"project_path: {init_result.get('project_path')}")
    # if init_result.get("status") != "success":
    #     print(f"error: {init_result.get('error')}")
    #     raise SystemExit(1)
    # else:
    #     source_project = init_result["project_path"]

    #     # Case 1: pscale only
    #     refine_result = refine(
    #         project_path=init_result["project_path"],
    #         refine_config={
    #             "run_control": {"cycles": 1},
    #             "parameter_controls": [
    #                 {
    #                     "selector": "phase[1].pscale",
    #                     "bind": "ps1",
    #                     "refine": True,
    #                 }
    #             ],
    #         },
    #         user_id=user_id,
    #         session_id=session_id,
    #         mission_name=mission_name,
    #         task_id="refine_pscale_only",
    #     )
    #     print("\n===== Refine (Si pscale only) =====")
    #     print(f"status: {refine_result.get('status')}")
    #     print(f"project_path: {refine_result.get('project_path')}")
    #     if refine_result.get("status") != "success":
    #         print(f"error: {refine_result.get('error')}")
    #         raise SystemExit(1)
    #     else:
    #         rp = refine_result.get("refined_params", {})
    #         print(f"rw: {rp.get('rw')}")
    #         print(f"scale_1: {rp.get('scale_1')}")
    #         print(f"lattice_parameter_1: {rp.get('lattice_parameter_1')}")

    #         rw = float(rp.get("rw", 9e9))
    #         scale_1 = float(rp.get("scale_1", -1.0))
    #         if not (0.0 < rw < 0.6):
    #             raise RuntimeError(f"Case1 failed: unexpected rw={rw}")
    #         if not (0.0 < scale_1 < 2.0):
    #             raise RuntimeError(f"Case1 failed: unexpected scale_1={scale_1}")

    #     # Case 2: lattice tied (a=b=c) + pscale + delta2 released
    #     refine_result_2 = refine(
    #         project_path=source_project,
    #         refine_config={
    #             "run_control": {"cycles": 2},
    #             "parameter_controls": [
    #                 {
    #                     "selector": "phase[1].lat.a",
    #                     "bind": "lat1",
    #                     "refine": True,
    #                 },
    #                 {
    #                     "selector": "phase[1].lat.b",
    #                     "bind": "lat1",
    #                 },
    #                 {
    #                     "selector": "phase[1].lat.c",
    #                     "bind": "lat1",
    #                 },
    #                 {
    #                     "selector": "phase[1].pscale",
    #                     "bind": "ps1",
    #                     "refine": True,
    #                 },
    #                 {
    #                     "selector": "delta2",
    #                     "bind": "d21",
    #                     "initial": 0.0003,
    #                     "refine": True,
    #                 },
    #             ],
    #         },
    #         user_id=user_id,
    #         session_id=session_id,
    #         mission_name=mission_name,
    #         task_id="refine_lattice_and_pscale",
    #     )

    #     print("\n===== Refine (lattice + pscale + delta2) =====")
    #     print(f"status: {refine_result_2.get('status')}")
    #     print(f"project_path: {refine_result_2.get('project_path')}")
    #     if refine_result_2.get("status") != "success":
    #         print(f"error: {refine_result_2.get('error')}")
    #         raise SystemExit(1)
    #     else:
    #         rp2 = refine_result_2.get("refined_params", {})
    #         print(f"rw: {rp2.get('rw')}")
    #         print(f"scale_1: {rp2.get('scale_1')}")
    #         print(f"delta2: {rp2.get('delta2')}")
    #         print(f"lattice_a_1: {rp2.get('lattice_a_1')}")
    #         print(f"lattice_b_1: {rp2.get('lattice_b_1')}")
    #         print(f"lattice_c_1: {rp2.get('lattice_c_1')}")

    #         rw2 = float(rp2.get("rw", 9e9))
    #         la = float(rp2.get("lattice_a_1", 0.0))
    #         lb = float(rp2.get("lattice_b_1", 0.0))
    #         lc = float(rp2.get("lattice_c_1", 0.0))
    #         if not (0.0 < rw2 < 0.14):
    #             raise RuntimeError(f"Case2 failed: expected rw<0.14, got rw={rw2}")
    #         if max(abs(la - lb), abs(lb - lc), abs(la - lc)) > 1e-6:
    #             raise RuntimeError(f"Case2 failed: lattice not tied, a/b/c=({la}, {lb}, {lc})")

    #     print("\nAll inline refine tests passed.")
    