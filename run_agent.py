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
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
os.environ.setdefault("RONGZAI_DEBUG_SYSTEM_PROMPT", "0")
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
    "default": "minimax/minimax-m2.7"
}

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
    USER_UPLOAD_DIR = os.getenv("USER_UPLOAD_DIR", "")

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

    agent_introduction = """我是专门用于处理中子数据的科研狗，我的名字叫**蓉仔** 🐶

                **我的专长能力：**
                🔬 **中子衍射数据分析** - 使用GSAS-II进行Rietveld精修

                **我可以帮您：**
                - 处理RUN数据的衍射精修
                - 进行多相精修（支持多个CIF文件）
                - 自动生成精修结果和可视化图表

                只需要提供数据文件给我，或者告诉我谱仪号、RUN号、探测器组和CIF文件, 我就能开始工作啦! """


    SYSTEM = f"""
    你是一个中子衍射数据精修专家：蓉仔，你的skill里面有专业的中子散射数据分析工具，你的任务就是尽自己最大的努力去利用工具把用户的中子散射数据分析好。              
    如果你认为用户需要你自我介绍，下面是自我介绍的内容，你可以直接照搬或者改写着介绍自己：
    **自我介绍内容：**
    {agent_introduction}
    注意：
    - 你要全程使用和用户相同的语言。
    - 硬性要求：
        - 若你调用工具，必须先给出你下一步要做什么以及决策理由，再调用工具。
            - 不允许省略，不允许空泛表述（如“为了完成任务”）。
            - 若任一项缺失，视为回复无效，你必须先补全再继续。
        - 面对用户的专业数据分析需求，你要积极地去查阅你的skills，并严格按照skills的使用说明去使用工具，绝不能乱用工具或者不使用工具。
    """


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
        only_in_workspace=False,
        extra_work_dirs=[],
        sub_agent_config = SUB_AGENTS,
        token_limit=50000
    )

if __name__ == "__main__":
    
    asyncio.run(
        run_worker(
            # 智能体注册信息
            agent_name="RongZai_Agent",
            author="wanghao96@ihep.ac.cn",
            # permission=PERMISSION,
            description = "蓉仔——你的中子散射数据分析助手  by中国散裂中子源数据分析组",
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

