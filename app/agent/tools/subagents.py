"""Subagents wrapped as tools. History control lives in the wrapper, not the graph."""
from __future__ import annotations

from langchain.tools import ToolRuntime, tool


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
            f"overrides_set={apply_result.get('overrides_set')} "
            f"overrides_removed={apply_result.get('overrides_removed')} "
            f"skipped={skipped} "
            f"reclass_scanned={apply_result.get('reclass_scanned')} "
            f"reclass_updated={apply_result.get('reclass_updated')}"
        )
    for message in result.get("messages") or []:
        content = getattr(message, "content", "") or ""
        if isinstance(content, str) and "nothing was applied" in content.lower():
            return "rejected"
    return _last_text(result) or "nothing unmapped"


def _enricher_summary(result: dict) -> str:
    for message in reversed(result.get("messages") or []):
        if getattr(message, "type", None) != "tool":
            continue
        content = getattr(message, "content", "") or ""
        if isinstance(content, str) and content.startswith("proposal #"):
            return content
        name = getattr(message, "name", None)
        if name == "submit_recommendation" and isinstance(content, str):
            return content
    return _last_text(result)


def make_subagent_tools(*, analyst, steward, enricher):
    """Wrap compiled subagents. Invoke with a fresh user message; return the final text only."""

    @tool(
        "ask_analyst",
        description=(
            "Delegate insights and discovery: comparisons, trends, patterns, and "
            "anything that may be mislabeled or split across names. Include date "
            "ranges, owner/account ids, and whether refunds count. Use this when "
            "the question is open, not only as a last resort."
        ),
    )
    def ask_analyst(task: str, runtime: ToolRuntime) -> str:
        thread_id = "standalone"
        if runtime and hasattr(runtime, "config") and isinstance(runtime.config, dict):
            cfg = runtime.config
            configurable = cfg.get("configurable")
            if isinstance(configurable, dict) and configurable.get("thread_id"):
                thread_id = str(configurable["thread_id"])
            elif cfg.get("metadata") and isinstance(cfg["metadata"], dict) and cfg["metadata"].get("thread_id"):
                thread_id = str(cfg["metadata"]["thread_id"])

        result = analyst.invoke(
            {"messages": [{"role": "user", "content": task}]},
            config={"configurable": {"thread_id": thread_id}},
        )
        artifact_ids = result.get("artifact_ids")
        narrative = result.get("narrative")
        if artifact_ids is not None and narrative is not None:
            ids_str = ", ".join(str(i) for i in artifact_ids)
            return f"artifacts: [{ids_str}]. {narrative}".strip()
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

    @tool(
        "run_enricher",
        description=(
            "Delegate receipt research: what a purchase was, or enrich/research "
            "transactions from email. Include transaction ids or a merchant and "
            "YYYY-MM-DD date range in the task. Returns a proposal id; do not "
            "treat the result as applied changes."
        ),
    )
    def run_enricher(task: str) -> str:
        result = enricher.invoke(
            {"messages": [{"role": "user", "content": task}]},
            {"recursion_limit": 15},
        )
        return _enricher_summary(result)

    return [ask_analyst, run_data_steward, run_enricher]
