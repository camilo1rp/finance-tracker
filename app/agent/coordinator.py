"""Coordinator agent: resolve names/dates, answer simple totals, delegate the rest."""
from __future__ import annotations

from langchain.agents import create_agent

from app.agent.analyst import build_analyst
from app.agent.config import EnricherDeps, enricher_model_name, model_name
from app.agent.enricher_graph import build_enricher_graph
from app.agent.middleware import CurrentDateMiddleware
from app.agent.steward_graph import build_steward_graph
from app.agent.tools.read import get_cash_flow, get_total, list_accounts, list_owners, summarize
from app.agent.tools.subagents import make_subagent_tools

COORDINATOR_PROMPT = """You are the conversational entrypoint for a personal finance ledger.

Resolve people and account names to ids (list_owners, list_accounts) and relative dates such as "last month" to concrete YYYY-MM-DD ranges *before* delegating. Put those ids and dates in the task text; subagents do not see this conversation.
A current calendar date is attached to each turn; use it to resolve relative dates. Never guess the calendar. Do not treat that date as something the user said or confirmed.

Answer "how much did I spend" with get_total. Type SPEND means purchases, not net spending. Lead with `spend` (purchases − refunds) and `net_cash_flow` (income + refunds − purchases − fees). Then mention purchases and refunds. Do not call the SPEND bucket "total spend". Transfers and adjustments are not spending or cash-flow net. sign_convention is import convention, not the sign of returned amounts. summarize, top_merchants, get_cash_flow, and list wrappers (largest/search totals) use the same field meanings.
Delegate multi-step analysis (comparisons, trends, top merchants, unusual transactions, description search) to ask_analyst.
Delegate anything touching mappings, unmapped values, or overrides to run_data_steward.
When delegating mapping work, include any account, kind (type, category, owner, or merchant), or merchant scope the user asked for in the task text. If the user asks for contains/starts-with/wildcard matching, say so in the steward task (patterns use `%` as a wildcard; no `%` is exact).

Never fabricate numbers. If the steward pauses for approval, tell the user what is pending.
When relaying steward outcomes, repeat the steward's created_ids, updated_ids, deleted_ids, and reclass_updated exactly; never paraphrase counts into vague success claims.

Questions about what a purchase was, or requests to enrich or research transactions from email, go to run_enricher with a task string naming the transactions or a merchant and date range. The enricher returns a proposal id. To apply it, call run_data_steward with a task that names that proposal id. Never pass op lists to the steward yourself. Pure analytics stays with ask_analyst. If the enricher reports the email source is unavailable, tell the user how to enable it (the EMAIL_PROVIDER variable) and do not retry.
"""


def build_coordinator(
    *,
    model=None,
    analyst_model=None,
    steward_model=None,
    enricher_model=None,
    enricher_deps: EnricherDeps | None = None,
    checkpointer=None,
):
    """Compile the coordinator. This is the only graph that gets a checkpointer.

    checkpointer=None is valid only when the graph is served by the LangGraph API
    server, which injects its own persistence; interrupts will fail without one
    otherwise. The only in-repo caller that passes None is app.agent.studio.
    """
    resolved = model if model is not None else model_name()
    analyst = build_analyst(model=analyst_model if analyst_model is not None else resolved)
    steward = build_steward_graph(
        model=steward_model if steward_model is not None else resolved,
        checkpointer=None,
    )
    if enricher_model is not None:
        enricher_resolved = enricher_model
    elif model is not None:
        enricher_resolved = resolved
    else:
        enricher_resolved = enricher_model_name()
    enricher = build_enricher_graph(model=enricher_resolved, deps=enricher_deps)
    tools = [
        list_owners,
        list_accounts,
        get_total,
        get_cash_flow,
        summarize,
        *make_subagent_tools(analyst=analyst, steward=steward, enricher=enricher),
    ]
    agent_kwargs: dict = {
        "system_prompt": COORDINATOR_PROMPT,
        "middleware": [CurrentDateMiddleware()],
        "name": "coordinator",
    }
    if checkpointer is not None:
        agent_kwargs["checkpointer"] = checkpointer
    return create_agent(
        resolved,
        tools,
        **agent_kwargs,
    )
