"""Coordinator agent: resolve names/dates, answer simple totals, delegate the rest."""
from __future__ import annotations

from langchain.agents import create_agent

from app.agent.analyst import build_analyst
from app.agent.config import model_name
from app.agent.steward_graph import build_steward_graph
from app.agent.tools.read import get_total, list_accounts, list_owners, summarize
from app.agent.tools.subagents import make_subagent_tools

COORDINATOR_PROMPT = """You are the conversational entrypoint for a personal finance ledger.

Resolve people and account names to ids (list_owners, list_accounts) and relative dates such as "last month" to concrete YYYY-MM-DD ranges *before* delegating. Put those ids and dates in the task text; subagents do not see this conversation.

Answer single-number questions (a total, one summary) yourself with get_total or summarize.
Delegate multi-step analysis (comparisons, trends, top merchants, unusual transactions, description search) to ask_analyst.
Delegate anything touching mappings, unmapped values, or overrides to run_data_steward.

Never fabricate numbers. If the steward pauses for approval, tell the user what is pending.
"""


def build_coordinator(
    *,
    model=None,
    analyst_model=None,
    steward_model=None,
    checkpointer=None,
):
    """Compile the coordinator. This is the only graph that gets a checkpointer."""
    if checkpointer is None:
        raise ValueError("checkpointer is required on the coordinator (the only saver in the stack)")
    resolved = model if model is not None else model_name()
    analyst = build_analyst(model=analyst_model if analyst_model is not None else resolved)
    steward = build_steward_graph(
        model=steward_model if steward_model is not None else resolved,
        checkpointer=None,
    )
    tools = [
        list_owners,
        list_accounts,
        get_total,
        summarize,
        *make_subagent_tools(analyst=analyst, steward=steward),
    ]
    return create_agent(
        resolved,
        tools,
        system_prompt=COORDINATOR_PROMPT,
        checkpointer=checkpointer,
        name="coordinator",
    )
