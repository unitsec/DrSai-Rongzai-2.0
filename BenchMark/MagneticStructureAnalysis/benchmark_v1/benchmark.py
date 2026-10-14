"""Prepare a blind case and compare periodic magnetic structures offline."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import dataclass
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import Any

import CifFile
import numpy as np
from numpy.typing import NDArray
from scipy.linalg import orthogonal_procrustes
from scipy.optimize import linear_sum_assignment
import spglib


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
GSASII = HERE.parents[2] / "tools" / "GSAS-II-master" / "GSASII"
sys.path.insert(0, str(GSASII))
sys.path.insert(0, str(GSASII / "bin"))
with redirect_stdout(sys.stderr):
    import GSASIIElem
    import GSASIIlattice as G2lat

Array = NDArray[np.float64]


class InvalidStructure(ValueError):
    """The submitted file cannot describe a consistent periodic structure."""


@dataclass
class Structure:
    lattice: Array
    positions: Array
    species: list[str]
    moments: Array


def number(value: Any) -> float:
    text = re.sub(r"\(\d+\)$", "", str(value).strip())
    try:
        result = float(text)
    except ValueError as exc:
        raise InvalidStructure(f"Invalid numeric value: {value!r}") from exc
    if not math.isfinite(result):
        raise InvalidStructure(f"Non-finite numeric value: {value!r}")
    return result


def affine(expression: str) -> Array:
    """Parse a CIF coordinate expression without executing submitted code."""
    def visit(node: ast.AST) -> Array:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name) and node.id in ("x", "y", "z"):
            result = np.zeros(4)
            result[("x", "y", "z").index(node.id)] = 1
            return result
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return np.array([0, 0, 0, number(node.value)])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                if not np.any(left[:3]):
                    return left[3] * right
                if not np.any(right[:3]):
                    return right[3] * left
            if isinstance(node.op, ast.Div) and not np.any(right[:3]) and right[3] != 0:
                return left / right[3]
        raise InvalidStructure(f"Unsupported symmetry expression: {expression!r}")

    try:
        return visit(ast.parse(expression.strip().lower(), mode="eval"))
    except SyntaxError as exc:
        raise InvalidStructure(f"Invalid symmetry expression: {expression!r}") from exc


def operation(text: str) -> tuple[Array, Array, int]:
    fields = str(text).split(",")
    if len(fields) != 4:
        raise InvalidStructure(f"Expected x,y,z,time-reversal operation: {text!r}")
    rows = np.array([affine(field) for field in fields[:3]])
    rotation, translation = rows[:, :3], rows[:, 3]
    spin = number(fields[3])
    if spin not in (-1, 1) or not np.isclose(abs(np.linalg.det(rotation)), 1):
        raise InvalidStructure(f"Invalid magnetic operation: {text!r}")
    if not np.allclose(rotation, np.rint(rotation), atol=1e-8):
        raise InvalidStructure("Symmetry rotations must be integral in the supplied cell")
    return rotation, translation, int(spin)


def field(block: Any, name: str, *, optional: bool = False) -> Any:
    for key in (name, name.replace(".", "_")):
        if key in block:
            return block[key]
    if optional:
        return None
    raise InvalidStructure(f"Missing required CIF field: {name}")


def periodic_distance(delta: Array, lattice: Array) -> Array:
    wrapped = delta - np.rint(delta)
    # Nearest Cartesian image need not be the component-wise nearest image.
    images = np.array(list(itertools.product((-1, 0, 1), repeat=3)))
    return np.min(np.linalg.norm((wrapped[..., None, :] + images) @ lattice, axis=-1), axis=-1)


def read_structure(path: Path) -> Structure:
    if not path.is_file():
        raise InvalidStructure(f"Submission does not exist: {path}")
    if path.stat().st_size > 2_000_000:
        raise InvalidStructure("MCIF exceeds the 2 MB submission limit")
    try:
        cif = CifFile.ReadCif(str(path))
    except (CifFile.StarError, CifFile.CifError, ValueError) as exc:
        raise InvalidStructure(f"Cannot parse CIF: {exc}") from exc
    blocks = [cif[key] for key in cif.keys() if "_atom_site_fract_x" in cif[key]]
    if len(blocks) != 1:
        raise InvalidStructure("Submit exactly one structure block")
    block = blocks[0]
    cell = [number(field(block, "_cell_" + name)) for name in
            ("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma")]
    if min(cell[:3]) <= 0 or any(not 0 < angle < 180 for angle in cell[3:]):
        raise InvalidStructure("Invalid unit cell")
    alpha, beta, gamma = np.deg2rad(cell[3:])
    metric_factor = (1 + 2 * np.cos(alpha) * np.cos(beta) * np.cos(gamma)
                     - np.cos(alpha)**2 - np.cos(beta)**2 - np.cos(gamma)**2)
    if metric_factor <= 1e-10:
        raise InvalidStructure("Degenerate unit cell")
    lattice = np.asarray(G2lat.cell2AB(cell)[0].T, dtype=float)
    axes = lattice / np.linalg.norm(lattice, axis=1)[:, None]
    labels = list(field(block, "_atom_site_label"))
    species = list(field(block, "_atom_site_type_symbol"))
    coordinates = [field(block, "_atom_site_fract_" + axis) for axis in "xyz"]
    if not labels or len(labels) > 256 or len(set(labels)) != len(labels):
        raise InvalidStructure("Require 1-256 uniquely labelled independent sites")
    if any(len(column) != len(labels) for column in [species, *coordinates]):
        raise InvalidStructure("Inconsistent atom-site loop lengths")
    positions = np.array([[number(column[i]) for column in coordinates] for i in range(len(labels))])
    elements = []
    for symbol in species:
        match = re.fullmatch(r"([A-Z][a-z]?)(?:\d*[+-])?", str(symbol))
        if match is None:
            raise InvalidStructure(f"Unsupported element symbol: {symbol}")
        elements.append(match.group(1))
    moment_labels = list(field(block, "_atom_site_moment.label"))
    moment_columns = [field(block, "_atom_site_moment.crystalaxis_" + axis) for axis in "xyz"]
    if not moment_labels or len(set(moment_labels)) != len(moment_labels):
        raise InvalidStructure("Missing or duplicate magnetic site labels")
    if any(len(column) != len(moment_labels) for column in moment_columns):
        raise InvalidStructure("Inconsistent magnetic-moment loop lengths")
    moments = np.zeros((len(labels), 3))
    for i, label in enumerate(moment_labels):
        if label not in labels:
            raise InvalidStructure(f"Magnetic site is not an atom site: {label}")
        moments[labels.index(label)] = np.array([number(column[i]) for column in moment_columns]) @ axes
    operations = list(field(block, "_space_group_symop_magn_operation.xyz"))
    centers = field(block, "_space_group_symop_magn_centering.xyz", optional=True)
    if centers is None:
        centers = ["x,y,z,+1"]
    if not operations or not centers or len(operations) * len(centers) > 512:
        raise InvalidStructure("Require 1-512 combined magnetic symmetry operations")
    expanded_positions: list[Array] = []
    expanded_species: list[str] = []
    expanded_moments: list[Array] = []
    for op_text, center_text in itertools.product(operations, centers):
        rotation, shift, spin = operation(op_text)
        center_r, center_t, center_s = operation(center_text)
        shift = center_r @ shift + center_t
        rotation = center_r @ rotation
        spin *= center_s
        cart_rotation = np.linalg.inv(lattice) @ rotation.T @ lattice
        if not np.allclose(cart_rotation @ cart_rotation.T, np.eye(3), atol=0.02):
            raise InvalidStructure("Symmetry operation is incompatible with the unit-cell metric")
        transformed_moments = spin * np.linalg.det(rotation) * moments @ cart_rotation
        for i, position in enumerate((positions @ rotation.T + shift) % 1):
            matches = [j for j, old in enumerate(expanded_positions)
                       if float(periodic_distance(position - old, lattice)) < 1e-5]
            if matches:
                j = matches[0]
                if expanded_species[j] != elements[i]:
                    raise InvalidStructure("Different elements occupy the same site")
                if not np.allclose(expanded_moments[j], transformed_moments[i], atol=1e-4):
                    raise InvalidStructure("Moments conflict at a symmetry-equivalent site")
            else:
                expanded_positions.append(position)
                expanded_species.append(elements[i])
                expanded_moments.append(transformed_moments[i])
    if len(expanded_positions) > 1024:
        raise InvalidStructure("Expanded cell exceeds 1024 atoms")
    return Structure(lattice, np.array(expanded_positions), expanded_species, np.array(expanded_moments))


def primitive(structure: Structure, position_tol: float) -> Structure:
    types = {symbol: i + 1 for i, symbol in enumerate(sorted(set(structure.species)))}
    dataset = spglib.get_symmetry_dataset(
        (structure.lattice, structure.positions, [types[s] for s in structure.species]),
        symprec=1e-4,
    )
    if dataset is None:
        raise InvalidStructure("Cannot determine the chemical translation lattice")
    reduced = spglib.niggli_reduce(dataset.primitive_lattice, eps=1e-7)
    if reduced is None:
        raise InvalidStructure("Cannot reduce the primitive lattice")
    lattice = np.asarray(reduced)
    positions = (structure.positions @ structure.lattice @ np.linalg.inv(lattice)) % 1
    kept: list[int] = []
    for i, position in enumerate(positions):
        matches = [j for j in kept if structure.species[j] == structure.species[i]
                   and float(periodic_distance(position - positions[j], lattice)) < position_tol]
        if matches:
            if not np.allclose(structure.moments[i], structure.moments[matches[0]], atol=1e-4):
                raise InvalidStructure("Magnetic order does not repeat on the chemical primitive cell (not k=0)")
        else:
            kept.append(i)
    ratio = abs(np.linalg.det(structure.lattice) / np.linalg.det(lattice))
    if not np.isclose(ratio, len(positions) / len(kept), atol=1e-5):
        raise InvalidStructure("Primitive-cell atom count is inconsistent")
    return Structure(lattice, positions[kept], [structure.species[i] for i in kept], structure.moments[kept])


def lattice_mappings(reference: Array, candidate: Array, length_tol: float, angle_tol: float):
    lengths = np.linalg.norm(reference, axis=1)
    radius = max(lengths) * (1 + length_tol)
    bounds = np.ceil(radius * np.linalg.norm(np.linalg.inv(candidate), axis=0)).astype(int)
    if max(bounds) > 12:
        raise InvalidStructure("Reduced-cell lattice search exceeds supported bounds")
    vectors = np.array([v for v in itertools.product(*(range(-b, b + 1) for b in bounds))
                        if v != (0, 0, 0)])
    norms = np.linalg.norm(vectors @ candidate, axis=1)
    options = [vectors[np.abs(norms / length - 1) <= length_tol + 1e-10] for length in lengths]
    ref_cos = reference @ reference.T / np.outer(lengths, lengths)
    ref_angles = np.rad2deg(np.arccos(np.clip(ref_cos, -1, 1)))
    for rows in itertools.product(*options):
        transform = np.array(rows)
        if not np.isclose(abs(np.linalg.det(transform)), 1):
            continue
        mapped = transform @ candidate
        mapped_lengths = np.linalg.norm(mapped, axis=1)
        angles = np.rad2deg(np.arccos(np.clip(
            mapped @ mapped.T / np.outer(mapped_lengths, mapped_lengths), -1, 1)))
        if np.max(np.abs(angles - ref_angles)) > angle_tol + 1e-8:
            continue
        rotation, _ = orthogonal_procrustes(mapped, reference)
        yield transform, rotation


def _scale_ratio(reference_norms: Array, candidate_norms: Array) -> float | None:
    """Median candidate/reference magnitude ratio over the magnetic sites, or None.

    A submission may be a uniformly scaled version of the reference structure; this
    single number reports how far that shared scale is from the reference's own
    unconfirmed magnitude without deciding the verdict.
    """
    magnetic = reference_norms > 1e-6
    if not np.any(magnetic):
        return None
    return float(np.median(candidate_norms[magnetic] / reference_norms[magnetic]))


def compare(reference: Structure, candidate: Structure, rules: dict[str, Any]) -> dict[str, Any]:
    position_tol = rules["position_tolerance_angstrom"]
    # A reference whose moment magnitude is not experimentally confirmed cannot gate the
    # verdict: scoring it would pass only submissions that reproduce the unverified number
    # and fail fits that are physically correct. The protocol decides; magnitudes are
    # reported either way, and antiferromagnetic sites must still agree on whether a
    # moment exists at all.
    magnitude_scored = bool(rules.get("moment_magnitude_in_verdict", True))
    reversals = (1, -1) if rules.get("allow_global_time_reversal", True) else (1,)
    reference = primitive(reference, position_tol)
    candidate = primitive(candidate, position_tol)
    if Counter(reference.species) != Counter(candidate.species):
        return {"passed": False, "reason": "Different chemical contents in the primitive cell"}
    n = len(reference.species)
    ref_norm = np.linalg.norm(reference.moments, axis=1)
    if not np.any(ref_norm > 1e-6):
        raise InvalidStructure("Reference has no magnetic moments")
    mappings = 0
    for transform, rotation in lattice_mappings(
        reference.lattice, candidate.lattice,
        rules["lattice_length_relative_tolerance"], rules["lattice_angle_tolerance_degrees"],
    ):
        mappings += 1
        positions = candidate.positions @ np.linalg.inv(transform)
        moments = candidate.moments @ rotation * np.linalg.det(rotation)
        for anchor in range(n):
            if candidate.species[anchor] != reference.species[0]:
                continue
            shift = reference.positions[0] - positions[anchor]
            distances = periodic_distance(
                reference.positions[:, None, :] - (positions + shift)[None, :, :], reference.lattice)
            same_species = np.array([[a == b for b in candidate.species] for a in reference.species])
            for reversal in reversals:
                cand_norm = np.linalg.norm(moments, axis=1)
                magnitude_error = np.abs(ref_norm[:, None] - cand_norm[None, :])
                magnitude_ok = magnitude_error <= (
                    ref_norm[:, None] * rules["moment_relative_tolerance"] + 1e-6)
                # A moment appearing or vanishing is a structural difference whether or not
                # magnitudes are scored, so presence must still agree site by site.
                presence_ok = (ref_norm[:, None] > 1e-6) == (cand_norm[None, :] > 1e-6)
                dot = reference.moments @ (reversal * moments).T
                denominator = ref_norm[:, None] * cand_norm[None, :]
                cosine = np.divide(dot, denominator, out=np.zeros_like(dot), where=denominator > 1e-12)
                angles = np.rad2deg(np.arccos(np.clip(cosine, -1, 1)))
                angles[(ref_norm[:, None] < 1e-6) & (cand_norm[None, :] < 1e-6)] = 0
                valid = (same_species & (distances <= position_tol)
                         & (angles <= rules["moment_angle_tolerance_degrees"] + 1e-8)
                         & (magnitude_ok if magnitude_scored else presence_ok))
                row, col = linear_sum_assignment(np.where(valid, distances, 1e6))
                if not np.all(valid[row, col]):
                    continue
                magnetic = ref_norm[row] > 1e-6
                relative_error = magnitude_error[row, col] / np.maximum(ref_norm[row], 1e-6)
                return {
                    "passed": True, "reason": "Equivalent periodic magnetic structure",
                    "max_position_error_angstrom": float(np.max(distances[row, col])),
                    "max_moment_relative_error": float(np.max(relative_error[magnetic])) if np.any(magnetic) else 0.0,
                    "max_moment_angle_error_degrees": float(np.max(angles[row, col])),
                    "moment_scale_ratio": _scale_ratio(ref_norm[row], cand_norm[col]),
                    "magnitude_within_tolerance": bool(np.all(magnitude_ok[row, col])),
                    "magnitude_scored": magnitude_scored,
                    "global_time_reversal": reversal,
                    "candidate_basis_transform": transform.tolist(),
                }
    return {"passed": False, "reason": (
        "No equivalent lattice within tolerance" if mappings == 0
        else "No common site mapping with equivalent magnetic moments" if magnitude_scored
        else "No common site mapping with equivalent moment directions")}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def neutralized_cif(text: str) -> str:
    """Strip material-identifying metadata from the nuclear starting structure.

    The phase name survives a file rename and names the compound, which is exactly the
    prior this case forbids: an agent that recognises it can recall the ordered structure
    instead of deriving it. Element symbols stay, because scattering lengths and magnetic
    form factors need them.
    """
    return re.sub(r"(?m)^(\s*_(?:chemical_name|chemical_formula)\S*\s+).*$", r"\1'reference phase'", text)


def prepare(output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing run: {output}")
    banks = ("BS", "HA", "LA", "MA", "SE")
    sources: list[tuple[Path, str]] = [
        (SOURCE / "MnTe_初始核结构.cif", "input/initial.cif"),
        (HERE / "task.md", "TASK.md"),
    ]
    for bank in banks:
        sources.append((SOURCE / "Instrument_file_20251204" / f"{bank}_2025.instprm",
                        f"input/instruments/{bank}.instprm"))
        for run, label in (("2218", "low_temperature"), ("2221", "high_temperature")):
            folder = next(SOURCE.glob(f"RUN000{run}_*"))
            sources.append((folder / f"RUN000{run}_group{bank}.dat", f"input/{label}/{bank}.dat"))
    reference = SOURCE / "MnTe_最终磁结构.mcif"
    for source, _ in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
    read_structure(reference)
    output.mkdir(parents=True)
    records = []
    for source, relative in sources:
        target = output / "public" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".cif"):
            target.write_text(neutralized_cif(source.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copyfile(source, target)
        records.append({"path": relative, "sha256": digest(target)})
    private = output / "private"
    private.mkdir()
    shutil.copyfile(reference, private / "reference.mcif")
    shutil.copyfile(HERE / "protocol.json", private / "protocol.json")
    manifest = {
        "benchmark_id": "magnetic-structure-001-v1",
        "banks": list(banks),
        "data_columns": ["TOF", "INT", "ERR"],
        "header_lines": {"BS": 0, "HA": 0, "LA": 3, "MA": 0, "SE": 3},
        "temperature_metadata": "Relative high/low only; exact temperatures not provided.",
        "files": records,
    }
    (output / "public" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "submission").mkdir()
    print(json.dumps({"public_input": str(output / "public"),
                      "submission": str(output / "submission"),
                      "warning": "Mount only public/ and submission/ into the agent environment."}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    packing = commands.add_parser("prepare", help="Create a fresh blind case; never overwrites")
    packing.add_argument("--output", required=True, type=Path)
    grading = commands.add_parser("evaluate", help="Offline binary structure-equivalence verdict")
    grading.add_argument("--submission", required=True, type=Path)
    grading.add_argument("--reference", required=True, type=Path)
    grading.add_argument("--protocol", type=Path, default=HERE / "protocol.json")
    grading.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        try:
            prepare(args.output.resolve())
        except (InvalidStructure, OSError, StopIteration) as exc:
            print(f"PREPARATION ERROR: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        rules = json.loads(args.protocol.read_text(encoding="utf-8"))
        reference = read_structure(args.reference)
        primitive(reference, rules["position_tolerance_angstrom"])
    except (InvalidStructure, OSError, json.JSONDecodeError) as exc:
        print(f"EVALUATOR ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        result = compare(reference, read_structure(args.submission), rules)
    except InvalidStructure as exc:
        result = {"passed": False, "reason": f"Invalid submission: {exc}"}
    result.update({"benchmark_id": rules["benchmark_id"],
                   "verdict": "PASS" if result["passed"] else "FAIL",
                   "magnitude_in_verdict": bool(rules.get("moment_magnitude_in_verdict", True)),
                   "reference_sha256": digest(args.reference),
                   "submission_sha256": digest(args.submission) if args.submission.is_file() else None,
                   "protocol": rules})
    text = json.dumps(result, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
