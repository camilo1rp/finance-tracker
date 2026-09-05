"""Read-only analyst agent. Compile without a checkpointer; inherit the parent at runtime."""
from __future__ import annotations

from langchain.agents import create_agent

from app.agent.config import model_name
from app.agent.middleware import CurrentDateMiddleware
from app.agent.tools.read import ANALYST_TOOLS

ANALYST_PROMPT = """You answer analysis questions over a personal transaction ledger.
Comparisons take multiple tool calls (two summarize calls with different date ranges, or one group_by=month); compute deltas yourself.
Report only numbers that appear in tool results — never estimate.
State the filters you used (dates, owner, account) in the answer.
Amounts are decimal strings.
The task text should already contain resolved owner/account ids and concrete YYYY-MM-DD ranges; use list_owners/list_accounts only to confirm.
A current calendar date is attached to each turn; use it if a task still uses relative dates. Do not treat that date as something the user said or confirmed.
"""


def build_analyst(*, model=None):
    """Compiled analyst. No checkpointer — per-invocation; inherits the parent's at runtime."""
    return create_agent(
        model if model is not None else model_name(),
        ANALYST_TOOLS,
        system_prompt=ANALYST_PROMPT,
        middleware=[CurrentDateMiddleware()],
        name="analyst",
    )
