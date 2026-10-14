"""Headless client for the existing RongZai worker's native event stream."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse
import uuid

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent


class WorkerError(RuntimeError):
    """Worker did not confirm a successful operation."""


async def events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line:
            if data:
                text = "\n".join(data)
                data.clear()
                if text == "[DONE]":
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise WorkerError("Invalid JSON in worker event stream") from exc
                if not isinstance(event, dict):
                    raise WorkerError("Worker event is not an object")
                yield event
        elif line.startswith((":","event:","id:","retry:")):
            continue
        else:
            raise WorkerError(f"Unexpected non-SSE response line: {line[:200]}")
    if data:
        raise WorkerError("Truncated SSE event at end of stream")


def terminal(event: dict[str, Any]) -> bool:
    return isinstance(event.get("messages"), list) and "stop_reason" in event


def failure(event: dict[str, Any]) -> str | None:
    if event.get("type") == "UserInputRequestedEvent":
        return "Worker requires human input"
    if event.get("type") == "AgentLogEvent" and (
        event.get("title") == "StreamTimeout" or str(event.get("send_level", "")).lower() == "error"
    ):
        return str(event.get("content") or event.get("title"))
    if event.get("error"):
        return str(event["error"])
    return None


async def call(client: httpx.AsyncClient, base: str, name: str, kwargs: dict[str, Any]) -> Any:
    response = await client.post(f"{base}/worker/unified_gate/{name}",
                                 json={"args": [], "kwargs": kwargs})
    response.raise_for_status()
    value = response.json()
    if isinstance(value, dict) and value.get("status") is False:
        raise WorkerError(f"{name}: {value.get('message', value)}")
    return value


def copy_input(source: Path, destination: Path) -> list[dict[str, str]]:
    if source.is_symlink():
        raise ValueError("Input directory must not be a symlink")
    if not source.is_dir():
        raise ValueError(f"Input directory does not exist: {source}")
    files = sorted(source.rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ValueError("Input directory must not contain symlinks")
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for path in files:
        if path.is_file():
            relative = path.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            records.append({"path": str(relative), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    return records


async def execute(args: argparse.Namespace, output: Path, summary: dict[str, Any]) -> int:
    config = dotenv_values(ROOT / ".env")
    workspace_root = args.worker_workspace_root or os.getenv("WORKDIR") or config.get("WORKDIR")
    if not workspace_root:
        raise ValueError("Specify --worker-workspace-root (the worker's WORKDIR)")
    if args.expect_artifact:
        expected = Path(args.expect_artifact)
        if expected.is_absolute() or ".." in expected.parts:
            raise ValueError("--expect-artifact must be relative to submission/")
    base = args.worker_url.rstrip("/")
    if urlparse(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("This shared-filesystem client supports localhost workers only")
    # RongZai normalizes non-timestamp thread IDs; send its native format so
    # the client directory and the agent/MCP session directory remain identical.
    session_id = (datetime(2000, 1, 1) + timedelta(
        seconds=uuid.uuid4().int % 3_155_760_000)).strftime("%Y%m%d%H%M%S")
    user = "cli-" + uuid.uuid4().hex
    workspace = Path(str(workspace_root)).resolve() / user / session_id
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / "submission").mkdir()
    summary.update({"session_id": session_id, "user_id": user, "workspace": str(workspace),
                    "worker_url": base, "model_config": args.model_config})
    if args.input_dir:
        summary["input_files"] = copy_input(args.input_dir.absolute(), workspace / "input")
    task = args.task_file.read_text(encoding="utf-8") if args.task_file else args.task
    if not task or not task.strip():
        raise ValueError("Task must not be empty")
    task += (
        f"\n\n本次独立测试工作目录：{workspace}\n"
        f"本次工具调用身份参数必须逐字复制：user_id={user}，session_id={session_id}。\n"
        "若有输入文件，它们位于此目录的 input/。只分析提供的输入，不寻找其他任务或隐藏答案。"
        "将最终产物写入此目录的 submission/，不要使用上一会话的结果。"
    )
    (output / "task.txt").write_text(task, encoding="utf-8")
    api_key = os.getenv(args.api_key_env) or config.get(args.api_key_env) or ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    initialized = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(args.idle_timeout, connect=10), headers=headers,
                                trust_env=False) as client:
        try:
            info = await call(client, base, "get_info", {})
            if not isinstance(info, dict):
                raise WorkerError("get_info returned an invalid response")
            configs = info.get("agent_config", {})
            if args.model_config not in configs:
                raise ValueError(f"Unknown model configuration {args.model_config!r}; available: {list(configs)}")
            summary["worker_name"] = info.get("name")
            initialized = True
            init = await call(client, base, "lazy_init", {
                "chat_id": session_id, "api_key": api_key,
                "run_info": {"email": user}, "defult_config_name": args.model_config,
            })
            if not isinstance(init, dict) or init.get("status") is not True:
                raise WorkerError("lazy_init did not confirm initialization")
            kwargs = {
                "chat_id": session_id, "user": {"email": user},
                "api_key": api_key, "stream": True, "defult_config_name": args.model_config,
                "messages": [{"type": "TextMessage", "source": "user", "content": task}],
            }
            completed = False
            async with client.stream("POST", f"{base}/worker/unified_gate/a_chat_completions",
                                     json={"args": [], "kwargs": kwargs}) as response:
                response.raise_for_status()
                with (output / "events.jsonl").open("w", encoding="utf-8") as log, \
                        (output / "transcript.txt").open("w", encoding="utf-8") as transcript:
                    async for event in events(response):
                        log.write(json.dumps({"received_at": datetime.now(timezone.utc).isoformat(),
                                              "event": event}, ensure_ascii=False) + "\n")
                        log.flush()
                        kind = event.get("type", "TaskResult" if terminal(event) else "unknown")
                        content = event.get("content")
                        if kind == "ModelClientStreamingChunkEvent" and isinstance(content, str):
                            print(content, end="", flush=True)
                            transcript.write(content)
                        elif isinstance(content, str):
                            text = f"\n[{kind}] {content}\n"
                            print(text, end="", flush=True)
                            transcript.write(text)
                        transcript.flush()
                        reason = failure(event)
                        if reason:
                            summary["status"] = ("needs_input" if kind == "UserInputRequestedEvent" else "worker_error")
                            raise WorkerError(reason)
                        if terminal(event):
                            summary["stop_reason"] = event["stop_reason"]
                            summary["task_result"] = event
                            completed = True
            if not completed:
                raise WorkerError("Stream ended without TaskResult; completion is unconfirmed")
            summary["status"] = "completed"
            files = copy_input(workspace / "submission", output / "artifacts")
            summary["artifacts"] = files
            if args.expect_artifact:
                expected = Path(args.expect_artifact)
                path = output / "artifacts" / expected
                if not path.is_file() or path.stat().st_size == 0:
                    summary["status"] = "missing_artifact"
                    summary["error"] = f"Missing or empty expected artifact: {expected}"
                    return 1
            return 0
        finally:
            if initialized:
                try:
                    # Fresh timeout scope allows cancellation cleanup to run.
                    async with asyncio.timeout(20):
                        closed = await call(client, base, "close", {"chat_id": session_id})
                        if not isinstance(closed, dict) or closed.get("status") is not True:
                            raise WorkerError("close did not confirm cleanup")
                    summary["cleanup_confirmed"] = True
                except (httpx.HTTPError, WorkerError, TimeoutError) as exc:
                    summary["cleanup_confirmed"] = False
                    summary["cleanup_error"] = str(exc)
                    print(f"\nCLEANUP ERROR: {exc}", file=sys.stderr)


async def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary: dict[str, Any] = {"status": "starting"}
    result = 2
    try:
        async with asyncio.timeout(args.timeout):
            result = await execute(args, output, summary)
        if summary.get("cleanup_confirmed") is False and result == 0:
            summary["status"] = "cleanup_error"
            result = 2
    except TimeoutError:
        summary.update(status="timeout", error="Total execution timeout; remote stop requires cleanup confirmation")
        print(summary["error"], file=sys.stderr)
    except asyncio.CancelledError:
        summary.update(status="cancelled", error="Client interrupted")
        raise
    except (httpx.HTTPError, WorkerError, ValueError, OSError) as exc:
        if summary["status"] not in ("needs_input", "worker_error"):
            summary["status"] = "error"
        summary["error"] = str(exc)
        print(f"\nRUN ERROR: {exc}", file=sys.stderr)
    finally:
        summary["duration_seconds"] = time.monotonic() - started
        summary["exit_code"] = result
        (output / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nRun report: {output / 'run.json'}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-url", default="http://127.0.0.1:42812/apiv2")
    parser.add_argument("--model-config", required=True)
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--task")
    task.add_argument("--task-file", type=Path)
    parser.add_argument("--input-dir", type=Path, help="Copy ONLY this public input directory")
    parser.add_argument("--output", type=Path, required=True, help="New client-side result directory")
    parser.add_argument("--worker-workspace-root", type=Path)
    parser.add_argument("--expect-artifact", help="Required nonempty file relative to submission/")
    parser.add_argument("--api-key-env", default="HEPAI_API_KEY")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--idle-timeout", type=float, default=600)
    args = parser.parse_args()
    if args.timeout <= 0 or args.idle_timeout <= 0:
        parser.error("Timeouts must be positive")
    try:
        return asyncio.run(run(args))
    except (OSError, KeyboardInterrupt) as exc:
        print(f"CLIENT ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
