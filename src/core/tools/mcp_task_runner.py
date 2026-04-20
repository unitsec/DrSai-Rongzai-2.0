import inspect
import multiprocessing
import time
import traceback
import uuid
from typing import Any, Dict, Optional


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
        timeout_sec: float = 1200.0,
        poll_interval: float = 1.0,
        target_func: Any = None,
        param_mapping: Dict[str, str] = None,
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
        self.id_to_task: Dict[str, Optional[multiprocessing.Process]] = {}
        self.id_to_result: Dict[str, multiprocessing.Queue] = {}

    def _create_wrapper(self):
        """Create a wrapper that handles queue communication for the developer's function.
        
        Automatically injects:
        - task_id: Business parameter for file naming/tracking (passed to target_func)
        - que: Framework parameter for result communication (hidden from target_func)
        """
        def wrapper(**kwargs):
            que = kwargs.pop('que')
            # task_id remains in kwargs - it's a business parameter
            try:
                result = self.target_func(**kwargs)
                que.put(result)
            except Exception as e:
                que.put({
                    'status': 'failed',
                    'content': traceback.format_exc()
                })
        return wrapper

    def build_process_kwargs(self, **tool_kwargs: Any) -> Dict[str, Any]:
        return {
            proc_key: tool_kwargs.get(tool_key)
            for proc_key, tool_key in self.param_mapping.items()
        }

    def _task_snapshot(self, task_id: str) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {"task_id": task_id, "state": None, "result": None}

        proc = self.id_to_task.get(task_id)
        resq = self.id_to_result.get(task_id)

        if proc is None:
            snapshot["state"] = "completed"
            snapshot["result"] = {
                "status": "failed",
                "content": f"task_id={task_id} does not exist or has been cleaned up.",
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

        self.id_to_task[task_id] = None
        snapshot["state"] = "completed"
        snapshot["result"] = info or {
            "status": "failed",
            "content": "Task process exited but no result payload was returned.",
        }
        return snapshot

    def _start_task(self, process_kwargs: Dict[str, Any], task_id: Optional[str] = None) -> str:
        task_id = task_id or str(uuid.uuid4())
        self.id_to_result[task_id] = multiprocessing.Queue()
        full_kwargs = dict(process_kwargs)
        full_kwargs["task_id"] = task_id
        full_kwargs["que"] = self.id_to_result[task_id]

        proc = multiprocessing.Process(target=self._create_wrapper(), kwargs=full_kwargs)
        self.id_to_task[task_id] = proc
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
        if task_id and task_id not in self.id_to_task:
            process_kwargs = self.build_process_kwargs(**tool_kwargs)
            task_id = self._start_task(process_kwargs=process_kwargs, task_id=task_id)
        elif not task_id:
            process_kwargs = self.build_process_kwargs(**tool_kwargs)
            task_id = process_kwargs.get("task_id")
            task_id = self._start_task(process_kwargs=process_kwargs, task_id=task_id)

        deadline = time.time() + self.timeout_sec
        while True:
            snapshot = self._task_snapshot(task_id)
            if snapshot["state"] == "completed":
                return snapshot

            if time.time() >= deadline:
                snapshot["result"] = {
                    "status": "pending",
                    "content": (
                        f"Task is still running after timeout ({self.timeout_sec:.1f}s). "
                        "Call again with the same task_id to continue waiting."
                    ),
                }
                return snapshot

            time.sleep(self.poll_interval)
