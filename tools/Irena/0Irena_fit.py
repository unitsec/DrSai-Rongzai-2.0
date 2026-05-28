"""
小角散射 (SAS) 尺寸分布计算脚本
====================================
将 Irena (Igor Pro) 中的 IR1_Sizes.ipf 算法移植为 Python 实现。

支持三种求解方法：
  - Regularization （正则化，默认）
  - MaxEnt          （最大熵）
  - NNLS            （非负最小二乘）

粒子形状：目前支持 "sphere"（球形），可扩展。

参考文献：
  J Skilling and RK Bryan; MON NOT R ASTR SOC 211 (1984) 111-124.
  JA Potton et al., J APPL CRYST 21 (1988) 663-668.
  原始 Igor Pro 实现：Jan Ilavsky (ANL), Irena package.

输入数据格式（列分隔，# 开头为注释行）：
  Q(1/A)  I(Q)(1/cm)  dI(Q)(1/cm)  [sigmaQ(忽略)]
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.linalg import solve
from scipy.optimize import nnls
import argparse
import json
import os
import sys
import traceback
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.tools.mcp_task_runner import ProcessPollingMcpToolBase
from mcp.server.fastmcp import FastMCP


TMP_DIR = Path(__file__).resolve().parent.parent / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 1. 粒子形状因子 (Form Factor)
# ============================================================

def sphere_ff_amplitude(q: float, r: float) -> float:
    """
    球形粒子形状因子振幅 F(q, R)。
    来源：IR1R_CalculateSphereFFPoints / IR1T_CalculateSphereFFPoints
    """
    qr = q * r
    if qr < 1e-10:
        return 1.0  # limit qr->0
    return (3.0 / (qr ** 3)) * (np.sin(qr) - qr * np.cos(qr))


def sphere_volume(r: float) -> float:
    """球体体积 V = (4/3)π r³"""
    return (4.0 / 3.0) * np.pi * r ** 3


def sphere_ff_squared(q: float, r: float) -> float:
    """F²(q,R)·V(R)（体积加权，VolumePower=1，与 G 矩阵计算一致）"""
    amp = sphere_ff_amplitude(q, r)
    return amp * amp * sphere_volume(r)


# ============================================================
# 2. 构建 G 矩阵（散射核矩阵）
# ============================================================

def build_G_matrix(
    q_vec: np.ndarray,
    r_dist: np.ndarray,
    scattering_contrast: float = 1.0,
    shape: str = "sphere",
) -> np.ndarray:
    """
    构建 G 矩阵 [M x N]，M=Q点数，N=半径分布点数。
    G[j,i] = drho² * 1e20 * F²(q_j, r_i) * V(r_i)
    来源：IR1T_GenerateGMatrix + G_matrix = G_matrixFF * ScatteringContrast * 1e20
    单位：内部均使用 Angstrom。
    """
    M = len(q_vec)
    N = len(r_dist)
    G = np.zeros((M, N))
    for i, r in enumerate(r_dist):
        if shape == "sphere":
            col = np.array([sphere_ff_squared(q, r) for q in q_vec])
        else:
            raise NotImplementedError(f"Shape '{shape}' not supported yet.")
        G[:, i] = col
    G = G * scattering_contrast * 1e20
    return G


# ============================================================
# 3. 构建 R/D 分布网格
# ============================================================

def make_r_distribution(
    r_min: float,
    r_max: float,
    n_points: int,
    log_scale: bool = True,
) -> np.ndarray:
    """
    构建半径分布网格（对数或线性）。
    来源：IR1R_FinishSetupOfRegParam 中的 R_distribution 生成逻辑。
    """
    if log_scale:
        return np.logspace(np.log10(r_min), np.log10(r_max), n_points)
    else:
        return np.linspace(r_min, r_max, n_points)


def bin_width_in_radii(r_dist: np.ndarray) -> np.ndarray:
    """
    计算每个半径 bin 的宽度（用于归一化）。
    来源：IR1R_BinWidthInRadia
    """
    N = len(r_dist)
    widths = np.zeros(N)
    widths[0] = r_dist[1] - r_dist[0]
    widths[-1] = r_dist[-1] - r_dist[-2]
    widths[1:-1] = ((r_dist[1:-1] - r_dist[:-2]) / 2.0
                    + (r_dist[2:] - r_dist[1:-1]) / 2.0)
    return widths


# ============================================================
# 4. 构建 H 矩阵（平滑约束，二阶导数惩罚）
# ============================================================

def build_H_matrix(n: int) -> np.ndarray:
    """
    构建 H 矩阵 [N x N]（二阶导罚矩阵，1 -4 6 -4 1 模板）。
    来源：IR1R_MakeHmatrix
    """
    H = np.zeros((n, n))
    # 内部行：1 -4 6 -4 1
    for i in range(2, n - 2):
        H[i, i - 2] = 1
        H[i, i - 1] = -4
        H[i, i] = 6
        H[i, i + 1] = -4
        H[i, i + 2] = 1
    # 边界
    H[0, 0] = 1;  H[0, 1] = -2; H[0, 2] = 1
    H[1, 0] = -2; H[1, 1] = 5;  H[1, 2] = -4; H[1, 3] = 1
    H[n - 2, n - 4] = 1; H[n - 2, n - 3] = -4
    H[n - 2, n - 2] = 5; H[n - 2, n - 1] = -2
    H[n - 1, n - 3] = 1; H[n - 1, n - 2] = -2; H[n - 1, n - 1] = 1
    return H


# ============================================================
# 5. 构建 D 矩阵和 B 向量（来自 G、I、sigma）
# ============================================================

def build_D_matrix(G: np.ndarray, errors: np.ndarray) -> np.ndarray:
    """
    D = Gᵀ diag(1/σ²) G
    来源：IR1R_CalculateDMatrix
    """
    inv_sigma2 = 1.0 / (errors ** 2)
    G_scaled = G * inv_sigma2[:, np.newaxis]      # 广播 (M,N)
    return G_scaled.T @ G                          # (N,N)


def build_B_vector(G: np.ndarray, intensity: np.ndarray, errors: np.ndarray) -> np.ndarray:
    """
    B[i] = Σ_j G[j,i] * I[j] / σ[j]²
    来源：IR1R_CalculateBVector
    """
    inv_sigma2 = 1.0 / (errors ** 2)
    return G.T @ (intensity * inv_sigma2)          # (N,)


# ============================================================
# 6. 正则化求解（Regularization）
# ============================================================

REGULARIZATION_MIN_RATIO = 1e-4  # RegularizationMinRatio


def solve_regularization(
    G: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    r_dist: np.ndarray,
    e_value: float = 0.1,
    q_power: float = 0.0,
    q_vec: np.ndarray = None,
    max_iter: int = None,
) -> tuple:
    """
    正则化方法（IR1R_DoInternalRegularization + IR1R_FindOptimumAvalue）。
    通过二分搜索寻找最优拉格朗日乘子 α，使 χ² ≈ M（数据点数）。

    返回：
        x_dist  : 体积尺寸分布 dV/dD（按直径归一化），shape=(N,)
        fit_int : 拟合强度，shape=(M,)
        chi2    : χ²
        n_iter  : 迭代次数
    """
    M = len(intensity)
    N = len(r_dist)

    # Q 加权缩放（仅当无误差时 UseNoErrors，此处如有误差则 q_power=0）
    if q_power != 0.0 and q_vec is not None:
        q_scale = q_vec ** q_power
        I_w = intensity * q_scale
        e_w = errors * q_scale
        G_w = G * q_scale[:, np.newaxis]
    else:
        I_w, e_w, G_w = intensity, errors, G

    D = build_D_matrix(G_w, e_w)
    B = build_B_vector(G_w, I_w, e_w)
    H = build_H_matrix(N)

    tolerance = e_value * np.sqrt(2 * M)
    log_a_max, log_a_min = 100.0, -100.0

    if max_iter is None:
        max_iter = M

    x = np.zeros(N)
    chi2 = np.nan
    n_iter = 0

    for n_iter in range(1, max_iter + 1):
        mid = (log_a_max + log_a_min) / 2.0
        a_val = 10.0 ** mid

        A_mat = D + a_val * H
        try:
            x = solve(A_mat, B, assume_a='sym')
        except np.linalg.LinAlgError:
            return np.zeros(N), np.zeros(M), np.nan, n_iter

        # 转换为半径分布（除以2：直径→半径，再除以 bin 宽度）
        bin_w = bin_width_in_radii(r_dist)
        x_r = x / 2.0 / bin_w

        # 强制非负（最小值 = REGULARIZATION_MIN_RATIO * max）
        x_max = np.max(x_r) if np.max(x_r) > 0 else 1.0
        x_r = np.where(x_r > REGULARIZATION_MIN_RATIO * x_max,
                       x_r, REGULARIZATION_MIN_RATIO * x_max)

        # 计算拟合强度和 χ²
        fit_m = G_w @ x          # 使用原始 x（未归一化）
        if q_power != 0.0 and q_vec is not None:
            fit_int = fit_m / q_vec ** q_power
        else:
            fit_int = fit_m.copy()

        residuals = (I_w - fit_m) / e_w
        chi2 = np.sum(residuals ** 2)

        if chi2 > M:
            log_a_max = mid
        else:
            log_a_min = mid

        if abs(chi2 - M) <= tolerance:
            break

    return x_r, fit_int, chi2, n_iter


# ============================================================
# 7. NNLS 求解（非负最小二乘）
# ============================================================

def solve_nnls(
    G: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    r_dist: np.ndarray,
    q_power: float = 0.0,
    q_vec: np.ndarray = None,
) -> tuple:
    """
    NNLS 方法（IR1R_DoNNLS / IR1R_TNNLS）。
    求解 min‖(G/σ) x - (I/σ)‖²，  subject to x ≥ 0。

    返回：
        x_dist  : 尺寸分布（已归一化）
        fit_int : 拟合强度
        chi2    : χ²
    """
    # 误差加权
    inv_sigma = 1.0 / errors
    if q_power != 0.0 and q_vec is not None:
        q_scale = q_vec ** q_power
        A = (G * q_scale[:, np.newaxis]) * inv_sigma[:, np.newaxis]
        b = intensity * q_scale * inv_sigma
    else:
        A = G * inv_sigma[:, np.newaxis]
        b = intensity * inv_sigma

    x, _ = nnls(A, b)

    fit_m = G @ x
    residuals = (intensity - fit_m) / errors
    chi2 = np.sum(residuals ** 2)

    bin_w = bin_width_in_radii(r_dist)
    x_dist = x / 2.0 / bin_w

    return x_dist, fit_m, chi2


# ============================================================
# 8. MaxEnt 求解（最大熵，简化版）
# ============================================================

def solve_maxent(
    G: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    r_dist: np.ndarray,
    sky_background: float = 0.01,
    max_iter: int = 100,
    stability: float = 0.1,
    q_power: float = 0.0,
    q_vec: np.ndarray = None,
) -> tuple:
    """
    最大熵方法（IR1R_DoInternalMaxent）。
    使用 Cambridge 算法（Skilling & Bryan 1984）的简化迭代实现。

    返回：
        x_dist  : 尺寸分布（已归一化）
        fit_int : 拟合强度
        chi2    : χ²
        n_iter  : 实际迭代次数
    """
    M = len(intensity)
    N = len(r_dist)

    if q_power != 0.0 and q_vec is not None:
        q_scale = q_vec ** q_power
        I_w = intensity * q_scale
        e_w = errors * q_scale
        G_w = G * q_scale[:, np.newaxis]
    else:
        I_w, e_w, G_w = intensity, errors, G

    # 初始模型：均匀背景
    f = np.full(N, sky_background)
    target_chi2 = M  # 目标 χ² = 数据点数

    chi2 = np.nan
    n_iter = 0
    for n_iter in range(1, max_iter + 1):
        # 计算当前散射
        fit_m = G_w @ f
        residuals = (I_w - fit_m) / e_w
        chi2 = np.sum(residuals ** 2)

        # 熵梯度（相对于 f）
        grad_entropy = -np.log(f / sky_background) - 1.0

        # χ² 梯度
        grad_chi2 = -2.0 * G_w.T @ (residuals / e_w)

        # 简单步进：沿熵梯度方向，以 stability 控制步长
        step = stability * grad_entropy - (stability / target_chi2) * grad_chi2
        f = f + step
        f = np.maximum(f, 1e-20)  # 强制非负

        if abs(chi2 - target_chi2) / target_chi2 < stability:
            break

    # 最终拟合强度
    fit_m = G_w @ f
    if q_power != 0.0 and q_vec is not None:
        fit_int = fit_m / q_vec ** q_power
    else:
        fit_int = fit_m.copy()

    residuals = (intensity - fit_int) / errors
    chi2 = np.sum(residuals ** 2)

    bin_w = bin_width_in_radii(r_dist)
    x_dist = f / 2.0 / bin_w

    return x_dist, fit_int, chi2, n_iter


# ============================================================
# 9. 数据读取
# ============================================================

def load_sas_data(filepath: str) -> tuple:
    """
    读取 SAS 数据文件（跳过 # 注释行）。
    期望列：Q  I  dI  [sigmaQ（可选，忽略）]
    返回：q, intensity, errors（均为 numpy 数组，已去除 NaN/非正值行）
    """
    data = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                row = [float(x) for x in parts[:3]]
                data.append(row)
            except ValueError:
                continue

    arr = np.array(data)
    q = arr[:, 0]
    intensity = arr[:, 1]
    errors = arr[:, 2] if arr.shape[1] > 2 else np.sqrt(np.abs(intensity))

    # 去掉非正值
    mask = (q > 0) & (intensity > 0) & (errors > 0) & np.isfinite(q) & np.isfinite(intensity)
    return q[mask], intensity[mask], errors[mask]


# ============================================================
# 10. 后处理：计算 Rg 和体积分数
# ============================================================

def compute_rg_volume_fraction(d_dist: np.ndarray, vol_dist: np.ndarray) -> tuple:
    """
    由体积尺寸分布估算 Rg 和总体积分数（近似）。
    来源：IR2L_CalculateRg + areaXY 在 IR1R_SizesFitting 中的调用。
    """
    # 总体积（梯形积分）
    total_vol = np.trapezoid(vol_dist, d_dist) if hasattr(np, "trapezoid") else np.trapz(vol_dist, d_dist)

    # 质心直径
    if total_vol > 0:
        _trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
        d_mean = _trapz(d_dist * vol_dist, d_dist) / total_vol
        # 球形 Rg = sqrt(3/5) * R_mean
        rg = np.sqrt(3.0 / 5.0) * (d_mean / 2.0)
    else:
        d_mean, rg = np.nan, np.nan

    return rg, total_vol


# ============================================================
# 11. 绘图
# ============================================================

def plot_results(
    q_vec: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    q_fit: np.ndarray,
    fit_int: np.ndarray,
    d_dist: np.ndarray,
    vol_dist: np.ndarray,
    vol_dist_err: np.ndarray | None,
    chi2: float,
    method: str,
    background_curve: np.ndarray | None = None,
    fit_bp_range: tuple[float, float] | None = None,
    fit_bg_range: tuple[float, float] | None = None,
    fit_range: tuple[float, float] | None = None,
    output_dir: str = None,
):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # 左图：散射曲线
    ax = axes[0]
    ax.errorbar(q_vec, intensity, yerr=errors, fmt="o", ms=3,
                color="steelblue", label="Data (original)", alpha=0.7)
    ax.plot(q_fit, fit_int, "r-", lw=1.5, label=f"Fit ({method})")
    if background_curve is not None:
        ax.plot(q_vec, background_curve, "--", color="orange", lw=1.2, label="Background (B*q^-P + flat)")

    def _y_at(q0: float) -> float | None:
        if q_vec.size == 0:
            return None
        idx = int(np.argmin(np.abs(q_vec - q0)))
        y0 = float(intensity[idx])
        return y0 if np.isfinite(y0) and y0 > 0 else None

    def _mark_pair(range_pair: tuple[float, float] | None, marker: str, color: str, label: str):
        if not range_pair:
            return
        q1, q2 = float(range_pair[0]), float(range_pair[1])
        y1, y2 = _y_at(q1), _y_at(q2)
        if y1 is not None:
            ax.plot(q1, y1, marker=marker, color=color, markersize=8, linestyle="None", label=label)
            label = "_nolegend_"
        if y2 is not None:
            ax.plot(q2, y2, marker=marker, color=color, markersize=8, linestyle="None", label=label)

    # 紫色倒三角：q_power/低Q幂律拟合区域端点
    _mark_pair(fit_bp_range, marker="v", color="purple", label="q_power region")
    # 黑色方块：flat background 拟合区域端点
    _mark_pair(fit_bg_range, marker="s", color="black", label="flat background region")
    # 红色方块：最终曲线拟合区间端点
    _mark_pair(fit_range, marker="s", color="red", label="fit interval")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(r"$Q\ (\AA^{-1})$")
    ax.set_ylabel(r"$I(Q)\ (\mathrm{cm}^{-1})$")
    ax.set_title(f"Scattering Curve (χ²={chi2:.2f})")
    ax.legend()

    # 右图：尺寸分布
    ax = axes[1]
    if vol_dist_err is not None:
        ax.errorbar(
            d_dist,
            vol_dist,
            yerr=vol_dist_err,
            fmt="g-o",
            ms=4,
            lw=1.2,
            capsize=2,
            alpha=0.9,
        )
    else:
        ax.plot(d_dist, vol_dist, "g-o", ms=4, lw=1.5)
    ax.set_xlabel(r"Diameter $D\ (\AA)$")
    ax.set_ylabel(r"$dV/dD$")
    ax.set_title("Volume Size Distribution")
    ax.set_xscale("log")

    plt.tight_layout()
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        save_path = os.path.join(output_dir, f"size_distribution_{method}.png")
        plt.savefig(save_path, dpi=150)
        print(f"[INFO] Plot saved to: {save_path}")
    # plt.show()


# ============================================================
# 12. 保存结果
# ============================================================

def save_results(
    output_dir: str,
    q_vec: np.ndarray,
    intensity: np.ndarray,
    fit_int: np.ndarray,
    d_dist: np.ndarray,
    vol_dist: np.ndarray,
    vol_dist_err: np.ndarray | None,
    method: str,
    chi2: float,
    rg: float,
    total_vol: float,
):
    os.makedirs(output_dir, exist_ok=True)

    # 拟合曲线
    fit_path = os.path.join(output_dir, f"fit_intensity_{method}.dat")
    np.savetxt(fit_path,
               np.column_stack([q_vec, intensity, fit_int]),
               header="Q(1/A)  I_data(1/cm)  I_fit(1/cm)",
               fmt="%.6e")

    # 尺寸分布
    dist_path = os.path.join(output_dir, f"size_distribution_{method}.dat")
    if vol_dist_err is not None:
        np.savetxt(
            dist_path,
            np.column_stack([d_dist, vol_dist, vol_dist_err]),
            header=(
                f"# Method: {method}  chi2={chi2:.4f}  Rg={rg:.2f}A  TotalVol={total_vol:.4e}\n"
                "D(A)  dV/dD  dV/dD_err"
            ),
            fmt="%.6e",
        )
    else:
        np.savetxt(
            dist_path,
            np.column_stack([d_dist, vol_dist]),
            header=(
                f"# Method: {method}  chi2={chi2:.4f}  Rg={rg:.2f}A  TotalVol={total_vol:.4e}\n"
                "D(A)  dV/dD"
            ),
            fmt="%.6e",
        )

    print(f"[INFO] Fit saved  : {fit_path}")
    print(f"[INFO] Dist saved : {dist_path}")


def _slice_range(
    q: np.ndarray,
    y: np.ndarray,
    qmin: float | None,
    qmax: float | None,
    positive_only: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    lo = float(np.nanmin(q)) if qmin is None else float(qmin)
    hi = float(np.nanmax(q)) if qmax is None else float(qmax)
    if lo > hi:
        lo, hi = hi, lo

    mask = np.isfinite(q) & np.isfinite(y) & (q >= lo) & (q <= hi)
    if positive_only:
        mask &= y > 0
    return q[mask], y[mask]


def _fit_low_q_powerlaw(
    q: np.ndarray,
    intensity: np.ndarray,
    qmin: float | None,
    qmax: float | None,
    fixed_power: float | None = None,
) -> tuple[float, float]:
    q_fit, i_fit = _slice_range(q, intensity, qmin, qmax, positive_only=True)
    if q_fit.size < 2:
        raise ValueError("低Q幂律拟合点数不足（至少需要2个点）。")

    # IR1R_FitPowerLawBackground 在 log-log 空间直线拟合
    log_q = np.log10(q_fit)
    log_i = np.log10(i_fit)

    if fixed_power is None:
        slope, intercept = np.polyfit(log_q, log_i, deg=1)
        power = abs(float(slope))
        scaling = float(10.0 ** intercept)
    else:
        power = abs(float(fixed_power))
        intercept = float(np.mean(log_i + power * log_q))
        scaling = float(10.0 ** intercept)

    return scaling, power


def _fit_flat_background(
    q: np.ndarray,
    intensity: np.ndarray,
    qmin: float | None,
    qmax: float | None,
) -> float:
    _, i_fit = _slice_range(q, intensity, qmin, qmax, positive_only=False)
    if i_fit.size < 1:
        raise ValueError("平坦背景拟合点数不足（至少需要1个点）。")
    return float(np.mean(i_fit))


def _build_background_wave(
    q: np.ndarray,
    low_q_scaling_factor: float,
    low_q_power_law_slope: float,
    background: float,
) -> np.ndarray:
    return low_q_scaling_factor * np.power(q, -abs(low_q_power_law_slope)) + background


def _prepare_intensity_for_size_fit(
    q: np.ndarray,
    intensity: np.ndarray,
    background: float,
    low_q_scaling_factor: float,
    low_q_power_law_slope: float,
    fit_low_q_b: bool,
    fit_low_q_bp: bool,
    fit_flat_background: bool,
    fit_bp_qmin: float | None,
    fit_bp_qmax: float | None,
    fit_bg_qmin: float | None,
    fit_bg_qmax: float | None,
) -> tuple[np.ndarray, dict]:
    b = float(low_q_scaling_factor)
    p = abs(float(low_q_power_law_slope))
    bg = float(background)

    # 对齐 IR1R_FitBPBackOnGraph: 先低Q(B/B+P)，再平坦背景
    if fit_low_q_b or fit_low_q_bp:
        if fit_low_q_bp:
            b, p = _fit_low_q_powerlaw(q, intensity, fit_bp_qmin, fit_bp_qmax, fixed_power=None)
        else:
            b, p = _fit_low_q_powerlaw(q, intensity, fit_bp_qmin, fit_bp_qmax, fixed_power=p)

    if fit_flat_background:
        bg = _fit_flat_background(q, intensity, fit_bg_qmin, fit_bg_qmax)

    background_wave = _build_background_wave(q, b, p, bg)
    corrected_intensity = intensity - background_wave
    meta = {
        "low_q_scaling_factor": float(b),
        "low_q_power_law_slope": float(p),
        "background": float(bg),
        "fit_low_q_b": bool(fit_low_q_b),
        "fit_low_q_bp": bool(fit_low_q_bp),
        "fit_flat_background": bool(fit_flat_background),
        "fit_bp_qmin": fit_bp_qmin,
        "fit_bp_qmax": fit_bp_qmax,
        "fit_bg_qmin": fit_bg_qmin,
        "fit_bg_qmax": fit_bg_qmax,
    }
    return corrected_intensity, meta


def _solve_size_distribution_core(
    q: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    d_min: float,
    d_max: float,
    n_points: int,
    log_scale: bool,
    scattering_contrast: float,
    method: str,
    e_value: float,
    sky_background: float,
    max_iter: int,
    q_power: float,
    shape: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    # 构建半径分布网格
    r_min = d_min / 2.0
    r_max = d_max / 2.0
    r_dist = make_r_distribution(r_min, r_max, n_points, log_scale)
    d_dist = r_dist * 2.0  # 直径

    print(
        f"[INFO] R distribution: {r_min:.1f} ~ {r_max:.1f} A, {n_points} points, "
        f"{'log' if log_scale else 'linear'} scale"
    )

    # 构建 G 矩阵
    print("[INFO] Building G matrix ...")
    G = build_G_matrix(q, r_dist, scattering_contrast=scattering_contrast, shape=shape)

    # 求解
    method = method.lower()
    print(f"[INFO] Solving with method: {method}")

    if method == "regularization":
        x_dist, fit_int, chi2, n_iter = solve_regularization(
            G, intensity, errors, r_dist,
            e_value=e_value, q_power=q_power,
            q_vec=q if q_power != 0 else None,
        )
        print(f"[INFO] Converged in {n_iter} iterations, χ² = {chi2:.4f}")

    elif method == "nnls":
        x_dist, fit_int, chi2 = solve_nnls(
            G, intensity, errors, r_dist,
            q_power=q_power,
            q_vec=q if q_power != 0 else None,
        )
        print(f"[INFO] NNLS done, χ² = {chi2:.4f}")

    elif method == "maxent":
        x_dist, fit_int, chi2, n_iter = solve_maxent(
            G, intensity, errors, r_dist,
            sky_background=sky_background,
            max_iter=max_iter,
            stability=e_value,
            q_power=q_power,
            q_vec=q if q_power != 0 else None,
        )
        print(f"[INFO] MaxEnt done in {n_iter} iterations, χ² = {chi2:.4f}")

    else:
        raise ValueError(f"Unknown method '{method}'. Choose: regularization, nnls, maxent")

    return d_dist, x_dist, fit_int, chi2


def run_size_distribution_with_uncertainty(
    data_file: str,
    d_min: float = 10.0,
    d_max: float = 1000.0,
    n_points: int = 100,
    log_scale: bool = True,
    scattering_contrast: float = 1.0,
    background: float = 0.0,
    method: str = "regularization",
    e_value: float = 0.1,
    sky_background: float = 0.01,
    max_iter: int = 100,
    q_power: float = 0.0,
    shape: str = "sphere",
    low_q_scaling_factor: float = 0.0,
    low_q_power_law_slope: float = 4.0,
    fit_low_q_b: bool = False,
    fit_low_q_bp: bool = False,
    fit_flat_background: bool = False,
    fit_bp_qmin: float | None = None,
    fit_bp_qmax: float | None = None,
    fit_bg_qmin: float | None = None,
    fit_bg_qmax: float | None = None,
    uncertainty_runs: int = 10,
    output_dir: str = None,
    plot: bool = True,
) -> tuple:
    """
    基于 IR1R_SizesEstimateErrors 的思路：对强度做多次噪声扰动并重复拟合，
    以每个粒径点分布的标准差作为 dV/dD 的不确定度。
    """
    print(f"[INFO] Loading data: {data_file}")
    q, intensity, errors = load_sas_data(data_file)
    print(f"[INFO] Data points: {len(q)}, Q range: {q[0]:.4f} ~ {q[-1]:.4f} A⁻¹")

    q_raw = q.copy()
    intensity_raw = intensity.copy()
    errors_raw = errors.copy()

    intensity, background_fit_meta = _prepare_intensity_for_size_fit(
        q=q,
        intensity=intensity,
        background=background,
        low_q_scaling_factor=low_q_scaling_factor,
        low_q_power_law_slope=low_q_power_law_slope,
        fit_low_q_b=fit_low_q_b,
        fit_low_q_bp=fit_low_q_bp,
        fit_flat_background=fit_flat_background,
        fit_bp_qmin=fit_bp_qmin,
        fit_bp_qmax=fit_bp_qmax,
        fit_bg_qmin=fit_bg_qmin,
        fit_bg_qmax=fit_bg_qmax,
    )
    if fit_low_q_b or fit_low_q_bp or fit_flat_background:
        print(
            "[INFO] Background prefit -> "
            f"B={background_fit_meta['low_q_scaling_factor']:.6g}, "
            f"P={background_fit_meta['low_q_power_law_slope']:.6g}, "
            f"flat={background_fit_meta['background']:.6g}"
        )
    mask = intensity > 0
    q, intensity, errors = q[mask], intensity[mask], errors[mask]

    # 基准拟合（用于回退与比较）
    d_dist, base_x_dist, base_fit_int, base_chi2 = _solve_size_distribution_core(
        q=q,
        intensity=intensity,
        errors=errors,
        d_min=d_min,
        d_max=d_max,
        n_points=n_points,
        log_scale=log_scale,
        scattering_contrast=scattering_contrast,
        method=method,
        e_value=e_value,
        sky_background=sky_background,
        max_iter=max_iter,
        q_power=q_power,
        shape=shape,
    )

    runs = max(1, int(uncertainty_runs))
    x_samples = []
    fit_samples = []

    for i in range(runs):
        noisy_intensity = intensity + np.random.normal(loc=0.0, scale=errors)
        # 与 Irena 图形流程一致，扰动后强度保持可计算状态
        noisy_intensity = np.where(np.isfinite(noisy_intensity), noisy_intensity, intensity)
        noisy_intensity = np.where(noisy_intensity > 0, noisy_intensity, np.finfo(float).eps)

        try:
            _, x_dist_i, fit_int_i, _ = _solve_size_distribution_core(
                q=q,
                intensity=noisy_intensity,
                errors=errors,
                d_min=d_min,
                d_max=d_max,
                n_points=n_points,
                log_scale=log_scale,
                scattering_contrast=scattering_contrast,
                method=method,
                e_value=e_value,
                sky_background=sky_background,
                max_iter=max_iter,
                q_power=q_power,
                shape=shape,
            )
            if (
                x_dist_i.shape == base_x_dist.shape
                and np.all(np.isfinite(x_dist_i))
                and fit_int_i.shape == base_fit_int.shape
            ):
                x_samples.append(x_dist_i)
                fit_samples.append(fit_int_i)
        except Exception:
            continue

    if x_samples:
        x_stack = np.vstack(x_samples)
        fit_stack = np.vstack(fit_samples)
        x_dist = np.mean(x_stack, axis=0)
        ddof = 1 if x_stack.shape[0] > 1 else 0
        x_dist_err = np.std(x_stack, axis=0, ddof=ddof)
        fit_int = np.mean(fit_stack, axis=0)
        residuals = (intensity - fit_int) / errors
        chi2 = float(np.sum(residuals ** 2))
        success_runs = int(x_stack.shape[0])
    else:
        x_dist = base_x_dist
        x_dist_err = np.full_like(base_x_dist, np.nan)
        fit_int = base_fit_int
        chi2 = float(base_chi2)
        success_runs = 0

    rg, total_vol = compute_rg_volume_fraction(d_dist, x_dist)
    print(f"[INFO] Rg ≈ {rg:.2f} A,  Total volume fraction ∝ {total_vol:.4e}")
    print(f"[INFO] Uncertainty runs: success {success_runs}/{runs}")

    if output_dir:
        save_results(
            output_dir,
            q,
            intensity,
            fit_int,
            d_dist,
            x_dist,
            x_dist_err,
            method,
            chi2,
            rg,
            total_vol,
        )

    if plot:
        background_curve = _build_background_wave(
            q_raw,
            background_fit_meta["low_q_scaling_factor"],
            background_fit_meta["low_q_power_law_slope"],
            background_fit_meta["background"],
        )
        fit_bp_range = None
        if (fit_low_q_b or fit_low_q_bp) and fit_bp_qmin is not None and fit_bp_qmax is not None:
            fit_bp_range = (float(fit_bp_qmin), float(fit_bp_qmax))
        fit_bg_range = None
        if fit_flat_background and fit_bg_qmin is not None and fit_bg_qmax is not None:
            fit_bg_range = (float(fit_bg_qmin), float(fit_bg_qmax))
        fit_range = (float(np.min(q)), float(np.max(q))) if q.size > 0 else None

        plot_results(
            q_raw,
            intensity_raw,
            errors_raw,
            q,
            fit_int,
            d_dist,
            x_dist,
            x_dist_err,
            chi2,
            method,
            background_curve=background_curve,
            fit_bp_range=fit_bp_range,
            fit_bg_range=fit_bg_range,
            fit_range=fit_range,
            output_dir=output_dir,
        )

    return q, intensity, fit_int, d_dist, x_dist, x_dist_err, chi2, success_runs, background_fit_meta


# ============================================================
# 13. 主入口
# ============================================================

def run_size_distribution(
    data_file: str,
    d_min: float = 10.0,
    d_max: float = 1000.0,
    n_points: int = 100,
    log_scale: bool = True,
    scattering_contrast: float = 1.0,
    background: float = 0.0,
    method: str = "regularization",
    e_value: float = 0.1,
    sky_background: float = 0.01,
    max_iter: int = 100,
    q_power: float = 0.0,
    shape: str = "sphere",
    low_q_scaling_factor: float = 0.0,
    low_q_power_law_slope: float = 4.0,
    fit_low_q_b: bool = False,
    fit_low_q_bp: bool = False,
    fit_flat_background: bool = False,
    fit_bp_qmin: float | None = None,
    fit_bp_qmax: float | None = None,
    fit_bg_qmin: float | None = None,
    fit_bg_qmax: float | None = None,
    output_dir: str = None,
    plot: bool = True,
):
    """
    使用Irena的 IR1_Sizes.ipf 算法计算小角散射 (SAS) 数据的尺寸分布。

    参数说明
    --------
    data_file           : SAS 数据文件路径（Q, I, dI 列格式）
    d_min, d_max        : 粒子直径搜索范围（Angstrom）
    n_points            : 半径分布点数
    log_scale           : 对数间距（True）或线性间距
    scattering_contrast : 散射对比度 Δρ²（cm⁻⁴），默认 1.0（内部用 ×1e20 缩放）
    background          : 平坦背景，从 I(Q) 中减去
    low_q_scaling_factor: 低Q幂律背景比例系数 B
    low_q_power_law_slope: 低Q幂律指数 P（背景项 B*q^-P）
    fit_low_q_b         : 在区间 [fit_bp_qmin, fit_bp_qmax] 拟合 B（固定 P）
    fit_low_q_bp        : 在区间 [fit_bp_qmin, fit_bp_qmax] 同时拟合 B 和 P
    fit_flat_background : 在区间 [fit_bg_qmin, fit_bg_qmax] 拟合平坦背景
    method              : 'regularization' | 'nnls' | 'maxent'
    e_value             : Regularization 精度参数（约 0.1）
    sky_background      : MaxEnt 初始天空背景
    max_iter            : 最大迭代次数
    q_power             : Q^N 加权指数（UseNoErrors 时用，一般为 0~4）
    shape               : 粒子形状，目前支持 'sphere'
    output_dir          : 输出目录，None 则不保存
    plot                : 是否显示结果图
    """
    print(f"[INFO] Loading data: {data_file}")
    q, intensity, errors = load_sas_data(data_file)
    print(f"[INFO] Data points: {len(q)}, Q range: {q[0]:.4f} ~ {q[-1]:.4f} A⁻¹")

    q_raw = q.copy()
    intensity_raw = intensity.copy()
    errors_raw = errors.copy()

    # 扣除背景：对齐 IR1_Sizes 中的 Low-Q 幂律+平坦背景流程
    intensity, background_fit_meta = _prepare_intensity_for_size_fit(
        q=q,
        intensity=intensity,
        background=background,
        low_q_scaling_factor=low_q_scaling_factor,
        low_q_power_law_slope=low_q_power_law_slope,
        fit_low_q_b=fit_low_q_b,
        fit_low_q_bp=fit_low_q_bp,
        fit_flat_background=fit_flat_background,
        fit_bp_qmin=fit_bp_qmin,
        fit_bp_qmax=fit_bp_qmax,
        fit_bg_qmin=fit_bg_qmin,
        fit_bg_qmax=fit_bg_qmax,
    )
    if fit_low_q_b or fit_low_q_bp or fit_flat_background:
        print(
            "[INFO] Background prefit -> "
            f"B={background_fit_meta['low_q_scaling_factor']:.6g}, "
            f"P={background_fit_meta['low_q_power_law_slope']:.6g}, "
            f"flat={background_fit_meta['background']:.6g}"
        )
    mask = intensity > 0
    q, intensity, errors = q[mask], intensity[mask], errors[mask]

    d_dist, x_dist, fit_int, chi2 = _solve_size_distribution_core(
        q=q,
        intensity=intensity,
        errors=errors,
        d_min=d_min,
        d_max=d_max,
        n_points=n_points,
        log_scale=log_scale,
        scattering_contrast=scattering_contrast,
        method=method,
        e_value=e_value,
        sky_background=sky_background,
        max_iter=max_iter,
        q_power=q_power,
        shape=shape,
    )

    # 后处理
    rg, total_vol = compute_rg_volume_fraction(d_dist, x_dist)
    print(f"[INFO] Rg ≈ {rg:.2f} A,  Total volume fraction ∝ {total_vol:.4e}")

    # 保存
    if output_dir:
        save_results(
            output_dir,
            q,
            intensity,
            fit_int,
            d_dist,
            x_dist,
            None,
            method,
            chi2,
            rg,
            total_vol,
        )

    # 绘图
    if plot:
        background_curve = _build_background_wave(
            q_raw,
            background_fit_meta["low_q_scaling_factor"],
            background_fit_meta["low_q_power_law_slope"],
            background_fit_meta["background"],
        )
        fit_bp_range = None
        if (fit_low_q_b or fit_low_q_bp) and fit_bp_qmin is not None and fit_bp_qmax is not None:
            fit_bp_range = (float(fit_bp_qmin), float(fit_bp_qmax))
        fit_bg_range = None
        if fit_flat_background and fit_bg_qmin is not None and fit_bg_qmax is not None:
            fit_bg_range = (float(fit_bg_qmin), float(fit_bg_qmax))
        fit_range = (float(np.min(q)), float(np.max(q))) if q.size > 0 else None

        plot_results(
            q_raw,
            intensity_raw,
            errors_raw,
            q,
            fit_int,
            d_dist,
            x_dist,
            None,
            chi2,
            method,
            background_curve=background_curve,
            fit_bp_range=fit_bp_range,
            fit_bg_range=fit_bg_range,
            fit_range=fit_range,
            output_dir=output_dir,
        )

    return q, intensity, fit_int, d_dist, x_dist, chi2, background_fit_meta


# ============================================================
# 14. CLI 命令行接口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="小角散射 (SAS) 尺寸分布计算（基于 Irena IR1_Sizes.ipf 算法）"
    )
    parser.add_argument("data_file", help="SAS 数据文件路径 (Q I dI 格式)")
    parser.add_argument("--dmin", type=float, default=10.0,
                        help="最小直径 (Angstrom)，默认 10")
    parser.add_argument("--dmax", type=float, default=1000.0,
                        help="最大直径 (Angstrom)，默认 1000")
    parser.add_argument("--npoints", type=int, default=100,
                        help="分布点数，默认 100")
    parser.add_argument("--linear", action="store_true",
                        help="使用线性间距（默认对数间距）")
    parser.add_argument("--contrast", type=float, default=1.0,
                        help="散射对比度 Δρ² (cm⁻⁴)，默认 1.0")
    parser.add_argument("--background", type=float, default=0.0,
                        help="平坦背景值，默认 0")
    parser.add_argument("--method", default="regularization",
                        choices=["regularization", "nnls", "maxent"],
                        help="求解方法，默认 regularization")
    parser.add_argument("--evalues", type=float, default=0.1,
                        help="正则化精度参数 e，默认 0.1")
    parser.add_argument("--sky", type=float, default=0.01,
                        help="MaxEnt 天空背景，默认 0.01")
    parser.add_argument("--maxiter", type=int, default=200,
                        help="最大迭代次数，默认 200")
    parser.add_argument("--qpower", type=float, default=0.0,
                        help="Q^N 加权指数（0=无权重），默认 0")
    parser.add_argument("--shape", default="sphere",
                        choices=["sphere"],
                        help="粒子形状，默认 sphere")
    parser.add_argument("--output", default=None,
                        help="结果输出目录")
    parser.add_argument("--no-plot", action="store_true",
                        help="不显示图形")
    return parser.parse_args()


# ============================================================
# 14. mcp 工具接口
# ============================================================

def _default_irena_fit_params() -> dict:
    return {
        "d_min": 10.0,
        "d_max": 1000.0,
        "n_points": 100,
        "log_scale": True,
        "scattering_contrast": 1.0,
        "background": 0.0,
        "method": "regularization",
        "e_value": 0.1,
        "sky_background": 0.01,
        "max_iter": 100,
        "q_power": 0.0,
        "shape": "sphere",
        "low_q_scaling_factor": 0.0,
        "low_q_power_law_slope": 4.0,
        "fit_low_q_b": False,
        "fit_low_q_bp": False,
        "fit_flat_background": False,
        "fit_bp_qmin": None,
        "fit_bp_qmax": None,
        "fit_bg_qmin": None,
        "fit_bg_qmax": None,
        "estimate_uncertainties": False,
        "uncertainty_runs": 10,
    }


def _merge_fit_params(base: dict, updates: dict | None) -> dict:
    merged = dict(base)
    if isinstance(updates, dict):
        merged.update(updates)
    return merged

def irena_size_distribution(
    data_path: str = None,
    json_path: str = None,
    fit_params: dict = None,
    user_id: str = None,
    mission_name: str = "",
    task_id: int | str = 0,
) -> dict:
    """
    使用Irena的 IR1_Sizes.ipf 算法拟合小角散射 (SAS) 一维散射曲线，得到粒子的尺寸分布。

    Mission / Task 概念约定（给大模型）：
    - mission_name 表示一个完整的大任务。
    - task_id 表示该 mission 中某一次具体拟合步骤/子任务。
    - 一个 mission 往往需要多次调用本工具（多个 task）才能完成。
    - 因此，同一个 mission 的多轮调用，应持续使用同一个 mission_name。
    - 在同一个 mission 内，每一轮可使用不同 task_id（例如 0,1,2...）表示阶段推进。
    - 继续轮询同一个未完成 task 时，必须同时传入相同 mission_name 和 task_id。
    - 开始新的子任务时，可在同一 mission_name 下使用新的 task_id。

    Args:输入时需要提供的参数
        data_path: 小角散射数据文件的绝对路径。
        json_path: 包含拟合参数结果的文件绝对路径。初始化时和每次model改变时应该没有拟合结果 ，在进行多轮调用时，由前一次工具输出决定。
        fit_params: 决定本轮拟合的参数。修改value参数，变化初始值。未出现在 fit_params 中的参数将使用默认值。具体参数说明如下：
            {
            d_min: float = 10.0,                 # 粒子直径搜索范围的最小值，单位为 Angstrom。
            d_max: float = 1000.0,               # 粒子直径搜索范围的最大值，单位为 Angstrom。
            n_points: int = 100,                 # 半径分布点数，即拟合结果中尺寸分布的离散点数量。
            scattering_contrast: float = 1.0,    # 散射对比度 Δρ²，单位为 cm⁻⁴。默认值 1.0，内部计算时会乘以 1e20 进行缩放。
            background: float = 0.0,             # 平坦背景值，从 I(Q) 中扣除，单位与 I(Q) 相同（通常为 1/cm）。
            method: str = "regularization",      # 拟合方法，支持 "regularization"（正则化）和 "nnls"（非负最小二乘）和 "maxent"（最大熵）。默认 "regularization"。
            e_value: float = 0.1,                # 正则化精度参数 e，默认 0.1
            sky_background: float = 0.01,        # MaxEnt 天空背景，默认 0.01
            max_iter: int = 100,                 # 最大迭代次数，默认 100
            q_power: float = 0.0,                # Q^N 加权指数（0=无权重），默认 0
            shape: str = "sphere",               # 粒子形状，默认 sphere
            "low_q_scaling_factor": 0.0,         # 低Q幂律背景比例系数 B，背景项形式为 B*q^-P
            "low_q_power_law_slope": 4.0,        # 低Q幂律指数 P，背景项形式为 B*q^-P
            "fit_low_q_b": False,                # 是否在区间 [fit_bp_qmin, fit_bp_qmax] 拟合 B（固定 P），默认 False
            "fit_low_q_bp": False,               # 是否在区间 [fit_bp_qmin, fit_bp_qmax] 拟合 B 和 P，默认 False
            "fit_flat_background": False,        # 是否拟合平坦背景，默认 False
            "fit_bp_qmin": None,                 # 幂律背景拟合区间的最小 q 值
            "fit_bp_qmax": None,                 # 幂律背景拟合区间的最大 q 值
            "fit_bg_qmin": None,                 # 平坦背景拟合区间的最小 q 值
            "fit_bg_qmax": None,                 # 平坦背景拟合区间的最大 q 值
            estimate_uncertainties: bool = False, # 是否进行不确定度估计，默认 False
            uncertainty_runs: int = 10,          # 进行不确定度估计时的扰动拟合次数，默认 10
            }
        user_id: 用户 ID，用于输出目录组织。
        mission_name: mission 名称（必填，建议全程保持不变）；用于标识同一个大任务。
        task_id: 任务轮次索引/标识。初始化和每次model改变时，建议设置为0；
                 使用相同model继续拟合时，按上次返回的 task_id+1。

    Returns:
    该函数作为 ProcessPollingMcpToolBase 的业务函数，返回：
    {
        'description': '由工具irena_size_distribution执行拟合后返回的结果。',
        'status': 'success' | 'failed',
        'content': {
            'data_path': 原始数据路径,
            'json_path': 参数文件路径,
            'model': 使用的模型名,
            'fit_params': 拟合后的参数,
            'chisquare': 最终拟合 χ²,
            'plot_path': 拟合图路径,
            'plot_data_path': 拟合曲线导出数据路径,
            'output_dir': 输出目录,
            'mission_name': 任务名,
            'task_id': 拟合轮次索引
        } 或字符串（失败时）
    }

    """
    try:
        print(
            f"irena_size_distribution called with data_path={data_path}, json_path={json_path}, "
            f"fit_params={fit_params}, user_id={user_id}, mission_name={mission_name}, task_id={task_id}"
        )

        user_key = user_id or "default_user"
        mission_key = mission_name or "default_mission"
        output_dir = TMP_DIR / user_key / mission_key
        output_dir.mkdir(parents=True, exist_ok=True)

        if not data_path or not os.path.exists(data_path):
            return {
                "description": "由工具irena_size_distribution执行拟合后返回的结果。",
                "status": "failed",
                "content": f"数据文件不存在: {data_path}",
            }

        if not mission_name:
            return {
                "description": "由工具irena_size_distribution执行拟合后返回的结果。",
                "status": "failed",
                "content": "缺少必填参数 mission_name。请保持 mission_name 在同一任务轮询中一致。",
            }

        default_params = _default_irena_fit_params()
        is_init_run = not json_path or not os.path.exists(json_path)

        if is_init_run:
            base_params = dict(default_params)
            json_path = str(output_dir / "init_size_distribution_params.json")
            with open(json_path, "w") as f:
                json.dump(base_params, f, indent=4)
            print(f"初始参数已生成并保存: {json_path}")
        else:
            with open(json_path, "r") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                loaded = {}
            base_params = _merge_fit_params(default_params, loaded)

        params = _merge_fit_params(base_params, fit_params)
        method = str(params.get("method", "regularization")).lower()
        shape = str(params.get("shape", "sphere")).lower()

        if method not in {"regularization", "nnls", "maxent"}:
            return {
                "description": "由工具irena_size_distribution执行拟合后返回的结果。",
                "status": "failed",
                "content": f"不支持的 method: {method}。支持值为 regularization、nnls、maxent。",
            }

        if shape != "sphere":
            return {
                "description": "由工具irena_size_distribution执行拟合后返回的结果。",
                "status": "failed",
                "content": f"不支持的 shape: {shape}。当前仅支持 sphere。",
            }
        
        estimate_uncertainties = bool(params.get("estimate_uncertainties", False))
        uncertainty_runs = int(params.get("uncertainty_runs", 10))
        fit_low_q_b = bool(params.get("fit_low_q_b", False))
        fit_low_q_bp = bool(params.get("fit_low_q_bp", False))
        fit_flat_background = bool(params.get("fit_flat_background", False))
        if fit_low_q_b and fit_low_q_bp:
            # 与 Irena 面板行为一致：同时启用时优先 B+P
            fit_low_q_b = False
            fit_low_q_bp = True

        common_kwargs = dict(
            data_file=data_path,
            d_min=float(params.get("d_min", 10.0)),
            d_max=float(params.get("d_max", 1000.0)),
            n_points=int(params.get("n_points", 100)),
            log_scale=bool(params.get("log_scale", True)),
            scattering_contrast=float(params.get("scattering_contrast", 1.0)),
            background=float(params.get("background", 0.0)),
            method=method,
            e_value=float(params.get("e_value", 0.1)),
            sky_background=float(params.get("sky_background", 0.01)),
            max_iter=int(params.get("max_iter", 100)),
            q_power=float(params.get("q_power", 0.0)),
            shape=shape,
            low_q_scaling_factor=float(params.get("low_q_scaling_factor", 0.0)),
            low_q_power_law_slope=float(params.get("low_q_power_law_slope", 4.0)),
            fit_low_q_b=fit_low_q_b,
            fit_low_q_bp=fit_low_q_bp,
            fit_flat_background=fit_flat_background,
            fit_bp_qmin=(None if params.get("fit_bp_qmin") is None else float(params.get("fit_bp_qmin"))),
            fit_bp_qmax=(None if params.get("fit_bp_qmax") is None else float(params.get("fit_bp_qmax"))),
            fit_bg_qmin=(None if params.get("fit_bg_qmin") is None else float(params.get("fit_bg_qmin"))),
            fit_bg_qmax=(None if params.get("fit_bg_qmax") is None else float(params.get("fit_bg_qmax"))),
            output_dir=str(output_dir),
            plot=True,
        )

        if estimate_uncertainties:
            q, intensity, fit_int, d_dist, x_dist, x_dist_err, chi2, success_runs, background_fit_meta = run_size_distribution_with_uncertainty(
                uncertainty_runs=uncertainty_runs,
                **common_kwargs,
            )
            params["estimate_uncertainties"] = True
            params["uncertainty_runs"] = uncertainty_runs
            params["uncertainty_success_runs"] = int(success_runs)
            params["distribution_error_available"] = bool(success_runs > 0)
        else:
            q, intensity, fit_int, d_dist, x_dist, chi2, background_fit_meta = run_size_distribution(**common_kwargs)
            x_dist_err = None
            params["estimate_uncertainties"] = False

        data_stem = Path(data_path).stem
        fit_param_json_path = output_dir / f"{data_stem}_irena_fit_{task_id}.json"
        params["method"] = method
        params["shape"] = shape
        params["chisquare"] = float(chi2)
        params["n_q_points"] = int(len(q))
        params["n_distribution_points"] = int(len(d_dist))
        params["low_q_scaling_factor"] = float(background_fit_meta.get("low_q_scaling_factor", params.get("low_q_scaling_factor", 0.0)))
        params["low_q_power_law_slope"] = float(background_fit_meta.get("low_q_power_law_slope", params.get("low_q_power_law_slope", 4.0)))
        params["background"] = float(background_fit_meta.get("background", params.get("background", 0.0)))
        if x_dist_err is not None:
            params["distribution_error_mean"] = float(np.nanmean(x_dist_err))
            params["distribution_error_max"] = float(np.nanmax(x_dist_err))
        with open(fit_param_json_path, "w") as f:
            json.dump(params, f, indent=4)

        plot_path = output_dir / f"size_distribution_{method}.png"
        plot_data_path = output_dir / f"fit_intensity_{method}.dat"
        distribution_data_path = output_dir / f"size_distribution_{method}.dat"

        if is_init_run:
            return {
                "description": f"irena_size_distribution工具已成功生成初始化参数文件: {json_path}。",
                "status": "success",
                "content": {
                    "data_path": data_path,
                    "json_path": str(fit_param_json_path),
                    "model": shape,
                    "fit_params": params,
                    "chisquare": float(chi2),
                    "plot_path": str(plot_path),
                    "plot_data_path": str(plot_data_path),
                    "size_distribution_path": str(distribution_data_path),
                    "output_dir": str(output_dir),
                    "mission_name": mission_name,
                    "task_id": task_id,
                },
            }

        return {
            "description": "由工具irena_size_distribution执行拟合后返回的结果。",
            "status": "success",
            "content": {
                "data_path": data_path,
                "json_path": str(fit_param_json_path),
                "model": shape,
                "fit_params": params,
                "chisquare": float(chi2),
                "plot_path": str(plot_path),
                "plot_data_path": str(plot_data_path),
                "size_distribution_path": str(distribution_data_path),
                "output_dir": str(output_dir),
                "mission_name": mission_name,
                "task_id": task_id,
            },
        }
    except Exception:
        return {
            "description": "由工具irena_size_distribution执行拟合后返回的结果。",
            "status": "failed",
            "content": traceback.format_exc(),
        }

refine_tools_runner = ProcessPollingMcpToolBase(
    timeout_sec=1800.0,
    poll_interval=1.0,
    target_func=irena_size_distribution,
    # param_mapping auto-derived from irena_size_distribution's signature
)

mcp=FastMCP(
    name="IrenaTools",
    instructions=(
        "A tool for refining small-angle neutron scattering (SANS) data using Irena core function. "
        "Important calling rule: mission_name is a high-level mission identifier and can stay the same "
        "across multiple tool calls; task_id identifies a sub-task/step inside that mission. "
        "Use the same mission_name for the same overall user goal, and advance task_id per step."
    ),
    port = 42506, # for transport = "sse" to start a server-sent events (SSE) server
    )

refine_tools_runner.register_mcp_tool(mcp, name="irena_size_distribution")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # ----------------------------------------------------------------
        # 本地测试入口：无需启动 agent，直接调用 irena_size_distribution 验证流程
        # 用法：python 0Irena_fit.py test
        # ----------------------------------------------------------------
        import tempfile

        _DATA_PATH = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "Example Data",
            "Test data.dat",
        )
        _OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "irena_test_output")
        os.makedirs(_OUTPUT_DIR, exist_ok=True)

        print("\n========== 测试1: 初始化参数并 regularization 拟合 ==========")
        res1 = irena_size_distribution(
            data_path=_DATA_PATH,
            json_path=None,
            fit_params={
                "method": "regularization",
                "d_min": 20.0,
                "d_max": 5000.0,
                "n_points": 120,
                "q_power": 4.0,
            },
            user_id="test_user",
            mission_name="irena_test_local",
            task_id=0,
        )
        if res1.get("status") != "success":
            print(f"[FAIL] {res1.get('content')}")
        else:
            c1 = res1.get("content", {})
            print(f"[OK] chi²={c1.get('chisquare', float('nan')):.4f}")
            print(f"     plot -> {c1.get('plot_path')}")
            print(f"     json -> {c1.get('json_path')}")

        print("\n========== 测试2: 基于上轮 json，切换为 nnls ==========")
        prev_json = (res1.get("content") or {}).get("json_path") if isinstance(res1, dict) else None
        res2 = irena_size_distribution(
            data_path=_DATA_PATH,
            json_path=prev_json,
            fit_params={
                "method": "nnls",
                "background": 0.0,
                "q_power": 0.0,
            },
            user_id="test_user",
            mission_name="irena_test_local",
            task_id=1,
        )
        if res2.get("status") != "success":
            print(f"[FAIL] {res2.get('content')}")
        else:
            c2 = res2.get("content", {})
            print(f"[OK] chi²={c2.get('chisquare', float('nan')):.4f}")
            print(f"     plot -> {c2.get('plot_path')}")
            print(f"     json -> {c2.get('json_path')}")

        print("\n========== 测试3: 非法 method 参数校验 ==========")
        res3 = irena_size_distribution(
            data_path=_DATA_PATH,
            json_path=prev_json,
            fit_params={"method": "unknown_method"},
            user_id="test_user",
            mission_name="irena_test_local",
            task_id=2,
        )
        if res3.get("status") == "failed":
            print(f"[OK] 参数校验生效: {res3.get('content')}")
        else:
            print(f"[FAIL] 预期失败但返回成功: {res3}")

    elif len(sys.argv) > 1 and sys.argv[1] == "cli":
        # 兼容命令行直接拟合：python 0Irena_fit.py cli <data_file> ...
        sys.argv.pop(1)
        args = parse_args()
        run_size_distribution(
            data_file=args.data_file,
            d_min=args.dmin,
            d_max=args.dmax,
            n_points=args.npoints,
            log_scale=not args.linear,
            scattering_contrast=args.contrast,
            background=args.background,
            method=args.method,
            e_value=args.evalues,
            sky_background=args.sky,
            max_iter=args.maxiter,
            q_power=args.qpower,
            shape=args.shape,
            output_dir=args.output,
            plot=not args.no_plot,
        )
    else:
        # 默认行为与 sasview 工具保持一致：启动 MCP SSE 服务
        mcp.run(transport="sse")
