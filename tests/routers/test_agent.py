from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy.orm import Session

from app.agent.config import in_memory_checkpointer
from app.agent.coordinator import build_coordinator
from app.agent.tools.enricher import email_source_status
from app.main import app
from app.models import AnalysisArtifact
from app.routers.agent import get_agent_graph
from tests.agent.helpers import OPS, ScriptedChatModel, agent_sessions, seed_coffee


def _idle(content: str = "idle") -> ScriptedChatModel:
    return ScriptedChatModel(responses=[AIMessage(content=content)])


def _build_test_coordinator(
    model: ScriptedChatModel,
    *,
    analyst_model: ScriptedChatModel | None = None,
    steward_model: ScriptedChatModel | None = None,
    enricher_model: ScriptedChatModel | None = None,
    checkpointer=None,
):
    return build_coordinator(
        model=model,
        analyst_model=analyst_model or _idle("ANALYST_IDLE"),
        steward_model=steward_model or _idle("STEWARD_IDLE"),
        enricher_model=enricher_model or _idle("ENRICHER_IDLE"),
        checkpointer=checkpointer or in_memory_checkpointer(),
    )


def test_agent_chat_non_interrupt(client: TestClient, db_session: Session, agent_sessions) -> None:
    seed_coffee(db_session)
    coord_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_total",
                        "args": {"date_from": "2024-06-01", "date_to": "2024-06-30"},
                        "id": "tot-1",
                    }
                ],
            ),
            AIMessage(content="Total spend is 4.50 in June."),
        ]
    )
    graph = _build_test_coordinator(coord_model)
    app.dependency_overrides[get_agent_graph] = lambda: graph

    try:
        resp = client.post(
            "/agent/chat",
            json={"thread_id": "t-chat-1", "message": "How much was spent in June?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == "t-chat-1"
        assert not data["interrupted"]
        assert data["interrupt"] is None
        assert len(data["messages"]) >= 2
        last_msg = data["messages"][-1]
        assert last_msg["role"] == "assistant"
        assert "Total spend is 4.50" in last_msg["content"]
    finally:
        app.dependency_overrides.pop(get_agent_graph, None)


def test_agent_chat_interrupt_and_resume_approve(
    client: TestClient, db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    coord_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_data_steward",
                        "args": {"task": "Map Food & Drink to Dining"},
                        "id": "stew-call-1",
                    }
                ],
            ),
            AIMessage(content="Applied mapping changes successfully."),
        ]
    )
    steward_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "ops": OPS,
                            "account_id": None,
                            "rationale": "map coffee to dining",
                        },
                        "id": "submit-call-1",
                    }
                ],
            ),
            AIMessage(content="Steward finished applying."),
        ]
    )
    shared_checkpointer = in_memory_checkpointer()
    graph = _build_test_coordinator(
        coord_model,
        steward_model=steward_model,
        checkpointer=shared_checkpointer,
    )
    app.dependency_overrides[get_agent_graph] = lambda: graph

    try:
        # 1. Chat triggers interrupt
        resp = client.post(
            "/agent/chat",
            json={"thread_id": "t-interrupt-1", "message": "Clean up unmapped"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["interrupted"] is True
        interrupt = data["interrupt"]
        assert interrupt is not None

        # Verify all four interrupt keys per acceptance criteria
        assert "ops" in interrupt
        assert "preview" in interrupt
        assert "preview_artifact_id" in interrupt
        assert "rationale" in interrupt
        assert interrupt["rationale"] == "map coffee to dining"

        # Verify preview includes overrides key
        assert "overrides" in interrupt["preview"]
        assert "ops" in interrupt["preview"]
        assert interrupt["preview_artifact_id"] is not None

        # Verify artifacts list includes the preview artifact
        preview_art_id = interrupt["preview_artifact_id"]
        art_ids = [a["artifact_id"] for a in data["artifacts"]]
        assert preview_art_id in art_ids

        # 2. Rehydrate state from GET /agent/thread/{id}/state
        state_resp = client.get("/agent/thread/t-interrupt-1/state")
        assert state_resp.status_code == 200
        state_data = state_resp.json()
        assert state_data["thread_id"] == "t-interrupt-1"
        assert state_data["interrupted"] is True
        assert state_data["interrupt"]["rationale"] == "map coffee to dining"
        assert state_data["interrupt"]["preview_artifact_id"] == preview_art_id

        # 3. Resume with approve subset (sending op objects, not indices)
        subset_ops = [OPS[0]]
        resume_resp = client.post(
            "/agent/resume",
            json={
                "thread_id": "t-interrupt-1",
                "decision": "approve",
                "ops": subset_ops,
            },
        )
        assert resume_resp.status_code == 200
        resume_data = resume_resp.json()
        assert resume_data["interrupted"] is False
        assert resume_data["interrupt"] is None
        last_content = resume_data["messages"][-1]["content"]
        assert "Applied mapping changes successfully." in last_content

        # 4. State after resume should no longer be interrupted
        state_after = client.get("/agent/thread/t-interrupt-1/state").json()
        assert not state_after["interrupted"]
        assert state_after["interrupt"] is None

    finally:
        app.dependency_overrides.pop(get_agent_graph, None)


def test_agent_resume_reject(client: TestClient, db_session: Session, agent_sessions) -> None:
    seed_coffee(db_session)
    coord_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_data_steward",
                        "args": {"task": "Map categories"},
                        "id": "stew-call-2",
                    }
                ],
            ),
            AIMessage(content="Rejected plan. Nothing was applied."),
        ]
    )
    steward_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {"ops": OPS, "account_id": None, "rationale": "testing rejection"},
                        "id": "submit-call-2",
                    }
                ],
            ),
            AIMessage(content="Steward idle."),
        ]
    )
    graph = _build_test_coordinator(
        coord_model,
        steward_model=steward_model,
        checkpointer=in_memory_checkpointer(),
    )
    app.dependency_overrides[get_agent_graph] = lambda: graph

    try:
        chat_resp = client.post(
            "/agent/chat",
            json={"thread_id": "t-reject-1", "message": "Propose mapping"},
        )
        assert chat_resp.json()["interrupted"] is True

        resume_resp = client.post(
            "/agent/resume",
            json={"thread_id": "t-reject-1", "decision": "reject"},
        )
        assert resume_resp.status_code == 200
        data = resume_resp.json()
        assert data["interrupted"] is False
        assert data["interrupt"] is None
    finally:
        app.dependency_overrides.pop(get_agent_graph, None)


def test_agent_thread_isolation(client: TestClient, db_session: Session, agent_sessions) -> None:
    seed_coffee(db_session)
    coord_model = ScriptedChatModel(
        responses=[
            AIMessage(content="Response for thread 1"),
            AIMessage(content="Response for thread 2"),
        ]
    )
    graph = _build_test_coordinator(coord_model, checkpointer=in_memory_checkpointer())
    app.dependency_overrides[get_agent_graph] = lambda: graph

    try:
        r1 = client.post("/agent/chat", json={"thread_id": "thread-alpha", "message": "Alpha 1"})
        assert r1.status_code == 200
        r2 = client.post("/agent/chat", json={"thread_id": "thread-beta", "message": "Beta 1"})
        assert r2.status_code == 200

        state1 = client.get("/agent/thread/thread-alpha/state").json()
        state2 = client.get("/agent/thread/thread-beta/state").json()

        assert state1["thread_id"] == "thread-alpha"
        assert state2["thread_id"] == "thread-beta"

        contents1 = [m["content"] for m in state1["messages"]]
        contents2 = [m["content"] for m in state2["messages"]]

        assert "Alpha 1" in contents1
        assert "Beta 1" not in contents1
        assert "Beta 1" in contents2
        assert "Alpha 1" not in contents2

    finally:
        app.dependency_overrides.pop(get_agent_graph, None)


def test_agent_analyst_artifacts_scoped_to_thread(
    client: TestClient, db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    coord_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_analyst",
                        "args": {"task": "Summarize June spend by category"},
                        "id": "analyst-call-1",
                    }
                ],
            ),
            AIMessage(content="Analyst completed June category summary."),
        ]
    )
    analyst_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize",
                        "args": {
                            "group_by": "category",
                            "date_from": "2024-06-01",
                            "date_to": "2024-06-30",
                        },
                        "id": "sum-call-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_analysis",
                        "args": {
                            "artifact_ids": [1],
                            "narrative": "June spending was 4.50 in Food & Drink.",
                        },
                        "id": "sub-analysis-1",
                    }
                ],
            ),
            AIMessage(content="Analysis submitted."),
        ]
    )
    graph = _build_test_coordinator(
        coord_model,
        analyst_model=analyst_model,
        checkpointer=in_memory_checkpointer(),
    )
    app.dependency_overrides[get_agent_graph] = lambda: graph

    try:
        resp = client.post(
            "/agent/chat",
            json={"thread_id": "thread-analyst-scope", "message": "Analyze June"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert not data["interrupted"]
        assert len(data["artifacts"]) >= 1
        art = data["artifacts"][0]
        assert art["kind"] == "group_summary"
        assert art["title"] == "Summary by category"
        assert "artifact_id" in art

        # Verify DB row is scoped to thread-analyst-scope
        db_art = db_session.get(AnalysisArtifact, art["artifact_id"])
        assert db_art is not None
        assert db_art.thread_id == "thread-analyst-scope"

    finally:
        app.dependency_overrides.pop(get_agent_graph, None)


def test_agent_email_source_status_endpoint(client: TestClient) -> None:
    resp = client.get("/agent/email-source-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "provider" in data
    assert "available" in data
    # In test environment, EMAIL_PROVIDER defaults to none (or gmail_rest if configured in env)
    assert isinstance(data["available"], bool)


def test_agent_chat_stream_sse(client: TestClient, db_session: Session, agent_sessions) -> None:
    seed_coffee(db_session)
    coord_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_total",
                        "args": {"date_from": "2024-06-01", "date_to": "2024-06-30"},
                        "id": "stream-tot-1",
                    }
                ],
            ),
            AIMessage(content="Streaming total: 4.50"),
        ]
    )
    graph = _build_test_coordinator(coord_model, checkpointer=in_memory_checkpointer())
    app.dependency_overrides[get_agent_graph] = lambda: graph

    try:
        resp = client.post(
            "/agent/chat/stream",
            json={"thread_id": "thread-stream-1", "message": "Stream June total"},
        )
        assert resp.status_code == 200
        text = resp.text
        assert "event: tool" in text
        assert "event: done" in text
        assert "Streaming total: 4.50" in text
    finally:
        app.dependency_overrides.pop(get_agent_graph, None)


def test_agent_ui_filters_provisional_and_superseded_artifacts(
    client: TestClient, db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    coord_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_analyst",
                        "args": {"task": "Explore categories and summarize June"},
                        "id": "analyst-call-1",
                    }
                ],
            ),
            AIMessage(content="Analyst completed June summary."),
        ]
    )
    analyst_model = ScriptedChatModel(
        responses=[
            # Probe 1: list_values
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_values",
                        "args": {"dimension": "category"},
                        "id": "probe-call-1",
                    }
                ],
            ),
            # Probe 2 / Deliverable: summarize
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize",
                        "args": {
                            "group_by": "category",
                            "date_from": "2024-06-01",
                            "date_to": "2024-06-30",
                        },
                        "id": "sum-call-1",
                    }
                ],
            ),
            # Finish: submit_analysis citing only the summarize deliverable
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_analysis",
                        "args": {
                            "artifact_ids": [2],
                            "narrative": "June spending was 4.50 in Food & Drink.",
                        },
                        "id": "sub-analysis-1",
                    }
                ],
            ),
            AIMessage(content="Analysis submitted."),
        ]
    )
    graph = _build_test_coordinator(
        coord_model,
        analyst_model=analyst_model,
        checkpointer=in_memory_checkpointer(),
    )
    app.dependency_overrides[get_agent_graph] = lambda: graph

    try:
        resp = client.post(
            "/agent/chat",
            json={"thread_id": "thread-filter-test", "message": "Explore and summarize"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert not data["interrupted"]
        # Only the cited deliverable is returned, NOT the uncited probe
        assert len(data["artifacts"]) == 1
        art = data["artifacts"][0]
        assert art["artifact_id"] == 2
        assert art["kind"] == "group_summary"

        # Check thread state rehydration endpoint
        state_resp = client.get("/agent/thread/thread-filter-test/state")
        assert state_resp.status_code == 200
        state_data = state_resp.json()
        assert len(state_data["artifacts"]) == 1
        assert state_data["artifacts"][0]["artifact_id"] == 2

        # Check DB states
        probe_art = db_session.get(AnalysisArtifact, 1)
        deliverable_art = db_session.get(AnalysisArtifact, 2)
        assert probe_art is not None
        assert deliverable_art is not None
        assert probe_art.status == "superseded"
        assert deliverable_art.status == "open"

    finally:
        app.dependency_overrides.pop(get_agent_graph, None)


def test_agent_offload_ephemeral_excluded_from_thread_artifacts(
    client: TestClient, db_session: Session
) -> None:
    from app.services import artifact_service

    # Seed an ephemeral offload artifact directly in the thread
    art_id, _ = artifact_service.persist_artifact(
        db_session,
        thread_id="thread-offload-test",
        kind="large_tool_output",
        title="Offloaded tool output",
        spec={"tool": "list_mappings", "kwargs": {}},
        result={"raw": "lots of data"},
        produced_by="analyst",
        status="ephemeral",
        expires_in_seconds=86400,
    )
    # Rehydration should ignore ephemeral artifacts
    state_resp = client.get("/agent/thread/thread-offload-test/state")
    assert state_resp.status_code == 200
    state_data = state_resp.json()
    assert not any(a["artifact_id"] == art_id for a in state_data["artifacts"])

    # But GET /artifacts/{id} still works for JIT inspection
    art_resp = client.get(f"/artifacts/{art_id}")
    assert art_resp.status_code == 200
    assert art_resp.json()["id"] == art_id

