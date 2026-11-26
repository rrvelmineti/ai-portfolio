"""Append-only audit log writer. There is deliberately no update or delete function."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ai_platform_common.logging import get_logger
from sqlalchemy import insert
from sqlalchemy.orm import Session

from ai_platform_auth.models import AuditLog

log = get_logger("ai_platform_auth.audit")

# Keys whose values must never be written to the audit log or logs.
_SENSITIVE_KEYS = {"password", "password_hash", "token", "access_token", "refresh_token",
                   "secret", "jwt_secret", "authorization"}  # fmt: skip


@dataclass
class RequestInfo:
    """Who/where a request came from. ``audited`` tells the middleware an explicit
    audit entry was already written, so it does not add a generic one."""

    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    audited: bool = False


def _clean(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    return {k: ("[redacted]" if k.lower() in _SENSITIVE_KEYS else v) for k, v in details.items()}


def audit(
    session: Session,
    action: str,
    *,
    request: RequestInfo | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Insert one audit row in the caller's transaction and emit a JSON log line."""
    info = request or RequestInfo()
    clean = _clean(details)
    session.execute(
        insert(AuditLog).values(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip=info.ip,
            user_agent=(info.user_agent or "")[:500] or None,
            request_id=info.request_id,
            details=clean,
        )
    )
    info.audited = True
    log.info(
        "audit",
        extra={"fields": {
            "action": action, "tenant_id": str(tenant_id) if tenant_id else None,
            "user_id": str(user_id) if user_id else None, "resource_type": resource_type,
            "resource_id": resource_id, "request_id": info.request_id,
        }},
    )  # fmt: skip
