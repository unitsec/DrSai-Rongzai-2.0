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
from autogen_core.models import RequestUsage
import autogen_ext.models.openai._openai_client as _openai_client_patch_target
from drsai.modules.agents.skills_agent import SkillAgent, DrSaiAssistant
from drsai.modules.baseagent import (
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
from src.configs.system_prompt import RONGZAI_SYSTEM_PROMPT
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
os.environ.setdefault("RONGZAI_DEBUG_SYSTEM_PROMPT", "0")
parent_path = os.path.dirname(os.path.abspath(__file__))
appdir = os.path.join(parent_path, "tmp/drsai")
os.makedirs(appdir, exist_ok=True)

VIP_USER_LIST = os.getenv("VIP_USER_LIST", "").split(",") if os.getenv("VIP_USER_LIST") else []


def _normalize_users(users: list[str]) -> list[str]:
    return [u.strip() for u in users if u and u.strip()]


PERMISSION_USERS = list(dict.fromkeys(_normalize_users(VIP_USER_LIST)))
PERMISSION = (
    f"groups: rongzai,payg,hairongzai@ihep.ac.cn; users: {','.join(PERMISSION_USERS)}; "
    "owner: wanghao96@ihep.ac.cn"
)
print(f"PERMISSION: {PERMISSION}")


def _safe_add_usage(usage1: RequestUsage, usage2: RequestUsage) -> RequestUsage:
    """兼容部分服务端在流式 chunk usage 中返回 None 的情况。"""
    return RequestUsage(
        prompt_tokens=(usage1.prompt_tokens or 0) + (usage2.prompt_tokens or 0),
        completion_tokens=(usage1.completion_tokens or 0) + (usage2.completion_tokens or 0),
    )


# 保留流式输出，同时避免 _add_usage 因 None token 字段崩溃。
_openai_client_patch_target._add_usage = _safe_add_usage

llm_mode_config = {
    "gpt 5": "openai/gpt-5",
    "miniMax": "minimax/minimax-m2.7"
}

async def create_agent(
        api_key: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        db_manager: DatabaseManager | None = None,
        defult_config_name: str | None = "default",
    ) -> RongZaiAgent:

    llm_model = llm_mode_config.get(defult_config_name, "openai/gpt-4o")
    is_openai_gpt = llm_model.startswith("openai/gpt-")
    agent_token_limit = 300000 if is_openai_gpt else 500000

    def set_model_client(defult_config_name: str|None = "deepseek-v3.2(No image)") -> HepAIAnthropicChatCompletionClient| HepAIChatCompletionClient:
        llm_model = llm_mode_config.get(defult_config_name, "openai/gpt-4o")
        # GPT 路径在 HepAI 的 OpenAI 兼容层上更容易受到参数兼容性影响。
        # 对 GPT 尽量少传可选参数，让服务端自己决定 completion budget，避免因为
        # max_tokens 不兼容被包装成 5xx 后触发长时间重试。
        llm_max_tokens = 60000
        llm_max_retries = 12
        omit_max_tokens = False

        if llm_model.startswith("openai/gpt-"):
            omit_max_tokens = True
            llm_max_retries = 1

        is_vip_user = bool(user_id and user_id in PERMISSION_USERS)
        if is_vip_user:
            client_kwargs = {
                "model": llm_model,
                "api_key": "sk-qywwUDyCxrUQYyyxhUCNKZbasiRwAzszJSdIQXdsWdsjHOV",
                "timeout": 180,
                "max_retries": llm_max_retries,
            }
        else:
            client_kwargs = {
                "model": llm_model,
                "api_key": api_key,
                "timeout": 180,
                "max_retries": llm_max_retries,
            }
        if not omit_max_tokens:
            client_kwargs["max_tokens"] = llm_max_tokens


        if ("claude" in llm_model) or ("minimax" in llm_model):
            model_client = HepAIAnthropicChatCompletionClient(
                # model="claude-haiku-4-5",
                # model="claude-sonnet-4-6",
                base_url="https://aiapi.ihep.ac.cn/apiv2/anthropic",
                # base_url = "https://api.zhizengzeng.com/anthropic",
                # api_key=os.environ.get("ZZZ_API_KEY"),
                model_info=_MODEL_INFO["claude-sonnet-4-5"],
                # temperature=0.5,
                **client_kwargs,
            )
        else:
            is_vision = True
            if "deepseek" in llm_model:
                is_vision = False
            model_client = HepAIChatCompletionClient(
                # model="openai/gpt-4o",
                base_url="https://aiapi.ihep.ac.cn/apiv2",
                **client_kwargs,
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


    SYSTEM = RONGZAI_SYSTEM_PROMPT


    return RongZaiAgent(
        name = "RongZaiAgent",
        model_client=set_model_client(defult_config_name),
        system_message=SYSTEM,
        reflect_on_tool_use=False,
        model_client_stream=True,
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
        token_limit=agent_token_limit
    )

if __name__ == "__main__":
    
    asyncio.run(
        run_worker(
            # 智能体注册信息
            agent_name="Rongzai Agent",
            author="wanghao96@ihep.ac.cn",
            permission=PERMISSION,
            description = "蓉仔——你的中子散射数据分析助手  by中国散裂中子源数据分析组",
            version = "0.1.0",
            logo="/home/dur/rongzai-agent/src/configs/resized_rzera_logo.png",
            examples=["用GSAS-II帮我把BL16的RUN0023568的group5的衍射谱精修好，cif文件是Si.cif",
                      "用Z-Rietveld帮我把BL09的RUN0000410的groupMA的衍射谱精修好，cif文件是zeolite.cif",
                      "用我上传的附件文件精修。如果不确定用哪个精修引擎，找我确认。",
                      "帮我使用sasview工具，分析上传的小角散射数据，使用Fit功能，选择空心圆柱模型，拟合散射曲线，直到chisquare小于10，才返回拟合结果和拟合图。"],
            agent_config = llm_mode_config,
            defult_config_name = "gpt 5",
            # 智能体实体
            agent_factory=create_agent, 
            # 后端服务配置
            port = 42812, 
            # 是否注册到HepAI智能体平台
            no_register=False,
            drsai_dir = "/home/dur/rongzai-agent/tmp",
            # 是否注册为OpenWebUI的pipeline
            enable_openwebui_pipeline=False, 
            history_mode = "backend",
            # use_api_key_mode = "backend",
            join_topics = ["test"],
            metadata={"others": "test"},
            link_wechat = False,
        )
    )

