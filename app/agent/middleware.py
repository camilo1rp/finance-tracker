"""Transient per-call date that must not sit in the cached system prefix."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

DATE_CONTEXT_PREFIX = "Current date:"


def format_current_date(today: date) -> str:
    return f"{DATE_CONTEXT_PREFIX} {today.isoformat()} ({today.strftime('%A')})."


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        return str(block.get("text") or "")
    return ""


def _human_already_prefixed(message: HumanMessage) -> bool:
    content = message.content
    if isinstance(content, str):
        return content.startswith(DATE_CONTEXT_PREFIX)
    if isinstance(content, list) and content:
        return _block_text(content[0]).startswith(DATE_CONTEXT_PREFIX)
    return False


def _prefix_human(message: HumanMessage, date_text: str) -> HumanMessage:
    content = message.content
    if isinstance(content, str):
        return HumanMessage(content=f"{date_text}\n\n{content}")
    if isinstance(content, list):
        return HumanMessage(content=[{"type": "text", "text": date_text}, *content])
    return HumanMessage(content=f"{date_text}\n\n{content}")


class CurrentDateMiddleware(AgentMiddleware):
    """Prefix today's date onto the last user message for this model call only.
    Leaves system_message and earlier turns unchanged so the cached prefix stays stable.
    """

    def __init__(self, clock: Callable[[], date] | None = None) -> None:
        super().__init__()
        self.clock = clock or date.today

    def _with_date(self, request: ModelRequest) -> ModelRequest:
        messages = request.messages
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, HumanMessage):
                continue
            if _human_already_prefixed(message):
                return request
            prefixed = _prefix_human(message, format_current_date(self.clock()))
            return request.override(
                messages=[*messages[:index], prefixed, *messages[index + 1 :]]
            )
        return request

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
