"""Platform-guided, local edge selection for the existing midpoint definition of m.

This is a data-analysis heuristic, not a reflectivity model fit. Ambiguous or
insufficient data raise ValueError instead of returning an invalid m/r pair.
"""
import numpy as np
from scipy.signal import savgol_filter
from scipy.stats import theilslopes
from scipy.optimize import least_squares

METHOD = "platform_local_edge_v1"


def _line_fit(x, y, error):
    """Robust line, with a 1% scale floor so tiny counting errors do not dominate."""
    x0 = float(np.mean(x))
    slope, intercept, _, _ = theilslopes(y, x)
    scale = np.maximum(error, max(float(np.median(np.abs(y))) * .01, 1e-12))
    fit = least_squares(lambda p: (p[0] * (x - x0) + p[1] - y) / scale,
                        [slope, intercept + slope * x0], loss="soft_l1")
    if not fit.success:
        raise ValueError("平台拟合未收敛")
    return float(fit.x[0]), float(fit.x[1] - fit.x[0] * x0)


def _first_run(mask, start, count, x, max_gap):
    for i in range(start, len(mask) - count + 1):
        if np.all(mask[i:i + count]) and np.all(np.diff(x[i:i + count]) <= max_gap):
            return i
    return None


def calculate_mr_from_dataset(dataset, segment1_start_q=0.0217,
                              threshold_high=-0.5, threshold_low=0.5,
                              *, smoothing_window=7, plateau_points=8,
                              drop_fraction=.8, tail_fraction=.15,
                              max_relative_error=.5):
    """Find the first sustained platform-to-low-reflectivity transition.

    Qm stays the midpoint between local negative/positive curvature extrema;
    m=Qm/0.0217, r is the robust platform line extrapolated to Qm.
    threshold_high/low remain unused legacy arguments for call compatibility.
    No expected m/r or run-specific Q bounds are encoded.
    """
    if (not isinstance(smoothing_window, int) or smoothing_window < 5 or smoothing_window % 2 != 1
            or plateau_points < 5 or not 0 < tail_fraction < drop_fraction < 1
            or max_relative_error <= 0):
        raise ValueError("分段参数无效")
    x = np.asarray(dataset["xvalue"].values, dtype=float).reshape(-1)
    y = np.asarray(dataset["histogram"].values, dtype=float).reshape(-1)
    if x.size != y.size:
        raise ValueError("Q与反射率长度不一致")
    if "error" in dataset:
        error = np.asarray(dataset["error"].values, dtype=float).reshape(-1)
        if error.size != y.size:
            raise ValueError("误差与反射率长度不一致")
    else:
        error = np.zeros_like(y)
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(error) & (error >= 0) & (x >= segment1_start_q)
    x, y, error = x[valid], y[valid], error[valid]
    order = np.argsort(x, kind="stable")
    x, y, error = x[order], y[order], error[order]
    if len(x) < plateau_points + 7 or np.any(np.diff(x) <= 0):
        raise ValueError("有效数据不足或Q值重复，无法可靠分段")
    spacing = float(np.median(np.diff(x)))
    max_gap = 2.5 * spacing
    reliable = (y > 0) & (error <= max_relative_error * np.abs(y))
    if not np.all(reliable[:plateau_points]) or np.any(np.diff(x[:plateau_points]) > max_gap):
        raise ValueError("起始平台数据不连续或误差过大，请检查数据/平台起点")
    slope, intercept = _line_fit(x[:plateau_points], y[:plateau_points], error[:plateau_points])
    level = float(np.median(y[:plateau_points]))
    initial_residual = y[:plateau_points] - (slope * x[:plateau_points] + intercept)
    if abs(slope) * np.ptp(x[:plateau_points]) > .25 * level or np.median(np.abs(initial_residual)) > .08 * level:
        raise ValueError("指定起点后的数据不是稳定平台，无法自动确定m/r")

    baseline = slope * x + intercept
    ratio = np.divide(y, baseline, out=np.full_like(y, np.nan), where=baseline > 0)
    crossing = _first_run(reliable & (ratio < drop_fraction), plateau_points, 3, x, max_gap)
    if crossing is None:
        raise ValueError("未找到平台之后持续下降的主边沿")
    tail = _first_run(reliable & (ratio < tail_fraction), crossing, 3, x, max_gap)
    if tail is None or tail - crossing > 12:
        raise ValueError("下降沿后没有可信低反射率区，不能确定分段终点")
    above = np.where(reliable[:crossing] & (ratio[:crossing] >= .9))[0]
    if not len(above):
        raise ValueError("下降沿前缺少可信平台")
    plateau_end = int(above[-1])
    # Stop before the later high-Q fluctuations; never smooth across missing bins.
    analysis_end = min(tail + 4, len(x) - 1)
    if np.any(np.diff(x[:analysis_end + 1]) > max_gap) or not np.all(reliable[:analysis_end + 1]):
        raise ValueError("主边沿分析区存在缺点或高误差点，需人工检查")
    xa, ya = x[:analysis_end + 1], y[:analysis_end + 1]
    window = min(smoothing_window, len(xa) if len(xa) % 2 else len(xa) - 1)
    smooth = savgol_filter(ya, window, 3)
    d1 = savgol_filter(np.gradient(smooth, xa), window, 3)
    d2 = np.gradient(d1, xa)
    # Display exactly the curvature used for selection (no extra hidden smoothing).
    left = max(plateau_points, plateau_end - window // 2)
    right = min(analysis_end - 1, tail + 2)
    peak = left + int(np.argmin(d1[left:right + 1]))
    if not left < peak < right or d1[peak] >= 0:
        raise ValueError("下降沿导数极值位于边界或无明确下降，请人工检查")
    idx_start = left + int(np.argmin(d2[left:peak + 1]))
    idx_end = peak + int(np.argmax(d2[peak:right + 1]))
    if not (idx_start < peak < idx_end) or d2[idx_start] >= 0 or d2[idx_end] <= 0:
        raise ValueError("未找到主下降沿两侧的有效曲率极值")

    # The platform cannot contain any point after the detected onset of the edge.
    platform_stop = min(idx_start, plateau_end + 1)
    x1, y1, e1 = x[:platform_stop], y[:platform_stop], error[:platform_stop]
    if len(x1) < plateau_points:
        raise ValueError("平台点数不足，不能拟合")
    slope, intercept = _line_fit(x1, y1, e1)
    q_start, q_end = float(xa[idx_start]), float(xa[idx_end])
    qm = (q_start + q_end) / 2
    r = slope * qm + intercept
    predicted = slope * x1 + intercept
    if not np.isfinite(r) or r <= 0 or np.any(predicted <= 0):
        raise ValueError("平台外推产生非正反射率，m/r无效")
    if np.median(np.abs(y1 - predicted)) > .1 * float(np.median(y1)):
        raise ValueError("平台残差过大，m/r分段不可靠")
    if not .5 <= r / float(np.median(y1)) <= 1.5:
        raise ValueError("平台外推偏离过大，m/r分段不可靠")
    return {"Qm": qm, "m": qm / .0217, "r": float(r), "slope1": slope, "intercept1": intercept,
            "x1": x1, "y1": y1, "x2": xa[idx_start:idx_end + 1], "y2": ya[idx_start:idx_end + 1],
            "x_remain": xa, "y_remain": ya, "d1": d1, "d2": d2,
            "method": METHOD, "status": "estimated", "Q_start": q_start, "Q_end": q_end,
            "platform_q_start": float(x1[0]), "platform_q_end": float(x1[-1]),
            "analysis_q_end": float(xa[-1]), "smoothing_window": window,
            "drop_fraction": drop_fraction, "tail_fraction": tail_fraction,
            "max_relative_error": max_relative_error, "seed_plateau_points": plateau_points,
            "warnings": ["自动分段估计，需结合诊断图检查；未进行反射模型拟合"]}
