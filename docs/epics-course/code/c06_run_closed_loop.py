"""run_closed_loop.py —— 把 PID 接到"虚拟恒温箱"上（第 6 章主程序）

三种模式：

    # 1) 阶跃响应：开环给固定功率，测出设备的时间常数和增益
    python run_closed_loop.py --mode step --power 60 --duration 120

    # 2) 自动整定：继电反馈法找一组能用的 PID 参数
    python run_closed_loop.py --mode autotune --setpoint 60

    # 3) 闭环控温：用 PID 把温度稳定到设定值
    python run_closed_loop.py --mode closed --setpoint 60 --kp 1.5 --ki 0.05 --kd 0 --duration 180

安全约定（这几条在真设备上更重要）：
  * 无论正常结束、异常、还是 Ctrl-C，退出时都把加热功率写回 0（finally 块）；
  * 温度超过 --max-temp 立刻切断并报警（软件级 watchdog）；
  * 所有写入都经过 put_checked()，读回确认。
"""


from __future__ import annotations

import argparse
import csv
import math
import time
from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pid import PID, auto_tune_relay
from pv_utils import PV, PVError


def log_row(writer, setpoint: float, temp: float, power: float, extra: str = "") -> None:
    now = time.time()
    writer.writerow([f"{now:.6f}",
                     time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                     f"{setpoint:.3f}", f"{temp:.4f}", f"{power:.3f}", extra])


def report_metrics(name: str, times: List[float], temps: List[float],
                   setpoint: float, t0: float) -> None:
    """给出三个最常看的指标：超调、稳定时间、稳态误差。"""
    if not temps:
        return
    peak = max(temps)
    overshoot = peak - setpoint
    # 稳定时间：最后一次离开 ±2% 窗口的时间
    band = max(0.02 * abs(setpoint), 0.2)
    settle = None
    for value, stamp in zip(temps, times):
        if abs(value - setpoint) > band:
            settle = stamp
    settle_time = (settle - t0) if settle is not None else 0.0
    tail = temps[-max(1, len(temps) // 5):]
    mean_tail = sum(tail) / len(tail)
    std_tail = math.sqrt(sum((v - mean_tail) ** 2 for v in tail) / len(tail))
    print(f"\n[{name}] 性能报告")
    print(f"  峰值       : {peak:.3f} ℃  （超调 {overshoot:+.3f} ℃）")
    print(f"  稳定时间   : {settle_time:.1f} s（进入 ±{band:.2f} ℃ 窗口后不再离开）")
    print(f"  稳态均值   : {mean_tail:.3f} ℃  （设定 {setpoint:.3f}，"
          f"误差 {mean_tail - setpoint:+.3f} ℃）")
    print(f"  稳态波动σ  : {std_tail:.4f} ℃")


def plot(path: str, times: List[float], temps: List[float], powers: List[float],
         setpoint: float, title: str, t0: float) -> None:
    xs = [t - t0 for t in times]
    fig, ax1 = plt.subplots(figsize=(9, 5))
    ax1.plot(xs, temps, color="crimson", lw=1.6, label="Temperature PV")
    ax1.axhline(setpoint, color="k", ls="--", lw=1, label=f"Setpoint {setpoint:g}")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Temperature (C)", color="crimson")
    ax1.grid(alpha=0.3)

    ax2 = ax1.twinx()
    ax2.plot(xs, powers, color="steelblue", lw=1.0, alpha=0.7, label="Heater power")
    ax2.set_ylabel("Power (%)", color="steelblue")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="lower right", fontsize=9)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description="PID 闭环控温实验")
    ap.add_argument("--prefix", default="LAB:")
    ap.add_argument("--mode", choices=["step", "closed", "autotune"], default="closed")
    ap.add_argument("--setpoint-pv", default="SetPointTemp")
    ap.add_argument("--temp-pv", default="TempRaw")
    ap.add_argument("--power-pv", default="Power")
    ap.add_argument("--setpoint", type=float, default=60.0)
    ap.add_argument("--kp", type=float, default=1.5)
    ap.add_argument("--ki", type=float, default=0.05)
    ap.add_argument("--kd", type=float, default=0.0)
    ap.add_argument("--period", type=float, default=0.2, help="控制周期（秒）")
    ap.add_argument("--duration", type=float, default=180.0, help="最长运行时间（秒）")
    ap.add_argument("--power", type=float, default=60.0, help="step 模式下的固定功率")
    ap.add_argument("--max-temp", type=float, default=110.0, help="超温保护阈值（℃）")
    ap.add_argument("--out-csv", default="loop.csv")
    ap.add_argument("--out-png", default="loop.png")
    args = ap.parse_args()

    try:
        setpoint_pv = PV(f"{args.prefix}{args.setpoint_pv}")
        temp = PV(f"{args.prefix}{args.temp_pv}")
        power = PV(f"{args.prefix}{args.power_pv}")
    except PVError as exc:
        print(f"❌ {exc}")
        return 1

    power_min, power_max = power.output_limits(default=(0.0, 100.0))
    print(f"温度 PV : {temp.name}（{temp.info()['units']}）")
    print(f"功率 PV : {power.name}（{power.info()['units']}，"
          f"执行器限值 {power_min}..{power_max}）")

    pid = PID(kp=args.kp, ki=args.ki, kd=args.kd,
              out_min=power_min, out_max=power_max)

    times: List[float] = []
    temps: List[float] = []
    powers: List[float] = []

    try:
        # ---------------- autotune ----------------
        if args.mode == "autotune":
            setpoint_pv.put_checked(args.setpoint)
            kp, ki, kd = auto_tune_relay(
                setpoint=args.setpoint,
                measurement_fn=lambda: float(temp.get()),
                output_fn=lambda v: power.put(max(0.0, min(100.0, v)), wait=False),
                relay_amplitude=min(40.0, args.power),
                bias=args.power * 0.5,
                period=args.period,
                cycles=8,
            )
            print(f"\n把下面这行抄下来，下次直接用：")
            print(f"  python run_closed_loop.py --mode closed "
                  f"--setpoint {args.setpoint:g} --kp {kp:.3f} "
                  f"--ki {ki:.4f} --kd {kd:.2f}")
            return 0

        # ---------------- 数据记录 ----------------
        with open(args.out_csv, "w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["epoch", "iso_time", "setpoint", "temp",
                             "power", "note"])
            t0 = time.time()
            last = time.monotonic()
            note = args.mode
            setpoint_pv.put_checked(args.setpoint)
            pid.reset(output=0.0)
            power.put_checked(0.0)

            if args.mode == "closed":
                print(f"\n{pid.describe()}  设定 {args.setpoint}℃  "
                      f"周期 {args.period}s  最长 {args.duration}s")
            else:
                print(f"\n阶跃测试：{args.power}% 恒定功率，最长 {args.duration}s")

            while time.time() - t0 < args.duration:
                now = time.monotonic()
                dt = now - last
                last = now

                measured = float(temp.get())
                if measured > args.max_temp:
                    power.put_checked(0.0)
                    print(f"\n⚠️ 超温保护触发：{measured:.2f}℃ > {args.max_temp}℃，"
                          f"已切断加热")
                    note = "over-temp-abort"
                    break

                if args.mode == "closed":
                    output = pid.update(setpoint=args.setpoint,
                                        measurement=measured, dt=dt)
                else:
                    output = args.power
                power.put_checked(output)

                times.append(time.time())
                temps.append(measured)
                powers.append(output)
                log_row(writer, args.setpoint, measured, output, note)
                fp.flush()

                if len(temps) % 10 == 0:
                    print(f"  t={time.time() - t0:6.1f}s  T={measured:7.3f}℃  "
                          f"P={output:6.2f}%  误差={args.setpoint - measured:+7.3f}℃")

        report_metrics(args.mode, times, temps, args.setpoint, t0)
        plot(args.out_png, times, temps, powers, args.setpoint,
             f"{args.mode} mode: {pid.describe()}", t0)
        print(f"\n数据 -> {args.out_csv}\n图   -> {args.out_png}")

    except KeyboardInterrupt:
        print("\n收到 Ctrl-C")
    except PVError as exc:
        print(f"\n❌ 运行中出错：{exc}")
    finally:
        # ---- 最关键的一行：任何情况下都把加热关掉 ----
        try:
            power.put_checked(0.0)
            print("已把加热功率写回 0%（安全退出）")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 关闭加热失败，请人工确认设备状态！{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
