"""
Link token service — issue and verify one-time passes between Pro and Tool bots.
Called directly by bot handlers (no HTTP round-trip needed within same process).
"""
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.link_token import LinkToken

TOKEN_TTL_HOURS = 24
TOOL_SERVICES = frozenset({"screen", "interpretator", "conceptualizator", "simulator"})


async def issue_link(
    db: AsyncSession,
    *,
    service_id: str,
    context_id: uuid.UUID,
    role: str,
    subject_id: int,
) -> LinkToken:
    """
    Create a one-time link token for accessing a tool bot.
    Called by Pro bot handler when specialist clicks 'Launch tool'.
    db.commit() is the caller's responsibility.
    """
    token = LinkToken(
        run_id=uuid.uuid4(),
        service_id=service_id,
        context_id=context_id,
        role=role,
        subject_id=subject_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    )
    db.add(token)
    await db.flush()  # populate jti via server_default
    return token


class LinkVerifyError(Exception):
    """Raised when a link token fails verification."""
    pass


async def verify_link(
    db: AsyncSession,
    *,
    raw_token: str,
    service_id: str,
    subject_id: int,
) -> LinkToken:
    """
    Verify a link token received via /start deep link.
    Called by Tool bot handler on /start TOKEN.
    Marks token as used on success.
    db.commit() is the caller's responsibility.
    """
    try:
        jti = uuid.UUID(raw_token)
    except ValueError:
        raise LinkVerifyError("Invalid token format")

    result = await db.execute(select(LinkToken).where(LinkToken.jti == jti))
    token = result.scalar_one_or_none()

    if token is None:
        raise LinkVerifyError("Token not found")
    if token.used_at is not None:
        raise LinkVerifyError("Token already used")
    if datetime.now(timezone.utc) > token.expires_at:
        raise LinkVerifyError("Token expired")
    if token.service_id != service_id:
        raise LinkVerifyError("Token not valid for this service")
    if token.subject_id != subject_id:
        raise LinkVerifyError("Token not valid for this user")
    # Rule 3.4: client tokens are only valid for Screen
    if token.role == "client" and token.service_id != "screen":
        raise LinkVerifyError("Client token cannot be used with non-screen service")

    token.used_at = datetime.now(timezone.utc)
    await db.flush()
    return token
