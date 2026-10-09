import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from drsai_ui.agent_factory.magentic_one.teams.roundrobin_orchestrator import (
    RoundRobinGroupChat,
)
from drsai_ui.agent_factory.remote_agent.worker_agent import (
    ConfirmedPauseWorkerAgent,
    RemotePauseError,
)
from drsai_ui.ui_backend.backend.teammanager.teammanager import TeamManager
from drsai_ui.ui_backend.backend.web.managers.connection import (
    RunStatus,
    WebSocketManager,
)


def make_worker(pause):
    worker = object.__new__(ConfirmedPauseWorkerAgent)
    worker._name = "test-worker"
    worker._chat_id = "test-chat"
    worker._funcs_map = {"pause": pause}
    worker.is_paused = False
    worker._paused = asyncio.Event()
    return worker


class RemotePauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_rejection_preserves_stream_state(self):
        worker = make_worker(lambda **kwargs: {"status": False, "message": "not found"})
        with self.assertRaisesRegex(RemotePauseError, "not found"):
            await worker.pause()
        self.assertFalse(worker.is_paused)
        self.assertFalse(worker._paused.is_set())

    async def test_worker_transport_error_preserves_stream_state(self):
        def fail(**kwargs):
            raise ConnectionError("worker unavailable")

        worker = make_worker(fail)
        with self.assertRaisesRegex(RemotePauseError, "worker unavailable") as raised:
            await worker.pause()
        self.assertIsInstance(raised.exception.__cause__, ConnectionError)
        self.assertFalse(worker.is_paused)
        self.assertFalse(worker._paused.is_set())

    async def test_worker_only_pauses_after_acknowledgement(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def pause(**kwargs):
            calls.append(kwargs)
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test worker was not released")
            return {"status": True}

        worker = make_worker(pause)
        pending = asyncio.create_task(worker.pause())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            self.assertFalse(worker.is_paused)
            self.assertFalse(worker._paused.is_set())
        finally:
            release.set()
            await pending
        self.assertEqual(calls, [{"chat_id": "test-chat"}])
        self.assertTrue(worker.is_paused)
        self.assertTrue(worker._paused.is_set())

    async def test_invalid_worker_response_is_not_success(self):
        for result in (None, {}, {"status": "false"}, {"status": 1}):
            with self.subTest(result=result):
                worker = make_worker(lambda **kwargs: result)
                with self.assertRaises(RemotePauseError):
                    await worker.pause()
                self.assertFalse(worker.is_paused)

    async def test_missing_remote_function_or_chat_id_is_error(self):
        worker = make_worker(lambda **kwargs: {"status": True})
        worker._funcs_map = {}
        with self.assertRaisesRegex(RemotePauseError, "unavailable"):
            await worker.pause()
        worker._funcs_map["pause"] = lambda **kwargs: {"status": True}
        worker._chat_id = None
        with self.assertRaisesRegex(RemotePauseError, "chat_id"):
            await worker.pause()
        self.assertFalse(worker.is_paused)

    async def test_timeout_is_unconfirmed_not_paused(self):
        async def timeout(awaitable, timeout):
            awaitable.close()
            raise asyncio.TimeoutError()

        worker = make_worker(lambda **kwargs: {"status": True})
        with patch(
            "drsai_ui.agent_factory.remote_agent.worker_agent.asyncio.wait_for",
            side_effect=timeout,
        ):
            with self.assertRaisesRegex(RemotePauseError, "unconfirmed"):
                await worker.pause()
        self.assertFalse(worker.is_paused)

    async def test_group_does_not_pause_orchestrator_when_worker_rejects(self):
        worker = make_worker(lambda **kwargs: {"status": False, "message": "rejected"})
        user_proxy = SimpleNamespace(pause=AsyncMock())
        orchestrator = SimpleNamespace(pause=AsyncMock())
        team = object.__new__(RoundRobinGroupChat)
        team._runtime = SimpleNamespace(
            try_get_underlying_agent_instance=AsyncMock(return_value=orchestrator)
        )
        team._group_chat_manager_topic_type = "test-manager"
        team._team_id = "test-team"
        team._participants = [worker, user_proxy]
        with self.assertRaises(RemotePauseError):
            await team.pause()
        orchestrator.pause.assert_not_awaited()
        user_proxy.pause.assert_not_awaited()

    async def test_missing_team_is_not_success(self):
        manager = object.__new__(TeamManager)
        manager.team = None
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            await manager.pause_run()


class PauseRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = WebSocketManager(
            db_manager=SimpleNamespace(),
            internal_workspace_root=Path("."),
            external_workspace_root=Path("."),
            inside_docker=False,
            config={},
        )
        self.manager._connections[1] = object()
        self.manager._run_states[1] = RunStatus.ACTIVE
        self.team = SimpleNamespace(pause_run=AsyncMock(), resume_run=AsyncMock())
        self.manager._team_managers[1] = self.team
        self.messages = []

        async def send(run_id, message):
            self.messages.append(message)

        async def update(run_id, status, **kwargs):
            self.manager._run_states[run_id] = status
            self.messages.append({"type": "system", "status": status})
            return True

        self.manager._send_message = send
        self.manager._update_run_status = update

    async def test_worker_failure_reports_recoverable_error_without_paused_status(self):
        self.team.pause_run.side_effect = RemotePauseError("worker rejected pause")
        await self.manager.pause_run(1)
        self.assertEqual(self.manager._run_states[1], RunStatus.ACTIVE)
        self.assertEqual(len(self.messages), 1)
        message = self.messages[0]
        self.assertEqual(message["type"], "error")
        self.assertEqual(message["operation"], "pause")
        self.assertEqual(message["status"], RunStatus.ACTIVE)
        self.assertIn("worker rejected pause", message["error"])
        self.assertIn(1, self.manager._connections)
        self.team.pause_run.side_effect = None
        await self.manager.pause_run(1)
        self.assertEqual(self.manager._run_states[1], RunStatus.PAUSED)
        self.assertEqual(self.team.pause_run.await_count, 2)

    async def test_status_waits_for_confirmation_and_duplicate_requests_are_serialized(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pause():
            entered.set()
            await release.wait()

        self.team.pause_run.side_effect = pause
        first = asyncio.create_task(self.manager.pause_run(1))
        await entered.wait()
        second = asyncio.create_task(self.manager.pause_run(1))
        await asyncio.sleep(0)
        self.assertEqual(self.manager._run_states[1], RunStatus.ACTIVE)
        self.assertEqual(self.messages, [])
        release.set()
        await asyncio.gather(first, second)
        self.team.pause_run.assert_awaited_once()
        self.assertEqual(self.manager._run_states[1], RunStatus.PAUSED)

    async def test_resume_waits_for_pending_pause(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pause():
            entered.set()
            await release.wait()

        self.team.pause_run.side_effect = pause
        pending_pause = asyncio.create_task(self.manager.pause_run(1))
        await entered.wait()
        pending_resume = asyncio.create_task(self.manager.resume_run(1))
        await asyncio.sleep(0)
        self.team.resume_run.assert_not_awaited()
        release.set()
        await asyncio.gather(pending_pause, pending_resume)
        self.team.resume_run.assert_awaited_once()
        self.assertEqual(self.manager._run_states[1], RunStatus.ACTIVE)

    async def test_completion_during_pause_is_not_overwritten(self):
        async def pause():
            self.manager._run_states[1] = RunStatus.COMPLETE

        self.team.pause_run.side_effect = pause
        await self.manager.pause_run(1)
        self.assertEqual(self.manager._run_states[1], RunStatus.COMPLETE)
        self.assertEqual(self.messages, [])

    async def test_disconnected_run_reports_failure(self):
        self.manager._connections.clear()
        await self.manager.pause_run(1)
        self.team.pause_run.assert_not_awaited()
        self.assertEqual(self.messages[0]["operation"], "pause")
        self.assertEqual(self.messages[0]["status"], RunStatus.ACTIVE)


if __name__ == "__main__":
    unittest.main()
