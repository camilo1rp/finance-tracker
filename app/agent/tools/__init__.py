from app.agent.tools.enricher import ENRICHER_TOOLS
from app.agent.tools.read import READ_TOOLS, list_accounts, list_transactions, search_transactions_tool
from app.agent.tools.steward import STEWARD_TOOLS

STEWARD_AGENT_TOOLS = [*READ_TOOLS, *STEWARD_TOOLS]
ENRICHER_AGENT_TOOLS = [
    list_accounts,
    list_transactions,
    search_transactions_tool,
    *ENRICHER_TOOLS,
]
