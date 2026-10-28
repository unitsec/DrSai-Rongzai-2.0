import pytest
from pathlib import Path

from src.core.agent.agent import resolve_mcp_skill_endpoints, resolve_skill_runtime_config
from src.configs.constants import get_datasets_dir


def test_resolve_mcp_skill_endpoints_uses_container_port(monkeypatch):
    monkeypatch.setenv("MCP_GSASII_REFINE_PORT", "43506")

    result = resolve_mcp_skill_endpoints(
        "url: http://127.0.0.1:${MCP_GSASII_REFINE_PORT}/sse"
    )

    assert result == "url: http://127.0.0.1:43506/sse"


def test_resolve_mcp_skill_endpoints_uses_service_default(monkeypatch):
    monkeypatch.delenv("MCP_NEUTRON_REFLECTION_PORT", raising=False)

    result = resolve_mcp_skill_endpoints(
        "url: http://127.0.0.1:${MCP_NEUTRON_REFLECTION_PORT}/sse"
    )

    assert result == "url: http://127.0.0.1:42520/sse"


@pytest.mark.parametrize("port", ["", "invalid", "0", "65536"])
def test_resolve_mcp_skill_endpoints_rejects_invalid_port(monkeypatch, port):
    monkeypatch.setenv("MCP_PDF_REFINE_PORT", port)

    with pytest.raises(ValueError, match="MCP_PDF_REFINE_PORT"):
        resolve_mcp_skill_endpoints("${MCP_PDF_REFINE_PORT}")


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_datasets_default_is_checkout_relative(monkeypatch, tmp_path, configured):
    if configured is None:
        monkeypatch.delenv("RONGZAI_DATASETS_DIR", raising=False)
    else:
        monkeypatch.setenv("RONGZAI_DATASETS_DIR", configured)
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[1]
    assert get_datasets_dir() == root / "datasets"


def test_dataset_path_config_supports_migration_and_spaces(monkeypatch, tmp_path):
    data = tmp_path / "migrated data"
    data.mkdir()
    alias = tmp_path / "data-alias"
    alias.symlink_to(data, target_is_directory=True)
    monkeypatch.setenv("RONGZAI_DATASETS_DIR", str(alias))
    assert get_datasets_dir() == data
    monkeypatch.setenv("MCP_GSASII_REFINE_PORT", "43506")
    monkeypatch.setenv("HEPAI_API_KEY", "synthetic-secret-not-a-real-key")
    content = resolve_skill_runtime_config(
        "${RONGZAI_DATASETS_DIR}/BL16 http://127.0.0.1:${MCP_GSASII_REFINE_PORT}/sse ${HEPAI_API_KEY}"
    )
    assert content == f"{data}/BL16 http://127.0.0.1:43506/sse ${{HEPAI_API_KEY}}"


@pytest.mark.parametrize("kind", ["relative", "missing", "file"])
def test_invalid_dataset_config_fails_explicitly(monkeypatch, tmp_path, kind):
    file = tmp_path / "file"
    file.write_text("not a directory")
    configured = {"relative": "datasets", "missing": str(tmp_path / "missing"), "file": str(file)}[kind]
    monkeypatch.setenv("RONGZAI_DATASETS_DIR", configured)
    with pytest.raises(ValueError, match="RONGZAI_DATASETS_DIR"):
        get_datasets_dir()


@pytest.mark.parametrize("skill", ["gsas2_refine", "pdf_refine", "zrietveld_refine"])
def test_refinement_skills_resolve_configured_dataset_path(monkeypatch, tmp_path, skill):
    monkeypatch.setenv("RONGZAI_DATASETS_DIR", str(tmp_path))
    root = Path(__file__).resolve().parents[1]
    content = (root / "agent_skills" / skill / "SKILL.md").read_text()
    assert "${RONGZAI_DATASETS_DIR}" in content
    resolved = resolve_skill_runtime_config(content)
    assert f"{tmp_path}/" in resolved
    assert "${RONGZAI_DATASETS_DIR}" not in resolved
    assert "~/rongzai-agent/datasets" not in resolved
