"""Persisted, staged neutron TOF magnetic analysis/native subgroup refinement.

Only caller-supplied files are read. Importing this module never starts a server,
loads a project, or makes network requests.
"""

from __future__ import annotations

import copy
import importlib
import itertools
import json
import math
import warnings
from functools import wraps
from pathlib import Path
import re
import sys
import time

import numpy as np

from .science import (collinear_modes, difference_peaks, moment_to_cartesian,
                      moment_to_crystalaxis, crystal_axis_basis)
from .profiles import cached_tof_profiles

ROOT = Path(__file__).resolve().parents[1]


def _modules():
    for path in (ROOT, ROOT / "GSASII", ROOT / "GSASII" / "bin",
                 ROOT / "GSASII" / "imports"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    return (importlib.import_module("GSASIIscriptable"),
            importlib.import_module("GSASIIlattice"),
            importlib.import_module("GSASIIspc"),
            importlib.import_module("GSASIIElem"),
            importlib.import_module(
                "magnetic_structure_candidates.magnetic_structure_candidates"))


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fail rather than silently turning invalid scientific numbers into null.
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    return str(path.resolve())


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _slug(value):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)


def discover_inputs(input_dir):
    """Discover only the defined input tree, pairing banks by normalized names."""
    root = Path(input_dir).expanduser().resolve()
    if (root / "input").is_dir():
        root /= "input"
    if not root.resolve().is_relative_to(Path(input_dir).expanduser().resolve()):
        raise ValueError("Input subdirectory symlink escapes requested input root")
    cif = root / "initial.cif"
    if not cif.is_file():
        raise FileNotFoundError(f"Required nuclear CIF missing: {cif}")
    directories = [root / name for name in ("high_temperature", "low_temperature")]
    if not all(d.is_dir() for d in directories):
        raise FileNotFoundError("Need high_temperature and low_temperature input directories")
    def contained(path):
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Input symlink escapes requested input root: {path}")
    contained(cif)
    for directory in directories:
        contained(directory)
    extensions = {".dat", ".xye", ".gsa", ".fxye", ".gsas", ".xy"}
    banks = []
    mappings = []
    for directory in directories:
        files = sorted(p for p in directory.iterdir()
                       if p.is_file() and p.suffix.lower() in extensions)
        mapping = {}
        for path in files:
            contained(path)
            key = path.stem.lower()
            if key in mapping:
                raise ValueError(f"Ambiguous bank name {key} in {directory}")
            mapping[key] = path
        mappings.append(mapping)
    if not mappings[0] or set(mappings[0]) != set(mappings[1]):
        raise ValueError("High/low bank names must match exactly (case insensitive)")
    instrument_files = []
    for directory in (root / "instruments", root):
        if directory.is_dir():
            contained(directory)
            instrument_files.extend(
                p for p in directory.iterdir() if p.is_file()
                and p.suffix.lower() in {".instprm", ".prm", ".iparm", ".ins"})
    for path in instrument_files:
        contained(path)
    for bank in mappings[0]:
        # Named matching: bank stem occurs as a token in the instrument stem,
        # or instrument and bank have exactly the same normalized stem.
        candidates = [p for p in instrument_files if (
            p.stem.lower() == bank
            or re.search(rf"(^|[^a-z0-9]){re.escape(bank)}([^a-z0-9]|$)",
                         p.stem.lower()))]
        if len(candidates) != 1:
            raise ValueError(
                f"Bank {bank!r} needs exactly one name-matched instrument; "
                f"found {[p.name for p in candidates]}. Rename parameters to {bank}.instprm."
            )
        banks.append({"name": bank, "high": str(mappings[0][bank]),
                      "low": str(mappings[1][bank]), "instrument": str(candidates[0])})
    return {"input_dir": str(root), "cif": str(cif), "banks": banks}


def _histogram(project, bank, temperature):
    source = Path(bank[temperature])
    datafile, hint, point_data = str(source), None, None
    if source.suffix.lower() in {".dat", ".xy", ".xye"}:
        rows, started, text_format = [], False, True
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "!", "'")):
                continue
            try:
                values = [float(v) for v in line.split()]
            except ValueError:
                if started:
                    raise ValueError(f"Non-numeric row inside TOF text data: {source}")
                if line.startswith(("BANK", "INS")):
                    text_format = False
                    break
                continue
            if len(values) != 3:
                text_format = False
                break
            started = True
            rows.append(values)
        if text_format and rows:
            arrays = np.asarray(rows)
            if not np.all(np.isfinite(arrays)) or np.any(arrays[:, 2] <= 0):
                raise ValueError(f"TOF text requires finite values and positive errors: {source}")
            prepared = Path(project.filename).parent / "prepared_inputs"
            prepared.mkdir(exist_ok=True)
            normalized = prepared / f"{_slug(bank['name'])}_{temperature}.xye"
            np.savetxt(normalized, arrays, header=f"Source: {source}")
            datafile, hint = str(normalized), "Topas"
            point_data = arrays
    hist = project.add_powder_histogram(datafile, bank["instrument"], fmthint=hint)
    hist.data["Comments"].append(f"Original supplied spectrum: {source}")
    kind = hist.data["Instrument Parameters"][0]["Type"][0]
    if kind != "PNT":
        raise ValueError(f"{bank['name']} is {kind}, not neutron TOF (PNT)")
    if point_data is not None:
        # XY(E) TOF is point-sampled intensity, not GSAS bin-edge counts.
        # Scriptable unconditionally shifts TOF positions by half a bin and
        # drops the last point, including non-GSAS XYE. Restore supplied points.
        x, y, error = point_data.T
        if np.any(np.diff(x) <= 0):
            raise ValueError(f"TOF positions must increase strictly: {source}")
        hist.data["data"][1] = [x.copy(), y.copy(), 1 / error**2,
                                np.zeros(len(x)), np.zeros(len(x)), np.zeros(len(x))]
        hist.data["Limits"] = [(float(x[0]), float(x[-1])), [float(x[0]), float(x[-1])]]
        hist.data["Comments"].append("Point-sampled TOF XYE coordinates retained; no bin-edge shift")
    return hist


def inspect_spectra(input_dir, output_dir, **peak_options):
    """Load real spectra/instruments in GSAS-II, align/scale and persist peaks."""
    sc, _, _, _, _ = _modules()
    inputs = discover_inputs(input_dir)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = sc.G2Project(newgpx=str(output / "inspection.gpx"))
    banks = []
    for bank in inputs["banks"]:
        high, low = [_histogram(project, bank, t) for t in ("high", "low")]
        diag, arrays = difference_peaks(
            tuple(high.getdata(k) for k in ("d", "yobs", "yweight")),
            tuple(low.getdata(k) for k in ("d", "yobs", "yweight")), **peak_options)
        data_path = output / f"difference_{_slug(bank['name'])}.npz"
        np.savez_compressed(data_path, **arrays)
        diag.update({"name": bank["name"], "instrument": bank["instrument"],
                     "difference_path": str(data_path),
                     "high_points": len(high.getdata("x")), "low_points": len(low.getdata("x"))})
        banks.append(diag)
    project.save()
    report = {"stage": "inspect", "inputs": inputs, "banks": banks,
              "gpx_path": str(output / "inspection.gpx")}
    path = _write(output / "inspect_report.json", report)
    return {"stage": "inspect", "report_path": path, "gpx_path": report["gpx_path"],
            "bank_count": len(banks), "peak_count": sum(len(b["peaks"]) for b in banks)}


def _integer_indexing(cell, d_values, max_index=8, tolerance=0.01,
                      fit_dilation=False, max_dilation=0.03):
    """Test the reciprocal lattice, WITHOUT nuclear systematic-absence filtering."""
    _, lat, _, _, _ = _modules()
    metric = lat.cell2Gmat(cell)[0]
    hkls = np.array([h for h in itertools.product(range(-max_index, max_index + 1), repeat=3)
                     if any(h)], dtype=float)
    predicted = 1 / np.sqrt(np.einsum("ni,ij,nj->n", hkls, metric, hkls))
    dilation = 0.0
    if fit_dilation and len(d_values) >= 3:
        scales = np.linspace(1 - max_dilation, 1 + max_dilation, 601)
        observed = np.asarray(d_values)
        errors = np.min(abs(predicted[None, :, None] * scales[:, None, None]
                            - observed[None, None, :]) / observed[None, None, :], axis=1)
        dilation = float(scales[np.argmin(np.mean(errors**2, axis=1))] - 1)
        predicted *= 1 + dilation
    matches = []
    for d in d_values:
        index = int(np.argmin(abs(predicted - d)))
        delta = float(abs(predicted[index] - d) / d)
        matches.append({"d": float(d), "hkl": hkls[index].astype(int).tolist(),
                        "relative_delta_d": delta, "indexed": delta <= tolerance,
                        "fitted_lattice_dilation": dilation})
    return matches


def _propagation_peaks(banks, min_relative_significance=0.04, min_significance=10):
    """Cross-bank, non-overlapping peaks suppress subtraction/impurity noise."""
    eligible = []
    for bank in banks:
        maximum = max((p["significance"] for p in bank["peaks"]), default=0)
        for peak in bank["peaks"]:
            if (not peak["nuclear_overlap"]
                    and peak["significance"] >= max(min_significance, maximum * min_relative_significance)):
                eligible.append({**peak, "bank": bank["name"]})
    clusters = []
    for peak in sorted(eligible, key=lambda p: -p["significance"]):
        match = next((c for c in clusters if abs(c[0]["d"] - peak["d"]) <
                      max(c[0]["width_d"], peak["width_d"], peak["d"] * 0.003)), None)
        if match is None:
            clusters.append([peak])
        else:
            match.append(peak)
    accepted = []
    required = 2 if len(banks) > 1 else 1
    for cluster in clusters:
        names = sorted({p["bank"] for p in cluster})
        if len(names) < required:
            continue
        weights = np.array([1 / max(p["width_d"], p["d"] * 0.001)**2 for p in cluster])
        accepted.append({"d": float(np.average([p["d"] for p in cluster], weights=weights)),
                         "banks": names, "significance": max(p["significance"] for p in cluster),
                         "width_d": min(p["width_d"] for p in cluster)})
    return accepted


def search_propagation(input_dir, output_dir, *, k_vectors=None,
                       max_index=8, indexing_tolerance=0.01, search_option=0):
    """Use measured peaks for k=0 indexing; wrapper searches noninteger extras."""
    if not 1 <= max_index <= 16 or search_option not in (0, 1):
        raise ValueError("max_index must be 1..16 and search_option 0 or 1 (no unbounded grid)")
    output = Path(output_dir).resolve()
    inspection = _read(output / "inspect_report.json")
    inputs = discover_inputs(input_dir)
    if inputs != inspection["inputs"]:
        raise ValueError("Inspection belongs to different inputs; rerun inspect")
    _, _, _, _, wrapper = _modules()
    parent = wrapper.load_parent_phase(inputs["cif"])
    corroborated = _propagation_peaks(inspection["banks"])
    strongest = max((p["significance"] for p in corroborated), default=0)
    peaks = [p for p in corroborated if p["significance"] >= strongest * 0.05]
    ds = sorted({round(p["d"], 5) for p in peaks})
    matches = _integer_indexing(parent["General"]["Cell"][1:7], ds,
                                max_index, indexing_tolerance, fit_dilation=True)
    unexplained = [m["d"] for m in matches if not m["indexed"]]
    vectors = []
    if matches and not unexplained:
        vectors.append({"k": [0.0, 0.0, 0.0], "source": "all extra peaks index on parent reciprocal lattice"})
    wrapper_results = []
    if unexplained:
        kv = importlib.import_module("k_vector_candidate_search.k_vector_candidate_search")
        wrapper_results = kv.search_candidate_k_vectors(
            cif_path=inputs["cif"], satellite_d_spacings=unexplained,
            nuclear_hkls=kv.generate_nuclear_hkls(max_index=min(max_index, 8)),
            option=search_option, processes=1)
        vectors.extend({"k": [r["kx"], r["ky"], r["kz"]], "source": "GSAS-II k search",
                        "indicator_distance": r["indicator_distance"]} for r in wrapper_results)
    if k_vectors is not None:
        supplied = np.asarray(k_vectors, dtype=float)
        if supplied.ndim != 2 or supplied.shape[1] != 3 or not np.all(np.isfinite(supplied)):
            raise ValueError("k_vectors must be a finite Nx3 list")
        vectors = [{"k": v.tolist(), "source": "caller supplied; not a measured determination"}
                   for v in supplied]
    report = {
        "stage": "search", "inputs": inputs, "propagation_peaks": peaks,
        "lower_confidence_corroborated_peaks": [p for p in corroborated if p not in peaks],
        "integer_lattice_matches": matches,
        "k_candidates": vectors, "wrapper_results": wrapper_results,
        "diagnostics": {
            "decision": ("k=0 is consistent, not uniquely established" if matches and not unexplained
                         else "noninteger peak positions require propagation search" if unexplained
                         else "No extra peaks: k is undetermined; overlapping magnetic intensity may exist"),
            "nuclear_absences": "An integer hkl nuclear-forbidden peak can be magnetic at k=0.",
            "indexing_tolerance": indexing_tolerance,
            "peak_selection": "Non-nuclear-overlapping peaks >=10 sigma and >=4% of bank "
                              "maximum significance, corroborated by two banks when available; "
                              "primary indexing uses >=5% of strongest corroborated significance. "
                              "Weaker peaks retained separately, not used to force a propagation.",
            "lattice_dilation": "Common isotropic initial-CIF to low-T dilation fitted within ±3%; "
                                "requires >=3 positions. This is evidence, not a lattice refinement.",
            "warning": "Peak positions alone cannot establish magnetic order or a unique k.",
        },
    }
    path = _write(output / "search_report.json", report)
    return {"stage": "search", "report_path": path, "k_candidates": vectors,
            "diagnostics": report["diagnostics"]}


def _expanded_parent(parent):
    _, lat, _, _, _ = _modules()
    atoms, codes = lat.FillUnitCell(parent, True)
    cx, ct, _, _ = parent["General"]["AtomPtrs"]
    vectors = lat.cell2AB(parent["General"]["Cell"][1:7])[0].T
    types = [a[ct] for a in atoms]
    numbers = [sorted(set(types)).index(t) + 1 for t in types]
    positions = np.array([a[cx:cx + 3] for a in atoms]) % 1
    return atoms, codes, vectors, positions, numbers


def _magnetic_dataset(vectors, positions, numbers, moments):
    import spglib
    dataset = spglib.get_magnetic_symmetry_dataset(
        (vectors, positions, numbers, moment_to_cartesian(moments, vectors)),
        is_axial=True, symprec=1e-4, mag_symprec=1e-4)
    if dataset is None:
        raise RuntimeError("spglib failed to identify candidate magnetic symmetry")
    group = spglib.get_magnetic_spacegroup_type(dataset.uni_number)
    if group is None:
        raise RuntimeError("spglib failed to identify magnetic space-group type")
    return dataset, group


def _mode_fingerprint(moments, positions, numbers, vectors, symmetry):
    """Identify modes related by parent symmetry and global spin reversal."""
    cart = moment_to_cartesian(moments, vectors)
    A = vectors.T
    keys = []
    for rotation, translation in zip(symmetry["rotations"], symmetry["translations"]):
        transformed = (positions @ rotation.T + translation) % 1
        transformed_m = cart @ (np.linalg.det(rotation) * A @ rotation @ np.linalg.inv(A)).T
        permuted = np.zeros_like(cart)
        for i, position in enumerate(transformed):
            delta = positions - position
            delta -= np.rint(delta)
            compatible = np.array(numbers) == numbers[i]
            distances = np.linalg.norm(delta, axis=1)
            distances[~compatible] = np.inf
            j = int(np.argmin(distances))
            if distances[j] > 1e-4:
                raise RuntimeError("Parent symmetry does not map the expanded atomic orbit")
            permuted[j] = transformed_m[i]
        keys.extend(tuple(np.round(sign * permuted, 6).ravel()) for sign in (1, -1))
    return min(keys)


def _direction_space(vectors, positions, numbers, moments):
    """Exact common-collinear-vector subspace compatible with the stabilizer."""
    dataset, _ = _magnetic_dataset(vectors, positions, numbers, moments)
    cart = moment_to_cartesian(moments, vectors)
    indices = np.flatnonzero(np.linalg.norm(cart, axis=1) > 1e-8)
    reference = cart[indices[0]]
    signs = cart @ reference / np.dot(reference, reference)
    A = vectors.T
    rows = []
    for rotation, translation, reversal in zip(dataset.rotations, dataset.translations,
                                                dataset.time_reversals):
        operation = (-1 if reversal else 1) * np.linalg.det(rotation) * A @ rotation @ np.linalg.inv(A)
        for i in indices:
            target = (rotation @ positions[i] + translation) % 1
            delta = positions - target
            delta -= np.rint(delta)
            distance = np.linalg.norm(delta, axis=1)
            distance[np.asarray(numbers) != numbers[i]] = np.inf
            j = int(np.argmin(distance))
            if distance[j] > 1e-4:
                raise ValueError("Magnetic stabilizer does not map the selected atomic orbit")
            rows.extend(signs[j] * np.eye(3) - signs[i] * operation)
    _, singular, vh = np.linalg.svd(np.asarray(rows), full_matrices=False)
    rank = int(np.count_nonzero(singular > 1e-6))
    if rank == 3:
        raise ValueError("Candidate stabilizer forbids its proposed collinear moment")
    return {"direction_dimension": 3 - rank,
            "direction_basis_cartesian": vh[rank:].T.tolist(),
            "direction_constraints_crystalaxis": (vh[:rank] @ crystal_axis_basis(vectors)).tolist()}


def enumerate_local_candidates(input_dir, output_dir, *, magnetic_elements=None,
                         k_vectors=None, max_candidates=128):
    """Local k=0 sign/direction enumeration; Bilbao is never invoked.

    Reuses the existing wrapper's parent loader, magnetic-site selection and
    generic phase constructor, not its network subgroup endpoint.
    """
    import spglib
    inputs = discover_inputs(input_dir)
    output = Path(output_dir).resolve()
    search = _read(output / "search_report.json")
    if search["inputs"] != inputs:
        raise ValueError("Search belongs to different inputs")
    ks = k_vectors if k_vectors is not None else [v["k"] for v in search["k_candidates"]]
    if not ks:
        raise ValueError("Propagation is undetermined; supply a hypothesis k_vectors explicitly")
    if any(np.linalg.norm(np.asarray(k, float) - np.rint(k)) > 1e-8 for k in ks):
        raise NotImplementedError("Offline mode enumeration supports k=0 only; nonzero k requires "
                                  "a local symmetry-mode/supercell implementation, not a network fallback")
    _, _, _, _, wrapper = _modules()
    parent = wrapper.load_parent_phase(inputs["cif"])
    _, selected = wrapper._select_parent_atoms(parent, magnetic_elements, None)
    atoms, codes, vectors, positions, numbers = _expanded_parent(parent)
    magnetic_indices = [i for i, code in enumerate(codes)
                        if int(code.split("+", 1)[0].split(":", 1)[0]) in selected]
    symmetry = spglib.get_symmetry((vectors, positions, numbers), symprec=1e-4)
    if symmetry is None:
        raise RuntimeError("spglib failed to identify parent symmetry")
    modes, seen = [], set()
    for mode in collinear_modes(len(magnetic_indices), vectors, max_candidates):
        moments = np.zeros((len(atoms), 3))
        moments[magnetic_indices] = mode
        key = _mode_fingerprint(moments, positions, numbers, vectors, symmetry)
        if key in seen:
            continue
        seen.add(key)
        dataset, group = _magnetic_dataset(vectors, positions, numbers, moments)
        space = _direction_space(vectors, positions, numbers, moments)
        modes.append({
            "id": f"mode_{len(modes):03d}", "k": [0.0, 0.0, 0.0],
            "bns_number": str(group.bns_number), "uni_number": int(dataset.uni_number),
            "moment_crystalaxis": moments.tolist(),
            "magnetic_indices": magnetic_indices,
            "magnetic_symmetry_operations": int(dataset.n_operations),
            **space,
        })
    cx, ct, _, _ = parent["General"]["AtomPtrs"]
    report = {
        "stage": "candidates", "inputs": inputs, "magnetic_elements": magnetic_elements,
        "selected_parent_indices": sorted(selected), "modes": modes,
        "expanded_positions": positions.tolist(), "cell_vectors": vectors.tolist(),
        "expanded_atom_codes": codes,
        "expanded_types": [a[ct] for a in atoms],
        "diagnostics": {
            "method": "13 axis/diagonal + one generic normalized seed direction × all collinear "
                      "sign patterns, modulo nuclear symmetry/time reversal; spglib stabilizers "
                      "with all symmetry-allowed common-vector components refined",
            "limitations": "Finite collinear equal-magnitude stabilizer families, not exhaustive "
                          "noncollinear irreps. All symmetry-allowed common-vector components are "
                          "refined within each family's invariant subspace; powder-degenerate models reported.",
            "magnetic_site_count": len(magnetic_indices), "max_candidates": max_candidates,
            "moment_units": "Unit physical Cartesian norm; crystalaxis is a unit-axis basis, not fractional coordinates",
            "network": "disabled; no Bilbao calls",
        },
    }
    path = _write(output / "candidates_report.json", report)
    return {"stage": "candidates", "report_path": path, "candidate_count": len(modes),
            "candidates": [{"id": m["id"], "bns_number": m["bns_number"]} for m in modes],
            "diagnostics": report["diagnostics"]}


def _quality(project, *, calculation_only=False):
    cov = project.data["Covariance"]["data"]
    if not cov.get("Rvals") and not calculation_only:
        raise RuntimeError("GSAS-II did not produce a fresh refinement result (solver failure)")
    varying = cov.get("varyList", [])
    # GSAS may converge after removing singular variables; expose rather than hide.
    histograms, total, count = [], 0.0, 0
    for hist in project.histograms():
        x, obs, calc, weight = [np.asarray(hist.getdata(k), float)
                                for k in ("x", "yobs", "ycalc", "yweight")]
        lo, hi = hist.data["Limits"][1]
        mask = (x >= lo) & (x <= hi) & (weight > 0)
        for lower, upper in hist.data["Limits"][2:]:
            mask &= ~((x >= lower) & (x <= upper))
        if np.any(mask & ~np.isfinite(calc)):
            raise RuntimeError(f"Nonfinite calculated intensity in fitted range for {hist.name}")
        mask &= np.isfinite(obs) & np.isfinite(calc) & np.isfinite(weight)
        chisq = float(np.sum(weight[mask] * (obs[mask] - calc[mask])**2))
        if not math.isfinite(chisq) or not mask.any():
            raise RuntimeError(f"Invalid refinement residual for {hist.name}")
        total += chisq
        count += int(mask.sum())
        histograms.append({"name": hist.name, "rwp": float(hist.get_wR()),
                           "weighted_residual_sum": chisq, "points": int(mask.sum())})
    npar = len(varying)
    convergence = cov.get("Rvals", {}).get("converged")
    return {"histograms": histograms, "weighted_residual_sum": total,
            "points": count, "parameter_count": npar,
            "reduced_chisq": total / max(count - npar, 1),
            "bic_known_variance": total + npar * math.log(max(count, 1)),
            "vary_list": [str(v) for v in varying],
            "converged": None if convergence is None else bool(convergence),
            "max_shift_over_esd": float(cov.get("Rvals", {}).get("Max shft/sig", 0)),
            "refinement_warnings": str(cov.get("Rvals", {}).get("msg", "")),
            "removed_singular_parameters": [str(v) for v in cov.get("Rvals", {}).get("SVD0", [])]
            if isinstance(cov.get("Rvals", {}).get("SVD0"), (list, tuple)) else
            cov.get("Rvals", {}).get("SVD0", 0)}


def _refine(project, cycles, *, relative_tolerance=1e-6):
    from threadpoolctl import threadpool_limits
    project.set_Controls("cycles", cycles)
    project.data["Controls"]["data"]["min dM/M"] = relative_tolerance
    # GSAS-II has small least-squares matrices; large BLAS pools spend more
    # time synchronizing than computing and compete with parallel MCP workers.
    pwd = importlib.import_module("GSASIIpwd")
    boundary_parameters = []
    # An active-set update can introduce a new boundary on another atom.
    # Allow one bounded boundary pass per ADP site, plus two convergence passes.
    max_passes = 2 + sum(len(p.data["Atoms"]) for p in project.phases())
    convergence_passes = 0
    with threadpool_limits(limits=1, user_api="blas"), cached_tof_profiles(pwd) as cache_stats:
        for passes in range(1, max_passes + 1):
            # Scriptable ignores Refine's failure return and reloads the old
            # GPX covariance. Never mistake an earlier stage's convergence for
            # success of the current fit.
            project.data.setdefault("Covariance", {})["data"] = {}
            project.refine()
            # This GSAS version clips parmDict only after SetPhaseData, so the
            # persisted atom and computed profile still contain the violation.
            # Apply active ADP boundaries to atoms and recompute before ranking.
            clipped = []
            for phase in project.phases():
                cia = phase.data["General"]["AtomPtrs"][3]
                for index, atom in enumerate(phase.data["Atoms"]):
                    offsets = [1] if atom[cia] == "I" else [2, 3, 4]
                    for offset in offsets:
                        if atom[cia + offset] < 0:
                            atom[cia + offset] = 0.0
                            component = "iso" if offset == 1 else {2: "11", 3: "22", 4: "33"}[offset]
                            project.set_Frozen(
                                variable=f"{phase.data['pId']}::AU{component}:{index}", mode="add")
                            clipped.append(f"{phase.name}:atom{index}:ADP{offset}")
            boundary_parameters.extend(clipped)
            if clipped:
                if passes == max_passes:
                    raise RuntimeError("ADP boundary failed to stabilize within bounded active-set passes")
                continue
            convergence_passes += 1
            quality = _quality(project)
            quality["active_adp_boundaries"] = sorted(set(boundary_parameters))
            quality["frozen_parameters"] = [
                str(v) for v in project.data["Controls"]["data"].get(
                    "parmFrozen", {}).get("FrozenList", [])]
            quality["profile_cache"] = dict(cache_stats)
            quality["refinement_passes"] = passes
            quality["active_set_pass_limit"] = max_passes
            quality["relative_objective_tolerance"] = relative_tolerance
            if quality["converged"] is True:
                break
            if quality["weighted_residual_sum"] / max(quality["points"], 1) < 1e-8:
                quality["convergence_reason"] = "Numerical residual floor reached; original GSAS convergence flag retained"
                break
            if convergence_passes >= 2:
                raise RuntimeError("GSAS-II refinement did not converge within two bounded passes")
        else:
            raise RuntimeError("GSAS-II refinement did not converge within two bounded passes")
    project.save()
    return quality


def _physical_limits(project, phase):
    # Phase copying changes random IDs; refresh the GSAS global ID lookup before
    # constructing G2VarObj bounds, otherwise they target the discarded phase.
    project.index_ids()
    for variable in ("AUiso", "AU11", "AU22", "AU33"):
        project.set_Controls("parmMin", 0.0,
                             variable=f"{phase.data['pId']}::{variable}:*")

def _release_adp_boundaries(project):
    """Release active ADP bounds when the conditional physical model changes."""
    controls = project.data["Controls"]["data"]
    frozen = controls.setdefault("parmFrozen", {}).setdefault("FrozenList", [])
    released = [str(v) for v in frozen if "::AU" in str(v)]
    frozen[:] = [v for v in frozen if "::AU" not in str(v)]
    for phase in project.phases():
        _physical_limits(project, phase)
    return released

def _bounded_profile_fit(project, cycles, *, magnetic_name=None, reference_profiles=None):
    """Positive high-T profile fit with exact GSAS calculations and variable projection."""
    from scipy.optimize import least_squares, lsq_linear
    names = [h.name for h in project.histograms()]
    parameters = ["alpha", "beta-0", "sig-1", "X"]
    phase = project.phase("nuclear")
    phases = project.phases() if magnetic_name else [phase]
    original_use = {p.name: {n: v["Use"] for n, v in p.data["Histograms"].items()}
                    for p in phases}
    for p in phases:
        p.set_refinements({"Cell": False, "Atoms": {"all": " "}})
        p.set_HAP_refinements({"Scale": False})
        ct = p.data["General"]["AtomPtrs"][1]
        for atom in p.data["Atoms"]:
            atom[ct + 1] = ""
    diagnostics = []
    for name in names:
        for p in phases:
            for key in names:
                p.data["Histograms"][key]["Use"] = key == name and original_use[p.name][key]
        hist = project.histogram(name)
        background = hist.data["Background"][0]
        background[1] = False
        nback = background[2]
        background[3:] = [0.] * nback
        inst = hist.data["Instrument Parameters"][0]
        seed = np.array([max(float(inst[k][1]), 1e-6) for k in parameters])
        bound_seed = np.asarray(reference_profiles[name] if reference_profiles else seed)
        hist.data["Sample Parameters"]["Scale"][1] = False
        x, y, w = [np.asarray(hist.getdata(k), float) for k in ("x", "yobs", "yweight")]
        lower, upper = hist.data["Limits"][1]
        mask = (x >= lower) & (x <= upper) & (w > 0)
        for lo, hi in hist.data["Limits"][2:]:
            mask &= ~((x >= lo) & (x <= hi))
        # GSAS chebyschev background uses the fitted interval.
        z = 2 * (x - x[mask][0]) / (x[mask][-1] - x[mask][0]) - 1
        polynomials = np.polynomial.chebyshev.chebvander(z, nback - 1)
        sqrtw = np.sqrt(w[mask])
        state = {}
        def residual(log_values):
            nuclear = project.phase("nuclear")
            current = project.histogram(name)
            for p in project.phases() if magnetic_name else [nuclear]:
                p.data["Histograms"][name]["Scale"] = [1., False]
            current.data["Background"][0][3:] = [0.] * nback
            for parameter, value in zip(parameters, np.exp(log_values)):
                current.data["Instrument Parameters"][0][parameter][1] = float(value)
            project.set_Controls("cycles", 0)
            project.data["Covariance"]["data"] = {}
            current.data["data"][1][3][:] = np.nan
            project.refine()
            calculated = np.asarray(project.histogram(name).getdata("ycalc"), float)
            if not np.all(np.isfinite(calculated[mask])):
                raise RuntimeError("Bounded profile calculation did not produce a finite fresh profile")
            design = np.column_stack([calculated, polynomials])
            fit = lsq_linear(design[mask] * sqrtw[:, None], y[mask] * sqrtw,
                             bounds=([0.] + [-np.inf] * nback, np.inf),
                             tol=1e-9)
            if not fit.success:
                raise RuntimeError("Profile scale/background projection failed")
            state["linear"] = fit.x
            return (design[mask] @ fit.x - y[mask]) * sqrtw
        fit = least_squares(residual, np.log(seed),
                            bounds=(np.log(bound_seed / 100), np.log(bound_seed * 100)),
                            max_nfev=40, ftol=1e-6, xtol=1e-6, gtol=1e-6,
                            diff_step=1e-3)
        if not fit.success:
            raise RuntimeError(f"Bounded profile fit failed for {name}: {fit.message}")
        residual(fit.x)
        phase = project.phase("nuclear")
        for p in project.phases() if magnetic_name else [phase]:
            p.data["Histograms"][name]["Scale"] = [float(state["linear"][0]), True]
        project.histogram(name).data["Background"][0][3:] = state["linear"][1:].tolist()
        diagnostics.append({"histogram": name, "evaluations": fit.nfev,
                            "profile_parameters": dict(zip(parameters, np.exp(fit.x).tolist())),
                            "positive_parameter_bounds": {
                                p: [float(v / 100), float(v * 100)]
                                for p, v in zip(parameters, bound_seed)},
                            "weighted_residual_sum": float(2 * fit.cost)})
    for p in project.phases() if magnetic_name else [phase]:
        for name in names:
            p.data["Histograms"][name]["Use"] = original_use[p.name][name]
    for name in names:
        project.histogram(name).data["Background"][0][1] = True
    phase.set_refinements({"Cell": False, "Atoms": {"all": " "}})
    if magnetic_name:
        project.set_Controls("cycles", 0)
        project.refine()
        quality = _quality(project, calculation_only=True)
        quality["parameter_count"] = sum(
            1 + h.data["Background"][0][2] for h in project.histograms())
        quality["bic_known_variance"] = quality["weighted_residual_sum"] + (
            quality["parameter_count"] * math.log(quality["points"]))
        quality["reduced_chisq"] = quality["weighted_residual_sum"] / max(
            quality["points"] - quality["parameter_count"], 1)
    else:
        quality = _refine(project, cycles)
    quality["parameter_count"] += len(parameters) * len(names)
    quality["bic_known_variance"] += len(parameters) * len(names) * math.log(quality["points"])
    quality["bounded_profile_fits"] = diagnostics
    return quality

def _conditional_cell_fit(project):
    """Fit only parent-symmetry reciprocal metric terms, synchronizing phases."""
    from scipy.optimize import least_squares
    _, lat, _, _, _ = _modules()
    io = importlib.import_module("GSASIIstrIO")
    parent = project.phase("nuclear")
    sg = parent.data["General"]["SGData"]
    unique = lat.cellUnique(sg)
    initial = np.asarray(lat.cell2A(parent.data["General"]["Cell"][1:7]))
    scale = max(initial[:3])
    bank_data = []
    for hist in project.histograms():
        x, y, w = [np.asarray(hist.getdata(k)) for k in ("x", "yobs", "yweight")]
        lo, hi = hist.data["Limits"][1]
        mask = (x >= lo) & (x <= hi) & (w > 0)
        for lower, upper in hist.data["Limits"][2:]:
            mask &= ~((x >= lower) & (x <= upper))
        z = 2 * (x - x[mask][0]) / (x[mask][-1] - x[mask][0]) - 1
        sw = np.sqrt(w[mask])
        design = np.polynomial.chebyshev.chebvander(
            z, hist.data["Background"][0][2] - 1)[mask] * sw[:, None]
        q, r = np.linalg.qr(design, mode="reduced")
        wy = y[mask] * sw
        bank_data.append((hist.name, mask, sw, q, r, q.T @ wy, wy - q @ (q.T @ wy)))
    linear = {}
    def residual(delta):
        terms = initial.copy()
        terms[unique] += delta * scale
        parm = {f"0::A{i}": terms[i] for i in range(6)}
        metric, _ = io.cellFill("0::", sg, parm, {})
        if min(np.linalg.eigvalsh(lat.A2Gmat(metric, inverse=False))) <= 0:
            raise ValueError("Conditional cell metric is not positive definite")
        cell = lat.A2cell(metric)
        for p in project.phases():
            p.data["General"]["Cell"][1:7] = cell
            p.data["General"]["Cell"][7] = lat.calc_V(metric)
            for n in p.data["Histograms"]:
                p.data["Histograms"][n]["Scale"][0] = 1.
        for h in project.histograms():
            h.data["Background"][0][3:] = [0.] * h.data["Background"][0][2]
        project.set_Controls("cycles", 0)
        project.refine()
        errors = []
        for name, mask, sw, q, r, yq, yp in bank_data:
            calc = np.asarray(project.histogram(name).getdata("ycalc"), dtype=float)[mask] * sw
            cq = q.T @ calc
            cp = calc - q @ cq
            ratio = max(0., float(cp @ yp) / float(cp @ cp))
            linear[name] = (ratio, np.linalg.solve(r, yq - ratio * cq))
            errors.append(ratio * cp - yp)
        return np.concatenate(errors)
    fit = least_squares(residual, np.zeros(len(unique)), bounds=(-.03, .03),
                        diff_step=1e-3, max_nfev=30, ftol=1e-6, xtol=1e-7, gtol=1e-6)
    if not fit.success:
        raise RuntimeError("Conditional common-cell fit exceeded 30 evaluations")
    residual(fit.x)
    for p in project.phases():
        for name, (ratio, _) in linear.items():
            p.data["Histograms"][name]["Scale"][0] = ratio
    for name, (_, background) in linear.items():
        project.histogram(name).data["Background"][0][3:] = background.tolist()
    project.refine()
    return {"unique_reciprocal_metric_terms": unique, "parameter_count": len(unique),
            "evaluations": fit.nfev,
            "cell": project.phase("nuclear").data["General"]["Cell"][1:7],
            "weighted_residual_sum": float(2 * fit.cost)}


def refine_nuclear_baseline(input_dir, output_dir, *, cycles=24, background_coeffs=6,
                            refine_cell=True, refine_profile=True):
    """Refine high-T nuclear baseline, then low-T nuclear-only control."""
    if not 1 <= cycles <= 100 or not 1 <= background_coeffs <= 20:
        raise ValueError("cycles must be 1..100; background_coeffs 1..20")
    sc, _, _, _, _ = _modules()
    inputs = discover_inputs(input_dir)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    high = sc.G2Project(newgpx=str(output / "high_nuclear.gpx"))
    histograms = [_histogram(high, b, "high") for b in inputs["banks"]]
    phase = high.add_phase(inputs["cif"], phasename="nuclear", histograms=histograms)
    _physical_limits(high, phase)
    for hist in histograms:
        hist.set_refinements({"Background": {"no. coeffs": background_coeffs, "refine": True}})
        hist.data["Sample Parameters"]["Scale"][1] = False
    phase.set_HAP_refinements({"Scale": True})
    stages = [_refine(high, cycles)]
    if refine_cell:
        phase.set_refinements({"Cell": True})
        stages.append(_refine(high, cycles))
    # Refine ADPs only after a stable lattice/scale baseline.
    phase.set_refinements({"Atoms": {"all": "XU"}})
    if phase.data["General"]["SGData"].get("SGPolax"):
        phase.atoms()[0].refinement_flags = "U"
    stages.append(_refine(high, cycles))
    profile_comparison = {"selected": "fixed_input_instrument"}
    if refine_profile and stages[-1]["reduced_chisq"] > 3:
        fixed_path = output / "high_nuclear_fixed_profile.gpx"
        high.save(str(fixed_path))
        profile_comparison["alternatives"] = []
        best_quality = stages[-1]
        for name, parameters in (("positive_widths_and_tails", ["alpha", "beta-0", "sig-1", "X"]),):
            alternative = sc.G2Project(gpxfile=str(fixed_path))
            alternative.save(str(output / f"high_nuclear_effective_{name}.gpx"))
            diagnostic = {"model": name, "parameters_per_bank": parameters}
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("error", message="invalid value encountered",
                                            category=RuntimeWarning)
                    quality = _bounded_profile_fit(alternative, cycles)
                diagnostic["quality"] = quality
                if quality["bic_known_variance"] < best_quality["bic_known_variance"] - 6:
                    high = alternative
                    phase = high.phase("nuclear")
                    histograms = high.histograms()
                    best_quality = quality
                    profile_comparison["selected"] = f"high_T_effective_{name}"
            except Exception as exc:
                diagnostic["failure"] = {"type": type(exc).__name__, "error": str(exc)}
            profile_comparison["alternatives"].append(diagnostic)
        if best_quality is not stages[-1]:
            stages.append(best_quality)
        high.save(str(output / "high_nuclear.gpx"))
    low = sc.G2Project(newgpx=str(output / "low_nuclear.gpx"))
    lows = [_histogram(low, b, "low") for b in inputs["banks"]]
    low_phase = low.add_phase(inputs["cif"], phasename="nuclear", histograms=lows)
    haps = copy.deepcopy(low_phase.data["Histograms"])
    low_phase.data.update(copy.deepcopy(phase.data))
    low_phase.data["Histograms"] = haps
    low_phase.data["General"]["Name"] = low_phase.name
    _physical_limits(low, low_phase)
    inspection_path = output / "inspect_report.json"
    if refine_cell and inspection_path.is_file():
        inspection = _read(inspection_path)
        if inspection["inputs"] == inputs:
            strain = float(np.median([b["relative_d_strain"] for b in inspection["banks"]]))
            low_phase.data["General"]["Cell"][1:4] = [
                v * (1 + strain) for v in low_phase.data["General"]["Cell"][1:4]]
    low_phase.set_refinements({"Cell": refine_cell, "Atoms": {"all": "XU"}})
    if low_phase.data["General"]["SGData"].get("SGPolax"):
        low_phase.atoms()[0].refinement_flags = "U"
    for source, hist in zip(histograms, lows):
        hist.data["Background"] = copy.deepcopy(source.data["Background"])
        hist.data["Instrument Parameters"] = copy.deepcopy(source.data["Instrument Parameters"])
        for value in hist.data["Instrument Parameters"][0].values():
            if isinstance(value, list) and len(value) >= 3:
                value[2] = False
        hist.data["Sample Parameters"]["Scale"][1] = False
        low_phase.data["Histograms"][hist.name]["Scale"] = copy.deepcopy(
            phase.data["Histograms"][source.name]["Scale"])
    low_phase.set_HAP_refinements({"Scale": True})
    low_quality = _refine(low, cycles)
    low_profile_control = None
    if refine_profile:
        reference_profiles = {
            h.name: [max(float(h.data["Instrument Parameters"][0][k][1]), 1e-6)
                     for k in ("alpha", "beta-0", "sig-1", "X")]
            for h in low.histograms()}
        previous = low_quality["weighted_residual_sum"]
        control_steps = []
        for conditional_pass in range(3):
            profile = _bounded_profile_fit(low, cycles, magnetic_name="nuclear",
                                          reference_profiles=reference_profiles)
            cell = _conditional_cell_fit(low) if refine_cell else None
            _release_adp_boundaries(low)
            low.phase("nuclear").set_refinements({"Atoms": {"all": "U"}})
            low_quality = _refine(low, cycles)
            added = 4 * len(low.histograms()) + (cell["parameter_count"] if cell else 0)
            low_quality["parameter_count"] += added
            low_quality["bic_known_variance"] = low_quality["weighted_residual_sum"] + (
                low_quality["parameter_count"] * math.log(low_quality["points"]))
            low_quality["reduced_chisq"] = low_quality["weighted_residual_sum"] / max(
                low_quality["points"] - low_quality["parameter_count"], 1)
            change = abs(previous - low_quality["weighted_residual_sum"]) / max(previous, 1.)
            control_steps.append({"profile": profile, "cell": cell,
                                  "relative_objective_change": change})
            previous = low_quality["weighted_residual_sum"]
            if change < 1e-4:
                break
        low_profile_control = {"steps": control_steps, "stabilized": change < 1e-4,
                               "added_parameter_count": added}
    report = {"stage": "baseline", "inputs": inputs, "high_stages": stages,
              "low_nuclear_control": low_quality, "high_gpx_path": high.filename,
              "low_gpx_path": low.filename,
              "profile_comparison": profile_comparison,
              "low_temperature_profile_control": low_profile_control,
              "diagnostics": "High and low nuclear scale/ADPs fitted independently; instrument fixed. "
                             "High-T-only effective profile model compared by BIC when the "
                             "fixed-input nuclear residual is overdispersed; any fitted profile "
                             "initializes independent low-T effective-profile fitting and is not an independent "
                             "instrument calibration. Low-T nuclear-only control can absorb "
                             "overlapping magnetic intensity."}
    return {"report_path": _write(output / "baseline_report.json", report),
            "high_gpx_path": high.filename, "low_gpx_path": low.filename}


def _insert_magnetic(project, parent, candidates, mode):
    """Construct a magnetic-only P1 phase using existing generic wrapper."""
    _, lat, spc, elem, wrapper = _modules()
    _, sg = spc.SpcGroup("P 1")
    spc.SetMagnetic(sg)
    descriptor = {"Name": mode["id"], "SGData": sg, "Cell": parent["General"]["Cell"][1:],
                  "Trans": np.eye(3), "Uvec": np.zeros(3)}
    mag, _ = wrapper._build_magnetic_phase(
        parent, descriptor, set(candidates["selected_parent_indices"]), 1.0)
    # The generic wrapper inherits the GUI's 1 Å magnetic cutoff. Refinement
    # must instead use every measured bin/reflection, including high-Q data.
    mag["General"]["MagDmin"] = 0.0
    cx, ct, _, _ = mag["General"]["AtomPtrs"]
    source_positions = np.array(candidates["expanded_positions"])
    moments = np.array(mode["moment_crystalaxis"])
    thermal_sources = []
    _, expanded_codes, _, _, _ = _expanded_parent(parent)
    pcia = parent["General"]["AtomPtrs"][3]
    candidate_codes = candidates.get("expanded_atom_codes")
    current_codes = mag.get("MagAtomCodes", [])
    for atom_index, atom in enumerate(mag["Atoms"]):
        delta = source_positions - np.array(atom[cx:cx + 3])
        delta -= np.rint(delta)
        compatible = np.array(candidates["expanded_types"]) == atom[ct]
        distances = np.linalg.norm(delta, axis=1)
        distances[~compatible] = np.inf
        if candidate_codes is not None:
            code = current_codes[atom_index]
            if code not in candidate_codes:
                raise RuntimeError("Refined nuclear orbit differs from enumerated candidate orbit")
            index = candidate_codes.index(code)
        else:
            index = int(np.argmin(distances))
            if distances[index] > 1e-4:
                raise RuntimeError("Magnetic phase construction did not preserve the selected orbit")
        atom[cx + 4:cx + 7] = moments[index].tolist()
        atom[ct + 1] = "M"
        source_code = current_codes[atom_index]
        source_index = int(source_code.split("+", 1)[0].split(":", 1)[0])
        thermal_sources.append(source_index)
    elem.SetupGeneral(mag, None)
    phase = project.add_phase(phasename=mode["id"])
    mag["General"]["Name"] = phase.name
    mag["Histograms"] = {}
    phase.data.update(mag)
    project.update_ids()
    for hist in project.histograms():
        project.link_histogram_phase(hist, phase)
    project.index_ids()
    phase.set_refinements({"Cell": False})
    phase.set_HAP_refinements({"Scale": False})
    # This GSAS-II implementation's magnetic phase computes magnetic scattering
    # only. A separate nuclear phase is REQUIRED (not duplicated nuclear density).
    vectors = lat.cell2AB(parent["General"]["Cell"][1:7])[0].T
    norm = np.linalg.norm(moment_to_cartesian(phase.data["Atoms"][0][cx + 4:cx + 7], vectors))
    for atom in phase.data["Atoms"]:
        atom[cx + 4:cx + 7] = (np.asarray(atom[cx + 4:cx + 7]) / norm).tolist()
    expanded, _, lattice, positions, numbers = _expanded_parent(parent)
    expanded_moments = np.zeros((len(expanded), 3))
    for i, code in enumerate(current_codes):
        expanded_moments[expanded_codes.index(code)] = phase.data["Atoms"][i][cx + 4:cx + 7]
    space = _direction_space(lattice, positions, numbers, expanded_moments)[
        "direction_constraints_crystalaxis"]
    seed_cart = moment_to_cartesian([a[cx + 4:cx + 7] for a in phase.data["Atoms"]], vectors)
    signs = seed_cart @ seed_cart[0] / np.dot(seed_cart[0], seed_cart[0])
    for axis in "xyz":
        variables = [f"{phase.data['pId']}::AM{axis}:{i}" for i in range(len(phase.data["Atoms"]))]
        if len(variables) > 1:
            project.add_EquivConstr(variables, [1.0 / s for s in signs], override=True)
    for row in space:
        variables, coefficients = [], []
        for axis, coefficient in zip("xyz", row):
            if abs(coefficient) > 1e-8:
                variables.append(f"{phase.data['pId']}::AM{axis}:0")
                coefficients.append(float(coefficient))
        if len(variables) == 1:
            project.add_HoldConstr(variables, override=True)
        elif variables:
            project.add_EqnConstr(0.0, variables, coefficients, override=True)
    nuclear = project.phase("nuclear")
    mcia = phase.data["General"]["AtomPtrs"][3]
    for i, source in enumerate(thermal_sources):
        if parent["Atoms"][source][pcia] == "I" and phase.data["Atoms"][i][mcia] == "I":
            project.add_EquivConstr(
                [f"{nuclear.data['pId']}::AUiso:{source}", f"{phase.data['pId']}::AUiso:{i}"])
        elif parent["Atoms"][source][pcia] == "A" and phase.data["Atoms"][i][mcia] == "A":
            operation = abs(int(current_codes[i].split(":", 1)[1].split("+", 1)[0])) % 100
            rotation = parent["General"]["SGData"]["SGOps"][operation - 1][0]
            transform = np.array([
                lat.UijtoU6(rotation @ lat.U6toUij(unit) @ rotation.T)
                for unit in np.eye(6)]).T
            for component, row in zip(("11", "22", "33", "12", "13", "23"), transform):
                variables = [f"{phase.data['pId']}::AU{component}:{i}"]
                coefficients = [1.0]
                for parent_component, coefficient in zip(("11", "22", "33", "12", "13", "23"), row):
                    if abs(coefficient) > 1e-10:
                        variables.append(f"{nuclear.data['pId']}::AU{parent_component}:{source}")
                        coefficients.append(float(-coefficient))
                project.add_EqnConstr(0.0, variables, coefficients, override=True)
    for hist in project.histograms():
        source = nuclear.data["Histograms"][hist.name]
        target = phase.data["Histograms"][hist.name]
        for key in ("Scale", "Size", "Mustrain"):
            if key != "Scale" and source[key][0] not in ("isotropic", "uniaxial"):
                raise NotImplementedError(
                    f"{key} model {source[key][0]} cannot be transferred to a P1 magnetic phase")
            target[key] = copy.deepcopy(source[key])
        target["Scale"][1] = True
        source["Scale"][1] = True
        project.add_EquivConstr(
            [f"{nuclear.data['pId']}:{hist.id}:Scale",
             f"{phase.data['pId']}:{hist.id}:Scale"])
    return phase


def _symmetry_mcif(path, parent, magnetic):
    """Export the full structure and validated axial/time-reversal operations."""
    from fractions import Fraction
    _, lat, _, _, _ = _modules()
    if not np.allclose(parent["General"]["Cell"][1:7],
                       magnetic["General"]["Cell"][1:7], rtol=1e-8, atol=1e-8):
        raise ValueError("Nuclear and magnetic unit cells differ; combined export is invalid")
    atoms, _, vectors, positions, numbers = _expanded_parent(parent)
    cx, ct, _, cia = parent["General"]["AtomPtrs"]
    mx, mt, _, _ = magnetic["General"]["AtomPtrs"]
    moments = np.zeros((len(atoms), 3))
    adp_eigenvalues = []
    for atom in atoms:
        eigenvalues = ([float(atom[cia + 1])] * 3 if atom[cia] == "I"
                       else np.linalg.eigvalsh(lat.U6toUij(atom[cia + 2:cia + 8])).tolist())
        if min(eigenvalues) < -1e-7:
            raise ValueError("Refined nuclear displacement tensor is not positive semidefinite")
        adp_eigenvalues.append(eigenvalues)
    for atom in magnetic["Atoms"]:
        delta = positions - np.asarray(atom[mx:mx + 3])
        delta -= np.rint(delta)
        distance = np.linalg.norm(delta, axis=1)
        distance[np.array([a[ct] for a in atoms]) != atom[mt]] = np.inf
        i = int(np.argmin(distance))
        if distance[i] > 1e-4:
            raise RuntimeError("Final magnetic atom does not match nuclear structure")
        moments[i] = atom[mx + 4:mx + 7]
    dataset, group = _magnetic_dataset(vectors, positions, numbers, moments)
    cart = moment_to_cartesian(moments, vectors)
    representative, covered = [], set()
    A = vectors.T
    for i in range(len(atoms)):
        if i in covered:
            continue
        representative.append(i)
        for r, t, reversal in zip(dataset.rotations, dataset.translations, dataset.time_reversals):
            xyz = (r @ positions[i] + t) % 1
            delta = positions - xyz
            delta -= np.rint(delta)
            distance = np.linalg.norm(delta, axis=1)
            distance[np.array(numbers) != numbers[i]] = np.inf
            j = int(np.argmin(distance))
            transformed = (-1 if reversal else 1) * np.linalg.det(r) * A @ r @ np.linalg.inv(A) @ cart[i]
            if distance[j] > 1e-4 or not np.allclose(transformed, cart[j], atol=1e-4):
                raise RuntimeError("Candidate moments incompatible with exported magnetic symmetry")
            covered.add(j)
    lines = ["data_refined_magnetic", f"_space_group_magn.number_BNS '{group.bns_number}'",
             "# Operations and cell use the original parent setting, not a standardized BNS setting."]
    for key, value in zip(("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma"),
                          parent["General"]["Cell"][1:7]):
        lines.append(f"_cell_{key} {value:.12g}")
    lines.extend(["loop_", "_space_group_symop_magn_operation.id",
                  "_space_group_symop_magn_operation.xyz"])
    for i, (rotation, translation, reversal) in enumerate(
            zip(dataset.rotations, dataset.translations, dataset.time_reversals), 1):
        parts = []
        for row, shift in zip(rotation, translation):
            text = ""
            for coef, axis in zip(row, "xyz"):
                if coef:
                    text += ("+" if coef > 0 and text else "-" if coef < 0 else "") + (
                        str(abs(int(coef))) if abs(coef) != 1 else "") + axis
            frac = Fraction(float(shift) % 1).limit_denominator(96)
            if frac:
                text += ("+" if text else "") + str(frac)
            parts.append(text or "0")
        parts.append("-1" if reversal else "+1")
        lines.append(f"{i} '{','.join(parts)}'")
    lines.extend(["loop_", "_space_group_symop_magn_centering.id",
                  "_space_group_symop_magn_centering.xyz", "1 'x,y,z,+1'",
                  "loop_", "_atom_site_label", "_atom_site_type_symbol",
                  "_atom_site_fract_x", "_atom_site_fract_y", "_atom_site_fract_z",
                  "_atom_site_occupancy", "_atom_site_U_iso_or_equiv"])
    labels = {}
    for i in representative:
        atom = atoms[i]
        labels[i] = f"{_slug(atom[ct - 1])}_{i}"
        uiso = f"{atom[cia + 1]:.12g}" if atom[cia] == "I" else "."
        lines.append(f"'{labels[i]}' '{atom[ct]}' " +
                     " ".join(f"{v:.12g}" for v in (*positions[i], atom[cx + 3])) + f" {uiso}")
    anisotropic = [i for i in representative if atoms[i][cia] == "A"]
    if anisotropic:
        lines.extend(["loop_", "_atom_site_aniso_label",
                      *[f"_atom_site_aniso_U_{ij}" for ij in ("11", "22", "33", "12", "13", "23")]])
        for i in anisotropic:
            lines.append(f"'{labels[i]}' " +
                         " ".join(f"{v:.12g}" for v in atoms[i][cia + 2:cia + 8]))
    lines.extend(["loop_", "_atom_site_moment.label", "_atom_site_moment.crystalaxis_x",
                  "_atom_site_moment.crystalaxis_y", "_atom_site_moment.crystalaxis_z"])
    for i in representative:
        lines.append(f"'{labels[i]}' " + " ".join(f"{v:.12g}" for v in moments[i]))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"bns_number": str(group.bns_number), "uni_number": int(dataset.uni_number),
            "moments_cartesian": cart.tolist(), "moments_crystalaxis": moments.tolist(),
            "moment_norms": np.linalg.norm(cart, axis=1).tolist(),
            "adp_eigenvalues": adp_eigenvalues,
            "magnetic_form_factor_types": magnetic["General"]["AtomTypes"],
            "lande_g_factors": magnetic["General"].get("Lande g"),
            "cell": parent["General"]["Cell"][1:7], "symmetry_validated": True}


def _seed_moment_from_profile(project, magnetic):
    """Estimate amplitude squared by weighted LS; no guessed physical moment.

    With fixed nuclear model, magnetic-only intensity is exactly quadratic in
    the common signed amplitude. Two nonzero calculations avoid GSAS-II's
    undefined normalization of zero magnetic vectors.
    """
    from threadpoolctl import threadpool_limits
    cx = magnetic.data["General"]["AtomPtrs"][0]
    seeds = [np.asarray(a[cx + 4:cx + 7], dtype=float).copy()
             for a in magnetic.data["Atoms"]]
    profiles = []
    project.set_Controls("cycles", 0)
    pwd = importlib.import_module("GSASIIpwd")
    with threadpool_limits(limits=1, user_api="blas"), cached_tof_profiles(pwd):
        for amplitude in (1.0, 0.5):
            for atom, seed in zip(magnetic.data["Atoms"], seeds):
                atom[cx + 4:cx + 7] = (seed * amplitude).tolist()
            project.refine()
            profiles.append([np.asarray(h.getdata("ycalc"), dtype=float).copy()
                             for h in project.histograms()])
    numerator, denominator = 0.0, 0.0
    for hist, full, half in zip(project.histograms(), *profiles):
        magnetic_profile = (full - half) / 0.75
        nuclear_profile = full - magnetic_profile
        x, y, w = [np.asarray(hist.getdata(k), dtype=float) for k in ("x", "yobs", "yweight")]
        lo, hi = hist.data["Limits"][1]
        mask = (x >= lo) & (x <= hi) & (w > 0)
        for lower, upper in hist.data["Limits"][2:]:
            mask &= ~((x >= lower) & (x <= upper))
        numerator += float(np.sum(w[mask] * magnetic_profile[mask] * (y[mask] - nuclear_profile[mask])))
        denominator += float(np.sum(w[mask] * magnetic_profile[mask]**2))
    if denominator <= 0 or not math.isfinite(denominator):
        raise ValueError("Candidate has no measurable magnetic scattering in fitted range")
    # A 1e-6 moment is numerically indistinguishable from zero in GSAS's
    # finite-difference moment derivatives, producing catastrophic Hessian
    # steps when the profile-LS optimum lies on the zero boundary. Initialize
    # at the data's one-sigma amplitude-squared resolution instead; this is an
    # initialization, not a lower bound on the final ordered moment.
    resolution = 1 / math.sqrt(denominator)
    amplitude_sq = max(numerator / denominator, resolution)
    amplitude = math.sqrt(amplitude_sq)
    for atom, seed in zip(magnetic.data["Atoms"], seeds):
        atom[cx + 4:cx + 7] = (seed * amplitude).tolist()
    return {"amplitude": amplitude, "method": "weighted linear least squares in amplitude squared",
            "positive_boundary": numerator <= 0,
            "amplitude_squared_resolution": resolution,
            "regularized_to_measurement_resolution": numerator / denominator < resolution}

def _nuclear_window_scales(project, magnetic):
    """Initialize common scales on magnetic-extinct nuclear profile support.

    Sum magnetic profiles over the full allowed Cartesian basis, not a seed
    direction: extinction must persist for every vector in the candidate space.
    Exact integer-reflection GSAS profiles include overlapping reflections/tails.
    This is initialization only; final joint scales remain free on all data.
    """
    _, codes, vectors, positions, numbers = _expanded_parent(project.phase("nuclear").data)
    cx = magnetic.data["General"]["AtomPtrs"][0]
    seed = np.asarray([a[cx + 4:cx + 7] for a in magnetic.data["Atoms"]])
    cart = moment_to_cartesian(seed, vectors)
    signs = np.sign(cart @ cart[0])
    expanded = np.zeros((len(positions), 3))
    for code, moment in zip(magnetic.data["MagAtomCodes"], seed):
        expanded[codes.index(code)] = moment
    basis = np.asarray(_direction_space(vectors, positions, numbers, expanded)[
        "direction_basis_cartesian"])
    name = magnetic.name
    project.set_Controls("cycles", 0)
    for hist in project.histograms():
        magnetic.data["Histograms"][hist.name]["Use"] = False
    project.refine()
    nuclear = {h.name: h.getdata("ycalc").copy() - h.getdata("background")
               for h in project.histograms()}
    trace = {key: np.zeros_like(value) for key, value in nuclear.items()}
    for direction in basis.T:
        phase = project.phase(name)
        for hist in project.histograms():
            phase.data["Histograms"][hist.name]["Use"] = True
        components = moment_to_crystalaxis(direction, vectors)
        for atom, sign in zip(phase.data["Atoms"], signs):
            atom[cx + 4:cx + 7] = (sign * components).tolist()
        project.refine()
        for hist in project.histograms():
            trace[hist.name] += np.maximum(0, hist.getdata("ycalc") -
                hist.getdata("background") - nuclear[hist.name])
    diagnostics = []
    for hist in project.histograms():
        x, y, w, background = [hist.getdata(k) for k in
                               ("x", "yobs", "yweight", "background")]
        profile = nuclear[hist.name]
        lo, hi = hist.data["Limits"][1]
        mask = (x >= lo) & (x <= hi) & (w > 0)
        for lower, upper in hist.data["Limits"][2:]:
            mask &= ~((x >= lower) & (x <= upper))
        mask &= (profile > .01 * np.max(profile)) & (
            trace[hist.name] < 1e-4 * profile)
        record = {"histogram": hist.name, "selected_points": int(np.count_nonzero(mask)),
                  "magnetic_to_nuclear_unit_moment_threshold": 1e-4,
                  "direction_dimension_checked": basis.shape[1]}
        if np.count_nonzero(mask) < 20:
            record["warning"] = "Insufficient magnetic-extinct nuclear support; initialization unchanged"
        else:
            denominator = float(np.sum(w[mask] * profile[mask]**2))
            ratio = max(0., float(np.sum(w[mask] * profile[mask] *
                                        (y[mask] - background[mask]))) / denominator)
            if ratio <= 0:
                raise ValueError("Magnetic-extinct nuclear windows have nonpositive fitted scale")
            record["scale_correction"] = ratio
            for phase in project.phases():
                phase.data["Histograms"][hist.name]["Scale"][0] *= ratio
        diagnostics.append(record)
    for atom, moment in zip(project.phase(name).data["Atoms"], seed):
        atom[cx + 4:cx + 7] = moment.tolist()
    return diagnostics

def _quadratic_moment_fit(project, magnetic, mode, vectors):
    """Fit common moments including zero using exact quadratic GSAS profiles.

    Nuclear/profile/ADP state is fixed in this step. Per-bank common scales and
    backgrounds are variable-projected; there is no finite difference at zero.
    """
    from scipy.optimize import least_squares
    _, codes, vectors, positions, numbers = _expanded_parent(project.phase("nuclear").data)
    cx, ct, _, _ = magnetic.data["General"]["AtomPtrs"]
    current = moment_to_cartesian([a[cx + 4:cx + 7] for a in magnetic.data["Atoms"]], vectors)
    reference = current[0]
    if np.linalg.norm(reference) < 1e-14:
        seeds = np.asarray(mode["moment_crystalaxis"])
        seeds = seeds[np.linalg.norm(seeds, axis=1) > 0]
        if len(seeds) != len(magnetic.data["Atoms"]):
            raise ValueError("Zero-moment mode seed does not match magnetic orbit")
        seed_cart = moment_to_cartesian(seeds, vectors)
        signs = np.sign(seed_cart @ seed_cart[0])
        symmetry_moments = seeds
    else:
        signs = np.sign(current @ reference)
        symmetry_moments = np.array([a[cx + 4:cx + 7] for a in magnetic.data["Atoms"]])
    expanded = np.zeros((len(positions), 3))
    for code, moment in zip(magnetic.data["MagAtomCodes"], symmetry_moments):
        expanded[codes.index(code)] = moment
    basis = np.asarray(_direction_space(vectors, positions, numbers, expanded)[
        "direction_basis_cartesian"])
    dimension = basis.shape[1]
    initial = basis.T @ reference
    names = [h.name for h in project.histograms()]
    nuclear = project.phase("nuclear")
    scales = {name: nuclear.data["Histograms"][name]["Scale"][0] for name in names}
    flags = [a[ct + 1] for a in magnetic.data["Atoms"]]
    for atom in magnetic.data["Atoms"]:
        atom[ct + 1] = ""
    project.set_Controls("cycles", 0)
    def calculate(coefficients):
        active = np.linalg.norm(coefficients) > 0
        magnetic_now = project.phase(magnetic.name)
        for name in names:
            magnetic_now.data["Histograms"][name]["Use"] = bool(active)
        if active:
            vector = moment_to_crystalaxis(basis @ coefficients, vectors)
            for atom, sign in zip(magnetic_now.data["Atoms"], signs):
                atom[cx + 4:cx + 7] = (sign * vector).tolist()
        for hist in project.histograms():
            hist.data["data"][1][3][:] = np.nan
        project.refine()
        arrays = []
        for hist in project.histograms():
            array = np.asarray(hist.getdata("ycalc"), float) - np.asarray(hist.getdata("background"), float)
            if not np.all(np.isfinite(array)):
                raise RuntimeError("Exact quadratic magnetic calculation failed")
            arrays.append(array.copy())
        return arrays
    base = calculate(np.zeros(dimension))
    diagonal = []
    for axis in np.eye(dimension):
        diagonal.append([a - b for a, b in zip(calculate(axis), base)])
    cross = {}
    for i in range(dimension):
        for j in range(i):
            cross[i, j] = [a - b - di - dj for a, b, di, dj in zip(
                calculate(np.eye(dimension)[i] + np.eye(dimension)[j]), base,
                diagonal[i], diagonal[j])]
    bank_data = []
    for hist in project.histograms():
        x, y, w = [np.asarray(hist.getdata(k), float) for k in ("x", "yobs", "yweight")]
        lo, hi = hist.data["Limits"][1]
        mask = (x >= lo) & (x <= hi) & (w > 0)
        for lower, upper in hist.data["Limits"][2:]:
            mask &= ~((x >= lower) & (x <= upper))
        nback = hist.data["Background"][0][2]
        z = 2 * (x - x[mask][0]) / (x[mask][-1] - x[mask][0]) - 1
        sqrtw = np.sqrt(w[mask])
        background = np.polynomial.chebyshev.chebvander(z, nback - 1)[mask] * sqrtw[:, None]
        q, r = np.linalg.qr(background, mode="reduced")
        weighted_y = y[mask] * sqrtw
        yq = q.T @ weighted_y
        yperp = weighted_y - q @ yq
        b = len(bank_data)
        columns = [base[b], *[diagonal[i][b] for i in range(dimension)],
                   *[cross[i, j][b] for i in range(dimension) for j in range(i)]]
        weighted = np.column_stack(columns)[mask] * sqrtw[:, None]
        pq = q.T @ weighted
        pperp = weighted - q @ pq
        bank_data.append((r, yq, yperp, pq, pperp))
    linear = []
    def residual(coefficients):
        linear.clear()
        residuals = []
        tensor_coefficients = np.array([1., *coefficients**2,
            *[coefficients[i] * coefficients[j] for i in range(dimension) for j in range(i)]])
        for r, yq, yperp, pq, pperp in bank_data:
            projected_profile = pperp @ tensor_coefficients
            denominator = float(projected_profile @ projected_profile)
            if denominator <= 0:
                raise RuntimeError("Quadratic magnetic/nuclear scale is not identifiable")
            scale = max(0., float(projected_profile @ yperp) / denominator)
            background = np.linalg.solve(r, yq - scale * (pq @ tensor_coefficients))
            linear.append(np.r_[scale, background])
            residuals.append(scale * projected_profile - yperp)
        return np.concatenate(residuals)
    # Explicitly include the zero boundary and nonzero orthogonal seeds, so
    # vanishing gradient at zero cannot trap the vector optimizer.
    zero = residual(np.zeros(dimension))
    best = {"coefficients": np.zeros(dimension), "chisq": float(zero @ zero),
            "linear": [a.copy() for a in linear], "jacobian": None}
    for seed in [initial, *np.eye(dimension)]:
        fitted = least_squares(residual, seed, max_nfev=100,
                               ftol=1e-7, xtol=1e-7, gtol=1e-7)
        if not fitted.success:
            raise RuntimeError("Quadratic moment optimizer exceeded its bound")
        errors = residual(fitted.x)
        objective = float(errors @ errors)
        if objective < best["chisq"] - 1e-7 * max(1., best["chisq"]):
            best = {"coefficients": fitted.x, "chisq": objective,
                    "linear": [a.copy() for a in linear], "jacobian": fitted.jac}
    coefficients = best["coefficients"]
    vector = moment_to_crystalaxis(basis @ coefficients, vectors)
    nuclear = project.phase("nuclear")
    magnetic = project.phase(magnetic.name)
    zero_boundary = bool(np.linalg.norm(coefficients) == 0)
    for atom, sign, flag in zip(magnetic.data["Atoms"], signs, flags):
        atom[cx + 4:cx + 7] = (sign * vector).tolist()
        atom[ct + 1] = flag.replace("M", "")
    for name, values in zip(names, best["linear"]):
        common_scale = float(scales[name] * values[0])
        for phase in (nuclear, magnetic):
            phase.data["Histograms"][name]["Scale"][0] = common_scale
        magnetic.data["Histograms"][name]["Use"] = not zero_boundary
        project.histogram(name).data["Background"][0][3:] = values[1:].tolist()
    project.set_Controls("cycles", 0)
    project.refine()
    project.save()
    quality = _quality(project, calculation_only=True)
    quality["converged"] = True
    quality["convergence_reason"] = "Bounded quadratic optimizer converged, including explicit "
    quality["convergence_reason"] += "zero boundary; GSAS zero-cycle used only for final exact profile"
    quality["parameter_count"] = sum(len(v) for v in best["linear"]) + dimension
    quality["bic_known_variance"] = quality["weighted_residual_sum"] + (
        quality["parameter_count"] * math.log(quality["points"]))
    return {"method": "Exact quadratic magnetic-profile tensor with variable-projected common "
                     "scales/backgrounds; zero moment is explicitly tested",
            "moment_cartesian": (basis @ coefficients).tolist(), "zero_boundary": zero_boundary,
            "weighted_residual_sum": best["chisq"],
            "direction_dimension": dimension,
            "quality": quality,
            "conditional_on": "Fixed nuclear geometry, ADPs and instrument profile",
            "moment_jacobian_singular_values": ([] if best["jacobian"] is None else
                np.linalg.svd(best["jacobian"], compute_uv=False).tolist())}


def _cached_refinement(function):
    @wraps(function)
    def execute(*args, **kwargs):
        from threadpoolctl import threadpool_limits
        _modules()
        with threadpool_limits(1, user_api="blas"), cached_tof_profiles(importlib.import_module("GSASIIpwd")):
            return function(*args, **kwargs)
    return execute

def _peak_decomposition(project, peaks, output):
    """Persist observed and separated-phase peak integrals on actual TOF bins."""
    _, lat, _, _, _ = _modules()
    project.save(str(output / "peak_decomposition.gpx"))
    nuclear = project.phase("nuclear")
    magnetic = next(p for p in project.phases() if p.name != "nuclear")
    names = [h.name for h in project.histograms()]
    profiles = {}
    for phase in (nuclear, magnetic):
        for p in project.phases():
            for name in names:
                p.data["Histograms"][name]["Use"] = p.name == phase.name
        project.set_Controls("cycles", 0)
        project.refine()
        profiles[phase.name] = {h.name: np.asarray(h.getdata("ycalc")) -
                               np.asarray(h.getdata("background"))
                               for h in project.histograms()}
    result = []
    for hist in project.histograms():
        x, y, bg = [np.asarray(hist.getdata(k)) for k in ("x", "yobs", "background")]
        d = lat.TOF2dsp(hist.data["Instrument Parameters"][0], x)
        widths = np.gradient(x)
        items = []
        from scipy.signal import find_peaks
        nuclear_profile = profiles[nuclear.name][hist.name]
        magnetic_profile = profiles[magnetic.name][hist.name]
        maxima, _ = find_peaks(nuclear_profile)
        isolated = []
        for index in sorted(maxima, key=lambda i: nuclear_profile[i], reverse=True):
            if magnetic_profile[index] > .05 * nuclear_profile[index]:
                continue
            if any(abs(d[index] - p["d"]) < .04 * d[index] for p in isolated):
                continue
            isolated.append({"d": float(d[index]), "kind": "nuclear_dominated"})
            if len(isolated) == 4:
                break
        for peak in [dict(p, kind="magnetic_sensitive") for p in peaks] + isolated:
            center = peak["d"]
            halfwidth = max(3 * peak.get("width_d", 0.), .015 * center)
            mask = abs(d - center) < halfwidth
            if mask.sum() < 3:
                continue
            integrals = {name: float(np.sum(profile[hist.name][mask] * widths[mask]))
                         for name, profile in profiles.items()}
            items.append({"d": center, "kind": peak["kind"], "halfwidth_d": halfwidth,
                          "observed_background_subtracted_area_TOF": float(
                              np.sum((y[mask] - bg[mask]) * widths[mask])),
                          "calculated_phase_areas_TOF": integrals})
        result.append({"histogram": hist.name, "peaks": items,
                       "bin_width_range_TOF": [float(widths.min()), float(widths.max())]})
    return {"banks": result, "convention": "Supplied point-sampled TOF intensity; integrals "
            "use actual TOF widths. Separate nuclear/magnetic calculations with identical "
            "cell, profile, background and tied phase scales, no fitting during decomposition."}


@_cached_refinement
def refine_local_candidates(input_dir, output_dir, *, cycles=24, max_models=64,
                      time_limit_seconds=1800, background_coeffs=6, refine_cell=True,
                      refine_profile=True):
    """Fit all bounded modes against actual low-T banks; export ranked best model."""
    if not 1 <= max_models <= 4096 or not 1 <= time_limit_seconds <= 86400:
        raise ValueError("Invalid refinement work bounds")
    sc, _, _, _, _ = _modules()
    output = Path(output_dir).resolve()
    candidates = _read(output / "candidates_report.json")
    if candidates["inputs"] != discover_inputs(input_dir):
        raise ValueError("Candidates belong to different inputs")
    if not candidates["modes"] or len(candidates["modes"]) > max_models:
        raise ValueError(f"Need to fit {len(candidates['modes'])} models; max_models={max_models}")
    start = time.monotonic()
    baseline_paths = refine_nuclear_baseline(
        input_dir, output_dir, cycles=cycles, background_coeffs=background_coeffs,
        refine_cell=refine_cell, refine_profile=refine_profile)
    baseline = _read(baseline_paths["report_path"])
    high_project = sc.G2Project(gpxfile=baseline_paths["high_gpx_path"])
    high_parent = high_project.phase("nuclear").data
    high_instruments = [copy.deepcopy(h.data["Instrument Parameters"]) for h in high_project.histograms()]
    results = []
    report = {"stage": "refine", "inputs": candidates["inputs"], "models": results,
              "baseline_report_path": baseline_paths["report_path"],
              "complete": False}
    for mode in candidates["modes"]:
        if time.monotonic() - start > time_limit_seconds:
            _write(output / "refinement_report.json", report)
            raise TimeoutError("Refinement time bound reached; partial diagnostics persisted")
        path = output / f"{mode['id']}.gpx"
        try:
            project = sc.G2Project(gpxfile=baseline_paths["low_gpx_path"])
            project.save(str(path))
            # Frozen parameters in the nuclear-only control must not prevent
            # a magnetic model from independently fitting the same bounds.
            project.data["Controls"]["data"].setdefault("parmFrozen", {})["FrozenList"] = []
            nuclear = project.phase("nuclear")
            for hist, instrument in zip(project.histograms(), high_instruments):
                hist.data["Instrument Parameters"] = copy.deepcopy(instrument)
            # Nuclear-only low-T ADPs may absorb magnetic scattering. Initialize
            # model comparison from the independently measured high-T baseline.
            cia = nuclear.data["General"]["AtomPtrs"][3]
            for index, (atom, high_atom) in enumerate(zip(nuclear.data["Atoms"], high_parent["Atoms"])):
                atom[cia:cia + 8] = copy.deepcopy(high_atom[cia:cia + 8])
                cx = nuclear.data["General"]["AtomPtrs"][0]
                atom[cx:cx + 3] = copy.deepcopy(high_atom[cx:cx + 3])
            nuclear.set_refinements({"Cell": False, "Atoms": {"all": " "}})
            magnetic = _insert_magnetic(project, nuclear.data, candidates, mode)
            # Initialize on magnetic-extinct nuclear support, then release the
            # tied scales immediately: nuclear-only all-bin scales are not a
            # physical constraint on ordered moments.
            nuclear.set_HAP_refinements({"Scale": False})
            magnetic.set_HAP_refinements({"Scale": False})
            nuclear_scale_initialization = _nuclear_window_scales(project, magnetic)
            magnetic = project.phase(mode["id"])
            moment_seed = _seed_moment_from_profile(project, magnetic)
            nuclear = project.phase("nuclear")
            nuclear.set_HAP_refinements({"Scale": True})
            magnetic.set_HAP_refinements({"Scale": True})
            quadratic = None
            if moment_seed["positive_boundary"]:
                quadratic = _quadratic_moment_fit(project, magnetic, mode,
                                                  np.asarray(candidates["cell_vectors"]))
                first = quadratic["quality"]
            else:
                first = _refine(project, cycles)
            nuclear.set_HAP_refinements({"Scale": True})
            magnetic.set_HAP_refinements({"Scale": True})
            released_boundaries = _release_adp_boundaries(project)
            nuclear.set_refinements({"Atoms": {"all": "U"}})
            for index, atom in enumerate(nuclear.atoms()):
                if index not in candidates["selected_parent_indices"]:
                    atom.refinement_flags = "XU"
            if nuclear.data["General"]["SGData"].get("SGPolax"):
                nuclear.atoms()[0].refinement_flags = "U"
            # This bundled scriptable atom setter accepts only FXU even
            # though the refinement engine explicitly implements M.
            ct = magnetic.data["General"]["AtomPtrs"][1]
            for atom in magnetic.data["Atoms"]:
                atom[ct + 1] = "U" if quadratic is not None else "MU"
            quality = _refine(project, cycles)
            if quadratic is not None:
                quadratic = _quadratic_moment_fit(
                    project, project.phase(mode["id"]), mode,
                    np.asarray(candidates["cell_vectors"]))
                # Compute the final exact projected profile with no unsafe
                # derivative of a vanishing moment. Conditional direction
                # optimization is explicit in diagnostics.
                nuisance_quality = quality
                quality = quadratic["quality"]
                quality["conditional_moment_fit"] = {
                    k: v for k, v in quadratic.items() if k != "quality"}
                # Nuisance ADPs/coordinates were fitted in the preceding GSAS
                # step and remain fitted degrees of freedom in comparison.
                quality["parameter_count"] = max(
                    quality["parameter_count"], nuisance_quality["parameter_count"] +
                    quadratic["direction_dimension"])
                quality["bic_known_variance"] = quality["weighted_residual_sum"] + (
                    quality["parameter_count"] * math.log(quality["points"]))
            low_profile = None
            if refine_profile:
                previous = quality["weighted_residual_sum"]
                steps = []
                stabilized = False
                reference_profiles = {
                    h.name: [max(float(h.data["Instrument Parameters"][0][k][1]), 1e-6)
                             for k in ("alpha", "beta-0", "sig-1", "X")]
                    for h in project.histograms()}
                for alternating_pass in range(3):
                    profile_quality = _bounded_profile_fit(
                        project, cycles, magnetic_name=mode["id"],
                        reference_profiles=reference_profiles)
                    cell_quality = _conditional_cell_fit(project) if refine_cell else None
                    _release_adp_boundaries(project)
                    project.phase("nuclear").set_refinements({"Atoms": {"all": "U"}})
                    mphase = project.phase(mode["id"])
                    mct = mphase.data["General"]["AtomPtrs"][1]
                    for atom in mphase.data["Atoms"]:
                        atom[mct + 1] = "U"
                    nuisance = _refine(project, cycles)
                    quadratic = _quadratic_moment_fit(
                        project, project.phase(mode["id"]), mode,
                        np.asarray(candidates["cell_vectors"]))
                    quality = quadratic["quality"]
                    added = 4 * len(project.histograms()) + (
                        cell_quality["parameter_count"] if cell_quality else 0)
                    quality["parameter_count"] = max(
                        quality["parameter_count"],
                        nuisance["parameter_count"] + quadratic["direction_dimension"]) + added
                    quality["bic_known_variance"] = quality["weighted_residual_sum"] + (
                        quality["parameter_count"] * math.log(quality["points"]))
                    relative = abs(previous - quality["weighted_residual_sum"]) / max(previous, 1.)
                    steps.append({"pass": alternating_pass + 1, "profile": profile_quality,
                                  "cell": cell_quality,
                                  "weighted_residual_sum": quality["weighted_residual_sum"],
                                  "relative_objective_change": relative})
                    previous = quality["weighted_residual_sum"]
                    # A residual already at the numerical floor (perfect or synthetic
                    # patterns) leaves nothing for further alternation to stabilize, so
                    # the relative-change test alone would call a perfect fit unstable.
                    if relative < 1e-4 or quality["reduced_chisq"] <= 1e-3:
                        stabilized = True
                        break
                low_profile = {"strategy": "positive_full_pattern_conditional",
                               "parameters_per_bank": ["alpha", "beta-0", "sig-1", "X"],
                               "added_parameter_count": added, "steps": steps,
                               "stabilized": stabilized, "pass_limit": 3,
                               "relative_objective_tolerance": 1e-4}
                quality["low_temperature_profile_fit"] = low_profile
            nuclear = project.phase("nuclear")
            magnetic = project.phase(mode["id"])
            mcif = output / f"{mode['id']}.mcif"
            physical = _symmetry_mcif(mcif, nuclear.data, magnetic.data)
            for hist in project.histograms():
                columns = np.column_stack([hist.getdata(k) for k in
                                           ("x", "yobs", "ycalc", "background", "yweight")])
                np.savetxt(output / f"{mode['id']}_{_slug(hist.name)}_fit.csv", columns,
                           delimiter=",", header="TOF,yobs,ycalc,background,weight")
            results.append({"id": mode["id"], "status": "ok", "quality": quality,
                            "moment_only_quality": first, "physical": physical,
                            "data_derived_moment_seed": moment_seed,
                            "nuclear_scale_initialization": nuclear_scale_initialization,
                            "adp_boundaries_released_before_joint_fit": released_boundaries,
                            "quadratic_moment_fit": quadratic,
                            "low_temperature_profile_fit": low_profile,
                            "gpx_path": str(path), "mcif_path": str(mcif),
                            "improvement_in_weighted_residual": baseline["low_nuclear_control"][
                                "weighted_residual_sum"] - quality["weighted_residual_sum"]})
        except Exception as exc:
            import traceback
            failed = {"id": mode["id"], "status": "failed",
                            "error_type": type(exc).__name__, "error": str(exc),
                            "traceback": traceback.format_exc()}
            try:
                failed["last_solver_quality"] = _quality(project)
            except Exception:
                pass
            results.append(failed)
        _write(output / "refinement_report.json", report)
    good = sorted((m for m in results if m["status"] == "ok"),
                  key=lambda m: m["quality"]["bic_known_variance"])
    if not good:
        reasons = "; ".join(f"{m['id']}: {m.get('error', 'unknown failure')}" for m in results[:8])
        raise RuntimeError(f"All candidate refinements failed: {reasons}; "
                           f"see {output / 'refinement_report.json'}")
    best = good[0]
    failures = len(results) - len(good)
    uncertainty = str(best["quality"].get("refinement_warnings", ""))
    high_correlation = "**" in uncertainty and "AM" in uncertainty
    inadequate = best["quality"]["reduced_chisq"] > 5
    alternatives = [m["id"] for m in good if m["quality"]["bic_known_variance"] -
                    best["quality"]["bic_known_variance"] < 6]
    conditional_unstable = any(
        m.get("low_temperature_profile_fit") and not m["low_temperature_profile_fit"]["stabilized"]
        for m in good)
    qualified = (failures > 0 or high_correlation or inadequate or
                 len(alternatives) > 1 or conditional_unstable)
    scientific = {
        "status": "unresolved" if qualified else "qualified_model_comparison",
        "comparison_complete": failures == 0 and not conditional_unstable,
        "models_attempted": len(results),
        "models_completed": len(good),
        "models_converged": sum(
            not m.get("low_temperature_profile_fit") or m["low_temperature_profile_fit"]["stabilized"]
            for m in good),
        "failed_models": failures,
        "best_reduced_chisq": best["quality"]["reduced_chisq"],
        "bank_rwp": best["quality"]["histograms"],
        "identifiability_status": "not_established" if qualified else "conditional_on_model_scope",
        "moment_direction_highly_correlated": high_correlation,
        "near_degenerate_models": alternatives,
        "powder_symmetry_ambiguity": len(alternatives) > 1,
        "conditional_profile_stabilized": not conditional_unstable,
        "warning": ("Export is a fitted representative, not a unique magnetic-group determination "
                    "or a verified scientific PASS. Inspect solver failures, systematic residuals "
                    "and moment-direction covariance; inspect conditional profile stabilization "
                    "before treating a completed model as converged." if qualified else
                    "Comparison is conditional on supplied form factors, collinear mode scope "
                    "and powder identifiability; not a benchmark validation."),
    }
    search = _read(output / "search_report.json") if (output / "search_report.json").is_file() else {}
    if search.get("propagation_peaks"):
        decomposition = _peak_decomposition(
            sc.G2Project(gpxfile=best["gpx_path"]), search["propagation_peaks"], output)
        report["peak_decomposition_report_path"] = _write(output / "peak_decomposition.json", decomposition)
    report.update({
        "complete": True, "best_model": best["id"], "gpx_path": best["gpx_path"],
        "mcif_path": best["mcif_path"], "ranking": [m["id"] for m in good],
        "elapsed_seconds": time.monotonic() - start,
        "scientific_status": scientific,
        "diagnostics": {
            "failed_models": failures,
            "selection": "Known-variance BIC on identical low-T points; nuclear-only control also reported",
            "delta_bic_vs_nuclear_only": best["quality"]["bic_known_variance"] -
                                       baseline["low_nuclear_control"]["bic_known_variance"],
            "near_degenerate_models": [m["id"] for m in good
                                      if m["quality"]["bic_known_variance"] -
                                      best["quality"]["bic_known_variance"] < 6],
            "ambiguity": "BIC differences below 6 are reported as unresolved alternatives. "
                         "The exported minimum-BIC representative is not a unique magnetic "
                         "symmetry determination; no BNS-number preference or tie override is used.",
            "scale_moment": "Magnetic-only and nuclear HAP scales tied 1:1 in the same cell. "
                            "Equal-magnitude signed collinear moments with every symmetry-allowed "
                            "direction component free, not fixed seed ratios. Instrument and "
                            "selected-atom geometry fixed; matched isotropic or symmetry-transformed "
                            "anisotropic ADPs and nonmagnetic "
                            "atomic coordinates co-refined in magnetic comparison.",
            "model_misspecification_warning": (
                "Best reduced chi-square remains >5; systematic profile/nuclear errors can bias "
                "moment direction and exaggerate BIC distinctions. A unique magnetic group "
                "is not established." if best["quality"]["reduced_chisq"] > 5 else None),
            "scope": candidates["diagnostics"]["limitations"],
            "blas_threads": 1,
            "form_factor_assignment": {
                "types": best["physical"]["magnetic_form_factor_types"],
                "source": "Imported CIF atom-site scattering types, preserved in magnetic phase",
                "ionic_charge_explicit": all(
                    "+" in t or "-" in t for t in best["physical"]["magnetic_form_factor_types"]),
                "warning": "Uncharged element labels do not establish an oxidation state. "
                           "No ionic magnetic form factor is inferred from charge neutrality "
                           "alone; moments remain conditional on supplied scattering types.",
            },
            "low_temperature_profile_scope": "Positive low-temperature effective alpha, beta-0, "
                "sig-1 and X fitted on full pattern for every candidate, initialized high-T. "
                "Up to three conditional profile/common-cell/nuclear-ADP/moment passes, "
                "including nonnegative scale and background variable projection. "
                "Selected nuclear coordinates shared and held in magnetic comparison. "
                "An unresolved residual is not evidence these nuisance models are correct.",
        },
    })
    path = _write(output / "refinement_report.json", report)
    return {"stage": "refine", "report_path": path, "best_model": best["id"],
            "gpx_path": best["gpx_path"], "mcif_path": best["mcif_path"],
            "final_gpx_path": best["gpx_path"], "final_mcif_path": best["mcif_path"],
            "scientific_status": scientific,
            "fit_quality": {"reduced_chisq": best["quality"]["reduced_chisq"],
                            "bank_rwp": best["quality"]["histograms"]},
            "failed_models": failures, "warning": scientific["warning"],
            "diagnostics": report["diagnostics"]}


def enumerate_candidates(input_dir, output_dir, **options):
    """Authoritative unrestricted existing-wrapper magnetic subgroup search."""
    from .native import enumerate_native
    return enumerate_native(input_dir, output_dir, **options)


def refine_candidates(input_dir, output_dir, **options):
    """Ordinary native subgroup Rietveld refinement (independent magnetic orbits)."""
    from .native import refine_native
    return refine_native(input_dir, output_dir, **options)


def analyze(input_dir, output_dir, stage="inspect", *, magnetic_elements=None,
            k_vectors=None, **options):
    """MCP-friendly stage API. Prerequisite stages must already be persisted.

    Stages are inspect, search, candidates and refine (not an implicit monolith).
    Every return value is small and JSON serializable. Detailed arrays, models,
    GPX and MCIF files are persisted under output_dir.
    """
    functions = {"inspect": inspect_spectra, "search": search_propagation,
                 "candidates": enumerate_candidates, "refine": refine_candidates}
    if stage not in functions:
        raise ValueError(f"Unknown stage {stage!r}; choose {list(functions)}")
    if stage in ("search", "candidates"):
        options["k_vectors"] = k_vectors
    if stage == "candidates":
        options["magnetic_elements"] = magnetic_elements
    try:
        return functions[stage](input_dir, output_dir, **options)
    except Exception as exc:
        _write(Path(output_dir).resolve() / f"{stage}_error.json",
               {"stage": stage, "error_type": type(exc).__name__, "error": str(exc)})
        raise
