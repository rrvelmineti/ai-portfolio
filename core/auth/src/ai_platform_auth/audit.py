"""Append-only audit log writer. There is deliberately no update or delete function.

Request details (request_id, IP, user agent, and the authenticated tenant/user when
not given explicitly) come from the shared request context.
"""

from __future__ import annotations

import uuid
from typing import Any

from ai_platform_common.context import current
from ai_platform_common.logging import get_logger, redact
from sqlalchemy import insert
from sqlalchemy.orm import Session

from ai_platform_auth.models import AuditLog

log = get_logger("ai_platform_auth.audit")

_UNSET: Any = object()


def audit(
    session: Session,
    action: str,
    *,
    tenant_id: uuid.UUID | None = _UNSET,
    user_id: uuid.UUID | None = _UNSET,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Insert one audit row in the caller's transaction and emit a JSON log line.

    ``tenant_id`` / ``user_id`` default to the authenticated user in the request
    context; pass them explicitly (even ``None``) to override. Sensitive keys in
    ``details`` are redacted.
    """
    ctx = current()
    if tenant_id is _UNSET:
        tenant_id = ctx.tenant_id if ctx else None
    if user_id is _UNSET:
        user_id = ctx.user_id if ctx else None
    clean: dict[str, Any] = redact(details or {})
    session.execute(
        insert(AuditLog).values(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip=ctx.ip if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
            request_id=ctx.request_id if ctx else None,
            details=clean,
        )
    )
    if ctx is not None:
        ctx.audited = True
    log.info(
        "audit",
        extra={"fields": {
            "action": action, "audit_tenant_id": str(tenant_id) if tenant_id else None,
            "audit_user_id": str(user_id) if user_id else None,
            "resource_type": resource_type, "resource_id": resource_id,
        }},
    )  # fmt: skip
