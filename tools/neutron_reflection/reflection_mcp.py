import os
import re
import threading
import uuid

os.environ["MPLBACKEND"] = "Agg"

from mcp.server.fastmcp import FastMCP
import sys
from pathlib import Path

# Use the bundled interface unless deployment explicitly overrides it.
interface_dir = os.environ.get("NEUTRON_REFLECTION_INTERFACE_DIR")
interface_path = (
    Path(interface_dir).expanduser().resolve()
    if interface_dir
    else Path(__file__).resolve().parent / "interface"
)
if not (interface_path / "agent_interface.py").is_file():
    raise RuntimeError(f"找不到接口文件：{interface_path / 'agent_interface.py'}")
sys.path.insert(0, str(interface_path))

from agent_interface import process_reflection
from batch_reflection import prepare_groups, execute_groups

mcp = FastMCP(
    "BL08A Neutron Reflection",
    host="127.0.0.1",
    port=42520,
)

processing_lock = threading.Lock()


@mcp.tool()
def process_neutron_reflection(
    runs: list[str], user_id: str, session_id: str, mission_name: str
) -> dict:
    """处理 CSNS BL08A 中子反射数据。

    runs 为 RUN 编号列表，例如 ["RUN0000963", "RUN0000964"]。
    必须包含且仅包含一个直通 RUN，并包含至少一个反射 RUN。
    根据服务器配置读取数据，生成反射率数据、图片和 summary.csv。
    user_id、session_id 必须来自框架当前用户和会话，不能猜测。
    mission_name 为任务标识（使用英文、数字、下划线或连字符）。
    每次调用创建唯一任务子目录，返回服务器路径和 task_id。
    """
    if len(runs) < 2:
        raise ValueError("至少提供一个直通 RUN 和一个反射 RUN")
    if any(re.fullmatch(r"RUN[0-9]{7}", run) is None for run in runs):
        raise ValueError("RUN 编号格式应为 RUN 加七位数字")
    if len(set(runs)) != len(runs):
        raise ValueError("RUN 编号不能重复")

    for name, value in (("user_id", user_id), ("session_id", session_id),
                        ("mission_name", mission_name)):
        if (not value or len(value) > 128 or value in (".", "..")
                or re.fullmatch(r"[A-Za-z0-9_@.+-]+", value) is None):
            raise ValueError(f"{name} 必须是有效目录标识，不能包含路径分隔符")
    root_value = os.environ.get("WORKDIR")
    if not root_value:
        raise RuntimeError("请设置 WORKDIR 为结果工作区的绝对路径")
    root = Path(root_value).expanduser()
    if not root.is_absolute():
        raise ValueError("WORKDIR 必须是绝对路径")
    root = root.resolve()
    output = (root / user_id / session_id / mission_name / uuid.uuid4().hex).resolve()
    if not output.is_relative_to(root):
        raise ValueError("输出目录超出 WORKDIR")
    output.mkdir(parents=True, exist_ok=False)
    # Matplotlib processing remains serial within this service process.
    with processing_lock:
        result = process_reflection(runs, output_dir=str(output))
        return {**result, "user_id": user_id, "session_id": session_id,
                "mission_name": mission_name, "task_id": output.name}


@mcp.tool()
def process_neutron_reflection_groups(
    groups: list[dict], user_id: str, session_id: str, mission_name: str
) -> dict:
    """处理用户明确划分的多组BL08A数据，输出一张反射率总图和总summary.csv。

    groups 每项字段：group_name（可选名称）、runs（必填RUN编号列表）、
    data_source（管理员数据源别名，默认default）、direct_run（用户明确指定时填写）、
    angles_deg（反射RUN到角度的映射，单位度，如{"RUN0000216":1.22}）、
    confirm_metadata_override（默认false，仅用户明确确认覆盖冲突元数据后为true）。
    示例：[{"runs":["RUN0000216","RUN0000219"],"data_source":"legacy_home",
    "direct_run":"RUN0000219","angles_deg":{"RUN0000216":1.22}}]。
    不知道直通或角度时不要猜，先只传用户明确的参数。元数据缺失或冲突时
    返回status=needs_input及questions，不执行计算；询问用户后补全参数再调用。
    各组独立归一化，总图仅叠加曲线，不拼接或平均。
    当前默认以PHITS 3.37性能经验模型拟合m及截止宽度，不是膜层结构拟合。
    用户summary.csv只含m与r_envelope(%)，后者是平台到截止中心的包络外推值。
    总图是带原始反射率误差的散点，不连接。其他拟合参数只留内部记录。
    analysis_status=needs_review时需报告质量警告，不得称为厂家验收结果。
    user_id、session_id来自当前真实会话。success不代表数据质量已验证。
    """
    for name, value in (("user_id", user_id), ("session_id", session_id),
                        ("mission_name", mission_name)):
        if (not value or len(value) > 128 or value in (".", "..")
                or re.fullmatch(r"[A-Za-z0-9_@.+-]+", value) is None):
            raise ValueError(f"{name} 必须是有效目录标识，不能包含路径分隔符")
    root = Path(os.environ.get("WORKDIR", "")).expanduser()
    if not os.environ.get("WORKDIR") or not root.is_absolute():
        raise ValueError("WORKDIR 必须配置为结果工作区的绝对路径")
    import json
    with (interface_path / "machine_paths.json").open(encoding="utf-8") as f:
        paths = json.load(f)
    plans, questions = prepare_groups(groups, paths)
    if questions:
        return {"status": "needs_input", "questions": questions,
                "message": "尚未计算。请询问用户直通RUN、各反射角（度）或冲突覆盖确认，再提交完整分组。"}
    root = root.resolve()
    output = (root / user_id / session_id / mission_name / uuid.uuid4().hex).resolve()
    if not output.is_relative_to(root):
        raise ValueError("输出目录超出 WORKDIR")
    output.mkdir(parents=True, exist_ok=False)
    with processing_lock:
        try:
            result = execute_groups(plans, paths, output)
        except Exception as exc:
            result = {"status": "failed", "error": str(exc), "output_path": str(output),
                      "message": "批次未全部完成，目录可能含部分结果，不得报告总批次成功。"}
    return {**result, "user_id": user_id, "session_id": session_id,
            "mission_name": mission_name, "task_id": output.name}


if __name__ == "__main__":
    mcp.run(transport="sse")
