from app.agent.tools import ENRICHER_AGENT_TOOLS, STEWARD_AGENT_TOOLS
from app.agent.tools.read import ANALYST_TOOLS, CATALOG_TOOLS


def test_steward_and_enricher_tool_sets_contain_no_apply_capable_tool() -> None:
    names = [tool.name for tool in (*STEWARD_AGENT_TOOLS, *ENRICHER_AGENT_TOOLS)]
    assert all("apply" not in name for name in names)


def test_catalog_and_detail_tools_are_shared() -> None:
    catalog = {tool.name for tool in CATALOG_TOOLS}
    assert catalog == {"list_owners", "list_accounts", "list_values"}
    analyst = {tool.name for tool in ANALYST_TOOLS}
    assert {"list_values", "get_transaction"}.issubset(analyst)
    enricher = {tool.name for tool in ENRICHER_AGENT_TOOLS}
    assert {"list_values", "get_transaction"}.issubset(enricher)
    steward = {tool.name for tool in STEWARD_AGENT_TOOLS}
    assert {"list_values", "get_transaction", "list_mappings"}.issubset(steward)
