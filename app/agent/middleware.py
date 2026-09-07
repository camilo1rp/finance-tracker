"""Transient per-call context that must not sit in the cached system prefix."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain.agents.middleware.summarization import count_tokens_approximately
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

DATE_CONTEXT_PREFIX = "Current date:"
LEDGER_CONTEXT_PREFIX = "Ledger snapshot:"


def format_current_date(today: date) -> str:
    return f"{DATE_CONTEXT_PREFIX} {today.isoformat()} ({today.strftime('%A')})."


def format_ledger_snapshot(snapshot: dict[str, Any]) -> str:
    txn_n = snapshot["transaction_count"]
    merchant_n = snapshot["merchant_count"]
    txn_label = "transaction" if txn_n == 1 else "transactions"
    merchant_label = "merchant" if merchant_n == 1 else "merchants"
    lines = [
        f"{LEDGER_CONTEXT_PREFIX} {txn_n} {txn_label}, {merchant_n} {merchant_label}."
    ]
    owners = snapshot.get("owners") or []
    if owners:
        owner_bits = "; ".join(f"{owner['id']}={owner['name']}" for owner in owners)
        lines.append(f"Owners: {owner_bits}")
    else:
        lines.append("Owners: none")
    categories = snapshot.get("categories") or []
    if not categories:
        lines.append("Categories: none")
        return "\n".join(lines)
    lines.append("Categories:")
    for category in categories:
        name = category["name"]
        count = category["count"]
        subcategories = category.get("subcategories") or []
        if not subcategories:
            lines.append(f"- {name} ({count})")
            continue
        sub_bits = ", ".join(
            f"{sub['name']} ({sub['count']})" for sub in subcategories
        )
        lines.append(f"- {name} ({count}): {sub_bits}")
    return "\n".join(lines)


def _load_ledger_snapshot_text() -> str:
    from app.agent.config import tool_session
    from app.services.analytics_service import ledger_snapshot

    with tool_session() as db:
        return format_ledger_snapshot(ledger_snapshot(db))


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        return str(block.get("text") or "")
    return ""


def _human_has_prefix(message: HumanMessage, prefix: str) -> bool:
    content = message.content
    if isinstance(content, str):
        return content.startswith(prefix) or f"\n{prefix}" in content
    if isinstance(content, list) and content:
        return any(_block_text(block).startswith(prefix) for block in content)
    return False


def _human_already_prefixed(message: HumanMessage) -> bool:
    return _human_has_prefix(message, DATE_CONTEXT_PREFIX)


def _prefix_human(message: HumanMessage, text: str) -> HumanMessage:
    content = message.content
    if isinstance(content, str):
        return HumanMessage(content=f"{text}\n\n{content}")
    if isinstance(content, list):
        return HumanMessage(content=[{"type": "text", "text": text}, *content])
    return HumanMessage(content=f"{text}\n\n{content}")


def _insert_after_date(message: HumanMessage, text: str) -> HumanMessage:
    content = message.content
    if isinstance(content, str):
        if content.startswith(DATE_CONTEXT_PREFIX):
            date_line, sep, rest = content.partition("\n\n")
            if sep:
                return HumanMessage(content=f"{date_line}\n{text}\n\n{rest}")
            return HumanMessage(content=f"{content}\n{text}")
        return HumanMessage(content=f"{text}\n\n{content}")
    if isinstance(content, list):
        if content and _block_text(content[0]).startswith(DATE_CONTEXT_PREFIX):
            return HumanMessage(
                content=[content[0], {"type": "text", "text": text}, *content[1:]]
            )
        return HumanMessage(content=[{"type": "text", "text": text}, *content])
    return HumanMessage(content=f"{text}\n\n{content}")


def _map_last_human(
    request: ModelRequest,
    mutate: Callable[[HumanMessage], HumanMessage | None],
) -> ModelRequest:
    messages = request.messages
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, HumanMessage):
            continue
        updated = mutate(message)
        if updated is None:
            return request
        return request.override(
            messages=[*messages[:index], updated, *messages[index + 1 :]]
        )
    return request


class CurrentDateMiddleware(AgentMiddleware):
    """Prefix today's date onto the last user message for this model call only.
    Leaves system_message and earlier turns unchanged so the cached prefix stays stable.
    """

    def __init__(self, clock: Callable[[], date] | None = None) -> None:
        super().__init__()
        self.clock = clock or date.today

    def _with_date(self, request: ModelRequest) -> ModelRequest:
        date_text = format_current_date(self.clock())

        def mutate(message: HumanMessage) -> HumanMessage | None:
            if _human_already_prefixed(message):
                return None
            return _prefix_human(message, date_text)

        return _map_last_human(request, mutate)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._with_date(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._with_date(request))


class LedgerSnapshotMiddleware(AgentMiddleware):
    """Prefix a compact ledger catalog onto the last user message for this call only.

    Same injection point as CurrentDateMiddleware: last HumanMessage of this model
    call, not the system prompt. Inserts after the date line when date is already
    present. Analyst-only.
    """

    def __init__(self, loader: Callable[[], str] | None = None) -> None:
        super().__init__()
        self.loader = loader or _load_ledger_snapshot_text

    def _with_ledger(self, request: ModelRequest) -> ModelRequest:
        def mutate(message: HumanMessage) -> HumanMessage | None:
            if _human_has_prefix(message, LEDGER_CONTEXT_PREFIX):
                return None
            return _insert_after_date(message, self.loader())

        return _map_last_human(request, mutate)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._with_ledger(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._with_ledger(request))


OFFLOAD_EXEMPT_TOOLS = frozenset({"open_artifact", "submit_analysis"})


class ArtifactOffloadMiddleware(AgentMiddleware):
    """Intercept tool execution: if tool output content exceeds token threshold, offload to artifact store."""

    def __init__(self, max_tokens: int = 2000) -> None:
        super().__init__()
        self.max_tokens = max_tokens

    def _maybe_offload(
        self,
        request: ToolCallRequest,
        response: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        if not isinstance(response, ToolMessage):
            return response

        tool_call = request.tool_call or {}
        tool_name = tool_call.get("name", "tool")
        if tool_name in OFFLOAD_EXEMPT_TOOLS:
            return response

        approx_tokens = count_tokens_approximately([response])
        if approx_tokens <= self.max_tokens:
            return response

        from app.agent.config import tool_session
        from app.services import artifact_service

        tool_args = tool_call.get("args", {})
        thread_id = "standalone"
        if request.runtime and hasattr(request.runtime, "config") and isinstance(request.runtime.config, dict):
            configurable = request.runtime.config.get("configurable") or {}
            if isinstance(configurable, dict) and configurable.get("thread_id"):
                thread_id = str(configurable["thread_id"])

        content_str = str(response.content or "")
        with tool_session() as db:
            art_id, digest = artifact_service.persist_artifact(
                db,
                thread_id=thread_id,
                kind="large_tool_output",
                title=f"Offloaded {tool_name} output ({approx_tokens} tokens)",
                spec={"tool": tool_name, "kwargs": tool_args},
                result={"raw": content_str},
                produced_by="analyst",
            )

        new_content = (
            f"{digest}\n\n[Output offloaded ({approx_tokens} tokens > {self.max_tokens} limit). "
            f"Artifact #{art_id} stored. Use open_artifact(artifact_id={art_id}) to inspect subsets.]"
        )
        return ToolMessage(
            content=new_content,
            tool_call_id=response.tool_call_id,
            artifact={"artifact_id": art_id, "kind": "large_tool_output", "offloaded": True},
            status=response.status,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return self._maybe_offload(request, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return self._maybe_offload(request, await handler(request))

