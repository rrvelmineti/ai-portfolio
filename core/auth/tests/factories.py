"""Synthetic test data helpers (no real people or credentials)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ai_platform_auth.models import Role, Tenant, User
from ai_platform_auth.passwords import hash_password
from sqlalchemy.orm import Session

PASSWORD = "synthetic-pass-phrase-1"  # noqa: S105 - test-only synthetic password
TEST_SECRET = b"unit-test-jwt-secret-" + b"z" * 40


class FakeClock:
    def __init__(self) -> None:
        # Real "now" (to the second): access tokens are verified against the real clock.
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


_HASH_CACHE: dict[str, str] = {}


def make_tenant(session: Session, slug: str | None = None) -> Tenant:
    slug = slug or f"t-{uuid.uuid4().hex[:8]}"
    tenant = Tenant(id=uuid.uuid4(), slug=slug, name=slug.upper())
    session.add(tenant)
    session.flush()
    return tenant


def make_user(
    session: Session,
    tenant: Tenant,
    username: str,
    role: Role = Role.VIEWER,
    *,
    superadmin: bool = False,
    password: str = PASSWORD,
) -> User:
    if password not in _HASH_CACHE:  # Argon2 is slow on purpose; hash once per password
        _HASH_CACHE[password] = hash_password(password)
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        username=username,
        email=f"{username}@example.test",
        password_hash=_HASH_CACHE[password],
        role=role,
        is_superadmin=superadmin,
    )
    session.add(user)
    session.flush()
    return user
