from pathlib import Path
import importlib.util
import venv
import asyncio, os

from drsai.modules.components.model_client import  HepAIChatCompletionClient, ModelFamily
from drsai.modules.components.model_client.anthropic import (
    HepAIAnthropicChatCompletionClient,
    get_info,
    get_token_limit,
    _MODEL_INFO
)
from drsai.modules.components.model_context import DrSaiChatCompletionContext
from drsai.modules.agents.skills_agent import SkillAgent, DrSaiAssistant
from drsai.modules.baseagent import (
    DockerCommandLineCodeExecutor,
    LocalCommandLineCodeExecutor,
    CodeBlock
)
from drsai.modules.managers.database import DatabaseManager
from drsai.modules.managers.messages import (
    ModelClientStreamingChunkEvent,
    TextMessage,)
from drsai.configs.constant import RUNS_DIR

here = Path(__file__).parent.absolute()

import asyncio
from drsai.backend import run_worker

from src.core.agent.agent import RongZaiAgent
from src.tools import SkillRegistryManager
import os
from dotenv import load_dotenv
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

# async def get_GSASII_Workbench() -> StaticWorkbench:
#     tools = []
#     tools.extend(await mcp_server_tools(SseServerParams(
#         url = "http://127.0.0.1:42501/sse",
#         # url="http://202.122.37.163:42851/sse",
#         timeout = 30,
#     )))
#     return StaticWorkbench(tools)

async def create_agent(
        api_key: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        db_manager: DatabaseManager | None = None,
        defult_config_name: str | None = "default",
    ) -> RongZaiAgent:

    def set_model_client(defult_config_name: str|None = "deepseek-v3.2(No image)") -> HepAIAnthropicChatCompletionClient| HepAIChatCompletionClient:
        llm_model = llm_mode_config.get(defult_config_name, "openai/gpt-4o")
        if ("claude" in llm_model) or ("minimax" in llm_model):
            model_client = HepAIAnthropicChatCompletionClient(
                # model="claude-haiku-4-5",
                # model="claude-sonnet-4-6",
                model=llm_model,
                base_url="https://aiapi.ihep.ac.cn/apiv2/anthropic",
                # base_url = "https://api.zhizengzeng.com/anthropic",
                # api_key=api_key or os.environ.get("HEPAI_API_KEY"),
                api_key=api_key,
                # api_key=os.environ.get("ZZZ_API_KEY"),
                model_info=_MODEL_INFO["claude-sonnet-4-5"],
                # temperature=0.5,
                max_tokens=60000,
            )
        else:
            is_vision = True
            if "deepseek" in llm_model:
                is_vision = False
            model_client = HepAIChatCompletionClient(
                # model="openai/gpt-4o",
                model=llm_model,
                # api_key=api_key or os.environ.get("HEPAI_API_KEY"),
                api_key=api_key,
                base_url="https://aiapi.ihep.ac.cn/apiv2",
                model_info={
                        "vision": is_vision,
                        "function_calling": True,  # You must sure that the model can handle function calling
                        "json_output": True,
                        "structured_output": False,
                        "family": ModelFamily.GPT_41,
                        "multiple_system_messages":True,
                        "token_model": "gpt-4o-2024-11-20", # Default model for token counting
                    }
            )
        
        return model_client
    
    # Code executor and working directory
    WORKDIR=os.getenv("WORKDIR")

    # Sub-agents configuration
    SUB_AGENTS = {
        "coder": {
            "type": "DrSaiAgent",
            "description": "Full agent for writing codes, implementing features and fixing bugs",
            "tools": ["run_bash", "run_read", "run_write", "run_edit"],
            "prompt": """You are a coding agent. Implement the requested changes efficiently. 
If you want to test your code or editting, you must generate a shell script and ask sub agent-coder_executor to execute the code. The style of shell script should be as follows:

```bash

# filename: xxx.sh

your_code

```
""",
        },
        "coder_executor": {
            "type": "CodeExecutorAgent",
            "description": "A computer terminal that performs no other action than running Python scripts (provided to it quoted in ```python code blocks), or sh shell scripts (provided to it quoted in ```sh code blocks).",
            "tools": [],
            "prompt": "A Code Execution Agent that generates and executes Python and shell scripts based on user instructions. Python code should be provided in ```python code blocks, and sh shell scripts should be provided in ```sh code blocks for execution. It ensures correctness, efficiency, and minimal errors while gracefully handling edge cases.",
        },
    }

    SYSTEM = """You are a helpful and precise assistant for helping users to analyze and refine their powder neutron diffraction data. You have expertise in using GSASII software for data analysis and refinement. You can understand user requests, analyze the data, and provide step-by-step guidance on how to use GSASII to achieve the desired results. Always ensure that your instructions are clear and concise, and provide any necessary code snippets or commands that the user can directly use in GSASII. Your goal is to assist users in efficiently analyzing their powder neutron diffraction data and refining their results using GSASII."""


    return RongZaiAgent(
        name = "RongZai_Agent",
        model_client=set_model_client(defult_config_name),
        system_message=SYSTEM,
        reflect_on_tool_use=False,
        model_client_stream=True,  # Enable streaming tokens from the model client.
        # model_context=long_memory_context,
        tools=[],
        # drsaiAgent specific
        thread_id=thread_id,
        db_manager=db_manager,
        user_id=user_id,
        set_model_client=set_model_client,
        llm_mode_config=llm_mode_config,

        # skills and executor
        skills_dir=os.getenv("SYSTEM_SKILLS_DIR"),
        # executor=local_executor,

        work_dir=WORKDIR,
        only_in_workspace=True,
        extra_work_dirs=[],
        sub_agent_config = SUB_AGENTS,
        max_turn_count=20,
        token_limit=50000,
        rag_flow_url=os.getenv('RAGFLOW_URL'),
        rag_flow_token=os.getenv('RAGFLOW_TOKEN'),
        memory_dataset_id=os.getenv('MEMORY_DATASET_ID'),
    )

if __name__ == "__main__":
    
    asyncio.run(
        run_worker(
            # 智能体注册信息
            agent_name="RongZai_Agent",
            author="wanghao96@ihep.ac.cn",
            # permission=PERMISSION,
            description = "蓉仔——你的粉末中子衍射精修助手  by中国散裂中子源数据分析组",
            version = "0.1.0",
            logo="/home/dur/rongzai-agent/src/configs/resized_rzera_logo.png",
            examples=[],
            agent_config = llm_mode_config,
            defult_config_name = "default",
            # 智能体实体
            agent_factory=create_agent, 
            # 后端服务配置
            port = 42812, 
            # 是否注册到HepAI智能体平台
            no_register=False,
            drsai_dir = "/home/wangh/rongzai-agent/tmp",
            # 是否注册为OpenWebUI的pipeline
            enable_openwebui_pipeline=True, 
            history_mode = "backend",
            # use_api_key_mode = "backend",
            join_topics = ["test"],
            metadata={"others": "test"},
            link_wechat = False,
            
            
        )
    )

