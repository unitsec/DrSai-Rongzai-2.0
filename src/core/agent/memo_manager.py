from __future__ import annotations

from typing import Any, Dict, List

from drsai.modules.components.tool import ParametersSchema, ToolSchema


class StructuredMemoManager:
    """Manage a short runtime memo for critical facts, paths, and constraints."""

    def __init__(self) -> None:
        self.items: List[Dict[str, str]] = []

    def update(self, items: List[Dict[str, Any]], clear_all: bool = False) -> str:
        if not isinstance(items, list):
            raise ValueError("MemoWrite.items must be an array.")

        if clear_all:
            self.items = []
            return "No active memo."

        if not items:
            return self.render()

        value_by_key: Dict[str, str] = {item["key"]: item["value"] for item in self.items}
        key_order: List[str] = [item["key"] for item in self.items]

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"Item {index}: each memo item must be an object.")

            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
            remove = bool(item.get("remove", False))
            if not key:
                raise ValueError(f"Item {index}: key is required.")

            if remove or not value:
                value_by_key.pop(key, None)
                if key in key_order:
                    key_order.remove(key)
                continue

            value_by_key[key] = value
            if key in key_order:
                key_order.remove(key)
            key_order.append(key)

        self.items = [
            {"key": key, "value": value_by_key[key]}
            for key in key_order
            if key in value_by_key
        ]
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No active memo."

        lines: List[str] = []
        for index, item in enumerate(self.items, start=1):
            lines.append(f"{index}. {item['key']}: {item['value']}")
        return "\n".join(lines)

    def build_system_prompt_block(self) -> str:
        if not self.items:
            return ""
        return (
            "[Active Memo]\n"
            "Use MemoWrite to keep a short runtime memo of the most important facts, paths, decisions, or constraints. "
            "Keep it brief and useful. Remove outdated entries immediately by setting remove=true for that item.\n\n"
            f"{self.render()}"
        )


def get_memo_write_tool(strict: bool = False) -> ToolSchema:
    parameters = ParametersSchema(
        type="object",
        properties={
            "items": {
                "type": "array",
                "description": "Incremental memo updates. You can send only changed entries; existing entries are preserved unless removed.",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Short memo key, for example 'diffraction_path' or 'pdf_path'.",
                        },
                        "value": {
                            "type": "string",
                            "description": "Short memo value. Keep it concise and useful. Leave empty to remove this entry.",
                        },
                        "remove": {
                            "type": "boolean",
                            "description": "Set to true to remove this memo entry explicitly, even if it still has content. Useful for deleting stale or useless information.",
                        },
                    },
                    "required": ["key", "value"],
                    "additionalProperties": False,
                },
            }
            ,
            "clear_all": {
                "type": "boolean",
                "description": "Set true to clear the entire memo explicitly. Use only when you really want to reset all memo entries.",
            }
        },
        required=["items"],
        additionalProperties=False,
    )
    return ToolSchema(
        name="MemoWrite",
        description=(
            "Create or update a short runtime memo for important facts, file paths, decisions, or constraints that may be needed later. "
            "Keep the memo brief and useful. MemoWrite uses incremental updates by default: send only changed entries and old entries will be kept. "
            "Remove stale entries with remove=true (or empty value), and clear all entries only with clear_all=true."
        ),
        parameters=parameters,
        strict=strict,
    )
