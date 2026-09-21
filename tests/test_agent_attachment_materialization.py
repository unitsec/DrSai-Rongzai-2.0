from pathlib import Path

from src.core.agent.agent import RongZaiAgent


def test_materialize_attachment_resolves_missing_path_from_user_upload_dir(
    tmp_path, monkeypatch
):
    user_id = "user@example.com"
    source_dir = tmp_path / "ui-files" / user_id
    source_dir.mkdir(parents=True)
    source = source_dir / "sample.txt"
    source.write_text("sample data", encoding="utf-8")

    upload_dir = tmp_path / "workspace" / "uploads"
    upload_dir.mkdir(parents=True)
    monkeypatch.setenv("DRSAI_UI_USER_FILES_ROOT", str(tmp_path / "ui-files"))

    agent = object.__new__(RongZaiAgent)
    agent._user_id = user_id
    agent._thread_id = "20260911161913"
    agent._upload_dir = upload_dir

    result = agent._materialize_attached_file(
        {
            "name": source.name,
            "path": None,
            "size": source.stat().st_size,
            "url": "https://invalid.example/unused",
        }
    )

    target = Path(result)
    assert target == upload_dir / source.name
    assert target.read_text(encoding="utf-8") == "sample data"