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
from app.schemas import MappingOp, MappingPlanIn, parse_mapping_op
from app.services.mapping_preview_service import apply_mapping_plan, preview_mappings
from app.services.proposal_service import mark_consumed

STEWARD_PROMPT = """You clean up normalization mappings.
Workflow: fetch unmapped values → list_mappings for the kind (global, plus the account scope if relevant) → inspect examples → propose ops → always preview before submitting → submit the plan with the preview attached.
Rules may be global or scoped to an account; category rules may also be scoped to a merchant. Propose ops and submit plans that match the scope the user requested.
Never claim anything was applied; applying happens only after a human approves.

If preview reports conflicts_with_existing_id, submit an update on that mapping_id — never resubmit the create. Collapsing near-duplicate canonicals (e.g. Grocery/Groceries) is an update on the existing rule plus creates for other raw keys.

For transaction-specific corrections, use `set_transaction_category` only when a rule would be wrong because the change applies to one specific transaction, not the broader raw value. Cite `evidence_ids` when they exist. If preview shows `replace_conflict`, do not submit that plan — either drop the op or submit `remove_transaction_override` for that transaction earlier in the same plan and re-preview.

After execute, report created_ids, updated_ids, deleted_ids, and reclass_updated verbatim. If reclass_updated is 0 when changes were expected, say so explicitly; do not claim rows were updated.

When the task references a proposal id, call load_proposal first, preview the ops as given, drop or precede with remove_transaction_override any op the preview marks replace_conflict, do not add ops that are not in the proposal unless the task says so, and mention new_categories in the rationale so the approver sees them.
"""


def _route_after_steward(state: StewardState) -> Literal["human_approval", "__end__"]:
    if state.get("proposed_ops"):
        return "human_approval"
    return "__end__"


def _as_ops(ops: list) -> list[MappingOp]:
    return [parse_mapping_op(op) for op in ops]


def _recompute_preview(ops: list, account_id: int | None) -> dict:
    """Fresh preview for `ops`. Opens a short-lived session; never call across interrupt()."""
    parsed = _as_ops(ops)
    with tool_session() as db:
        preview = preview_mappings(
            db, MappingPlanIn(ops=parsed, account_id=account_id)
        )
    return preview.model_dump(mode="json")


def human_approval(state: StewardState) -> Command[Literal["steward", "execute"]]:
    submitted = state.get("proposed_ops") or []
    account_scope = state.get("account_scope")
    # Recompute from the ops actually in state. Do not trust pending_preview:
    # the model can submit a different set than it last previewed, or skip preview.
    # Close the session before interrupt() — this node restarts on resume and the
    # graph may sit paused for hours.
    preview = _recompute_preview(submitted, account_scope)
    payload = {
        "ops": submitted,
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
                "proposed_ops": [],
                "pending_preview": None,
                "rationale": None,
                "messages": [
                    HumanMessage(
                        content="Plan rejected. Nothing was applied. Propose a different plan if needed."
                    )
                ],
            },
        )
    ops = decision.get("ops")
    if ops is None:
        ops = submitted
    subset_preview = _recompute_preview(ops, account_scope)
    return Command(
        goto="execute",
        update={"proposed_ops": ops, "pending_preview": subset_preview},
    )


def _format_skipped(skipped: list) -> str:
    parts: list[str] = []
    for item in skipped:
        op = item.op
        parts.append(f"{op.op}:{item.reason}")
    return str(parts)


def execute(state: StewardState) -> Command[Literal["steward"]]:
    parsed = _as_ops(state.get("proposed_ops") or [])
    with tool_session() as db:
        result = apply_mapping_plan(
            db,
            MappingPlanIn(ops=parsed, account_id=state.get("account_scope")),
        )
        proposal_id = state.get("proposal_id")
        if proposal_id is not None:
            # Same session as apply. apply_mapping_plan already committed;
            # mark_consumed is a short follow-up write on this session.
            mark_consumed(db, proposal_id, f"execute:{proposal_id}")
            db.commit()
    summary = (
        f"Plan executed. created_ids={result.created_ids} "
        f"updated_ids={result.updated_ids} "
        f"deleted_ids={result.deleted_ids} "
        f"overrides_set={result.overrides_set} "
        f"overrides_removed={result.overrides_removed} "
        f"skipped={_format_skipped(result.skipped)} "
        f"reclass_scanned={result.reclass_scanned} "
        f"reclass_updated={result.reclass_updated}. "
        "Nothing else will be applied unless a new plan is submitted. "
        "Report created_ids, updated_ids, deleted_ids, overrides_set, overrides_removed, and reclass_updated verbatim. "
        "If reclass_updated is 0, say so explicitly; do not claim rows were updated."
    )
    return Command(
        goto="steward",
        update={
            "apply_result": result.model_dump(),
            "proposed_ops": [],
            "pending_preview": None,
            "rationale": None,
            "messages": [HumanMessage(content=summary)],
        },
    )


def build_steward_builder(*, model=None) -> StateGraph:
    """Uncompiled steward graph. Entrypoints decide whether to attach a checkpointer."""
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
    return builder


def build_steward_graph(*, model=None, checkpointer=None):
    """Compile the steward graph.

    Standalone CLI/tests pass a checkpointer. The coordinator path omits it so
    the subgraph inherits the parent's checkpointer and interrupt() propagates.
    """
    builder = build_steward_builder(model=model)
    if checkpointer is None:
        return builder.compile()
    return builder.compile(checkpointer=checkpointer)
