"""c08_mcp_server.py —— 把仪器工具暴露成 MCP 服务（第 8 章）

MCP（Model Context Protocol）是目前最通用的"给大模型接工具"的协议。
蓉仔（RONGZAI）通过 `McpWorkbench` 连接 MCP 服务，所以这个文件就是
"智能体 → 实验设备"的正式接口。

两种传输方式：

    # 1) stdio：蓉仔用 StdioServerParams 直接拉起这个进程（最简单，适合本机）
    python c08_mcp_server.py

    # 2) SSE：蓉仔用 SseServerParams 连 http://127.0.0.1:42521/sse
    #    （和 start_tools.sh 里其它服务一样，适合常驻服务）
    python c08_mcp_server.py --sse --port 42521

暴露的工具（名字就是大模型看到的函数名，所以起名要"动词 + 语义对象"）：

    describe_instrument   这台仪器有哪些通道、单位、限值、联锁
    self_check            上机前体检（连不通/超范围都会报出来）
    snapshot              读所有通道的当前值
    read_channel          读一个通道
    write_channel         写一个通道（必须给 reason）
    hold_temperature      调到目标温度并稳定
    move_axis             走轴并等到位
    run_scan              扫描 + 自动找峰
    emergency_stop        急停到安全状态
    get_progress          长任务进展（防止智能体/用户面对长时间静默）

设计要点（第 8 章正文会展开）：
  1. **工具描述就是提示词**。写清楚单位、限值、什么时候用、失败怎么办，
     比在 system prompt 里塞一大堆规则有效得多。
  2. **返回结构化结果**，不要返回一大段自然语言 —— 让模型看得懂、能判断。
  3. **不要在工具里抛异常**：把失败变成 {"ok": false, "error": ..., "hint": ...}，
     模型就能自己纠正，而不是直接崩掉整轮对话。
  4. **危险动作要能被人工拦下**：dry_run 开关 + 审计日志 + 急停。
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

# 让模块无论从哪个目录启动都能找到同目录的仪器代码
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

from c08_tools import Instrument  # noqa: E402

CONFIG = os.environ.get("EPICS_INSTRUMENT_CONFIG", "c08_instrument.json")
DRY_RUN = os.environ.get("EPICS_INSTRUMENT_DRY_RUN", "0") == "1"

mcp = FastMCP(
    "EPICS Instrument Tools",
    instructions=(
        "这是一个实验站控制服务。全部操作都经过安全层：白名单、限值、单步幅度、"
        "联锁、写入速率都会被检查，被拒绝时会返回 rule 字段。\n"
        "使用顺序建议：describe_instrument → self_check → snapshot → 动作。\n"
        "每个写操作都必须给出 reason（为什么写这个值）。\n"
        "长任务（hold_temperature / run_scan）可以用 get_progress 查看进展。\n"
        "如果安全层拒绝了你，不要尝试绕过它：读 error 和 hint，改变你的请求，"
        "或者向用户报告。"
    ),
    host=os.environ.get("EPICS_MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("EPICS_MCP_PORT", "42521")),
)

_instrument: Optional[Instrument] = None


def instrument() -> Instrument:
    """懒加载：MCP 服务启动时不立刻连设备，第一次调用工具时才连。"""
    global _instrument
    if _instrument is None:
        _instrument = Instrument(CONFIG, dry_run=DRY_RUN or None,
                                 actor="llm-agent", echo=True)
    return _instrument


# =============================================================================
# 只读工具
# =============================================================================
@mcp.tool(title="描述仪器")
def describe_instrument() -> Dict[str, Any]:
    """列出这台仪器的所有可用通道：名字、PV、单位、是否可写、限值、
    单步幅度、联锁依赖、以及安全状态。

    在开始任何操作之前**先调用这个工具**，不要凭猜测使用通道名。
    """
    return instrument().describe_instrument()


@mcp.tool(title="上机前体检")
def self_check() -> Dict[str, Any]:
    """检查所有通道是否可连通、读数是否在登记范围内、联锁状态是否符合预期。

    返回 problems 列表；problems 非空时不要开始实验。
    """
    return instrument().self_check()


@mcp.tool(title="读取全部通道")
def snapshot() -> Dict[str, Any]:
    """读一遍所有通道的当前值（含温度、角度、计数率、联锁标志）。

    每次决策前都建议先看一次，避免基于过期信息行动。
    """
    return instrument().snapshot()


@mcp.tool(title="读取一个通道")
def read_channel(channel: str) -> Dict[str, Any]:
    """读取指定通道的当前值。

    Args:
        channel: 通道名，必须是 describe_instrument 里列出的名字，
                 例如 "temperature"、"theta_rb"、"counts"。
    """
    return instrument().read_channel(channel)


# =============================================================================
# 写入 / 动作类工具
# =============================================================================
@mcp.tool(title="写一个通道")
def write_channel(channel: str, value: float, reason: str) -> Dict[str, Any]:
    """给一个可写通道设置数值。写操作必须提供理由，并且会经过安全层检查。

    Args:
        channel: 通道名，例如 "setpoint"、"heater"、"theta"、"run_permit"。
        value: 目标数值，单位见 describe_instrument（温度 degC、功率 percent）。
        reason: 为什么写这个值（会被记进审计日志），例如"开始升温到 60 度"。

    返回里 requested 是你请求的值，actual 是实际生效的值 ——
    永远以 actual 为准。若 ok=false，看 rule 和 hint。
    """
    return instrument().write_channel(channel, value, reason)


@mcp.tool(title="控温到目标温度")
def hold_temperature(target: float, tolerance: float = 0.5,
                     timeout_s: float = 300.0, reason: str = "") -> Dict[str, Any]:
    """把样品温度调到 target 并稳定在 ±tolerance 以内，然后返回证据。

    这是一个"动作"而不是"写一个值"：它内部会分步升降温、等待稳定、
    并把过程数据落盘。超时会返回 ok=false 以及当前温度。

    Args:
        target: 目标温度（摄氏度）。
        tolerance: 允许的稳定窗口，默认 0.5 ℃。
        timeout_s: 最长等待时间，默认 300 秒。
        reason: 实验意图说明，例如"测量 60 度下的衍射谱"。
    """
    return instrument().hold_temperature(target, tol=tolerance,
                                         timeout=timeout_s, reason=reason)


@mcp.tool(title="移动轴并等到位")
def move_axis(axis: str, target: float, reason: str = "") -> Dict[str, Any]:
    """移动一个轴（例如 theta）到目标位置，并等待回读值真的到位。

    Args:
        axis: 轴名，例如 "theta"。
        target: 目标位置，单位见 describe_instrument（角度 deg）。
        reason: 实验意图说明。
    """
    return instrument().move_axis(axis, target, reason)


@mcp.tool(title="扫描并找峰")
def run_scan(axis: str = "theta", detector: str = "counts",
             start: float = 10.0, stop: float = 30.0, step: float = 1.0,
             dwell_s: float = 0.2, samples: int = 1,
             reason: str = "") -> Dict[str, Any]:
    """做一次扫描：轴从 start 走到 stop，每点取一次探测器的数，自动找峰。

    适合"先粗扫找峰，再在峰附近细扫"。返回 peak（峰位、峰高、半高宽）、
    summary（文字摘要，适合直接读）和 data_file（落盘路径）。

    Args:
        axis: 扫描轴，默认 "theta"。
        detector: 探测器通道，默认 "counts"（counts/s）。
        start: 起始角度（deg）。
        stop: 结束角度（deg）。
        step: 步长（deg）。点数不要超过 500。
        dwell_s: 每点等待时间（秒），等电机和探测器稳定。
        samples: 每点取几次平均（降噪）。
        reason: 实验意图说明。
    """
    return instrument().run_scan(axis=axis, detector=detector, start=start,
                                 stop=stop, step=step, dwell=dwell_s,
                                 samples=samples, reason=reason)


@mcp.tool(title="急停")
def emergency_stop(reason: str = "agent requested stop") -> Dict[str, Any]:
    """立即把所有执行器写回安全状态（加热 0%、快门关闭、轴归零）。

    什么时候用：任何你觉得"情况不对"的时候 —— 温度异常、读数乱跳、
    设备没反应、或者用户喊停。**不要犹豫，先急停再排查。**
    """
    return instrument().emergency_stop(reason)


@mcp.tool(title="查看长任务进展")
def get_progress(last_lines: int = 10) -> Dict[str, Any]:
    """查看长任务（控温、扫描）当前进行到哪一步。

    返回 last_lines 里每行都是带时间戳的进度说明和 age_seconds，
    你应该把最新一行转述给用户，不要让用户面对长时间的静默。
    """
    return instrument().get_progress(last_lines)


def main() -> int:
    global CONFIG

    ap = argparse.ArgumentParser(description="EPICS 仪器控制 MCP 服务")
    ap.add_argument("--sse", action="store_true",
                    help="用 SSE 传输（常驻服务）；默认 stdio")
    ap.add_argument("--host", default=os.environ.get("EPICS_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("EPICS_MCP_PORT", "42521")))
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--dry-run", action="store_true",
                    help="演习模式：所有检查照做，但不真正写设备")
    args = ap.parse_args()

    CONFIG = args.config
    if args.dry_run:
        os.environ["EPICS_INSTRUMENT_DRY_RUN"] = "1"

    mcp.settings.host = args.host
    mcp.settings.port = args.port

    if args.sse:
        print(f"MCP 服务（SSE）启动在 http://{args.host}:{args.port}/sse")
        print(f"  配置 {args.config}；干跑模式 {args.dry_run}")
        mcp.run(transport="sse")
    else:
        # stdio 模式：不要把任何东西打印到 stdout！它会污染协议。
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
