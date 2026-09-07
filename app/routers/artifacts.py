"""REST endpoints for analysis artifacts: listing, row materialization, derivation, and eviction."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.label_filter import UnknownLabelFilterError
from app.models import AnalysisArtifact
from app.schemas import ArtifactDeriveIn, ArtifactOut, ArtifactSummary
from app.services import artifact_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=list[ArtifactSummary])
def list_artifacts(
    thread_id: str | None = Query(None, description="Filter by thread ID"),
    kind: str | None = Query(None, description="Filter by artifact kind"),
    status_filter: str | None = Query("open", alias="status", description="Filter by status"),
    db: Session = Depends(get_session),
) -> list[AnalysisArtifact]:
    """List analysis artifacts with optional thread_id, kind, and status filtering."""
    stmt = select(AnalysisArtifact).order_by(AnalysisArtifact.id.desc())
    if thread_id is not None:
        stmt = stmt.where(AnalysisArtifact.thread_id == thread_id)
    if kind is not None:
        stmt = stmt.where(AnalysisArtifact.kind == kind)
    if status_filter is not None:
        stmt = stmt.where(AnalysisArtifact.status == status_filter)
    return list(db.scalars(stmt).all())


@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(
    artifact_id: int,
    db: Session = Depends(get_session),
) -> AnalysisArtifact:
    """Get metadata, query spec, and digest for a specific artifact."""
    artifact = db.get(AnalysisArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    return artifact


@router.get("/{artifact_id}/rows")
def get_artifact_rows(
    artifact_id: int,
    limit: int | None = Query(None, ge=1, le=500),
    offset: int | None = Query(None, ge=0),
    sort: str | None = Query(None),
    force_refresh: bool = Query(False),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Materialize full dataset rows for an artifact without LLM reasoning tokens."""
    artifact = db.get(AnalysisArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    try:
        return artifact_service.materialize_artifact(
            db, artifact, limit=limit, offset=offset, sort=sort, force_refresh=force_refresh
        )
    except UnknownLabelFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/{artifact_id}/derive",
    response_model=ArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
def derive_artifact(
    artifact_id: int,
    payload: ArtifactDeriveIn,
    db: Session = Depends(get_session),
) -> AnalysisArtifact:
    """Zero-LLM drilldown: mutate an existing artifact's query spec and persist a derived artifact."""
    artifact = db.get(AnalysisArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    try:
        return artifact_service.derive_artifact(
            db,
            artifact_id,
            mutations=payload.mutations,
            title=payload.title,
            thread_id=payload.thread_id or artifact.thread_id,
            produced_by="user",
        )
    except UnknownLabelFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_artifact(
    artifact_id: int,
    db: Session = Depends(get_session),
) -> None:
    """Evict or expire an artifact."""
    artifact = db.get(AnalysisArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    artifact.status = "expired"
    db.commit()
