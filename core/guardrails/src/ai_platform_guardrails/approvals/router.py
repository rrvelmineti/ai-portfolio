"""``/approvals`` router (mount via ``install_guardrails``).

No ``from __future__ import annotations``: FastAPI must see the real dependencies.
"""

import uuid
from datetime import datetime
from typing import Annotated

from ai_platform_auth import Auth, Role, User
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ai_platform_guardrails.approvals.models import ApprovalRequest, ApprovalStatus
from ai_platform_guardrails.approvals.service import ApprovalService


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    requester_id: uuid.UUID
    action: str
    payload_summary: str
    status: ApprovalStatus
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    reason: str | None
    request_id: str | None
    created_at: datetime
    expires_at: datetime


class ApprovalCreate(BaseModel):
    action: str = Field(min_length=1, max_length=100)
    payload_summary: str = Field(min_length=1, max_length=2000)


class Decision(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


def _out(req: ApprovalRequest) -> ApprovalOut:
    return ApprovalOut.model_validate(req)


def build_approvals_router(auth: Auth, service: ApprovalService) -> APIRouter:
    router = APIRouter(prefix="/approvals", tags=["approvals"])
    SessionDep = Annotated[Session, Depends(auth.get_session, scope="function")]  # noqa: N806
    ViewerDep = Annotated[User, Depends(auth.require_role(Role.VIEWER))]  # noqa: N806
    AnalystDep = Annotated[User, Depends(auth.require_role(Role.ANALYST))]  # noqa: N806

    @router.post("", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
    def create(body: ApprovalCreate, user: ViewerDep, session: SessionDep) -> ApprovalOut:
        """Request approval for an action (products usually call require_approval())."""
        req = service.require_approval(
            session, body.action, body.payload_summary,
            tenant_id=user.tenant_id, requester_id=user.id,
        )  # fmt: skip
        return _out(req)

    @router.get("", response_model=list[ApprovalOut])
    def list_requests(
        user: ViewerDep,
        session: SessionDep,
        status_filter: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    ) -> list[ApprovalOut]:
        return [_out(r) for r in service.list_for(session, user, status_filter)]

    @router.get("/{approval_id}", response_model=ApprovalOut)
    def get(approval_id: uuid.UUID, user: ViewerDep, session: SessionDep) -> ApprovalOut:
        return _out(service.get_for(session, user, approval_id))

    @router.post("/{approval_id}/approve", response_model=ApprovalOut)
    def approve(
        approval_id: uuid.UUID, body: Decision, user: AnalystDep, session: SessionDep
    ) -> ApprovalOut:
        return _out(service.decide(session, user, approval_id, approve=True, reason=body.reason))

    @router.post("/{approval_id}/reject", response_model=ApprovalOut)
    def reject(
        approval_id: uuid.UUID, body: Decision, user: AnalystDep, session: SessionDep
    ) -> ApprovalOut:
        return _out(service.decide(session, user, approval_id, approve=False, reason=body.reason))

    return router
