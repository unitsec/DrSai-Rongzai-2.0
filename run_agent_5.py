import sys
import json
from pathlib import Path
here = Path(__file__).parent.absolute()
# agent_path = str(here.parent)
# sys.path.append(agent_path)

import asyncio
from drsai import (
    HepAIChatCompletionClient, 
    run_backend, 
    run_console, 
    run_worker,
    DrSaiAPP
    )
from autogen_agentchat.messages import TextMessage
from autogen_core.tools import StaticWorkbench
from autogen_ext.tools.mcp import (
    StdioServerParams, 
    SseServerParams,
    mcp_server_tools)
# from src.core.agent.reply_function import action_reply_function
from src.core.agent.agent_config import DataAgentHandoff

from src.core.agent.agent import RongZaiAgent
from autogen_core.model_context import (
    BufferedChatCompletionContext,)

from dotenv import load_dotenv
load_dotenv()
import os
data_dir = os.getenv("WORKER_SPACE", "")

async def get_GSASII_Workbench() -> StaticWorkbench:
    tools = []
    tools.extend(await mcp_server_tools(SseServerParams(
        url = "http://127.0.0.1:42501/sse",
        # url="http://202.122.37.163:42851/sse",
        timeout = 30,
    )))
    return StaticWorkbench(tools)

async def create_agent() -> RongZaiAgent:

    model_client_qwen3_235b_a22b = HepAIChatCompletionClient(
        model="aliyun/qwen3-235b-a22b",
    )
    model_client_qwen3_max_preview = HepAIChatCompletionClient(
        model="aliyun/qwen3-max-preview",
    )
    model_client_gpt_4_1 = HepAIChatCompletionClient(
        model="openai/gpt-4.1",
        api_key=os.getenv("HEPAI_API_KEY"),
    )
    model_client_gpt_5 = HepAIChatCompletionClient(
        model="openai/gpt-5",
        api_key=os.getenv("HEPAI_API_KEY"),
    )
    model_client_gpt_5_2 = HepAIChatCompletionClient(
        model="openai/gpt-5.2",
        api_key=os.getenv("HEPAI_API_KEY"),
    )
    model_client_v3 = HepAIChatCompletionClient(
        model="deepseek-ai/deepseek-v3",
    )
    model_client_claude = HepAIChatCompletionClient(
        model="anthropic/claude-sonnet-4-5",
    )
    try:
        GSASII_Workbench = await asyncio.wait_for(get_GSASII_Workbench(), timeout=60)
    except asyncio.TimeoutError:
        raise Exception("获取 Workbench 超时，请检查服务器连接")
    tool_info = ""
    for tool in GSASII_Workbench._tools:
        tool_info += f"{tool.name}({tool.description})\n"
    return RongZaiAgent(
        "RongZai_Agent",
        # model_client=model_client_qwen3_235b_a22b,
        model_client=model_client_gpt_5,
        handoffs=[DataAgentHandoff(target="user", description="在无法完成相关操作时，需要向用户询问你需要的信息。")],
        system_message=f"""你是一个擅长中子衍射数据精修的科研狗,如果用户对你打招呼，你就回复汪汪，如果用户问你是谁，你说你是一条狗， 你可以使用以下工具：\n{tool_info}。</no_think>""",
        description=f"""中子衍射数据和PDF数据精修以及中子衍射对称性预测的专家。""",
        model_context=BufferedChatCompletionContext(buffer_size = 50), # 限制最多2、50条消息
        data_dir = data_dir,
        GSASII_Workbench = GSASII_Workbench,
        sleep_time = 3,
    )


async def test():

    drsaiapp = DrSaiAPP(agent_factory=create_agent)
    stream =  drsaiapp.a_drsai_ui_completions(
        messages = [TextMessage(
            # content="我需要对BL16的RUN0023568的group5的PDF数据进行精修处理，PDF数据精修对应的CIF文件为mp-149.cif，精修参数设置qmax=30，qdamp=0.01，rmax=20,rmin=1.5", 
            # content="我需要对BL16的RUN0023568的group5的PDF数据进行精修处理，PDF数据精修对应的CIF文件为mp-149.cif和Ni.cif，精修参数设置qmax=30，qdamp=0.01，rmax=20,rmin=1.5", 
            # content="需要对BL16的RUN0025914的group5的中子衍散数据进行精修处理，精修的CIF文件为SrAgSb.cif和SrAg.cif",
            # content="需要对BL09的RUN0000719的groupHA和groupMA的中子衍散数据进行精修, 精修的CIF文件为zeolite.cif。",
            # content="需要对BL16的RUN0023568的group5的中子衍散数据进行精修处理，精修的CIF文件为mp-149.cif",
            # content="需要对BL16的RUN0023568的group2的中子衍散数据使用AI进行晶系、空间群、晶胞参数预测。", 
            # content="我需要对BL16的RUN0023568的group5的中子衍射数据进行模拟, 晶体结构是Si.cif", 
            source="user", 
            metadata={"timestamp": "1626713600"}).model_dump(mode="json")],
        stream=True,
        use_api_key_mode = "frontend",  # Use frontend API key mode
        api_key = os.environ.get("HEPAI_API_KEY"),
        chat_id = "test_chat_id",
        )

    text_messages = []
    async for message in stream:
        json_str = message.lstrip('data: ')
        oai_json = json.loads(json_str)
        if oai_json.get("type", "") == "ModelClientStreamingChunkEvent":
            textchunck = oai_json.get("content", "")
            sys.stdout.write(textchunck)
            sys.stdout.flush()
        else:
            text_messages.append(oai_json)
    print(text_messages)

if __name__ == "__main__":
    # asyncio.run(test())
    
    asyncio.run(
        run_worker(
            # 智能体注册信息
            agent_name="RongZai_Agent(gpt-5)",
            author="wanghao96@ihep.ac.cn",
            permission='groups: drsai, pagy; users: admin, xiongdb@ihep.ac.cn, ddf_free; owner: xiongdb@ihep.ac.cn',
            description = "你的粉末中子衍射精修助手-by中国散裂中子源数据分析组",
            version = "0.1.0",
            logo="/home/dur/rozai_agent_test/src/configs/resized_rzera_logo.png",
            # 智能体实体
            agent_factory=create_agent, 
            # 后端服务配置
            port = 42812, 
            no_register=False,
            enable_openwebui_pipeline=True, 
            history_mode = "backend",
            use_api_key_mode = "backend",
        )
    )

    # Deprecated
    # asyncio.run(run_console(
    #     agent_factory=create_agent, 
    #     # task="需要对RUN0021905衍散数据进行精修处理，精修的CIF文件为mp-165，主要对晶胞参数精修执行2次， 不执行尺寸和应变精修"
    #     task="需要对RUN0021905衍散数据和PDF数据进行精修处理，精修的CIF文件为bl16_21905.cif，PDF的精修参数设置qmax=30，qdamp=0.01，rmax=20,rmin=1.5"
    #     ))
    # pipelines_dir = str(here.joinpath("src/pipelines"))
    # asyncio.run(run_backend(
    #     agent_factory=create_agent, 
    #     history_mode = "frontend",
    #     port = 42808,
    #     enable_openwebui_pipeline=True,
    #     model_name = "RoZai_agent",
    #     pipelines_dir = pipelines_dir
    #     ))
