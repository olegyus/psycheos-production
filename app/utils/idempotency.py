"""
Idempotency key generation — unified format from Dev Spec Appendix C.

Format: <scope>|<service_id>|<run_id>|<context_id>|<actor_id>|<step>|<fingerprint>

Rules:
- No timestamps in key (they kill idempotency)
- All fields lowercase, no spaces
- Max length 200-300 chars
"""
import hashlib
import json
from typing import Optional


# Fixed scope values
SCOPE_TG_UPDATE = "tg_update"
SCOPE_JOB_ENQUEUE = "job_enqueue"
SCOPE_USAGE_RESERVE = "usage_reserve"
SCOPE_ARTIFACT_SUBMIT = "artifact_submit"
SCOPE_OUTBOX = "outbox"


def make_idempotency_key(
    scope: str,
    service_id: str,
    run_id: str = "none",
    context_id: str = "none",
    actor_id: str = "none",
    step: str = "none",
    fingerprint: str = "-",
) -> str:
    """
    Build a deterministic idempotency key.

    Examples:
        # Telegram update dedup
        make_idempotency_key("tg_update", "pro", actor_id="tg:12345", step="upd:67890")

        # Job enqueue
        make_idempotency_key("job_enqueue", "interpretator", run_id="uuid", context_id="uuid", actor_id="user:uuid", step="op:run")

        # Artifact submit
        make_idempotency_key("artifact_submit", "interpretator", run_id="uuid", context_id="uuid", actor_id="user:uuid", step="type:full_report", fingerprint="abc123")
    """
    parts = [
        scope.lower(),
        service_id.lower(),
        str(run_id).lower(),
        str(context_id).lower(),
        str(actor_id).lower(),
        step.lower(),
        fingerprint.lower(),
    ]
    key = "|".join(parts)

    if len(key) > 300:
        raise ValueError(f"Idempotency key too long ({len(key)} chars): {key[:80]}...")

    return key


def make_fingerprint(data: dict, fields: Optional[list] = None) -> str:
    """
    Create a short hash from essential fields of a payload.
    Returns first 16 hex chars of SHA-256.
    """
    if fields:
        data = {k: v for k, v in data.items() if k in fields}

    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]
