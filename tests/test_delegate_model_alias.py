from src.core.agent.agent import RongZaiAgent


def _agent():
    agent = object.__new__(RongZaiAgent)
    agent._thread_id = "thread-1"
    agent._sub_agent_config = {}
    agent._subagent_model_alias_cache = {
        "thread-1:YuanYuan_Agent": [
            "deepseek-v4-pro (free)",
            "glm-5.2 (free)",
            "gpt-5 (charge)",
            "miniMax (free)",
        ]
    }
    return agent


def test_extract_delegate_model_alias_preserves_parenthesized_suffix():
    agent = _agent()

    alias = agent._extract_delegate_model_alias(
        {
            "agent_type": "YuanYuan_Agent",
            "context": "default_config_name=glm-5.2 (free)\n继续完成精修。",
        }
    )

    assert alias == "glm-5.2 (free)"


def test_extract_delegate_model_alias_does_not_rewrite_shorthand():
    agent = _agent()

    alias = agent._extract_delegate_model_alias(
        {
            "agent_type": "YuanYuan_Agent",
            "context": "subagent_model=miniMax",
        }
    )

    assert alias == "miniMax"


def test_extract_delegate_model_alias_supports_quoted_alias():
    agent = _agent()

    alias = agent._extract_delegate_model_alias(
        {
            "agent_type": "YuanYuan_Agent",
            "context": 'subagent_model="deepseek-v4-pro (free)" then retry',
        }
    )

    assert alias == "deepseek-v4-pro (free)"


def test_extract_delegate_model_alias_preserves_suffix_before_alias_discovery():
    agent = _agent()
    agent._subagent_model_alias_cache = {}

    alias = agent._extract_delegate_model_alias(
        {
            "agent_type": "YuanYuan_Agent",
            "context": "subagent_model=deepseek-v4-pro (free)\n开始任务。",
        }
    )

    assert alias == "deepseek-v4-pro (free)"