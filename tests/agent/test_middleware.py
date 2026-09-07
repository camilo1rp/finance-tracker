import re
from datetime import date

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.analyst import ANALYST_PROMPT
from app.agent.coordinator import COORDINATOR_PROMPT
from app.agent.middleware import (
    DATE_CONTEXT_PREFIX,
    LEDGER_CONTEXT_PREFIX,
    ArtifactOffloadMiddleware,
    CurrentDateMiddleware,
    LedgerSnapshotMiddleware,
    format_current_date,
    format_ledger_snapshot,
)
from langchain.agents.middleware import ModelRequest, ModelResponse, ToolCallRequest
from tests.agent.helpers import agent_sessions


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


def _ledger_text() -> str:
    return format_ledger_snapshot(
        {
            "transaction_count": 3,
            "merchant_count": 2,
            "owners": [{"id": 1, "name": "Pat"}],
            "categories": [
                {
                    "name": "Dining",
                    "count": 2,
                    "subcategories": [{"name": "Coffee", "count": 2}],
                },
                {"name": "Transport", "count": 1, "subcategories": []},
            ],
        }
    )


def test_format_ledger_snapshot_lists_taxonomy_and_counts() -> None:
    text = _ledger_text()
    assert text.startswith("Ledger snapshot: 3 transactions, 2 merchants.")
    assert "Owners: 1=Pat" in text
    assert "- Dining (2): Coffee (2)" in text
    assert "- Transport (1)" in text
    assert "Categories: none" not in text


def test_format_ledger_snapshot_empty() -> None:
    assert format_ledger_snapshot(
        {"transaction_count": 0, "merchant_count": 0, "owners": [], "categories": []}
    ) == (
        "Ledger snapshot: 0 transactions, 0 merchants.\n"
        "Owners: none\n"
        "Categories: none"
    )


def test_format_ledger_snapshot_singular_counts() -> None:
    text = format_ledger_snapshot(
        {"transaction_count": 1, "merchant_count": 1, "owners": [], "categories": []}
    )
    assert text.startswith("Ledger snapshot: 1 transaction, 1 merchant.")


def test_ledger_middleware_prefixes_last_human_and_leaves_system_untouched() -> None:
    middleware = LedgerSnapshotMiddleware(loader=_ledger_text)
    original = [
        HumanMessage(content="older turn"),
        AIMessage(content="ok"),
        HumanMessage(content="compare dining"),
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
    assert captured[0].messages[0].content == "older turn"
    assert captured[0].messages[-1].content.startswith(LEDGER_CONTEXT_PREFIX)
    assert captured[0].messages[-1].content.endswith("compare dining")
    assert "Owners: 1=Pat" in captured[0].messages[-1].content
    assert response.result[0].content == "ok"


def test_ledger_middleware_skips_second_inject() -> None:
    middleware = LedgerSnapshotMiddleware(loader=_ledger_text)
    request = _request(HumanMessage(content="subscriptions"))
    seen: list[list] = []

    def handler(req: ModelRequest) -> ModelResponse:
        seen.append(req.messages)
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(request, handler)
    injected = seen[0]
    second_request = request.override(messages=list(injected))
    middleware.wrap_model_call(second_request, handler)
    assert seen[1] is second_request.messages
    assert seen[1][0].content.count(LEDGER_CONTEXT_PREFIX) == 1


def test_date_then_ledger_inserts_snapshot_after_date() -> None:
    date_mw = CurrentDateMiddleware(clock=lambda: date(2026, 9, 2))
    ledger_mw = LedgerSnapshotMiddleware(loader=_ledger_text)
    request = _request(HumanMessage(content="compare dining"))
    captured: list[ModelRequest] = []

    def handler(req: ModelRequest) -> ModelResponse:
        captured.append(req)
        return ModelResponse(result=[AIMessage(content="ok")])

    def inner(req: ModelRequest) -> ModelResponse:
        return ledger_mw.wrap_model_call(req, handler)

    date_mw.wrap_model_call(request, inner)
    content = captured[0].messages[-1].content
    assert isinstance(content, str)
    assert content.startswith(
        "Current date: 2026-09-02 (Wednesday).\nLedger snapshot:"
    )
    assert content.endswith("\n\ncompare dining")
    assert "Owners: 1=Pat" in content
    assert "- Dining (2): Coffee (2)" in content

    second = request.override(messages=list(captured[0].messages))
    date_mw.wrap_model_call(second, inner)
    assert captured[1].messages[-1].content == content
    assert content.count(DATE_CONTEXT_PREFIX) == 1
    assert content.count(LEDGER_CONTEXT_PREFIX) == 1


def test_analyst_prompt_mentions_attached_snapshot() -> None:
    assert re.search(r"\d{4}-\d{2}-\d{2}", ANALYST_PROMPT) is None
    assert "ledger snapshot" in ANALYST_PROMPT.lower()
    assert "current calendar date" in ANALYST_PROMPT.lower()


def test_artifact_offload_middleware_under_threshold() -> None:
    mw = ArtifactOffloadMiddleware(max_tokens=100)
    req = ToolCallRequest(
        tool_call={"name": "test_tool", "args": {"a": 1}, "id": "tc1"},
        tool=None,
        state={},
        runtime=None,
    )
    msg = ToolMessage(content="short content", tool_call_id="tc1")
    res = mw.wrap_tool_call(req, lambda r: msg)
    assert res == msg


def test_artifact_offload_middleware_over_threshold(db_session, agent_sessions) -> None:
    mw = ArtifactOffloadMiddleware(max_tokens=20)
    req = ToolCallRequest(
        tool_call={"name": "test_tool", "args": {"query": "long"}, "id": "tc1"},
        tool=None,
        state={},
        runtime=None,
    )
    long_content = "word " * 100
    msg = ToolMessage(content=long_content, tool_call_id="tc1")
    res = mw.wrap_tool_call(req, lambda r: msg)
    assert isinstance(res, ToolMessage)
    assert "Output offloaded" in res.content
    assert res.artifact is not None
    assert res.artifact.get("offloaded") is True
    art_id = res.artifact["artifact_id"]
    from app.models import AnalysisArtifact

    stored = db_session.get(AnalysisArtifact, art_id)
    assert stored is not None
    assert stored.kind == "large_tool_output"


def test_artifact_offload_middleware_exempts_open_artifact(db_session, agent_sessions) -> None:
    mw = ArtifactOffloadMiddleware(max_tokens=20)
    req = ToolCallRequest(
        tool_call={"name": "open_artifact", "args": {"artifact_id": 1}, "id": "tc1"},
        tool=None,
        state={},
        runtime=None,
    )
    long_content = "word " * 100
    msg = ToolMessage(content=long_content, tool_call_id="tc1")
    res = mw.wrap_tool_call(req, lambda r: msg)
    assert res is msg
    assert "Output offloaded" not in res.content


def test_artifact_offload_middleware_exempts_submit_analysis(db_session, agent_sessions) -> None:
    mw = ArtifactOffloadMiddleware(max_tokens=20)
    req = ToolCallRequest(
        tool_call={"name": "submit_analysis", "args": {"artifact_ids": [1], "narrative": "ok"}, "id": "tc1"},
        tool=None,
        state={},
        runtime=None,
    )
    long_content = "word " * 100
    msg = ToolMessage(content=long_content, tool_call_id="tc1")
    res = mw.wrap_tool_call(req, lambda r: msg)
    assert res is msg
    assert "Output offloaded" not in res.content

