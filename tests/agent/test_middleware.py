import re
from datetime import date

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.coordinator import COORDINATOR_PROMPT
from app.agent.middleware import (
    DATE_CONTEXT_PREFIX,
    CurrentDateMiddleware,
    format_current_date,
)


def _request(*messages, system: str = "frozen") -> ModelRequest:
    return ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="ok")]),
        messages=list(messages),
        system_message=SystemMessage(content=system),
    )


def test_format_current_date_includes_iso_and_weekday() -> None:
    assert format_current_date(date(2026, 9, 2)) == "Current date: 2026-09-02 (Wednesday)."


def test_wrap_model_call_prefixes_last_human_and_leaves_system_untouched() -> None:
    middleware = CurrentDateMiddleware(clock=lambda: date(2026, 9, 2))
    original = [
        HumanMessage(content="older turn"),
        AIMessage(content="ok"),
        HumanMessage(content="cuanto gaste hoy?"),
    ]
    request = _request(*original)
    captured: list[ModelRequest] = []

    def handler(req: ModelRequest) -> ModelResponse:
        captured.append(req)
        return ModelResponse(result=[AIMessage(content="ok")])

    response = middleware.wrap_model_call(request, handler)

    assert captured[0] is not request
    assert request.messages == original
    assert captured[0].system_message is request.system_message
    assert captured[0].system_message is not None
    assert captured[0].system_message.content == "frozen"
    assert captured[0].messages[0].content == "older turn"
    assert captured[0].messages[-1].content == (
        "Current date: 2026-09-02 (Wednesday).\n\ncuanto gaste hoy?"
    )
    assert len(captured[0].messages) == len(original)
    assert response.result[0].content == "ok"


def test_wrap_model_call_skips_second_inject() -> None:
    middleware = CurrentDateMiddleware(clock=lambda: date(2026, 9, 2))
    request = _request(HumanMessage(content="last month"))
    seen: list[list] = []

    def handler(req: ModelRequest) -> ModelResponse:
        seen.append(req.messages)
        return ModelResponse(result=[AIMessage(content="ok")])

    first = middleware.wrap_model_call(request, handler)
    assert first.result[0].content == "ok"
    injected = seen[0]
    assert len(injected) == 1
    assert injected[0].content.startswith(DATE_CONTEXT_PREFIX)
    second_request = request.override(messages=list(injected))
    middleware.wrap_model_call(second_request, handler)
    assert seen[1] is second_request.messages
    assert seen[1][0].content == injected[0].content


def test_wrap_model_call_prefixes_human_before_tool_result() -> None:
    middleware = CurrentDateMiddleware(clock=lambda: date(2026, 9, 2))
    request = _request(
        HumanMessage(content="yesterday"),
        AIMessage(content="", tool_calls=[{"name": "get_total", "args": {}, "id": "t1"}]),
        ToolMessage(content="{}", tool_call_id="t1"),
    )
    captured: list[ModelRequest] = []

    def handler(req: ModelRequest) -> ModelResponse:
        captured.append(req)
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(request, handler)
    assert captured[0].messages[0].content.startswith(DATE_CONTEXT_PREFIX)
    assert captured[0].messages[-1].type == "tool"
    assert captured[0].system_message is request.system_message


def test_coordinator_prompt_has_no_interpolated_date() -> None:
    assert re.search(r"\d{4}-\d{2}-\d{2}", COORDINATOR_PROMPT) is None
    assert "current calendar date" in COORDINATOR_PROMPT.lower()
