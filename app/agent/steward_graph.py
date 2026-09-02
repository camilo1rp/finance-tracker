"""Steward subgraph: propose → preview → interrupt → apply → re-check."""
from __future__ import annotations

from typing import Literal

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.schemas import StewardState
from app.agent.tools import STEWARD_AGENT_TOOLS
from app.agent.config import model_name, tool_session
from app.schemas import ApplyMappingPlanIn, ProposedMappingIn
from app.services.mapping_preview_service import apply_mapping_plan, preview_mappings

STEWARD_PROMPT = """You clean up normalization mappings.
Workflow: fetch unmapped values → inspect examples → propose rules → always preview before submitting → submit the plan with the preview attached.
Never claim anything was applied; applying happens only after a human approves.
"""


def _route_after_steward(state: StewardState) -> Literal["human_approval", "__end__"]:
    if state.get("proposed_rules"):
        return "human_approval"
    return "__end__"


def _as_proposed_rules(rules: list) -> list[ProposedMappingIn]:
    parsed: list[ProposedMappingIn] = []
    for rule in rules:
        if isinstance(rule, ProposedMappingIn):
            parsed.append(rule)
        else:
            parsed.append(ProposedMappingIn.model_validate(rule))
    return parsed


def _recompute_preview(rules: list, account_id: int | None) -> dict:
    """Fresh preview for `rules`. Opens a short-lived session; never call across interrupt()."""
    parsed = _as_proposed_rules(rules)
    with tool_session() as db:
        preview = preview_mappings(db, parsed, account_id=account_id)
    return preview.model_dump(mode="json")


def human_approval(state: StewardState) -> Command[Literal["steward", "execute"]]:
    submitted = state.get("proposed_rules") or []
    account_scope = state.get("account_scope")
    # Recompute from the rules actually in state. Do not trust pending_preview:
    # the model can submit a different set than it last previewed, or skip preview.
    # Close the session before interrupt() — this node restarts on resume and the
    # graph may sit paused for hours.
    preview = _recompute_preview(submitted, account_scope)
    payload = {
        "rules": submitted,
        "preview": preview,
        "rationale": state.get("rationale"),
    }
    decision = interrupt(payload)
    if not isinstance(decision, dict):
        decision = {"decision": decision}
    if decision.get("decision") != "approve":
        return Command(
            goto="steward",
            update={
                "proposed_rules": [],
                "pending_preview": None,
                "rationale": None,
                "messages": [
                    HumanMessage(
                        content="Plan rejected. Nothing was applied. Propose a different plan if needed."
                    )
                ],
            },
        )
    rules = decision.get("rules")
    if rules is None:
        rules = submitted
    # Evidence for exactly the subset that will run.
    subset_preview = _recompute_preview(rules, account_scope)
    return Command(
        goto="execute",
        update={"proposed_rules": rules, "pending_preview": subset_preview},
    )


def execute(state: StewardState) -> Command[Literal["steward"]]:
    rules = [
        ProposedMappingIn.model_validate(rule)
        for rule in (state.get("proposed_rules") or [])
    ]
    with tool_session() as db:
        result = apply_mapping_plan(
            db,
            ApplyMappingPlanIn(rules=rules, account_id=state.get("account_scope")),
        )
    summary = (
        f"Plan executed. created_mapping_ids={result.created_mapping_ids} "
        f"skipped_duplicates={len(result.skipped_duplicates)} "
        f"reclass_scanned={result.reclass_scanned} "
        f"reclass_updated={result.reclass_updated}. "
        "Nothing else will be applied unless a new plan is submitted. "
        "Re-check unmapped values if useful, then tell the user what changed."
    )
    return Command(
        goto="steward",
        update={
            "apply_result": result.model_dump(),
            "proposed_rules": [],
            "pending_preview": None,
            "rationale": None,
            "messages": [HumanMessage(content=summary)],
        },
    )


def build_steward_graph(*, model=None, checkpointer=None):
    """Compile the steward graph. `checkpointer` is required for interrupt()."""
    if checkpointer is None:
        raise ValueError("checkpointer is required (use InMemorySaver in tests)")
    steward = create_agent(
        model if model is not None else model_name(),
        STEWARD_AGENT_TOOLS,
        system_prompt=STEWARD_PROMPT,
        state_schema=StewardState,
        name="steward",
    )
    builder = StateGraph(StewardState)
    builder.add_node("steward", steward)
    builder.add_node("human_approval", human_approval)
    builder.add_node("execute", execute)
    builder.add_edge(START, "steward")
    builder.add_conditional_edges(
        "steward",
        _route_after_steward,
        {"human_approval": "human_approval", "__end__": END},
    )
    return builder.compile(checkpointer=checkpointer)
