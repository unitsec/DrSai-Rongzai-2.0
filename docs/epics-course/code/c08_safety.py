"""c08_safety.py —— 智能体和真设备之间的那层"防呆栏杆"（第 8 章，最重要的一章）

先说结论：**不要让大模型直接写 PV。**

大模型会犯错，而且犯得很有创意：把 600 写成 60、把单位当另一个单位、
把"停机"理解成"冷却"、在两轮对话之间忘记自己刚才开过加热。
在软件里这些错误的代价是一行日志；在实验站里，代价是烧掉的样品、
撞坏的探测器、或者（最坏的情况）伤人。

所以真实系统的结构永远是这样：

    ┌──────────────────────────────────────────────┐
    │  大模型（会犯错，可以犯错）                     │
    └───────────────────┬──────────────────────────┘
                        │  只调"语义工具"：set_temperature(60)
    ┌───────────────────▼──────────────────────────┐
    │  安全层（本文件）：白名单 / 限值 / 变化率 /     │
    │  联锁 / 速率限制 / 审计日志 / dry-run          │
    └───────────────────┬──────────────────────────┘
                        │  只允许通过检查的写操作
    ┌───────────────────▼──────────────────────────┐
    │  EPICS 客户端（pv_utils）+ IOC 自带的限值/联锁  │
    └───────────────────┬──────────────────────────┘
                        │
                   真·硬件（不可逆的那一层）

**这一层的目标不是"让智能体什么都能干"，而是"让智能体在任何情况下
都干不出危险的事"。** 做不到这一点，就不要把智能体接到真设备上。

本文件提供四个能力：
  1. 白名单 + 语义注册表（读 c08_instrument.json）：没登记的 PV，一个都写不了；
  2. 五道检查：能不能写 / 在不在范围内 / 单步幅度 / 联锁条件 / 写入速率；
  3. 审计日志：每一次写操作都记下"谁、什么时候、想写什么、实际生效多少、为什么"；
  4. dry-run（演习）与 abort（急停到安全状态）。

用法见 c08_tools.py。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pv_utils import PV, PVError


class SafetyViolation(RuntimeError):
    """写操作被安全层拒绝。**这是正常现象，不是 bug。**"""

    def __init__(self, message: str, *, channel: str = "", rule: str = ""):
        super().__init__(message)
        self.channel = channel
        self.rule = rule


@dataclass
class ChannelSpec:
    """一个通道的"契约"：它是什么、能不能写、能写到哪。"""

    name: str
    pv: str                # 也可以是个"逻辑名"，见 PV_ALIASES
    units: str = ""
    role: str = "measurement"
    writable: bool = False
    limits: Optional[List[float]] = None
    allowed: Optional[List[float]] = None
    max_step: Optional[float] = None
    requires: Dict[str, Any] = field(default_factory=dict)
    settle: Dict[str, float] = field(default_factory=dict)
    optional: bool = False
    desc: str = ""

    @property
    def is_actuator(self) -> bool:
        return self.writable and self.role in ("actuator", "axis", "setpoint",
                                               "permit", "enable")


class SafetyLayer:
    """安全层的全部逻辑。故意写得"啰嗦"：安全代码宁可显式，不要聪明。"""

    def __init__(self, config_path: str | Path, *,
                 dry_run: Optional[bool] = None,
                 actor: str = "agent",
                 session: str = "",
                 logger: Optional[Callable[[str], None]] = None):
        self.config_path = Path(config_path)
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.config = config
        self.instrument = config.get("instrument", "")
        self.prefix = config.get("prefix", "")
        safety = config.get("safety", {})
        self.dry_run = bool(safety.get("dry_run", False) if dry_run is None else dry_run)
        self.max_write_rate_hz = float(safety.get("max_write_rate_hz", 5))
        self.max_actions = int(safety.get("max_actions_per_run", 2000))
        log_path = safety.get("audit_log", "logs/audit.jsonl")
        self.audit_path = (self.config_path.parent / log_path).resolve()
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

        self.actor = actor
        self.session = session
        self._log = logger or (lambda message: None)

        self.channels: Dict[str, ChannelSpec] = {}
        for name, raw in config.get("channels", {}).items():
            self.channels[name] = ChannelSpec(
                name=name,
                pv=raw.get("pv", self.prefix + name),
                units=raw.get("units", ""),
                role=raw.get("role", "measurement"),
                writable=bool(raw.get("writable", False)),
                limits=raw.get("limits"),
                allowed=raw.get("allowed"),
                max_step=raw.get("max_step"),
                requires=raw.get("requires", {}),
                settle=raw.get("settle", {}),
                optional=bool(raw.get("optional", False)),
                desc=raw.get("desc", ""),
            )
        self.safe_state: Dict[str, Any] = config.get("safe_state", {})

        self._last_write: Dict[str, float] = {}
        #: 同一通道两次写入之间至少要隔多久（秒）。工具内部要自己遵守它，
        #: 而不是"先写一大堆，然后被安全层拒绝"。
        self.min_write_interval = (1.0 / self.max_write_rate_hz
                                   if self.max_write_rate_hz > 0 else 0.0)
        self._action_count = 0
        self._pv_cache: Dict[str, PV] = {}

    # ------------------------------------------------------------------ 连接
    def pv(self, channel: str) -> PV:
        """按通道名拿一个 PV 对象（带缓存）。通道必须在白名单里。"""
        spec = self.spec(channel)
        if spec.pv not in self._pv_cache:
            self._pv_cache[spec.pv] = PV(spec.pv)
        return self._pv_cache[spec.pv]

    def spec(self, channel: str) -> ChannelSpec:
        if channel not in self.channels:
            raise SafetyViolation(
                f"通道 {channel!r} 不在仪器白名单里。"
                f"可用通道：{sorted(self.channels)}",
                channel=channel, rule="whitelist",
            )
        return self.channels[channel]

    def close(self) -> None:
        for pv in self._pv_cache.values():
            try:
                pv._pv.clear_callbacks()  # noqa: SLF001
            except Exception:
                pass

    # ------------------------------------------------------------------ 读
    def read(self, channel: str) -> Dict[str, Any]:
        spec = self.spec(channel)
        self._bump("read", channel)
        value = float(self.pv(channel).get())
        return {
            "channel": channel,
            "pv": spec.pv,
            "value": value,
            "units": spec.units,
            "role": spec.role,
            "desc": spec.desc,
        }

    def read_many(self, channels: List[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for channel in channels:
            try:
                out[channel] = self.read(channel)["value"]
            except (SafetyViolation, PVError) as exc:
                out[channel] = {"error": str(exc)}
        return out

    # ------------------------------------------------------------------ 写
    def write(self, channel: str, value: float, reason: str = "") -> Dict[str, Any]:
        """唯一允许的写入口。所有检查都在这里，绕不过去。"""
        spec = self.spec(channel)
        started = time.monotonic()
        checks: List[str] = []

        # --- 检查 1：这个通道允许写吗 ---
        if not spec.writable:
            raise self._reject("ch1-只读", f"{channel} 是只读通道（{spec.role}），"
                                          f"不允许写。{spec.desc}", channel)

        # --- 检查 2：值的类型和范围 ---
        value = float(value)
        if spec.allowed is not None:
            if value not in [float(v) for v in spec.allowed]:
                raise self._reject("ch2-取值", f"{channel} 只允许 "
                                              f"{spec.allowed}，收到 {value}",
                                   channel)
        elif spec.limits is not None:
            low, high = float(spec.limits[0]), float(spec.limits[1])
            if not (low <= value <= high):
                raise self._reject("ch2-范围",
                                   f"{channel}={value} 超出允许范围 {spec.limits}"
                                   f"（{spec.units}）。注意：这和 IOC 的 DRVL/DRVH "
                                   f"是两套独立防线，都要有。", channel)

        # --- 检查 3：单步幅度（防止"一口气开到最大"）---
        current = None
        try:
            current = float(self.pv(channel).get())
        except PVError:
            current = None
        if spec.max_step is not None and current is not None:
            if abs(value - current) > float(spec.max_step):
                raise self._reject("ch3-单步",
                                   f"{channel} 想从 {current:g} 一步变到 {value:g}，"
                                   f"超过单步上限 {spec.max_step:g}。"
                                   f"请分几步走（或明确要求人工授权）。", channel)

        # --- 检查 4：联锁条件 ---
        for dep_channel, expected in spec.requires.items():
            actual = float(self.pv(dep_channel).get())
            if float(expected) != actual:
                raise self._reject(
                    "ch4-联锁",
                    f"拒绝写 {channel}：要求 {dep_channel}={expected:g}，"
                    f"当前 {actual:g}。{spec.desc}", channel)

        # --- 检查 5：写入速率 ---
        now = time.monotonic()
        last = self._last_write.get(channel, 0.0)
        min_interval = 1.0 / self.max_write_rate_hz if self.max_write_rate_hz > 0 else 0.0
        if now - last < min_interval:
            raise self._reject("ch5-速率",
                               f"{channel} 写入太快（限 {self.max_write_rate_hz:g} Hz）。"
                               f"控制系统不是消息队列，别用写 PV 的方式刷数据。", channel)
        self._last_write[channel] = now

        # --- 全部通过：写 + 读回确认 ---
        if self.dry_run:
            actual = value
            self._log(f"[dry-run] 本应写 {channel}={value:g} {spec.units}")
        else:
            actual = self.pv(channel).put_checked(value)
        checks.append("通过全部 5 项检查")

        record = {
            "ok": True,
            "action": "write",
            "channel": channel,
            "pv": spec.pv,
            "requested": value,
            "actual": actual,
            "units": spec.units,
            "changed": current is None or abs(actual - current) > 1e-12,
            "elapsed_s": round(time.monotonic() - started, 4),
            "checks": checks,
            "dry_run": self.dry_run,
        }
        self._audit(record, reason=reason,
                    after={channel: actual} if not self.dry_run else {})
        self._bump("write", channel)
        return record

    # ------------------------------------------------------------------ 急停
    def abort(self, reason: str = "手动急停") -> Dict[str, Any]:
        """把所有执行器回到"安全状态"。这是软件层急停，不是物理急停。

        ⚠️ 真设备上，**物理急停按钮永远优先于这里**。
        软件急停可能因为网络、进程、权限而失效；物理按钮不会。
        """
        results: Dict[str, Any] = {}
        self._log(f"⚠️ 执行急停：{reason}")
        for channel, value in self.safe_state.items():
            if channel not in self.channels:
                continue
            try:
                # 急停时不检查 max_step / 速率：安全优先于优雅
                self.pv(channel).put_checked(float(value))
                results[channel] = float(value)
            except Exception as exc:  # noqa: BLE001
                results[channel] = f"失败：{exc}"
        record = {"ok": True, "action": "abort", "reason": reason,
                  "safe_state": results}
        self._audit(record, reason=reason, after=results)
        return record

    # ------------------------------------------------------------- 内部工具
    def _reject(self, rule: str, message: str, channel: str) -> SafetyViolation:
        violation = SafetyViolation(message, channel=channel, rule=rule)
        self._audit({"ok": False, "action": "rejected", "channel": channel,
                     "rule": rule, "message": message}, reason="被安全层拒绝")
        return violation

    def _bump(self, kind: str, channel: str) -> None:
        self._action_count += 1
        if self._action_count > self.max_actions:
            raise SafetyViolation(
                f"本轮已执行 {self._action_count} 次操作，超过上限 {self.max_actions}。"
                f"这通常意味着智能体陷入循环，请人工检查。",
                channel=channel, rule="ch6-总量",
            )

    def _audit(self, record: Dict[str, Any], *, reason: str = "",
               after: Optional[Dict[str, Any]] = None) -> None:
        """审计日志：一行一个 JSON。这是事后复盘和"能不能信任它"的唯一依据。"""
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
                  + f".{int(time.time() % 1 * 1000):03d}",
            "epoch": round(time.time(), 3),
            "actor": self.actor,
            "session": self.session,
            "reason": reason,
            **record,
        }
        if after:
            entry["after"] = after
        try:
            with open(self.audit_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._log(f"⚠️ 审计日志写入失败：{exc}")

    # ----------------------------------------------------- 给智能体看的说明书
    def describe(self) -> Dict[str, Any]:
        """把仪器"翻译"成大模型能看懂的说明。工具描述越清楚，幻觉越少。"""
        channels = []
        for name, spec in self.channels.items():
            item: Dict[str, Any] = {
                "name": name, "pv": spec.pv, "units": spec.units,
                "role": spec.role, "writable": spec.writable,
                "desc": spec.desc,
            }
            if spec.limits:
                item["limits"] = spec.limits
            if spec.allowed:
                item["allowed_values"] = spec.allowed
            if spec.max_step:
                item["max_step_per_write"] = spec.max_step
            if spec.requires:
                item["requires"] = spec.requires
            channels.append(item)
        return {
            "instrument": self.instrument,
            "prefix": self.prefix,
            "dry_run": self.dry_run,
            "write_rate_limit_hz": self.max_write_rate_hz,
            "safe_state": self.safe_state,
            "note": "写操作必须通过 write_channel 工具，并带上 reason；"
                    "不要尝试绕过安全层（你也没有别的写入口）。",
            "channels": channels,
        }
