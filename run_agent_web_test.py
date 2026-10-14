from pathlib import Path
import asyncio, os
import json
from loguru import logger


def _patch_worker_advertise_ip() -> None:
    """
    让 worker 向 HepAI controller 注册时使用显式指定的 IP。

    Why: 默认的 auto_worker_address 用 `socket.connect(("10.255.255.255", 1))`
    的 trick 猜本机 IP，拿到的是默认出口网卡的地址。本机默认路由走公网 NIC，
    结果注册地址变成公网 IP（如 202.122.38.201），而不是内网宿主机 IP
    （如 10.1.251.56）。
    How to apply: 设置环境变量 RZ_WORKER_ADVERTISE_IP=10.1.251.56 即可。
    未设置时保持原行为。
    """
    advertise_ip = os.getenv("RZ_WORKER_ADVERTISE_IP", "").strip()
    if not advertise_ip:
        return

    def _patched(worker_address, host, port):
        if worker_address != "auto":
            return worker_address
        if host in ("localhost", "127.0.0.1"):
            return f"http://{host}:{port}"
        return f"http://{advertise_ip}:{port}"

    try:
        from drsai.utils import utils as _drsai_utils
        _drsai_utils.auto_worker_address = _patched
    except Exception as e:
        logger.warning("patch drsai auto_worker_address failed: {}", e)

    # hepai 1.4.3 起 HWorkerConfig / worker 实体实际使用的是
    # `hepai.components.haiddf.worker.utils`，`hepai.modules.*` 仅为旧路径的
    # 兼容镜像。两处都 patch，避免 import 顺序变化又漏打补丁。
    import importlib
    for _module_path in (
        "hepai.components.haiddf.worker.utils",
        "hepai.modules.haiddf.worker.utils",
    ):
        try:
            _hepai_utils = importlib.import_module(_module_path)
            _hepai_utils.auto_worker_address = _patched
        except Exception as e:
            logger.warning("patch {}.auto_worker_address failed: {}", _module_path, e)

    logger.info("worker advertise IP overridden to {}", advertise_ip)


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
from collections.abc import Mapping
from src.core.agent.agent import RongZaiAgent, normalize_thread_id
from src.configs.system_prompt import get_rongzai_system_prompt
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(here, ".env"), override=True)
_patch_worker_advertise_ip()
os.environ.setdefault("RONGZAI_DEBUG_SYSTEM_PROMPT", "0")
# 打开后会把每轮模型完整输出（含流式文本与最终结构化内容）写入日志。
os.environ.setdefault("RONGZAI_DEBUG_LLM_OUTPUT", "1")
# 模型请求由 RongZaiAgent 统一控制：当前模型首次失败后只重试一次，
# 仍失败则切换备用模型。禁用 SDK 内部重试，避免两层重试累积等待时间。
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
    "deepseek-v4.1-flash (free)": "deepseek-ai/deepseek-v4.1-flash",
    "deepseek-v4-flash (free)": "deepseek-ai/deepseek-v4-flash",
    "deepseek-v4-pro (free)": "deepseek-ai/deepseek-v4-pro",
    "gpt-5 (charge)": "openai/gpt-5",
    "glm-5.2 (free)": "zhipu/glm-5.2",
    "miniMax (free)": "minimax/minimax-m2.7"
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
        user_cookies: Mapping[str, str] | None = None,
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


    SYSTEM = get_rongzai_system_prompt()

    return RongZaiAgent(
        name = "RongzaiAgentDev",
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
        user_cookies=user_cookies,
        set_model_client=set_model_client,
        llm_mode_config=llm_mode_config,

        # skills and executor
        skills_dir=os.getenv("SYSTEM_SKILLS_DIR"),
        sub_agent_config=sub_agent_config,
        # executor=local_executor,

        work_dir=WORKDIR,
        only_in_workspace=True,
        extra_work_dirs=[
            str((here / "datasets").resolve()),
            str((here / "agent_skills").resolve()),
        ],
        token_limit=agent_token_limit
    )

if __name__ == "__main__":
    
    asyncio.run(
        run_worker(
            # 智能体注册信息
            agent_name="Rongzai Agent Dev",
            author="wanghao96@ihep.ac.cn",
            permission="groups: rongzai,payg,hairongzai@ihep.ac.cn; users:admin, ddf_free; owner: wanghao96@ihep.ac.cn",
            description=json.dumps(
                {
                    "en": "Rongzai-Your neutron scattering data analysis assistant\nby CSNS data analysis group",
                    "zh": "蓉仔——你的中子散射数据分析助手\nby 中国散裂中子源数据分析组",
                },
                ensure_ascii=False,
            ),
            version = "0.1.0",
            logo="https://rongzai.ihep.ac.cn/rongzai-agent-logo.png",
            examples=[{"en":"Example1: Help me refine the BL16 RUN0023568 group5 diffraction spectrum using GSAS-II, the cif file is Si.cif",
                       "zh":"示例1：用GSAS-II帮我把BL16的RUN0023568的group5的衍射谱精修好，cif文件是Si.cif"},
                        {"en":"Example2: Help me refine the BL09 RUN0000410 groupMA diffraction spectrum using Z-Rietveld, the cif file is zeolite.cif",
                        "zh":"示例2：用Z-Rietveld帮我把BL09的RUN0000410的groupMA的衍射谱精修好，cif文件是zeolite.cif"},
                        {"en":"Example3: Help me fit the PDF data of BL16 RUN0023568, the fitting range is 1-15 Å, the cif file is Si.cif",
                        "zh":"示例3：帮我拟合好BL16的RUN0023568的PDF数据，拟合范围在1-15埃，cif文件是Si.cif"},
                      {"en":"Lithium battery materials anal1ysis: I have uploaded the powder diffraction data of a lithium battery material, along with the instrument parameter file and an initial cif file. The chemical formula of this sample is XXX. Please help me study the Li/Ni mixing situation of this sample using refinement.",
                       "zh":"锂电池材料分析：附件中是锂电池材料的粉末衍射数据，以及仪器参数文件和一个初始的cif文件。这个样品的化学式是XXX,你帮我用精修的方式研究这个样品的LiNi混排情况。"},
                      {"en":"Zeolite materials analysis: I have uploaded the powder diffraction data of a zeolite material doped with XX, along with the instrument parameter file and an initial cif file. The chemical formula of this sample is XXX. Please help me study the enrichment distribution of XX at X sites, and also study which position is suitable for placing X. Note that the bond length of X-X and X-X should be within a reasonable range without too much deviation. X should also maintain an appropriate distance from surrounding atoms.",
                        "zh":"沸石类材料分析：附件中是一个掺杂了XX的沸石材料的粉末衍射数据，以及仪器参数文件和一个初始的cif文件。这个样品的化学式是XXX,好，现在请你帮我研究XX在X个X位的富集分布，也研究一下X放在哪个位置合适。注意X-X键的键长和X-X的键长应该在合理范围内，不能有太大偏离。X也要与周围原子保持合适的距离。"},
                      {"en":"SANS data fitting: Use the sasview tool to analyze the uploaded small-angle scattering data, use the Fit function, select the hollow cylinder model, fit the scattering curve until the chisquare is less than 10, then return the fitting results and fitting graph.",
                       "zh":"小角中子散射拟合：帮我使用sasview工具，分析上传的小角散射数据，使用Fit功能，选择空心圆柱模型，拟合散射曲线，直到chisquare小于10，才返回拟合结果和拟合图。"}],
            announcements=[{"en":"This is a test agent for DrSai Rongzai, please do not use it for production purposes. If you have any questions, please contact wanghao96@ihep.ac.cn."},
                        {"zh":"这是DrSai Rongzai的测试智能体，请勿用于生产用途。如有任何问题，请联系 wanghao96@ihep.ac.cn."}],
            agent_config = llm_mode_config,
            default_config_name = "deepseek-v4.1-flash",
            # 智能体实体
            agent_factory=create_agent, 
            # 后端服务配置
            port = int(os.getenv('RZ_PORT_AGENT_WEB', '42501')),
            # 是否注册到HepAI智能体平台
            no_register=False,
            drsai_dir = appdir,
            # 是否注册为OpenWebUI的pipeline
            enable_openwebui_pipeline=False, 
            history_mode = "backend",
            # use_api_key_mode = "backend",
            join_topics = ["test"],
            metadata={"others": "test"},
            link_wechat = False
        )
    )
