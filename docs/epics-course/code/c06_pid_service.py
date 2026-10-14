"""c06_pid_service.py —— 常驻 PID 控制器（第 6、8 章）

为什么要有"服务"这一层？
真实实验站里，温度从来不是"某个脚本顺手调一下"，而是一个**常驻的控制器**
一直在跑：它盯着设定值和实际值，持续输出加热功率。
你的实验脚本（或者智能体）只负责"改设定值"和"看结果"。

这也是本章要建立的架构直觉：

    实验脚本 / 智能体   ──只写设定值──►  控制器（常驻）  ──写执行器──►  硬件
                                        └──读测量值 ◄──────────────┘

用法：
    python c06_pid_service.py --setpoint-pv LAB:SetPointTemp \
        --pv-pv LAB:TempRaw --out-pv LAB:Power --permit-pv LAB:HeaterEnable \
        --kp 2.0 --ki 0.08 --kd 0 --period 0.2

它做四件事：
  1. 读设定值(SP) 和 测量值(PV)，用 PID 算输出，写执行器；
  2. **联锁**：如果 permit PV 为 0（比如超温、没有运行许可），立刻输出 0；
  3. **看门狗**：超温、读不到值、连续异常 → 输出 0 并报错；
  4. 把 (时间, SP, PV, 输出) 记成 CSV，方便事后复盘和给智能体看。
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from pid import PID
from pv_utils import PV, PVError


def main() -> int:
    ap = argparse.ArgumentParser(description="常驻 PID 温度控制器")
    ap.add_argument("--setpoint-pv", default="LAB:SetPointTemp")
    ap.add_argument("--pv-pv", default="LAB:TempRaw")
    ap.add_argument("--out-pv", default="LAB:Power")
    ap.add_argument("--permit-pv", default="LAB:HeaterEnable",
                    help="联锁 PV：为 0 时强制输出 0（留空字符串可关闭此检查）")
    ap.add_argument("--kp", type=float, default=2.0)
    ap.add_argument("--ki", type=float, default=0.08)
    ap.add_argument("--kd", type=float, default=0.0)
    ap.add_argument("--period", type=float, default=0.2)
    ap.add_argument("--max-temp", type=float, default=105.0, help="硬超温保护")
    ap.add_argument("--log", default="", help="CSV 日志路径（默认 logs/pid_service.csv）")
    ap.add_argument("--duration", type=float, default=0.0, help="0 = 一直跑")
    args = ap.parse_args()

    try:
        sp_pv = PV(args.setpoint_pv)
        pv_pv = PV(args.pv_pv)
        out_pv = PV(args.out_pv)
        permit_pv = PV(args.permit_pv) if args.permit_pv else None
    except PVError as exc:
        print(f"❌ {exc}")
        return 1

    out_min, out_max = out_pv.output_limits(default=(0.0, 100.0))
    pid = PID(kp=args.kp, ki=args.ki, kd=args.kd,
              out_min=out_min, out_max=out_max)
    pid.reset(output=0.0)

    log_path = Path(args.log) if args.log else Path("logs/pid_service.csv")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(log_path, "a", newline="", encoding="utf-8")
    writer = csv.writer(fp)
    if log_path.stat().st_size == 0:
        writer.writerow(["epoch", "iso_time", "setpoint", "pv", "output",
                         "permit", "note"])

    print("常驻 PID 控制器已启动")
    print(f"  SP={sp_pv.name}  PV={pv_pv.name}  输出={out_pv.name}"
          f"  联锁={permit_pv.name if permit_pv else '（无）'}")
    print(f"  {pid.describe()}  周期 {args.period}s  日志 {log_path}")
    print("  Ctrl-C 退出（退出时会把输出写回 0）")

    started = time.monotonic()
    last = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            dt = now - last
            last = now

            setpoint = float(sp_pv.get())
            measured = float(pv_pv.get())

            note = ""
            permit = 1.0
            if permit_pv is not None:
                permit = float(permit_pv.get())

            if measured > args.max_temp:
                output = 0.0
                note = "over-temp"
            elif permit != 1.0:
                output = 0.0
                note = "interlock"
                pid.reset(output=0.0)
            else:
                output = pid.update(setpoint=setpoint, measurement=measured, dt=dt)

            out_pv.put_checked(output)

            writer.writerow([f"{time.time():.3f}",
                             time.strftime("%Y-%m-%dT%H:%M:%S"),
                             f"{setpoint:.3f}", f"{measured:.4f}",
                             f"{output:.3f}", f"{permit:.0f}", note])
            fp.flush()

            if note:
                print(f"  ⚠️ {note}：输出强制 0（PV={measured:.2f}℃, "
                      f"permit={permit:.0f}）", flush=True)

            if args.duration and time.monotonic() - started > args.duration:
                break
            time.sleep(args.period)
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C")
    except PVError as exc:
        print(f"\n❌ 运行中出错：{exc}")
    finally:
        try:
            out_pv.put_checked(0.0)
            print("已把输出写回 0（安全退出）")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 关闭输出失败，请人工确认！{exc}")
        fp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
