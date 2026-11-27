"""Approval gate for risky AI actions.

* ``require_approval(action, payload_summary)`` records a pending request. The summary
  is PII-masked before storage (fail closed).
* Analysts and admins decide; nobody decides their own request (four-eyes).
* Pending requests expire (default 7 days, ``APPROVAL_TTL_HOURS``).
* Visibility: viewers see their own requests; analysts their own plus pending ones in
  their tenant; admins everything in their tenant. Other tenants' requests are "not found".
* Every request and decision is written to the audit log.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ai_platform_auth.audit import audit
from ai_platform_auth.errors import Conflict, NotFound, PermissionDenied
from ai_platform_auth.models import ROLE_RANK, Role, User
from ai_platform_common.config import get_env
from ai_platform_common.context import current
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ai_platform_guardrails.approvals.models import ApprovalRequest, ApprovalStatus
from ai_platform_guardrails.pii.masking import mask_text

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def default_ttl() -> timedelta:
    raw = get_env("APPROVAL_TTL_HOURS")
    return timedelta(hours=int(raw)) if raw else timedelta(days=7)


class ApprovalService:
    def __init__(self, ttl: timedelta | None = None, clock: Clock = _utcnow) -> None:
        self.ttl = ttl or default_ttl()
        self.clock = clock

    # ------------------------------------------------------------------ create
    def require_approval(
        self,
        session: Session,
        action: str,
        payload_summary: str,
        *,
        tenant_id: uuid.UUID | None = None,
        requester_id: uuid.UUID | None = None,
    ) -> ApprovalRequest:
        """Create a pending approval request for the current (or given) user."""
        ctx = current()
        tid = tenant_id or (ctx.tenant_id if ctx else None)
        uid = requester_id or (ctx.user_id if ctx else None)
        if tid is None or uid is None:
            raise PermissionDenied("An authenticated user is required to request approval")
        summary = mask_text(payload_summary[:2000], reversible=False).masked_text
        now = self.clock()
        req = ApprovalRequest(
            id=uuid.uuid4(), tenant_id=tid, requester_id=uid, action=action[:100],
            payload_summary=summary, status=ApprovalStatus.PENDING,
            request_id=ctx.request_id if ctx else None, created_at=now,
            expires_at=now + self.ttl,
        )  # fmt: skip
        session.add(req)
        session.flush()
        audit(session, "approval.requested", tenant_id=tid, user_id=uid,
              resource_type="approval", resource_id=str(req.id),
              details={"action": req.action})  # fmt: skip
        session.commit()
        return req

    # ------------------------------------------------------------------ read
    def list_for(
        self, session: Session, user: User, status: ApprovalStatus | None = None
    ) -> list[ApprovalRequest]:
        self._expire_due(session, user.tenant_id)
        stmt = select(ApprovalRequest).where(ApprovalRequest.tenant_id == user.tenant_id)
        rank = ROLE_RANK[user.role]
        if rank < ROLE_RANK[Role.ANALYST]:
            stmt = stmt.where(ApprovalRequest.requester_id == user.id)
        elif rank < ROLE_RANK[Role.ADMIN]:
            stmt = stmt.where(or_(ApprovalRequest.requester_id == user.id,
                                  ApprovalRequest.status == ApprovalStatus.PENDING))  # fmt: skip
        if status is not None:
            stmt = stmt.where(ApprovalRequest.status == status)
        return list(session.scalars(stmt.order_by(ApprovalRequest.created_at.desc())))

    def get_for(self, session: Session, user: User, approval_id: uuid.UUID) -> ApprovalRequest:
        visible = {r.id: r for r in self.list_for(session, user)}
        if approval_id not in visible:
            raise NotFound("Approval request not found")
        return visible[approval_id]

    def is_approved(self, session: Session, approval_id: uuid.UUID) -> bool:
        req = session.get(ApprovalRequest, approval_id)
        return req is not None and req.status == ApprovalStatus.APPROVED

    # ------------------------------------------------------------------ decide
    def decide(
        self,
        session: Session,
        user: User,
        approval_id: uuid.UUID,
        *,
        approve: bool,
        reason: str | None = None,
    ) -> ApprovalRequest:
        if ROLE_RANK[user.role] < ROLE_RANK[Role.ANALYST]:
            raise PermissionDenied("Requires role analyst")
        self._expire_due(session, user.tenant_id)
        req = session.scalar(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == user.tenant_id)
            .with_for_update()
        )
        if req is None:
            raise NotFound("Approval request not found")
        if req.requester_id == user.id:
            raise PermissionDenied("You cannot decide your own request")
        if req.status != ApprovalStatus.PENDING:
            raise Conflict(f"Request is already {req.status.value}")
        if not approve and not (reason and reason.strip()):
            raise Conflict("A reason is required to reject a request")
        req.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        req.decided_by = user.id
        req.decided_at = self.clock()
        req.reason = (reason or "").strip()[:1000] or None
        audit(session, f"approval.{req.status.value}", tenant_id=user.tenant_id,
              user_id=user.id, resource_type="approval", resource_id=str(req.id),
              details={"action": req.action, "requester_id": str(req.requester_id),
                       "has_reason": req.reason is not None})  # fmt: skip
        session.commit()
        return req

    def _expire_due(self, session: Session, tenant_id: uuid.UUID) -> None:
        due = session.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == tenant_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.expires_at <= self.clock(),
            )
        ).all()
        for req in due:
            req.status = ApprovalStatus.EXPIRED
            audit(session, "approval.expired", tenant_id=tenant_id, user_id=None,
                  resource_type="approval", resource_id=str(req.id),
                  details={"action": req.action})  # fmt: skip
        if due:
            session.commit()
