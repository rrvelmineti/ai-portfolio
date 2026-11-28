"""HTTP endpoints for Phase 1: clients, files, status changes, mapping decisions, sign-off.

No ``from __future__ import annotations``: FastAPI needs the real ``Depends`` objects in
the endpoint signatures.

Roles: viewer reads; analyst (and above) writes; only admin (manager) signs off. Every
write commits its audit row with it (``service.py``).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from ai_platform_auth import Auth, Role, TenantScope, User
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from claims_onboarding import service
from claims_onboarding.models import FileStatus

# ---------------------------------------------------------------- schemas


class ClientIn(BaseModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    go_live_target: date | None = None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    status: str
    go_live_target: date | None
    created_at: datetime


class FileIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    size_bytes: int = Field(ge=0)
    row_count: int | None = Field(default=None, ge=0)
    headers: list[str] = Field(min_length=1, max_length=500)
    previous_file_id: uuid.UUID | None = None

    @field_validator("headers")
    @classmethod
    def _headers_ok(cls, headers: list[str]) -> list[str]:
        if any(not h.strip() or len(h) > 300 for h in headers):
            raise ValueError("each header must be 1-300 characters and not blank")
        return headers


class ColumnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    position: int
    header: str


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    file_column_id: uuid.UUID
    target_field: str
    status: str
    confidence: Decimal | None
    model: str | None
    approved_by: uuid.UUID | None
    rejected_by: uuid.UUID | None
    decided_at: datetime | None


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    sequence: int
    previous_file_id: uuid.UUID | None
    filename: str
    sha256: str
    size_bytes: int
    row_count: int | None
    status: str
    status_changed_at: datetime
    created_at: datetime


class FileDetail(FileOut):
    columns: list[ColumnOut]
    mappings: list[MappingOut]


class TransitionIn(BaseModel):
    to: FileStatus
    reason: str | None = Field(default=None, max_length=service.MAX_REASON)


class MappingIn(BaseModel):
    file_column_id: uuid.UUID
    target_field: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    model: str | None = Field(default=None, max_length=200)


class DecisionIn(BaseModel):
    approve: bool


class SignoffIn(BaseModel):
    approve: bool
    comment: str = Field(default="", max_length=2000)


class SignoffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    file_id: uuid.UUID
    signed_by: uuid.UUID
    decision: str
    comment: str
    signed_at: datetime


# ---------------------------------------------------------------- router


def build_router(auth: Auth) -> APIRouter:
    router = APIRouter(tags=["claims"])
    SessionDep = Annotated[Session, Depends(auth.get_session, scope="function")]  # noqa: N806
    ScopeDep = Annotated[TenantScope, Depends(auth.get_tenant_scope)]  # noqa: N806
    Viewer = Annotated[User, Depends(auth.require_role(Role.VIEWER))]  # noqa: N806
    Analyst = Annotated[User, Depends(auth.require_role(Role.ANALYST))]  # noqa: N806
    Manager = Annotated[User, Depends(auth.require_role(Role.ADMIN))]  # noqa: N806

    def detail(session: Session, scope: TenantScope, file_id: uuid.UUID) -> FileDetail:
        file = service.get_file(session, scope, file_id)
        return FileDetail(
            **FileOut.model_validate(file).model_dump(),
            columns=[ColumnOut.model_validate(c)
                     for c in service.file_columns(session, scope, file.id)],
            mappings=[MappingOut.model_validate(m)
                      for m in service.file_mappings(session, scope, file.id)],
        )  # fmt: skip

    @router.post("/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
    def create_client(body: ClientIn, user: Analyst, scope: ScopeDep,
                      session: SessionDep) -> ClientOut:  # fmt: skip
        client = service.create_client(session, scope, user, code=body.code, name=body.name,
                                       go_live_target=body.go_live_target)  # fmt: skip
        session.commit()
        return ClientOut.model_validate(client)

    @router.get("/clients", response_model=list[ClientOut])
    def list_clients(_user: Viewer, scope: ScopeDep, session: SessionDep) -> list[ClientOut]:
        return [ClientOut.model_validate(c) for c in service.list_clients(session, scope)]

    @router.get("/clients/{client_id}", response_model=ClientOut)
    def get_client(client_id: uuid.UUID, _user: Viewer, scope: ScopeDep,
                   session: SessionDep) -> ClientOut:  # fmt: skip
        return ClientOut.model_validate(service.get_client(session, scope, client_id))

    @router.post("/clients/{client_id}/files", response_model=FileDetail,
                 status_code=status.HTTP_201_CREATED)  # fmt: skip
    def register_file(client_id: uuid.UUID, body: FileIn, user: Analyst, scope: ScopeDep,
                      session: SessionDep) -> FileDetail:  # fmt: skip
        """Register a file the client sent: metadata and headings only (no contents yet)."""
        file = service.register_file(
            session, scope, user, client_id, filename=body.filename, sha256=body.sha256,
            size_bytes=body.size_bytes, row_count=body.row_count, headers=body.headers,
            previous_file_id=body.previous_file_id,
        )  # fmt: skip
        session.commit()
        return detail(session, scope, file.id)

    @router.get("/files/{file_id}", response_model=FileDetail)
    def get_file(file_id: uuid.UUID, _user: Viewer, scope: ScopeDep,
                 session: SessionDep) -> FileDetail:  # fmt: skip
        return detail(session, scope, file_id)

    @router.post("/files/{file_id}/transitions", response_model=FileOut)
    def move_file(file_id: uuid.UUID, body: TransitionIn, user: Analyst, scope: ScopeDep,
                  session: SessionDep) -> FileOut:  # fmt: skip
        """Change a file's status. Invalid or unguarded changes return 409."""
        file = service.transition(session, scope, user, file_id, body.to, reason=body.reason)
        session.commit()
        return FileOut.model_validate(file)

    @router.post("/files/{file_id}/mappings", response_model=MappingOut,
                 status_code=status.HTTP_201_CREATED)  # fmt: skip
    def suggest_mapping(file_id: uuid.UUID, body: MappingIn, user: Analyst, scope: ScopeDep,
                        session: SessionDep) -> MappingOut:  # fmt: skip
        mapping = service.suggest_mapping(
            session, scope, user, file_id, file_column_id=body.file_column_id,
            target_field=body.target_field, confidence=body.confidence, model=body.model,
        )  # fmt: skip
        session.commit()
        return MappingOut.model_validate(mapping)

    @router.post("/mappings/{mapping_id}/decision", response_model=MappingOut)
    def decide_mapping(mapping_id: uuid.UUID, body: DecisionIn, user: Analyst, scope: ScopeDep,
                       session: SessionDep) -> MappingOut:  # fmt: skip
        mapping = service.decide_mapping(session, scope, user, mapping_id, approve=body.approve)
        session.commit()
        return MappingOut.model_validate(mapping)

    @router.post("/files/{file_id}/signoff", response_model=SignoffOut,
                 status_code=status.HTTP_201_CREATED)  # fmt: skip
    def sign_off(file_id: uuid.UUID, body: SignoffIn, user: Manager, scope: ScopeDep,
                 session: SessionDep) -> SignoffOut:  # fmt: skip
        """Go-live decision (manager only; never the person who approved the mappings)."""
        signoff = service.sign_off(session, scope, user, file_id, approve=body.approve,
                                   comment=body.comment)  # fmt: skip
        session.commit()
        return SignoffOut.model_validate(signoff)

    return router
