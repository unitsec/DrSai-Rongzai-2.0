"""c08_bluesky_demo.py —— 用 Bluesky/ophyd 做同一件事（第 8 章）

这是"正统路线"的最小样例：把第 5 章的 `c05_scan_demo.py` 用
NSLS-II 那套框架重写一遍。**跑完之后你会立刻明白它们的对应关系**：

    ophyd.Axis   ≈ 你的 PV 封装（把一组 PV 变成"一台设备"）
    bluesky.scan ≈ 你的 run_scan()（把"一次实验"变成可中断、可复现的 plan）
    RunEngine    ≈ 你的 for 循环 + 出错处理 + 数据落盘

为什么值得迁移？因为 bluesky 的 plan 是**可中断、可暂停、可恢复**的，
并且每次 run 都带着完整元数据落盘——这在真实束线上是刚需。

安装：  pip install ophyd bluesky
用法：  python c08_bluesky_demo.py            （需要 runlab.sh start 已起好）
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from bluesky import RunEngine
from bluesky.callbacks import LiveTable
from bluesky.plans import scan
from ophyd import Component as Cpt
from ophyd import Device, EpicsSignal, EpicsSignalRO
from ophyd.status import DeviceStatus

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)


class Axis(Device):
    """一个"轴"设备：写设定值，等回读到容差内。

    注意这里的关键点（也是 ophyd 设备抽象的全部价值）：
    调用者只需要 `axis.set(20.0)` 并等待返回的 status 对象；
    "怎么写、怎么等到位、超时怎么办"都封装在设备里。
    **上层的 plan 完全不需要知道底层是 EPICS、串口还是仿真。**
    """

    setpoint = Cpt(EpicsSignal, "LAB:Theta")
    readback = Cpt(EpicsSignalRO, "LAB:ThetaRB")
    tolerance = 0.05
    move_timeout = 20.0

    def set(self, value: float) -> DeviceStatus:
        status = DeviceStatus(self)
        target = float(value)

        def _waiter() -> None:
            try:
                self.setpoint.put(target, wait=True)
                t0 = time.monotonic()
                while time.monotonic() - t0 < self.move_timeout:
                    if abs(float(self.readback.get()) - target) <= self.tolerance:
                        status.set_finished()
                        return
                    time.sleep(0.02)
                status.set_exception(
                    TimeoutError(f"轴在 {self.move_timeout}s 内没有到位"
                                 f"（目标 {target}，当前 {self.readback.get()}）"))
            except Exception as exc:  # noqa: BLE001
                status.set_exception(exc)

        threading.Thread(target=_waiter, daemon=True).start()
        return status

    def stop(self, *, success: bool = False) -> None:
        """bluesky 中断扫描时会调用它 —— 这就是"可中断"的落点。"""
        try:
            current = float(self.readback.get())
            self.setpoint.put(current, wait=False)   # 停在当前位置
        except Exception:
            pass


class Detector(Device):
    """探测器：只读的计数率。"""

    counts = Cpt(EpicsSignalRO, "LAB:Counts", kind="hinted")


def main() -> int:
    axis = Axis(name="theta", labels={"motors"})
    det = Detector(name="det", labels={"detectors"})

    RE = RunEngine({})
    # LiveTable 会在终端里打印一张实时表格（bluesky 自带的"进度条"）
    RE.subscribe(LiveTable([det]))

    print("=== 用 bluesky 扫描 theta 18°→22°，共 9 点 ===")
    RE(scan([det], axis, 18.0, 22.0, 9))

    print("\n=== 也可以让 scans 模块来（bluesky 的语法糖）===")
    from bluesky.plans import count
    RE(count([det], num=2, delay=0.5))
    print("完成。真实项目里这一步会把数据交给 databroker 存成 HDF5/NeXus。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
