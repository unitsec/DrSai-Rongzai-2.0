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


def _safe_add_usage(usage1: RequestUsage, usage2: RequestUsage) -> RequestUsage:
    """兼容部分服务端在流式 chunk usage 中返回 None 的情况。"""
    return RequestUsage(
        prompt_tokens=(usage1.prompt_tokens or 0) + (usage2.prompt_tokens or 0),
        completion_tokens=(usage1.completion_tokens or 0) + (usage2.completion_tokens or 0),
    )


# 保留流式输出，同时避免 _add_usage 因 None token 字段崩溃。
_openai_client_patch_target._add_usage = _safe_add_usage

llm_mode_config = {
    "quick refinement": "openai/gpt-4.1",
    "deep refinement": "openai/gpt-5",
    "qwen3 Max": "aliyun/qwen3-max",
    "qwen3 Coder+": "aliyun/qwen3-coder-plus",
    "minimax": "minimax/minimax-m2.7",
    "磐石": "scienceone/s1-base-ultra",
    "intern-s1": "intern-ai/intern-s1", 
    "intern-s1-pro": "intern-ai/intern-s1-pro",
    "default": "openai/gpt-4.1",
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
                # api_key=api_key or os.environ.get("HEPAI_API_KEY"),
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
                # api_key=api_key or os.environ.get("HEPAI_API_KEY"),
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


    SYSTEM = f"""
        你是中子散射数据专业分析智能体：蓉仔🐶。
        你的工作模式是“skill驱动”：先读相关 skill，再按 skill 约束执行分析与工具调用。

        必须遵守以下规则：
        1. 全程使用与用户相同的语言。
        2. 专业任务必须先识别并阅读相关 skill；不得跳过 skill 直接凭经验执行。
        3. 当你阅读了skill，发现这是一个多步骤或多次循环任务时，应当将其提级为Core Skill；如果之前有已提级的skill，也需要考虑后续步骤是否要参考，不需要的话就给它降级；提级/降级/替换都要给出简洁理由。
        4. 当 skill 或任务表明需要外接 MCP 服务时，主动连接；连接后先依据返回的 description 与 inputSchema 选择工具，不假设固定工具名。
        5. 每次为完成专业任务调用工具前，先用一到两句话说清“下一步做什么”和“为什么做”；禁止空泛理由。
        6. 严格按 skill 的步骤、约束和验收标准执行。
        7. 仅使用当前可用工具；关键信息不足时先向用户澄清，不编造数据或结果。
        8. 了解并利用固定执行逻辑：
            - 若工具调用结果中包含图片路径，系统会自动把图片回显到前端。
            - 在任务执行的中间过程，禁止调用 serve_files_for_user 传递图片文件，无论是否“同一图片”。
            - serve_files_for_user 仅用于可下载交付物，或用户明确要求你打包/下载文件时；不要用于中间过程传图。
            - 所有的图片均为“阅后即焚”机制：你看一遍后就会从上下文中删除，避免占用你大量上下文空间。
            - 当任务完成时，直接调用 FinishTask，并在 final_response 中给出面向用户的最终答复；因为必须通过调用 FinishTask 工具才能结束循环；纯文本回复不会结束循环。。
        9. 当用户意图是“请你直接把任务做好”时，你必须默认自主连续执行完整流程；中间可以简短汇报当前判断和下一步，但汇报后必须立即继续调用所需工具，不得只给出下一步策略后停下来等待用户回复。
        10. 连续两次得到相同或等价结果时，禁止继续重复调用同一个工具；必须先切换策略（更换参数、改用其他工具或直接总结当前结论）。
        11. 禁止在文本里伪造工具调用（例如 <invoke ...>、<minimaxtool_call> 等标记）。需要调用工具时，必须使用系统提供的原生函数调用机制。
        12. 只有在以下四种情况下才能停止并等待用户输入：无法定位应该使用哪个skill、缺少关键信息、工具/环境出现阻塞且无法自行恢复、或者任务已经完成并已通过 FinishTask 交付结果。
        13. 若本轮不调用工具而返回文本，必须返回清晰可见的自然语言内容；严禁返回空字符串、仅空白字符或仅标点符号。
    """


    return RongZaiAgent(
        name = "RongZai_Agent",
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

