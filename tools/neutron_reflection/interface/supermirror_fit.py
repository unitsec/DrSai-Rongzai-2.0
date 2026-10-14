"""Fit the PHITS 3.37 empirical supermirror profile, not a manufacturer test standard.

Reference: https://phits.jaea.go.jp/manual/PHITS-en/chapters/sections/supermirror.html
The automatic fit-window policy and robust loss are our analysis choices, not PHITS rules.
"""
import numpy as np
from scipy.optimize import least_squares

REFERENCE = "https://phits.jaea.go.jp/manual/PHITS-en/chapters/sections/supermirror.html"
METHOD = "phits_3_37_tanh_fit_v1"


def phits_reflectivity(q, r0, alpha, q_star, width, qc=.0217):
    """Exact PHITS piecewise expression; q_star=m*qc, alpha in Å, width in Å^-1."""
    q = np.asarray(q, dtype=float)
    envelope = r0 * (1 - alpha * (q - qc))
    result = .5 * envelope * (1 - np.tanh((q - q_star) / width))
    return np.where(q <= qc, r0, result)


def fit_supermirror_from_dataset(dataset, *, qc=.0217, fit_q_range=None, error_floor_fraction=.01):
    """Return fitted m and explicitly named envelope/center reflectivities.

    For compatibility, r denotes envelope reflectivity at the cutoff, NOT R(q_star).
    No resolution convolution or multilayer structural fit is performed.
    """
    from calculatemr import calculate_mr_from_dataset
    if not np.isfinite(qc) or qc <= 0 or not 0 < error_floor_fraction <= .1:
        raise ValueError("Qc或误差下限参数无效")
    x = np.asarray(dataset["xvalue"].values, dtype=float).reshape(-1)
    y = np.asarray(dataset["histogram"].values, dtype=float).reshape(-1)
    if "error" not in dataset:
        raise ValueError("参考模型拟合需要反射率误差列")
    error = np.asarray(dataset["error"].values, dtype=float).reshape(-1)
    if x.shape != y.shape or y.shape != error.shape:
        raise ValueError("Q、反射率和误差长度不一致")
    usable = np.isfinite(x) & np.isfinite(y) & np.isfinite(error) & (error >= 0) & ~((y == 0) & (error == 0))
    order = np.argsort(x[usable], kind="stable")
    x, y, error = x[usable][order], y[usable][order], error[usable][order]
    if len(x) < 16 or np.any(np.diff(x) <= 0):
        raise ValueError("拟合数据不足或Q重复")
    if fit_q_range is None:
        # Existing heuristic supplies ONLY the initial guess and automatic fit window.
        # Reported m and width come from the fit, not from curvature endpoints.
        seed = calculate_mr_from_dataset(dataset, segment1_start_q=qc)
        qmin, qmax = max(qc, seed["platform_q_start"]), seed["analysis_q_end"]
        q_guess = seed["Qm"]
        r_guess = seed["slope1"] * qc + seed["intercept1"]
        alpha_guess = max(0., -seed["slope1"] / r_guess)
        window_policy = "platform_local_edge_v1_for_initial_window_only"
    else:
        if len(fit_q_range) != 2:
            raise ValueError("fit_q_range必须为[Q_min,Q_max]")
        qmin, qmax = map(float, fit_q_range)
        if not np.isfinite([qmin, qmax]).all() or not qc <= qmin < qmax:
            raise ValueError("拟合范围无效，当前分析只拟合Q>=Qc的超镜部分")
        window_policy = "explicit_q_range"
        q_guess = None
        r_guess = alpha_guess = None
    use = (x >= qmin) & (x <= qmax)
    xf, yf, ef = x[use], y[use], error[use]
    if len(xf) < 16:
        raise ValueError("选定范围的有效点不足16个")
    dx = float(np.median(np.diff(xf)))
    if np.any(np.diff(xf) > 2.5 * dx):
        raise ValueError("拟合范围存在过大Q缺口")
    if r_guess is None:
        r_guess = float(np.median(yf[:8]))
        alpha_guess = 1.
        crossing = np.where(yf < .5 * r_guess)[0]
        if not len(crossing):
            raise ValueError("拟合范围没有覆盖反射率下降")
        q_guess = float(xf[crossing[0]])
    if r_guess <= 0:
        raise ValueError("起始包络反射率无效")
    floor = error_floor_fraction * r_guess
    sigma = np.maximum(ef, floor)
    alpha_max = .99 / (xf[-1] - qc)
    lower = np.array([.01, 0., xf[2], dx / 20])
    upper = np.array([1.5, alpha_max, xf[-3], (xf[-1] - xf[0]) / 4])
    if np.any(upper <= lower):
        raise ValueError("拟合范围不足以约束参数")
    def residual(p):
        return (phits_reflectivity(xf, *p, qc=qc) - yf) / sigma
    fits = []
    for shift in (-dx, 0., dx):
        for w0 in (dx, 2 * dx, 4 * dx):
            guess = np.clip([r_guess, alpha_guess, q_guess + shift, w0], lower + 1e-9, upper - 1e-9)
            fit = least_squares(residual, guess, bounds=(lower, upper), loss="soft_l1",
                                x_scale="jac", max_nfev=3000, ftol=1e-10, xtol=1e-10, gtol=1e-10)
            if fit.success and np.isfinite(fit.x).all():
                fits.append(fit)
    if not fits:
        raise ValueError("PHITS经验模型拟合未收敛")
    best = min(fits, key=lambda item: item.cost)
    r0, alpha, q_star, width = map(float, best.x)
    envelope = r0 * (1 - alpha * (q_star - qc))
    if envelope <= 0:
        raise ValueError("拟合截止处包络非正，参数无效")
    half_span = float(np.arctanh(.8) * width)
    q90, q10 = q_star - half_span, q_star + half_span
    wide_half_span = float(np.arctanh(.98) * width)
    if not xf[0] < q90 < q_star < q10 < xf[-1]:
        raise ValueError("拟合范围未包住90%至10%过渡区，无法可靠报告m与宽度")
    if np.count_nonzero(xf < q90) < 8 or np.count_nonzero(xf > q10) < 2:
        raise ValueError("拟合的下降前或下降后约束不足")
    predicted = phits_reflectivity(xf, *best.x, qc=qc)
    scaled_residual = (predicted - yf) / sigma
    # Use the explicitly defined wider 99%-1% interval for local shape diagnostics.
    edge_mask = (xf >= q_star - wide_half_span) & (xf <= q_star + wide_half_span)
    edge_residual = scaled_residual[edge_mask]
    edge_rms = float(np.sqrt(np.mean(edge_residual ** 2)))
    warnings = ["参考PHITS经验模型；不是厂家验收标准；未卷积仪器分辨率，W是本次测量的有效宽度"]
    status = "estimated"
    if r0 > 1:
        warnings.append("拟合R0超过1，可能有归一化/模型偏差，不能作为物理反射概率直接接受")
        status = "needs_review"
    if np.any((best.x - lower) / (upper - lower) < 1e-4) or np.any((upper - best.x) / (upper - lower) < 1e-4):
        warnings.append("有参数接近拟合边界，需要人工检查")
        status = "needs_review"
    if width < dx / 2:
        warnings.append("过渡宽度小于半个Q采样间隔，宽度未充分分辨")
        status = "needs_review"
    if np.sqrt(np.mean(scaled_residual ** 2)) > 3:
        warnings.append("拟合残差相对于有效权重较大，请检查模型适用性与拟合范围")
        status = "needs_review"
    signed_runs = any(np.any(np.convolve((sign * edge_residual > 3).astype(int), [1, 1], mode="valid") == 2)
                      for sign in (-1, 1)) if len(edge_residual) >= 2 else False
    if edge_rms > 3 or signed_runs:
        warnings.append("下降沿存在连续同向大残差，PHITS对称截止形状未充分描述实测曲线，m/r仅作候选估计")
        status = "needs_review"
    result = {"Qm": q_star, "m": q_star / qc, "r": envelope,
              "r_envelope": envelope, "r_center": envelope / 2, "R0": r0, "alpha": alpha,
              "W": width, "Qc_Ni": qc, "Q90": q90, "Q10": q10, "width_90_10": q10 - q90,
              "Q99": q_star - wide_half_span, "Q01": q_star + wide_half_span,
              "width_99_01": 2 * wide_half_span,
              "Q_start": q90, "Q_end": q10, "method": METHOD, "status": status,
              "r_definition": "envelope_at_fitted_cutoff_not_actual_R_at_center",
              "reference": REFERENCE, "fit_window_policy": window_policy,
              "fit_q_min": float(xf[0]), "fit_q_max": float(xf[-1]), "fit_points": len(xf),
              "error_floor_fraction": error_floor_fraction, "effective_error_floor": floor,
              "edge_weighted_rms_with_error_floor": edge_rms,
              "robust_loss": "soft_l1", "weighted_rms_with_error_floor": float(np.sqrt(np.mean(scaled_residual ** 2))),
              "unweighted_rms": float(np.sqrt(np.mean((predicted - yf) ** 2))),
              "warnings": warnings, "fit_x": xf, "fit_y": yf, "fit_error": ef,
              "fit_prediction": predicted, "fit_scaled_residual": scaled_residual,
              "slope1": -r0 * alpha, "intercept1": r0 * (1 + alpha * qc)}
    return result


def plot_model_result(dataset, fit, run, folder):
    """Plot the fit, a 90%-10% transition band, and residuals; no derivative claims."""
    from pathlib import Path
    import matplotlib.pyplot as plt
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    x = np.asarray(dataset["xvalue"].values).reshape(-1)
    y = np.asarray(dataset["histogram"].values).reshape(-1)
    error = np.asarray(dataset["error"].values).reshape(-1)
    visible = np.isfinite(x) & np.isfinite(y) & np.isfinite(error) & (y > 0) & (error >= 0)
    xx = np.linspace(fit["fit_q_min"], fit["fit_q_max"], 600)
    yy = phits_reflectivity(xx, fit["R0"], fit["alpha"], fit["Qm"], fit["W"], fit["Qc_Ni"])
    envelope = fit["R0"] * (1 - fit["alpha"] * (xx - fit["Qc_Ni"]))
    for diagnostic in (False, True):
        fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1]}, layout="constrained")
        ax, residual = axes
        ax.errorbar(x[visible], y[visible], yerr=error[visible], fmt=".", color=".65", label="Measured (positive points on log plot)")
        ax.plot(xx, yy, color="navy", label="PHITS empirical profile fit")
        ax.plot(xx, envelope, "--", color="green", label="Pre-cutoff envelope B(Q)")
        ax.axvspan(fit["Q99"], fit["Q01"], color="orange", alpha=.10, label="Cutoff factor: 99% to 1%")
        ax.axvspan(fit["Q90"], fit["Q10"], color="orange", alpha=.25, label="Cutoff factor: 90% to 10%")
        ax.axvline(fit["Qm"], color="navy", linestyle=":", label=f"Cutoff center, m={fit['m']:.4f}")
        ax.scatter([fit["Qm"]], [fit["r_envelope"]], marker="*", s=110, color="green", label=f"Envelope: {100*fit['r_envelope']:.2f}%")
        ax.scatter([fit["Qm"]], [fit["r_center"]], marker="D", s=45, color="navy", label=f"Actual fitted R(center): {100*fit['r_center']:.2f}%")
        ax.set(yscale="log", ylabel="Reflectivity", title=f"{run} — PHITS reference fit ({fit['status']})")
        if diagnostic:
            span = fit["Q10"] - fit["Q90"]
            ax.set_xlim(max(fit["fit_q_min"], fit["Q90"] - span), min(fit["fit_q_max"], fit["Q10"] + span))
            ax.set_ylim(max(.001, fit["r_envelope"] / 100), fit["R0"] * 1.5)
        ax.legend(fontsize=8, loc="best")
        residual.plot(fit["fit_x"], fit["fit_scaled_residual"], ".", color="purple")
        residual.axhline(0, color="black", linewidth=.8)
        residual.set(xlabel="Q (Å⁻¹)", ylabel="Residual /\neffective σ")
        fig.savefig(folder / ("reflectivity_fit_diagnostic.png" if diagnostic else "reflectivity.png"), dpi=180)
        plt.close(fig)
