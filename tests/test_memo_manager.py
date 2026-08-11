from src.core.agent.memo_manager import StructuredMemoManager, get_memo_write_tool


def test_memo_manager_keeps_only_essential_items_and_can_clear_them():
    manager = StructuredMemoManager(max_items=3)

    rendered = manager.update([
        {"key": "diffraction_path", "value": "/data/1.cif"},
        {"key": "pdf_path", "value": "/data/1.pdf"},
    ])

    assert "diffraction_path" in rendered
    assert "pdf_path" in rendered

    # Incremental update: sending only new entries should keep existing ones.
    rendered = manager.update([
        {"key": "instprm_path", "value": "/data/rt.instprm"},
    ])
    assert "diffraction_path" in rendered
    assert "pdf_path" in rendered
    assert "instprm_path" in rendered

    cleared = manager.update([], clear_all=True)
    assert cleared == "No active memo."

    assert manager.build_system_prompt_block() == ""


def test_memo_tool_guides_short_and_removal():
    tool = get_memo_write_tool()
    description = tool.get("description", "").lower()
    assert "short runtime memo" in description or "short" in description
    assert "remove" in description
    assert "facts" in description or "constraints" in description
    assert "brief" in description or "useful" in description
    assert "incremental" in description
    assert "clear_all" in description


def test_memo_manager_supports_rich_content_and_explicit_removal():
    manager = StructuredMemoManager(max_items=3)

    rendered = manager.update([
        {
            "key": "expert_rule",
            "value": "First check the sample geometry.\nThen verify the calibration before any conclusion.",
            "remove": False,
        }
    ])

    assert "First check the sample geometry" in rendered
    assert "verify the calibration" in rendered

    cleared = manager.update([
        {
            "key": "expert_rule",
            "value": "This entry should be removed explicitly.",
            "remove": True,
        }
    ])

    assert cleared == "No active memo."
    assert manager.items == []
