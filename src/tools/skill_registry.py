from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List

from drsai.modules.components.tool import BaseTool, SseServerParams, StdioServerParams, mcp_server_tools


@dataclass
class SkillMeta:
    name: str
    description: str
    body_path: Path
    toolchains: List[str]
    mcp_servers: List[Dict[str, Any]]


class SkillRegistryManager:
    """Manage skill metadata and dynamic MCP loading from a JSON registry."""

    def __init__(self, registry_path: str | Path) -> None:
        self.registry_path = Path(registry_path)
        self.registry_dir = self.registry_path.parent

    def _read_registry(self) -> Dict[str, Any]:
        if not self.registry_path.exists():
            raise FileNotFoundError(f"Skill registry not found: {self.registry_path}")
        with self.registry_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Skill registry must be a JSON object.")
        data.setdefault("skills", [])
        return data

    def _to_meta(self, item: Dict[str, Any]) -> SkillMeta:
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        body = str(item.get("body", "")).strip()
        if not name:
            raise ValueError("Each skill must include a non-empty 'name'.")
        if not body:
            raise ValueError(f"Skill '{name}' must include a non-empty 'body'.")

        body_path = Path(body)
        if not body_path.is_absolute():
            body_path = self.registry_dir / body_path

        toolchains = item.get("toolchains") or []
        if not isinstance(toolchains, list):
            raise ValueError(f"Skill '{name}' field 'toolchains' must be a list.")

        mcp_servers = item.get("mcp_servers") or []
        if not isinstance(mcp_servers, list):
            raise ValueError(f"Skill '{name}' field 'mcp_servers' must be a list.")

        return SkillMeta(
            name=name,
            description=description,
            body_path=body_path,
            toolchains=[str(x) for x in toolchains],
            mcp_servers=[dict(x) for x in mcp_servers],
        )

    def list_skills(self) -> List[SkillMeta]:
        data = self._read_registry()
        return [self._to_meta(item) for item in data.get("skills", [])]

    def get_skill(self, skill_name: str) -> SkillMeta:
        skill_name = skill_name.strip()
        for skill in self.list_skills():
            if skill.name == skill_name:
                return skill
        raise ValueError(f"Skill '{skill_name}' not found in registry.")

    def load_skill_body(self, skill_name: str) -> str:
        meta = self.get_skill(skill_name)
        if not meta.body_path.exists():
            raise FileNotFoundError(f"Skill body not found: {meta.body_path}")
        return meta.body_path.read_text(encoding="utf-8")

    async def load_mcp_tools(self, skill_name: str) -> List[BaseTool[Any, Any]]:
        meta = self.get_skill(skill_name)
        loaded: List[BaseTool[Any, Any]] = []

        for server in meta.mcp_servers:
            server_type = str(server.get("type", "")).strip().lower()
            if server_type in {"mcp-sse", "sse"}:
                url = server.get("url")
                if not url:
                    raise ValueError(f"Skill '{meta.name}' has mcp-sse server without url.")
                tools = await mcp_server_tools(
                    SseServerParams(
                        url=str(url),
                        headers=server.get("headers", None),
                        timeout=float(server.get("timeout", 20)),
                        sse_read_timeout=float(server.get("sse_read_timeout", 300)),
                    )
                )
                loaded.extend(tools)
            elif server_type in {"mcp-std", "std", "stdio"}:
                command = server.get("command")
                args = server.get("args")
                if not command or not isinstance(args, list):
                    raise ValueError(
                        f"Skill '{meta.name}' has mcp-std server with invalid command/args."
                    )
                tools = await mcp_server_tools(
                    StdioServerParams(
                        command=str(command),
                        args=[str(arg) for arg in args],
                    )
                )
                loaded.extend(tools)
            else:
                raise ValueError(
                    f"Skill '{meta.name}' has unsupported MCP server type: {server_type!r}"
                )

        return loaded

    def render_catalog(self) -> str:
        skills = self.list_skills()
        if not skills:
            return "(当前没有可用技能)"
        return "\n".join(f"- {s.name}: {s.description}" for s in skills)
