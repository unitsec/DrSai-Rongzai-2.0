"""detector_sim.py —— 一个"虚拟探测器"（第 5、6、10 章通用）

它扮演真实设备的位置：监听样品台角度，算出"这个角度上能看到多少计数"，
然后把结果写进 PV。于是你的扫描代码可以完全不用知道它是假的。

    Counts(θ) = 本底 + 峰高 · exp(−½((θ−θ₀)/σ)²) · (1 + 噪声)

这就是中子/ X 射线衍射峰的穷人版模型：一个高斯峰 + 本底 + 统计噪声。
真实谱仪的峰来自晶体结构，这里的峰来自数学，但**取数流程一模一样**。

用法：
    python detector_sim.py --prefix LAB: --peak 20 --height 800 --width 1.2
"""


from __future__ import annotations

import argparse
import math
import random
import time

from pv_utils import PV, PVError


def counts_model(theta: float, peak: float, height: float, width: float,
                 background: float, noise: float) -> float:
    gaussian = height * math.exp(-0.5 * ((theta - peak) / width) ** 2)
    return max(0.0, background + gaussian * (1.0 + random.gauss(0.0, noise)))


def main() -> int:
    ap = argparse.ArgumentParser(description="虚拟探测器：角度 -> 计数率")
    ap.add_argument("--prefix", default="LAB:")
    ap.add_argument("--axis-readback", default="ThetaRB")
    ap.add_argument("--counts-pv", default="Counts")
    ap.add_argument("--peak", type=float, default=20.0, help="峰位（度）")
    ap.add_argument("--height", type=float, default=800.0, help="峰高（计数/秒）")
    ap.add_argument("--width", type=float, default=1.2, help="高斯宽度 sigma（度）")
    ap.add_argument("--background", type=float, default=20.0)
    ap.add_argument("--noise", type=float, default=0.03, help="相对噪声（0.03 = 3%%）")
    ap.add_argument("--period", type=float, default=0.1, help="更新周期（秒）")
    args = ap.parse_args()

    try:
        theta = PV(f"{args.prefix}{args.axis_readback}")
        counts = PV(f"{args.prefix}{args.counts_pv}")
    except PVError as exc:
        print(f"❌ {exc}")
        return 1

    print(f"虚拟探测器已启动：监听 {theta.name}，写 {counts.name}")
    print(f"  峰位 {args.peak}°，峰高 {args.height}，σ={args.width}°，"
          f"本底 {args.background}，更新周期 {args.period}s")
    print("  Ctrl-C 退出")

    try:
        while True:
            angle = float(theta.get())
            value = counts_model(angle, args.peak, args.height, args.width,
                                 args.background, args.noise)
            counts.put(round(value, 1), wait=False)
            time.sleep(args.period)
    except KeyboardInterrupt:
        print("\n虚拟探测器已停止")
    except PVError as exc:
        print(f"\n运行中出错：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
