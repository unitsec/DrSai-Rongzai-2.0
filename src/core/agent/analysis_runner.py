"""Isolated execution for untrusted analysis commands and file operations."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from src.configs.constants import get_datasets_dir

RUNTIME_PATH = f"{sys.prefix}/bin:/usr/local/bin:/usr/bin:/bin"


class AnalysisSandbox:
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
        binary = shutil.which("bwrap", path="/usr/local/bin:/usr/bin:/bin")
        if sys.platform != "linux" or not binary:
            raise RuntimeError(
                "Analysis sandbox unavailable: Linux and bubblewrap are required; "
                "host execution is disabled."
            )
        environment = {
            "PATH": RUNTIME_PATH,
            "HOME": "/home/analyst",
            "USER": "analyst",
            "LOGNAME": "analyst",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": "/tmp",
            "PYTHONNOUSERSITE": "1",
            "MPLBACKEND": "Agg",
        }
        command = [
            binary, "--unshare-all", "--unshare-user", "--die-with-parent",
            "--new-session", "--cap-drop", "ALL", "--clearenv",
            "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/proc",
            "--tmpfs", "/home/analyst",
        ]
        readonly = {Path("/usr"), Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
        aliases = {
            self.workspace_path: self.workspace,
            Path(sys.prefix).absolute(): Path(sys.prefix).resolve(),
            Path(sys.base_prefix).absolute(): Path(sys.base_prefix).resolve(),
        }
        configured = json.loads(os.getenv("RONGZAI_SANDBOX_READONLY_PATHS", "[]") or "[]")
        if not isinstance(configured, list) or not all(isinstance(p, str) for p in configured):
            raise ValueError("RONGZAI_SANDBOX_READONLY_PATHS must be a JSON array of absolute paths")
        for item in configured:
            path = Path(item)
            if not path.is_absolute():
                raise ValueError("Sandbox runtime paths must be absolute")
            readonly.add(path.resolve(strict=True))
            aliases[path.absolute()] = path.resolve()
        for name in ("/bin", "/sbin", "/lib", "/lib64"):
            path = Path(name)
            if path.is_symlink():
                command.extend(["--symlink", os.readlink(path), name])
            elif path.exists():
                readonly.add(path)
        for name in ("/etc/ld.so.cache", "/etc/fonts", "/etc/alternatives"):
            if Path(name).exists():
                readonly.add(Path(name))

        writable = {self.workspace}
        for path in self.extra_dirs():
            original = path.absolute()
            path = path.resolve()
            if not path.exists():
                continue
            (readonly if path in self.readonly_dirs else writable).add(path)
            aliases[original] = path
        # Reject broad grants and overlapping mounts rather than exposing hidden host files.
        protected = (
            Path("/proc"), Path("/sys"), Path("/dev"), Path("/etc"), Path("/home/analyst"),
            Path.home().resolve(), Path(__file__).resolve().parents[3],
        )
        mounts = readonly | writable
        for path in mounts:
            if any(root.is_relative_to(path) for root in protected):
                raise ValueError(f"Unsafe sandbox mount: {path}")
        if readonly & writable:
            raise ValueError(f"Conflicting sandbox mount permissions: {readonly & writable}")
        for path in mounts:
            for other in mounts:
                if path != other and path.is_relative_to(other):
                    if (path in writable) != (other in writable):
                        raise ValueError(f"Conflicting sandbox mount permissions: {path}, {other}")
        for path in sorted(mounts, key=lambda p: (len(p.parts), str(p))):
            if not path.exists():
                raise FileNotFoundError(f"Sandbox mount not found: {path}")
            command.extend(["--bind" if path in writable else "--ro-bind", str(path), str(path)])
        visible_roots = set(mounts)
        for original, resolved in sorted(aliases.items(), key=lambda item: len(item[0].parts)):
            if original == resolved or any(original.is_relative_to(root) for root in visible_roots):
                continue
            command.extend(["--symlink", str(resolved), str(original)])
            visible_roots.add(original)
        command.extend(["--chdir", str(cwd)])
        datasets_dir = get_datasets_dir()
        if datasets_dir in readonly:
            environment["RONGZAI_DATASETS_DIR"] = str(datasets_dir)
        for key, value in environment.items():
            command.extend(["--setenv", key, value])
        # Without /proc/self/fd, Python closes descriptors up to RLIMIT_NOFILE.
        # Some containers inherit a billion-FD limit, stalling every subprocess.
        bootstrap = (
            "import os,resource,sys; "
            "soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE); "
            "limit=min(65536,hard) if hard != resource.RLIM_INFINITY else 65536; "
            "resource.setrlimit(resource.RLIMIT_NOFILE,(min(soft,limit) if soft != resource.RLIM_INFINITY else limit,limit)); "
            "os.execvpe(sys.argv[1],sys.argv[1:],os.environ)"
        )
        command.extend(["--remount-ro", "/", "--", sys.executable, "-I", "-c", bootstrap, *args])
        return command, environment

    async def start(
        self, args: Sequence[str], cwd: Path, *, input_pipe: bool = False,
    ) -> asyncio.subprocess.Process:
        command, environment = self.command(args, cwd)
        return await asyncio.create_subprocess_exec(
            *command, cwd="/", env=environment, start_new_session=True,
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
            return f"Error: sandbox command exited with status {process.returncode}\n{output}"
        return output

    async def run(
        self, args: Sequence[str], cwd: Path, timeout: float, data: bytes | None = None,
    ) -> str:
        process = await self.start(args, cwd, input_pipe=data is not None)
        return await self.finish(process, timeout, data)

    async def file_operation(self, operation: str, payload: dict, timeout: float) -> str:
        script = Path(__file__).with_name("sandbox_files.py").read_text(encoding="utf-8")
        return await self.run(
            [sys.executable, "-I", "-c", script, operation], self.workspace, timeout,
            json.dumps(payload).encode("utf-8"),
        )

    def read_base64(self, path: Path) -> str:
        script = Path(__file__).with_name("sandbox_files.py").read_text(encoding="utf-8")
        command, environment = self.command(
            [sys.executable, "-I", "-c", script, "base64"], self.workspace,
        )
        result = subprocess.run(
            command, input=json.dumps({"path": str(path)}).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd="/",
            timeout=30, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        return result.stdout.decode("ascii")


def check_sandbox_support() -> None:
    """Fail worker startup if isolation cannot be established."""
    with tempfile.TemporaryDirectory(prefix="rongzai-sandbox-check-") as directory:
        sandbox = AnalysisSandbox(Path(directory), lambda: [])
        command, environment = sandbox.command(["/bin/true"], Path(directory))
        subprocess.run(command, env=environment, cwd="/", timeout=15, check=True)
