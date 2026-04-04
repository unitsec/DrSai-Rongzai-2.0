
import asyncio,json,time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client

async def main():

    gsa_path = "/home/wangh/rongzai-agent/tools/GSAS-II-master/tests/testData/RUN0023568_group5.gsa"  # 必需的gsa文件路径（当gpx_path为None时必需）
    instprm_path="/home/wangh/rongzai-agent/tools/GSAS-II-master/tests/testData/RT_group5.instprm" # 必需的instprm文件路径（当gpx_path为None时必需）
    cif_paths=["/home/wangh/rongzai-agent/tools/GSAS-II-master/tests/testData/Si.cif"]  # 可选的结构文件列表（当gpx_path为None时必需）
    gpx_path= 'tools/GSAS-II-master/tmp/5f371c5c-e472-4b0f-9c87-5c9423375183.gpx'
    refine_params = {}
    task_id = None


    # 连接到你的 SSE 服务器
    async with sse_client("http://localhost:42501/sse") as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化会话
            await session.initialize()
            
            # 列出所有可用的工具
            # tools = await session.list_tools()
            # print("可用工具:", tools)
            
            # 调用具体工具（请替换为实际工具名和参数）
            result = await session.call_tool(
                "RefineToolsGSASII",  # 替换为实际的工具名称
                arguments={"gsa_path": gsa_path, "instprm_path": instprm_path, "cif_paths": cif_paths, "gpx_path": gpx_path, "refine_params": refine_params, "task_id": task_id}
            )
            if getattr(result, "structuredContent", None):
                data = result.structuredContent
            elif getattr(result, "content", None):
                text = getattr(result.content[0], "text", "")
                data = json.loads(text) if text else {}

            print("工具调用结果:", data)
            task_id = data['result']['task_id']  # 获取工具调用返回的task_id

            time.sleep(1)  # 等待一段时间以确保工具开始执行
            # 轮询查询工具执行状态
            while True:
                status_result = await session.call_tool(
                    "RefineToolsGSASII",  # 替换为实际的工具名称
                    arguments={"task_id": task_id}
                )
                if getattr(status_result, "structuredContent", None):
                    status_data = status_result.structuredContent
                elif getattr(status_result, "content", None):
                    text = getattr(status_result.content[0], "text", "")
                    status_data = json.loads(text) if text else {}

                print("当前任务状态:", status_data["result"]["state"])
                if status_data["result"]["state"] == "completed":
                    print("最终结果:", status_data["result"])
                    break
                time.sleep(2)  # 每隔2秒查询一次


if __name__ == "__main__":
    asyncio.run(main())