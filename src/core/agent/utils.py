from typing import List, Any, Optional
from collections.abc import Callable, Sequence
import asyncio
import json
import mimetypes
import os
import re
import requests
import shutil
import shlex
import uuid
from pathlib import Path
from urllib.parse import quote
from drsai.modules.managers.messages import (
    FileInfo,
    FilesContent,
    FilesEvent,
)
from .analysis_sandbox import AnalysisSandbox, RUNTIME_PATH

_DANGEROUS_RE = re.compile(
    r"(^|[\s;&|])(rm\s+-rf|rm\s+-r|mkfs|dd\s+if=|shutdown|reboot|poweroff|halt|:\s*\(\)\s*\{|chmod\s+777|chown\s+-R)",
    re.IGNORECASE,
)
_SCRIPT_EXEC_RE = re.compile(
    r"(^|[\s;&|])(python\s+.*\.py|python3\s+.*\.py|bash\s+.*\.sh|sh\s+.*\.sh|./.*\.sh|source\s+.*\.(?:sh|bash))",
    re.IGNORECASE,
)
_RUN_READ_MAX_CHARS = 5_000

def _extract_existing_image_paths_from_content(content: str) -> List[str]:
    """Extract existing local image file paths from tool result text.

    The content can be plain text or nested JSON serialized as text.
    """
    image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
    quoted_path_pattern = re.compile(
        r"[\"']([^\"']+\.(?:png|jpg|jpeg|gif|webp|bmp|tif|tiff))[\"']",
        re.IGNORECASE,
    )
    unquoted_path_pattern = re.compile(
        r"((?:/|\.?\.?/|[A-Za-z0-9_.@-]+/)[^\n\"'<>]+?\.(?:png|jpg|jpeg|gif|webp|bmp|tif|tiff))",
        re.IGNORECASE,
    )
    # Common trailing punctuation when paths appear in natural text.
    trailing_chars = ".,;:!?)]}>'\""

    def _append_if_valid_path(raw_path: str, output: List[str]):
        candidate = raw_path.strip().strip('"\'').rstrip(trailing_chars)
        if not candidate:
            return
        p = Path(candidate)
        if p.exists() and p.is_file() and p.suffix.lower() in image_suffixes:
            output.append(str(p))

    def _collect_strings(value: Any) -> List[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                out.extend(_collect_strings(item))
            return out
        if isinstance(value, dict):
            out = []
            for item in value.values():
                out.extend(_collect_strings(item))
            return out
        return []

    def _extract_from_text(text: str) -> List[str]:
        found: List[str] = []

        for match in quoted_path_pattern.findall(text):
            _append_if_valid_path(match, found)

        for match in unquoted_path_pattern.findall(text):
            _append_if_valid_path(match, found)

        return found

    pending_texts = [content]
    visited = set()
    candidates: List[str] = []

    while pending_texts:
        text = pending_texts.pop()
        if text in visited:
            continue
        visited.add(text)

        candidates.extend(_extract_from_text(text))

        try:
            parsed = json.loads(text)
        except Exception:
            continue

        nested_strings = _collect_strings(parsed)
        pending_texts.extend(nested_strings)

    # Keep order and remove duplicates.
    deduped: List[str] = []
    seen = set()
    for path in candidates:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped

def show_file_to_user(model_client, file_path: str, purpose: str = "user_data") -> str:
    """上传文件到图床接口。

    Args:
        file_path: 文件路径
        purpose: 上传用途，默认为 user_data

    Returns:
        上传后的 URL
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=model_client._client.api_key,
        base_url="https://aiapi.ihep.ac.cn/apiv2",
    )

    file_obj = client.files.create(
        file=open(file_path, "rb"),
        purpose=purpose,
    )
    return f"https://aiapi.ihep.ac.cn/apiv2/files/{file_obj.id}/preview"

def publish_file_to_ui(file_path: str, user_id: str) -> str:
    """Copy a generated file into the UI static tree and return a URL the UI serves.

    The internal UI in this deployment mounts its static tree at ``/files`` and does
    not register a ``/api/generated-files`` route, so a URL under that route returns
    404 and the image never renders. Set ``DRSAI_UI_GENERATED_FILES_ROUTE`` when a
    deployment serves a different route; the default matches the shipped UI.
    """
    source = Path(file_path).expanduser().resolve(strict=True)
    user_files_root = Path(os.environ["DRSAI_UI_USER_FILES_ROOT"]).expanduser().resolve()
    target_dir = (user_files_root / user_id / "generated").resolve()
    if not target_dir.is_relative_to(user_files_root):
        raise ValueError("Generated file target escapes UI user files root")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{uuid.uuid4().hex}_{source.name}"
    shutil.copy2(source, target_dir / target_name)
    ui_prefix = os.getenv("DRSAI_UI_PUBLIC_PREFIX", "").strip().strip("/")
    prefix = f"/{ui_prefix}" if ui_prefix else ""
    route = os.getenv("DRSAI_UI_GENERATED_FILES_ROUTE", "/files/user").strip().rstrip("/")
    if not route.startswith("/"):
        raise ValueError("DRSAI_UI_GENERATED_FILES_ROUTE must be an absolute path")
    return (
        f"{prefix}{route}/{quote(user_id, safe='')}"
        f"/generated/{quote(target_name, safe='')}"
    )



def upload_to_throway(file_path: str) -> str:
    """上传文件到 throway，返回无需鉴权的公开下载 URL。"""
    filename = file_path.split("/")[-1]
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"https://skale.dev/throway/?name={filename}",
            data=f,
            headers={"Content-Type": "application/octet-stream"},
            timeout=30,
        )
    resp.raise_for_status()
    data = resp.json()
    return data["url"]

def get_operator_funcs_rongzai(
    worker_dir: str | Path,
    thread_id: str,
    extra_dirs: Sequence[str | Path] | None = None,
    only_in_workspace: bool = True,
    is_powershell: bool = False,
    allolow_dangrous_cmd: bool = False,
    storage_dir: str | Path | None = None,
    source_name: str = "rongzai",
    read_only_dirs: Sequence[str | Path] | None = None,
) -> list[Callable[..., Any]]:
    WORKDIR = Path(worker_dir).resolve()
    _cwd = [WORKDIR]
    _only_in_workspace = [only_in_workspace]
    _dangerous_allowed = [allolow_dangrous_cmd]
    readonly = {Path(path).resolve() for path in (read_only_dirs or [])}
    background_tasks: dict[str, asyncio.Task[str]] = {}

    def _allowed_dirs() -> list[Path]:
        return [WORKDIR, *(Path(item).resolve() for item in (extra_dirs or []))]

    sandbox = AnalysisSandbox(
        Path(worker_dir), lambda: [Path(item) for item in (extra_dirs or [])], list(readonly),
    )

    def safe_path(p: str, *, write: bool = False) -> Path:
        resolved = Path(p).resolve()
        allowed_dirs = _allowed_dirs()
        if Path(p).is_absolute():
            if _only_in_workspace[0] and not any(resolved.is_relative_to(d) for d in allowed_dirs):
                raise ValueError(f"Path escapes workspace: {p}")
            path = resolved
        else:
            path = (WORKDIR / p).resolve()
        if _only_in_workspace[0] and not any(path.is_relative_to(d) for d in allowed_dirs):
            raise ValueError(f"Path escapes workspace: {p}")
        if write and any(path.is_relative_to(d) for d in readonly):
            raise ValueError(f"Path is read-only: {p}")
        return path

    def get_file_base64(file_path: str):
        return sandbox.read_base64(safe_path(file_path))

    async def run_read(
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        encoding: str = "utf-8",
        timeout: float = 30.0,
    ) -> str:
        """
        Read a text file by line range. Suggest using this function to read files in chunks instead of reading the entire file at once.

        Args:
            path: Local file path.
            start_line: 1-based start line, inclusive.
            end_line: 1-based end line, inclusive. None means read to EOF.
            encoding: File encoding, default utf-8.
            timeout: Maximum time allowed for the read operation.

        Returns:
            File content for the requested line window.
        """
        try:
            file_path = safe_path(path)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            start_line = int(start_line)
            if start_line < 1:
                return "Error: start_line must be >= 1"

            if end_line is not None:
                end_line = int(end_line)
                if end_line < start_line:
                    return "Error: end_line must be >= start_line"

            return await sandbox.file_operation("read", {
                "path": str(file_path), "start_line": start_line, "end_line": end_line,
                "encoding": encoding, "max_chars": _RUN_READ_MAX_CHARS,
            }, timeout)
        except asyncio.TimeoutError:
            return f"Error: Read operation timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"

    async def run_write(path: str, content: str, timeout: float = 30.0) -> str:
        """
        Write content to file.
        """
        try:
            content_size_mb = len(content.encode('utf-8')) / (1024 * 1024)
            fp = safe_path(path, write=True)
            result = await sandbox.file_operation("write", {
                "path": str(fp), "content": content,
            }, timeout)
            if result.startswith("Error:"):
                return result
            return f"Wrote {len(content)} bytes ({content_size_mb:.2f}MB) to {path}"
        except asyncio.TimeoutError:
            return f"Error: Write operation timed out after {timeout}s (file may be partially written)"
        except Exception as e:
            return f"Error: {e}"

    async def run_edit(path: str, old_text: str, new_text: str, timeout: float = 30.0) -> str:
        """
        Replace exact text in file.
        """
        try:
            fp = safe_path(path, write=True)
            result = await sandbox.file_operation("edit", {
                "path": str(fp), "old_text": old_text, "new_text": new_text,
            }, timeout)
            if result.startswith("Error:"):
                return result
            return f"Edited {path}"
        except asyncio.TimeoutError:
            return f"Error: Operation timed out after {timeout}s (file may be partially written)"
        except Exception as e:
            return f"Error: {e}"

    async def run_grep(
        pattern: str,
        path: str = None,
        glob: str = None,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        show_line_numbers: bool = True,
        case_insensitive: bool = False,
        file_type: str = None,
        max_results: int = 250,
    ) -> str:
        """
        Search for pattern in file contents using grep/ripgrep.

        Args:
            pattern: Regular expression pattern to search for
            path: File or directory to search (defaults to workspace root)
            glob: Glob pattern to filter files (e.g. "*.py", "*.{js,ts}")
            output_mode: Output format - "content" (matching lines),
                        "files_with_matches" (file paths), "count" (match counts)
            context_before: Number of lines before each match
            context_after: Number of lines after each match
            show_line_numbers: Show line numbers in output
            case_insensitive: Case insensitive search
            file_type: File type filter (e.g. "py", "js")
            max_results: Maximum results to return

        Returns:
            Search results as string
        """
        try:
            rg_available = shutil.which("rg", path=RUNTIME_PATH)
            grep_available = shutil.which("grep", path=RUNTIME_PATH)

            search_path = str(safe_path(path)) if path else str(WORKDIR)

            if rg_available:
                cmd = [rg_available, "--hidden", "--max-columns", "500"]
                if output_mode == "files_with_matches":
                    cmd.append("-l")
                elif output_mode == "count":
                    cmd.append("-c")
                if case_insensitive:
                    cmd.append("-i")
                if show_line_numbers and output_mode == "content":
                    cmd.append("-n")
                if context_before > 0 and output_mode == "content":
                    cmd.extend(["-B", str(context_before)])
                if context_after > 0 and output_mode == "content":
                    cmd.extend(["-A", str(context_after)])
                if file_type:
                    cmd.extend(["--type", file_type])
                if glob:
                    for pattern_item in glob.split(","):
                        cmd.extend(["--glob", pattern_item.strip()])
                cmd.extend(["--", pattern, search_path])
            elif grep_available:
                cmd = [grep_available, "-r"]
                if case_insensitive:
                    cmd.append("-i")
                if show_line_numbers:
                    cmd.append("-n")
                if output_mode == "files_with_matches":
                    cmd.append("-l")
                elif output_mode == "count":
                    cmd.append("-c")
                if context_before > 0:
                    cmd.extend(["-B", str(context_before)])
                if context_after > 0:
                    cmd.extend(["-A", str(context_after)])
                if glob:
                    cmd.extend(["--include", glob])
                cmd.extend(["--", pattern, search_path])
            else:
                return "Error: Search requires ripgrep or grep in the analysis runtime"

            output = await sandbox.file_operation("grep", {
                "path": search_path, "command": cmd,
            }, 30)
            if output.startswith("Error:"):
                return output

            if not output:
                return "No matches found"

            lines = output.strip().split("\n")
            if len(lines) > max_results:
                limited = "\n".join(lines[:max_results])
                return f"{limited}\n\n[Showing first {max_results} of {len(lines)} results]"
            return output.strip()[:5000]
        except asyncio.TimeoutError:
            return "Error: Search timeout"
        except Exception as e:
            return f"Error: {e}"

    async def run_bash(cmd: str, timeout: float = 60) -> str:
        """
        Execute a bash command asynchronously and wait for completion.

        **IMPORTANT: This is the default and preferred function for most shell commands.**
        timeout: Timeout in seconds (max: 120).

        Example workflow:
            1. Try: run_bash("npm test")  # Try synchronous first
            2. If timeout → Use: run_bash_background("npm test", timeout=300)
        """
        if not _dangerous_allowed[0] and _DANGEROUS_RE.search(cmd):
            return "Error: Dangerous command detected. Use /dangerous on to authorize."
        if not _dangerous_allowed[0] and _SCRIPT_EXEC_RE.search(cmd):
            return "Error: Script execution command detected. Use /dangerous on to authorize."
        wrapped = f'{cmd}\n__rongzai_status=$?\nprintf "\\n__DRSAI_CWD__:%s\\n" "$PWD"\nexit "$__rongzai_status"'

        try:
            raw_output = await sandbox.run(
                ["/bin/bash", "--noprofile", "--norc", "-c", wrapped], _cwd[0], timeout,
            )

            out_lines = []
            for line in raw_output.splitlines():
                if line.startswith("__DRSAI_CWD__:"):
                    new_dir_str = line[len("__DRSAI_CWD__:"):].strip()
                    try:
                        new_dir = Path(new_dir_str).resolve()
                        allowed_dirs = _allowed_dirs()
                        if not _only_in_workspace[0] or any(new_dir.is_relative_to(d) for d in allowed_dirs):
                            _cwd[0] = new_dir
                        else:
                            out_lines.append(f"Warning: cd target '{new_dir}' is outside workspace; cwd not updated")
                    except Exception:
                        pass
                else:
                    out_lines.append(line)

            output = "\n".join(out_lines).strip() or "(no output)"
            return output[:50000]
        except asyncio.TimeoutError:
            return f"Error: Command timed out after {timeout}s (all child processes terminated)"
        except Exception as e:
            return f"Error: {e}"

    async def run_bash_background(cmd: str, timeout: float = 500.0, wait_time: float = 10.0) -> str:
        """
        Execute shell command with smart background mode for LONG-RUNNING tasks.

        **⚠️ WARNING: Use this function ONLY when:**
        1. run_bash() returned a timeout error, OR
        2. You know the command will take > 2 minutes (e.g., long builds, extensive tests)
        **For most commands, use run_bash() first!**
        Important Notes:
            - Logs persist in the workspace; task status is available in this agent instance
            - Don't use sleep commands after launching background tasks
            - Use get_bash_task(task_id) to check status and retrieve output
        """
        if not _dangerous_allowed[0] and _DANGEROUS_RE.search(cmd):
            return "Error: Dangerous command detected. Use /dangerous on to authorize."
        if not _dangerous_allowed[0] and _SCRIPT_EXEC_RE.search(cmd):
            return "Error: Script execution command detected. Use /dangerous on to authorize."
        try:
            task_id = f"bash_task_{uuid.uuid4().hex}"
            log = WORKDIR / f"{task_id}.log"
            wrapped = f"(\n{cmd}\n) > {shlex.quote(str(log))} 2>&1"
            proc = await sandbox.start(
                ["/bin/bash", "--noprofile", "--norc", "-c", wrapped], _cwd[0],
            )

            async def monitor() -> str:
                try:
                    return await sandbox.finish(proc, timeout)
                except TimeoutError:
                    return f"Error: Command timed out after {timeout}s (all child processes terminated)"

            task = asyncio.create_task(monitor())
            background_tasks[task_id] = task
            await asyncio.wait({task}, timeout=max(0.01, min(wait_time, 1.0)))
            if task.done():
                result = task.result()
                if result.startswith("Error:"):
                    return result
            return json.dumps({
                "task_id": task_id, "pid": proc.pid, "cwd": str(_cwd[0]),
                "log_path": str(log),
            }, ensure_ascii=False)
        except Exception as e:
            return f"Error: failed to launch sandboxed background task: {e}"

    async def get_bash_task(task_id: str) -> str:
        """Check a background analysis task and read its workspace log."""
        task = background_tasks.get(task_id)
        if task is None:
            return f"Error: Unknown background task: {task_id}"
        status = "running"
        result = ""
        if task.done():
            if task.cancelled():
                return f"Error: Background task was cancelled: {task_id}"
            result = task.result()
            status = "failed" if result.startswith("Error:") else "completed"
        log = await run_read(f"{task_id}.log")
        return json.dumps({"task_id": task_id, "status": status, "result": result, "log": log})

    def serve_files_for_user(files_list: list[str], descriptions_list: list[str]):
        """
        Serve the files to the user for downloading by showing it to the frontend.
        if you need to serve downloadable artifacts to user, you can call this function with the local paths of the files and their corresponding descriptions.
        The files will be shown to user in the frontend and user can download them.

        Important:
            - Do NOT use this tool to send image files during intermediate steps.
            - Image files produced by tool calls are already auto-displayed to the user.
            - Prefer this tool for downloadable deliverables requested by the user.

        args:
            files_list (list[str]): The local paths of the files to serve.
            descriptions_list (list[str]): The corresponding descriptions of the files to serve.
        """
        try:
            base64_files = []
            for file_path, description in zip(files_list, descriptions_list):
                file_base64 = get_file_base64(file_path)
                base64_file = FileInfo(
                    name = os.path.basename(file_path),
                    base64_content = file_base64,
                    description=description,
                    mime_type=mimetypes.guess_type(file_path)[0] or "application/octet-stream",
                    download_method="base64"
                    )
                base64_files.append(base64_file)
                
            files=FilesContent(
                files=base64_files,
                title="files for download",
                description="these files are served for user downloading.",
                )

            event = FilesEvent(
                source=source_name,
                content=files.model_dump(),
                )

            return json.dumps({"type": "files_event", "event": event.model_dump()}, ensure_ascii=False)
        except Exception as e:
            return f"Error in serve_files_for_user: {e}"
    
    return [run_bash, run_bash_background, run_read, run_write, run_edit, run_grep, get_bash_task, serve_files_for_user]


if __name__ == "__main__":
    funcs = get_operator_funcs_rongzai(Path.cwd(), thread_id="test_source", source_name="test_source")
    check_user_permission_for_file = funcs[-1]
    checkinfo = check_user_permission_for_file(["/tmp/demo.txt"], ["demo"])
    print(f"checkinfo: {checkinfo}")
