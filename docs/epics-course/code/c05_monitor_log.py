"""monitor_log.py —— 订阅若干 PV 并把变化记成 CSV（第 5 章）

用法：
    python monitor_log.py LAB:TempRaw LAB:Power LAB:ThetaRB --seconds 20 --out data.csv

这是一个"最简可用的数据采集程序"，它已经包含了真实采集的四个要点：
  1. 用订阅而不是轮询（不会漏掉跳变）；
  2. 每行都带时间戳（EPICS 的时间戳优先，缺失时用本地时间）；
  3. 落盘用追加 + flush（程序被 Ctrl-C 打断也不丢数据）；
  4. 退出时优雅关闭订阅。

真实的束线数据采集当然用 Bluesky/databroker（第 8 章），
但你要能自己写出这一百行，才算真的懂了数据是怎么流出来的。
"""


from __future__ import annotations

import argparse
import csv
import time
from typing import Dict, List

from pv_utils import Monitor, PVError


def main() -> int:
    ap = argparse.ArgumentParser(description="订阅 PV 并记录成 CSV")
    ap.add_argument("pvs", nargs="+", help="要记录的 PV 名")
    ap.add_argument("--seconds", type=float, default=20.0, help="记录时长")
    ap.add_argument("--out", default="monitor_log.csv")
    ap.add_argument("--flush-every", type=int, default=20, help="每多少行落盘一次")
    args = ap.parse_args()

    monitors: Dict[str, Monitor] = {}
    last: Dict[str, object] = {name: None for name in args.pvs}

    def make_callback(pvname: str):
        def _cb(value):
            last[pvname] = value
        return _cb

    for name in args.pvs:
        try:
            monitors[name] = Monitor(name, callback=make_callback(name))
            print(f"✓ 已订阅 {name}")
        except PVError as exc:
            print(f"✗ {exc}")

    if not monitors:
        print("没有任何 PV 订阅成功，退出。")
        return 1

    started = time.time()
    rows: List[List[object]] = []
    with open(args.out, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["iso_time", "epoch", "pv", "value"])
        print(f"\n开始记录 {args.seconds}s -> {args.out}（Ctrl-C 可提前结束）\n")
        try:
            while time.time() - started < args.seconds:
                for name, mon in monitors.items():
                    for stamp, value in mon.drain():
                        rows.append([
                            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp))
                            + f".{int((stamp % 1) * 1e6):06d}",
                            f"{stamp:.6f}",
                            name,
                            value,
                        ])
                if len(rows) >= args.flush_every:
                    writer.writerows(rows)
                    fprintf = len(rows)
                    rows.clear()
                    fp.flush()
                    print(f"  已写入 {fprintf} 行（已扫描 {len(monitors)} 个 PV）", end="\r")
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n收到 Ctrl-C，正在收尾……")
        finally:
            if rows:
                writer.writerows(rows)
            for mon in monitors.values():
                mon.close()

    print(f"\n完成：{args.out}")
    for name, mon in monitors.items():
        print(f"  {name:<24} 收到 {mon.count} 次变化，最后值 {mon.last!r}"
              + (f"（另有 {mon.skipped} 个连接期占位事件已忽略）" if mon.skipped else ""))
    total = sum(mon.count for mon in monitors.values())
    if total == 0:
        print("\n提示：一个事件都没收到，通常不是故障，而是：\n"
              "  * 这个 PV 的值这段时间里根本没变 —— monitor 只在**变化超过死区**时推送，\n"
              "    值恒定就没有事件，它不会按固定周期发心跳；\n"
              "  * 或者它的 SCAN 是 Passive，没人写它，它就一直不动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
