from __future__ import annotations

from typing import List

from drsai.modules.components.tool import ParametersSchema, ToolSchema


class StructuredSkillPolicyManager:
    """Track a single active core-skill slot for runtime guidance."""

    def __init__(self) -> None:
        self.active_core_skill_name: str | None = None
        self.active_core_skill_guidance: str = ""

    def promote(self, skill_name: str, guidance: str) -> None:
        name = (skill_name or "").strip()
        if not name:
            raise ValueError("skill_name is required")
        self.active_core_skill_name = name
        self.active_core_skill_guidance = (guidance or "").strip()

    def demote(self, skill_name: str, guidance: str) -> None:
        name = (skill_name or "").strip()
        if not name:
            raise ValueError("skill_name is required")
        if self.active_core_skill_name == name:
            self.active_core_skill_name = None
            self.active_core_skill_guidance = ""

    def replace(self, skill_name: str, target_skill: str, guidance: str) -> None:
        target = (target_skill or "").strip()
        if not target:
            raise ValueError("target_skill is required")
        self.active_core_skill_name = target
        self.active_core_skill_guidance = (guidance or "").strip()

    def get_active_core_skill_name(self) -> str | None:
        return self.active_core_skill_name

    def build_system_prompt_block(self) -> str:
        if not self.active_core_skill_name:
            return (
                "[Runtime Skill Policy]\n"
                "No core skill is active. You may use standard skills normally. "
                "If you need to promote one skill, promote exactly one skill; promoting another skill replaces the current one immediately."
            )

        return (
            "[Runtime Skill Policy]\n"
            "Single core-skill slot: only one skill may be promoted at a time. "
            "Promoting a different skill replaces the previous one immediately. "
            f"Active core skill: {self.active_core_skill_name}. "
            f"Guidance: {self.active_core_skill_guidance or 'No extra guidance.'}"
        )


def _skill_tool_schema(name: str, description: str) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=description,
        parameters=ParametersSchema(
            type="object",
            properties={
                "skill_name": {"type": "string", "description": "The skill name to update."},
                "guidance": {"type": "string", "description": "Reason or policy text for the update."},
            },
            required=["skill_name", "guidance"],
            additionalProperties=False,
        ),
        strict=False,
    )


def get_promote_core_skill_tool() -> ToolSchema:
    return _skill_tool_schema(
        "PromoteCoreSkill",
        "Promote exactly one core skill for the current task. If another core skill was active, it is replaced immediately.",
    )


def get_demote_core_skill_tool() -> ToolSchema:
    return _skill_tool_schema(
        "DemoteCoreSkill",
        "Remove the current core-skill slot for the current task. Use this only when you want to clear the active promoted skill.",
    )
