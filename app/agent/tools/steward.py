"""Preview and plan-submission tools. There is no tool that applies mappings."""
from __future__ import annotations

import json

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agent.config import tool_session
from app.schemas import MappingOp, MappingPlanIn, parse_mapping_op
from app.services.mapping_preview_service import preview_mappings
from app.services.proposal_service import load_proposal as fetch_proposal
from app.services.proposal_service import proposal_to_ops

_PREVIEW_DESCRIPTION = """\
Preview a mapping plan against stored transactions. Performs no writes.

ops is a list of create / update / delete / set_transaction_category / remove_transaction_override operations:
- create: {op: "create", kind, raw_value, canonical_value, account_id?, merchant?}
- update: {op: "update", mapping_id, canonical_value}  (changes an existing rule)
- delete: {op: "delete", mapping_id}
- set_transaction_category: {op: "set_transaction_category", transaction_id, category, evidence_ids?, rationale?}
- remove_transaction_override: {op: "remove_transaction_override", transaction_id, rationale?}

Identity of a create is (kind, cleaned raw_value, account_id, merchant).
If that identity exists with the same canonical, preview sets duplicate_of_existing_id.
If it exists with a different canonical, preview sets conflicts_with_existing_id —
do not resubmit the create; submit an update on that mapping_id instead.

Domain quirks you must respect:
- Raw values are matched trimmed + lowercased.
- Category and transaction_type precedence is account+merchant → account →
  global+merchant → global, so a proposed global rule can be shadowed by an
  existing account rule (check shadowed_by_existing). merchant is valid on
  category and transaction_type only. Type merchant scope also matches a
  space-bounded prefix (merchant=western union hits "western union capture…").
  Merchant kind raw_value uses the same prefix: one "western union" alias
  covers "western union capture 623… web id: …" — do not create one rule
  per capture id.
- Transaction overrides write `Transaction.category_override`; preview reports them under
  `overrides`, not under the rule-impact list.
- Reclassify never touches category_override, merchant_override, or type_override.
- Transaction type is always recalculated on reclassify (lookup if raw_type, else sign+account_kind when sign_convention is set). Type mapping ops still only impact rows with raw_type.
- Owner is recalculated only for rows with owner_raw.
"""


def _as_ops(ops: list[MappingOp] | list[dict]) -> list[MappingOp]:
    return [parse_mapping_op(op) for op in ops]


@tool(description=_PREVIEW_DESCRIPTION)
def preview_mapping_rules(
    ops: list[dict],
    account_id: int | None = None,
) -> str:
    parsed = _as_ops(ops)
    with tool_session() as db:
        preview = preview_mappings(
            db, MappingPlanIn(ops=parsed, account_id=account_id)
        )
    return preview.model_dump_json()


@tool(return_direct=True)
def submit_plan(
    ops: list[dict],
    rationale: str,
    runtime: ToolRuntime,
    account_id: int | None = None,
) -> Command:
    """Submit a mapping plan for human approval. Does not apply anything.

    Always preview first. The submitted ops are paused for approval; applying
    happens only after a human resumes the graph.
    """
    parsed = _as_ops(ops)
    with tool_session() as db:
        preview = preview_mappings(
            db, MappingPlanIn(ops=parsed, account_id=account_id)
        )
    serialized = [op.model_dump() for op in parsed]
    return Command(
        update={
            "proposed_ops": serialized,
            "account_scope": account_id,
            "pending_preview": preview.model_dump(),
            "rationale": rationale,
            "messages": [
                ToolMessage(
                    content=(
                        "Plan submitted for approval. Nothing has been applied. "
                        + preview.model_dump_json()
                    ),
                    tool_call_id=runtime.tool_call_id or "",
                )
            ],
        }
    )


_LOAD_PROPOSAL_DESCRIPTION = """\
Load a stored enrichment proposal by id.

Returns the proposal narrative, new_categories, an unresolved summary, and the mapping ops derived from the proposal. Pass those ops unchanged to preview_mapping_rules and submit_plan.

Does not apply anything. A missing id returns an error string.
"""


@tool(description=_LOAD_PROPOSAL_DESCRIPTION)
def load_proposal(proposal_id: int, runtime: ToolRuntime) -> Command | str:
    with tool_session() as db:
        proposal = fetch_proposal(db, proposal_id)
        if proposal is None:
            return f"No enrichment proposal with id {proposal_id}."
        rec = proposal.recommendation if isinstance(proposal.recommendation, dict) else {}
        ops = proposal_to_ops(proposal)
        payload = {
            "proposal_id": proposal.id,
            "narrative": rec.get("narrative"),
            "new_categories": rec.get("new_categories") or [],
            "unresolved": rec.get("unresolved") or [],
            "ops": ops,
        }
    return Command(
        update={
            "proposal_id": proposal.id,
            "messages": [
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id=runtime.tool_call_id or "",
                )
            ],
        }
    )


STEWARD_TOOLS = [preview_mapping_rules, submit_plan, load_proposal]
