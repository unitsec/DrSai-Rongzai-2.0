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
print(f"PERMISSION: {PERMISSION}")

llm_mode_config = {
    "quick refinement": "openai/gpt-4.1",
    "deep refinement": "openai/gpt-5",
    "qwen3 Max": "aliyun/qwen3-max",
    "qwen3 Coder+": "aliyun/qwen3-coder-plus",
    "minimax": "minimax/minimax-m2.7",
    "磐石": "scienceone/s1-base-ultra",
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
    print(
        f"[LLM_SELECT] mode={selected_mode}, model={selected_model},"
        f"thread_id={thread_id}, user_id={user_id}",
        flush=True,
    )

    # backend mode may not pass api_key into agent_factory; keep a local fallback.
    is_vip_user = bool(user_id and user_id in PERMISSION_USERS)

    if is_vip_user:
        model_client = HepAIChatCompletionClient(
            model=selected_model,
            api_key="sk-qywwUDyCxrUQYyyxhUCNKZbasiRwAzszJSdIQXdsWdsjHOV",
            base_url="https://aiapi.ihep.ac.cn/apiv2",
        )
    else:
        model_client = HepAIChatCompletionClient(
            model=selected_model,
            api_key=api_key,
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

if __name__ == "__main__":
    
    asyncio.run(
        run_worker(
            # 智能体注册信息
            agent_name="RongZai_Agent",
            author="wanghao96@ihep.ac.cn",
            permission=PERMISSION,
            description = "蓉仔——你的粉末中子衍射精修助手  by中国散裂中子源数据分析组",
            version = "0.1.0",
            logo="/home/dur/rongzai-agent/src/configs/resized_rzera_logo.png",
            # 智能体实体
            agent_factory=create_agent, 
            # 后端服务配置
            port = 42812, 
            history_mode = "backend",
            # 智能体数据库地址
            engine_uri = f"sqlite:////{appdir}/drsai.db",
            # 数据库及文件存储目录
            base_dir = appdir, 
            agent_config = llm_mode_config,
            defult_config_name = "default",
            # 是否注册到HepAI智能体平台
            no_register=False,
            # 是否注册为OpenWebUI的pipeline
            enable_openwebui_pipeline=True, 
            # 使用backend/frontend的api_key
            use_api_key_mode = "frontend"
        )
    )

