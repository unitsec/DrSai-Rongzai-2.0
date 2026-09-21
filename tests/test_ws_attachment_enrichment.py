import json

from drsai_ui.ui_backend.backend.utils.utils import construct_task
from drsai_ui.ui_backend.backend.web.routes.ws import _enrich_input_response_with_files


def test_input_response_files_are_enriched_with_structured_settings(tmp_path, monkeypatch):
    user_id = "user@example.com"
    user_dir = tmp_path / user_id
    user_dir.mkdir()
    source = user_dir / "sample.txt"
    source.write_text("sample data", encoding="utf-8")
    monkeypatch.setenv("DRSAI_UI_USER_FILES_ROOT", str(tmp_path))
    metadata = {
        "settings_config": {"agent_id": "agent-1"},
        "files": [
            {
                "name": source.name,
                "type": "text/plain",
                "path": None,
                "size": source.stat().st_size,
            }
        ],
    }

    result = _enrich_input_response_with_files(
        json.dumps({"content": "analyze this file"}),
        metadata,
    )

    assert result is metadata
    assert result["settings_config"] == {"agent_id": "agent-1"}
    attached_files = json.loads(result["attached_files"])
    assert attached_files[0]["name"] == source.name
    assert attached_files[0]["path"] is None


def test_construct_task_does_not_embed_local_attachment_payload(tmp_path):
    source = tmp_path / "large.txt"
    payload = "large attachment payload " * 10_000
    source.write_text(payload, encoding="utf-8")

    messages = construct_task(
        "refine this file",
        files=[
            {
                "name": source.name,
                "type": "text/plain",
                "path": str(source),
                "size": source.stat().st_size,
            }
        ],
        metadata={},
    )

    assert len(messages) == 2
    assert messages[0].content == f"Attached file: {source.name}"
    assert payload not in messages[0].content
    attached_files = json.loads(messages[-1].metadata["attached_files"])
    assert attached_files == [
        {
            "name": source.name,
            "type": "text/plain",
            "size": source.stat().st_size,
            "path": str(source),
            "url": "",
        }
    ]