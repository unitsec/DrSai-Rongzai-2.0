import asyncio

from src.core.agent.utils import get_operator_funcs_rongzai


def test_run_bash_accepts_tilde_path_in_allowed_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.agent.utils.Path.home", lambda: tmp_path)
    functions = {
        function.__name__: function
        for function in get_operator_funcs_rongzai(
            tmp_path,
            thread_id="workspace-permission-test",
            extra_dirs=[tmp_path],
            only_in_workspace=True,
        )
    }

    result = asyncio.run(functions["run_bash"]("ls -d ~/"))

    assert "outside the allowed workspace" not in result


def test_run_bash_accepts_allowed_directory_referenced_by_shell_variable(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.agent.utils.Path.home", lambda: tmp_path)
    functions = {
        function.__name__: function
        for function in get_operator_funcs_rongzai(
            tmp_path,
            thread_id="workspace-permission-test",
            extra_dirs=[tmp_path],
            only_in_workspace=True,
        )
    }

    result = asyncio.run(functions["run_bash"]('DATA=~/; ls -d "$DATA"'))

    assert "outside the allowed workspace" not in result


def test_run_bash_still_rejects_unallowed_absolute_path(tmp_path):
    functions = {
        function.__name__: function
        for function in get_operator_funcs_rongzai(
            tmp_path,
            thread_id="workspace-permission-test",
            only_in_workspace=True,
        )
    }

    result = asyncio.run(functions["run_bash"]("ls -d /etc"))

    assert result == "Error: Path '/etc' is outside the allowed workspace"