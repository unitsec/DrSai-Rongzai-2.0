"""Explicit public report artifacts: one annotated curve and a data-only ZIP."""
from pathlib import Path
import re
import zipfile


def curve_arrays(dataset):
    import numpy as np
    arrays = [np.asarray(dataset[name].values, dtype=float).reshape(-1)
              for name in ("xvalue", "histogram", "error")]
    if not arrays[0].size or not all(array.shape == arrays[0].shape for array in arrays):
        raise ValueError("曲线Q、R、dR必须非空且长度一致")
    return arrays


def export_curve_data(output, group_id, run, dataset):
    """Keep every original bin, including zero, negative and non-finite values."""
    import numpy as np
    if not re.fullmatch(r"group_[0-9]{3,}", group_id) or not re.fullmatch(r"RUN[0-9]{7}", run):
        raise ValueError("曲线导出标识无效")
    arrays = curve_arrays(dataset)
    folder = Path(output) / "reflectivity_data" / group_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (run + ".dat")
    np.savetxt(path, np.column_stack(arrays), fmt="%.17g",
               header="Q(A^-1)  R  dR\nR and dR are fractions (not percent)")
    return path


def zip_curve_data(output, paths):
    output = Path(output).resolve()
    root = output / "reflectivity_data"
    if not paths:
        raise ValueError("没有可打包的反射率数据")
    members = []
    for path in paths:
        path = Path(path).resolve()
        if not path.is_relative_to(root) or path.suffix != ".dat" or not path.is_file():
            raise ValueError("只能打包本任务生成的反射率.dat文件")
        members.append((path, path.relative_to(root).as_posix()))
    if len({name for _, name in members}) != len(members):
        raise ValueError("ZIP中的曲线文件不能重复")
    archive = output / "reflectivity_data.zip"
    temporary = output / "reflectivity_data.zip.tmp"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as z:
        for path, name in members:
            z.write(path, name)
    temporary.replace(archive)
    return archive


def plot_representative(output, run, dataset, estimate):
    """A single m/envelope-R illustration; other diagnostics are not auto-published."""
    import numpy as np
    import matplotlib.pyplot as plt
    x, y, error = curve_arrays(dataset)
    qm, m = float(estimate["Qm"]), float(estimate["m"])
    r = float(estimate["r_envelope"] if "r_envelope" in estimate else estimate["r"])
    if not np.isfinite([qm, m, r]).all() or r <= 0:
        raise ValueError("示意图缺少有效m、包络反射率")
    lower = float(estimate.get("fit_q_min", np.nanmin(x)))
    upper = float(estimate.get("fit_q_max", np.nanmax(x)))
    visible = np.isfinite(x) & np.isfinite(y) & np.isfinite(error) & (y > 0) & (error >= 0)
    if not np.any(visible):
        raise ValueError("示意图没有可展示的正反射率数据")
    xx = np.linspace(lower, upper, 500)
    if estimate.get("method") == "phits_3_37_tanh_fit_v1":
        from supermirror_fit import phits_reflectivity
        envelope = estimate["R0"] * (1 - estimate["alpha"] * (xx - estimate["Qc_Ni"]))
        predicted = phits_reflectivity(xx, estimate["R0"], estimate["alpha"], qm, estimate["W"], estimate["Qc_Ni"])
    else:
        envelope = estimate.get("slope1", 0) * xx + estimate.get("intercept1", r)
        predicted = None
    fig, ax = plt.subplots(figsize=(9, 5.5), layout="constrained")
    try:
        ax.errorbar(x[visible], y[visible], yerr=error[visible], fmt="o", linestyle="none",
                    markersize=3, capsize=2, elinewidth=.8, color=".55", label=run)
        if predicted is not None:
            ax.plot(xx, predicted, color="navy", label="PHITS profile fit")
        ax.plot(xx, envelope, "--", color="green", label="Pre-cutoff envelope B(Q)")
        ax.axvline(qm, color="navy", linestyle=":", label="Fitted cutoff center Q*")
        ax.scatter([qm], [r], marker="*", s=160, color="green", zorder=5)
        ax.annotate(f"m = {m:.4f}\nR envelope = {100*r:.2f}%", xy=(qm, r),
                    xycoords="data", xytext=(.52, .88), textcoords="axes fraction",
                    arrowprops={"arrowstyle": "->", "color": "green"}, fontsize=11,
                    bbox={"boxstyle": "round", "facecolor": "white", "alpha": .9})
        ax.set(xlabel="Q (Å⁻¹)", ylabel="Reflectivity", yscale="log",
               title=f"m / envelope R illustration — {run}", xlim=(lower, upper),
               ylim=(max(.0001, r / 200), max(1.3 * r, float(np.nanmax(envelope)) * 1.2)))
        ax.legend(fontsize=8, loc="lower left")
        path = Path(output) / "representative_reflectivity.png"
        fig.savefig(path, dpi=180)
    finally:
        plt.close(fig)
    return path


def method_markdown(methods):
    if set(methods) == {"phits_3_37_tanh_fit_v1"}:
        return (
            "模型来源：[PHITS 3.37 手册 §5.15](https://phits.jaea.go.jp/manual/PHITS-en/chapters/sections/supermirror.html)。\n\n"
            "```text\n"
            "Q > Qc： B(Q) = R0 × [1 − α(Q − Qc)]\n"
            "         R(Q) = B(Q) × {1 − tanh[(Q − Q*)/W]}/2\n"
            "Q ≤ Qc： R(Q) = R0\n"
            "m = Q*/Qc，Qc = 0.0217 Å⁻¹\n"
            "R_envelope(%) = 100 × B(Q*)\n"
            "```\n\n"
            "Q*为拟合截止中心；包络反射率为下降前平台/包络外推到Q*的高度。"
        )
    return "当前使用局部边沿分析：m=Q*/0.0217；包络反射率为下降前平台直线外推到Q*的高度。"
