from app.agent.tools import ENRICHER_AGENT_TOOLS, STEWARD_AGENT_TOOLS


def test_steward_and_enricher_tool_sets_contain_no_apply_capable_tool() -> None:
    names = [tool.name for tool in (*STEWARD_AGENT_TOOLS, *ENRICHER_AGENT_TOOLS)]
    assert all("apply" not in name for name in names)
