from typing import (
    AsyncGenerator, 
    List, 
    Sequence, 
    Dict, 
    Any, 
    Callable, 
    Awaitable, 
    Union, 
    Optional, 
    Self,
    )
import os, json, re
import asyncio, traceback
from pydantic import BaseModel
from pathlib import Path
from loguru import logger
from autogen_ext.tools.mcp import McpWorkbench, SseServerParams, StdioServerParams

from drsai import CancellationToken, FunctionCall
from drsai.modules.baseagent import (
    DrSaiAgent, 
    HandoffBase, 
    Response, 
    TaskResult, 
    CreateResult,
    FunctionExecutionResultMessage,
    FunctionExecutionResult,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    LLMMessage
    )
from drsai.modules.baseagent.drsaiagent import DrSaiAgentConfig
from drsai.modules.baseagent import CodeExecutor
from drsai.modules.components import (
    ComponentModel,
)
from drsai.modules.components.model_client import ChatCompletionClient
from drsai.modules.components.model_client import HepAIChatCompletionClient, ModelFamily
from drsai.modules.components.model_client.anthropic import (
    HepAIAnthropicChatCompletionClient,
    _MODEL_INFO,
)
from drsai.modules.components.model_context import (
    ChatCompletionContext,
    DrSaiChatCompletionContext,
)
from drsai.modules.components.memory import Memory
from drsai.modules.components.tool import (
    BaseTool, 
    FunctionTool, 
    Workbench,
    ToolSchema,
    ParametersSchema,
    )
from drsai.modules.managers.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    ModelClientStreamingChunkEvent,
    TextMessage,
    ToolCallRequestEvent,
    AgentLogEvent,
    FilesEvent,
    StructuredMessage,
    StructuredMessageFactory,
    Image,
    
)
from drsai.modules.managers.database import DatabaseManager
from drsai.configs.constant import RUNS_DIR
from drsai.modules.agents.skills_agent.managers import (
    get_operator_funcs,
)
from drsai.modules.components.skills import SkillLoader
from drsai.utils.utils import download_file_from_url_or_base64
from drsai.modules.agents.skills_agent.managers.get_managers_tools import (
    get_agent_skills_tool,
)
from .utils import (
    _extract_existing_image_paths_from_content,
    show_file_to_user,
    get_operator_funcs_rongzai
)

from src.utils.check_language import generate_prompt
import traceback

from src.core.auth.icat_permission_provider import IcatPermissionProvider
from src.core.auth.domas_permission_provider import DomasPermissionProvider


def _get_env_float(name: str, default: float) -> float:
    """读取浮点型环境变量，非法值时回退默认值。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)

class DrSaiAssistantConfig(DrSaiAgentConfig):
    skills_dir: Optional[str | List[str]]
    work_dir: str | None
    only_in_workspace: bool
    extra_work_dirs: List[str]
    executor: ComponentModel
    token_limit: int
    rag_flow_url: str
    rag_flow_token: str
    memory_dataset_id: str
    learning_dataset_id: str

class RongZaiAgent(DrSaiAgent):
    """
    融载智能体（精简版）。

    当前设计目标：
    1. 保留基础工具调用能力。
    2. 仅加载系统级 skills（不允许用户覆盖）。
    3. 保留 Core Skill 提升/降级/替换能力，用于多轮任务中强化关键约束。
    4. 移除用户画像与用户自定义 tools/skills/system prompt 动态注入。
    """


    def __init__(
        self,
        name: str,
        *,
        model_client: ChatCompletionClient = None,
        tools: List[BaseTool[Any, Any] | Callable[..., Any] | Callable[..., Awaitable[Any]]] | None = None,
        workbench: Workbench | None = None,
        handoffs: List[HandoffBase | str] | None = None,
        model_context: ChatCompletionContext | None = None,
        description: str = "An agent that provides assistance with ability to use tools.",
        system_message: (
            str | None
        ) = "You are a helpful AI assistant. Solve tasks using your tools. Reply with TERMINATE when the task has been completed.",
        model_client_stream: bool = True,
        reflect_on_tool_use: bool | None = None,
        tool_call_summary_format: str = "{result}",
        tool_call_summary_prompt: str| None = None,
        output_content_type: type[BaseModel] | None = None,
        output_content_type_format: str | None = None,
        memory: Sequence[Memory] | None = None,
        metadata: Dict[str, str] | None = None,
        # drsaiAgent specific
        memory_function: Callable | None = None,
        reply_function: Callable | None = None,
        db_manager: DatabaseManager | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        set_model_client: Callable | None = None,
        llm_mode_config: Dict = {},
        fallback_config_names: Optional[List[str]] = None,
        # skills and executor
        skills_dir: Optional[str | List[str]] = None,
        work_dir: str | None = None,
        only_in_workspace: bool = True,
        extra_work_dirs: List[str] | None = None,
        executor: CodeExecutor | None = None,
        token_limit: int = 500000
        ):
        
        super().__init__(
            name=name,
            model_client=model_client,
            tools=tools,
            workbench=workbench,
            handoffs=handoffs,
            model_context=model_context,
            description=description,
            system_message=system_message,
            model_client_stream=model_client_stream,
            reflect_on_tool_use=reflect_on_tool_use,
            tool_call_summary_format=tool_call_summary_format,
            tool_call_summary_prompt=tool_call_summary_prompt,
            output_content_type=output_content_type,
            output_content_type_format=output_content_type_format,
            memory=memory,
            metadata=metadata,
            memory_function=memory_function,
            reply_function=reply_function,
            db_manager=db_manager,
            thread_id=thread_id,
            user_id=user_id,
            set_model_client=set_model_client,
            llm_mode_config=llm_mode_config,
        )

        self._fallback_config_names = fallback_config_names or []
        self._env_fallback_config_names = self._parse_fallback_configs_from_env()

        self._developer_system_message = system_message

        # === workspace for assistant ===
        if not work_dir:
            if self._db_manager:
                DEFAULT_RUN_DIR: Path = self._db_manager.schema_manager.base_dir / "runs"
                self._work_dir = DEFAULT_RUN_DIR / self._user_id
            else:
                self._work_dir = Path(RUNS_DIR) / self._user_id
        else:
            self._work_dir = Path(work_dir) / self._user_id
        if not self._work_dir.exists():
            self._work_dir.mkdir(parents=True)

        # 固定智能体名，不再通过用户画像动态改名。
        self._agent_name = self._name
        
        # === basic tools ===
        # 将run_bash, run_read, run_write, run_edit4个函数转换为大模型能理解的FunctionTool对象并加入self._tools列表
        self._only_in_workspace = only_in_workspace
        self._extra_work_dirs = extra_work_dirs
        self._basic_funcs: List[Callable] = get_operator_funcs(
            work_dir, 
            only_in_workspace=self._only_in_workspace,
            extra_dirs = self._extra_work_dirs,
            thread_id=self._thread_id,
            allolow_dangrous_cmd = True, # 融载智能体默认允许执行潜在危险命令，前提是用户明确提出需求。风险由用户承担。
            )
        # self._basic_funcs_names = [func.__name__ for func in self._basic_funcs]
        for func in self._basic_funcs:
            if func.__name__ in {"run_bash", "run_read", "run_write", "run_edit"}:
                self._tools.append(FunctionTool(func, description=func.__doc__ or ""))

        # 将rongzai需要的特定的函数转换为FunctionTool对象并加入self._tools列表
        self._otherBasic_funcs: List[Callable] = get_operator_funcs_rongzai(source_name=self._name)
        for func in self._otherBasic_funcs:
            self._tools.append(FunctionTool(func, description=func.__doc__))

        # MCP 基础工具：当 skill 里提到需要连接某个 MCP 服务/工具时，模型应优先使用这里的连接工具。
        self._tools.append(
            FunctionTool(
                self.ConnectMcpServer,
                description=self.ConnectMcpServer.__doc__ or "",
                name="ConnectMcpServer",
            )
        )

        # 视觉子智能体工具：将读图任务委托给独立视觉模型，主模型无需具备视觉能力。
        self._tools.append(
            FunctionTool(
                self.AnalyzeImageWithVision,
                description=self.AnalyzeImageWithVision.__doc__ or "",
                name="AnalyzeImageWithVision",
            )
        )

        # domas和icat文件访问权限检查工具。
        self._tools.append(
            FunctionTool(
                self.check_user_permission_for_file,
                description=self.check_user_permission_for_file.__doc__ or "",
                name="CheckUserPermission",
            )
        )

        # === model context ===
        # 对话上下文管理器
        self._model_context = DrSaiChatCompletionContext(
            agent_name=self._agent_name,
            model_client=self._model_client,
            user_id=self._user_id,
            thread_id=self._thread_id,
            work_dir=self._work_dir,
            token_limit=token_limit,
        )

        self._upload_dir = self._work_dir / self._thread_id /"uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        
        self._download_dir = self._work_dir / self._thread_id /"downloads"
        self._download_dir.mkdir(parents=True, exist_ok=True)

        # 浏览器 cookie 由 drsai_ui 在 WS 握手时抓白名单快照,沿
        # body["user"]["cookies"] 流过 drsai cores 落到 UserInput.extra_requests。
        # 这里默认空,_load_user_cookies() 在每轮消息进来时刷新。
        self._user_cookies: Dict[str, str] = {}

        # === skills ===
        # 仅加载系统级 skills，禁用用户覆盖。
        self._skills_dir = skills_dir
        self._agent_skills_tools = []
        self._core_skill_tools = [
            self._get_promote_skill_tool(),
            self._get_demote_skill_tool(),
            self._get_replace_core_skill_tool(),
        ]
        self._core_skill_slots: List[Dict[str, Any]] = []
        self._max_core_skill_slots = 2
        self._core_skill_update_seq = 0
        self._core_skill_policy_prompt = (
            "[Core Skill Promotion Policy]\n"
            "Evaluate whether to call PromoteSkillToCore before important actions.\n"
            "Use it especially for high-risk or strict multi-step domain tasks.\n"
            "Use it especially when you need a skill for a professional data analysis work with multi-step operation.\n"
            "Core skills do not auto-expire; manage lifecycle explicitly with DemoteCoreSkill/ReplaceCoreSkill.\n"
            "Always provide a concise reason when promoting, demoting, or replacing core skills."
        )

        # 初始化固定系统提示词（仅受开发者配置影响）。
        self._lang_prompt = ""
        self.update_system_prompt()

        self._local_executor = executor

        # 记录 skills 目录/文件 mtime，用于按需重载。
        self._config_mtimes: Dict[str, float] = {}
        self._cached_skills_loader = None

        # === MCP 动态连接 ===
        # key: server_id（SSE 用 url，stdio 用 command），value: 已启动的 McpWorkbench
        self._mcp_connections: Dict[str, McpWorkbench] = {}
        # key: server_id，value: 该 server 注册到 workbench 的代理工具名列表
        self._mcp_server_tool_names: Dict[str, List[str]] = {}
        self._tools_names = [tool.name for tool in self._tools]
        self._default_model_client_component = self._model_client.dump_component()
        self._default_model_client_name = ""
        try:
            self._default_model_client_name = self._model_client._create_args.get("model", "")
        except Exception:
            self._default_model_client_name = ""

        # Legacy (disabled): tool 图片回注主模型上下文的状态字段。
        # 如需回滚到旧方案，取消注释以下两行，并恢复下方 Legacy 方法与调用点。
        # self._ephemeral_images_pending: List[UserMessage] = []
        # self._ephemeral_images_inflight: List[UserMessage] = []

    # Legacy (disabled): tool 图片回注主模型上下文管理方法。
    # def _record_ephemeral_image_message(self, multimodal_msg: UserMessage) -> None:
    #     """记录新注入的临时图片消息，供下一轮推理使用。"""
    #     self._ephemeral_images_pending.append(multimodal_msg)

    # def _arm_ephemeral_images_for_inference(self) -> None:
    #     """在每轮推理开始前装载临时图片消息。"""
    #     self._ephemeral_images_inflight = self._ephemeral_images_pending
    #     self._ephemeral_images_pending = []

    # def _cleanup_inflight_ephemeral_images(self, model_context: ChatCompletionContext) -> None:
    #     """在每轮推理结束后清理已消费的临时图片消息。"""
    #     if not self._ephemeral_images_inflight:
    #         return
    #     if not isinstance(model_context, DrSaiChatCompletionContext):
    #         self._ephemeral_images_inflight = []
    #         return
    #
    #     target_ids = {id(msg) for msg in self._ephemeral_images_inflight}
    #     if target_ids:
    #         model_context._messages = [msg for msg in model_context._messages if id(msg) not in target_ids]
    #         model_context._current_messages = [msg for msg in model_context._current_messages if id(msg) not in target_ids]
    #     self._ephemeral_images_inflight = []

    # def _cleanup_all_ephemeral_images(self, model_context: ChatCompletionContext) -> None:
    #     """兜底清理：移除 pending/inflight 的所有临时图片消息。"""
    #     if not isinstance(model_context, DrSaiChatCompletionContext):
    #         self._ephemeral_images_pending = []
    #         self._ephemeral_images_inflight = []
    #         return
    #
    #     all_ephemeral = self._ephemeral_images_pending + self._ephemeral_images_inflight
    #     target_ids = {id(msg) for msg in all_ephemeral}
    #     if target_ids:
    #         model_context._messages = [msg for msg in model_context._messages if id(msg) not in target_ids]
    #         model_context._current_messages = [msg for msg in model_context._current_messages if id(msg) not in target_ids]
    #     self._ephemeral_images_pending = []
    #     self._ephemeral_images_inflight = []

    def _file_changed(self, path: Path) -> bool:
        """判断文件或目录是否变化，并更新 mtime 缓存。"""
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return True
        key = str(path)
        if self._config_mtimes.get(key) != mtime:
            self._config_mtimes[key] = mtime
            return True
        return False

    def _skills_inputs_changed(self) -> bool:
        """仅检查系统 skills 输入目录是否变化。"""
        changed = False

        skills_dirs: List[Path] = []
        if self._skills_dir:
            system_dirs = self._skills_dir if isinstance(self._skills_dir, list) else [self._skills_dir]
            skills_dirs.extend(Path(d) for d in system_dirs)

        for skills_dir in skills_dirs:
            changed = self._file_changed(skills_dir) or changed
            if skills_dir.exists():
                for skill_file in skills_dir.glob("*/SKILL.md"):
                    changed = self._file_changed(skill_file) or changed

        return changed

    def update_system_prompt(self) -> None:
        """构建系统提示词：开发者提示词 + 会话 ID + Core Skill 规则区。"""
        enhanced_system_message = f"{self._lang_prompt}\n\n{self._developer_system_message}"
        if self._thread_id:
            enhanced_system_message = f"{enhanced_system_message}\n\nCurrent session_id is {self._thread_id}"
        if self._user_id:
            enhanced_system_message = (
                f"{enhanced_system_message}\n\nCurrent user_id is {self._user_id}. ")
        if self._work_dir:
            enhanced_system_message = (
                f"{enhanced_system_message}\n\nThe files user uploaded are in {self._work_dir}/{self._thread_id}/uploads.")
        enhanced_system_message += f"\n\n{self._core_skill_policy_prompt}"
        core_skill_prompt = self._build_core_skill_prompt()
        if core_skill_prompt:
            enhanced_system_message += f"\n\n{core_skill_prompt}"
        
        
        self._system_messages = [SystemMessage(content=enhanced_system_message)]

    def _debug_log_system_prompt(self, system_messages: List[SystemMessage]) -> None:
        """按开关输出即将发送给模型的完整系统提示词。"""
        debug_enabled = os.getenv("RONGZAI_DEBUG_SYSTEM_PROMPT", "").lower() in {"1", "true", "yes", "on"}
        if not debug_enabled:
            return

        parts: List[str] = []
        for idx, message in enumerate(system_messages, start=1):
            content = message.content
            if isinstance(content, str):
                parts.append(f"[SystemMessage {idx}]\n{content}")
            else:
                parts.append(f"[SystemMessage {idx}]\n{str(content)}")

        logger.info("===== FULL SYSTEM PROMPT BEFORE LLM CALL =====\n{}\n===== END SYSTEM PROMPT =====", "\n\n".join(parts))

    def _is_context_length_error(self, error: Exception) -> bool:
        """判断异常是否属于上下文超长。"""
        text = str(error).lower()
        flags = [
            "context_length_exceeded",
            "input tokens exceed",
            "maximum context length",
            "context window",
            "too many tokens",
        ]
        return any(flag in text for flag in flags)

    def _is_retryable_model_error(self, error: Exception) -> bool:
        """判断异常是否适合切换到备用模型后重试。"""
        text = str(error).lower()
        flags = [
            "insufficient_quota",
            "rate limit",
            "too many requests",
            "429",
            "500",
            "502",
            "503",
            "504",
            "timeout",
            "timed out",
            "connection reset",
            "service unavailable",
            "no resource",
        ]
        return any(flag in text for flag in flags)

    def _flatten_message_content_for_summary(self, content: Any) -> str:
        """将消息内容拍平成短文本，供总结子智能体使用。"""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, FunctionCall):
                    parts.append(f"[FunctionCall] name={item.name}, arguments={item.arguments}")
                else:
                    try:
                        parts.append(str(item))
                    except Exception:
                        parts.append("[Unserializable list item]")
            return "\n".join(parts)
        try:
            return str(content)
        except Exception:
            return "[Unserializable content]"

    def _build_history_text_for_summary(self, messages: List[LLMMessage]) -> str:
        """将历史消息序列裁剪并序列化成可总结文本。"""
        max_chars = int(_get_env_float("RONGZAI_SUMMARY_INPUT_MAX_CHARS", 24000))
        per_msg_max_chars = int(_get_env_float("RONGZAI_SUMMARY_PER_MESSAGE_MAX_CHARS", 1200))
        lines: List[str] = []
        total = 0
        for i, msg in enumerate(messages, start=1):
            role = msg.__class__.__name__
            source = getattr(msg, "source", "")
            raw = self._flatten_message_content_for_summary(getattr(msg, "content", ""))
            snippet = raw[:per_msg_max_chars]
            line = f"[{i}] role={role}; source={source}\n{snippet}\n"
            line_len = len(line)
            if total + line_len > max_chars:
                remain = max_chars - total
                if remain > 0:
                    lines.append(line[:remain])
                break
            lines.append(line)
            total += line_len
        return "\n".join(lines)

    def _build_fallback_history_summary(self, old_messages: List[LLMMessage], recent_messages: List[LLMMessage]) -> str:
        """总结子智能体失败时的兜底摘要（不再额外调用模型）。"""
        old_count = len(old_messages)
        recent_count = len(recent_messages)
        recent_tail: List[str] = []
        for msg in recent_messages[-6:]:
            role = msg.__class__.__name__
            raw = self._flatten_message_content_for_summary(getattr(msg, "content", ""))
            recent_tail.append(f"- {role}: {raw[:180]}")
        bullets = "\n".join(recent_tail) if recent_tail else "- (empty)"
        return (
            "Auto-generated compressed history (fallback, no model summary).\n"
            f"Older messages omitted: {old_count}. Recent messages kept: {recent_count}.\n"
            "Recent message tail:\n"
            f"{bullets}"
        )

    def _get_summary_model_client(self) -> ChatCompletionClient:
        """获取总结子智能体专用模型客户端。

        优先级：
        1) 环境变量 RONGZAI_SUMMARY_MODEL_CONFIG 对应的 set_model_client 配置
        2) 主模型客户端（回退）
        """
        summary_config_name = os.getenv("RONGZAI_SUMMARY_MODEL_CONFIG", "").strip()
        if summary_config_name and self._set_model_client:
            try:
                summary_client = self._set_model_client(summary_config_name)
                model_name = ""
                try:
                    model_name = summary_client._create_args.get("model", "")
                except Exception:
                    model_name = ""
                logger.info(
                    "History summarizer model selected by RONGZAI_SUMMARY_MODEL_CONFIG='{}'{}",
                    summary_config_name,
                    f" -> {model_name}" if model_name else "",
                )
                return summary_client
            except Exception as e:
                logger.warning(
                    "Failed to create summary model client from RONGZAI_SUMMARY_MODEL_CONFIG='{}': {}. Fallback to primary model.",
                    summary_config_name,
                    e,
                )

        return self._model_client

    def _parse_fallback_configs_from_env(self) -> List[str]:
        """从环境变量读取备用模型列表，支持 JSON 数组或逗号分隔文本。"""
        raw = (os.getenv("RONGZAI_LLM_FALLBACK_CONFIGS") or "").strip()
        if not raw:
            return []

        parsed: List[str] = []
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                parsed = [str(item).strip() for item in value if str(item).strip()]
            elif isinstance(value, str) and value.strip():
                parsed = [value.strip()]
        except Exception:
            parsed = [item.strip() for item in raw.split(",") if item.strip()]

        # 去重并保序
        seen: set[str] = set()
        ordered: List[str] = []
        for item in parsed:
            if item not in seen:
                ordered.append(item)
                seen.add(item)
        return ordered

    def _get_fallback_config_names(self) -> List[str]:
        """获取备用模型标识列表（配置传参或环境变量）。"""
        names: List[str] = []
        seen: set[str] = set()

        for config_name in self._fallback_config_names:
            if config_name and config_name not in seen:
                names.append(config_name)
                seen.add(config_name)

        if not names:
            for config_name in self._env_fallback_config_names:
                if config_name and config_name not in seen:
                    names.append(config_name)
                    seen.add(config_name)

        if names:
            return names
        return names

    def _ensure_component_token_model(self, component: ComponentModel) -> ComponentModel:
        """为模型组件补齐 token_model，避免未知模型名导致 token 计数初始化失败。"""
        try:
            cfg = getattr(component, "config", None)
            if not isinstance(cfg, dict):
                return component

            model_info = cfg.get("model_info")
            if not isinstance(model_info, dict):
                model_info = {}
                cfg["model_info"] = model_info

            token_model = model_info.get("token_model")
            if not token_model:
                model_info["token_model"] = os.getenv("RONGZAI_TOKEN_MODEL_FALLBACK", "gpt-4o-2024-11-20")
        except Exception:
            pass
        return component

    def _build_model_client_from_model_name(self, model_name: str) -> Optional[ChatCompletionClient]:
        """按模型名称构建客户端；claude/minimax 走 Anthropic 路径，其他走 OpenAI 兼容路径。"""
        candidate = (model_name or "").strip()
        if not candidate:
            return None

        try:
            api_key = None
            try:
                api_key = self._model_client._client.api_key
            except Exception:
                api_key = os.getenv("HEPAI_API_KEY")

            if not api_key:
                return None

            timeout = float(_get_env_float("RONGZAI_FALLBACK_TIMEOUT", 180.0))
            # 与主路径保持一致：默认重试更高，避免瞬时 5xx 导致过早判定失败。
            max_retries = int(_get_env_float("RONGZAI_FALLBACK_MAX_RETRIES", 12.0))
            max_tokens = int(_get_env_float("RONGZAI_FALLBACK_MAX_TOKENS", 60000.0))
            is_openai_gpt = candidate.startswith("openai/gpt-")

            if ("claude" in candidate) or ("minimax" in candidate):
                return HepAIAnthropicChatCompletionClient(
                    model=candidate,
                    api_key=api_key,
                    base_url=os.getenv("RONGZAI_ANTHROPIC_BASE_URL", "https://aiapi.ihep.ac.cn/apiv2/anthropic").strip(),
                    timeout=timeout,
                    max_retries=max_retries,
                    max_tokens=max_tokens,
                    # 使用稳定已知的 Anthropic model_info，避免未知模型名触发 token 计数初始化错误。
                    model_info=_MODEL_INFO["claude-sonnet-4-5"],
                )

            is_vision = "deepseek" not in candidate
            token_model = os.getenv("RONGZAI_TOKEN_MODEL_FALLBACK", "gpt-4o-2024-11-20").strip()
            client_kwargs: Dict[str, Any] = {
                "model": candidate,
                "api_key": api_key,
                "base_url": os.getenv("RONGZAI_OPENAI_BASE_URL", "https://aiapi.ihep.ac.cn/apiv2").strip(),
                "timeout": timeout,
                "max_retries": max_retries,
                "model_info": {
                    "vision": is_vision,
                    "function_calling": True,
                    "json_output": True,
                    "structured_output": False,
                    "family": ModelFamily.GPT_41,
                    "multiple_system_messages": True,
                    "token_model": token_model,
                },
            }
            if not is_openai_gpt:
                client_kwargs["max_tokens"] = max_tokens
            return HepAIChatCompletionClient(**client_kwargs)
        except Exception as e:
            logger.warning("Failed to clone model client for model '{}': {}", candidate, e)
            return None

    async def _switch_to_fallback_model(self, config_name: str) -> Optional[str]:
        """尝试切换到一个备用模型（优先按模型名直连，不依赖 llm_mode_config）。"""
        fallback_id = (config_name or "").strip()
        if not fallback_id:
            return None

        new_model_client: ChatCompletionClient | None = self._build_model_client_from_model_name(fallback_id)

        # 兜底：若外部注入了 set_model_client，则将标识原样透传给外部工厂。
        if new_model_client is None and self._set_model_client:
            try:
                new_model_client = self._set_model_client(fallback_id)
            except Exception as e:
                logger.warning("Failed to create fallback model client from identifier '{}': {}", fallback_id, e)
                return None

        if new_model_client is None:
            logger.warning("Fallback identifier '{}' did not produce a model client.", fallback_id)
            return None

        try:
            await self.switch_model(new_model_client)
            logger.info(
                "Switched to fallback model '{}'.",
                fallback_id,
            )
            return fallback_id
        except Exception as e:
            logger.warning("Failed to switch to fallback model '{}': {}", fallback_id, e)
            return None

    async def _restore_default_model(self) -> None:
        """在一轮任务结束后恢复初始化时的默认模型。"""
        try:
            current_component = self._model_client.dump_component()
        except Exception:
            current_component = None

        if current_component == self._default_model_client_component:
            return

        try:
            default_model_client: ChatCompletionClient | None = None

            # 优先按默认模型名重建，走与 fallback 一致的 provider-specific 构造路径。
            if self._default_model_client_name:
                default_model_client = self._build_model_client_from_model_name(self._default_model_client_name)

            # 回退：使用初始化快照组件重建。
            if default_model_client is None:
                default_component = self._default_model_client_component.model_copy(deep=True)
                default_component = self._ensure_component_token_model(default_component)
                default_model_client = ChatCompletionClient.load_component(default_component)

            await self.switch_model(default_model_client)
            logger.info("Restored default model client after run.")
        except Exception as e:
            logger.warning("Failed to restore default model client: {}", e)

    async def _summarize_history_with_subagent(
        self,
        model_client: ChatCompletionClient,
        old_messages: List[LLMMessage],
        recent_messages: List[LLMMessage],
        cancellation_token: CancellationToken,
    ) -> str:
        """调用轻量总结子智能体，生成可替代长历史的压缩摘要。"""
        history_text = self._build_history_text_for_summary(old_messages)
        if not history_text.strip():
            return self._build_fallback_history_summary(old_messages, recent_messages)

        prompt = (
            "Summarize prior conversation history for retry after context overflow.\n"
            "Output must be concise and preserve only: goal, constraints, decisions, key tool outputs, "
            "and unresolved questions.\n"
            "Do not include chain-of-thought.\n"
            "Return plain text with short bullet points.\n\n"
            "History to summarize:\n"
            f"{history_text}"
        )

        summary_messages: List[LLMMessage] = [
            SystemMessage(content="You are a history compression sub-agent."),
            UserMessage(content=prompt, source="user"),
        ]

        try:
            summary_model_client = self._get_summary_model_client()
            rsp = await summary_model_client.create(
                summary_messages,
                cancellation_token=cancellation_token,
            )
            summary = rsp.content if isinstance(rsp.content, str) else str(rsp.content)
            summary = (summary or "").strip()
            if summary:
                return summary[: int(_get_env_float("RONGZAI_SUMMARY_OUTPUT_MAX_CHARS", 6000))]
        except Exception as summary_err:
            logger.warning("History summarizer sub-agent failed: {}", summary_err)

        return self._build_fallback_history_summary(old_messages, recent_messages)

    async def _build_retry_llm_messages_after_context_overflow(
        self,
        model_client: ChatCompletionClient,
        system_messages: List[SystemMessage],
        runtime_messages: List[LLMMessage],
        cancellation_token: CancellationToken,
    ) -> List[LLMMessage]:
        """基于总结子智能体压缩历史，构建重试上下文。"""
        keep_recent = int(_get_env_float("RONGZAI_CONTEXT_RETRY_KEEP_RECENT", 12))
        keep_recent = max(4, keep_recent)

        if len(runtime_messages) <= keep_recent:
            return self._get_compatible_context(
                model_client=model_client,
                messages=system_messages + runtime_messages,
            )

        old_messages = runtime_messages[:-keep_recent]
        recent_messages = runtime_messages[-keep_recent:]
        summary = await self._summarize_history_with_subagent(
            model_client=model_client,
            old_messages=old_messages,
            recent_messages=recent_messages,
            cancellation_token=cancellation_token,
        )

        summary_msg = SystemMessage(
            content=(
                "Compressed prior context generated after context overflow. "
                "Use this as authoritative historical memory for this retry only:\n"
                f"{summary}"
            )
        )

        return self._get_compatible_context(
            model_client=model_client,
            messages=system_messages + [summary_msg] + recent_messages,
        )

    def _build_missing_tool_results(self, call_ids: set[str]) -> FunctionExecutionResultMessage:
        """为缺失的 tool call 生成占位结果，修复上下文消息链。"""
        results: List[FunctionExecutionResult] = []
        for call_id in sorted(call_ids):
            results.append(
                FunctionExecutionResult(
                    content="Tool result missing due to context trimming. Continue with available context.",
                    call_id=call_id,
                    name="unknown",
                    is_error=True,
                )
            )
        return FunctionExecutionResultMessage(content=results)

    def _extract_tool_call_id(self, item: Any) -> Optional[str]:
        """兼容对象/字典两种结构，提取 tool call id。"""
        if isinstance(item, dict):
            value = item.get("id") or item.get("call_id")
        else:
            value = getattr(item, "id", None) or getattr(item, "call_id", None)
        if value is None:
            return None
        return str(value)

    def _is_tool_call_payload(self, content: Any) -> bool:
        """判断 assistant content 是否是工具调用列表（宽松判定）。"""
        if not isinstance(content, list) or not content:
            return False
        for item in content:
            if isinstance(item, FunctionCall):
                continue
            if isinstance(item, dict) and "id" in item and "name" in item and "arguments" in item:
                continue
            return False
        return True

    def _repair_trimmed_runtime_messages(self, messages: List[LLMMessage]) -> List[LLMMessage]:
        """修复 token 裁剪后可能损坏的 tool_call/tool_result 序列。"""
        repaired: List[LLMMessage] = []
        pending_call_ids: set[str] = set()
        repaired_count = 0
        orphan_result_groups_kept = 0
        placeholder_results_inserted = 0
        debug_enabled = os.getenv("RONGZAI_DEBUG_CONTEXT_REPAIR", "").lower() in {"1", "true", "yes", "on"}

        for msg in messages:
            if isinstance(msg, AssistantMessage) and self._is_tool_call_payload(msg.content):
                if pending_call_ids:
                    repaired.append(self._build_missing_tool_results(pending_call_ids))
                    repaired_count += 1
                    placeholder_results_inserted += len(pending_call_ids)
                    pending_call_ids = set()
                repaired.append(msg)
                pending_call_ids = {
                    call_id
                    for item in msg.content
                    for call_id in [self._extract_tool_call_id(item)]
                    if call_id
                }
                continue

            if isinstance(msg, FunctionExecutionResultMessage):
                if not pending_call_ids:
                    # 保守策略：孤立结果保留，避免误删有效上下文。
                    orphan_result_groups_kept += 1
                    repaired.append(msg)
                    continue

                matched_ids: set[str] = set()
                for item in msg.content:
                    item_id = self._extract_tool_call_id(item)
                    if item_id in pending_call_ids:
                        matched_ids.add(item_id)

                missing = pending_call_ids - matched_ids
                repaired.append(msg)
                if missing:
                    repaired.append(self._build_missing_tool_results(missing))
                    repaired_count += 1
                    placeholder_results_inserted += len(missing)

                pending_call_ids = set()
                continue

            if pending_call_ids:
                repaired.append(self._build_missing_tool_results(pending_call_ids))
                repaired_count += 1
                placeholder_results_inserted += len(pending_call_ids)
                pending_call_ids = set()

            repaired.append(msg)

        if pending_call_ids:
            repaired.append(self._build_missing_tool_results(pending_call_ids))
            repaired_count += 1
            placeholder_results_inserted += len(pending_call_ids)

        if debug_enabled and repaired_count:
            logger.debug(
                "Context repair stats: repaired_groups={}, orphan_result_groups_kept={}, placeholder_results_inserted={}",
                repaired_count,
                orphan_result_groups_kept,
                placeholder_results_inserted,
            )
        return repaired

    def _sanitize_tool_name(self, value: str) -> str:
        """将远程工具名转换为本地可用的稳定工具名片段。"""
        cleaned = []
        for ch in value:
            cleaned.append(ch if (ch.isalnum() or ch == "_") else "_")
        result = "".join(cleaned).strip("_")
        return result or "mcp"

    def _remove_mcp_proxy_tools(self, server_id: str) -> None:
        """移除某个 MCP server 在本地注册的所有代理工具。"""
        names = set(self._mcp_server_tool_names.get(server_id, []))
        if not names:
            return
        self._tools = [tool for tool in self._tools if tool.name not in names]
        self._tools_names = [tool.name for tool in self._tools]
        self._mcp_server_tool_names[server_id] = []

    async def ConnectMcpServer(
        self,
        type: str,
        url: str = "",
        command: str = "",
        args: List[str] | None = None,
        headers: Dict[str, Any] | None = None,
        env: Dict[str, Any] | None = None,
    ) -> str:
        """连接远程 MCP server，并把其工具动态注册到当前 workbench 供后续直接调用。

        设计意图：
        - 当某个 skill、系统提示词或用户指令提到“需要连接某个 MCP 工具/服务”时，优先调用本工具。
        - skill 不需要硬编码写出“调用 ConnectMcpServer”这句话；只要给出足够明确的连接线索即可，
          例如服务类型（sse/stdio）、URL、command、headers、env 或其他配置说明。

        使用规则：
        - 若是 SSE 服务，传入 type='sse' 和 url。
                - 若是 stdio 服务，传入 type='stdio' 和 command，可选 args/env。

        返回结果：
        - 会返回该 MCP server 暴露出的远程工具列表。
        - 每个远程工具都会被注册为当前 agent 可直接调用的本地代理工具。
        """
        conn_type = (type or "").strip().lower()
        try:
            if conn_type == "sse":
                server_id = (url or "").strip()
                if not server_id:
                    return "Error: 'url' is required when type='sse'."
                # 默认 300s 的 sse_read_timeout 对长耗时工具调用不够，容易触发 httpx.ReadTimeout。
                sse_connect_timeout = _get_env_float("RONGZAI_MCP_SSE_CONNECT_TIMEOUT", 30.0)
                sse_read_timeout = _get_env_float("RONGZAI_MCP_SSE_READ_TIMEOUT", 1800.0)
                server_params = SseServerParams(
                    url=server_id,
                    headers=headers or {},
                    timeout=sse_connect_timeout,
                    sse_read_timeout=sse_read_timeout,
                )
            elif conn_type == "stdio":
                server_id = (command or "").strip()
                if not server_id:
                    return "Error: 'command' is required when type='stdio'."
                server_params = StdioServerParams(command=server_id, args=args or [], env=env or None)
            else:
                return "Error: 'type' must be either 'sse' or 'stdio'."

            # 重新连接同一 server 时，先清理旧连接与旧代理工具。
            if server_id in self._mcp_connections:
                try:
                    await self._mcp_connections[server_id].stop()
                except Exception:
                    pass
                self._remove_mcp_proxy_tools(server_id)

            mcp_workbench = McpWorkbench(server_params=server_params)
            await mcp_workbench.start()
            self._mcp_connections[server_id] = mcp_workbench

            remote_tools = await mcp_workbench.list_tools()
            local_names: List[str] = []
            for remote in remote_tools:
                remote_name = remote["name"]
                remote_name_part = self._sanitize_tool_name(remote_name)
                local_name = f"mcp_{remote_name_part}"
                if local_name in self._tools_names:
                    local_name = f"{local_name}_{len(self._tools_names)}"

                def _make_proxy(sid: str, tool: str):
                    async def _proxy(arguments_json: str = "{}") -> str:
                        """调用远程 MCP 工具。arguments_json 必须是 JSON 对象字符串。"""
                        try:
                            parsed_args = json.loads(arguments_json or "{}")
                        except json.JSONDecodeError as e:
                            return f"Error: invalid arguments_json: {e}"
                        result = await self._mcp_connections[sid].call_tool(tool, parsed_args)
                        return result.to_text()
                    return _proxy

                _proxy = _make_proxy(server_id, remote_name)

                description = (
                    f"Proxy tool for MCP '{server_id}' tool '{remote_name}'. "
                    f"Original description: {remote.get('description', '')}. "
                    "Pass arguments via arguments_json (JSON object string)."
                )
                self._tools.append(FunctionTool(_proxy, description=description, name=local_name))
                local_names.append(local_name)

            self._mcp_server_tool_names[server_id] = local_names
            self._tools_names = [tool.name for tool in self._tools]

            returned_tools = [
                {
                    "remote_name": remote.get("name", ""),
                    "local_proxy_name": local_name,
                    "description": remote.get("description", ""),
                    "inputSchema": remote.get("inputSchema", {}),
                }
                for remote, local_name in zip(remote_tools, local_names)
            ]
            return (
                f"Connected MCP server '{server_id}' and registered {len(local_names)} proxy tools. "
                f"Use local_proxy_name in subsequent tool calls.\n"
                + json.dumps(returned_tools, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            return f"Failed to connect MCP server: {e}"

    async def AnalyzeImageWithVision(self, image_path: str, query: str, vision_model: str = "") -> str:
        """Delegate image understanding to a dedicated vision model.

        Use this tool when you need to extract information from an image file.
        Args:
            image_path: local image path (.png/.jpg/.jpeg/.webp/.gif/.bmp/.tif/.tiff)
            query: what information should be extracted from the image
            vision_model: optional override model name; default reads env RONGZAI_VISION_MODEL
        Returns:
            text answer from the vision model
        """
        try:
            if not image_path or not query:
                return "Error: image_path and query are required."

            p = Path(image_path).expanduser().resolve()
            if not p.exists() or not p.is_file():
                return f"Error: image file not found: {image_path}"

            image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
            if p.suffix.lower() not in image_suffixes:
                return f"Error: unsupported image type: {p.suffix}"

            # 视觉模型按顺序降级：优先 gpt-5，再 gpt-4o，最后 minimax。
            # 可通过 RONGZAI_VISION_MODEL_FALLBACKS 覆盖（逗号分隔）。
            if (vision_model or "").strip():
                model_candidates = [(vision_model or "").strip()]
            else:
                fallback_env = os.getenv("RONGZAI_VISION_MODEL_FALLBACKS", "").strip()
                if fallback_env:
                    model_candidates = [m.strip() for m in fallback_env.split(",") if m.strip()]
                else:
                    primary_env = os.getenv("RONGZAI_VISION_MODEL", "").strip()
                    model_candidates = []
                    if primary_env:
                        model_candidates.append(primary_env)
                    for m in ("openai/gpt-4o", "openai/gpt-5", "minimax/minimax-m2.7"):
                        if m not in model_candidates:
                            model_candidates.append(m)

            base_url = os.getenv("RONGZAI_VISION_BASE_URL", "https://aiapi.ihep.ac.cn/apiv2").strip()

            api_key = None
            try:
                api_key = self._model_client._client.api_key
            except Exception:
                api_key = os.getenv("HEPAI_API_KEY")
            if not api_key:
                return "Error: missing API key for vision model."

            image_ob = Image.from_file(file_path=str(p))
            llm_messages: List[LLMMessage] = [
                SystemMessage(
                    content="You are a vision sub-agent. Extract only what the user asked from the image and return concise factual text."
                ),
                UserMessage(content=[query, image_ob], source="user"),
            ]

            failures: List[str] = []
            for model_name in model_candidates:
                try:
                    vision_client = HepAIChatCompletionClient(
                        model=model_name,
                        api_key=api_key,
                        base_url=base_url,
                        timeout=180,
                        max_retries=1,
                        model_info={
                            "vision": True,
                            "function_calling": False,
                            "json_output": True,
                            "structured_output": False,
                            "family": ModelFamily.GPT_41,
                            "multiple_system_messages": True,
                            "token_model": "gpt-4o-2024-11-20",
                        },
                    )
                    rsp = await vision_client.create(
                        llm_messages,
                        cancellation_token=CancellationToken(),
                    )
                    answer = rsp.content if isinstance(rsp.content, str) else str(rsp.content)
                    if answer:
                        return answer
                    failures.append(f"{model_name}: empty output")
                except Exception as model_err:
                    failures.append(f"{model_name}: {model_err}")

            return (
                "Error: all vision models failed. Tried: "
                + ", ".join(model_candidates)
                + ". Details: "
                + " | ".join(failures)
            )
        except Exception as e:
            return f"Error in AnalyzeImageWithVision: {e}"

    async def check_user_permission_for_file(self, user_id: str, beamline: str, runNumber: str):
        """
        如果用户要求处理某个谱仪的某个RUN号的数据，先检查用户是否有权限访问该RUN号对应的课题，并检查该课题下的reduction文件夹中是否有包含runNumber的文件夹。数据就在这些文件夹中。
        该工具会返回检查结果，包含用户是否有权限访问该RUN号对应的课题，以及reduction文件夹中是否有包含runNumber的文件夹。
        一旦检查通过，你会直接获得访问reduction文件夹的权限和同级目录下的analysis文件夹的权限。
        
        注意，一旦你调用这个工具并检查通过，你就有一个必须做的事情：在数据分析得到一个更好结果后，将数据分析结果保存到analysis文件夹中。
            分析结果具体指：
                1.一个你生成的md文件，用于系统描述分析结果。
                2.这个分析产生的重要数据文件。
            你可能在分析过程中保存多次，注意必须覆盖保存，同类文件不能保存成不同名字也不能保存到其他地方。
            
        
        Args:
            user_id: 用户ID
            beamline: 光束线编号
            runNumber: 数据的RUN号，必须传“RUNXXXXXXX”这种格式，RUN后面有7位数字，不足的前面补0。
        
        Returns:
            dict: 包含检查结果的字典，可能包含以下键：
                - status: "success" 或 "failed"
                - error: 错误信息（如果有）
                - proposal: proposal编号（如果检查成功）
                - reduction_folder_path: reduction文件夹路径（如果检查成功）
                - run_folders_path: reduction目录下包含runNumber的文件夹名称列表（如果检查成功）
        """
        
        try:
            results = {"status": "failed"}
            
            # 每轮消息开始前,从 DB 拉一次最新 cookies(WS 重连/前端刷新后下一轮就生效)。
            self._load_user_cookies()
            sso = self._user_cookies.get("sso-token", "")
            auth_provider = (self._user_cookies.get("rz-auth-provider", "") or "").strip().lower()
            ui_role = (self._user_cookies.get("rz-role", "") or "").strip().lower()
            logger.info(
                f"[cookie-debug] user={self._user_id} thread={self._thread_id} "
                f"cookie_keys={list(self._user_cookies.keys())} "
                f"sso_token={ sso if sso else 'EMPTY' }"
            )
            # token = _get_user_token(user_id) or "Rongzai@324"
            if auth_provider == "domas":
                PermissionCheckTool = DomasPermissionProvider()
                probe = PermissionCheckTool.get_user_permissions(user_id, sso, ui_role)
                all_proposals = [p['proposal_id'] for p in probe.to_dict()['proposals']]
                if ui_role == "scientist":
                    result = PermissionCheckTool.get_proposal_by_runid(sso, user_id, runNumber,beamline)
                    found_proposal = result['proposalId']
                    # 检查found_proposal是否在用户可访问的proposal列表中
                    if str(found_proposal) not in all_proposals:
                        results["error"] = f"用户 {user_id} 无权访问 {runNumber} 对应的课题 {found_proposal}"
                        return results    

                else:
                    found_proposal = None
                    for proposal in all_proposals:
                        result = PermissionCheckTool._fetch_runs_for_proposal(sso,user_id,proposal)
                        checkedRUNs = [run.to_dict()['run_number'] for run in result]
                        if runNumber in checkedRUNs:
                            found_proposal = proposal
                            break
                    if not found_proposal:
                        results["error"] = f"用户 {user_id} 无权访问 run {runNumber} 对应的课题"
                        return results
            else:
                # 根据user_id、beamline和runNumber获取proposal_name
                from src.core.auth.icat.icat_helper import IcatHelper
                helper = IcatHelper("https://data.csns.ihep.ac.cn", cert=False)
                icat_session = helper.loginIcat(user_id, sso)
                try:
                    result = helper.getProposalByRunName(icat_session, runNumber, beamline)
                finally:
                    helper.logoutIcat(icat_session)
                if not result:
                    results["error"] = f"未找到 run {runNumber} 对应的课题"
                    return results
                found_proposal = result.get("Investigation").get("name")
                print(f"Found proposal for run {runNumber}: {found_proposal}")

                # 根据user_id和token获取用户可访问的所有proposal
                from src.core.auth.icat_permission_provider import IcatPermissionProvider
                icat_plugin = "umt" if sso else "ldap"
                PermissionCheckTool = IcatPermissionProvider("https://data.csns.ihep.ac.cn", plugin=icat_plugin)

                import logging
                _prev_disable = logging.root.manager.disable
                logging.disable(logging.INFO)
                try:
                    if ui_role == "scientist":
                        probe = PermissionCheckTool.get_user_permissions(user_id, sso, role='scientist')
                    else:
                        probe = PermissionCheckTool.get_user_permissions(user_id, sso, role='user')
                finally:
                    logging.disable(_prev_disable)

            

                # print(f"P162025122900008 in probe: {any('P162025122900008' in str(p) for p in probe.to_dict()['proposals'])}")
                all_proposals = [p['proposal_name'] for p in probe.to_dict()['proposals']]
                

                # 检查found_proposal是否在用户可访问的proposal列表中
                if str(found_proposal) not in all_proposals:
                    results["error"] = f"用户 {user_id} 无权访问 {runNumber} 对应的课题 {found_proposal}"
                    return results

            # 检查found_proposal下是否有reduction文件夹
            reduction_folder_path = f"/csns_data/{beamline}/{found_proposal}/reduction"
            if not os.path.exists(reduction_folder_path):
                results["error"] = f"有权限，但课题 {found_proposal} 下不存在 reduction 文件夹"
                return results
            # 检查found_proposal下的reduction文件夹中是否有包含runNumber的文件夹（可能有多个）
            run_folder_pattern = re.compile(rf".*{runNumber}.*")
            run_folders = [f for f in os.listdir(reduction_folder_path) if os.path.isdir(os.path.join(reduction_folder_path, f)) and run_folder_pattern.match(f)]
            if not run_folders:
                results["error"] = f"有权限，但课题 {found_proposal} 下的 reduction 文件夹中不存在包含 run {runNumber} 的文件夹"
                return results
            
            results["status"] = "success"
            results["proposal"] = found_proposal
            results["run_folders_path"] = run_folders
            results["reduction_folder_path"] = reduction_folder_path
            if len(run_folders) == 1:
                results["description"] = f"用户 {user_id} 有权访问 run {runNumber} 对应的课题 {found_proposal}，并且在 {reduction_folder_path}下找到了包含 run {runNumber} 的文件夹: {run_folders[0]}。"
            else:
                results["description"] = f"用户 {user_id} 有权访问 run {runNumber} 对应的课题 {found_proposal}，并且在 {reduction_folder_path}下找到了多个包含 run {runNumber} 的文件夹: {run_folders}。文件夹名中有从年份具体到秒的时间信息，你需要问用户用哪个里面的数据。"

            # 给予智能体操作权限，允许访问reduction文件夹和analysis文件夹
            self._extra_work_dirs.append(reduction_folder_path)
            os.makedirs(f"/csns_data/{beamline}/{found_proposal}/analysis", exist_ok=True)
            self._extra_work_dirs.append(f"/csns_data/{beamline}/{found_proposal}/analysis")
        except Exception as e:
            import traceback
            traceback_str = traceback.format_exc()
            results["status"] = "failed"
            results["error"] = f"检查用户权限时发生异常: {e}\n{traceback_str}"
        return results
    
    
    def _get_promote_skill_tool(self) -> ToolSchema:
        """供模型调用：将某个 skill 提升为 Core Skill。"""
        parameters = ParametersSchema(
            type="object",
            properties={
                "skill": {
                    "type": "string",
                    "description": "Skill name to promote into the core system prompt area.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this skill must be treated as a high-priority execution rule now.",
                },
                "focus": {
                    "type": "string",
                    "description": "Optional focus area/keywords to extract from the skill content.",
                },
            },
            required=["skill", "reason"],
            additionalProperties=False,
        )
        return ToolSchema(
            name="PromoteSkillToCore",
            description=(
                "Promote one loaded skill into high-priority core system rules. "
                "Use this only when strict skill compliance is important for the current task. "
                "You decide whether to call this tool."
            ),
            parameters=parameters,
            strict=False,
        )

    def _get_demote_skill_tool(self) -> ToolSchema:
        """供模型调用：将某个 Core Skill 降级/移除。"""
        parameters = ParametersSchema(
            type="object",
            properties={
                "skill": {
                    "type": "string",
                    "description": "Core skill name to demote/remove.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this core skill should be demoted now.",
                },
            },
            required=["skill"],
            additionalProperties=False,
        )
        return ToolSchema(
            name="DemoteCoreSkill",
            description="Demote/remove a promoted core skill from high-priority system rules.",
            parameters=parameters,
            strict=False,
        )

    def _get_replace_core_skill_tool(self) -> ToolSchema:
        """供模型调用：替换 Core Skill。"""
        parameters = ParametersSchema(
            type="object",
            properties={
                "old_skill": {
                    "type": "string",
                    "description": "Existing promoted core skill to replace.",
                },
                "new_skill": {
                    "type": "string",
                    "description": "New skill to promote as replacement.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why replacement is needed now.",
                },
                "focus": {
                    "type": "string",
                    "description": "Optional focus area/keywords for the new skill extraction.",
                },
            },
            required=["old_skill", "new_skill", "reason"],
            additionalProperties=False,
        )
        return ToolSchema(
            name="ReplaceCoreSkill",
            description="Replace one promoted core skill with another in high-priority system rules.",
            parameters=parameters,
            strict=False,
        )

    def _extract_core_skill_rules(self, skill_content: str, focus: str = "") -> str:
        """提取 Core Skill 规则正文，尽量保留完整内容。"""
        text = (skill_content or "").strip()
        if not text:
            return ""
        if focus:
            return f"[Focus]\n{focus}\n\n{text}"
        return text

    def _build_core_skill_prompt(self) -> str:
        """构建注入系统提示词的 Core Skill 规则区块。"""
        if not self._core_skill_slots:
            return ""

        lines = [
            "[Core Skill Rules]",
            "The following promoted skill rules are high-priority execution constraints for the current task.",
        ]
        for idx, slot in enumerate(self._core_skill_slots, start=1):
            lines.append(f"\n## CoreSkill {idx}: {slot['skill']}")
            lines.append(f"Reason: {slot['reason']}")
            lines.append(slot["core_rules"])
        return "\n".join(lines)

    def _promote_skill_to_core(
        self,
        *,
        skills_loader: SkillLoader | None,
        skill_name: str,
        reason: str,
        focus: str = "",
    ) -> str:
        """将指定 skill 提升为系统高优先级规则。"""
        if skills_loader is None:
            return "Cannot promote skill: no skills loader is available in this session."

        skill_content = skills_loader.get_skill_content(skill_name)
        if skill_content is None:
            available = ", ".join(skills_loader.list_skills()) or "none"
            return f"Cannot promote skill '{skill_name}': not found. Available skills: {available}."

        core_rules = self._extract_core_skill_rules(skill_content=skill_content, focus=focus)
        if not core_rules.strip():
            return f"Cannot promote skill '{skill_name}': extracted core rules are empty."

        self._core_skill_update_seq += 1
        existing = None
        for slot in self._core_skill_slots:
            if slot["skill"] == skill_name:
                existing = slot
                break

        if existing is not None:
            existing["reason"] = reason
            existing["core_rules"] = core_rules
            existing["focus"] = focus
            existing["updated_at_seq"] = self._core_skill_update_seq
        else:
            self._core_skill_slots.append(
                {
                    "skill": skill_name,
                    "reason": reason,
                    "core_rules": core_rules,
                    "focus": focus,
                    "updated_at_seq": self._core_skill_update_seq,
                }
            )

        self._core_skill_slots.sort(key=lambda s: s.get("updated_at_seq", 0), reverse=True)
        self._core_skill_slots = self._core_skill_slots[: self._max_core_skill_slots]

        active = ", ".join(slot["skill"] for slot in self._core_skill_slots)
        return (
            f"Promoted skill '{skill_name}' to core rules. "
            f"Use DemoteCoreSkill or ReplaceCoreSkill when needed. Active core skills: {active}."
        )

    def _demote_core_skill(self, skill_name: str, reason: str = "") -> str:
        """显式移除已提升的 Core Skill。"""
        before = len(self._core_skill_slots)
        self._core_skill_slots = [slot for slot in self._core_skill_slots if slot.get("skill") != skill_name]
        if len(self._core_skill_slots) == before:
            active = ", ".join(slot["skill"] for slot in self._core_skill_slots) or "none"
            return f"Core skill '{skill_name}' is not active. Active core skills: {active}."
        active = ", ".join(slot["skill"] for slot in self._core_skill_slots) or "none"
        if reason:
            return f"Demoted core skill '{skill_name}' (reason: {reason}). Active core skills: {active}."
        return f"Demoted core skill '{skill_name}'. Active core skills: {active}."

    def _replace_core_skill(
        self,
        *,
        skills_loader: SkillLoader | None,
        old_skill: str,
        new_skill: str,
        reason: str,
        focus: str = "",
    ) -> str:
        """将一个 Core Skill 替换为另一个。"""
        demote_message = self._demote_core_skill(skill_name=old_skill, reason=f"replace -> {new_skill}")
        promote_message = self._promote_skill_to_core(
            skills_loader=skills_loader,
            skill_name=new_skill,
            reason=reason,
            focus=focus,
        )
        return f"{demote_message} {promote_message}"

    def update_system_skills(self) -> SkillLoader:
        """加载/刷新系统级 skills（开发者维护）。"""
        skills_loader = None

        # 只扫描系统 skills 目录。
        if self._skills_dir:
            system_dirs = self._skills_dir if isinstance(self._skills_dir, list) else [self._skills_dir]
            for skills_dir in system_dirs:
                skills_path = Path(skills_dir)
                if skills_path.exists() and list(skills_path.glob("*/SKILL.md")):
                    if skills_loader is None:
                        skills_loader = SkillLoader(skills_dir=str(skills_path))
                    else:
                        skills_loader.add_skills_by_dir(skills_dir=str(skills_path))

        # 生成给模型看的 Skill 工具描述
        if skills_loader and skills_loader.skills:
            self._agent_skills_tools = [get_agent_skills_tool(descriptions=skills_loader.get_descriptions())]
        else:
            self._agent_skills_tools = []
        return skills_loader

    async def run_stream(
        self,
        *,
        task: str | BaseChatMessage | Sequence[BaseChatMessage] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | TaskResult, None]:
        """执行任务并流式返回事件，最后输出 TaskResult。"""
        if cancellation_token is None:
            cancellation_token = CancellationToken()
        self._cancellation_token = cancellation_token
        
        # for msg in task:
        #     if getattr(msg, "source", None) == "user_proxy":
        #         msg.content = f"user input: {msg.content}"
        #         msg.metadata["content"] = msg.content

        input_messages: List[BaseChatMessage] = []
        output_messages: List[BaseAgentEvent | BaseChatMessage] = []
        if task is None:
            pass
        elif isinstance(task, str):
            text_msg = TextMessage(content=task, source="user", metadata={"internal": "yes"})
            # text_msg = TextMessage(content=task, source="user")
            input_messages.append(text_msg)
            output_messages.append(text_msg)
            yield text_msg
        elif isinstance(task, BaseChatMessage):
            task.metadata["internal"] = "yes"
            input_messages.append(task)
            output_messages.append(task)  
            yield task
        else:
            if not task:
                raise ValueError("Task list cannot be empty.")

            has_any_attached_files = any(
                isinstance(m, BaseChatMessage)
                and (
                    (m.metadata or {}).get("attached_files")
                    or (m.metadata or {}).get("files")
                )
                for m in task
            )

            for msg in task:
                if isinstance(msg, BaseChatMessage):
                    msg.metadata["internal"] = "yes"
                    try:
                        attached_files_json = msg.metadata.get("attached_files") or msg.metadata.get("files")

                        # 过滤 construct_task 注入的“文件正文消息”，避免正文直接进入模型上下文。
                        if (
                            has_any_attached_files
                            and not attached_files_json
                            and isinstance(msg.content, str)
                            and msg.content.lstrip().startswith(("Attached file:", "Attached image:"))
                        ):
                            continue

                        # output_messages 保留原始消息（含图片对象）供前端展示；
                        # input_messages 只放纯文本消息，因为 DrSaiChatCompletionContext
                        # 的 LocalMesssage 不接受 list 类型 content。
                        output_messages.append(msg)

                        if attached_files_json:
                            attached_files = json.loads(attached_files_json)
                            saved_files: List[str] = []
                            for file in attached_files:
                                save_path = f"{self._upload_dir}/{file['name']}"
                                download_file_from_url_or_base64(
                                    file_info=file,
                                    save_path=save_path,
                                )
                                saved_files.append(save_path)

                            # 提取纯文本内容（多模态消息 content 是 list，取其中的字符串部分）。
                            if isinstance(msg.content, list):
                                text_parts = [c for c in msg.content if isinstance(c, str)]
                                text_content = "\n".join(text_parts)
                            else:
                                text_content = msg.content or ""

                            # 追加文件名提示，让模型自行决定是否读取。
                            if saved_files:
                                file_names = [Path(p).name for p in saved_files]
                                names_block = "\n".join(f"- {name}" for name in file_names)
                                file_hint = (
                                    f"User uploaded files:{names_block}"
                                )
                                text_content = f"{text_content}\n\n{file_hint}" if text_content else file_hint

                            # 若原消息是多模态，用纯文本 TextMessage 替换进入上下文的消息。
                            if isinstance(msg.content, list):
                                context_msg = TextMessage(
                                    content=text_content,
                                    source=msg.source,
                                    metadata=msg.metadata,
                                )
                            else:
                                msg.content = text_content
                                context_msg = msg
                        else:
                            context_msg = msg

                        input_messages.append(context_msg)

                        settings_config = msg.metadata.get("settings_config")
                        if settings_config:
                            settings_config = json.loads(settings_config)
                            default_config_name = settings_config.get("defult_config_name")
                            llm_name = self._llm_mode_config.get(default_config_name)
                            if llm_name != self._model_client._create_args["model"] and self._set_model_client:
                                self._model_client = self._set_model_client(default_config_name)
                    except Exception as e:
                        logger.error(f"Error processing message metadata: {e}")
                    yield msg
                else:
                    raise ValueError(f"Invalid message type in sequence: {type(msg)}")


        async for message in self.on_messages_stream(input_messages, cancellation_token):
            if isinstance(message, Response):
                yield message.chat_message
                output_messages.append(message.chat_message)
                yield TaskResult(messages=output_messages)
            else:
                yield message
                if isinstance(message, ModelClientStreamingChunkEvent):
                    # 流式 token 事件不写入最终消息列表。
                    continue
                output_messages.append(message)
        
        await self.resume()

    def _load_user_cookies(self) -> None:
        """从 UserInput.extra_requests["user"]["cookies"] 把浏览器 cookie 读回来。

        drsai_ui 在 WebSocket 握手时把白名单 cookie 注入 run_info,
        随 body["user"]["cookies"] 流到 drsai cores 的 handle_input_info,
        落入 UserInput.extra_requests。这里按 (user_id, thread_id) 取最近一条,
        把 cookies 缓存到 self._user_cookies 供 tools 使用。
        """
        if self._db_manager is None or not self._user_id or not self._thread_id:
            return
        try:
            from drsai.modules.managers.datamodel.db import UserInput
            resp = self._db_manager.get(
                UserInput,
                filters={"user_id": self._user_id, "thread_id": self._thread_id},
                return_json=False,
            )
            if not (resp.status and resp.data):
                return
            extra = getattr(resp.data[0], "extra_requests", None) or {}
            user_blob = extra.get("user") or {}
            cookies = user_blob.get("cookies")
            if isinstance(cookies, dict):
                self._user_cookies = {str(k): str(v) for k, v in cookies.items()}
        except Exception as e:
            logger.debug(f"_load_user_cookies failed: {e}")

    async def on_messages_stream(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        """处理输入消息并按事件流实时产出响应。"""

        # 响应暂停状态
        if self.is_paused:
            yield Response(
                chat_message=TextMessage(
                    content=f"The {self.name} is paused.",
                    source=self.name,
                    metadata={"internal": "yes"},
                )
            )
            return

        inner_messages: List[BaseAgentEvent | BaseChatMessage] = []
        last_buffered_stream_events: List[ModelClientStreamingChunkEvent] = []
        fallback_candidates: List[str] = self._get_fallback_config_names()
        fallback_attempted: set[str] = set()
        forced_invalid_output_once = False
        try:
            self._lang_prompt = ""
            
            for msg in messages:
                if getattr(msg, "source", None) == "user" or getattr(msg, "source", None) == "user_proxy":
                    self._lang_prompt = generate_prompt(msg.content)
                    # msg.content = f"{msg.content}, {self._lang_prompt}"
                    # msg.metadata["content"] = msg.content
            
            # 将用户消息写入模型上下文
            await self._add_messages_to_context(
                model_context=self._model_context,
                messages=messages,
            )

            while True:
                # 每轮重建系统提示词，以纳入最新 Core Skill 状态。
                self.update_system_prompt()

                # 中断可能导致工具调用消息不完整（assistant tool_call 缺少对应 tool 结果）。
                # 该修复可在下一轮推理前自动补齐占位结果，避免模型端返回空响应或上下文报错。
                await self._sanitize_api_messages()

                # 按需重载系统 skills。
                skills_changed = self._skills_inputs_changed()
                if skills_changed or self._cached_skills_loader is None:
                    self._cached_skills_loader = self.update_system_skills()
                skills_loader = self._cached_skills_loader

                manager_tools = self._agent_skills_tools + self._core_skill_tools

                # 将管理类工具 schema 同步到上下文，供模型决策调用。
                if isinstance(self._model_context, DrSaiChatCompletionContext):
                    self._model_context._tool_schema = await self._workbench.list_tools()
                    self._model_context._tool_schema += manager_tools

                # 提取运行期所需对象，便于后续调用。
                agent_name = self._agent_name
                model_context = self._model_context
                system_messages = self._system_messages
                workbench = self._workbench
                handoff_tools = self._handoff_tools
                handoffs = self._handoffs
                model_client = self._model_client
                model_client_stream = self._model_client_stream
                reflect_on_tool_use = self._reflect_on_tool_use
                tool_call_summary_format = self._tool_call_summary_format
                output_content_type = self._output_content_type
                format_string = self._output_content_type_format

                self._debug_log_system_prompt(system_messages)
                model_result = None
                buffered_stream_events: List[ModelClientStreamingChunkEvent] = []
                last_buffered_stream_events = buffered_stream_events
                # Legacy (disabled): 如需恢复 tool 图片回注上下文，启用下一行。
                # self._arm_ephemeral_images_for_inference()
                try:
                    async for inference_output in self._call_llm(
                        model_client=model_client,
                        model_client_stream=model_client_stream,
                        system_messages=system_messages,
                        model_context=model_context,
                        workbench=workbench,
                        handoff_tools=handoff_tools,
                        manager_tools=manager_tools,
                        agent_name=agent_name,
                        cancellation_token=cancellation_token,
                        output_content_type=output_content_type,
                    ):
                        if self.is_paused:
                            raise asyncio.CancelledError()
                        
                        if isinstance(inference_output, CreateResult):
                            model_result = inference_output
                        else:
                            # 先缓存本轮模型流式文本，待确认本轮结果是否合法后再决定是否对用户可见。
                            if isinstance(inference_output, ModelClientStreamingChunkEvent):
                                buffered_stream_events.append(inference_output)
                except Exception as llm_err:
                    if not self._is_retryable_model_error(llm_err):
                        raise

                    remaining_fallbacks = [name for name in fallback_candidates if name not in fallback_attempted]
                    switched_model: Optional[str] = None
                    for fallback_config_name in remaining_fallbacks:
                        fallback_attempted.add(fallback_config_name)
                        switched_model = await self._switch_to_fallback_model(fallback_config_name)
                        if switched_model:
                            break

                    if switched_model:
                        yield ModelClientStreamingChunkEvent(
                            content=f"\n[System Notice] Model call failed. Switched to fallback model: {switched_model}. Retrying.\n",
                            source=agent_name,
                        )
                        logger.warning(
                            "Retryable model error detected; switched to fallback model '{}' and retrying. error={}",
                            switched_model,
                            llm_err,
                        )
                        continue

                    raise
                # Legacy (disabled): 如需恢复 tool 图片回注上下文，启用下一行。
                # self._cleanup_inflight_ephemeral_images(model_context)
                
                if model_result is None:
                    if self.is_paused or cancellation_token.is_cancelled():
                        raise asyncio.CancelledError()
                    raise RuntimeError("No model result was produced.")

                force_invalid_output = os.getenv("RONGZAI_FORCE_INVALID_MODEL_OUTPUT", "").lower() in {"1", "true", "yes", "on"}
                if force_invalid_output and not forced_invalid_output_once:
                    # 测试开关：强制注入非法输出类型，验证备用模型切换与重试链路。
                    model_result.content = {
                        "forced_error": "invalid model output for fallback verification"
                    }
                    logger.warning("RONGZAI_FORCE_INVALID_MODEL_OUTPUT is enabled: forcing invalid model output type.")
                    forced_invalid_output_once = True

                if model_result.content == "":
                    remaining_fallbacks = [name for name in fallback_candidates if name not in fallback_attempted]
                    switched_model: Optional[str] = None
                    for fallback_config_name in remaining_fallbacks:
                        fallback_attempted.add(fallback_config_name)
                        switched_model = await self._switch_to_fallback_model(fallback_config_name)
                        if switched_model:
                            break

                    if switched_model:
                        yield ModelClientStreamingChunkEvent(
                            content=f"\n[System Notice] The current model returned no response. Switched to fallback model: {switched_model}. Retrying.\n",
                            source=agent_name,
                        )
                        logger.warning(
                            "No response from current model; switched to fallback model '{}' and retrying.",
                            switched_model,
                        )
                        continue

                    yield Response(
                        chat_message=TextMessage(
                            content="(No response was generated by the model.) All fallback models are unavailable now. Please try again later.",
                            source=self._agent_name,
                            metadata={"internal": "no"},
                        ),
                        inner_messages=inner_messages,
                    )
                    break

                # 纯文本响应：不调用工具，直接结束循环。
                if isinstance(model_result.content, str):
                    # 纯文本场景直接写回 assistant 响应。
                    await model_context.add_message(
                        AssistantMessage(
                            content=model_result.content,
                            source=self._name,
                            thought=getattr(model_result, "thought", None),
                        )
                    )
                    for buffered_event in buffered_stream_events:
                        yield buffered_event
                    response = await self.handle_str_reponse(
                        model_result=model_result,
                        agent_name=agent_name,
                        format_string=format_string,
                        inner_messages=inner_messages,
                        output_content_type=output_content_type,
                    )
                    yield response
                    break

                # 工具调用分支：严格要求返回 FunctionCall 列表。
                if isinstance(model_result.content, list) and all(
                    isinstance(item, FunctionCall) for item in model_result.content
                ):
                    for buffered_event in buffered_stream_events:
                        yield buffered_event

                    # 记录模型请求的工具调用
                    tool_call_msg = ToolCallRequestEvent(
                        content=model_result.content,
                        source=agent_name,
                        models_usage=model_result.usage,
                    )
                    inner_messages.append(tool_call_msg)
                    logger.debug(tool_call_msg)
                    yield tool_call_msg

                    pending_tool_results: List[FunctionExecutionResult] = []

                    # 处理模型请求的工具调用
                    for i in range(len(model_result.content)):
                        argument = json.loads(model_result.content[i].arguments)
                        tool_name = model_result.content[i].name
                        call_id = model_result.content[i].id
                        if tool_name == "Skill":
                            if skills_loader is None:
                                skill_content = "No system skill is loaded."
                            else:
                                skill_content = skills_loader.run_skill(argument["skill"])
                            pending_tool_results.append(
                                FunctionExecutionResult(
                                    content=f"Skill for {argument['skill']}:\n\n {skill_content}",
                                    name=tool_name,
                                    call_id=call_id,
                                    is_error=False,
                                )
                            )
                            yield AgentLogEvent(
                                title=f"I am reading skill: {argument['skill']}.",
                                source=agent_name,
                                content=str(argument),
                                content_type="tools")
                        elif tool_name == "PromoteSkillToCore":
                            promote_message = self._promote_skill_to_core(
                                skills_loader=skills_loader,
                                skill_name=argument["skill"],
                                reason=argument["reason"],
                                focus=argument.get("focus", ""),
                            )
                            pending_tool_results.append(
                                FunctionExecutionResult(
                                    content=promote_message,
                                    name=tool_name,
                                    call_id=call_id,
                                    is_error=False,
                                )
                            )
                            self.update_system_prompt()
                            system_messages = self._system_messages
                            yield AgentLogEvent(
                                title="I am promoting a skill to core system rules.",
                                source=agent_name,
                                content=str(argument),
                                content_type="tools",
                            )
                        elif tool_name == "DemoteCoreSkill":
                            demote_message = self._demote_core_skill(
                                skill_name=argument["skill"],
                                reason=argument.get("reason", ""),
                            )
                            pending_tool_results.append(
                                FunctionExecutionResult(
                                    content=demote_message,
                                    name=tool_name,
                                    call_id=call_id,
                                    is_error=False,
                                )
                            )
                            self.update_system_prompt()
                            system_messages = self._system_messages
                            yield AgentLogEvent(
                                title="I am demoting a core skill.",
                                source=agent_name,
                                content=str(argument),
                                content_type="tools",
                            )
                        elif tool_name == "ReplaceCoreSkill":
                            replace_message = self._replace_core_skill(
                                skills_loader=skills_loader,
                                old_skill=argument["old_skill"],
                                new_skill=argument["new_skill"],
                                reason=argument["reason"],
                                focus=argument.get("focus", ""),
                            )
                            pending_tool_results.append(
                                FunctionExecutionResult(
                                    content=replace_message,
                                    name=tool_name,
                                    call_id=call_id,
                                    is_error=False,
                                )
                            )
                            self.update_system_prompt()
                            system_messages = self._system_messages
                            yield AgentLogEvent(
                                title="I am replacing a core skill.",
                                source=agent_name,
                                content=str(argument),
                                content_type="tools",
                            )
                        elif tool_name in self._tools_names:
                            yield AgentLogEvent(
                                title="I am using tool: " + tool_name,
                                source=agent_name,
                                content=str(model_result.content[i]),
                                content_type="tools")

                            _, result = await self._execute_tool_call(
                                tool_call=model_result.content[i],
                                workbench=workbench,
                                handoff_tools=handoff_tools,
                                agent_name=agent_name,
                                cancellation_token=cancellation_token,
                            )

                            try:
                                parsed = json.loads(result.content)
                                if isinstance(parsed, dict) and parsed.get("type") == "files_event":
                                    event_data = parsed.get("event", None)
                                    if isinstance(event_data, str):
                                        try:
                                            event_data = json.loads(event_data)
                                        except Exception:
                                            event_data = None
                                    if isinstance(event_data, dict):
                                        try:
                                            files_event = FilesEvent.model_validate(event_data)
                                            yield files_event
                                            result.content = "Served files to the right side of frontend for download."
                                        except Exception:
                                            # logger.error(f"Error parsing FilesEvent: {event_data}, error: {traceback.format_exc()}")
                                            result.content = "Served 0 file event(s) to user."
                                    else:
                                        result.content = "Served 0 file event(s) to user."
                            except Exception:
                                # logger.error(f"Error parsing tool result content as JSON: {result.content}, error: {traceback.format_exc()}")
                                pass

                            result.content = f"{self._lang_prompt}\n\n{result.content}\n\n{self._lang_prompt}"
                            pending_tool_results.append(result)
                            img_paths = []
                            img_paths.extend(_extract_existing_image_paths_from_content(result.content))
                            if img_paths:
                                for img_path in img_paths:
                                    # 把工具输出中的图片回显给用户
                                    fig_url = show_file_to_user(model_client, file_path=img_path)
                                    info2user = f"### ![image.png]({fig_url})\n\n"
                                    yield ModelClientStreamingChunkEvent(content=info2user, source=agent_name)

                                    # 暂时禁用：工具调用后将图片强制回注到主模型上下文。
                                    # 当前仅回显给用户，不回注到主模型上下文。

                        else:
                            pending_tool_results.append(
                                FunctionExecutionResult(
                                    content=f"Unknown tool: {model_result.content[i].name}",
                                    name=tool_name,
                                    call_id=call_id,
                                    is_error=False,
                                )
                            )

                    if pending_tool_results:
                        # 工具调用场景：将 tool_call 与 tool_result 作为一个原子对提交到上下文，
                        # 避免 pause/cancel 时只写入半对消息导致顺序不一致。
                        await model_context.add_message(
                            AssistantMessage(
                                content=model_result.content,
                                source=self._name,
                                thought=getattr(model_result, "thought", None),
                            )
                        )
                        await model_context.add_message(
                            FunctionExecutionResultMessage(content=pending_tool_results)
                        )
                    continue

                remaining_fallbacks = [name for name in fallback_candidates if name not in fallback_attempted]
                fallback_errors: List[str] = []
                switched_model: Optional[str] = None
                unexpected_type = type(model_result.content).__name__
                for fallback_config_name in remaining_fallbacks:
                    fallback_attempted.add(fallback_config_name)
                    switched_model = await self._switch_to_fallback_model(fallback_config_name)
                    if switched_model:
                        break
                    fallback_errors.append(fallback_config_name)

                if switched_model:
                    yield ModelClientStreamingChunkEvent(
                        content=f"\n[System Notice] Abnormal model output detected. Switched to fallback model: {switched_model}. Retrying.\n",
                        source=agent_name,
                    )
                    logger.warning(
                        "Recovered from unexpected model output type '{}' by switching to a fallback model. Retrying the full turn.",
                        unexpected_type,
                    )
                    continue

                all_candidates = fallback_candidates or ["<none>"]
                if not remaining_fallbacks:
                    error_detail = (
                        f"Model returned unsupported output type '{unexpected_type}'. "
                        f"All fallback models have already been tried in this request: {all_candidates}. "
                        "Please try again later."
                    )
                else:
                    error_detail = (
                        f"Model returned unsupported output type '{unexpected_type}'. "
                        f"Fallback model configs tried: {sorted(fallback_attempted) or all_candidates}. "
                        "Please try again later."
                    )
                if fallback_errors:
                    error_detail += f" Failed fallback configs: {', '.join(fallback_errors)}."

                logger.error(error_detail)
                yield Response(
                    chat_message=TextMessage(
                        content=error_detail,
                        source=self._agent_name,
                        metadata={"internal": "no"},
                    ),
                    inner_messages=inner_messages,
                )
                break

        except asyncio.CancelledError:
            for buffered_event in last_buffered_stream_events:
                yield buffered_event
            # 任务被取消时，返回统一提示
            yield Response(
                chat_message=TextMessage(
                    content="The task was cancelled by the user.",
                    source=self._agent_name,
                    metadata={"internal": "no"},
                ),
                inner_messages=inner_messages,
            )
        except Exception as e:
            logger.error(f"Error in {self._agent_name}: {e}")
            logger.error(traceback.format_exc())
            # add to chat history
            await self._model_context.add_message(
                AssistantMessage(
                    content=f"An error occurred while executing the task: {e}",
                    source=self._name
                )
            )
            yield Response(
                chat_message=TextMessage(
                    content=f"An error occurred while executing the task: {e}",
                    source=self._agent_name,
                    metadata={"internal": "no"},
                ),
                inner_messages=inner_messages,
            )
        finally:
            # 收尾：上传会话到 ragflow（如启用）并清理当前消息缓存
            if isinstance(self._model_context, DrSaiChatCompletionContext):
                # Legacy (disabled): 如需恢复 tool 图片回注上下文，启用下一行。
                # self._cleanup_all_ephemeral_images(self._model_context)
                if self._model_context._rag_flow_manager:
                    await self._model_context.upload_conversation_to_ragflow()
                await self._restore_default_model()
                self._model_context._current_messages = []
            # await self.resume()


    async def _call_llm(
        self,
        model_client: ChatCompletionClient,
        model_client_stream: bool,
        system_messages: List[SystemMessage],
        model_context: ChatCompletionContext,
        workbench: Workbench,
        handoff_tools: List[BaseTool[Any, Any]],
        manager_tools: List[ToolSchema],
        agent_name: str,
        cancellation_token: CancellationToken,
        output_content_type: type[BaseModel] | None,
    ) -> AsyncGenerator[Union[CreateResult, ModelClientStreamingChunkEvent], None]:
        """执行一次模型推理，流式产出 chunk，最后产出 CreateResult。"""

        if isinstance(model_context, DrSaiChatCompletionContext):
            all_messages = await model_context.get_messages(cancellation_token = cancellation_token)
        else:
            all_messages = await model_context.get_messages()
        
        runtime_messages = list(all_messages)
        # Legacy (disabled): 如需恢复 tool 图片回注上下文，在此拼接 inflight 图片消息。
        # if self._ephemeral_images_inflight:
        #     runtime_messages.extend(self._ephemeral_images_inflight)
        # runtime_messages = self._repair_trimmed_runtime_messages(runtime_messages)

        llm_messages: List[LLMMessage] = self._get_compatible_context(
            model_client=model_client,
            messages=system_messages + runtime_messages,
        )

        # 自定义的memory_function，用于RAG检索等功能，为大模型回复增加最新的知识
        if self._memory_function is not None:
            llm_messages = await self._call_memory_function(llm_messages, model_client, cancellation_token, agent_name)

        all_tools = (await workbench.list_tools()) + handoff_tools + manager_tools
        try:
            if self._reply_function is not None:
                # 自定义回复函数：允许上层接管模型调用流程
                async for chunk in self._call_reply_function(
                    llm_messages,
                    model_client=model_client,
                    workbench=workbench,
                    handoff_tools=handoff_tools,
                    tools=all_tools,
                    agent_name=agent_name,
                    cancellation_token=cancellation_token,
                    db_manager=self._db_manager,
                ):
                    yield chunk
            else:
                async for chunk in self.call_llm(
                    agent_name=agent_name,
                    model_client=model_client,
                    llm_messages=llm_messages,
                    tools=all_tools,
                    model_client_stream=model_client_stream,
                    cancellation_token=cancellation_token,
                    output_content_type=output_content_type,
                ):
                    yield chunk
            return
        except Exception as call_err:
            if not self._is_context_length_error(call_err):
                raise

            logger.warning(
                "Detected context overflow, invoking history summarizer sub-agent and retrying once. error={} ",
                call_err,
            )
            retry_messages = await self._build_retry_llm_messages_after_context_overflow(
                model_client=model_client,
                system_messages=system_messages,
                runtime_messages=runtime_messages,
                cancellation_token=cancellation_token,
            )

            # 重试时不再注入 memory_function，避免再次膨胀上下文。
            if self._reply_function is not None:
                async for chunk in self._call_reply_function(
                    retry_messages,
                    model_client=model_client,
                    workbench=workbench,
                    handoff_tools=handoff_tools,
                    tools=all_tools,
                    agent_name=agent_name,
                    cancellation_token=cancellation_token,
                    db_manager=self._db_manager,
                ):
                    yield chunk
            else:
                async for chunk in self.call_llm(
                    agent_name=agent_name,
                    model_client=model_client,
                    llm_messages=retry_messages,
                    tools=all_tools,
                    model_client_stream=model_client_stream,
                    cancellation_token=cancellation_token,
                    output_content_type=output_content_type,
                ):
                    yield chunk

    async def handle_str_reponse(
            self,
            model_result: CreateResult,
            agent_name: str,
            format_string: str | None,
            inner_messages: List[BaseAgentEvent | BaseChatMessage],
            output_content_type: type[BaseModel] | None,
    ) -> Response:

        if output_content_type:
            content = output_content_type.model_validate_json(model_result.content)
            return Response(
                chat_message=StructuredMessage[output_content_type](  # type: ignore[valid-type]
                    content=content,
                    source=agent_name,
                    models_usage=model_result.usage,
                    format_string=format_string,
                ),
                inner_messages=inner_messages,
            )
        else:
            return Response(
                chat_message=TextMessage(
                    content=model_result.content,
                    source=agent_name,
                    models_usage=model_result.usage,
                ),
                inner_messages=inner_messages,
            )
    
    # 后续可扩展：在不引入用户可写配置的前提下，增加更多系统级能力。
    def _to_config(self) -> DrSaiAssistantConfig:
        """将当前智能体实例导出为声明式配置。"""

        return DrSaiAssistantConfig(
            name=self.name,
            model_client=self._model_client.dump_component(),
            tools=None,  # versionchanged:: v0.5.5  Now tools are not serialized, Cause they are part of the workbench.
            workbench=self._workbench.dump_component() if self._workbench else None,
            handoffs=list(self._handoffs.values()) if self._handoffs else None,
            model_context=self._model_context.dump_component(),
            memory=[memory.dump_component() for memory in self._memory] if self._memory else None,
            description=self.description,
            system_message=self._system_messages[0].content
            if self._system_messages and isinstance(self._system_messages[0].content, str)
            else None,
            model_client_stream=self._model_client_stream,
            reflect_on_tool_use=self._reflect_on_tool_use,
            tool_call_summary_format=self._tool_call_summary_format,
            tool_call_summary_prompt=self._tool_call_summary_prompt,
            structured_message_factory=self._structured_message_factory.dump_component()
            if self._structured_message_factory
            else None,
            metadata=self._metadata,
            # drsaiAgent specific
            db_manager_config=self._db_manager.dump_component(),
            thread_id=self._thread_id,
            user_id=self._user_id,
            # skills and executor
            skills_dir=self._skills_dir,
            work_dir=self._work_dir,
            only_in_workspace=self._only_in_workspace,
            extra_work_dirs=self._extra_work_dirs,
            executor=self._local_executor.dump_component(),
            token_limit=self._token_limit,
            rag_flow_url=self._rag_flow_url,
            rag_flow_token=self._rag_flow_token,
            memory_dataset_id=self._memory_dataset_id,
            learning_dataset_id=self._learning_dataset_id,
        )
    
    @classmethod
    def _from_config(
        cls, config: DrSaiAssistantConfig, 
        db_manager: DatabaseManager,
        memory_function: Callable = None,
        reply_function: Callable = None,
        **kwargs,
        ) -> Self:
        """Create an assistant agent from a declarative config."""
        if config.structured_message_factory:
            structured_message_factory = StructuredMessageFactory.load_component(config.structured_message_factory)
            format_string = structured_message_factory.format_string
            output_content_type = structured_message_factory.ContentModel

        else:
            format_string = None
            output_content_type = None

        return cls(
            name=config.name,
            model_client=ChatCompletionClient.load_component(config.model_client),
            workbench=Workbench.load_component(config.workbench) if config.workbench else None,
            handoffs=config.handoffs,
            model_context=ChatCompletionContext.load_component(config.model_context) if config.model_context else None,
            tools=[BaseTool.load_component(tool) for tool in config.tools] if config.tools else None,
            memory=[Memory.load_component(memory) for memory in config.memory] if config.memory else None,
            description=config.description,
            system_message=config.system_message,
            model_client_stream=config.model_client_stream,
            reflect_on_tool_use=config.reflect_on_tool_use,
            tool_call_summary_format=config.tool_call_summary_format,
            tool_call_summary_prompt=config.tool_call_summary_prompt,
            output_content_type=output_content_type,
            output_content_type_format=format_string,
            metadata=config.metadata,
            # drsaiAgent specific
            memory_function=memory_function,
            reply_function=reply_function,
            db_manager=db_manager,
            thread_id=config.thread_id,
            user_id=config.user_id,
            # skills and executor
            skills_dir=config.skills_dir,
            work_dir=config.work_dir,
            only_in_workspace=config.only_in_workspace,
            extra_work_dirs=config.extra_work_dirs,
            executor=CodeExecutor.load_component(config.executor),
            token_limit = config.token_limit,
            rag_flow_url = config.rag_flow_url,
            rag_flow_token = config.rag_flow_token,
            memory_dataset_id = config.memory_dataset_id,
            learning_dataset_id = config.learning_dataset_id,
            **kwargs,
        )


        

