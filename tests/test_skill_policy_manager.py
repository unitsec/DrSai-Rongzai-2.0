from src.core.agent.skill_policy_manager import (
    StructuredSkillPolicyManager,
    get_demote_core_skill_tool,
    get_promote_core_skill_tool,
    get_replace_core_skill_tool,
)


def test_skill_policy_manager_keeps_single_active_core_skill_slot():
    manager = StructuredSkillPolicyManager()

    manager.promote("gsas2_refine", "Use this for refinement-heavy work.")
    manager.promote("pdf_refine", "Use this instead for simpler tasks.")
    manager.replace("zrietveld_refine", "gsas2_refine", "Prefer the more complete refinement workflow.")

    rendered = manager.build_system_prompt_block()

    assert "Single core-skill slot" in rendered
    assert "Active core skill: gsas2_refine" in rendered
    assert manager.get_active_core_skill_name() == "gsas2_refine"


def test_skill_policy_tools_expose_separate_actions():
    promote_tool = get_promote_core_skill_tool()
    demote_tool = get_demote_core_skill_tool()
    replace_tool = get_replace_core_skill_tool()

    assert promote_tool.get("name") == "PromoteCoreSkill"
    assert demote_tool.get("name") == "DemoteCoreSkill"
    assert replace_tool.get("name") == "ReplaceCoreSkill"
