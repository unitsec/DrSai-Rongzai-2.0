import json

from drsai.modules.managers.messages import FileInfo, FilesContent, FilesEvent

from src.core.agent.agent import RongZaiAgent


def _agent():
    return object.__new__(RongZaiAgent)


def test_subagent_markdown_images_are_controlled_by_env(monkeypatch):
    agent = _agent()
    markdown = "result\n![fit](https://example.test/fit.png)"

    monkeypatch.delenv("RONGZAI_SHOW_SUBAGENT_IMAGES", raising=False)
    assert "![fit]" not in agent._strip_subagent_image_markdown(markdown)

    monkeypatch.setenv("RONGZAI_SHOW_SUBAGENT_IMAGES", "true")
    assert agent._strip_subagent_image_markdown(markdown) == markdown


def test_disabled_subagent_images_do_not_hide_other_files(monkeypatch):
    agent = _agent()
    monkeypatch.setenv("RONGZAI_SHOW_SUBAGENT_IMAGES", "false")
    event = FilesEvent(
        source="subagent",
        content=FilesContent(
            files=[
                FileInfo(name="fit.png", mime_type="image/png"),
                FileInfo(name="result.csv", mime_type="text/csv"),
            ]
        ),
    )

    filtered = agent._filter_subagent_files_event(event)

    assert filtered is not None
    payload = filtered.content if isinstance(filtered.content, dict) else filtered.content.model_dump()
    assert [item["name"] for item in payload["files"]] == ["result.csv"]


def test_enabled_subagent_images_preserve_files_event(monkeypatch):
    agent = _agent()
    monkeypatch.setenv("RONGZAI_SHOW_SUBAGENT_IMAGES", "true")
    event = FilesEvent(
        source="subagent",
        content=json.dumps({"files": [{"name": "fit.png", "mime_type": "image/png"}]})
    )

    assert agent._filter_subagent_files_event(event) is event