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
import os, json
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
    get_operator_funcs_rongzai,
)

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
            )
        # self._basic_funcs_names = [func.__name__ for func in self._basic_funcs]
        for func in self._basic_funcs:
            if func.__name__ in {"run_bash", "run_read", "run_write", "run_edit"}:
                self._tools.append(FunctionTool(func, description=func.__doc__))

        # 将rongzai需要的特定的函数转换为FunctionTool对象并加入self._tools列表
        self._otherBasic_funcs: List[Callable] = get_operator_funcs_rongzai(source_name = self._name)
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
        self._tools.append(
            FunctionTool(
                self.CallMcpTool,
                description=self.CallMcpTool.__doc__ or "",
                name="CallMcpTool",
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

        self._download_dir = self._work_dir / "downloads"
        self._download_dir.mkdir(parents=True, exist_ok=True)

        # === skills ===
        # 仅加载系统级 skills，禁用用户覆盖。
        self._skills_dir = skills_dir
        self._agent_skills_tools = []
        self._core_skill_tools = [
            self._get_promote_skill_tool(),
            self._get_demote_skill_tool(),
            self._get_replace_core_skill_tool(),
            self._get_finish_task_tool(),
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

        # === Ephemeral multimodal image context (Stage A) ===
        # pending: 新注入图片，供“下一轮”推理可见
        # inflight: 当前这轮推理正在消费，推理完成后清理
        self._ephemeral_images_pending: List[UserMessage] = []
        self._ephemeral_images_inflight: List[UserMessage] = []

    def _record_ephemeral_image_message(self, multimodal_msg: UserMessage) -> None:
        """记录新注入的临时图片消息，供下一轮推理使用。"""
        self._ephemeral_images_pending.append(multimodal_msg)

    def _arm_ephemeral_images_for_inference(self) -> None:
        """在每轮推理开始前装载临时图片消息。"""
        self._ephemeral_images_inflight = self._ephemeral_images_pending
        self._ephemeral_images_pending = []

    def _cleanup_inflight_ephemeral_images(self, model_context: ChatCompletionContext) -> None:
        """在每轮推理结束后清理已消费的临时图片消息。"""
        if not self._ephemeral_images_inflight:
            return
        if not isinstance(model_context, DrSaiChatCompletionContext):
            # 仅 DrSai 运行时上下文支持按对象身份清理。
            self._ephemeral_images_inflight = []
            return

        target_ids = {id(msg) for msg in self._ephemeral_images_inflight}
        if target_ids:
            model_context._messages = [msg for msg in model_context._messages if id(msg) not in target_ids]
            model_context._current_messages = [msg for msg in model_context._current_messages if id(msg) not in target_ids]
        self._ephemeral_images_inflight = []

    def _cleanup_all_ephemeral_images(self, model_context: ChatCompletionContext) -> None:
        """兜底清理：移除 pending/inflight 的所有临时图片消息。"""
        if not isinstance(model_context, DrSaiChatCompletionContext):
            self._ephemeral_images_pending = []
            self._ephemeral_images_inflight = []
            return

        all_ephemeral = self._ephemeral_images_pending + self._ephemeral_images_inflight
        target_ids = {id(msg) for msg in all_ephemeral}
        if target_ids:
            model_context._messages = [msg for msg in model_context._messages if id(msg) not in target_ids]
            model_context._current_messages = [msg for msg in model_context._current_messages if id(msg) not in target_ids]
        self._ephemeral_images_pending = []
        self._ephemeral_images_inflight = []

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
        enhanced_system_message = self._developer_system_message or ""
        if self._thread_id:
            enhanced_system_message = f"{enhanced_system_message}\n\nCurrent Session_ID is {self._thread_id}"
        if self._user_id:
            enhanced_system_message = (
                f"{enhanced_system_message}\n\nCurrent user_id is {self._user_id}. "
                f"Uploaded user files are under /home/wangh/rongzai-agent/tmp/drsai_ui/files/user/{self._user_id}/. If user has uploaded any files, they are in this directory."
            )
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
        - 后续应优先使用这些新注册的本地代理工具；只有在确有必要时，才使用 CallMcpTool 兜底。
        """
        conn_type = (type or "").strip().lower()
        try:
            if conn_type == "sse":
                server_id = (url or "").strip()
                if not server_id:
                    return "Error: 'url' is required when type='sse'."
                server_params = SseServerParams(url=server_id, headers=headers or {})
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

    async def CallMcpTool(self, server_id: str, tool_name: str, arguments_json: str = "{}") -> str:
        """调用已连接 MCP server 的原始工具。

        这是兜底工具：
        - 如果 ConnectMcpServer 已经把远程工具注册成了本地代理工具，优先直接调用那些代理工具。
        - 仅在你需要按原始远程工具名调用，或暂时无法直接使用本地代理工具时，再使用本工具。

        参数要求：
        - server_id 是 ConnectMcpServer 连接时使用的 URL 或 command。
        - tool_name 是远程 MCP 工具的原始名称。
        - arguments_json 必须是一个 JSON 对象字符串。
        """
        if server_id not in self._mcp_connections:
            return (
                f"Error: MCP server '{server_id}' is not connected. "
                "Call ConnectMcpServer first."
            )
        try:
            parsed_args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as e:
            return f"Error: invalid arguments_json: {e}"
        try:
            result = await self._mcp_connections[server_id].call_tool(tool_name, parsed_args)
            return result.to_text()
        except Exception as e:
            return f"Error calling '{tool_name}' on '{server_id}': {e}"

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

    def _get_finish_task_tool(self) -> ToolSchema:
        """供模型调用：结束当前任务循环。"""
        parameters = ParametersSchema(
            type="object",
            properties={
                "final_response": {
                    "type": "string",
                    "description": "Final natural-language response to the user.",
                },
            },
            required=["final_response"],
            additionalProperties=False,
        )
        return ToolSchema(
            name="FinishTask",
            description="End the current loop and deliver final_response to the user.",
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
            "If there is a conflict with normal skill text, follow this core section first.",
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
            for msg in task:
                if isinstance(msg, BaseChatMessage):
                    msg.metadata["internal"] = "yes"
                    input_messages.append(msg)
                    output_messages.append(msg)
                    try:
                        attached_files_json = msg.metadata.get("attached_files")
                        if attached_files_json:
                            attached_files = json.loads(attached_files_json)
                            for file in attached_files:
                                download_file_from_url_or_base64(
                                    file_info = file, 
                                    save_path = f"{self._download_dir}/{file['name']}")
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
        try:
            # 每轮重建系统提示词，以纳入最新 Core Skill 状态。
            self.update_system_prompt()

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
            
            # 将用户消息写入模型上下文
            await self._add_messages_to_context(
                model_context=model_context,
                messages=messages,
            )

            text_only_retry = 0
            text_only_fallback_threshold = 2
            while True:
                self._debug_log_system_prompt(system_messages)
                model_result = None
                buffered_stream_events: List[ModelClientStreamingChunkEvent] = []
                self._arm_ephemeral_images_for_inference()
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
                finally:
                    # 临时图片只保留到“当前推理”结束。
                    self._cleanup_inflight_ephemeral_images(model_context)

                assert model_result is not None, "No model result was produced."
                

                
                # 将 assistant 结果写回上下文（包含可选 thought）
                await model_context.add_message(
                    AssistantMessage(
                        content=model_result.content,
                        source=self._name,
                        thought=getattr(model_result, "thought", None),
                    )
                )
                
                # 纯文本响应不允许直接结束循环；必须通过 FinishTask 显式结束
                if isinstance(model_result.content, str):
                    text_only_retry += 1
                    plain_text = model_result.content.strip()

                    # 兜底：兼容不支持工具调用的模型，避免无限循环无输出。
                    if text_only_retry >= text_only_fallback_threshold:
                        fallback_text = plain_text or "任务已完成。"
                        yield Response(
                            chat_message=TextMessage(
                                content=fallback_text,
                                source=agent_name,
                                models_usage=model_result.usage,
                            ),
                            inner_messages=inner_messages,
                        )
                        break

                    guidance = (
                        "You just returned plain text. Plain text cannot terminate this loop. "
                        "If the task is finished, call FinishTask with final_response. "
                        "If not finished, continue with necessary tool calls."
                    )
                    await model_context.add_message(AssistantMessage(content=guidance, source=self._name))
                    continue

                # 否则进入工具调用分支
                assert isinstance(model_result.content, list) and all(
                    isinstance(item, FunctionCall) for item in model_result.content
                )
                text_only_retry = 0

                has_finish_task = any(call.name == "FinishTask" for call in model_result.content)

                # 普通工具轮次仍可显示模型流式文本；FinishTask 轮次则丢弃这些文本，避免结束前重复输出。
                if not has_finish_task:
                    for buffered_event in buffered_stream_events:
                        yield buffered_event

                # 处理模型请求的工具调用
                finish_requested = False
                finish_response_text = ""
                for i in range(len(model_result.content)):
                    argument = json.loads(model_result.content[i].arguments)
                    tool_name = model_result.content[i].name
                    call_id = model_result.content[i].id
                    if tool_name == "Skill":
                        if skills_loader is None:
                            skill_content = "No system skill is loaded."
                        else:
                            skill_content = skills_loader.run_skill(argument["skill"])
                        await model_context.add_message(FunctionExecutionResultMessage(
                            content=[FunctionExecutionResult(
                                content = f"Skill for {argument['skill']}:\n\n {skill_content}",
                                name = tool_name,
                                call_id = call_id,
                                is_error = False,
                            ),]
                        ))
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
                        await model_context.add_message(FunctionExecutionResultMessage(
                            content=[FunctionExecutionResult(
                                content=promote_message,
                                name=tool_name,
                                call_id=call_id,
                                is_error=False,
                            )]
                        ))
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
                        await model_context.add_message(FunctionExecutionResultMessage(
                            content=[FunctionExecutionResult(
                                content=demote_message,
                                name=tool_name,
                                call_id=call_id,
                                is_error=False,
                            )]
                        ))
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
                        await model_context.add_message(FunctionExecutionResultMessage(
                            content=[FunctionExecutionResult(
                                content=replace_message,
                                name=tool_name,
                                call_id=call_id,
                                is_error=False,
                            )]
                        ))
                        self.update_system_prompt()
                        system_messages = self._system_messages
                        yield AgentLogEvent(
                            title="I am replacing a core skill.",
                            source=agent_name,
                            content=str(argument),
                            content_type="tools",
                        )
                    elif tool_name == "FinishTask":
                        tool_feedback = "Loop termination requested via FinishTask."
                        finish_response_text = str(argument.get("final_response", "")).strip()
                        if not finish_response_text:
                            finish_response_text = "任务已完成。"
                        await model_context.add_message(FunctionExecutionResultMessage(
                            content=[FunctionExecutionResult(
                                content=tool_feedback,
                                name=tool_name,
                                call_id=call_id,
                                is_error=False,
                            )]
                        ))
                        finish_requested = True
                    elif tool_name in self._tools_names:
                        async for message in self._process_model_result(
                            model_result=model_result,
                            inner_messages=inner_messages,
                            cancellation_token=cancellation_token,
                            agent_name=agent_name,
                            system_messages=system_messages,
                            model_context=model_context,
                            workbench=workbench,
                            handoff_tools=handoff_tools,
                            handoffs=handoffs,
                            model_client=model_client,
                            model_client_stream=model_client_stream,
                            reflect_on_tool_use=reflect_on_tool_use,
                            tool_call_summary_format=tool_call_summary_format,
                            tool_call_summary_prompt=self._tool_call_summary_prompt,
                            output_content_type=output_content_type,
                            format_string=format_string,
                            image_message_recorder=self._record_ephemeral_image_message,
                        ):
                            if self.is_paused:
                                raise asyncio.CancelledError()
                            if isinstance(message, Response):
                                yield message.chat_message
                            else:
                                yield message
                    else:
                        await model_context.add_message(FunctionExecutionResultMessage(
                            content=[FunctionExecutionResult(
                                content = f"Unknown tool: {model_result.content[i].name}",
                                name = tool_name,
                                call_id = call_id,
                                is_error = False,
                            ),]
                        ))

                if finish_requested:
                    await model_context.add_message(
                        AssistantMessage(
                            content=finish_response_text,
                            source=self._name,
                        )
                    )
                    yield ModelClientStreamingChunkEvent(
                        content=finish_response_text,
                        source=agent_name,
                    )
                    yield Response(
                        chat_message=TextMessage(
                            content=finish_response_text,
                            source=agent_name,
                            models_usage=model_result.usage,
                        ),
                        inner_messages=inner_messages,
                    )
                    break

        except asyncio.CancelledError:
            # 任务被取消时，返回统一提示
            yield Response(
                chat_message=TextMessage(
                    content="The task was cancelled by the user.",
                    source=self._agent_name,
                    metadata={"internal": "yes"},
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
                self._cleanup_all_ephemeral_images(self._model_context)
                if self._model_context._rag_flow_manager:
                    await self._model_context.upload_conversation_to_ragflow()
                self._model_context._current_messages = []


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
        
        llm_messages: List[LLMMessage] = self._get_compatible_context(model_client=model_client, messages=system_messages + all_messages)

        # 自定义的memory_function，用于RAG检索等功能，为大模型回复增加最新的知识
        if self._memory_function is not None:
            llm_messages = await self._call_memory_function(llm_messages, model_client, cancellation_token, agent_name)

        all_tools = (await workbench.list_tools()) + handoff_tools + manager_tools
        if self._reply_function is not None:
            # 自定义回复函数：允许上层接管模型调用流程
            async for chunk in self._call_reply_function(
                llm_messages, 
                model_client = model_client, 
                workbench=workbench,
                handoff_tools=handoff_tools,
                tools = all_tools,
                agent_name=agent_name, 
                cancellation_token=cancellation_token,
                db_manager=self._db_manager,
            ):
                yield chunk
        else:
           async for chunk in self.call_llm(
                agent_name = agent_name,
                model_client = model_client,
                llm_messages = llm_messages, 
                tools = all_tools, 
                model_client_stream = model_client_stream,
                cancellation_token = cancellation_token,
                output_content_type = output_content_type,
           ):
               yield chunk
    
    @classmethod
    async def _process_model_result(
        cls,
        model_result: CreateResult,
        inner_messages: List[BaseAgentEvent | BaseChatMessage],
        cancellation_token: CancellationToken,
        agent_name: str,
        system_messages: List[SystemMessage],
        model_context: ChatCompletionContext,
        workbench: Workbench,
        handoff_tools: List[BaseTool[Any, Any]],
        handoffs: Dict[str, HandoffBase],
        model_client: ChatCompletionClient,
        model_client_stream: bool,
        reflect_on_tool_use: bool,
        tool_call_summary_format: str,
        tool_call_summary_prompt: str | None,
        output_content_type: type[BaseModel] | None,
        format_string: str | None = None,
        image_message_recorder: Callable[[UserMessage], None] | None = None,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        """执行工具调用并把结果写回上下文，同时处理文件与图片事件。"""
        
        tool_call_msg = ToolCallRequestEvent(
            content=model_result.content,
            source=agent_name,
            models_usage=model_result.usage,
        )
        inner_messages.append(tool_call_msg)
        logger.debug(tool_call_msg)
        yield tool_call_msg
        tools_name = [tool.name for tool in model_result.content] 
        yield AgentLogEvent(
            title="I am using tools: " + " ".join(tools_name),
            source=agent_name, 
            content=str(tool_call_msg.content), 
            content_type="tools")

        # 并发执行本轮工具调用
        executed_calls_and_results = await asyncio.gather(
            *[
                cls._execute_tool_call(
                    tool_call=call,
                    workbench=workbench,
                    handoff_tools=handoff_tools,
                    agent_name=agent_name,
                    cancellation_token=cancellation_token,
                )
                for call in model_result.content
            ]
        )
        exec_results = [result for _, result in executed_calls_and_results]

        # 解析工具返回的结构化文件事件并转发给用户
        for result in exec_results:
            try:
                parsed = json.loads(result.content)
            except Exception:
                continue

            if isinstance(parsed, dict) and parsed.get("type") == "files_events":
                events = parsed.get("events", [])
                sent_count = 0
                for event_data in events:
                    try:
                        files_event = FilesEvent.model_validate(event_data)
                        yield files_event
                        sent_count += 1
                    except Exception:
                        continue
                result.content = f"Served {sent_count} file event(s) to user."

        await model_context.add_message(FunctionExecutionResultMessage(content=exec_results))
        img_paths = []
        for result in exec_results:
            img_paths.extend(_extract_existing_image_paths_from_content(result.content))
        if img_paths:
            for img_path in img_paths:
                # 把工具输出中的图片回显给用户
                fig_url = show_file_to_user(model_client, file_path=img_path)
                info2user = f"### ![image.png]({fig_url})\n\n"
                yield ModelClientStreamingChunkEvent(content=info2user, source=agent_name)

                # 同时把图片作为多模态用户消息回注到上下文，便于后续推理
                image_ob = Image.from_file(file_path=img_path)
                multimodal_msg = UserMessage(
                    content=[f"the figure of {img_path}", image_ob],
                    source="user",
                )
                if isinstance(model_context, DrSaiChatCompletionContext):
                    # 本地序列化暂不支持多模态持久化，直接写入运行时消息缓存
                    model_context._messages.append(multimodal_msg)
                    model_context._current_messages.append(multimodal_msg)
                    if image_message_recorder is not None:
                        image_message_recorder(multimodal_msg)
                else:
                    await model_context.add_message(multimodal_msg)

        # 设计上不再额外输出工具摘要文本
        return

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


        

