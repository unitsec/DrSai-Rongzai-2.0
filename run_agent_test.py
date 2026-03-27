import sys
import json
import uuid
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
from drsai.modules.managers.database import DatabaseManager
# from src.core.agent.reply_function import action_reply_function
from src.core.agent.agent_config import DataAgentHandoff

from src.core.agent.agent import RongZaiAgent
from autogen_core.model_context import (
    BufferedChatCompletionContext,)

from dotenv import load_dotenv
import os
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
data_dir = os.getenv("WORKER_SPACE", "")
parent_path = os.path.dirname(os.path.abspath(__file__))
appdir = os.path.join(parent_path, "tmp/drsai")
os.makedirs(appdir, exist_ok=True)
VIP_USER_LIST = os.getenv("VIP_USER_LIST", "").split(",") if os.getenv("VIP_USER_LIST") else []


def _normalize_users(users: list[str]) -> list[str]:
    return [u.strip() for u in users if u and u.strip()]


PERMISSION_USERS = list(dict.fromkeys(_normalize_users(VIP_USER_LIST)))
PERMISSION = (
    f"groups: rongzai; users: {','.join(PERMISSION_USERS)}; "
    "owner: wanghao96@ihep.ac.cn"
)

llm_mode_config = {
    "quick refinement": "openai/gpt-4.1",
    "deep refinement": "openai/gpt-5",
    "default": "openai/gpt-5"
}

async def get_GSASII_Workbench() -> StaticWorkbench:
    tools = []
    tools.extend(await mcp_server_tools(SseServerParams(
        url = "http://127.0.0.1:42501/sse",
        # url="http://202.122.37.163:42851/sse",
        timeout = 30,
    )))
    return StaticWorkbench(tools)

async def create_agent(
        api_key: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        db_manager: DatabaseManager | None = None,
        defult_config_name: str | None = "default",
    ) -> RongZaiAgent:
    selected_mode = defult_config_name or "default"
    selected_model = llm_mode_config.get(selected_mode, llm_mode_config["default"])
    thread_id = thread_id or f"test-thread-{uuid.uuid4()}"
    user_id = user_id or "local_console_user"
    db_manager = db_manager or DatabaseManager(
        engine_uri=f"sqlite:////{appdir}/drsai_test.db",
        base_dir=Path(appdir),
    )
    db_init_response = db_manager.initialize_database()
    if not db_init_response.status:
        raise RuntimeError(f"初始化测试数据库失败: {db_init_response.message}")
    print(
        f"[LLM_SELECT] mode={selected_mode}, model={selected_model},"
        f"thread_id={thread_id}, user_id={user_id}",
        flush=True,
    )

    model_client = HepAIChatCompletionClient(
        # model="deepseek-ai/deepseek-r1",
        model=selected_model,
        api_key=os.environ.get("HEPAI_API_KEY"),
        base_url="https://aiapi.ihep.ac.cn/apiv2",
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
        model_client=model_client,
        handoffs=[DataAgentHandoff(target="user", description="在无法完成相关操作时，需要向用户询问你需要的信息。")],
        system_message=f"""你是一个擅长中子衍射数据精修的科研狗,如果用户对你打招呼，你就回复汪汪，如果用户问你是谁，你说你是一条狗。用户用什么语言，你就用什么语言。 你可以使用以下工具：\n{tool_info}。</no_think>""",
        description=f"""中子衍射数据和PDF数据精修以及中子衍射对称性预测的专家。""",
        model_context=BufferedChatCompletionContext(buffer_size = 50), # 限制最多2、50条消息
        data_dir = data_dir,
        GSASII_Workbench = GSASII_Workbench,
        sleep_time = 3,
        thread_id=thread_id,
        db_manager=db_manager,
        user_id=user_id,
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
    asyncio.run(run_console(agent_factory=create_agent, task="Hello?"))
    
    # asyncio.run(
    #     run_worker(
    #         # 智能体注册信息
    #         agent_name="RongZai_Agent",
    #         author="wanghao96@ihep.ac.cn",
    #         permission=PERMISSION,
    #         description = "你的粉末中子衍射精修助手-by中国散裂中子源数据分析组",
    #         version = "0.1.0",
    #         logo="/home/dur/rongzai-agent/src/configs/resized_rzera_logo.png",
    #         # 智能体实体
    #         agent_factory=create_agent, 
    #         # 后端服务配置
    #         port = 42812, 
    #         history_mode = "backend",
    #         # 智能体数据库地址
    #         engine_uri = f"sqlite:////{appdir}/drsai.db",
    #         # 数据库及文件存储目录
    #         base_dir = appdir, 
    #         agent_config = llm_mode_config,
    #         defult_config_name = "default",
    #         # 是否注册到HepAI智能体平台
    #         no_register=True,
    #         # 是否注册为OpenWebUI的pipeline
    #         enable_openwebui_pipeline=True, 
    #         # 使用backend/frontend的api_key
    #         use_api_key_mode = "fronted"
    #     )
    # )

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
