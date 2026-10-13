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


if __name__ == "__main__":
    mcp.run(transport="sse")
