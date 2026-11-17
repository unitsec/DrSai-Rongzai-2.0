"""Direct subprocess execution for analysis commands and file operations."""

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence

RUNTIME_PATH = f"{sys.prefix}/bin:/usr/local/bin:/usr/bin:/bin"


class CommandRunner:
    """Runs analysis commands and file operations as direct host subprocesses."""

    def __init__(
        self,
        workspace: Path,
        extra_dirs: Callable[[], list[Path]],
        readonly_dirs: Sequence[Path] = (),
    ) -> None:
        self.workspace_path = workspace.absolute()
        self.workspace = workspace.resolve()
        self.extra_dirs = extra_dirs
        self.readonly_dirs = {path.resolve() for path in readonly_dirs}

    def command(self, args: Sequence[str], cwd: Path) -> tuple[list[str], dict[str, str]]:
        environment = os.environ.copy()
        environment["MPLBACKEND"] = "Agg"
        environment["PYTHONNOUSERSITE"] = "1"
        path = environment.get("PATH", "")
        environment["PATH"] = RUNTIME_PATH + (":" + path if path else "")
        return list(args), environment

    async def start(
        self, args: Sequence[str], cwd: Path, *, input_pipe: bool = False,
    ) -> asyncio.subprocess.Process:
        command, environment = self.command(args, cwd)
        return await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd), env=environment, start_new_session=True,
            stdin=asyncio.subprocess.PIPE if input_pipe else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

    async def finish(
        self, process: asyncio.subprocess.Process, timeout: float, data: bytes | None = None,
    ) -> str:
        try:
            async with asyncio.timeout(timeout):
                stdout, stderr = await process.communicate(data)
        except (TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            raise
        output = (stdout + stderr).decode("utf-8", errors="replace")
        if process.returncode:
            return f"Error: command exited with status {process.returncode}\n{output}"
        return output

    async def run(
        self, args: Sequence[str], cwd: Path, timeout: float, data: bytes | None = None,
    ) -> str:
        process = await self.start(args, cwd, input_pipe=data is not None)
        return await self.finish(process, timeout, data)

    async def file_operation(self, operation: str, payload: dict, timeout: float) -> str:
        script = Path(__file__).with_name("file_ops.py").read_text(encoding="utf-8")
        return await self.run(
            [sys.executable, "-I", "-c", script, operation], self.workspace, timeout,
            json.dumps(payload).encode("utf-8"),
        )

    def read_base64(self, path: Path) -> str:
        script = Path(__file__).with_name("file_ops.py").read_text(encoding="utf-8")
        command, environment = self.command(
            [sys.executable, "-I", "-c", script, "base64"], self.workspace,
        )
        result = subprocess.run(
            command, input=json.dumps({"path": str(path)}).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            cwd=str(self.workspace), timeout=30, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        return result.stdout.decode("ascii")
