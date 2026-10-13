"""Mission-scoped orchestration for the magnetic structure capability.

This module is the layer between the MCP tool surface and the scientific engine
(`workflow`, `native`, `science`, and the two headless wrappers around GSAS-II's
k-vector search and Bilbao's k-SUBGROUPSMAG). It follows the conventions every
other rongzai tool already uses:

* one mission lives in ``WORKDIR/<user_id>/<session_id>/<mission_name>/``;
* tools accept ``user_id``/``session_id``/``mission_name``/``task_id``;
* each stage prints Chinese progress lines (the frontend streams them) and
  returns absolute paths, including PNG figures rendered by :mod:`present`;
* a stage reads the evidence the previous stage persisted, so a mission can be
  resumed and every step stays inspectable.

The workflow mirrors the experimental procedure a spectrometer scientist uses:
high-temperature nuclear refinement, low-temperature nuclear refinement with a
propagation-vector search on the peaks the parent cell cannot explain, Bilbao
magnetic subgroups for that propagation vector, symmetry-constrained magnetic
Rietveld comparison, and a single exported magnetic structure.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

WORKDIR_ENV = "WORKDIR"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_@.+-]+")

# Open-shell candidates a magnetic search may consider; chemistry only, never a
# material-specific prior.
MAGNETIC_ELEMENTS = {
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Y", "Zr", "Nb", "Mo",
    "Tc", "Ru", "Rh", "Pd", "Ag", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt",
    "Au", "Th", "U", "Np", "Pu", "C", "N", "O",
}


class MissionError(ValueError):
    """The mission could not be set up or advanced as requested."""


def workdir() -> Path:
    """Root that holds every account's agent workspace."""
    value = os.getenv(WORKDIR_ENV)
    if not value:
        raise MissionError(f"{WORKDIR_ENV} is required for magnetic analysis")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise MissionError(f"{WORKDIR_ENV} does not exist: {root}")
    return root


def _safe_identifier(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text) or text in (".", ".."):
        raise MissionError(f"Invalid {label}: {value!r}")
    return text


def mission_dir(user_id: str, session_id: str, mission_name: str, *, create: bool = True) -> Path:
    """Resolve ``WORKDIR/<user>/<session>/<mission>`` and refuse escapes."""
    root = workdir()
    mission = (root / _safe_identifier(user_id, "user_id")
               / _safe_identifier(session_id, "session_id")
               / _safe_identifier(mission_name, "mission_name")).resolve()
    if not mission.is_relative_to(root):
        raise MissionError("Mission directory escapes the agent workspace")
    if create:
        mission.mkdir(parents=True, exist_ok=True)
    return mission


def mission_paths(user_id: str, session_id: str, mission_name: str) -> dict[str, Path]:
    """Standard directories of one mission."""
    mission = mission_dir(user_id, session_id, mission_name)
    return {
        "mission": mission,
        "inputs": mission / "input",
        "analysis": mission / "analysis",
        "figures": mission / "figures",
        # Mission-local copy of the deliverable, and the session-level directory a
        # task asks the agent to write into (``<session>/submission/``).
        "submission": mission / "submission",
        "session_submission": mission.parent / "submission",
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _figure_paths_relative(paths: dict[str, Path], values: Iterable[str]) -> list[str]:
    out = []
    for value in values:
        if not value:
            continue
        try:
            out.append(str(Path(value).relative_to(paths["mission"])))
        except ValueError:
            out.append(str(value))
    return out


def _bank_files(*values: Any) -> list[Path]:
    """Expand a directory, a glob, a comma list or explicit paths into files."""
    files: list[Path] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            files.extend(_bank_files(*value))
            continue
        for chunk in str(value).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            path = Path(chunk).expanduser()
            if path.is_dir():
                files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
            elif "*" in chunk or "?" in chunk:
                files.extend(sorted(Path(p) for p in Path(path.parent).glob(path.name)))
            elif path.is_file():
                files.append(path)
            else:
                raise MissionError(f"Input path does not exist: {chunk}")
    return files


def _pair_suffix(path: Path, temperature: str) -> str:
    """Return the bank token of a spectrum or instrument file.

    Tolerates the spellings this group's files actually use: a leading run number
    (``RUN0002218``), a ``group`` infix, and the temperature words in English or
    Chinese (``high``/``low``, ``高温``/``低温``/``顺磁``).
    """
    text = path.stem
    text = re.sub(r"(?i)^run[_\-\s]*\d+", "", text)
    text = re.sub(r"(?i)(?:high|low|ht|lt|paramagnetic|高温|低温|顺磁|有磁峰|无磁峰)", "", text)
    text = re.sub(r"(?i)group", "", text)
    # Instrument files routinely carry an acquisition year or version the spectra do
    # not (BS_2025.instprm vs RUN0002218_groupBS.dat), so drop both before comparing.
    text = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", "", text)
    text = re.sub(r"(?i)[_\-\s]*v(?:er)?\d+$", "", text)
    text = re.sub(r"[^0-9A-Za-z]+", "", text)
    return text.lower() or path.stem.lower()


def stage_inputs(paths: dict[str, Path], initial_cif: str | Path, high_temperature: Any,
                 low_temperature: Any, instruments: Any) -> dict[str, Any]:
    """Copy the supplied data into the layout the engine's input discovery expects.

    Accepts directories, globs, comma lists or explicit file paths. Banks are paired
    by their filename token so a mission never silently mixes two measurements.
    """
    inputs = paths["inputs"]
    (inputs / "high_temperature").mkdir(parents=True, exist_ok=True)
    (inputs / "low_temperature").mkdir(parents=True, exist_ok=True)
    (inputs / "instruments").mkdir(parents=True, exist_ok=True)

    cif = Path(str(initial_cif)).expanduser()
    if not cif.is_file():
        raise MissionError(f"Initial structure does not exist: {initial_cif}")
    shutil.copyfile(cif, inputs / "initial.cif")

    high_files = _bank_files(high_temperature)
    low_files = _bank_files(low_temperature)
    instrument_files = _bank_files(instruments)
    if not high_files or not low_files or not instrument_files:
        raise MissionError("High, low and instrument inputs are all required")

    def index(files: list[Path], temperature: str) -> dict[str, Path]:
        table: dict[str, Path] = {}
        for path in files:
            key = _pair_suffix(path, temperature)
            if key in table:
                raise MissionError(f"Two {temperature} banks share the token {key!r}: "
                                   f"{table[key].name} and {path.name}")
            table[key] = path
        return table

    def refine(table: dict[str, Path]) -> dict[str, Path]:
        """Collapse a version/year suffix onto the bank letters when that is unambiguous.

        ``bs2025`` and ``bs`` name the same detector bank, but collapsing is only safe
        while no two files would meet: ``bank1``/``bank2`` both reduce to ``bank``, so
        such a set keeps its literal tokens.
        """
        collapsed = {}
        for token in table:
            match = re.match(r"[a-z]+", token)
            collapsed[token] = match.group(0) if match else token
        if len(set(collapsed.values())) != len(table):
            return table
        return {collapsed[token]: path for token, path in table.items()}

    high = refine(index(high_files, "high"))
    low = refine(index(low_files, "low"))
    instrument = refine(index(instrument_files, "instrument"))
    shared = sorted(set(high) & set(low) & set(instrument))
    if not shared:
        raise MissionError(
            "No bank token appears in all three inputs, so banks cannot be paired "
            "safely. Extracted tokens: high=%s, low=%s, instruments=%s. Name each "
            "spectrum and instrument file so they share a bank token (for example "
            "RUN..._groupBS.dat with BS.instprm)." % (
                sorted(high) or [f.name for f in high_files],
                sorted(low) or [f.name for f in low_files],
                sorted(instrument) or [f.name for f in instrument_files]))
    copied = {"banks": shared, "high": {}, "low": {}, "instruments": {}}
    for token in shared:
        for kind, source, folder in (
                ("high", high[token], "high_temperature"),
                ("low", low[token], "low_temperature"),
                ("instruments", instrument[token], "instruments")):
            target = inputs / folder / f"{token}{source.suffix}"
            shutil.copyfile(source, target)
            copied[kind][token] = str(target)
    copied["unpaired"] = {
        "high": sorted(set(high) - set(shared)),
        "low": sorted(set(low) - set(shared)),
        "instruments": sorted(set(instrument) - set(shared)),
    }
    return copied


def magnetic_elements_from_cif(cif_path: Path, requested: Sequence[str] | None = None) -> list[str]:
    """Elements a magnetic search may consider, from chemistry alone."""
    if requested:
        return [str(element).strip() for element in requested if str(element).strip()]
    text = cif_path.read_text(encoding="utf-8", errors="replace")
    symbols = {match.group(1) for match in re.finditer(r"\b([A-Z][a-z]?)\b", text)}
    return sorted(symbols & MAGNETIC_ELEMENTS)


def _module():
    from . import workflow as w

    return w


_PROGRESS: Path | None = None


def _set_progress(paths: dict[str, Path], stage: str) -> None:
    """Point stage narration at a log inside the mission directory.

    The MCP polling base returns only a final result, so a stage that takes minutes
    looks silent in the UI apart from the repeated poll. Each stage appends its step
    lines to ``analysis/progress.log`` — inside the workspace tree the agent already
    owns — so the agent can read real progress while it waits and narrate it to the
    user instead of only saying "still running".
    """
    global _PROGRESS
    analysis = paths["analysis"]
    analysis.mkdir(parents=True, exist_ok=True)
    _PROGRESS = analysis / "progress.log"
    _steps(f"=== 阶段: {stage} ===")


def _steps(*lines: str) -> None:
    """Emit one progress line: to the service log, and to the mission progress log."""
    global _PROGRESS
    stamp = time.strftime("%H:%M:%S")
    for line in lines:
        text = f"[{stamp}] {line}"
        print(text, flush=True)
        if _PROGRESS is None:
            continue
        try:
            with _PROGRESS.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass


def _elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 3)


# --------------------------------------------------------------------------- #
# Stage 1: inputs, paramagnetic nuclear structure, low-temperature control
# --------------------------------------------------------------------------- #
def initialize(user_id: str, session_id: str, mission_name: str, task_id: str,
               initial_cif: str, high_temperature: Any, low_temperature: Any,
               instruments: Any, magnetic_elements: Sequence[str] | None = None,
               cycles: int = 24, background_coeffs: int = 6,
               profile_comparison: bool = False) -> dict[str, Any]:
    """Prepare the mission and refine the high-temperature nuclear structure.

    ``profile_comparison`` additionally weighs an alternative low-temperature profile
    model by BIC. It is off by default: it costs minutes, and the magnetic refinement
    stage repeats that comparison where it actually matters.
    """
    start = time.perf_counter()
    paths = mission_paths(user_id, session_id, mission_name)
    analysis, figures = paths["analysis"], paths["figures"]
    _set_progress(paths, "initialize")
    analysis.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"status": "failure", "task_id": task_id, "mission": str(paths["mission"])}
    try:
        _steps(f"步骤 1/6: 校验并整理输入数据（mission={mission_name}）")
        staged = stage_inputs(paths, initial_cif, high_temperature, low_temperature, instruments)
        elements = magnetic_elements_from_cif(paths["inputs"] / "initial.cif", magnetic_elements)
        _steps(f"  ✓ 配对成功 bank={staged['banks']}", f"  ✓ 候选磁性元素={elements}")

        w = _module()
        _steps("步骤 2/6: 检查谱段、峰位与母相结构（inspect）")
        inspection = w.inspect_spectra(str(paths["inputs"]), str(analysis))
        _steps(f"  ✓ {inspection['bank_count']} 个 bank、{inspection['peak_count']} 个峰")

        _steps("步骤 3/6: 绘制输入数据总览图")
        overview = present_data_overview(paths, figures, staged)

        _steps("步骤 4/6: 精修顺磁相（高温）核结构作为基线")
        baseline = w.refine_nuclear_baseline(
            str(paths["inputs"]), str(analysis), cycles=cycles,
            background_coeffs=background_coeffs, refine_profile=profile_comparison)
        _steps("步骤 5/6: 绘制高温/低温核拟合图")
        high_figure = present_fit(paths, figures, baseline["high_gpx_path"],
                                  "high_temperature_nuclear_fit.png",
                                  "Paramagnetic (high-T) nuclear refinement")
        low_figure = present_fit(paths, figures, baseline["low_gpx_path"],
                                 "low_temperature_nuclear_fit.png",
                                 "Low-T nuclear-only control")
        report = _read_json(Path(baseline["report_path"]))
        _steps("步骤 6/6: 汇总结果")
        high_quality = report["high_stages"][-1]
        result.update(
            status="completed",
            magnetic_elements=elements,
            banks=staged["banks"],
            unpaired_banks=staged["unpaired"],
            input_dir=str(paths["inputs"]),
            analysis_dir=str(analysis),
            high_gpx_path=baseline["high_gpx_path"],
            low_gpx_path=baseline["low_gpx_path"],
            baseline_report_path=baseline["report_path"],
            overview_figure=overview,
            high_fit_figure=high_figure,
            low_fit_figure=low_figure,
            progress_log=str(paths["analysis"] / "progress.log"),
            high_reduced_chisq=high_quality["reduced_chisq"],
            low_nuclear_reduced_chisq=report["low_nuclear_control"]["reduced_chisq"],
            summary=(f"顺磁相核结构已精修（reduced χ²={high_quality['reduced_chisq']:.2f}）；"
                     f"低温相纯核模型 reduced χ²={report['low_nuclear_control']['reduced_chisq']:.2f}，"
                     f"两者差异就是磁散射要找的对象。"),
        )
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        import traceback
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        _steps(f"  ✗ 初始化失败: {result['error']}")
    result["execution_time_sec"] = _elapsed(start)
    return result


# --------------------------------------------------------------------------- #
# Stage 6: deepen the selected model, after the comparison has chosen it
# --------------------------------------------------------------------------- #
def deepen(user_id: str, session_id: str, mission_name: str, task_id: str,
           model: str | None = None, cycles: int = 24, deep_cycles: int | None = None,
           deep_rounds: int = 3, time_limit_seconds: float = 1800.0,
           native_solver: str = "analytic Jacobian") -> dict[str, Any]:
    """Refine the selected model harder: cell, ADPs, profile and more cycles.

    The comparison stage treats every candidate identically so its BIC ranking is fair.
    Only *after* a model is selected may it be refined further — so this stage labels its
    metrics as post-comparison, keeps the comparison report, and restores the
    comparison-stage MCIF when the deeper fit turns out worse.
    """
    start = time.perf_counter()
    paths = mission_paths(user_id, session_id, mission_name)
    analysis, figures = paths["analysis"], paths["figures"]
    _set_progress(paths, "deepen")
    result: dict[str, Any] = {"status": "failure", "task_id": task_id,
                              "progress_log": str(analysis / "progress.log")}
    try:
        from . import present
        from .native import refine_native

        if not (analysis / "refinement_report.json").is_file():
            raise MissionError("Run the magnetic refinement stage first")
        comparison = _read_json(analysis / "refinement_report.json")
        converged = [m for m in comparison.get("models", []) if m.get("status") == "ok"]
        if not converged:
            raise MissionError("The magnetic refinement stage produced no converged model")
        winner = str(model or comparison.get("best_model") or converged[0]["id"])
        if winner not in {str(m["id"]) for m in converged}:
            raise MissionError(f"{winner!r} is not a converged candidate; choose one of "
                               f"{sorted(str(m['id']) for m in converged)}")
        # Deepening is handed to the gsas2-refine service. Its step-by-step,
        # rollback-capable workflow (one parameter group at a time) is the right way to
        # push a single model further; releasing every group at once — which this engine
        # used to do — drives the solver into nonfinite intensities (measured).
        winner_record = next(m for m in converged if str(m["id"]) == winner)
        source_gpx = Path(str(winner_record.get("gpx_path") or ""))
        if not source_gpx.is_file():
            raise MissionError("The selected model has no saved GSAS-II project to hand off")
        _steps(f"步骤 1/2: 把选定模型 {winner} 交接为可继续精修的 GSAS-II 项目")
        handoff = analysis / "selected_model.gpx"
        shutil.copyfile(source_gpx, handoff)
        note = analysis / "selected_model.md"
        quality = winner_record.get("comparison_quality") or winner_record.get("quality") or {}
        banks = "；".join(f"{h.get('name')} Rwp={100 * float(h.get('rwp') or 0):.2f}%"
                          for h in (quality.get("histograms") or [])[:8])
        note.write_text(
            "# 交接给 gsas2-refine 的磁结构精修\n\n"
            f"- 选定模型：`{winner}`\n"
            f"- 项目文件：`{handoff.name}`（含核相与磁相；磁空间群约束已烘进相里，"
            "GSAS-II 会自动施加，**不需要手工加约束**）\n"
            f"- 磁相名：`{winner}`；比较阶段 reduced χ²={quality.get('reduced_chisq')}\n"
            f"- 各 bank：{banks or '（未记录）'}\n"
            f"- 磁性位点：{json.dumps(winner_record.get('magnetic_sites') or [], ensure_ascii=False)[:400]}\n\n"
            "## 建议的逐步精修顺序（一次只放开一组，每步看图并确认）\n"
            "1. 只放开磁矩分量：`refine_config={\"refine_moments\": [{\"phase\": \"<磁相名>\", "
            "\"atom\": \"<磁性原子标签>\", \"components\": \"sym\"}]}`"
            "（**注意键名是 `components`**；`components` 取 `\"sym\"` 表示只放开磁空间群允许的分量，"
            "也可显式写 `\"x\"`/`\"xyz\"`；也支持扁平写法 `{\"<磁相名>:<原子>\": \"x\"}`）；\n"
            "2. 再放开核相晶胞 → 确认峰位仍对齐；\n"
            "3. 再依次放开原子位移、峰型；每步若 χ² 上升或差值谱变差就回退。\n\n"
            "## 完成后\n"
            "把精修好的项目交回磁性 MCP 的 `ExportMagneticStructure`（传 `gpx_path`），"
            "由它写出带磁矩信息的最终 MCIF。\n", encoding="utf-8")
        _steps(f"步骤 2/2: 已写出交接项目与说明（{handoff.name}、{note.name}）")
        result.update(status="completed", model=winner, handoff_gpx=str(handoff),
                      handoff_note=str(note), improved=None,
                      before_reduced_chisq=quality.get("reduced_chisq"),
                      mcif_path=str(analysis / f"{winner}.mcif"),
                      comparison_report_path=str(analysis / "comparison_report.json"),
                      summary=(f"已把选定模型 {winner} 交接为 `selected_model.gpx`（含磁相与磁约束）。"
                               "请用 gsas2-refine 的 MCP 工具逐步精修（一次只放开一组），"
                               "完成后用 `ExportMagneticStructure(gpx_path=...)` 导出最终 MCIF。"))
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        import traceback
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        _steps(f"  ✗ 深修失败: {result['error']}")
    result["execution_time_sec"] = _elapsed(start)
    return result


def present_data_overview(paths: dict[str, Path], figures: Path, staged: dict[str, Any]) -> str:
    """Input overview figure: high and low spectra of every paired bank."""
    from . import present

    records = []
    for token in staged["banks"]:
        record: dict[str, Any] = {"bank": token}
        for kind, key in (("high", "high"), ("low", "low")):
            path = Path(staged[key][token])
            data = _load_columns(path)
            if data is not None:
                record[kind] = data
        records.append(record)
    return present.data_overview_figure(records, figures / "input_overview.png")


def _load_columns(path: Path) -> tuple[list[float], list[float]] | None:
    """Read TOF/intensity columns, skipping the textual header rows some banks carry."""
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            rows.append((float(fields[0]), float(fields[1])))
        except ValueError:
            continue
    if not rows:
        return None
    return [row[0] for row in rows], [row[1] for row in rows]


def present_fit(paths: dict[str, Path], figures: Path, gpx_path: str, name: str,
                title: str, control_gpx: str | None = None) -> str:
    """Fit figure for one refined GSAS-II project."""
    from . import present

    return present.nuclear_fit_figure(gpx_path, figures / name, title, control_gpx)


# --------------------------------------------------------------------------- #
# Stage 2: propagation vector from the peaks the parent cell cannot explain
# --------------------------------------------------------------------------- #
def propagation(user_id: str, session_id: str, mission_name: str, task_id: str,
                k_vectors: Sequence[Sequence[float]] | None = None,
                max_index: int = 8, indexing_tolerance: float = 0.01,
                search_option: int = 0) -> dict[str, Any]:
    """Search candidate propagation vectors with GSAS-II's own k-vector search."""
    start = time.perf_counter()
    paths = mission_paths(user_id, session_id, mission_name)
    analysis, figures = paths["analysis"], paths["figures"]
    _set_progress(paths, "propagation")
    result: dict[str, Any] = {"status": "failure", "task_id": task_id}
    try:
        w = _module()
        _steps("步骤 1/4: 找出母相晶胞无法解释的峰（低温核拟合的强度/峰位残差）")
        search = w.search_propagation(
            str(paths["inputs"]), str(analysis), k_vectors=k_vectors,
            max_index=max_index, indexing_tolerance=indexing_tolerance,
            search_option=search_option)
        report = _read_json(Path(search["report_path"]))
        peaks = report.get("propagation_peaks", [])
        _steps(f"  ✓ 证据峰 {len(peaks)} 个，k 候选 {len(search['k_candidates'])} 个")

        _steps("步骤 2/4: 用 GSAS-II k-vector search 复核候选传播矢")
        candidates = _run_k_vector_search(paths, report, search_option)
        _steps(f"  ✓ k-vector search 返回 {len(candidates)} 个候选: "
               + ", ".join(str(c.get("k")) for c in candidates[:5]))

        _steps("步骤 3/4: 绘制传播矢证据图")
        figure = present_propagation(paths, peaks, candidates or search["k_candidates"])
        _steps("步骤 4/4: 汇总")
        chosen = candidates[0]["k"] if candidates else (
            search["k_candidates"][0]["k"] if search["k_candidates"] else None)
        result.update(
            status="completed",
            report_path=search["report_path"],
            k_candidates=candidates or search["k_candidates"],
            chosen_k=chosen,
            evidence_peaks=peaks,
            propagation_figure=figure,
            diagnostics=search.get("diagnostics"),
            summary=(f"k 候选 {len(candidates or search['k_candidates'])} 个；"
                     f"证据峰 {len(peaks)} 个。k=000 表示磁胞与晶胞同大（不会出现额外峰），"
                     f"磁散射只改变已有峰的强度。"),
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        _steps(f"  ✗ 传播矢搜索失败: {result['error']}")
    result["execution_time_sec"] = _elapsed(start)
    return result


def _run_k_vector_search(paths: dict[str, Path], report: dict[str, Any],
                         option: int) -> list[dict[str, Any]]:
    """GSAS-II k-vector search over the d-spacings the parent cell left unindexed."""
    import sys

    tokens = _search_tokens(report)
    if not tokens:
        return []
    root = Path(__file__).resolve().parents[1]
    wrapper_dir = root / "k_vector_candidate_search"
    if str(wrapper_dir) not in sys.path:
        sys.path.insert(0, str(wrapper_dir))
    from k_vector_candidate_search import search_candidate_k_vectors

    raw = search_candidate_k_vectors(
        cif_path=paths["inputs"] / "initial.cif",
        satellite_d_spacings=tokens,
        option=option,
    )
    # The wrapper returns component fields (kx/ky/kz); normalise to one list form so
    # every consumer sees the same k shape as the engine's own search stage.
    candidates = []
    for item in raw:
        if item.get("k") is not None:
            vector = [float(value) for value in item["k"]]
        else:
            vector = [float(item.get("kx", 0.0)), float(item.get("ky", 0.0)),
                      float(item.get("kz", 0.0))]
        candidates.append({
            "k": vector,
            "indicator_distance": item.get("indicator_distance"),
            "average_delta_d": item.get("average_delta_d"),
            "max_delta_d": item.get("max_delta_d"),
            "setting": item.get("setting"),
            "source": "GSAS-II k-vector search over unindexed peaks",
        })
    return candidates


def _search_tokens(report: dict[str, Any]) -> list[float]:
    """d-spacings of the peaks the parent cell could not explain."""
    values: list[float] = []
    for peak in report.get("propagation_peaks", []):
        for key in ("d", "d_spacing", "d_obs", "d_observed"):
            if peak.get(key) is not None:
                try:
                    value = float(peak[key])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value > 0:
                    values.append(value)
                break
    return values


def present_propagation(paths: dict[str, Path], peaks: Sequence[dict[str, Any]],
                        candidates: Sequence[dict[str, Any]]) -> str:
    """Evidence figure for the propagation-vector search."""
    from . import present

    return present.propagation_figure(peaks, candidates,
                                      paths["figures"] / "propagation_vector.png")


# --------------------------------------------------------------------------- #
# Stage 3: Bilbao k-SUBGROUPSMAG candidates built as GSAS-II magnetic phases
# --------------------------------------------------------------------------- #
def candidates(user_id: str, session_id: str, mission_name: str, task_id: str,
               magnetic_elements: Sequence[str] | None = None,
               k_vectors: Sequence[Sequence[float]] | None = None,
               max_candidates: int = 256,
               candidate_scope: str = "magnetic_site") -> dict[str, Any]:
    """Enumerate magnetic subgroups for the chosen propagation vector.

    ``candidate_scope`` decides which candidate settings are asked for, and it dominates
    both the physics and the cost of the comparison:

    * ``"magnetic_site"`` (default) keeps only settings that leave the magnetic ion on
      its parent Wyckoff orbit — the condition the manual workflow applies when it
      pre-selects that site (131 -> 14 candidates on the reference case);
    * ``"all_subgroups"`` asks Bilbao for every k-subgroup, which is the right scope
      when a non-magnetic site or a change of orbit may be involved.
    """
    start = time.perf_counter()
    paths = mission_paths(user_id, session_id, mission_name)
    analysis, figures = paths["analysis"], paths["figures"]
    _set_progress(paths, "candidates")
    result: dict[str, Any] = {"status": "failure", "task_id": task_id}
    try:
        from .native import enumerate_native

        elements = list(magnetic_elements or [])
        if not elements:
            elements = magnetic_elements_from_cif(paths["inputs"] / "initial.cif")
        if k_vectors is None:
            k_vectors = _chosen_k(paths, analysis)
        _steps(f"步骤 1/3: 向 Bilbao k-SUBGROUPSMAG 查询磁子群（k={k_vectors}，"
               f"磁性元素={elements}）")
        if candidate_scope not in ("magnetic_site", "all_subgroups"):
            raise MissionError("candidate_scope must be 'magnetic_site' or 'all_subgroups'")
        narrow = candidate_scope == "magnetic_site"
        try:
            enumerated = enumerate_native(str(paths["inputs"]), str(analysis),
                                          magnetic_elements=elements, k_vectors=k_vectors,
                                          max_candidates=max_candidates,
                                          exact_wyckoff_orbit=narrow)
        except (ValueError, ImportError) as exc:
            if not narrow:
                raise
            _steps(f"  ⚠ 精确 Wyckoff 轨道筛选不可用（{type(exc).__name__}: {exc}），回退到全量子群")
            candidate_scope = "all_subgroups"
            enumerated = enumerate_native(str(paths["inputs"]), str(analysis),
                                          magnetic_elements=elements, k_vectors=k_vectors,
                                          max_candidates=max_candidates)
        report = _read_json(Path(enumerated["report_path"]))
        modes = report.get("modes", [])
        _steps(f"  ✓ 得到 {len(modes)} 个磁结构候选（含磁胞变换、原点与磁矩约束）")

        _steps("步骤 2/3: 绘制候选概览图")
        figure = present_candidates(paths, modes)
        _steps("步骤 3/3: 汇总候选表")
        table = []
        for mode in modes[:80]:
            site = (mode.get("magnetic_sites") or [{}])[0]
            table.append({
                "id": mode.get("id"),
                "magnetic_space_group": mode.get("magnetic_space_group"),
                "bns_number": mode.get("bns_number"),
                "free_moment_components": _free_components(mode),
                "allowed_moment_direction_parent_basis":
                    site.get("parent_basis_constraint_relations"),
                "magnetic_site_symmetry": site.get("magnetic_site_symmetry"),
                "phase_file": mode.get("phase_path"),
            })
        result.update(
            status="completed",
            candidate_scope=candidate_scope,
            report_path=enumerated["report_path"],
            candidate_count=len(modes),
            candidates=table,
            candidates_figure=figure,
            summary=(f"共 {len(modes)} 个磁空间群候选。下一步对候选做磁 Rietveld 比较，"
                     f"保留磁空间群自身的磁矩约束（例如 Mx=My、Mz=0）。"),
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        _steps(f"  ✗ 候选枚举失败: {result['error']}")
    result["execution_time_sec"] = _elapsed(start)
    return result


def _chosen_k(paths: dict[str, Path], analysis: Path) -> list[list[float]]:
    """Propagation vector the search stage settled on."""
    report_path = analysis / "search_report.json"
    if report_path.is_file():
        report = _read_json(report_path)
        chosen = report.get("chosen_k")
        if chosen:
            return [list(map(float, chosen))]
        candidates = report.get("k_candidates") or []
        if candidates:
            return [list(map(float, candidates[0]["k"]))]
    raise MissionError("Run the propagation-vector stage first, or pass k_vectors")


def _free_components(mode: dict[str, Any]) -> int:
    """Number of independent moment components the candidate's symmetry allows.

    ``child_basis_constraint_relations`` lists the basis vectors of the allowed
    moment subspace in the magnetic (child) cell, so its length is the number of
    free components the refinement must keep tied to each other.
    """
    total = 0
    sites = mode.get("magnetic_sites") or []
    if not sites:
        return 0
    for site in sites:
        relations = site.get("child_basis_constraint_relations")
        total += len(relations) if relations else 3
    return total


def present_candidates(paths: dict[str, Path], modes: Sequence[dict[str, Any]]) -> str:
    """Candidate overview figure."""
    from . import present

    entries = [{"bns_number": mode.get("bns_number"), "id": mode.get("id"),
                "free_moment_components": _free_components(mode)} for mode in modes]
    return present.candidate_figure(entries, paths["figures"] / "magnetic_candidates.png")


# --------------------------------------------------------------------------- #
# Stage 4: symmetry-constrained magnetic Rietveld comparison
# --------------------------------------------------------------------------- #
def refine(user_id: str, session_id: str, mission_name: str, task_id: str,
           screen_cycles: int = 6, final_cycles: int = 24, shortlist: int | None = None,
           screen_time_limit_seconds: float = 900.0,
           final_time_limit_seconds: float = 5400.0,
           native_solver: str = "analytic Jacobian",
           models: Sequence[str] | None = None,
           refine_profile: bool = True, refine_cell: bool = True,
           background_coeffs: int = 6) -> dict[str, Any]:
    """Compare magnetic candidates with symmetry-constrained Rietveld refinement.

    ``shortlist`` selects the strategy:

    * an integer (default 12) runs the two-pass screen — every candidate is fitted
      with a small cycle budget to rank them, then the shortlist (best screens plus
      near-degenerate groups) is refined at full settings. Fast, but the ranking
      rests on the screen converging;
    * ``None`` skips the screen and refines **every** enumerated candidate in one
      full pass. Slower, but the ranking never depends on a crude screen. Use it
      when the candidate set is small, when the screen's best reduced χ² is still
      worse than the nuclear-only control (a sign the screen did not converge), or
      when the comparison must be exhaustive.

    Either way the scope and any wall-clock truncation travel with the report.
    """
    start = time.perf_counter()
    paths = mission_paths(user_id, session_id, mission_name)
    analysis, figures = paths["analysis"], paths["figures"]
    _set_progress(paths, "refine")
    result: dict[str, Any] = {"status": "failure", "task_id": task_id}
    try:
        from .native import refine_native

        screen = None
        if models is not None:
            shortlist_ids = [str(name) for name in models]
            _steps(f"步骤 1/5: 使用指定候选子集（{len(shortlist_ids)} 个）")
        elif shortlist is None:
            shortlist_ids = None
            _steps(f"步骤 1/5: 单次全量精修全部候选（{final_cycles} 周期，不预筛）")
        else:
            _steps(f"步骤 1/5: 对全部候选做廉价预筛（{screen_cycles} 周期、不放开峰型）")
            screen = refine_native(
                str(paths["inputs"]), str(analysis), cycles=screen_cycles,
                max_models=4096, time_limit_seconds=screen_time_limit_seconds,
                refine_cell=False, refine_profile=False,
                background_coeffs=background_coeffs,
                native_solver=native_solver,
                progress_log=str(paths["analysis"] / "progress.log"), stage_label="screen")
            screen_report = _read_json(Path(screen["report_path"]))
            shortlist_ids = _shortlist_ids(screen_report, shortlist)
            unreliable = _screen_unreliable(screen_report)
            if unreliable:
                _steps(f"  ⚠ 预筛可能未收敛：{unreliable['detail']}；"
                       f"短名单排序不可靠，建议改用 shortlist=null 做单次全量精修")
            else:
                _steps(f"  ✓ 预筛完成 {screen['models_evaluated']}/{screen['models_total']}，"
                       f"进入精修的短名单 {len(shortlist_ids)} 个")

        _steps("步骤 2/5: 磁 Rietveld 精修（保留磁空间群约束）")
        final = refine_native(
            str(paths["inputs"]), str(analysis), cycles=final_cycles,
            max_models=4096, time_limit_seconds=final_time_limit_seconds,
            refine_cell=refine_cell, refine_profile=refine_profile,
            background_coeffs=background_coeffs,
            native_solver=native_solver,
            progress_log=str(paths["analysis"] / "progress.log"), models=shortlist_ids or None,
            stage_label="final")
        report = _read_json(Path(final["report_path"]))
        _steps(f"  ✓ 精修完成 {final['models_evaluated']}/{final['models_total']}，"
               f"最优 {final['best_model']}")

        _steps("步骤 3/5: 绘制模型对比图")
        comparison = present_comparison(paths, report["models"])
        _steps("步骤 4/5: 绘制最优模型的拟合图")
        best = _best_model(report["models"], final["best_model"])
        fit_figure = present_fit(paths, figures, best["gpx_path"],
                                 "best_magnetic_fit.png",
                                 f"Magnetic Rietveld fit - {best['id']}")
        _steps("步骤 5/5: 汇总排名与诊断")
        ranking = [{"id": model["id"],
                    "magnetic_space_group": _mode_field(analysis, model["id"], "magnetic_space_group"),
                    "bns_number": model.get("bns_number"),
                    "reduced_chisq": model["quality"]["reduced_chisq"],
                    "bic": model["quality"]["bic_known_variance"],
                    "moment_magnitudes": (model.get("physical") or {}).get("moment_norms"),
                    "mcif": model.get("mcif_path")}
                   for model in sorted((m for m in report["models"] if m.get("status") == "ok"),
                                       key=lambda m: m["quality"]["bic_known_variance"])]
        result.update(
            status="completed",
            report_path=final["report_path"],
            best_model=final["best_model"],
            best_mcif_path=final.get("final_mcif_path"),
            best_gpx_path=final.get("final_gpx_path"),
            complete=final.get("complete"),
            time_bound_reached=final.get("time_bound_reached"),
            models_evaluated=final.get("models_evaluated"),
            models_total=final.get("models_total"),
            shortlist=shortlist_ids,
            refine_cell=refine_cell, background_coeffs=background_coeffs,
            strategy=("single full pass over every enumerated candidate"
                      if models is None and shortlist_ids is None
                      else "explicit candidate subset" if models is not None
                      else "screen then full-refine the shortlist"),
            screen_report_path=(screen or {}).get("report_path"),
            screen_unreliable=(_screen_unreliable(screen_report) if shortlist_ids is not None and screen is not None else None),
            comparison_figure=comparison,
            fit_figure=fit_figure,
            progress_log=str(paths["analysis"] / "progress.log"),
            ranking=ranking,
            scientific_status=final.get("scientific_status"),
            summary=_refine_summary(ranking, final),
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        _steps(f"  ✗ 磁精修失败: {result['error']}")
    result["execution_time_sec"] = _elapsed(start)
    return result


def _shortlist_ids(report: dict[str, Any], limit: int, extra: int = 8,
                   near_degenerate_bic: float = 6.0) -> list[str]:
    """Candidates the full pass refines: the best screens plus near-degenerate groups.

    Deduplication is by *exact* magnetic space group, never by a coarse family: two
    different groups can share a BNS prefix and still be different magnetic structures,
    so collapsing them would silently drop a competing model. The extra budget is
    bounded, so the scope stays discloseable.
    """
    ok = [m for m in report.get("models", []) if m.get("status") == "ok"]
    if not ok:
        raise MissionError("The screening pass produced no converged candidate")
    ranked = sorted(ok, key=lambda m: m["quality"]["bic_known_variance"])
    best = ranked[0]["quality"]["bic_known_variance"]
    chosen: list[str] = []
    seen: set[str] = set()
    for model in ranked[:max(1, int(limit))]:
        chosen.append(model["id"])
        seen.add(str(model.get("bns_number")))
    if extra > 0:
        for model in ranked:
            group = str(model.get("bns_number"))
            if group in seen:
                continue
            if model["quality"]["bic_known_variance"] - best > near_degenerate_bic:
                break
            seen.add(group)
            chosen.append(model["id"])
            if len(chosen) >= max(1, int(limit)) + int(extra):
                break
    return chosen


def _screen_unreliable(report: dict[str, Any]) -> dict[str, Any] | None:
    """Flag a screen whose best fit still loses to the nuclear-only control.

    A magnetic model contains its nuclear parent, so a converged magnetic fit must beat
    the nuclear-only control. When even the best screen cannot, the screen has not
    converged and ranking models by it would pick a winner for the wrong reason.
    """
    ok = [m for m in report.get("models", []) if m.get("status") == "ok"]
    if not ok:
        return None
    best = min(m["quality"]["reduced_chisq"] for m in ok)
    control = None
    for path in (("baseline", "low_nuclear_control", "reduced_chisq"),
                 ("low_nuclear_control", "reduced_chisq")):
        node: Any = report
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, (int, float)):
            control = float(node)
            break
    if control is None or best <= control:
        return None
    return {"detail": (f"预筛最优 reduced χ²={best:.1f} 仍高于纯核对照 {control:.1f}"),
            "best_screen_reduced_chisq": best,
            "nuclear_only_reduced_chisq": control}


def _mode_field(analysis: Path, model_id: str, key: str) -> Any:
    """One field of a candidate record, for the ranking table."""
    try:
        report = _read_json(analysis / "candidates_report.json")
    except Exception:
        return None
    for mode in report.get("modes", []):
        if mode.get("id") == model_id:
            return mode.get(key)
    return None


def _best_model(models: Sequence[dict[str, Any]], best_id: str) -> dict[str, Any]:
    for model in models:
        if model.get("id") == best_id:
            return model
    raise MissionError(f"Best model {best_id} is missing from the report")


def _refine_summary(ranking: Sequence[dict[str, Any]], final: dict[str, Any]) -> str:
    if not ranking:
        return "没有收敛的磁模型。"
    top = ranking[0]
    moment = top.get("moment_magnitudes") or []
    magnitude = max(moment) if moment else float("nan")
    scope = "全部候选" if final.get("complete") else f"{final.get('models_total')} 个候选中的短名单"
    return (f"短名单精修后最优模型 {top['id']}（{top.get('magnetic_space_group')}，"
            f"BNS {top.get('bns_number')}），reduced χ²={top['reduced_chisq']:.2f}，"
            f"最大磁矩 {magnitude:.3f} μB；本次比较范围：{scope}。")


def present_comparison(paths: dict[str, Path], models: Sequence[dict[str, Any]]) -> str:
    """Model-comparison figure."""
    from . import present

    return present.model_comparison_figure(models, paths["figures"] / "model_comparison.png")


# --------------------------------------------------------------------------- #
# Stage 5: final export
# --------------------------------------------------------------------------- #
def export(user_id: str, session_id: str, mission_name: str, task_id: str,
           destination: str | Path | None = None,
           gpx_path: str | None = None) -> dict[str, Any]:
    """Copy the winning magnetic structure to the requested final location."""
    start = time.perf_counter()
    paths = mission_paths(user_id, session_id, mission_name)
    result: dict[str, Any] = {"status": "failure", "task_id": task_id}
    try:
        _steps("步骤 1/2: 定位最终磁结构文件")
        report_path = paths["analysis"] / "refinement_report.json"
        if not report_path.is_file():
            raise MissionError("Run the magnetic refinement stage first")
        report = _read_json(report_path)
        if not report.get("best_model") or not report.get("mcif_path"):
            raise MissionError("The refinement report carries no exported magnetic structure")
        source = Path(report["mcif_path"])
        # A deepening pass that improved the fit is the better structure to deliver; it is
        # recorded in its own report, so the comparison record stays the selection evidence.
        # A model refined further by the gsas2-refine service comes back as a project file;
        # exporting the magnetic MCIF from that project is how the handoff completes.
        if gpx_path:
            from .native import export_project_mcif

            exported = export_project_mcif(gpx_path, paths["analysis"] / "handoff_refined.mcif")
            source = Path(exported["mcif_path"])
            _steps(f"  · 从交接回来的项目导出磁结构：{Path(str(gpx_path)).name}"
                   f"（磁相 {exported.get('magnetic_phase')}）")
        if not source.is_file():
            raise MissionError(f"Exported MCIF is missing: {source}")
        submission = paths["submission"]
        submission.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, submission / "final.mcif")
        # A task normally names the session workspace ("write to submission/"), so the
        # default destination is the session-level submission directory, not the mission
        # subdirectory; an explicit destination still wins.
        if destination:
            target = Path(destination).expanduser()
        else:
            target = paths["session_submission"] / "final.mcif"
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target != (submission / "final.mcif").resolve():
            shutil.copyfile(source, target)
        _steps(f"步骤 2/2: 已写出 {target}")
        result.update(
            status="completed",
            mcif_path=str(target),
            mission_mcif_path=str(submission / "final.mcif"),
            source_mcif_path=str(source),
            best_model=report.get("best_model"),
            magnetic_space_group=report.get("scientific_status", {}).get("warning") and None,
            complete=report.get("complete"),
            scope=report.get("scope"),
            summary=f"最终磁结构已导出到 {target}（模型 {report.get('best_model')}）。",
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        _steps(f"  ✗ 导出失败: {result['error']}")
    result["execution_time_sec"] = _elapsed(start)
    return result
