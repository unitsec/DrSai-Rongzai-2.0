import inspect
import multiprocessing
import time
import traceback
import uuid
from typing import Any, Dict, Optional, Tuple


def _run_target_with_queue(target_func: Any, target_kwargs: Dict[str, Any], que: multiprocessing.Queue):
    """Module-level subprocess entrypoint to stay picklable under spawn/forkserver."""
    try:
        result = target_func(**target_kwargs)
        que.put(result)
    except Exception:
        que.put({
            'status': 'failed',
            'content': traceback.format_exc()
        })


class ProcessPollingMcpToolBase:
    """Reusable base for MCP tools that run jobs in subprocesses and poll results.

    Developers implement pure functions that only handle business logic.
    The framework automatically handles queue communication and task tracking.

    Configuration:
    - target_func: The subprocess entry function (receives business params + task_id, returns result dict)
    - param_mapping: Dict mapping subprocess param names to MCP tool param names

    The framework automatically injects:
    - task_id: Business parameter for file naming/tracking (auto-generated if not provided in mapped kwargs)
    - que: Framework parameter for result communication (completely hidden from target_func)

    A single task_id is used for both polling and business semantics.
    """

    def __init__(
        self,
        timeout_sec: float = 60.0,
        poll_interval: float = 1.0,
        target_func: Any = None,
        param_mapping: Dict[str, str] = None,
        task_namespace_param: str = "mission_name",
    ):
        if target_func is None:
            raise ValueError("target_func is required")

        self.timeout_sec = float(timeout_sec)
        self.poll_interval = float(poll_interval)
        self.target_func = target_func
        if param_mapping is None:
            # Auto-derive identity mapping from target_func's signature
            sig = inspect.signature(target_func)
            param_mapping = {p: p for p in sig.parameters}
        self.param_mapping: Dict[str, str] = dict(param_mapping)
        self.task_namespace_param = task_namespace_param
        self.id_to_task: Dict[Tuple[str, str], multiprocessing.Process] = {}
        self.id_to_result: Dict[Tuple[str, str], multiprocessing.Queue] = {}

    def build_process_kwargs(self, **tool_kwargs: Any) -> Dict[str, Any]:
        return {
            proc_key: tool_kwargs.get(tool_key)
            for proc_key, tool_key in self.param_mapping.items()
        }

    def _task_namespace(self, process_kwargs: Dict[str, Any]) -> str:
        raw = process_kwargs.get(self.task_namespace_param)
        return str(raw) if raw not in (None, "") else "__default__"

    def _task_key(self, task_namespace: str, task_id: Any) -> Tuple[str, str]:
        return (task_namespace, str(task_id))

    def _task_snapshot(self, task_key: Tuple[str, str], task_id: Any, task_namespace: str) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "task_id": task_id,
            "mission_name": None if task_namespace == "__default__" else task_namespace,
            "state": None,
            "result": None,
        }

        proc = self.id_to_task.get(task_key)
        resq = self.id_to_result.get(task_key)

        if proc is None:
            snapshot["state"] = "completed"
            snapshot["result"] = {
                "status": "failed",
                "content": (
                    f"task_id={task_id} (mission_name={snapshot['mission_name']}) "
                    "does not exist or has been cleaned up."
                ),
            }
            return snapshot

        if proc.is_alive():
            snapshot["state"] = "running"
            return snapshot

        info = None
        try:
            if resq is not None and not resq.empty():
                info = resq.get_nowait()
        except Exception:
            info = None

        try:
            proc.join(timeout=0.1)
        except Exception:
            pass

        # Cleanup completed task so the same task_id can start a new task later.
        self.id_to_task.pop(task_key, None)
        q = self.id_to_result.pop(task_key, None)
        if q is not None:
            try:
                q.close()
            except Exception:
                pass
        snapshot["state"] = "completed"
        snapshot["result"] = info or {
            "status": "failed",
            "content": "Task process exited but no result payload was returned.",
        }
        return snapshot

    def _start_task(
        self,
        process_kwargs: Dict[str, Any],
        task_namespace: str,
        task_id: Optional[str] = None,
    ) -> Any:
        if task_id is None:
            task_id = str(uuid.uuid4())
        task_key = self._task_key(task_namespace, task_id)
        self.id_to_result[task_key] = multiprocessing.Queue()
        full_kwargs = dict(process_kwargs)
        full_kwargs["task_id"] = task_id

        proc = multiprocessing.Process(
            target=_run_target_with_queue,
            kwargs={
                "target_func": self.target_func,
                "target_kwargs": full_kwargs,
                "que": self.id_to_result[task_key],
            },
        )
        self.id_to_task[task_key] = proc
        proc.start()
        return task_id

    def register_mcp_tool(self, mcp_server, name: str = None, doc: str = None):
        """Register target_func as an MCP tool reusing its own signature.

        No need to re-declare parameters — the signature, type annotations,
        and docstring are copied directly from target_func.

        Args:
            mcp_server: A FastMCP (or compatible) server instance.
            name: Tool name exposed to MCP clients. Defaults to target_func.__name__.
            doc: Override docstring. Defaults to target_func.__doc__.
        """
        sig = inspect.signature(self.target_func)
        annotations = getattr(self.target_func, '__annotations__', {}).copy()
        tool_name = name or self.target_func.__name__
        tool_doc = doc if doc is not None else (self.target_func.__doc__ or "")

        runner = self

        def tool_func(**kwargs):
            return runner.run(**kwargs)

        tool_func.__signature__ = sig
        tool_func.__annotations__ = annotations
        tool_func.__name__ = tool_name
        tool_func.__qualname__ = tool_name
        tool_func.__doc__ = tool_doc

        mcp_server.tool()(tool_func)
        return tool_func

    def run(self, task_id: Optional[str] = None, **tool_kwargs: Any) -> Dict[str, Any]:
        # Keep a single task_id for both polling and business file naming.
        # Task records are isolated by task namespace (mission_name by default).
        process_kwargs = self.build_process_kwargs(**tool_kwargs)
        task_namespace = self._task_namespace(process_kwargs)

        if task_id is None:
            task_id = process_kwargs.get("task_id")

        if task_id is None:
            task_id = self._start_task(
                process_kwargs=process_kwargs,
                task_namespace=task_namespace,
                task_id=task_id,
            )

        task_key = self._task_key(task_namespace, task_id)
        if task_key not in self.id_to_task:
            task_id = self._start_task(
                process_kwargs=process_kwargs,
                task_namespace=task_namespace,
                task_id=task_id,
            )
            task_key = self._task_key(task_namespace, task_id)

        deadline = time.time() + self.timeout_sec
        while True:
            snapshot = self._task_snapshot(task_key=task_key, task_id=task_id, task_namespace=task_namespace)
            if snapshot["state"] == "completed":
                return snapshot

            if time.time() >= deadline:
                snapshot["result"] = {
                    "status": "pending",
                    "content": (
                        f"Task is still running after timeout ({self.timeout_sec:.1f}s). "
                        "If you want to continue waiting, you should call again with the same task_id and mission_name."
                    ),
                }
                return snapshot

            time.sleep(self.poll_interval)
