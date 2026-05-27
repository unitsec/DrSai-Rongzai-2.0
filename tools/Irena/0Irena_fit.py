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
import os
import sys


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
    fit_int: np.ndarray,
    d_dist: np.ndarray,
    vol_dist: np.ndarray,
    chi2: float,
    method: str,
    output_dir: str = None,
):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # 左图：散射曲线
    ax = axes[0]
    ax.errorbar(q_vec, intensity, yerr=errors, fmt="o", ms=3,
                color="steelblue", label="Data", alpha=0.7)
    ax.plot(q_vec, fit_int, "r-", lw=1.5, label=f"Fit ({method})")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(r"$Q\ (\AA^{-1})$")
    ax.set_ylabel(r"$I(Q)\ (\mathrm{cm}^{-1})$")
    ax.set_title(f"Scattering Curve (χ²={chi2:.2f})")
    ax.legend()

    # 右图：尺寸分布
    ax = axes[1]
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
    plt.show()


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
    np.savetxt(dist_path,
               np.column_stack([d_dist, vol_dist]),
               header=f"# Method: {method}  chi2={chi2:.4f}  Rg={rg:.2f}A  TotalVol={total_vol:.4e}\n"
                      "D(A)  dV/dD",
               fmt="%.6e")

    print(f"[INFO] Fit saved  : {fit_path}")
    print(f"[INFO] Dist saved : {dist_path}")


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

    # 扣除背景
    intensity = intensity - background
    mask = intensity > 0
    q, intensity, errors = q[mask], intensity[mask], errors[mask]

    # 构建半径分布网格
    r_min = d_min / 2.0
    r_max = d_max / 2.0
    r_dist = make_r_distribution(r_min, r_max, n_points, log_scale)
    d_dist = r_dist * 2.0  # 直径

    print(f"[INFO] R distribution: {r_min:.1f} ~ {r_max:.1f} A, {n_points} points, "
          f"{'log' if log_scale else 'linear'} scale")

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

    # 后处理
    rg, total_vol = compute_rg_volume_fraction(d_dist, x_dist)
    print(f"[INFO] Rg ≈ {rg:.2f} A,  Total volume fraction ∝ {total_vol:.4e}")

    # 保存
    if output_dir:
        save_results(output_dir, q, intensity, fit_int, d_dist, x_dist,
                     method, chi2, rg, total_vol)

    # 绘图
    if plot:
        plot_results(q, intensity, errors, fit_int, d_dist, x_dist,
                     chi2, method, output_dir)

    return q, intensity, fit_int, d_dist, x_dist, chi2


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

def Irena_size_distribution(
    data_path: str = None,
    json_path: str = None,
    model: str = None,
    fit_params: dict = None,
    user_id: str = None,
    mission_name: str = "",
    task_id: int | str = 0,
) -> dict:


if __name__ == "__main__":
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
