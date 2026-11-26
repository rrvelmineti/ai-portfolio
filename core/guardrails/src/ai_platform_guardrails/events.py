"""Recording guardrail events (blocks, flags) to the audit log and JSON logs.

Events carry reasons, score and a short hash + length of the payload, never the payload
itself. If no database is configured, the event is still logged.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ai_platform_common.logging import get_logger
from sqlalchemy.orm import Session, sessionmaker

log = get_logger("ai_platform_guardrails.events")

_state: dict[str, sessionmaker[Session] | None] = {"session_factory": None}


def configure_events(session_factory: sessionmaker[Session] | None) -> None:
    """Where to write audit rows (``None`` = log only)."""
    _state["session_factory"] = session_factory


def payload_fingerprint(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
        "payload_len": len(payload),
    }


def record_event(
    action: str,
    *,
    reasons: list[str],
    score: float | None = None,
    payload: str | None = None,
    kind: str = "input",
    source: str | None = None,
) -> None:
    """Log and audit a guardrail event. Never raises (the block itself still happens)."""
    details: dict[str, Any] = {"reasons": reasons, "kind": kind, **payload_fingerprint(payload)}
    if score is not None:
        details["score"] = round(score, 4)
    if source:
        details["source"] = source[:100]
    log.warning(action, extra={"fields": details})
    factory = _state["session_factory"]
    if factory is None:
        return
    try:
        from ai_platform_auth.audit import audit

        with factory() as session:
            audit(session, action, resource_type="guardrail", resource_id=kind, details=details)
            session.commit()
    except Exception as exc:
        log.error("guardrail.audit_failed", extra={"fields": {"error_type": type(exc).__name__}})
