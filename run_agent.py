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


def _resolve_agent_work_root(user_id: str | None, workdir: str | None, db_manager: DatabaseManager | None) -> Path:
    resolved_user_id = user_id or "default_user"
    if workdir:
        return Path(workdir) / resolved_user_id
    if db_manager:
        return db_manager.schema_manager.base_dir / "runs" / resolved_user_id
    return Path(RUNS_DIR) / resolved_user_id


def _build_skills_dirs() -> list[str]:
    skill_dirs: list[str] = []
    system_skills_dir = os.getenv("SYSTEM_SKILLS_DIR")
    if system_skills_dir:
        skill_dirs.append(system_skills_dir)
    local_skills_dir = str(here / "agent_skills")
    if os.path.isdir(local_skills_dir):
        skill_dirs.append(local_skills_dir)
    return list(dict.fromkeys(skill_dirs))


def _load_skill_tool_factory(skill_name: str, factory_name: str):
    skill_tool_path = here / "agent_skills" / skill_name / "scripts" / "skill_tools.py"
    if not skill_tool_path.exists():
        raise FileNotFoundError(f"Skill tool module not found: {skill_tool_path}")

    module_name = f"skill_tools_{skill_name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, skill_tool_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load module spec from {skill_tool_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    factory = getattr(module, factory_name, None)
    if factory is None:
        raise AttributeError(f"Factory {factory_name!r} not found in {skill_tool_path}")
    return factory

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
    

    # backend mode may not pass api_key into agent_factory; keep a local fallback.
    is_vip_user = bool(user_id and user_id in PERMISSION_USERS)

    work_root = _resolve_agent_work_root(user_id=user_id, workdir=WORKDIR, db_manager=db_manager)
    upload_dir = str(work_root / "downloads")
    skill_registry_path = os.getenv("SKILL_REGISTRY_PATH", str(here / "agent_skills" / "skill_registry.json"))
    skill_registry = SkillRegistryManager(skill_registry_path)
    build_rietveld_skill_tools = _load_skill_tool_factory(
        skill_name="rietveld-auto-refiner",
        factory_name="build_rietveld_skill_tools",
    )
    find_refinement_files_tool, run_rietveld_refinement_tool = build_rietveld_skill_tools(
        data_dir=data_dir,
        user_upload_dir=upload_dir,
        thread_id=thread_id,
    )

    def _build_system_prompt(
        active_skill_name: str | None = None,
        active_skill_description: str | None = None,
        active_skill_body: str | None = None,
    ) -> str:
        try:
            skill_catalog = skill_registry.render_catalog()
        except Exception as e:
            skill_catalog = f"(技能注册表加载失败: {e})"

        if active_skill_name:
            return f"""你是蓉仔，一个专注于中子散射全谱拟合与数据分析的科研助手。

你现在拥有 {active_skill_name} 能力。
能力描述：{active_skill_description or ""}

技能执行规范：
{active_skill_body or ""}

通用约束：
- 用户问你是谁时，回答你是蓉仔。
- 用户用什么语言，你就用什么语言。
- 不臆造路径、工具结果或实验结论；不确定时明确说明并请求补充信息。
"""

        return f"""你是蓉仔，一个中子散射数据分析专家，能够使用多种工具实现多种类型中子散射数据的全谱拟合。

可用技能有：
{skill_catalog}

当用户请求分析数据时，请先选择合适的技能。
可先调用 list_registered_skills_tool 查看技能，再调用 activate_skill_tool 激活目标技能。

通用约束：
- 用户问你是谁时，回答你是蓉仔。
- 用户用什么语言，你就用什么语言。
- 不臆造路径、工具结果或实验结论；不确定时明确说明并请求补充信息。
"""

    def list_registered_skills_tool() -> dict:
        """
        返回技能注册表中的技能清单（name、description、toolchains、mcp_servers）。
        """
        skills = skill_registry.list_skills()
        return {
            "status": "success",
            "registry": str(skill_registry.registry_path),
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "toolchains": s.toolchains,
                    "mcp_servers": s.mcp_servers,
                }
                for s in skills
            ],
        }

    agent_holder: dict[str, RongZaiAgent] = {}

    async def activate_skill_tool(skill_name: str) -> dict:
        """
        激活一个技能：
        1) 从技能注册表读取 skill 元数据与 body；
        2) 动态连接并挂载该 skill 的 MCP 工具；
        3) 更新主智能体系统提示词为该 skill 的执行模式。
        """
        meta = skill_registry.get_skill(skill_name)
        skill_body = skill_registry.load_skill_body(skill_name)
        mcp_tools = await skill_registry.load_mcp_tools(skill_name)

        agent = agent_holder.get("agent")
        if agent is None:
            return {
                "status": "success",
                "message": "skill metadata loaded; agent instance not ready yet",
                "skill": {
                    "name": meta.name,
                    "description": meta.description,
                    "toolchains": meta.toolchains,
                },
                "mounted_mcp_tools": [tool.name for tool in mcp_tools],
            }

        old_dynamic_tools = getattr(agent, "_active_skill_mcp_tools", [])
        old_dynamic_names = {tool.name for tool in old_dynamic_tools}
        if old_dynamic_names:
            agent._tools = [tool for tool in agent._tools if tool.name not in old_dynamic_names]
            agent._workbench._tools = [tool for tool in agent._workbench._tools if tool.name not in old_dynamic_names]

        existing_agent_tool_names = {tool.name for tool in agent._tools}
        for tool in mcp_tools:
            if tool.name not in existing_agent_tool_names:
                agent._tools.append(tool)
                existing_agent_tool_names.add(tool.name)

        existing_workbench_tool_names = {tool.name for tool in agent._workbench._tools}
        for tool in mcp_tools:
            if tool.name not in existing_workbench_tool_names:
                agent._workbench._tools.append(tool)
                existing_workbench_tool_names.add(tool.name)

        agent._active_skill_mcp_tools = mcp_tools
        agent._active_skill_name = meta.name
        agent._developer_system_message = _build_system_prompt(
            active_skill_name=meta.name,
            active_skill_description=meta.description,
            active_skill_body=skill_body,
        )
        agent.update_system_prompt()
        agent._tools_names = [tool.name for tool in agent._workbench._tools]

        return {
            "status": "success",
            "active_skill": {
                "name": meta.name,
                "description": meta.description,
                "toolchains": meta.toolchains,
            },
            "mounted_mcp_tools": [tool.name for tool in mcp_tools],
            "prompt_updated": True,
        }

    list_registered_skills_tool.__name__ = "list_registered_skills_tool"
    activate_skill_tool.__name__ = "activate_skill_tool"

    SYSTEM = _build_system_prompt()


    agent = RongZaiAgent(
        name = "RongZai_Agent",
        model_client=set_model_client(defult_config_name),
        system_message=SYSTEM,
        reflect_on_tool_use=False,
        model_client_stream=True,  # Enable streaming tokens from the model client.
        # model_context=long_memory_context,
        tools=[
            find_refinement_files_tool,
            run_rietveld_refinement_tool,
            list_registered_skills_tool,
            activate_skill_tool,
        ],

        # drsaiAgent specific
        thread_id=thread_id,
        db_manager=db_manager,
        user_id=user_id,
        set_model_client=set_model_client,
        llm_mode_config=llm_mode_config,

        # skills and executor
        skills_dir=_build_skills_dirs(),
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

    agent_holder["agent"] = agent
    return agent

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

