import os
import asyncio

from autogen_core import CancellationToken, FunctionCall
from autogen_core.tools import (
    BaseTool, 
    Workbench,
    StaticWorkbench,
    FunctionTool,
    ToolSchema)
from autogen_ext.tools.mcp import (
    StdioServerParams, 
    SseServerParams,
    mcp_server_tools)
from dotenv import load_dotenv
load_dotenv()

from src.configs.constants import tools_dir


async def get_GSASII_Workbench() -> StaticWorkbench:
    tools = []
    # tools.extend(await mcp_server_tools(StdioServerParams(
    #                         command="conda",
    #                         args=[
    #                             "run",
    #                             "-n",
    #                             "gsas2_py311",
    #                             "--live-stream",
    #                             "python",
    #                             f"{tools_dir}/GSAS-II-master/GSASII/0neutron_gsas_refine.py",
    #                             ],
    #                         env=None)
    #                 ) )
    tools.extend(await mcp_server_tools(SseServerParams(
        url = "http://127.0.0.1:50051/sse",
        # url = "http://202.122.37.163:42851/sse",
        timeout = 30,
    )))
    workbench =  StaticWorkbench(tools)
    
    gsa_file = "/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/BL16/RUNdataFiles/RUN0021905/RUN0021905_group3.gsa"
    instprm_file = "/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/BL16/instrumentFiles/RT_group3.instprm"
    cifs = ["/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/cifFiles/bl16_21905.cif"]
    task_id = "12341"
    ToolResult = await workbench.call_tool(
        name = "write_info",
        arguments = {
            "gsa_file": gsa_file,
            "instprm_file": instprm_file,
            "cif_files": cifs,
            "task_id": task_id,
        })
    print(ToolResult)

    # await asyncio.sleep(30)

    gsa_file = "/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/BL16/RUNdataFiles/RUN0021905/RUN0021905_group3.gsa"
    instprm_file = "/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/BL16/instrumentFiles/RT_group3.instprm"
    cifs = ["/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/cifFiles/bl16_21905.cif"]
    task_id = "12342"
    ToolResult = await workbench.call_tool(
        name = "write_info",
        arguments = {
            "gsa_file": gsa_file,
            "instprm_file": instprm_file,
            "cif_files": cifs,
            "task_id": task_id,
        })
    print(ToolResult)



async def get_pdf_Workbench() -> StaticWorkbench:
    tools = []
    
    tools.extend(await mcp_server_tools(SseServerParams(
        url = "http://127.0.0.1:50052/sse",
        # url = "http://202.122.37.163:42852/sse",
        timeout = 30,
    )))
    workbench =  StaticWorkbench(tools)
    
    data_file = "/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/BL16/RUNdataFiles/RUN0021905/G_r_RUN0021905.txt"
    structure_file = "/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/cifFiles/mp-149.cif"
    # structure_file = "/aifs/user/home/xiongdb/VSproject/rozai_agent/datasets/cifFiles/bl16_21905.cif"
    task_id = "123421"
    thread_id = "ISYOURBABY"
    ToolResult = await workbench.call_tool(
        name = "write_info",
        arguments = {
            "data_file": data_file,
            "structure_file": structure_file,
            "qmax": 30.0,
            "qdamp": 0.01,
            "rmin": 1.5,
            "rmax": 20.0,
            "task_id": task_id,
            "thread_id": thread_id
        })
    print(ToolResult)

    # await asyncio.sleep(30)

    
if __name__ == '__main__':
    asyncio.run(get_GSASII_Workbench())
    # asyncio.run(get_pdf_Workbench())