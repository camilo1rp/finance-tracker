from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from langchain.agents.middleware.summarization import count_tokens_approximately
from langchain_core.messages import ToolMessage

from app.models import Account, AnalysisArtifact, Owner, Transaction
from app.services import artifact_service
from tests.agent.helpers import seed_coffee


def test_format_digest_under_token_cap() -> None:
    sample_result = {
        "groups": [
            {"group_value": "Dining", "spend": "1204.00", "count": 10},
            {"group_value": "Groceries", "spend": "980.55", "count": 15},
            {"group_value": "Transport", "spend": "611.20", "count": 5},
            {"group_value": "Entertainment", "spend": "300.00", "count": 3},
        ],
        "match_count": 33,
        "returned": 4,
        "truncated": True,
        "totals": {
            "spend": Decimal("3095.75"),
            "net_cash_flow": Decimal("-3000.00"),
            "purchases": Decimal("3200.00"),
            "refunds": Decimal("104.25"),
        },
    }
    filters = {
        "date_from": "2026-04-01",
        "date_to": "2026-06-30",
        "owner_id": 2,
        "account_id": None,
    }
    digest = artifact_service.format_digest(
        kind="group_summary",
        result=sample_result,
        filters=filters,
        artifact_id=47,
        title="Spend by category Q2",
    )
    tokens = count_tokens_approximately([ToolMessage(content=digest, tool_call_id="call_1")])
    assert tokens <= artifact_service.MAX_DIGEST_TOKENS
    assert "Artifact #47" in digest
    assert "kind=group_summary" in digest
    assert "truncated=true" in digest
    assert "date_from=2026-04-01" in digest
    assert "Dining 1204.00" in digest


def test_persist_materialize_and_derive_artifact(db_session: Session) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()

    spec = {
        "tool": "summarize",
        "kwargs": {
            "group_by": "category",
            "date_from": "2024-07-01",
            "date_to": "2024-07-31",
            "owner_id": owner.id,
        },
    }
    res = artifact_service.execute_spec(db_session, "summarize", spec["kwargs"])
    artifact_id, digest_text = artifact_service.persist_artifact(
        db_session,
        thread_id="test-thread-1",
        kind="group_summary",
        title="July spend by category",
        spec=spec,
        result=res,
    )
    assert artifact_id is not None
    assert f"Artifact #{artifact_id}" in digest_text

    artifact = db_session.get(AnalysisArtifact, artifact_id)
    assert artifact is not None
    assert artifact.kind == "group_summary"
    assert artifact.thread_id == "test-thread-1"

    # Materialize
    materialized = artifact_service.materialize_artifact(db_session, artifact)
    assert "groups" in materialized

    # Derive
    derived = artifact_service.derive_artifact(
        db_session,
        artifact_id,
        mutations={"group_by": "subcategory"},
        title="July spend by subcategory",
    )
    assert derived.id != artifact_id
    assert derived.derived_from == artifact_id
    assert derived.spec["kwargs"]["group_by"] == "subcategory"


def test_cleanup_expired_artifacts_and_proposals(db_session: Session) -> None:
    from datetime import timedelta
    from app.models import EnrichmentProposal, ProposalStatus, _utcnow_naive

    now = _utcnow_naive()
    art = AnalysisArtifact(
        thread_id="test",
        produced_by="analyst",
        kind="test",
        title="Expired art",
        spec={},
        digest={},
        status="open",
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(hours=1),
    )
    db_session.add(art)

    prop = EnrichmentProposal(
        task="research coffee",
        recommendation={},
        status=ProposalStatus.OPEN.value,
        created_at=now - timedelta(days=35),
    )
    db_session.add(prop)
    db_session.commit()

    stats = artifact_service.cleanup_expired_artifacts_and_proposals(db_session, now=now)
    assert stats["expired_artifacts"] >= 1
    assert stats["discarded_proposals"] >= 1

    db_session.refresh(art)
    db_session.refresh(prop)
    assert art.status == "expired"
    assert prop.status == ProposalStatus.DISCARDED.value

