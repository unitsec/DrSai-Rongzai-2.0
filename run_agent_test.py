from pathlib import Path
import asyncio, os
import json
from loguru import logger

from drsai.modules.components.model_client import  HepAIChatCompletionClient, ModelFamily

from drsai.modules.components.model_client.anthropic import (
    HepAIAnthropicChatCompletionClient,
    _MODEL_INFO
)
from autogen_core.models import RequestUsage
import autogen_ext.models.openai._openai_client as _openai_client_patch_target
from drsai.modules.managers.database import DatabaseManager

here = Path(__file__).parent.absolute()

import asyncio
from drsai.backend import run_worker

from src.core.agent.agent import RongZaiAgent, normalize_thread_id
from src.core.agent.analysis_sandbox import check_sandbox_support
from src.configs.constants import get_datasets_dir
from src.configs.system_prompt import get_rongzai_system_prompt
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
os.environ.setdefault("RONGZAI_DEBUG_SYSTEM_PROMPT", "0")
# 打开后会把每轮模型完整输出（含流式文本与最终结构化内容）写入日志。
os.environ.setdefault("RONGZAI_DEBUG_LLM_OUTPUT", "1")
# 模型请求由 RongZaiAgent 统一控制：当前模型失败后直接切换备用模型。
# 禁用 SDK 内部重试，避免两层重试累积等待时间。
os.environ.setdefault("RONGZAI_STREAM_RETRY_ATTEMPTS", "1")
os.environ.setdefault("RONGZAI_FALLBACK_MAX_RETRIES", "0")
os.environ.setdefault("RONGZAI_FALLBACK_TIMEOUT", "60")
data_dir = os.getenv("WORKER_SPACE", "")
parent_path = os.path.dirname(os.path.abspath(__file__))
appdir = os.path.join(parent_path, "tmp/drsai")
os.makedirs(appdir, exist_ok=True)



def _safe_add_usage(usage1: RequestUsage, usage2: RequestUsage) -> RequestUsage:
    """兼容部分服务端在流式 chunk usage 中返回 None 的情况。"""
    return RequestUsage(
        prompt_tokens=(usage1.prompt_tokens or 0) + (usage2.prompt_tokens or 0),
        completion_tokens=(usage1.completion_tokens or 0) + (usage2.completion_tokens or 0),
    )


# 保留流式输出，同时避免 _add_usage 因 None token 字段崩溃。
_openai_client_patch_target._add_usage = _safe_add_usage

llm_mode_config = {
    "deepseek-v4-flash (free)": "deepseek-ai/deepseek-v4-flash",
    "deepseek-v4-pro (free)": "deepseek-ai/deepseek-v4-pro",
    "gpt-5 (charge)": "openai/gpt-5",
    "glm-5.2 (free)": "zhipu/glm-5.2",
    "miniMax (free)": "minimax/minimax-m2.7"
}

def _get_optional_float_env(name: str, minimum: float, maximum: float) -> float | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw_value!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _get_optional_int_env(name: str, minimum: int, maximum: int) -> int | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false, got {raw_value!r}")


class DeepSeekChatCompletionClient(HepAIChatCompletionClient):
    def __init__(self, *args, thinking_enabled: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_args["extra_body"] = {
            "thinking": {
                "type": "enabled" if thinking_enabled else "disabled"
            }
        }


def _is_free_model_config(config_name: str | None) -> bool:
    return "(free)" in (config_name or "").lower()
def _load_remote_subagent_config() -> dict[str, dict]:
    """从专用子智能体配置文件构建 RongZaiAgent 所需的 sub_agent_config。

    优先读取：
    1. RONGZAI_SUBAGENT_CONFIG_FILE（推荐）
    2. 仓库根目录下 subagent_remote_workers.json（默认）

    兼容：
    - dict: 直接作为 sub_agent_config 使用
    - list: 按历史 remote_agents.json / DDF agent list 格式转换
    """
    candidates = [
        os.getenv("RONGZAI_SUBAGENT_CONFIG_FILE", "").strip(),
        str((here / "subagent_remote_workers.json").resolve()),
    ]
    candidates = [p for p in candidates if p]

    def _normalize_entry(name: str, raw_cfg: dict) -> dict | None:
        if not isinstance(raw_cfg, dict):
            return None

        nested_cfg = raw_cfg.get("model_remote_configs")
        if not isinstance(nested_cfg, dict):
            nested_cfg = raw_cfg.get("config")
        if not isinstance(nested_cfg, dict):
            nested_cfg = {}

        remote_cfg = dict(nested_cfg)

        flat_name = raw_cfg.get("name") or raw_cfg.get("model") or name
        flat_url = raw_cfg.get("url")
        flat_api_key = raw_cfg.get("api_key") or raw_cfg.get("apiKey")
        flat_default_cfg = raw_cfg.get("defult_config_name") or raw_cfg.get("default_config_name")
        mode = str(raw_cfg.get("mode") or "").strip().lower()

        if not mode:
            if flat_url or remote_cfg.get("url"):
                mode = "remote"
            else:
                mode = "ddf"

        remote_cfg.setdefault("name", flat_name)
        if flat_url and not remote_cfg.get("url"):
            remote_cfg["url"] = flat_url
        if mode == "ddf" and not remote_cfg.get("url"):
            remote_cfg["url"] = "https://aiapi.ihep.ac.cn/apiv2"
        if flat_api_key and not remote_cfg.get("api_key"):
            remote_cfg["api_key"] = flat_api_key
        if flat_default_cfg and not (remote_cfg.get("defult_config_name") or remote_cfg.get("default_config_name")):
            remote_cfg["defult_config_name"] = flat_default_cfg

        agent_type = raw_cfg.get("type", "HepAIWorkerAgent")
        if agent_type not in {"HepAIWorkerAgent", "RemoteAgent"}:
            return None

        if not remote_cfg.get("name"):
            return None

        if not remote_cfg.get("url"):
            return None

        description = raw_cfg.get("description", "")
        if isinstance(description, dict):
            description = description.get("zh") or description.get("en") or json.dumps(description, ensure_ascii=False)

        return {
            "mode": mode,
            "type": agent_type,
            "description": description or f"Remote worker: {name}",
            "tools": raw_cfg.get("tools", ["*"]),
            "model_remote_configs": remote_cfg,
        }

    for path_str in candidates:
        cfg_path = Path(path_str)
        if not cfg_path.exists():
            continue
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))

            # 推荐格式：直接使用 sub_agent_config 字典
            if isinstance(raw, dict):
                normalized: dict[str, dict] = {}
                for name, cfg in raw.items():
                    entry = _normalize_entry(str(name), cfg)
                    if entry:
                        normalized[str(name)] = entry

                if normalized:
                    logger.info("Loaded {} remote sub-agents from {}", len(normalized), cfg_path)
                    return normalized

                logger.warning("No valid remote sub-agent entries found in {}", cfg_path)
                continue

            # 兼容旧格式：remote_agents.json（list）
            if not isinstance(raw, list):
                logger.warning("Unsupported sub-agent config format in {}", cfg_path)
                continue

            sub_agents: dict[str, dict] = {}
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if item.get("mode") not in {"remote", "ddf"}:
                    continue

                name = item.get("name")
                if not name:
                    continue

                entry = _normalize_entry(str(name), item)
                if entry:
                    sub_agents[str(name)] = entry

            if sub_agents:
                logger.warning("Loaded remote sub-agents from legacy list format file: {}", cfg_path)
                logger.info("Loaded {} remote sub-agents from {}", len(sub_agents), cfg_path)
                return sub_agents
        except Exception as e:
            logger.warning("Failed to load remote agents from {}: {}", cfg_path, e)

    logger.info("No remote sub-agent config loaded.")
    return {}

async def create_agent(
        api_key: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        db_manager: DatabaseManager | None = None,
        defult_config_name: str | None = "default",
    ) -> RongZaiAgent:

    logger.info(
        "[AgentFactory] creating agent user_id={} thread_id={} config={} has_api_key={} worker_space={}",
        user_id,
        thread_id,
        defult_config_name,
        bool(api_key),
        os.getenv("WORKER_SPACE", ""),
    )

    llm_model = llm_mode_config.get(defult_config_name, "openai/gpt-4o")
    is_openai_gpt = llm_model.startswith("openai/gpt-")
    agent_token_limit = 300000 if is_openai_gpt else 500000
    sub_agent_config = _load_remote_subagent_config()
    thread_id = normalize_thread_id(thread_id, user_id)

    def set_model_client(defult_config_name: str|None = "deepseek-v3.2(No image)") -> HepAIAnthropicChatCompletionClient| HepAIChatCompletionClient:
        llm_model = llm_mode_config.get(defult_config_name, "openai/gpt-4o")
        # GPT 路径在 HepAI 的 OpenAI 兼容层上更容易受到参数兼容性影响。
        # 对 GPT 尽量少传可选参数，让服务端自己决定 completion budget，避免因为
        # max_tokens 不兼容被包装成 5xx 后触发长时间重试。
        llm_max_tokens = 60000
        llm_max_retries = 0
        omit_max_tokens = False

        if llm_model.startswith("openai/gpt-"):
            omit_max_tokens = True
            llm_max_retries = 0

        BLACK_LIST = [
            account.strip()
            for account in os.getenv("BLACK_LIST", "").split(",")
            if account.strip()
        ]
        is_black_user = bool(user_id and user_id in BLACK_LIST)
        is_free_model = _is_free_model_config(defult_config_name)
        # 黑名单账户始终消耗其传入的 key；普通账户仅免费模型使用服务端 key。
        selected_api_key = os.getenv("HEPAI_API_KEY") if is_free_model and not is_black_user else api_key
        print(
            f"User {user_id} is blacklisted: {is_black_user}; "
            f"model config is free: {is_free_model}"
        )
        client_kwargs = {
            "model": llm_model,
            "api_key": selected_api_key,
            "timeout": 60,
            "max_retries": llm_max_retries,
        }
        temperature = _get_optional_float_env("RONGZAI_LLM_TEMPERATURE", 0.0, 2.0)
        seed = _get_optional_int_env("RONGZAI_LLM_SEED", 0, 2147483647)
        if temperature is not None:
            client_kwargs["temperature"] = temperature
        if not omit_max_tokens:
            client_kwargs["max_tokens"] = llm_max_tokens


        if ("claude" in llm_model) or ("minimax" in llm_model):
            if seed is not None:
                logger.warning(
                    "RONGZAI_LLM_SEED={} is not applied to Anthropic-compatible model {} because that API has no seed parameter.",
                    seed,
                    llm_model,
                )
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
            if seed is not None:
                client_kwargs["seed"] = seed
            is_vision = True
            if "deepseek" in llm_model:
                is_vision = False
                client_class = DeepSeekChatCompletionClient
                client_kwargs["thinking_enabled"] = _get_bool_env(
                    "RONGZAI_DEEPSEEK_THINKING", True
                )
            else:
                client_class = HepAIChatCompletionClient
            model_client = client_class(
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


    SYSTEM = get_rongzai_system_prompt()


    return RongZaiAgent(
        name = "RongzaiAgentTest",
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
        sub_agent_config=sub_agent_config,
        # executor=local_executor,

        work_dir=WORKDIR,
        only_in_workspace=True,
        extra_work_dirs=[str(get_datasets_dir()),str((here / "agent_skills").resolve())],
        read_only_work_dirs=[str(get_datasets_dir()),str((here / "agent_skills").resolve())],
        token_limit=agent_token_limit
    )

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run the RongZai worker.")
    parser.add_argument("--local-test", action="store_true",
                        help="Bind localhost and disable platform registration.")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--runtime-dir", default=None,
                        help="Override the worker database/runtime directory.")
    cli_args, worker_args = parser.parse_known_args()
    sys.argv = [sys.argv[0], *worker_args]

    check_sandbox_support()
    asyncio.run(
        run_worker(
            # 智能体注册信息
            agent_name="RongzaiAgentTest",
            author="wanghao96@ihep.ac.cn",
            permission="groups: rongzai,payg,hairongzai@ihep.ac.cn; users:admin, ddf_free; owner: wanghao96@ihep.ac.cn",
            description = '{"en":"Rongzai-Your neutron scattering data analysis assistant by CSNS data analysis group","zh":"蓉仔——你的中子散射数据分析助手  by中国散裂中子源数据分析组"}',
            version = "0.1.0",
            logo="/home/dur/rongzai-agent/src/configs/resized_rzera_logo.png",
            examples=[{"en":"Help me refine the BL16 RUN0023568 group5 diffraction spectrum using GSAS-II, the cif file is Si.cif",
                       "zh":"用GSAS-II帮我把BL16的RUN0023568的group5的衍射谱精修好，cif文件是Si.cif"},
                      {"en":"Help me refine the BL09 RUN0000410 groupMA diffraction spectrum using Z-Rietveld, the cif file is zeolite.cif",
                       "zh":"用Z-Rietveld帮我把BL09的RUN0000410的groupMA的衍射谱精修好，cif文件是zeolite.cif"},
                      {"en":"Refine the data using the uploaded attachment. If unsure which refinement engine to use, ask me.",
                       "zh":"用我上传的附件文件精修。如果不确定用哪个精修引擎，找我确认。"},
                      {"en":"This is battery material data, first refine this data, then do LiNi mixing and refine again, finally give the data analysis conclusion.",
                       "zh":"这是个电池材料的数据，你先帮我精修好这个数据，然后再做一下LiNi混排然后再精修，最后给出数据分析的结论。"},
                      {"en":"Use the sasview tool to analyze the uploaded small-angle scattering data, use the Fit function, select the hollow cylinder model, fit the scattering curve until the chisquare is less than 10, then return the fitting results and fitting graph.",
                       "zh":"帮我使用sasview工具，分析上传的小角散射数据，使用Fit功能，选择空心圆柱模型，拟合散射曲线，直到chisquare小于10，才返回拟合结果和拟合图。"}],
            agent_config = llm_mode_config,
            defult_config_name = "deepseek-v4-pro",
            announcements=[{"en":"This is a test agent for DrSai Rongzai, please do not use it for production purposes. If you have any questions, please contact wanghao96@ihep.ac.cn."},
                        {"zh":"这是DrSai Rongzai的测试智能体，请勿用于生产用途。如有任何问题，请联系 wanghao96@ihep.ac.cn."}],
            # 智能体实体
            agent_factory=create_agent, 
            # 后端服务配置
            port = cli_args.port or int(os.getenv("RONGZAI_AGENT_PORT", 42812)),
            host = "127.0.0.1" if cli_args.local_test else None,
            # 是否注册到HepAI智能体平台
            no_register=cli_args.local_test,
            drsai_dir = cli_args.runtime_dir or "/home/wangh/rongzai-agent/tmp",
            # 是否注册为OpenWebUI的pipeline
            enable_openwebui_pipeline=False, 
            history_mode = "backend",
            # use_api_key_mode = "backend",
            join_topics = ["test"],
            metadata={"others": "test"},
            link_wechat = False
        )
    )
