from src.core.agent.agent import _inject_runtime_session_id


def test_injects_current_thread_for_supported_mcp_tool():
    arguments = {"user_id": "user@example.com", "mission_name": "PDF_refine"}

    result = _inject_runtime_session_id(
        arguments,
        supports_session_id=True,
        session_id="20260911173219",
    )

    assert result["session_id"] == "20260911173219"


def test_does_not_inject_session_into_unsupported_mcp_tool():
    arguments = {"query": "example"}

    result = _inject_runtime_session_id(
        arguments,
        supports_session_id=False,
        session_id="20260911173219",
    )

    assert result == {"query": "example"}


def test_preserves_explicit_session_id():
    arguments = {"session_id": "explicit-session"}

    result = _inject_runtime_session_id(
        arguments,
        supports_session_id=True,
        session_id="20260911173219",
    )

    assert result["session_id"] == "explicit-session"