#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools/pdf_refinement/0pdf_refine.py"

PDF_FILE = str(REPO_ROOT / "datasets/BL16/RUNdataFiles/RUN0023568/Gr_Hermite.txt")
CIF_FILES = [str(REPO_ROOT / "datasets/BL16/RUNdataFiles/RUN0023568/cifFiles/Si.cif")]

USER_ID = "wanghao96"
SESSION_ID = "si_auto_search"
MISSION_NAME = "si_pdf_refine"

TARGET_RW = 0.13
MAX_ROUNDS = 10
MIN_IMPROVEMENT = 1e-4


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("pdf_refine_mod", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _detect_r_bounds(data_file: str) -> Tuple[float, float]:
    r_values: List[float] = []
    with open(data_file, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            try:
                r_values.append(float(parts[0]))
            except Exception:
                continue
    if not r_values:
        raise ValueError(f"No numeric r values found in {data_file}")
    return min(r_values), max(r_values)


def _cfg(
    refine_params: Dict[str, Any],
    phase_refine: Optional[Dict[str, bool]] = None,
    params_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    phase_ref = {
        "lattice": False,
        "scale": False,
        "temperature": False,
    }
    if isinstance(phase_refine, dict):
        phase_ref.update(phase_refine)

    cfg: Dict[str, Any] = {
        "refine_params": refine_params,
        "phases": [
            {
                "phase_index": 1,
                "initial": {
                    "lattice": None,
                    "scale": 1.0,
                    "temperature_factor": 0.005,
                },
                "refine": phase_ref,
                "atom_params": {},
            }
        ],
    }
    if params_override:
        cfg["paramsCurrentValue"] = dict(params_override)
    return cfg


def _is_finite_number(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def _is_physical(result: Dict[str, Any]) -> bool:
    rp = result.get("refined_params") if isinstance(result.get("refined_params"), dict) else {}
    if not rp:
        return False

    rw = rp.get("rw")
    if not _is_finite_number(rw):
        return False

    checks = [
        ("qdamp", 0.0, 0.2),
        ("delta2", 0.0, 20.0),
        ("dataScale", 0.0, 5.0),
        ("scale_1", 0.0, 5.0),
        ("lattice_parameter_1", 4.0, 6.0),
        ("temperature_factor_1", 0.0, 0.5),
    ]

    for key, vmin, vmax in checks:
        if key in rp and rp.get(key) is not None:
            val = rp.get(key)
            if not _is_finite_number(val):
                return False
            fval = float(val)
            if fval < vmin or fval > vmax:
                return False

    return True


def _extract_rw(result: Dict[str, Any]) -> float:
    rp = result.get("refined_params") if isinstance(result.get("refined_params"), dict) else {}
    return float(rp.get("rw", 9e9))


def build_candidates(rmin: float, rmax: float, round_idx: int) -> List[Tuple[str, Dict[str, Any]]]:
    base = {
        "data_scale": False,
        "qdamp": False,
        "qbroad": False,
        "delta1": False,
        "delta2": False,
        "lattice": False,
        "phase_scale": False,
        "temperature": False,
        "cycles": 1,
        "coordinates": False,
        "occupancy": False,
        "uij": False,
    }

    def rp(**kwargs: Any) -> Dict[str, Any]:
        merged = dict(base)
        merged.update(kwargs)
        return merged

    cands: List[Tuple[str, Dict[str, Any]]] = [
        ("data_scale", _cfg(rp(data_scale=True, cycles=2))),
        ("lattice", _cfg(rp(lattice=True, cycles=2), phase_refine={"lattice": True})),
        ("qdamp", _cfg(rp(qdamp=True, cycles=2))),
        ("delta2", _cfg(rp(delta2=True, cycles=2))),
        ("temperature", _cfg(rp(temperature=True, cycles=1), phase_refine={"temperature": True})),
        ("data_scale+lattice", _cfg(rp(data_scale=True, lattice=True, cycles=2), phase_refine={"lattice": True})),
        ("data_scale+qdamp", _cfg(rp(data_scale=True, qdamp=True, cycles=2))),
        ("lattice+qdamp", _cfg(rp(lattice=True, qdamp=True, cycles=2), phase_refine={"lattice": True})),
        ("lattice+delta2", _cfg(rp(lattice=True, delta2=True, cycles=2), phase_refine={"lattice": True})),
        (
            "data_scale+lattice+qdamp",
            _cfg(rp(data_scale=True, lattice=True, qdamp=True, cycles=2), phase_refine={"lattice": True}),
        ),
    ]

    if round_idx == 0:
        # First round also tests shorter fitting windows.
        for trial_rmax in [10.0, 12.0, 15.0, min(18.0, rmax)]:
            if trial_rmax <= rmin + 1.0:
                continue
            cands.append(
                (
                    f"window_rmax_{trial_rmax:g}+data_scale+lattice",
                    _cfg(
                        rp(data_scale=True, lattice=True, cycles=2),
                        phase_refine={"lattice": True},
                        params_override={"rmin": rmin, "rmax": float(trial_rmax)},
                    ),
                )
            )

    return cands


def build_reference_stages() -> List[Tuple[str, Dict[str, Any]]]:
    """Reference staged flow provided by user.

    1) lattice + phase scale
    2) add delta2
    3) add temperature
    4) add qdamp (all released)
    """

    def stage_cfg(
        *,
        qdamp: bool,
        delta2: bool,
        lattice: bool,
        phase_scale: bool,
        temperature: bool,
        cycles: int,
    ) -> Dict[str, Any]:
        return {
            "refine_params": {
                "data_scale": False,
                "qdamp": qdamp,
                "qbroad": False,
                "delta1": False,
                "delta2": delta2,
                "lattice": lattice,
                "phase_scale": phase_scale,
                "temperature": temperature,
                "cycles": cycles,
                "coordinates": False,
                "occupancy": False,
                "uij": False,
            },
            "phases": [
                {
                    "phase_index": 1,
                    "initial": {
                        "lattice": None,
                        "scale": 1.0,
                        "temperature_factor": 0.005,
                    },
                    "refine": {
                        "lattice": lattice,
                        "scale": phase_scale,
                        "temperature": temperature,
                    },
                    "atom_params": {},
                }
            ],
        }

    return [
        ("ref_stage1_lattice_scale", stage_cfg(qdamp=False, delta2=False, lattice=True, phase_scale=True, temperature=False, cycles=1)),
        ("ref_stage2_release_delta2", stage_cfg(qdamp=False, delta2=True, lattice=True, phase_scale=True, temperature=False, cycles=1)),
        ("ref_stage3_release_temperature", stage_cfg(qdamp=False, delta2=True, lattice=True, phase_scale=True, temperature=True, cycles=1)),
        ("ref_stage4_release_qdamp", stage_cfg(qdamp=True, delta2=True, lattice=True, phase_scale=True, temperature=True, cycles=1)),
    ]


def main() -> None:
    mod = load_module()
    data_rmin, data_rmax = _detect_r_bounds(PDF_FILE)
    fit_rmin = max(1.0, data_rmin)
    safe_rmax = data_rmax - max(0.01, (data_rmax - data_rmin) * 0.001)
    fit_rmax = min(20.0, safe_rmax)

    init = mod.initialize_pdf_project(
        data_file=PDF_FILE,
        structure_files=CIF_FILES,
        qmax=25.0,
        rmin=fit_rmin,
        rmax=fit_rmax,
        pdf_source="N",
        user_id=USER_ID,
        session_id=SESSION_ID,
        mission_name=MISSION_NAME,
        task_id="init",
    )
    if init.get("status") != "success":
        raise SystemExit(json.dumps(init, ensure_ascii=False, indent=2))

    best_path = init["project_path"]
    best_rw = 9e9
    accepted_steps: List[Dict[str, Any]] = []

    print(f"init snapshot: {best_path}")
    print(f"fit range: [{fit_rmin:.6f}, {fit_rmax:.6f}]")

    print("\n=== REFERENCE STAGED FLOW ===")
    for stage_name, stage_cfg in build_reference_stages():
        try:
            stage_res = mod.refine(
                project_path=best_path,
                refine_config=stage_cfg,
                user_id=USER_ID,
                session_id=SESSION_ID,
                mission_name=MISSION_NAME,
                task_id=stage_name,
            )
        except Exception as e:
            print(f"[EXCEPTION] {stage_name}: {e}")
            continue

        if stage_res.get("status") != "success":
            print(f"[FAIL] {stage_name}: {stage_res.get('error')}")
            continue

        rw = _extract_rw(stage_res)
        if not _is_physical(stage_res):
            print(f"[REJECT] {stage_name}: non-physical params, rw={rw:.6f}")
            continue

        if rw <= best_rw - MIN_IMPROVEMENT:
            best_rw = rw
            best_path = stage_res["project_path"]
            rp = stage_res.get("refined_params", {})
            accepted_steps.append(
                {
                    "round": 0,
                    "candidate": stage_name,
                    "rw": best_rw,
                    "project_path": best_path,
                    "dataScale": rp.get("dataScale"),
                    "qdamp": rp.get("qdamp"),
                    "delta2": rp.get("delta2"),
                    "lattice_parameter_1": rp.get("lattice_parameter_1"),
                    "temperature_factor_1": rp.get("temperature_factor_1"),
                    "scale_1": rp.get("scale_1"),
                }
            )
            print(f"[ACCEPT] {stage_name}: rw={best_rw:.6f}")
        else:
            print(f"[SKIP] {stage_name}: rw={rw:.6f}, no improvement")

        if best_rw <= TARGET_RW:
            print(f"Reached target rw <= {TARGET_RW:.3f} in reference staged flow")
            break

    for round_idx in range(MAX_ROUNDS):
        print(f"\n=== ROUND {round_idx + 1}/{MAX_ROUNDS} ===")
        candidates = build_candidates(fit_rmin, fit_rmax, round_idx)
        round_best: Optional[Dict[str, Any]] = None

        for i, (name, cfg) in enumerate(candidates, start=1):
            task_id = f"r{round_idx+1:02d}_c{i:02d}_{name}".replace("+", "_").replace(".", "p")
            try:
                res = mod.refine(
                    project_path=best_path,
                    refine_config=cfg,
                    user_id=USER_ID,
                    session_id=SESSION_ID,
                    mission_name=MISSION_NAME,
                    task_id=task_id,
                )
            except Exception as e:
                print(f"[EXCEPTION] {name}: {e}")
                continue

            if res.get("status") != "success":
                err = res.get("error", "unknown")
                print(f"[FAIL] {name}: {err}")
                continue

            rw = _extract_rw(res)
            ok_physical = _is_physical(res)
            if not ok_physical:
                print(f"[REJECT] {name}: non-physical params, rw={rw:.6f}")
                continue

            print(f"[OK] {name}: rw={rw:.6f}")
            if round_best is None or rw < round_best["rw"]:
                round_best = {
                    "name": name,
                    "rw": rw,
                    "result": res,
                }

        if round_best is None:
            print("No acceptable candidate in this round. Stop search.")
            break

        improvement = best_rw - round_best["rw"]
        if improvement < MIN_IMPROVEMENT:
            print(f"Best candidate improves only {improvement:.6g} (< {MIN_IMPROVEMENT}). Stop search.")
            break

        best_rw = round_best["rw"]
        best_path = round_best["result"]["project_path"]
        rp = round_best["result"].get("refined_params", {})

        accepted = {
            "round": round_idx + 1,
            "candidate": round_best["name"],
            "rw": best_rw,
            "project_path": best_path,
            "dataScale": rp.get("dataScale"),
            "qdamp": rp.get("qdamp"),
            "delta2": rp.get("delta2"),
            "lattice_parameter_1": rp.get("lattice_parameter_1"),
            "temperature_factor_1": rp.get("temperature_factor_1"),
            "scale_1": rp.get("scale_1"),
        }
        accepted_steps.append(accepted)
        print(f"[ACCEPT] {round_best['name']} -> rw={best_rw:.6f}")

        if best_rw <= TARGET_RW:
            print(f"Reached target rw <= {TARGET_RW:.3f}")
            break

    export_res = mod.export_cifs(
        project_path=best_path,
        task_id="export_final",
        user_id=USER_ID,
        session_id=SESSION_ID,
        mission_name=MISSION_NAME,
    )

    report = {
        "target_rw": TARGET_RW,
        "final_rw": best_rw,
        "final_project_path": best_path,
        "accepted_steps": accepted_steps,
        "export_status": export_res.get("status"),
        "export_files": export_res.get("files", []),
    }

    report_path = REPO_ROOT / "test_output/si_refine_search_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== FINAL REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved report: {report_path}")


if __name__ == "__main__":
    main()
