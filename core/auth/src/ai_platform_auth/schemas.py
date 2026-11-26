"""Request/response models for the auth API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ai_platform_auth.models import Role, Tenant, User


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    username: str
    email: str
    role: Role
    is_active: bool
    is_superadmin: bool
    locked: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def from_user(cls, user: User, now: datetime) -> UserOut:
        return cls(
            id=user.id,
            tenant_id=user.tenant_id,
            username=user.username,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            is_superadmin=user.is_superadmin,
            locked=user.locked_until is not None and user.locked_until > now,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    role: Role = Role.VIEWER


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    unlock: bool = False


class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=200)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    created_at: datetime

    @classmethod
    def from_tenant(cls, tenant: Tenant) -> TenantOut:
        return cls.model_validate(tenant)
