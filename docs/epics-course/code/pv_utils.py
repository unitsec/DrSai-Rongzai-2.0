"""pv_utils.py —— 把 EPICS PV 当成"可靠的 Python 对象"来用（第 5 章）

为什么不用裸 `epics.caget()` / `epics.PV().put()`？
因为实验控制里最常见的 bug 不是"算错了"，而是这四种：

1. PV 根本不存在 / IOC 没起来 → 裸 API 给你返回 None，你在十层之后才崩，
   而且崩的时候已经不知道是哪个 PV 出的事；
2. put 发出去了就以为设备到位了 → 必须等一下、并且读回实际值确认；
3. 读到 None 就往下算 → 得到一堆 NaN 写进数据文件；
4. 轮询死等 → 把 CPU 和 CA 连接都耗光。

本模块把"连接、超时、读回、等待、订阅"这五件事都包好，
剩下的交给你的业务代码。智能体（第 8 章）也是直接用它。

依赖：pip install pyepics
用法：
    from pv_utils import PV, PVError
    t = PV("LAB:TempRaw")
    print(t.get(), t.info()["units"])
    t.wait_within(60.0, tol=0.5, timeout=120)   # 等到真的到 60 度
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

import epics

__all__ = ["PVError", "PV", "Monitor", "wait_until", "SEVERITY"]

SEVERITY = {0: "NO_ALARM", 1: "MINOR", 2: "MAJOR", 3: "INVALID"}
STATUS = {
    0: "NO_ALARM", 1: "READ", 2: "WRITE", 3: "HIHI", 4: "HIGH", 5: "LOLO",
    6: "LOW", 7: "STATE", 8: "COS", 9: "COMM", 10: "TIMEOUT", 11: "HWLIMIT",
    12: "CALC", 13: "SCAN", 14: "LINK", 15: "SOFT", 16: "BAD_SUB", 17: "UDF",
    18: "DISABLE", 19: "SIMM", 20: "READ_ACCESS", 21: "WRITE_ACCESS",
}


class PVError(RuntimeError):
    """所有 PV 相关的可预期错误。

    特意做成一个独立异常类型：智能体（第 8 章）捕获它就能知道
    "这是设备和网络的问题，不是我推理错了"，可以直接向用户报告，
    而不是编一个假数据继续往下跑。
    """


class PV:
    """一个 EPICS PV（带超时、等待、读回语义）。

    参数
    ----
    name : PV 名，可以是 `LAB:TempRaw`，也可以是字段 `LAB:TempRaw.HIHI`。
    connect_timeout : 连接超时（秒）。连不上直接抛 PVError。
    """

    def __init__(self, name: str, connect_timeout: float = 2.0):
        self.name = name
        self._pv = epics.PV(name, connection_timeout=connect_timeout)
        self._field_cache: Dict[tuple, Any] = {}
        if not self._pv.wait_for_connection(timeout=connect_timeout):
            raise PVError(
                f"连接 PV 失败：{name}（{connect_timeout}s 超时）。"
                f"按顺序检查：① PV 名有没有拼错；② IOC 在不在跑（caget {name}）；"
                f"③ 环境变量 EPICS_CA_ADDR_LIST / EPICS_CA_AUTO_ADDR_LIST；"
                f"④ 是否被 CA Gateway 或防火墙挡住。"
            )

    # ------------------------------------------------------------------ 元数据
    @property
    def connected(self) -> bool:
        return bool(self._pv.connected)

    def get(self, timeout: float = 3.0, as_string: bool = False) -> Any:
        """读当前值。超时/读不到 → 抛 PVError（绝不悄悄返回 None）。"""
        value = self._pv.get(timeout=timeout, as_string=as_string)
        if value is None:
            raise PVError(f"读取 {self.name} 超时（{timeout}s）——设备可能离线或网络中断")
        return value

    def get_field(self, field: str, as_string: bool = False,
                  timeout: float = 1.5) -> Any:
        """读任意字段，例如 EGU / PREC / HIHI / SEVR / DRVH / SCAN。

        在 EPICS 里"字段本身就是 PV"，所以拼上名字直接读即可。
        字段不存在（比如输入记录没有 DRVH）时返回 None，不抛异常——
        因为"探测这个字段在不在"本身就是常用需求。
        """
        key = (field, as_string)
        if key not in self._field_cache:
            sub = epics.PV(f"{self.name}.{field}", connection_timeout=timeout)
            self._field_cache[key] = (sub if sub.wait_for_connection(timeout=timeout)
                                      else None)
        sub = self._field_cache[key]
        if sub is None:
            return None
        return sub.get(timeout=timeout, as_string=as_string)

    def info(self, timeout: float = 2.0) -> Dict[str, Any]:
        """一次性拿到这条记录的"自描述信息"。

        这是给智能体（第 8 章）看的最有用的一坨数据：单位、量程、精度、
        报警等级、能不能写、枚举含义。有了它，智能体就不用猜。

        ⚠️ 两个容易误解的地方：
          * disp_limits 来自 HOPR/LOPR，是"操作界面上显示的范围"，
            不等于"能写到哪里"，也不等于"设备物理上能到哪"；
          * ctrl_limits 来自 DRVL/DRVH，但**只有输出记录才有这两个字段**。
            对输入记录，CA 会把显示限值当成控制限值返回，
            所以别把 ctrl_limits 当作"输入记录的允许范围"。
            真正想确认时读 drv_limits：输入记录会是 [None, None]。
        """
        p = self._pv
        return {
            "name": self.name,
            "value": self.get(timeout=timeout),
            "units": p.units,
            "precision": p.precision,
            "disp_limits": [p.lower_disp_limit, p.upper_disp_limit],
            "ctrl_limits": [p.lower_ctrl_limit, p.upper_ctrl_limit],
            "drv_limits": [self.get_field("DRVL"), self.get_field("DRVH")],
            "alarm_limits": {"LOLO": self.get_field("LOLO"),
                             "LOW": self.get_field("LOW"),
                             "HIGH": self.get_field("HIGH"),
                             "HIHI": self.get_field("HIHI")},
            "scan": self.get_field("SCAN", as_string=True),
            "description": self.get_field("DESC"),
            "severity": SEVERITY.get(p.severity, str(p.severity)),
            "status": STATUS.get(p.status, str(p.status)),
            "timestamp": p.timestamp,
            "enum_strs": list(p.enum_strs) if p.enum_strs else [],
            "access": getattr(p, "access", None),
            "host": getattr(p, "host", None),
        }

    # -------------------------------------------------------------------- 写
    def output_limits(self, default=(-100.0, 100.0)) -> tuple:
        """执行器限值 (DRVL, DRVH)。这是"我能写到多大"的唯一可信来源。

        读不到 DRVL/DRVH（比如输出记录的限值没设）时退回到 CA 的控制限值，
        再不行就返回 default ——但请把 default 当成"危险信号"：说明这条记录的
        限值信息不完整，应该去问控制组，而不是自己拍一个数。
        """
        low, high = self.get_field("DRVL"), self.get_field("DRVH")
        if low is not None and high is not None and float(low) != float(high):
            return float(low), float(high)
        ctrl_low, ctrl_high = self._pv.lower_ctrl_limit, self._pv.upper_ctrl_limit
        if ctrl_low is not None and ctrl_high is not None and ctrl_low != ctrl_high:
            return float(ctrl_low), float(ctrl_high)
        return default

    def put(self, value: Any, wait: bool = True, timeout: float = 3.0) -> None:
        """写值。

        wait=True 表示等 IOC 完成"处理"（CA put callback）。注意：
        **put 完成 ≠ 设备到位**。对于电机、温控这类慢设备，
        必须再用 wait_within() / wait_until() 等实际值。
        """
        ret = self._pv.put(value, wait=wait, timeout=timeout)
        if ret is None:
            raise PVError(f"写入 {self.name} 失败或超时（值={value!r}）")

    def put_checked(self, value: float, timeout: float = 3.0,
                    verify_timeout: float = 0.5) -> float:
        """写一个数值并且**读回确认**（对输出记录，IOC 会把超限值截到 DRVL/DRVH）。

        返回实际生效的值。如果实际值和请求值差太多，抛 PVError。
        这是"不要相信自己的写操作"的最小实现。

        ⚠️ 读回要**重试几次**再下结论：有些实现（比如用 caproto 写的 Python IOC）
        在 put 回调返回后，内部值还要过几毫秒才更新；只采样一次就会误报
        "写入失败"，这种假故障比真故障更浪费时间。
        """
        self.put(value, wait=True, timeout=timeout)
        target = float(value)
        deadline = time.monotonic() + verify_timeout
        actual = None
        while True:
            actual = float(self.get(timeout=timeout))
            if abs(actual - target) <= max(1e-9, abs(target) * 1e-3):
                return actual
            lo = self._pv.lower_ctrl_limit
            hi = self._pv.upper_ctrl_limit
            if actual in (lo, hi):
                return actual          # 被驱动限值截断，属于"正常但没到你要的值"
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        if abs(actual - float(value)) > max(1e-9, abs(float(value)) * 1e-3):
            raise PVError(
                f"写入 {self.name}={value} 后读回 {actual}，不一致。"
                f"可能原因：记录被联锁/闭环覆盖（OMSL=closed_loop）、"
                f"设备拒绝、或值被限值截断。"
            )
        return actual

    # ------------------------------------------------------------------ 等待
    def wait_until(
        self,
        predicate: Callable[[Any], bool],
        timeout: float = 30.0,
        poll: float = 0.05,
        desc: str = "",
    ) -> Any:
        """等到 PV 的值满足条件为止；超时抛 PVError（带最后一次读到的值）。

        predicate 例：
            lambda v: v > 5
            lambda v: abs(v - 60.0) < 0.5
        """
        t0 = time.monotonic()
        last: Any = None
        while True:
            last = self.get()
            if predicate(last):
                return last
            if time.monotonic() - t0 > timeout:
                raise PVError(
                    f"等待 {self.name} 满足条件超时（{timeout}s）"
                    f"{'：' + desc if desc else ''}，最后读到 {last!r}"
                )
            time.sleep(poll)

    def wait_within(self, target: float, tol: float = 0.1, timeout: float = 30.0,
                    poll: float = 0.05) -> float:
        """等实际值进入 target ± tol —— 电机的"到位判断"就这么写。"""
        return float(self.wait_until(
            lambda v: abs(float(v) - float(target)) <= float(tol),
            timeout=timeout, poll=poll,
            desc=f"目标 {target}±{tol}",
        ))

    # ------------------------------------------------------------------ 订阅
    def monitor(self, callback: Optional[Callable[[Any], None]] = None,
                history: int = 1000, connect_timeout: float = 2.0) -> "Monitor":
        """订阅这个 PV 的变化（EPICS 的 monitor 机制）。

        返回的 Monitor 会缓存最近的值，可以直接 .wait_for(...) 用。
        """
        return Monitor(self.name, callback=callback, history=history,
                       connect_timeout=connect_timeout)

    def __repr__(self) -> str:
        return f"<PV {self.name} = {self._pv.value!r} ({self._pv.units})>"


class Monitor:
    """PV 订阅：值一变就回调，同时缓存历史值。

    关键点：**不要用 while True + sleep 去轮询**。
    轮询会漏掉瞬时跳变（比如限位开关闪一下），还浪费网络。
    订阅是"值变了才推给你"。

    ⚠️ 一个真实踩过的坑：PV 刚连上、第一个 monitor 事件还没到时，
    pyepics 会用一个"占位值"（value=0.0，timestamp=EPICS 纪元 1990-01-01）
    回调一次。如果不滤掉，你的数据文件第一行永远是
    "1990-01-01 00:00:00, 0.0" ——看起来像设备瞬间掉到 0 度，
    实际什么都没发生。我们在 _on_change 里把这个占位事件滤掉了。
    """

    EPICS_EPOCH = 631152000.0  # EPICS 时间戳的 0 点：1990-01-01T00:00:00Z

    def __init__(self, name: str, callback: Optional[Callable[[Any], None]] = None,
                 history: int = 1000, connect_timeout: float = 2.0):
        self.name = name
        self._pv = epics.PV(name, connection_timeout=connect_timeout)
        if not self._pv.wait_for_connection(timeout=connect_timeout):
            raise PVError(f"订阅失败：连接不上 {name}")
        self.values: Deque[Any] = deque(maxlen=history)
        self.timestamps: Deque[float] = deque(maxlen=history)
        self.count = 0
        self.skipped = 0
        self._last_value: Any = None
        self._callback = callback
        self._lock = threading.Lock()
        self._pv.add_callback(self._on_change)

    def _on_change(self, value=None, timestamp=None, **kwargs) -> None:
        # 滤掉"还没有数据"的占位回调
        if value is None:
            self.skipped += 1
            return
        if timestamp is None or abs(float(timestamp) - self.EPICS_EPOCH) < 1.0:
            self.skipped += 1
            return
        with self._lock:
            self.values.append(value)
            self.timestamps.append(float(timestamp))
            self._last_value = value
            self.count += 1
        if self._callback is not None:
            try:
                self._callback(value)
            except Exception:  # 回调里抛出的错误不能弄死 CA 线程
                pass

    @property
    def last(self) -> Any:
        """最后一次收到的值（注意：drain() 清空缓存后这个值仍在）。"""
        with self._lock:
            return self._last_value

    def drain(self) -> list:
        """取走并清空到目前为止积累的 (timestamp, value)，线程安全。

        数据采集程序应该用它，而不是直接 popleft self.values。
        """
        with self._lock:
            items = list(zip(self.timestamps, self.values))
            self.values.clear()
            self.timestamps.clear()
        return items

    def wait_for(self, predicate: Callable[[Any], bool], timeout: float = 30.0,
                 desc: str = "") -> Any:
        """等一个"事件"发生（而不是等一个值）。

        例：等限位开关变成 1、等快门关闭、等计数率超过阈值。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                snapshot = list(self.values)
            for value in reversed(snapshot):
                if predicate(value):
                    return value
            time.sleep(0.02)
        raise PVError(f"等待 {self.name} 的事件超时（{timeout}s）{desc}")

    def close(self) -> None:
        try:
            self._pv.clear_callbacks()
        except Exception:
            pass

    def __enter__(self) -> "Monitor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def wait_until(predicate: Callable[[], bool], timeout: float = 30.0,
               poll: float = 0.05, desc: str = "condition") -> None:
    """等一个纯 Python 条件成立（和 PV 无关时用）。"""
    t0 = time.monotonic()
    while not predicate():
        if time.monotonic() - t0 > timeout:
            raise PVError(f"等待条件超时（{timeout}s）：{desc}")
        time.sleep(poll)
