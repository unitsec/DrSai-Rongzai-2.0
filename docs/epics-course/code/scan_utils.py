"""scan_utils.py —— 扫描和找峰的公共小工具（第 5、8、10 章共用）

这些东西看着简单，但每一条都是"实验物理"里的常识，值得单独放一处：

* frange：生成扫描点位（不用 float 累加，避免 0.1+0.2 之类的误差）；
* find_peak：从 (x, y) 数据里找峰位 —— 最高点 + 抛物线插值 + 半高宽；
* centroid：用质心法找峰（对不对称的峰更稳）；
* summarize：把一段扫描数据压缩成几句人话，方便塞进大模型的上下文。
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple


def frange(start: float, stop: float, step: float) -> List[float]:
    """从 start 到 stop（含端点）按 step 生成点，数量不对就报错。"""
    if step == 0:
        raise ValueError("步长不能为 0")
    if (stop - start) * step < 0:
        raise ValueError(f"步长方向不对：start={start}, stop={stop}, step={step}")
    count = int(round((stop - start) / step)) + 1
    return [round(start + i * step, 10) for i in range(count)]


def find_peak(points: Sequence[Tuple[float, float]]) -> Dict[str, float]:
    """找峰：返回最高点、抛物线细化的峰位、质心峰位、半高宽、峰高。

    为什么不能只取"最高的那个点"？
    因为真实扫描是离散的，峰顶往往落在两个点之间，
    取最高点会有半个步长的系统误差 —— 做科学就别省这一步。
    """
    if not points:
        return {}
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    i = max(range(len(ys)), key=lambda k: ys[k])
    result: Dict[str, float] = {
        "peak_index": float(i),
        "peak_x": xs[i],
        "peak_y": ys[i],
        "baseline": min(ys),
    }

    if 1 < len(xs):
        result["step"] = xs[1] - xs[0]

    # 抛物线插值细化峰位
    if 0 < i < len(ys) - 1:
        y0, y1, y2 = ys[i - 1], ys[i], ys[i + 1]
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            delta = 0.5 * (y0 - y2) / denom
            if -3 < delta < 3:  # 插值不该跑到邻居之外
                result["peak_x_refined"] = xs[i] + delta * (xs[1] - xs[0])
                result["peak_y_refined"] = y1 - 0.25 * (y0 - y2) * delta

    # 质心（只算高于本底的部分）
    baseline = min(ys)
    weights = [max(0.0, y - baseline) for y in ys]
    total = sum(weights)
    if total > 0:
        result["centroid_x"] = sum(x * w for x, w in zip(xs, weights)) / total
        result["area"] = total * (result.get("step", 1.0))

    # 半高宽 FWHM：以（峰高+本底）/2 为半高点，左右线性插值
    half = baseline + (ys[i] - baseline) / 2.0
    left = right = None
    for k in range(i, 0, -1):
        if ys[k - 1] <= half:
            y_a, y_b, x_a, x_b = ys[k - 1], ys[k], xs[k - 1], xs[k]
            left = x_a + (half - y_a) * (x_b - x_a) / (y_b - y_a) if y_b != y_a else x_a
            break
    for k in range(i, len(ys) - 1):
        if ys[k + 1] <= half:
            y_a, y_b, x_a, x_b = ys[k], ys[k + 1], xs[k], xs[k + 1]
            right = x_a + (half - y_a) * (x_b - x_a) / (y_b - y_a) if y_b != y_a else x_a
            break
    if left is not None and right is not None:
        result["fwhm"] = right - left
    return result


def centroid(points: Sequence[Tuple[float, float]]) -> float:
    """质心法找峰位（对分布不对称的峰比抛物线插值更稳）。"""
    peak = find_peak(points)
    return float(peak.get("centroid_x", peak.get("peak_x", float("nan"))))


def summarize_scan(points: Sequence[Tuple[float, float]], axis: str,
                   detector: str) -> str:
    """把一次扫描的结果压缩成 3~5 句人话（给大模型看的"摘要"）。

    上下文很贵：不要把 500 个数据点塞给大模型，把结论和几个关键数给它。
    """
    if not points:
        return "没有数据点。"
    peak = find_peak(points)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_refined = peak.get("peak_x_refined", peak.get("peak_x"))
    lines = [
        f"扫描范围 {min(xs):g}..{max(xs):g}，{len(points)} 个点，"
        f"步长 {peak.get('step', float('nan')):g}。",
        f"{detector} 最小 {min(ys):.1f}、最大 {max(ys):.1f}，"
        f"本底约 {peak.get('baseline', 0):.1f}。",
        f"峰位 ≈ {x_refined:.4f} {axis}"
        + (f"（抛物线细化），质心 {peak['centroid_x']:.4f}。" if "centroid_x" in peak else "。"),
        f"峰高 ≈ {peak.get('peak_y', float('nan')):.1f}"
        + (f"，半高宽 FWHM ≈ {peak['fwhm']:.4f}。" if "fwhm" in peak else "。"),
    ]
    if "fwhm" in peak and peak["fwhm"] > 0:
        lines.append(f"峰/本底 = {peak['peak_y'] / max(1e-9, peak.get('baseline', 0)):.1f}。")
    return "\n".join(lines)


def rms(values: Iterable[float]) -> float:
    seq = [float(v) for v in values]
    if not seq:
        return float("nan")
    mean = sum(seq) / len(seq)
    return math.sqrt(sum((v - mean) ** 2 for v in seq) / len(seq))
