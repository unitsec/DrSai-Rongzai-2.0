import asyncio
import types

from drsai.modules.baseagent import SystemMessage, UserMessage

from src.core.agent.agent import RongZaiAgent


class _ModelContext:
    def __init__(self):
        self._messages = []
        self._current_messages = []

    def replace_messages(self, messages):
        self._messages = list(messages)
        self._current_messages = list(messages)


def test_emergency_context_compression_drops_raw_tool_history(monkeypatch):
    agent = object.__new__(RongZaiAgent)
    agent._model_context = _ModelContext()

    async def _summarize(self, **kwargs):
        assert kwargs["recent_messages"] == []
        assert len(kwargs["old_messages"]) == 2
        return "goal and latest successful refinement state"

    agent._summarize_history_with_subagent = types.MethodType(_summarize, agent)
    agent._get_compatible_context = types.MethodType(
        lambda self, model_client, messages: list(messages),
        agent,
    )
    monkeypatch.setenv("RONGZAI_EMERGENCY_SUMMARY_MAX_CHARS", "4000")

    large_payload = "x" * 1_000_000
    messages = asyncio.run(
        agent._build_emergency_retry_messages_after_context_overflow(
            model_client=object(),
            system_messages=[SystemMessage(content="system")],
            runtime_messages=[
                UserMessage(content="start", source="user"),
                UserMessage(content=large_payload, source="user"),
            ],
            cancellation_token=object(),
        )
    )

    serialized = "\n".join(str(message.content) for message in messages)
    assert large_payload not in serialized
    assert "goal and latest successful refinement state" in serialized
    assert len(messages) == 3
    assert agent._model_context._messages == messages