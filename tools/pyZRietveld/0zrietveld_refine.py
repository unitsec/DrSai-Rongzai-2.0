import logging
import os
import sys
import traceback
import re
import csv
import shutil
from itertools import dropwhile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

try:
    from src.core.tools import ProcessPollingMcpToolBase
except ImportError:
    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.core.tools import ProcessPollingMcpToolBase


# Ensure this repository root is importable so that refineTool can be imported.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

import refineTool  # noqa: E402


TMP_DIR = WORKSPACE_ROOT / "userRecord"
TMP_DIR.mkdir(parents=True, exist_ok=True)


mcp = FastMCP(
    name="RefineToolsZRietveld",
    instructions=(
        "A tool for Z-Rietveld project generation and refinement. "
        "Supports generating .zrietveldn and manual refinement with cfg."
    ),
    port=42511,
)


def _setup_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("zrietveld_mcp")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if module is reloaded.
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(output_dir / "zrietveld_refine.log")
    fh.setFormatter(formatter)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _ensure_env(zrt_data_dir: Optional[str], zrt_lib_dir: Optional[str]) -> None:
    # Allow caller override; otherwise use conventional local paths.
    if zrt_data_dir:
        os.environ["ZRT_DATA_DIR"] = zrt_data_dir
    else:
        os.environ.setdefault(
            "ZRT_DATA_DIR",
            str(REPO_ROOT / "Z-Rietveld" / "Z-Rietveld" / "utility_atomic_data"),
        )

    if zrt_lib_dir:
        os.environ["ZRT_LIB_DIR"] = zrt_lib_dir
    else:
        os.environ.setdefault("ZRT_LIB_DIR", str(REPO_ROOT / "Z-Rietveld" / "lib"))


def _normalize_cfg(cfg: Optional[Dict[str, object]]) -> Dict[str, str]:
    if not cfg:
        return {}

    out: Dict[str, str] = {}
    for k, v in cfg.items():
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        else:
            out[k] = str(v)
    return out


def _run_manual_refine(zrtn_path: str, cfg: Dict[str, str], logger: logging.Logger) -> Dict[str, object]:
    logger.info("Start manual refine: %s", zrtn_path)
    logger.info("cfg keys: %s", sorted(cfg.keys()))
    refine_result = refineTool.refine(zrtn_path, cfg)
    logger.info("Manual refine done: %s", zrtn_path)
    return refine_result


def _parse_rwp_from_report(report_path: Optional[str]) -> Optional[float]:
    if not report_path:
        return None
    p = Path(report_path)
    if not p.exists():
        return None

    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    m = re.search(r"\*\s*Rwp\s*,\s*Re\s*\n\s*([+-]?[0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?)", text)
    if not m:
        return None
    try:
        return float(m.group(1)) * 100.0
    except Exception:
        return None


def _build_compact_report(report_path: Optional[str], drop_start: int = 36, drop_end: int = 109) -> str:
    if not report_path:
        return ""
    p = Path(report_path)
    if not p.exists():
        return ""

    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""

    kept: List[str] = []
    for i, line in enumerate(lines, start=1):
        if drop_start <= i <= drop_end:
            continue
        kept.append(line)
    return "\n".join(kept)


def _prepare_task_zrtn_copy(
    zrtn_path: str,
    run_dir: Path,
    task_id: Optional[str],
    logger: logging.Logger,
) -> (str, str):
    src = Path(zrtn_path)
    if not src.exists():
        raise FileNotFoundError(f"zrietveldn file not found: {zrtn_path}")

    effective_task_id = str(task_id).strip() if task_id is not None and str(task_id).strip() else str(uuid.uuid4())
    dst = run_dir / f"{effective_task_id}.zrietveldn"

    # Keep a task-scoped snapshot so the model can rollback by selecting another task file.
    shutil.copy2(str(src), str(dst))
    logger.info("Prepared task zrietveldn copy: %s -> %s", src, dst)
    return str(dst), effective_task_id


def _resolve_fit_csvs(zrtn_path: str, refine_result: Dict[str, object]) -> List[Path]:
    csvs: List[Path] = []

    raw = refine_result.get("fit_data_csvs") if isinstance(refine_result, dict) else None
    if isinstance(raw, str) and raw.strip():
        for part in raw.split(";"):
            p = Path(part.strip())
            if p.exists() and p.suffix.lower() == ".csv":
                csvs.append(p)

    if csvs:
        return csvs

    zrtn_dir = Path(zrtn_path).resolve().parent
    return sorted(zrtn_dir.glob("*_fit.csv"))


def _resolve_bragg_csvs(csv_paths: List[Path], refine_result: Dict[str, object]) -> List[Path]:
    out: List[Path] = []
    raw = refine_result.get("bragg_csvs") if isinstance(refine_result, dict) else None
    if isinstance(raw, str) and raw.strip():
        for part in raw.split(";"):
            p = Path(part.strip())
            if p.exists() and p.suffix.lower() == ".csv":
                out.append(p)
        if out:
            return out

    for fit_csv in csv_paths:
        p = fit_csv.with_name(fit_csv.stem.replace("_fit", "_bragg") + ".csv")
        if p.exists():
            out.append(p)
    return out


def _load_bragg_positions(bragg_csv_path: Path, tofmin: float, tofmax: float) -> Dict[str, List[float]]:
    phase_to_positions: Dict[str, List[float]] = {}
    with bragg_csv_path.open("r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                phase_label = (row.get("phase_label") or "").strip() or "Phase"
                pos = float(row.get("position", ""))
            except Exception:
                continue
            if pos < tofmin or pos > tofmax:
                continue
            phase_to_positions.setdefault(phase_label, []).append(pos)
    return phase_to_positions


def _plot_saved_fit_data(
    zrtn_path: str,
    refine_result: Dict[str, object],
    logger: logging.Logger,
) -> Dict[str, object]:
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        logger.warning("Plot dependencies unavailable: %s", e)
        return {"fit_data_files": [], "bragg_data_files": [], "plot_files": []}

    csv_paths = _resolve_fit_csvs(zrtn_path, refine_result)
    if not csv_paths:
        logger.warning("No fit CSV files found for plotting.")
        return {"fit_data_files": [], "bragg_data_files": [], "plot_files": []}

    bragg_csv_paths = _resolve_bragg_csvs(csv_paths, refine_result)
    bragg_csv_by_stem: Dict[str, Path] = {}
    for p in bragg_csv_paths:
        stem = p.stem
        if stem.endswith("_bragg"):
            stem = stem[: -len("_bragg")]
        bragg_csv_by_stem[stem] = p

    rwp_percent = _parse_rwp_from_report(refine_result.get("report_path") if isinstance(refine_result, dict) else None)

    def _extract_tof_range_from_csv(csv_path: Path) -> tuple:
        """Extract tof_min and tof_max from CSV header comments.
        Returns (tof_min, tof_max) or (None, None) if not found or invalid.
        """
        try:
            with csv_path.open("r", encoding="utf-8", errors="ignore") as f:
                tof_min = None
                tof_max = None
                for _ in range(5):  # Read first few lines for metadata
                    line = f.readline()
                    if not line:
                        break
                    if line.startswith("# tof_min="):
                        try:
                            val = float(line.replace("# tof_min=", "").strip())
                            if not np.isnan(val):
                                tof_min = val
                        except Exception:
                            pass
                    elif line.startswith("# tof_max="):
                        try:
                            val = float(line.replace("# tof_max=", "").strip())
                            if not np.isnan(val):
                                tof_max = val
                        except Exception:
                            pass
                return tof_min, tof_max
        except Exception:
            pass
        return None, None

    fit_data_files: List[str] = []
    bragg_data_files: List[str] = []
    plot_files: List[str] = []

    for csv_path in csv_paths:
        try:
            # Extract TOF range from this CSV file's metadata
            fit_tof_min, fit_tof_max = _extract_tof_range_from_csv(csv_path)
            
            xs: List[float] = []
            y_obs: List[float] = []
            y_calc: List[float] = []
            y_bkg: List[float] = []

            with csv_path.open("r", encoding="utf-8", errors="ignore") as f:
                # Skip metadata/comment lines (e.g. '# tof_min=...') before CSV header.
                data_lines = dropwhile(lambda ln: ln.lstrip().startswith("#") or not ln.strip(), f)
                reader = csv.DictReader(data_lines)
                for row in reader:
                    try:
                        x = float(row.get("x", ""))
                        yo = float(row.get("y_obs", ""))
                        yc = float(row.get("y_calc", ""))
                        yb = float(row.get("y_bkg", "0"))
                    except Exception:
                        continue
                    xs.append(x)
                    y_obs.append(yo)
                    y_calc.append(yc)
                    y_bkg.append(yb)

            if not xs:
                continue

            x_data = np.asarray(xs, dtype=float)
            y_obs_arr = np.asarray(y_obs, dtype=float)
            y_calc_arr = np.asarray(y_calc, dtype=float)
            y_bkg_arr = np.asarray(y_bkg, dtype=float)
            diff = y_obs_arr - y_calc_arr

            data_tof_min = float(np.nanmin(x_data))
            data_tof_max = float(np.nanmax(x_data))
            plot_mask = (x_data >= data_tof_min) & (x_data <= data_tof_max)
            x_plot = x_data[plot_mask]
            y_obs_plot = y_obs_arr[plot_mask]
            y_calc_plot = y_calc_arr[plot_mask]
            y_bkg_plot = y_bkg_arr[plot_mask]
            diff_plot = diff[plot_mask]

            signal_min = float(np.nanmin([np.nanmin(y_obs_plot), np.nanmin(y_calc_plot), np.nanmin(y_bkg_plot)]))
            signal_max = float(np.nanmax([np.nanmax(y_obs_plot), np.nanmax(y_calc_plot), np.nanmax(y_bkg_plot)]))
            diff_min = float(np.nanmin(diff_plot))
            diff_max = float(np.nanmax(diff_plot))
            y_span = max(signal_max, diff_max) - min(signal_min, diff_min)
            margin = 0.08 * y_span if y_span > 0 else 1.0
            target_lower = diff_max + margin
            y_offset = max(0.0, target_lower - signal_min)

            y_obs_shifted = y_obs_plot + y_offset
            y_calc_shifted = y_calc_plot + y_offset
            y_bkg_shifted = y_bkg_plot + y_offset

            title_fs = 14
            label_fs = 12
            tick_fs = 11

            plt.figure(figsize=(10, 6))
            ax = plt.gca()
            ax.plot(x_plot, diff_plot, "k-", label="Difference (Obs-Calc)", linewidth=1)
            ax.plot(
                x_plot,
                y_obs_shifted,
                color="b",
                marker="x",
                linestyle="None",
                label="Observed",
                markersize=6,
                alpha=0.8,
            )
            ax.plot(x_plot, y_calc_shifted, "r-", label="Calculated", linewidth=3.6)
            ax.plot(x_plot, y_bkg_shifted, "g-", label="Background", linewidth=1.2)

            if fit_tof_min is not None:
                ax.axvline(x=fit_tof_min, color="m", linestyle="--", linewidth=1, label="Refine TOF Min")
            if fit_tof_max is not None:
                ax.axvline(x=fit_tof_max, color="c", linestyle="--", linewidth=1, label="Refine TOF Max")

            if rwp_percent is not None:
                ax.set_title(f"Neutron Refinement - Rwp: {rwp_percent:.2f}%", fontsize=title_fs)
            else:
                ax.set_title("Neutron Refinement", fontsize=title_fs)

            ax.set_xlabel("Time (μs)", fontsize=label_fs)
            ax.set_ylabel("Intensity", fontsize=label_fs)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=tick_fs)

            # Draw Bragg positions as short ticks near the bottom, per phase.
            try:
                base_stem = csv_path.stem
                if base_stem.endswith("_fit"):
                    base_stem = base_stem[: -len("_fit")]
                bragg_path = bragg_csv_by_stem.get(base_stem)
                if bragg_path and bragg_path.exists():
                    phase_to_positions = _load_bragg_positions(bragg_path, data_tof_min, data_tof_max)
                    if phase_to_positions:
                        from matplotlib.lines import Line2D

                        y0, y1 = ax.get_ylim()
                        yspan = y1 - y0 if (y1 - y0) != 0 else 1.0
                        start = y0 + 0.05 * yspan
                        tick_len = 0.02 * yspan
                        cmap = plt.cm.get_cmap("tab10")

                        bragg_handles = []
                        bragg_labels = []
                        for ip, (phase_name, positions) in enumerate(sorted(phase_to_positions.items(), key=lambda kv: kv[0])):
                            if not positions:
                                continue
                            col = cmap(ip % 10)
                            ax.vlines(positions, start, start + tick_len, color=col, linewidth=1.6, alpha=0.9)
                            handle = Line2D([0], [0], color=col, marker="|", linestyle="None", markersize=12, markeredgewidth=4)
                            bragg_handles.append(handle)
                            bragg_labels.append(f"Bragg peaks ({phase_name})")

                        if bragg_handles:
                            cur_handles, cur_labels = ax.get_legend_handles_labels()
                            cur_handles.extend(bragg_handles)
                            cur_labels.extend(bragg_labels)
                            ax.legend(
                                cur_handles,
                                cur_labels,
                                fontsize=9,
                                ncol=1,
                                loc="upper left",
                                bbox_to_anchor=(1.01, 1.0),
                                borderaxespad=0.0,
                                frameon=True,
                                framealpha=0.9,
                            )
                            bragg_data_files.append(str(bragg_path))
                        else:
                            ax.legend(
                                fontsize=9,
                                ncol=1,
                                loc="upper left",
                                bbox_to_anchor=(1.01, 1.0),
                                borderaxespad=0.0,
                                frameon=True,
                                framealpha=0.9,
                            )
                    else:
                        ax.legend(
                            fontsize=9,
                            ncol=1,
                            loc="upper left",
                            bbox_to_anchor=(1.01, 1.0),
                            borderaxespad=0.0,
                            frameon=True,
                            framealpha=0.9,
                        )
                else:
                    ax.legend(
                        fontsize=9,
                        ncol=1,
                        loc="upper left",
                        bbox_to_anchor=(1.01, 1.0),
                        borderaxespad=0.0,
                        frameon=True,
                        framealpha=0.9,
                    )
            except Exception:
                ax.legend(
                    fontsize=9,
                    ncol=1,
                    loc="upper left",
                    bbox_to_anchor=(1.01, 1.0),
                    borderaxespad=0.0,
                    frameon=True,
                    framealpha=0.9,
                )

            plt.tight_layout(pad=2.0)
            out_png = csv_path.with_suffix("").with_name(csv_path.stem + "_styled.png")
            plt.savefig(str(out_png), dpi=120, bbox_inches="tight")
            plt.close()

            fit_data_files.append(str(csv_path))
            plot_files.append(str(out_png))
            logger.info("Saved styled refinement plot: %s", out_png)
        except Exception as e:
            logger.warning("Failed plotting from %s: %s", csv_path, e)

    return {
        "fit_data_files": fit_data_files,
        "bragg_data_files": sorted(set(bragg_data_files)),
        "plot_files": plot_files,
    }


def zrt_generate_project(
    neutron_data: str,
    neutron_inst: str,
    cif_files: List[str],
    user_id: str = "default",
    mission_name: str = "default",
    task_id: Optional[str] = None,
) -> Dict[str, object]:
    """
    Generate a Z-Rietveld .zrietveldn project file in preparation for subsequent refinement.

    This tool generates a complete Z-Rietveld project from neutron powder diffraction data,
    instrument parameter files, and crystal structure files. The generated project file can
    be used for subsequent manual or automatic refinement.

    Args:
        neutron_data: Absolute path to the neutron powder diffraction data file.
                      Supported format: .histogramIgor (Z-Rietveld proprietary neutron
                      diffraction histogram format). The file must exist and contain valid
                      diffraction intensity data.

        neutron_inst: Absolute path to the Z-Rietveld instrument parameter file.
                      File format is .zDiffractometer (Z-Rietveld proprietary format).
                      Contains instrument geometry, detector parameters, and related information.

        cif_files: List of crystal structure files, each item as an absolute path string.
                   Supports standard CIF format (.cif), containing unit cell parameters,
                   space group, atomic positions, etc. Multiple CIF files can be passed
                   for multi-phase refinement.

        user_id: User identifier string, used for file management and logging.
                 Default value is "default".

        mission_name: Task name string identifying the current task. For refinement of one
                      dataset, the same mission_name should be used. Used to organize
                      generated project files and logs.

        task_id: Unique task identifier (optional).
                 When starting a new tool call, pass None or omit this parameter; the tool
                 will automatically generate a unique ID. If querying the result of an
                 existing task, the previous task_id must be passed.

    Returns:
        A dictionary containing the following keys:
        - 'ok': bool, indicates success (True) or failure (False).
        - 'zrietveldn': str, absolute path to the generated .zrietveldn project file (on success).
        - 'run_dir': str, task run directory storing logs and intermediate files.
        - 'error': str, error message (on failure).
        - 'traceback': str, complete exception stack trace (on failure, for debugging).

    Usage example:
        result = zrt_generate_project(
            neutron_data="/data/sample.xye",
            neutron_inst="/data/instrument.zDiffractometer",
            cif_files=["/data/structure1.cif", "/data/structure2.cif"],
            user_id="wanghao96",
            mission_name="LNO_refinement"
        )
        if result['ok']:
            zrtn_file = result['zrietveldn']
            print(f"Project generated successfully: {zrtn_file}")
        else:
            print(f"Project generation failed: {result['error']}")

    Notes:
        - Generation may take several seconds to tens of seconds depending on data volume
          and structural complexity.
        - If multiple CIF files point to the same structure, it will only be loaded once.
        - The generated .zrietveldn file can be loaded and refined by the zrt_manual_refine tool.
    """
    run_dir = TMP_DIR / user_id / mission_name
    logger = _setup_logging(run_dir)
    _ensure_env("/home/wangh/rongzai-agent/tools/pyZRietveld/Z-Rietveld/Z-Rietveld/utility_atomic_data", "/home/wangh/rongzai-agent/tools/pyZRietveld/Z-Rietveld/lib")

    logger.info("Generate project start")
    logger.info("data=%s", neutron_data)
    logger.info("inst=%s", neutron_inst)
    logger.info("cif_count=%d", len(cif_files or []))

    try:
        effective_task_id = str(task_id).strip() if task_id is not None and str(task_id).strip() else str(uuid.uuid4())
        dst = run_dir / f"{effective_task_id}.zrietveldn"

        zrt_path = refineTool.genZRTn(neutron_data, neutron_inst, cif_files, str(dst))
        out_path = Path(zrt_path)
        if not out_path.exists():
            raise FileNotFoundError(f"Generated zrietveldn file not found: {zrt_path}")

        logger.info("Generate project done: %s", out_path)
        return {
            "ok": True,
            "zrietveldn": str(out_path),
            "task_id": effective_task_id,
            "run_dir": str(run_dir),
        }
    except Exception as e:
        logger.error("Generate project failed: %s", e)
        logger.error(traceback.format_exc())
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "run_dir": str(run_dir),
        }


def zrt_manual_refine(
    zrtn_path: str,
    cfg: Optional[Dict[str, object]] = None,
    user_id: str = "default",
    mission_name: str = "default",
    task_id: Optional[str] = None,
) -> Dict[str, object]:
    """
    Execute manual refinement on an existing Z-Rietveld project file according to
    user-specified parameter configuration.

    This tool loads an already-generated .zrietveldn project file and refines neutron powder
    diffraction data according to the cfg-configured parameters. Supports flexible combinations
    of refinement parameters including scale factor, background, unit cell, instrument
    parameters, atomic coordinates, etc.

    Args:
        zrtn_path: Absolute path to an already-generated Z-Rietveld project file
                   (.zrietveldn format). The file must have been generated by the
                   zrt_generate_project tool or the Z-Rietveld GUI.

        cfg: Refinement configuration dictionary (optional, default None means no refinement
             is performed). All configuration values must be passed as strings.
             Supported configuration keys and their values are shown in the table below:

             | Parameter | Type | Default | Description |
             |-----------|------|---------|-------------|
             | `scaleVary` / `refine_scale` | str | "false" | Whether to refine the overall scale factor. Value is "true" or "false". |
             | `scaleInit` | str | (none) | Initial value of the scale factor, as a numeric string, e.g. "1.0". |
             | `varyBackground` / `refine_background` | str | "false" | Whether to refine the background. Value is "true" or "false". |
             | `bgOrder` / `background_coeffs` | str | "6" | Number of background polynomial terms, e.g. "3", "6". |
             | `bgInit` | str | "1.0,0,0,0,0,0" | Initial background values, comma-separated numeric list. |
             | `bgMode` | str | "legendre" | Background type, currently mainly supports "legendre". |
             | `refine_cell` / `varyLattice` | str | "false" | Whether to refine unit cell parameters. Automatically applies constraints based on space group (cubic/tetragonal/hexagonal/orthorhombic/monoclinic/triclinic). |
             | `refine_atoms` | str | (none) | Refinement control for specified atoms. Format: '{"Fe1":"XU","O2":"YZF","all":"U"}'. Supported flags: X/Y/Z (coordinates), U/UISO (thermal factor), F/OCC/OCCUPANCY (occupancy). The available atoms can be found in the document at 'report_path' returned after refinement. |
             | `space_group` | str | (none) | Modify space group, format: '{"phase_name":"203_2"}'. Phase name comes from CIF filename (without extension). |
             | `absorption` | str | (none) | Absorption correction configuration, format: '{"name":{"refine":true,"function":"cylindrical"}}'. name should be the internal diffraction spectrum label (usually `A`), not the prefixed `Histogram_A`. If the report shows `Histogram_A`, write `A` in absorption. The tool layer is compatible with both `A` and `Histogram_A`. Available function values: "cylindrical", "spherical", "plate", "flatplate", "lobanov1", "lobanov2". Default to "cylindrical"; if adjustment is needed, try "lobanov2" or "lobanov1" first; if further change is required, ask the user about the sample shape. |
             | `tof_min` | str | (none) | Lower TOF refinement limit (microseconds). Excludes the low-TOF region (dense Bragg peaks), e.g. "10000". |
             | `tof_max` | str | (none) | Upper TOF refinement limit (microseconds). Excludes the high-TOF noise region, e.g. "100000". |
             | `refine_profile` | str | (none) | Peak profile parameter refinement control (TOF neutron diffraction only). Available values: `level0` / `level1` / `level2`, mutually exclusive. Parameters opened at each level: `level0` opens σ₁², γ₁; `level1` opens σ₀², σ₂², γ₀, γ₂; `level2` simultaneously opens σ₀², σ₁², σ₂², γ₀, γ₁, γ₂ (all Gaussian/Lorentzian peak width parameters). α, β₀, β₁, r₀ remain fixed at any level. Recommended to enable only after completing scale/background/unit cell refinement, typically starting with `level0`. |

        user_id: User identifier string, used for file management and logging.
                 Default value is "default".

        mission_name: Task name string identifying the current task. For refinement of one
                      dataset, the same mission_name should be used. Used to organize
                      generated project files and logs.

        task_id: Unique task identifier (optional).
                 This parameter is automatically injected by the MCP wrapper layer for task
                 tracking. Usually does not need to be manually passed in business logic.

    Returns:
        A dictionary containing the following keys:
        - 'ok': bool, indicates success (True) or failure (False).
        - 'zrietveldn': str, the .zrietveldn path actually used and produced in this refinement (task_id named copy).
        - 'cfg': dict or str, the configuration parameters used (on success).
        - 'refine_result': dict, detailed result returned by the underlying pybind (on success).
        - 'report_text': str, condensed text extracted from the log at report_path (lines 36-109 removed, on success).
        - 'report_path': str, the path where the RunLog text was saved (on success).
        - 'plot_files': list, list of generated fit plot image paths (on success).
        - 'task_id': str, the task ID used in this refinement.
        - 'run_dir': str, task run directory storing logs and intermediate files.
        - 'error': str, error message (on failure).
        - 'traceback': str, complete exception stack trace (on failure, for debugging).

    Usage examples:
        # Refine scale and background
        result = zrt_manual_refine(
            zrtn_path="/output/project.zrietveldn",
            cfg={
                "refine_scale": "true",
                "refine_background": "true",
                "background_coeffs": "6"
            },
            user_id="wanghao96",
            mission_name="LNO_step1"
        )

        # Further refine unit cell and atomic positions
        result = zrt_manual_refine(
            zrtn_path="/output/project.zrietveldn",
            cfg={
                "refine_cell": "true",
                "refine_atoms": "{\"all\":\"XU\"}",
                "refine_cycle": "10"
            },
            user_id="wanghao96",
            mission_name="LNO_step2"
        )

    Notes:
        - All values in cfg must be strings. For boolean values, use "true" or "false".
        - If cfg is None or an empty dict, no refinement is performed; the project is only loaded.
        - Refinement typically takes 10 seconds to several minutes depending on iteration count and data volume.
        - Old parameter names (e.g. varyLattice) are generally compatible with new names (e.g. refine_cell).
        - The flag X in refine_atoms refers to the X coordinate of that atom (for historical reasons,
          actually equivalent to XYZ); F refers to occupancy.

    Fit plot element descriptions (images corresponding to plot_files):
        - Black solid line `Difference (Obs-Calc)`: Residual curve, equal to observed intensity minus
          calculated intensity; closer to 0 generally means better fit.
        - Blue crosses `Observed`: Experimental observed intensity points (vertically shifted upward
          for display alongside the residual).
        - Red solid line `Calculated`: Model calculated intensity curve (also shifted upward
          consistently with the observed curve).
        - Green solid line `Background`: Background intensity curve (also shifted upward).
        - Magenta dashed vertical line `Refine TOF Min`: Lower TOF limit used in this refinement
          (if present in input or data metadata).
        - Cyan dashed vertical line `Refine TOF Max`: Upper TOF limit used in this refinement
          (if present in input or data metadata).
        - Short vertical ticks at bottom `Bragg peaks (phase)`: Bragg peak positions for each phase,
          different colors for different phases.
        - `Rwp: xx.xx%` in title: Weighted profile R-factor (percentage), measuring goodness of fit;
          generally lower is better.
    """
    run_dir = TMP_DIR / user_id / mission_name
    logger = _setup_logging(run_dir)
    _ensure_env("/home/wangh/rongzai-agent/tools/pyZRietveld/Z-Rietveld/Z-Rietveld/utility_atomic_data", "/home/wangh/rongzai-agent/tools/pyZRietveld/Z-Rietveld/lib")
    cfg_str = _normalize_cfg(cfg)

    try:
        refine_zrtn_path, effective_task_id = _prepare_task_zrtn_copy(zrtn_path, run_dir, task_id, logger)
        result = _run_manual_refine(refine_zrtn_path, cfg_str, logger)
        plot_info = _plot_saved_fit_data(
            refine_zrtn_path,
            result if isinstance(result, dict) else {},
            logger,
        )
        if isinstance(result, dict):
            for k in ("bragg_csvs", "fit_data_csvs", "fit_status", "history_index"):
                result.pop(k, None)
            result["zrietveldn"] = refine_zrtn_path
            result["task_id"] = effective_task_id
            result["report_text"] = _build_compact_report(result.get("report_path"))
            result["plot_files"] = plot_info.get("plot_files", [])
        else:
            result = {
                "ok": False,
                "error": "Unexpected refine result type",
                "zrietveldn": refine_zrtn_path,
                "task_id": effective_task_id,
            }
        result["run_dir"] = str(run_dir)
        return result
    except Exception as e:
        logger.error("Manual refine failed: %s", e)
        logger.error(traceback.format_exc())
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "run_dir": str(run_dir),
        }



zrt_generate_runner = ProcessPollingMcpToolBase(
    timeout_sec=60.0,
    poll_interval=1.0,
    target_func=zrt_generate_project,
)

zrt_manual_runner = ProcessPollingMcpToolBase(
    timeout_sec=60.0,
    poll_interval=1.0,
    target_func=zrt_manual_refine,
)


zrt_generate_runner.register_mcp_tool(mcp, name="zrt_generate_project")
zrt_manual_runner.register_mcp_tool(mcp, name="zrt_manual_refine")


if __name__ == "__main__":
    mcp.run(transport="sse")

    # difPath = "/home/wangh/rongzai-agent/tools/pyZRietveld/testData/RUN0001094_groupBM.histogramIgor"
    # cifPath = "/home/wangh/rongzai-agent/tools/pyZRietveld/testData/LNO.cif"
    # instruPath = "/home/wangh/rongzai-agent/tools/pyZRietveld/testData/BM.zDiffractometer"

    # result = zrt_generate_project(difPath, instruPath, [cifPath], user_id="test_user", mission_name="test_mission")
    # print(result)

    # zrietveldnPath = "/home/wangh/rongzai-agent/userRecord/wanghao96/LNO_RUN0001094_BM/6862ac44-3adb-4427-a8af-730310406243.zrietveldn"
    
    # cfg0 = {
    # "refine_scale": "true",
    # "refine_background": "true",
    # "background_coeffs": "6",
    # "refine_cell": "true",
    # # "refine_atoms": '{"O1":"Z","all":"U"}',
    # # "absorption": '{"A":{"refine":"true","function":"cylindrical"}}'
    # }
    
    # cfg1 = {
    # "refine_scale": "true",
    # "refine_background": "true",
    # "background_coeffs": "6",
    # "refine_cell": "true",
    # "absorption": '{"A":{"refine":"true","function":"Lobanov2"}}',
    # "refine_cycle": "10"
    #     }
    # result1 = zrt_manual_refine(result['zrietveldn'], cfg0, user_id="test_user", mission_name="test_mission")
    
    # result2 = zrt_manual_refine(result1['zrietveldn'], cfg1, user_id="test_user", mission_name="test_mission")
    # print(result1)
    # print(result2)