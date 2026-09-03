"""Subagents wrapped as tools. History control lives in the wrapper, not the graph."""
from __future__ import annotations

from langchain.tools import tool


def _last_text(result: dict) -> str:
    messages = result.get("messages") or []
    if not messages:
        return ""
    content = getattr(messages[-1], "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "".join(parts)
    return str(content)


def _steward_summary(result: dict) -> str:
    apply_result = result.get("apply_result")
    if apply_result:
        skipped = apply_result.get("skipped") or []
        return (
            "applied "
            f"created_ids={apply_result.get('created_ids')} "
            f"updated_ids={apply_result.get('updated_ids')} "
            f"deleted_ids={apply_result.get('deleted_ids')} "
            f"skipped={skipped} "
            f"reclass_scanned={apply_result.get('reclass_scanned')} "
            f"reclass_updated={apply_result.get('reclass_updated')}"
        )
    for message in result.get("messages") or []:
        content = getattr(message, "content", "") or ""
        if isinstance(content, str) and "nothing was applied" in content.lower():
            return "rejected"
    return _last_text(result) or "nothing unmapped"


def make_subagent_tools(*, analyst, steward):
    """Wrap compiled subagents. Invoke with a fresh user message; return the final text only."""

    @tool(
        "ask_analyst",
        description=(
            "Delegate spending analysis: comparisons across months/owners/accounts/"
            "merchants/categories, trends, largest or unusual transactions, description "
            "search. Include all relevant scope in the task: exact date ranges, "
            "owner/account ids, whether refunds should be included."
        ),
    )
    def ask_analyst(task: str) -> str:
        result = analyst.invoke({"messages": [{"role": "user", "content": task}]})
        return _last_text(result)

    @tool(
        "run_data_steward",
        description=(
            "Delegate normalization cleanup for types, categories, owners, and merchants — "
            "including account- or merchant-scoped rules when the user asks. Reviews unmapped "
            "values, proposes and previews mapping changes, and pauses for human approval "
            "before anything is applied."
        ),
    )
    def run_data_steward(task: str) -> str:
        result = steward.invoke({"messages": [{"role": "user", "content": task}]})
        return _steward_summary(result)

    return [ask_analyst, run_data_steward]
