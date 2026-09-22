"""Small wrapper around GSAS-II's k-vector search implementation.

This module exposes a GUI-free function for searching candidate magnetic
propagation vectors from observed extra/satellite peak d-spacings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib
import importlib.util
from pathlib import Path
import sys
from typing import Sequence
from types import ModuleType
import warnings


GSASII_ROOT = Path(__file__).resolve().parents[1]
GSASII_DIR = GSASII_ROOT / "GSASII"
GSASII_BIN_DIR = GSASII_DIR / "bin"

for path in (GSASII_DIR, GSASII_BIN_DIR):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)


def _load_k_vector_search() -> ModuleType:
    module_path = GSASII_DIR / "k_vector_search.py"
    spec = importlib.util.spec_from_file_location("gsasii_k_vector_search", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load GSAS-II k-vector search from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


kvs = _load_k_vector_search()


def _require_seekpath() -> None:
    try:
        seekpath = importlib.import_module("seekpath")
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "GSAS-II k-vector search requires the Python package 'seekpath'. "
            "Install it in the Python environment used to run this wrapper."
        ) from exc

    kvs.seekpath = seekpath


def _require_kvec_general() -> None:
    try:
        kvec_general = importlib.import_module("kvec_general")
    except (ImportError, ModuleNotFoundError) as exc:
        raise ModuleNotFoundError(
            "option=2 requires GSAS-II's compiled 'kvec_general' extension. "
            "Use option=0/1 or run with a Python version/platform matching "
            "the binary in GSASII/bin."
        ) from exc

    kvs.parallel_proc = kvec_general.parallel_proc
    kvs.gen_option_avail = True


@dataclass(frozen=True)
class KVectorCandidate:
    """One candidate propagation vector returned by GSAS-II's search."""

    kx: float
    ky: float
    kz: float
    indicator_distance: float
    average_delta_d: float
    max_delta_d: float
    setting: str = "conventional"


@dataclass(frozen=True)
class CifStructureInput:
    """Structure fields parsed from a CIF file for k-vector search."""

    bravais_symbol: str
    cell: list[list[float]]
    positions: list[list[float]]
    atom_types: list[str]


def generate_nuclear_hkls(max_index: int = 5) -> list[list[int]]:
    """Generate a simple non-negative hkl grid matching the GSAS-II GUI default."""
    if max_index < 0:
        raise ValueError("max_index must be greater than or equal to 0")

    return [
        [h, k, l]
        for h in range(max_index + 1)
        for k in range(max_index + 1)
        for l in range(max_index + 1)
    ]


def lattice_parameters_to_vectors(
    lattice_parameters: Sequence[float],
) -> list[list[float]]:
    """Convert [a, b, c, alpha, beta, gamma] to GSAS-II lattice vectors."""
    if len(lattice_parameters) != 6:
        raise ValueError(
            "lattice_parameters must contain [a, b, c, alpha, beta, gamma]"
        )

    return kvs.lat_params_to_vec(list(lattice_parameters))


def atom_types_to_numbers(atom_types: Sequence[str]) -> list[int]:
    """Map repeated atom-type labels to the integer IDs required by seekpath."""
    return kvs.unique_id_gen(list(atom_types))


def _ase_read_cif_without_gsasii_noise(cif_path: str | Path):
    """Read a GSAS-II CIF with ASE while suppressing known non-structural noise."""
    from ase.io import read as ase_read

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Wrong number 2 of tokens, expected 0: .*",
            category=UserWarning,
            module=r"ase\.io\.cif",
        )
        warnings.filterwarnings(
            "ignore",
            message=r"crystal system '.*' is not interpreted for space group .* This may result in wrong setting!",
            category=UserWarning,
            module=r"ase\.io\.cif",
        )
        return ase_read(str(cif_path), index=0)


def parse_cif_structure(
    cif_path: str | Path,
    bravais_symbol: str | None = None,
) -> CifStructureInput:
    """Parse cell, fractional coordinates and atom types from a CIF file."""
    try:
        from ase.io import read as ase_read
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Reading cif_path requires the Python package 'ase'. Install it in "
            "the Python environment used to run this wrapper."
        ) from exc

    _require_seekpath()

    atoms = _ase_read_cif_without_gsasii_noise(cif_path)
    cell = atoms.cell.array.tolist()
    positions = atoms.get_scaled_positions(wrap=True).tolist()
    atom_types = atoms.get_chemical_symbols()

    if bravais_symbol is None:
        numbers = atom_types_to_numbers(atom_types)
        structure = (cell, positions, numbers)
        bravais_symbol = kvs.seekpath.get_path(structure, True)["bravais_lattice"]

    return CifStructureInput(
        bravais_symbol=bravais_symbol,
        cell=cell,
        positions=positions,
        atom_types=atom_types,
    )


def search_candidate_k_vectors(
    bravais_symbol: str | None = None,
    cif_path: str | Path | None = None,
    cell: Sequence[Sequence[float]] | None = None,
    lattice_parameters: Sequence[float] | None = None,
    positions: Sequence[Sequence[float]] | None = None,
    numbers: Sequence[int] | None = None,
    atom_types: Sequence[str] | None = None,
    satellite_d_spacings: Sequence[float] | None = None,
    nuclear_hkls: Sequence[Sequence[int]] | None = None,
    threshold: float = 0.001,
    option: int = 0,
    kstep: Sequence[float] | None = None,
    processes: int = 1,
    return_dicts: bool = True,
) -> list[dict[str, float | str]] | list[KVectorCandidate]:
    """Search candidate magnetic propagation vectors from satellite peaks.

    Args:
        bravais_symbol: seekpath/GSAS-II Bravais symbol such as ``P``, ``oF``,
            ``cI`` or ``hR``. Inferred from ``cif_path`` when omitted.
        cif_path: CIF file for the parent nuclear structure. When provided,
            missing ``cell``, ``positions`` and ``atom_types`` are parsed from it.
        cell: 3x3 lattice vectors. Provide either ``cell`` or
            ``lattice_parameters`` when ``cif_path`` is not provided.
        lattice_parameters: [a, b, c, alpha, beta, gamma] in Angstrom/degrees.
        positions: Nx3 fractional atomic coordinates in the parent cell.
        numbers: Integer atom IDs required by seekpath. Provide either
            ``numbers`` or ``atom_types``.
        atom_types: Atom labels such as ["Mn", "O", "O"], converted to IDs.
        satellite_d_spacings: Observed extra/satellite peak positions in d.
        nuclear_hkls: Parent nuclear hkl list. Defaults to GSAS-II GUI's
            non-negative 0..5 grid when omitted.
        threshold: delta d / d stopping threshold used by GSAS-II.
        option: 0 high-symmetry points, 1 points plus paths, 2 full grid search.
        kstep: k-grid/path step. Required only to tune option 2; GSAS-II uses
            [0.01, 0.01, 0.01] when omitted.
        processes: Number of workers for option 2.
        return_dicts: Return plain dictionaries by default. Set False to return
            ``KVectorCandidate`` instances.

    Returns:
        Up to ten candidate propagation vectors in the conventional reciprocal
        cell setting, sorted by GSAS-II's indicator distance.
    """
    if cif_path is not None:
        cif_structure = parse_cif_structure(cif_path, bravais_symbol=bravais_symbol)
        bravais_symbol = cif_structure.bravais_symbol
        if cell is None and lattice_parameters is None:
            cell = cif_structure.cell
        if positions is None:
            positions = cif_structure.positions
        if atom_types is None and numbers is None:
            atom_types = cif_structure.atom_types

    if bravais_symbol is None:
        raise ValueError("Provide bravais_symbol, or provide cif_path so it can be inferred")

    if cell is None:
        if lattice_parameters is None:
            raise ValueError("Provide either cell or lattice_parameters")
        cell = lattice_parameters_to_vectors(lattice_parameters)

    if positions is None or len(positions) == 0:
        raise ValueError("positions must contain at least one fractional coordinate")

    if numbers is None:
        if atom_types is None:
            raise ValueError("Provide either numbers or atom_types")
        numbers = atom_types_to_numbers(atom_types)

    if len(numbers) != len(positions):
        raise ValueError("numbers/atom_types length must match positions length")

    if satellite_d_spacings is None or len(satellite_d_spacings) == 0:
        raise ValueError("satellite_d_spacings must contain at least one d-spacing")

    if nuclear_hkls is None:
        nuclear_hkls = generate_nuclear_hkls()

    if option not in (0, 1, 2):
        raise ValueError("option must be 0, 1, or 2")

    _require_seekpath()
    if option == 2:
        _require_kvec_general()

    search = kvs.kVector(
        bravais_symbol,
        [list(vector) for vector in cell],
        [list(position) for position in positions],
        list(numbers),
        [list(hkl) for hkl in nuclear_hkls],
        list(satellite_d_spacings),
        threshold,
        option=option,
        kstep=None if kstep is None else list(kstep),
        processes=processes,
    )
    k_vectors, distances, average_delta_d, max_delta_d = search.kOptFinder()

    candidates = [
        KVectorCandidate(
            kx=float(k_conv[0]),
            ky=float(k_conv[1]),
            kz=float(k_conv[2]),
            indicator_distance=float(distance),
            average_delta_d=float(ave_dd),
            max_delta_d=float(max_dd),
        )
        for k_conv, distance, ave_dd, max_dd in zip(
            (search.kVecPrimToConv(k_vec) for k_vec in k_vectors),
            distances,
            average_delta_d,
            max_delta_d,
        )
    ]

    if return_dicts:
        return [asdict(candidate) for candidate in candidates]

    return candidates


__all__ = [
    "CifStructureInput",
    "KVectorCandidate",
    "atom_types_to_numbers",
    "generate_nuclear_hkls",
    "lattice_parameters_to_vectors",
    "parse_cif_structure",
    "search_candidate_k_vectors",
]

if __name__ == "__main__":
    cif_path = "/home/wangh/rongzai-agent/tools/GSAS-II-master/tests/kSearchTest/HABSRefine.cif"  # Replace with the actual path to your CIF file
    dSpacing = [6.6574, 3.1483, 2.2207, 1.97, 1.8854]  # Replace with the actual d-spacing values you want to use
    
    # Example usage of the search_candidate_k_vectors function
    candidates = search_candidate_k_vectors(
        cif_path=cif_path,
        satellite_d_spacings=dSpacing,
    )
    for candidate in candidates:
        print(candidate)