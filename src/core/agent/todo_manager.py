from __future__ import annotations

from typing import Any, Dict, List

from drsai.modules.components.tool import ParametersSchema, ToolSchema


class StructuredTodoManager:
    """Manage a structured runtime todo list for complex multi-step tasks."""

    VALID_STATUSES = {"pending", "in_progress", "completed"}

    def __init__(self, max_items: int = 20) -> None:
        self._max_items = max(1, int(max_items))
        self.items: List[Dict[str, str]] = []
        self._last_warning: str | None = None

    def update(self, items: List[Dict[str, Any]]) -> str:
        if not isinstance(items, list):
            raise ValueError("TodoWrite.items must be an array.")

        if not items:
            self.items = []
            self._last_warning = None
            return "Todo list cleared."

        validated: List[Dict[str, str]] = []
        in_progress_indices: List[int] = []

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"Item {index}: each todo item must be an object.")

            title = str(item.get("title", "")).strip()
            details = str(item.get("details", "")).strip()
            status = str(item.get("status", "pending")).strip().lower()

            if not title:
                raise ValueError(f"Item {index}: title is required.")
            if not details:
                raise ValueError(f"Item {index}: details is required.")
            if status not in self.VALID_STATUSES:
                raise ValueError(f"Item {index}: invalid status '{status}'.")
            if status == "in_progress":
                in_progress_indices.append(index)

            validated.append(
                {
                    "title": title,
                    "details": details,
                    "status": status,
                }
            )

        self._last_warning = None
        if len(in_progress_indices) > 1:
            keep_index = in_progress_indices[0]
            for idx in in_progress_indices[1:]:
                validated[idx]["status"] = "pending"
            kept_title = validated[keep_index]["title"]
            changed_titles = [validated[idx]["title"] for idx in in_progress_indices[1:]]
            self._last_warning = (
                "Auto-corrected TodoWrite: only one step can be in_progress. "
                f"Kept '{kept_title}' as in_progress; reset these to pending: {changed_titles}."
            )

        self.items = validated[: self._max_items]
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No active todo list."

        status_marks = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
        }
        lines: List[str] = []
        for index, item in enumerate(self.items, start=1):
            lines.append(f"{index}. {status_marks[item['status']]} {item['title']}")
            lines.append(f"   Action: {item['details']}")
        done = sum(1 for item in self.items if item["status"] == "completed")
        lines.append(f"Progress: {done}/{len(self.items)} completed")
        if self._last_warning:
            lines.append(f"Warning: {self._last_warning}")
        return "\n".join(lines)

    def build_system_prompt_block(self) -> str:
        if not self.items:
            return ""
        return (
            "[Active Todo List]\n"
            "Maintain this list with TodoWrite while executing the current complex task. "
            "Exactly one step may be in_progress at a time. Update the full list whenever step status changes.\n\n"
            f"{self.render()}"
        )

    def unload_if_completed(self) -> bool:
        if self.items and all(item["status"] == "completed" for item in self.items):
            self.items = []
            self._last_warning = None
            return True
        return False


def get_todo_write_tool(strict: bool = False) -> ToolSchema:
    parameters = ParametersSchema(
        type="object",
        properties={
            "items": {
                "type": "array",
                "description": "The complete active todo list. Send the full list on every update.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short step title.",
                        },
                        "details": {
                            "type": "string",
                            "description": "Concrete action for this step.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Current step status.",
                        },
                    },
                    "required": ["title", "details", "status"],
                    "additionalProperties": False,
                },
            }
        },
        required=["items"],
        additionalProperties=False,
    )
    return ToolSchema(
        name="TodoWrite",
        description=(
            "Create or update the active todo list for a complex multi-step task. "
            "Use this when the task requires multiple specialized tools, repeated loops, or sub-agent delegation. "
            "Always send the full list, keep exactly one step in_progress, and update statuses as work advances."
        ),
        parameters=parameters,
        strict=strict,
    )