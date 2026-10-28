import os
import re
import threading
import uuid
from pathlib import Path

os.environ["MPLBACKEND"] = "Agg"

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import sys

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=True)

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
    port=int(os.getenv("MCP_NEUTRON_REFLECTION_PORT", "42520")),
)

processing_lock = threading.Lock()


@mcp.tool()
def process_neutron_reflection(
    runs: list[str], user_id: str, session_id: str, mission_name: str
) -> dict:
    """处理 CSNS BL08A 中子反射数据。

    runs 为 RUN 编号列表，例如 ["RUN0000963", "RUN0000964"]。
    兼容入口，转入与分组工具相同的身份预检、六项设置匹配和完整报告流程。
    缺参数或多候选返回needs_input，不绕过匹配校验；补充参数使用分组工具。
    user_id、session_id 必须来自框架当前用户和会话，不能猜测。
    mission_name 为任务标识（使用英文、数字、下划线或连字符）。
    每次调用创建唯一任务子目录，返回服务器路径和 task_id。
    """
    return process_neutron_reflection_groups(
        [{"runs": runs}], user_id, session_id, mission_name
    )


@mcp.tool()
def process_neutron_reflection_groups(
    groups: list[dict], user_id: str, session_id: str, mission_name: str
) -> dict:
    """按测试设置自动匹配直通或校验用户指定关系，处理多组BL08A数据，输出一张反射率总图和总summary.csv。

    未指定分组时，把用户提供的全部RUN放在同一项runs中。先按Rz识别身份，再逐条将反射与直通比较六项设置。
    每项max/min<=1.01才匹配；唯一候选自动使用，多候选询问，未匹配请用户补充直通。
    缺失Rz时询问roles及反射角；缺失或无效设置不能默认通过；全批次预检通过前不计算。
    groups 每项字段：group_name（可选名称）、runs（必填RUN编号列表）、
    data_root（可选课题/年份数据目录，其下为RUN/RUN，必须位于管理员数据根目录内）、
    data_source（管理员数据源别名，默认default）、direct_run（用户明确指定时填写）、
    angles_deg（反射RUN到角度的映射，单位度，如{"RUN0000216":1.22}）、
    roles（RUN到direct/reflection，用户明确身份时填写）、
    direct_assignments（反射RUN到直通RUN，用于多候选选择）、
    target_m（可选，用户提供的本项共同设计目标，不能从测量值猜测）、
    confirm_metadata_override（默认false，仅用户明确确认覆盖冲突元数据后为true）。
    示例：[{"runs":["RUN0000216","RUN0000219"],"data_source":"legacy_home",
    "direct_run":"RUN0000219","angles_deg":{"RUN0000216":1.22}}]。
    不知道直通或角度时不要猜，先只传用户明确的参数。元数据缺失或冲突时
    指定直通同样必须通过六项匹配，冲突覆盖不能豁免设置校验。
    返回status=needs_input及questions，不执行计算；询问用户后补全参数再调用。
    各组独立归一化，总图仅叠加曲线，不拼接或平均。
    当前默认以PHITS 3.37性能经验模型拟合m及截止宽度，不是膜层结构拟合。
    用户summary.csv依次包含Run、m、r_envelope(%)三列，后者是平台到截止中心的包络外推值。
    总图是带原始反射率误差的散点，不连接。其他拟合参数只留内部记录。
    质量诊断保留在内部记录，默认不主动展示。
    默认交付public_files中的一张m/R标注示意图、总图、summary.csv和所有反射率.dat的ZIP。
    image_delivery=files_event表示不要生成静态链接自动回显；统一用文件工具发送public_files四项。
    只展示representative_plot，不逐RUN展示其他图；使用presentation表格、conclusion及method_markdown。
    方法说明只给PHITS来源、模型公式和当前m/包络R定义，不展示内部诊断。
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
                "message": "尚未计算。按questions补齐身份、角度、匹配设置或直通选择，再提交完整请求。"}
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
