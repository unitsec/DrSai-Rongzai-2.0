"""Stage figures for the magnetic structure workflow.

Every stage of the magnetic mission renders one or more standalone PNG figures
under the mission directory, so the same 图文 presentation the nuclear, PDF and
small-angle capabilities already provide is available here too: the user sees
which step ran, what the fit currently looks like, and which model won.

Axis labels stay in English with the existing GSAS-II tool (the environment has
no guaranteed CJK font); Chinese text lives in tool docstrings and markdown
summaries instead.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
from matplotlib.lines import Line2D  # noqa: E402

OBSERVED_COLOR = "#1f77b4"
CALCULATED_COLOR = "#d62728"
BACKGROUND_COLOR = "#2ca02c"
DIFFERENCE_COLOR = "black"
CONTROL_COLOR = "#7f7f7f"


def _save(fig, path: Path) -> str:
    """Write one figure to PNG and close it; return the absolute path."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(path), dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def load_project(gpx_path: str | Path):
    """Open a GSAS-II project through the engine's own module loader."""
    from . import workflow as w

    sc, _, _, _, _ = w._modules()
    return sc.G2Project(gpxfile=str(gpx_path))


def _bragg_positions(project, histogram, dmin: float = 0.4) -> list[float]:
    """Reflection positions of every phase in one histogram, in TOF units.

    Mirrors the nuclear tool's per-phase peak markers so a magnetic phase and its
    nuclear parent appear as separate colour-coded tick clusters.
    """
    import GSASIIpwd as G2pwd

    positions: list[float] = []
    try:
        inst = histogram.data["Instrument Parameters"][0]
    except Exception:
        return positions
    for phase in project.phases():
        try:
            data = phase.data
            A = data["General"]["Cell"][1:]
            SGData = data["General"]["SGData"]
            hkls = G2pwd.getHKLpeak(dmin, SGData, A, inst)
        except Exception:
            continue
        if hkls is None:
            continue
        for hkl in hkls:
            try:
                value = float(hkl[4])
            except Exception:
                try:
                    value = float(G2pwd.Dsp2pos(inst, hkl[3]))
                except Exception:
                    continue
            if math.isfinite(value):
                positions.append(value)
    return positions


def _panel_figure(
    panels: Sequence[dict[str, Any]],
    path: Path,
    title: str,
    *,
    bragg: bool = True,
) -> str:
    """Stacked obs/calc/difference panels, one row per histogram.

    ``panels`` items carry ``name``, ``x``, ``observed``, ``calculated``,
    ``background``, optional ``control`` (nuclear-only calculation) and optional
    ``bragg`` positions.
    """
    rows = max(len(panels), 1)
    fig, axes = plt.subplots(rows, 1, figsize=(11, 3.1 * rows + 0.9), squeeze=False)
    for index, panel in enumerate(panels):
        ax = axes[index][0]
        x = np.asarray(panel["x"], float)
        obs = np.asarray(panel["observed"], float)
        calc = np.asarray(panel["calculated"], float)
        background = np.asarray(panel.get("background", np.zeros_like(x)), float)
        diff = obs - calc
        finite = np.isfinite(x) & np.isfinite(obs) & np.isfinite(calc)
        x, obs, calc, background, diff = (a[finite] for a in (x, obs, calc, background, diff))
        if x.size < 2:
            ax.set_visible(False)
            continue
        span = max(float(np.nanmax([obs.max(), calc.max(), background.max()]))
                   - float(np.nanmin([obs.min(), calc.min(), background.min()])), 1.0)
        dspan = max(float(np.nanmax(np.abs(diff))), span * 0.05)
        # The difference curve sits below the pattern, as in the nuclear tool.
        offset = float(np.nanmin(obs)) - 0.55 * span - dspan
        ax.plot(x, diff + offset, color=DIFFERENCE_COLOR, linewidth=0.7, zorder=2)
        ax.plot(x, background, color=BACKGROUND_COLOR, linewidth=1.0, zorder=3)
        ax.step(x, obs, where="mid", color=OBSERVED_COLOR, linewidth=0.8,
                alpha=0.95, zorder=4)
        ax.plot(x, calc, color=CALCULATED_COLOR, linewidth=1.0, alpha=0.95, zorder=5)
        if panel.get("control") is not None:
            control = np.asarray(panel["control"], float)[finite]
            ax.plot(x, control, color=CONTROL_COLOR, linewidth=0.8,
                    linestyle="--", alpha=0.9, zorder=4)
            ax.axhline(offset, color=CONTROL_COLOR, linewidth=0.5, alpha=0.35)
        handles = [
            Line2D([0], [0], color=OBSERVED_COLOR, linewidth=1.4, label="Experiment"),
            Line2D([0], [0], color=CALCULATED_COLOR, linewidth=1.4, label="Calculated"),
            Line2D([0], [0], color=BACKGROUND_COLOR, linewidth=1.4, label="Background"),
            Line2D([0], [0], color=DIFFERENCE_COLOR, linewidth=1.2, label="Obs-Calc"),
        ]
        if panel.get("control") is not None:
            handles.append(Line2D([0], [0], color=CONTROL_COLOR, linewidth=1.2,
                                  linestyle="--", label="Nuclear-only control"))
        if bragg and panel.get("bragg"):
            base = float(np.nanmin(obs)) - 0.95 * span - dspan
            colours = plt.get_cmap("tab10")
            for phase_index, (phase_name, positions) in enumerate(panel["bragg"]):
                inside = [p for p in positions if x.min() <= p <= x.max()]
                if not inside:
                    continue
                colour = colours(phase_index % 10)
                ax.vlines(inside, base, base + 0.12 * span, color=colour,
                          linewidth=0.9, alpha=0.9, zorder=1)
                handles.append(Line2D([0], [0], color=colour, marker="|",
                                      linestyle="None", markersize=10,
                                      markeredgewidth=2.4, label=f"Bragg ({phase_name})"))
        residual = panel.get("rwp")
        label = panel["name"] + (f"   Rwp = {residual:.2f}%" if residual is not None else "")
        ax.set_title(label, fontsize=11, loc="left")
        ax.set_xlim(float(x.min()), float(x.max()))
        ax.set_yticks([])
        ax.legend(handles=handles, fontsize=8, ncol=3, loc="upper center",
                  bbox_to_anchor=(0.5, -0.12), frameon=False)
    fig.suptitle(title, fontsize=13, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    return _save(fig, path)


def _panels_from_project(
    project,
    *,
    min_d_spacing: float = 0.4,
    control_project=None,
    bragg: bool = True,
) -> list[dict[str, Any]]:
    """Collect one plot panel per histogram of a GSAS-II project."""
    controls = {}
    if control_project is not None:
        controls = {hist.name: hist for hist in control_project.histograms()}
    panels = []
    for histogram in project.histograms():
        try:
            x = np.asarray(histogram.getdata("x"), float)
            observed = np.asarray(histogram.getdata("yobs"), float)
            calculated = np.asarray(histogram.getdata("ycalc"), float)
            background = np.asarray(histogram.getdata("background"), float)
        except Exception:
            continue
        panel: dict[str, Any] = {"name": histogram.name, "x": x, "observed": observed,
                                 "calculated": calculated, "background": background}
        try:
            panel["rwp"] = float(histogram.get_wR())
        except Exception:
            panel["rwp"] = None
        control = controls.get(histogram.name)
        if control is not None:
            try:
                panel["control"] = np.asarray(control.getdata("ycalc"), float)
            except Exception:
                panel["control"] = None
        if bragg:
            positions = _bragg_positions(project, histogram, min_d_spacing)
            if positions:
                panel["bragg"] = [(_phase_name(phase), positions)
                                  for phase in project.phases()]
        panels.append(panel)
    return panels


def _phase_name(phase) -> str:
    """Phase label for legends: GSAS-II name, then object name, then a fallback."""
    try:
        name = phase.data.get("General", {}).get("Name")
    except Exception:
        name = None
    if not name:
        name = getattr(phase, "name", None)
    return str(name or "phase")


def nuclear_fit_figure(gpx_path: str | Path, path: Path, title: str,
                       control_gpx: str | Path | None = None) -> str:
    """Fit panel(s) for a refined project, optionally against a control project."""
    project = load_project(gpx_path)
    control = load_project(control_gpx) if control_gpx else None
    return _panel_figure(_panels_from_project(project, control_project=control), path, title)


def data_overview_figure(records: Iterable[dict[str, Any]], path: Path,
                         title: str = "Input spectra: high vs low temperature") -> str:
    """Overlay the paired banks so the user sees what the mission was given.

    ``records`` items carry ``bank``, ``high``/``low`` (x, intensity) pairs.
    """
    records = list(records)
    rows = max(len(records), 1)
    fig, axes = plt.subplots(rows, 1, figsize=(11, 2.4 * rows + 0.8), squeeze=False)
    for index, record in enumerate(records):
        ax = axes[index][0]
        for key, colour, label in (("high", "#ff7f0e", "high temperature"),
                                   ("low", OBSERVED_COLOR, "low temperature")):
            values = record.get(key)
            if not values:
                continue
            x = np.asarray(values[0], float)
            y = np.asarray(values[1], float)
            if key == "high":
                y = y + float(np.nanmax(values[1])) * 1.15
            ax.plot(x, y, color=colour, linewidth=0.7, label=label)
        ax.set_title(str(record.get("bank")), fontsize=10, loc="left")
        ax.set_yticks([])
        ax.legend(fontsize=8, loc="upper right")
        if index == rows - 1:
            ax.set_xlabel("TOF (µs)")
    fig.suptitle(title, fontsize=13, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return _save(fig, path)


def propagation_figure(peaks: Sequence[dict[str, Any]], candidates: Sequence[dict[str, Any]],
                       path: Path, title: str = "Propagation-vector search evidence") -> str:
    """Show which peaks could not be indexed by the parent cell, and the k candidates."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    left, right = axes
    labels = [f"{p.get('bank', '')}\n{p.get('tof', float('nan')):.0f}" for p in peaks][:24]
    magnitudes = [abs(float(p.get("relative_difference", 0.0))) * 100 for p in peaks][:24]
    left.bar(range(len(magnitudes)), magnitudes, color=OBSERVED_COLOR)
    left.set_xticks(range(len(labels)))
    left.set_xticklabels(labels, rotation=90, fontsize=6)
    left.set_ylabel("|relative d error| (%)")
    left.set_title("Peaks the parent cell does not explain", fontsize=10, loc="left")

    names = [str(c.get("k")) for c in candidates][:10]
    quality = [float(c.get("indicator_distance", 0.0)) for c in candidates][:10]
    right.barh(range(len(quality))[::-1], quality, color=CALCULATED_COLOR)
    right.set_yticks(range(len(names))[::-1])
    right.set_yticklabels(names, fontsize=8)
    right.set_xlabel("GSAS-II indicator distance")
    right.set_title("Candidate propagation vectors", fontsize=10, loc="left")
    fig.suptitle(title, fontsize=13, y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save(fig, path)


def candidate_figure(candidates: Sequence[dict[str, Any]], path: Path,
                     title: str = "Magnetic space-group candidates (Bilbao k-SUBGROUPSMAG)") -> str:
    """One bar per candidate showing how many moment components stay free."""
    entries = list(candidates)[:40]
    names = [str(c.get("bns_number", c.get("id", ""))) for c in entries]
    free = [float(c.get("free_moment_components", 0)) for c in entries]
    fig, ax = plt.subplots(figsize=(max(7.0, 0.28 * len(names) + 4), 4.0))
    ax.bar(range(len(names)), free, color=CALCULATED_COLOR)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=90, fontsize=6)
    ax.set_ylabel("free moment components")
    ax.set_title(title, fontsize=11, loc="left")
    fig.tight_layout()
    return _save(fig, path)



def deepening_figure(model: dict[str, Any], path: Path,
                     title: str = "Post-comparison deepening of the selected model") -> str:
    """show what deepening the selected model changed, round by round and per bank."""
    model = model or {}
    rounds = ((model.get("deepening") or {}).get("rounds")) or []
    before = model.get("comparison_quality") or {}
    after = model.get("quality") or {}
    fig, (left, right) = plt.subplots(1, 2, figsize=(11.5, 3.8))
    if rounds:
        left.plot([r.get("round") for r in rounds],
                  [r.get("reduced_chisq") for r in rounds], marker="o", color="#1f77b4")
        left.set_xlabel("deepening round")
        left.set_ylabel("reduced chi-squared")
        left.set_title(f"{model.get('id')}: fit per round")
        left.grid(alpha=.2)
    else:
        left.text(.5, .5, "no deepening rounds recorded", ha="center", va="center")
        left.set_axis_off()
    before_banks = {str(h.get("name")): float(h.get("rwp") or 0.) for h in (before.get("histograms") or [])}
    after_banks = {str(h.get("name")): float(h.get("rwp") or 0.) for h in (after.get("histograms") or [])}
    names = [n for n in after_banks or before_banks]
    if names:
        x = range(len(names))
        width = .38
        right.bar([i - width / 2 for i in x], [before_banks.get(n, 0.) * 100 for n in names],
                  width, label="before deepening", color="#7f7f7f")
        right.bar([i + width / 2 for i in x], [after_banks.get(n, 0.) * 100 for n in names],
                  width, label="after deepening", color="#2ca02c")
        right.set_xticks(list(x))
        right.set_xticklabels([n.split()[-1][:14] for n in names], rotation=30, ha="right", fontsize=8)
        right.set_ylabel("Rwp (%)")
        right.set_title("per-bank Rwp before / after")
        right.legend(fontsize=8)
    else:
        right.text(.5, .5, "no per-bank Rwp recorded", ha="center", va="center")
        right.set_axis_off()
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    return _save(fig, path)


def model_comparison_figure(models: Sequence[dict[str, Any]], path: Path,
                            title: str = "Magnetic model comparison") -> str:
    """Rank the refined models by BIC and show their fitted moment magnitude."""
    ok = [m for m in models if m.get("status") == "ok"]
    if not ok:
        fig, ax = plt.subplots(figsize=(8, 2.4))
        ax.text(0.5, 0.5, "no converged magnetic model", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, path)
    ordered = sorted(ok, key=lambda m: m["quality"]["bic_known_variance"])
    best = ordered[0]["quality"]["bic_known_variance"]
    names = [str(m["id"]) for m in ordered][:30]
    delta = [float(m["quality"]["bic_known_variance"]) - best for m in ordered][:30]
    moment = [float(max((m.get("physical") or {}).get("moment_norms") or [0.0]))
              for m in ordered][:30]
    chi = [float(m["quality"]["reduced_chisq"]) for m in ordered][:30]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2))
    for ax, values, label, colour in (
            (axes[0], delta, "ΔBIC vs best", CALCULATED_COLOR),
            (axes[1], moment, "|m| (μB)", OBSERVED_COLOR),
            (axes[2], chi, "reduced χ²", BACKGROUND_COLOR)):
        ax.bar(range(len(names)), values, color=colour)
        ax.set_ylabel(label, fontsize=9)
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, rotation=90, fontsize=6)
    fig.suptitle(title + f"   (best: {ordered[0]['id']})", fontsize=13, y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save(fig, path)
