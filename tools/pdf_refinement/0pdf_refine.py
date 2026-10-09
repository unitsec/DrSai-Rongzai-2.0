#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF refinement tools organized like the neutron GSAS-II tool suite.

This module exposes six polling MCP tools:
- InitializeProject: validate inputs, run structure checks, and create a project manifest
- Refine: run staged PDF refinement and write outputs
- UpdateConstraintSet: edit the snapshot constraintSet and keep refineState synchronized
- UpdateRefineState: edit the snapshot refineState values and refinement flags
- AutoSpaceGroupConstraints: generate symmetry-equivalent constraints from the space group
- ExportCIFs: collect or regenerate refined CIF outputs from a project

The architecture intentionally mirrors the GSAS-II neutron tool pattern:
pure business functions + ProcessPollingMcpToolBase runners + MCP registration.
"""

from __future__ import annotations

import json
import itertools
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
from diffpy.structure.spacegroups import GetSpaceGroup
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

ConstraintSetSchema = Dict[str, List[str]]
RefineStateSchema = List[List[Any]]


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


_NON_REFINABLE_GLOBAL_KEYS = {"qmax", "rmin", "rmax", "pdf_source"}
_REFINABLE_GLOBAL_KEYS = {"dscale", "qdamp", "qbroad"}


def _strip_refinable_globals_from_non_refine_value(raw_non_refine: Any) -> Dict[str, Any]:
    if not isinstance(raw_non_refine, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for key, value in raw_non_refine.items():
        key_text = str(key).strip()
        if key_text.lower() in _REFINABLE_GLOBAL_KEYS:
            continue
        cleaned[key_text] = value
    return cleaned


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
        issues.append("未检测到 ADP 字段(_atom_site_Uiso/Biso 或 _atom_site_aniso_U/B_*)！会导致拟合不收敛或 rw 异常！请立即解决！")
    elif not adp_values:
        issues.append("检测到 ADP 字段但未读到有效数值！会导致拟合不收敛或 rw 异常！请立即解决！")
    elif all_zero:
        issues.append("检测到所有 ADP 数值均为 0!会导致拟合不收敛或 rw 异常！请立即解决！")

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


def create_output_directory(path: str | Path) -> Path:
    output_dir = Path(path).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _build_pdf_project_dir(
    user_id: Optional[str],
    session_id: Optional[str],
    mission_name: Optional[str],
) -> Path:
    """按 user_id/session_id/mission_name 组织 PDF 工具输出目录。"""
    if not user_id:
        raise ValueError("user_id is required")
    if not mission_name:
        raise ValueError("mission_name is required")

    project_dir = Path(DEFAULT_WORKDIR) / str(user_id)
    if session_id:
        project_dir = project_dir / str(session_id)
    project_dir = project_dir / str(mission_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


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


def _safe_getpar(pf: PdfFit, idx: int, default: Any = None) -> Any:
    try:
        return pf.getpar(idx)
    except Exception:
        return default


_PHASE_SELECTOR_RE = re.compile(r"^phase\[(\d+)\]\.(.+)$", re.IGNORECASE)
_ATOM_SELECTOR_RE = re.compile(r"^atom\[(\d+)\]\.(x|y|z|occ|u11|u22|u33|u12|u13|u23)$", re.IGNORECASE)


def _format_number_literal(value: float) -> str:
    number = float(value)
    if abs(number - round(number)) < 1e-12:
        return str(int(round(number)))
    text = f"{number:.12g}"
    if text == "-0":
        return "0"
    return text


def _wrap_fractional_vector(values: np.ndarray) -> np.ndarray:
    return np.mod(np.asarray(values, dtype=float), 1.0)


def _fractional_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    delta = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    return np.abs(np.mod(delta + 0.5, 1.0) - 0.5)


def _fractional_coords_close(a: np.ndarray, b: np.ndarray, tol: float = 1e-4) -> bool:
    return bool(np.max(_fractional_distance(a, b)) <= tol)


def _coerce_origin_offset(origin_offset: Optional[List[float]]) -> np.ndarray:
    if origin_offset is None:
        return np.zeros(3, dtype=float)
    if not isinstance(origin_offset, list) or len(origin_offset) != 3:
        raise ValueError("origin_offset 必须是长度为 3 的列表")
    try:
        return np.asarray([float(v) for v in origin_offset], dtype=float)
    except Exception as e:
        raise ValueError(f"origin_offset 必须是数字列表: {origin_offset}") from e


def _spacegroup_from_identifier(space_group: Any):
    if space_group is None:
        return None
    if hasattr(space_group, "symop_list") and hasattr(space_group, "short_name"):
        return space_group
    return GetSpaceGroup(space_group)


def _atom_u_tensor(atom: Any) -> np.ndarray:
    raw_u = getattr(atom, "U", None)
    if raw_u is None:
        return np.zeros((3, 3), dtype=float)

    try:
        array = np.asarray(raw_u, dtype=float)
    except Exception:
        return np.zeros((3, 3), dtype=float)

    if array.shape == (3, 3):
        return array.astype(float)
    if array.ndim == 0:
        value = float(array)
        return np.diag([value, value, value])
    if array.size == 6:
        u11, u22, u33, u12, u13, u23 = [float(v) for v in array.flatten()[:6]]
        return np.array(
            [[u11, u12, u13], [u12, u22, u23], [u13, u23, u33]],
            dtype=float,
        )
    if array.size >= 9:
        return array.reshape(3, 3).astype(float)
    value = float(array.flatten()[0]) if array.size else 0.0
    return np.diag([value, value, value])


def _apply_symop_to_coords(coords: np.ndarray, symop: Any, origin_offset: np.ndarray) -> np.ndarray:
    rotation = np.asarray(symop.R, dtype=float)
    translation = np.asarray(symop.t, dtype=float)
    return rotation @ np.asarray(coords, dtype=float) + translation + origin_offset - rotation @ origin_offset


def _linear_expression(
    terms: List[tuple[float, str]],
    constant: float = 0.0,
) -> str:
    pieces: List[str] = []
    for coefficient, symbol in terms:
        coeff = float(coefficient)
        if abs(coeff) < 1e-12:
            continue
        if abs(coeff - 1.0) < 1e-12:
            piece = symbol
        elif abs(coeff + 1.0) < 1e-12:
            piece = f"-{symbol}"
        else:
            piece = f"{_format_number_literal(coeff)}*{symbol}"
        pieces.append(piece)

    constant_value = float(constant)
    if abs(constant_value) >= 1e-12:
        pieces.append(_format_number_literal(constant_value))

    if not pieces:
        return "0"

    expr = pieces[0]
    for piece in pieces[1:]:
        if piece.startswith("-"):
            expr += piece
        else:
            expr += f"+{piece}"
    return expr


def _tensor_linear_expression(
    rotation: np.ndarray,
    base_symbols: Dict[str, str],
) -> Dict[str, str]:
    basis_mats = {
        "u11": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]),
        "u22": np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]]),
        "u33": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),
        "u12": np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]]),
        "u13": np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        "u23": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]]),
    }
    output_components = {
        "u11": (0, 0),
        "u22": (1, 1),
        "u33": (2, 2),
        "u12": (0, 1),
        "u13": (0, 2),
        "u23": (1, 2),
    }

    transformed_coefficients: Dict[str, str] = {}
    for output_name, (row_idx, col_idx) in output_components.items():
        terms: List[tuple[float, str]] = []
        for basis_name in ("u11", "u22", "u33", "u12", "u13", "u23"):
            transformed = rotation @ basis_mats[basis_name] @ rotation.T
            coefficient = float(transformed[row_idx, col_idx])
            terms.append((coefficient, base_symbols[basis_name]))
        transformed_coefficients[output_name] = _linear_expression(terms)

    return transformed_coefficients


def _nullspace(matrix: np.ndarray, tol: float = 1e-10) -> np.ndarray:
    array = np.asarray(matrix, dtype=float)
    if array.size == 0:
        return np.eye(array.shape[1], dtype=float)
    _, singular_values, vh = np.linalg.svd(array, full_matrices=True)
    rank = int(np.sum(singular_values > tol))
    return vh[rank:].T.copy()


def _choose_component_rows(basis: np.ndarray) -> List[int]:
    n_rows, n_cols = basis.shape
    if n_cols == 0:
        return []
    if n_cols > n_rows:
        raise ValueError("Basis dimension exceeds component dimension")

    for rows in itertools.combinations(range(n_rows), n_cols):
        sub = basis[list(rows), :]
        if abs(float(np.linalg.det(sub))) > 1e-10:
            return list(rows)
    raise ValueError("Unable to select independent component rows")


def _build_affine_parameterization(values0: np.ndarray, basis: np.ndarray) -> tuple[np.ndarray, np.ndarray, List[int], np.ndarray]:
    n_rows, n_cols = basis.shape
    if n_cols == 0:
        return np.zeros((n_rows, 0), dtype=float), np.asarray(values0, dtype=float), [], np.zeros(0, dtype=float)

    selected_rows = _choose_component_rows(basis)
    sub = basis[selected_rows, :]
    inv_sub = np.linalg.inv(sub)
    linear_map = basis @ inv_sub
    values0 = np.asarray(values0, dtype=float)
    selected_values0 = values0[selected_rows]
    const = values0 - linear_map @ selected_values0
    return linear_map, const, selected_rows, selected_values0


def _tensor_components_from_matrix(tensor: np.ndarray) -> np.ndarray:
    t = np.asarray(tensor, dtype=float)
    return np.asarray(
        [
            float(t[0, 0]),
            float(t[1, 1]),
            float(t[2, 2]),
            float(t[0, 1]),
            float(t[0, 2]),
            float(t[1, 2]),
        ],
        dtype=float,
    )


def _tensor_transform_matrix(rotation: np.ndarray) -> np.ndarray:
    basis_mats = [
        np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=float),
        np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]], dtype=float),
        np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]], dtype=float),
        np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=float),
        np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=float),
        np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]], dtype=float),
    ]
    rows: List[np.ndarray] = []
    rot = np.asarray(rotation, dtype=float)
    for output_idx in range(6):
        coeffs: List[float] = []
        for basis in basis_mats:
            transformed = rot @ basis @ rot.T
            coeffs.append(float(_tensor_components_from_matrix(transformed)[output_idx]))
        rows.append(np.asarray(coeffs, dtype=float))
    return np.vstack(rows)


def _build_space_group_orbits(
    structure: Structure,
    spacegroup: Any,
    origin_offset: np.ndarray,
    tol: float = 1e-4,
) -> List[Dict[str, Any]]:
    atoms = list(structure)
    positions = [np.asarray(getattr(atom, "xyz", [0.0, 0.0, 0.0]), dtype=float) for atom in atoms]
    elements = [str(getattr(atom, "element", "") or "").strip() for atom in atoms]
    symops = list(getattr(spacegroup, "symop_list", []))

    visited: set[int] = set()
    orbits: List[Dict[str, Any]] = []

    for rep_idx, rep_atom in enumerate(atoms, start=1):
        atom_index = rep_idx - 1
        if atom_index in visited:
            continue

        rep_pos = positions[atom_index]
        rep_element = elements[atom_index]
        orbit_indices: List[int] = [rep_idx]
        op_by_index: Dict[int, Any] = {rep_idx: None}

        for candidate_idx, candidate_atom in enumerate(atoms, start=1):
            zero_idx = candidate_idx - 1
            if zero_idx in visited or candidate_idx == rep_idx:
                continue
            if elements[zero_idx] != rep_element:
                continue

            candidate_pos = positions[zero_idx]
            for symop in symops:
                transformed = _wrap_fractional_vector(
                    _apply_symop_to_coords(rep_pos, symop, origin_offset)
                )
                if _fractional_coords_close(transformed, candidate_pos, tol=tol):
                    orbit_indices.append(candidate_idx)
                    op_by_index[candidate_idx] = symop
                    break

        for idx in orbit_indices:
            visited.add(idx - 1)

        orbits.append(
            {
                "representative_index": rep_idx,
                "indices": orbit_indices,
                "representative_atom": rep_atom,
                "positions": positions,
                "op_by_index": op_by_index,
            }
        )

    return orbits


def _build_space_group_constraint_snapshot(
    structure: Structure,
    phase_index: int,
    spacegroup: Any,
    origin_offset: np.ndarray,
    constrain_positions: bool,
    constrain_temperature_factors: bool,
    symbol_counter_start: int,
) -> tuple[ConstraintSetSchema, RefineStateSchema, int]:
    orbits = _build_space_group_orbits(structure, spacegroup, origin_offset)

    constraint_params: List[str] = []
    constraint_exprs: List[str] = []
    refine_state: RefineStateSchema = []
    symbol_counter = symbol_counter_start

    tol = 1e-10
    coord_names = ["x", "y", "z"]
    tensor_names = ["u11", "u22", "u33", "u12", "u13", "u23"]

    def next_symbol() -> str:
        nonlocal symbol_counter
        symbol = f"@{symbol_counter}"
        symbol_counter += 1
        return symbol

    def add_parameter(selector: str, expression: str, value: float, should_refine: bool = True) -> None:
        constraint_params.append(selector)
        constraint_exprs.append(expression)
        if should_refine and expression.startswith("@") and expression[1:].isdigit():
            if all(row[0] != expression for row in refine_state):
                refine_state.append([expression, float(value), True])

    for orbit in orbits:
        rep_idx = int(orbit["representative_index"])
        rep_atom = orbit["representative_atom"]
        orbit_indices = list(orbit["indices"])
        op_by_index = orbit["op_by_index"]
        non_rep_indices = [idx for idx in orbit_indices if idx != rep_idx]

        rep_coords = _wrap_fractional_vector(np.asarray(getattr(rep_atom, "xyz", [0.0, 0.0, 0.0]), dtype=float))

        stabilizer_ops: List[Any] = []
        for symop in getattr(spacegroup, "symop_list", []):
            transformed = _wrap_fractional_vector(_apply_symop_to_coords(rep_coords, symop, origin_offset))
            if _fractional_coords_close(transformed, rep_coords, tol=1e-4):
                stabilizer_ops.append(symop)

        pos_linear = np.zeros((3, 0), dtype=float)
        pos_const = rep_coords.copy()
        pos_selected_rows: List[int] = []
        pos_symbols: List[str] = []

        if constrain_positions:
            pos_constraints: List[np.ndarray] = []
            for symop in stabilizer_ops:
                rot = np.asarray(symop.R, dtype=float)
                pos_constraints.append(np.eye(3, dtype=float) - rot)

            pos_basis = _nullspace(np.vstack(pos_constraints), tol=tol) if pos_constraints else np.eye(3, dtype=float)
            pos_linear, pos_const, pos_selected_rows, pos_selected_values0 = _build_affine_parameterization(rep_coords, pos_basis)

            for row_idx in pos_selected_rows:
                axis_name = coord_names[row_idx]
                symbol = next_symbol()
                pos_symbols.append(symbol)
                add_parameter(
                    f"phase[{phase_index}].atom[{rep_idx}].{axis_name}",
                    symbol,
                    float(rep_coords[row_idx]),
                )

            # Add dependent representative coordinate components (e.g., y = x)
            for axis_idx, axis_name in enumerate(coord_names):
                if axis_idx in pos_selected_rows:
                    continue
                coeffs = pos_linear[axis_idx, :]
                if coeffs.size == 0 or np.max(np.abs(coeffs)) <= tol:
                    continue
                predicted0 = float(np.dot(coeffs, pos_selected_values0) + pos_const[axis_idx])
                target = float(rep_coords[axis_idx])
                shift = float(round(target - predicted0))
                const_adj = float(pos_const[axis_idx] + shift)
                terms = [(float(c), sym) for c, sym in zip(coeffs, pos_symbols)]
                expr = _linear_expression(terms, const_adj)
                add_parameter(
                    f"phase[{phase_index}].atom[{rep_idx}].{axis_name}",
                    expr,
                    target,
                    should_refine=False,
                )
        else:
            pos_selected_values0 = np.zeros(0, dtype=float)

        rep_tensor_components = _tensor_components_from_matrix(_atom_u_tensor(rep_atom))
        ten_linear = np.zeros((6, 0), dtype=float)
        ten_const = rep_tensor_components.copy()
        ten_selected_rows: List[int] = []
        ten_symbols: List[str] = []

        if constrain_temperature_factors:
            ten_constraints: List[np.ndarray] = []
            for symop in stabilizer_ops:
                transform = _tensor_transform_matrix(np.asarray(symop.R, dtype=float))
                ten_constraints.append(np.eye(6, dtype=float) - transform)

            ten_basis = _nullspace(np.vstack(ten_constraints), tol=tol) if ten_constraints else np.eye(6, dtype=float)
            ten_linear, ten_const, ten_selected_rows, ten_selected_values0 = _build_affine_parameterization(
                rep_tensor_components,
                ten_basis,
            )

            for row_idx in ten_selected_rows:
                comp_name = tensor_names[row_idx]
                symbol = next_symbol()
                ten_symbols.append(symbol)
                add_parameter(
                    f"phase[{phase_index}].atom[{rep_idx}].{comp_name}",
                    symbol,
                    float(rep_tensor_components[row_idx]),
                )

            # Add dependent representative tensor components (e.g., u22 = u11)
            for comp_idx, comp_name in enumerate(tensor_names):
                if comp_idx in ten_selected_rows:
                    continue
                coeffs = ten_linear[comp_idx, :]
                if coeffs.size == 0 or np.max(np.abs(coeffs)) <= tol:
                    continue
                predicted0 = float(np.dot(coeffs, ten_selected_values0) + ten_const[comp_idx])
                target = float(rep_tensor_components[comp_idx])
                const_adj = float(ten_const[comp_idx] + (target - predicted0))
                terms = [(float(c), sym) for c, sym in zip(coeffs, ten_symbols)]
                expr = _linear_expression(terms, const_adj)
                add_parameter(
                    f"phase[{phase_index}].atom[{rep_idx}].{comp_name}",
                    expr,
                    target,
                    should_refine=False,
                )
        else:
            ten_selected_values0 = np.zeros(0, dtype=float)

        for atom_idx in non_rep_indices:
            symop = op_by_index.get(atom_idx)
            if symop is None:
                continue

            atom = structure[atom_idx - 1]
            selector_prefix = f"phase[{phase_index}].atom[{atom_idx}]"

            if constrain_positions and pos_symbols:
                rot = np.asarray(symop.R, dtype=float)
                trans = np.asarray(symop.t, dtype=float)
                off = origin_offset + trans - rot @ origin_offset
                coeff_matrix = rot @ pos_linear
                base_const = rot @ pos_const + off

                atom_coords = _wrap_fractional_vector(np.asarray(getattr(atom, "xyz", [0.0, 0.0, 0.0]), dtype=float))
                for axis_idx, axis_name in enumerate(coord_names):
                    coeffs = coeff_matrix[axis_idx, :]
                    if coeffs.size == 0 or np.max(np.abs(coeffs)) <= tol:
                        continue

                    predicted0 = float(np.dot(coeffs, pos_selected_values0) + base_const[axis_idx])
                    target = float(atom_coords[axis_idx])
                    shift = float(round(target - predicted0))
                    const_adj = float(base_const[axis_idx] + shift)

                    terms = [(float(c), sym) for c, sym in zip(coeffs, pos_symbols)]
                    expr = _linear_expression(terms, const_adj)
                    add_parameter(f"{selector_prefix}.{axis_name}", expr, target)

            if constrain_temperature_factors and ten_symbols:
                transform = _tensor_transform_matrix(np.asarray(symop.R, dtype=float))
                coeff_matrix = transform @ ten_linear
                base_const = transform @ ten_const

                atom_tensor = _tensor_components_from_matrix(_atom_u_tensor(atom))
                for comp_idx, comp_name in enumerate(tensor_names):
                    coeffs = coeff_matrix[comp_idx, :]
                    if coeffs.size == 0 or np.max(np.abs(coeffs)) <= tol:
                        continue

                    predicted0 = float(np.dot(coeffs, ten_selected_values0) + base_const[comp_idx])
                    target = float(atom_tensor[comp_idx])
                    const_adj = float(base_const[comp_idx] + (target - predicted0))

                    terms = [(float(c), sym) for c, sym in zip(coeffs, ten_symbols)]
                    expr = _linear_expression(terms, const_adj)
                    add_parameter(f"{selector_prefix}.{comp_name}", expr, target)

    constraint_set: ConstraintSetSchema = {
        "params": constraint_params,
        "constraints": constraint_exprs,
    }

    selector_order = {
        "x": 0,
        "y": 1,
        "z": 2,
        "u11": 3,
        "u22": 4,
        "u33": 5,
        "u12": 6,
        "u13": 7,
        "u23": 8,
        "occ": 9,
    }

    def _selector_sort_key(item: tuple[str, str, int]) -> tuple[int, int, int, int]:
        selector = item[0]
        idx = item[2]
        m = re.match(r"^phase\[(\d+)\]\.atom\[(\d+)\]\.([a-zA-Z0-9_.]+)$", selector)
        if not m:
            return (10**9, 10**9, 10**9, idx)
        phase_i = int(m.group(1))
        atom_i = int(m.group(2))
        comp = m.group(3).strip().lower()
        comp_order = selector_order.get(comp, 999)
        return (phase_i, atom_i, comp_order, idx)

    packed = [(p, c, i) for i, (p, c) in enumerate(zip(constraint_set["params"], constraint_set["constraints"]))]
    packed.sort(key=_selector_sort_key)
    constraint_set["params"] = [p for p, _, _ in packed]
    constraint_set["constraints"] = [c for _, c, _ in packed]

    return constraint_set, refine_state, symbol_counter


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

    fig.subplots_adjust(left=0.10, right=0.98, top=0.93, bottom=0.10, hspace=0.06)
    plt.savefig(output_path, dpi=240, bbox_inches="tight")
    plt.close()
    return str(output_path)


def _infer_pdf_export_base_from_rstr(rstr_files: List[str]) -> Optional[Path]:
    if not isinstance(rstr_files, list) or not rstr_files:
        return None

    first_path = Path(str(rstr_files[0])).expanduser()
    stem = first_path.stem
    match = re.match(r"^(?P<base>.+)_phase\d+$", stem)
    if match:
        return first_path.with_name(match.group("base"))
    return first_path.with_suffix("")


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


def _iter_cif_expansion_candidates(
    parser: Any,
    spacegroup: Any = None,
    offsets: Optional[List[tuple[float, float, float]]] = None,
):
    if getattr(parser, "asymmetric_unit", None) is None or getattr(parser, "spacegroup", None) is None:
        raise ValueError("CIF parser did not return asymmetric-unit metadata")

    corepos = [atom.xyz for atom in parser.asymmetric_unit]
    coreUijs = [atom.U for atom in parser.asymmetric_unit]

    sg = _spacegroup_from_identifier(spacegroup) if spacegroup is not None else parser.spacegroup

    if offsets is None:
        base_offsets: List[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
        diag_shifts = [0.0, 0.125, 0.25, 0.375, 0.5]
        for atom in parser.asymmetric_unit:
            xyz = tuple(float(v) % 1.0 for v in atom.xyz)
            base_offsets.append(xyz)
            for shift in diag_shifts[1:]:
                base_offsets.append(tuple((v + shift) % 1.0 for v in xyz))
    else:
        base_offsets = list(offsets)

    seen_offsets = set()
    for offset in base_offsets:
        if offset in seen_offsets:
            continue
        seen_offsets.add(offset)
        eau = ExpandAsymmetricUnit(sg, corepos, coreUijs, sgoffset=offset)
        total_atoms = int(sum(eau.multiplicity))
        yield offset, eau, total_atoms, len(eau.multiplicity)


def _select_best_cif_expansion(
    parser: Any,
    spacegroup: Any = None,
    origin_offset: Optional[np.ndarray] = None,
):
    best_offset = None
    best_expanded = None
    best_score = None

    candidate_offsets: Optional[List[tuple[float, float, float]]] = None
    if origin_offset is not None:
        candidate_offsets = [tuple(float(v) for v in np.asarray(origin_offset, dtype=float))]

    for offset, eau, total_atoms, site_count in _iter_cif_expansion_candidates(
        parser,
        spacegroup=spacegroup,
        offsets=candidate_offsets,
    ):
        score = (total_atoms, site_count, offset)
        if best_score is None or score < best_score:
            best_score = score
            best_offset = offset
            best_expanded = eau
    if best_expanded is None or best_offset is None:
        raise ValueError("Unable to select a CIF expansion candidate")
    return best_expanded, best_offset, best_score


def _load_cif_as_pdffit_structure(
    structure_file: str | Path,
    spacegroup: Any = None,
    origin_offset: Optional[np.ndarray] = None,
) -> PDFFitStructure:
    parser = getParser("cif")
    parsed = parser.parseFile(str(structure_file))
    best_expanded, _, _ = _select_best_cif_expansion(parser, spacegroup=spacegroup, origin_offset=origin_offset)

    stru = PDFFitStructure(lattice=parsed.lattice, title=parsed.title)
    sg = _spacegroup_from_identifier(spacegroup) if spacegroup is not None else parser.spacegroup
    stru.pdffit["spcgr"] = sg.short_name
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


def _load_structure_files(
    pf: PdfFit,
    structure_files: List[str],
    spacegroup_override: Any = None,
    origin_offset: Optional[np.ndarray] = None,
) -> None:
    for idx, structure_file in enumerate(structure_files, start=1):
        if structure_file.lower().endswith(".cif"):
            try:
                structure = _load_cif_as_pdffit_structure(
                    structure_file,
                    spacegroup=spacegroup_override,
                    origin_offset=origin_offset,
                )
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


def _get_phase_spacegroup(structure_file: str | Path, structure: Structure, override_spacegroup: Any = None):
    if override_spacegroup is not None:
        return _spacegroup_from_identifier(override_spacegroup)

    path = Path(structure_file)
    if path.suffix.lower() == ".cif":
        parser = getParser("cif")
        parser.parseFile(str(path))
        return parser.spacegroup

    pdffit_info = getattr(structure, "pdffit", None)
    if isinstance(pdffit_info, dict):
        for key in ("spcgr", "spacegroup", "sgname"):
            if key in pdffit_info and pdffit_info[key]:
                return _spacegroup_from_identifier(pdffit_info[key])

    raise ValueError(f"无法为结构文件确定空间群: {structure_file}")


def _build_refinable_param_catalog(structure_files: List[str]) -> Dict[str, Any]:
    """Build a compact refinable-parameter guide from current phase/atom topology."""

    global_params = [
        "dscale",
        "qdamp",
        "qbroad",
    ]
    phase_level_keys = [
        "pscale",
        "delta1",
        "delta2",
        "sratio",
        "spdiameter",
        "lat.a",
        "lat.b",
        "lat.c",
        "lat.alpha",
        "lat.beta",
        "lat.gamma",
    ]
    atom_level_keys = [
        "x",
        "y",
        "z",
        "occ",
        "u11",
        "u22",
        "u33",
        "u12",
        "u13",
        "u23",
    ]

    phase_summaries: List[Dict[str, Any]] = []

    pf = PdfFit()
    _load_structure_files(pf, structure_files)

    for phase_idx, structure_file in enumerate(structure_files, start=1):
        phase_name = Path(structure_file).stem
        atoms_by_element: Dict[str, List[Dict[str, Any]]] = {}
        atom_count = 0
        try:
            structure = pf.get_structure(phase_idx)
            atom_count = len(structure)
            for atom_idx, atom in enumerate(structure, start=1):
                atom_label = str(getattr(atom, "label", "") or "").strip()
                atom_name = atom_label if atom_label else f"atom_{atom_idx}"
                atom_element = str(getattr(atom, "element", "") or "").strip()
                atom_entry = {
                    "atom_index": atom_idx,
                    "atom_name": atom_name,
                    "element": atom_element,
                }
                group_key = atom_element or "UNKNOWN"
                atoms_by_element.setdefault(group_key, []).append(
                    {
                        "atom_index": atom_idx,
                        "atom_name": atom_name,
                    }
                )
        except Exception:
            atom_count = 0

        phase_summaries.append(
            {
                "phase_index": phase_idx,
                "phase_name": phase_name,
                "structure_file": str(structure_file),
                "atom_count": atom_count,
                "atom_groups": [
                    {
                        "element": element,
                        "count": len(entries),
                        "atoms": entries,
                    }
                    for element, entries in sorted(atoms_by_element.items())
                ],
            }
        )

    return {
        "description": "Use selector templates with phase_index and atom_index from phase_summaries.",
        "selector_templates": {
            "phase_param": "phase[{phase_index}].{phase_param_key}",
            "atom_param": "phase[{phase_index}].atom[{atom_index}].{atom_param_key}",
        },
        "global_params": global_params,
        "phase_param_keys": phase_level_keys,
        "atom_param_keys": atom_level_keys,
        "phase_summaries": phase_summaries,
    }


def initialize_pdf_project(
    data_file: str,
    structure_files: List[str],
    qmax: float = 25.0,
    rmin: float = 1.0,
    rmax: float = 20.0,
    pdf_source: str = "N",
    user_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
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
        session_id: Current Agent session ID. Always pass it so outputs are isolated under the current session directory.
        mission_name: Mission namespace used to build the project directory and task polling.
        task_id: task name under mission for the purpose of naming the project file and for task polling.    

    the snapshot file is a JSON object with the following fields:
        - `data_file`: path to the experimental PDF data file
        - `structure_files`: list of paths to the initial structure files
        - 'nonRefineValue': dictionary of non-refinable parameters and their initial values
        - `constraintSet`: a single dictionary with `params` and `constraints` lists
        - `refineState`: a list of free-parameter rows `[parameter_name, value, should_refine]`
        - `output_files`: dictionary of output files generated by the refinement process, initially empty
        - `validation_warnings`: list of warning messages generated during structure validation
        - `structure_checks`: list of per-file validation details, including detected issues and status
    
    - `constraintSet`: dictionary with parallel lists
        - `params`: parameter names that can be refined later
        - `constraints`: one-to-one expressions for each param, e.g. `@11`, `2*@11+5`, `-@12`, `@11+2.5`
        - the `params` and `constraints` lists must always have the same length
        - a free parameter symbol such as `@11` may appear only once in `params`
    - `refineState`: list of rows in the form `[parameter_name, value, should_refine]`
        - example: `[['@11', 10, True], ['@12', 1.5, False]]`
    
    
    Returns:
        A dictionary with the initial project result.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: path to the created JSON project snapshot file
        - `project_content`: the project snapshot content written to disk
        - `refinable_params`: compact guide of refinable parameter templates and phase/atom index mapping
        - `has_warnings`: whether structure validation produced warnings
        - `warnings`: warning message list
        - `structure_checks`: structured per-file validation details
        - `error`: error message when `status` is `failed`
        - `traceback`: debug traceback when `status` is `failed`
    """
    results = {"status": "failed"}

    try:
        if not user_id:
            raise ValueError("user_id is required")
        if not mission_name:
            raise ValueError("mission_name is required")
        if not task_id:
            raise ValueError("task_id is required")

        project_dir = _build_pdf_project_dir(user_id, session_id, mission_name)
        path = Path(project_dir)
        path.mkdir(parents=True, exist_ok=True)

        if not Path(data_file).exists():
            raise FileNotFoundError(f"数据文件不存在: {data_file}")
        if not isinstance(structure_files, list) or not structure_files:
            raise ValueError("structure_files 必须是非空列表")
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
        non_refine_value: Dict[str, Any] = {
            "qmax": float(qmax),
            "rmin": float(rmin),
            "rmax": float(rmax),
            "pdf_source": _normalize_pdf_source(pdf_source),
        }

        constraint_set: ConstraintSetSchema = {
            "params": [],
            "constraints": [],
        }
        refine_state: RefineStateSchema = []
        refinable_param_catalog = _build_refinable_param_catalog(structure_files)
        
        manifest = {
            "data_file": data_file,
            "structure_files": [str(p) for p in structure_files],
            "nonRefineValue": non_refine_value,
            "constraintSet": constraint_set,
            "refineState": refine_state,
            "output_files": {},
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
        results["refinable_params"] = refinable_param_catalog
        return results
    
    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results




def update_nonRefineValue_and_constraintSet(
    project_path: str,
    mergeConstraintSet: Optional[ConstraintSetSchema] = None,
    deleteConstraintSet: Optional[ConstraintSetSchema] = None,
    nonRefineValue: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """tool for editing snapshot constraintSet.

    behavior:
        - 'nonRefineValue' is a dictionary of non-refinable parameters and their initial values.
        - if 'nonRefineValue' is provided, it updates the snapshot's non-refinable parameters.
        - `mergeConstraintSet` and `deleteConstraintSet` are optional dictionaries with two parallel lists: `params` and `constraints`.
        - If either one is omitted (or null), it is treated as an empty constraint set.
        - The two lists must have identical length.
        - Each `params[i]` is a physical-quantity selector and each `constraints[i]` is its linear expression.
        - Expressions may use the free-parameter syntax `@n`, `A@n+B`, `@n-B`, or `-@n`.
        - mergeConstraintSet adds or updates the listed free parameters and their expressions.
        - deleteConstraintSet removes the listed free parameters from the snapshot.
        - Duplicate physical quantities in `params` are rejected.
        - Any free parameter referenced by `constraintSet` but missing from `refineState` is auto-created.
        - Any free parameter no longer referenced by `constraintSet` is removed from `refineState` immediately.
    
    returns:
        A dictionary with the updated project result.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: path to the updated JSON project snapshot file
        - `project_content`: the updated project snapshot content written to disk
        - `error`: error message when `status` is `failed`
        
    """

    results = {"status": "failed"}

    def _normalize_constraint_set(raw: Any, field_name: str) -> ConstraintSetSchema:
        if not isinstance(raw, dict):
            raise ValueError(f"{field_name} 必须是包含 params/constraints 的字典")

        if "params" not in raw:
            raise ValueError(f"{field_name} 缺少必需键 params")
        if "constraints" not in raw:
            raise ValueError(f"{field_name} 缺少必需键 constraints")

        params_raw = raw.get("params")
        constraints_raw = raw.get("constraints")
        if not isinstance(params_raw, list):
            raise ValueError(f"{field_name}.params 必须是列表")
        if not isinstance(constraints_raw, list):
            raise ValueError(f"{field_name}.constraints 必须是列表")
        if len(params_raw) != len(constraints_raw):
            raise ValueError(f"{field_name}.params 和 {field_name}.constraints 长度必须一致")

        params: List[str] = []
        constraints: List[str] = []
        seen_params: set[str] = set()
        for idx, (param, expr) in enumerate(zip(params_raw, constraints_raw), start=1):
            param_text = str(param).strip()
            expr_text = str(expr).strip()
            if not param_text:
                raise ValueError(f"{field_name}.params 第 {idx} 项不能为空")
            if not expr_text:
                raise ValueError(f"{field_name}.constraints 第 {idx} 项不能为空")
            if param_text in seen_params:
                raise ValueError(f"{field_name}.params 存在重复参数: {param_text}")
            seen_params.add(param_text)
            params.append(param_text)
            constraints.append(expr_text)

        return {
            "params": params,
            "constraints": constraints,
        }

    def _normalize_manifest_constraint_set(raw: Any) -> ConstraintSetSchema:
        if not isinstance(raw, dict):
            return {"params": [], "constraints": []}

        params_raw = raw.get("params", [])
        constraints_raw = raw.get("constraints", [])
        return _normalize_constraint_set(
            {
                "params": params_raw,
                "constraints": constraints_raw,
            },
            "project.constraintSet",
        )

    def _normalize_existing_refine_state(raw: Any) -> RefineStateSchema:
        if not isinstance(raw, list):
            return []

        normalized: RefineStateSchema = []
        seen_names: set[str] = set()
        for row in raw:
            if not isinstance(row, list) or len(row) != 3:
                continue
            name = str(row[0]).strip()
            if not re.fullmatch(r"@\d+", name):
                continue
            if name in seen_names:
                continue
            try:
                value = float(row[1])
            except Exception:
                value = 0.0
            should_refine = bool(row[2])
            normalized.append([name, value, should_refine])
            seen_names.add(name)
        return normalized

    def _extract_free_parameters(expressions: List[str]) -> List[str]:
        ordered_symbols: List[str] = []
        seen_symbols: set[str] = set()
        for expr in expressions:
            for symbol in re.findall(r"@\d+", str(expr)):
                if symbol not in seen_symbols:
                    seen_symbols.add(symbol)
                    ordered_symbols.append(symbol)
        return ordered_symbols

    def _safe_eval_constraint_expression(expr: str, free_param: str, free_param_value: float) -> float:
        expr_text = str(expr).strip().replace(" ", "")
        if not expr_text:
            raise ValueError("空约束表达式")

        symbol_name = free_param.replace("@", "__p")
        python_expr = expr_text.replace(free_param, symbol_name)
        if not re.fullmatch(r"[0-9A-Za-z_@+\-*/().]+", python_expr):
            raise ValueError(f"不支持的约束表达式: {expr}")

        return float(eval(python_expr, {"__builtins__": {}}, {symbol_name: float(free_param_value)}))

    def _invert_single_free_parameter_expression(expr: str, free_param: str, target_value: float) -> float:
        symbols = set(re.findall(r"@\d+", str(expr)))
        if symbols != {free_param}:
            raise ValueError(f"表达式 {expr} 不能唯一反解 {free_param}")

        intercept = _safe_eval_constraint_expression(expr, free_param, 0.0)
        at_one = _safe_eval_constraint_expression(expr, free_param, 1.0)
        coefficient = at_one - intercept
        if abs(coefficient) < 1e-12:
            raise ValueError(f"表达式 {expr} 对 {free_param} 的系数为 0")
        return float((float(target_value) - intercept) / coefficient)

    def _load_current_parameter_values(
        manifest_data: Dict[str, Any],
        structure_file_list: List[str],
    ) -> Dict[str, float]:
        current_values: Dict[str, float] = {}

        pf = PdfFit()
        _load_structure_files(pf, structure_file_list)

        candidate_selectors = [str(v) for v in normalized_constraint_set["params"]]
        for selector in candidate_selectors:
            try:
                var = _resolve_pf_variable(pf, selector)
                current_values[selector] = float(pf.getvar(var))
            except Exception:
                continue

        return current_values

    def _normalize_non_refine_updates(
        raw: Optional[Dict[str, Any]],
        phase_count: int,
    ) -> Dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("nonRefineValue 必须是字典")

        allowed_global = set(_NON_REFINABLE_GLOBAL_KEYS)
        normalized: Dict[str, Any] = {}
        for key, value in raw.items():
            key_text = str(key).strip()
            if key_text in allowed_global:
                if key_text == "pdf_source":
                    normalized[key_text] = _normalize_pdf_source(value)
                else:
                    normalized[key_text] = float(value)
                continue

            match = _PHASE_SELECTOR_RE.match(key_text)
            if not match:
                raise ValueError(f"nonRefineValue 包含不支持的参数: {key_text}")

            phase_idx = int(match.group(1))
            param_key = match.group(2).strip().lower()
            if phase_idx < 1 or phase_idx > phase_count:
                raise ValueError(f"nonRefineValue 相索引越界: {key_text}")
            if param_key not in {"stepcut", "rcut"}:
                raise ValueError(f"nonRefineValue 包含不支持的相参数: {key_text}")
            normalized[f"phase[{phase_idx}].{param_key}"] = float(value)

        return normalized

    try:
        if not user_id:
            raise ValueError("user_id is required")
        if not mission_name:
            raise ValueError("mission_name is required")
        if not task_id:
            raise ValueError("task_id is required")

        project_path_obj = Path(project_path)
        if not project_path_obj.exists():
            raise FileNotFoundError(f"project_path 不存在: {project_path}")

        manifest = _safe_json_load(project_path_obj)
        structure_files = manifest.get("structure_files")
        if not isinstance(structure_files, list) or not structure_files:
            raise ValueError("project manifest 缺少 structure_files")

        normalized_merge_constraint_set = _normalize_constraint_set(
            mergeConstraintSet if mergeConstraintSet is not None else {"params": [], "constraints": []},
            "mergeConstraintSet",
        )
        normalized_delete_constraint_set = _normalize_constraint_set(
            deleteConstraintSet if deleteConstraintSet is not None else {"params": [], "constraints": []},
            "deleteConstraintSet",
        )
        existing_constraint_set = _normalize_manifest_constraint_set(manifest.get("constraintSet"))

        merged_constraint_map: Dict[str, str] = {}
        for selector, expr in zip(existing_constraint_set["params"], existing_constraint_set["constraints"]):
            merged_constraint_map[selector] = expr

        for selector in normalized_delete_constraint_set["params"]:
            merged_constraint_map.pop(selector, None)

        for selector, expr in zip(
            normalized_merge_constraint_set["params"],
            normalized_merge_constraint_set["constraints"],
        ):
            merged_constraint_map[selector] = expr

        normalized_constraint_set: ConstraintSetSchema = {
            "params": list(merged_constraint_map.keys()),
            "constraints": list(merged_constraint_map.values()),
        }

        current_parameter_values = _load_current_parameter_values(manifest, structure_files)
        referenced_parameters = _extract_free_parameters(normalized_constraint_set["constraints"])

        inferred_initial_values: Dict[str, float] = {}
        for selector, expr in zip(
            normalized_constraint_set["params"],
            normalized_constraint_set["constraints"],
        ):
            symbols = re.findall(r"@\d+", str(expr))
            if len(symbols) != 1:
                continue
            free_param = symbols[0]
            if free_param in inferred_initial_values:
                continue
            if selector not in current_parameter_values:
                continue
            try:
                inferred_initial_values[free_param] = _invert_single_free_parameter_expression(
                    expr,
                    free_param,
                    current_parameter_values[selector],
                )
            except Exception:
                continue

        existing_refine_state = _normalize_existing_refine_state(manifest.get("refineState"))
        existing_refine_state_map: Dict[str, List[Any]] = {
            str(row[0]): list(row) for row in existing_refine_state
        }
        synced_refine_state: RefineStateSchema = []
        for free_param in referenced_parameters:
            if free_param in existing_refine_state_map:
                synced_refine_state.append(existing_refine_state_map[free_param])
            else:
                synced_refine_state.append([
                    free_param,
                    float(inferred_initial_values.get(free_param, 0.0)),
                    True,
                ])

        updated_manifest = dict(manifest)

        current_non_refine_value = _strip_refinable_globals_from_non_refine_value(
            manifest.get("nonRefineValue")
        )
        current_non_refine_value.update(
            _normalize_non_refine_updates(nonRefineValue, len(structure_files))
        )

        updated_manifest["nonRefineValue"] = current_non_refine_value
        updated_manifest["constraintSet"] = normalized_constraint_set
        updated_manifest["refineState"] = synced_refine_state
        updated_manifest["output_files"] = {}

        project_dir = _build_pdf_project_dir(user_id, session_id, mission_name)
        next_project_path = Path(project_dir) / f"{task_id}.json"
        _safe_json_dump(next_project_path, updated_manifest)

        results["status"] = "success"
        results["project_path"] = str(next_project_path)
        results["project_content"] = updated_manifest
        return results

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results

def update_refine_state(
    project_path: str,
    refineState: RefineStateSchema,
    user_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """tool for editing snapshot refineState.

    behavior:
        - `refineState` is a list of `[parameter_name, value, should_refine]` rows.
        - `parameter_name` refers to a free parameter such as `@11`.
        - `value` updates the initial value used before refinement.
        - `should_refine` controls whether the free parameter is free during refinement.
        - The method only edits existing free parameters and only edits value and should_refine; missing rows are created only when the caller explicitly adds them through constraintSet synchronization.
    """
    results = {"status": "failed"}

    try:
        if not user_id:
            raise ValueError("user_id is required")
        if not mission_name:
            raise ValueError("mission_name is required")
        if not task_id:
            raise ValueError("task_id is required")

        project_path_obj = Path(project_path)
        if not project_path_obj.exists():
            raise FileNotFoundError(f"project_path 不存在: {project_path}")

        if not isinstance(refineState, list):
            raise ValueError("refineState 必须是列表")

        manifest = _safe_json_load(project_path_obj)
        existing_refine_state_raw = manifest.get("refineState")
        if not isinstance(existing_refine_state_raw, list):
            existing_refine_state_raw = []

        existing_rows: RefineStateSchema = []
        existing_map: Dict[str, int] = {}
        for row in existing_refine_state_raw:
            if not isinstance(row, list) or len(row) != 3:
                continue
            name = str(row[0]).strip()
            if not re.fullmatch(r"@\d+", name):
                continue
            try:
                value = float(row[1])
            except Exception:
                value = 0.0
            should_refine = bool(row[2])
            existing_map[name] = len(existing_rows)
            existing_rows.append([name, value, should_refine])

        if not existing_rows:
            raise ValueError("当前快照中不存在可编辑的 refineState")

        seen_updates: set[str] = set()
        normalized_updates: List[List[Any]] = []
        for idx, row in enumerate(refineState, start=1):
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError(
                    f"refineState 第 {idx} 项必须是 [parameter_name, value, should_refine]"
                )

            name = str(row[0]).strip()
            if not re.fullmatch(r"@\d+", name):
                raise ValueError(f"refineState 第 {idx} 项 parameter_name 非法: {name}")
            if name in seen_updates:
                raise ValueError(f"refineState 更新列表中存在重复 parameter_name: {name}")
            if name not in existing_map:
                raise ValueError(f"refineState 只能编辑已存在参数，未找到: {name}")

            try:
                value = float(row[1])
            except Exception as e:
                raise ValueError(f"refineState 第 {idx} 项 value 必须是数值: {row[1]}") from e

            if not isinstance(row[2], bool):
                raise ValueError(
                    f"refineState 第 {idx} 项 should_refine 必须是布尔值: {row[2]}"
                )
            should_refine = row[2]

            normalized_updates.append([name, value, should_refine])
            seen_updates.add(name)

        for name, value, should_refine in normalized_updates:
            pos = existing_map[name]
            existing_rows[pos] = [name, value, should_refine]

        updated_manifest = dict(manifest)
        updated_manifest["nonRefineValue"] = _strip_refinable_globals_from_non_refine_value(
            manifest.get("nonRefineValue")
        )
        updated_manifest["refineState"] = existing_rows
        updated_manifest["output_files"] = {}

        project_dir = _build_pdf_project_dir(user_id, session_id, mission_name)
        next_project_path = Path(project_dir) / f"{task_id}.json"
        _safe_json_dump(next_project_path, updated_manifest)

        results["status"] = "success"
        results["project_path"] = str(next_project_path)
        results["project_content"] = updated_manifest
        return results

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results


def auto_spacegroup_constraints(
    project_path: str,
    constrain_positions: bool = True,
    constrain_temperature_factors: bool = True,
    phase_indices: Optional[List[int]] = None,
    space_group: Optional[Any] = None,
    origin_offset: Optional[List[float]] = None,
    user_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate space-group-based atomic symmetry constraints and write a new snapshot.

    Inputs:
        - project_path:
            Path to an existing JSON snapshot. Must exist and contain structure_files.
        - constrain_positions:
            Whether to regenerate atomic coordinate constraints (x, y, z).
            If False, coordinate-related atomic constraints are not replaced.
        - constrain_temperature_factors:
            Whether to regenerate anisotropic displacement constraints
            (u11, u22, u33, u12, u13, u23).
            If False, temperature-factor atomic constraints are not replaced.
        - phase_indices:
            Optional 1-based phase index list.
            - None: process all phases.
            - [1, 3]: process only selected phases.
            Empty or out-of-range indices return failed.
        - space_group:
            Optional space-group override. Accepts space-group name, number,
            or a diffpy space-group object.
            - Provided: all processed phases use this space group.
            - Omitted: each phase resolves its own space group from structure input.
        - origin_offset:
            Optional numeric list of length 3: [ox, oy, oz].
            Default is [0, 0, 0].
            Used for origin-shift correction in symmetry operations.
        - user_id, mission_name, task_id:
            Required. Used to write the output snapshot to:
            DEFAULT_WORKDIR/user_id/mission_name/task_id.json

    Processing logic:
        1) Load snapshot from project_path and read structure_files.
        2) Validate/normalize phase_indices into selected_phases.
        3) Read existing constraintSet and apply preserve+replace policy:
            - Preserve non-atomic constraints (for example, phase[1].pscale).
            - Preserve atomic constraints in unselected phases.
            - Replace only selected atomic components in selected phases:
              x/y/z when constrain_positions=True,
              u11/u22/u33/u12/u13/u23 when constrain_temperature_factors=True.
        4) Load structures with PdfFit and regenerate symmetry constraints
           for each selected phase:
            - Build symmetry-equivalent orbits from space-group operations.
            - Use site-stabilizer constraints to compute DOF subspaces and
              produce PDFgui-style linked atomic expressions.
        5) Merge refineState:
            - Keep existing free-parameter rows referenced by preserved expressions,
              in first-appearance order.
            - Append newly generated free-parameter rows.
            - Continue numbering from max preserved @n + 1 to avoid collisions.
        6) Clear output_files and write a new immutable snapshot.

    Output:
        On success:
            {
              "status": "success",
              "project_path": ".../task_id.json",
              "project_content": {
                ...,
                "constraintSet": {"params": [...], "constraints": [...]},
                "refineState": [...],
                "output_files": {}
              },
              "summary": {
                "constrain_positions": bool,
                "constrain_temperature_factors": bool,
                "selected_phases": [int, ...],
                "origin_offset": [float, float, float],
                "preserved_constraint_count": int,
                "generated_constraint_count": int,
                "phases": [
                  {
                    "phase_index": int,
                    "space_group": str,
                    "constraint_count": int,
                    "free_parameter_count": int
                  },
                  ...
                ]
              }
            }

        On failure:
            {
              "status": "failed",
              "error": "...",
              "traceback": "..."
            }

    Notes:
        - constrain_positions and constrain_temperature_factors cannot both be False.
        - This function writes a new snapshot and does not overwrite the input file in place.
    """

    results = {"status": "failed"}

    try:
        if not user_id:
            raise ValueError("user_id is required")
        if not mission_name:
            raise ValueError("mission_name is required")
        if not task_id:
            raise ValueError("task_id is required")

        project_path_obj = Path(project_path)
        if not project_path_obj.exists():
            raise FileNotFoundError(f"project_path 不存在: {project_path}")

        if not constrain_positions and not constrain_temperature_factors:
            raise ValueError("constrain_positions 和 constrain_temperature_factors 不能同时为 False")

        manifest = _safe_json_load(project_path_obj)
        structure_files = manifest.get("structure_files")
        if not isinstance(structure_files, list) or not structure_files:
            raise ValueError("project manifest 缺少 structure_files")

        if phase_indices is None:
            selected_phases = list(range(1, len(structure_files) + 1))
        else:
            if not isinstance(phase_indices, list) or not phase_indices:
                raise ValueError("phase_indices 必须是非空整数列表")
            selected_phases = []
            seen_phase: set[int] = set()
            for item in phase_indices:
                try:
                    phase_idx = int(item)
                except Exception as e:
                    raise ValueError(f"phase_indices 包含非法值: {item}") from e
                if phase_idx < 1 or phase_idx > len(structure_files):
                    raise ValueError(f"phase_indices 越界: {phase_idx}，有效范围 1..{len(structure_files)}")
                if phase_idx in seen_phase:
                    continue
                seen_phase.add(phase_idx)
                selected_phases.append(phase_idx)

            if not selected_phases:
                raise ValueError("phase_indices 解析后为空")

        origin_offset_vec = _coerce_origin_offset(origin_offset)
        # Keep adaptive CIF origin-search behavior when caller does not provide origin_offset.
        # Passing [0,0,0] unconditionally would force certain Fd-3m CIFs (for example Si)
        # into a 16-atom expansion that disagrees with the initialized phase topology.
        cif_expansion_offset = origin_offset_vec if origin_offset is not None else None
        spacegroup_override = _spacegroup_from_identifier(space_group) if space_group is not None else None

        pf = PdfFit()
        _load_structure_files(
            pf,
            structure_files,
            spacegroup_override=spacegroup_override,
            origin_offset=cif_expansion_offset,
        )

        existing_constraint_set = manifest.get("constraintSet")
        existing_params_raw = []
        existing_exprs_raw = []
        if isinstance(existing_constraint_set, dict):
            p = existing_constraint_set.get("params")
            c = existing_constraint_set.get("constraints")
            if isinstance(p, list) and isinstance(c, list) and len(p) == len(c):
                existing_params_raw = [str(x).strip() for x in p]
                existing_exprs_raw = [str(x).strip() for x in c]

        atom_selector = re.compile(r"^phase\[(\d+)\]\.atom\[(\d+)\]\.(x|y|z|u11|u22|u33|u12|u13|u23|occ)$", re.IGNORECASE)
        replace_components: set[str] = set()
        if constrain_positions:
            replace_components.update({"x", "y", "z"})
        if constrain_temperature_factors:
            replace_components.update({"u11", "u22", "u33", "u12", "u13", "u23"})

        preserved_constraint_params: List[str] = []
        preserved_constraint_exprs: List[str] = []
        preserved_symbols_ordered: List[str] = []
        seen_preserved_symbol: set[str] = set()

        for param, expr in zip(existing_params_raw, existing_exprs_raw):
            keep_entry = True
            match = atom_selector.match(param)
            if match:
                phase_idx = int(match.group(1))
                comp = match.group(3).lower()
                if phase_idx in selected_phases and comp in replace_components:
                    keep_entry = False
            if keep_entry:
                preserved_constraint_params.append(param)
                preserved_constraint_exprs.append(expr)
                for sym in re.findall(r"@\d+", expr):
                    if sym not in seen_preserved_symbol:
                        seen_preserved_symbol.add(sym)
                        preserved_symbols_ordered.append(sym)

        existing_refine_state_map: Dict[str, List[Any]] = {}
        refine_state_raw = manifest.get("refineState")
        if isinstance(refine_state_raw, list):
            for row in refine_state_raw:
                if not isinstance(row, list) or len(row) != 3:
                    continue
                name = str(row[0]).strip()
                if not re.fullmatch(r"@\d+", name):
                    continue
                try:
                    value = float(row[1])
                except Exception:
                    value = 0.0
                should_refine = bool(row[2])
                existing_refine_state_map[name] = [name, value, should_refine]

        max_existing_symbol_idx = 0
        for sym in preserved_symbols_ordered:
            try:
                max_existing_symbol_idx = max(max_existing_symbol_idx, int(sym[1:]))
            except Exception:
                continue

        generated_constraint_params: List[str] = []
        generated_constraint_exprs: List[str] = []
        generated_refine_state: RefineStateSchema = []
        symbol_counter = max_existing_symbol_idx + 1
        phase_summaries: List[Dict[str, Any]] = []

        for phase_idx, structure_file in enumerate(structure_files, start=1):
            if phase_idx not in selected_phases:
                continue
            phase_structure = pf.get_structure(phase_idx)
            phase_spacegroup = _get_phase_spacegroup(structure_file, phase_structure, spacegroup_override)
            constraint_set, refine_state, symbol_counter = _build_space_group_constraint_snapshot(
                phase_structure,
                phase_idx,
                phase_spacegroup,
                origin_offset_vec,
                constrain_positions,
                constrain_temperature_factors,
                symbol_counter,
            )
            generated_constraint_params.extend(constraint_set["params"])
            generated_constraint_exprs.extend(constraint_set["constraints"])
            generated_refine_state.extend(refine_state)
            phase_summaries.append(
                {
                    "phase_index": phase_idx,
                    "space_group": getattr(phase_spacegroup, "short_name", str(phase_spacegroup)),
                    "constraint_count": len(constraint_set["params"]),
                    "free_parameter_count": len(refine_state),
                }
            )

        merged_constraint_params = preserved_constraint_params + generated_constraint_params
        merged_constraint_exprs = preserved_constraint_exprs + generated_constraint_exprs

        merged_refine_state: RefineStateSchema = []
        for sym in preserved_symbols_ordered:
            if sym in existing_refine_state_map:
                merged_refine_state.append(existing_refine_state_map[sym])
            else:
                merged_refine_state.append([sym, 0.0, True])

        seen_symbols_in_refine: set[str] = {row[0] for row in merged_refine_state}
        for row in generated_refine_state:
            name = str(row[0]).strip()
            if name in seen_symbols_in_refine:
                continue
            seen_symbols_in_refine.add(name)
            merged_refine_state.append([name, float(row[1]), bool(row[2])])

        updated_manifest = dict(manifest)
        updated_manifest["constraintSet"] = {
            "params": merged_constraint_params,
            "constraints": merged_constraint_exprs,
        }
        updated_manifest["refineState"] = merged_refine_state
        updated_manifest["output_files"] = {}

        project_dir = _build_pdf_project_dir(user_id, session_id, mission_name)
        next_project_path = Path(project_dir) / f"{task_id}.json"
        _safe_json_dump(next_project_path, updated_manifest)

        results["status"] = "success"
        results["project_path"] = str(next_project_path)
        results["project_content"] = updated_manifest
        results["summary"] = {
            "constrain_positions": constrain_positions,
            "constrain_temperature_factors": constrain_temperature_factors,
            "selected_phases": selected_phases,
            "origin_offset": [float(v) for v in origin_offset_vec.tolist()],
            "preserved_constraint_count": len(preserved_constraint_params),
            "generated_constraint_count": len(generated_constraint_params),
            "phases": phase_summaries,
        }
        return results

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results


def refine(
    project_path: str,
    user_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Args:
        - project_path: Path to the JSON project snapshot file.
        - user_id: User namespace for project directory.
        - mission_name: Mission namespace for project directory and task polling.
        - session_id: Current Agent session ID. Always pass it so outputs are isolated under the current session directory.
        - task_id: Task identifier for naming output files and task polling.
        
    Execution flow:
        1. Load snapshot from project_path.
        2. Read PDF data and structures.
        3. Use the snapshot `nonRefineValue` to apply non-refinable settings, including global values (`qmax/rmin/rmax/pdf_source`) and phase-level values (`phase[i].stepcut/rcut`).
        4. Use the snapshot `constraintSet` to bind refinable physical quantities (including refinable globals such as `qdamp`), and use `refineState` rows to seed free parameters and set free/fixed flags.
        5. Run pf.refine_step(tolerancy) repeatedly until convergence.
        6. Save outputs and write a new immutable snapshot with refreshed free-parameter values.
    
    returns:
        A dictionary with the refinement result.
        Key fields:
        - `status`: `success` or `failed`
        - `project_path`: path to the updated JSON project snapshot file
        - `project_content`: the updated project snapshot content written to disk
        - `results_file`: dictionary with result file path and content (.res)
            {
            "path": "/path/to/result.res",
            "content": "...full res content..."
            }
        - `plot_file`: plot image path (.png)
        - `error`: error message when `status` is `failed`
    """

    results = {"status": "failed"}

    try:
        if not user_id:
            raise ValueError("user_id is required")
        if not mission_name:
            raise ValueError("mission_name is required")
        if not task_id:
            raise ValueError("task_id is required")

        project_path_obj = Path(project_path)
        if not project_path_obj.exists():
            raise FileNotFoundError(f"project_path 不存在: {project_path}")

        manifest = _safe_json_load(project_path_obj)

        data_file = manifest.get("data_file")
        if not isinstance(data_file, str) or not data_file.strip():
            raise ValueError("project manifest 缺少有效 data_file")

        structure_files_raw = manifest.get("structure_files")
        if not isinstance(structure_files_raw, list) or not structure_files_raw:
            raise ValueError("project manifest 缺少有效 structure_files")
        structure_files = [str(p).strip() for p in structure_files_raw if str(p).strip()]
        if len(structure_files) != len(structure_files_raw):
            raise ValueError("structure_files 中存在空路径")

        non_refine_value_raw = manifest.get("nonRefineValue")
        if not isinstance(non_refine_value_raw, dict):
            raise ValueError("project manifest 缺少 nonRefineValue")
        non_refine_value = dict(non_refine_value_raw)
        forbidden_non_refine = [
            key for key in non_refine_value.keys() if str(key).strip().lower() in _REFINABLE_GLOBAL_KEYS
        ]
        if forbidden_non_refine:
            raise ValueError(
                "nonRefineValue 不允许包含可精修全局参数: " + ", ".join(sorted(set(forbidden_non_refine)))
            )

        constraint_set_raw = manifest.get("constraintSet")
        if not isinstance(constraint_set_raw, dict):
            raise ValueError("project manifest 缺少 constraintSet")
        params_raw = constraint_set_raw.get("params")
        constraints_raw = constraint_set_raw.get("constraints")
        if not isinstance(params_raw, list) or not isinstance(constraints_raw, list):
            raise ValueError("constraintSet 必须包含 params/constraints 列表")
        if len(params_raw) != len(constraints_raw):
            raise ValueError("constraintSet.params 和 constraintSet.constraints 长度必须一致")

        constraint_params: List[str] = []
        constraint_exprs: List[str] = []
        seen_constraint_param: set[str] = set()
        for idx, (param, expr) in enumerate(zip(params_raw, constraints_raw), start=1):
            p_text = str(param).strip()
            e_text = str(expr).strip()
            if not p_text:
                raise ValueError(f"constraintSet.params 第 {idx} 项不能为空")
            if not e_text:
                raise ValueError(f"constraintSet.constraints 第 {idx} 项不能为空")
            if p_text in seen_constraint_param:
                raise ValueError(f"constraintSet.params 存在重复参数: {p_text}")
            seen_constraint_param.add(p_text)
            constraint_params.append(p_text)
            constraint_exprs.append(e_text)

        refine_state_raw = manifest.get("refineState")
        if not isinstance(refine_state_raw, list):
            raise ValueError("project manifest 缺少 refineState")

        refine_state_rows: RefineStateSchema = []
        refine_state_map: Dict[str, List[Any]] = {}
        for idx, row in enumerate(refine_state_raw, start=1):
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError(
                    f"refineState 第 {idx} 项必须是 [parameter_name, value, should_refine]"
                )
            name = str(row[0]).strip()
            if not re.fullmatch(r"@\d+", name):
                raise ValueError(f"refineState 第 {idx} 项 parameter_name 非法: {name}")
            if name in refine_state_map:
                raise ValueError(f"refineState 存在重复 parameter_name: {name}")
            try:
                value = float(row[1])
            except Exception as e:
                raise ValueError(f"refineState 第 {idx} 项 value 必须是数值: {row[1]}") from e
            if not isinstance(row[2], bool):
                raise ValueError(f"refineState 第 {idx} 项 should_refine 必须是布尔值: {row[2]}")
            should_refine = row[2]
            normalized_row = [name, value, should_refine]
            refine_state_rows.append(normalized_row)
            refine_state_map[name] = normalized_row

        referenced_free_params: List[str] = []
        seen_ref: set[str] = set()
        for expr in constraint_exprs:
            for symbol in re.findall(r"@\d+", expr):
                if symbol not in seen_ref:
                    seen_ref.add(symbol)
                    referenced_free_params.append(symbol)

        max_iterations = 200

        for symbol in referenced_free_params:
            if symbol not in refine_state_map:
                raise ValueError(f"constraintSet 引用了 refineState 中不存在的自由参数: {symbol}")


        qmax = float(non_refine_value.get("qmax", 25.0))
        rmin = float(non_refine_value.get("rmin", 1.0))
        rmax = float(non_refine_value.get("rmax", 20.0))
        pdf_source = _normalize_pdf_source(non_refine_value.get("pdf_source", "N"))
        # qdamp is refinable; data load uses a neutral initial value and refinement controls it later.
        read_data_qdamp = 0.001

        project_dir = _build_pdf_project_dir(user_id, session_id, mission_name)
        output_prefix = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in str(task_id))

        pf = PdfFit()
        cleaned_data_file: Optional[Path] = None
        try:
            cleaned_data_file = _preprocess_pdf_data_for_pdffit(data_file, output_dir=project_dir)
            pf.read_data(str(cleaned_data_file), pdf_source, qmax, read_data_qdamp)
        finally:
            if cleaned_data_file is not None:
                try:
                    cleaned_data_file.unlink(missing_ok=True)
                except Exception:
                    pass

        _load_structure_files(pf, structure_files)
        num_phases = len(structure_files)

        for phase_idx in range(1, num_phases + 1):
            for key in ("stepcut", "rcut"):
                selector = f"phase[{phase_idx}].{key}"
                if selector not in non_refine_value:
                    continue
                value = float(non_refine_value[selector])
                try:
                    pf.setphase(phase_idx)
                    var = getattr(pf, key)()
                except Exception as e:
                    raise ValueError(
                        f"无法解析非精修参数变量 {selector}: {e}"
                    ) from e
                try:
                    pf.setvar(var, value)
                except Exception as e:
                    raise ValueError(
                        f"无法设置非精修参数 {selector}={value}: {e}"
                    ) from e

        for selector, expr in zip(constraint_params, constraint_exprs):
            var = _resolve_pf_variable(pf, selector)
            pf.constrain(var, expr)

        for name, value, should_refine in refine_state_rows:
            par_idx = int(name[1:])
            pf.setpar(par_idx, float(value))
            if should_refine:
                pf.freepar(par_idx)
            else:
                pf.fixpar(par_idx)

        pf.pdfrange(1, rmin, rmax)

        step_count = 0
        converged = False
        while step_count < max_iterations:
            finished = bool(pf.refine_step(PDFGUI_TOLERANCY))
            step_count += 1
            if finished:
                converged = True
                break

        updated_refine_state: RefineStateSchema = []
        for name, old_value, should_refine in refine_state_rows:
            par_idx = int(name[1:])
            new_value = _safe_getpar(pf, par_idx, old_value)
            updated_refine_state.append([name, float(new_value), should_refine])

        rw = float(pf.getrw())

        output_files = {
            "results": str(project_dir / f"{output_prefix}.res"),
            "fgr": str(project_dir / f"{output_prefix}.fgr"),
            "plot": str(project_dir / f"{output_prefix}.png"),
            "refined_structures": [],
        }
        pf.save_res(output_files["results"])
        pf.save_pdf(1, output_files["fgr"])

        for phase_idx in range(1, num_phases + 1):
            refined_structure_path = str(project_dir / f"{output_prefix}_phase{phase_idx}.rstr")
            pf.save_struct(phase_idx, refined_structure_path)
            output_files["refined_structures"].append(refined_structure_path)

        output_files["plot"] = _plot_refinement_result(
            pf,
            Path(output_files["plot"]),
            title=f"PDF Refinement - Rw: {rw:.4f}",
        )

        with open(output_files["results"], "r", encoding="utf-8", errors="ignore") as f:
            results_content = f.read()

        if not output_files["refined_structures"]:
            raise ValueError("refine 未生成任何 refined_structures")

        updated_manifest = dict(manifest)
        updated_manifest["data_file"] = data_file
        updated_manifest["structure_files"] = list(output_files["refined_structures"])
        updated_manifest["nonRefineValue"] = _strip_refinable_globals_from_non_refine_value(non_refine_value)
        updated_manifest["nonRefineValue"]["qmax"] = qmax
        updated_manifest["nonRefineValue"]["rmin"] = rmin
        updated_manifest["nonRefineValue"]["rmax"] = rmax
        updated_manifest["nonRefineValue"]["pdf_source"] = pdf_source
        updated_manifest["constraintSet"] = {
            "params": constraint_params,
            "constraints": constraint_exprs,
        }
        updated_manifest["refineState"] = updated_refine_state
        updated_manifest["output_files"] = output_files

        next_project_path = project_dir / f"{task_id}.json"
        _safe_json_dump(next_project_path, updated_manifest)

        results["status"] = "success"
        results["task_id"] = task_id
        results["project_path"] = str(next_project_path)
        results["project_content"] = updated_manifest
        results["results_file"] = {
            "path": output_files["results"],
            "content": results_content,
        }
        results["plot_file"] = output_files["plot"]
        return results

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results


def export_cifs(
    rstr_files: List[str],
    project_path: Optional[str] = None,
    user_id: Optional[str] = None,
    mission_name: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert refined structure files (.rstr/.stru) into CIF files.

    Args:
        rstr_files: Paths to refined structure files.
        project_path: Optional project snapshot path. When provided and .fgr is
            missing on disk, it will be regenerated from the snapshot.
        user_id: User namespace used to build CIF output directory.
        mission_name: Mission namespace used to build CIF output directory.
        session_id: Current Agent session ID. Always pass it so outputs are isolated under the current session directory.
        task_id: Task identifier used to build CIF file name.

    Returns:
        A dictionary describing the conversion result.
        Key fields:
        - `status`: `success` or `failed`
        - `cif_files`: generated CIF paths
        - `fgr_file`: fitted PDF file path when available
    """
    results = {"status": "failed"}

    try:
        if not user_id:
            raise ValueError("user_id is required")
        if not mission_name:
            raise ValueError("mission_name is required")
        if not task_id:
            raise ValueError("task_id is required")

        if not isinstance(rstr_files, list) or not rstr_files:
            raise ValueError("rstr_files 必须是非空列表")

        output_dir = _build_pdf_project_dir(user_id, session_id, mission_name)
        safe_task_id = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in str(task_id))

        cif_files: List[str] = []
        for idx, rstr_path in enumerate(rstr_files, start=1):
            src = Path(str(rstr_path))
            if not src.exists():
                raise FileNotFoundError(f"rstr_file 不存在: {src}")

            if len(rstr_files) == 1:
                dst = output_dir / f"{safe_task_id}.cif"
            else:
                dst = output_dir / f"{safe_task_id}_phase{idx}.cif"

            ok = _convert_refined_structure_to_cif(src, dst)
            if not ok or not dst.exists():
                raise ValueError(f"无法将结构文件转换为 CIF: {src}")
            cif_files.append(str(dst))

        fgr_path: Optional[Path] = None
        export_base = _infer_pdf_export_base_from_rstr(rstr_files)
        if export_base is not None:
            candidate_fgr = export_base.with_suffix(".fgr")
            if candidate_fgr.exists():
                fgr_path = candidate_fgr

        if fgr_path is None and project_path:
            project_path_obj = Path(project_path)
            if not project_path_obj.exists():
                raise FileNotFoundError(f"project_path 不存在: {project_path}")

            manifest = _safe_json_load(project_path_obj)
            data_file = manifest.get("data_file")
            structure_files_raw = manifest.get("structure_files")
            non_refine_value = manifest.get("nonRefineValue")
            if not isinstance(data_file, str) or not data_file.strip():
                raise ValueError("project manifest 缺少有效 data_file")
            if not isinstance(structure_files_raw, list) or not structure_files_raw:
                raise ValueError("project manifest 缺少有效 structure_files")
            if not isinstance(non_refine_value, dict):
                raise ValueError("project manifest 缺少 nonRefineValue")

            qmax = float(non_refine_value.get("qmax", 25.0))
            pdf_source = _normalize_pdf_source(non_refine_value.get("pdf_source", "N"))
            read_data_qdamp = 0.001

            pf = PdfFit()
            cleaned_data_file: Optional[Path] = None
            try:
                cleaned_data_file = _preprocess_pdf_data_for_pdffit(data_file, output_dir=output_dir)
                pf.read_data(str(cleaned_data_file), pdf_source, qmax, read_data_qdamp)
            finally:
                if cleaned_data_file is not None:
                    try:
                        cleaned_data_file.unlink(missing_ok=True)
                    except Exception:
                        pass

            _load_structure_files(pf, [str(p) for p in structure_files_raw])
            fgr_path = output_dir / f"{safe_task_id}.fgr"
            pf.save_pdf(1, str(fgr_path))

        results["status"] = "success"
        results["cif_files"] = cif_files
        if fgr_path is not None and fgr_path.exists():
            results["fgr_file"] = str(fgr_path)
        return results

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        return results

if __name__ == "__main__":
    initialize_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1.0,
        target_func=initialize_pdf_project,
    )

    update_nonRefineValue_and_constraintSet_runner = ProcessPollingMcpToolBase(
            timeout_sec=60.0,
            poll_interval=1.0,
            target_func=update_nonRefineValue_and_constraintSet,
        )
    
    update_refine_state_runner = ProcessPollingMcpToolBase(
            timeout_sec=60.0,
            poll_interval=1.0,
            target_func=update_refine_state,
        )

    auto_spacegroup_constraints_runner = ProcessPollingMcpToolBase(
        timeout_sec=60.0,
        poll_interval=1.0,
        target_func=auto_spacegroup_constraints,
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
            "UpdateConstraintSet and UpdateRefineState to edit snapshot state, and ExportCIFs to collect the generated outputs. "
            "Every tool call must include the current Agent session_id so all outputs stay under "
            "WORKDIR/user_id/session_id/mission_name. "
            "Mission/task rule: mission_name identifies one high-level mission and should stay the same across multiple tool calls; "
            "task_id identifies a sub-task/step inside that mission."
        ),
        port=int(os.getenv("MCP_PDF_REFINE_PORT", "42507")),
    )

    initialize_runner.register_mcp_tool(mcp, name="InitializeProject")
    refine_runner.register_mcp_tool(mcp, name="Refine")
    update_nonRefineValue_and_constraintSet_runner.register_mcp_tool(mcp, name="UpdateNonRefineValueAndConstraintSet")
    auto_spacegroup_constraints_runner.register_mcp_tool(mcp, name="AutoSpaceGroupConstraints")
    update_refine_state_runner.register_mcp_tool(mcp, name="UpdateRefineState")
    export_cifs_runner.register_mcp_tool(mcp, name="ExportCIFs")
    mcp.run(transport="sse")

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

    # result1 = auto_spacegroup_constraints(
    #     project_path=init_result["project_path"],
    #     constrain_positions=True,
    #     constrain_temperature_factors=True,
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="auto_spacegroup_constraints"
    # )

    # print(f"auto_spacegroup_constraints result: {result1}")


    

    # result2 = update_nonRefineValue_and_constraintSet(
    #     project_path=init_result["project_path"],
    #     nonRefineValue={
    #         "phase[1].stepcut": 0.0,
    #         "phase[1].rcut": 0.0,
    #     },
    #     constraintSet={
    #             "params": ["phase[1].pscale", "phase[1].lat.a", "phase[1].lat.b", "phase[1].lat.c"],
    #             "constraints": ["@1","@2","@2","@3"]
    #     },
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="update_nonRefine_and_constraintSet"
    # )

    # print(f"update_nonRefineValue_and_constraintSet result: {result2}")

    # result3 = update_refine_state(
    #     project_path=result2["project_path"],
    #     refineState=[
    #         ["@1", 1.0, True],
    #     ],
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="update_refine_state"
    # )

    # print(f"update_refine_state result: {result3}")

    # result4 = refine(
    #     project_path=result3["project_path"],
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="refine"
    # )

    # print(f"refine result: {result4}")

    # result5 = export_cifs(
    #     rstr_files=result4["project_content"]["structure_files"],
    #     user_id=user_id,
    #     mission_name=mission_name,
    #     task_id="export_cif"
    # )

    # print(f"export_cifs result: {result5}")

    