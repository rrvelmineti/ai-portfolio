"""Auth business logic: login, lockout, refresh rotation, logout, users and tenants.

All functions take an open ``Session``. They commit their own audit rows even when
they then raise (e.g. failed logins), so security events are never rolled back.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ai_platform_common.logging import get_logger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_auth.audit import RequestInfo, audit
from ai_platform_auth.errors import (
    Conflict,
    InvalidCredentials,
    NotFound,
    PermissionDenied,
    TokenInvalid,
)
from ai_platform_auth.models import ROLE_RANK, RefreshToken, Role, Tenant, User
from ai_platform_auth.passwords import dummy_verify, hash_password, verify_password
from ai_platform_auth.scope import TenantScope
from ai_platform_auth.settings import AuthSettings
from ai_platform_auth.tokens import create_access_token, hash_refresh_token, new_refresh_token

log = get_logger("ai_platform_auth")

Clock = Callable[[], datetime]
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret


@dataclass(frozen=True)
class UserChanges:
    role: Role | None = None
    is_active: bool | None = None
    unlock: bool = False


class AuthService:
    def __init__(self, settings: AuthSettings, clock: Clock = utcnow) -> None:
        self.settings = settings
        self.clock = clock

    # ------------------------------------------------------------------ tokens
    def _issue(
        self, session: Session, user: User, family_id: uuid.UUID
    ) -> tuple[TokenPair, RefreshToken]:
        now = self.clock()
        access, _claims = create_access_token(
            user_id=user.id, tenant_id=user.tenant_id, role=user.role,
            settings=self.settings, now=now,
        )  # fmt: skip
        raw, token_hash = new_refresh_token()
        row = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            family_id=family_id,
            token_hash=token_hash,
            expires_at=now + self.settings.refresh_token_ttl,
        )
        session.add(row)
        pair = TokenPair(
            access_token=access,
            refresh_token=raw,
            expires_in=int(self.settings.access_token_ttl.total_seconds()),
        )
        return pair, row

    def _revoke_family(self, session: Session, family_id: uuid.UUID) -> int:
        result = session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=self.clock())
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def _revoke_user_tokens(self, session: Session, user_id: uuid.UUID) -> None:
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=self.clock())
        )

    # ------------------------------------------------------------------ login
    def login(
        self, session: Session, tenant_slug: str, username: str, password: str,
        request: RequestInfo,
    ) -> TokenPair:  # fmt: skip
        """Authenticate; every failure raises the same ``InvalidCredentials``."""
        now = self.clock()
        tenant = session.scalar(select(Tenant).where(Tenant.slug == tenant_slug.lower()))
        user = None
        if tenant is not None:
            user = session.scalar(
                select(User)
                .where(User.tenant_id == tenant.id, User.username == username)
                .with_for_update()
            )

        def fail(reason: str, action: str = "auth.login_failed") -> InvalidCredentials:
            audit(
                session, action, request=request,
                tenant_id=tenant.id if tenant else None,
                user_id=user.id if user else None,
                resource_type="user", resource_id=str(user.id) if user else None,
                details={"reason": reason, "tenant": tenant_slug, "username": username},
            )  # fmt: skip
            session.commit()
            return InvalidCredentials()

        if user is None:
            dummy_verify(password)
            raise fail("unknown_user" if tenant else "unknown_tenant")
        if user.locked_until is not None and user.locked_until > now:
            dummy_verify(password)
            raise fail("locked")
        if not user.is_active:
            dummy_verify(password)
            raise fail("inactive")

        ok, new_hash = verify_password(password, user.password_hash)
        if not ok:
            user.failed_logins += 1
            if user.failed_logins >= self.settings.max_failed_logins:
                user.locked_until = now + self.settings.lockout_duration
                user.failed_logins = 0
                audit(
                    session, "auth.lockout", request=request, tenant_id=user.tenant_id,
                    user_id=user.id, resource_type="user", resource_id=str(user.id),
                    details={"locked_until": user.locked_until.isoformat()},
                )  # fmt: skip
            raise fail("bad_password")

        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        if new_hash:
            user.password_hash = new_hash
        pair, _ = self._issue(session, user, family_id=uuid.uuid4())
        audit(
            session, "auth.login", request=request, tenant_id=user.tenant_id,
            user_id=user.id, resource_type="user", resource_id=str(user.id),
        )  # fmt: skip
        session.commit()
        return pair

    # ------------------------------------------------------------------ refresh / logout
    def refresh(self, session: Session, raw_token: str, request: RequestInfo) -> TokenPair:
        """Rotate a refresh token. Reusing a revoked token revokes its whole family."""
        now = self.clock()
        row = session.scalar(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
            .with_for_update()
        )
        if row is None:
            raise TokenInvalid()
        user = session.get(User, row.user_id)
        assert user is not None  # FK guarantees it  # noqa: S101
        if row.revoked_at is not None:
            revoked = self._revoke_family(session, row.family_id)
            audit(
                session, "auth.refresh_reuse_detected", request=request,
                tenant_id=user.tenant_id, user_id=user.id, resource_type="refresh_token",
                resource_id=str(row.id),
                details={"family_id": str(row.family_id), "revoked": revoked},
            )  # fmt: skip
            session.commit()
            log.warning(
                "auth.refresh_reuse_detected",
                extra={"fields": {"user_id": str(user.id), "family_id": str(row.family_id)}},
            )
            raise TokenInvalid()
        if row.expires_at <= now:
            raise TokenInvalid("Token expired")
        if not user.is_active:
            self._revoke_family(session, row.family_id)
            session.commit()
            raise TokenInvalid()

        pair, new_row = self._issue(session, user, family_id=row.family_id)
        session.flush()
        row.revoked_at = now
        row.replaced_by = new_row.id
        audit(
            session, "auth.refresh", request=request, tenant_id=user.tenant_id,
            user_id=user.id, resource_type="refresh_token", resource_id=str(new_row.id),
            details={"family_id": str(row.family_id), "replaces": str(row.id)},
        )  # fmt: skip
        session.commit()
        return pair

    def logout(self, session: Session, raw_token: str, request: RequestInfo) -> None:
        """Revoke the refresh token's family. Unknown tokens are ignored (idempotent)."""
        row = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        )
        if row is None:
            return
        user = session.get(User, row.user_id)
        self._revoke_family(session, row.family_id)
        audit(
            session, "auth.logout", request=request,
            tenant_id=user.tenant_id if user else None, user_id=row.user_id,
            resource_type="refresh_token", resource_id=str(row.id),
            details={"family_id": str(row.family_id)},
        )  # fmt: skip
        session.commit()

    # ------------------------------------------------------------------ current user
    def load_user(self, session: Session, user_id: uuid.UUID, tenant_id: uuid.UUID) -> User:
        """Load the token's user; inactive users and tenant mismatches are rejected."""
        user = session.get(User, user_id)
        if user is None or not user.is_active or user.tenant_id != tenant_id:
            raise TokenInvalid()
        return user

    # ------------------------------------------------------------------ users (admin)
    def create_user(
        self, session: Session, actor: User, scope: TenantScope, *, username: str,
        email: str, password: str, role: Role, request: RequestInfo,
    ) -> User:  # fmt: skip
        """Create a user in the actor's tenant."""
        user = User(
            id=uuid.uuid4(),
            tenant_id=scope.tenant_id,
            username=username,
            email=email.lower(),
            password_hash=hash_password(password),
            role=role,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            raise Conflict("Username already exists in this tenant") from exc
        audit(
            session, "user.create", request=request, tenant_id=scope.tenant_id,
            user_id=actor.id, resource_type="user", resource_id=str(user.id),
            details={"username": username, "role": role.value},
        )  # fmt: skip
        session.commit()
        return user

    def list_users(self, session: Session, scope: TenantScope) -> list[User]:
        """Users of the scope's tenant only."""
        stmt = scope.apply(select(User), User).order_by(User.username)
        return list(session.scalars(stmt))

    def get_user(self, session: Session, scope: TenantScope, user_id: uuid.UUID) -> User:
        """A user of the scope's tenant; other tenants' users are 'not found'."""
        user = session.scalar(scope.apply(select(User).where(User.id == user_id), User))
        if user is None:
            raise NotFound("User not found")
        return user

    def update_user(
        self, session: Session, actor: User, scope: TenantScope, user_id: uuid.UUID,
        changes: UserChanges, request: RequestInfo,
    ) -> User:  # fmt: skip
        """Change role, (de)activate or unlock a user in the actor's tenant."""
        user = self.get_user(session, scope, user_id)
        if user.id == actor.id and (
            (changes.role is not None and changes.role != user.role) or changes.is_active is False
        ):
            raise PermissionDenied("Admins cannot demote or deactivate themselves")
        diff: dict[str, object] = {}
        if changes.role is not None and changes.role != user.role:
            diff["role"] = {"from": user.role.value, "to": changes.role.value}
            user.role = changes.role
        if changes.is_active is not None and changes.is_active != user.is_active:
            diff["is_active"] = {"from": user.is_active, "to": changes.is_active}
            user.is_active = changes.is_active
            if not changes.is_active:
                self._revoke_user_tokens(session, user.id)
        if changes.unlock and (user.locked_until is not None or user.failed_logins):
            diff["unlocked"] = True
            user.locked_until = None
            user.failed_logins = 0
        if diff:
            audit(
                session, "user.update", request=request, tenant_id=scope.tenant_id,
                user_id=actor.id, resource_type="user", resource_id=str(user.id),
                details=diff,
            )  # fmt: skip
        session.commit()
        return user

    # ------------------------------------------------------------------ tenants (superadmin)
    def create_tenant(
        self, session: Session, actor: User, *, slug: str, name: str, request: RequestInfo
    ) -> Tenant:
        if not SLUG_RE.match(slug):
            raise Conflict("Slug must be lowercase letters, digits and hyphens")
        tenant = Tenant(id=uuid.uuid4(), slug=slug, name=name)
        session.add(tenant)
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            raise Conflict("Tenant slug already exists") from exc
        audit(
            session, "tenant.create", request=request, tenant_id=tenant.id, user_id=actor.id,
            resource_type="tenant", resource_id=str(tenant.id), details={"slug": slug},
        )  # fmt: skip
        session.commit()
        return tenant

    def list_tenants(self, session: Session) -> list[Tenant]:
        return list(session.scalars(select(Tenant).order_by(Tenant.slug)))


def role_allows(actual: Role, required: Role) -> bool:
    """Role hierarchy: admin > analyst > viewer."""
    return ROLE_RANK[actual] >= ROLE_RANK[required]
