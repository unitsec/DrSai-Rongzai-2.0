import os
import re
import threading

os.environ["MPLBACKEND"] = "Agg"

from mcp.server.fastmcp import FastMCP
import sys
from pathlib import Path

interface_dir = os.environ.get("NEUTRON_REFLECTION_INTERFACE_DIR")
if not interface_dir:
    raise RuntimeError(
        "请设置 NEUTRON_REFLECTION_INTERFACE_DIR，指向包含 agent_interface.py 的目录"
    )
interface_path = Path(interface_dir).expanduser().resolve()
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
def process_neutron_reflection(runs: list[str]) -> dict:
    """处理 CSNS BL08A 中子反射数据。

    runs 为 RUN 编号列表，例如 ["RUN0000963", "RUN0000964"]。
    必须包含且仅包含一个直通 RUN，并包含至少一个反射 RUN。
    根据服务器配置读取数据，生成反射率数据、图片和 summary.csv。
    返回结果文件所在的服务器路径。
    """
    if len(runs) < 2:
        raise ValueError("至少提供一个直通 RUN 和一个反射 RUN")
    if any(re.fullmatch(r"RUN[0-9]{7}", run) is None for run in runs):
        raise ValueError("RUN 编号格式应为 RUN 加七位数字")
    if len(set(runs)) != len(runs):
        raise ValueError("RUN 编号不能重复")

    # 当前接口共用输出目录，串行执行，避免同时写入。
    with processing_lock:
        return process_reflection(runs)


if __name__ == "__main__":
    mcp.run(transport="sse")
