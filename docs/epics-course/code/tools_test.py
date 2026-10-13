"""tools_test.py —— 安全层和仪器工具的自动化测试（不需要大模型）

跑法（需要先起 softIoc + 虚拟恒温箱 + PID 服务 + 虚拟探测器）：
    python tools_test.py

它故意去撞每一条安全规则，验证：
  只读通道写不进去、超范围写不进去、单步太大写不进去、
  联锁不满足写不进去、不给理由写不进去、写太快被限流。
如果这些测试没有全部通过，就不要把智能体接到真设备上。
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)          # 让 c08_instrument.json / logs / data 都落在 code/ 下

from c08_tools import Instrument  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"{'✓' if condition else '✗'} {name}" + (f"  —— {detail}" if detail else ""))


def brief(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) < 300 else text[:300] + "…"


def main() -> int:
    inst = Instrument("c08_instrument.json", actor="pytest", echo=True)

    print("\n=== 0. 上机前体检 ===")
    report = inst.self_check()
    check("体检通过（所有 PV 都能连上）", report["ok"], brief(report.get("problems")))

    print("\n=== 1. 只读通道不能写 ===")
    r = inst.write_channel("temperature", 50, "试图直接改测量值")
    check("拒绝写只读通道", r["ok"] is False and r.get("rule") == "ch1-只读", brief(r))

    print("\n=== 2. 超范围不能写 ===")
    r = inst.write_channel("setpoint", 500, "测试超范围")
    check("拒绝超范围写入", r["ok"] is False and r.get("rule") == "ch2-范围", brief(r))

    print("\n=== 3. 枚举取值只能是允许值 ===")
    r = inst.write_channel("shutter", 7, "测试非法取值")
    check("拒绝非法取值", r["ok"] is False and r.get("rule") == "ch2-取值", brief(r))

    print("\n=== 4. 单步幅度限制 ===")
    inst.write_channel("setpoint", 25, "复位到室温")
    r = inst.write_channel("setpoint", 90, "一步从 25 跳到 90")
    check("拒绝单步过大", r["ok"] is False and r.get("rule") == "ch3-单步", brief(r))

    print("\n=== 5. 联锁：没有运行许可时不许加热 ===")
    inst.write_channel("run_permit", 0, "先撤销运行许可")
    time.sleep(0.8)     # 等 IOC 里的联锁记录重算（SCAN .2 second）
    r = inst.write_channel("heater", 30, "在没有许可的情况下加热")
    check("拒绝违反联锁的写入", r["ok"] is False and r.get("rule") == "ch4-联锁", brief(r))

    print("\n=== 6. 写操作必须给理由 ===")
    r = inst.write_channel("setpoint", 30, "")
    check("拒绝没有理由的写", r["ok"] is False and r.get("rule") == "ch0-理由", brief(r))

    print("\n=== 7. 速率限制 ===")
    inst.write_channel("run_permit", 1, "恢复运行许可")
    time.sleep(0.8)
    inst.write_channel("heater", 20, "正常加热")
    r = inst.write_channel("heater", 25, "立刻又写一次（应该被限流）")
    check("拒绝过快写入", r["ok"] is False and r.get("rule") == "ch5-速率", brief(r))

    print("\n=== 8. 合法写入 + 读回确认 ===")
    time.sleep(0.3)
    r = inst.write_channel("heater", 35, "升温到 35%")
    check("合法写入成功且读回一致",
          r["ok"] and abs(r["actual"] - 35) < 1e-6, brief(r))

    print("\n=== 9. 快照 ===")
    snap = inst.snapshot()
    check("快照能读到所有通道", snap["ok"] and len(snap["values"]) >= 5, brief(snap))

    print("\n=== 10. 控温动作（需要 PID 服务在跑）===")
    r = inst.hold_temperature(45, tol=1.0, timeout=150)
    check("hold_temperature 到达目标", r["ok"] is True, brief(r))

    print("\n=== 11. 扫描 + 找峰（需要虚拟探测器在跑）===")
    r = inst.run_scan(axis="theta", detector="counts", start=16, stop=24,
                      step=0.5, dwell=0.15)
    peak = r.get("peak", {})
    peak_x = peak.get("peak_x_refined", peak.get("peak_x"))
    check("扫描完成", r["ok"] is True, brief({k: v for k, v in r.items() if k != "points"}))
    check("峰位在 20 度附近（误差 < 0.5 度）",
          r["ok"] and peak_x is not None and abs(float(peak_x) - 20.0) < 0.5,
          f"找到 {peak_x}")

    print("\n=== 12. 急停 ===")
    r = inst.emergency_stop("测试结束")
    check("急停把执行器写回安全状态",
          r["ok"] and r["safe_state"].get("heater") == 0.0, brief(r))

    print("\n=== 13. 审计日志 ===")
    audit = inst.safety.audit_path
    lines = audit.read_text(encoding="utf-8").strip().splitlines() if audit.exists() else []
    rejected = sum(1 for line in lines if '"ok": false' in line)
    check("审计日志有内容", len(lines) > 0, f"{len(lines)} 行 -> {audit}")
    check("被拒绝的操作也进了日志", rejected >= 5, f"{rejected} 条拒绝记录")

    print(f"\n{'=' * 60}")
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  ✗ {name}")
    print("=" * 60)
    inst.safety.close()
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
