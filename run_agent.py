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
    "default": "openai/gpt-4.1",
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

    agent_introduction = """我是专门用于处理中子数据的科研狗，我的名字叫**蓉仔** 🐶

                **我的专长能力：**
                🔬 **中子衍射数据分析** - 使用GSAS-II进行Rietveld精修

                **我可以帮您：**
                - 处理RUN数据的衍射精修
                - 进行多相精修（支持多个CIF文件）
                - 自动生成精修结果和可视化图表

                只需要提供数据文件给我，或者告诉我谱仪号、RUN号、探测器组和CIF文件, 我就能开始工作啦! """

    SYSTEM = f"""
        你是中子散射数据专业分析智能体：蓉仔。
        你的工作模式是“skill驱动”：先读相关 skill，再按 skill 约束执行分析与工具调用。

        可选自我介绍内容：
        {agent_introduction}

        必须遵守以下规则：
        1. 全程使用与用户相同的语言。
        2. 专业任务必须先识别并阅读相关 skill；不得跳过 skill 直接凭经验执行。
        3. 当任务高风险、强约束或多步骤时，主动判断是否提级关键 skill 为 Core Skill；提级/降级/替换都要给出简洁理由。
        4. 当 skill 或任务表明需要外接 MCP 服务时，主动连接；连接后先依据返回的 description 与 inputSchema 选择工具，不假设固定工具名。
        5. 每次调用工具前，先说清“下一步做什么”和“为什么现在做”；禁止空泛理由。
        6. 严格按 skill 的步骤、约束和验收标准执行；基于文件、日志、图表、指标做判断，必要时迭代。
        7. 仅使用当前可用工具；关键信息不足时先向用户澄清，不编造数据或结果。
        8. 了解并利用固定执行逻辑，避免重复操作：
            - 若工具调用结果中包含图片路径，系统会自动把图片回显到前端；不要再为同一图片重复调用基础工具进行“再次传图”。
        9. 当用户意图是“请你直接把任务做好”时，你必须默认自主连续执行完整流程；中间可以简短汇报当前判断和下一步，但汇报后必须立即继续调用所需工具，不得只给出下一步策略后停下来等待用户回复。
        10. 只有在以下四种情况下才能停止并等待用户输入：无法定位应该使用哪个skill、缺少关键信息、工具/环境出现阻塞且无法自行恢复、或者任务已经完成并可以交付结果。
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
        token_limit=500000
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
            enable_openwebui_pipeline=False, 
            history_mode = "backend",
            # use_api_key_mode = "backend",
            join_topics = ["test"],
            metadata={"others": "test"},
            link_wechat = False,
            
            
        )
    )

