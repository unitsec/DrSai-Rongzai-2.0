import argparse
import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import httpx

import run_agent_cli as cli


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, text):
        response = httpx.Response(200, text=text)
        return [event async for event in cli.events(response)]

    async def test_sse_comments_multiline_and_done(self):
        result = await self.collect(': heartbeat\n\ndata: {"type":\ndata: "TextMessage"}\n\n'
                                    'data: [DONE]\n\n')
        self.assertEqual(result, [{"type": "TextMessage"}])

    async def test_truncated_invalid_and_non_sse(self):
        for text in ('data: {}\n', 'data: invalid\n\n', '{"error":"failure"}\n',
                     'data: []\n\n'):
            with self.subTest(text=text), self.assertRaises(cli.WorkerError):
                await self.collect(text)

    def test_explicit_completion_and_human_input(self):
        self.assertTrue(cli.terminal({"messages": [], "stop_reason": None}))
        self.assertFalse(cli.terminal({"type": "TextMessage", "content": "done"}))
        self.assertIsNotNone(cli.failure({"type": "UserInputRequestedEvent"}))
        self.assertIsNotNone(cli.failure({"type": "AgentLogEvent", "title": "StreamTimeout"}))
        self.assertIsNotNone(cli.failure({"error": "backend failure"}))


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []
        self.mode = "success"
        self.args = argparse.Namespace(
            output=self.root / "results",
            worker_workspace_root=self.root / "work",
            worker_url="http://127.0.0.1:42812/apiv2",
            model_config="test-model", input_dir=None, task="Smoke task", task_file=None,
            api_key_env="CLI_TEST_UNUSED_KEY", timeout=5, idle_timeout=2, expect_artifact=None,
        )
        self.client_class = httpx.AsyncClient

    async def handler(self, request):
        payload = json.loads(request.content)["kwargs"]
        name = request.url.path.split("/")[-1]
        self.requests.append((name, payload))
        if name == "get_info":
            return httpx.Response(200, json={"name": "test-worker", "agent_config": {"test-model": "test"}})
        if name == "lazy_init":
            return httpx.Response(200, json={"status": self.mode != "init_error", "message": "init"})
        if name == "close":
            return httpx.Response(200, json={"status": self.mode != "close_error"})
        if name == "a_chat_completions":
            if self.mode == "timeout":
                await asyncio.sleep(1)
            if self.mode == "http_error":
                return httpx.Response(500, text="failed")
            events = [{"type": "TextMessage", "content": "done"}]
            if self.mode == "needs_input":
                events.append({"type": "UserInputRequestedEvent"})
            elif self.mode != "truncated":
                events.append({"messages": [], "stop_reason": "finished"})
            if self.mode == "artifact":
                workspace = self.args.worker_workspace_root / payload["user"]["email"] / payload["chat_id"]
                (workspace / "submission" / "smoke.txt").write_text("CLI_SMOKE_OK")
            return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))
        self.fail(f"Unexpected endpoint: {name}")

    async def invoke(self):
        def factory(**kwargs):
            return self.client_class(transport=httpx.MockTransport(self.handler), **kwargs)
        with patch.object(cli.httpx, "AsyncClient", side_effect=factory), \
                patch.object(cli, "dotenv_values", return_value={}), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = await cli.run(self.args)
        summary = json.loads((self.args.output / "run.json").read_text())
        return code, summary

    async def test_success_requires_task_result_and_cleanup(self):
        code, summary = await self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "completed")
        self.assertRegex(summary["session_id"], r"^\d{14}$")
        task = (self.args.output / "task.txt").read_text()
        self.assertIn(f"user_id={summary['user_id']}", task)
        self.assertIn(f"session_id={summary['session_id']}", task)
        self.assertTrue(summary["cleanup_confirmed"])
        self.assertEqual([name for name, _ in self.requests],
                         ["get_info", "lazy_init", "a_chat_completions", "close"])
        self.assertEqual(len((self.args.output / "events.jsonl").read_text().splitlines()), 2)

    async def test_no_terminal_result_is_failure(self):
        self.mode = "truncated"
        code, summary = await self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "error")
        self.assertIn("without TaskResult", summary["error"])

    async def test_needs_input_is_not_completion(self):
        self.mode = "needs_input"
        code, summary = await self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "needs_input")

    async def test_http_and_init_errors_are_not_success(self):
        for mode in ("http_error", "init_error"):
            self.mode = mode
            self.args.output = self.root / mode
            code, summary = await self.invoke()
            self.assertEqual(code, 2)
            self.assertEqual(summary["status"], "error")

    async def test_total_timeout_and_cleanup(self):
        self.mode = "timeout"
        self.args.timeout = 0.05
        code, summary = await self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "timeout")
        self.assertTrue(summary["cleanup_confirmed"])

    async def test_close_failure_is_visible(self):
        self.mode = "close_error"
        code, summary = await self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "cleanup_error")
        self.assertFalse(summary["cleanup_confirmed"])

    async def test_missing_artifact(self):
        self.args.expect_artifact = "final.mcif"
        code, summary = await self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "missing_artifact")

    async def test_collect_exact_artifact(self):
        self.mode = "artifact"
        self.args.expect_artifact = "smoke.txt"
        code, summary = await self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual((self.args.output / "artifacts/smoke.txt").read_text(), "CLI_SMOKE_OK")
        self.assertEqual(summary["artifacts"][0]["path"], "smoke.txt")

    async def test_model_config_is_validated(self):
        self.args.model_config = "wrong"
        code, summary = await self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("Unknown model", summary["error"])
        self.assertEqual(len(self.requests), 1)

    async def test_only_explicit_public_input_is_copied(self):
        public = self.root / "case/public"
        private = self.root / "case/private"
        public.mkdir(parents=True)
        private.mkdir()
        (public / "data.dat").write_text("public")
        (private / "reference.mcif").write_text("secret-answer")
        self.args.input_dir = public
        code, summary = await self.invoke()
        self.assertEqual(code, 0)
        workspace = Path(summary["workspace"])
        self.assertEqual((workspace / "input/data.dat").read_text(), "public")
        self.assertFalse(list(workspace.rglob("*.mcif")))
        self.assertNotIn("secret-answer", (self.args.output / "task.txt").read_text())

    async def test_symlink_input_is_rejected(self):
        public = self.root / "public"
        public.mkdir()
        (public / "answer").symlink_to(self.root / "hidden")
        self.args.input_dir = public
        code, summary = await self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("symlinks", summary["error"])
        self.assertEqual(self.requests, [])

    async def test_nonlocal_worker_is_rejected(self):
        self.args.worker_url = "https://example.com/apiv2"
        code, summary = await self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("localhost", summary["error"])
        self.assertEqual(self.requests, [])

    async def test_existing_result_directory_not_overwritten(self):
        self.args.output.mkdir()
        with self.assertRaises(FileExistsError):
            await cli.run(self.args)


if __name__ == "__main__":
    unittest.main()
