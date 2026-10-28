import asyncio
import base64
import json
import shlex
import sys
from types import SimpleNamespace

import pytest

from src.core.agent.analysis_sandbox import AnalysisSandbox, check_sandbox_support
from src.core.agent.agent import RongZaiAgent
from src.core.agent.utils import get_operator_funcs_rongzai
from src.configs.constants import get_datasets_dir


def operators(workspace, extra_dirs=None, readonly=None):
    return {
        function.__name__: function
        for function in get_operator_funcs_rongzai(
            workspace, thread_id="sandbox-test", extra_dirs=extra_dirs,
            read_only_dirs=readonly, allolow_dangrous_cmd=True,
        )
    }


def call(functions, name, *args, **kwargs):
    return asyncio.run(functions[name](*args, **kwargs))


def test_worker_startup_checks_real_isolation():
    check_sandbox_support()


def test_shell_and_file_tools_preserve_analysis_workflow(tmp_path):
    functions = operators(tmp_path)
    assert call(functions, "run_write", "data.csv", "x,y\n1,3\n2,5\n3,7\n").startswith("Wrote")
    script = """
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
data = pd.read_csv("data.csv")
fit, _ = curve_fit(lambda x, a, b: a*x+b, data.x, data.y)
assert np.allclose(fit, [2, 1])
data.to_csv("result.csv", index=False)
plt.plot(data.x, data.y)
plt.savefig("fit.png")
with open("fit.json", "w") as output:
    json.dump(fit.tolist(), output)
print("ANALYSIS_OK")
"""
    assert call(functions, "run_write", "analyze.py", script).startswith("Wrote")
    result = call(functions, "run_bash", "python analyze.py", timeout=90)
    assert "Error:" not in result, result
    assert "ANALYSIS_OK" in result
    assert json.loads((tmp_path / "fit.json").read_text()) == pytest.approx([2, 1])
    assert (tmp_path / "fit.png").read_bytes().startswith(b"\x89PNG")
    assert call(functions, "run_read", "result.csv", start_line=2, end_line=2) == "1,3"
    assert call(functions, "run_edit", "result.csv", "1,3", "1,4").startswith("Edited")
    assert "1,4" in call(functions, "run_grep", "1,4", output_mode="content")
    event = json.loads(functions["serve_files_for_user"](["result.csv"], ["analysis output"]))
    content = event["event"]["content"]
    if isinstance(content, str):
        content = json.loads(content)
    assert base64.b64decode(content["files"][0]["base64_content"]) == (tmp_path / "result.csv").read_bytes()


def test_service_environment_and_host_proc_are_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("RONGZAI_TEST_SECRET", "synthetic-secret-not-a-real-credential")
    monkeypatch.setenv("BASH_ENV", "/not/a/real/startup-script")
    functions = operators(tmp_path)
    result = call(functions, "run_bash", "env")
    assert "Error:" not in result, result
    assert "RONGZAI_TEST_SECRET" not in result
    assert "synthetic-secret" not in result
    assert "BASH_ENV" not in result
    assert "HOME=/home/analyst" in result
    result = call(functions, "run_bash", "cat /proc/1/environ")
    assert result.startswith("Error:")


def test_tilde_writes_do_not_touch_host_home(tmp_path, monkeypatch):
    host_home = tmp_path / "host-home"
    host_home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(host_home))
    result = call(
        operators(workspace), "run_bash",
        "cd ~; mkdir .ssh; printf sandbox-marker > .ssh/authorized_keys; cat .ssh/authorized_keys",
    )
    assert "sandbox-marker" in result, result
    assert not (host_home / ".ssh").exists()


@pytest.mark.parametrize("method", ["shell", "python", "read", "write", "edit", "download", "grep"])
def test_host_files_inaccessible_across_tools(tmp_path, method):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = tmp_path / "host-private-dir" / "private"
    private.parent.mkdir()
    private.write_text("host-marker")
    functions = operators(workspace)
    target = str(private)
    if method == "shell":
        result = call(functions, "run_bash", f"cat {shlex.quote(target)}")
    elif method == "python":
        code = f"from pathlib import Path; Path({target!r}).write_text('changed')"
        result = call(functions, "run_bash", f"python -c {shlex.quote(code)}")
    elif method == "download":
        result = functions["serve_files_for_user"]([target], ["not allowed"])
    elif method == "grep":
        result = call(functions, "run_grep", "host-marker", path=target)
    else:
        args = {"read": (), "write": ("changed",), "edit": ("host-marker", "changed")}
        result = call(functions, f"run_{method}", target, *args[method])
    assert "Error" in result, result
    assert private.read_text() == "host-marker"


def test_symlinks_cannot_escape_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = tmp_path / "private"
    private.write_text("host-marker")
    (workspace / "link").symlink_to(private)
    functions = operators(workspace)
    for name, args in [
        ("run_read", ("link",)), ("run_write", ("link", "changed")),
        ("run_edit", ("link", "host-marker", "changed")),
        ("run_bash", ("cat link",)),
    ]:
        assert "Error" in call(functions, name, *args)
    assert private.read_text() == "host-marker"


def test_readonly_skills_and_dynamic_authorized_data(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    skill = skills / "skill.txt"
    skill.write_text("trusted")
    extra = [skills]
    functions = operators(workspace, extra, [skills])
    assert call(functions, "run_read", str(skill)) == "trusted"
    assert "Error" in call(functions, "run_write", str(skill), "changed")
    assert "Error" in call(functions, "run_bash", f"echo changed > {shlex.quote(str(skill))}")
    assert skill.read_text() == "trusted"
    data = tmp_path / "authorized-data"
    data.mkdir()
    (data / "input.csv").write_text("1,2")
    assert "Error" in call(functions, "run_read", str(data / "input.csv"))
    extra.append(data)
    assert call(functions, "run_read", str(data / "input.csv")) == "1,2"
    assert call(functions, "run_write", str(data / "output.csv"), "3,4").startswith("Wrote")


def test_authorized_symlink_paths_remain_usable(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_alias = tmp_path / "workspace-alias"
    workspace_alias.symlink_to(workspace, target_is_directory=True)
    data = tmp_path / "data"
    data.mkdir()
    (data / "input.csv").write_text("1,2")
    data_alias = tmp_path / "data-alias"
    data_alias.symlink_to(data, target_is_directory=True)
    functions = operators(workspace_alias, [data_alias])
    result = call(functions, "run_bash", f"cat {shlex.quote(str(data_alias / 'input.csv'))}")
    assert result == "1,2", result
    result = call(functions, "run_bash", f"echo output > {shlex.quote(str(workspace_alias / 'result'))}")
    assert result == "(no output)", result
    assert (workspace / "result").read_text().strip() == "output"


def test_migrated_datasets_are_searchable_readonly_and_analysis_still_works(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "migrated datasets"
    input_dir = data / "BL16" / "RUNdataFiles" / "RUN0023568"
    input_dir.mkdir(parents=True)
    (input_dir / "group2.dat").write_text("1 3\n2 5\n3 7\n")
    monkeypatch.setenv("RONGZAI_DATASETS_DIR", str(data))
    monkeypatch.setenv("HEPAI_API_KEY", "synthetic-secret")
    configured = get_datasets_dir()
    functions = operators(workspace, [configured], [configured])
    result = call(functions, "run_bash", 'find "$RONGZAI_DATASETS_DIR" -name "*.dat"')
    assert str(input_dir / "group2.dat") in result, result
    script = """
import os
from pathlib import Path
import numpy as np
assert "HEPAI_API_KEY" not in os.environ
root = Path(os.environ["RONGZAI_DATASETS_DIR"])
input_file = next(root.glob("BL16/RUNdataFiles/RUN*/group2.dat"))
data = np.loadtxt(input_file)
fit = np.polyfit(data[:, 0], data[:, 1], 1)
assert np.allclose(fit, [2, 1])
np.savetxt("fit.csv", fit, delimiter=",")
print("MIGRATED_ANALYSIS_OK")
"""
    assert call(functions, "run_write", "analyze.py", script).startswith("Wrote")
    assert "MIGRATED_ANALYSIS_OK" in call(functions, "run_bash", "python analyze.py")
    assert (workspace / "fit.csv").exists()
    assert call(functions, "run_read", str(input_dir / "group2.dat")).startswith("1 3")
    assert call(functions, "run_write", str(input_dir / "new.dat"), "changed").startswith("Error:")
    assert call(functions, "run_bash", 'echo changed > "$RONGZAI_DATASETS_DIR/new.dat"').startswith("Error:")
    assert not (data / "new.dat").exists()


def test_dataset_variable_not_exported_without_readonly_grant(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "private-datasets"
    data.mkdir()
    monkeypatch.setenv("RONGZAI_DATASETS_DIR", str(data))
    result = call(operators(workspace), "run_bash", "env")
    assert "RONGZAI_DATASETS_DIR" not in result, result


def test_shell_home_is_private_and_cwd_persists(tmp_path):
    functions = operators(tmp_path)
    assert call(functions, "run_bash", 'cd ~; printf "%s\\n" "$HOME"').startswith("/home/analyst")
    assert call(functions, "run_bash", "mkdir sub; cd sub") == "(no output)"
    assert call(functions, "run_bash", "pwd") == str(tmp_path / "sub")
    assert call(functions, "run_bash", "printf ok >/dev/null") == "(no output)"
    assert call(functions, "run_bash", "head -c 1 /dev/zero") == "\x00"


def test_network_namespace_cannot_connect_to_host(tmp_path):
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        script = (
            "import socket; s=socket.socket(); s.settimeout(1); "
            f"s.connect(('127.0.0.1',{port}))"
        )
        result = call(operators(tmp_path), "run_bash", f"python -c {shlex.quote(script)}")
    assert result.startswith("Error:"), result


def test_background_analysis_and_timeouts_use_same_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("RONGZAI_TEST_SECRET", "synthetic-secret")
    functions = operators(tmp_path)

    async def scenario():
        launch = json.loads(await functions["run_bash_background"](
            "python -c 'import time; time.sleep(.2); print(6*7)'; env", wait_time=0.01,
        ))
        for _ in range(100):
            state = json.loads(await functions["get_bash_task"](launch["task_id"]))
            if state["status"] != "running":
                break
            await asyncio.sleep(0.05)
        assert state["status"] == "completed", state
        assert "42" in state["log"]
        assert "RONGZAI_TEST_SECRET" not in state["log"]
        timeout_launch = await functions["run_bash_background"](
            "sleep .3; echo failed > late.txt", timeout=0.1, wait_time=1,
        )
        assert "timed out" in timeout_launch, timeout_launch
        await asyncio.sleep(0.5)
        assert not (tmp_path / "late.txt").exists()

    asyncio.run(scenario())


def test_sync_timeout_kills_children(tmp_path):
    async def scenario():
        result = await operators(tmp_path)["run_bash"]("sleep .3; echo late > late.txt", timeout=0.1)
        assert "timed out" in result
        await asyncio.sleep(0.5)
        assert not (tmp_path / "late.txt").exists()
    asyncio.run(scenario())


def test_cancellation_kills_children(tmp_path):
    async def scenario():
        task = asyncio.create_task(operators(tmp_path)["run_bash"](
            "sleep .4; echo late > late.txt",
        ))
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)
        assert not (tmp_path / "late.txt").exists()
    asyncio.run(scenario())


def test_file_read_truncation_and_errors(tmp_path):
    (tmp_path / "large").write_text("x" * 6000)
    functions = operators(tmp_path)
    result = call(functions, "run_read", "large")
    assert result.startswith("x" * 5000 + "\n\n[Truncated:")
    assert "Error:" in call(functions, "run_edit", "large", "missing", "new")
    assert call(functions, "run_grep", "absent") == "No matches found"
    assert "Error:" in call(functions, "run_grep", "[")


def test_missing_sandbox_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.agent.analysis_sandbox.shutil.which", lambda *a, **kw: None)
    result = call(operators(tmp_path), "run_bash", "echo should-not-run > marker")
    assert "host execution is disabled" in result
    assert not (tmp_path / "marker").exists()


def test_failed_sandbox_search_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.agent.analysis_sandbox.shutil.which", lambda *a, **kw: "/bin/false")
    result = call(operators(tmp_path), "run_grep", "anything")
    assert result.startswith("Error:"), result


def test_unsafe_runtime_mounts_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("RONGZAI_SANDBOX_READONLY_PATHS", '["/"]')
    sandbox = AnalysisSandbox(tmp_path, lambda: [])
    with pytest.raises(ValueError, match="Unsafe sandbox mount"):
        sandbox.command([sys.executable], tmp_path)


def mcp_agent(workspace):
    agent = object.__new__(RongZaiAgent)
    agent._work_dir = workspace
    agent._analysis_sandbox = AnalysisSandbox(workspace, lambda: [])
    agent._mcp_connections = {}
    agent._mcp_server_tool_names = {}
    agent._tools = []
    agent._tools_names = []
    return agent


def test_stdio_mcp_runs_in_real_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("RONGZAI_TEST_SECRET", "synthetic-secret")
    agent = mcp_agent(tmp_path)
    script = """
import os
from mcp.server.fastmcp import FastMCP
server = FastMCP("sandbox-regression")
@server.tool()
def calculate() -> str:
    assert "RONGZAI_TEST_SECRET" not in os.environ
    return str(6 * 7)
server.run(transport="stdio")
"""

    async def scenario():
        try:
            result = await agent.ConnectMcpServer("stdio", command=sys.executable, args=["-I", "-c", script])
            assert result.startswith("Connected MCP server"), result
            result = await agent._mcp_connections[sys.executable].call_tool("calculate", {})
            assert "42" in result.to_text()
        finally:
            for workbench in agent._mcp_connections.values():
                await workbench.stop()

    asyncio.run(scenario())


def test_stdio_env_override_is_rejected(tmp_path):
    agent = mcp_agent(tmp_path)
    result = asyncio.run(agent.ConnectMcpServer("stdio", command=sys.executable, env={"PATH": "/tmp"}))
    assert result.startswith("Error:")
    assert not agent._mcp_connections


def test_sse_mcp_connection_keeps_existing_transport(tmp_path, monkeypatch):
    from autogen_ext.tools.mcp import SseServerParams

    received = []

    class Workbench:
        def __init__(self, server_params):
            received.append(server_params)

        async def start(self):
            pass

        async def list_tools(self):
            return [{"name": "fit", "description": "Fit data"}]

    monkeypatch.setattr("src.core.agent.agent.McpWorkbench", Workbench)
    result = asyncio.run(mcp_agent(tmp_path).ConnectMcpServer("sse", url="http://127.0.0.1:42506/sse"))
    assert result.startswith("Connected MCP server"), result
    assert isinstance(received[0], SseServerParams)
    assert received[0].url == "http://127.0.0.1:42506/sse"


def test_vision_file_read_uses_isolation(tmp_path, monkeypatch):
    from PIL import Image as PillowImage

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent = mcp_agent(workspace)
    agent._model_client = SimpleNamespace(_client=SimpleNamespace(api_key="synthetic-test-key"))
    picture = workspace / "plot.png"
    PillowImage.new("RGB", (4, 4)).save(picture)
    private = tmp_path / "private.png"
    PillowImage.new("RGB", (4, 4)).save(private)

    class VisionClient:
        def __init__(self, **kwargs):
            pass

        async def create(self, *args, **kwargs):
            return SimpleNamespace(content="plot understood")

    monkeypatch.setattr("src.core.agent.agent.HepAIChatCompletionClient", VisionClient)
    assert asyncio.run(agent.AnalyzeImageWithVision(str(picture), "describe")) == "plot understood"
    denied = asyncio.run(agent.AnalyzeImageWithVision(str(private), "describe"))
    assert denied.startswith("Error"), denied
