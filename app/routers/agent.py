"""Agent HTTP router: chat turns, resumes, state inspection, and streaming transport."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import BaseMessage
from langgraph.types import Command
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.agent.config import email_provider, get_enricher_deps, in_memory_checkpointer
from app.agent.coordinator import build_coordinator
from app.database import get_session
from app.models import AnalysisArtifact
from app.schemas import (
    AgentChatIn,
    AgentResumeIn,
    AgentThreadStateOut,
    AgentTurnOut,
    ChatMessageOut,
    EmailSourceStatusOut,
    InterruptPayloadOut,
    TurnArtifactOut,
)

router = APIRouter(prefix="/agent", tags=["agent"])

_thread_locks: dict[str, asyncio.Lock] = {}
_thread_locks_guard = asyncio.Lock()


async def get_thread_lock(thread_id: str) -> asyncio.Lock:
    """Serialize turns per thread_id to respect non-concurrency invariant §15."""
    async with _thread_locks_guard:
        if thread_id not in _thread_locks:
            _thread_locks[thread_id] = asyncio.Lock()
        return _thread_locks[thread_id]


def get_agent_graph(request: Request) -> Any:
    """Retrieve compiled coordinator graph from app state, with test fallback."""
    graph = getattr(request.app.state, "coordinator_graph", None)
    if graph is not None:
        return graph
    if not hasattr(request.app.state, "_fallback_agent_graph"):
        request.app.state._fallback_agent_graph = build_coordinator(
            checkpointer=in_memory_checkpointer()
        )
    return request.app.state._fallback_agent_graph


def serialize_message(m: Any) -> ChatMessageOut:
    """Convert LangChain BaseMessage to ChatMessageOut schema."""
    role = getattr(m, "type", None) or getattr(m, "role", "assistant")
    if role == "human":
        role = "user"
    elif role == "ai":
        role = "assistant"

    content = getattr(m, "content", "")
    msg_id = getattr(m, "id", None)
    name = getattr(m, "name", None)
    tool_calls = getattr(m, "tool_calls", None)

    return ChatMessageOut(
        role=role,
        content=content,
        id=str(msg_id) if msg_id is not None else None,
        name=str(name) if name is not None else None,
        tool_calls=tool_calls if isinstance(tool_calls, list) else None,
    )


def extract_interrupt(result_or_state: dict[str, Any]) -> tuple[bool, InterruptPayloadOut | None, int | None]:
    """Extract pending interrupt from LangGraph result dict or state."""
    interrupts = result_or_state.get("__interrupt__") or ()
    if not interrupts:
        return False, None, None
    raw = interrupts[0].value
    if not isinstance(raw, dict):
        return True, InterruptPayloadOut(ops=[], preview={}), None

    preview_artifact_id = raw.get("preview_artifact_id")
    payload = InterruptPayloadOut(
        ops=raw.get("ops") or [],
        preview=raw.get("preview") or {},
        preview_artifact_id=preview_artifact_id,
        rationale=raw.get("rationale"),
    )
    return True, payload, preview_artifact_id


def get_turn_artifacts(
    db: Session,
    thread_id: str,
    watermark: int,
    preview_artifact_id: int | None = None,
) -> list[TurnArtifactOut]:
    """Retrieve AnalysisArtifact rows created above watermark for this thread."""
    if preview_artifact_id is not None:
        db.execute(
            update(AnalysisArtifact)
            .where(
                AnalysisArtifact.id == preview_artifact_id,
                AnalysisArtifact.thread_id == "steward",
            )
            .values(thread_id=thread_id)
        )
        db.commit()

    stmt = (
        select(AnalysisArtifact)
        .where(
            AnalysisArtifact.id > watermark,
            AnalysisArtifact.thread_id == thread_id,
            AnalysisArtifact.status == "open",
        )
        .order_by(AnalysisArtifact.id.asc())
    )
    rows = list(db.scalars(stmt).all())
    result = [
        TurnArtifactOut(
            artifact_id=r.id,
            kind=r.kind,
            title=r.title,
            digest=r.digest,
            spec=r.spec,
            data=r.cache,
        )
        for r in rows
    ]

    if preview_artifact_id is not None and not any(a.artifact_id == preview_artifact_id for a in result):
        preview_art = db.get(AnalysisArtifact, preview_artifact_id)
        if preview_art is not None:
            result.append(
                TurnArtifactOut(
                    artifact_id=preview_art.id,
                    kind=preview_art.kind,
                    title=preview_art.title,
                    digest=preview_art.digest,
                    spec=preview_art.spec,
                    data=preview_art.cache,
                )
            )
    return result


def get_email_status() -> EmailSourceStatusOut:
    deps = get_enricher_deps()
    source = deps.source_factory()
    if source is None:
        return EmailSourceStatusOut(
            provider=email_provider(),
            available=False,
            detail=f"EMAIL_PROVIDER is {email_provider()}",
        )
    status_obj = source.health()
    return EmailSourceStatusOut(
        provider=status_obj.provider,
        available=status_obj.available,
        account_hint=status_obj.account_hint,
        detail=status_obj.detail,
    )


@router.get("/email-source-status", response_model=EmailSourceStatusOut)
def email_source_status_endpoint() -> EmailSourceStatusOut:
    """Report email source availability without requiring a turn invocation."""
    return get_email_status()


@router.post("/chat", response_model=AgentTurnOut)
async def agent_chat(
    payload: AgentChatIn,
    graph: Any = Depends(get_agent_graph),
    db: Session = Depends(get_session),
) -> AgentTurnOut:
    """Run a conversational turn against the LangGraph coordinator."""
    lock = await get_thread_lock(payload.thread_id)
    async with lock:
        watermark = db.scalar(select(func.coalesce(func.max(AnalysisArtifact.id), 0))) or 0
        config = {
            "configurable": {"thread_id": payload.thread_id},
            "recursion_limit": 25,
            "metadata": {"thread_id": payload.thread_id},
        }

        result = await asyncio.to_thread(
            graph.invoke,
            {"messages": [{"role": "user", "content": payload.message}]},
            config,
        )

        interrupted, interrupt_payload, preview_art_id = extract_interrupt(result)
        artifacts = get_turn_artifacts(db, payload.thread_id, watermark, preview_art_id)
        raw_messages = result.get("messages") or []
        messages = [serialize_message(m) for m in raw_messages]

        return AgentTurnOut(
            thread_id=payload.thread_id,
            messages=messages,
            interrupted=interrupted,
            interrupt=interrupt_payload,
            artifacts=artifacts,
        )


@router.post("/resume", response_model=AgentTurnOut)
async def agent_resume(
    payload: AgentResumeIn,
    graph: Any = Depends(get_agent_graph),
    db: Session = Depends(get_session),
) -> AgentTurnOut:
    """Resume a pending interrupt approval with approve or reject."""
    lock = await get_thread_lock(payload.thread_id)
    async with lock:
        watermark = db.scalar(select(func.coalesce(func.max(AnalysisArtifact.id), 0))) or 0
        config = {
            "configurable": {"thread_id": payload.thread_id},
            "recursion_limit": 25,
            "metadata": {"thread_id": payload.thread_id},
        }

        if payload.decision == "approve":
            decision_dict: dict[str, Any] = {"decision": "approve"}
            if payload.ops is not None:
                decision_dict["ops"] = payload.ops
        else:
            decision_dict = {"decision": "reject", "ops": []}

        result = await asyncio.to_thread(
            graph.invoke,
            Command(resume=decision_dict),
            config,
        )

        interrupted, interrupt_payload, preview_art_id = extract_interrupt(result)
        artifacts = get_turn_artifacts(db, payload.thread_id, watermark, preview_art_id)
        raw_messages = result.get("messages") or []
        messages = [serialize_message(m) for m in raw_messages]

        return AgentTurnOut(
            thread_id=payload.thread_id,
            messages=messages,
            interrupted=interrupted,
            interrupt=interrupt_payload,
            artifacts=artifacts,
        )


@router.get("/thread/{thread_id}/state", response_model=AgentThreadStateOut)
def agent_thread_state(
    thread_id: str,
    graph: Any = Depends(get_agent_graph),
    db: Session = Depends(get_session),
) -> AgentThreadStateOut:
    """Inspect current checkpointer state for thread rehydration after reload or reconnect."""
    config = {
        "configurable": {"thread_id": thread_id},
        "metadata": {"thread_id": thread_id},
    }
    state = graph.get_state(config)
    messages: list[ChatMessageOut] = []
    interrupted = False
    interrupt_payload: InterruptPayloadOut | None = None
    preview_artifact_id: int | None = None

    if state and state.values:
        raw_messages = state.values.get("messages") or []
        messages = [serialize_message(m) for m in raw_messages]

    if state and state.tasks:
        for task in state.tasks:
            task_interrupts = getattr(task, "interrupts", ()) or ()
            if task_interrupts:
                raw = task_interrupts[0].value
                if isinstance(raw, dict):
                    preview_artifact_id = raw.get("preview_artifact_id")
                    interrupt_payload = InterruptPayloadOut(
                        ops=raw.get("ops") or [],
                        preview=raw.get("preview") or {},
                        preview_artifact_id=preview_artifact_id,
                        rationale=raw.get("rationale"),
                    )
                    interrupted = True
                    break

    # Rehydrate thread artifacts
    stmt = (
        select(AnalysisArtifact)
        .where(
            AnalysisArtifact.thread_id == thread_id,
            AnalysisArtifact.status == "open",
        )
        .order_by(AnalysisArtifact.id.asc())
    )
    rows = list(db.scalars(stmt).all())
    artifacts = [
        TurnArtifactOut(
            artifact_id=r.id,
            kind=r.kind,
            title=r.title,
            digest=r.digest,
            spec=r.spec,
            data=r.cache,
        )
        for r in rows
    ]
    if preview_artifact_id is not None and not any(a.artifact_id == preview_artifact_id for a in artifacts):
        preview_art = db.get(AnalysisArtifact, preview_artifact_id)
        if preview_art is not None:
            artifacts.append(
                TurnArtifactOut(
                    artifact_id=preview_art.id,
                    kind=preview_art.kind,
                    title=preview_art.title,
                    digest=preview_art.digest,
                    spec=preview_art.spec,
                    data=preview_art.cache,
                )
            )

    return AgentThreadStateOut(
        thread_id=thread_id,
        messages=messages,
        interrupted=interrupted,
        interrupt=interrupt_payload,
        artifacts=artifacts,
        email_source_status=get_email_status(),
    )


@router.post("/chat/stream")
async def agent_chat_stream(
    payload: AgentChatIn,
    graph: Any = Depends(get_agent_graph),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Stream events (token/tool/artifact/interrupt/done) over SSE for long agent turns."""
    lock = await get_thread_lock(payload.thread_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        async with lock:
            watermark = db.scalar(select(func.coalesce(func.max(AnalysisArtifact.id), 0))) or 0
            config = {
                "configurable": {"thread_id": payload.thread_id},
                "recursion_limit": 25,
                "metadata": {"thread_id": payload.thread_id},
            }

            try:
                interrupted = False
                interrupt_payload: InterruptPayloadOut | None = None
                preview_art_id: int | None = None

                queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
                loop = asyncio.get_running_loop()

                def run_sync_stream() -> None:
                    try:
                        for update_event in graph.stream(
                            {"messages": [{"role": "user", "content": payload.message}]},
                            config,
                            stream_mode="updates",
                        ):
                            loop.call_soon_threadsafe(queue.put_nowait, ("event", update_event))
                        loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
                    except Exception as stream_exc:
                        loop.call_soon_threadsafe(queue.put_nowait, ("error", stream_exc))

                stream_task = asyncio.create_task(asyncio.to_thread(run_sync_stream))

                while True:
                    kind, val = await queue.get()
                    if kind == "done":
                        break
                    if kind == "error":
                        raise val

                    update_event = val
                    if not isinstance(update_event, dict):
                        continue

                    # Surface tool-call progress so long turns (e.g. Gmail enrichment) are not silent spinners
                    for node_name, node_output in update_event.items():
                        if isinstance(node_output, dict):
                            msgs = node_output.get("messages") or []
                            for msg in msgs:
                                tool_calls = getattr(msg, "tool_calls", None) or []
                                for tc in tool_calls:
                                    tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                                    if tc_name:
                                        yield (
                                            f"event: tool\n"
                                            f"data: {json.dumps({'subagent': tc_name, 'tool': tc_name})}\n\n"
                                        )

                    if "__interrupt__" in update_event:
                        interrupted, interrupt_payload, preview_art_id = extract_interrupt(update_event)
                        if interrupt_payload:
                            yield (
                                f"event: interrupt\n"
                                f"data: {json.dumps(interrupt_payload.model_dump(mode='json'))}\n\n"
                            )
                        break

                await stream_task

                # Stream new artifacts produced
                artifacts = get_turn_artifacts(db, payload.thread_id, watermark, preview_art_id)
                for art in artifacts:
                    yield (
                        f"event: artifact\n"
                        f"data: {json.dumps(art.model_dump(mode='json'))}\n\n"
                    )

                # Fetch final state to emit transcript
                state = graph.get_state(config)
                raw_messages = (state.values.get("messages") or []) if state and state.values else []
                serialized_messages = [serialize_message(m).model_dump(mode="json") for m in raw_messages]

                done_payload = {
                    "thread_id": payload.thread_id,
                    "messages": serialized_messages,
                    "interrupted": interrupted,
                    "interrupt": interrupt_payload.model_dump(mode="json") if interrupt_payload else None,
                    "artifacts": [a.model_dump(mode="json") for a in artifacts],
                }
                yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

            except Exception as exc:
                err_text = str(exc) or exc.__class__.__name__
                yield f"event: error\ndata: {json.dumps({'error': err_text})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
