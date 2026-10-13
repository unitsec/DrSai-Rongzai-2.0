"""thermal_plant.py —— 一个"虚拟恒温箱"（第 6 章的核心道具）

物理模型（一阶热学，教科书里最常见的那种）：

    C · dT/dt = η · P − k · (T − T_ambient)

    P   : 加热功率 0..100 %（来自 PV LAB:Power）
    T   : 样品温度（写回 PV LAB:TempRaw）
    C   : 热容（J/K）—— 越大升得越慢
    k   : 散热系数（W/K）—— 越大散热越快
    η   : 100% 功率对应的加热功率（W）

稳态时 dT/dt = 0，所以 P=100% 能达到的最高温度是：
    T_max = T_ambient + η/k
本脚本默认 η=100 W、k=1.0 W/K，环境 25℃，满功率 → 125℃。
时间常数 τ = C/k = 25 s（C=25 J/K）——比真设备快，方便你在几分钟内做完实验。

为什么第 6 章要先玩这个假的？
因为它有**真实设备的全部坏毛病**：会滞后、会被限幅、有噪声、有环境扰动，
但没有真实设备的全部风险（不会烧东西，也不会把样品烤坏）。
PID 调好了再上真硬件，是唯一正确的顺序。

用法：
    python thermal_plant.py --prefix LAB: --period 0.1
    python thermal_plant.py --ambient-drift 0.5    # 加上缓慢的环境漂移
"""


from __future__ import annotations

import argparse
import math
import random
import time

from pv_utils import PV, PVError


class ThermalPlant:
    """一阶热学模型。dt 由脚本按真实时间推进。"""

    def __init__(self, ambient: float = 25.0, capacitance: float = 25.0,
                 loss: float = 1.0, eta: float = 100.0, temp: float = 25.0):
        self.ambient = ambient
        self.capacitance = capacitance
        self.loss = loss
        self.eta = eta
        self.temp = temp

    @property
    def time_constant(self) -> float:
        return self.capacitance / self.loss

    @property
    def max_temperature(self) -> float:
        return self.ambient + self.eta / self.loss

    def step(self, power_percent: float, dt: float) -> float:
        """用显式欧拉法推进一步。power_percent 会被截到 0..100。"""
        power = max(0.0, min(100.0, float(power_percent)))
        heat_in = self.eta * power / 100.0
        heat_out = self.loss * (self.temp - self.ambient)
        self.temp += (heat_in - heat_out) / self.capacitance * dt
        return self.temp


def main() -> int:
    ap = argparse.ArgumentParser(description="虚拟恒温箱")
    ap.add_argument("--prefix", default="LAB:")
    ap.add_argument("--power-pv", default="Power")
    ap.add_argument("--temp-pv", default="TempRaw")
    ap.add_argument("--ambient", type=float, default=25.0)
    ap.add_argument("--capacitance", type=float, default=25.0, help="热容 C（J/K）")
    ap.add_argument("--loss", type=float, default=1.0, help="散热系数 k（W/K）")
    ap.add_argument("--eta", type=float, default=100.0, help="满功率加热功率（W）")
    ap.add_argument("--period", type=float, default=0.1, help="控制周期（秒）")
    ap.add_argument("--sensor-noise", type=float, default=0.05, help="传感器噪声（K）")
    ap.add_argument("--ambient-drift", type=float, default=0.0,
                    help="环境温度漂移速度（K/s），用来模拟真实实验室的干扰")
    args = ap.parse_args()

    try:
        power = PV(f"{args.prefix}{args.power_pv}")
        temp = PV(f"{args.prefix}{args.temp_pv}")
    except PVError as exc:
        print(f"❌ {exc}")
        return 1

    plant = ThermalPlant(ambient=args.ambient, capacitance=args.capacitance,
                         loss=args.loss, eta=args.eta, temp=args.ambient)

    print("虚拟恒温箱已启动")
    print(f"  房间温度 {args.ambient}℃，热容 C={args.capacitance} J/K，"
          f"散热 k={args.loss} W/K")
    print(f"  → 时间常数 τ = {plant.time_constant:.1f} s，"
          f"满功率最高能到 {plant.max_temperature:.1f}℃")
    print(f"  读 {power.name}，写 {temp.name}，周期 {args.period}s")
    print("  Ctrl-C 退出")

    last = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            dt = now - last
            last = now

            power_percent = float(power.get())
            measured = plant.step(power_percent, dt)
            measured += random.gauss(0.0, args.sensor_noise)
            temp.put(round(measured, 3), wait=False)

            if args.ambient_drift:
                plant.ambient += args.ambient_drift * dt

            time.sleep(args.period)
    except KeyboardInterrupt:
        print("\n虚拟恒温箱已停止")
    except PVError as exc:
        print(f"\n运行中出错：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
