#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF refinement tools organized like the neutron GSAS-II tool suite.

This module exposes three polling MCP tools:
- InitializeProject: validate inputs, run structure checks, and create a project manifest
- Refine: run staged PDF refinement and write outputs
- ExportCIFs: collect or regenerate refined CIF outputs from a project

The architecture intentionally mirrors the GSAS-II neutron tool pattern:
pure business functions + ProcessPollingMcpToolBase runners + MCP registration.
"""

from __future__ import annotations

import json
import math
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
PDFGUI_TOLERANCY = 0.001


def calcu_byte_size(file_path: str | Path) -> int:
    return Path(file_path).stat().st_size

def _preprocess_pdf_data_for_pdffit(
    data_file: str | Path,
    output_dir: Optional[str | Path] = None,
) -> Path:
    """Convert mixed-format PDF data into pdffit2-friendly whitespace text.

    Accepted input lines may be whitespace or comma separated. Non-data rows
    (headers/comments/single-number rows) are skipped.
    """

    src = Path(data_file)
    if not src.exists():
        raise FileNotFoundError(f"数据文件不存在: {src}")

    dst_dir = Path(output_dir) if output_dir is not None else src.parent
    dst_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    dst = dst_dir / f"{src.stem}__pdffit_cleaned__{stamp}.dat"

    cleaned_rows: List[tuple[float, float]] = []
    with src.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("#", "!", ";")):
                continue

            parts = [p for p in re.split(r"[\s,]+", stripped) if p]
            if len(parts) < 2:
                continue

            try:
                r_val = float(parts[0])
                g_val = float(parts[1])
            except ValueError:
                continue

            cleaned_rows.append((r_val, g_val))

    if len(cleaned_rows) < 10:
        raise ValueError(
            f"清洗后有效数据点过少({len(cleaned_rows)}): {src}，请检查原始数据格式"
        )

    with dst.open("w", encoding="utf-8") as f:
        f.write("# r  G(r)\n")
        for r_val, g_val in cleaned_rows:
            f.write(f"{r_val:.10g} {g_val:.10g}\n")

    return dst


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
        "validation_warnings": manifest.get("validation_warnings") if isinstance(manifest.get("validation_warnings"), list) else [],
        "structure_checks": manifest.get("structure_checks") if isinstance(manifest.get("structure_checks"), list) else [],
    }


def _try_parse_cif_numeric(token: str) -> Optional[float]:
    text = str(token).strip().strip("'\"")
    if not text or text in {"?", "."}:
        return None
    if "(" in text:
        text = text.split("(", 1)[0]
    try:
        return float(text)
    except ValueError:
        return None


def _inspect_cif_adp(cif_path: str | Path) -> Dict[str, Any]:
    """Inspect ADP fields in CIF and report warning-level issues."""

    lines = Path(cif_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    adp_prefixes = (
        "_atom_site_u_iso_or_equiv",
        "_atom_site_b_iso_or_equiv",
        "_atom_site_aniso_u_",
        "_atom_site_aniso_b_",
    )

    adp_values: List[float] = []
    has_adp_field = False
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped.lower() != "loop_":
            idx += 1
            continue

        headers: List[str] = []
        j = idx + 1
        while j < len(lines):
            candidate = lines[j].strip()
            if candidate.startswith("_"):
                headers.append(candidate)
                j += 1
                continue
            break

        if not headers:
            idx += 1
            continue

        adp_indices: List[int] = []
        for pos, header in enumerate(headers):
            lower = header.lower()
            if any(lower.startswith(prefix) for prefix in adp_prefixes):
                adp_indices.append(pos)
        if adp_indices:
            has_adp_field = True

        while j < len(lines):
            record = lines[j].strip()
            if not record or record == "loop_" or record.startswith("_") or record.lower().startswith("data_"):
                break
            parts = record.split()
            if len(parts) >= len(headers):
                for pos in adp_indices:
                    maybe_value = _try_parse_cif_numeric(parts[pos])
                    if maybe_value is not None:
                        adp_values.append(float(maybe_value))
            j += 1

        idx = j

    nonzero_count = sum(1 for v in adp_values if abs(v) > 1e-12)
    all_zero = bool(adp_values) and nonzero_count == 0

    issues: List[str] = []
    if not has_adp_field:
        issues.append("未检测到 ADP 字段(_atom_site_Uiso/Biso 或 _atom_site_aniso_U/B_*)")
    elif not adp_values:
        issues.append("检测到 ADP 字段但未读到有效数值")
    elif all_zero:
        issues.append("检测到所有 ADP 数值均为 0，可能导致拟合退化为近似直线")

    return {
        "file": str(cif_path),
        "format": "cif",
        "has_adp_field": has_adp_field,
        "adp_value_count": len(adp_values),
        "nonzero_adp_count": nonzero_count,
        "all_zero_adp": all_zero,
        "issues": issues,
        "status": "warning" if issues else "ok",
    }


def _inspect_structure_file(structure_file: str | Path) -> Dict[str, Any]:
    path = Path(structure_file)
    suffix = path.suffix.lower()
    if suffix == ".cif":
        return _inspect_cif_adp(path)
    return {
        "file": str(path),
        "format": suffix.lstrip("."),
        "status": "skipped",
        "issues": ["仅对 CIF 执行 ADP 检测"],
    }


def _make_next_manifest_path(source_path: Path, task_id: str) -> Path:
    safe_task_id = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in str(task_id))
    return source_path.with_name(f"{safe_task_id}.json")


def create_output_directory(path: str | Path) -> Path:
    output_dir = Path(path).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _convert_refined_structure_to_cif(
    refined_structure_path: str | Path,
    output_cif_path: str | Path,
) -> bool:
    """Convert a refined structure file (typically .rstr / pdffit) to CIF."""

    src = Path(refined_structure_path)
    dst = Path(output_cif_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Preferred path: parse pdffit structure and write CIF.
    try:
        parser = getParser("pdffit")
        structure = parser.parseFile(str(src))
        dst.write_text(structure.writeStr("cif"), encoding="utf-8")
        return True
    except Exception:
        pass

    # Fallback path: rely on Structure I/O auto-detection.
    try:
        structure = Structure()
        structure.read(str(src))
        dst.write_text(structure.writeStr("cif"), encoding="utf-8")
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

    if isinstance(override_config.get("parameter_controls"), list):
        merged["parameter_controls"] = list(override_config["parameter_controls"])

    for key, value in override_config.items():
        if key not in {"paramsCurrentValue", "parameter_controls"}:
            merged[key] = value

    return merged


def _safe_getpar(pf: PdfFit, idx: int, default: Any = None) -> Any:
    try:
        return pf.getpar(idx)
    except Exception:
        return default


def _is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


_PHASE_SELECTOR_RE = re.compile(r"^phase\[(\d+)\]\.(.+)$", re.IGNORECASE)
_ATOM_SELECTOR_RE = re.compile(r"^atom\[(\d+)\]\.(x|y|z|occ|u11|u22|u33|u12|u13|u23)$", re.IGNORECASE)
_RETAINED_GLOBAL_SELECTORS = {"dscale", "qdamp", "qbroad"}
_RETAINED_PHASE_KEYS = {"pscale", "delta1", "delta2", "sratio", "spdiameter"}


def _is_retained_refinable_selector(selector: str) -> bool:
    s = str(selector or "").strip().lower()
    if not s:
        return False
    if s in _RETAINED_GLOBAL_SELECTORS:
        return True
    m = _PHASE_SELECTOR_RE.match(s)
    if not m:
        return False
    return m.group(2).strip().lower() in _RETAINED_PHASE_KEYS


def _collect_retained_refined_values(pf: PdfFit, num_phases: int) -> Dict[str, float]:
    values: Dict[str, float] = {
        "dscale": float(pf.getvar(pf.dscale)),
        "qdamp": float(pf.getvar(pf.qdamp)),
        "qbroad": float(pf.getvar(pf.qbroad)),
    }
    for phase_idx in range(1, num_phases + 1):
        pf.setphase(phase_idx)
        values[f"phase[{phase_idx}].pscale"] = float(pf.getvar(pf.pscale()))
        values[f"phase[{phase_idx}].delta1"] = float(pf.getvar(pf.delta1()))
        values[f"phase[{phase_idx}].delta2"] = float(pf.getvar(pf.delta2()))
        values[f"phase[{phase_idx}].sratio"] = float(pf.getvar(pf.sratio()))
        values[f"phase[{phase_idx}].spdiameter"] = float(pf.getvar(pf.spdiameter()))
    return values


def _resolve_pf_variable(pf: PdfFit, selector: str):
    text = str(selector or "").strip()
    if not text:
        raise ValueError("Empty parameter selector")

    key = text.lower()
    global_vars = {
        "dscale": pf.dscale,
        "qdamp": pf.qdamp,
        "qbroad": pf.qbroad,
    }
    if key in global_vars:
        return global_vars[key]()

    m = _PHASE_SELECTOR_RE.match(text)
    if not m:
        raise ValueError(f"Unsupported selector: {selector}")

    phase_idx = int(m.group(1))
    phase_key = m.group(2).strip().lower()
    pf.setphase(phase_idx)

    if phase_key in {"pscale", "sratio", "spdiameter", "stepcut", "rcut", "delta1", "delta2"}:
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


def initialize_pdf_project(
    data_file,
    structure_files,
    qmax = 25.0,
    rmin = 1.0,
    rmax = 20.0,
    pdf_source = "N",
    user_id = None,
    mission_name = None,
    task_id = None,
) -> Dict[str, Any]:
    """Create the first immutable PDF refinement project snapshot file (.json).

    Args:
        data_file: Experimental PDF data file.
        structure_files: Initial structure files, usually CIF files.
        qmax: Q cutoff used when reading PDF data.
        rmin: Lower r-range used for refinement.
        rmax: Upper r-range used for refinement.
        pdf_source: PDF source type, `X` or `N`. 'X'means X-ray, 'N' means neutron.
        user_id: User namespace used to build the project directory.
        mission_name: Mission namespace used to build the project directory and task polling.
        task_id: task name under mission for the purpose of naming the project file and for task polling.

    the content of the project snapshot file is a JSON object with the following fields:
        - `data_file`: path to the experimental PDF data file
        - `structure_files`: list of paths to the initial structure files
        - `nonRefineValue`: dictionary of global non-refinable parameters. These include qmax/rmin/rmax/pdf_source and per-phase phase[i].stepcut/phase[i].rcut.
        - `retainedRefinedValue`: dictionary of refinable but non-structural parameters that should be retained across snapshots.
            These include dscale/qdamp/qbroad and per-phase phase[i].pscale/delta1/delta2/sratio/spdiameter.
        - `refine_config`: dictionary of constraints, refinable parameters and their values after refinement. They are initial value in this snapshot, and will be updated after each refinement.
            [{"constraint_label": "label1", "params": ["param1", "param2"], "value": value1}, ..., {"constraint_label": "labelN", "params": ["paramX", "paramY"], "value": valueN}]
            Note: the parameters in the same label will be bound together and keep same in refinement, so they share one value. The same parameter can't be bound to multiple labels.
        - `output_files`: dictionary of output files generated by the refinement process, initially empty
        - `validation_warnings`: list of warning messages generated during structure validation
        - `structure_checks`: list of per-file validation details, including detected issues and status
    
    
    
    Returns:
        A dictionary with the initial project result.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: path to the created JSON project snapshot file
        - `project_content`: the project snapshot content written to disk
        - `has_warnings`: whether structure validation produced warnings
        - `warnings`: warning message list
        - `structure_checks`: structured per-file validation details
        - `error`: error message when `status` is `failed`
        - `traceback`: debug traceback when `status` is `failed`
    """
    results = {
        "status": "failed"}

    try:
        project_dir = DEFAULT_WORKDIR / user_id / mission_name
        path = Path(project_dir)
        path.mkdir(parents=True, exist_ok=True)

        if not Path(data_file).exists():
            raise FileNotFoundError(f"数据文件不存在: {data_file}")
        for i, struct_file in enumerate(structure_files, start=1):
            if not Path(struct_file).exists():
                raise FileNotFoundError(f"结构文件 {i} 不存在: {struct_file}")

        # 检查结构文件的 ADP 字段，收集警告信息
        structure_checks: List[Dict[str, Any]] = []
        validation_warnings: List[str] = []
        for struct_file in structure_files:
            check = _inspect_structure_file(struct_file)
            structure_checks.append(check)
            for issue in check.get("issues", []):
                validation_warnings.append(f"{check.get('file')}: {issue}")
            
        # 创建项目 JSON 文件并保存
        project_path = str(project_dir / f"{task_id}.json")
        nonRefineValue = {
            "qmax": qmax,
            "rmin": rmin,
            "rmax": rmax,
            "pdf_source": pdf_source
        }
        for phase_idx, _ in enumerate(structure_files, start=1):
            nonRefineValue[f"phase[{phase_idx}].stepcut"] = 0.0
            nonRefineValue[f"phase[{phase_idx}].rcut"] = 0.0
        
        manifest = {
            "data_file": data_file,
            "structure_files": structure_files,
            "nonRefineValue": nonRefineValue,
            "retainedRefinedValue": {},
            "refine_config": [],
            "output_files": {},
            "validation_warnings": validation_warnings,
            "structure_checks": structure_checks,
        }
        Path(project_path).parent.mkdir(parents=True, exist_ok=True)
        with open(Path(project_path), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        results["status"] = "success"
        results["project_path"] = project_path
        results["project_content"] = manifest
        results["has_warnings"] = bool(validation_warnings)
        results["warnings"] = list(validation_warnings)
        results["structure_checks"] = list(structure_checks)
        return results
    
    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results


def refine(
    project_path: str,
    deleteRefineConstraint: Optional[List[str]] = None,
    deleteRefineParam: Optional[List[str]] = None,
    addRefineParam: Optional[Dict[str, Any]] = None,
    setParam: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Args:
        - project_path: Path to the JSON project snapshot file.
        - deleteRefineConstraint: Optional list of constraint labels to remove from refinement.
            ['label1','label2',...,'labelN']
        - deleteRefineParam: Optional list of parameter selectors to remove from refinement.
            ['param1','param2',...,'paramN']
        - addRefineParam: Optional dictionary to add new refinement parameters.
            {'label1': ['param1','param2',...,'paramN'],
             'label2': ['param1','param2',...,'paramN'], ...}
            the params with same constraint_label will be bound together and keep same in refinement.
            the same param can't be bound to multiple constraint_label.
        - setParam: Optional dictionary to set specific parameter values.
            {'param1': value1, 'param2': value2, ...}
            Note1: if a parameter in setParam is bound to any constraint, the other parameters bound to the same constraint will also be set to the same value.
            Note2: if a parameter in setParam is not bound to any constraint, it will be set to the specified value before refine and won't change during refine.
        - user_id: User namespace for project directory.
        - mission_name: Mission namespace for project directory and task polling.
        - task_id: Task identifier for naming output files and task polling.
        
    refine parameters white list (parameters can be refined) can be specified in deleteRefineParam,addRefineParam,.
        - Global Parameters:
            - dscale: data scale factor.
            - qdamp: PDF damping from instrument Q-resolution.
            - qbroad: quadratic peak broadening factor.
        - Phase Parameters:
            - phase[i].pscale: fraction of total structure represented by phase i.
            - phase[i].delta1: 1/R peak sharpening factor of phase i.
            - phase[i].delta2: (1/R^2) peak sharpening factor of phase i.
            - phase[i].sratio: sigma ratio for reduction of Debye-Waller factor below rcut.
            - phase[i].spdiameter: spherical particle correction diameter; 0 disables envelope.
            - phase[i].lat.a (aliases: lat1, lat.a1, lata): lattice parameter a.
            - phase[i].lat.b (aliases: lat2, lat.b2, latb): lattice parameter b.
            - phase[i].lat.c (aliases: lat3, lat.c3, latc): lattice parameter c.
        - Atom Parameters:
            - phase[i].atom[j].x: fractional x coordinate of atom j in phase i
            - phase[i].atom[j].y: fractional y coordinate of atom j in phase i
            - phase[i].atom[j].z: fractional z coordinate of atom j in phase i
            - phase[i].atom[j].occ: occupancy of atom j in phase i
            - phase[i].atom[j].u11: U(1,1) anisotropic thermal factor tensor component
            - phase[i].atom[j].u22: U(2,2) anisotropic thermal factor tensor component
            - phase[i].atom[j].u33: U(3,3) anisotropic thermal factor tensor component
            - phase[i].atom[j].u12: U(1,2) anisotropic thermal factor tensor component
            - phase[i].atom[j].u13: U(1,3) anisotropic thermal factor tensor component
            - phase[i].atom[j].u23: U(2,3) anisotropic thermal factor tensor component
    
    parameters can be specified in setParam.
        - all the parameters in refine parameters white list.
        - other parameters which can be specified in setParam are as follows. But they won't be refined.
            - Global Parameters:
                - qmax: float, Q cutoff in read_data.
                - rmin: float, fit lower bound.
                - rmax: float, fit upper bound.
                - pdf_source: str, "N" or "X".
            - Phase Parameters:
                - phase[i].stepcut: empirical step-envelope cutoff radius for amorphous-like correlation loss; non-refinable in pdffit2.
                - phase[i].rcut: radius below which sratio-based sharpening applies; non-refinable in pdffit2.
    
    Execution flow:
        1. Load snapshot from project_path.
        2. Read PDF data and structures.
        3. Read refine_config and change it. 
            - firstly: delete the constraints according to the deleteRefineConstraint.
            - secondly: delete the parameters according to the deleteRefineParam.
            - thirdly: add the parameters according to the addRefineParam.
            - fourthly: delete the constraints with empty parameters.
        4. Apply setParam and run pf.refine_step(tolerancy) repeatedly until convergence.
            - firstly: all parameters listed in refine_config participate in refinement.
            - secondly: determine initial value for each refine_config constraint group with this priority:
                setParam value on any member parameter > refine_config[group].value > fallback value.
            - thirdly: fallback rule for missing initial value:
                global parameters use default values; structure/atom parameters use current values from loaded structures.
            - fourthly: set all non-refinable parameters from setParam if provided, otherwise from nonRefineValue.
                These include qmax/rmin/rmax/pdf_source and per-phase phase[i].stepcut/phase[i].rcut.
            - fifthly: run pf.refine_step(tolerancy) repeatedly until convergence or max iteration reached.
            - sixthly: update refine_config values and nonRefineValue values from this run.
        5. Save outputs and write a new immutable snapshot.

    Usage examples:
        1) Add one constraint group and set initial values:
            refine(
                project_path="/path/to/project_init.json",
                addRefineParam={
                    "@1": ["phase[1].lat.a", "phase[1].lat.b", "phase[1].lat.c"],
                    "@2": ["phase[1].pscale"],
                },
                setParam={
                    "phase[1].lat.a": 5.431,
                    "phase[1].pscale": 0.95,
                },
                user_id="user123",
                mission_name="mission456",
                task_id="refine_lattice_scale",
            )
            the tool would firstly add two constraint groups, then set the initial values for the parameters, and finally run the refinement.

        2) Change non-refinable values and run refinement:
            refine(
                project_path="/path/to/project_prev.json",
                setParam={
                    "qmax": 27.0,
                    "rmin": 1.2,
                    "rmax": 18.0,
                    "pdf_source": "N",
                    "phase[1].stepcut": 0.0,
                    "phase[1].rcut": 0.0,
                },
                user_id="user123",
                mission_name="mission456",
                task_id="refine_range_override",
            )
            the tool would firstly override the non-refinable values then run the refinement by using the refine_config in the project snapshot. Finally it would update the nonRefineValue, value in refine_config, new refined structures in the new snapshot.

        3) Remove an existing constraint and parameter:
            refine(
                project_path="/path/to/project_prev.json",
                deleteRefineConstraint=["@1"],
                deleteRefineParam=["phase[1].delta2"],
                user_id="user123",
                mission_name="mission456",
                task_id="refine_remove_controls"
            )
            the tool would firstly remove the constraint group "@1" and the parameter "phase[1].delta2" from the refine_config, then run the refinement by using the updated refine_config in the project snapshot. Finally it would update the refine_config, new refined structures in the new snapshot.

        4) seperate a paramter from a constraint group and set its value:
            refine(
                project_path="/path/to/project_prev.json",
                deleteRefineParam=["phase[1].lat.c"],
                addRefineParam={
                    "@3": ["phase[1].lat.c"]
                },
                setParam={
                    "phase[1].lat.c": 5.431
                },
                user_id="user123",
                mission_name="mission456",
                task_id="refine_separate_param"
            )
            the tool would firstly remove the parameter "phase[1].lat.c" from its original constraint group, then add a new constraint group "@3" with the parameter "phase[1].lat.c", set its initial value to 5.431, and finally run the refinement by using the updated refine_config in the project snapshot. Finally it would update the refine_config, new refined structures in the new snapshot.
    
    returns:
        A dictionary with the refinement result.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: path to the updated JSON project snapshot file
        - `project_content`: the updated project snapshot content written to disk
        - `refined_structures`: list of refined structure file paths (.rstr)
        - `results_file`: dictionary with result file path and content (.res)
            {
            "path": "/path/to/result.res",
            "content": "...full res content..."
            }
        - `plot_file`: plot image path (.png)
        - `error`: error message when `status` is `failed`

        
        
    """

    results = {"status": "failed"}
    if task_id is None:
        results["error"] = "task_id is required"
        return results

    project_path_obj = Path(project_path)
    if not project_path_obj.exists():
        results["error"] = f"project_path 不存在: {project_path}"
        return results

    with open(project_path_obj, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    original_refine_config = manifest.get("refine_config", [])
    if not isinstance(original_refine_config, list):
        original_refine_config = []

    def _normalize_refine_config_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for item in groups:
            if not isinstance(item, dict):
                continue
            label = item.get("constraint_label")
            params = item.get("params")
            if label is None or not isinstance(params, list):
                continue

            label_text = str(label)
            if label_text not in merged:
                merged[label_text] = {
                    "constraint_label": label_text,
                    "params": [],
                }
                order.append(label_text)

            entry = merged[label_text]
            existing = set(entry["params"])
            for p in params:
                p_text = str(p).strip()
                if not p_text or p_text in existing:
                    continue
                entry["params"].append(p_text)
                existing.add(p_text)

            # Prefer the latest explicit value when duplicate labels are merged.
            if "value" in item and item.get("value") is not None:
                entry["value"] = item.get("value")

        return [merged[label] for label in order if merged[label].get("params")]

    refine_config: List[Dict[str, Any]] = _normalize_refine_config_groups(original_refine_config)

    if deleteRefineConstraint:
        to_delete = {str(v) for v in deleteRefineConstraint}
        refine_config = [c for c in refine_config if c.get("constraint_label") not in to_delete]

    if deleteRefineParam:
        to_delete_params = {str(v).strip() for v in deleteRefineParam if str(v).strip()}
        for constraint in refine_config:
            params = constraint.get("params", [])
            constraint["params"] = [p for p in params if p not in to_delete_params]

    if addRefineParam:
        for label, params in addRefineParam.items():
            if not isinstance(params, list):
                continue
            clean_params = [str(p).strip() for p in params if str(p).strip()]
            refine_config.append({"constraint_label": str(label), "params": clean_params})

    refine_config = _normalize_refine_config_groups(refine_config)

    seen_param_to_label: Dict[str, str] = {}
    for c in refine_config:
        label = str(c.get("constraint_label"))
        for p in c.get("params", []):
            if p in seen_param_to_label and seen_param_to_label[p] != label:
                results["error"] = f"参数 {p} 同时出现在多个约束组中: {seen_param_to_label[p]} 和 {label}"
                return results
            seen_param_to_label[p] = label

    data_file = manifest.get("data_file")
    structure_files = manifest.get("structure_files")
    if not data_file or not isinstance(structure_files, list) or not structure_files:
        results["error"] = "project manifest 缺少 data_file 或 structure_files"
        return results

    set_param = setParam if isinstance(setParam, dict) else {}
    non_refine_value = manifest.get("nonRefineValue", {}) if isinstance(manifest.get("nonRefineValue"), dict) else {}
    retained_refined_value_raw = manifest.get("retainedRefinedValue", {}) if isinstance(manifest.get("retainedRefinedValue"), dict) else {}
    retained_refined_value = {
        str(k).strip().lower(): v
        for k, v in retained_refined_value_raw.items()
    }

    updated_non_refine_value: Dict[str, Any] = dict(non_refine_value)
    qmax = float(set_param.get("qmax", updated_non_refine_value.get("qmax", 25.0)))
    rmin = float(set_param.get("rmin", updated_non_refine_value.get("rmin", 1.0)))
    rmax = float(set_param.get("rmax", updated_non_refine_value.get("rmax", 20.0)))
    pdf_source = _normalize_pdf_source(set_param.get("pdf_source", updated_non_refine_value.get("pdf_source", "N")))
    qdamp = float(set_param.get("qdamp", retained_refined_value.get("qdamp", 0.001)))

    updated_non_refine_value["qmax"] = qmax
    updated_non_refine_value["rmin"] = rmin
    updated_non_refine_value["rmax"] = rmax
    updated_non_refine_value["pdf_source"] = pdf_source

    output_dir = str(project_path_obj.parent)
    project_dir_path = project_path_obj.parent
    start_time = datetime.now()

    cleaned_data_file: Optional[Path] = None
    pf = PdfFit()
    try:
        cleaned_data_file = _preprocess_pdf_data_for_pdffit(data_file, output_dir=output_dir)
        pf.read_data(str(cleaned_data_file), pdf_source, qmax, qdamp)
    finally:
        if cleaned_data_file is not None:
            try:
                cleaned_data_file.unlink(missing_ok=True)
            except Exception:
                pass

    _load_structure_files(pf, list(structure_files))
    num_phases = len(structure_files)

    def _safe_getvar(pf_obj: PdfFit, var: Any, default: float = 0.0) -> float:
        try:
            return float(pf_obj.getvar(var))
        except Exception:
            return float(default)

    def _is_refine_whitelist_selector(selector: str) -> bool:
        s = str(selector or "").strip()
        if not s:
            return False
        global_allowed = {"dscale", "qdamp", "qbroad"}
        if s.lower() in global_allowed:
            return True
        m = _PHASE_SELECTOR_RE.match(s)
        if not m:
            return False
        phase_key = m.group(2).strip().lower()
        phase_allowed = {
            "pscale", "delta1", "delta2", "sratio", "spdiameter",
            "lat.a", "lat1", "lat.a1", "lata",
            "lat.b", "lat2", "lat.b2", "latb",
            "lat.c", "lat3", "lat.c3", "latc",
        }
        if phase_key in phase_allowed:
            return True
        return _ATOM_SELECTOR_RE.match(phase_key) is not None

    def _label_to_param_idx(label: str, next_idx: int) -> tuple[int, int]:
        text = str(label).strip()
        if text.startswith("@") and text[1:].lstrip("+-").isdigit():
            return int(text[1:]), next_idx
        if text.lstrip("+-").isdigit():
            return int(text), next_idx
        return next_idx, next_idx + 1

    label_to_params: Dict[str, List[str]] = {
        str(c.get("constraint_label")): list(c.get("params", [])) for c in refine_config
    }
    param_to_label: Dict[str, str] = {}
    for lbl, plist in label_to_params.items():
        for p in plist:
            param_to_label[p] = lbl

    label_param_idx: Dict[str, int] = {}
    next_group_idx = 30000
    for c in refine_config:
        label = str(c.get("constraint_label"))
        if label in label_param_idx:
            continue
        idx, next_group_idx = _label_to_param_idx(label, next_group_idx)
        label_param_idx[label] = idx

    default_global_init = {
        "dscale": 1.0,
        "qdamp": 0.001,
        "qbroad": 0.0,
    }

    initialized_group: set[str] = set()
    for c in refine_config:
        label = str(c.get("constraint_label"))
        params = list(c.get("params", []))
        if not params:
            continue

        param_idx = label_param_idx[label]
        group_value: Optional[float] = None
        for p in params:
            if p in set_param:
                group_value = float(set_param[p])
                break

        if group_value is None and "value" in c:
            try:
                group_value = float(c.get("value"))
            except (TypeError, ValueError):
                group_value = None

        if group_value is None:
            for p in params:
                p_key = str(p).strip().lower()
                if p_key in retained_refined_value:
                    try:
                        group_value = float(retained_refined_value[p_key])
                        break
                    except (TypeError, ValueError):
                        continue

        first_var = None
        for p in params:
            try:
                var = _resolve_pf_variable(pf, p)
            except Exception:
                continue
            if first_var is None:
                first_var = var
            pf.constrain(var, f"@{param_idx}")

        if first_var is None:
            continue

        if label not in initialized_group:
            if group_value is None:
                representative = str(params[0]).strip().lower()
                if representative in default_global_init:
                    group_value = float(default_global_init[representative])
                else:
                    group_value = _safe_getvar(pf, first_var, default=0.0)
            pf.setpar(param_idx, float(group_value))
            pf.freepar(param_idx)
            initialized_group.add(label)

    next_fixed_idx = 40000
    for key, value in set_param.items():
        selector = str(key).strip()
        if not _is_refine_whitelist_selector(selector):
            continue
        if selector in param_to_label:
            continue
        try:
            var = _resolve_pf_variable(pf, selector)
        except Exception:
            continue
        pf.constrain(var, f"@{next_fixed_idx}")
        pf.setpar(next_fixed_idx, float(value))
        pf.fixpar(next_fixed_idx)
        next_fixed_idx += 1

    for phase_idx in range(1, num_phases + 1):
        pf.setphase(phase_idx)
        for param_name in ("stepcut", "rcut"):
            selector = f"phase[{phase_idx}].{param_name}"
            raw_val = set_param.get(selector, updated_non_refine_value.get(selector, 0.0))
            value = float(raw_val)
            updated_non_refine_value[selector] = value
            try:
                var = getattr(pf, param_name)()
                pf.constrain(var, f"@{next_fixed_idx}")
                pf.setpar(next_fixed_idx, value)
                pf.fixpar(next_fixed_idx)
                next_fixed_idx += 1
            except Exception:
                pass

    pf.pdfrange(1, rmin, rmax)

    max_iterations = 200
    step_count = 0
    converged = False
    try:
        while step_count < max_iterations:
            finished = bool(pf.refine_step(PDFGUI_TOLERANCY))
            step_count += 1
            if finished:
                converged = True
                break
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

    for c in refine_config:
        label = str(c.get("constraint_label"))
        pidx = label_param_idx.get(label)
        if pidx is None:
            continue
        c["value"] = _safe_getpar(pf, pidx)

    updated_retained_refined_value: Dict[str, float] = {
        str(k).strip().lower(): float(v)
        for k, v in retained_refined_value.items()
        if _is_retained_refinable_selector(str(k))
    }
    try:
        updated_retained_refined_value.update(_collect_retained_refined_values(pf, num_phases))
    except Exception:
        pass

    stage_results: List[Dict[str, Any]] = [
        {
            "stage": "auto_converged" if converged else "max_iteration_reached",
            "steps": step_count,
            "max_iterations": max_iterations,
            "tolerancy": PDFGUI_TOLERANCY,
            "rw": pf.getrw(),
        }
    ]

    refined_params: Dict[str, Any] = {
        "rw": pf.getrw(),
        "stage_results": stage_results,
        "elapsed_sec": round((datetime.now() - start_time).total_seconds(), 3),
    }

    for phase_idx in range(1, num_phases + 1):
        pf.setphase(phase_idx)
        try:
            refined_params[f"lattice_a_{phase_idx}"] = _safe_getvar(pf, pf.lat(1))
            refined_params[f"lattice_b_{phase_idx}"] = _safe_getvar(pf, pf.lat(2))
            refined_params[f"lattice_c_{phase_idx}"] = _safe_getvar(pf, pf.lat(3))
            refined_params[f"lattice_parameter_{phase_idx}"] = refined_params[f"lattice_a_{phase_idx}"]
            refined_params[f"scale_{phase_idx}"] = _safe_getvar(pf, pf.pscale(), 1.0)
            refined_params[f"delta1_{phase_idx}"] = _safe_getvar(pf, pf.delta1(), 0.0)
            refined_params[f"delta2_{phase_idx}"] = _safe_getvar(pf, pf.delta2(), 0.0003)
            refined_params[f"sratio_{phase_idx}"] = _safe_getvar(pf, pf.sratio(), 1.0)
            refined_params[f"spdiameter_{phase_idx}"] = _safe_getvar(pf, pf.spdiameter(), 0.0)
            refined_params[f"stepcut_{phase_idx}"] = _safe_getvar(pf, pf.stepcut(), updated_non_refine_value.get(f"phase[{phase_idx}].stepcut", 0.0))
            refined_params[f"rcut_{phase_idx}"] = _safe_getvar(pf, pf.rcut(), updated_non_refine_value.get(f"phase[{phase_idx}].rcut", 0.0))
            n_atoms = len(pf.get_structure(phase_idx))
            refined_params[f"temperature_factor_{phase_idx}"] = _safe_getvar(pf, pf.u11(1), 0.0) if n_atoms >= 1 else 0.0
        except Exception:
            continue

    invalid_refined_params = {
        key: value
        for key, value in refined_params.items()
        if isinstance(value, (int, float)) and not _is_finite_number(value)
    }
    if invalid_refined_params:
        results["error"] = f"refinement diverged: non-finite refined parameters detected: {invalid_refined_params}"
        results["traceback"] = "Detected non-finite refined parameters before saving outputs."
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

    output_prefix = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in str(task_id))
    output_files = {
        "results": str(project_dir_path / f"{output_prefix}.res"),
        "plot": str(project_dir_path / f"{output_prefix}.png"),
        "refined_structures": [],
    }

    pf.save_res(output_files["results"])

    for phase_idx in range(1, num_phases + 1):
        refined_structure_path = str(project_dir_path / f"{output_prefix}_phase{phase_idx}.rstr")
        pf.save_struct(phase_idx, refined_structure_path)
        output_files["refined_structures"].append(refined_structure_path)

    output_files["plot"] = _plot_refinement_result(
        pf,
        Path(output_files["plot"]),
        title=f"PDF Refinement - Rw: {refined_params['rw']:.4f}",
    )

    with open(output_files["results"], "r", encoding="utf-8", errors="ignore") as f:
        results_content = f.read()

    manifest_out = {
        "data_file": data_file,
        "structure_files": list(output_files["refined_structures"] or structure_files),
        "nonRefineValue": updated_non_refine_value,
        "retainedRefinedValue": updated_retained_refined_value,
        "refine_config": refine_config,
        "output_files": output_files,
        "refined_params": refined_params,
        "validation_warnings": manifest.get("validation_warnings", []),
        "structure_checks": manifest.get("structure_checks", []),
    }

    next_manifest_path = _make_next_manifest_path(project_path_obj, task_id)
    _safe_json_dump(next_manifest_path, manifest_out)

    return {
        "status": "success",
        "task_id": task_id,
        "project_path": str(next_manifest_path),
        "project_content": manifest_out,
        "refined_structures": list(output_files.get("refined_structures", [])),
        "results_file": {
            "path": output_files["results"],
            "content": results_content,
        },
        "plot_file": output_files["plot"],
    }


def export_cifs(
    project_path: str,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    mission_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect only refined CIF files from a refinement snapshot.

    This export step does not recompute refinement. It reads the JSON snapshot
    at `project_path`, resolves CIF output paths recorded in that snapshot,
    and returns only existing `.cif` files on disk.

    Args:
        project_path: Path to a refinement snapshot JSON file.
        task_id: Sub-task identifier used by the caller.
        user_id: Unused in the snapshot-chain model, kept for tool shape.
        session_id: Unused in the snapshot-chain model, kept for tool shape.
        mission_name: Unused in the snapshot-chain model, kept for tool shape.

    Returns:
        A dictionary describing the exported CIF files.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: snapshot that was read
        - `files`: list of existing CIF files
        - `total_size_bytes`: combined size of exported CIF files
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

    # ExportCIFs should only return CIF artifacts.
    files: List[Dict[str, Any]] = []
    total_size = 0

    candidate_paths: List[str] = []

    refined_cif_files = output_files.get("refined_cif_files")
    if isinstance(refined_cif_files, list):
        candidate_paths.extend(str(p) for p in refined_cif_files)

    for key, value in output_files.items():
        if key.startswith("refined_cif_phase") and isinstance(value, str):
            candidate_paths.append(value)

    if not candidate_paths:
        refined_structures = output_files.get("refined_structures")
        if isinstance(refined_structures, list):
            for structure_path in refined_structures:
                if not isinstance(structure_path, str):
                    continue
                src = Path(structure_path)
                if not src.exists():
                    continue
                generated_cif = src.with_suffix(".cif")
                if _convert_refined_structure_to_cif(src, generated_cif):
                    candidate_paths.append(str(generated_cif))

    if not candidate_paths:
        for value in output_files.values():
            if isinstance(value, str) and value.lower().endswith(".cif"):
                candidate_paths.append(value)
            elif isinstance(value, list):
                candidate_paths.extend(
                    str(item) for item in value if isinstance(item, str) and item.lower().endswith(".cif")
                )

    seen: set[str] = set()
    for item in candidate_paths:
        path_text = str(item)
        if path_text in seen:
            continue
        seen.add(path_text)

        path_obj = Path(path_text)
        if path_obj.exists() and path_obj.suffix.lower() == ".cif":
            size = calcu_byte_size(path_obj)
            total_size += size
            files.append(
                {
                    "type": "cif",
                    "path": str(path_obj),
                    "size_bytes": size,
                }
            )

    if not files:
        raise ValueError("未找到可导出的 CIF 文件，请先执行 refine 并确认生成 refined_cif_files")

    return {
        "status": "success",
        "task_id": task_id,
        "project_path": str(project_path_obj),
        "files": files,
        "total_size_bytes": total_size,
        "refined_params": refined_params,
        "summary": f"Collected {len(files)} CIF file(s) from the refinement project.",
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
        port=int(os.getenv("MCP_PDF_REFINE_PORT", "42505")),
    )

    initialize_runner.register_mcp_tool(mcp, name="InitializeProject")
    refine_runner.register_mcp_tool(mcp, name="Refine")
    export_cifs_runner.register_mcp_tool(mcp, name="ExportCIFs")
    mcp.run(transport="sse")

    # def _print_demo_refine_summary(tag: str, result: Dict[str, Any]) -> None:
    #     if result.get("status") != "success":
    #         print(f"{tag}: failed")
    #         print(json.dumps(result, ensure_ascii=False, indent=2))
    #         return

    #     print(f"{tag}: success")
    #     print(
    #         json.dumps(
    #             {
    #                 "project_path": result.get("project_path"),
    #                 "refined_structures": result.get("refined_structures", []),
    #                 "results_file": result.get("results_file", {}).get("path"),
    #                 "plot_file": result.get("plot_file"),
    #             },
    #             ensure_ascii=False,
    #             indent=2,
    #         )
    #     )

    # def _print_demo_export_summary(tag: str, result: Dict[str, Any]) -> None:
    #     if result.get("status") != "success":
    #         print(f"{tag}: failed")
    #         print(json.dumps(result, ensure_ascii=False, indent=2))
    #         return

    #     print(f"{tag}: success")
    #     print(
    #         json.dumps(
    #             {
    #                 "project_path": result.get("project_path"),
    #                 "files": [item.get("path") for item in result.get("files", [])],
    #                 "total_size_bytes": result.get("total_size_bytes"),
    #             },
    #             ensure_ascii=False,
    #             indent=2,
    #         )
    #     )

    # def _is_refinable_fractional_coordinate(value: float, tol: float = 1e-8) -> bool:
    #     wrapped = float(value) % 1.0
    #     return min(abs(wrapped - 0.0), abs(wrapped - 0.5), abs(wrapped - 1.0)) > tol

    # def _build_site_coordinate_groups(site: Dict[str, Any], tol: float = 1e-8) -> List[List[str]]:
    #     groups: List[List[str]] = []
    #     x_val = float(site.get("x", 0.0))
    #     y_val = float(site.get("y", 0.0))
    #     z_val = float(site.get("z", 0.0))

    #     x_free = _is_refinable_fractional_coordinate(x_val, tol=tol)
    #     y_free = _is_refinable_fractional_coordinate(y_val, tol=tol)
    #     z_free = _is_refinable_fractional_coordinate(z_val, tol=tol)

    #     if x_free:
    #         groups.append(["x"])
    #     if y_free:
    #         groups.append(["y"])

    #     if z_free:
    #         groups.append(["z"])

    #     return groups

    # def _build_all_site_refine_steps(structure_path: str) -> List[Dict[str, Any]]:
    #     parser = getParser("pdffit")
    #     structure = parser.parseFile(str(structure_path))

    #     steps: List[Dict[str, Any]] = []
    #     for atom_idx, atom in enumerate(structure, start=1):
    #         add_refine_param: Dict[str, List[str]] = {}
    #         site = {
    #             "x": float(atom.xyz[0]),
    #             "y": float(atom.xyz[1]),
    #             "z": float(atom.xyz[2]),
    #         }
    #         coordinate_groups = _build_site_coordinate_groups(site)
    #         for group_idx, axes in enumerate(coordinate_groups, start=1):
    #             add_refine_param[f"atom_{atom_idx:02d}_pos_{group_idx}"] = [
    #                 f"phase[1].atom[{atom_idx}].{axis}" for axis in axes
    #             ]

    #         add_refine_param[f"atom_{atom_idx:02d}_uiso"] = [
    #             f"phase[1].atom[{atom_idx}].u11",
    #             f"phase[1].atom[{atom_idx}].u22",
    #             f"phase[1].atom[{atom_idx}].u33",
    #         ]

    #         steps.append(
    #             {
    #                 "task_id": f"refine_atom_{atom_idx:02d}",
    #                 "atom_index": atom_idx,
    #                 "addRefineParam": add_refine_param,
    #             }
    #         )

    #     return steps

    # pdf_file = "/home/wangh/rongzai-agent/tools/pdf_refinement/tests/CAT_RUN611_Gr.dat"
    # cif_files = ["/home/wangh/rongzai-agent/tools/pdf_refinement/tests/CsAgTe-RT.cif"]
    # user_id = "wanghao96"
    # mission_name = "CAT_RUN611_pdf_refine"

    # init_result = initialize_pdf_project(
    #     data_file=pdf_file,
    #     structure_files=cif_files,
    #     qmax=25.0,
    #     rmin=2.5,
    #     rmax=21.0,
    #     pdf_source="N",
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="init"
    # )

    # refine1_result = refine(
    #     project_path=init_result["project_path"],
    #     addRefineParam={
    #         "@1": ["phase[1].lat.a", "phase[1].lat.b"],
    #         "@2": ["phase[1].lat.c"],
    #         "@3": ["phase[1].pscale"],
    #     },
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="refine_lattice_scale"
    # )
    # _print_demo_refine_summary("refine1_result", refine1_result)
    
    # refine2_result = refine(
    #     project_path=refine1_result["project_path"],
    #     addRefineParam={
    #         "@4": ["phase[1].delta1", "phase[1].delta2"],
    #     },
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="refine_sharpening"
    # )
    # _print_demo_refine_summary("refine2_result", refine2_result)

    # latest_result = refine2_result
    # if latest_result.get("status") == "success":
    #     latest_structure_files = latest_result.get("project_content", {}).get("structure_files", [])
    #     if latest_structure_files:
    #         all_site_steps = _build_all_site_refine_steps(latest_structure_files[0])
    #         print(f"all_site_step_count: {len(all_site_steps)}")
    #     else:
    #         all_site_steps = []
    #     for step in all_site_steps:
    #         latest_result = refine(
    #             project_path=latest_result["project_path"],
    #             addRefineParam=step["addRefineParam"],
    #             user_id=user_id,
    #             mission_name=mission_name,
    #             task_id=step["task_id"],
    #         )
    #         _print_demo_refine_summary(step["task_id"], latest_result)
    #         if latest_result.get("status") != "success":
    #             break

    #     if latest_result.get("status") == "success":
    #         export_result = export_cifs(
    #             project_path=latest_result["project_path"],
    #             task_id="export_cifs_final",
    #             user_id=user_id,
    #             mission_name=mission_name,
    #         )
    #         _print_demo_export_summary("export_cifs_result", export_result)

    