"""c08_agent_demo.py —— 一个"自主实验"闭环的最小完整实现（第 8 章）

它演示智能体驱动实验的完整循环，但**不依赖任何大模型 API**：
`--policy script` 用一个写死的策略走完流程，让你先看清"结构"；
`--policy llm` 才把决策交给真正的大模型（需要一个 OpenAI 兼容的接口）。

    感知(observe) → 决策(decide) → 动作(act) → 验证(verify) → 记录 → 回到感知

任务（和真实谱仪上的"自动寻峰 + 测峰"完全同构）：

    1. 体检、看当前状态；
    2. 粗扫 10°→30°，找出峰大概在哪；
    3. 在峰附近细扫（步长缩小 5 倍），得到精确峰位；
    4. 在峰位上稳定温度，再测一次强度，观察温度对峰强的影响；
    5. 输出一份实验报告（含数据文件路径和审计日志）。

        第 2~3 步就是真实实验中"coarse scan + fine scan"的标准做法：
        先用便宜的方式找到位置，再花时间做精确测量。
        大模型的价值恰恰在这里 —— 它会根据粗扫结果**自己决定**细扫范围，
        而写死的脚本只会照本宣科。

用法：
    # 不用大模型，跑通流程（推荐先跑这个）
    python c08_agent_demo.py --policy script

    # 用真模型（需要 OPENAI_API_KEY / OPENAI_BASE_URL，模型名用 --model 指定）
    python c08_agent_demo.py --policy llm --model deepseek-chat

跑之前需要（另开终端）：
    cd softioc && softIocPVA st.cmd          # 设备 IOC
    python c06_thermal_plant.py              # 虚拟恒温箱
    python c06_detector_sim.py               # 虚拟探测器
    python c06_pid_service.py                 # 常驻 PID 控制器
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

from c08_tools import Instrument  # noqa: E402

SYSTEM_PROMPT = """你是一个散裂中子源谱仪的实验控制智能体。

你的目标：在保证安全的前提下，自主完成用户交给的实验任务，并如实报告结果。

你必须遵守的规则：
1. 只能用提供的工具操作设备，绝不能凭空猜测通道名或数值。
2. 每个写操作都要给出 reason，写清楚"为什么"。
3. 写完之后必须验证：读回实际值，或者用 hold_temperature/move_axis 这类
   会自己等待到位、并返回证据的动作类工具。
4. 安全层拒绝你时（ok=false），读 rule 和 hint，改变你的做法；
   绝不要绕开它，也不要把被拒绝描述成"已完成"。
5. 任何时候觉得情况不对（读数乱跳、设备无响应、结果不合理），
   立刻调用 emergency_stop，然后向用户报告。
6. 报告结果要给出：做了什么、实际数值、数据文件路径、有什么异常。
   不要编造没有测到的数据。

回答用中文，简洁、直接、有条理。"""


# =============================================================================
# 1. 工具定义：给大模型看的"函数清单"（OpenAI function-calling 格式）
# =============================================================================
TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "describe_instrument",
            "description": "列出仪器所有通道（名字、单位、限值、联锁）。开始前先调用它。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_check",
            "description": "上机前体检：所有通道是否可读、联锁是否正常。problems 非空就不要开始实验。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": "读一遍所有通道的当前值。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hold_temperature",
            "description": "把样品温度调到目标值并稳定在 ±tolerance 内，返回实际温度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "number", "description": "目标温度 ℃"},
                    "tolerance": {"type": "number", "description": "允许窗口 ℃，默认 0.5"},
                    "timeout_s": {"type": "number", "description": "最长等待秒数"},
                    "reason": {"type": "string", "description": "为什么调这个温度"},
                },
                "required": ["target", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_axis",
            "description": "移动轴（theta）到目标角度并等待回读到位的证据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "axis": {"type": "string"},
                    "target": {"type": "number", "description": "目标角度 deg"},
                    "reason": {"type": "string"},
                },
                "required": ["axis", "target", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_scan",
            "description": "扫描：轴从 start 到 stop，每点取探测器计数，自动找峰（返回 peak/summary/data_file）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "axis": {"type": "string", "default": "theta"},
                    "detector": {"type": "string", "default": "counts"},
                    "start": {"type": "number"},
                    "stop": {"type": "number"},
                    "step": {"type": "number"},
                    "dwell_s": {"type": "number", "default": 0.2},
                    "samples": {"type": "integer", "default": 1},
                    "reason": {"type": "string"},
                },
                "required": ["start", "stop", "step", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_channel",
            "description": "读一个通道的当前值（含单位）。",
            "parameters": {
                "type": "object",
                "properties": {"channel": {"type": "string"}},
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_progress",
            "description": "查看长任务进展（防止长时间静默）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emergency_stop",
            "description": "急停：所有执行器回到安全状态。情况不对就立刻调用。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


def call_tool(inst: Instrument, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """把工具名映射到 Instrument 的方法。这就是"工具层"的全部。"""
    if name == "describe_instrument":
        return inst.describe_instrument()
    if name == "self_check":
        return inst.self_check()
    if name == "snapshot":
        return inst.snapshot()
    if name == "read_channel":
        return inst.read_channel(args["channel"])
    if name == "hold_temperature":
        return inst.hold_temperature(float(args["target"]),
                                     tol=float(args.get("tolerance", 0.5)),
                                     timeout=float(args.get("timeout_s", 300)),
                                     reason=args.get("reason", ""))
    if name == "move_axis":
        return inst.move_axis(args.get("axis", "theta"), float(args["target"]),
                              reason=args.get("reason", ""))
    if name == "run_scan":
        return inst.run_scan(axis=args.get("axis", "theta"),
                             detector=args.get("detector", "counts"),
                             start=float(args["start"]), stop=float(args["stop"]),
                             step=float(args["step"]),
                             dwell=float(args.get("dwell_s", 0.2)),
                             samples=int(args.get("samples", 1)),
                             reason=args.get("reason", ""))
    if name == "get_progress":
        return inst.get_progress()
    if name == "emergency_stop":
        return inst.emergency_stop(args.get("reason", "agent requested"))
    return {"ok": False, "error": f"未知工具 {name}"}


# =============================================================================
# 2. 两种"大脑"：写死的策略 / 真大模型
# =============================================================================
def trim(result: Dict[str, Any], limit: int = 1200) -> Dict[str, Any]:
    """工具结果可能很大（扫描点），送给大模型前先瘦身。

    这是很重要的工程细节：上下文是有限资源，别把几千个数据点塞进去。
    run_scan 已经给了 summary 和 peak，points 就没必要全带上。
    """
    slim = {k: v for k, v in result.items() if k not in ("points", "last_lines")}
    text = json.dumps(slim, ensure_ascii=False)
    if len(text) > limit:
        slim = {"_truncated": True, "text": text[:limit] + "…"}
    return slim


def run_scripted(inst: Instrument) -> None:
    """写死的策略：把"自主实验"该有的步骤完整走一遍。

    注意它的结构 —— 这和你将来让大模型做的决策**完全一样**，
    只是决策从"模型推理"换成了"if/else"。
    """
    print("\n" + "=" * 70)
    print("阶段 0：体检")
    report = inst.self_check()
    print(json.dumps(trim(report), ensure_ascii=False))
    if not report["ok"]:
        print("❌ 体检没过，停止实验（真实系统里这里就该退出）")
        return
    print(json.dumps(trim(inst.snapshot()), ensure_ascii=False))

    print("\n" + "=" * 70)
    print("阶段 1：粗扫 10°→30°，步长 2°")
    coarse = inst.run_scan(axis="theta", detector="counts", start=10, stop=30,
                           step=2, dwell=0.15, reason="粗扫找峰")
    if not coarse["ok"]:
        print("❌ 粗扫失败：", coarse.get("error"))
        return
    print(coarse["summary"])
    peak_x = float(coarse["peak"].get("peak_x_refined", coarse["peak"]["peak_x"]))

    print("\n" + "=" * 70)
    print(f"阶段 2：在峰附近细扫（{peak_x - 1.5:.1f}°→{peak_x + 1.5:.1f}°，步长 0.25°）")
    fine = inst.run_scan(axis="theta", detector="counts",
                         start=peak_x - 1.5, stop=peak_x + 1.5, step=0.25,
                         dwell=0.15, samples=2, reason="细扫定位峰位")
    if not fine["ok"]:
        print("❌ 细扫失败：", fine.get("error"))
        return
    print(fine["summary"])
    fine_x = float(fine["peak"].get("peak_x_refined", fine["peak"]["peak_x"]))

    print("\n" + "=" * 70)
    print("阶段 3：把轴停在峰位，测温（观察峰强随温度变化）")
    results = []
    for temperature in (25.0, 45.0):
        moved = inst.move_axis("theta", fine_x, reason=f"{temperature:g}℃ 前对准峰位")
        if not moved["ok"]:
            print("❌ 移动失败：", moved.get("error"))
            inst.emergency_stop("移动失败，转人工处理")
            return
        held = inst.hold_temperature(temperature, tol=1.0, timeout=180,
                                     reason=f"测 {temperature:g}℃ 下的峰强")
        if not held["ok"]:
            print(f"⚠️ {temperature:g}℃ 未达到稳定：{held.get('error')}")
        counts = inst.read_channel("counts")["value"]
        results.append({"temperature": temperature, "counts": counts,
                        "theta": fine_x})
        print(f"  {temperature:g}℃ → counts = {counts}")

    print("\n" + "=" * 70)
    print("阶段 4：收尾（回安全状态）+ 实验报告")
    stop = inst.emergency_stop("实验结束，回到安全状态")
    print(f"  急停/回安全状态：{json.dumps(stop.get('safe_state', {}), ensure_ascii=False)}")
    report = {
        "任务": "粗扫 → 细扫定位峰位 → 变温测峰强",
        "细扫峰位_deg": fine_x,
        "细扫峰高": fine["peak"].get("peak_y"),
        "半高宽_deg": fine["peak"].get("fwhm"),
        "变温结果": results,
        "粗扫数据文件": coarse["data_file"],
        "细扫数据文件": fine["data_file"],
        "审计日志": str(inst.safety.audit_path),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    path = inst.data_dir / f"report_{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已保存：{path}")


def run_llm(inst: Instrument, model: str, max_turns: int = 25) -> None:
    """用真正的大模型做决策。需要 OpenAI 兼容接口。

    这段代码是"智能体"这个词的全部含义：
        循环 { 把历史和当前状态发给模型 → 模型返回工具调用 → 执行 → 把结果喂回去 }
    直到模型不再调用工具、或者达到轮次上限。
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("需要安装 openai：pip install openai")
        return

    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None,
                    api_key=os.environ.get("OPENAI_API_KEY"))
    task = (f"请自主完成一次实验：先用粗扫找到峰的大致位置，再在峰附近细扫定位精确峰位，"
            f"然后分别在 25℃ 和 45℃ 下测量峰位处的计数率，最后给出实验报告。"
            f"仪器前缀是 {inst.safety.prefix}。")
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    for turn in range(max_turns):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS, temperature=0.2,
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            print("\n=== 智能体的最终回答 ===")
            print(message.content)
            return

        for call in message.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"\n[第 {turn + 1} 轮] → {name}({json.dumps(args, ensure_ascii=False)})")
            result = call_tool(inst, name, args)
            print(f"     ← {json.dumps(trim(result, 400), ensure_ascii=False)}")
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(trim(result), ensure_ascii=False),
            })
    print(f"⚠️ 达到最大轮次 {max_turns}，主动停止（防止失控循环）。")


def main() -> int:
    ap = argparse.ArgumentParser(description="智能体自主实验闭环演示")
    ap.add_argument("--policy", choices=["script", "llm"], default="script")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true",
                    help="演习模式：所有检查照做，但不真正写设备")
    args = ap.parse_args()

    inst = Instrument("c08_instrument.json", dry_run=args.dry_run or None,
                      actor=f"agent-{args.policy}", echo=True)
    print(f"仪器：{inst.safety.instrument}  干跑={inst.safety.dry_run}  "
          f"审计日志={inst.safety.audit_path}")
    try:
        if args.policy == "script":
            run_scripted(inst)
        else:
            run_llm(inst, args.model, args.max_turns)
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，执行急停")
        inst.emergency_stop("用户中断")
    finally:
        inst.safety.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
