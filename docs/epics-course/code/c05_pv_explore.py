"""pv_explore.py —— 一条命令看清一个 PV 的全部"自描述信息"（第 5 章）

用法：
    python pv_explore.py LAB:TempRaw LAB:Theta LAB:State
    python pv_explore.py --prefix LAB:          # 自动列出一批常见 PV

为什么这个脚本很重要？
因为"这个 PV 到底什么意思"是每次接新设备时问得最多的问题。
这条命令把答案全部打印出来：单位、量程、精度、报警限、能不能写、枚举含义。
接硬件前先跑它，能省掉一半和控制组的来回确认。
"""


from __future__ import annotations

import argparse
import sys

from pv_utils import PV, PVError


def explore(name: str, fields: list[str]) -> None:
    try:
        pv = PV(name, connect_timeout=2.0)
    except PVError as exc:
        print(f"\n❌ {name}\n   {exc}")
        return

    info = pv.info()
    print(f"\n=== {name} ===")
    print(f"  当前值   : {info['value']!r}  {info['units']}")
    print(f"  描述     : {info['description']}")
    print(f"  精度     : {info['precision']}")
    print(f"  显示范围 : {info['disp_limits']}   (HOPR/LOPR，给操作界面看的)")
    print(f"  驱动限值 : {info['drv_limits']}   (DRVL/DRVH，只有输出记录才有)")
    print(f"  报警限值 : {info['alarm_limits']}")
    print(f"  扫描方式 : {info['scan']}")
    print(f"  报警状态 : {info['severity']} / {info['status']}"
          f"   (UDF 表示这个值从来没被写过)")
    if info["enum_strs"]:
        print(f"  枚举含义 : {info['enum_strs']}")

    shown = {"DESC", "EGU", "PREC", "HOPR", "LOPR", "DRVL", "DRVH",
             "HIGH", "HIHI", "HSV", "SEVR", "STAT", "SCAN"}
    for field in fields:
        if field in shown:
            continue
        value = pv.get_field(field)
        if value is not None:
            print(f"  {field:<8} : {value}")

    print(f"  其它字段 : 任何字段都是 PV，例如 "
          f"caget {name}.EGU / {name}.PREC / {name}.SEVR / {name}.HIHI")


def main() -> int:
    ap = argparse.ArgumentParser(description="查看 EPICS PV 的自描述信息")
    ap.add_argument("pvs", nargs="*", help="PV 名，可给多个")
    ap.add_argument("--prefix", default="LAB:")
    ap.add_argument("--fields", default="DESC,EGU,PREC,HOPR,LOPR,DRVL,DRVH,HIGH,HIHI,HSV,SEVR,STAT,SCAN")
    ap.add_argument("--list", action="store_true", help="列出一组常用 PV")
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]

    if args.list or not args.pvs:
        default = ["Counter", "Wave", "TempRaw", "TempK", "SetPointTemp",
                   "SetPointTempRB", "Power", "Theta", "ThetaRB", "Counts",
                   "Shutter", "State", "Trace"]
        args.pvs = [f"{args.prefix}{name}" for name in default]

    for name in args.pvs:
        explore(name, fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
