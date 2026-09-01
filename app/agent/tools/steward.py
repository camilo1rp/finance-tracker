"""Preview and plan-submission tools. There is no tool that applies mappings."""
from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agent.config import tool_session
from app.schemas import ProposedMappingIn
from app.services.mapping_preview_service import preview_mappings

_PREVIEW_DESCRIPTION = """\
Preview proposed normalization mapping rules against stored transactions.
Performs no writes.

Domain quirks you must respect:
- Raw values are matched trimmed + lowercased.
- Category precedence is account+merchant → account → global+merchant → global,
  so a proposed global rule can be shadowed by an existing account rule
  (check shadowed_by_existing).
- Reclassify never touches category_override or merchant_override.
- Transaction type is recalculated only for rows with raw_type.
- Owner is recalculated only for rows with owner_raw.
"""


def _as_rules(rules: list[ProposedMappingIn] | list[dict]) -> list[ProposedMappingIn]:
    parsed: list[ProposedMappingIn] = []
    for rule in rules:
        if isinstance(rule, ProposedMappingIn):
            parsed.append(rule)
        else:
            parsed.append(ProposedMappingIn.model_validate(rule))
    return parsed


@tool(description=_PREVIEW_DESCRIPTION)
def preview_mapping_rules(
    rules: list[dict],
    account_id: int | None = None,
) -> str:
    parsed = _as_rules(rules)
    with tool_session() as db:
        preview = preview_mappings(db, parsed, account_id=account_id)
    return preview.model_dump_json()


@tool(return_direct=True)
def submit_plan(
    rules: list[dict],
    rationale: str,
    runtime: ToolRuntime,
    account_id: int | None = None,
) -> Command:
    """Submit a mapping plan for human approval. Does not apply anything.

    Always preview first. The submitted rules are paused for approval; applying
    happens only after a human resumes the graph.
    """
    parsed = _as_rules(rules)
    with tool_session() as db:
        preview = preview_mappings(db, parsed, account_id=account_id)
    serialized = [rule.model_dump() for rule in parsed]
    return Command(
        update={
            "proposed_rules": serialized,
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


STEWARD_TOOLS = [preview_mapping_rules, submit_plan]
