"""Native GSAS-II subgroup phases and ordinary Rietveld model comparison."""

import copy
import hashlib
import importlib
import json
from pathlib import Path
import pickle
import signal
import time
from contextlib import contextmanager

import numpy as np


@contextmanager
def _deadline(seconds):
    """Interrupt native Python/Fortran call returns within a total stage budget."""
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    def expired(*_):
        raise TimeoutError("Native refinement wall-clock budget reached")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds, 1.)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, old_handler)


# Two magnetic models whose reduced χ² differ by less than this fraction are not
# distinguishable by the data: Rietveld systematics (background, absorption, extinction,
# form-factor model) are far larger than that. Deciding between them on the χ² difference
# hands the decision to noise — on the reference case 63.462 and 63.457 differ by 0.012 %,
# and only 63.457 keeps the moment on the diagonal the data actually constrain.
TIE_RELATIVE_CHISQ = 1e-3


def export_project_mcif(gpx_path, mcif_path):
    """Write the magnetic MCIF of an already-refined project (handoff completion).

    A selected model may be refined further by the gsas2-refine service, whose
    step-by-step, rollback-capable workflow suits pushing one structure harder than this
    engine's fixed comparison ladder. This turns that refined project back into the
    magnetic MCIF the mission delivers.
    """
    from . import workflow as w

    sc = w._modules()[0]
    project = sc.G2Project(gpxfile=str(gpx_path))
    phases = list(project.phases())
    magnetic = next((ph for ph in phases
                     if ph.data["General"]["SGData"].get("MagMom") is not None), None)
    if magnetic is None:
        raise ValueError("The handed-off project contains no magnetic phase")
    nuclear = next((ph for ph in phases if ph is not magnetic), None)
    physical = export_full(Path(mcif_path),
                           (nuclear or magnetic).data, magnetic.data)
    return {"mcif_path": str(mcif_path), "physical": physical,
            "magnetic_phase": magnetic.name,
            "nuclear_phase": getattr(nuclear, "name", None)}


def _note(progress_log, text: str) -> None:
    """Append one human-readable step line where the polling runner can relay it.

    Long magnetic stages must not look silent: the tool's caller sees these lines while it
    waits, so it can tell the user which candidate is being refined right now.
    """
    if not progress_log:
        return
    try:
        with Path(progress_log).open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
    except OSError:
        pass


def classify_failure(model: dict) -> str:
    """Why a candidate did not converge, so failures can be triaged not lumped together.

    A failed candidate is not an excluded structure (a solver can stall on a model the
    data would accept), so the report separates causes: exhausting the wall-clock budget,
    parameters hitting their bounds, a solver that produced no result, or a geometry the
    engine refused to build.
    """
    text = f"{model.get('error_type', '')}: {model.get('error', '')}".lower()
    if "timeout" in text or "budget" in text:
        return "time_budget"
    if "bound" in text or "limit" in text or "range" in text:
        return "parameter_bounds"
    if "solver" in text or "rvals" in text or "refinement result" in text or "singular" in text:
        return "solver"
    if "symmetry" in text or "cell" in text or "site" in text or "orbit" in text:
        return "geometry"
    return "other"


def free_moment_components(candidate: dict) -> int | None:
    """Number of magnetic components a candidate still refines, when reported."""
    sites = candidate.get("magnetic_sites")
    if not isinstance(sites, list) or not sites:
        return None
    total = 0
    reported = False
    for site in sites:
        relations = site.get("child_basis_constraint_relations")
        if isinstance(relations, list):
            total += len(relations)
            reported = True
    return total if reported else None


def enumerate_native(input_dir, output_dir, *, magnetic_elements=None,
                     k_vectors=None, max_candidates=256,
                     bilbao_limits: str | None = None,
                     bilbao_wyckoff: str | None = None,
                     exact_wyckoff_orbit: bool = False,
                     preserve_parent_wyckoff_type: bool = False,
                     wyckoff_position_type: str | None = None,
                     maximal_only: bool = False,
                     landau_only: bool = False,
                     all_have_moment: bool = False,
                     use_whole_star: bool = False):
    """Enumerate Bilbao k-SUBGROUPSMAG candidates for the parent phase.

    ``bilbao_limits``/``bilbao_wyckoff`` narrow the Bilbao query itself, which is how
    the manual workflow keeps the candidate list to the structures a chemist would
    consider: ``"maximal"`` asks for maximal k-subgroups only, and ``bilbao_wyckoff``
    names the Wyckoff positions allowed to order. Both default to ``None``, i.e. the
    full subgroup list GSAS-II requests.
    """
    from . import workflow as w
    _, _, _, _, wrapper = w._modules()
    inputs = w.discover_inputs(input_dir)
    output = Path(output_dir).resolve()
    search = w._read(output / "search_report.json")
    if search["inputs"] != inputs:
        raise ValueError("Search belongs to different inputs")
    ks = k_vectors if k_vectors is not None else [v["k"] for v in search["k_candidates"]]
    if not ks:
        raise ValueError("Propagation undetermined; supply k_vectors")
    if not 1 <= max_candidates <= 4096:
        raise ValueError("max_candidates must be 1..4096")
    pathmod = importlib.import_module("GSASIIpath")
    original = pathmod.postURL
    cache = output / "generic_symmetry_cache"
    cache.mkdir(exist_ok=True)
    query_records = []
    allowed = {"centrosymmetry", "crystalsystem", "landau", "eleccion", "inicio",
               "celtodas", "limite", "list", "listado", "starmagnetica", "pointgroup",
               "polarity", "sub", "super", "tipog", "wyckoffstrain", "generators"}
    allowed |= {f"{prefix}{i}{axis}" for prefix in ("km", "knm")
                for i in (1, 2, 3) for axis in "xyz"}
    def generic_query(url, data, **kwargs):
        # This endpoint receives symmetry operators and reciprocal k only.
        # Reject any future wrapper change that adds structure/experiment data.
        if not isinstance(data, dict) or not set(data) <= allowed:
            raise PermissionError("Bilbao payload is not a generic symmetry/k query")
        if "subgrmag1_general_GSAS.pl" not in url:
            raise PermissionError("Unexpected Bilbao endpoint")
        if bilbao_limits or bilbao_wyckoff is not None:
            data = dict(data)
            if bilbao_limits:
                data["limite"] = bilbao_limits
            if bilbao_wyckoff is not None:
                data["wyckoffstrain"] = bilbao_wyckoff
        encoded = json.dumps({"url": url, "query": data}, sort_keys=True).encode()
        key = hashlib.sha256(encoded).hexdigest()
        response = cache / f"{key}.html"
        if response.is_file():
            page = response.read_text()
            source = "generic_symmetry_cache"
        else:
            page = original(url, data, timeout=30, retry=0)
            if not page:
                raise ConnectionError("Bilbao generic symmetry query returned no response")
            response.write_text(page)
            source = "Bilbao"
        query_records.append({"sha256": key, "source": source,
                              "transmitted_fields": sorted(data)})
        return page
    pathmod.postURL = generic_query
    try:
        candidates = wrapper.search_magnetic_structure_candidates(
            inputs["cif"], ks, magnetic_atom_types=magnetic_elements,
            exact_wyckoff_orbit=exact_wyckoff_orbit, use_whole_star=use_whole_star,
            landau_only=landau_only, maximal_only=maximal_only,
            all_have_moment=all_have_moment,
            wyckoff_position_type=wyckoff_position_type,
            preserve_parent_wyckoff_type=preserve_parent_wyckoff_type,
            max_unique=100000, initial_moment=1.0, kept_only=True)
    finally:
        pathmod.postURL = original
    if not candidates:
        raise ValueError("Native subgroup wrapper returned no magnetic candidates")
    if len(candidates) > max_candidates:
        raise ValueError(f"Native wrapper returned {len(candidates)} candidates; "
                         f"max_candidates={max_candidates}. No truncation performed")
    modes = []
    for index, candidate in enumerate(candidates):
        identity = f"subgroup_{index:03d}"
        phase_path = output / f"{identity}_phase.pkl"
        # Locally generated engine artifacts only, never caller-supplied pickle.
        with phase_path.open("wb") as stream:
            pickle.dump(candidate["phase"], stream)
        metadata = {key: value for key, value in candidate.items() if key != "phase"}
        metadata.update(id=identity, phase_path=str(phase_path))
        modes.append(metadata)
    report = {"stage": "candidates", "inputs": inputs, "modes": modes,
              "k_vectors": ks, "magnetic_elements": magnetic_elements,
              "engine": "native_subgroup", "generic_queries": query_records,
              "diagnostics": {
                  "method": "magnetic_structure_candidates.search_magnetic_structure_candidates",
                  # Report the query actually made, not a default: a narrowed run that
                  # claims to be unnarrowed is a misleading record.
                  "exact_wyckoff_orbit": bool(exact_wyckoff_orbit),
                  "limitations": "Bilbao subgroup enumeration, not finite orientation modes. "
                  "Native magnetic symmetry/site constraints and independent orbit moments "
                  "are authoritative; no equal-amplitude inter-orbit constraint.",
                  "network": "Only generic symmetry operators/k transmitted; no CIF/atoms/data."}}
    return {"stage": "candidates", "report_path": w._write(output / "candidates_report.json", report),
            "candidate_count": len(modes),
            "candidates": [{"id": m["id"], "bns_number": m["bns_number"]} for m in modes],
            "diagnostics": report["diagnostics"]}


def assemble(project, parent, candidate, magnetic_phase, *, refine_cell=True):
    """Both scattering phases share the wrapper's exact transformed cell/origin."""
    from . import workflow as w
    _, lat, _, elem, _ = w._modules()
    transform = np.asarray(candidate["transformation_matrix"], float)
    origin = np.asarray(candidate["origin_shift"], float)
    cell_ratio = abs(float(np.linalg.det(transform)))
    if cell_ratio < 1e-12:
        raise ValueError("Singular wrapper cell transformation")
    child = copy.deepcopy(parent)
    child["General"]["Name"] = "nuclear"
    child["General"]["SGData"] = copy.deepcopy(magnetic_phase["General"]["SGData"])
    child["General"]["Cell"] = copy.deepcopy(magnetic_phase["General"]["Cell"])
    child, codes = lat.TransformPhase(
        parent, child, transform, origin, np.zeros(3), False)
    child["General"]["Type"] = "nuclear"
    elem.SetupGeneral(child, None)
    nuclear = project.phase("nuclear")
    haps = copy.deepcopy(nuclear.data["Histograms"])
    nuclear.data.update(child)
    nuclear.data["Histograms"] = {}
    for hist in project.histograms():
        project.link_histogram_phase(hist, nuclear)
        nuclear.data["Histograms"][hist.name]["Scale"] = copy.deepcopy(haps[hist.name]["Scale"])
        # GSAS powder correction is V* = 1/V, while full-cell SF² scales
        # quadratically with cell multiplicity. HAP scales therefore change
        # inversely with cell volume under a representation transformation.
        nuclear.data["Histograms"][hist.name]["Scale"][0] /= cell_ratio
    magnetic = project.add_phase(phasename=candidate["id"], histograms=project.histograms())
    magnetic.data.update(copy.deepcopy(magnetic_phase))
    magnetic.data["General"]["Name"] = candidate["id"]
    magnetic.data["General"]["MagDmin"] = 0.
    magnetic.data["Histograms"] = {}
    for hist in project.histograms():
        project.link_histogram_phase(hist, magnetic)
    # New copied phase identities must not collide in GSAS constraints/bounds.
    import random
    for phase in (nuclear, magnetic):
        phase.data["ranId"] = random.randrange(1, 2**63)
        cia = phase.data["General"]["AtomPtrs"][3]
        for atom in phase.data["Atoms"]:
            atom[cia + 8] = random.randrange(1, 2**63)
    project.update_ids()
    project.index_ids()
    nuclear.set_refinements({"Cell": refine_cell, "Atoms": {"all": "U"}})
    magnetic.set_refinements({"Cell": refine_cell})
    mct = magnetic.data["General"]["AtomPtrs"][1]
    for atom in magnetic.data["Atoms"]:
        atom[mct + 1] = "MU"
    for phase in (nuclear, magnetic):
        w._physical_limits(project, phase)
    # Identical spatial groups/cells: native cell symmetry already reduces A.
    for term in lat.cellUnique(nuclear.data["General"]["SGData"]):
        project.add_EquivConstr(
            [f"{nuclear.data['pId']}::A{term}", f"{magnetic.data['pId']}::A{term}"])
    nx, _, _, ncia = nuclear.data["General"]["AtomPtrs"]
    mx, _, _, mcia = magnetic.data["General"]["AtomPtrs"]
    for i, code in enumerate(magnetic.data["MagAtomCodes"]):
        if code not in codes:
            raise ValueError(f"Magnetic atom operation code {code} absent from transformed nuclear phase")
        j = codes.index(code)
        if not np.allclose(nuclear.data["Atoms"][j][nx:nx+3],
                           magnetic.data["Atoms"][i][mx:mx+3], atol=1e-6):
            raise ValueError("Wrapper magnetic/nuclear transformed coordinates differ")
        if nuclear.data["Atoms"][j][ncia] != magnetic.data["Atoms"][i][mcia]:
            raise ValueError("Wrapper nuclear/magnetic ADP representations differ")
        terms = ["iso"] if nuclear.data["Atoms"][j][ncia] == "I" else [
            "11", "22", "33", "12", "13", "23"]
        for term in terms:
            project.add_EquivConstr(
                [f"{nuclear.data['pId']}::AU{term}:{j}",
                 f"{magnetic.data['pId']}::AU{term}:{i}"])
        for axis in "xyz":
            project.add_EquivConstr(
                [f"{nuclear.data['pId']}::dA{axis}:{j}",
                 f"{magnetic.data['pId']}::dA{axis}:{i}"])
    for hist in project.histograms():
        source = nuclear.data["Histograms"][hist.name]
        target = magnetic.data["Histograms"][hist.name]
        # Each transformed phase has native profile/strain symmetry arrays;
        # only scalar scale is transferred across the transformation.
        target["Scale"] = copy.deepcopy(source["Scale"])
        source["Scale"][1] = target["Scale"][1] = True
        project.add_EquivConstr(
            [f"{nuclear.data['pId']}:{hist.id}:Scale",
             f"{magnetic.data['pId']}:{hist.id}:Scale"])
    return nuclear, magnetic


def export_full(path, nuclear, magnetic):
    """Expand native magnetic symmetry for validated full-composition MCIF."""
    from . import workflow as w
    _, lat, spc, _, _ = w._modules()
    phase = copy.deepcopy(magnetic)
    mx, _, _, cia = phase["General"]["AtomPtrs"]
    sg = phase["General"]["SGData"]
    A = lat.cell2AB(phase["General"]["Cell"][1:7])[0]
    unit = A / np.linalg.norm(A, axis=0)
    atoms = []
    for atom in phase["Atoms"]:
        tensor = atom[cia+2:cia+8] if atom[cia] == "A" else []
        for generated in spc.GenAtom(atom[mx:mx+3], sg, False, Uij=tensor):
            if tensor:
                position, adp, operation, _, spin = generated
            else:
                position, operation, _, spin = generated
            index = abs(operation) % 100 - 1
            rotation = sg["SGOps"][index][0] * (-1 if operation < 0 else 1)
            cartop = A @ rotation @ np.linalg.inv(A)
            moment = spin * np.linalg.det(cartop) * cartop @ unit @ atom[mx+4:mx+7]
            expanded = copy.deepcopy(atom)
            expanded[mx:mx+3] = position.tolist()
            expanded[mx+4:mx+7] = np.linalg.solve(unit, moment).tolist()
            if tensor:
                expanded[cia+2:cia+8] = list(adp)
            atoms.append(expanded)
    phase["Atoms"] = atoms
    phase["General"]["SGData"] = spc.SpcGroup("P 1")[1]
    return w._symmetry_mcif(path, nuclear, phase)


def export_fit_evidence(project, output_dir, identity, nuclear_control=None):
    """Persist full-resolution bank residuals and standalone SVG fit plots."""
    from html import escape
    from . import workflow as w
    output = Path(output_dir)
    paths = []
    controls = ({hist.name: hist for hist in nuclear_control.histograms()}
                if nuclear_control is not None else {})
    for hist in project.histograms():
        x, obs, calc, bg, weight = [np.asarray(hist.getdata(key), float)
                                   for key in ("x", "yobs", "ycalc", "background", "yweight")]
        stem = f"{identity}_{w._slug(hist.name)}_fit"
        csv = output / f"{stem}.csv"
        np.savetxt(csv, np.column_stack((x, obs, calc, obs-calc, bg, weight)),
                   delimiter=",", header="TOF,observed,calculated,difference,background,weight")
        lo, hi = hist.data["Limits"][1]
        used = (x >= lo) & (x <= hi) & np.isfinite(x) & np.isfinite(obs) & np.isfinite(calc)
        if used.sum() < 2:
            raise ValueError(f"Insufficient finite fitted points for plot: {hist.name}")
        xx, yy, cc = x[used], obs[used], calc[used]
        minimum, maximum = min(yy.min(), cc.min()), max(yy.max(), cc.max())
        span = max(float(maximum-minimum), 1.)
        diff = yy-cc
        dspan = max(float(np.abs(diff).max()), 1.)
        def points(values, residual=False):
            xp = 75 + 970*(xx-xx.min())/max(float(xx.max()-xx.min()), 1e-12)
            yp = (490-65*values/dspan if residual else 375-310*(values-minimum)/span)
            return " ".join(f"{a:.3f},{b:.3f}" for a, b in zip(xp, yp))
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="590" '
               'viewBox="0 0 1100 590"><rect width="1100" height="590" fill="white"/>',
               f'<text x="75" y="30" font-size="18">{escape(identity+" — "+hist.name)}</text>',
               '<path d="M75 55V390H1045 M75 415V565H1045 M75 490H1045" '
               'fill="none" stroke="black"/>']
        traces = [("Observed", yy, "black"), ("Nuclear + magnetic calculated", cc, "red")]
        if hist.name in controls:
            control = controls[hist.name]
            if not np.array_equal(x, np.asarray(control.getdata("x"), float)):
                raise ValueError("Nuclear control and magnetic fit plot grids differ")
            traces.append(("Nuclear-only control", np.asarray(control.getdata("ycalc"))[used], "grey"))
        for index, (label, values, color) in enumerate(traces):
            svg.append(f'<polyline fill="none" stroke="{color}" stroke-width=".65" '
                       f'points="{points(values)}"/>')
            svg.append(f'<text x="{75+index*310}" y="405" fill="{color}" '
                       f'font-size="12">{escape(label)}</text>')
        svg.extend([f'<polyline fill="none" stroke="blue" stroke-width=".65" '
                    f'points="{points(diff, True)}"/>',
                    '<text x="75" y="435" font-size="12">Observed − calculated</text>',
                    '<text x="475" y="585" font-size="14">TOF (microseconds)</text>',
                    '<text x="10" y="200" font-size="12">Intensity</text>',
                    f'<text x="75" y="580" font-size="11">{xx.min():.4g}</text>',
                    f'<text x="990" y="580" font-size="11">{xx.max():.4g}</text>', '</svg>'])
        plot = output / f"{stem}.svg"
        plot.write_text("\n".join(svg))
        paths.append({"bank": hist.name, "csv_path": str(csv), "plot_path": str(plot),
                      "plotted_points": int(used.sum()), "full_resolution_points": len(x)})
    return paths


def _profile_limits(hist):
    """Bound effective TOF rates/widths, not just individual coefficients."""
    from . import workflow as w
    inst = hist.data["Instrument Parameters"][0]
    lat = w._modules()[1]
    x = np.asarray(hist.getdata("x"), float)
    lo, hi = hist.data["Limits"][1]
    d = np.asarray(lat.Pos2dsp(inst, x[(x >= lo) & (x <= hi)]), float)
    for reflections in hist.data.get("Reflection Lists", {}).values():
        im = int(reflections.get("Super", 0))
        rows = np.asarray(reflections.get("RefList", []))
        if len(rows):
            used = (rows[:, 5+im] >= lo) & (rows[:, 5+im] <= hi)
            d = np.concatenate((d, rows[used, 4+im]))
    if not len(d) or np.any(~np.isfinite(d)) or np.any(d <= 0):
        raise ValueError(f"Cannot establish positive fitted TOF d range for {hist.name}")
    value = lambda key: float(inst.get(key, [0., 0.])[1])
    return {
        "alpha": 1e-8,
        "beta-0": max(1e-8, float(np.max(-value("beta-1") / d**4 -
                                        value("beta-q") / d**2)) + 1e-8),
        "sig-1": max(0., float(np.max(-(value("sig-0") + value("sig-2") * d**4 +
                                        value("sig-q") * d) / d**2)) + 1e-8),
        "X": max(0., float(np.max(-(value("Y") * d**2 + value("Z")) / d))),
    }


def _fit_native(project, cycles):
    from . import workflow as w
    active = []
    for _ in range(1 + 4 * len(project.histograms())):
        # GSAS's standard Controls default is 0.001. The experimental local
        # solver's stricter tolerance must not silently change native behavior.
        for hist in project.histograms():
            inst = hist.data["Instrument Parameters"][0]
            limits = _profile_limits(hist)
            for parameter in ("alpha", "beta-0", "sig-1", "X"):
                lower = limits[parameter]
                project.set_Controls("parmMin", lower, variable=f":{hist.id}:{parameter}")
                if float(inst[parameter][1]) < lower:
                    inst[parameter][1] = lower
                    variable = f":{hist.id}:{parameter}"
                    project.set_Frozen(variable=variable, mode="add")
                    active.append(variable)
        quality = w._refine(project, cycles, relative_tolerance=.001)
        rv = project.data["Covariance"]["data"].get("Rvals", {})
        numerical_floor = quality.get("weighted_residual_sum", float("inf")) / max(
            quality.get("points", 1), 1) < 1e-8
        if float(rv.get("DelChi2", 0.)) < -.001 and not numerical_floor:
            raise RuntimeError("Native GSAS reported convergence after an increasing objective "
                               f"(DelChi2={rv['DelChi2']}); refusing false convergence")
        invalid = []
        for hist in project.histograms():
            inst = hist.data["Instrument Parameters"][0]
            limits = _profile_limits(hist)
            for parameter in ("alpha", "beta-0", "sig-1", "X"):
                lower = limits[parameter]
                if float(inst[parameter][1]) < lower:
                    invalid.append(f":{hist.id}:{parameter}")
        if not invalid:
            quality["active_profile_boundaries"] = sorted(set(active))
            quality["effective_profile_limits"] = {
                hist.name: _profile_limits(hist) for hist in project.histograms()}
            validity = {}
            for hist in project.histograms():
                lo, hi = hist.data["Limits"][1]
                for name, reflections in hist.data.get("Reflection Lists", {}).items():
                    rows = np.asarray(reflections.get("RefList", []))
                    if not len(rows):
                        continue
                    im = int(reflections.get("Super", 0))
                    used = rows[(rows[:, 5+im] >= lo) & (rows[:, 5+im] <= hi)]
                    if not len(used):
                        continue
                    profile = used[:, np.array([6, 7, 12, 13]) + im]
                    if (np.any(~np.isfinite(profile)) or np.any(profile[:, 0] <= 0) or
                            np.any(profile[:, 1] < 0) or np.any(profile[:, 2:] <= 0)):
                        raise RuntimeError(f"Invalid final fitted TOF reflection widths/rates: "
                                           f"{hist.name}, {name}; refusing skipped-peak model")
                    validity[f"{hist.name}/{name}"] = {
                        "reflection_count": len(used),
                        "minimum_sigma_variance": float(profile[:, 0].min()),
                        "minimum_gamma": float(profile[:, 1].min()),
                        "minimum_alpha": float(profile[:, 2].min()),
                        "minimum_beta": float(profile[:, 3].min())}
            quality["final_reflection_profile_validity"] = validity
            return quality
    raise RuntimeError("Native positive profile active set failed to stabilize")


def refine_native(input_dir, output_dir, *, cycles=24, max_models=256,
                  time_limit_seconds=1800, background_coeffs=6,
                  progress_log=None, report_name="refinement_report.json",
                  refine_cell=True, refine_profile=True, deepen=False,
                  deep_cycles: int | None = None, deep_rounds: int = 3,
                  resume_high_baseline=False,
                  resume_low_baseline=False, native_solver="analytic Hessian",
                  models=None, stage_label="refine"):
    from . import workflow as w
    from threadpoolctl import threadpool_limits
    sc, _, _, _, _ = w._modules()
    if native_solver not in ("Hessian SVD", "analytic Hessian", "analytic Jacobian"):
        raise ValueError("native_solver must be a supported native GSAS analytic solver")
    output = Path(output_dir).resolve()
    candidates = w._read(output / "candidates_report.json")
    inputs = w.discover_inputs(input_dir)
    if candidates["inputs"] != inputs or candidates.get("engine") != "native_subgroup":
        raise ValueError("Native candidate stage must be rerun for these inputs")
    if not 1 <= cycles <= 100 or not 1 <= max_models <= 4096 or not 1 <= background_coeffs <= 20:
        raise ValueError("Invalid native refinement bounds")
    if not 1 <= time_limit_seconds <= 86400:
        raise ValueError("Invalid wall-clock bound")
    # A refinement pass may cover every enumerated candidate or a disclosed subset
    # (the shortlist a cheap screen selected). The scope travels with the report so a
    # partial pass is never mistaken for a complete comparison.
    selected = list(candidates["modes"])
    if models is not None:
        wanted = [str(name) for name in models]
        by_id = {str(mode["id"]): mode for mode in candidates["modes"]}
        unknown = [name for name in wanted if name not in by_id]
        if unknown:
            raise ValueError(f"Unknown native candidate ids: {unknown}")
        if not wanted:
            raise ValueError("No candidates selected for refinement")
        selected = [by_id[name] for name in wanted]
    if len(selected) > max_models:
        raise ValueError(f"Need all {len(selected)} native models; max_models={max_models}")
    start = time.monotonic()
    report = {"stage": "refine", "engine": "native_subgroup", "inputs": inputs,
              "native_solver": native_solver, "models": [], "complete": False,
              "scope": {"stage_label": stage_label,
                        "selected": len(selected),
                        "enumerated": len(candidates["modes"]),
                        "selector": "explicit candidate subset" if models is not None
                        else "all enumerated candidates"}}
    report_path = output / str(report_name)
    def persist():
        report["elapsed_seconds"] = time.monotonic() - start
        report["progress"] = {
            "models_completed": len(report["models"]),
            "models_total": len(selected),
            "models_converged": sum(model.get("status") == "ok" for model in report["models"]),
            "remaining_budget_seconds": max(0., time_limit_seconds-report["elapsed_seconds"])}
        return w._write(report_path, report)
    def fit(project):
        project.data["Controls"]["data"]["deriv type"] = native_solver
        return _fit_native(project, cycles)
    resolved_deep_cycles = int(deep_cycles) if deep_cycles else max(cycles * 2, 24)

    def deep_fit(project, solver=None):
        project.data["Controls"]["data"]["deriv type"] = solver or native_solver
        return _fit_native(project, resolved_deep_cycles)

    def profile_flags(project):
        if not refine_profile:
            return
        project.index_ids()
        for hist in project.histograms():
            hist.set_refinements({"Instrument Parameters": ["alpha", "beta-0", "sig-1", "X"]})
            for parameter in ("alpha", "beta-0", "sig-1", "X"):
                project.set_Controls("parmMin", 1e-8 if parameter in ("alpha", "beta-0") else 0.,
                                     variable=f":{hist.id}:{parameter}")
    try:
        _note(progress_log, f"开始核基线精修（{len(inputs['banks'])} 个 bank），随后逐个候选做磁精修")
        with _deadline(time_limit_seconds), threadpool_limits(1), w.cached_tof_profiles(
                importlib.import_module("GSASIIpwd")):
            if resume_high_baseline:
                previous = w._read(report_path)
                if previous["inputs"] != inputs:
                    raise ValueError("Persisted native baseline belongs to different inputs")
                high = sc.G2Project(gpxfile=str(output / "high_nuclear.gpx"))
                high_stages = [w._quality(high)]
                if not high_stages[0]["converged"]:
                    raise ValueError("Persisted high-temperature baseline did not converge")
            else:
                high = sc.G2Project(newgpx=str(output / "high_nuclear.gpx"))
                high_hist = [w._histogram(high, b, "high") for b in inputs["banks"]]
                nuclear = high.add_phase(inputs["cif"], phasename="nuclear", histograms=high_hist)
                for hist in high_hist:
                    hist.set_refinements({"Background": {"no. coeffs": background_coeffs, "refine": True}})
                    hist.data["Sample Parameters"]["Scale"][1] = False
                nuclear.set_HAP_refinements({"Scale": True})
                w._physical_limits(high, nuclear)
                high_stages = [fit(high)]
                nuclear.set_refinements({"Cell": refine_cell, "Atoms": {"all": "XU"}})
                if nuclear.data["General"]["SGData"].get("SGPolax"):
                    nuclear.atoms()[0].refinement_flags = "U"
                high_stages.append(fit(high))
                if refine_profile:
                    profile_flags(high)
                    high_stages.append(fit(high))
            parent = copy.deepcopy(high.phase("nuclear").data)
            if resume_low_baseline:
                previous = w._read(report_path)
                if previous["inputs"] != inputs:
                    raise ValueError("Persisted low baseline belongs to different inputs")
                base = sc.G2Project(gpxfile=str(output / "low_nuclear.gpx"))
                control = w._quality(base)
                if not control["converged"]:
                    raise ValueError("Persisted low-temperature baseline did not converge")
                low_stages = [control]
            else:
                base = sc.G2Project(newgpx=str(output / "low_nuclear.gpx"))
                low_hist = [w._histogram(base, b, "low") for b in inputs["banks"]]
                low = base.add_phase(inputs["cif"], phasename="nuclear", histograms=low_hist)
                haps = low.data["Histograms"]
                low.data.update(copy.deepcopy(parent))
                low.data["Histograms"] = haps
                for hi, lo in zip(high.histograms(), low_hist):
                    lo.data["Background"] = copy.deepcopy(hi.data["Background"])
                    lo.data["Instrument Parameters"] = copy.deepcopy(hi.data["Instrument Parameters"])
                    for parameter in lo.data["Instrument Parameters"][0].values():
                        if isinstance(parameter, list) and len(parameter) > 2:
                            parameter[2] = False
                    lo.data["Sample Parameters"]["Scale"][1] = False
                    low.data["Histograms"][lo.name]["Scale"] = copy.deepcopy(
                        parent["Histograms"][hi.name]["Scale"])
                low.set_refinements({"Cell": False, "Atoms": {"all": " "}})
                w._physical_limits(base, low)
                low_stages = [fit(base)]
                low.set_refinements({"Cell": refine_cell, "Atoms": {"all": "XU"}})
                if low.data["General"]["SGData"].get("SGPolax"):
                    low.atoms()[0].refinement_flags = "U"
                low_stages.append(fit(base))
                profile_flags(base)
                control = fit(base)
                low_stages.append(control)
            report["baseline"] = {"high": high_stages, "low_nuclear_control": control,
                                  "low_stages": low_stages}
            persist()
            _note(progress_log, f"核基线就绪，开始磁精修：共 {len(selected)} 个候选，每个候选走"
                                f"固定阶梯（先磁矩分量→再峰型/ADP/晶胞）")
            for candidate in selected:
                model = {"id": candidate["id"], "bns_number": candidate["bns_number"],
                         "native_magnetic_space_group": candidate["magnetic_space_group"],
                         "transformation_matrix": candidate["transformation_matrix"],
                         "origin_shift": candidate["origin_shift"]}
                try:
                    project = sc.G2Project(gpxfile=base.filename)
                    project.save(str(output / f"{candidate['id']}.gpx"))
                    project.data["Controls"]["data"].setdefault("parmFrozen", {})["FrozenList"] = []
                    phase_path = Path(candidate["phase_path"]).resolve()
                    if phase_path.parent != output:
                        raise ValueError("Candidate phase artifact escapes output directory")
                    with phase_path.open("rb") as stream:
                        phase = pickle.load(stream)
                    # Transform refined low-T nuclear model, not initial cell.
                    current = copy.deepcopy(project.phase("nuclear").data)
                    pcia = current["General"]["AtomPtrs"][3]
                    pcx = current["General"]["AtomPtrs"][0]
                    for atom, high_atom in zip(current["Atoms"], parent["Atoms"]):
                        atom[pcia:pcia+8] = copy.deepcopy(high_atom[pcia:pcia+8])
                        atom[pcx:pcx+3] = copy.deepcopy(high_atom[pcx:pcx+3])
                    transform = np.asarray(candidate["transformation_matrix"])
                    phase["General"]["Cell"][1:] = w._modules()[1].TransformCell(
                        current["General"]["Cell"][1:7], transform)
                    # Rebuild with authoritative descriptor to propagate thermal
                    # state and origin identically into magnetic phase.
                    descriptor = {"Name": candidate["id"], "SGData": phase["General"]["SGData"],
                                  "Trans": transform, "Uvec": np.asarray(candidate["origin_shift"]),
                                  "Cell": phase["General"]["Cell"][1:]}
                    wrapper = w._modules()[4]
                    # Keep the parent-atom selection out of `selected`: that name holds the
                    # refinement scope, which persist() and the completion check read.
                    _, parent_atoms = wrapper._select_parent_atoms(
                        current, candidates["magnetic_elements"], None)
                    phase, sites = wrapper._build_magnetic_phase(current, descriptor, parent_atoms, 1.)
                    nuclear, magnetic = assemble(project, current, candidate, phase, refine_cell=refine_cell)
                    for hist in project.histograms():
                        for parameter in hist.data["Instrument Parameters"][0].values():
                            if isinstance(parameter, list) and len(parameter) > 2:
                                parameter[2] = False
                    nuclear.set_refinements({"Cell": False, "Atoms": {"all": " "}})
                    magnetic.set_refinements({"Cell": False})
                    mct = magnetic.data["General"]["AtomPtrs"][1]
                    for atom in magnetic.data["Atoms"]:
                        atom[mct+1] = "M"
                    # Independent native moment components/common scales first;
                    # contaminated nuclear-only ADPs must not initialize them.
                    first_quality = fit(project)
                    nuclear = project.phase("nuclear")
                    magnetic = project.phase(candidate["id"])
                    nuclear.set_refinements({"Cell": refine_cell, "Atoms": {"all": "XU"}})
                    magnetic.set_refinements({"Cell": refine_cell})
                    for atom in magnetic.data["Atoms"]:
                        atom[mct+1] = "MXU"
                    if nuclear.data["General"]["SGData"].get("SGPolax"):
                        nuclear.atoms()[0].refinement_flags = "U"
                        nx = nuclear.data["General"]["AtomPtrs"][0]
                        mx = magnetic.data["General"]["AtomPtrs"][0]
                        anchor = nuclear.data["Atoms"][0][nx:nx+3]
                        for atom in magnetic.data["Atoms"]:
                            if np.allclose(atom[mx:mx+3], anchor, atol=1e-6):
                                atom[mct+1] = "MU"
                    w._release_adp_boundaries(project)
                    profile_flags(project)
                    quality = fit(project)
                    comparison_quality = quality
                    nuclear = project.phase("nuclear")
                    magnetic = project.phase(candidate["id"])
                    mcif = output / f"{candidate['id']}.mcif"
                    physical = export_full(mcif, nuclear.data, magnetic.data)
                    evidence = export_fit_evidence(project, output, candidate["id"], base)
                    model.update(status="ok", quality=quality, comparison_quality=comparison_quality,
                                 deepening=None, moment_scale_stage=first_quality, physical=physical,
                                 magnetic_sites=sites, gpx_path=project.filename, mcif_path=str(mcif),
                                 fit_evidence_paths=evidence,
                                 free_moment_components=free_moment_components(candidate))
                    model["symmetry_diagnostics"] = {
                        "native_bns_number": candidate["bns_number"],
                        "refined_moment_stabilizer_bns_number": physical["bns_number"],
                        "export_basis": "Wrapper transformed child cell with full composition",
                        "note": "A zero/specialized fitted moment may have higher symmetry than "
                                "the nominated subgroup; both identities are retained."}
                    if deepen:
                      try:
                        # The comparison above already decided which model to trust. This
                        # ladder then pushes that one model as far as the data support, and
                        # its metrics are recorded as post-comparison so nobody reads them
                        # as if every candidate had received the same treatment.
                        # The comparison ladder already released cell, ADPs, the magnetic
                        # moments and the profile, so merely re-releasing them changes nothing.
                        # A real deepening attacks the residual with *new* freedom or a
                        # different route out of a stall, one strategy per round.
                        # Raising the background order here would re-parameterize only the
                        # selected model and pushed this case's fit *away* from its minimum
                        # (45.09 -> 46.67). Background/profile freedom has to be applied to
                        # every candidate in the comparison stage to stay fair, so deepening
                        # only uses routes that cannot corrupt the parameterization.
                        strategies = [
                            ("refit_same_model_48_cycles", {}),
                            ("alternative_solver", {"solver": "analytic Hessian"}),
                            ("perturbed_moment_restart", {"perturb": 0.05}),
                        ][:max(1, int(deep_rounds))]
                        rounds = []
                        for round_index, (strategy, options) in enumerate(strategies):
                            nuclear = project.phase("nuclear")
                            magnetic = project.phase(candidate["id"])
                            nuclear.set_refinements({"Cell": True, "Atoms": {"all": "XU"}})
                            magnetic.set_refinements({"Cell": True})
                            for atom in magnetic.data["Atoms"]:
                                atom[mct+1] = "MXU"
                            w._release_adp_boundaries(project)
                            profile_flags(project)
                            if options.get("perturb"):
                                mxa = magnetic.data["General"]["AtomPtrs"][0]
                                for atom in magnetic.data["Atoms"]:
                                    atom[mxa+4:mxa+7] = (np.asarray(atom[mxa+4:mxa+7],
                                                                    dtype=float)
                                                         * (1.0 + float(options["perturb"]))).tolist()
                            quality = deep_fit(project, options.get("solver"))
                            rounds.append({"round": round_index + 1,
                                           "strategy": strategy,
                                           "reduced_chisq": quality["reduced_chisq"],
                                           "histograms": quality["histograms"]})
                            _note(progress_log, f"{candidate['id']} 深修第 {round_index + 1} 轮"
                                                f"（{strategy}）：reduced χ²={quality['reduced_chisq']:.2f}")
                            # Every strategy gets its turn: a round that changes nothing is
                            # exactly the case the next strategy exists for, so stopping on it
                            # would hide the solver switch and the perturbed restart.
                        deepening = {
                            "rounds": rounds,
                            "before_reduced_chisq": comparison_quality["reduced_chisq"],
                            "after_reduced_chisq": quality["reduced_chisq"],
                            "improved": quality["reduced_chisq"] < comparison_quality["reduced_chisq"],
                            "cycles_per_round": resolved_deep_cycles,
                            "note": "Post-comparison deepening of the selected model; these "
                                    "metrics are not comparable with the other candidates."}
                        # Export the deepened structure separately: the comparison-stage MCIF
                        # must survive even if this export is what fails.
                        nuclear = project.phase("nuclear")
                        magnetic = project.phase(candidate["id"])
                        deep_mcif = output / f"{candidate['id']}.deep.mcif"
                        deep_physical = export_full(deep_mcif, nuclear.data, magnetic.data)
                        deepening["mcif_path"] = str(deep_mcif)
                        deepening["bns_number"] = deep_physical.get("bns_number")
                        deepening["fit_evidence_paths"] = export_fit_evidence(
                            project, output, f"{candidate['id']}-deep", base)
                      except Exception as exc:
                        # A deepening strategy may drive the solver into a nonfinite state.
                        # That must never invalidate the comparison result: the model already
                        # converged, so keep it and record only that deepening failed.
                        deepening = {"status": "failed", "error_type": type(exc).__name__,
                                     "error": str(exc),
                                     "note": "Deepening failed; the comparison-stage result of "
                                             "this model stands."}
                        try:
                            nuclear = project.phase("nuclear")
                            magnetic = project.phase(candidate["id"])
                        except Exception:
                            pass
                    if deepen:
                        # Attach the deepening record only after it ran: the comparison result
                        # above is already finalised and is what a failed deepening falls back to.
                        model.update(quality=quality, deepening=model.get("deepening") or deepening,
                                     mcif_path=model["mcif_path"])
                except TimeoutError:
                    raise
                except Exception as exc:
                    import traceback
                    model.update(status="failed", error_type=type(exc).__name__,
                                 error=str(exc), traceback=traceback.format_exc())
                    model["failure_class"] = classify_failure(model)
                    try:
                        model["last_solver_quality"] = w._quality(project)
                        model["gpx_path"] = project.filename
                        model["fit_evidence_paths"] = export_fit_evidence(
                            project, output, candidate["id"], base)
                    except Exception:
                        pass
                report["models"].append(model)
                persist()
                print(json.dumps({"native_refinement_progress": report["progress"],
                                  "latest_model": model["id"], "latest_status": model["status"]}),
                      flush=True)
                best_so_far = min((m for m in report["models"] if m["status"] == "ok"),
                                  key=lambda m: m["quality"]["bic_known_variance"], default=None)
                _note(progress_log,
                      f"磁精修进度 {len(report['models'])}/{len(selected)}：{model['id']}"
                      f"（{candidate.get('bns_number')}）{model['status']}"
                      + (f"，当前最优 {best_so_far['id']} reduced χ²="
                         f"{best_so_far['quality']['reduced_chisq']:.2f}" if best_so_far else ""))
    except TimeoutError as exc:
        # The wall-clock bound is a work bound, not a verdict: every model already fitted is
        # valid evidence, and the comparison the budget allowed must still reach ranking and
        # export. Discarding that work turned a usable partial answer into a stage failure.
        # Only a run with nothing usable left in it is a genuine failure.
        if not any(model.get("status") == "ok" for model in report["models"]):
            report["scientific_status"] = {"status": "unresolved", "comparison_complete": False,
                                           "warning": str(exc)}
            persist()
            raise
        report["time_bound_reached"] = True
        report["incomplete_reason"] = str(exc)
    except Exception as exc:
        report["scientific_status"] = {"status": "unresolved", "comparison_complete": False,
                                       "warning": f"Native baseline failed: {type(exc).__name__}: {exc}"}
        persist()
        raise
    good = sorted([m for m in report["models"] if m["status"] == "ok"],
                  key=lambda m: m["quality"]["bic_known_variance"])
    if not good:
        persist()
        reasons = "; ".join(f"{m['id']}: {m.get('error')}" for m in report["models"][:8])
        raise RuntimeError(f"All native models failed: {reasons}; see persisted refinement_report.json")
    best = good[0]
    # Within the statistically tied set, prefer the more constrained description rather
    # than the one that happened to fit the noise 0.01 % better.
    best_chisq = best["quality"]["reduced_chisq"]
    tied = [m for m in good
            if abs(m["quality"]["reduced_chisq"] - best_chisq) <= TIE_RELATIVE_CHISQ * abs(best_chisq)]
    tie_break = None
    if len(tied) > 1:
        ranked = sorted((m.get("free_moment_components"), m["quality"]["bic_known_variance"], m["id"], m)
                        for m in tied if m.get("free_moment_components") is not None)
        if ranked and ranked[0][3]["id"] != best["id"]:
            chosen = ranked[0][3]
            tie_break = {
                "preferred": chosen["id"], "over": best["id"],
                "rule": "fewest free moment components among statistically tied models",
                "tied_models": sorted(m["id"] for m in tied),
                "relative_chisq_gap": round(abs(best_chisq - chosen["quality"]["reduced_chisq"])
                                            / abs(best_chisq), 8),
                "note": ("The tied models fit within the Rietveld systematic-error level; the "
                         "more constrained one is exported and the tie is reported."),
            }
            best = chosen
    failures = len(report["models"]) - len(good)
    alternatives = [m["id"] for m in good if m["quality"]["bic_known_variance"] -
                    best["quality"]["bic_known_variance"] < 6]
    covariance_warning = best["quality"].get("refinement_warnings", "")
    moment_correlated = "**" in covariance_warning and "AM" in covariance_warning
    time_bound_reached = bool(report.get("time_bound_reached"))
    scope_complete = len(report["models"]) >= len(selected)
    incomplete = time_bound_reached or not scope_complete
    unresolved = (failures > 0 or incomplete or len(alternatives) > 1 or
                  best["quality"]["reduced_chisq"] > 5 or moment_correlated)
    scientific = {"status": "unresolved" if unresolved else "qualified_model_comparison",
                  "comparison_complete": failures == 0 and not incomplete,
                  "time_bound_reached": time_bound_reached,
                  "models_attempted": len(report["models"]),
                  "models_total": len(selected),
                  "models_converged": len(good), "failed_models": failures,
                  "tie_break": tie_break,
                  "failure_classes": {name: sum(1 for m in report["models"]
                                                if m.get("failure_class") == name)
                                      for name in sorted({m.get("failure_class")
                                                          for m in report["models"]
                                                          if m.get("failure_class")})},
                  "deepened_models": [m["id"] for m in good if m.get("deepening")],
                  "free_moment_components": {m["id"]: m.get("free_moment_components") for m in good},
                  "near_degenerate_models": alternatives, "identifiability_status":
                  "not_established" if unresolved else "conditional_on_native_model_scope",
                  "best_reduced_chisq": best["quality"]["reduced_chisq"],
                  "bank_rwp": best["quality"]["histograms"],
                  "moment_direction_highly_correlated": moment_correlated,
                  "warning": "Native subgroup fitted representative only; examine convergence, "
                  "covariance, systematic residuals and ion form factors. No benchmark PASS established."}
    report.update(complete=not incomplete, best_model=best["id"], scientific_status=scientific,
                  gpx_path=best["gpx_path"], mcif_path=best["mcif_path"],
                  diagnostics={"method": "Native GSASIIscriptable Rietveld, wrapper subgroup phases",
                               "native_solver": native_solver,
                               "independent_orbit_moments": True,
                               "phase_scales": "Magnetic-only+nuclear same transformed cell, tied 1:1",
                               "network": "Only candidate-stage generic symmetry/k query",
                               "form_factor_types": best["physical"]["magnetic_form_factor_types"]})
    path = persist()
    return {"stage": "refine", "report_path": path, "best_model": best["id"],
            "gpx_path": best["gpx_path"], "mcif_path": best["mcif_path"],
            "final_gpx_path": best["gpx_path"], "final_mcif_path": best["mcif_path"],
            "fit_evidence_paths": best["fit_evidence_paths"],
            "scientific_status": scientific, "failed_models": failures,
            "complete": report["complete"], "time_bound_reached": time_bound_reached,
            "models_evaluated": len(report["models"]), "models_total": len(selected),
            "fit_quality": {"reduced_chisq": best["quality"]["reduced_chisq"],
                            "bank_rwp": best["quality"]["histograms"]},
            "warning": scientific["warning"], "diagnostics": report["diagnostics"]}
