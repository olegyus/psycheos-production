"""
REST API for link token operations.

POST /v1/links/issue  — Pro generates a pass for a tool bot
POST /v1/links/verify — Tool bot validates the pass from /start TOKEN
"""
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.links import issue_link, verify_link, LinkVerifyError, TOOL_SERVICES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/links", tags=["links"])


# ── Request / Response schemas ──────────────────────────────────────────────

class IssueRequest(BaseModel):
    service_id: str       # which tool bot: screen | interpretator | conceptualizator | simulator
    context_id: uuid.UUID
    role: str             # specialist | client
    subject_id: int       # telegram_id of the intended user


class IssueResponse(BaseModel):
    jti: uuid.UUID
    run_id: uuid.UUID
    start_param: str      # use as: t.me/BotName?start={start_param}


class VerifyRequest(BaseModel):
    raw_token: str        # the string received from /start TOKEN
    service_id: str       # the calling bot's own service_id
    subject_id: int       # telegram_id of the user who sent /start


class VerifyResponse(BaseModel):
    context_id: uuid.UUID
    run_id: uuid.UUID
    role: str
    service_id: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/issue", response_model=IssueResponse)
async def issue(body: IssueRequest, db: AsyncSession = Depends(get_db)):
    if body.service_id not in TOOL_SERVICES:
        raise HTTPException(
            400, detail=f"Invalid service_id. Must be one of: {sorted(TOOL_SERVICES)}"
        )
    if body.role not in {"specialist", "client"}:
        raise HTTPException(400, detail="role must be 'specialist' or 'client'")
    if body.role == "client" and body.service_id != "screen":
        raise HTTPException(400, detail="Client role is only valid for service_id=screen")

    token = await issue_link(
        db,
        service_id=body.service_id,
        context_id=body.context_id,
        role=body.role,
        subject_id=body.subject_id,
    )
    await db.commit()

    return IssueResponse(
        jti=token.jti,
        run_id=token.run_id,
        start_param=str(token.jti),
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify(body: VerifyRequest, db: AsyncSession = Depends(get_db)):
    try:
        token = await verify_link(
            db,
            raw_token=body.raw_token,
            service_id=body.service_id,
            subject_id=body.subject_id,
        )
        await db.commit()
    except LinkVerifyError as e:
        raise HTTPException(400, detail=str(e))

    return VerifyResponse(
        context_id=token.context_id,
        run_id=token.run_id,
        role=token.role,
        service_id=token.service_id,
    )
