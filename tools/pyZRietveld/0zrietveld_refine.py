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
    生成Z-Rietveld .zrietveldn项目文件，为后续精修做准备。
    
    此工具从中子粉末衍射数据、仪器参数文件和晶体结构文件生成完整的Z-Rietveld项目。
    生成的项目文件可用于后续的手动精修或自动精修。
    
    Args:
        neutron_data: 中子粉末衍射数据文件的绝对路径。
                      支持格式：.histogramIgor（Z-Rietveld专有的中子衍射直方图格式）。
                      文件必须存在，且包含有效的衍射强度数据。
        
        neutron_inst: Z-Rietveld仪器参数文件的绝对路径。
                      文件格式为 .zDiffractometer（Z-Rietveld专有格式）。
                      包含仪器几何、探测器参数等信息。
        
        cif_files: 晶体结构文件列表，每项为绝对路径的字符串。
                   支持标准CIF格式(.cif)，包含晶胞参数、空间群、原子位置等。
                   可传入多个CIF文件用于多相精修。
        
        user_id: 用户标识符，字符串，用于文件管理和日志追踪。
                 默认值为 "default"。
        
        mission_name: 任务名称，字符串，标识当前精修任务。
                      用于组织生成的项目文件和日志。
                      默认值为 "default"。

        task_id: 任务唯一标识（可选）。
             在开启一次工具调用时，这里写None或不传，工具会自动生成一个唯一ID；如果是对已有任务的结果查询，则必须传入之前的task_id
    
    Returns:
        字典，包含以下键值：
        - 'ok': bool，表示操作成功（True）或失败（False）。
        - 'zrietveldn': str，生成的.zrietveldn项目文件的绝对路径（成功时）。
        - 'run_dir': str，任务运行目录，存放日志和中间文件。
        - 'error': str，错误信息（失败时）。
        - 'traceback': str，完整的异常堆栈（失败时，用于调试）。
    
    使用示例:
        result = zrt_generate_project(
            neutron_data="/data/sample.xye",
            neutron_inst="/data/instrument.zDiffractometer",
            cif_files=["/data/structure1.cif", "/data/structure2.cif"],
            user_id="wanghao96",
            mission_name="LNO_refinement_v1"
        )
        if result['ok']:
            zrtn_file = result['zrietveldn']
            print(f"项目生成成功：{zrtn_file}")
        else:
            print(f"项目生成失败：{result['error']}")
    
    注意事项:
        - 生成过程可能耗时数秒到数十秒，取决于数据量和结构复杂性。
        - 若多个CIF文件指向同一结构，只会加载一次。
        - 生成的.zrietveldn文件可被 zrt_manual_refine 工具加载并精修。
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
    对现有Z-Rietveld项目文件执行手动精修，按用户指定的参数配置进行精修。
    
    此工具加载已生成的 .zrietveldn 项目文件，按 cfg 配置的参数精修中子粉末衍射数据。
    支持灵活的精修参数组合，包括标度因子、背景、晶胞、仪器参数、原子坐标等。
    
    Args:
        zrtn_path: 已生成的Z-Rietveld项目文件的绝对路径（.zrietveldn格式）。
                   文件必须由 zrt_generate_project 工具或Z-Rietveld GUI生成。
        
        cfg: 精修配置字典（可选，默认为None表示不进行任何精修）。
             所有配置值必须以字符串形式传入。
             支持的配置键及其值如下表所示：
             
             | 参数 | 类型 | 默认值 | 说明 |
             |------|------|--------|------|
             | `scaleVary` / `refine_scale` | str | "false" | 是否精修整体标度因子。值为"true"或"false"。 |
             | `scaleInit` | str | （无） | 标度因子初值，格式为数字字符串，如"1.0"。 |
             | `varyBackground` / `refine_background` | str | "false" | 是否精修背景。值为"true"或"false"。 |
             | `bgOrder` / `background_coeffs` | str | "6" | 背景多项式项数，如"3"、"6"。 |
             | `bgInit` | str | "1.0,0,0,0,0,0" | 背景初值，逗号分隔的数值列表。 |
             | `bgMode` | str | "legendre" | 背景类型，目前主要支持"legendre"。 |
             | `refine_cell` / `varyLattice` | str | "false" | 是否精修晶胞参数。根据空间群自动应用约束（立方/四方/六方/正交/单斜/三斜）。 |
             | `refine_atoms` | str | （无） | 针对指定原子的精修控制。格式：'{"Fe1":"XU","O2":"YZF","all":"U"}' 支持标记：X/Y/Z（坐标），U/UISO（热因子），F/OCC/OCCUPANCY（占有率）。 |
             | `space_group` | str | （无） | 修改空间群，格式：'{"phase_name":"203_2"}'。相名来自CIF文件名(去扩展名)。 |
             | `absorption` | str | （无） | 吸收修正配置，格式：'{"DataLabel":{"refine":true,"function":"spherical"}}'。function可选值："none"、"cylindrical"、"spherical"、"plate"、"flatplate"、"lobanov1"、"lobanov2"。 |
             | `tof_min` | str | （无） | 精修 TOF 下限（微秒）。排除低TOF区（Bragg峰密集区），如 "10000"。 |
             | `tof_max` | str | （无） | 精修 TOF 上限（微秒）。排除高TOF区噪声，如 "100000"。 |
             
             ⚠️ BM 谱仪等大数据量数据集，Bragg峰在低TOF区极为密集，**强烈建议**第一步精修设置 `tof_min` 跳过低TOF区，例如：
                cfg = {"refine_scale": "true", "refine_background": "true", "tof_min": "10000", "refine_cycle": "3"}
             
             典型配置示例:
                cfg = {
                    "refine_scale": "true",
                    "refine_background": "true",
                    "background_coeffs": "6",
                    "bgInit": "1.0,0.1,0,0,0,0",
                    "refine_cell": "true",
                    "refine_atoms": "{"all":"U"}"
                }
        
        user_id: 用户标识符，字符串，用于文件管理和日志追踪。
                 默认值为 "default"。
        
        mission_name: 任务名称，字符串，标识当前精修任务。
                      用于组织日志和输出文件。
                      默认值为 "default"。

        task_id: 任务唯一标识（可选）。
             该参数由 MCP 包装层自动注入，用于任务跟踪。
             业务逻辑中通常无需手动传入。
        
    
    Returns:
        字典，包含以下键值：
        - 'ok': bool，表示操作成功（True）或失败（False）。
        - 'zrietveldn': str，本次精修实际使用并产出的 .zrietveldn 路径（task_id命名副本）。
        - 'cfg': dict或str，使用的配置参数（成功时）。
        - 'refine_result': dict，底层 pybind 返回的详细结果（成功时）。
        - 'report_text': str，从 report_path 对应日志中提取的精简文本（已删除第36-109行，成功时）。
        - 'report_path': str，RunLog 文本落盘路径（成功时）。
        - 'task_id': str，本次精修使用的任务ID。
        - 'run_dir': str，任务运行目录，存放日志和中间文件。
        - 'error': str，错误信息（失败时）。
        - 'traceback': str，完整的异常堆栈（失败时，用于调试）。
    
    使用示例:
        # 精修scale和背景
        result = zrt_manual_refine(
            zrtn_path="/output/project.zrietveldn",
            cfg={
                "refine_scale": "true",
                "refine_background": "true",
                "background_coeffs": "6",
                "refine_cycle": "5"
            },
            user_id="wanghao96",
            mission_name="LNO_step1"
        )
        
        # 进一步精修晶胞和原子位置
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
    
    注意事项:
        - cfg中的所有值都必须是字符串。若要传入布尔值，请使用"true"或"false"。
        - 若cfg为None或为空字典，则不会进行任何精修，仅加载项目。
        - 精修过程通常耗时10秒到数分钟，取决于迭代次数和数据量。
        - 旧参数名（如varyLattice）与新参数名（如refine_cell）通常兼容。
        - refine_atoms中的标记X表示该原子的X坐标（历史原因，实际等价XYZ）；F表示占有率。
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
    timeout_sec=1200.0,
    poll_interval=1.0,
    target_func=zrt_generate_project,
)

zrt_manual_runner = ProcessPollingMcpToolBase(
    timeout_sec=1200.0,
    poll_interval=1.0,
    target_func=zrt_manual_refine,
)


zrt_generate_runner.register_mcp_tool(mcp, name="zrt_generate_project")
zrt_manual_runner.register_mcp_tool(mcp, name="zrt_manual_refine")


if __name__ == "__main__":
    mcp.run(transport="sse")

    # difPath = "/home/wangh/rongzai-agent/tools/pyZRietveld/testData/RUN0000410_groupMA.histogramIgor"
    # cifPath = "/home/wangh/rongzai-agent/tools/pyZRietveld/testData/run410-zeolyte.cif"
    # instruPath = "/home/wangh/rongzai-agent/tools/pyZRietveld/testData/MA.zDiffractometer"

    # result = zrt_generate_project(difPath, instruPath, [cifPath], user_id="test_user", mission_name="test_mission")
    # print(result)
    # cfg1 = {
    # "scaleVary": "true",
    # "varyBackground": "true"
    #     }
    # result = zrt_manual_refine(result["zrietveldn"], cfg1, user_id="test_user", mission_name="test_mission")
    # print(result)