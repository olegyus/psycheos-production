"""
REST API for artifacts.

GET /v1/artifacts?context_id=...&service_id=...  — list artifacts for a case
GET /v1/artifacts/{artifact_id}                  — retrieve a single artifact
"""
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.artifact import Artifact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])

_LIST_LIMIT = 20


# ── Response schemas ──────────────────────────────────────────────────────────

class ArtifactItem(BaseModel):
    """Minimal representation — used in list responses."""
    artifact_id: uuid.UUID
    context_id: uuid.UUID
    service_id: str
    run_id: uuid.UUID
    specialist_telegram_id: int
    summary: Optional[str]
    created_at: str  # ISO-8601

    @classmethod
    def from_orm(cls, a: Artifact) -> "ArtifactItem":
        return cls(
            artifact_id=a.artifact_id,
            context_id=a.context_id,
            service_id=a.service_id,
            run_id=a.run_id,
            specialist_telegram_id=a.specialist_telegram_id,
            summary=a.summary,
            created_at=a.created_at.isoformat(),
        )


class ArtifactDetail(ArtifactItem):
    """Full representation — includes payload."""
    payload: dict

    @classmethod
    def from_orm(cls, a: Artifact) -> "ArtifactDetail":
        return cls(
            artifact_id=a.artifact_id,
            context_id=a.context_id,
            service_id=a.service_id,
            run_id=a.run_id,
            specialist_telegram_id=a.specialist_telegram_id,
            summary=a.summary,
            created_at=a.created_at.isoformat(),
            payload=a.payload,
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ArtifactItem])
async def list_artifacts(
    context_id: uuid.UUID = Query(..., description="Filter by case context_id"),
    service_id: Optional[str] = Query(None, description="Filter by service: interpretator | conceptualizator | simulator"),
    db: AsyncSession = Depends(get_db),
) -> list[ArtifactItem]:
    """
    List artifacts for a case, newest first.
    Scoped by context_id; optionally filtered by service_id.
    Returns at most 20 items.
    """
    stmt = (
        select(Artifact)
        .where(Artifact.context_id == context_id)
        .order_by(Artifact.created_at.desc())
        .limit(_LIST_LIMIT)
    )
    if service_id:
        stmt = stmt.where(Artifact.service_id == service_id)

    result = await db.execute(stmt)
    artifacts = result.scalars().all()
    return [ArtifactItem.from_orm(a) for a in artifacts]


@router.get("/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ArtifactDetail:
    """Retrieve a single artifact including full payload."""
    result = await db.execute(
        select(Artifact).where(Artifact.artifact_id == artifact_id)
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return ArtifactDetail.from_orm(artifact)
