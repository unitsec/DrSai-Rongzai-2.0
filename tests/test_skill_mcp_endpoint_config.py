import pytest

from src.core.agent.agent import resolve_mcp_skill_endpoints


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


def test_resolve_mcp_skill_endpoints_uses_magnetic_service_default(monkeypatch):
    monkeypatch.delenv("MCP_MAGNETIC_STRUCTURE_PORT", raising=False)

    result = resolve_mcp_skill_endpoints(
        "url: http://127.0.0.1:${MCP_MAGNETIC_STRUCTURE_PORT}/sse"
    )

    assert result == "url: http://127.0.0.1:42511/sse"


@pytest.mark.parametrize("port", ["", "invalid", "0", "65536"])
def test_resolve_mcp_skill_endpoints_rejects_invalid_port(monkeypatch, port):
    monkeypatch.setenv("MCP_PDF_REFINE_PORT", port)

    with pytest.raises(ValueError, match="MCP_PDF_REFINE_PORT"):
        resolve_mcp_skill_endpoints("${MCP_PDF_REFINE_PORT}")
