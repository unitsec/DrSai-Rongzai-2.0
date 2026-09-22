"""Headless magnetic subgroup search and phase construction for GSAS-II."""

from __future__ import annotations

import ast
import copy
from fractions import Fraction
import importlib
import itertools
from pathlib import Path
import sys
from typing import Any, Sequence
import warnings

import numpy as np


GSASII_ROOT = Path(__file__).resolve().parents[1]
GSASII_DIR = GSASII_ROOT / "GSASII"
GSASII_IMPORTS_DIR = GSASII_DIR / "imports"
GSASII_BIN_DIR = GSASII_DIR / "bin"

for path in (GSASII_DIR, GSASII_IMPORTS_DIR, GSASII_BIN_DIR):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

G2elem = importlib.import_module("GSASIIElem")
G2lat = importlib.import_module("GSASIIlattice")
G2spc = importlib.import_module("GSASIIspc")
G2sub = importlib.import_module("SUBGROUPS")
CIFPhaseReader = importlib.import_module("G2phase_CIF").CIFPhaseReader

# Bilbao's current TLS certificate covers cryst.ehu.es, but not
# www.cryst.ehu.es. Override the legacy GSAS-II URL so HTTPS works.
G2sub.bilbaoSite = "https://cryst.ehu.es/cgi-bin/cryst/programs/"
G2sub.submagSite = G2sub.bilbaoSite + "subgrmag1_general_GSAS.pl?"


def load_parent_phase(cif_path: str | Path) -> dict[str, Any]:
    """Read one parent nuclear phase from a CIF with GSAS-II's CIF reader."""
    path = Path(cif_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"CIF file does not exist: {path}")

    reader = CIFPhaseReader()
    if not reader.Reader(str(path)):
        detail = reader.errors or reader.warnings or "unknown CIF read error"
        raise ValueError(f"GSAS-II could not read CIF {path}: {detail}")
    return reader.Phase


def _format_k_component(value: str | int | float) -> str:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("k-vector components cannot be blank")
        try:
            Fraction(text)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"Invalid k-vector component: {value!r}") from exc
        return text

    return str(Fraction(float(value)).limit_denominator(24))


def format_k_vectors(
    k_vectors: Sequence[Sequence[str | int | float]],
) -> list[str]:
    """Convert one to three k-vectors to Bilbao's nine-field representation."""
    if not 1 <= len(k_vectors) <= 3:
        raise ValueError("k_vectors must contain one, two, or three vectors")

    fields: list[str] = []
    for vector in k_vectors:
        if len(vector) != 3:
            raise ValueError("Each k-vector must contain exactly three components")
        fields.extend(_format_k_component(value) for value in vector)
    fields.extend([" "] * (9 - len(fields)))
    return fields


def _parse_bilbao_numeric(value: str) -> Any:
    """Parse Bilbao lists containing numeric fractions without using eval."""
    expression = ast.parse(value, mode="eval")

    def convert(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return convert(node.body)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [convert(item) for item in node.elts]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = convert(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp):
            left = convert(node.left)
            right = convert(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError(f"Unsupported Bilbao numeric expression: {value!r}")

    return convert(expression)


def _parse_bilbao_bns_numbers(page: str) -> dict[int, str]:
    """Map Bilbao result IDs to the BNS numbers omitted by GSAS-II's parser."""
    rows = page.replace("&", "\n").split("\n")
    start = next((index + 1 for index, row in enumerate(rows) if "yesz" in row), 0)
    bns_numbers: dict[int, str] = {}
    for row in rows[start:]:
        for item in row.split("GGG"):
            fields = item.split("z")
            if len(fields) < 8:
                continue
            try:
                bns_numbers[int(fields[0])] = fields[3]
            except (ValueError, IndexError):
                continue
    return bns_numbers


def _get_bilbao_subgroups(
    parent_sg_data: dict[str, Any],
    formatted_k_vectors: list[str],
    use_whole_star: bool,
    landau_only: bool,
    maximal_only: bool,
) -> tuple[list[Any] | None, list[int] | None, dict[int, str]]:
    """Call GSAS-II's parser while retaining BNS numbers from the raw page."""
    original_post_url = G2sub.GSASIIpath.postURL
    captured: dict[str, str] = {}

    def capture_page(url: str, data: Any, **kwargs: Any) -> str | None:
        page = original_post_url(url, data, **kwargs)
        if page:
            captured["page"] = page
        return page

    G2sub.GSASIIpath.postURL = capture_page
    try:
        results, base_list = G2sub.GetNonStdSubgroupsmag(
            parent_sg_data,
            formatted_k_vectors,
            star=use_whole_star,
            landau=landau_only,
            maximal=maximal_only,
        )
    finally:
        G2sub.GSASIIpath.postURL = original_post_url

    return results, base_list, _parse_bilbao_bns_numbers(captured.get("page", ""))


def _enumerate_k0_orbit_stabilizers(
    cif_path: str | Path,
    selected_parent_indices: set[int],
) -> dict[str, dict[str, Any]]:
    """Enumerate exact k=0 magnetic stabilizers for the selected CIF orbit."""
    try:
        import spglib
        from ase.io import read as ase_read
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "exact_wyckoff_orbit=True requires the 'ase' and 'spglib' packages"
        ) from exc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        atoms = ase_read(str(cif_path), index=0)

    source_indices = atoms.arrays.get("spacegroup_kinds")
    if source_indices is None:
        raise ValueError("ASE did not provide CIF spacegroup_kinds for orbit mapping")
    orbit_indices = [
        index for index, source_index in enumerate(source_indices)
        if int(source_index) in selected_parent_indices
    ]
    if not orbit_indices:
        raise ValueError("Selected magnetic atoms could not be mapped to the full CIF orbit")
    if len(orbit_indices) > 2:
        raise NotImplementedError(
            "Exact Wyckoff-orbit stabilizer enumeration currently supports at "
            "most two magnetic positions in the expanded cell"
        )

    moment_vectors = [
        vector
        for vector in itertools.product(range(-2, 3), repeat=3)
        if vector != (0, 0, 0)
    ]
    representatives: dict[str, dict[str, Any]] = {}
    for orbit_moments in itertools.product(moment_vectors, repeat=len(orbit_indices)):
        moments = np.zeros((len(atoms), 3), dtype=float)
        for atom_index, moment in zip(orbit_indices, orbit_moments):
            moments[atom_index] = moment
        dataset = spglib.get_magnetic_symmetry_dataset(
            (
                atoms.cell.array,
                atoms.get_scaled_positions(wrap=True),
                atoms.get_atomic_numbers(),
                moments,
            ),
            is_axial=True,
            symprec=1e-4,
            mag_symprec=1e-4,
        )
        if dataset is None:
            continue
        group_type = spglib.get_magnetic_spacegroup_type(dataset.uni_number)
        if group_type is None:
            continue
        bns_number = str(group_type.bns_number)
        representatives.setdefault(
            bns_number,
            {
                "uni_number": int(dataset.uni_number),
                "bns_number": bns_number,
                "orbit_indices": orbit_indices,
                "fractional_coordinates": [
                    atoms.get_scaled_positions(wrap=True)[index].tolist()
                    for index in orbit_indices
                ],
                "moments": [list(map(float, moment)) for moment in orbit_moments],
            },
        )
    return representatives


def _normalized_magnetic_symbol(symbol: str) -> str:
    return symbol.replace(" ", "").replace("_", "").lower()


def _repair_magnetic_group(candidate: dict[str, Any], target_symbol: str) -> None:
    """Repair spin generators when GSAS-II misreads a compact Bilbao symbol."""
    sg_data = candidate["SGData"]
    target = _normalized_magnetic_symbol(target_symbol)
    if _normalized_magnetic_symbol(sg_data.get("MagSpGrp", "")) == target:
        return

    generator_count = max(0, len(sg_data.get("SGSpin", [])) - 1)
    for signs in itertools.product((-1, 1), repeat=generator_count):
        trial = copy.deepcopy(sg_data)
        trial["SGSpin"] = [1, *signs]
        trial["SpnFlp"] = G2spc.GenMagOps(trial)[1]
        generated_symbol = G2spc.MagSGSym(trial)
        if _normalized_magnetic_symbol(generated_symbol) == target:
            trial["MagSpGrp"] = generated_symbol
            candidate["SGData"] = trial
            return


def _representative_moment_scale_for_atom(
    atom_coordinates: Sequence[float],
    candidate: dict[str, Any],
    orbit_model: dict[str, Any],
) -> float | None:
    parent_coordinates = (
        np.inner(atom_coordinates, candidate["Trans"].T) - candidate["Uvec"]
    ) % 1.0
    differences = np.array(orbit_model["fractional_coordinates"]) - parent_coordinates
    differences -= np.rint(differences)
    distances = np.linalg.norm(differences, axis=1)
    match_index = int(np.argmin(distances))
    if distances[match_index] > 1e-4:
        return None

    parent_moment = np.array(orbit_model["moments"][match_index], dtype=float)
    max_norm = max(np.linalg.norm(moment) for moment in orbit_model["moments"])
    if max_norm == 0:
        return 0.0
    sign = np.sign(next((value for value in parent_moment if abs(value) > 1e-10), 1.0))
    return float(sign * np.linalg.norm(parent_moment) / max_norm)


def _select_parent_atoms(
    phase: dict[str, Any],
    magnetic_atom_types: Sequence[str] | None,
    magnetic_atom_labels: Sequence[str] | None,
) -> tuple[list[list[Any]], set[int]]:
    cx, ct, _, _ = phase["General"]["AtomPtrs"]
    requested_types = set(magnetic_atom_types or [])
    requested_labels = set(magnetic_atom_labels or [])
    selected: list[list[Any]] = []
    selected_indices: set[int] = set()

    for index, atom in enumerate(phase["Atoms"]):
        label = atom[ct - 1]
        atom_type = atom[ct]
        explicitly_selected = atom_type in requested_types or label in requested_labels
        has_magnetic_form_factor = bool(G2elem.GetMFtable([atom_type], [2.0]))
        automatically_selected = (
            not requested_types
            and not requested_labels
            and atom_type != "O"
            and has_magnetic_form_factor
        )
        if explicitly_selected or automatically_selected:
            if explicitly_selected and not has_magnetic_form_factor:
                raise ValueError(
                    f"Selected atom {label!r} ({atom_type}) has no GSAS-II "
                    "magnetic form factor"
                )
            selected.append(atom)
            selected_indices.add(index)

    if not selected:
        criteria = []
        if requested_types:
            criteria.append(f"types={sorted(requested_types)}")
        if requested_labels:
            criteria.append(f"labels={sorted(requested_labels)}")
        suffix = ", ".join(criteria) or "automatic magnetic-form-factor selection"
        raise ValueError(f"No magnetic atoms matched the CIF phase ({suffix})")

    return selected, selected_indices


def _wyckoff_position_type(
    coordinates: Sequence[float],
    sg_data: dict[str, Any],
) -> tuple[str, int, int]:
    """Classify a site as a general or special Wyckoff position."""
    multiplicity = int(G2spc.SytSym(coordinates, sg_data)[1])
    general_multiplicity = int(
        G2spc.SytSym([0.123457, 0.234569, 0.345673], sg_data)[1]
    )
    position_type = "general" if multiplicity == general_multiplicity else "special"
    return position_type, multiplicity, general_multiplicity


def _test_magnetic_atoms(
    candidate: dict[str, Any],
    magnetic_atoms: Sequence[list[Any]],
    parent_sg_data: dict[str, Any],
    all_have_moment: bool,
    max_unique: int,
    maximal_only: bool,
    wyckoff_position_type: str | None,
    preserve_parent_wyckoff_type: bool,
) -> list[list[Any]]:
    """Apply the same magnetic-site filtering used by the GSAS-II GUI."""
    transform = candidate["Trans"]
    origin_shift = candidate["Uvec"]
    inverse_transform = np.linalg.inv(transform)
    atom_codes: list[str] = []
    test_phase = {
        "General": {
            "AtomPtrs": [2, 1],
            "SGData": copy.deepcopy(candidate["SGData"]),
        },
        "Atoms": [],
    }
    any_moment = False
    forbidden_site_found = False
    wyckoff_mismatch_found = False

    for atom in magnetic_atoms:
        parent_position_type = _wyckoff_position_type(atom[3:6], parent_sg_data)[0]
        parent_positions = [
            generated[0]
            for generated in G2spc.GenAtom(atom[3:6], parent_sg_data, False, Move=True)
        ]
        atom_codes.extend(["1"] * len(parent_positions))
        parent_positions, atom_codes = G2lat.ExpandCell(
            parent_positions, atom_codes, 0, transform
        )
        for position in parent_positions:
            transformed = G2lat.TransformXYZ(
                position - origin_shift,
                inverse_transform.T,
                np.zeros(3),
            ) % 1.0
            test_phase["Atoms"].append(atom[:2] + list(transformed))
            _, _, _, duplicate_directions = G2spc.SytSym(
                transformed, candidate["SGData"]
            )
            constraints = G2spc.GetCSpqinel(
                candidate["SGData"]["SpnFlp"], duplicate_directions
            )
            candidate_position_type = _wyckoff_position_type(
                transformed, candidate["SGData"]
            )[0]
            site_allows_moment = any(constraints[0])
            any_moment = any_moment or site_allows_moment
            if all_have_moment and not site_allows_moment:
                forbidden_site_found = True
            if (
                wyckoff_position_type is not None
                and candidate_position_type != wyckoff_position_type
            ):
                wyckoff_mismatch_found = True
            if (
                preserve_parent_wyckoff_type
                and candidate_position_type != parent_position_type
            ):
                wyckoff_mismatch_found = True

    unique_atoms = G2lat.GetUnique(test_phase, atom_codes)[0]
    candidate["Keep"] = (
        any_moment
        and len(unique_atoms) <= max_unique
        and not forbidden_site_found
        and not wyckoff_mismatch_found
        and not (maximal_only and candidate["supList"][0])
    )
    return unique_atoms


def _initial_moment(constraints: Sequence[Sequence[float]], magnitude: float) -> list[float]:
    parameter_ids, multipliers = constraints
    independent_ids = [int(value) for value in parameter_ids if value]
    if not independent_ids:
        return [0.0, 0.0, 0.0]
    selected_id = independent_ids[0]
    return [
        float(magnitude * multiplier) if parameter_id == selected_id else 0.0
        for parameter_id, multiplier in zip(parameter_ids, multipliers)
    ]


def _constraint_relations(
    constraints: Sequence[Sequence[float]],
    transform: np.ndarray,
) -> tuple[list[list[float]], list[list[float]]]:
    parameter_ids, multipliers = constraints
    child_relations: list[list[float]] = []
    parent_relations: list[list[float]] = []
    for parameter_id in sorted({int(value) for value in parameter_ids if value}):
        child = np.array(
            [
                float(multiplier) if int(component_id) == parameter_id else 0.0
                for component_id, multiplier in zip(parameter_ids, multipliers)
            ]
        )
        parent = np.inner(child, np.linalg.inv(transform))
        nonzero = next((abs(value) for value in parent if abs(value) > 1e-10), 1.0)
        parent /= nonzero
        child_relations.append(child.tolist())
        parent_relations.append(np.around(parent, 10).tolist())
    return child_relations, parent_relations


def _build_magnetic_phase(
    parent_phase: dict[str, Any],
    candidate: dict[str, Any],
    selected_parent_indices: set[int],
    initial_moment: float,
    orbit_model: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    phase = copy.deepcopy(parent_phase)
    phase["General"]["Name"] = f'{parent_phase["General"]["Name"]}-{candidate["Name"]}'
    phase["General"]["SGData"] = copy.deepcopy(candidate["SGData"])
    phase["General"]["Cell"][1:] = candidate["Cell"][:]
    phase["General"]["MagDmin"] = 1.0
    phase["MagXform"] = (
        candidate["Trans"],
        candidate["Uvec"],
        np.zeros(3),
    )
    phase, atom_codes = G2lat.TransformPhase(
        parent_phase,
        phase,
        candidate["Trans"],
        candidate["Uvec"],
        np.zeros(3),
        True,
    )

    cx, ct, _, _ = phase["General"]["AtomPtrs"]
    retained_atoms: list[list[Any]] = []
    retained_codes: list[str] = []
    site_summaries: list[dict[str, Any]] = []

    for atom, code in zip(phase["Atoms"], atom_codes):
        source_index = int(code.split("+", 1)[0].split(":", 1)[0])
        if source_index not in selected_parent_indices:
            continue
        site_symmetry, _, _, duplicate_directions = G2spc.SytSym(
            atom[cx : cx + 3], candidate["SGData"]
        )
        magnetic_site_symmetry = G2spc.MagSytSym(
            site_symmetry, duplicate_directions, candidate["SGData"]
        )
        constraints = G2spc.GetCSpqinel(
            candidate["SGData"]["SpnFlp"], duplicate_directions
        )
        position_type, multiplicity, general_multiplicity = _wyckoff_position_type(
            atom[cx : cx + 3], candidate["SGData"]
        )
        parent_position_type, parent_multiplicity, parent_general_multiplicity = (
            _wyckoff_position_type(
                parent_phase["Atoms"][source_index][3:6],
                parent_phase["General"]["SGData"],
            )
        )
        representative_scale = (
            _representative_moment_scale_for_atom(
                atom[cx : cx + 3],
                candidate,
                orbit_model,
            )
            if orbit_model is not None
            else None
        )
        if orbit_model is not None:
            if representative_scale is None:
                continue
            moment = _initial_moment(
                constraints, initial_moment * representative_scale
            )
        else:
            moment = _initial_moment(constraints, initial_moment)
        if orbit_model is None and not any(constraints[0]):
            continue
        child_relations, parent_relations = _constraint_relations(
            constraints, candidate["Trans"]
        )
        atom[cx + 4 : cx + 7] = moment
        retained_atoms.append(atom)
        retained_codes.append(code)
        site_summaries.append(
            {
                "label": atom[ct - 1],
                "atom_type": atom[ct],
                "fractional_coordinates": [float(value) for value in atom[cx : cx + 3]],
                "magnetic_site_symmetry": magnetic_site_symmetry,
                "wyckoff_position_type": position_type,
                "multiplicity": multiplicity,
                "general_multiplicity": general_multiplicity,
                "parent_wyckoff_position_type": parent_position_type,
                "parent_multiplicity": parent_multiplicity,
                "parent_general_multiplicity": parent_general_multiplicity,
                "parameter_ids": [int(value) for value in constraints[0]],
                "multipliers": [float(value) for value in constraints[1]],
                "child_basis_constraint_relations": child_relations,
                "parent_basis_constraint_relations": parent_relations,
                "initial_moment": moment,
                "initial_moment_source": (
                    "exact_orbit_stabilizer" if orbit_model is not None else "site_symmetry"
                ),
            }
        )

    phase["Atoms"] = retained_atoms
    atom_types = list(dict.fromkeys(atom[ct] for atom in retained_atoms))
    phase["General"]["AtomTypes"] = atom_types
    phase["General"]["Lande g"] = len(atom_types) * [2.0]
    phase["MagAtomCodes"] = retained_codes
    G2elem.SetupGeneral(phase, None)
    return phase, site_summaries


def search_magnetic_structure_candidates(
    cif_path: str | Path,
    k_vectors: Sequence[Sequence[str | int | float]],
    magnetic_atom_types: Sequence[str] | None = None,
    magnetic_atom_labels: Sequence[str] | None = None,
    *,
    use_whole_star: bool = False,
    landau_only: bool = False,
    maximal_only: bool = False,
    all_have_moment: bool = False,
    max_unique: int = 100,
    wyckoff_position_type: str | None = None,
    preserve_parent_wyckoff_type: bool = False,
    exact_wyckoff_orbit: bool = False,
    initial_moment: float = 1.0,
    kept_only: bool = True,
) -> list[dict[str, Any]]:
    """Search magnetic subgroups and build initial GSAS-II magnetic phases.

    The magnetic subgroup list is obtained from Bilbao k-SUBGROUPSMAG, so this
    function requires network access. Returned ``phase`` values are GSAS-II
    phase dictionaries suitable for further integration into a GPX workflow.
    """
    if max_unique < 1:
        raise ValueError("max_unique must be at least 1")
    if initial_moment <= 0:
        raise ValueError("initial_moment must be greater than 0")
    if landau_only and len(k_vectors) > 1:
        raise ValueError("Landau filtering is not compatible with multiple k-vectors")
    if wyckoff_position_type not in (None, "general", "special"):
        raise ValueError("wyckoff_position_type must be 'general', 'special', or None")
    if exact_wyckoff_orbit and any(
        Fraction(_format_k_component(value)) != 0
        for vector in k_vectors
        for value in vector
    ):
        raise NotImplementedError(
            "exact_wyckoff_orbit currently supports k=(0,0,0) only"
        )

    parent_phase = load_parent_phase(cif_path)
    parent_sg_data = parent_phase["General"]["SGData"]
    magnetic_atoms, selected_parent_indices = _select_parent_atoms(
        parent_phase, magnetic_atom_types, magnetic_atom_labels
    )
    formatted_k_vectors = format_k_vectors(k_vectors)
    orbit_stabilizers = (
        _enumerate_k0_orbit_stabilizers(cif_path, selected_parent_indices)
        if exact_wyckoff_orbit
        else {}
    )

    results, base_list, bns_numbers = _get_bilbao_subgroups(
        parent_sg_data,
        formatted_k_vectors,
        use_whole_star,
        landau_only,
        maximal_only,
    )
    if results is None:
        raise ConnectionError(
            "Bilbao k-SUBGROUPSMAG did not respond; check network access"
        )
    if not results:
        return []

    candidates: list[dict[str, Any]] = []
    seen_bns_numbers: set[str] = set()
    for result in results:
        bns_number = bns_numbers.get(result[3], "")
        if exact_wyckoff_orbit:
            if bns_number not in orbit_stabilizers or bns_number in seen_bns_numbers:
                continue
            seen_bns_numbers.add(bns_number)
        transform = np.array(_parse_bilbao_numeric(result[2][0]), dtype=float)
        origin_shift = np.array(_parse_bilbao_numeric(result[2][1]), dtype=float)
        candidate = G2lat.makeBilbaoPhase(result[:2], origin_shift, transform, True)
        _repair_magnetic_group(candidate, result[0])
        candidate["gid"] = result[3]
        candidate["altList"] = result[4]
        candidate["supList"] = _parse_bilbao_numeric(result[5])
        candidate["Cell"] = G2lat.TransformCell(
            parent_phase["General"]["Cell"][1:7], transform
        )
        unique_atoms = _test_magnetic_atoms(
            candidate,
            magnetic_atoms,
            parent_sg_data,
            all_have_moment,
            max_unique,
            maximal_only,
            wyckoff_position_type,
            preserve_parent_wyckoff_type,
        )
        if exact_wyckoff_orbit:
            candidate["Keep"] = True
        if kept_only and not candidate["Keep"]:
            continue

        magnetic_phase, sites = _build_magnetic_phase(
            parent_phase,
            candidate,
            selected_parent_indices,
            initial_moment,
            orbit_stabilizers.get(bns_number),
        )
        candidates.append(
            {
                "paramagnetic_space_group": parent_sg_data["SpGrp"],
                "paramagnetic_gray_group": f'{parent_sg_data["SpGrp"]} 1\'',
                "magnetic_space_group": candidate["SGData"]["MagSpGrp"],
                "bns_symbol": result[1],
                "bns_number": bns_number,
                "gid": result[3],
                "is_base_candidate": result[3] in base_list,
                "keep": bool(candidate["Keep"]),
                "transformation_matrix": transform.tolist(),
                "origin_shift": origin_shift.tolist(),
                "cell": [float(value) for value in candidate["Cell"]],
                "unique_tested_site_count": len(unique_atoms),
                "magnetic_sites": sites,
                "orbit_stabilizer_model": orbit_stabilizers.get(bns_number),
                "phase": magnetic_phase,
            }
        )

    return candidates


__all__ = [
    "format_k_vectors",
    "load_parent_phase",
    "search_magnetic_structure_candidates",
]

if __name__ == "__main__":
    cif_path = GSASII_ROOT / "tests" / "kSearchTest" / "HABSRefine.cif"
    candidates = search_magnetic_structure_candidates(
        cif_path=cif_path,
        k_vectors=[["0", "0", "0"]],
        magnetic_atom_types=["Mn"],
        exact_wyckoff_orbit=True,
        initial_moment=1.5,
    )

    print(f"Found {len(candidates)} magnetic structure candidates")
    for candidate in candidates:
        print(
            candidate
        )