import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.agent.agent import RongZaiAgent


def test_stream_retry_replays_after_timeout(monkeypatch):
    agent = object.__new__(RongZaiAgent)

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr("src.core.agent.agent.asyncio.sleep", fake_sleep)

    call_count = 0

    async def stream_factory():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError("stream timed out")
        yield {"ok": True}

    async def run_test():
        collected = []
        async for item in agent._run_stream_with_retry(stream_factory, max_attempts=2):
            collected.append(item)
        return collected

    collected = asyncio.run(run_test())

    assert collected == [{"ok": True}]
    assert call_count == 2
