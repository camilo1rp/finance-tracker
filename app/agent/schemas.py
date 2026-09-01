from typing import NotRequired

from langchain.agents import AgentState


class StewardState(AgentState):
    proposed_rules: NotRequired[list[dict]]
    account_scope: NotRequired[int | None]
    pending_preview: NotRequired[dict | None]
    apply_result: NotRequired[dict | None]
    rationale: NotRequired[str | None]
