"""scan_demo.py —— 一次"真正的"扫描：动轴、等到位、取数、画图（第 5 章）

用法（需要先跑 softIoc 和虚拟探测器）：
    python scan_demo.py --start 10 --stop 30 --step 0.5 --dwell 0.3

它做的事，和散裂中子源上一台谱仪做 θ-2θ 扫描在结构上**完全一样**：

    1. 写目标位置（LAB:Theta）
    2. 等实际位置到位（LAB:ThetaRB，带容差和时间上限）  ← 新手最容易漏的一步
    3. 在这一点上取数（平均 N 次，降低噪声）
    4. 记一行数据（角度, 计数, 时间戳）
    5. 全部走完 → 存 CSV、画图、找峰

把这五个步骤做对，你就已经会"驱动实验"了；
剩下的是把这些步骤接到更大的编排框架（Bluesky，第 8 章）里。
"""


from __future__ import annotations

import argparse
import csv
import math
import time
from typing import List, Tuple

import matplotlib
matplotlib.use("Agg")  # 服务器上没有显示器，必须用无界面后端
import matplotlib.pyplot as plt

from pv_utils import PV, PVError
from scan_utils import find_peak, frange


def move_and_wait(axis: PV, readback: PV, target: float, tol: float,
                  timeout: float) -> float:
    """把轴走到 target 并等它真的到位。返回到位时的实际位置。"""
    axis.put(target, wait=True)
    return readback.wait_within(target, tol=tol, timeout=timeout)


def measure(detector: PV, samples: int, interval: float) -> Tuple[float, float]:
    """在当前位置取 samples 次，返回 (平均值, 标准差)。"""
    values = [float(detector.get()) for _ in range(max(1, samples))]
    if samples > 1:
        time.sleep(interval)  # 简化版：真实实现应该用订阅 + 固定时间窗
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, math.sqrt(var)


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描演示：动轴 + 取数 + 画图 + 找峰")
    ap.add_argument("--prefix", default="LAB:")
    ap.add_argument("--axis", default="Theta")
    ap.add_argument("--readback", default="ThetaRB")
    ap.add_argument("--detector", default="Counts")
    ap.add_argument("--start", type=float, default=10.0)
    ap.add_argument("--stop", type=float, default=30.0)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--dwell", type=float, default=0.3)
    ap.add_argument("--tol", type=float, default=0.02,
                    help="到位容差：|回读-目标| 小于它就算到位")
    ap.add_argument("--move-timeout", type=float, default=15.0)
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--out-csv", default="scan.csv")
    ap.add_argument("--out-png", default="scan.png")
    args = ap.parse_args()

    try:
        axis = PV(f"{args.prefix}{args.axis}")
        readback = PV(f"{args.prefix}{args.readback}")
        detector = PV(f"{args.prefix}{args.detector}")
    except PVError as exc:
        print(f"❌ {exc}")
        return 1

    print(f"扫描 {args.axis}: {args.start} → {args.stop} 步长 {args.step}，"
          f"每点停留 {args.dwell}s")
    axis_min, axis_max = axis.output_limits(default=(args.start, args.stop))
    print(f"轴单位 {axis.info()['units']}，执行器限值 {axis_min}..{axis_max}\n")

    points: List[Tuple[float, float, float, float]] = []
    t_start = time.time()
    for target in frange(args.start, args.stop, args.step):
        reached = move_and_wait(axis, readback, target, args.tol, args.move_timeout)
        time.sleep(args.dwell)
        value, sigma = measure(detector, args.samples, 0.1)
        points.append((reached, value, sigma, time.time()))
        print(f"  目标 {target:8.3f} → 到位 {reached:8.3f}  计数 {value:8.1f}")

    duration = time.time() - t_start

    with open(args.out_csv, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["angle_deg", "counts", "sigma", "epoch", "iso_time"])
        for angle, counts, sigma, stamp in points:
            writer.writerow([f"{angle:.6f}", f"{counts:.3f}", f"{sigma:.3f}",
                             f"{stamp:.6f}",
                             time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp))])

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    peak = find_peak(list(zip(xs, ys)))

    plt.figure(figsize=(8, 5))
    plt.plot(xs, ys, "o-", ms=3, lw=1)
    if "peak_x" in peak:
        plt.axvline(peak["peak_x"], color="crimson", ls="--", lw=1,
                    label=f"peak ~ {peak['peak_x']:.3f}")
        plt.legend()
    plt.xlabel(f"{args.axis} ({axis.info()['units']})")
    plt.ylabel(f"{args.detector} ({detector.info()['units']})")
    plt.title(f"{args.axis} scan: {len(points)} points in {duration:.1f} s")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(args.out_png, dpi=120)

    print(f"\n数据 -> {args.out_csv}")
    print(f"图   -> {args.out_png}")
    if peak:
        refined = peak.get("peak_x_refined", peak["peak_x"])
        print(f"峰位 ≈ {refined:.4f} {axis.info()['units']}，"
              f"峰高 ≈ {peak['peak_y']:.1f}"
              + (f"，FWHM ≈ {peak['fwhm']:.3f}" if "fwhm" in peak else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
