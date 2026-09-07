from app.agent.tools.enricher import ENRICHER_TOOLS
from app.agent.tools.read import (
    READ_TOOLS,
    get_transaction,
    list_accounts,
    list_transactions,
    list_values,
    search_transactions_tool,
)
from app.agent.tools.steward import STEWARD_TOOLS

STEWARD_AGENT_TOOLS = [*READ_TOOLS, *STEWARD_TOOLS]
ENRICHER_AGENT_TOOLS = [
    list_accounts,
    list_values,
    list_transactions,
    get_transaction,
    search_transactions_tool,
    *ENRICHER_TOOLS,
]
