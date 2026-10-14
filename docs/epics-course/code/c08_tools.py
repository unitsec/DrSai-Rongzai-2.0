"""c08_tools.py —— 智能体的"手"：仪器语义工具（第 8 章）

核心思想再强调一次：

    给大模型的**不是** `caput LAB:Power 35`
    而是 `set_heater(35, reason="开始升温")`

为什么？把这句话对比一下：

| 裸 PV 操作 | 语义工具 |
|---|---|
| `caput LAB:Power 35` | `set_heater(35, reason="...")` |
| 模型要知道 PV 名、单位、量程、极性 | 模型只需要知道"加热功率百分比" |
| 模型自己判断有没有超限、要不要等 | 工具内部检查 + 等待 + 读回确认 |
| 出错时模型只能看到一个字符串 | 返回结构化 JSON（含规则名、建议） |
| 换个设备就要重写提示词 | 换设备只改配置文件 |
| 无法审计 | 每次调用都写审计日志 |

本文件里的每个函数都会返回一个 **JSON 可序列化的 dict**，
并且**永远不抛异常**（可预期的失败都变成 `{"ok": false, ...}`）——
因为"把错误变成数据"是让智能体能自我纠正的前提。

工具清单（和 c08_mcp_server.py 暴露给智能体的工具一一对应）：

    describe_instrument()                 我的仪器有哪些通道、限值、单位
    snapshot()                            现在所有通道的值
    read_channel(name)                    读一个通道
    write_channel(name, value, reason)    写一个通道（必须带理由）
    hold_temperature(target, tol, timeout) 升/降到目标温度并稳定住
    move_axis(axis, target)               走轴并等到位
    run_scan(...)                         扫描 + 自动找峰
    emergency_stop(reason)                急停到安全状态
    get_progress()                        长任务现在进行到哪一步了
    self_check()                          上机前体检：所有 PV 通不通、联锁是否正常
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from c08_safety import SafetyLayer, SafetyViolation
from pv_utils import PVError
from scan_utils import find_peak, frange, summarize_scan


class Instrument:
    """一个实验站。所有工具方法都是它的成员，方便共享安全层和进度文件。"""

    def __init__(self, config_path: str | Path = "c08_instrument.json",
                 *, dry_run: Optional[bool] = None, actor: str = "agent",
                 session: str = "", echo: bool = True):
        self.echo = echo
        self.session = session or time.strftime("s%Y%m%d-%H%M%S")
        self.safety = SafetyLayer(config_path, dry_run=dry_run, actor=actor,
                                  session=self.session, logger=self._say)
        self.progress_path = Path(self.safety.audit_path).parent / "progress.jsonl"
        self.data_dir = Path(self.safety.audit_path).parent.parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._step = 0

    # ------------------------------------------------------------------ 小工具
    def _say(self, message: str) -> None:
        """人类可读的日志。

        ⚠️ 一定要写 stderr，不能写 stdout！当这个模块被 MCP 以 **stdio**
        模式启动时，stdout 是 JSON-RPC 协议通道，往里写一行中文日志就会
        把协议打碎，客户端会报 "Invalid JSON: expected `,` or `]`"。
        这个坑非常常见，且报错信息完全指不到真正的原因。
        """
        if self.echo:
            print(f"[{time.strftime('%H:%M:%S')}] {message}",
                  file=sys.stderr, flush=True)

    def progress(self, message: str, **extra: Any) -> None:
        """写一行进度。长任务必须让用户/智能体知道"现在在干什么"。

        （这也是蓉仔 MCP 服务的约定：progress_file 里每一行都是最新进展。）
        """
        self._step += 1
        entry = {"ts": time.strftime("%H:%M:%S"), "step": self._step,
                 "message": message, **extra}
        try:
            with open(self.progress_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
        self._say(f"{message}")

    def get_progress(self, last: int = 10) -> Dict[str, Any]:
        lines: List[Dict[str, Any]] = []
        if self.progress_path.exists():
            raw = self.progress_path.read_text(encoding="utf-8").strip().splitlines()
            for line in raw[-last:]:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return {"ok": True, "log": str(self.progress_path),
                "total_steps": self._step, "last_lines": lines}

    def _ok(self, **payload: Any) -> Dict[str, Any]:
        return {"ok": True, **payload}

    def _err(self, message: str, *, rule: str = "", hint: str = "") -> Dict[str, Any]:
        out: Dict[str, Any] = {"ok": False, "error": message}
        if rule:
            out["rule"] = rule
        if hint:
            out["hint"] = hint
        return out

    # =========================================================== 只读工具
    def describe_instrument(self) -> Dict[str, Any]:
        """告诉智能体这台仪器的一切：通道、单位、限值、联锁、安全状态。"""
        return self._ok(**self.safety.describe())

    def snapshot(self) -> Dict[str, Any]:
        """读一遍所有通道的当前值。智能体每次决策前都该先看这个。"""
        values: Dict[str, Any] = {}
        for name in self.safety.channels:
            try:
                values[name] = self.safety.read(name)["value"]
            except (SafetyViolation, PVError) as exc:
                values[name] = {"error": str(exc)}
        return self._ok(values=values, t=time.strftime("%H:%M:%S"))

    def read_channel(self, channel: str) -> Dict[str, Any]:
        """读一个通道的当前值（含单位和语义）。"""
        try:
            return self._ok(**self.safety.read(channel))
        except SafetyViolation as exc:
            return self._err(str(exc), rule=exc.rule)
        except PVError as exc:
            return self._err(f"读 {channel} 失败：{exc}", hint="检查 IOC 是否在运行")

    # =========================================================== 写入工具
    def write_channel(self, channel: str, value: float, reason: str) -> Dict[str, Any]:
        """写一个通道。**必须给出 reason**（安全审计要求，也是在逼智能体想清楚）。"""
        if not reason or not str(reason).strip():
            return self._err("写操作必须提供 reason（为什么写这个值）。"
                             "这是安全审计的硬要求。", rule="ch0-理由")
        try:
            result = self.safety.write(channel, value, reason=str(reason))
            self.progress(f"写 {channel}={result['actual']:g} "
                          f"{result['units']}（{reason}）")
            return self._ok(**result)
        except SafetyViolation as exc:
            self._say(f"⛔ 安全层拒绝：{exc}")
            return self._err(str(exc), rule=exc.rule,
                             hint="不要绕过安全层；请调整请求值或先满足联锁条件。")
        except PVError as exc:
            return self._err(f"写 {channel} 失败：{exc}")

    # =========================================================== 高层动作
    def hold_temperature(self, target: float, tol: float = 0.5,
                         timeout: float = 180.0, reason: str = "") -> Dict[str, Any]:
        """把温度调到 target 并稳定在 ±tol 以内。

        这是"动作"而不是"操作"：它内部会
          ① 检查目标是否在允许范围内 → ② 写设定值 → ③ 打开许可 →
          ④ 轮询直到稳定 → ⑤ 把过程数据落盘 → ⑥ 返回证据
        """
        started = time.monotonic()
        reason = reason or f"把样品温度稳定到 {target:g} 度"
        try:
            spec = self.safety.spec("setpoint")
            if spec.limits and not (spec.limits[0] <= target <= spec.limits[1]):
                return self._err(f"目标温度 {target} 超出允许范围 {spec.limits}",
                                 rule="目标范围")

            self.progress(f"开始控温到 {target:g}±{tol:g} ℃")
            self.safety.write("run_permit", 1, reason="允许加热")
            # 分步靠近：既满足"单步幅度"限制，也更安全（真设备上尤其重要）
            current = float(self.safety.pv("setpoint").get())
            max_step = float(self.safety.spec("setpoint").max_step or 20)
            steps = []
            step_target = current
            while abs(step_target - target) > 1e-9:
                step_target += max(-max_step, min(max_step, target - step_target))
                steps.append(round(step_target, 4))
            for step_target in steps:
                self.safety.write("setpoint", step_target,
                                  reason=f"分步升温到 {target:g}（当前 {step_target:g}）")
                self.progress(f"设定值 → {step_target:g} ℃")
                # 遵守本通道的写入速率限制：分步走的时候也要"慢下来"
                time.sleep(self.safety.min_write_interval + 0.02)

            # 等温度真的稳定（读回确认，而不是相信写入）
            temperature = self.safety.pv("temperature")
            samples: List[Tuple[float, float]] = []
            t0 = time.monotonic()
            while True:
                value = float(temperature.get())
                samples.append((time.monotonic() - t0, value))
                if abs(value - target) <= tol:
                    # 连续 3 次都在窗口里才算"稳定"（防抖）
                    if len(samples) >= 3 and all(
                            abs(v - target) <= tol for _, v in samples[-3:]):
                        break
                if time.monotonic() - t0 > timeout:
                    self.progress(f"控温超时：{timeout:.0f}s 后温度 {value:.2f} ℃")
                    self._dump_samples("hold_temperature", samples)
                    return self._err(
                        f"控温超时：{timeout:.0f}s 内没有稳定到 {target}±{tol} ℃"
                        f"（当前 {value:.3f} ℃）",
                        hint="检查加热功率上限、环境温度、或者自己算一下"
                             "这个设定值到底能不能达到（满功率最高温度）。")
                time.sleep(0.2)

            self._dump_samples("hold_temperature", samples)
            actual = samples[-1][1]
            self.progress(f"温度稳定在 {actual:.3f} ℃（目标 {target:g}）")
            return self._ok(
                action="hold_temperature", target=target, actual=actual,
                tolerance=tol, elapsed_s=round(time.monotonic() - started, 1),
                samples=len(samples), evidence={"final_temperature": actual},
            )
        except SafetyViolation as exc:
            return self._err(str(exc), rule=exc.rule)
        except PVError as exc:
            return self._err(f"控温失败：{exc}")

    def move_axis(self, axis: str = "theta", target: float = 0.0,
                  reason: str = "") -> Dict[str, Any]:
        """写轴目标值，然后等回读值到位。**到位判断才是重点**。"""
        started = time.monotonic()
        reason = reason or f"把 {axis} 移动到 {target:g}"
        readback = f"{axis}_rb" if f"{axis}_rb" in self.safety.channels else None
        try:
            spec = self.safety.spec(axis)
            record = self.safety.write(axis, target, reason=reason)
            if readback is None:
                return self._ok(action="move_axis", **record,
                                note="这个轴没有登记回读通道，无法确认是否到位")
            settle = self.safety.spec(readback).settle or {"tol": 0.05, "timeout": 20}
            readback_pv = self.safety.pv(readback)
            self.progress(f"{axis} → {target:g}，等待回读到位（±{settle['tol']:g}）")
            reached = readback_pv.wait_within(target, tol=float(settle["tol"]),
                                              timeout=float(settle["timeout"]))
            elapsed = round(time.monotonic() - started, 2)
            self.progress(f"{axis} 到位：回读 {reached:.4f}")
            return self._ok(action="move_axis", axis=axis, requested=target,
                            actual=float(reached), units=spec.units,
                            elapsed_s=elapsed,
                            evidence={"readback": float(reached)})
        except SafetyViolation as exc:
            return self._err(str(exc), rule=exc.rule)
        except PVError as exc:
            return self._err(f"移动 {axis} 失败：{exc}",
                             hint="可能是电机卡住、限位触发、或到位容差太小")

    def run_scan(self, axis: str = "theta", detector: str = "counts",
                 start: float = 10.0, stop: float = 30.0, step: float = 1.0,
                 dwell: float = 0.2, samples: int = 1,
                 reason: str = "") -> Dict[str, Any]:
        """一次扫描：动轴 → 等到位 → 取数 → 找峰 → 落盘。

        这是本课程里"最像真实实验"的一个工具；散裂上的 θ-2θ 扫描、
        能量扫描、温度扫描，结构完全一样，只是设备换成真的。
        """
        started = time.monotonic()
        reason = reason or f"从 {start:g} 到 {stop:g} 扫描 {axis}，探测器 {detector}"
        try:
            targets = frange(start, stop, step)
        except ValueError as exc:
            return self._err(f"扫描参数不合法：{exc}")
        if len(targets) > 500:
            return self._err(f"扫描点数 {len(targets)} 太多（>500）。"
                             f"请先用粗步长找峰，再在峰附近细扫。",
                             hint="两段式扫描是实验的标准做法。")

        points: List[Tuple[float, float]] = []
        self.progress(f"开始扫描：{axis} {start:g}→{stop:g} 共 {len(targets)} 点")
        for index, target in enumerate(targets, start=1):
            moved = self.move_axis(axis, target, reason=f"扫描第 {index} 点")
            if not moved["ok"]:
                self._dump_scan(axis, detector, points, reason)
                return self._err(f"扫描在 {target:g} 处中断：{moved.get('error')}",
                                 hint="已把中断前的数据落盘，可以用更小的步长重试。")
            time.sleep(dwell)     # 等轴和探测器都稳定
            # 记录"取数当时"的角度：到位瞬间的回读可能还在爬行（机械滞后），
            # 而你要记进数据文件的是真正取数时的位置。
            readback_channel = f"{axis}_rb"
            if readback_channel in self.safety.channels:
                actual_angle = float(self.safety.pv(readback_channel).get())
            else:
                actual_angle = float(moved["actual"])
            values = [self.safety.read(detector)["value"] for _ in range(max(1, samples))]
            counts = sum(values) / len(values)
            points.append((actual_angle, counts))
            if index % 5 == 0 or index == len(targets):
                self.progress(f"扫描进度 {index}/{len(targets)}："
                              f"{axis}={actual_angle:g}，{detector}={counts:.1f}")

        peak = find_peak(points)
        summary = summarize_scan(points, axis, detector)
        path = self._dump_scan(axis, detector, points, reason, peak=peak, summary=summary)
        self.progress(f"扫描完成：{summary.splitlines()[2] if len(summary.splitlines()) > 2 else summary}")
        return self._ok(
            action="run_scan", axis=axis, detector=detector,
            n_points=len(points), elapsed_s=round(time.monotonic() - started, 1),
            peak=peak, summary=summary, data_file=str(path),
            points=[[round(x, 6), round(y, 3)] for x, y in points],
        )

    def emergency_stop(self, reason: str = "智能体请求急停") -> Dict[str, Any]:
        """急停：所有执行器回到安全状态。"""
        self.progress(f"🛑 急停：{reason}")
        result = self.safety.abort(reason)
        return self._ok(**result)

    # =========================================================== 体检
    def self_check(self) -> Dict[str, Any]:
        """上机前体检：每个通道能不能连上、读到的值是否合理、联锁是否如预期。

        **每次真正开始实验前都跑一遍。** 它能在 5 秒内发现 90% 的
        "昨天还好好的，今天怎么回事"：PV 改名了、IOC 没起、传感器掉了、
        有人把联锁关掉了。
        """
        self.progress("开始上机前体检")
        report: Dict[str, Any] = {"channels": {}, "problems": [], "warnings": []}
        for name, spec in self.safety.channels.items():
            try:
                value = self.safety.pv(name).get()
                info: Dict[str, Any] = {"value": value, "units": spec.units}
                if spec.limits and not (spec.limits[0] <= float(value) <= spec.limits[1]):
                    info["status"] = "OUT_OF_RANGE"
                    report["problems"].append(
                        f"{name}={value} 超出登记范围 {spec.limits}")
                else:
                    info["status"] = "OK"
                report["channels"][name] = info
            except (PVError, SafetyViolation) as exc:
                report["channels"][name] = {"status": "UNREACHABLE", "error": str(exc)}
                if spec.optional:
                    # 可选设备（比如开发板）不在线不算故障，但要让人知道
                    report["warnings"].append(
                        f"{name} 连不上（可选设备，不影响主流程）：{exc}")
                else:
                    report["problems"].append(f"{name} 连不上：{exc}")

        # 联锁自检：确认"没有许可时，加热一定上不去"
        try:
            if "heater_enable" in self.safety.channels:
                enabled = float(self.safety.pv("heater_enable").get())
                permit = float(self.safety.pv("run_permit").get())
                over = float(self.safety.pv("over_temp").get())
                expect = 1 if (permit == 1 and over == 0) else 0
                if enabled != expect:
                    report["warnings"].append(
                        f"联锁状态和预期不一致：HeaterEnable={enabled}，"
                        f"但 RunPermit={permit}、OverTemp={over} 推算应为 {expect}")
                report["interlock"] = {"heater_enable": enabled,
                                       "run_permit": permit, "over_temp": over}
        except (PVError, SafetyViolation) as exc:
            report["warnings"].append(f"联锁自检失败：{exc}")

        report["ok"] = not report["problems"]
        self.progress("体检完成：" +
                      ("全部正常" if report["ok"] else f"{len(report['problems'])} 个问题"))
        return self._ok(**report)

    # =========================================================== 落盘
    def _dump_scan(self, axis: str, detector: str,
                   points: Sequence[Tuple[float, float]], reason: str = "",
                   peak: Optional[Dict[str, float]] = None,
                   summary: str = "") -> Path:
        path = self.data_dir / f"scan_{axis}_{detector}_{time.strftime('%Y%m%d-%H%M%S')}.csv"
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(f"# reason: {reason}\n")
            fp.write(f"# actor: {self.safety.actor}  session: {self.session}\n")
            fp.write(f"# instrument: {self.safety.instrument}\n")
            if summary:
                for line in summary.splitlines():
                    fp.write(f"# {line}\n")
            if peak:
                fp.write(f"# peak: {json.dumps(peak, ensure_ascii=False)}\n")
            fp.write(f"{axis},{detector}\n")
            for x, y in points:
                fp.write(f"{x:.6f},{y:.6f}\n")
        return path

    def _dump_samples(self, tag: str, samples: Sequence[Tuple[float, float]]) -> Path:
        path = self.data_dir / f"{tag}_{time.strftime('%Y%m%d-%H%M%S')}.csv"
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("t_s,value\n")
            for t, v in samples:
                fp.write(f"{t:.3f},{v:.6f}\n")
        return path


# =============================================================================
# 命令行自测：不需要大模型，直接把这些工具当函数调用一遍
# =============================================================================
def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="仪器语义工具自测（不需要大模型）")
    ap.add_argument("--config", default="c08_instrument.json")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--hold", type=float, help="稳定到这个温度")
    ap.add_argument("--scan", action="store_true", help="跑一次扫描")
    ap.add_argument("--dry-run", action="store_true", help="只检查不真写")
    args = ap.parse_args()

    inst = Instrument(args.config, dry_run=args.dry_run or None, actor="cli")
    if args.self_check:
        print(json.dumps(inst.self_check(), ensure_ascii=False, indent=2))
    if args.snapshot:
        print(json.dumps(inst.snapshot(), ensure_ascii=False, indent=2))
    if args.hold is not None:
        print(json.dumps(inst.hold_temperature(args.hold), ensure_ascii=False, indent=2))
    if args.scan:
        result = inst.run_scan(axis="theta", detector="counts",
                               start=15, stop=25, step=0.5, dwell=0.2)
        result.pop("points", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if not any([args.self_check, args.snapshot, args.hold is not None, args.scan]):
        print(json.dumps(inst.describe_instrument(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
